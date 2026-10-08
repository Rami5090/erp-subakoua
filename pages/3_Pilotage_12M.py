from __future__ import annotations

import json
import os
import pandas as pd
import streamlit as st
import altair as alt
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
    backtest_forecast_methods,
    select_backtest_method,
    rolling_forecast_12m,
    latest_observed_period,
    compute_market_forecast,
    ordered_periods,
    period_index,
    period_label_from_index,
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


def _study_payload_has_monitoring(payloads):
    if not isinstance(payloads, dict):
        return False
    for key, value in payloads.items():
        if key == "monitoring" and isinstance(value, dict):
            return True
        if isinstance(value, dict) and ("marketShares" in value or "monitoringTurnoverData" in value):
            return True
    return False

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

sales = extract_own_sales_history(period_data, study_data=study_data)

# Diagnostics: périodes présentes dans les données vs périodes ayant des ventes
# réellement reconnues par le moteur. Cette variable doit toujours être définie
# avant l'affichage du bloc de diagnostic pour éviter un NameError lorsque
# l'utilisateur change l'ancrage (ex. juin).
recognized_sales_periods = set(sales["periode"].astype(str).tolist()) if not sales.empty else set()
missing_sales_periods = [p for p in periods if p not in recognized_sales_periods]

latest_real = latest_observed_period(sales)
if latest_real is None:
    st.error("Aucune période de ventes réellement observée n'est disponible.")
    st.stop()
# Pour empêcher toute fuite d'information, l'ancrage est limité au dernier mois
# réellement observé dans les ventes. Les études futures peuvent exister dans la
# base, mais ne doivent pas servir à préparer une décision historique.
real_periods = [p for p in periods if period_index(p) <= period_index(latest_real)]
default_anchor_idx = real_periods.index(latest_real) if latest_real in real_periods else len(real_periods) - 1
anchor = st.selectbox(
    "Période d'ancrage du plan",
    real_periods,
    index=default_anchor_idx,
    help="Dernier mois réellement observé par défaut. Les données futures ne sont jamais utilisées pour calibrer la décision.",
)
own_company = st.number_input("N° entreprise pilotée", 1, 9, 3, 1)

struct = extract_structural_seasonality(period_data, study_data=study_data)
potential = extract_market_potential(period_data, study_data=study_data)
competitive = extract_competitive_snapshot(period_data, int(own_company), study_data=study_data, anchor_period=anchor)
metrics = current_competitive_metrics(competitive)
global_share = competitive.get("global_share", competitive.get("share") if competitive.get("share_scope") == "overall_monitoring" else None)
segment_share = competitive.get("segment_share")
current_share = global_share
# Saisie en points de pourcentage : 13.0 signifie 13 %, puis conversion en fraction pour le moteur.
default_target_pct = 13.0
target_pct = st.slider("🎯 Objectif de part de marché globale", 0.0, 50.0, default_target_pct, 0.5, format="%.1f %%", help="Objectif global uniquement. Si la PDM globale n'est pas disponible, les calculs d'écart restent indéterminés.")
target_share = target_pct / 100.0

with st.expander("🔎 Diagnostic des sources", expanded=False):
    st.write(f"Périodes historiques : {len(period_data)} | Études API : {len(studies)} lignes")
    st.write(f"Historique de ventes reconnu : {len(sales)} période(s)")
    st.write(f"Périodes scrappées détectées : {len(periods)} : {', '.join(periods)}")
    if missing_sales_periods:
        st.warning("Périodes présentes dans les données mais sans ventes reconnues : " + ", ".join(missing_sales_periods))
    else:
        st.success("Toutes les périodes présentes contiennent des ventes reconnues par le moteur.")
    st.write(f"Saisonnalité structurelle : {'disponible (' + str(len(struct)) + ' familles produit)' if struct else 'NON DISPONIBLE'}")
    st.write(f"Marché potentiel structurel : {'disponible (' + str(len(potential)) + ' produits)' if potential else 'NON DISPONIBLE'}")
    # Le monitoring peut être stocké dans erp_etudes ou, pour les exports legacy,
    # directement dans erp_donnees. On ne doit pas déclarer « non disponible »
    # uniquement parce que la table API est vide.
    monitoring_legacy = any(_study_payload_has_monitoring(period_data.get(p, {})) for p in period_data)
    monitoring_api = any(_study_payload_has_monitoring(study_data.get(p, {})) for p in study_data)
    st.write(f"Tableau de bord / PDM : {'disponible' if (monitoring_api or monitoring_legacy) else 'NON DISPONIBLE'}")
    st.write(f"Concurrence détaillée : {'disponible (' + str(len(competitive.get('rows') or [])) + ' entreprises)' if competitive.get('rows') else 'NON DISPONIBLE'}")
    scope = competitive.get("share_scope")
    st.write(f"Périmètre PDM : {'global (tableau de bord)' if scope == 'overall_monitoring' else 'segment concurrentiel' if scope == 'competitive_segment' else 'indéterminé'}")
    if scope == "competitive_segment":
        st.info("Le benchmark concurrentiel couvre actuellement un segment produit ; il ne doit pas être interprété comme la PDM globale de l'entreprise.")
    if not potential:
        st.warning("Le marché potentiel structurel n'est pas synchronisé. La trajectoire de PDM globale restera indéterminée tant qu'une base de marché globale n'est pas disponible.")
    if not struct:
        st.warning("La saisonnalité structurelle n'est pas synchronisée. Avec un seul mois réel, le forecast serait une extrapolation de niveau et ne doit pas être interprété comme une prévision saisonnière fiable.")
    if not sales.empty:
        st.dataframe(sales[["periode", *PRODUCTS]].tail(12).round(1), width="stretch", hide_index=True)

