"""Page 6 — Analyse quantitative : point d'équilibre, marge et mix optimal.

Lecture seule pour tous. La page rassemble les données réelles du mois
(prix, coûts unitaires variables, charges fixes, volumes, capacité) puis
répond à trois questions :

1. **Quel volume faut-il vendre pour couvrir les charges fixes ?**
   (point d'équilibre, en unités et en chiffre d'affaires, marge de sécurité)
2. **Cet équilibre est-il atteignable ?** la capacité industrielle limite le
   volume : si la marge maximale atteignable est inférieure aux charges
   fixes, l'équilibre est hors de portée — l'application le dit.
3. **Comment maximiser la marge ?** le mix proposé sert d'abord les produits
   les plus rentables par unité de capacité rare, sans jamais dépasser un
   atelier.

Chaque chiffre affiché indique sa source ; aucune valeur n'est inventée pour
combler une donnée manquante.
"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import text

from auth import setup as auth_setup
from break_even_engine import (
    ProductEconomics,
    capacity_units_per_product,
    compute_break_even,
    sensitivity_price,
)
from constants import (
    CAPACITY_MIN_PER_MACHINE,
    DEFAULT_TIMES,
    PRODUCTS,
    PRODUCT_KEYS,
    PRODUCT_KEY_TO_NAME,
    TAUX_CHARGES_SOCIALES,
    TOURS_PERIODES,
)
from db_utils import get_read_engine
from finance_engine import extract_financial_state
from forecast_engine import (
    build_period_data,
    extract_own_sales_history,
    extract_structural_seasonality,
    latest_observed_period,
    period_index,
    rolling_forecast_12m,
)
from livrable_finance import couts_from_cost_study
from period_context import normalize_period, pick_reference_row

st.set_page_config(page_title="Analyse quantitative", layout="wide")
auth_setup()
st.title("📐 Analyse quantitative — point d'équilibre & mix optimal")
st.caption(
    "Combien faut-il vendre pour couvrir les charges fixes ? Cet équilibre est-il "
    "atteignable avec la capacité industrielle ? Quel mix maximise la marge ?"
)

engine = get_read_engine()
if engine is None:
    st.error("Connexion Aiven indisponible.")
    st.stop()

TOURS = sorted(TOURS_PERIODES)
tour = st.selectbox(
    "Période analysée",
    TOURS,
    index=TOURS.index(8) if 8 in TOURS else 0,
    format_func=lambda t: f"Tour {t} — {TOURS_PERIODES[t]}",
)
periode = TOURS_PERIODES[int(tour)]
tour_m1 = max(1, int(tour) - 1)
periode_m1 = TOURS_PERIODES[tour_m1]


# --------------------------------------------------------------------------- accès base
def _decode(value):
    for _ in range(2):
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return {}
        else:
            break
    return value if isinstance(value, dict) else {}


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
    except Exception:
        return []


def _table(name: str) -> pd.DataFrame:
    rows = _rows(f"SELECT * FROM {name}")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


rows_donnees = _rows("SELECT periode, module, contenu FROM erp_donnees WHERE type_donnee='etat_actuel'")
rows_etudes = _rows("SELECT periode, study_id, payload FROM erp_etudes WHERE read_allowed = 1")
df_mkt = _table("Decisions_Marketing")
df_rh = _table("Decisions_RH_Mensuel")
df_ventes = _table("Ventes_Historique")
df_fin = _table("Finances_Mensuelles")
df_parc = _table("Parc_Machines_Mensuel")
df_temps = _table("Parametres_TempsAteliers")


def _ligne(df: pd.DataFrame, tour_id: int) -> dict:
    """Dernière ligne d'un tour ≤ `tour_id` (jamais vers l'avenir)."""
    return pick_reference_row(df, tour_id).data


def _periode_data(periode_cible: str) -> dict:
    """Données d'une période : modules `erp_donnees` + études achetées."""
    data: dict = {}
    cible = normalize_period(periode_cible)
    for row in rows_donnees:
        if normalize_period(row.get("periode")) == cible:
            data.setdefault("etat_actuel", {})[str(row.get("module"))] = _decode(row.get("contenu"))
    for row in rows_etudes:
        if normalize_period(row.get("periode")) == cible:
            data[f"etude_{row.get('study_id')}"] = _decode(row.get("payload"))
    return data


