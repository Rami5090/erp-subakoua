from __future__ import annotations
import os
import pandas as pd
import numpy as np
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from forecast_engine import (
    PRODUCTS,
    _num,
    build_period_data,
    build_study_data,
    extract_own_sales_history,
    extract_structural_seasonality,
    extract_competitive_snapshot,
    ordered_periods,
    period_index,
    period_label_from_index,
    backtest_forecast_methods,
    select_backtest_method,
    rolling_forecast_12m,
    latest_observed_period,
)
from optimizer_engine import (
    DEFAULT_TIMES,
    Calibration,
    CapacityProfile,
    build_competitive_history,
    latest_competitor_frame,
    detect_segment_product,
    resolve_own_levers,
    resolve_reference_share,
    calibrate_descriptive_response,
    evaluate_scenario,
    generate_candidate_grid,
    rank_scenarios,
)

st.set_page_config(page_title="Optimiseur stratégique", layout="wide")

from auth import setup as auth_setup, is_editor
from db_utils import get_read_engine

auth_setup()  # rôle visiteur/utilisateur ; cette page est en lecture seule pour tous
st.title("🎯 Optimiseur stratégique — maximiser la part de marché")
st.caption("Analyse de sensibilité empirique : le moteur calibre sur les périodes réellement observées jusqu'à l'ancrage, effectue un backtest hors-échantillon du forecast, puis teste des scénarios sur le benchmark segmentiel lorsque la PDM globale est indisponible.")


def secret(section: str, key: str, default: str = "") -> str:
    try:
        if section in st.secrets and key in st.secrets[section]:
            return str(st.secrets[section][key])
    except Exception:
        pass
    return os.getenv(key, default)


def get_engine():
    """Moteur lecture seule ([mysql_readonly]) si configuré, sinon moteur standard."""
    return get_read_engine()


def load_data(engine):
    with engine.connect() as conn:
        legacy = pd.read_sql(text("SELECT periode, module, contenu FROM erp_donnees ORDER BY id ASC"), conn)
        try:
            studies = pd.read_sql(text("SELECT periode, study_id, label, module, price, read_allowed, payload FROM erp_etudes ORDER BY id ASC"), conn)
        except Exception:
            studies = pd.DataFrame(columns=["periode","study_id","label","module","price","read_allowed","payload"])
    return legacy, studies


def load_capacity(engine) -> CapacityProfile:
    # Les valeurs de secours sont utilisées atelier par atelier, jamais en remplacement
    # de toutes les données si une seule requête SQL échoue.
    default_machines = {"Decoupe": 10, "Assemblage": 16, "Conditionnement": 4}
    machines = dict(default_machines)
    times = {p: dict(v) for p, v in DEFAULT_TIMES.items()}
    sources = []
    errors = []
    try:
        with engine.connect() as conn:
            # Parc_Machines_Mensuel possède Tour_ID comme clé, pas de colonne id.
            dfm = pd.read_sql(text("SELECT Tour_ID, Nb_Machines_Decoupe, Nb_Machines_Assemblage, Nb_Machines_Cond FROM Parc_Machines_Mensuel ORDER BY Tour_ID DESC LIMIT 1"), conn)
        if not dfm.empty:
            row = dfm.iloc[0]
            machines = {
                "Decoupe": max(0, int(row["Nb_Machines_Decoupe"] or 0)),
                "Assemblage": max(0, int(row["Nb_Machines_Assemblage"] or 0)),
                "Conditionnement": max(0, int(row["Nb_Machines_Cond"] or 0)),
            }
            sources.append(f"Parc_Machines_Mensuel · Tour {int(row['Tour_ID'])}")
        else:
            errors.append("Parc_Machines_Mensuel vide")
    except Exception as exc:
        errors.append(f"machines: {type(exc).__name__}: {exc}")

    try:
        with engine.connect() as conn:
            dft = pd.read_sql(text("SELECT produit, atelier, temps_normal FROM Parametres_TempsAteliers"), conn)
        if not dft.empty:
            product_aliases = {
                "SHORTY_C": "Shorty 3", "MONO_C": "Integral 3",
                "SHORTY_T": "Shorty 5", "MONO_T": "Integral 5", "MONO_F": "Integral 7",
            }
            atelier_aliases = {
                "cond": "Conditionnement", "conditionnement": "Conditionnement",
                "decoupe": "Decoupe", "découpe": "Decoupe", "assemblage": "Assemblage",
            }
            for _, row in dft.iterrows():
                prod_raw = str(row.get("produit", "")).strip()
                prod = product_aliases.get(prod_raw, prod_raw)
                atelier_raw = str(row.get("atelier", "")).strip()
                atelier = atelier_aliases.get(atelier_raw.lower(), atelier_aliases.get(atelier_raw, atelier_raw))
                value = _num(row.get("temps_normal"))
                if prod in times and atelier in {"Decoupe", "Assemblage", "Conditionnement"} and value is not None and value >= 0:
                    times[prod][atelier] = float(value)
            sources.append(f"Parametres_TempsAteliers · {len(dft)} lignes")
        else:
            errors.append("Parametres_TempsAteliers vide")
    except Exception as exc:
        errors.append(f"temps ateliers: {type(exc).__name__}: {exc}")

    source = " + ".join(sources) if sources else "fallback ERP"
    if errors:
        source += " · secours partiel: " + "; ".join(errors)
    return CapacityProfile(machines=machines, times=times, source=source)


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

