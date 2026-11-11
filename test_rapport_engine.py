"""Moteur du rapport professionnel : consolidation annuelle, sources et analyses.

Invariants protégés :
* aucune valeur inventée (une donnée absente reste absente et est signalée) ;
* les sources sont hiérarchisées (dossier comptable avant saisie manuelle,
  saisie 0 = « non saisi ») ;
* une seule étude « Ventes mensuelles » de décembre reconstitue toute l'année ;
* la décomposition prix / volume / mix est exacte (somme des effets = écart).
"""
import json

import pytest

import rapport_engine as R
from rapport_jeu_essai import ca_du_mois, jeu_essai_annee, pdm_du_mois, resultat_du_mois, unites_du_mois


@pytest.fixture(scope="module")
def jeu():
    return jeu_essai_annee()


@pytest.fixture(scope="module")
def audit(jeu):
    sources = R.indexer_sources(jeu["erp_donnees"], jeu["erp_etudes"], jeu["tables"])
    return R.construire_audit(sources, "Année 1 - Janvier", "Année 1 - Décembre", 3, "Néoprène & Co")


# --------------------------------------------------------------------------- indexation
def test_une_etude_non_lisible_est_ignoree():
    sources = R.indexer_sources(lignes_etudes=[
        {"id": 1, "periode": "Année 1 - Mars", "study_id": "monitoring", "read_allowed": 0,
         "payload": json.dumps({"marketShares": 99.0})},
    ])
    assert sources.etudes_de("Année 1 - Mars") == {}


def test_le_libelle_canonique_puis_la_ligne_la_plus_recente_l_emportent():
    lignes = [
        {"id": 1, "periode": "Année 1 - Mars", "study_id": "monitoring", "payload": json.dumps({"marketShares": 10.0})},
        {"id": 3, "periode": "Année 1 – Mars", "study_id": "monitoring", "payload": json.dumps({"marketShares": 30.0})},
        {"id": 2, "periode": "Année 1 - Mars", "study_id": "monitoring", "payload": json.dumps({"marketShares": 20.0})},
    ]
    sources = R.indexer_sources(lignes_etudes=lignes)
    # Libellé exact prioritaire sur la variante typographique, puis id le plus élevé.
    assert sources.etudes_de("Année 1 - Mars")["monitoring"]["marketShares"] == 20.0


def test_periodes_et_periode_par_defaut(jeu):
    sources = R.indexer_sources(jeu["erp_donnees"], jeu["erp_etudes"], jeu["tables"])
    assert sources.periodes()[0] == "Année 1 - Janvier"
    assert R.periode_par_defaut(sources) == ("Année 1 - Janvier", "Année 1 - Décembre")
    assert R.periodes_entre("Année 1 - Novembre", "Année 2 - Février") == [
        "Année 1 - Novembre", "Année 1 - Décembre", "Année 2 - Janvier", "Année 2 - Février"]


# --------------------------------------------------------------------------- ventes
def test_l_etude_ventes_de_decembre_reconstitue_toute_l_annee(jeu):
    sources = R.indexer_sources(lignes_etudes=jeu["erp_etudes"])
    ventes = R.ventes_publiees(sources)
    assert len(ventes) == 12
    valeurs, source = ventes["Année 1 - Mars"]
    assert valeurs["Integral 7"] == unites_du_mois(3)["MONO_F"]
    assert "déc. A1" in source


def test_la_ligne_du_mois_meme_est_preferee():
    def etude(periode, mois, ventes_i7):
        return {"periode": periode, "study_id": "ensaacvm", "payload": json.dumps({"sales": [
            {"month": mois, "monthlySalesByProducts": [{"productId": "MONO_F", "sales": ventes_i7}]}]})}
    sources = R.indexer_sources(lignes_etudes=[
        etude("Année 1 - Mars", 15, 100), etude("Année 1 - Juin", 15, 999),
    ])
    valeurs, source = R.ventes_publiees(sources)["Année 1 - Mars"]
    assert valeurs == {"Integral 7": 100}
    assert "mars A1" in source


