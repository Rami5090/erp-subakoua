from __future__ import annotations

import json
import os
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from forecast_engine import (
    PRODUCTS,
    build_period_data,
    build_study_data,
    extract_own_sales_history,
    extract_structural_seasonality,
    extract_market_potential,
    extract_competitive_snapshot,
    build_12m_forecast,
    compute_market_forecast,
    ordered_periods,
)
from strategic_engine import current_competitive_metrics, strategic_alerts

st.set_page_config(page_title="Pilotage stratégique 12 mois", layout="wide")
st.title("🧭 Pilotage stratégique — prévision 12 mois")
st.caption("Holt-Winters/ETS + saisonnalité structurelle + position concurrentielle. Source prioritaire : payloads API validés stockés dans erp_etudes ; fallback sur erp_donnees historique.")


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


engine = get_engine()
if engine is None:
    st.error("Connexion Aiven indisponible.")
    st.stop()

try:
    with engine.connect() as conn:
        legacy = pd.read_sql(text("SELECT periode, module, contenu FROM erp_donnees ORDER BY id ASC"), conn)
        try:
            studies = pd.read_sql(text("SELECT periode, study_id, label, module, price, read_allowed, payload FROM erp_etudes ORDER BY id ASC"), conn)
        except Exception:
            studies = pd.DataFrame(columns=["periode", "study_id", "label", "module", "price", "read_allowed", "payload"])
except Exception as exc:
    st.error(f"Lecture Aiven impossible : {exc}")
    st.stop()

if legacy.empty and studies.empty:
    st.warning("Aucune donnée disponible dans erp_donnees / erp_etudes.")
    st.stop()

period_data = build_period_data(legacy.to_dict("records"))
study_data = build_study_data(studies.loc[studies["read_allowed"].fillna(False).astype(bool)].to_dict("records")) if not studies.empty else {}
periods = ordered_periods(set(period_data) | set(study_data))
if not periods:
    st.error("Aucune période exploitable dans les données." )
    st.stop()

anchor = st.selectbox("Période d'ancrage du plan", periods, index=len(periods) - 1, help="Les observations jusqu'à cette période servent à estimer le reste de l'année.")
own_company = st.number_input("N° entreprise pilotée", 1, 9, 3, 1)

sales = extract_own_sales_history(period_data, study_data=study_data)
struct = extract_structural_seasonality(period_data, study_data=study_data)
potential = extract_market_potential(period_data, study_data=study_data)
competitive = extract_competitive_snapshot(period_data, int(own_company), study_data=study_data)
metrics = current_competitive_metrics(competitive)
current_share = competitive.get("share")
default_target = min(0.50, max(0.01, float(current_share or 0.10) + 0.03))
target_share = st.slider("🎯 Objectif de part de marché", 0.01, 0.50, float(default_target), 0.005, format="%.1f%%")

with st.expander("🔎 Diagnostic des sources", expanded=False):
    st.write(f"Périodes historiques : {len(period_data)} | Études API : {len(studies)} lignes")
    st.write(f"Historique de ventes reconnu : {len(sales)} période(s)")
    st.write(f"Saisonnalité structurelle : {'disponible' if struct else 'non disponible'}")
    st.write(f"Marché potentiel : {'disponible' if potential else 'non disponible'}")
    if not sales.empty:
        st.dataframe(sales[["periode", *PRODUCTS]].tail(12).round(1), width="stretch", hide_index=True)

forecast_df, results = build_12m_forecast(sales, struct, anchor)
if forecast_df.empty:
    st.error("Impossible de construire le forecast : aucune série de ventes valide n'a été reconnue jusqu'à la période d'ancrage.")
    st.info("Le pilote accepte désormais les ventes API ensaacvm (payload brut) et les anciennes données normalisées. Vérifie que l'étude « Ventes mensuelles » a bien été synchronisée dans erp_etudes ou que le contenu de erp_donnees contient une table de ventes exploitable.")
    st.stop()

market = compute_market_forecast(forecast_df, struct, potential, competitive, target_share, anchor)

c1, c2, c3, c4 = st.columns(4)
c1.metric("PDM actuelle", f"{current_share * 100:.2f} %" if current_share is not None else "—")
own_snapshot = competitive.get("own") or {}
current_sales = own_snapshot.get("ventes")
if current_sales is None:
    actual_rows = forecast_df.loc[forecast_df["statut"] == "Réel"].tail(1)
    current_sales = float(actual_rows["Total unités"].iloc[0]) if not actual_rows.empty else 0.0
c2.metric("Ventes actuelles", f"{current_sales:,.0f} u")
c3.metric("Prix actuel", f"{metrics['own_price']:.2f} €" if metrics.get('own_price') is not None else "—")
c4.metric("Concurrents observés", str(metrics.get("competitor_count", 0)))

st.subheader("📈 Trajectoire 12 mois")
chart = forecast_df.set_index("periode")[PRODUCTS + ["Total unités"]]
st.line_chart(chart, height=380)
st.dataframe(forecast_df[["periode", "statut", *PRODUCTS, "Total unités"]].round(1), width="stretch", hide_index=True)

st.subheader("🎯 Part de marché : trajectoire de base vs cible")
mt = pd.DataFrame({
    "Période": market.horizon_months,
    "Ventes prévues (u)": market.own_sales_forecast,
    "Marché projeté (u)": market.market_volume_proxy,
    "PDM base (%)": [x * 100 for x in market.baseline_share_forecast],
    "PDM cible (%)": [target_share * 100] * len(market.horizon_months),
    "Unités à produire/vendre pour cible": market.required_units_for_target,
    "Écart à combler (u)": market.unit_gap,
})
st.dataframe(mt.round(1), width="stretch", hide_index=True)

m1, m2, m3 = st.columns(3)
m1.metric("PDM projetée fin d'année", f"{market.baseline_share_forecast[-1] * 100:.2f} %" if market.baseline_share_forecast else "—")
m2.metric("Écart moyen", f"{sum(market.unit_gap) / len(market.unit_gap):,.0f} u" if market.unit_gap else "0 u")
m3.metric("Méthode marché", market.calibration_method)
for alert in strategic_alerts(list(market.baseline_share_forecast), target_share, current_share):
    st.warning("⚠️ " + alert)

st.subheader("🏁 Position concurrentielle")
if competitive.get("rows"):
    dfc = pd.DataFrame(competitive["rows"]).sort_values(["part_marche", "ventes"], ascending=False)
    st.dataframe(dfc.round(2), width="stretch", hide_index=True)
else:
    st.info("Les données concurrentielles détaillées ne sont pas disponibles dans les sources actuellement synchronisées.")

st.subheader("🧪 Méthode scientifique")
st.info("Le moteur ne force pas un Holt-Winters annuel lorsque l'historique est insuffisant. Avec < 8 observations, il utilise une tendance amortie et la saisonnalité structurelle ; de 8 à 23 mois, Holt amorti sur série désaisonnalisée ; à partir de 24 mois, ETS/Holt-Winters saisonnier 12 mois. Les effets causaux prix/publicité/qualité restent séparés tant qu'ils ne sont pas calibrés statistiquement.")
