"""Data quality checks (methodology §6.1).

Checks never fail silently: every finding is written to the exception log.
Records that fail a blocking check are excluded from the calculation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import Config
from .ingest import Inputs


@dataclass
class QualityResult:
    sessions: pd.DataFrame
    meter_values: pd.DataFrame
    grid_meter: pd.DataFrame | None
    exceptions: list[dict] = field(default_factory=list)
    grid_complete: bool = False
    grid_completeness: float = 0.0
    reconciled: bool = True


def _log(exc: list, check: str, severity: str, record: str, detail: str) -> None:
    exc.append({"check": check, "severity": severity, "record": record, "detail": detail})


def run_checks(inputs: Inputs, cfg: Config) -> QualityResult:
    exc: list[dict] = []
    s = inputs.sessions.copy()
    mv = inputs.meter_values.copy()

    # --- Sessions: duplicates (same charger, same plug-in time, same energy)
    dup = s.duplicated(subset=["charger_id", "plug_in", "energy_kwh"], keep="first")
    for sid in s.loc[dup, "session_id"]:
        _log(exc, "duplicates", "excluded", str(sid), "Duplicate session (same charger, plug-in time and energy)")
    s = s.loc[~dup]

    # --- Sessions: meter continuity and plausibility
    bad = pd.Series(False, index=s.index)
    for idx, row in s.iterrows():
        sid = str(row.session_id)
        dur_min = (row.plug_out - row.plug_in).total_seconds() / 60
        if pd.isna(row.energy_kwh) or row.energy_kwh < 0:
            _log(exc, "meter_continuity", "excluded", sid, "Missing or negative session energy")
            bad[idx] = True
        elif row.energy_kwh < cfg.min_session_kwh:
            _log(exc, "plausibility", "excluded", sid, f"Energy {row.energy_kwh} kWh below {cfg.min_session_kwh} kWh")
            bad[idx] = True
        if dur_min <= 0:
            _log(exc, "plausibility", "excluded", sid, "Plug-out not after plug-in")
            bad[idx] = True
        elif dur_min < cfg.min_session_minutes:
            _log(exc, "plausibility", "excluded", sid, f"Duration {dur_min:.1f} min below {cfg.min_session_minutes} min")
            bad[idx] = True
        if pd.isna(row.charger_rating_kw) or row.charger_rating_kw <= 0:
            _log(exc, "plausibility", "excluded", sid, "Missing charger rating")
            bad[idx] = True
    s = s.loc[~bad]
    valid_ids = set(s.session_id)

    # --- Meter values: unknown sessions, duplicates, negatives
    unknown = ~mv.session_id.isin(valid_ids)
    for sid in sorted(set(mv.loc[unknown, "session_id"].astype(str))):
        _log(exc, "meter_values", "excluded", sid, "Meter values for a session that is unknown or excluded")
    mv = mv.loc[~unknown]

    dupmv = mv.duplicated(subset=["session_id", "interval_start"], keep="first")
    if dupmv.any():
        for _, r in mv.loc[dupmv].iterrows():
            _log(exc, "duplicates", "excluded", str(r.session_id), f"Duplicate meter value at {r.interval_start}")
        mv = mv.loc[~dupmv]

    neg = mv.energy_kwh.isna() | (mv.energy_kwh < 0)
    drop_sessions: set = set()
    for _, r in mv.loc[neg].iterrows():
        _log(exc, "meter_continuity", "excluded", str(r.session_id), f"Missing or negative interval energy at {r.interval_start}")
        drop_sessions.add(r.session_id)

    # --- Interval power above charger rating (+ tolerance)
    rating = s.set_index("session_id").charger_rating_kw
    power = mv.energy_kwh / cfg.interval_hours
    limit = mv.session_id.map(rating) * (1 + cfg.power_tolerance)
    over = power > limit
    for _, r in mv.loc[over].iterrows():
        _log(exc, "plausibility", "excluded", str(r.session_id), f"Interval power above charger rating at {r.interval_start}")
        drop_sessions.add(r.session_id)

    # --- Meter values outside the plug-in window (flag only)
    win = s.set_index("session_id")[["plug_in", "plug_out"]]
    tol = pd.Timedelta(seconds=cfg.time_tolerance_s)
    for _, r in mv.iterrows():
        pi, po = win.loc[r.session_id]
        if r.energy_kwh > 0 and (r.interval_start + cfg.interval <= pi - tol or r.interval_start >= po + tol):
            _log(exc, "time_consistency", "flag", str(r.session_id), f"Energy recorded outside plug-in window at {r.interval_start}")

    if drop_sessions:
        s = s.loc[~s.session_id.isin(drop_sessions)]
        mv = mv.loc[~mv.session_id.isin(drop_sessions)]

    # --- Reconciliation: session energy vs. sum of interval meter values
    reconciled = True
    sums = mv.groupby("session_id").energy_kwh.sum()
    for _, row in s.iterrows():
        total = float(sums.get(row.session_id, 0.0))
        if row.energy_kwh > 0 and abs(total - row.energy_kwh) / row.energy_kwh > cfg.reconciliation_tolerance:
            reconciled = False
            _log(exc, "reconciliation", "flag", str(row.session_id),
                 f"Interval sum {total:.3f} kWh vs. session energy {row.energy_kwh:.3f} kWh")

    # --- Grid meter: completeness over the analysis window
    gm = inputs.grid_meter
    grid_complete, completeness = False, 0.0
    if gm is None:
        _log(exc, "completeness", "flag", "grid_meter", "No grid meter data: K2 and demand cost are not reported")
    else:
        gm = gm.copy()
        dupg = gm.duplicated(subset=["interval_start"], keep="first")
        if dupg.any():
            _log(exc, "duplicates", "excluded", "grid_meter", f"{int(dupg.sum())} duplicate grid meter intervals")
            gm = gm.loc[~dupg]
        negg = gm.energy_kwh.isna() | (gm.energy_kwh < 0)
        if negg.any():
            _log(exc, "meter_continuity", "excluded", "grid_meter", f"{int(negg.sum())} missing or negative grid meter intervals")
            gm = gm.loc[~negg]
        expected = cfg.expected_intervals()
        present = gm.interval_start.isin(expected).sum()
        completeness = present / len(expected) if len(expected) else 0.0
        grid_complete = completeness >= cfg.min_completeness
        if not grid_complete:
            _log(exc, "completeness", "flag", "grid_meter",
                 f"Completeness {completeness:.1%} below {cfg.min_completeness:.0%}: K2 and demand cost not reported")

    return QualityResult(s.reset_index(drop=True), mv.reset_index(drop=True), gm, exc,
                         grid_complete, completeness, reconciled)
