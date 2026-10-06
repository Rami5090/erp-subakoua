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



def _unwrap_legacy_module(value: Any) -> dict[str, Any]:
    """Normalise un contenu legacy/API pouvant être enveloppé sous etat_actuel."""
    obj = _json_load(value)
    if isinstance(obj.get("etat_actuel"), Mapping):
        return dict(obj["etat_actuel"])
    return obj


def _norm_key(value: Any) -> str:
    import unicodedata
    s = str(value or "").strip().lower()
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return " ".join(s.split())


def _find_sections(obj: Any, names: Sequence[str]) -> list[Any]:
    """Retourne tous les objets dont la clé/nom correspond à l'un des labels."""
    targets = {_norm_key(n) for n in names}
    found: list[Any] = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if _norm_key(key) in targets:
                found.append(value)
            if isinstance(value, (Mapping, list)):
                found.extend(_find_sections(value, names))
    elif isinstance(obj, list):
        for value in obj:
            if isinstance(value, (Mapping, list)):
                found.extend(_find_sections(value, names))
    return found


def _find_rows_with_months(obj: Any) -> list[dict[str, Any]]:
    """Cherche récursivement des lignes de tableau contenant Produit + mois."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, Mapping):
        keys = {_norm_key(k) for k in obj.keys()}
        has_product = "produit" in keys or "nomprod" in keys or "productid" in keys
        month_hits = sum(1 for m in MONTH_NAMES if _norm_key(m) in keys)
        if has_product and month_hits >= 4:
            found.append(dict(obj))
        for v in obj.values():
            found.extend(_find_rows_with_months(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_find_rows_with_months(v))
    return found


def _extract_market_potential_from_any(obj: Any) -> dict[str, float]:
    """Extrait le marché potentiel depuis une structure legacy imbriquée."""
    for candidate in _find_sections(obj, ["Prévision des ventes"]):
        def scan(node: Any) -> dict[str, float]:
            if isinstance(node, list):
                for row in node:
                    if not isinstance(row, Mapping):
                        continue
                    labels = [_norm_key(row.get(k)) for k in ("Colonne_0", "Produit", "Libellé", "Indicateur", "Mois précédent") if row.get(k) is not None]
                    if "marche potentiel" in labels or any("marche potentiel" in x for x in labels):
                        out = {}
                        for product in PRODUCTS:
                            v = _num(row.get(product))
                            if v is not None:
                                out[product] = max(0.0, v)
                        if out:
                            return out
                    nested = scan(row)
                    if nested:
                        return nested
            elif isinstance(node, Mapping):
                for value in node.values():
                    nested = scan(value)
                    if nested:
                        return nested
            return {}
        out = scan(candidate)
        if out:
            return out
    return {}


def _extract_seasonality_from_any(obj: Any) -> dict[int, dict[int, float]]:
    """Extrait les coefficients mensuels depuis les différents formats observés."""
    # Format API-first.
    if isinstance(obj, Mapping):
        parsed = _structural_from_payload(obj)
        if parsed:
            return parsed

    rows = _find_rows_with_months(obj)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        prod_raw = row.get("Produit", row.get("nomProd", row.get("productId")))
        if isinstance(prod_raw, (int, float)) and float(prod_raw).is_integer():
            group = int(prod_raw)
            if group in (3, 5, 7):
                grouped.setdefault(group, []).append(row)
            continue
        product = PRODUCT_ID_TO_NAME.get(str(prod_raw), str(prod_raw))
        if product in PRODUCTS:
            grouped.setdefault(PRODUCT_GROUP[product], []).append(row)
    out: dict[int, dict[int, float]] = {}
    for group, group_rows in grouped.items():
        raw: list[float] = []
        for month in MONTH_NAMES:
            vals = [_num(r.get(month)) for r in group_rows]
            valid = [v for v in vals if v is not None and v >= 0]
            raw.append(float(np.mean(valid)) if valid else 0.0)
        if any(raw):
            mean = float(np.mean(raw)) or 1.0
            out[group] = {i: max(0.01, raw[i] / mean) for i in range(12)}
    return out

def build_period_data(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Construit le format historique ``periode -> module -> json``."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        period = str(row.get("periode", ""))
        module = str(row.get("module", ""))
        if period and module:
            out.setdefault(period, {})[module] = _unwrap_legacy_module(row.get("contenu"))
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
    """Fallback DOM : cherche des lignes produits, sans privilégier les stocks."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            found.extend(_recursive_find_product_rows(item))
    elif isinstance(obj, dict):
        keys = {_norm_key(k) for k in obj}
        score = sum(1 for p in PRODUCTS if p in obj)
        has_sales_context = any(k in keys for k in (
            "ventes", "ventes mensuelles", "sales", "monthly sales", "total ventes",
            "ventes du mois", "ventes produits"
        ))
        if score >= 2 and has_sales_context:
            found.append(obj)
        for key, value in obj.items():
            # Une table explicitement nommée "Ventes mensuelles" est une source prioritaire.
            if _norm_key(key) in {"ventes mensuelles", "monthly sales", "ventes du mois"}:
                found.extend(_recursive_find_product_rows(value))
            elif isinstance(value, (Mapping, list)):
                found.extend(_recursive_find_product_rows(value))
    return found


