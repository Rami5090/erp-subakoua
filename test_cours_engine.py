"""Prévision des cours : méthode retenue, intervalle et garde-fous.

Invariants protégés ici :
  * la méthode est choisie par **validation hors-échantillon**, pas par un
    réglage arbitraire ;
  * une série mal ordonnée ou trop courte ne produit **jamais** de chiffre
    inventé : l'avertissement le dit ;
  * la bande affichée est une **bande d'erreur empirique** (erreur du
    backtest), pas un intervalle de confiance statistique.
"""
import pytest

from cours_engine import (
    CoursForecast,
    PricePoint,
    comparer_cours,
    extraire_cours_titres,
    forecast_cours,
    forecast_cours_set,
    historique_cours,
    normaliser_cle_cours,
)


def _serie(valeurs, prefixe="Année 1 - "):
    mois = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
            "Septembre", "Octobre", "Novembre", "Décembre"]
    return [PricePoint(periode=f"{prefixe}{mois[i]}", valeur=v) for i, v in enumerate(valeurs)]


def test_une_serie_croissante_est_prevue_a_la_hausse():
    prevision = forecast_cours("a1", "Action A1", _serie([10, 11, 12, 13, 14]))

    assert prevision.prevision is not None
    assert prevision.prevision > prevision.dernier
    assert prevision.confiance in ("faible", "moyenne", "bonne")


def test_une_serie_plate_est_prevue_stable():
    prevision = forecast_cours("a1", "Action A1", _serie([12, 12, 12, 12, 12]))

    assert prevision.prevision == pytest.approx(12.0)
    assert prevision.derive_relative == pytest.approx(0.0)


def test_une_serie_decroissante_est_prevue_a_la_baisse():
    prevision = forecast_cours("a1", "Action A1", _serie([20, 19, 18, 17, 16]))

    assert prevision.prevision < prevision.dernier


def test_une_serie_trop_courte_ne_produit_aucune_prevision():
    prevision = forecast_cours("a1", "Action A1", _serie([10]))

    assert prevision.prevision is None
    assert prevision.confiance == "faible"
    assert any("Historique insuffisant" in w for w in prevision.avertissements)


def test_une_serie_vide_ne_produit_aucune_prevision():
    prevision = forecast_cours("a1", "Action A1", [])

    assert prevision.prevision is None
    assert prevision.avertissements


def test_la_serie_est_ordonnee_chronologiquement():
    """La base ne garantit pas l'ordre : une série inversée fausserait la prévision."""
    points = [
        PricePoint(periode="Année 1 - Mars", valeur=12.0),
        PricePoint(periode="Année 1 - Janvier", valeur=10.0),
        PricePoint(periode="Année 1 - Février", valeur=11.0),
    ]
    prevision = forecast_cours("a1", "Action A1", points)

    assert [p.valeur for p in prevision.serie] == [10.0, 11.0, 12.0]
    assert prevision.dernier == 12.0


def test_la_methode_est_choisie_par_backtest():
    """Sur une série à paliers, la méthode retenue doit être celle qui
    reconstruit le mieux l'historique en validation hors-échantillon."""
    prevision = forecast_cours("a1", "Action A1", _serie([10, 10, 10, 14, 14, 14, 14]))

    assert prevision.methode in ("dernier", "derive", "derive_amortie", "moyenne", "niveau")
    assert prevision.erreur_moyenne is not None
    assert prevision.erreur_moyenne >= 0


def test_la_bande_erreur_est_centree_sur_la_prevision():
    prevision = forecast_cours("a1", "Action A1", _serie([10, 12, 11, 13, 12, 14]))

    assert prevision.borne_basse is not None and prevision.borne_haute is not None
    assert prevision.borne_basse <= prevision.prevision <= prevision.borne_haute


def test_une_prevision_negative_est_ramenee_a_zero():
    """Effondrement marqué : la tendance extrapolée tombe sous zéro."""
    prevision = forecast_cours("a1", "Action A1", _serie([10, 9, 8, 7, 0.5]))

    assert prevision.prevision is not None
    assert prevision.prevision >= 0
    if prevision.prevision == 0:
        assert any("négative" in w for w in prevision.avertissements)


