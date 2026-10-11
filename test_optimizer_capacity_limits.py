import pandas as pd

from optimizer_engine import (
    Calibration,
    CapacityProfile,
    evaluate_scenario,
    latest_competitor_frame,
    build_competitive_history,
)
from forecast_engine import extract_competitive_snapshot


def test_competitive_snapshot_merges_pevc_api_payload_when_legacy_has_only_own():
    period = "Année 1 - Août"
    legacy = {
        period: {
            "veille_concurrentielle": {
                "Performance commerciale": {
                    "Tableau_1": [{
                        "Entreprise": "3", "en quantité (%)": 14.54,
                        "Ventes": 200, "Prix (€)": 115, "Qualité": 64,
                        "Publicité de marque": 55000, "Publicité produit (€)": 3000,
                    }]
                }
            }
        }
    }
    rows = []
    for company in [1, 2, 4, 5, 6, 7, 8]:
        rows.append({
            "Entreprise": str(company),
            "en quantité (%)": 10.0 + company / 10,
            "Ventes": 100 + company,
            "Prix (€)": 110 + company,
            "Qualité": 55 + company,
            "Publicité de marque": 20000 + company * 100,
            "Publicité produit (€)": 1000 + company * 10,
        })
    studies = {period: {"pevcvipm": {"data": {"Tableau_1": rows}}}}

    snapshot = extract_competitive_snapshot(legacy, own_company_number=3, study_data=studies, anchor_period=period)
    assert len(snapshot["rows"]) == 8
    assert snapshot["own"]["entreprise"] == 3
    assert snapshot["segment_share"] == 0.1454
    assert {r["entreprise"] for r in snapshot["rows"] if not r["is_own"]} == {1, 2, 4, 5, 6, 7, 8}

    hist = build_competitive_history(legacy, 3, lambda p: extract_competitive_snapshot(legacy, 3, studies, p), periods=[period])
    competitor_frame = latest_competitor_frame(hist, 3, period)
    assert competitor_frame["entreprise"].nunique() == 7


def test_capacity_is_applied_as_volume_cap_not_auto_rejecting_scenario():
    cal = Calibration(
        feature_names=("price", "quality", "log_brand", "log_product"),
        means=(100.0, 50.0, 10.0, 8.0), scales=(1.0, 1.0, 1.0, 1.0),
        coefficients=(0.0, 0.0, 0.0, 0.0), intercept=-2.0,
        n_obs=8, n_periods=1, r2=0.1, ridge_alpha=8.0,
        confidence="faible", note="test",
    )
    forecast = pd.DataFrame({
        "periode": ["Année 1 - Septembre", "Année 1 - Octobre"],
        "Shorty 3": [100.0, 120.0],
        "Integral 3": [0.0, 0.0],
        "Shorty 5": [0.0, 0.0],
        "Integral 5": [0.0, 0.0],
        "Integral 7": [0.0, 0.0],
    })
    cap = CapacityProfile(
        machines={"Decoupe": 1, "Assemblage": 1, "Conditionnement": 1},
        times={"Shorty 3": {"Decoupe": 2.0, "Assemblage": 3.0, "Conditionnement": 1.0}},
        capacity_min=100.0,
        source="test",
    )
    scenario = {"price": 100.0, "quality": 50.0, "brand_ads": 100.0, "product_ads": 50.0}
    competitors = pd.DataFrame(columns=["entreprise", "prix", "qualite", "publicite", "publicite_produit"])
    competitors.attrs["baseline_scenario"] = scenario
    result = evaluate_scenario(forecast, "Shorty 3", 0.15, 100.0, cal, competitors, scenario, 1000.0, cap)

    assert result["feasible"] is True
    assert result["max_demand_capacity_utilization"] > 1.0
    assert result["max_capacity_utilization"] <= 1.0 + 1e-9
    assert result["average_unserved_units_month"] > 0
    assert result["forecast"]["scenario_total"].max() < result["demand_forecast"]["demand_total"].max()
    assert result["predicted_share"] < result["potential_share"]
