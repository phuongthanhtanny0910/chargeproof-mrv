"""KPI calculation (methodology §4.3 – §4.7) and conservative rules (§6.2)."""

from __future__ import annotations

import pandas as pd

from .config import Config
from .quality import QualityResult


def _series(df: pd.DataFrame | None, col: str, grid: pd.DatetimeIndex) -> pd.Series | None:
    if df is None:
        return None
    return df.set_index("interval_start")[col].reindex(grid)


def interval_table(actual: pd.DataFrame, baseline: pd.DataFrame, q: QualityResult,
                   prices: pd.DataFrame | None, factors: pd.DataFrame | None,
                   cfg: Config) -> tuple[pd.DataFrame, list[dict]]:
    grid = actual.index
    exc: list[dict] = []
    t = pd.DataFrame(index=grid)
    t["ev_actual_kwh"] = actual.sum(axis=1)
    t["ev_baseline_kwh"] = baseline.sum(axis=1)
    t["grid_meter_kwh"] = _series(q.grid_meter, "energy_kwh", grid)
    t["price_eur_per_kwh"] = _series(prices, "price_eur_per_kwh", grid)
    t["factor_kg_per_kwh"] = _series(factors, "kg_co2e_per_kwh", grid)

    if q.grid_meter is not None:
        raw_other = t.grid_meter_kwh - t.ev_actual_kwh
        above = raw_other < -1e-6
        for ts in t.index[above.fillna(False)]:
            exc.append({"check": "charger_above_site", "severity": "flag", "record": str(ts),
                        "detail": "Charger energy above grid meter energy; other load set to 0"})
        t["other_load_kwh"] = raw_other.clip(lower=0)
        t["site_actual_kw"] = t.grid_meter_kwh / cfg.interval_hours
        t["site_baseline_kw"] = (t.other_load_kwh + t.ev_baseline_kwh) / cfg.interval_hours
    t["shifted_kwh"] = (t.ev_baseline_kwh - t.ev_actual_kwh).clip(lower=0)
    return t, exc


def compute(t: pd.DataFrame, sessions: pd.DataFrame, q: QualityResult, cfg: Config) -> dict:
    k: dict = {}
    not_reported: dict = {}

    # K1 Energy (§4.4) – identical by construction; only shifted energy is reported (rule 5)
    k["K1_energy_delivered_kwh"] = {"baseline": float(t.ev_baseline_kwh.sum()),
                                    "actual": float(t.ev_actual_kwh.sum()),
                                    "session_total": float(sessions.energy_kwh.sum())}
    k["K1_energy_shifted_kwh"] = float(t.shifted_kwh.sum())

    # K2 Peak (§4.5)
    if q.grid_complete and "site_actual_kw" in t:
        p_a = float(t.site_actual_kw.max())
        p_b_sim = float(t.site_baseline_kw.max())
        p_b = p_b_sim
        rule7 = False
        if cfg.pre_period_peak_kw is not None and cfg.pre_period_peak_kw < p_b_sim:
            p_b, rule7 = float(cfg.pre_period_peak_kw), True        # rule 3/7: lower baseline peak wins
        delta = p_b - p_a
        threshold = cfg.peak_uncertainty * max(p_a, p_b)
        k["K2_peak_kw"] = {"baseline": p_b, "baseline_simulated": p_b_sim, "actual": p_a,
                           "difference": delta, "rule7_pre_period_applied": rule7,
                           "uncertainty_kw": threshold,
                           "significant": bool(delta > threshold)}      # rule 9
        if cfg.grid_connection_limit_kw is not None:
            k["K2_peak_kw"]["baseline_above_limit"] = p_b > cfg.grid_connection_limit_kw
            k["K2_peak_kw"]["actual_above_limit"] = p_a > cfg.grid_connection_limit_kw
    else:
        not_reported["K2"] = "Grid meter missing or incomplete"

    # K3 Cost (§4.6)
    cost: dict = {}
    needs_price = (t.ev_actual_kwh > 0) | (t.ev_baseline_kwh > 0)
    if t.price_eur_per_kwh.notna()[needs_price].all() and needs_price.any():
        cost["energy"] = {"baseline": float((t.price_eur_per_kwh * t.ev_baseline_kwh).sum()),
                          "actual": float((t.price_eur_per_kwh * t.ev_actual_kwh).sum())}
    else:
        not_reported["K3_energy"] = "Prices missing for intervals with charging"
    if "K2_peak_kw" in k and cfg.demand_charge_eur_per_kw_year > 0:
        f = cfg.demand_charge_eur_per_kw_year * cfg.period_months / 12      # rule 7: pro-rated
        cost["demand"] = {"baseline": f * k["K2_peak_kw"]["baseline"], "actual": f * k["K2_peak_kw"]["actual"]}
    elif "K2_peak_kw" in k:
        not_reported["K3_demand"] = "No demand charge configured"
    if cost:
        tb = sum(v["baseline"] for v in cost.values())
        ta = sum(v["actual"] for v in cost.values())
        cost["total"] = {"baseline": tb, "actual": ta, "difference": tb - ta}
        for v in cost.values():
            v.setdefault("difference", v["baseline"] - v["actual"])
        k["K3_cost_eur"] = cost

    # K4 Emissions (§4.7)
    if cfg.annual_emission_factor is not None:
        e = k["K1_energy_delivered_kwh"]["actual"]
        k["K4_emissions_official_kg"] = {"baseline": cfg.annual_emission_factor * e,
                                         "actual": cfg.annual_emission_factor * e,
                                         "difference": 0.0,                   # rule 8
                                         "factor_source": cfg.annual_emission_factor_source}
    else:
        not_reported["K4_official"] = "No annual emission factor configured"
    if needs_price.any() and t.factor_kg_per_kwh.notna()[needs_price].all():
        gb = float((t.factor_kg_per_kwh * t.ev_baseline_kwh).sum())
        ga = float((t.factor_kg_per_kwh * t.ev_actual_kwh).sum())
        k["K4_emissions_hourly_supplementary_kg"] = {"baseline": gb, "actual": ga, "difference": gb - ga}
    else:
        not_reported["K4_hourly"] = "Hourly factors missing"

    k["not_reported"] = not_reported
    k["indicative_only"] = not q.reconciled
    return _round(k)


def _round(x, nd: int = 6):
    """Round reported values to remove floating-point noise (e.g. -1e-9 shown instead of 0)."""
    if isinstance(x, dict):
        return {key: _round(v, nd) for key, v in x.items()}
    if isinstance(x, float):
        r = round(x, nd)
        return 0.0 if r == 0 else r
    return x
