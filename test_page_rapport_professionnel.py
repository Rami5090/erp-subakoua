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


# --------------------------------------------------------------------------- rédaction assistée
#: Saisie minimale : un paragraphe de contexte et l'équipe, aucun texte rédigé.
SAISIE_REDACTION = {
    "rp_debut": "Année 1 - Janvier", "rp_fin": "Année 1 - Décembre", "rp_entreprise": "Néoprène & Co",
    "rp_membres": SAISIE["rp_membres"],
    "rp_contexte": "Stratégie de volume : prix sous le marché, focalisation sur l'Integral 7. Alex a piloté la "
                   "trésorerie et l'emprunt de mars ; Sam a géré la saturation de l'atelier de découpe.",
}


@pytest.fixture(scope="module")
def redaction(tmp_path_factory):
    """Utilisateur connecté : « Rédiger les paragraphes » puis « Générer le rapport Word » (API Claude simulée)."""
    dossier = tmp_path_factory.mktemp("redaction")
    data = _run_page(dossier, saisie=SAISIE_REDACTION, extra_env={
        "SIM_EDITOR": "1", "SIM_ANTHROPIC": "1", "SIM_CLICK": "Rédiger les paragraphes|Générer le rapport Word"})
    return data, dossier


def test_la_redaction_envoie_le_contexte_et_les_chiffres_de_l_audit(redaction):
    data, _ = redaction
    assert data["errors"] == []
    (requete,) = data["redaction"]
    assert (requete["model"], requete["effort"], requete["format"]) == ("claude-opus-5-5", "medium", "json_schema")
    assert requete["fallbacks"] == "default" and requete["betas"] == ["server-side-fallback-2026-07-01"]
    assert requete["system_cache"] is True
    assert SAISIE_REDACTION["rp_contexte"] in requete["message"]
    assert "15 272 032 €" in requete["message"].replace(" ", " ")  # CA de l'audit, format du Word
    assert list(requete["schema"]["properties"]["membres"]["properties"]) == ["membre_1", "membre_2"]


def test_les_paragraphes_rediges_remplissent_le_rapport_word(redaction):
    data, dossier = redaction
    docx = pytest.importorskip("docx")
    assert any("21 champ(s) rempli(s)" in s for s in data["success"])   # 7 collectifs + 2 × 7 individuels
    fichier = next(d["file_name"] for d in data["downloads"] if d["file_name"].endswith(".docx"))
    document = docx.Document(io.BytesIO((dossier / fichier).read_bytes()))
    texte = "\n".join(p.text for p in document.paragraphs)
    assert "Texte rédigé pour collectif.synthese" in texte
    assert "Texte rédigé pour membres.membre_2.bilan" in texte
    assert any("2 passage(s) « [À COMPLÉTER : …] »" in i for i in data["infos"])


def test_un_chiffre_absent_de_l_audit_est_signale(redaction):
    data, _ = redaction
    alertes = [w.replace(" ", " ") for w in data["warnings"] if "à vérifier" in w]
    assert len(alertes) == 1 and "99,9 %" in alertes[0] and "15 272 032" not in alertes[0]


def test_le_contexte_et_les_textes_rediges_sont_dans_le_brouillon(redaction):
    data, dossier = redaction
    import rapport_engine as R
    fichier = next(d["file_name"] for d in data["downloads"] if d["file_name"].endswith(".json"))
    contenu = R.ContenuRapport.from_json((dossier / fichier).read_bytes())
    assert contenu.contexte == SAISIE_REDACTION["rp_contexte"]
    assert contenu.synthese.startswith("Texte rédigé pour collectif.synthese")


def test_un_visiteur_ne_peut_pas_lancer_la_redaction(tmp_path):
    data = _run_page(tmp_path, saisie=SAISIE_REDACTION, clic=False, extra_env={
        "SIM_EDITOR": "0", "SIM_ANTHROPIC": "1", "SIM_CLICK": "Rédiger les paragraphes"})
    assert data["errors"] == []
    assert data["redaction"] == []                                    # bouton désactivé : aucun appel facturé
    assert any("Réservé aux utilisateurs connectés" in i for i in data["infos"])