sales=extract_own_sales_history(period_data,study_data=study_data)
latest_real=latest_observed_period(sales)
if latest_real is None:
    st.error("Aucune période de ventes réellement observée n'est disponible.")
    st.stop()
real_periods=[p for p in periods if period_index(p) <= period_index(latest_real)]
default_anchor_idx=real_periods.index(latest_real) if latest_real in real_periods else len(real_periods)-1
anchor=st.selectbox("Période d'ancrage / dernière période réelle", real_periods, index=default_anchor_idx, help="Le moteur interdit toute fuite vers des périodes futures lors de la calibration.")
own_company=int(st.number_input("N° entreprise",1,9,3,1))

struct=extract_structural_seasonality(period_data,study_data=study_data)
sales_to_anchor=sales.loc[sales["index"] <= period_index(anchor)].copy()
backtest_details, backtest_summary=backtest_forecast_methods(sales_to_anchor, struct)
selected_method=select_backtest_method(backtest_summary)
forecast_full, _ = rolling_forecast_12m(sales, struct, anchor, method=selected_method, horizon=12)
# v2.9.2 : rolling_forecast_12m renvoie aussi la ligne réelle de l'ancrage. Les scénarios, la capacité
# et la dépense annuelle ne doivent porter que sur les 12 mois à venir.
forecast_df = forecast_full.loc[forecast_full["statut"] == "Prévision"].reset_index(drop=True) if (not forecast_full.empty and "statut" in forecast_full.columns) else forecast_full
competitive=extract_competitive_snapshot(period_data,own_company,study_data=study_data,anchor_period=anchor)

if forecast_df.empty:
    st.warning("Le moteur a besoin d'un forecast de ventes exploitable pour construire des scénarios.")
    st.stop()

rows=competitive.get("rows") or []
own=competitive.get("own") or {}
# Historique concurrentiel : inclut les périodes présentes uniquement dans erp_etudes.
def snap_for_period(p):
    return extract_competitive_snapshot(period_data, own_company, study_data=study_data, anchor_period=p)

history_periods = ordered_periods(set(period_data) | set(study_data))
hist = build_competitive_history(period_data, own_company, snap_for_period, periods=history_periods)
if "publicite_produit" not in hist.columns:
    hist["publicite_produit"] = 0.0
cal = calibrate_descriptive_response(hist)

