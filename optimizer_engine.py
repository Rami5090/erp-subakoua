"""Optimiseur stratégique exploratoire Subakoua.

Objectif : chercher des combinaisons de leviers qui améliorent la part de marché
sur le segment concurrentiel observé, tout en respectant un budget marketing et
une capacité industrielle simplifiée.

Important : avec peu de périodes historiques, le modèle est DESCRIPTIF et non
causal. Il utilise une régression ridge régularisée sur les parts de marché
observées par entreprise et période, puis une normalisation softmax pour produire
un score de choix relatif. Les scénarios doivent être interprétés comme une
analyse de sensibilité, pas comme une élasticité causale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import math
import numpy as np
import pandas as pd

from constants import (
    PRODUCTS,
    DEFAULT_TIMES,
    DEFAULT_NOMENCLATURE,
    CAPACITY_MIN_PER_MACHINE,
)


@dataclass(frozen=True)
class Calibration:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    n_obs: int
    n_periods: int
    r2: float
    ridge_alpha: float
    confidence: str
    note: str


@dataclass(frozen=True)
class CapacityProfile:
    machines: dict[str, int]
    times: dict[str, dict[str, float]]
    capacity_min: float = CAPACITY_MIN_PER_MACHINE
    source: str = "fallback"


@dataclass(frozen=True)
class FleetUpdate:
    """Parc machines après application des achats et des cessions."""

    parc: dict[str, int]
    cessions_retenues: dict[str, int]
    warnings: tuple[str, ...] = ()


def apply_fleet_decisions(
    parc_actuel: Mapping[str, Any],
    achats: Mapping[str, Any] | None = None,
    cessions: Mapping[str, Any] | None = None,
) -> FleetUpdate:
    """Applique les achats et les cessions au parc machines.

    On ne peut céder que ce que l'on possède : une cession supérieure au parc
    est ramenée au parc réellement détenu, et le parc final ne descend jamais
    sous zéro. Toute cession bridée est signalée — jamais corrigée en silence —
    pour que l'interface puisse prévenir l'utilisateur.
    """
    achats = achats or {}
    cessions = cessions or {}
    parc: dict[str, int] = {}
    retenues: dict[str, int] = {}
    warnings: list[str] = []
    for atelier, valeur in parc_actuel.items():
        actuel = max(0, int(_num(valeur) or 0))
        achete = max(0, int(_num(achats.get(atelier)) or 0))
        demande = max(0, int(_num(cessions.get(atelier)) or 0))
        cede = min(demande, actuel)
        if demande > cede:
            warnings.append(
                f"Cession {atelier} bridée : {demande} machine(s) demandée(s) pour {actuel} au parc — "
                f"seule(s) {cede} machine(s) cède(nt) le parc."
            )
        parc[str(atelier)] = max(0, actuel + achete - cede)
        retenues[str(atelier)] = cede
    return FleetUpdate(parc, retenues, tuple(warnings))


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if not math.isfinite(x):
            return None
        return x
    except Exception:
        return None


def _zscore(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = np.nanmean(values, axis=0)
    scale = np.nanstd(values, axis=0)
    scale[~np.isfinite(scale) | (scale < 1e-9)] = 1.0
    return (values - mean) / scale, mean, scale


def calibrate_descriptive_response(history: pd.DataFrame, ridge_alpha: float = 8.0) -> Calibration | None:
    """Calibre une relation descriptive log(share) ~ prix + qualité + publicité."""
    required = {"part_marche", "prix", "qualite", "publicite", "publicite_produit", "entreprise"}
    if history is None or history.empty or not required.issubset(history.columns):
        return None
    df = history.copy()
    for col in ["part_marche", "prix", "qualite", "publicite", "publicite_produit"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["part_marche", "prix", "qualite", "publicite", "publicite_produit"])
    df = df[(df["part_marche"] > 0) & (df["part_marche"] < 100) & (df["prix"] > 0)]
    if len(df) < 6:
        return None

    X_raw = np.column_stack([
        df["prix"].to_numpy(float),
        df["qualite"].to_numpy(float),
        np.log1p(np.maximum(0.0, df["publicite"].to_numpy(float))),
        np.log1p(np.maximum(0.0, df["publicite_produit"].to_numpy(float))),
    ])
    feature_names = ("prix", "qualite", "log_publicite_marque", "log_publicite_produit")
    X, mean, scale = _zscore(X_raw)
    y = np.log(np.clip(df["part_marche"].to_numpy(float) / 100.0, 1e-6, 1 - 1e-6))
    X1 = np.column_stack([np.ones(len(X)), X])
    reg = np.diag([0.0] + [ridge_alpha] * X.shape[1])
    beta = np.linalg.solve(X1.T @ X1 + reg, X1.T @ y)
    pred = X1 @ beta
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
    r2 = 1.0 - ss_res / ss_tot
    confidence = "faible" if len(set(df.get("periode", []))) < 3 or len(df) < 18 else ("modérée" if len(df) < 36 else "bonne")
    note = "Descriptif, non causal. Les coefficients ne doivent pas être lus comme des élasticités structurelles." \
        if confidence != "bonne" else "Descriptif régularisé ; une validation temporelle reste nécessaire."
    return Calibration(tuple(feature_names), tuple(mean.tolist()), tuple(scale.tolist()), tuple(beta[1:].tolist()), float(beta[0]),
                       int(len(df)), int(df["periode"].nunique()) if "periode" in df else 1, float(r2), float(ridge_alpha), confidence, note)


def predict_relative_share(calibration: Calibration, scenario: Mapping[str, float], competitors: pd.DataFrame) -> float:
    """Prédit une PDM relative par softmax des utilités des entreprises."""
    feature_means = np.asarray(calibration.means, dtype=float)
    feature_scales = np.asarray(calibration.scales, dtype=float)
    beta = np.asarray(calibration.coefficients, dtype=float)

    def utility(price: float, quality: float, brand_ads: float, product_ads: float) -> float:
        raw = np.array([price, quality, math.log1p(max(0.0, brand_ads)), math.log1p(max(0.0, product_ads))], dtype=float)
        z = (raw - feature_means) / feature_scales
        return calibration.intercept + float(z @ beta)

    utilities: list[float] = []
    own_price = float(scenario["price"])
    own_quality = float(scenario["quality"])
    own_brand = float(scenario["brand_ads"])
    own_product = float(scenario["product_ads"])
    utilities.append(utility(own_price, own_quality, own_brand, own_product))
    if competitors is not None and not competitors.empty:
        for _, r in competitors.iterrows():
            utilities.append(utility(float(r["prix"]), float(r["qualite"]), float(r["publicite"]), float(r["publicite_produit"])))
    arr = np.asarray(utilities, dtype=float)
    arr = arr - np.max(arr)
    ex = np.exp(np.clip(arr, -30, 30))
    return float(ex[0] / ex.sum())


def build_competitive_history(period_data: Mapping[str, Mapping[str, Any]], own_company: int, extract_snapshot_fn, periods: Sequence[str] | None = None) -> pd.DataFrame:
    """Construit l'historique concurrentiel à partir des périodes legacy et/ou API.

    ``periods`` permet d'inclure les mois présents uniquement dans erp_etudes ;
    auparavant, l'historique se limitait aux clés de erp_donnees.
    """
    rows: list[dict[str, Any]] = []
    period_list = list(periods) if periods is not None else list(period_data.keys())
    for period in sorted(set(period_list)):

        try:
            snap = extract_snapshot_fn(period)
        except Exception:
            continue
        snap_rows = snap.get("rows", []) or []
        # v2.9.2 : si l'étude ne publie pas la colonne de PDM, on la reconstruit à partir des ventes
        # du benchmark ; sinon toutes les lignes étaient écartées et la calibration devenait impossible.
        sales_total = sum(v for v in (_num(x.get("ventes")) for x in snap_rows) if v and v > 0)
        n_sales = sum(1 for x in snap_rows if (_num(x.get("ventes")) or 0) > 0)
        for r in snap_rows:
            try:
                company_id = int(float(r.get("entreprise")))
            except Exception:
                continue
            share_pct = _num(r.get("part_marche"))
            if share_pct is None and n_sales >= 2 and sales_total > 0 and (_num(r.get("ventes")) or 0) > 0:
                share_pct = float(_num(r.get("ventes"))) / sales_total * 100.0
            rows.append({
                "periode": period,
                "entreprise": company_id,
                "part_marche": share_pct,
                "prix": _num(r.get("prix")),
                "qualite": _num(r.get("qualite")),
                "publicite": _num(r.get("publicite")),
                "publicite_produit": _num(r.get("publicite_produit")) or 0.0,
                "ventes": _num(r.get("ventes")),
                "is_own": int(r.get("entreprise")) == own_company,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.dropna(subset=["entreprise", "part_marche", "prix", "qualite", "publicite"]).reset_index(drop=True)



def latest_competitor_frame(history: pd.DataFrame, own_company: int, anchor_period: str, products_ads_default: float = 0.0) -> pd.DataFrame:
    """Retourne une ligne récente exploitable par concurrent, sans fuite future."""
    cols = ["entreprise", "prix", "qualite", "publicite", "publicite_produit"]
    empty = pd.DataFrame(columns=cols)
    if history is None or history.empty:
        return empty
    df = history.copy()
    if "periode" in df.columns:
        from forecast_engine import period_index
        df = df.loc[df["periode"].map(period_index) <= period_index(anchor_period)].copy()
        df["_period_index"] = df["periode"].map(period_index)
    else:
        df["_period_index"] = 0
    if df.empty:
        return empty
    df["entreprise"] = pd.to_numeric(df["entreprise"], errors="coerce")
    df = df.dropna(subset=["entreprise"])
    df = df.loc[df["entreprise"].astype(int) != int(own_company)].copy()
    for col in ["prix", "qualite", "publicite", "publicite_produit"]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["publicite_produit"] = df["publicite_produit"].fillna(products_ads_default)
    df = df.dropna(subset=["prix", "qualite", "publicite"])
    if df.empty:
        return empty
    df = df.sort_values(["_period_index"]).drop_duplicates("entreprise", keep="last")
    return df[cols].reset_index(drop=True)


def _safe_competitor_rows(rows: Sequence[Mapping[str, Any]], own_company: int) -> pd.DataFrame:
    records=[]
    for r in rows or []:
        try:
            c=int(r.get("entreprise"))
        except Exception:
            continue
        if c == own_company:
            continue
        if not all(_num(r.get(k)) is not None for k in ["prix", "qualite", "publicite"]):
            continue
        records.append({
            "entreprise": c,
            "prix": float(r["prix"]),
            "qualite": float(r["qualite"]),
            "publicite": float(r["publicite"]),
            "publicite_produit": float(_num(r.get("publicite_produit")) or 0.0),
        })
    return pd.DataFrame(records).drop_duplicates("entreprise") if records else pd.DataFrame(columns=["entreprise","prix","qualite","publicite","publicite_produit"])



def predict_anchored_share(
    calibration: Calibration,
    scenario: Mapping[str, float],
    baseline_scenario: Mapping[str, float],
    baseline_share: float,
) -> float:
    """Projette une variation de PDM à partir d'un niveau de référence observé.

    On n'utilise pas la régression pour inventer le niveau absolu : le niveau
    de départ est ancré sur la PDM réellement observée et seul le changement
    d'utilité entre la décision candidate et la décision de référence est
    appliqué. Un shrinkage dépendant de la confiance limite les extrapolations
    lorsque peu de périodes sont disponibles.
    """
    mean = np.asarray(calibration.means, dtype=float)
    scale = np.asarray(calibration.scales, dtype=float)
    beta = np.asarray(calibration.coefficients, dtype=float)

    def u(sc: Mapping[str, float]) -> float:
        raw=np.array([float(sc["price"]), float(sc["quality"]),
                      math.log1p(max(0.0,float(sc["brand_ads"]))),
                      math.log1p(max(0.0,float(sc["product_ads"])))], dtype=float)
        return calibration.intercept + float(((raw-mean)/scale) @ beta)

    confidence_shrink = {"faible":0.35, "modérée":0.65, "bonne":0.90}.get(calibration.confidence,0.35)
    delta = confidence_shrink * (u(scenario) - u(baseline_scenario))
    base = min(max(float(baseline_share),1e-5),1-1e-5)
    logit = math.log(base/(1-base)) + delta
    return float(1.0/(1.0+math.exp(-max(-30.0,min(30.0,logit)))))

def evaluate_scenario(
    baseline_forecast: pd.DataFrame,
    segment_product: str,
    baseline_segment_share: float | None,
    baseline_segment_units: float | None,
    calibration: Calibration | None,
    competitors: pd.DataFrame,
    scenario: Mapping[str, float],
    budget_monthly: float,
    capacity: CapacityProfile,
) -> dict[str, Any]:
    """Évalue un scénario en limitant la production à la capacité disponible.

    La prévision initiale représente la demande potentielle. Un scénario dont la
    demande dépasse la capacité n'est pas éliminé automatiquement : les volumes
    servis sont plafonnés à la capacité mensuelle, la demande non servie est
    mesurée et la PDM réalisable est réduite à proportion du service rendu pour
    le produit segment suivi. Seul un dépassement budgétaire (ou une capacité
    nulle rendant la production impossible) rend le scénario irréalisable.
    """
    spend = float(scenario["brand_ads"] + scenario["product_ads"])
    base_share = float(baseline_segment_share) if baseline_segment_share and baseline_segment_share > 0 else None
    potential_share = None
    if calibration and base_share is not None:
        base_sc = competitors.attrs.get("baseline_scenario") if hasattr(competitors, "attrs") else None
        if not isinstance(base_sc, Mapping):
            base_sc = {"price": scenario["price"], "quality": scenario["quality"], "brand_ads": scenario["brand_ads"], "product_ads": scenario["product_ads"]}
        potential_share = predict_anchored_share(calibration, scenario, base_sc, base_share)
    ratio = (potential_share / base_share) if (potential_share is not None and base_share) else 1.0

    demand = baseline_forecast.copy()
    product_cols = [p for p in PRODUCTS if p in demand.columns]
    for product in product_cols:
        demand[product] = pd.to_numeric(demand[product], errors="coerce").fillna(0.0).clip(lower=0.0)
    # v2.9.2 : si le produit du segment n'est pas identifiable (segment_product vide ou inconnu),
    # l'effet PDM est appliqué à l'ensemble de la gamme au lieu d'un produit arbitraire.
    scaled_products = [segment_product] if segment_product in product_cols else list(product_cols)
    for product in scaled_products:
        demand[product] = demand[product] * ratio
    demand["demand_total"] = demand[product_cols].sum(axis=1)
    served = demand.copy()

    machines = {str(k): max(0.0, float(v or 0)) for k, v in (capacity.machines or {}).items()}
    atelier_names = ("Decoupe", "Assemblage", "Conditionnement")
    demand_pressure_rows: list[float] = []
    served_util_rows: list[float] = []
    production_factors: list[float] = []
    capacity_bottlenecks: set[str] = set()
    capacity_impossible = False
    unserved_by_month: list[float] = []
    segment_demand_total = float(demand[scaled_products].sum().sum()) if scaled_products else 0.0
    segment_served_total = 0.0

    for idx, row in demand.iterrows():
        loads = {a: 0.0 for a in atelier_names}
        for product in product_cols:
            units = max(0.0, float(row.get(product, 0.0) or 0.0))
            times = capacity.times.get(product, {}) if capacity.times else {}
            for atelier, t in times.items():
                normalized = {"Cond": "Conditionnement", "conditionnement": "Conditionnement", "decoupe": "Decoupe", "assemblage": "Assemblage"}.get(str(atelier).strip(), str(atelier).strip())
                if normalized in loads:
                    loads[normalized] += units * max(0.0, float(t or 0.0))
        factors = []
        pressure = 0.0
        for atelier, load in loads.items():
            cap_minutes = machines.get(atelier, 0.0) * float(capacity.capacity_min)
            if load > 1e-9 and cap_minutes <= 0:
                factors.append(0.0)
                pressure = float("inf")
                capacity_bottlenecks.add(atelier)
                capacity_impossible = True
            elif cap_minutes > 0:
                util = load / cap_minutes
                pressure = max(pressure, util)
                factors.append(min(1.0, cap_minutes / load) if load > 0 else 1.0)
                if util > 1.0 + 1e-9:
                    capacity_bottlenecks.add(atelier)
            else:
                # Atelier inutilisé : il n'impose pas de contrainte.
                factors.append(1.0)
        factor = max(0.0, min([1.0, *factors]))
        production_factors.append(factor)
        demand_pressure_rows.append(pressure)
        unserved = float(row[product_cols].sum()) * (1.0 - factor)
        unserved_by_month.append(max(0.0, unserved))
        for product in product_cols:
            served.at[idx, product] = float(row.get(product, 0.0) or 0.0) * factor
        served_total = float(sum(float(served.at[idx, product]) for product in product_cols))
        served.at[idx, "scenario_total"] = served_total
        served_util = 0.0
        for atelier, load in loads.items():
            cap_minutes = machines.get(atelier, 0.0) * float(capacity.capacity_min)
            if cap_minutes > 0:
                served_util = max(served_util, load * factor / cap_minutes)
            elif load > 1e-9:
                served_util = float("inf")
        served_util_rows.append(served_util)
        segment_served_total += float(sum(float(served.at[idx, product]) for product in scaled_products))

    served["unserved_demand"] = unserved_by_month
    served["production_factor"] = production_factors
    avg_service = (segment_served_total / segment_demand_total) if segment_demand_total > 0 else 1.0
    predicted_share = (potential_share * avg_service) if potential_share is not None else None
    feasible = spend <= float(budget_monthly) + 1e-9 and not capacity_impossible
    max_pressure = max(demand_pressure_rows, default=0.0)
    max_served_util = max(served_util_rows, default=0.0)
    return {
        "predicted_share": predicted_share,
        "potential_share": potential_share,
        "share_delta": (predicted_share - base_share) if predicted_share is not None and base_share is not None else None,
        "ratio_vs_baseline": ratio,
        "marketing_spend": spend,
        "annual_marketing_spend": spend * len(served),
        "forecast": served,
        "demand_forecast": demand,
        "max_capacity_utilization": max_served_util,
        "max_demand_capacity_utilization": max_pressure,
        "capacity_utilization": served_util_rows,
        "production_factors": production_factors,
        "service_rate": avg_service,
        "unserved_units_total_horizon": float(sum(unserved_by_month)),
        "average_unserved_units_month": float(np.mean(unserved_by_month)) if unserved_by_month else 0.0,
        "capacity_bottlenecks": sorted(capacity_bottlenecks),
        "budget_feasible": spend <= float(budget_monthly) + 1e-9,
        "feasible": feasible,
        "model_confidence": calibration.confidence if calibration else "non calibré",
    }

def generate_candidate_grid(base: Mapping[str, float], budget_monthly: float, quality_values: Sequence[float] | None = None) -> list[dict[str, float]]:
    price0=float(base["price"]); brand0=float(base["brand_ads"]); prod0=float(base["product_ads"]); qual0=float(base["quality"])
    price_values=np.unique(np.round(np.linspace(max(1.0, price0*0.85), price0*1.15, 7),2))
    brand_values=np.unique(np.round([0, brand0*0.5, brand0, brand0*1.25, brand0*1.5],2))
    prod_values=np.unique(np.round([0, prod0*0.5, prod0, prod0*1.25, prod0*1.5],2))
    qvals=list(quality_values) if quality_values is not None else [max(0,qual0-5), qual0, min(100,qual0+5), min(100,qual0+10)]
    out=[]
    for p in price_values:
        for b in brand_values:
            for a in prod_values:
                if b+a > budget_monthly+1e-9:
                    continue
                for q in qvals:
                    out.append({"price":float(p),"brand_ads":float(b),"product_ads":float(a),"quality":float(q)})
    return out


def rank_scenarios(evaluated: Sequence[Mapping[str, Any]], top_n: int = 10) -> pd.DataFrame:
    rows=[]
    for i,e in enumerate(evaluated):
        if not e.get("feasible"):
            continue
        s=e.get("scenario", {})
        rows.append({
            "rang": i+1,
            "prix (€)": s.get("price"),
            "publicité marque (€)": s.get("brand_ads"),
            "publicité produits (€)": s.get("product_ads"),
            "qualité cible": s.get("quality"),
            "PDM segment réalisable (%)": (e.get("predicted_share") or 0)*100 if e.get("predicted_share") is not None else None,
            "PDM potentielle (%)": (e.get("potential_share") or 0)*100 if e.get("potential_share") is not None else None,
            "Δ PDM réalisable vs base (pts)": (e.get("share_delta") or 0)*100 if e.get("share_delta") is not None else None,
            "dépense marketing mensuelle (€)": e.get("marketing_spend"),
            "service de la demande (%)": (e.get("service_rate") or 0)*100,
            "demande non servie moy. (u/mois)": e.get("average_unserved_units_month", 0.0),
            "pression capacité sur demande (%)": (e.get("max_demand_capacity_utilization") or 0)*100,
        })
    df=pd.DataFrame(rows)
    if df.empty:
        return df
    # Priorité : part de marché, puis capacité, puis coût.
    df = df.sort_values(["PDM segment réalisable (%)","pression capacité sur demande (%)","dépense marketing mensuelle (€)"], ascending=[False,True,True]).head(top_n).reset_index(drop=True)
    # v2.9.2 : le rang correspond à la position après tri (et non à l'index d'évaluation).
    df["rang"] = range(1, len(df) + 1)
    return df



def detect_segment_product(sales_row: Mapping[str, Any] | None, segment_units: float | None, tolerance: float = 0.5) -> str | None:
    """Identifie le produit dont les ventes correspondent aux ventes du segment observé.

    Retourne ``None`` si aucun produit ne correspond de façon univoque (ex. ventes = total gamme) ;
    ``evaluate_scenario`` applique alors l'effet PDM à toute la gamme. L'ancien code retombait
    silencieusement sur « Shorty 3 », ce qui déformait la demande et la capacité.
    """
    if sales_row is None or segment_units is None:
        return None
    matches = []
    for product in PRODUCTS:
        v = _num(sales_row.get(product)) if hasattr(sales_row, "get") else None
        if v is not None and abs(v - float(segment_units)) <= tolerance and v > 0:
            matches.append(product)
    return matches[0] if len(matches) == 1 else None


def _share_fraction(value: Any) -> float | None:
    v = _num(value)
    if v is None or v <= 0:
        return None
    return v / 100.0 if v > 1 else v


def resolve_reference_share(
    competitive: Mapping[str, Any] | None,
    history: pd.DataFrame | None,
    own_company: int,
    anchor_period: str,
    fallback_units: float | None = None,
) -> dict[str, Any]:
    """Détermine la PDM de référence de l'ancrage, avec traçabilité de la source.

    Ordre de priorité :
    1. PDM segmentielle du snapshot d'ancrage (colonne de PDM, ou ventes/total du benchmark) ;
    2. dernière PDM propre observée dans le benchmark au plus tard à l'ancrage ;
    3. PDM globale du tableau de bord, utilisée comme référence approchée (signalée).

    Retourne ``share`` (fraction 0-1 ou None), ``units``, ``source``, ``level`` et ``warning``.
    """
    from forecast_engine import period_index

    competitive = competitive or {}
    own = competitive.get("own") or {}
    units = _num(own.get("ventes"))
    if units is not None and units <= 0:
        units = None
    if units is None:
        units = fallback_units if fallback_units and fallback_units > 0 else None

    seg = _share_fraction(competitive.get("segment_share"))
    if seg is not None:
        src = competitive.get("segment_share_source") or "part_marche"
        label = "PDM du benchmark" if src == "part_marche" else "PDM reconstruite (ventes / total du benchmark)"
        return {"share": seg, "units": units, "level": "segment",
                "source": f"{label} · {competitive.get('period') or anchor_period}", "warning": None}

    if history is not None and not history.empty and {"entreprise", "periode", "part_marche"}.issubset(history.columns):
        h = history.copy()
        h["entreprise"] = pd.to_numeric(h["entreprise"], errors="coerce")
        h = h.loc[h["entreprise"] == int(own_company)].copy()
        h["_idx"] = h["periode"].map(period_index)
        h = h.loc[h["_idx"] <= period_index(anchor_period)].sort_values("_idx")
        for _, r in h.iloc[::-1].iterrows():
            frac = _share_fraction(r.get("part_marche"))
            if frac is not None:
                u = _num(r.get("ventes"))
                return {"share": frac, "units": units or (u if u and u > 0 else None), "level": "historique",
                        "source": f"dernière PDM propre observée · {r['periode']}",
                        "warning": f"La PDM segmentielle de {anchor_period} est absente : le moteur utilise la dernière observation propre disponible ({r['periode']})."}

    glob = _share_fraction(competitive.get("global_share"))
    if glob is not None:
        return {"share": glob, "units": units, "level": "globale",
                "source": "PDM globale du tableau de bord (référence approchée)",
                "warning": "Aucune PDM segmentielle exploitable : la PDM globale du tableau de bord sert de référence. "
                           "Les variations relatives restent valides, mais le niveau absolu est approximatif."}

    return {"share": None, "units": units, "level": None, "source": "aucune",
            "warning": "Aucune PDM exploitable (ni segmentielle, ni historique, ni globale)."}


def resolve_own_levers(own: Mapping[str, Any] | None, history: pd.DataFrame | None, own_company: int, anchor_period: str) -> dict[str, Any]:
    """Valeurs de départ des leviers (prix, qualité, publicités) avec leur provenance.

    Complète la ligne propre du snapshot par la dernière observation complète de l'historique,
    puis seulement en dernier recours par des valeurs par défaut explicitement signalées.
    """
    from forecast_engine import period_index

    defaults = {"prix": 115.0, "qualite": 57.2, "publicite_marque": 55000.0, "publicite_produit": 3000.0}
    out: dict[str, Any] = {}
    sources: dict[str, str] = {}
    own = own or {}
    last = {}
    if history is not None and not history.empty and "entreprise" in history.columns and "periode" in history.columns:
        h = history.copy()
        h["entreprise"] = pd.to_numeric(h["entreprise"], errors="coerce")
        h = h.loc[h["entreprise"] == int(own_company)].copy()
        h["_idx"] = h["periode"].map(period_index)
        h = h.loc[h["_idx"] <= period_index(anchor_period)].sort_values("_idx")
        if not h.empty:
            last = h.iloc[-1].to_dict()
    mapping = {
        "prix": (own.get("prix"), last.get("prix")),
        "qualite": (own.get("qualite"), last.get("qualite")),
        "publicite_marque": (own.get("publicite_marque", own.get("publicite")), last.get("publicite")),
        "publicite_produit": (own.get("publicite_produit"), last.get("publicite_produit")),
    }
    for key, (primary, secondary) in mapping.items():
        p, q = _num(primary), _num(secondary)
        if p is not None and (p > 0 or key.startswith("publicite")):
            out[key], sources[key] = p, "snapshot d'ancrage"
        elif q is not None and (q > 0 or key.startswith("publicite")):
            out[key], sources[key] = q, f"historique ({last.get('periode')})"
        else:
            out[key], sources[key] = defaults[key], "valeur par défaut (à vérifier)"
    out["sources"] = sources
    out["uses_defaults"] = [k for k, v in sources.items() if v.startswith("valeur par défaut")]
    return out
