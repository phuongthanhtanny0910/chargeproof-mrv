"""Command-line entry point.

    python -m chargeproof_mrv.run --data data/sample --out output

Creates output/raw/<run_id>/ (immutable, hashed copy of the inputs) and
output/runs/<run_id>/ (results.json, intervals.csv, sessions.csv, exceptions.csv, report.md).
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import ENGINE_VERSION, METHODOLOGY_VERSION
from .baseline import actual_matrix, baseline_matrix, baseline_parameters
from .config import Config
from .ingest import load, store_raw
from .kpis import compute, interval_table
from .quality import run_checks


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def run(data_dir: str | Path, out_dir: str | Path, run_id: str | None = None) -> dict:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    run_id = run_id or new_run_id()
    raw_dir = out_dir / "raw" / run_id
    manifest = store_raw(data_dir, raw_dir)

    cfg = Config.load(raw_dir / "config.json")
    inputs = load(raw_dir, manifest)
    q = run_checks(inputs, cfg)

    grid = cfg.expected_intervals()
    actual = actual_matrix(q.meter_values, q.sessions, grid)
    params = baseline_parameters(q.sessions, actual, cfg)
    for _, r in params.loc[~params.baseline_feasible].iterrows():
        q.exceptions.append({"check": "rule5_infeasible_baseline", "severity": "info", "record": str(r.session_id),
                             "detail": "Baseline cannot deliver the energy within the plug-in window; actual profile used, no saving claimed"})
    base = baseline_matrix(params, actual, cfg)
    table, exc = interval_table(actual, base, q, inputs.prices, inputs.factors, cfg)
    q.exceptions.extend(exc)
    kpis = compute(table, q.sessions, q, cfg)

    result = {
        "run_id": run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine_version": ENGINE_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "period": {"start": cfg.period_start, "end": cfg.period_end},
        "inputs": manifest["files"],
        "config": cfg.to_dict(),
        "data_quality": {"grid_completeness": q.grid_completeness, "grid_complete": q.grid_complete,
                         "reconciled": q.reconciled, "sessions_used": int(len(q.sessions)),
                         "exceptions": len(q.exceptions)},
        "kpis": kpis,
    }

    run_dir = out_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "results.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    detail = table.join(actual.add_prefix("actual_")).join(base.add_prefix("baseline_"))
    detail.to_csv(run_dir / "intervals.csv")
    params.to_csv(run_dir / "sessions.csv", index=False)
    pd.DataFrame(q.exceptions, columns=["check", "severity", "record", "detail"]).to_csv(run_dir / "exceptions.csv", index=False)
    (run_dir / "report.md").write_text(render_report(result), encoding="utf-8")
    return result


def _fmt(x, nd=2):
    return "–" if x is None else f"{x:,.{nd}f}"


def render_report(r: dict) -> str:
    k = r["kpis"]
    lines = [
        f"# ChargeProof MRV report",
        "",
        f"Run `{r['run_id']}` · engine {r['engine_version']} · methodology {r['methodology_version']}",
        f"Period {r['period']['start']} to {r['period']['end']} (UTC)",
        "",
        "| KPI | Baseline | Actual | Difference | Unit |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    e = k["K1_energy_delivered_kwh"]
    lines.append(f"| K1 Energy delivered | {_fmt(e['baseline'])} | {_fmt(e['actual'])} | 0.00 | kWh |")
    lines.append(f"| K1 Energy shifted | | | {_fmt(k['K1_energy_shifted_kwh'])} | kWh |")
    if "K2_peak_kw" in k:
        p = k["K2_peak_kw"]
        sig = "" if p["significant"] else " (not significant)"
        lines.append(f"| K2 Peak site demand | {_fmt(p['baseline'])} | {_fmt(p['actual'])} | {_fmt(p['difference'])}{sig} | kW |")
    if "K3_cost_eur" in k:
        for part, label in [("energy", "K3 Energy cost"), ("demand", "K3 Demand cost (pro-rated)"), ("total", "K3 Total cost")]:
            if part in k["K3_cost_eur"]:
                c = k["K3_cost_eur"][part]
                lines.append(f"| {label} | {_fmt(c['baseline'])} | {_fmt(c['actual'])} | {_fmt(c['difference'])} | € |")
    if "K4_emissions_official_kg" in k:
        g = k["K4_emissions_official_kg"]
        lines.append(f"| K4 Emissions, official (annual factor) | {_fmt(g['baseline'])} | {_fmt(g['actual'])} | 0.00 | kg CO2e |")
    if "K4_emissions_hourly_supplementary_kg" in k:
        g = k["K4_emissions_hourly_supplementary_kg"]
        lines.append(f"| K4 Emissions, hourly (supplementary) | {_fmt(g['baseline'])} | {_fmt(g['actual'])} | {_fmt(g['difference'])} | kg CO2e |")
    dq = r["data_quality"]
    lines += ["", "## Data quality",
              f"- Grid meter completeness: {dq['grid_completeness']:.1%}",
              f"- Sessions reconciled: {'yes' if dq['reconciled'] else 'NO – results indicative only'}",
              f"- Sessions used: {dq['sessions_used']}; exception log entries: {dq['exceptions']}"]
    if k["not_reported"]:
        lines += ["", "## Not reported"] + [f"- {key}: {why}" for key, why in k["not_reported"].items()]
    lines += ["", "Official emissions use the annual factor and are unchanged by managed charging (rule 8). "
              "The hourly figure is a supplementary indicator, not a Scope 2 reduction."]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="ChargeProof MRV engine")
    ap.add_argument("--data", required=True, help="Folder with config.json and input CSVs")
    ap.add_argument("--out", default="output", help="Output folder")
    a = ap.parse_args()
    r = run(a.data, a.out)
    print(render_report(r))
    print(f"Outputs written to {Path(a.out) / 'runs' / r['run_id']}")


if __name__ == "__main__":
    main()
