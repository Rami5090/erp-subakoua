"""Routes UI des études Subakoua dérivées de l'audit.

Les templates contenant ``{month}`` utilisent l'identifiant de mois UI Subakoua
(13 = A1-Janvier, ..., 24 = A1-Décembre, 25 = A2-Janvier, ...).

Les routes marquées ``inferred`` sont des routes de famille déduites de l'audit
(UI stable) et servent de fallback; elles sont volontairement distinctes des
routes explicitement observées.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROUTES_FILE = Path(__file__).resolve().parent / "study_ui_routes.json"
try:
    _DATA = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
except Exception:
    _DATA = {"routes": {}}

STUDY_UI_ROUTES: dict[str, dict[str, Any]] = _DATA.get("routes", {})


def routes_for(study_id: str, month_id: int, base_url: str = "https://subakoua.arkhe.com") -> list[dict[str, Any]]:
    """Retourne les routes candidates d'une étude, déjà matérialisées."""
    row = STUDY_UI_ROUTES.get(str(study_id))
    if not row:
        return []
    template = str(row.get("url_template") or "").strip()
    if not template:
        return []
    url = template.replace("{month}", str(int(month_id)))
    if url.startswith("/"):
        url = base_url.rstrip("/") + url
    return [{**row, "url": url}]
