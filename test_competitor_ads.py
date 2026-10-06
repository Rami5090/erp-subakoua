import json
from forecast_engine import build_period_data, extract_competitive_snapshot


def test_competitor_brand_and_product_ads_are_distinct():
    raw=json.load(open('/mnt/data/subakoua_full_database_dump.json',encoding='utf-8'))
    rows=[]
    p='Année 1 - Janvier'
    rows=[{"periode":p,"module":m,"contenu":json.dumps(v,ensure_ascii=False),"type_donnee":"etat_actuel"}
          for m,v in raw[p]["etat_actuel"].items()]
    pd=build_period_data(rows)
    snap=extract_competitive_snapshot(pd,3,anchor_period=p)
    own=snap['own']
    assert own is not None
    assert own['publicite_marque'] == 55000
    assert own['publicite_produit'] == 3000
    assert own['publicite'] == 55000
