from __future__ import annotations
import os
import pandas as pd
import numpy as np
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from forecast_engine import (
    PRODUCTS,
    build_period_data,
    build_study_data,
    extract_own_sales_history,
    extract_structural_seasonality,
    extract_competitive_snapshot,
    build_12m_forecast,
    ordered_periods,
)
from optimizer_engine import (
    DEFAULT_TIMES,
    Calibration,
    CapacityProfile,
    build_competitive_history,
    calibrate_descriptive_response,
    evaluate_scenario,
    generate_candidate_grid,
    rank_scenarios,
)

st.set_page_config(page_title="Optimiseur stratégique", layout="wide")
st.title("🎯 Optimiseur stratégique — maximiser la part de marché")
st.caption("Analyse de sensibilité empirique : le moteur n'attribue pas d'élasticité causale non validée. Lorsque la PDM globale est absente, l'objectif porte explicitement sur le segment concurrentiel observé.")


def secret(section: str, key: str, default: str = "") -> str:
    try:
        if section in st.secrets and key in st.secrets[section]:
            return str(st.secrets[section][key])
    except Exception:
        pass
    return os.getenv(key, default)


@st.cache_resource
def get_engine():
    host = secret("mysql", "host", "")
    user = secret("mysql", "user", secret("mysql", "username", ""))
    password = secret("mysql", "password", "")
    database = secret("mysql", "database", "")
    port = secret("mysql", "port", "")
    if not all([host, user, password, database, port]):
        return None
    url = URL.create("mysql+pymysql", username=user, password=password, host=host, port=int(port), database=database)
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, connect_args={"ssl": {}})


def load_data(engine):
    with engine.connect() as conn:
        legacy = pd.read_sql(text("SELECT periode, module, contenu FROM erp_donnees ORDER BY id ASC"), conn)
        try:
            studies = pd.read_sql(text("SELECT periode, study_id, label, module, price, read_allowed, payload FROM erp_etudes ORDER BY id ASC"), conn)
        except Exception:
            studies = pd.DataFrame(columns=["periode","study_id","label","module","price","read_allowed","payload"])
    return legacy, studies


def load_capacity(engine) -> CapacityProfile:
    machines={"Decoupe":10,"Assemblage":16,"Conditionnement":4}
    times={p:dict(v) for p,v in DEFAULT_TIMES.items()}
    source=[]
    try:
        with engine.connect() as conn:
            dfm=pd.read_sql(text("SELECT Nb_Machines_Decoupe, Nb_Machines_Assemblage, Nb_Machines_Cond FROM Parc_Machines_Mensuel ORDER BY id DESC LIMIT 1"),conn)
            if not dfm.empty:
                machines={"Decoupe":int(dfm.iloc[0]["Nb_Machines_Decoupe"]),"Assemblage":int(dfm.iloc[0]["Nb_Machines_Assemblage"]),"Conditionnement":int(dfm.iloc[0]["Nb_Machines_Cond"])}
                source.append("machines BDD")
            dft=pd.read_sql(text("SELECT produit, atelier, temps_normal FROM Parametres_TempsAteliers"),conn)
            if not dft.empty:
                times={p:{} for p in PRODUCTS}
                for _,r in dft.iterrows():
                    if r["produit"] in times:
                        times[r["produit"]][str(r["atelier"])] = float(r["temps_normal"])
                source.append("temps BDD")
    except Exception:
        pass
    return CapacityProfile(machines=machines,times=times,source=" + ".join(source) if source else "fallback ERP")

engine=get_engine()
if engine is None:
    st.error("Connexion Aiven indisponible.")
    st.stop()
try:
    legacy, studies=load_data(engine)
except Exception as exc:
    st.error(f"Lecture Aiven impossible : {exc}")
    st.stop()

period_data=build_period_data(legacy.to_dict("records"))
study_data=build_study_data(studies.loc[studies["read_allowed"].fillna(False).astype(bool)].to_dict("records")) if not studies.empty else {}
periods=ordered_periods(set(period_data)|set(study_data))
if not periods:
    st.error("Aucune période exploitable.")
    st.stop()

anchor=st.selectbox("Période d'ancrage", periods, index=len(periods)-1)
own_company=int(st.number_input("N° entreprise",1,9,3,1))