# --------------------------------------------------------------------------- mois
def test_le_dossier_comptable_prime_sur_la_saisie_et_zero_signifie_non_saisi():
    sources = R.indexer_sources(
        lignes_etudes=[{"periode": "Année 1 - Avril", "study_id": "companyResults",
                        "payload": json.dumps({"turnover": 1000.0, "results": 50.0})}],
        tables={"Finances_Mensuelles": [
            {"Tour_ID": 4, "CA_Net": 777.0, "Resultat_Net": 0, "Disponibilites_Banque": 0},
            {"Tour_ID": 5, "CA_Net": 888.0, "Disponibilites_Banque": 0},
        ]},
    )
    avril = R.analyser_mois(sources, "Année 1 - Avril")
    assert avril.v(R.CA) == 1000.0 and "Dossier comptable" in avril.sources[R.CA]
    mai = R.analyser_mois(sources, "Année 1 - Mai")
    assert mai.v(R.CA) == 888.0 and "Saisie manuelle" in mai.sources[R.CA]
    assert R.TRESORERIE not in mai.valeurs          # 0 = non saisi : jamais lu comme un solde nul


def test_un_resultat_absent_du_compte_simplifie_n_est_pas_lu_comme_zero():
    sources = R.indexer_sources(lignes_etudes=[
        {"periode": "Année 1 - Avril", "study_id": "peeedscrsm", "payload": json.dumps({"biensLivraisons": 5000.0})}])
    avril = R.analyser_mois(sources, "Année 1 - Avril")
    assert avril.v(R.CA) == 5000.0
    assert R.RESULTAT not in avril.valeurs


def test_un_mois_sans_donnee_reste_vide():
    mois = R.analyser_mois(R.indexer_sources(), "Année 1 - Avril")
    assert not mois.renseigne and mois.valeurs == {}


# --------------------------------------------------------------------------- audit annuel
def test_synthese_annuelle_exacte(audit):
    s = audit.synthese
    assert s.n_mois == 12 and s.ca_mois == 12
    assert s.ca_total == pytest.approx(sum(ca_du_mois(m) for m in range(1, 13)))
    assert s.resultat_total == pytest.approx(sum(resultat_du_mois(m) for m in range(1, 13)))
    assert s.mois_deficitaires == ("Année 1 - Janvier",)
    assert s.pdm_debut == ("Année 1 - Janvier", pdm_du_mois(1))
    assert s.pdm_fin == ("Année 1 - Décembre", pdm_du_mois(12))
    assert s.acquisitions_total == {"Découpe": 2.0, "Assemblage": 1.0}
    assert s.tresorerie_ouverture == ("Année 1 - Janvier", 1_200_000.0)
    assert s.ventes_totales == pytest.approx(sum(sum(unites_du_mois(m).values()) for m in range(1, 13)))


def test_les_capitaux_propres_sont_lus_dans_l_etude_resultats(audit):
    """Régression : une étude « Bilan » sans total des capitaux propres ne doit pas masquer
    celui publié par l'étude « Résultats » (finance_engine.extract_capitaux_propres)."""
    assert audit.synthese.capitaux_propres_fin is not None
    assert audit.synthese.capitaux_propres_fin > audit.synthese.capitaux_propres_debut


def test_analyse_par_produit(audit):
    lignes = {l.produit: l for l in audit.produits}
    assert sum(l.part_volumes for l in audit.produits) == pytest.approx(100.0)
    i7 = lignes["Integral 7"]
    assert i7.prix_debut == 375.0 and i7.prix_fin == 360.0
    assert i7.marge_unitaire == pytest.approx(360.0 - 155.0)
    assert i7.indice_prix == pytest.approx(360.0 / 370.8)


def test_decomposition_prix_volume_mix_exacte():
    prix_a = {"Shorty 3": 100.0, "Integral 7": 300.0}
    prix_b = {"Shorty 3": 110.0, "Integral 7": 280.0}
    d = R.decomposer_ecart_ca("Année 1 - Janvier", prix_a, {"Shorty 3": 10, "Integral 7": 10},
                              "Année 1 - Mars", prix_b, {"Shorty 3": 5, "Integral 7": 20})
    assert d.ca_a == 4000.0 and d.ca_b == 6150.0
    assert d.effet_volume + d.effet_mix + d.effet_prix == pytest.approx(d.ecart)
    assert d.effet_volume == pytest.approx(5 * 200.0)         # +5 unités au prix moyen initial (200 €)
    assert d.effet_mix == pytest.approx(5 * 100.0 + 20 * 300.0 - 25 * 200.0)
    assert d.effet_prix == pytest.approx(5 * 10.0 + 20 * -20.0)


