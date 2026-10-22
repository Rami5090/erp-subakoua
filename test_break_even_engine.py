"""Moteur d'analyse quantitative : point d'équilibre, marge, mix sous capacité.

Invariants protégés ici :
  * le point d'équilibre est exact : marge sur coût variable = charges fixes ;
  * les charges fixes sont imputées sans jamais être inventées ni perdues ;
  * le mix proposé respecte **toutes** les capacités d'atelier ;
  * un équilibre hors de portée de la capacité est signalé, pas masqué.
"""
import pytest

from break_even_engine import (
    ProductEconomics,
    allocate_fixed_costs,
    capacity_units_per_product,
    compute_break_even,
    compute_optimum_targets,
    optimize_mix,
    sensitivity_price,
)

S3 = ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=60.0, current_units=200.0)
I3 = ProductEconomics(key="i3", name="Integral 3", price=200.0, unit_variable_cost=100.0, current_units=100.0)

MIX = {"s3": 100.0, "i3": 50.0}
TIMES = {
    "Shorty 3": {"Decoupe": 2.0, "Assemblage": 4.0, "Conditionnement": 1.0},
    "Integral 3": {"Decoupe": 3.0, "Assemblage": 6.0, "Conditionnement": 1.0},
}


# --------------------------------------------------------------------------- point d'équilibre
def test_break_even_is_exact_on_a_simple_mix():
    # CA 20 000 €, marge 9 000 € (unitaire moyenne 60 €), charges fixes 9 000 €.
    result = compute_break_even([S3, I3], 9000, mix=MIX)

    assert result.break_even_units == pytest.approx(150.0)
    assert result.break_even_revenue == pytest.approx(20000.0)
    assert result.weighted_margin_per_unit == pytest.approx(60.0)
    assert result.weighted_margin_ratio == pytest.approx(0.45)


def test_at_the_break_even_the_margin_equals_fixed_costs():
    result = compute_break_even([S3, I3], 9000, mix=MIX)
    seuil = result.break_even_units
    mix_au_seuil = {"s3": seuil * 0.6666666667, "i3": seuil * 0.3333333333}

    marge = sum(p.margin_per_unit * mix_au_seuil[p.key] for p in [S3, I3])

    assert marge == pytest.approx(9000.0, rel=1e-3)


def test_margin_of_safety_measures_the_gap_to_the_equilibrium():
    result = compute_break_even([S3, I3], 9000, mix=MIX)

    # 300 unités vendues, 40 000 € de CA, pour un seuil de 150 unités / 20 000 €.
    assert result.current_units == pytest.approx(300.0)
    assert result.current_revenue == pytest.approx(40000.0)
    assert result.margin_of_safety_units == pytest.approx(150.0)
    assert result.margin_of_safety_revenue == pytest.approx(20000.0)
    assert result.margin_of_safety_ratio == pytest.approx(0.5)


def test_without_fixed_costs_the_equilibrium_is_zero():
    result = compute_break_even([S3, I3], 0, mix=MIX)

    assert result.break_even_units == pytest.approx(0.0)
    assert result.break_even_revenue == pytest.approx(0.0)


def test_missing_mix_is_reported_instead_of_guessed():
    result = compute_break_even(
        [ProductEconomics("s3", "Shorty 3", 100.0, 60.0), ProductEconomics("i3", "Integral 3", 200.0, 100.0)],
        9000,
    )

    assert result.break_even_units is None
    assert any("Mix de référence absent" in w for w in result.warnings)


def test_negative_margin_product_is_flagged():
    perdant = ProductEconomics(key="s5", name="Shorty 5", price=100.0, unit_variable_cost=120.0)

    result = compute_break_even([S3, perdant], 5000, mix={"s3": 100.0, "s5": 100.0})

    assert any("marge unitaire négative" in w for w in result.warnings)


def test_missing_unit_cost_is_flagged():
    sans_cout = ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=0.0)

    result = compute_break_even([sans_cout], 5000, mix={"s3": 100.0})

    assert any("coût variable unitaire manquant" in w for w in result.warnings)


# --------------------------------------------------------------------------- imputation
def test_fixed_costs_are_allocated_by_revenue():
    part = allocate_fixed_costs([S3, I3], 9000, MIX, method="revenue")

    assert part["s3"] == pytest.approx(4500.0)
    assert part["i3"] == pytest.approx(4500.0)
    assert sum(part.values()) == pytest.approx(9000.0)


def test_fixed_costs_are_allocated_by_units():
    part = allocate_fixed_costs([S3, I3], 9000, MIX, method="units")

    assert part["s3"] == pytest.approx(6000.0)
    assert part["i3"] == pytest.approx(3000.0)


def test_break_even_by_product_follows_the_allocation():
    result = compute_break_even([S3, I3], 9000, mix=MIX, allocation="revenue")

    # 4 500 € imputés / 40 € de marge unitaire = 112,5 unités de Shorty 3.
    assert result.break_even_units_by_product["s3"] == pytest.approx(112.5)
    # 4 500 € imputés / 100 € de marge unitaire = 45 unités d'Integral 3.
    assert result.break_even_units_by_product["i3"] == pytest.approx(45.0)


