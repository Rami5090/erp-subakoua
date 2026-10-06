"""Moteur de prévision Subakoua.

Le module accepte deux sources de données :
1) le schéma historique normalisé stocké dans ``erp_donnees`` ;
2) les payloads API bruts stockés dans ``erp_etudes`` par le scraper API-first.

Cette compatibilité est volontaire : elle évite qu'une nouvelle extraction API
rende les anciennes périodes inutilisables, et inversement.
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

MONTH_NAMES = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
    "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]
PRODUCTS = ["Shorty 3", "Integral 3", "Shorty 5", "Integral 5", "Integral 7"]
PRODUCT_GROUP = {"Shorty 3": 3, "Integral 3": 3, "Shorty 5": 5, "Integral 5": 5, "Integral 7": 7}
PRODUCT_ID_TO_NAME = {
    "SHORTY_C": "Shorty 3",
    "MONO_C": "Integral 3",
    "SHORTY_T": "Shorty 5",
    "MONO_T": "Integral 5",
    "MONO_F": "Integral 7",
}
PERIOD_INDEX = {f"Année {y} - {m}": (y - 1) * 12 + i for y in range(1, 5) for i, m in enumerate(MONTH_NAMES)}


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


def _unwrap_api_payload(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Déplie les enveloppes possibles utilisées par les synchronisations API.

    Selon la version du scraper ou du stockage, un payload peut être stocké
    directement, sous ``payload``/``response``, ou dans plusieurs niveaux
    d'enveloppe. On cherche l'objet métier plutôt que de dépendre d'un format
    unique de stockage.
    """
    if not isinstance(payload, Mapping):
        return {}
    current = dict(payload)
    for _ in range(4):
        sid = current.get("studyId")
        if sid:
            return current
        for key in ("payload", "response", "data", "result"):
            nested = current.get(key)
            if isinstance(nested, Mapping):
                current = dict(nested)
                break
        else:
            break
    return current


def _find_nested_by_key(obj: Any, predicate) -> dict[str, Any] | None:
    """Recherche prudente d'un objet métier dans une structure JSON imbriquée."""
    if isinstance(obj, Mapping):
        try:
            if predicate(obj):
                return dict(obj)
        except Exception:
            pass
        for value in obj.values():
            found = _find_nested_by_key(value, predicate)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_nested_by_key(value, predicate)
            if found is not None:
                return found
    return None