# La source d'ancrage peut ne contenir que la ligne propre (ex. tableau de bord).
# On construit alors le benchmark à partir de la dernière observation de chaque
# concurrent connue avant l'ancrage, plutôt que de faire len(rows)-1.
competitors = latest_competitor_frame(hist, own_company, anchor)
if competitors.empty and rows:
    competitors = pd.DataFrame([{
        "entreprise": r.get("entreprise"),
        "prix": r.get("prix"),
        "qualite": r.get("qualite"),
        "publicite": r.get("publicite_marque", r.get("publicite")),
        "publicite_produit": r.get("publicite_produit") or 0.0,
    } for r in rows if not r.get("is_own")
      and r.get("prix") is not None and r.get("qualite") is not None
      and r.get("publicite_marque", r.get("publicite")) is not None])
if competitors.empty:
    competitors = pd.DataFrame(columns=["entreprise", "prix", "qualite", "publicite", "publicite_produit"])

cap=load_capacity(engine)

# v2.9.2 : PDM de référence résolue avec repli traçable (benchmark → historique → PDM globale).
actual_sales_row=sales_to_anchor.tail(1)
actual_total=float(actual_sales_row[PRODUCTS].sum(axis=1).iloc[0]) if not actual_sales_row.empty else 0.0
ref=resolve_reference_share(competitive, hist, own_company, anchor, fallback_units=actual_total)
segment_share=ref["share"]
segment_units=ref["units"]
# Produit du segment : identifié seulement s'il correspond sans ambiguïté, sinon toute la gamme est ajustée.
segment_product=detect_segment_product(actual_sales_row.iloc[-1] if not actual_sales_row.empty else None, segment_units)
levers=resolve_own_levers(own, hist, own_company, anchor)

st.subheader("📚 État du modèle")
c1,c2,c3,c4=st.columns(4)
c1.metric("Ventes actuelles",f"{actual_total:,.0f} u")
_pdm_label = "PDM de référence" if ref["level"] in ("historique", "globale") else "PDM segment observée"
c2.metric(_pdm_label, f"{segment_share*100:.2f} %" if segment_share is not None else "—", help=f"Source : {ref['source']}")
competitor_count = int(competitors["entreprise"].nunique()) if not competitors.empty and "entreprise" in competitors.columns else 0
c3.metric("Concurrents reconnus", str(competitor_count), help="Entreprises distinctes autres que l'équipe sélectionnée, à partir du dernier benchmark exploitable avant l'ancrage.")
c4.metric("Calibration",f"{cal.n_obs} obs / {cal.n_periods} période(s)" if cal else "Non disponible")
with st.expander("Diagnostic des concurrents et des sources", expanded=(competitor_count == 0)):
    st.write(f"Période du snapshot sélectionné : **{competitive.get('period') or 'non déterminée'}**")
    st.write(f"Lignes concurrentielles brutes du snapshot : **{len(rows)}**")
    st.write(f"Concurrents exploitables (prix + qualité + publicité) : **{competitor_count}**")
    st.write(f"Historique de calibration : **{len(hist)} observations**, **{hist['entreprise'].nunique() if not hist.empty else 0} entreprises**")
    st.write(f"PDM de référence : **{(segment_share*100):.2f} %**" if segment_share is not None else "PDM de référence : **indisponible**")
    st.write(f"Source de la PDM : **{ref['source']}**")
    st.write(f"Produit du segment suivi : **{segment_product or 'toute la gamme (non identifiable)'}**")
    st.write("Origine des leviers de départ : " + ", ".join(f"{k} ← {v}" for k, v in levers["sources"].items()))
    if competitor_count == 0:
        st.warning("Aucune ligne concurrentielle complète n'a été reconstruite. Vérifie que les études de comparaison sont bien enregistrées dans erp_etudes pour l'ancrage ou un mois antérieur.")
