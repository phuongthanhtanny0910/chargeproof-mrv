"""Generate the synthetic sample used in ChargeProof_MRV_hand_calculation.xlsx.

5 sessions, one night (12.10.2026 16:00 – 13.10.2026 08:00, labelled UTC), 15-minute intervals.
All values are ILLUSTRATIVE and must not be presented as real results.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data" / "sample"
D0 = datetime(2026, 10, 12)


def t(h, m=0, day=0):
    return D0 + timedelta(days=day, hours=h, minutes=m)


# session_id, vehicle, charger, plug_in, plug_out, kWh, rating, vehicle_limit, observed_max, actual_start, actual_kw
SESSIONS = [
    ("S1", "Van-01", "CP-01", t(16, 30), t(6, 30, 1), 40, 22, 11, 11, t(22), 6),
    ("S2", "Van-02", "CP-02", t(17), t(7, 0, 1), 35, 11, 11, 11, t(23), 6),
    ("S3", "Van-03", "CP-03", t(17, 15), t(6, 0, 1), 30, 11, 7.4, 7.4, t(20), 5),
    ("S4", "Van-04", "CP-04", t(18), t(7, 30, 1), 45, 22, 22, 16, t(0, 0, 1), 8),
    ("S5", "Van-05", "CP-05", t(19), t(21, 30), 12, 11, 4.6, 4.8, t(19), 4.8),
]


def price(h): return 0.32 if 16 <= h < 21 else 0.24 if 21 <= h < 24 else 0.20 if 0 <= h < 6 else 0.26
def factor(h): return 0.45 if 16 <= h < 21 else 0.38 if 21 <= h < 24 else 0.33 if 0 <= h < 6 else 0.40
def base_kw(h): return 6 if 16 <= h < 18 else 4 if 18 <= h < 22 else 3 if (h >= 22 or h < 6) else 5


def overlap(a0, a1, b0, b1):
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds() / 3600)


def iso(x):
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    grid = [t(16) + timedelta(minutes=15 * k) for k in range(64)]

    pd.DataFrame([{
        "session_id": s[0], "vehicle_id": s[1], "charger_id": s[2], "plug_in": iso(s[3]), "plug_out": iso(s[4]),
        "energy_kwh": s[5], "charger_rating_kw": s[6], "vehicle_limit_kw": s[7], "observed_max_kw": s[8],
    } for s in SESSIONS]).to_csv(OUT / "sessions.csv", index=False)

    mv, gm, pr, fa = [], [], [], []
    for ks in grid:
        ke = ks + timedelta(minutes=15)
        ev = 0.0
        for s in SESSIONS:
            e = s[10] * overlap(ks, ke, s[9], s[9] + timedelta(hours=s[5] / s[10]))
            if e > 0:
                mv.append({"session_id": s[0], "interval_start": iso(ks), "energy_kwh": round(e, 4)})
                ev += e
        gm.append({"interval_start": iso(ks), "energy_kwh": round(base_kw(ks.hour) * 0.25 + ev, 4)})
        pr.append({"interval_start": iso(ks), "price_eur_per_kwh": price(ks.hour)})
        fa.append({"interval_start": iso(ks), "kg_co2e_per_kwh": factor(ks.hour)})

    pd.DataFrame(mv).to_csv(OUT / "meter_values.csv", index=False)
    pd.DataFrame(gm).to_csv(OUT / "grid_meter.csv", index=False)
    pd.DataFrame(pr).to_csv(OUT / "prices.csv", index=False)
    pd.DataFrame(fa).to_csv(OUT / "factors.csv", index=False)

    config = {
        "period_start": "2026-10-12T16:00:00Z",
        "period_end": "2026-10-13T08:00:00Z",
        "interval_hours": 0.25,
        "demand_charge_eur_per_kw_year": 100,
        "period_months": 1,
        "annual_emission_factor": 0.38,
        "annual_emission_factor_source": "ILLUSTRATIVE placeholder - replace with latest published UBA factor",
        "grid_connection_limit_kw": 60,
        "notes": {"data": "Synthetic sample matching ChargeProof_MRV_hand_calculation.xlsx. Not real results."},
    }
    (OUT / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Sample data written to {OUT}")


if __name__ == "__main__":
    main()