def build_period_data(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Construit le format historique ``periode -> module -> json``."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        period = str(row.get("periode", ""))
        module = str(row.get("module", ""))
        if period and module:
            out.setdefault(period, {})[module] = _json_load(row.get("contenu"))
    return out


def build_study_data(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Construit ``periode -> study_id -> payload`` depuis erp_etudes."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        period = str(row.get("periode", ""))
        study_id = str(row.get("study_id", ""))
        if period and study_id:
            payload = row.get("payload", row.get("contenu", {}))
            out.setdefault(period, {})[study_id] = _unwrap_api_payload(_json_load(payload))
    return out


def _table_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        # Quelques anciennes extractions enveloppent les lignes dans un sous-clé.
        for key in ("rows", "data", "tableau", "Tableau_1", "values"):
            if isinstance(value.get(key), list):
                return [x for x in value[key] if isinstance(x, dict)]
    return []


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            cleaned = value.replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace("€", "").replace("%", "")
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        return float(value)
    except Exception:
        return None


def _extract_sales_from_api_payload(payload: Mapping[str, Any]) -> dict[str, float]:
    payload = _unwrap_api_payload(payload)
    out: dict[str, float] = {}
    sales = payload.get("sales")
    if not isinstance(sales, list):
        nested = _find_nested_by_key(payload, lambda x: isinstance(x.get("sales"), list) or isinstance(x.get("listEnsaacvmProd"), list))
        if nested:
            payload = nested
            sales = payload.get("sales")
    if isinstance(sales, list) and sales:
        item = sales[-1] if isinstance(sales[-1], dict) else {}
        for p in item.get("monthlySalesByProducts", []) if isinstance(item.get("monthlySalesByProducts"), list) else []:
            if not isinstance(p, dict):
                continue
            name = PRODUCT_ID_TO_NAME.get(str(p.get("productId", "")), str(p.get("productId", "")))
            if name in PRODUCTS:
                v = _num(p.get("sales"))
                if v is not None:
                    out[name] = max(0.0, v)
    if not out and isinstance(payload.get("listEnsaacvmProd"), list):
        for p in payload["listEnsaacvmProd"]:
            if not isinstance(p, dict):
                continue
            name = PRODUCT_ID_TO_NAME.get(str(p.get("name", "")), str(p.get("name", "")))
            if name in PRODUCTS:
                v = _num(p.get("totalVentes", p.get("sales")))
                if v is not None:
                    out[name] = max(0.0, v)
    return out


def _recursive_find_product_rows(obj: Any) -> list[dict[str, Any]]:
    """Cherche dans une extraction DOM éventuelle une table contenant les produits."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            found.extend(_recursive_find_product_rows(item))
    elif isinstance(obj, dict):
        keys = {str(k) for k in obj}
        score = sum(1 for p in PRODUCTS if p in keys)
        if score >= 2:
            found.append(obj)
        for v in obj.values():
            found.extend(_recursive_find_product_rows(v))
    return found


def extract_own_sales_history(
    period_data: Mapping[str, Mapping[str, Any]],
    products: Sequence[str] = PRODUCTS,
    study_data: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> pd.DataFrame:
    """Extrait les ventes propres depuis API-first, puis fallback historique."""
    rows: list[dict[str, Any]] = []
    study_data = study_data or {}
    all_periods = ordered_periods(set(period_data) | set(study_data))
    for period in all_periods:
        values = {}
        payload = study_data.get(period, {}).get("ensaacvm")
        if isinstance(payload, dict):
            values = _extract_sales_from_api_payload(payload)

        if not values:
            di = period_data.get(period, {}).get("donnees_internes", {})
            marketing = di.get("Marketing", {}) if isinstance(di, dict) else {}
            table = marketing.get("Ventes mensuelles", {}).get("Tableau_1", []) if isinstance(marketing, dict) else []
            match = next((r for r in table if isinstance(r, dict) and str(r.get("Mois", "")).strip().lower() == period.lower()), None)
            if match:
                for product in products:
                    v = _num(match.get(product))
                    if v is not None:
                        values[product] = max(0.0, v)

        if not values:
            # Fallback très tolérant pour les anciennes extractions DOM :
            # recherche récursive d'un objet contenant plusieurs noms de produits.
            for candidate in _recursive_find_product_rows(period_data.get(period, {})):
                mapped = {}
                for product in products:
                    v = _num(candidate.get(product))
                    if v is not None:
                        mapped[product] = max(0.0, v)
                if len(mapped) >= 3:
                    values = mapped
                    break

        if values:
            row = {"periode": period, "index": period_index(period)}
            for product in products:
                row[product] = float(values.get(product, 0.0))
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["periode", "index", *products])
    return pd.DataFrame(rows).sort_values("index").drop_duplicates("index", keep="last").reset_index(drop=True)


def _structural_from_payload(payload: Mapping[str, Any]) -> dict[int, dict[int, float]]:
    payload = _unwrap_api_payload(payload)
    rows = payload.get("listCoefSaisonnier", [])
    if not isinstance(rows, list):
        nested = _find_nested_by_key(payload, lambda x: isinstance(x.get("listCoefSaisonnier"), list))
        rows = nested.get("listCoefSaisonnier", []) if nested else []
    out: dict[int, dict[int, float]] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("nomProd", ""))
        product = PRODUCT_ID_TO_NAME.get(pid)
        if not product:
            continue
        vals = [_num(row.get(f"coef{i}")) or 0.0 for i in range(1, 13)]
        mean = float(np.mean(vals)) or 1.0
        group = PRODUCT_GROUP[product]
        # Plusieurs produits d'une même famille partagent le même profil dans Subakoua.
        out[group] = {i: max(0.01, vals[i] / mean) for i in range(12)}
    return out


def extract_structural_seasonality(
    period_data: Mapping[str, Mapping[str, Any]],
    study_data: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[int, dict[int, float]]:
    """Extrait la saisonnalité structurelle avec plusieurs niveaux de secours.

    Priorité : étude API ``peeumreusreprv`` > tout payload contenant
    ``listCoefSaisonnier`` > ancienne extraction ``Prévision des ventes``.
    """
    study_data = study_data or {}

    # 1) API explicite par study_id.
    for period in reversed(ordered_periods(study_data)):
        payload = study_data.get(period, {}).get("peeumreusreprv")
        if isinstance(payload, Mapping):
            parsed = _structural_from_payload(payload)
            if parsed:
                return parsed

    # 2) Robustesse : certaines versions de synchronisation ont stocké le payload
    # sous une enveloppe ou avec un studyId différent mais le même contenu métier.
    for period in reversed(ordered_periods(study_data)):
        period_payloads = study_data.get(period, {})
        for payload in period_payloads.values():
            if not isinstance(payload, Mapping):
                continue
            parsed = _structural_from_payload(payload)
            if parsed:
                return parsed

    # 3) Fallback historique ``erp_donnees``.
    for period in reversed(ordered_periods(period_data)):
        etudes = period_data.get(period, {}).get("etudes_marche", {})
        if not isinstance(etudes, Mapping):
            continue
        rows = etudes.get("Etudes structurelles", {}).get("Prévision des ventes", {}).get("Tableau_2", [])
        if not isinstance(rows, list) or not rows:
            continue
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                grouped.setdefault(int(row.get("Produit")), []).append(dict(row))
            except Exception:
                continue
        out: dict[int, dict[int, float]] = {}
        for group, group_rows in grouped.items():
            raw = []
            for month in MONTH_NAMES:
                nums = [_num(r.get(month)) for r in group_rows]
                vals = [v for v in nums if v is not None and v > 0]
                raw.append(float(np.mean(vals)) if vals else 0.0)
            if not any(raw):
                continue
            mean = float(np.mean(raw)) or 1.0
            out[group] = {i: max(0.01, v / mean) for i, v in enumerate(raw)}
        if out:
            return out

    return {}

def extract_market_potential(
    period_data: Mapping[str, Mapping[str, Any]],
    study_data: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[str, float]:
    """Extrait le marché potentiel structurel avec le même niveau de robustesse que la saisonnalité."""
    study_data = study_data or {}
    for period in reversed(ordered_periods(study_data)):
        payloads = study_data.get(period, {})
        candidates = []
        explicit = payloads.get("peeumreusreprv") if isinstance(payloads, Mapping) else None
        if isinstance(explicit, Mapping):
            candidates.append(explicit)
        if isinstance(payloads, Mapping):
            candidates.extend(v for v in payloads.values() if isinstance(v, Mapping) and v is not explicit)
        for payload in candidates:
            payload = _unwrap_api_payload(payload)
            rows = payload.get("listPeeumreusreprvMoisProd", [])
            if not isinstance(rows, list):
                nested = _find_nested_by_key(payload, lambda x: isinstance(x.get("listPeeumreusreprvMoisProd"), list))
                rows = nested.get("listPeeumreusreprvMoisProd", []) if nested else []
            out = {}
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, Mapping):
                    continue
                product = PRODUCT_ID_TO_NAME.get(str(row.get("nomProd", "")))
                if product:
                    v = _num(row.get("mbase50"))
                    if v is not None:
                        out[product] = max(0.0, v)
            if out:
                return out
    # Fallback historique : structure explicitement présente dans le dump/erp_donnees.
    for period in reversed(ordered_periods(period_data)):
        rows = period_data[period].get("etudes_marche", {}).get("Etudes structurelles", {}).get("Prévision des ventes", {}).get("Tableau_1", [])
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, Mapping) and str(row.get("Colonne_0", "")).strip().lower() == "marché potentiel":
                return {p: max(0.0, _num(row.get(p)) or 0.0) for p in PRODUCTS}
    return {}

def _extract_api_monitoring(period: str, study_data: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any] | None:
    payload = study_data.get(period, {}).get("monitoring")
    if not isinstance(payload, Mapping):
        return None
    payload = _unwrap_api_payload(payload)
    market_share = _num(payload.get("marketShares"))
    turnover = payload.get("monitoringTurnoverData") or {}
    sales_value = None
    if isinstance(turnover, Mapping):
        arr = turnover.get("monthlySalesTurnover")
        if isinstance(arr, list) and arr:
            sales_value = _num(arr[-1])
    stocks = payload.get("remainingStocks")
    return {
        "market_share": (market_share / 100.0) if market_share is not None else None,
        "turnover": sales_value,
        "stocks": stocks if isinstance(stocks, Mapping) else {},
    }


def _latest_available_period(study_data: Mapping[str, Any], anchor_period: str | None = None) -> str | None:
    periods = ordered_periods(study_data.keys())
    if not periods:
        return None
    if anchor_period in PERIOD_INDEX:
        eligible = [p for p in periods if period_index(p) <= period_index(anchor_period)]
        if eligible:
            return eligible[-1]
    return periods[-1]

def extract_competitive_snapshot(
    period_data: Mapping[str, Mapping[str, Any]],
    own_company_number: int = 3,
    study_data: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    anchor_period: str | None = None,
) -> dict[str, Any]:
    study_data = study_data or {}
    periods = ordered_periods(set(period_data) | set(study_data))
    if not periods:
        return {"period": None, "rows": [], "own": None, "total_market": None, "share": None}
    period = anchor_period if anchor_period in periods else _latest_available_period({p: {} for p in periods}, anchor_period) or periods[-1]
    veille = period_data.get(period, {}).get("veille_concurrentielle", {})
    perf = veille.get("Performance commerciale", {}) if isinstance(veille, dict) else {}
    sales_rows = perf.get("Ventes", {}).get("Tableau_1", []) if isinstance(perf, dict) else []
    share_rows = perf.get("Parts de marché", {}).get("Tableau_1", []) if isinstance(perf, dict) else []
    shares = {int(r["Entreprise"]): r for r in share_rows if isinstance(r, dict) and str(r.get("Entreprise", "")).isdigit()}
    prices: dict[int, float] = {}
    quality: dict[int, float] = {}
    for section in (veille.get("Facteurs clés", {}) or {}).values() if isinstance(veille, dict) else []:
        rows = section.get("Tableau_1", []) if isinstance(section, dict) else []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not str(r.get("Entreprise", "")).isdigit():
                continue
            c = int(r["Entreprise"])
            for key in ("Prix (€)", "Prix"):
                v = _num(r.get(key))
                if v is not None:
                    prices[c] = v
            v = _num(r.get("Qualité"))
            if v is not None:
                quality[c] = v
    rows: list[dict[str, Any]] = []
    for r in sales_rows if isinstance(sales_rows, list) else []:
        if not isinstance(r, dict) or not str(r.get("Entreprise", "")).isdigit():
            continue
        c = int(r["Entreprise"])
        rows.append({
            "entreprise": c,
            "ventes": _num(r.get("Ventes")) or 0.0,
            "part_marche": _num(shares.get(c, {}).get("en quantité (%)")) or 0.0,
            "part_marche_valeur": _num(shares.get(c, {}).get("en valeur (%)")) or 0.0,
            "prix": prices.get(c),
            "qualite": quality.get(c),
            "is_own": c == own_company_number,
        })
    own = next((r for r in rows if r["is_own"]), None)
    monitoring = _extract_api_monitoring(period, study_data)
    if monitoring is None and anchor_period in PERIOD_INDEX:
        eligible_api = [p for p in ordered_periods(study_data) if period_index(p) <= period_index(anchor_period)]
        if eligible_api:
            for pp in reversed(eligible_api):
                monitoring = _extract_api_monitoring(pp, study_data)
                if monitoring is not None:
                    period = pp
                    break
    if own is None and monitoring is not None:
        own_sales_history = extract_own_sales_history(period_data, study_data=study_data)
        own_sales = None
        if not own_sales_history.empty and period in set(own_sales_history["periode"]):
            rr = own_sales_history.loc[own_sales_history["periode"] == period].iloc[-1]
            own_sales = float(sum(rr[p] for p in PRODUCTS))
        own = {"entreprise": own_company_number, "ventes": own_sales or 0.0, "part_marche": (monitoring.get("market_share") or 0.0) * 100, "part_marche_valeur": 0.0, "prix": None, "qualite": None, "is_own": True}
        rows.append(own)
    total_market = sum(r["ventes"] for r in rows) if rows else None
    share = None
    if own:
        share = ((own.get("part_marche") or 0) / 100.0) if own.get("part_marche") else ((own["ventes"] / total_market) if total_market and own["ventes"] else None)
    if monitoring and monitoring.get("market_share") is not None:
        share = monitoring["market_share"]
    if total_market is None and own and share and own.get("ventes", 0) > 0:
        total_market = own["ventes"] / share
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


def forecast_series(
    series: Sequence[float],
    future_month_indices: Sequence[int],
    structural_factors: Mapping[int, float] | None = None,
    seasonal_periods: int = 12,
) -> ForecastResult:
    values = np.asarray([max(0.0, float(v)) for v in series], dtype=float)
    n = len(values)
    horizon = len(future_month_indices)
    factors = dict(structural_factors or {i: 1.0 for i in range(12)})
    seasonal_known = len([v for v in factors.values() if abs(float(v) - 1.0) > 1e-9]) >= 2
    if horizon == 0:
        return ForecastResult("", n, "aucun", (), (), tuple(float(factors.get(i, 1.0)) for i in range(12)))
    future_factors = np.asarray([float(factors.get(i % 12, 1.0)) for i in future_month_indices], dtype=float)
    if ExponentialSmoothing is not None and n >= 24 and np.nanstd(values) > 0:
        try:
            fit = ExponentialSmoothing(values, trend="add", seasonal="add", seasonal_periods=seasonal_periods, damped_trend=True, initialization_method="estimated").fit(optimized=True)
            pred = np.maximum(0.0, np.asarray(fit.forecast(horizon), dtype=float))
            resid = values - np.asarray(fit.fittedvalues, dtype=float)
            sigma = float(np.nanstd(resid)) or 0.0
            return ForecastResult("", n, "Holt-Winters additif (ETS, 12 mois)", tuple(pred), tuple(np.maximum(0.0, pred - 1.96 * sigma)), tuple(np.maximum(0.0, pred + 1.96 * sigma)), tuple(float(factors.get(i, 1.0)) for i in range(12)))
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
            return ForecastResult("", n, "Holt amorti + saisonnalité structurelle", tuple(pred), tuple(np.maximum(0.0, (base - 1.65 * sigma) * future_factors)), tuple(np.maximum(0.0, (base + 1.65 * sigma) * future_factors)), tuple(float(factors.get(i, 1.0)) for i in range(12)))
        except Exception:
            pass
    if n == 1 and seasonal_known and horizon > 0:
        # Avec un seul mois réel, on ne peut pas estimer une tendance : on
        # conserve le niveau désaisonnalisé du mois d'ancrage et applique
        # uniquement la saisonnalité structurelle future.
        base = np.repeat(max(0.0, float(deseason[-1])), horizon)
        fallback_model = "Niveau ancré × saisonnalité structurelle (1 observation)"
    else:
        base = _damped_linear_forecast(deseason, horizon)
        fallback_model = "Tendance amortie + saisonnalité structurelle" if seasonal_known else "Tendance amortie sans saisonnalité (données structurelles absentes)"
    if n > 1:
        sigma = float(np.std(deseason - np.mean(deseason))) or 0.0
    else:
        sigma = max(1.0, float(values[-1]) * 0.25 if n else 1.0)
    pred = base * future_factors
    return ForecastResult("", n, fallback_model, tuple(pred), tuple(np.maximum(0.0, (base - 1.96 * sigma) * future_factors)), tuple(np.maximum(0.0, (base + 1.96 * sigma) * future_factors)), tuple(float(factors.get(i, 1.0)) for i in range(12)))


def build_12m_forecast(
    sales_history: pd.DataFrame,
    structural: Mapping[int, Mapping[int, float]],
    anchor_period: str,
    products: Sequence[str] = PRODUCTS,
) -> tuple[pd.DataFrame, dict[str, ForecastResult]]:
    """Retourne toujours l'année de l'ancrage, avec réel + prévisions."""
    if sales_history is None or anchor_period not in PERIOD_INDEX:
        return pd.DataFrame(), {}
    anchor_idx = period_index(anchor_period)
    year_start = (anchor_idx // 12) * 12
    year_end = year_start + 11
    hist = sales_history.loc[sales_history["index"] <= anchor_idx].copy().sort_values("index")
    if hist.empty:
        return pd.DataFrame(), {}
    last_hist_idx = int(hist["index"].max())
    future_indices = list(range(last_hist_idx + 1, year_end + 1))
    # Si on est déjà au dernier mois, il n'y a rien à prévoir ; on retourne quand même l'historique.
    out_results: dict[str, ForecastResult] = {}
    for product in products:
        ser = hist[product].astype(float).to_numpy() if product in hist else np.array([], dtype=float)
        factors = structural.get(PRODUCT_GROUP.get(product, 3), {i: 1.0 for i in range(12)})
        r = forecast_series(ser, future_indices, factors)
        out_results[product] = ForecastResult(product, len(ser), r.model, r.forecast, r.lower, r.upper, r.seasonal_factors)
    rows = []
    hist_map = {int(r["index"]): r for _, r in hist.iterrows()}
    future_lookup = {idx: j for j, idx in enumerate(future_indices)}
    for idx in range(year_start, year_end + 1):
        row = {"index": idx, "periode": period_label_from_index(idx), "statut": "Réel" if idx in hist_map else "Prévision"}
        for product in products:
            if idx in hist_map:
                row[product] = float(hist_map[idx][product])
            elif idx in future_lookup:
                j = future_lookup[idx]
                row[product] = float(out_results[product].forecast[j])
            else:
                row[product] = np.nan
        row["Total unités"] = float(sum(float(row[p]) for p in products if pd.notna(row[p])))
        rows.append(row)
    return pd.DataFrame(rows), out_results


def compute_market_forecast(
    forecast_df: pd.DataFrame,
    structural: Mapping[int, Mapping[int, float]],
    market_potential: Mapping[str, float],
    competitive: Mapping[str, Any],
    target_share: float,
    anchor_period: str,
) -> MarketForecastResult:
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
    proxies = []
    for period in future["periode"]:
        mi = month_index_from_label(period)
        mi = 0 if mi is None else mi
        proxy = 0.0
        for product, pot in market_potential.items():
            factor = structural.get(PRODUCT_GROUP.get(product, 3), {}).get(mi, 1.0)
            proxy += float(pot or 0) * float(factor)
        proxies.append(proxy)
    proxies = np.asarray(proxies, float)
    anchor_proxy = None
    if not actual.empty and market_potential:
        mi = month_index_from_label(str(actual["periode"].iloc[0]))
        mi = 0 if mi is None else mi
        anchor_proxy = sum(float(pot or 0) * float(structural.get(PRODUCT_GROUP.get(product, 3), {}).get(mi, 1.0)) for product, pot in market_potential.items())
    if anchor_proxy and anchor_proxy > 0:
        market = proxies * (current_market / anchor_proxy)
        method = "marché calibré sur volume observé + saisonnalité structurelle"
    else:
        market = np.maximum(1.0, proxies)
        method = "proxy marché structurel non calibré"
    baseline = np.divide(own, market, out=np.zeros_like(own), where=market > 0)
    required = market * target_share
    gap = np.maximum(0.0, required - own)
    return MarketForecastResult(anchor_period, tuple(future["periode"]), tuple(own), tuple(market), tuple(baseline), target_share, tuple(required), tuple(gap), float(current_share) if current_share is not None else None, float(current_market) if current_market is not None else None, method)
