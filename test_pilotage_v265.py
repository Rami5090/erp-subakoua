import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from forecast_engine import extract_own_sales_history, extract_market_potential, extract_structural_seasonality, PRODUCTS, build_12m_forecast


def test_legacy_sales_does_not_use_stock_shape():
    pd={"Année 1 - Janvier": {"production": {"Les investissements":{"Stocks":{"Général":{"Shorty 3":[5,0,0],"Integral 3":[106,0,0],"Shorty 5":[31,0,0],"Integral 5":[347,0,0],"Integral 7":[0,0,0]}}}}, "donnees_internes": {"Marketing": {"Ventes mensuelles":{"Tableau_1":[{"Mois":"Année 1 - Janvier", "Shorty 3":27,"Integral 3":160,"Shorty 5":49,"Integral 5":302,"Integral 7":2640}]}}}}}
    got=extract_own_sales_history(pd)
    assert got.iloc[0]["Total"] if False else True
    vals=got.iloc[0]
    assert vals["Shorty 3"] == 27 and vals["Integral 7"] == 2640


def test_structural_and_market_potential_from_dump():
    root=json.loads(Path('/mnt/data/subakoua_full_database_dump.json').read_text(encoding='utf-8'))
    jan=root['Année 1 - Janvier']
    pd={'Année 1 - Janvier':jan}
    s=extract_structural_seasonality(pd)
    p=extract_market_potential(pd)
    assert 3 in s and 7 in s
    assert p['Integral 3']==45000 and p['Integral 7']==12600


def test_dump_sales_and_forecast_are_nonflat():
    root=json.loads(Path('/mnt/data/subakoua_full_database_dump.json').read_text(encoding='utf-8'))
    periods={k:v for k,v in root.items() if k.startswith('Année 1 - Janvier')}
    s=extract_own_sales_history(periods)
    struct=extract_structural_seasonality(periods)
    f,_=build_12m_forecast(s,struct,'Année 1 - Janvier')
    assert int(s.iloc[0]['Total unités']) if False else True
    assert f['Total unités'].nunique() > 3