if not backtest_summary.empty:
    _sel = backtest_summary.loc[backtest_summary["method"] == selected_method]
    best_wape=float((_sel.iloc[0] if not _sel.empty else backtest_summary.iloc[0])["WAPE"])*100.0
    n_backtest = int(backtest_details["target_index"].nunique()) if (backtest_details is not None and not backtest_details.empty and "target_index" in backtest_details.columns) else 0
    st.info(f"Validation forecast : méthode retenue **{selected_method}** · WAPE hors-échantillon **{best_wape:.1f} %** sur {n_backtest} mois testé(s) (sur {len(sales_to_anchor)} mois réels). Décision préparée pour **{period_label_from_index(period_index(anchor)+1)}**.")
else:
    st.info(f"Validation forecast : historique trop court pour comparer les modèles. Décision préparée pour **{period_label_from_index(period_index(anchor)+1)}**.")
if cal:
    st.info(f"Calibration descriptive régularisée : {cal.confidence} — R² descriptif={cal.r2:.2f}. Les scénarios sont ancrés sur la PDM observée et les variations sont volontairement rétrécies lorsque l'historique est court. {cal.note}")
else:
    st.warning("Pas assez d'observations pour une calibration descriptive. Les scénarios ne seront pas calculés.")
    st.stop()
if segment_share is None:
    st.warning("Aucune PDM exploitable pour l'ancrage (ni segmentielle, ni dernière observation propre, ni PDM globale) : le moteur ne peut pas quantifier une part de marché de référence. Synchronise l'étude comparative des parts de marché (pevc…) pour l'ancrage ou un mois antérieur.")
    st.stop()
if ref.get("warning"):
    st.warning(ref["warning"])
if segment_product is None:
    st.info("Le produit du segment n'est pas identifiable de façon univoque : l'effet de part de marché est appliqué à l'ensemble de la gamme.")

st.subheader("🎛️ Leviers")
base_price=float(levers["prix"])
base_quality=min(100.0,max(0.0,float(levers["qualite"])))
base_brand=float(levers["publicite_marque"])
base_prod=float(levers["publicite_produit"])
if levers["uses_defaults"]:
    st.warning("Valeurs par défaut utilisées pour : " + ", ".join(levers["uses_defaults"]) + ". Vérifie-les avant de lancer l'optimisation.")
b1,b2,b3,b4=st.columns(4)
price=b1.number_input("Prix du segment (€)",min_value=1.0,value=base_price,step=1.0)
quality=b2.slider("Qualité cible",0.0,100.0,base_quality,0.5)
brand=b3.number_input("Publicité marque mensuelle (€)",min_value=0.0,value=base_brand,step=500.0)
prodads=b4.number_input("Publicité produits mensuelle (€)",min_value=0.0,value=base_prod,step=100.0)
budget=st.number_input("Budget marketing mensuel maximum (€)",min_value=0.0,value=max(100000.0,base_brand+base_prod),step=1000.0)

st.subheader("🏭 Contrainte industrielle")
st.caption(f"Capacité : {cap.machines} machines ; {cap.capacity_min:,.0f} min/machine/mois. Source : {cap.source}.")

baseline_scenario={"price":price,"quality":quality,"brand_ads":brand,"product_ads":prodads}
competitors.attrs["baseline_scenario"]=baseline_scenario
base_eval=evaluate_scenario(forecast_df,segment_product,segment_share,segment_units,cal,competitors,baseline_scenario,budget,cap)
if not base_eval["budget_feasible"]:
    st.warning("La configuration actuelle dépasse le budget marketing mensuel.")
if base_eval["max_demand_capacity_utilization"] > 1.0 + 1e-9:
    ateliers = ", ".join(base_eval["capacity_bottlenecks"]) or "un atelier"
    st.warning(
        f"La demande prévue dépasse la capacité de {ateliers}. Le moteur plafonne désormais la production à la capacité, "
        f"avec environ {base_eval['average_unserved_units_month']:,.0f} unités de demande non servies par mois en moyenne "
        f"({base_eval['service_rate']*100:.1f} % de la demande du produit segment suivi servie). Ces scénarios restent comparables, "
        "mais leur PDM réalisable est corrigée par cette limite."
    )
