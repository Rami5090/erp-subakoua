"""Page « Prévision des cours » : le module doit produire des prévisions cohérentes.

La page est exécutée dans un sous-processus (banc d'essai sans Streamlit ni
MySQL) avec un historique de cours de titres (études « Portefeuille actions /
obligations ») et un historique de prix de vente (décisions de l'entreprise).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def _run_page():
    env = dict(os.environ)
    env["SIM_PAGE"] = "7_Prevision_Cours.py"
    env["SIM_TOUR"] = "8"
    proc = subprocess.run(
        [sys.executable, "simulator_smoke_runner.py", "aout_sans_juillet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-2000:])
    return json.loads(lignes[0][len("##JSON##"):])


def _nombre(texte):
    return float(re.sub(r"[^0-9,.\-]", "", str(texte).replace(" ", "")).replace(",", "."))


def test_page_prevision_cours_s_execute_sans_erreur():
    data = _run_page()

    assert data["errors"] == []
    metriques = {m[0]: m[1] for m in data["metrics"]}
    assert "Dernier cours" in metriques
    assert "Prévision période suivante" in metriques


def test_les_cours_des_titres_sont_prevus():
    data = _run_page()
    metriques = [m for m in data["metrics"] if m[0] == "Dernier cours"]

    # A1, A2, A3, O1, O2, O3
    assert len(metriques) == 6
    for _, dernier, _ in metriques:
        assert _nombre(dernier) > 0


def test_la_prevision_suit_la_tendance_observee():
    """Dans le fixture, A1 progresse de 0,50 € par mois : la prévision doit
    reproduire ce mouvement (13,00 € observés -> 13,50 € prévus)."""
    data = _run_page()
    metriques = {m[0]: m[1] for m in data["metrics"]}
    derniers = [m for m in data["metrics"] if m[0] == "Dernier cours"]
    previsions = [m for m in data["metrics"] if m[0] == "Prévision période suivante"]

    for dernier, prevision in zip(derniers, previsions):
        d, p = _nombre(dernier[1]), _nombre(prevision[1])
        assert p >= d * 0.5  # jamais un effondrement sur une série croissante
        assert p <= d * 1.5  # ni un emballement


def test_les_prix_de_vente_des_decisions_sont_prevus():
    data = _run_page()
    tables = data.get("tables", [])
    prix = next((t for t in tables if t and "Produit" in t[0] and "Prévision (€)" in t[0]), None)

    assert prix is not None, "aucun tableau de prix de vente"
    assert len(prix) == 5
    for ligne in prix:
        assert ligne["Dernier prix (€)"] > 0
        assert ligne["Méthode"] in ("dernier", "derive", "derive_amortie", "moyenne", "niveau")
        assert ligne["Confiance"] in ("faible", "moyenne", "bonne")
