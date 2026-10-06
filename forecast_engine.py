"""Prévision Subakoua : Holt-Winters / ETS + saisonnalité structurelle.

Règles :
- >= 24 observations mensuelles pour un Holt-Winters saisonnier pleinement
  estimé (2 cycles de 12 mois) ;
- entre 8 et 23 observations : Holt amorti sur série désaisonnalisée +
  saisonnalité structurelle issue des études Subakoua ;
- < 8 observations : tendance fortement régularisée + saisonnalité
  structurelle, avec incertitude élargie.

Les effets causaux du prix, de la publicité et de la qualité ne sont pas
inventés dans ce module : ils seront calibrés séparément dès que l'historique
contiendra suffisamment d'observations et de variation décisionnelle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing, Holt
except Exception:  # pragma: no cover
    ExponentialSmoothing = None
    Holt = None

MONTH_NAMES = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
PRODUCTS = ["Shorty 3", "Integral 3", "Shorty 5", "Integral 5", "Integral 7"]
PRODUCT_GROUP = {"Shorty 3": 3, "Integral 3": 3, "Shorty 5": 5, "Integral 5": 5, "Integral 7": 7}
PERIOD_INDEX = {f"Année {y} - {m}": (y - 1) * 12 + i for y in range(1, 4) for i, m in enumerate(MONTH_NAMES)}


@dataclass(frozen=True)
class ForecastResult:
    product: str
    history_count: int
    model: str
    forecast: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    seasonal_factors: tuple[float, ...]


@dataclass(frozen=True)
class MarketForecastResult:
    target_period: str
    horizon_months: tuple[str, ...]
    own_sales_forecast: tuple[float, ...]
    market_volume_proxy: tuple[float, ...]
    baseline_share_forecast: tuple[float, ...]
    target_share: float
    required_units_for_target: tuple[float, ...]
    unit_gap: tuple[float, ...]
    current_market_share: float | None
    current_total_market: float | None
    calibration_method: str


def period_index(period: str) -> int:
    return PERIOD_INDEX.get(period, 10_000)


def period_label_from_index(idx: int) -> str:
    return f"Année {idx // 12 + 1} - {MONTH_NAMES[idx % 12]}"


def month_index_from_label(label: str) -> int | None:
    try:
        return MONTH_NAMES.index(label.rsplit(" - ", 1)[1])
    except Exception:
        return None


def ordered_periods(periods: Sequence[str]) -> list[str]:
    return sorted({p for p in periods if p in PERIOD_INDEX}, key=period_index)


def _json_load(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        obj = json.loads(value)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def build_period_data(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        period = str(row.get("periode", ""))
        module = str(row.get("module", ""))
        if period and module:
            out.setdefault(period, {})[module] = _json_load(row.get("contenu"))
    return out


def extract_own_sales_history(period_data: Mapping[str, Mapping[str, Any]], products: Sequence[str] = PRODUCTS) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period in ordered_periods(period_data):
        di = period_data[period].get("donnees_internes", {})
        table = di.get("Marketing", {}).get("Ventes mensuelles", {}).get("Tableau_1", [])
        match = next((r for r in table if isinstance(r, dict) and str(r.get("Mois", "")).strip().lower() == period.lower()), None)
        if not match:
            continue
        row = {"periode": period, "index": period_index(period)}
        for product in products:
            try:
                row[product] = max(0.0, float(match.get(product, 0) or 0))
            except Exception:
                row[product] = 0.0
        rows.append(row)
    return pd.DataFrame(rows).sort_values("index").reset_index(drop=True) if rows else pd.DataFrame(columns=["periode", "index", *products])


def extract_structural_seasonality(period_data: Mapping[str, Mapping[str, Any]]) -> dict[int, dict[int, float]]:
    tables = []
    for period in ordered_periods(period_data):
        table = period_data[period].get("etudes_marche", {}).get("Etudes structurelles", {}).get("Prévision des ventes", {}).get("Tableau_2", [])
        if isinstance(table, list) and table:
            tables.append(table)
    if not tables:
        return {}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in tables[-1]:
        try:
            grouped.setdefault(int(row.get("Produit")), []).append(row)
        except Exception:
            continue
    out: dict[int, dict[int, float]] = {}
    for group, rows in grouped.items():
        raw = []
        for month in MONTH_NAMES:
            vals = []
            for row in rows:
                try:
                    vals.append(float(row.get(month, 0) or 0))
                except Exception:
                    pass
            raw.append(float(np.mean(vals)) if vals else 0.0)
        mean = float(np.mean(raw)) or 1.0
        out[group] = {i: max(0.01, v / mean) for i, v in enumerate(raw)}
    return out


def extract_market_potential(period_data: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    for period in reversed(ordered_periods(period_data)):
        rows = period_data[period].get("etudes_marche", {}).get("Etudes structurelles", {}).get("Prévision des ventes", {}).get("Tableau_1", [])
        for row in rows if isinstance(rows, list) else []:
            if str(row.get("Colonne_0", "")).strip().lower() == "marché potentiel":
                return {p: max(0.0, float(row.get(p, 0) or 0)) for p in PRODUCTS}
    return {}


def extract_competitive_snapshot(period_data: Mapping[str, Mapping[str, Any]], own_company_number: int = 3) -> dict[str, Any]:
    periods = ordered_periods(period_data)
    if not periods:
        return {"period": None, "rows": [], "own": None, "total_market": None, "share": None}
    period = periods[-1]
    veille = period_data[period].get("veille_concurrentielle", {})
    perf = veille.get("Performance commerciale", {})
    sales_rows = perf.get("Ventes", {}).get("Tableau_1", []) if isinstance(perf, dict) else []
    share_rows = perf.get("Parts de marché", {}).get("Tableau_1", []) if isinstance(perf, dict) else []
    shares = {int(r["Entreprise"]): r for r in share_rows if isinstance(r, dict) and str(r.get("Entreprise", "")).isdigit()}
    prices: dict[int, float] = {}
    quality: dict[int, float] = {}
    for section in (veille.get("Facteurs clés", {}) or {}).values():
        rows = section.get("Tableau_1", []) if isinstance(section, dict) else []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not str(r.get("Entreprise", "")).isdigit():
                continue
            c = int(r["Entreprise"])
            for key in ("Prix (€)", "Prix"):
                if r.get(key) not in (None, ""):
                    try: prices[c] = float(r[key])
                    except Exception: pass
            if r.get("Qualité") not in (None, ""):
                try: quality[c] = float(r["Qualité"])
                except Exception: pass
    if not prices:
        rows = veille.get("Décisions marketing", {}).get("Prix", {}).get("Tableau_1", []) if isinstance(veille, dict) else []
        for r in rows if isinstance(rows, list) else []:
            if str(r.get("Entreprise", "")).isdigit():
                try: prices[int(r["Entreprise"])] = float(r.get("Prix", r.get("Prix (€)", 0)) or 0)
                except Exception: pass
    rows: list[dict[str, Any]] = []
    for r in sales_rows if isinstance(sales_rows, list) else []:
        if not isinstance(r, dict) or not str(r.get("Entreprise", "")).isdigit():
            continue
        c = int(r["Entreprise"])
        rows.append({"entreprise": c, "ventes": float(r.get("Ventes", 0) or 0), "part_marche": float(shares.get(c, {}).get("en quantité (%)", 0) or 0), "part_marche_valeur": float(shares.get(c, {}).get("en valeur (%)", 0) or 0), "prix": prices.get(c), "qualite": quality.get(c), "is_own": c == own_company_number})
    own = next((r for r in rows if r["is_own"]), None)
    total_market = sum(r["ventes"] for r in rows) if rows else None
    share = own["part_marche"] / 100 if own and own.get("part_marche") else ((own["ventes"] / total_market) if own and total_market else None)
    return {"period": period, "rows": rows, "own": own, "total_market": total_market, "share": share}


def _damped_linear_forecast(values: np.ndarray, horizon: int) -> np.ndarray:
    if len(values) == 0:
        return np.zeros(horizon)
    if len(values) == 1:
        return np.repeat(max(0.0, values[-1]), horizon)
    x = np.arange(len(values), dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    slope *= 0.65
    return np.maximum(0.0, intercept + slope * (len(values) + np.arange(horizon)))


def forecast_series(series: Sequence[float], future_month_indices: Sequence[int], structural_factors: Mapping[int, float] | None = None, seasonal_periods: int = 12) -> ForecastResult:
    values = np.asarray([max(0.0, float(v)) for v in series], dtype=float)
    n = len(values)
    horizon = len(future_month_indices)
    factors = dict(structural_factors or {i: 1.0 for i in range(12)})
    if horizon == 0:
        return ForecastResult("", n, "aucun", (), (), tuple(float(factors.get(i, 1.0)) for i in range(12)))
    future_factors = np.asarray([float(factors.get(i % 12, 1.0)) for i in future_month_indices], dtype=float)
    if ExponentialSmoothing is not None and n >= 24 and np.nanstd(values) > 0:
        try:
            fit = ExponentialSmoothing(values, trend="add", seasonal="add", seasonal_periods=seasonal_periods, damped_trend=True, initialization_method="estimated").fit(optimized=True)
            pred = np.maximum(0.0, np.asarray(fit.forecast(horizon), dtype=float))
            resid = values - np.asarray(fit.fittedvalues, dtype=float)
            sigma = float(np.nanstd(resid)) or 0.0
            return ForecastResult("", n, "Holt-Winters additif (ETS, 12 mois)", tuple(pred), tuple(np.maximum(0.0, pred - 1.96*sigma)), tuple(np.maximum(0.0, pred + 1.96*sigma)), tuple(float(factors.get(i, 1.0)) for i in range(12)))
        except Exception:
            pass
    hist_factors = np.asarray([max(0.01, float(factors.get(i % 12, 1.0))) for i in range(n)], dtype=float)
    deseason = values / hist_factors
    if Holt is not None and n >= 8 and np.nanstd(deseason) > 0:
        try:
            fit = Holt(deseason, damped_trend=True, initialization_method="estimated").fit(optimized=True)
            base = np.maximum(0.0, np.asarray(fit.forecast(horizon), dtype=float))
            sigma = float(np.nanstd(deseason - np.asarray(fit.fittedvalues, dtype=float))) or 0.0
            pred = base * future_factors
            return ForecastResult("", n, "Holt amorti + saisonnalité structurelle", tuple(pred), tuple(np.maximum(0.0, (base - 1.65*sigma)*future_factors)), tuple(np.maximum(0.0, (base + 1.65*sigma)*future_factors)), tuple(float(factors.get(i, 1.0)) for i in range(12)))
        except Exception:
            pass
    base = _damped_linear_forecast(deseason, horizon)
    sigma = float(np.std(deseason - np.mean(deseason))) if n > 1 else max(1.0, float(values[-1])*0.25 if n else 1.0)
    pred = base * future_factors
    return ForecastResult("", n, "Tendance amortie + saisonnalité structurelle", tuple(pred), tuple(np.maximum(0.0, (base - 1.96*sigma)*future_factors)), tuple(np.maximum(0.0, (base + 1.96*sigma)*future_factors)), tuple(float(factors.get(i, 1.0)) for i in range(12)))


def build_12m_forecast(sales_history: pd.DataFrame, structural: Mapping[int, Mapping[int, float]], anchor_period: str, products: Sequence[str] = PRODUCTS) -> tuple[pd.DataFrame, dict[str, ForecastResult]]:
    if sales_history.empty or anchor_period not in PERIOD_INDEX:
        return pd.DataFrame(), {}
    anchor_idx = period_index(anchor_period)
    year_start = (anchor_idx // 12) * 12
    year_end = year_start + 11
    hist = sales_history.loc[sales_history["index"] <= anchor_idx].copy().sort_values("index")
    last_hist_idx = int(hist["index"].max()) if not hist.empty else anchor_idx
    future_indices = list(range(last_hist_idx + 1, year_end + 1))
    out_results: dict[str, ForecastResult] = {}
    for product in products:
        ser = hist[product].astype(float).to_numpy() if product in hist else np.array([])
        factors = structural.get(PRODUCT_GROUP.get(product, 3), {i: 1.0 for i in range(12)})
        r = forecast_series(ser, [i % 12 for i in future_indices], factors)
        out_results[product] = ForecastResult(product, len(ser), r.model, r.forecast, r.lower, r.upper, r.seasonal_factors)
    rows = []
    hist_map = {int(r["index"]): r for _, r in hist.iterrows()}
    for idx in range(year_start, year_end + 1):
        row = {"index": idx, "periode": period_label_from_index(idx)}
        status = "Réel" if idx in hist_map else "Prévision"
        row["statut"] = status
        for product in products:
            if idx in hist_map:
                row[product] = float(hist_map[idx][product])
            elif idx > last_hist_idx and idx in future_indices:
                j = future_indices.index(idx)
                row[product] = float(out_results[product].forecast[j])
            else:
                row[product] = np.nan
        row["Total unités"] = float(sum(float(row[p] or 0) for p in products))
        rows.append(row)
    return pd.DataFrame(rows), out_results


def compute_market_forecast(forecast_df: pd.DataFrame, structural: Mapping[int, Mapping[int, float]], market_potential: Mapping[str, float], competitive: Mapping[str, Any], target_share: float, anchor_period: str) -> MarketForecastResult:
    target_share = min(0.95, max(0.0, float(target_share)))
    future = forecast_df.loc[forecast_df["statut"] == "Prévision"].copy()
    if future.empty:
        return MarketForecastResult(anchor_period, (), (), (), (), target_share, (), (), competitive.get("share"), competitive.get("total_market"), "aucune prévision")
    own = future["Total unités"].astype(float).to_numpy()
    current_market = competitive.get("total_market")
    current_share = competitive.get("share")
    actual = forecast_df.loc[forecast_df["statut"] == "Réel"].tail(1)
    own_current = float(actual["Total unités"].iloc[0]) if not actual.empty else 0.0
    if (current_market is None or current_market <= 0) and current_share and own_current > 0:
        current_market = own_current / current_share
    if current_market is None or current_market <= 0:
        current_market = max(own_current, float(np.sum(own)), 1.0)
    proxies=[]
    for period in future["periode"]:
        mi = month_index_from_label(period) or 0
        proxy = sum(float(pot or 0) * float(structural.get(PRODUCT_GROUP.get(product, 3), {}).get(mi, 1.0)) for product, pot in market_potential.items())
        proxies.append(proxy)
    proxies=np.asarray(proxies,float)
    anchor_proxy=None
    if not actual.empty and market_potential:
        mi=month_index_from_label(str(actual["periode"].iloc[0])) or 0
        anchor_proxy=sum(float(pot or 0)*float(structural.get(PRODUCT_GROUP.get(product,3),{}).get(mi,1.0)) for product,pot in market_potential.items())
    if anchor_proxy and anchor_proxy>0:
        market=proxies*(current_market/anchor_proxy)
        method="marché calibré sur volume observé + saisonnalité structurelle"
    else:
        market=np.maximum(1.0,proxies)
        method="proxy marché structurel non calibré"
    baseline=np.divide(own,market,out=np.zeros_like(own),where=market>0)
    required=market*target_share
    gap=np.maximum(0.0,required-own)
    return MarketForecastResult(anchor_period, tuple(future["periode"]), tuple(own), tuple(market), tuple(baseline), target_share, tuple(required), tuple(gap), float(current_share) if current_share is not None else None, float(current_market) if current_market is not None else None, method)
