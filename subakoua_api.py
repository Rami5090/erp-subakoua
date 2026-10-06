"""Client API-first pour Subakoua / Arkhe.

Le client réutilise une session Playwright authentifiée et appelle directement
les endpoints JSON observés lors de l'audit. Aucun jeton/cookie n'est stocké
par ce module.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode, urljoin

from playwright.sync_api import BrowserContext, APIResponse, TimeoutError as PlaywrightTimeoutError

import document_optimizer as optimizer

BASE_URL = "https://subakoua.arkhe.com"
ENDPOINT_CATALOG_PATH = Path(__file__).resolve().parent / "study_api_catalog.json"
DEFAULT_ENDPOINTS = json.loads(ENDPOINT_CATALOG_PATH.read_text(encoding="utf-8")).get("known_endpoints", {})


@dataclass(frozen=True)
class PlayerContext:
    session: str
    user_id: str
    team_id: str
    team_number: int | None
    team_name: str
    ranking_available: bool


@dataclass(frozen=True)
class StudyAvailability:
    study_id: str
    price: float
    bought_by_team: bool
    period_code: str


@dataclass(frozen=True)
class StudyFetchResult:
    study_id: str
    period: str
    period_code: str
    endpoint: str
    read_allowed: bool
    payload: dict[str, Any]
    valid: bool
    reason: str = ""


class SubakouaAPIError(RuntimeError):
    pass


class SubakouaAPIClient:
    def __init__(
        self,
        context: BrowserContext,
        *,
        logger: logging.Logger | None = None,
        base_url: str = BASE_URL,
        timeout_ms: int = 30_000,
    ) -> None:
        self.context = context
        self.request = context.request
        self.logger = logger or logging.getLogger("subakoua_api")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_ms
        self.player: PlayerContext | None = None

    @staticmethod
    def period_code(period: str) -> str:
        if re.fullmatch(r"\d{4}", str(period).strip()):
            return str(period).strip()
        return optimizer.period_label_to_code(period)

    @staticmethod
    def period_label(period: str) -> str:
        if re.fullmatch(r"\d{4}", str(period).strip()):
            return optimizer.period_code_to_label(period)
        return str(period).strip()

    @staticmethod
    def month_id(period: str) -> int:
        return optimizer.period_month_id(SubakouaAPIClient.period_label(period))

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    @staticmethod
    def _json_response(response: APIResponse, url: str) -> Any:
        if not response.ok:
            text = ""
            try:
                text = response.text()[:500]
            except Exception:
                pass
            raise SubakouaAPIError(f"HTTP {response.status} sur {url} | {text}")
        try:
            return response.json()
        except Exception as exc:
            raise SubakouaAPIError(f"Réponse non JSON sur {url}: {exc}") from exc

    def get_player_context(self, *, refresh: bool = False) -> PlayerContext:
        if self.player is not None and not refresh:
            return self.player
        url = self._url("/api/users/player/context")
        response = self.request.get(url, timeout=self.timeout)
        payload = self._json_response(response, url)
        user = payload.get("user") or {}
        session = str(payload.get("session") or "").strip()
        team_id = str(user.get("teamId") or "").strip()
        if not session or not team_id:
            raise SubakouaAPIError("Contexte Subakoua incomplet : session ou teamId absent.")
        self.player = PlayerContext(
            session=session,
            user_id=str(user.get("id") or ""),
            team_id=team_id,
            team_number=int(user["teamNumber"]) if str(user.get("teamNumber", "")).isdigit() else None,
            team_name=str(user.get("teamName") or ""),
            ranking_available=bool(payload.get("rankingAvailable", False)),
        )
        return self.player

    def catalog(self, period: str, study_ids: Iterable[str] | None = None, *, chunk_size: int = 25) -> list[StudyAvailability]:
        player = self.get_player_context()
        period_code = self.period_code(period)
        ids = list(dict.fromkeys(study_ids or optimizer.load_profiles().keys()))
        # Le catalogue local d'audit peut contenir 91 études. On fragmente pour
        # éviter de produire une URL trop longue.
        out: dict[str, StudyAvailability] = {}
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start:start + chunk_size]
            query = urlencode([("studyIds", sid) for sid in chunk])
            path = f"/api/sessions/{player.session}/periods/{period_code}/teams/{player.team_id}/links?{query}"
            url = self._url(path)
            response = self.request.get(url, timeout=self.timeout)
            payload = self._json_response(response, url)
            if not isinstance(payload, list):
                raise SubakouaAPIError(f"Catalogue inattendu pour {period_code}: {type(payload).__name__}")
            for row in payload:
                sid = str(row.get("studyId") or "").strip()
                if not sid:
                    continue
                out[sid] = StudyAvailability(
                    study_id=sid,
                    price=float(row.get("price") or 0),
                    bought_by_team=bool(row.get("boughtByTeam", False)),
                    period_code=period_code,
                )
        return list(out.values())

    def endpoint_for(self, study_id: str) -> str | None:
        info = DEFAULT_ENDPOINTS.get(study_id)
        return str(info.get("path")) if info else None

    def build_study_endpoint(self, study_id: str, period: str) -> str:
        template = self.endpoint_for(study_id)
        if not template:
            raise SubakouaAPIError(f"Endpoint API inconnu pour l'étude {study_id}.")
        player = self.get_player_context()
        period_label = self.period_label(period)
        replacements = {
            "session": player.session,
            "team": player.team_id,
            "month": str(self.month_id(period_label)),
        }
        path = template
        for key, value in replacements.items():
            path = path.replace("{" + key + "}", value)
        if "{" in path:
            raise SubakouaAPIError(f"Placeholder API non résolu pour {study_id}: {path}")
        return self._url(path)

    def fetch_study(self, study_id: str, period: str, *, require_read_allowed: bool = True) -> StudyFetchResult:
        period_code = self.period_code(period)
        period_label = self.period_label(period)
        url = self.build_study_endpoint(study_id, period_label)
        response = self.request.get(url, timeout=self.timeout)
        payload = self._json_response(response, url)
        if not isinstance(payload, dict):
            return StudyFetchResult(study_id, period_label, period_code, url, False, {}, False, "Réponse non objet")
        read_allowed = bool(payload.get("readAllowed", False))
        returned_id = str(payload.get("studyId") or study_id)
        valid = returned_id == study_id and (read_allowed or not require_read_allowed)
        reason = ""
        if returned_id != study_id:
            reason = f"studyId retourné={returned_id!r} différent de {study_id!r}"
        elif require_read_allowed and not read_allowed:
            reason = "Document non lisible : readAllowed=false. Aucun payload ne doit être considéré comme acquis."
        return StudyFetchResult(study_id, period_label, period_code, url, read_allowed, payload if isinstance(payload, dict) else {}, valid, reason)

    def purchase_study(self, purchase_period: str, study_id: str) -> dict[str, Any]:
        """Achète explicitement une étude pour la période d'achat indiquée.

        Cette méthode est volontairement non appelée automatiquement par le planificateur.
        """
        player = self.get_player_context()
        period_code = self.period_code(purchase_period)
        path = f"/api/sessions/{player.session}/periods/{period_code}/teams/{player.team_id}/studies/{study_id}/studyPurchases"
        url = self._url(path)
        response = self.request.put(url, timeout=self.timeout)
        payload = self._json_response(response, url)
        self.logger.info("Achat étude=%s période=%s prix=%s", study_id, period_code, payload.get("price"))
        return payload if isinstance(payload, dict) else {"response": payload}


def start_authenticated_client(config: Any, logger: logging.Logger | None = None):
    """Ouvre Chromium, se connecte via le login robuste existant et renvoie
    (playwright, browser, context, page, api_client)."""
    # Import tardif : les tests de planification/API n'ont pas besoin de PyMySQL.
    import scraper_subakoua as legacy

    log = logger or logging.getLogger("subakoua_api")
    pw = legacy.sync_playwright().start()
    browser = legacy.launch_browser(pw, config, log)
    context = legacy.create_context(browser, config, log)
    page = context.new_page()
    page.set_default_timeout(config.timeout_ms)
    page.set_default_navigation_timeout(config.timeout_ms)
    legacy.login(page, context, config, log)
    api = SubakouaAPIClient(context, logger=log, base_url=legacy.DOMAINE_BASE, timeout_ms=config.timeout_ms)
    api.get_player_context(refresh=True)
    return pw, browser, context, page, api