# Calibration hors-échantillon sur les seules périodes réelles jusqu'à l'ancrage.
sales_to_anchor = sales.loc[sales["index"] <= period_index(anchor)].copy()
backtest_details, backtest_summary = backtest_forecast_methods(sales_to_anchor, struct)
selected_method = select_backtest_method(backtest_summary)
with st.expander("🧪 Validation hors-échantillon", expanded=False):
    st.write(f"Historique réel utilisé : {len(sales_to_anchor)} période(s) jusqu'à {anchor}.")
    if not backtest_summary.empty:
        bt = backtest_summary.copy()
        bt["WAPE"] = bt["WAPE"] * 100.0
        bt["MAPE"] = bt["MAPE"] * 100.0
        st.dataframe(bt.round({"MAE": 1, "WAPE": 2, "MAPE": 2}), width="stretch", hide_index=True)
        st.success(f"Méthode retenue pour la décision suivante : **{selected_method}** (meilleur WAPE hors-échantillon).")
    else:
        st.info("Pas assez de périodes réelles pour départager les modèles hors-échantillon : le moteur conserve le niveau saisonnier.")

# Prévision opérationnelle : mois d'ancrage réel + 12 mois futurs.
forecast_df, results = rolling_forecast_12m(sales, struct, anchor, method=selected_method, horizon=12)
if results:
    st.caption("Modèle retenu pour la trajectoire : " + selected_method + f" · ancrage réel : {anchor} · prochaine décision : {period_label_from_index(period_index(anchor)+1)}")
if forecast_df.empty:
    st.error("Impossible de construire le forecast : aucune série de ventes valide n'a été reconnue jusqu'à la période d'ancrage.")
    st.info("Le pilote accepte désormais les ventes API ensaacvm (payload brut) et les anciennes données normalisées. Vérifie que l'étude « Ventes mensuelles » a bien été synchronisée dans erp_etudes ou que le contenu de erp_donnees contient une table de ventes exploitable.")
    st.stop()

market = compute_market_forecast(forecast_df, struct, potential, competitive, target_share, anchor)

c1, c2, c3, c4 = st.columns(4)
c1.metric("PDM globale actuelle", f"{current_share * 100:.2f} %" if current_share is not None else "—")
actual_rows = forecast_df.loc[forecast_df["statut"] == "Réel"].tail(1)
current_total_sales = float(actual_rows["Total unités"].iloc[0]) if not actual_rows.empty else 0.0
c2.metric("Ventes globales actuelles", f"{current_total_sales:,.0f} u")
if segment_share is not None:
    c3.metric("PDM segment observée", f"{segment_share * 100:.2f} %")
elif metrics.get("own_price") is not None:
    c3.metric("Prix observé", f"{metrics['own_price']:.2f} €")
else:
    c3.metric("PDM segment observée", "—")
c4.metric("Concurrents observés", str(metrics.get("competitor_count", 0)))
if current_share is None:
    st.info("La PDM globale réelle n'est pas disponible pour cette période. Le benchmark concurrentiel par segment reste exploitable ; aucune PDM globale n'est inventée.")
    if segment_share is not None:
        st.caption(f"Référence segmentielle : {segment_share * 100:.2f} % ; ventes du segment de l'entreprise : {float((competitive.get('own') or {}).get('ventes') or 0):,.0f} u.")
elif competitive.get("share_scope") == "competitive_segment":
    st.info("La PDM globale réelle est distincte du benchmark segmentiel ; les deux sont affichés séparément.")
elif competitive.get("rows") and len(competitive.get("rows") or []) < 3:
    st.warning("La vue concurrentielle détaillée est incomplète. Pour piloter contre les 8 concurrents, il faut synchroniser les études de parts de marché et/ou de ventes concurrentes.")

