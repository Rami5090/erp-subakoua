import pandas as pd
from forecast_engine import *


def test_structural_normalized():
    row={"Produit":3,**{m:1 for m in MONTH_NAMES}}
    data={"Année 1 - Janvier":{"etudes_marche":{"Etudes structurelles":{"Prévision des ventes":{"Tableau_2":[row]}}}}}
    s=extract_structural_seasonality(data)
    assert abs(sum(s[3].values())/12-1)<1e-9


def test_api_monthly_sales_payload_is_supported():
    study={"Année 1 - Janvier":{"ensaacvm":{
        "readAllowed": True,
        "studyId":"ensaacvm",
        "sales":[{"month":13,"monthlySalesByProducts":[
            {"productId":"SHORTY_C","sales":27},
            {"productId":"MONO_C","sales":160},
            {"productId":"SHORTY_T","sales":49},
            {"productId":"MONO_T","sales":302},
            {"productId":"MONO_F","sales":2640},
        ]}]
    }}}
    f=extract_own_sales_history({}, study_data=study)
    assert len(f)==1
    assert f.iloc[0]["Integral 7"]==2640
    assert f.iloc[0]["Total" if "Total" in f.columns else "Integral 7"] >= 0


def test_api_seasonality_and_market_potential():
    payload={"peeumreusreprv":{
        "listCoefSaisonnier":[{"nomProd":"SHORTY_C",**{f"coef{i}":float(i) for i in range(1,13)}}],
        "listPeeumreusreprvMoisProd":[{"nomProd":"SHORTY_C","mbase50":5400}]
    }}
    data={"Année 1 - Janvier":payload}
    s=extract_structural_seasonality({}, study_data=data)
    mp=extract_market_potential({}, study_data=data)
    assert 3 in s and len(s[3])==12
    assert mp["Shorty 3"]==5400


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


def test_12m_forecast_works_with_one_real_month():
    sales=pd.DataFrame([{"periode":"Année 1 - Janvier","index":0,"Shorty 3":27,"Integral 3":160,"Shorty 5":49,"Integral 5":302,"Integral 7":2640}])
    factors={3:{i:1 for i in range(12)},5:{i:1 for i in range(12)},7:{i:1 for i in range(12)}}
    f,_=build_12m_forecast(sales,factors,"Année 1 - Janvier")
    assert not f.empty
    assert len(f)==12
    assert f.loc[f['periode']=='Année 1 - Janvier','statut'].iloc[0]=='Réel'
    assert f.loc[f['periode']=='Année 1 - Février','statut'].iloc[0]=='Prévision'


def test_one_point_forecast_applies_structural_seasonality():
    sales=pd.DataFrame([{"periode":"Année 1 - Janvier","index":0,"Shorty 3":100,"Integral 3":0,"Shorty 5":0,"Integral 5":0,"Integral 7":0}])
    factors={3:{i:1.0 for i in range(12)},5:{i:1.0 for i in range(12)},7:{i:1.0 for i in range(12)}}
    factors[3][1]=2.0
    f,_=build_12m_forecast(sales,factors,"Année 1 - Janvier")
    feb=float(f.loc[f['periode']=='Année 1 - Février','Shorty 3'].iloc[0])
    mar=float(f.loc[f['periode']=='Année 1 - Mars','Shorty 3'].iloc[0])
    assert feb > mar
    assert feb == 200.0


def test_monitoring_api_gives_current_share_at_anchor():
    study={"Année 1 - Janvier":{"monitoring":{"studyId":"monitoring","readAllowed":True,"marketShares":15.06,"monitoringTurnoverData":{"monthlySalesTurnover":[1000477]},"remainingStocks":{}}}}
    snap=extract_competitive_snapshot({},3,study_data=study,anchor_period="Année 1 - Janvier")
    assert abs(snap["share"]-0.1506)<1e-9
    assert snap["own"]["ventes"] >= 0


