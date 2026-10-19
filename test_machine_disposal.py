"""Cession de machines : parc, encaissement et effet sur la capacité.

Invariants protégés ici :
  * on ne cède jamais plus de machines qu'on n'en possède, et le parc ne
    descend jamais sous zéro — toute limitation est signalée ;
  * seule une cession réellement retenue produit de l'argent ;
  * l'encaissement d'une cession est TTC et entre dans le flux
    d'investissement, sans toucher au chiffre d'affaires ;
  * une cession réduit la capacité servie, mais ne rend pas le scénario
    irréalisable tant que l'atelier reste alimenté en machines.
"""
from decimal import Decimal

import pandas as pd

from constants import PRIX_CESSION_MACHINES, TVA_STANDARD
from finance_engine import (
    calculate_machine_disposal,
    calculate_projection,
    calculate_vat_projection,
)
from optimizer_engine import (
    Calibration,
    CapacityProfile,
    apply_fleet_decisions,
    evaluate_scenario,
)

PARC = {"Decoupe": 10, "Assemblage": 16, "Conditionnement": 4}


# --------------------------------------------------------------------------- parc
def test_cession_is_capped_to_the_machines_actually_owned():
    update = apply_fleet_decisions(PARC, cessions={"Decoupe": 25, "Conditionnement": 2})

    assert update.parc["Decoupe"] == 0
    assert update.cessions_retenues["Decoupe"] == 10
    assert update.parc["Conditionnement"] == 2
    assert any("Decoupe" in w for w in update.warnings)


def test_fleet_never_goes_below_zero_and_net_purchases():
    update = apply_fleet_decisions(
        {"Decoupe": 1, "Assemblage": 3, "Conditionnement": 0},
        {"Decoupe": 2, "Assemblage": 0, "Conditionnement": 0},
        {"Decoupe": 5, "Assemblage": 1, "Conditionnement": 1},
    )

    assert update.parc["Decoupe"] == 2  # 1 + 2 achetées − 1 cédée (et non 1 + 2 − 5)
    assert update.parc["Assemblage"] == 2
    assert update.parc["Conditionnement"] == 0  # parc déjà vide : cession sans effet
    assert update.cessions_retenues["Decoupe"] == 1
    assert update.cessions_retenues["Assemblage"] == 1
    assert update.cessions_retenues["Conditionnement"] == 0


def test_cession_of_a_single_machine_keeps_the_workshop_productive():
    update = apply_fleet_decisions(PARC, cessions={"Assemblage": 1})

    assert update.cessions_retenues == {"Decoupe": 0, "Assemblage": 1, "Conditionnement": 0}
    assert update.parc["Assemblage"] == 15
    assert update.warnings == ()


# --------------------------------------------------------------------------- produit de cession
def test_disposal_price_is_ht_and_collects_vat():
    disposal = calculate_machine_disposal(PRIX_CESSION_MACHINES["Decoupe"])

    assert disposal.produit_ht == Decimal("125000")
    assert disposal.tva_collectee == Decimal("25000.00")
    assert disposal.produit_ttc == Decimal("150000.00")


def test_disposal_of_one_machine_per_workshop():
    total = sum(
        calculate_machine_disposal(prix).produit_ttc
        for prix in PRIX_CESSION_MACHINES.values()
    )

    assert total == Decimal("378000.00")  # (125 000 + 150 000 + 40 000) x 1,20


def test_disposal_never_produces_a_negative_product():
    disposal = calculate_machine_disposal(-5000)

    assert disposal.produit_ht == Decimal("0")
    assert disposal.produit_ttc == Decimal("0")


def test_cession_vat_is_collected_and_kept_out_of_turnover():
    disposal = calculate_machine_disposal(PRIX_CESSION_MACHINES["Decoupe"])
    turnover_only = calculate_vat_projection(chiffre_affaires_ht=100000, tva_deductible_hors_immo_mois=0)
    with_cession = calculate_vat_projection(
        chiffre_affaires_ht=100000,
        tva_deductible_hors_immo_mois=0,
        tva_collectee_immobilisations=disposal.tva_collectee,
    )

    # 20 % du CA inchangés, + la TVA collectée sur la cession (25 000 €).
    assert turnover_only.tva_collectee_mois == Decimal("20000.00")
    assert with_cession.tva_collectee_mois == Decimal("45000.00")
    assert with_cession.tva_collectee_mois - turnover_only.tva_collectee_mois == disposal.tva_collectee
    assert with_cession.tva_nette_mois == Decimal("45000.00")
    assert with_cession.tva_deductible_hors_immo_mois == Decimal("0.00")


def test_negative_cession_vat_is_reported_instead_of_being_ignored():
    vat = calculate_vat_projection(
        chiffre_affaires_ht=100000,
        tva_deductible_hors_immo_mois=0,
        tva_collectee_immobilisations=-1000,
    )

    assert vat.tva_collectee_mois == Decimal("20000.00")
    assert any("cession" in w for w in vat.warnings)