st.subheader("📈 Trajectoire 12 mois")
chart_df = forecast_df.sort_values("index").copy()
chart_df["periode"] = pd.Categorical(chart_df["periode"], categories=chart_df["periode"].tolist(), ordered=True)
chart_long = chart_df[["periode", *PRODUCTS, "Total unités"]].melt("periode", var_name="Série", value_name="Unités")
chart = (
    alt.Chart(chart_long)
    .mark_line(point=True)
    .encode(
        x=alt.X("periode:N", sort=chart_df["periode"].tolist(), title="Période"),
        y=alt.Y("Unités:Q", title="Unités"),
        color=alt.Color("Série:N", title="Produit"),
        tooltip=[alt.Tooltip("periode:N", title="Période"), alt.Tooltip("Série:N", title="Série"), alt.Tooltip("Unités:Q", format=",.0f")]
    )
    .properties(height=380)
)
st.altair_chart(chart, width="stretch")
st.dataframe(forecast_df[["periode", "statut", *PRODUCTS, "Total unités"]].round(1), width="stretch", hide_index=True)

st.subheader("🎯 Part de marché : trajectoire de base vs cible")
mt = pd.DataFrame({
    "Période": market.horizon_months,
    "Ventes prévues (u)": market.own_sales_forecast,
    "Marché projeté (u)": market.market_volume_proxy,
    "PDM base (%)": [x * 100 if pd.notna(x) else None for x in market.baseline_share_forecast],
    "PDM cible (%)": [target_pct] * len(market.horizon_months),
    "Unités à produire/vendre pour cible": market.required_units_for_target,
    "Écart à combler (u)": market.unit_gap,
})
st.dataframe(mt.round(1), width="stretch", hide_index=True)
if market.calibration_method.startswith("marché indisponible"):
    st.warning("La PDM réelle et le marché total ne sont pas disponibles pour cette période : les calculs de PDM et d'écart cible sont laissés indéterminés plutôt que de créer un marché fictif.")
elif market.calibration_method.startswith("proxy"):
    st.info("La trajectoire de PDM est un proxy structurel : elle ne remplace pas une observation réelle du marché concurrentiel.")

m1, m2, m3 = st.columns(3)
last_share = market.baseline_share_forecast[-1] if market.baseline_share_forecast else None
m1.metric("PDM globale projetée fin d'horizon", f"{last_share * 100:.2f} %" if last_share is not None and pd.notna(last_share) else "—")
finite_gaps = [float(x) for x in market.unit_gap if pd.notna(x)]
m2.metric("Écart moyen à la cible", f"{sum(finite_gaps) / len(finite_gaps):,.0f} u" if finite_gaps else "—")
m3.metric("Méthode de marché", market.calibration_method)
for alert in strategic_alerts(list(market.baseline_share_forecast), target_share, current_share):
    st.warning("⚠️ " + alert)

st.subheader("🗓️ Position dans le cycle décisionnel")
st.info(f"Dernier mois réel reconnu : **{latest_real}** · prochaine décision : **{period_label_from_index(period_index(latest_real)+1)}** · horizon projeté : 12 mois.")

st.subheader("🧭 Prérequis pour l'optimiseur stratégique")
preq = [
    ("✅", "Ventes propres historiques", not sales.empty),
    ("✅", "Saisonnalité structurelle", bool(struct)),
    ("✅", "Benchmark concurrentiel", bool(competitive.get("rows"))),
    ("⚠️", "PDM globale réelle", current_share is not None),
    ("⚠️", "Marché global / potentiel", bool(potential) or market.current_total_market is not None),
]
pcol1, pcol2 = st.columns(2)
for i, (ico, label, ok) in enumerate(preq):
    (pcol1 if i % 2 == 0 else pcol2).write(f"{ico} {label} : {'OK' if ok else 'manquant'}")
if current_share is None or (not potential and market.current_total_market is None):
    st.warning("L'optimisation de part de marché globale ne doit pas encore être exécutée comme optimisation réelle : il manque un dénominateur de marché global et/ou une PDM globale observée. Le moteur peut néanmoins analyser le forecast et le benchmark segmentiel.")

st.subheader("🏁 Position concurrentielle")
if competitive.get("rows"):
    dfc = pd.DataFrame(competitive["rows"]).sort_values(["part_marche", "ventes"], ascending=False)
    st.dataframe(dfc.round(2), width="stretch", hide_index=True)
    if competitive.get("share_scope") == "competitive_segment":
        st.caption("Les ventes/PDM de ce tableau correspondent au segment concurrentiel observé par Subakoua ; elles ne sont pas assimilées à la PDM globale de l'entreprise.")
else:
    st.info("Les données concurrentielles détaillées ne sont pas disponibles dans les sources actuellement synchronisées.")

st.subheader("🧪 Méthode scientifique")
st.info("Le moteur verrouille l'ancrage sur le dernier mois réellement observé, compare plusieurs modèles hors-échantillon (niveau saisonnier, tendance amortie, Holt amorti) puis retient celui qui minimise l'erreur WAPE disponible. Tant que l'historique reste court, la saisonnalité structurelle Subakoua complète l'information. ETS/Holt-Winters saisonnier 12 mois n'est activé qu'avec un historique suffisamment long. Les effets causaux prix/publicité/qualité restent séparés tant qu'ils ne sont pas statistiquement validés.")