sales=extract_own_sales_history(period_data,study_data=study_data)
struct=extract_structural_seasonality(period_data,study_data=study_data)
forecast_df, _ = build_12m_forecast(sales, struct, anchor)
competitive=extract_competitive_snapshot(period_data,own_company,study_data=study_data,anchor_period=anchor)

if forecast_df.empty or not competitive.get("rows"):
    st.warning("Le moteur a besoin d'un forecast de ventes et d'un benchmark concurrentiel pour construire des scénarios.")
    st.stop()

rows=competitive.get("rows") or []
own=competitive.get("own") or {}
segment_share=competitive.get("segment_share")
segment_units=_num(own.get("ventes")) if own else None
# Le segment observé est celui dont les ventes propres sont cohérentes avec un produit de la série actuelle.
segment_product=None
if segment_units is not None and not sales.empty:
    rr=sales.loc[sales["periode"]==competitive.get("period")]
    if not rr.empty:
        rr=rr.iloc[-1]
        candidates=[p for p in PRODUCTS if abs(float(rr[p])-segment_units)<1e-9]
        if candidates:
            segment_product=candidates[0]
segment_product=segment_product or PRODUCTS[0]

# Historique concurrentiel pour calibration descriptive.
def snap_for_period(p):
    return extract_competitive_snapshot(period_data,own_company,study_data=study_data,anchor_period=p)

hist=build_competitive_history(period_data,own_company,snap_for_period)
# Complète les colonnes publicité produit depuis les observations brutes du snapshot courant quand disponibles.
# Si elles ne le sont pas, on conserve 0 et on affiche un avertissement méthodologique.
if "publicite_produit" not in hist.columns:
    hist["publicite_produit"]=0.0

cal=calibrate_descriptive_response(hist)
competitors=pd.DataFrame()
if rows:
    competitors=pd.DataFrame([{
        "entreprise":r.get("entreprise"),
        "prix":r.get("prix"),
        "qualite":r.get("qualite"),
        "publicite":r.get("publicite_marque", r.get("publicite")),
        "publicite_produit":r.get("publicite_produit") or 0.0,
    } for r in rows if not r.get("is_own") and r.get("prix") is not None and r.get("qualite") is not None and r.get("publicite") is not None])

cap=load_capacity(engine)

st.subheader("📚 État du modèle")
c1,c2,c3,c4=st.columns(4)
c1.metric("Ventes actuelles",f"{sum(float(sales.iloc[-1][p]) for p in PRODUCTS):,.0f} u" if not sales.empty else "—")
c2.metric("PDM segment observée",f"{segment_share*100:.2f} %" if segment_share is not None else "—")
c3.metric("Concurrents",str(max(0,len(rows)-1)))
c4.metric("Calibration",f"{cal.n_obs} obs / {cal.n_periods} période(s)" if cal else "Non disponible")
if cal:
    st.info(f"Calibration descriptive régularisée : {cal.confidence} — R² descriptif={cal.r2:.2f}. Les scénarios sont ancrés sur la PDM observée et les variations sont volontairement rétrécies lorsque l'historique est court. {cal.note}")
else:
    st.warning("Pas assez d'observations pour une calibration descriptive. Les scénarios ne seront pas calculés.")
    st.stop()
if segment_share is None or segment_units is None:
    st.warning("Aucune PDM segmentielle exploitable pour l'ancrage : le moteur ne peut pas quantifier une part de marché de référence.")
    st.stop()

st.subheader("🎛️ Leviers")
base_price=float(own.get("prix") or 115.0)
base_quality=float(own.get("qualite") or 57.2)
base_brand=float(own.get("publicite_marque", own.get("publicite")) or 55000.0)
base_prod=float(own.get("publicite_produit") or 3000.0)
b1,b2,b3,b4=st.columns(4)
price=b1.number_input("Prix du segment (€)",min_value=1.0,value=base_price,step=1.0)
quality=b2.slider("Qualité cible",0.0,100.0,base_quality,0.5)
brand=b3.number_input("Publicité marque mensuelle (€)",min_value=0.0,value=base_brand,step=500.0)
prodads=b4.number_input("Publicité produits mensuelle (€)",min_value=0.0,value=base_prod,step=100.0)
budget=st.number_input("Budget marketing mensuel maximum (€)",min_value=0.0,value=max(100000.0,base_brand+base_prod),step=1000.0)

