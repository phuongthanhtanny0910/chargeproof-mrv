"""Load input files and store an immutable raw copy (methodology §7.3).

Expected files in the data folder:

    config.json        run configuration (see config.py)
    sessions.csv       one row per charging session
    meter_values.csv   actual energy per session and 15-minute interval
    grid_meter.csv     depot grid meter energy per 15-minute interval (RLM)
    prices.csv         energy price per interval (€/kWh, net)
    factors.csv        hourly grid emission factor per interval (kg CO2e/kWh), optional

All timestamps are ISO 8601 in UTC.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

INPUT_FILES = ["config.json", "sessions.csv", "meter_values.csv", "grid_meter.csv", "prices.csv", "factors.csv"]
REQUIRED = {"config.json", "sessions.csv", "meter_values.csv"}

SESSION_COLUMNS = [
    "session_id", "vehicle_id", "charger_id", "plug_in", "plug_out", "energy_kwh",
    "charger_rating_kw", "vehicle_limit_kw", "observed_max_kw",
]


@dataclass
class Inputs:
    sessions: pd.DataFrame
    meter_values: pd.DataFrame
    grid_meter: pd.DataFrame | None
    prices: pd.DataFrame | None
    factors: pd.DataFrame | None
    manifest: dict


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def store_raw(data_dir: Path, raw_dir: Path) -> dict:
    """Copy every input file into a new, run-specific raw folder and hash it.

    The folder must not exist yet: raw data is append-only and never overwritten.
    """
    raw_dir.mkdir(parents=True, exist_ok=False)
    files = {}
    for name in INPUT_FILES:
        src = data_dir / name
        if not src.exists():
            if name in REQUIRED:
                raise FileNotFoundError(f"Required input missing: {src}")
            continue
        dst = raw_dir / name
        shutil.copy2(src, dst)
        files[name] = {"sha256": sha256(dst), "bytes": dst.stat().st_size}
    manifest = {"files": files}
    (raw_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def load(raw_dir: Path, manifest: dict) -> Inputs:
    sessions = pd.read_csv(raw_dir / "sessions.csv")
    missing = [c for c in SESSION_COLUMNS if c not in sessions.columns]
    if missing:
        raise ValueError(f"sessions.csv is missing columns: {missing}")
    sessions["plug_in"] = _ts(sessions["plug_in"])
    sessions["plug_out"] = _ts(sessions["plug_out"])
    for c in ["energy_kwh", "charger_rating_kw", "vehicle_limit_kw", "observed_max_kw"]:
        sessions[c] = pd.to_numeric(sessions[c], errors="coerce")

    mv = pd.read_csv(raw_dir / "meter_values.csv")
    mv["interval_start"] = _ts(mv["interval_start"])
    mv["energy_kwh"] = pd.to_numeric(mv["energy_kwh"], errors="coerce")

    def optional(name: str, value_col: str) -> pd.DataFrame | None:
        p = raw_dir / name
        if not p.exists():
            return None
        df = pd.read_csv(p)
        df["interval_start"] = _ts(df["interval_start"])
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
        return df

    return Inputs(
        sessions=sessions,
        meter_values=mv,
        grid_meter=optional("grid_meter.csv", "energy_kwh"),
        prices=optional("prices.csv", "price_eur_per_kwh"),
        factors=optional("factors.csv", "kg_co2e_per_kwh"),
        manifest=manifest,
    )
