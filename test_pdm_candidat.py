"""Scénario candidat de la PDM du centre de décision : construit à partir de la base observée.

Régression protégée : le cockpit évaluait un candidat incohérent (prix M-1 de
l'Integral 5, qualité 50 codée en dur, publicité de marque + somme des PLV,
moyenne des PLV) sans ancrage ; `evaluate_scenario` prenait alors le candidat
comme sa propre référence et la PDM potentielle restait égale à la PDM de
référence, quels que soient les leviers.

Le candidat part désormais du scénario de base observé et n'en change que les
leviers pilotés par le simulateur, en variation par rapport à leur valeur M-1.
"""
import pandas as pd
import pytest

from constants import DEFAULT_TIMES, PRODUCTS
from optimizer_engine import (
    Calibration,
    CapacityProfile,
    anchor_competitors,
    build_simulator_candidate,
    detect_segment_product,
    detect_segment_product_from_sales,
    evaluate_scenario,
)

# Leviers observés à l'ancrage : ils diffèrent volontairement des décisions M-1.
BASE = {"price": 285.0, "quality": 58.0, "brand_ads": 72_000.0, "product_ads": 4_300.0}
PRIX_M1 = {"Shorty 3": 115.0, "Integral 3": 190.0, "Shorty 5": 220.0, "Integral 5": 280.0, "Integral 7": 375.0}
PLV_M1 = {"Shorty 3": 3_100.0, "Integral 3": 4_100.0, "Shorty 5": 3_200.0, "Integral 5": 4_000.0, "Integral 7": 3_500.0}
M1 = {"prix": PRIX_M1, "pub_marque": 70_000.0, "pub_produit": PLV_M1}
PDM_REFERENCE = 0.168


def _saisie(prix=None, pub_marque=None, pub_produit=None):
    """Leviers saisis dans le simulateur : ceux de M-1, sauf ceux qu'on modifie."""
    return {
        "prix": {**PRIX_M1, **(prix or {})},
        "pub_marque": M1["pub_marque"] if pub_marque is None else pub_marque,
        "pub_produit": {**PLV_M1, **(pub_produit or {})},
    }


# --------------------------------------------------------------------------- candidat
def test_sans_decision_nouvelle_le_candidat_est_exactement_la_base_observee():
    # 285 € observés contre 280 € décidés, etc. : seul compte l'écart saisie / M-1, nul ici.
    candidat, notes = build_simulator_candidate(BASE, "Integral 5", _saisie(), M1)

    assert candidat == BASE
    assert notes == []


def test_le_prix_du_segment_varie_en_relatif_par_rapport_au_prix_m1():
    candidat, _ = build_simulator_candidate(BASE, "Integral 5", _saisie(prix={"Integral 5": 252.0}), M1)

    assert candidat["price"] == pytest.approx(285.0 * 0.90)  # −10 % appliqués au prix observé
    for levier in ("quality", "brand_ads", "product_ads"):
        assert candidat[levier] == BASE[levier]


def test_les_leviers_des_autres_produits_ne_sont_pas_transmis():
    """Ni leur prix, ni leur PLV : la publicité de marque n'est plus « marque + somme des PLV »."""
    candidat, _ = build_simulator_candidate(
        BASE, "Integral 5",
        _saisie(prix={"Shorty 3": 99.0, "Integral 7": 300.0}, pub_produit={"Shorty 3": 20_000.0}), M1,
    )

    assert candidat == BASE


def test_publicite_de_marque_seule_et_publicite_du_produit_du_segment():
    candidat, _ = build_simulator_candidate(
        BASE, "Integral 5", _saisie(pub_marque=77_000.0, pub_produit={"Integral 5": 5_000.0}), M1,
    )

    assert candidat["brand_ads"] == pytest.approx(72_000.0 * 77_000.0 / 70_000.0)
    assert candidat["product_ads"] == pytest.approx(4_300.0 * 5_000.0 / 4_000.0)
    assert candidat["price"] == BASE["price"]
    assert candidat["quality"] == BASE["quality"]  # pas un levier du simulateur


def test_une_publicite_m1_nulle_se_transpose_en_ecart_absolu():
    base = {**BASE, "product_ads": 0.0}
    m1 = {**M1, "pub_produit": {**PLV_M1, "Integral 5": 0.0}}

    neutre, _ = build_simulator_candidate(base, "Integral 5", {**_saisie(), "pub_produit": m1["pub_produit"]}, m1)
    hausse, _ = build_simulator_candidate(
        base, "Integral 5", {**_saisie(), "pub_produit": {**m1["pub_produit"], "Integral 5": 2_500.0}}, m1,
    )

    assert neutre == base
    assert hausse["product_ads"] == pytest.approx(2_500.0)


def test_produit_du_segment_inconnu_seule_la_publicite_de_marque_est_transmise():
    candidat, notes = build_simulator_candidate(
        BASE, None, _saisie(prix={"Integral 5": 200.0}, pub_marque=80_000.0, pub_produit={"Integral 5": 9_000.0}), M1,
    )

    assert candidat["price"] == BASE["price"]
    assert candidat["product_ads"] == BASE["product_ads"]
    assert candidat["brand_ads"] == pytest.approx(72_000.0 * 80_000.0 / 70_000.0)
    assert len(notes) == 1 and "non identifiable" in notes[0]


