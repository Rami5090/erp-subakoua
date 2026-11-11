"""Page « Rapport professionnel » : le livrable doit se produire de bout en bout.

La page est exécutée dans un sous-processus (banc d'essai sans Streamlit ni
MySQL) sur une année complète de données. On simule la saisie de l'équipe et le
clic sur « Générer », puis on ouvre le fichier Word réellement téléchargé.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent

SAISIE = {
    "rp_debut": "Année 1 - Janvier",
    "rp_fin": "Année 1 - Décembre",
    "rp_entreprise": "Néoprène & Co",
    "rp_membres": [
        {"Nom": "Alex Martin", "Rôle": "Responsable financier", "Missions": "Trésorerie et financement"},
        {"Nom": "Sam Leroy", "Rôle": "Responsable production et logistique", "Missions": "Capacité et MRP"},
    ],
    "rp_strategie": "Stratégie de volume : prix sous le marché, focalisation sur l'Integral 7.",
    "rp_m_alex_martin_forces": "Rigueur dans le suivi de trésorerie.",
}


def _run_page(tmp_path, scenario="rapport_annee", saisie=None, clic=True, extra_env=None):
    env = dict(os.environ)
    env.update({"SIM_PAGE": "8_Rapport_Professionnel.py", "SIM_DOWNLOAD_DIR": str(tmp_path),
                "SIM_DECISIONS": json.dumps(SAISIE if saisie is None else saisie, ensure_ascii=False)})
    if clic:
        env["SIM_CLICK"] = "Générer le rapport Word"
    env.update(extra_env or {})
    proc = subprocess.run([sys.executable, "simulator_smoke_runner.py", scenario], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-3000:])
    return json.loads(lignes[0][len("##JSON##"):])


@pytest.fixture(scope="module")
def execution(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("rapport")
    return _run_page(dossier), dossier


def test_la_page_s_execute_et_affiche_l_audit(execution):
    data, _ = execution
    assert data["errors"] == []
    metriques = {m[0]: m[1] for m in data["metrics"]}
    assert metriques["Chiffre d'affaires"].replace(" ", " ") == "15 272 032 €"
    assert metriques["Part de marché finale"].replace(" ", " ") == "15,20 %"
    assert data["images"] >= 8                                     # aperçu des graphiques


def test_le_rapport_word_est_genere_et_telechargeable(execution):
    data, dossier = execution
    docx = pytest.importorskip("docx")
    noms = [d["file_name"] for d in data["downloads"]]
    fichier = next(n for n in noms if n.endswith(".docx"))
    assert fichier.startswith("Rapport_professionnel_neoprene_co_")
    document = docx.Document(io.BytesIO((dossier / fichier).read_bytes()))
    titres = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert "7. Alex Martin — Responsable financier" in titres
    assert "8. Sam Leroy — Responsable production et logistique" in titres
    texte = "\n".join(p.text for p in document.paragraphs)
    assert "Stratégie de volume" in texte
    assert any(n.endswith(".json") for n in noms) and any(n.endswith(".md") for n in noms)


def test_le_brouillon_telecharge_se_recharge(execution):
    data, dossier = execution
    import rapport_engine as R
    fichier = next(d["file_name"] for d in data["downloads"] if d["file_name"].endswith(".json"))
    contenu = R.ContenuRapport.from_json((dossier / fichier).read_bytes())
    assert contenu.entreprise == "Néoprène & Co"
    assert [m.nom for m in contenu.membres] == ["Alex Martin", "Sam Leroy"]
    assert contenu.membres[0].forces == "Rigueur dans le suivi de trésorerie."


def test_la_page_signale_ce_qui_reste_a_completer(execution):
    data, _ = execution
    assert any("À COMPLÉTER" in w for w in data["warnings"])
    assert "Critères remplis" in {m[0] for m in data["metrics"]}


def test_la_page_degrade_sans_planter_sur_une_base_fraiche(tmp_path):
    """Tables du scraper absentes : la page le dit et produit quand même la trame du rapport."""
    data = _run_page(tmp_path, saisie={}, extra_env={"SIM_SANS_EXTRACTIONS": "1"})
    assert data["errors"] == []
    assert any("impossible" in w for w in data["warnings"])
    assert any(d["file_name"].endswith(".docx") for d in data["downloads"])
