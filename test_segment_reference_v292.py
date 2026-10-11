import pandas as pd

from forecast_engine import extract_competitive_snapshot
from optimizer_engine import (
    CapacityProfile,
    Calibration,
    build_competitive_history,
    detect_segment_product,
    evaluate_scenario,
    rank_scenarios,
    resolve_own_levers,
    resolve_reference_share,
)

AUG = "Année 1 - Août"
JUL = "Année 1 - Juillet"


def _rows(companies, with_share=True):
    out = []
    for c in companies:
        r = {"Entreprise": str(c), "Ventes": 100 + c, "Prix (€)": 110 + c, "Qualité": 55 + c,
             "Publicité de marque": 20000 + c * 100, "Publicité produit (€)": 1000}
        if with_share:
            r["en quantité (%)"] = 10.0 + c / 10
        out.append(r)
    return out


def _study(rows):
    return {"pevcvipm": {"data": {"Tableau_1": rows}}}


def test_segment_share_rebuilt_from_sales_when_share_column_missing():
    rows = _rows([1, 2, 3, 4, 5], with_share=False)
    snap = extract_competitive_snapshot({}, 3, study_data={AUG: _study(rows)}, anchor_period=AUG)
    assert snap["segment_share"] is not None
    assert abs(snap["segment_share"] - 103 / sum(100 + c for c in [1, 2, 3, 4, 5])) < 1e-9
    assert snap["segment_share_source"] == "ventes/total benchmark"


def test_global_monitoring_share_is_not_mistaken_for_segment_share():
    # pevc ne contient que des concurrents ; l'équipe 3 n'existe que via le tableau de bord global.
    studies = {AUG: {**_study(_rows([1, 2, 4, 5])), "monitoring": {"marketShares": 9.5}}}
    snap = extract_competitive_snapshot({}, 3, study_data=studies, anchor_period=AUG)
    assert snap["segment_share"] is None
    assert snap["own_is_synthetic"] is True
    ref = resolve_reference_share(snap, pd.DataFrame(), 3, AUG, fallback_units=500.0)
    assert ref["level"] == "globale" and abs(ref["share"] - 0.095) < 1e-9 and ref["warning"]
    assert ref["units"] == 500.0


def test_reference_share_falls_back_to_last_own_observation_in_history():
    snap = {"segment_share": None, "own": {"ventes": 0}, "global_share": None}
    hist = pd.DataFrame([
        {"periode": JUL, "entreprise": 3, "part_marche": 12.0, "ventes": 150},
        {"periode": "Année 1 - Septembre", "entreprise": 3, "part_marche": 99.0, "ventes": 1},  # futur : ignoré
    ])
    ref = resolve_reference_share(snap, hist, 3, AUG)
    assert ref["level"] == "historique" and abs(ref["share"] - 0.12) < 1e-9 and JUL in ref["source"]


def test_reference_share_none_when_nothing_available():
    ref = resolve_reference_share({}, pd.DataFrame(), 3, AUG)
    assert ref["share"] is None and ref["level"] is None


def test_competitors_found_in_earlier_legacy_period():
    legacy = {
        JUL: {"veille_concurrentielle": {"Performance commerciale": {"Tableau_1": [
            {"Entreprise": str(c), "en quantité (%)": 10 + c, "Ventes": 100, "Prix (€)": 110, "Qualité": 55,
             "Publicité de marque": 20000} for c in [1, 2, 3, 4]]}}},
        AUG: {"veille_concurrentielle": {"Performance commerciale": {"Tableau_1": [
            {"Entreprise": "3", "en quantité (%)": 13, "Ventes": 100, "Prix (€)": 115, "Qualité": 60,
             "Publicité de marque": 55000}]}}},
    }
    snap = extract_competitive_snapshot(legacy, 3, study_data={}, anchor_period=AUG)
    assert snap["period"] == JUL
    assert {r["entreprise"] for r in snap["rows"]} == {1, 2, 3, 4}
    hist = build_competitive_history(legacy, 3, lambda p: extract_competitive_snapshot(legacy, 3, {}, p), periods=[JUL, AUG])
    assert not hist.empty