def test_decomposition_impossible_sans_prix():
    assert R.decomposer_ecart_ca("Année 1 - Janvier", {}, {"Shorty 3": 10},
                                 "Année 1 - Mars", {"Shorty 3": 5.0}, {"Shorty 3": 12}) is None


def test_chronologie_des_decisions(audit):
    textes = [(e.periode, e.texte) for e in audit.chronologie]
    assert ("Année 1 - Mars", "Achat de 2 machine(s) — atelier Découpe") in textes
    assert any(p == "Année 1 - Avril" and "Integral 7" in t and "360" in t for p, t in textes)
    assert any(p == "Année 1 - Mars" and "nouvel emprunt" in t for p, t in textes)


def test_concurrence_et_constats(audit):
    assert [int(r["entreprise"]) for r in audit.concurrence][:2] == [1, 3]
    textes = " ".join(k.texte for k in audit.constats)
    assert "classée 2ᵉ sur 6" in textes
    assert "1 mois déficitaire" in textes
    assert all(k.nature in {"force", "faiblesse", "vigilance", "info"} for k in audit.constats)
    assert any("Porter" in l for l in audit.lecture_strategique)


def test_aucun_constat_invente_sans_donnees():
    vide = R.construire_audit(R.indexer_sources(), "Année 1 - Janvier", "Année 1 - Mars")
    assert vide.synthese.ca_total is None and vide.synthese.resultat_total is None
    assert all(k.theme == "Données" for k in vide.constats)
    assert {m.element for m in vide.manques} >= {"Chiffre d'affaires mensuel", "Ventes par produit"}


# --------------------------------------------------------------------------- formats & contenu
def test_formats_francais():
    assert R.fmt_euros(1186400.4) == "1 186 400 €"
    assert R.fmt_pct(12.345, 1) == "12,3 %"
    assert R.fmt_euros_signe(-25875) == "-25 875 €"
    assert R.fmt_euros_signe(1500) == "+1 500 €"
    assert R.fmt_compact_euros(1_186_400) == "1,19 M€"
    assert R.fmt_euros(None) == "—"
    assert R.mois_court("Année 1 - Février") == "févr. A1"


def test_brouillon_json_aller_retour():
    contenu = R.ContenuRapport(entreprise="Néoprène & Co", strategie="Volume.", outils_gp=("Matrice RACI",),
                               membres=(R.Membre(nom="Alex", role="DAF", outils_erp=("Livrable Finance mensuel",),
                                                 forces="Rigueur"),))
    relu = R.ContenuRapport.from_json(contenu.to_json())
    assert relu == contenu
    with pytest.raises(ValueError):
        R.ContenuRapport.from_json("pas du json")


def test_controle_de_completude(audit):
    vide = R.controle_completude(audit, R.ContenuRapport())
    assert any(c.critere == "Membres de l'équipe" and c.statut == "manquant" for c in vide)
    plein = R.ContenuRapport(
        entreprise="N&Co", strategie="mot " * 200, methodes="mot " * 100, prise_de_recul="mot " * 200,
        conclusion="mot " * 80,
        membres=(R.Membre(nom="Alex", role="DAF", outils="ERP", forces="a", faiblesses="b", reussites="c", erreurs="d"),))
    criteres = R.controle_completude(audit, plein, logo=True, nb_graphiques=8)
    assert all(c.statut == "ok" for c in criteres), [c for c in criteres if c.statut != "ok"]
    assert R.taux_completude(criteres) == 1.0
    assert R.taux_completude(vide) < 1.0


def test_perimetre_d_un_role():
    assert R.perimetre_role("Responsable financier")[:2] == (R.CA, R.RESULTAT)
    assert R.EFFECTIFS in R.perimetre_role("Responsable RH")
    assert R.perimetre_role("") == ()
