import json
import pandas as pd
from forecast_engine import *


def test_structural_normalized():
    row={"Produit":3,**{m:1 for m in MONTH_NAMES}}
    data={"Année 1 - Janvier":{"etudes_marche":{"Etudes structurelles":{"Prévision des ventes":{"Tableau_2":[row]}}}}}
    s=extract_structural_seasonality(data)
    assert abs(sum(s[3].values())/12-1)<1e-9


def test_hybrid_forecast_uses_seasonality():
    r=forecast_series([100,110,105,115,120,118,125,130],[8,9],{i:1 for i in range(12)})
    assert len(r.forecast)==2 and all(x>=0 for x in r.forecast)


def test_12m_anchor_does_not_look_into_future():
    sales=pd.DataFrame([
        {"periode":"Année 1 - Janvier","index":0,"Shorty 3":10,"Integral 3":20,"Shorty 5":5,"Integral 5":2,"Integral 7":1},
        {"periode":"Année 1 - Février","index":1,"Shorty 3":20,"Integral 3":20,"Shorty 5":5,"Integral 5":2,"Integral 7":1},
        {"periode":"Année 1 - Mars","index":2,"Shorty 3":30,"Integral 3":20,"Shorty 5":5,"Integral 5":2,"Integral 7":1},
        {"periode":"Année 1 - Avril","index":3,"Shorty 3":40,"Integral 3":20,"Shorty 5":5,"Integral 5":2,"Integral 7":1},
        {"periode":"Année 1 - Mai","index":4,"Shorty 3":50,"Integral 3":20,"Shorty 5":5,"Integral 5":2,"Integral 7":1},
    ])
    f,_=build_12m_forecast(sales,{3:{i:1 for i in range(12)},5:{i:1 for i in range(12)},7:{i:1 for i in range(12)}},"Année 1 - Mars")
    assert f.loc[f['periode']=='Année 1 - Mars','statut'].iloc[0]=='Réel'
    assert f.loc[f['periode']=='Année 1 - Avril','statut'].iloc[0]=='Prévision'
    assert f.loc[f['periode']=='Année 1 - Avril','Shorty 3'].iloc[0] != 40


def test_competition_snapshot():
    data={"Année 1 - Janvier":{"veille_concurrentielle":{"Performance commerciale":{"Ventes":{"Tableau_1":[{"Entreprise":1,"Ventes":100},{"Entreprise":3,"Ventes":200}]},"Parts de marché":{"Tableau_1":[{"Entreprise":1,"en quantité (%)":33.33,"en valeur (%)":30},{"Entreprise":3,"en quantité (%)":66.67,"en valeur (%)":70}]}}}}}
    snap=extract_competitive_snapshot(data,3)
    assert snap['own']['ventes']==200
    assert 0.66 < snap['share'] < 0.67