# --------------------------------------------------------------------------- état financier
etat = None
try:
    etat = extract_financial_state(periode, _periode_data(periode), target_tour_id=int(tour))
except Exception:
    etat = None

# --------------------------------------------------------------------------- sources
mkt_m1 = _ligne(df_mkt, tour_m1)
rh_m1 = _ligne(df_rh, tour_m1)
ventes_m1 = _ligne(df_ventes, tour_m1)
fin_m1 = _ligne(df_fin, tour_m1)
parc_m1 = _ligne(df_parc, tour_m1)

# Coûts unitaires variables : étude de coûts de production du mois, sinon
# dernier mois antérieur disponible (jamais vers l'avenir).
couts_unitaires: dict[str, float] = {}
couts_src = "non disponible"
for t in range(int(tour), 0, -1):
    payload = _periode_data(TOURS_PERIODES[t]).get("etude_productionCostDetailStudy") or {}
    if payload.get("productsProductionCostDetails"):
        couts, _warns = couts_from_cost_study(payload)
        if couts:
            couts_unitaires = {k: float(v) for k, v in couts.items()}
            couts_src = f"productionCostDetailStudy — {TOURS_PERIODES[t]}"
            if t != int(tour):
                couts_src += " (reconducté, mois non acheté)"
            break

# Volumes : ventes réelles M-1, avec repli sur la prévision.
ventes = {k: float(ventes_m1.get(f"Ventes_{k.upper()}", 0) or 0) for k in PRODUCT_KEYS}

prevision = {}
try:
    period_data = build_period_data(rows_donnees)
    study_data = {}
    ventes_hist = extract_own_sales_history(period_data, study_data=study_data)
    struct = extract_structural_seasonality(period_data, study_data=study_data)
    anchor = latest_observed_period(ventes_hist)
    if anchor:
        prevision_df, _ = rolling_forecast_12m(ventes_hist, struct, anchor, horizon=12)
        if prevision_df is not None and not prevision_df.empty and "statut" in prevision_df.columns:
            suite = prevision_df[prevision_df["statut"] == "Prévision"]
            if not suite.empty:
                ligne = suite.iloc[0]
                prevision = {k: float(ligne.get(k, 0) or 0) for k in PRODUCTS}
except Exception:
    prevision = {}

demande = {k: prevision.get(PRODUCT_KEY_TO_NAME[k], 0.0) for k in PRODUCT_KEYS}

# Capacité : parc M-1 et temps d'atelier.
machines = {
    "Decoupe": int(parc_m1.get("Nb_Machines_Decoupe", 10) or 0),
    "Assemblage": int(parc_m1.get("Nb_Machines_Assemblage", 16) or 0),
    "Conditionnement": int(parc_m1.get("Nb_Machines_Cond", 4) or 0),
}
temps: dict[str, dict[str, float]] = {p: dict(v) for p, v in DEFAULT_TIMES.items()}
if not df_temps.empty:
    for _, row in df_temps.iterrows():
        produit = str(row.get("produit", "")).strip()
        atelier = str(row.get("atelier", "")).strip()
        minutes = row.get("temps_normal")
        if produit in DEFAULT_TIMES and atelier and minutes is not None:
            alias = {"cond": "Conditionnement", "conditionnement": "Conditionnement",
                     "decoupe": "Decoupe", "assemblage": "Assemblage"}
            cible = alias.get(atelier.casefold(), atelier)
            if cible in DEFAULT_TIMES[produit]:
                temps[produit][cible] = float(minutes or 0.0)
cap_minutes = {w: machines.get(w, 0) * CAPACITY_MIN_PER_MACHINE for w in machines}


def _nb(row: dict, key: str, default: float = 0.0) -> float:
    val = row.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _fmt_n(valeur, dec: int = 0) -> str:
    """Nombre avec séparateur de milliers espace (sans casser le texte)."""
    try:
        return f"{float(valeur):,.{dec}f}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _fmt_eur(valeur, dec: int = 0) -> str:
    return f"{_fmt_n(valeur, dec)} €"


# --------------------------------------------------------------------------- entrées modifiables
st.subheader("1 · Données d'entrée")
st.caption("Toutes les valeurs sont modifiables : l'analyse se recalcule immédiatement. La source de chaque donnée est rappelée sous les champs.")

