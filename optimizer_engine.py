"""Optimiseur stratégique exploratoire Subakoua.

Objectif : chercher des combinaisons de leviers qui améliorent la part de marché
sur le segment concurrentiel observé, tout en respectant un budget marketing et
une capacité industrielle simplifiée.

Important : avec peu de périodes historiques, le modèle est DESCRIPTIF et non
causal. Il utilise une régression ridge réguliarisée sur les parts de marché
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

PRODUCTS = ["Shorty 3", "Integral 3", "Shorty 5", "Integral 5", "Integral 7"]
DEFAULT_TIMES = {
    "Shorty 3": {"Decoupe": 2.0, "Assemblage": 4.0, "Conditionnement": 1.0},
    "Integral 3": {"Decoupe": 3.0, "Assemblage": 6.0, "Conditionnement": 1.0},
    "Shorty 5": {"Decoupe": 2.5, "Assemblage": 5.0, "Conditionnement": 1.0},
    "Integral 5": {"Decoupe": 3.5, "Assemblage": 7.0, "Conditionnement": 1.0},
    "Integral 7": {"Decoupe": 4.0, "Assemblage": 8.0, "Conditionnement": 1.5},
}
DEFAULT_NOMENCLATURE = {
    "Shorty 3": {"Néoprène 3": 1.1, "Renforts": 12.0, "Manchons": 2.0, "Fermetures": 1.0},
    "Integral 3": {"Néoprène 3": 1.6, "Renforts": 18.0, "Manchons": 4.0, "Fermetures": 1.0},
    "Shorty 5": {"Néoprène 5": 1.1, "Renforts": 12.0, "Manchons": 2.0, "Fermetures": 1.0},
    "Integral 5": {"Néoprène 5": 1.6, "Renforts": 18.0, "Manchons": 4.0, "Fermetures": 1.0},
    "Integral 7": {"Néoprène 7": 1.6, "Renforts": 22.0, "Manchons": 4.0, "Fermetures": 1.0},
}
CAPACITY_MIN_PER_MACHINE = 9600.0


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
        for r in snap.get("rows", []) or []:
            try:
                company_id = int(float(r.get("entreprise")))
            except Exception:
                continue
            rows.append({
                "periode": period,
                "entreprise": company_id,
                "part_marche": _num(r.get("part_marche")),
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
    if segment_product in demand.columns:
        demand[segment_product] = pd.to_numeric(demand[segment_product], errors="coerce").fillna(0.0).clip(lower=0.0) * ratio
    product_cols = [p for p in PRODUCTS if p in demand.columns]
    for product in product_cols:
        demand[product] = pd.to_numeric(demand[product], errors="coerce").fillna(0.0).clip(lower=0.0)
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
    segment_demand_total = float(demand[segment_product].sum()) if segment_product in demand else 0.0
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
        if segment_product in product_cols:
            segment_served_total += float(served.at[idx, segment_product])

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
    return df.sort_values(["PDM segment réalisable (%)","pression capacité sur demande (%)","dépense marketing mensuelle (€)"], ascending=[False,True,True]).head(top_n).reset_index(drop=True)
