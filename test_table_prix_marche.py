"""Lecture des tables du module expert-comptable (bilan et prix de marché).

La structure des extractions du portail varie : l'onglet « Expert-comptable »
peut être un dictionnaire **ou une liste de tableaux** (cas observé sur les
données réelles). Les tables doivent donc être retrouvées par leur contenu,
jamais par un chemin supposé.
"""
from livrable_finance import prix_marche_par_produit, table_bilan_detaille, table_prix_marche

LIGNES = [
    {"Combinaisons": "1", "Prix HT (€)": 130},
    {"Combinaisons": "2", "Prix HT (€)": 205},
    {"Combinaisons": "3", "Prix HT (€)": 240},
    {"Combinaisons": "4", "Prix HT (€)": 310},
    {"Combinaisons": "5", "Prix HT (€)": 360},
]

#: Structure observée sur les données réelles : l'onglet est une liste.
ONGLET_LISTE = {
    "Détail des comptes": {
        "Expert-comptable": [
            {"Ventes sur le marché concurrentiel": LIGNES},
        ],
    },
}

#: Structure historiquement rencontrée : l'onglet est un dictionnaire.
ONGLET_DICTIONNAIRE = {
    "Détail des comptes": {
        "Expert-comptable": {
            "Ventes sur le marché concurrentiel": LIGNES,
        },
    },
}


def test_table_prix_lue_dans_un_onglet_liste():
    assert table_prix_marche(ONGLET_LISTE) == LIGNES


def test_table_prix_lue_dans_un_onglet_dictionnaire():
    assert table_prix_marche(ONGLET_DICTIONNAIRE) == LIGNES


def test_une_onglet_qui_n_est_pas_un_dictionnaire_ne_plante_pas():
    assert table_prix_marche({"Détail des comptes": {"Expert-comptable": [1, 2, 3]}}) == []
    assert table_prix_marche({"Détail des comptes": "texte"}) == []
    assert table_prix_marche(None) == []
    assert table_prix_marche({}) == []


def test_prix_par_produit_sur_les_deux_structures():
    attendu = {
        "Shorty 3": 130.0, "Integral 3": 205.0, "Shorty 5": 240.0,
        "Integral 5": 310.0, "Integral 7": 360.0,
    }
    assert prix_marche_par_produit(ONGLET_LISTE) == attendu
    assert prix_marche_par_produit(ONGLET_DICTIONNAIRE) == attendu


def test_prix_par_produit_suit_le_rang_meme_si_les_prix_evoluent():
    """Un prix de marché qui bouge ne doit pas faire disparaître la série."""
    lignes = [
        {"Combinaisons": str(i + 1), "Prix HT (€)": p}
        for i, p in enumerate([135, 210, 245, 315, 365])
    ]
    payload = {"Détail des comptes": {"Expert-comptable": [{"Ventes sur le marché concurrentiel": lignes}]}}

    prix = prix_marche_par_produit(payload)

    assert prix["Shorty 3"] == 135.0
    assert prix["Integral 7"] == 365.0
    assert len(prix) == 5


def test_prix_absents_ne_produisent_aucun_mapping():
    payload = {"Détail des comptes": {"Expert-comptable": [{"Ventes sur le marché concurrentiel": []}]}}

    assert prix_marche_par_produit(payload) == {}


# --------------------------------------------------------------------------- bilan
BILAN = [
    {"Actif": "Banque", "Passif": "", "Net exercice N": "1250000", "Exercice N": ""},
    {"Actif": "", "Passif": "Total capitaux propres", "Net exercice N": "", "Exercice N": "2000000"},
]


def test_bilan_trouve_dans_un_onglet_liste():
    payload = {"Synthèse": {"Expert-comptable #3": [{"Tableau_1": BILAN}]}}

    assert table_bilan_detaille(payload) == BILAN


def test_bilan_trouve_dans_un_onglet_dictionnaire():
    payload = {"Synthèse": {"Expert-comptable #3": {"Tableau_1": BILAN}}}

    assert table_bilan_detaille(payload) == BILAN


def test_bilan_absent_ne_produit_aucune_ligne():
    assert table_bilan_detaille({"Synthèse": {}}) == []
    assert table_bilan_detaille({"Synthèse": "texte"}) == []
    assert table_bilan_detaille(None) == []
