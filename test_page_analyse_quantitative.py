"""La page d'analyse quantitative doit s'exécuter sans erreur.

Le test lance la page `pages/6_Analyse_Quantitative.py` dans un sous-processus
(banc d'essai sans Streamlit ni MySQL) et vérifie qu'elle produit un point
d'équilibre cohérent : une page qui plante au chargement ne rendrait aucune
analyse exploitable.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def _run_page():
    env = dict(os.environ)
    env["SIM_PAGE"] = "6_Analyse_Quantitative.py"
    env["SIM_TOUR"] = "1"
    proc = subprocess.run(
        [sys.executable, "simulator_smoke_runner.py", "aout_sans_juillet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-2000:])
    return json.loads(lignes[0][len("##JSON##"):])


def _nombre(texte: str) -> float:
    return float(str(texte).replace("€", "").replace("%", "").replace("u", "").replace(" ", "").replace(",", "."))


def test_page_analyse_quantitative_runs_without_error():
    data = _run_page()

    assert data["errors"] == []
    metriques = {m[0]: m[1] for m in data["metrics"]}
    assert "Point d'équilibre" in metriques
    assert "CA d'équilibre" in metriques


def test_page_computes_a_consistent_break_even():
    data = _run_page()
    metriques = {m[0]: m[1] for m in data["metrics"]}

    seuil_unites = _nombre(metriques["Point d'équilibre"])
    seuil_ca = _nombre(metriques["CA d'équilibre"])
    charges = _nombre(metriques["Charges fixes retenues"])
    taux = _nombre(metriques["Taux de marge pondéré"]) / 100.0

    assert seuil_unites > 0
    # CA d'équilibre = charges fixes / taux de marge pondéré
    assert seuil_ca == pytest.approx(charges / taux, rel=0.01)
    # marge de sécurité : volume vendu - seuil
    assert _nombre(metriques["Marge de sécurité"]) > 0


def test_page_does_not_warn_when_the_data_is_complete():
    """Avec l'étude de coûts disponible, aucune alerte de vigilance ne doit sortir."""
    data = _run_page()
    vigilance = [w for w in data.get("warnings", []) if "vigilance" in w.lower()]

    assert vigilance == []