def _extract_sales_from_legacy_any(obj: Any, target_period: str) -> dict[str, float]:
    """Recherche explicite de la table Ventes mensuelles dans n'importe quel module legacy."""
    targets = {"ventes mensuelles", "monthly sales", "ventes du mois"}

    def walk(node: Any, in_sales_section: bool = False) -> dict[str, float]:
        if isinstance(node, Mapping):
            for key, value in node.items():
                nk = _norm_key(key)
                is_sales = in_sales_section or nk in targets
                if is_sales:
                    result = walk(value, True)
                    if result:
                        return result
                else:
                    result = walk(value, False)
                    if result:
                        return result

            # Une fois dans la bonne section, chercher une ligne portant la période.
            if in_sales_section:
                for key, value in node.items():
                    result = walk(value, True)
                    if result:
                        return result

        elif isinstance(node, list):
            for item in node:
                if not isinstance(item, Mapping):
                    continue
                month_value = item.get("Mois", item.get("Month", item.get("periode", item.get("Période"))))
                if month_value is not None and _norm_key(month_value) == _norm_key(target_period):
                    out = {}
                    for product in PRODUCTS:
                        v = _num(item.get(product))
                        if v is not None:
                            out[product] = max(0.0, v)
                    if len(out) >= 2:
                        return out
                result = walk(item, in_sales_section)
                if result:
                    return result
        return {}

    return walk(obj, False)


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
            # Legacy : recherche explicite de la section "Ventes mensuelles"
            # dans tout le contenu de la période. On évite ainsi de confondre
            # une table de stocks (qui contient aussi les noms des produits)
            # avec les ventes.
            for module in period_data.get(period, {}).values():
                values = _extract_sales_from_legacy_any(module, period)
                if values:
                    break

        if not values:
            # Dernier secours : uniquement des objets où le contexte indique réellement
            # des ventes. Aucune table générique de stocks ne doit être acceptée.
            for candidate in _recursive_find_product_rows(period_data.get(period, {})):
                mapped = {}
                for product in products:
                    v = _num(candidate.get(product))
                    if v is not None:
                        mapped[product] = max(0.0, v)
                if len(mapped) >= 2:
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
    """Extrait la saisonnalité avec une recherche tolérante dans tous les formats audités."""
    study_data = study_data or {}

    # 1) Payload API explicitement connu.
    for period in reversed(ordered_periods(study_data)):
        payloads = study_data.get(period, {})
        if not isinstance(payloads, Mapping):
            continue
        ordered = []
        if isinstance(payloads.get("peeumreusreprv"), Mapping):
            ordered.append(payloads["peeumreusreprv"])
        ordered.extend(v for v in payloads.values() if isinstance(v, Mapping) and v not in ordered)
        for payload in ordered:
            parsed = _extract_seasonality_from_any(payload)
            if parsed:
                return parsed

    # 2) Legacy/DOM : on cherche la rubrique sans supposer son emplacement exact.
    for period in reversed(ordered_periods(period_data)):
        for module in period_data.get(period, {}).values():
            parsed = _extract_seasonality_from_any(module)
            if parsed:
                return parsed
    return {}