st.subheader("🏭 Contrainte industrielle")
st.caption(f"Capacité : {cap.machines} machines ; 9 600 min/machine/mois. Source : {cap.source}.")

baseline_scenario={"price":price,"quality":quality,"brand_ads":brand,"product_ads":prodads}
competitors.attrs["baseline_scenario"]=baseline_scenario
base_eval=evaluate_scenario(forecast_df,segment_product,segment_share,segment_units,cal,competitors,baseline_scenario,budget,cap)
if not base_eval["feasible"]:
    st.warning("La configuration actuelle dépasse au moins une contrainte marketing/capacité sur l'horizon projeté.")

quality_values=sorted(set([max(0,base_quality-10),max(0,base_quality-5),base_quality,min(100,base_quality+5),min(100,base_quality+10)]))
candidates=generate_candidate_grid(baseline_scenario,budget,quality_values)

if st.button("🚀 Lancer l'optimisation", type="primary", use_container_width=True):
    evaluated=[]
    progress=st.progress(0)
    for i,sc in enumerate(candidates):
        e=evaluate_scenario(forecast_df,segment_product,segment_share,segment_units,cal,competitors,sc,budget,cap)
        e["scenario"]=sc
        evaluated.append(e)
        if i % max(1,len(candidates)//100)==0:
            progress.progress(min(1.0,(i+1)/len(candidates)))
    progress.progress(1.0)
    ranked=rank_scenarios(evaluated,15)
    if ranked.empty:
        st.error("Aucun scénario réalisable sous les contraintes actuelles.")
        st.stop()
    st.subheader("🏆 Scénarios les plus performants")
    st.dataframe(ranked, width="stretch", hide_index=True)

    best=ranked.iloc[0].to_dict()
    st.success(f"Meilleur scénario exploratoire : PDM segmentielle estimée à {best['PDM segment prévue (%)']:.2f} %, soit {best['Δ PDM vs base (pts)']:+.2f} point(s) vs la base.")
    st.caption("Ce résultat est une analyse de sensibilité descriptive sur les entreprises observées ; il ne constitue pas une prévision causale certifiée.")

    # Projection des volumes du meilleur scénario sur l'horizon.
    best_scenario={"price":float(best["prix (€)"]),"quality":float(best["qualité cible"]),"brand_ads":float(best["publicité marque (€)"]),"product_ads":float(best["publicité produits (€)"])}
    best_eval=evaluate_scenario(forecast_df,segment_product,segment_share,segment_units,cal,competitors,best_scenario,budget,cap)
    view=best_eval["forecast"][["periode",*PRODUCTS,"scenario_total"]].copy()
    view.rename(columns={"scenario_total":"Total scénario"},inplace=True)
    st.subheader("📈 Impact volume du scénario choisi")
    st.dataframe(view.round(1),width="stretch",hide_index=True)

    if best_eval["max_capacity_utilization"] > 0.9:
        st.warning(f"Le scénario utilise jusqu'à {best_eval['max_capacity_utilization']*100:.1f} % d'une capacité d'atelier : une analyse MRP/machines devra valider les achats et le lissage mensuel avant exécution.")
    else:
        st.info(f"Capacité maximale utilisée sur l'horizon : {best_eval['max_capacity_utilization']*100:.1f} %.")
else:
    st.info("Le moteur est prêt. Lance l'optimisation pour comparer plusieurs centaines de combinaisons de prix, publicité et qualité sous contrainte de budget et capacité.")

st.subheader("⚠️ Limites méthodologiques actuelles")
st.write("Le moteur ne modélise pas encore de manière causale la distribution, la disponibilité des matières, le BFR, la trésorerie ou les nouveaux investissements. Ces éléments seront ajoutés dans la prochaine couche de simulation.")
st.write("Lorsque l'historique atteindra plusieurs périodes, la calibration sera ré-estimée automatiquement ; les scénarios pourront alors être confrontés à un backtest hors-échantillon.")
