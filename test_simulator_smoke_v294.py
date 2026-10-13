"""Exécution réelle du simulateur (Streamlit et MySQL simulés) — sous-processus isolé."""
import json
import os
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).parent / "simulator_smoke_runner.py"


def run(scenario, month="Août N+1"):
    proc = subprocess.run(
        [sys.executable, str(RUNNER), scenario], capture_output=True, text=True,
        env=dict(os.environ, SIM_MONTH=month), timeout=120,
    )
    assert "##JSON##" in proc.stdout, proc.stderr[-2000:]
    log = json.loads(proc.stdout.split("##JSON##")[1])
    log["m"] = {label: value for label, value, _ in log["metrics"]}
    return log


def test_opening_cash_from_month_extract_when_m1_missing():
    log = run("aout_sans_juillet")
    assert log["inputs"]["sim_treso_ouverture"] == 1_250_000.0
    assert log["m"]["Trésorerie finale"] != log["m"]["Résultat net"]
    assert any("Juillet" in w and "repli" in w for w in log["warnings"])   # le repli SQL est signalé


def test_m1_found_despite_dash_variant_in_period_label():
    log = run("juillet_present")
    assert log["inputs"]["sim_treso_ouverture"] == 1_250_000.0
    assert not any("Pont M-1 incomplet" in w for w in log["warnings"])


def test_scraped_decisions_fill_missing_sql_month():
    log = run("decisions_scrapees")
    assert log["inputs"]["sim_pm_s3"] == 130.0
    assert log["inputs"]["sim_pm_pub_marque"] == 70000.0
    assert log["inputs"]["ord_i7"] == 1800
    assert log["inputs"]["sim_treso_ouverture"] == 2_000_000.0   # extrait sans onglet « Banque » : toléré


def test_nothing_in_base_is_flagged_not_silent():
    log = run("rien")
    assert any("Pont M-1 incomplet" in w for w in log["warnings"])
    assert any("saisis-le" in w for w in log["warnings"])
    assert not log["errors"]


def test_empty_scraped_decisions_are_flagged_and_ignored():
    log = run("decisions_vides")
    assert any("VIDE" in w for w in log["warnings"])          # alerte explicite dans l'encart
    assert log["inputs"]["sim_pm_s3"] == 115.0                # valeurs du repli SQL, pas de valeur inventée
    assert not log["errors"]
