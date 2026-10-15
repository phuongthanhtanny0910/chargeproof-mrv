"""Unmanaged-charging baseline (methodology §3 and §4.2)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


def actual_matrix(meter_values: pd.DataFrame, sessions: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Actual energy per interval (rows) and session (columns), kWh. Missing values are 0."""
    pv = meter_values.pivot_table(index="interval_start", columns="session_id",
                                  values="energy_kwh", aggfunc="sum")
    pv = pv.reindex(index=grid, columns=list(sessions.session_id)).fillna(0.0)
    pv.index.name = "interval_start"
    return pv


def baseline_parameters(sessions: pd.DataFrame, actual: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Rule 4 (lowest plausible power), duration and rule 5 (feasibility)."""
    s = sessions.copy()

    # Observed maximum: use the provided period value; otherwise derive from this run's meter values.
    derived = (actual.max(axis=0) / cfg.interval_hours).rename("derived_max_kw")
    s = s.join(derived, on="session_id")
    s["observed_max_used_kw"] = s.observed_max_kw.where(s.observed_max_kw.notna(), s.derived_max_kw)

    # Rule 4: lowest of charger rating, vehicle limit and observed maximum (NaN ignored).
    s["baseline_power_kw"] = s[["charger_rating_kw", "vehicle_limit_kw", "observed_max_used_kw"]].min(axis=1, skipna=True)

    s["baseline_hours"] = s.energy_kwh / s.baseline_power_kw
    s["baseline_end"] = s.plug_in + pd.to_timedelta(s.baseline_hours, unit="h")
    tol = pd.Timedelta(seconds=cfg.time_tolerance_s)
    s["baseline_feasible"] = s.baseline_end <= s.plug_out + tol   # rule 5
    return s


def _overlap_hours(k_start: np.ndarray, k_end: np.ndarray, a: pd.Timestamp, b: pd.Timestamp) -> np.ndarray:
    lo = np.maximum(k_start, np.datetime64(a.tz_convert(None)))
    hi = np.minimum(k_end, np.datetime64(b.tz_convert(None)))
    return np.clip((hi - lo) / np.timedelta64(1, "s"), 0, None) / 3600.0


def baseline_matrix(params: pd.DataFrame, actual: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Baseline energy per interval and session, kWh (methodology §4.2).

    Feasible sessions charge at baseline power from plug-in until their energy is delivered.
    Infeasible sessions (rule 5) keep their actual profile: no saving is claimed for them.
    """
    grid = actual.index
    k_start = grid.tz_convert(None).values
    k_end = (grid + cfg.interval).tz_convert(None).values
    out = pd.DataFrame(index=grid)
    for _, r in params.iterrows():
        if r.baseline_feasible:
            out[r.session_id] = r.baseline_power_kw * _overlap_hours(k_start, k_end, r.plug_in, r.baseline_end)
        else:
            out[r.session_id] = actual[r.session_id].values
    out.index.name = "interval_start"
    return out