# --------------------------------------------------------------------------- projection
def _projection(**overrides):
    params = dict(
        chiffre_affaires=1_000_000,
        achats=400_000,
        masse_salariale=120_000,
        charges_sociales=60_000,
        marketing=80_000,
        assurances=5_000,
        resultat_historique=50_000,
        aace_historique=30_000,
        depreciations_historique=2_000,
        dotations_amortissements_historique=40_000,
        solde_tresorerie_initial=500_000,
        solde_epargne_initial=100_000,
        dette_bancaire=200_000,
        investissement_ht=0,
        credit_fournisseur=False,
        bfr_mois=120_000,
        bfr_m1=100_000,
        interets_emprunt=1_000,
        remboursement_capital=5_000,
    )
    params.update(overrides)
    return calculate_projection(**params)


def test_cession_proceeds_are_cashed_in_the_month():
    cession_ht = 2 * PRIX_CESSION_MACHINES["Conditionnement"]
    reference = _projection()
    ceded = _projection(cession_ht=cession_ht)

    expected = calculate_machine_disposal(cession_ht).produit_ttc
    assert ceded.flux_investissement - reference.flux_investissement == expected
    assert ceded.tresorerie_finale - reference.tresorerie_finale == expected
    assert ceded.cession_ht == Decimal("80000.00")
    assert ceded.cession_ttc == Decimal("96000.00")


def test_cession_does_not_distort_the_result_or_the_workforce():
    reference = _projection()
    ceded = _projection(cession_ht=PRIX_CESSION_MACHINES["Assemblage"])

    assert ceded.resultat_net == reference.resultat_net
    assert ceded.total_charges == reference.total_charges
    assert ceded.caf == reference.caf


def test_cession_is_flagged_as_a_cash_and_capacity_operation():
    projection = _projection(cession_ht=PRIX_CESSION_MACHINES["Decoupe"])

    assert any("Cession de machines" in w for w in projection.warnings)


def test_no_cession_keeps_the_legacy_projection_untouched():
    reference = _projection(cession_ht=0)
    default = _projection()

    assert default.cession_ht == Decimal("0")
    assert default.cession_ttc == Decimal("0")
    assert default.tresorerie_finale == reference.tresorerie_finale
    assert not any("Cession de machines" in w for w in default.warnings)


# --------------------------------------------------------------------------- capacité
def _capacity_scenario(machines):
    calibration = Calibration(
        feature_names=("price", "quality", "log_brand", "log_product"),
        means=(100.0, 50.0, 10.0, 8.0), scales=(1.0, 1.0, 1.0, 1.0),
        coefficients=(0.0, 0.0, 0.0, 0.0), intercept=-2.0,
        n_obs=8, n_periods=1, r2=0.1, ridge_alpha=8.0,
        confidence="faible", note="test",
    )
    forecast = pd.DataFrame({
        "periode": ["Année 1 - Septembre"],
        "Shorty 3": [100.0],
        "Integral 3": [0.0],
        "Shorty 5": [0.0],
        "Integral 5": [0.0],
        "Integral 7": [0.0],
    })
    capacity = CapacityProfile(
        machines=machines,
        times={"Shorty 3": {"Decoupe": 2.0, "Assemblage": 3.0, "Conditionnement": 1.0}},
        capacity_min=100.0,
        source="test",
    )
    scenario = {"price": 100.0, "quality": 50.0, "brand_ads": 100.0, "product_ads": 50.0}
    competitors = pd.DataFrame(columns=["entreprise", "prix", "qualite", "publicite", "publicite_produit"])
    competitors.attrs["baseline_scenario"] = scenario
    return evaluate_scenario(forecast, "Shorty 3", 0.15, 100.0, calibration, competitors, scenario, 1000.0, capacity)


def test_ceding_a_machine_cuts_the_served_volume_not_the_scenario():
    before = _capacity_scenario({"Decoupe": 2, "Assemblage": 4, "Conditionnement": 1})
    after = _capacity_scenario({"Decoupe": 1, "Assemblage": 4, "Conditionnement": 1})

    assert before["service_rate"] == 1.0
    assert after["service_rate"] < before["service_rate"]
    assert after["unserved_units_total_horizon"] > before["unserved_units_total_horizon"]
    assert after["capacity_bottlenecks"] == ["Decoupe"]
    assert after["predicted_share"] < after["potential_share"]
    assert after["feasible"] is True  # un dépassement de capacité plafonne, il n'élimine pas


def test_emptying_a_workshop_makes_the_scenario_impossible():
    result = _capacity_scenario({"Decoupe": 0, "Assemblage": 4, "Conditionnement": 1})

    assert result["feasible"] is False
    assert result["service_rate"] == 0.0
    assert "Decoupe" in result["capacity_bottlenecks"]