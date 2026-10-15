"""Run configuration. Every assumption lives here, never inside a formula."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class Config:
    # Analysis window (UTC). Intervals are [start, end).
    period_start: str
    period_end: str

    # Methodology §4: interval length in hours (15 minutes).
    interval_hours: float = 0.25

    # Tariff (methodology §4.6). Demand charge in €/kW per year.
    demand_charge_eur_per_kw_year: float = 0.0
    # Length of the reporting period in months, used to pro-rate the demand charge (rule 7).
    period_months: float = 1.0

    # Annual grid emission factor F in kg CO2e/kWh (methodology §4.7). Source must be documented.
    annual_emission_factor: Optional[float] = None
    annual_emission_factor_source: str = ""

    # Time zone of timestamps without a UTC offset (IANA name, e.g. "Europe/Berlin"). Optional.
    local_timezone: Optional[str] = None

    # Optional information
    grid_connection_limit_kw: Optional[float] = None

    # Rule 7: measured pre-period peak in kW, if available.
    pre_period_peak_kw: Optional[float] = None

    # Data quality thresholds (methodology §6.1)
    min_completeness: float = 0.95
    reconciliation_tolerance: float = 0.03       # session energy vs. sum of meter values
    min_session_kwh: float = 0.1
    min_session_minutes: float = 5.0
    power_tolerance: float = 0.05                 # interval power may exceed rating by 5 %

    # Rule 9: combined relative measurement uncertainty applied to the peak (e.g. 0.02 = 2 %).
    peak_uncertainty: float = 0.02

    # Time comparisons tolerance in seconds
    time_tolerance_s: float = 1.0

    notes: dict = field(default_factory=dict)

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.period_start, tz="UTC")

    @property
    def end(self) -> pd.Timestamp:
        return pd.Timestamp(self.period_end, tz="UTC")

    @property
    def interval(self) -> pd.Timedelta:
        return pd.Timedelta(hours=self.interval_hours)

    def expected_intervals(self) -> pd.DatetimeIndex:
        return pd.date_range(self.start, self.end, freq=self.interval, inclusive="left")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)