def extract_market_potential(
    period_data: Mapping[str, Mapping[str, Any]],
    study_data: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[str, float]:
    """Extrait le marché potentiel depuis API ou legacy, avec recherche récursive."""
    study_data = study_data or {}
    for period in reversed(ordered_periods(study_data)):
        payloads = study_data.get(period, {})
        if not isinstance(payloads, Mapping):
            continue
        ordered = []
        if isinstance(payloads.get("peeumreusreprv"), Mapping):
            ordered.append(payloads["peeumreusreprv"])
        ordered.extend(v for v in payloads.values() if isinstance(v, Mapping) and v not in ordered)
        for payload in ordered:
            payload = _unwrap_api_payload(payload)
            rows = payload.get("listPeeumreusreprvMoisProd", [])
            if not isinstance(rows, list):
                nested = _find_nested_by_key(payload, lambda x: isinstance(x.get("listPeeumreusreprvMoisProd"), list))
                rows = nested.get("listPeeumreusreprvMoisProd", []) if nested else []
            out = {}
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, Mapping):
                    product = PRODUCT_ID_TO_NAME.get(str(row.get("nomProd", "")))
                    if product:
                        v = _num(row.get("mbase50"))
                        if v is not None:
                            out[product] = max(0.0, v)
            if out:
                return out

    for period in reversed(ordered_periods(period_data)):
        for module in period_data.get(period, {}).values():
            out = _extract_market_potential_from_any(module)
            if out:
                return out
    return {}

def _extract_api_monitoring(period: str, study_data: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any] | None:
    """Récupère le tableau de bord même si le payload est enveloppé."""
    payloads = study_data.get(period, {})
    candidates = []
    if isinstance(payloads, Mapping):
        if isinstance(payloads.get("monitoring"), Mapping):
            candidates.append(payloads["monitoring"])
        candidates.extend(v for v in payloads.values() if isinstance(v, Mapping) and v not in candidates)
    for candidate in candidates:
        found = _find_nested_by_key(candidate, lambda x: "marketShares" in x or "monitoringTurnoverData" in x)
        payload = found or candidate
        if not isinstance(payload, Mapping):
            continue
        market_share = _num(payload.get("marketShares"))
        turnover = payload.get("monitoringTurnoverData") or {}
        sales_value = None
        if isinstance(turnover, Mapping):
            arr = turnover.get("monthlySalesTurnover")
            if isinstance(arr, list) and arr:
                sales_value = _num(arr[-1])
        stocks = payload.get("remainingStocks")
        if market_share is not None or sales_value is not None:
            return {
                "market_share": (market_share / 100.0) if market_share is not None and market_share > 1 else market_share,
                "turnover": sales_value,
                "stocks": stocks if isinstance(stocks, Mapping) else {},
            }
    return None

def _latest_available_period(study_data: Mapping[str, Any], anchor_period: str | None = None) -> str | None:
    periods = ordered_periods(study_data.keys())
    if not periods:
        return None
    if anchor_period in PERIOD_INDEX:
        eligible = [p for p in periods if period_index(p) <= period_index(anchor_period)]
        if eligible:
            return eligible[-1]
    return periods[-1]

def _rows_from_named_sections(obj: Any, names: Sequence[str]) -> list[dict[str, Any]]:
    """Extrait récursivement les lignes de tableaux situées sous des sections nommées."""
    targets = {_norm_key(n) for n in names}
    found: list[dict[str, Any]] = []
    def walk(node: Any, active: bool = False) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                is_active = active or _norm_key(key) in targets
                if is_active:
                    found.extend(_table_rows(value))
                if isinstance(value, (Mapping, list)):
                    walk(value, is_active)
        elif isinstance(node, list):
            for value in node:
                walk(value, active)
    walk(obj)
    return found


