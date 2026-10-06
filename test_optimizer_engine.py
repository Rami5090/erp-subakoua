import pandas as pd
from optimizer_engine import calibrate_descriptive_response, predict_relative_share, generate_candidate_grid, rank_scenarios


def make_hist(n_periods=3):
    rows=[]
    for t in range(n_periods):
        for c,(share,price,q,ads) in enumerate([(10,115,55,30000),(20,130,60,50000),(15,120,58,45000),(5,160,50,20000),(12,125,62,40000),(11,118,54,35000),(7,150,59,65000),(12,122,61,55000),(8,140,56,25000)],1):
            rows.append({"periode":f"P{t}","entreprise":c,"part_marche":share+0.1*t,"prix":price,"qualite":q,"publicite":ads,"publicite_produit":1000+100*c})
    return pd.DataFrame(rows)


def test_calibration_and_prediction():
    cal=calibrate_descriptive_response(make_hist())
    assert cal is not None
    assert cal.n_obs == 27
    competitors=pd.DataFrame([
        {"entreprise":1,"prix":115,"qualite":55,"publicite":30000,"publicite_produit":1100},
        {"entreprise":2,"prix":130,"qualite":60,"publicite":50000,"publicite_produit":1200},
    ])
    share=predict_relative_share(cal,{"price":115,"quality":60,"brand_ads":60000,"product_ads":4000},competitors)
    assert 0 < share < 1


def test_grid_respects_budget():
    base={"price":115,"quality":55,"brand_ads":30000,"product_ads":3000}
    grid=generate_candidate_grid(base,32000,[55,60])
    assert grid
    assert all(x["brand_ads"]+x["product_ads"]<=32000+1e-9 for x in grid)


def test_rank_prefers_higher_share():
    ranked=rank_scenarios([
        {"scenario":{"price":100,"brand_ads":0,"product_ads":0,"quality":50},"predicted_share":0.15,"share_delta":0.02,"marketing_spend":0,"max_capacity_utilization":0.5,"feasible":True},
        {"scenario":{"price":110,"brand_ads":0,"product_ads":0,"quality":50},"predicted_share":0.12,"share_delta":0.01,"marketing_spend":0,"max_capacity_utilization":0.4,"feasible":True},
    ],2)
    assert ranked.iloc[0]["PDM segment prévue (%)"] == 15
