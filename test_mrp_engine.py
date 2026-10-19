"""Tests du moteur MRP (besoins bruts → besoins nets → coûts).

Le moteur est pur : aucun Streamlit, aucun SQL. Les tests vérifient la
logique de calcul, en particulier l'arbitrage coût d'achat / coût de
possession qui est l'objectif de la jauge de stock de sécurité.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mrp_engine


NOMENCLATURE = {
    "Shorty 3": {"Néoprène 3": 1.0, "Fermetures": 1.0},
    "Integral 3": {"Néoprène 3": 2.0, "Fermetures": 1.0},
}

TARIFS = {
    "Néoprène 3": {"fournisseur": "Neosea", "prix": 6.0, "delai_mois": 1, "tarifaire": True, "origine": "tarifaire"},
    "Fermetures": {"fournisseur": "Zip", "prix": 2.0, "delai_mois": 2, "tarifaire": True, "origine": "tarifaire"},
}

VIEILLISSEMENT = {"Néoprène 3": 4.0, "Fermetures": 4.0}


def _params(**kwargs):
    defauts = dict(
        nomenclature=NOMENCLATURE,
        stocks_ouverture={},
        stocks_securite_base={},
        tarifs=TARIFS,
        taux_vieillissement_mois=VIEILLISSEMENT,
        volumes={"Shorty 3": 100, "Integral 3": 50},
        part_couverture_pct=100.0,
        coeff_stock_secu_pct=0.0,
        taux_possession_annuel_pct=8.0,
    )
    defauts.update(kwargs)
    return mrp_engine.ParametresMRP(**defauts)


class TestBesoinsBruts(unittest.TestCase):
    def test_somme_les_coefficients_par_matiere(self):
        besoins = mrp_engine.besoins_bruts(NOMENCLATURE, {"Shorty 3": 100, "Integral 3": 50})
        # Néoprène 3 : 100 x 1.0 + 50 x 2.0 = 200 ; Fermetures : 100 + 50 = 150
        self.assertAlmostEqual(besoins["Néoprène 3"], 200.0)
        self.assertAlmostEqual(besoins["Fermetures"], 150.0)

    def test_part_couverture_reduit_le_besoin(self):
        besoins = mrp_engine.besoins_bruts(NOMENCLATURE, {"Shorty 3": 100, "Integral 3": 50}, 50.0)
        self.assertAlmostEqual(besoins["Néoprène 3"], 100.0)
        self.assertAlmostEqual(besoins["Fermetures"], 75.0)

    def test_produit_absent_ignore(self):
        besoins = mrp_engine.besoins_bruts(NOMENCLATURE, {"Produit Inconnu": 999})
        self.assertEqual(besoins, {})

    def test_volume_negatif_ignore(self):
        besoins = mrp_engine.besoins_bruts(NOMENCLATURE, {"Shorty 3": -10})
        self.assertAlmostEqual(besoins["Néoprène 3"], 0.0)


class TestStockSecurite(unittest.TestCase):
    def test_cible_erp_est_un_plancher(self):
        # La cible ERP (100) est respectée même avec un coefficient à 0 %.
        valeur = mrp_engine.stock_securite_effectif(100.0, 500.0, 0.0)
        self.assertAlmostEqual(valeur, 100.0)

    def test_coefficient_ajoute_au_plancher(self):
        valeur = mrp_engine.stock_securite_effectif(100.0, 500.0, 50.0)
        self.assertAlmostEqual(valeur, 350.0)  # 100 + 500 x 0.5

    def test_cible_erp_absente(self):
        valeur = mrp_engine.stock_securite_effectif(0.0, 500.0, 100.0)
        self.assertAlmostEqual(valeur, 500.0)

    def test_valeurs_negatives_neutralisees(self):
        self.assertAlmostEqual(mrp_engine.stock_securite_effectif(-10.0, 100.0, -5.0), 0.0)


class TestCalculMrp(unittest.TestCase):
    def test_besoin_net_sans_stock(self):
        resultat = mrp_engine.calculer_mrp(_params())
        neopren = next(l for l in resultat.lignes if l.matiere == "Néoprène 3")
        self.assertAlmostEqual(neopren.besoin_brut, 200.0)
        self.assertAlmostEqual(neopren.stock_ouverture, 0.0)
        self.assertAlmostEqual(neopren.besoin_net, 200.0)
        self.assertAlmostEqual(neopren.montant_ht, 200.0 * 6.0)

    def test_stock_ouverture_reduit_le_besoin_net(self):
        resultat = mrp_engine.calculer_mrp(_params(stocks_ouverture={"Néoprène 3": 50.0}))
        neopren = next(l for l in resultat.lignes if l.matiere == "Néoprène 3")
        self.assertAlmostEqual(neopren.besoin_net, 150.0)

    def test_stock_suffisant_annule_le_besoin_net(self):
        resultat = mrp_engine.calculer_mrp(_params(stocks_ouverture={"Néoprène 3": 999.0}))
        neopren = next(l for l in resultat.lignes if l.matiere == "Néoprène 3")
        self.assertAlmostEqual(neopren.besoin_net, 0.0)

    def test_stock_final_et_couverture(self):
        resultat = mrp_engine.calculer_mrp(_params(stocks_ouverture={"Néoprène 3": 50.0}))
        neopren = next(l for l in resultat.lignes if l.matiere == "Néoprène 3")
        # stock final = max(ouverture - brut, sécurité) = max(50 - 200, 0) = 0
        self.assertAlmostEqual(neopren.stock_final, 0.0)
        self.assertAlmostEqual(neopren.couverture_mois, 0.0)

    def test_couverture_nulle_si_pas_de_consommation(self):
        resultat = mrp_engine.calculer_mrp(_params(volumes={}))
        self.assertEqual(resultat.lignes, ())

    def test_cout_total_achat_plus_possession(self):
        resultat = mrp_engine.calculer_mrp(_params(taux_possession_annuel_pct=12.0))
        attendu = resultat.cout_achat_ht + resultat.cout_possession_mensuel
        self.assertAlmostEqual(resultat.cout_total_mensuel, attendu)

    def test_alerte_sur_matiere_non_chiffrable(self):
        params = _params(tarifs={})
        resultat = mrp_engine.calculer_mrp(params)
        self.assertTrue(any("non chiffrable" in a for a in resultat.alertes))

    def test_valeur_a_risque(self):
        # Un stock final très supérieur à ce qui peut être consommé avant
        # péremption est signalé comme capital à risque.
        resultat = mrp_engine.calculer_mrp(
            _params(stocks_ouverture={"Néoprène 3": 10_000.0}, coeff_stock_secu_pct=0.0)
        )
        neopren = next(l for l in resultat.lignes if l.matiere == "Néoprène 3")
        # consommable avant péremption = 200 x 4 = 800 ; stock final = 9 800
        self.assertAlmostEqual(neopren.volume_a_risque, 9_000.0)
        self.assertAlmostEqual(neopren.valeur_a_risque, 9_000.0 * 6.0)
        self.assertGreater(resultat.valeur_a_risque, 0.0)


class TestComparerScenarios(unittest.TestCase):
    def test_le_scenario_le_moins_cher_est_recommande(self):
        params = _params()
        resultats = mrp_engine.comparer_scenarios(params, [
            mrp_engine.Scenario("A", 0.0, 100.0),
            mrp_engine.Scenario("B", 100.0, 100.0),
        ])
        self.assertEqual(len(resultats), 2)
        recos = [r for r in resultats if r["recommandé"]]
        self.assertEqual(len(recos), 1)
        # Un stock de sécurité plus élevé coûte plus cher à l'achat.
        self.assertEqual(recos[0]["libelle"], "A")
        self.assertGreater(resultats[1]["cout_achat_ht"], resultats[0]["cout_achat_ht"])

    def test_un_scenario_sans_reco(self):
        resultats = mrp_engine.comparer_scenarios(_params(), [])
        self.assertEqual(resultats, [])


if __name__ == "__main__":
    unittest.main()