def _competition_rows_from_any(obj: Any, own_company_number: int) -> list[dict[str, Any]]:
    """Construit une vue concurrentielle robuste à partir du JSON legacy/API.

    Le schéma Subakoua place les observations de concurrence dans différentes
    rubriques (Performance commerciale, Facteurs clés, etc.). Cette fonction
    fusionne les tables par numéro d'entreprise au lieu de dépendre d'un seul
    emplacement ou d'un seul nom de tableau.
    """
    by_company: dict[int, dict[str, Any]] = {}

    def ensure(c: int) -> dict[str, Any]:
        return by_company.setdefault(c, {"entreprise": c, "ventes": None, "part_marche": None, "part_marche_valeur": None,
                                         "prix": None, "qualite": None, "chiffre_affaires": None,
                                         "publicite": None, "axe_1": None, "axe_2": None,
                                         "is_own": c == own_company_number})

    def walk(node: Any, section: str = "") -> None:
        if isinstance(node, Mapping):
            # Une ligne de concurrence : l'entreprise est la clé de rattachement.
            if str(node.get("Entreprise", "")).isdigit() and 1 <= int(node["Entreprise"]) <= 9:
                c = int(node["Entreprise"])
                rec = ensure(c)
                if "Ventes" in node:
                    v = _num(node.get("Ventes")); rec["ventes"] = v if v is not None else rec["ventes"]
                for k in ("en quantité (%)", "part de marché", "Part de marché"):
                    if k in node:
                        v = _num(node.get(k));
                        if v is not None: rec["part_marche"] = v
                if "en valeur (%)" in node:
                    v = _num(node.get("en valeur (%)"));
                    if v is not None: rec["part_marche_valeur"] = v
                for k in ("Prix (€)", "Prix"):
                    if k in node:
                        v = _num(node.get(k));
                        if v is not None: rec["prix"] = v
                if "Qualité" in node:
                    v = _num(node.get("Qualité"));
                    if v is not None: rec["qualite"] = v
                for k in ("Chiffre d'affaires (€)", "CA (€)", "Chiffre d’affaires (€)"):
                    if k in node:
                        v = _num(node.get(k));
                        if v is not None: rec["chiffre_affaires"] = v
                for k in ("Publicité de marque", "Publicité produit (€)", "Publicité", "publicité"):
                    if k in node:
                        v = _num(node.get(k));
                        if v is not None: rec["publicite"] = v
                if "Axe 1" in node: rec["axe_1"] = node.get("Axe 1")
                if "Axe 2" in node: rec["axe_2"] = node.get("Axe 2")
            for key, value in node.items():
                walk(value, _norm_key(key) if isinstance(key, str) else section)
        elif isinstance(node, list):
            for value in node: walk(value, section)
    walk(obj)
    return list(by_company.values())


