"""Le centre de décision doit exposer un point d'équilibre cohérent.

Le simulateur (onglet Finance) affiche désormais le seuil de rentabilité du
scénario courant ; on vérifie qu'il est calculé et cohérent avec le volume
vendu, et que le calcul n'introduit aucune erreur d'exécution.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def _run_cockpit():
    env = dict(os.environ)
    env.pop("SIM_PAGE", None)
    env.pop("SIM_TOUR", None)
    proc = subprocess.run(
        [sys.executable, "simulator_smoke_runner.py", "aout_sans_juillet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-2000:])
    return json.loads(lignes[0][len("##JSON##"):])


def _nombre(texte: str) -> float:
    """Extrait le nombre d'un libellé (« 3 730 unités » -> 3730.0)."""
    brut = str(texte).replace("€", "").replace("%", "").replace(" ", "")
    chiffres = re.sub(r"[^0-9,.\-]", "", brut)
    return float(chiffres.replace(",", "."))


def test_centre_de_decision_affiche_le_point_equilibre():
    data = _run_cockpit()

    assert data["errors"] == []
    metriques = {m[0]: m[1] for m in data["metrics"]}
    for libelle in ("Point d'équilibre", "CA d'équilibre", "Marge de sécurité", "Charges fixes du mois"):
        assert libelle in metriques


def test_point_equilibre_du_scenario_est_coherent_avec_le_volume_vendu():
    data = _run_cockpit()
    metriques = {m[0]: m[1] for m in data["metrics"]}

    seuil = _nombre(metriques["Point d'équilibre"])
    securite = _nombre(metriques["Marge de sécurité"])
    volume = _nombre(metriques["Volume total à produire"])

    # Marge de sécurité = volume vendu − seuil d'équilibre
    assert seuil + securite == pytest.approx(volume, rel=0.01)
    assert seuil > 0