with st.expander("💶 Prix de vente HT par produit", expanded=True):
    source_prix = "catalogue observé (Détail des comptes)" if not mkt_m1 else f"Decisions_Marketing (tour {tour_m1})"
    if mkt_m1:
        st.caption(f"Source par défaut : {source_prix}.")
    else:
        st.caption(
            "Aucune décision marketing enregistrée : prix catalogue observés en repli "
            "(130 / 205 / 240 / 310 / 360 €) — à ajuster si le marché a bougé."
        )
    prix_defaut = {
        "s3": _nb(mkt_m1, "Prix_S3", 130.0),
        "i3": _nb(mkt_m1, "Prix_I3", 205.0),
        "s5": _nb(mkt_m1, "Prix_S5", 240.0),
        "i5": _nb(mkt_m1, "Prix_I5", 310.0),
        "i7": _nb(mkt_m1, "Prix_I7", 360.0),
    }
    c_prix = st.columns(5)
    prix = {}
    for idx, key in enumerate(PRODUCT_KEYS):
        with c_prix[idx]:
            prix[key] = st.number_input(
                f"{PRODUCT_KEY_TO_NAME[key]} (€)", min_value=0.0, step=1.0,
                value=float(prix_defaut[key]), key=f"aq_prix_{tour}_{key}",
            )

with st.expander("🧱 Coût variable unitaire par produit", expanded=True):
    st.caption(
        f"Source : {couts_src}. Coût direct (matières + ateliers + conditionnement) par "
        "unité vendue. Les charges fixes n'en font pas partie."
    )
    c_cout = st.columns(5)
    cout = {}
    for idx, key in enumerate(PRODUCT_KEYS):
        with c_cout[idx]:
            cout[key] = st.number_input(
                f"{PRODUCT_KEY_TO_NAME[key]} (€/u)", min_value=0.0, step=0.1,
                value=float(couts_unitaires.get(PRODUCT_KEY_TO_NAME[key], 0.0)),
                key=f"aq_cout_{tour}_{key}",
            )

with st.expander("🏢 Charges fixes du mois", expanded=True):
    masse_defaut = 0.0
    for pole in ("Prod", "Appro", "Admin"):
        for cat in ("Employes", "Cadres", "Directeurs"):
            masse_defaut += _nb(rh_m1, f"Eff_{cat}_{pole}", 0.0) * _nb(rh_m1, f"Sal_{cat}_{pole}", 0.0)
    if masse_defaut <= 0:
        masse_defaut = _nb(fin_m1, "Remuneration_Personnel", 0.0)

    social_defaut = _nb(fin_m1, "Charges_Sociales", 0.0) or masse_defaut * TAUX_CHARGES_SOCIALES
    mkg_defaut = (
        _nb(mkt_m1, "Budget_Marque", 0.0)
        + sum(_nb(mkt_m1, f"Pub_{k.upper()}", 0.0) for k in PRODUCT_KEYS)
    )
    amort_defaut = _nb(fin_m1, "Dotations_Amortissements", 0.0) or (
        float(etat.dotations_amortissements) if etat and etat.dotations_amortissements is not None else 0.0
    )
    aace_defaut = float(etat.aace) if etat and etat.aace is not None else 0.0
    deprec_defaut = float(etat.depreciations) if etat and etat.depreciations is not None else 0.0

    c_f1, c_f2, c_f3 = st.columns(3)
    with c_f1:
        st.markdown("**Personnel & structure**")
        ff_masse = st.number_input("Masse salariale (€)", value=float(masse_defaut), step=1000.0, key=f"aq_ms_{tour}")
        ff_social = st.number_input("Charges sociales (€)", value=float(social_defaut), step=1000.0, key=f"aq_cs_{tour}")
        ff_assur = st.number_input("Assurances (€)", value=5000.0, step=500.0, key=f"aq_ass_{tour}",
                                   help="Hypothèse du simulateur : 3 assurances (RC, dommages, pertes d'exploitation) moins 1 000 € de bundle.")
    with c_f2:
        st.markdown("**Exploitation**")
        ff_mkg = st.number_input("Publicité (€)", value=float(mkg_defaut), step=1000.0, key=f"aq_mkg_{tour}")
        ff_aace = st.number_input("Autres charges externes (€)", value=float(aace_defaut), step=1000.0, key=f"aq_aace_{tour}")
        ff_deprec = st.number_input("Dépréciations (€)", value=float(deprec_defaut), step=100.0, key=f"aq_dep_{tour}")
    with c_f3:
        st.markdown("**Amortissements**")
        ff_amort = st.number_input("Dotations aux amortissements (€)", value=float(amort_defaut), step=1000.0, key=f"aq_am_{tour}")
        st.caption(
            "Masse salariale : `Decisions_RH_Mensuel` (M-1) ou `Finances_Mensuelles`. "
            "Charges sociales : 50 % de la masse. Publicité : décisions marketing M-1. "
            "Dotations, AACE et dépréciations : état financier extrait du mois."
        )
    charges_fixes = ff_masse + ff_social + ff_assur + ff_mkg + ff_aace + ff_deprec + ff_amort
    st.metric("Charges fixes retenues", _fmt_eur(charges_fixes))