def _extract_legacy_competition(period_data: Mapping[str, Mapping[str, Any]], period: str, own_company_number: int) -> list[dict[str, Any]]:
    modules = period_data.get(period, {}) if isinstance(period_data, Mapping) else {}
    candidates: list[Any] = []
    veille = modules.get("veille_concurrentielle") if isinstance(modules, Mapping) else None
    if veille is not None:
        candidates.append(veille)
    for module in modules.values() if isinstance(modules, Mapping) else []:
        if isinstance(module, Mapping):
            if _find_sections(module, ["Performance commerciale", "Facteurs clés", "Décisions marketing", "Évolution marketing"]):
                candidates.append(module)
    merged: dict[int, dict[str, Any]] = {}
    for obj in candidates:
        for row in _competition_rows_from_any(obj, own_company_number):
            c = int(row["entreprise"])
            rec = merged.setdefault(c, row.copy())
            for key, value in row.items():
                if value is not None and (rec.get(key) is None or rec.get(key) == ""):
                    rec[key] = value
    return list(merged.values())


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
    period = anchor_period if anchor_period in periods else (_latest_available_period({p: {} for p in periods}, anchor_period) or periods[-1])

    rows = _extract_legacy_competition(period_data, period, own_company_number)
    # L'API peut aussi fournir la concurrence sous un payload d'étude. On cherche
    # toute structure contenant des lignes avec un numéro d'entreprise.
    if not rows:
        api_periods = [p for p in ordered_periods(study_data) if period_index(p) <= period_index(period)]
        for pp in reversed(api_periods):
            for payload in (study_data.get(pp, {}) or {}).values():
                if isinstance(payload, Mapping):
                    api_rows = _competition_rows_from_any(payload, own_company_number)
                    if api_rows:
                        rows = api_rows
                        period = pp
                        break
            if rows:
                break

    own = next((r for r in rows if r.get("is_own")), None)
    total_market = None
    share = None
    if rows:
        sales_vals = [float(r["ventes"]) for r in rows if r.get("ventes") is not None]
        if sales_vals:
            total_market = float(sum(sales_vals))
        if own:
            pct = _num(own.get("part_marche"))
            if pct is not None:
                share = pct / 100.0 if pct > 1 else pct
        if share is None and own and total_market and (own.get("ventes") or 0) > 0:
            share = float(own["ventes"]) / total_market

    # Tableau de bord API : source privilégiée pour la PDM de l'entreprise.
    monitoring = _extract_api_monitoring(period, study_data)
    if monitoring is None and anchor_period in PERIOD_INDEX:
        eligible_api = [p for p in ordered_periods(study_data) if period_index(p) <= period_index(anchor_period)]
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
            own_sales = float(sum(float(rr[p]) for p in PRODUCTS))
        own = {"entreprise": own_company_number, "ventes": own_sales or 0.0,
               "part_marche": (monitoring.get("market_share") or 0.0) * 100,
               "part_marche_valeur": None, "prix": None, "qualite": None,
               "chiffre_affaires": None, "publicite": None, "axe_1": None, "axe_2": None,
               "is_own": True}
        rows.append(own)
    if monitoring and monitoring.get("market_share") is not None:
        share = float(monitoring["market_share"])
    if total_market is None and own and share and own.get("ventes", 0) > 0:
        total_market = float(own["ventes"]) / float(share)
    share_scope = "overall_monitoring" if monitoring is not None and share is not None else ("competitive_segment" if share is not None else None)
    return {"period": period, "rows": rows, "own": own, "total_market": total_market, "share": share, "share_scope": share_scope}

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
    share_scope = competitive.get("share_scope")
    current_market = competitive.get("total_market") if share_scope == "overall_monitoring" else None
    current_share = competitive.get("share") if share_scope == "overall_monitoring" else None
    actual = forecast_df.loc[forecast_df["statut"] == "Réel"].tail(1)
    own_current = float(actual["Total unités"].iloc[0]) if not actual.empty else 0.0
    if (current_market is None or current_market <= 0) and current_share and own_current > 0:
        current_market = own_current / current_share
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
    if anchor_proxy and anchor_proxy > 0 and current_market and current_market > 0:
        market = proxies * (current_market / anchor_proxy)
        method = "marché global calibré sur PDM globale + saisonnalité structurelle"
    elif proxies.size and np.any(proxies > 0):
        market = proxies.copy()
        method = "proxy marché structurel non calibré"
    else:
        market = np.full_like(own, np.nan, dtype=float)
        method = "marché indisponible : aucune série concurrentielle/potentielle"
    baseline = np.divide(own, market, out=np.full_like(own, np.nan), where=np.isfinite(market) & (market > 0))
    required = market * target_share
    gap = np.where(np.isfinite(required), np.maximum(0.0, required - own), np.nan)
    return MarketForecastResult(anchor_period, tuple(future["periode"]), tuple(own), tuple(market), tuple(baseline), target_share, tuple(required), tuple(gap), float(current_share) if current_share is not None else None, float(current_market) if current_market is not None else None, method)
