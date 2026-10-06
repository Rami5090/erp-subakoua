import json
from forecast_engine import extract_competitive_snapshot

def test_competitive_parser_ignores_price_evolution_company_values():
    data={"Année 1 - Janvier": {"veille_concurrentielle": {
        "Évolution marketing": {"Prix": {"Tableau_1":[{"Mois":"Janvier","Entreprise":115},{"Mois":"Février","Entreprise":120}]}},
        "Performance commerciale": {
            "Ventes":{"Tableau_1":[{"Entreprise":1,"Ventes":10},{"Entreprise":3,"Ventes":27}]},
            "Parts de marché":{"Tableau_1":[{"Entreprise":1,"en quantité (%)":40},{"Entreprise":3,"en quantité (%)":60}]}
        }
    }}}
    r=extract_competitive_snapshot(data,3,anchor_period="Année 1 - Janvier")
    assert {x["entreprise"] for x in r["rows"]} == {1,3}
    assert r["share_scope"] == "competitive_segment"

def test_segment_share_not_used_as_overall_market_share():
    data={"Année 1 - Janvier": {"veille_concurrentielle": {
        "Performance commerciale": {
            "Ventes":{"Tableau_1":[{"Entreprise":1,"Ventes":10},{"Entreprise":3,"Ventes":27}]},
            "Parts de marché":{"Tableau_1":[{"Entreprise":1,"en quantité (%)":40},{"Entreprise":3,"en quantité (%)":60}]}
        }
    }}}
    r=extract_competitive_snapshot(data,3,anchor_period="Année 1 - Janvier")
    assert r["share"] == 0.60
    assert r["share_scope"] == "competitive_segment"