def test_allocation_without_any_base_stays_zero():
    part = allocate_fixed_costs([S3, I3], 9000, {}, method="revenue")

    assert part == {"s3": 0.0, "i3": 0.0}


# --------------------------------------------------------------------------- capacité et mix
def test_optimal_mix_never_exceeds_any_workshop():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    mix, _ = optimize_mix([S3, I3], TIMES, capacite)

    for atelier, minutes in capacite.items():
        charge = sum(e.minutes.get(atelier, 0.0) for e in mix)
        assert charge <= minutes + 1e-9


def test_optimal_mix_prefers_the_best_margin_per_scarce_capacity():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    mix, _ = optimize_mix([S3, I3], TIMES, capacite)

    # Integral 3 rapporte 33 €/minute de Découpe contre 20 €/minute pour Shorty 3 :
    # le goulot de Découpe est saturé par Integral 3.
    assert len(mix) == 1
    assert mix[0].key == "i3"
    assert mix[0].units == pytest.approx(1000.0 / 3.0)
    assert mix[0].margin == pytest.approx(100.0 * (1000.0 / 3.0))


def test_optimal_mix_respects_the_demand_cap():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    mix, _ = optimize_mix([S3, I3], TIMES, capacite, demand_caps={"i3": 10.0})

    volumes = {e.key: e.units for e in mix}
    assert volumes["i3"] == pytest.approx(10.0)
    assert volumes["s3"] > 0


def test_max_units_per_product_when_produced_alone():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    maxi = capacity_units_per_product([S3, I3], TIMES, capacite)

    # Shorty 3 : min(1000/2, 2000/4, 1000/1) = 500 ; Integral 3 : min(1000/3, 2000/6, 1000/1) = 333,3
    assert maxi["s3"] == pytest.approx(500.0)
    assert maxi["i3"] == pytest.approx(1000.0 / 3.0)


def test_equilibrium_out_of_reach_is_flagged():
    # Capacité minuscule : la marge maximale atteignable est loin des charges fixes.
    capacite = {"Decoupe": 10.0, "Assemblage": 10.0, "Conditionnement": 10.0}

    result = compute_break_even([S3, I3], 9000, mix=MIX, times=TIMES, capacity_minutes=capacite)

    assert result.equilibrium_reachable is False
    assert result.max_margin_under_capacity < 9000
    assert result.capacity_gap == pytest.approx(9000 - result.max_margin_under_capacity)
    assert any("Équilibre inatteignable" in w for w in result.warnings)


def test_equilibrium_within_reach():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    result = compute_break_even([S3, I3], 9000, mix=MIX, times=TIMES, capacity_minutes=capacite)

    assert result.equilibrium_reachable is True
    assert result.max_margin_under_capacity >= 9000
    assert result.optimal_mix_units > 0


# --------------------------------------------------------------------------- sensibilité
def test_price_increase_lowers_the_break_even_volume():
    result = sensitivity_price([S3, I3], 9000, 0.05, mix=MIX)

    assert result["apres"].break_even_units < result["avant"].break_even_units
    assert result["delta_break_even_units"] < 0


def test_price_decrease_raises_the_break_even_volume():
    result = sensitivity_price([S3, I3], 9000, -0.05, mix=MIX)

    assert result["apres"].break_even_units > result["avant"].break_even_units


# --------------------------------------------------------------------------- objectif volume
def test_volume_objective_serves_more_units_than_the_margin_objective():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    marge = compute_break_even([S3, I3], 9000, mix=MIX, times=TIMES, capacity_minutes=capacite, objective="margin")
    volume = compute_break_even([S3, I3], 9000, mix=MIX, times=TIMES, capacity_minutes=capacite, objective="volume")

    # Le mix « marge » sert Integral 3 (33 €/min de Découpe) ; le mix « volume »
    # sert Shorty 3 (500 u contre 333 u), moins rentable mais plus nombreux.
    assert volume.optimal_mix_units > marge.optimal_mix_units
    assert volume.optimal_mix_margin < marge.optimal_mix_margin
    assert volume.objective == "volume"
    assert marge.objective == "margin"


def test_volume_objective_still_respects_the_capacity():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}

    volume = compute_break_even([S3, I3], 9000, mix=MIX, times=TIMES, capacity_minutes=capacite, objective="volume")

    for atelier, minutes in capacite.items():
        charge = sum(e.minutes.get(atelier, 0.0) for e in volume.optimal_mix)
        assert charge <= minutes + 1e-9


# --------------------------------------------------------------------------- valeurs à atteindre
def test_targets_list_the_price_floor_needed_to_cover_fixed_costs():
    seul = [ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=60.0, current_units=100.0)]
    resultat = compute_break_even(seul, 9000, mix={"s3": 100.0})

    cibles, _ = compute_optimum_targets(seul, resultat)

    prix = next(c for c in cibles if c.cle == "prix_s3")
    # 60 €/u de coût variable + 9 000 € de charges fixes imputées / 100 u
    assert prix.valeur_cible == pytest.approx(150.0)
    assert prix.valeur_actuelle == pytest.approx(100.0)
    assert prix.ecart == pytest.approx(50.0)
    assert prix.atteint is False
    assert "150" in prix.action


