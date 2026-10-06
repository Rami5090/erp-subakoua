import pandas as pd
from optimizer_engine import calibrate_descriptive_response, predict_anchored_share
from test_optimizer_engine import make_hist

def test_anchor_returns_observed_share_for_baseline():
    cal=calibrate_descriptive_response(make_hist(1))
    assert cal is not None
    base={"price":115,"quality":55,"brand_ads":30000,"product_ads":1100}
    s=predict_anchored_share(cal,base,base,0.0996)
    assert abs(s-0.0996)<1e-9

def test_anchor_limits_extrapolation_with_low_confidence():
    cal=calibrate_descriptive_response(make_hist(1))
    base={"price":115,"quality":55,"brand_ads":30000,"product_ads":1100}
    s=predict_anchored_share(cal,{"price":90,"quality":100,"brand_ads":100000,"product_ads":50000},base,0.0996)
    assert 0.02 < s < 0.5