# --------------------------------------------------------------------------- moteur
produits = [
    ProductEconomics(
        key=key,
        name=PRODUCT_KEY_TO_NAME[key],
        price=float(prix[key]),
        unit_variable_cost=float(cout[key]),
        current_units=float(ventes.get(key, 0.0)),
        demand_units=float(demande.get(key, 0.0)) or None,
    )
    for key in PRODUCT_KEYS
]

mix_ref = {p.key: p.current_units for p in produits}
if sum(mix_ref.values()) <= 0:
    mix_ref = {p.key: (p.demand_units or 0.0) for p in produits}

resultat = compute_break_even(
    produits,
    charges_fixes,
    mix=mix_ref,
    allocation="revenue",
    times=temps,
    capacity_minutes=cap_minutes,
    demand_caps={p.key: p.demand_units for p in produits if p.demand_units},
)

# --------------------------------------------------------------------------- résultats
st.subheader("2 · Point d'équilibre")
if resultat.break_even_units is None:
    st.error("Point d'équilibre non calculable : aucun volume de référence. Saisissez des ventes ou attendez une prévision exploitable.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Point d'équilibre", f"{_fmt_n(resultat.break_even_units)} u")
    c2.metric("CA d'équilibre", _fmt_eur(resultat.break_even_revenue))
    if resultat.margin_of_safety_units is not None:
        c3.metric(
            "Marge de sécurité",
            f"{_fmt_n(resultat.margin_of_safety_units)} u",
            delta=f"{(resultat.margin_of_safety_ratio or 0) * 100:+.1f} % du volume vendu",
        )
    else:
        c3.metric("Marge de sécurité", "—")
    c4.metric(
        "Taux de marge pondéré",
        f"{(resultat.weighted_margin_ratio or 0) * 100:.1f} %",
        help="Marge sur coût variable rapportée au chiffre d'affaires, mix de référence.",
    )

    if resultat.current_units > 0:
        st.caption(
            f"Volume vendu au dernier mois connu : **{_fmt_n(resultat.current_units)} u** "
            f"pour **{_fmt_eur(resultat.current_revenue)}** de CA et "
            f"**{_fmt_eur(resultat.current_margin)}** de marge sur coût variable."
        )

st.subheader("3 · Économie unitaire par produit")
lignes = []
for p in resultat.products:
    seuil = resultat.break_even_units_by_product.get(p.key, 0.0)
    lignes.append({
        "Produit": p.name,
        "Prix HT (€)": round(p.price, 2),
        "Coût variable (€/u)": round(p.unit_variable_cost, 2),
        "Marge unitaire (€)": round(p.margin_per_unit, 2),
        "Taux de marge": round((p.margin_ratio or 0) * 100, 1),
        "Vendu M-1 (u)": round(p.current_units),
        "CA (€)": round(p.revenue),
        "Marge (€)": round(p.margin),
        "Charges fixes imputées (€)": round(resultat.fixed_cost_allocation.get(p.key, 0.0)),
        "Seuil d'équilibre (u)": (round(seuil) if seuil != float("inf") else "n/a"),
        "Écart au seuil (u)": (round(p.current_units - seuil) if seuil != float("inf") else "n/a"),
    })
st.dataframe(pd.DataFrame(lignes), width="stretch", hide_index=True)
st.caption(
    "Charges fixes imputées au chiffre d'affaires de chaque produit ; le seuil par produit "
    "est le volume qui couvre cette part imputée avec la marge unitaire du produit."
)

