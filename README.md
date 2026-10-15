# ChargeProof MRV engine

Python implementation of **ChargeProof MRV Methodology v1.0 (draft)**. It measures the effect of managed EV charging at a commercial depot against an *unmanaged charging* baseline and reports four KPIs:

| KPI | Unit | Methodology |
| --- | --- | --- |
| K1 Energy delivered and energy shifted | kWh | §4.4 |
| K2 Peak site demand | kW | §4.5 |
| K3 Cost (energy + pro-rated demand charge) | € | §4.6 |
| K4 Emissions: official (annual factor) and hourly shift effect (supplementary) | kg CO₂e | §4.7 |

> The sample data is **synthetic**. Results from it demonstrate the calculation, not real savings.

**Why this exists:** fleets electrifying their depots run into grid connection limits, and the savings that smart charging promises are rarely measured in a way a finance team or auditor can check. This engine turns raw charger and meter data into audit-ready figures: every number is traceable to raw records, a versioned method and a run ID.

## Quick start

```bash
pip install -r requirements.txt
python scripts/make_sample_data.py                     # writes data/sample/
python -m chargeproof_mrv.run --data data/sample --out output
python -m pytest -q                                    # 10 tests
```

Each run creates:

- `output/raw/<run_id>/` – an immutable copy of every input file, with SHA-256 hashes in `manifest.json`
- `output/runs/<run_id>/results.json` – KPIs, data-quality summary, versions, input hashes and configuration
- `output/runs/<run_id>/intervals.csv` – every 15-minute interval: actual and baseline energy per session, site load, prices, factors
- `output/runs/<run_id>/sessions.csv` – baseline power (rule 4), duration and feasibility (rule 5) per session
- `output/runs/<run_id>/exceptions.csv` – the exception log
- `output/runs/<run_id>/report.md` – a readable summary

## Input files

| File | Columns | Required |
| --- | --- | --- |
| `config.json` | see `chargeproof_mrv/config.py` | yes |
| `sessions.csv` | session_id, vehicle_id, charger_id, plug_in, plug_out, energy_kwh, charger_rating_kw, vehicle_limit_kw, observed_max_kw | yes |
| `meter_values.csv` | session_id, interval_start, energy_kwh | yes |
| `grid_meter.csv` | interval_start, energy_kwh (15-minute RLM data) | for K2 and demand cost |
| `prices.csv` | interval_start, price_eur_per_kwh | for energy cost |
| `factors.csv` | interval_start, kg_co2e_per_kwh | for hourly emissions |

Timestamps are ISO 8601. Either include a UTC offset (`Z`, `+02:00`), or omit it and set `local_timezone` (e.g. `"Europe/Berlin"`) in `config.json`. Ambiguous local times at clock changes are rejected, not guessed. `vehicle_limit_kw` and `observed_max_kw` may be empty; if `observed_max_kw` is empty, the maximum 15-minute power in the run's own meter values is used.

## Where each rule lives

| Methodology | Code |
| --- | --- |
| §3 rule 4 – lowest plausible baseline power | `baseline.baseline_parameters` |
| §3 rule 5 – infeasible baseline keeps actual profile | `baseline.baseline_parameters`, `baseline.baseline_matrix` |
| §3 rule 7 / §6.2 rule 3 – lower pre-period peak wins | `kpis.compute` (`pre_period_peak_kw` in config) |
| §6.1 data quality checks | `quality.run_checks` |
| §6.2 rule 4 – gaps never filled in our favour | grid completeness gate in `quality.run_checks`; K2/K3 suppressed when incomplete |
| §6.2 rule 6 – fixed tariff gives zero energy-cost saving | follows from `kpis.compute`; tested |
| §6.2 rule 7 – demand savings pro-rated | `kpis.compute` |
| §6.2 rule 8 – official emissions unchanged | `kpis.compute` |
| §6.2 rule 9 – small effects not significant | `kpis.compute` (`peak_uncertainty` in config) |
| §7.3 append-only raw layer with hashes | `ingest.store_raw` |

## Verification

`tests/test_engine.py::test_matches_excel` checks the engine against the hand calculation in `ChargeProof_MRV_hand_calculation.xlsx` (Results sheet). The other tests cover the conservative rules and data-quality checks.

## Not yet implemented (v0.1)

- Direct OCPP or charge-point-operator API ingestion (CSV export only)
- Battery storage or on-site PV inside the boundary
- Signed meter value (OCMF) verification

## About

Built by **Thanh Nguyen** (finance, accounting principles and data analytics) as the measurement layer of [ChargeProof](https://chargeproof.de), a project in preparation for smart charging of commercial EV fleets.

The approach applies accounting controls to energy data: an immutable raw layer, reconciliations, an exception log, versioned methodology and conservative estimates.

© 2026 Thanh Nguyen. All rights reserved. The code is published for review; please ask before reusing it.
