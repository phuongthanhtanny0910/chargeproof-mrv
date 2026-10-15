"""Tests for the ChargeProof MRV engine.

The expected values in test_matches_excel come from the 'Results' sheet of
ChargeProof_MRV_hand_calculation.xlsx (recalculated in LibreOffice), which
implements the same methodology with spreadsheet formulas.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from chargeproof_mrv.run import run

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"


@pytest.fixture(scope="session", autouse=True)
def sample_data():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_sample_data.py")], check=True)


def copy_sample(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    shutil.copytree(SAMPLE, d)
    return d


def patch_config(d: Path, **kw) -> None:
    cfg = json.loads((d / "config.json").read_text())
    cfg.update(kw)
    (d / "config.json").write_text(json.dumps(cfg))


# ---------------------------------------------------------------- reference run

def test_matches_excel(tmp_path):
    k = run(SAMPLE, tmp_path / "out")["kpis"]
    approx = lambda x: pytest.approx(x, abs=1e-6)
    assert k["K1_energy_delivered_kwh"]["actual"] == approx(162.0)
    assert k["K1_energy_delivered_kwh"]["baseline"] == approx(162.0)
    assert k["K1_energy_shifted_kwh"] == approx(143.35)
    assert k["K2_peak_kw"]["baseline"] == approx(54.2)
    assert k["K2_peak_kw"]["actual"] == approx(28.0)
    assert k["K3_cost_eur"]["energy"]["baseline"] == approx(51.468)
    assert k["K3_cost_eur"]["energy"]["actual"] == approx(35.568)
    assert k["K3_cost_eur"]["demand"]["baseline"] == approx(451.6666667)
    assert k["K3_cost_eur"]["demand"]["actual"] == approx(233.3333333)
    assert k["K3_cost_eur"]["total"]["difference"] == approx(234.2333333)
    assert k["K4_emissions_official_kg"]["actual"] == approx(61.56)
    assert k["K4_emissions_official_kg"]["difference"] == 0.0
    assert k["K4_emissions_hourly_supplementary_kg"]["baseline"] == approx(72.5745)
    assert k["K4_emissions_hourly_supplementary_kg"]["actual"] == approx(56.982)


def test_rule4_and_rule5(tmp_path):
    r = run(SAMPLE, tmp_path / "out")
    s = pd.read_csv(tmp_path / "out" / "runs" / r["run_id"] / "sessions.csv").set_index("session_id")
    assert s.loc["S4", "baseline_power_kw"] == 16          # observed 16 kW, not 22 kW nameplate
    assert s.loc["S3", "baseline_power_kw"] == 7.4         # vehicle limit
    assert not s.loc["S5", "baseline_feasible"]            # rule 5 triggers
    assert s.loc["S1", "baseline_feasible"]


def test_raw_layer_is_hashed_and_append_only(tmp_path):
    r = run(SAMPLE, tmp_path / "out", run_id="fixed")
    manifest = json.loads((tmp_path / "out" / "raw" / "fixed" / "manifest.json").read_text())
    assert len(manifest["files"]["sessions.csv"]["sha256"]) == 64
    with pytest.raises(FileExistsError):
        run(SAMPLE, tmp_path / "out", run_id="fixed")


# ---------------------------------------------------------------- conservative rules

def test_rule7_lower_pre_period_peak_wins(tmp_path):
    d = copy_sample(tmp_path)
    patch_config(d, pre_period_peak_kw=40)
    p = run(d, tmp_path / "out")["kpis"]["K2_peak_kw"]
    assert p["baseline"] == 40 and p["rule7_pre_period_applied"]
    assert p["difference"] == pytest.approx(12.0)


def test_rule9_small_effect_not_significant(tmp_path):
    d = copy_sample(tmp_path)
    patch_config(d, pre_period_peak_kw=28.3)               # 0.3 kW below actual peak + 2 % uncertainty
    p = run(d, tmp_path / "out")["kpis"]["K2_peak_kw"]
    assert p["significant"] is False


def test_fixed_tariff_gives_zero_energy_cost_saving(tmp_path):
    d = copy_sample(tmp_path)
    pr = pd.read_csv(d / "prices.csv")
    pr["price_eur_per_kwh"] = 0.28
    pr.to_csv(d / "prices.csv", index=False)
    c = run(d, tmp_path / "out")["kpis"]["K3_cost_eur"]["energy"]
    assert c["difference"] == 0.0


# ---------------------------------------------------------------- data quality

def test_incomplete_grid_meter_suppresses_k2_and_demand(tmp_path):
    d = copy_sample(tmp_path)
    gm = pd.read_csv(d / "grid_meter.csv").iloc[:50]        # 50 of 64 intervals
    gm.to_csv(d / "grid_meter.csv", index=False)
    k = run(d, tmp_path / "out")["kpis"]
    assert "K2_peak_kw" in k["not_reported"] or "K2" in k["not_reported"]
    assert "demand" not in k.get("K3_cost_eur", {})


def test_power_above_rating_excludes_session(tmp_path):
    d = copy_sample(tmp_path)
    mv = pd.read_csv(d / "meter_values.csv")
    i = mv.index[mv.session_id == "S2"][0]
    mv.loc[i, "energy_kwh"] = 5.0                            # 20 kW on an 11 kW charger
    mv.to_csv(d / "meter_values.csv", index=False)
    r = run(d, tmp_path / "out")
    assert r["data_quality"]["sessions_used"] == 4
    ex = pd.read_csv(tmp_path / "out" / "runs" / r["run_id"] / "exceptions.csv")
    assert ((ex.check == "plausibility") & (ex.record == "S2")).any()


def test_unreconciled_session_marks_results_indicative(tmp_path):
    d = copy_sample(tmp_path)
    s = pd.read_csv(d / "sessions.csv")
    s.loc[s.session_id == "S1", "energy_kwh"] = 45           # meter values sum to 40
    s.to_csv(d / "sessions.csv", index=False)
    r = run(d, tmp_path / "out")
    assert r["data_quality"]["reconciled"] is False
    assert r["kpis"]["indicative_only"] is True


def test_duplicate_session_is_dropped(tmp_path):
    d = copy_sample(tmp_path)
    s = pd.read_csv(d / "sessions.csv")
    dup = s.iloc[[0]].copy()
    dup["session_id"] = "S1-dup"
    pd.concat([s, dup]).to_csv(d / "sessions.csv", index=False)
    r = run(d, tmp_path / "out")
    assert r["data_quality"]["sessions_used"] == 5