if resultat.current_units > 0:
    chart_df = pd.DataFrame([
        {
            "Produit": p.name,
            "Vendu M-1 (u)": round(p.current_units),
            "Seuil d'équilibre (u)": round(resultat.break_even_units_by_product.get(p.key, 0.0)),
        }
        for p in resultat.products
    ])
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            x=alt.X("Produit:N", sort=chart_df["Produit"].tolist(), title=None),
            y=alt.Y("Unités:Q", title="Unités"),
            color=alt.Color("Produit:N", legend=None),
        )
        .properties(height=260)
    )
    st.altair_chart(chart, width="stretch")

st.subheader("4 · Capacité industrielle et mix optimal")
c5, c6, c7 = st.columns(3)
c5.metric("Machines", f"{machines['Decoupe']} / {machines['Assemblage']} / {machines['Conditionnement']}")
c6.metric("Capacité (min/mois)", _fmt_n(sum(cap_minutes.values())))
c7.metric(
    "Marge max sous capacité",
    _fmt_eur(resultat.max_margin_under_capacity) if resultat.max_margin_under_capacity is not None else "—",
)

if resultat.equilibrium_reachable is False:
    st.error(
        f"⚠️ Équilibre **hors de portée** de la capacité industrielle : la marge maximale "
        f"atteignable ({_fmt_eur(resultat.max_margin_under_capacity)}) est inférieure aux charges "
        f"fixes ({_fmt_eur(charges_fixes)}), soit {_fmt_eur(resultat.capacity_gap)} à gagner. "
        "Il faut augmenter la capacité, les prix, ou réduire les charges fixes."
    )
elif resultat.equilibrium_reachable:
    st.success("✅ L'équilibre est atteignable : la marge maximale sous capacité couvre les charges fixes.")

if resultat.optimal_mix:
    st.dataframe(pd.DataFrame([
        {
            "Produit": e.name,
            "Unités": round(e.units),
            "CA (€)": round(e.revenue),
            "Marge (€)": round(e.margin),
            "Découpe (min)": round(e.minutes.get("Decoupe", 0.0)),
            "Assemblage (min)": round(e.minutes.get("Assemblage", 0.0)),
            "Conditionnement (min)": round(e.minutes.get("Conditionnement", 0.0)),
        }
        for e in resultat.optimal_mix
    ]), width="stretch", hide_index=True)
    st.caption(
        f"Mix proposé : **{_fmt_n(resultat.optimal_mix_units)} u** pour "
        f"**{_fmt_eur(resultat.optimal_mix_revenue)}** de CA et "
        f"**{_fmt_eur(resultat.optimal_mix_margin)}** de marge sur coût variable. "
        "Heuristique gloutonne (marge par fraction de capacité consommée) : solution réalisable, "
        "pas un optimum exact."
    )

    maxi = capacity_units_per_product(produits, temps, cap_minutes)
    st.dataframe(pd.DataFrame([
        {"Produit": p.name, "Volume max seul (u)": round(maxi.get(p.key, 0.0))}
        for p in produits
    ]), width="stretch", hide_index=True)
    st.caption("Volume maximal par produit s'il était produit seul (contrainte d'atelier).")

st.subheader("5 · Sensibilité du point d'équilibre à une variation de prix")
st.caption("Les prix de la section 1 sont repris tels quels ; ce tableau mesure l'effet d'un choc de prix appliqué à toute la gamme.")
tab_sens = []
for delta in (-0.10, -0.05, 0.0, 0.05, 0.10):
    sim = sensitivity_price(produits, charges_fixes, delta, mix=mix_ref)
    avant, apres = sim["avant"], sim["apres"]
    tab_sens.append({
        "Variation prix": f"{delta * 100:+.0f} %",
        "Équilibre (u)": round(apres.break_even_units) if apres.break_even_units is not None else "—",
        "Écart (u)": round(sim["delta_break_even_units"]) if sim["delta_break_even_units"] is not None else "—",
        "CA d'équilibre (€)": round(apres.break_even_revenue) if apres.break_even_revenue is not None else "—",
        "Marge au volume actuel (€)": round(apres.current_margin),
    })
st.dataframe(pd.DataFrame(tab_sens), width="stretch", hide_index=True)

if resultat.warnings:
    st.warning("Points de vigilance :\n- " + "\n- ".join(resultat.warnings))
st.caption(
    f"Période analysée : {periode} · référence M-1 : {periode_m1} · "
    f"coûts unitaires : {couts_src}."
)
