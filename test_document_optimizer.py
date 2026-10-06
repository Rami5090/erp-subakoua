import csv
from pathlib import Path

import document_optimizer as d

BASE = Path(__file__).resolve().parent


def test_load_profiles_and_endpoint_catalog():
    profiles = d.load_profiles()
    assert len(profiles) == 91
    assert sum(p.endpoint_known for p in profiles.values()) == 54


def test_period_mapping_matches_audit_observation():
    assert d.period_label_to_code("Année 1 - Janvier") == "0101"
    assert d.period_label_to_code("Année 1 - Juin") == "0106"
    assert d.period_month_id("Année 1 - Juin") == 18
    assert d.next_period("Année 1 - Juin") == "Année 1 - Juillet"


def test_plan_does_not_consider_historical_bought_as_live_truth():
    profiles = d.load_profiles()
    rows = list(csv.DictReader((BASE / "subakoua_study_catalog.csv").open(encoding="utf-8-sig")))
    # Force two documents to be unavailable live and one to be available live.
    for row in rows:
        row["boughtByTeam"] = "False"
    rows_by_id = {r["study_id"]: r for r in rows}
    rows_by_id["potentialMarketStudy"]["boughtByTeam"] = "True"
    plan = d.build_purchase_plan(
        profiles,
        rows,
        target_period="Année 1 - Juillet",
        budget=200,
        allow_5000=False,
    )
    assert all(x.study_id != "potentialMarketStudy" for x in plan)
    assert all(x.price <= 200 for x in plan)


def test_live_bought_by_team_key_is_supported():
    profiles = d.load_profiles()
    rows = [{"study_id": "potentialMarketStudy", "price": 100, "bought_by_team": True}]
    plan = d.build_purchase_plan(profiles, rows, target_period="Année 1 - Juillet", budget=500)
    assert plan == []


def test_market_share_priority_is_reflected():
    profiles = d.load_profiles()
    candidate_ids = ["pevcvipm", "peeedcpofi"]
    scored = {sid: d.score_profile(profiles[sid])[0] for sid in candidate_ids}
    assert scored["pevcvipm"] > scored["peeedcpofi"]


def test_strategic_5000_excluded_by_default():
    profiles = d.load_profiles()
    rows = [{"study_id": "enapgeqtcofr", "price": 5000, "boughtByTeam": False}]
    plan = d.build_purchase_plan(profiles, rows, target_period="Année 1 - Juillet", budget=10_000, allow_5000=False)
    assert not plan