def test_targets_list_the_cost_ceiling_that_preserves_the_margin_rate():
    seul = [ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=60.0, current_units=100.0)]
    resultat = compute_break_even(seul, 9000, mix={"s3": 100.0})

    cibles, _ = compute_optimum_targets(seul, resultat)

    cout = next(c for c in cibles if c.cle == "cout_s3")
    # Taux de marge pondéré 40 % -> le coût unitaire doit rester sous 60 €/u
    assert cout.valeur_cible == pytest.approx(60.0)
    assert cout.atteint is True


def test_a_satisfied_price_floor_is_reported_as_reached():
    """Un prix déjà au-dessus du plancher n'est pas une cible à ajuster."""
    seul = [ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=60.0, current_units=100.0)]
    # Charges fixes faibles : le prix de 100 €/u couvre largement la part imputée.
    resultat = compute_break_even(seul, 1000, mix={"s3": 100.0})

    cibles, _ = compute_optimum_targets(seul, resultat)

    prix = next(c for c in cibles if c.cle == "prix_s3")
    assert prix.sens == "min"
    assert prix.valeur_cible == pytest.approx(70.0)
    assert prix.atteint is True
    assert prix.ecart < 0  # le plancher est dépassé


def test_a_cost_above_the_ceiling_is_reported_as_not_reached():
    """Sur un mix multi-produits, le plafond suit le taux du portefeuille :
    un produit trop cher par rapport à la moyenne doit être signalé."""
    produits = [
        ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=90.0, current_units=100.0),
        ProductEconomics(key="i3", name="Integral 3", price=200.0, unit_variable_cost=50.0, current_units=100.0),
    ]
    resultat = compute_break_even(produits, 1000, mix={"s3": 100.0, "i3": 100.0})

    cibles, _ = compute_optimum_targets(produits, resultat)

    cout = next(c for c in cibles if c.cle == "cout_s3")
    # Taux du portefeuille 53,3 % -> le coût de Shorty 3 doit passer sous 46,7 €/u
    assert cout.sens == "max"
    assert cout.valeur_cible == pytest.approx(100.0 * (1.0 - 16000.0 / 30000.0), rel=0.01)
    assert cout.atteint is False
    assert cout.ecart > 0

    cout_ok = next(c for c in cibles if c.cle == "cout_i3")
    assert cout_ok.atteint is True


def test_targets_list_the_volume_to_sell_for_the_optimal_mix():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}
    produits = [
        ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=60.0, current_units=40.0),
        ProductEconomics(key="i3", name="Integral 3", price=200.0, unit_variable_cost=100.0, current_units=10.0),
    ]
    resultat = compute_break_even(produits, 9000, mix=MIX, times=TIMES, capacity_minutes=capacite)

    cibles, _ = compute_optimum_targets(produits, resultat, times=TIMES, capacity_minutes=capacite)

    volume_i3 = next(c for c in cibles if c.cle == "volume_i3")
    assert volume_i3.valeur_cible == pytest.approx(1000.0 / 3.0)
    assert volume_i3.ecart > 0
    assert "Vendre" in volume_i3.action


def test_targets_count_the_machines_to_add_to_serve_the_whole_demand():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}
    produits = [
        ProductEconomics(key="s3", name="Shorty 3", price=100.0, unit_variable_cost=60.0, current_units=40.0),
    ]
    resultat = compute_break_even(produits, 9000, mix=MIX, times=TIMES, capacity_minutes=capacite)

    cibles, _ = compute_optimum_targets(
        produits, resultat, times=TIMES, capacity_minutes=capacite,
        machines={"Decoupe": 1, "Assemblage": 2, "Conditionnement": 1},
        capacity_min_per_machine=1000.0,
        mix_demande={"s3": 1000.0},
    )

    decoupe = next(c for c in cibles if c.cle == "capacite_Decoupe")
    # 1 000 u de Shorty 3 = 2 000 min de Découpe pour 1 000 disponibles
    assert decoupe.valeur_cible == pytest.approx(2000.0)
    assert decoupe.ecart == pytest.approx(1000.0)
    assert decoupe.bloquant is True
    assert "1 machine(s)" in decoupe.action


def test_targets_are_already_reached_when_the_mix_is_optimal():
    capacite = {"Decoupe": 1000.0, "Assemblage": 2000.0, "Conditionnement": 1000.0}
    produits = [
        ProductEconomics(key="i3", name="Integral 3", price=200.0, unit_variable_cost=100.0, current_units=1000.0 / 3.0),
    ]
    resultat = compute_break_even(produits, 9000, mix={"i3": 1000.0 / 3.0}, times=TIMES, capacity_minutes=capacite)

    cibles, _ = compute_optimum_targets(produits, resultat, times=TIMES, capacity_minutes=capacite)

    volume = next(c for c in cibles if c.cle == "volume_i3")
    assert volume.atteint is True
    assert "aligné" in volume.action