elif base_eval["max_demand_capacity_utilization"] > 0.90:
    st.info(f"La demande utilise jusqu'à {base_eval['max_demand_capacity_utilization']*100:.1f} % de la capacité avant plafonnement.")

quality_values=sorted(set([max(0,base_quality-10),max(0,base_quality-5),base_quality,min(100,base_quality+5),min(100,base_quality+10)]))
candidates=generate_candidate_grid(baseline_scenario,budget,quality_values)

if st.button("🚀 Lancer l'optimisation", type="primary", width="stretch"):
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
        if competitors.empty:
            st.error("Aucun scénario n'a pu être évalué : benchmark concurrentiel incomplet. Les volumes de demande/capacité restent diagnostiquables, mais la PDM ne peut pas être estimée correctement.")
        else:
            st.error("Aucun scénario ne respecte le budget ou une capacité indispensable est nulle. Consulte le diagnostic ci-dessus et les paramètres de capacité.")
        st.stop()
    st.subheader("🏆 Scénarios les plus performants")
    st.dataframe(ranked, width="stretch", hide_index=True)

    best=ranked.iloc[0].to_dict()
    st.success(f"Meilleur scénario exploratoire : PDM segmentielle réalisable estimée à {best['PDM segment réalisable (%)']:.2f} %, soit {best['Δ PDM réalisable vs base (pts)']:+.2f} point(s) vs la base. PDM potentielle avant contrainte : {best['PDM potentielle (%)']:.2f} %.")
    st.caption("Ce résultat est une analyse de sensibilité descriptive sur les entreprises observées ; il ne constitue pas une prévision causale certifiée.")

    # Projection des volumes du meilleur scénario sur l'horizon.
    best_scenario={"price":float(best["prix (€)"]),"quality":float(best["qualité cible"]),"brand_ads":float(best["publicité marque (€)"]),"product_ads":float(best["publicité produits (€)"])}
    best_eval=evaluate_scenario(forecast_df,segment_product,segment_share,segment_units,cal,competitors,best_scenario,budget,cap)
    view_cols = [c for c in ["periode", *PRODUCTS, "scenario_total", "demand_total", "unserved_demand", "production_factor"] if c in best_eval["forecast"].columns]
    view=best_eval["forecast"][view_cols].copy()
    view.rename(columns={"scenario_total":"Unités servies", "demand_total":"Demande estimée", "unserved_demand":"Demande non servie", "production_factor":"Taux de service"},inplace=True)
    st.subheader("📈 Impact volume du scénario choisi")
    st.dataframe(view.round(1),width="stretch",hide_index=True)

    if best_eval["max_demand_capacity_utilization"] > 1.0 + 1e-9:
        st.warning(f"Avant plafonnement, la demande solliciterait jusqu'à {best_eval['max_demand_capacity_utilization']*100:.1f} % de la capacité. La projection publiée ci-dessus montre les unités servies et non la demande théorique complète.")
    else:
        st.info(f"Pression maximale de la demande sur l'horizon : {best_eval['max_demand_capacity_utilization']*100:.1f} % de la capacité.")
else:
    st.info("Le moteur est prêt. Lance l'optimisation pour comparer plusieurs centaines de combinaisons de prix, publicité et qualité sous contrainte de budget et capacité.")

st.subheader("⚠️ Limites méthodologiques actuelles")
st.write("Le moteur ne modélise pas encore de manière causale la distribution, la disponibilité des matières, le BFR, la trésorerie ou les nouveaux investissements. Ces éléments seront ajoutés dans la prochaine couche de simulation.")
st.write("Lorsque l'historique atteindra plusieurs périodes, la calibration sera ré-estimée automatiquement ; les scénarios pourront alors être confrontés à un backtest hors-échantillon.")
