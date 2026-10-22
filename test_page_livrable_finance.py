"""Page « Livrable Finance » : le livrable doit se produire de bout en bout.

La page est exécutée dans un sous-processus (banc d'essai sans Streamlit ni
MySQL) avec un jeu de données comptables réaliste : bilan détaillé, ratios,
prix de vente, coûts de production, relevés bancaires et compte de résultat
simplifié. On vérifie que les indicateurs du livrable sont cohérents et que
les marges sont bien produites.
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
    env["SIM_PAGE"] = "5_Livrable_Finance.py"
    env["SIM_TOUR"] = "8"          # Année 1 - Août : le mois documenté par le fixture
    proc = subprocess.run(
        [sys.executable, "simulator_smoke_runner.py", "aout_sans_juillet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-2000:])
    return json.loads(lignes[0][len("##JSON##"):])


def test_page_livrable_finance_s_execute_sans_erreur():
    data = _run_page()

    assert data["errors"] == []
    metriques = {m[0]: m[1] for m in data["metrics"]}
    for libelle in ("Banque finale", "BFRE", "CA mensuel", "Coûts source", "Résultat net mensuel"):
        assert libelle in metriques


def test_le_bilan_detaille_est_lu():
    """Le bilan du mois doit alimenter le diagnostic (pas un bilan vide)."""
    data = _run_page()
    metriques = {m[0]: m[1] for m in data["metrics"]}

    def _nombre(texte):
        return float(str(texte).replace("€", "").replace(" ", "").replace(",", "."))

    # Banque 1 250 000 € et BFRE 807 725 € dans le jeu de données du fixture.
    assert _nombre(metriques["Banque finale"]) == pytest.approx(1250000.0)
    assert _nombre(metriques["BFRE"]) == pytest.approx(807725.0)
    # Un bilan vide aurait affiché un avertissement « Bilan détaillé vide ».
    assert not [w for w in data.get("warnings", []) if "Bilan détaillé vide" in w]


def test_les_marges_sont_produites_pour_toute_la_gamme():
    data = _run_page()
    tables = data.get("tables", [])
    marges = next((t for t in tables if t and "Produit" in t[0] and "Marge A" in t[0]), None)

    assert marges is not None, "aucun tableau de marges"
    assert len(marges) == 5
    produits = {ligne["Produit"] for ligne in marges}
    assert produits == {"Shorty 3", "Integral 3", "Shorty 5", "Integral 5", "Integral 7"}
    for ligne in marges:
        # Marge A = prix HT - coût variable (remise A nulle)
        assert ligne["Marge A"] == pytest.approx(ligne["Prix HT"] - ligne["Coût var."])
        assert ligne["Marge B"] < ligne["Marge A"]  # la remise B dégrade la marge


def test_le_cheque_source_des_couts_annonca_le_mois():
    data = _run_page()
    metriques = {m[0]: m[1] for m in data["metrics"]}

    assert "Août" in metriques["Coûts source"]


def test_la_page_degrade_sans_planter_si_les_tables_du_scraper_manquent():
    """Base fraîche (tables `erp_donnees` / `erp_etudes` absentes) : la page
    doit l'expliquer, pas planter sur une erreur SQL."""
    env = dict(os.environ)
    env["SIM_PAGE"] = "5_Livrable_Finance.py"
    env["SIM_TOUR"] = "8"
    env["SIM_SANS_EXTRACTIONS"] = "1"
    proc = subprocess.run(
        [sys.executable, "simulator_smoke_runner.py", "aout_sans_juillet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-2000:])
    data = json.loads(lignes[0][len("##JSON##"):])

    assert data["errors"] == []
    assert any("impossible" in w for w in data.get("warnings", []))