def test_detect_segment_product_unambiguous_or_none():
    row = {"Shorty 3": 200.0, "Integral 3": 100.0, "Shorty 5": 50.0, "Integral 5": 0.0, "Integral 7": 0.0}
    assert detect_segment_product(row, 200.0) == "Shorty 3"
    assert detect_segment_product(row, 350.0) is None  # total gamme : pas de produit unique
    row2 = dict(row, **{"Integral 3": 200.0})
    assert detect_segment_product(row2, 200.0) is None  # ambigu


def test_levers_use_history_before_defaults():
    own = {"prix": None, "qualite": None, "publicite_marque": None, "publicite_produit": None}
    hist = pd.DataFrame([{"periode": JUL, "entreprise": 3, "prix": 120.0, "qualite": 61.0, "publicite": 40000.0, "publicite_produit": 2500.0}])
    lv = resolve_own_levers(own, hist, 3, AUG)
    assert lv["prix"] == 120.0 and lv["qualite"] == 61.0 and lv["publicite_marque"] == 40000.0
    assert lv["uses_defaults"] == []
    lv2 = resolve_own_levers({}, pd.DataFrame(), 3, AUG)
    assert set(lv2["uses_defaults"]) == {"prix", "qualite", "publicite_marque", "publicite_produit"}


def _cal():
    return Calibration(("p", "q", "b", "a"), (100.0, 50.0, 10.0, 8.0), (1.0, 1.0, 1.0, 1.0), (-0.05, 0.02, 0.1, 0.05),
                       -2.0, 8, 1, 0.3, 8.0, "faible", "test")


def test_unknown_segment_product_scales_whole_range():
    fc = pd.DataFrame({"periode": ["m1"], "Shorty 3": [100.0], "Integral 3": [100.0], "Shorty 5": [0.0], "Integral 5": [0.0], "Integral 7": [0.0]})
    cap = CapacityProfile(machines={"Decoupe": 100, "Assemblage": 100, "Conditionnement": 100},
                          times={"Shorty 3": {"Decoupe": 1.0}, "Integral 3": {"Decoupe": 1.0}}, source="t")
    base = {"price": 100.0, "quality": 50.0, "brand_ads": 20000.0, "product_ads": 1000.0}
    better = dict(base, quality=70.0)
    comp = pd.DataFrame(columns=["entreprise", "prix", "qualite", "publicite", "publicite_produit"])
    comp.attrs["baseline_scenario"] = base
    r = evaluate_scenario(fc, None, 0.10, 200.0, _cal(), comp, better, 1e9, cap)
    assert r["ratio_vs_baseline"] > 1.0
    d = r["demand_forecast"]
    assert abs(d["Shorty 3"].iloc[0] - 100.0 * r["ratio_vs_baseline"]) < 1e-6
    assert abs(d["Integral 3"].iloc[0] - 100.0 * r["ratio_vs_baseline"]) < 1e-6


def test_rank_column_is_sequential_after_sorting():
    ev = [
        {"scenario": {"price": 100, "brand_ads": 0, "product_ads": 0, "quality": 50}, "predicted_share": 0.10, "marketing_spend": 0, "feasible": True},
        {"scenario": {"price": 101, "brand_ads": 0, "product_ads": 0, "quality": 50}, "predicted_share": 0.30, "marketing_spend": 0, "feasible": True},
        {"scenario": {"price": 102, "brand_ads": 0, "product_ads": 0, "quality": 50}, "predicted_share": 0.20, "marketing_spend": 0, "feasible": True},
    ]
    ranked = rank_scenarios(ev, 3)
    assert list(ranked["rang"]) == [1, 2, 3]
    assert list(ranked["prix (€)"]) == [101, 102, 100]