def test_le_set_de_previsions_couvre_toutes_cles():
    resultat = forecast_cours_set({
        "a1": ("Action A1", _serie([10, 11, 12])),
        "o1": ("Obligation O1", _serie([100, 101, 102])),
    })

    assert len(resultat.cours) == 2
    assert resultat.par_cle("a1") is not None
    assert resultat.par_cle("a1").libelle == "Action A1"
    assert resultat.par_cle("inconnu") is None


def test_comparer_cours_donne_l_ecart_en_pourcentage():
    serie = _serie([10, 11, 12])
    ecart = comparer_cours(serie, 13.2)

    assert ecart == pytest.approx(10.0, abs=0.01)
    assert comparer_cours(serie, None) is None
    assert comparer_cours([], 10.0) is None


# --------------------------------------------------------------------------- lecture des études
def test_cours_titres_lus_d_un_payload_plat():
    payload = {"clazz": "x", "price": 0, "actionA1": 12.5, "actionA2": 30.0, "actionA3": 45.5}

    cours = extraire_cours_titres(payload)

    assert cours == {"A1": 12.5, "A2": 30.0, "A3": 45.5}


def test_cours_titres_lus_d_un_payload_imbrique():
    payload = {"actionA1": {"price": 12.5, "variation": -0.4}}

    assert extraire_cours_titres(payload) == {"A1": 12.5}


def test_cours_titres_lus_d_une_liste():
    payload = [{"name": "actionA2", "price": 30.0}, {"name": "obligationO1", "price": 100.0}]

    cours = extraire_cours_titres(payload)

    assert cours == {"A2": 30.0, "O1": 100.0}


def test_un_payload_inconnu_ne_produit_aucun_cours():
    assert extraire_cours_titres({"totalReceipts": 12, "finalBankAmount": 3}) == {}
    assert extraire_cours_titres(None) == {}
    assert extraire_cours_titres("texte") == {}


def test_normalisation_des_cles_de_cours():
    assert normaliser_cle_cours("actionA1") == "A1"
    assert normaliser_cle_cours("obligationO3") == "O3"
    assert normaliser_cle_cours("totalReceipts") is None


def test_historique_de_cours_construit_depuis_les_etudes():
    etudes = {
        "Année 1 - Janvier": {"pebaifpuao": {"actionA1": 10.0, "actionA2": 20.0}},
        "Année 1 - Février": {"pebaifpuao": {"actionA1": 11.0, "actionA2": 20.5}},
        "Année 1 - Mars": {"pebaifpuoo": {"obligationO1": 100.0}},
    }

    serie = historique_cours(etudes)

    assert [p.valeur for p in serie["A1"]] == [10.0, 11.0]
    assert [p.valeur for p in serie["A2"]] == [20.0, 20.5]
    assert [p.valeur for p in serie["O1"]] == [100.0]
    # L'historique est ordonné chronologiquement
    assert serie["A1"][0].periode == "Année 1 - Janvier"


def test_historique_accepte_les_cles_etudes_prefixees():
    """Le cache interne des études préfixe les clés par « etude_ »."""
    etudes = {
        "Année 1 - Janvier": {"etude_pebaifpuao": {"actionA1": 10.0}},
        "Année 1 - Février": {"etude_pebaifpuao": {"actionA1": 11.0}},
    }

    serie = historique_cours(etudes)

    assert [p.valeur for p in serie["A1"]] == [10.0, 11.0]


def test_historique_trie_les_periodes_normalisees():
    """Le cache des études normalise les libellés (« annee 1 - aout ») : sans
    tri chronologique correct, l'historique serait dans le désordre."""
    etudes = {
        "annee 1 - aout": {"etude_pebaifpuao": {"actionA1": 13.0}},
        "annee 1 - fevrier": {"etude_pebaifpuao": {"actionA1": 10.0}},
        "annee 1 - juillet": {"etude_pebaifpuao": {"actionA1": 12.5}},
    }

    serie = historique_cours(etudes)

    assert [p.valeur for p in serie["A1"]] == [10.0, 12.5, 13.0]