# --------------------------------------------------------------------------- ancrage
COMPETITORS = pd.DataFrame([
    {"entreprise": 1, "prix": 300.0, "qualite": 61.0, "publicite": 82_000.0, "publicite_produit": 5_200.0},
    {"entreprise": 2, "prix": 272.0, "qualite": 54.0, "publicite": 52_000.0, "publicite_produit": 2_600.0},
])
FORECAST = pd.DataFrame([{"periode": "Année 2 - Janvier", **{p: 100.0 for p in PRODUCTS}}])
CAPACITE = CapacityProfile(
    machines={"Decoupe": 50, "Assemblage": 50, "Conditionnement": 50},
    times={p: dict(v) for p, v in DEFAULT_TIMES.items()},
)


def _calibration(beta_prix: float) -> Calibration:
    return Calibration(
        feature_names=("prix", "qualite", "log_publicite_marque", "log_publicite_produit"),
        means=(285.0, 58.0, 11.0, 8.0), scales=(20.0, 5.0, 0.3, 0.4),
        coefficients=(beta_prix, 0.1, 0.1, 0.05), intercept=0.0,
        n_obs=72, n_periods=12, r2=0.8, ridge_alpha=8.0, confidence="bonne", note="",
    )


def _pdm_potentielle(cal, scenario, competitors):
    evaluation = evaluate_scenario(
        FORECAST, "Integral 5", PDM_REFERENCE, 1_110.0, cal, competitors, scenario, float("inf"), CAPACITE,
    )
    return evaluation["potential_share"]


def test_l_ancrage_est_pose_sur_une_copie_jamais_sur_le_cadre_recu():
    en_cache = COMPETITORS.copy()

    ancre = anchor_competitors(en_cache, BASE)

    assert ancre is not en_cache
    assert ancre.attrs["baseline_scenario"] == BASE
    assert "baseline_scenario" not in en_cache.attrs
    assert ancre.attrs["baseline_scenario"] is not BASE  # le dict du cache n'est pas partagé non plus


def test_sans_ancrage_la_pdm_ne_bouge_jamais():
    """Le défaut d'origine : sans scénario de base, le candidat est sa propre référence."""
    cal = _calibration(-0.4)
    baisse, _ = build_simulator_candidate(BASE, "Integral 5", _saisie(prix={"Integral 5": 252.0}), M1)

    assert _pdm_potentielle(cal, baisse, COMPETITORS) == pytest.approx(PDM_REFERENCE, abs=1e-12)
    assert _pdm_potentielle(cal, baisse, anchor_competitors(COMPETITORS, BASE)) > PDM_REFERENCE


@pytest.mark.parametrize("beta_prix", [-0.4, 0.4])
def test_une_baisse_du_prix_du_segment_suit_le_signe_du_coefficient(beta_prix):
    cal = _calibration(beta_prix)
    competitors = anchor_competitors(COMPETITORS, BASE)
    neutre, _ = build_simulator_candidate(BASE, "Integral 5", _saisie(), M1)
    baisse, _ = build_simulator_candidate(BASE, "Integral 5", _saisie(prix={"Integral 5": 252.0}), M1)

    assert _pdm_potentielle(cal, neutre, competitors) == pytest.approx(PDM_REFERENCE, abs=1e-12)
    ecart = _pdm_potentielle(cal, baisse, competitors) - PDM_REFERENCE
    assert ecart != 0
    assert (ecart > 0) == (beta_prix < 0)


# --------------------------------------------------------------------------- produit du segment
def _ventes(*lignes):
    return pd.DataFrame([{"periode": p, "index": i, **dict(zip(PRODUCTS, v))} for p, i, v in lignes])


def test_la_ligne_du_benchmark_ne_permet_pas_de_reconnaitre_le_produit():
    """Elle ne porte que les ventes du segment : il faut l'historique des ventes par produit."""
    assert detect_segment_product({"entreprise": 3, "ventes": 1_110.0}, 1_110.0) is None


def test_le_produit_du_segment_est_reconnu_sur_les_ventes_reelles():
    ventes = _ventes(
        ("Année 1 - Novembre", 10, (51, 384, 102, 1_200, 1_793)),
        ("Année 1 - Décembre", 11, (51, 384, 102, 1_110, 1_793)),
    )

    # Ventes de la période du benchmark, puis repli sur la dernière période réelle ≤ ancrage.
    assert detect_segment_product_from_sales(ventes, 1_200.0, "Année 1 - Novembre", "Année 1 - Décembre") == "Integral 5"
    assert detect_segment_product_from_sales(ventes, 1_110.0, None, "Année 1 - Décembre") == "Integral 5"
    # Aucune fuite vers l'avenir : la période postérieure à l'ancrage n'est pas lue.
    assert detect_segment_product_from_sales(ventes, 1_110.0, None, "Année 1 - Novembre") is None
    # Total de la gamme ou historique vide : produit non identifiable.
    assert detect_segment_product_from_sales(ventes, 3_440.0, "Année 1 - Décembre", "Année 1 - Décembre") is None
    assert detect_segment_product_from_sales(pd.DataFrame(), 1_110.0, None, "Année 1 - Décembre") is None
