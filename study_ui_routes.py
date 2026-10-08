"""Routes UI des études Subakoua issues de l'audit du portail.

Les routes mensuelles utilisent l'identifiant UI ``/months/{month}`` du portail :
13 = Année 1 - Janvier, ..., 18 = Année 1 - Juin, 19 = Année 1 - Juillet.

La table est volontairement séparée du catalogue API : un identifiant d'étude
peut avoir un nom d'API différent du nom de route UI. Aucune URL n'est inventée
pour une étude dont l'audit ne fournit pas de cible UI exploitable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

ROUTES_FILE = Path(__file__).resolve().parent / "study_ui_routes.json"
try:
    _DATA = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
except Exception:
    _DATA = {"routes": {}}

STUDY_UI_ROUTES: dict[str, dict[str, Any]] = _DATA.get("routes", {})
BASE_URL = str(_DATA.get("base_url") or "https://subakoua.arkhe.com").rstrip("/")


def routes_for(study_id: str, month_id: int, base_url: str | None = None) -> list[dict[str, Any]]:
    """Retourne les routes UI auditée pour une étude et un mois.

    Une étude sans route UI observée retourne une liste vide plutôt qu'une URL
    fabriquée. Cela force le moteur à utiliser son fallback DOM/API de manière
    explicite.
    """
    row = STUDY_UI_ROUTES.get(str(study_id))
    if not row or not row.get("ui_supported", True):
        return []
    template = str(row.get("url_template") or "").strip()
    if not template:
        return []
    month = int(month_id)
    if month < 1:
        return []
    if "{month}" in template:
        url = template.replace("{month}", str(month))
    else:
        url = template
    if url.startswith("/"):
        url = urljoin((base_url or BASE_URL) + "/", url.lstrip("/"))
    if not re.match(r"^https://subakoua\.arkhe\.com/companies/partners/", url):
        return []
    return [{**row, "study_id": str(study_id), "month_id": month, "url": url}]
