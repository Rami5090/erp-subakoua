import pandas as pd
from forecast_engine import extract_competitive_snapshot


def test_january_competitive_snapshot_is_segment_not_global():
    data={"Année 1 - Janvier":{"veille_concurrentielle":{
        "Performance commerciale":{
            "Ventes":{"Tableau_1":[
                {"Entreprise":1,"Ventes":0},{"Entreprise":2,"Ventes":17},{"Entreprise":3,"Ventes":27},
                {"Entreprise":4,"Ventes":47},{"Entreprise":5,"Ventes":44},{"Entreprise":6,"Ventes":17},
                {"Entreprise":7,"Ventes":22},{"Entreprise":8,"Ventes":50},{"Entreprise":9,"Ventes":47}]},
            "Parts de marché":{"Tableau_1":[
                {"Entreprise":1,"en quantité (%)":0},{"Entreprise":2,"en quantité (%)":6.27},{"Entreprise":3,"en quantité (%)":9.96},
                {"Entreprise":4,"en quantité (%)":17.34},{"Entreprise":5,"en quantité (%)":16.24},{"Entreprise":6,"en quantité (%)":6.27},
                {"Entreprise":7,"en quantité (%)":8.12},{"Entreprise":8,"en quantité (%)":18.45},{"Entreprise":9,"en quantité (%)":17.34}]}
        }
    }}}
    r=extract_competitive_snapshot(data,3,anchor_period="Année 1 - Janvier")
    assert abs(r["share"]-0.0996)<1e-9
    assert r["share_scope"] == "competitive_segment"
    assert len(r["rows"]) == 9


def test_target_percentage_conversion():
    assert 13.0/100.0 == 0.13