def test_un_refus_du_modele_n_ecrit_aucun_texte(tmp_path):
    data = _run_page(tmp_path, saisie=SAISIE_REDACTION, extra_env={
        "SIM_EDITOR": "1", "SIM_ANTHROPIC": "1", "SIM_ANTHROPIC_ARRET": "refusal",
        "SIM_CLICK": "Rédiger les paragraphes|Générer le rapport Word"})
    assert any("déclinée par les garde-fous" in e for e in data["errors"])
    assert len(data["redaction"]) == 1 and not any("Dernière rédaction" in s for s in data["success"])
    import rapport_engine as R
    fichier = next(d["file_name"] for d in data["downloads"] if d["file_name"].endswith(".json"))
    contenu = R.ContenuRapport.from_json((tmp_path / fichier).read_bytes())
    assert contenu.synthese == "" and all(m.bilan == "" for m in contenu.membres)   # rien n'a été écrit


def test_le_mode_manuel_integre_une_reponse_collee_sans_cle_api(tmp_path):
    """Visiteur, sans clé API : la demande se copie (ou se télécharge), la réponse de claude.ai se colle."""
    import rapport_engine as R
    individuels = [c for c, _, _ in R.CHAMPS_INDIVIDUELS]
    reponse = {
        "collectif": {c: f"Texte collé pour {c}." for c, _, _ in R.CHAMPS_COLLECTIFS},
        "membres": {  # ordre inversé : chaque membre est reconnu à son nom
            "membre_1": {"nom": "Sam Leroy", **{c: f"Texte de Sam pour {c}." for c in individuels}},
            "membre_2": {"nom": "Alex Martin", **{c: f"Texte d'Alex pour {c}." for c in individuels}},
        },
    }
    reponse["collectif"]["synthese"] += " Part de marché de 88,8 %."
    colle = "Voici les textes :\n```json\n" + json.dumps(reponse, ensure_ascii=False, indent=2) + "\n```\nBon courage !"
    saisie = {**SAISIE_REDACTION, "rp_red_mode": "Manuel : claude.ai, sans clé API", "rp_red_reponse": colle}

    data = _run_page(tmp_path, saisie=saisie, extra_env={
        "SIM_EDITOR": "0", "SIM_CLICK": "Intégrer la réponse|Générer le rapport Word"})

    assert data["errors"] == [] and data["redaction"] == []           # aucun appel à l'API
    assert any("21 champ(s) rempli(s) par claude.ai (mode manuel)" in s for s in data["success"])
    assert any("à vérifier" in w and "88,8 %" in w.replace(" ", " ") for w in data["warnings"])
    demande = (tmp_path / "demande_redaction_rapport.txt").read_text(encoding="utf-8")
    assert SAISIE_REDACTION["rp_contexte"] in demande and '"nom": "Sam Leroy"' in demande
    fichier = next(d["file_name"] for d in data["downloads"] if d["file_name"].endswith(".json"))
    contenu = R.ContenuRapport.from_json((tmp_path / fichier).read_bytes())
    assert contenu.synthese.startswith("Texte collé pour synthese.")
    assert [m.bilan for m in contenu.membres] == ["Texte d'Alex pour bilan.", "Texte de Sam pour bilan."]


def test_la_page_degrade_sans_planter_sur_une_base_fraiche(tmp_path):
    """Tables du scraper absentes : la page le dit et produit quand même la trame du rapport."""
    data = _run_page(tmp_path, saisie={}, extra_env={"SIM_SANS_EXTRACTIONS": "1"})
    assert data["errors"] == []
    assert any("impossible" in w for w in data["warnings"])
    assert any(d["file_name"].endswith(".docx") for d in data["downloads"])