def test_legacy_wrapped_module_seasonality_and_market_potential():
    payload={"etudes_marche":{"Etudes structurelles":{"Prévision des ventes":{
        "Tableau_1":[{"Colonne_0":"Marché potentiel","Shorty 3":5400,"Integral 3":45000,"Shorty 5":6300,"Integral 5":54000,"Integral 7":12600}],
        "Tableau_2":[{"Produit":3, **{"Janvier":0.1,"Février":0.1,"Mars":0.3,"Avril":0.4,"Mai":1.1,"Juin":3,"Juillet":3.2,"Août":2.5,"Septembre":0.4,"Octobre":0.4,"Novembre":0.3,"Décembre":0.2}}]}}}}
    period_data={"Année 1 - Janvier":{"etudes_marche":payload}}
    s=extract_structural_seasonality(period_data)
    mp=extract_market_potential(period_data)
    assert 3 in s and s[3][6] > s[3][0]
    assert mp["Integral 7"] == 12600


def test_market_forecast_does_not_invent_market_of_one():
    sales=pd.DataFrame([{"periode":"Année 1 - Janvier","index":0,"Shorty 3":27,"Integral 3":160,"Shorty 5":49,"Integral 5":302,"Integral 7":2640}])
    factors={3:{i:1 for i in range(12)}}
    f,_=build_12m_forecast(sales,factors,"Année 1 - Janvier")
    result=compute_market_forecast(f,factors,{}, {"share":None,"total_market":None}, 0.13, "Année 1 - Janvier")
    assert all(np.isnan(x) for x in result.market_volume_proxy)
    assert all(np.isnan(x) for x in result.baseline_share_forecast)
    assert result.calibration_method.startswith("marché indisponible")


def test_realistic_wrapped_dump_supports_seasonal_forecast():
    payload = {
        "etudes_marche": {
            "Etudes structurelles": {
                "Prévision des ventes": {
                    "Tableau_1": [{"Colonne_0": "Marché potentiel", "Shorty 3": 5400, "Integral 3": 45000, "Shorty 5": 6300, "Integral 5": 54000, "Integral 7": 12600}],
                    "Tableau_2": [
                        {"Produit": 3, "Janvier": 0.1, "Février": 0.1, "Mars": 0.3, "Avril": 0.4, "Mai": 1.1, "Juin": 3.0, "Juillet": 3.2, "Août": 2.5, "Septembre": 0.4, "Octobre": 0.4, "Novembre": 0.3, "Décembre": 0.2},
                        {"Produit": 5, "Janvier": 0.2, "Février": 0.2, "Mars": 0.4, "Avril": 0.5, "Mai": 1.2, "Juin": 2.8, "Juillet": 3.0, "Août": 2.0, "Septembre": 0.5, "Octobre": 0.5, "Novembre": 0.4, "Décembre": 0.3},
                        {"Produit": 7, "Janvier": 2.5, "Février": 2.2, "Mars": 0.5, "Avril": 0.3, "Mai": 0.2, "Juin": 0.2, "Juillet": 0.2, "Août": 0.3, "Septembre": 0.3, "Octobre": 0.8, "Novembre": 2.0, "Décembre": 2.5},
                    ],
                }
            }
        }
    }
    pd_data = {"Année 1 - Janvier": payload}
    struct = extract_structural_seasonality(pd_data)
    potential = extract_market_potential(pd_data)
    sales = pd.DataFrame([{"periode": "Année 1 - Janvier", "index": 0, "Shorty 3": 5, "Integral 3": 106, "Shorty 5": 31, "Integral 5": 347, "Integral 7": 0}])
    f, _ = build_12m_forecast(sales, struct, "Année 1 - Janvier")
    assert f.loc[f["periode"] == "Année 1 - Juillet", "Total unités"].iloc[0] != f.loc[f["periode"] == "Année 1 - Janvier", "Total unités"].iloc[0]
    assert sum(potential.values()) > 0


def test_period_code_and_all_api_sales_payloads_are_recognized():
    study_data = {
        "Année 1 - Juin": {
            "some_other_study": {
                "studyId": "some_other_study",
                "tableauVentes": [
                    {"productId": "SHORTY_C", "sales": 100},
                    {"productId": "MONO_C", "sales": 200},
                    {"productId": "SHORTY_T", "sales": 50},
                    {"productId": "MONO_T", "sales": 300},
                    {"productId": "MONO_F", "sales": 400},
                ],
            }
        }
    }
    hist = extract_own_sales_history({}, study_data=study_data)
    assert hist["periode"].tolist() == ["Année 1 - Juin"]
    assert float(hist.loc[0, "Integral 7"]) == 400.0


def test_build_period_data_normalizes_subakoua_period_code():
    data = build_period_data([{"periode": "0106", "module": "marketing", "contenu": {}}])
    assert list(data) == ["Année 1 - Juin"]
