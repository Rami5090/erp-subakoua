"""Catalogue unifié des études Subakoua.

Source unique de vérité fusionnant :
- study_api_catalog.json (endpoints API + clés de réponse)
- study_ui_routes.json (routes UI directes)
- study_information_matrix.json (classification, importance, tags, leviers)

Généré par merge_catalogs.py à partir des 3 fichiers sources.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

CATALOG_PATH = Path(__file__).resolve().parent / "study_catalog.json"


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    with CATALOG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def get_all_studies() -> list[dict[str, Any]]:
    """Liste complète des études (91 entrées)."""
    return _load_raw().get("studies", [])


@lru_cache(maxsize=1)
def get_by_id(study_id: str) -> dict[str, Any] | None:
    """Récupère une étude par son ID."""
    for s in get_all_studies():
        if s["study_id"] == study_id:
            return s
    return None


def get_endpoint_path(study_id: str) -> str:
    """Chemin d'endpoint API (avec placeholders {session}, {month}, {team})."""
    s = get_by_id(study_id)
    return s.get("endpoint_path", "") if s else ""


def get_response_keys(study_id: str) -> list[str]:
    """Clés de réponse attendues pour l'endpoint API."""
    s = get_by_id(study_id)
    keys = s.get("response_keys", [])
    if isinstance(keys, str):
        return [k.strip() for k in keys.split(",") if k.strip()]
    return list(keys) if keys else []


def get_url_template(study_id: str) -> str:
    """Template d'URL UI directe (avec placeholder {month})."""
    s = get_by_id(study_id)
    return s.get("url_template", "") if s else ""


def get_ui_routes_for(study_id: str, month_id: int, base_url: str = "https://subakoua.arkhe.com") -> list[dict[str, Any]]:
    """Retourne les routes UI matérialisées pour un mois donné (compat study_ui_routes.routes_for)."""
    template = get_url_template(study_id)
    if not template:
        return []
    url = template.replace("{month}", str(int(month_id)))
    if url.startswith("/"):
        url = base_url.rstrip("/") + url
    s = get_by_id(study_id) or {}
    # Include 'source' and 'confidence' for backwards compatibility with tests
    result = {**s, "url": url}
    if "ui_source" in s:
        result["source"] = s["ui_source"]
    if "ui_confidence" in s:
        result["confidence"] = s["ui_confidence"]
    return [result]


def get_studies_by_domain(domain: str) -> list[dict[str, Any]]:
    """Filtre les études par domaine."""
    return [s for s in get_all_studies() if s.get("domain") == domain]


def get_studies_by_tag(tag: str) -> list[dict[str, Any]]:
    """Filtre les études par tag."""
    return [s for s in get_all_studies() if tag in s.get("tags", [])]


def get_mandatory_studies() -> list[dict[str, Any]]:
    """Études marquées strategic_only ou importance >= 80."""
    return [s for s in get_all_studies() if s.get("strategic_only") or s.get("importance", 0) >= 80]


def get_free_studies() -> list[dict[str, Any]]:
    """Études gratuites (prix = 0)."""
    return [s for s in get_all_studies() if s.get("price", 0) == 0]


# ---------------------------------------------------------------------------
# Compatibilité document_strategy / document_optimizer
# ---------------------------------------------------------------------------

# Pour document_strategy : MATRIX_CSV_PATH / MATRIX_JSON_PATH / ENDPOINT_CATALOG_PATH
# Ces modules lisent directement les fichiers CSV/JSON. On fournit des propriétés
# compatibles qui pointent vers le catalogue unifié.

CATALOG_DIR = CATALOG_PATH.parent

# Alias pour compatibilité (document_strategy les utilise comme Path)
MATRIX_JSON_PATH = CATALOG_PATH
ENDPOINT_CATALOG_PATH = CATALOG_PATH
CATALOG_PATH_COMPAT = CATALOG_PATH

# Pour document_optimizer : CATALOG_PATH, ENDPOINT_CATALOG_PATH
# Il charge via csv.DictReader sur CATALOG_PATH (attend CSV).
# On fournit un helper pour obtenir la liste sous forme de dicts "plat" compatibles CSV.
def load_catalog_flat() -> list[dict[str, str]]:
    """Retourne le catalogue sous forme aplatie (str -> str) compatible CSV reader."""
    out = []
    for s in get_all_studies():
        row = {
            "study_id": s["study_id"],
            "label": s.get("label", ""),
            "price": str(s.get("price", 0)),
            "bought_any_period": "True",  # legacy
            "never_bought": "False",
            "periods_seen": "",  # legacy
        }
        out.append(row)
    return out


def load_profiles() -> dict[str, Any]:
    """Charge les profils d'études pour document_optimizer (StudyProfile)."""
    from dataclasses import dataclass
    from typing import Tuple

    @dataclass(frozen=True)
    class StudyProfile:
        study_id: str
        label: str
        price: float
        domain: str
        tags: Tuple[str, ...]
        decision_levers: Tuple[str, ...]
        horizons: Tuple[str, ...]
        importance: float
        recurring: str = "monthly"
        strategic_only: bool = False
        endpoint_known: bool = False
        response_keys: Tuple[str, ...] = ()

    profiles = {}
    for s in get_all_studies():
        tags = s.get("tags", [])
        levers = s.get("decision_levers", [])
        horizons = s.get("horizons", [])
        profiles[s["study_id"]] = StudyProfile(
            study_id=s["study_id"],
            label=s.get("label", ""),
            price=float(s.get("price", 0)),
            domain=s.get("domain", "unknown"),
            tags=tuple(tags) if isinstance(tags, list) else tuple(str(tags).split(",")),
            decision_levers=tuple(levers) if isinstance(levers, list) else tuple(str(levers).split(",")),
            horizons=tuple(horizons) if isinstance(horizons, list) else tuple(str(horizons).split(",")),
            importance=float(s.get("importance", 0)),
            recurring=s.get("recurring", "monthly"),
            strategic_only=bool(s.get("strategic_only", False)),
            endpoint_known=bool(s.get("endpoint_known", False)),
            response_keys=tuple(get_response_keys(s["study_id"])),
        )
    return profiles