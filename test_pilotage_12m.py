from strategic_engine import current_competitive_metrics


def test_current_competitive_metrics_handles_none_own():
    snapshot = {"own": None, "rows": [], "share": 0.001, "total_market": 1000}
    metrics = current_competitive_metrics(snapshot)
    assert metrics["own_price"] is None
    assert metrics["own_quality"] is None
    assert metrics["competitor_count"] == 0


def test_page_sales_fallback_logic_equivalent():
    competitive = {"own": None}
    own_snapshot = competitive.get("own") or {}
    current_sales = own_snapshot.get("ventes")
    assert current_sales is None
