"""Client API-first pour Subakoua / Arkhe.

Le client réutilise une session Playwright authentifiée et appelle directement
les endpoints JSON observés lors de l'audit. Aucun jeton/cookie n'est stocké
par ce module.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from playwright.sync_api import Page
from urllib.parse import urlencode, urljoin

from playwright.sync_api import BrowserContext, APIResponse, TimeoutError as PlaywrightTimeoutError

import study_catalog
import document_optimizer as optimizer
from study_catalog import (
    get_endpoint_path,
    get_response_keys,
    get_url_template,
    get_ui_routes_for,
)
from constants import (
    period_label_to_code,
    period_code_to_label,
    period_month_id,
)

BASE_URL = "https://subakoua.arkhe.com"

#: Sélecteur des nœuds cliquables candidats (cartes d'études).
_CANDIDATE_SELECTOR = (
    'a, button, [role="link"], [role="button"], li, [tabindex="0"], '
    '[routerlink], [ng-reflect-router-link]'
)

#: Scoring des cartes en UN seul aller-retour navigateur (même règle que Python).
_CANDIDATE_SCORING_JS = """({selector, label, tokens, studyId}) => {
  const norm = (v) => (v || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const visible = (el) => {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const labelNorm = norm(label), idNorm = norm(studyId);
  const els = Array.from(document.querySelectorAll(selector));
  const out = [];
  const cap = Math.min(els.length, 500);
  for (let i = 0; i < cap; i++) {
    const el = els[i];
    if (!visible(el)) continue;
    const txt = norm(el.innerText || el.textContent || '');
    const attrs = norm([el.getAttribute('href'), el.getAttribute('routerlink'),
      el.getAttribute('ng-reflect-router-link'), el.getAttribute('data-study-id'),
      el.getAttribute('data-studyid'), el.getAttribute('data-id'),
      el.getAttribute('value')].filter(Boolean).join(' '));
    if (idNorm && attrs.includes(idNorm)) { out.push({i, score: 100}); continue; }
    if (!txt) continue;
    if (labelNorm && txt.includes(labelNorm)) { out.push({i, score: 95}); continue; }
    if (tokens.length) {
      const hits = tokens.filter(t => txt.includes(t)).length;
      const coverage = hits / tokens.length;
      if (coverage >= 0.60) {
        const words = txt.split(' ').length;
        const specificity = Math.max(0, 1 - Math.max(0, words - 20) / 80);
        out.push({i, score: coverage * 80 + specificity * 10});
      }
    }
  }
  out.sort((a, b) => b.score - a.score || a.i - b.i);
  return {total: els.length, matches: out.slice(0, 20)};
}"""

#: Détecte un contrôle Acheter par texte exact + ancêtre cliquable (sans les rôles ARIA).
_BUY_TEXT_DETECT_JS = """() => {
  const norm = (v) => (v || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const visible = (el) => {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const els = Array.from(document.querySelectorAll('button, a'));
  for (const el of els) {
    if (!visible(el)) continue;
    if (norm(el.innerText || el.textContent || '') !== 'acheter') continue;
    const target = el.closest('button, a, [role="button"]') || el;
    if (target && visible(target)) return true;
  }
  return false;
}"""

#: Clique le contrôle Acheter trouvé par texte exact (dernier recours d'achat).
_BUY_TEXT_CLICK_JS = """() => {
  const norm = (v) => (v || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const visible = (el) => {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const els = Array.from(document.querySelectorAll('button, a'));
  for (const el of els) {
    if (!visible(el)) continue;
    if (norm(el.innerText || el.textContent || '') !== 'acheter') continue;
    const target = el.closest('button, a, [role="button"]') || el;
    if (target && visible(target)) { target.click(); return true; }
  }
  return false;
}"""


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
    raw: dict[str, Any] = field(default_factory=dict)


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
        page: Page | None = None,
        dashboard_url: str | None = None,
    ) -> None:
        self.context = context
        self.request = context.request
        self.page = page
        self.logger = logger or logging.getLogger("subakoua_api")
        self.base_url = base_url.rstrip("/")
        self.dashboard_url = (dashboard_url or f"{self.base_url}/companies").rstrip("/")
        self.timeout = timeout_ms
        self.player: PlayerContext | None = None
        self._catalog_rows: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def period_code(period: str) -> str:
        if re.fullmatch(r"\d{4}", str(period).strip()):
            return str(period).strip()
        return period_label_to_code(period)

    @staticmethod
    def period_label(period: str) -> str:
        if re.fullmatch(r"\d{4}", str(period).strip()):
            return period_code_to_label(period)
        return str(period).strip()

    @staticmethod
    def month_id(period: str) -> int:
        return period_month_id(SubakouaAPIClient.period_label(period))

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
        raw_session = payload.get("session")
        # Les réponses Subakoua rencontrées varient selon le déploiement :
        # session peut être une chaîne ou un objet {id, genericSessionId, ...}.
        # Les routes /api/sessions/{id}/... exigent l'ID de session technique,
        # jamais la représentation Python complète du dictionnaire.
        if isinstance(raw_session, Mapping):
            session = str(raw_session.get("id") or raw_session.get("sessionId") or "").strip()
            if not session:
                session = str(raw_session.get("genericSessionId") or "").strip()
        else:
            session = str(raw_session or "").strip()
        team_id = str(user.get("teamId") or "").strip()
        if not session or not team_id:
            raise SubakouaAPIError("Contexte Subakoua incomplet : session.id ou teamId absent.")
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
                raw_row = dict(row)
                self._catalog_rows[(period_code, sid)] = raw_row
                out[sid] = StudyAvailability(
                    study_id=sid,
                    price=float(row.get("price") or 0),
                    bought_by_team=bool(row.get("boughtByTeam", False)),
                    period_code=period_code,
                    raw=raw_row,
                )
        return list(out.values())

    def endpoint_for(self, study_id: str) -> str | None:
        path = get_endpoint_path(study_id)
        return path if path else None

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

    def _select_purchase_period(self, page: Page, period: str) -> None:
        selector = "okw-select-period span[role='combobox']"
        loc = page.locator(selector).first
        # Sélecteur best-effort : 5 s max, sinon on continue sans (le contexte
        # de période est généralement conservé entre sections du portail).
        loc.wait_for(state="visible", timeout=5000)
        current = " ".join(loc.inner_text().split())
        wanted = self.period_label(period)
        if wanted in current:
            return
        loc.click()
        page.wait_for_timeout(250)
        option = page.locator("div.p-select-option-label").filter(has_text=wanted).last
        option.wait_for(state="visible", timeout=5000)
        option.click()
        page.wait_for_timeout(1200)

    @staticmethod
    def _click_first_visible(locator, timeout: int = 5000) -> bool:
        try:
            count = min(locator.count(), 30)
            for i in range(count):
                item = locator.nth(i)
                if item.is_visible():
                    item.click(timeout=timeout)
                    return True
        except Exception:
            pass
        return False

    def _open_study_area(self, page: Page) -> None:
        """Ouvre la zone documentaire du portail avec plusieurs sélecteurs robustes.

        Le menu Angular peut mettre un moment à se monter après navigation :
        on réessaie plusieurs fois avant d'abandonner.
        """
        labels = ["Études", "Etudes", "Documents", "Études disponibles", "Etudes disponibles"]
        for _ in range(4):
            for label in labels:
                if self._click_first_visible(page.get_by_role("link", name=re.compile(re.escape(label), re.I))):
                    page.wait_for_timeout(900)
                    return
                if self._click_first_visible(page.get_by_role("button", name=re.compile(re.escape(label), re.I))):
                    page.wait_for_timeout(900)
                    return
                if self._click_first_visible(page.get_by_text(label, exact=True)):
                    page.wait_for_timeout(900)
                    return
            page.wait_for_timeout(900)
        raise SubakouaAPIError("Zone Études/Documents introuvable dans le portail Subakoua.")

    def _buy_locators(self, page: Page, scope=None) -> list:
        """Localisateurs des contrôles d'achat (boutons ET liens)."""
        root = scope or page
        locs = []
        for pattern in (
            r"acheter",
            r"acheter l[’\']étude",
        ):
            rx = re.compile(pattern, re.I)
            for role in ("button", "link"):
                try:
                    locs.append(root.get_by_role(role, name=rx))
                except Exception:
                    pass
        return locs

    def _click_buy_visible(self, page: Page, scope=None) -> bool:
        """Clique sur le premier bouton/lien Acheter visible dans le scope donné."""
        for loc in self._buy_locators(page, scope):
            if self._click_first_visible(loc, timeout=5000):
                page.wait_for_timeout(600)
                return True
        return self._click_buy_text_fallback(page, scope)

    def _buy_text_target_exists(self, page: Page, scope=None) -> bool:
        """Détecte un contrôle Acheter par son texte exact, sans tenir compte des rôles.

        PrimeNG/Angular expose parfois le bouton sans rôle accessible : on cherche
        un libellé « acheter » exact avec un ancêtre cliquable (button/a).
        Ne clique jamais (dry-run compatible).
        """
        try:
            if scope is not None:
                loc = scope.locator("button, a").filter(
                    has_text=re.compile(r"^\s*acheter\s*$", re.I))
                return bool(loc.count() > 0)
            return bool(page.evaluate(_BUY_TEXT_DETECT_JS))
        except Exception:
            return False

    def _click_buy_text_fallback(self, page: Page, scope=None) -> bool:
        """Clique le contrôle Acheter trouvé par son texte (dernier recours)."""
        try:
            if scope is not None:
                loc = scope.locator("button, a").filter(
                    has_text=re.compile(r"^\s*acheter\s*$", re.I))
                for i in range(min(loc.count(), 5)):
                    try:
                        btn = loc.nth(i)
                        if btn.is_visible():
                            btn.click(timeout=5000)
                            page.wait_for_timeout(600)
                            return True
                    except Exception:
                        continue
                return False
            return bool(page.evaluate(_BUY_TEXT_CLICK_JS))
        except Exception:
            return False

    @staticmethod
    def _visible_text_locator(page: Page, label: str):
        """Retrouve le libellé même si Angular ajoute du contenu autour du titre."""
        if not label:
            return None
        try:
            loc = page.get_by_text(label, exact=False)
            count = min(loc.count(), 50)
            for i in range(count):
                item = loc.nth(i)
                if item.is_visible():
                    return item
        except Exception:
            pass
        return None

    @staticmethod
    def _norm_ui_text(value: str) -> str:
        """Normalise un texte UI pour des comparaisons tolérantes."""
        value = " ".join(str(value or "").split()).casefold()
        return value

    @classmethod
    def _label_tokens(cls, label: str) -> list[str]:
        """Retourne les mots significatifs d'un libellé d'étude."""
        norm = cls._norm_ui_text(label)
        # On enlève la ponctuation simple tout en conservant les accents.
        norm = re.sub(r"[^\wÀ-ÿ]+", " ", norm, flags=re.UNICODE)
        stop = {
            "de", "du", "des", "d", "et", "la", "le", "les", "sur",
            "pour", "en", "au", "aux", "a", "un", "une", "l"
        }
        return [tok for tok in norm.split() if len(tok) >= 3 and tok not in stop]

    def _wait_for_study_content(self, page: Page, label: str, study_id: str,
                                timeout_ms: int = 8000) -> bool:
        """Attend que les cartes soient montées avant de scorer (Angular tardif)."""
        deadline = time.time() + timeout_ms / 1000.0
        while True:
            if self._find_study_candidates(page, label, study_id):
                return True
            if time.time() >= deadline:
                return False
            try:
                page.wait_for_timeout(800)
            except Exception:
                return False

    def _find_study_candidates(self, page: Page, label: str, study_id: str) -> list:
        """Scoring des cartes d'étude dans le DOM visible. Ne clique jamais.

        Un seul aller-retour navigateur via evaluate ; repli Python si indisponible.
        Règle de score identique des deux côtés : ID exact dans les attributs
        (100), libellé exact (95), couverture des mots du titre (≥60%).
        """
        try:
            found = page.evaluate(_CANDIDATE_SCORING_JS, {
                "selector": _CANDIDATE_SELECTOR,
                "label": label,
                "tokens": self._label_tokens(label),
                "studyId": study_id,
            })
            if isinstance(found, dict) and isinstance(found.get("matches"), list):
                candidates = page.locator(_CANDIDATE_SELECTOR)
                return [candidates.nth(int(m["i"])) for m in found["matches"][:20]]
        except Exception as exc:
            self.logger.debug("Scoring JS indisponible, repli Python : %s", exc)
        return self._find_study_candidates_slow(page, label, study_id)

    def _find_study_candidates_slow(self, page: Page, label: str, study_id: str) -> list:
        """Version Python nœud par nœud (repli quand evaluate est indisponible)."""
        tokens = self._label_tokens(label)
        label_norm = self._norm_ui_text(label)
        id_norm = self._norm_ui_text(study_id)

        candidates = page.locator(_CANDIDATE_SELECTOR)
        try:
            count = min(candidates.count(), 500)
        except Exception:
            count = 0

        scored: list[tuple[float, int]] = []
        for i in range(count):
            node = candidates.nth(i)
            try:
                if not node.is_visible():
                    continue
                txt = self._norm_ui_text(node.inner_text())
                attrs = self._norm_ui_text(" ".join([
                    str(node.get_attribute("href") or ""),
                    str(node.get_attribute("routerlink") or ""),
                    str(node.get_attribute("ng-reflect-router-link") or ""),
                    str(node.get_attribute("data-study-id") or ""),
                    str(node.get_attribute("data-studyid") or ""),
                    str(node.get_attribute("data-id") or ""),
                    str(node.get_attribute("value") or ""),
                ]))
                if id_norm and id_norm in attrs:
                    scored.append((100.0, i))
                    continue
                if not txt:
                    continue
                if label_norm and label_norm in txt:
                    scored.append((95.0, i))
                    continue
                if tokens:
                    hits = sum(1 for tok in tokens if tok in txt)
                    coverage = hits / len(tokens)
                    # Bonus pour les titres relativement courts : évite de préférer
                    # un gros conteneur générique contenant plusieurs cartes.
                    specificity = max(0.0, 1.0 - max(0, len(txt.split()) - 20) / 80.0)
                    score = coverage * 80.0 + specificity * 10.0
                    if coverage >= 0.60:
                        scored.append((score, i))
            except Exception:
                continue
        return [candidates.nth(idx) for _, idx in sorted(scored, reverse=True)]

    def _click_search_result_candidate(self, page: Page, label: str, study_id: str) -> bool:
        """Cherche une étude dans le DOM visible puis clique sa cible exploitable.

        Angular/PrimeNG peut rendre le résultat de la recherche hors du composant
        ``okw-main-header-filtered-studies-list``. On cherche donc dans tout le
        DOM visible, en scorant les candidats sur les mots du titre et l'ID.
        Le bouton Acheter est d'abord cherché DANS la carte (pas de confusion
        avec une autre étude), puis on clique la carte et on cherche globalement.
        """
        for node in self._find_study_candidates(page, label, study_id):
            try:
                if self._click_buy_visible(page, scope=node):
                    return True
                before = page.url
                node.click(timeout=5000)
                page.wait_for_timeout(1000)
                if self._click_buy_visible(page):
                    return True
                if page.url != before:
                    # L'ouverture peut nécessiter encore un court temps de montage.
                    for _ in range(5):
                        page.wait_for_timeout(400)
                        if self._click_buy_visible(page):
                            return True
            except Exception:
                continue
        return False

    def _candidate_urls_from_row(self, row: Mapping[str, Any] | None) -> list[str]:
        """Extrait récursivement les éventuelles routes/URLs présentes dans une ligne catalogue."""
        if not isinstance(row, Mapping):
            return []
        found: list[str] = []
        keys_hint = ("url", "href", "link", "route", "path", "uri", "router", "location", "navigation")

        def walk(value: Any, key: str = "") -> None:
            if isinstance(value, Mapping):
                for k, v in value.items():
                    walk(v, str(k).lower())
            elif isinstance(value, (list, tuple)):
                for v in value:
                    walk(v, key)
            elif isinstance(value, str):
                v = value.strip()
                low = v.lower()
                hinted = any(h in key for h in keys_hint)
                if hinted or "/companies/partners/" in low or ("monthly" in low and "/companies/" in low):
                    if low.startswith(("/", "http://", "https://")):
                        found.append(v)

        walk(row)
        out: list[str] = []
        for v in found:
            full = urljoin(self.base_url + "/", v.lstrip("/")) if v.startswith("/") else v
            if self.base_url in full and full not in out:
                out.append(full)
        return out

    def _study_url_candidates(self, study_id: str, period: str | None) -> list[str]:
        """Routes UI candidates SANS navigation (audit + ligne catalogue live)."""
        urls: list[str] = []
        if period:
            try:
                month_id = self.month_id(self.period_label(period))
                for cand in study_catalog.get_ui_routes_for(study_id, month_id, self.base_url):
                    url = str((cand or {}).get("url") or "").strip()
                    if url and url not in urls:
                        urls.append(url)
            except Exception as exc:
                self.logger.debug("Routes audit indisponibles pour %s : %s", study_id, exc)
        try:
            period_code = self.period_code(period) if period else ""
            row = self._catalog_rows.get((period_code, study_id))
            if row is None and period:
                try:
                    self.catalog(period, [study_id])
                    row = self._catalog_rows.get((period_code, study_id))
                except Exception as exc:
                    self.logger.debug("Route catalogue indisponible pour %s : %s", study_id, exc)
                    row = None
            for url in self._candidate_urls_from_row(row):
                if url not in urls:
                    urls.append(url)
        except Exception as exc:
            self.logger.debug("Extraction routes catalogue %s : %s", study_id, exc)
        return urls

    def _buy_button_visible(self, page: Page, scope=None) -> bool:
        """Détecte un bouton/lien Acheter visible SANS cliquer (dry-run)."""
        for loc in self._buy_locators(page, scope):
            try:
                for i in range(min(loc.count(), 10)):
                    try:
                        if loc.nth(i).is_visible():
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
        return self._buy_text_target_exists(page, scope)

    def _wait_for_study_ready(self, page: Page, timeout_ms: int = 8000) -> bool:
        """Attend que la page d'étude soit stabilisée après changement de mois.

        Vrai quand un bouton Acheter est visible OU quand le bandeau
        (SPECIMEN + prix) est monté : dans les deux cas on sait quoi faire.
        """
        deadline = time.time() + timeout_ms / 1000.0
        while True:
            if self._buy_button_visible(page):
                return True
            try:
                low = (page.locator("body").inner_text(timeout=2000) or "").casefold()
                if "specimen" in low or "prix :" in low:
                    return True
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            try:
                page.wait_for_timeout(500)
            except Exception:
                return False

    def _navigate_to_study_url_candidates(self, page: Page, study_id: str, period: str) -> bool:
        """Ouvre la page de l'étude, sélectionne le mois, puis clique Acheter.

        Le mois dans l'URL ne commute pas la page : seul le sélecteur de mois
        en haut à droite le fait. Sans lui, on peut atterrir sur un mois déjà
        acheté (aucun bouton) et conclure à tort que l'étude est introuvable.
        """
        for url in self._study_url_candidates(study_id, period):
            try:
                self.logger.info(
                    "Achat UI | page étude | étude=%s | url=%s",
                    study_id, url,
                )
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                page.wait_for_timeout(1200)
                try:
                    self._select_purchase_period(page, period)
                except Exception as exc:
                    self.logger.debug("Sélecteur de mois indisponible %s : %s", url, exc)
                self._wait_for_study_ready(page)
                if self._click_buy_visible(page):
                    return True
            except Exception as exc:
                self.logger.debug("Route UI directe non exploitable %s : %s", url, exc)
        return False

    def resolve_study_ui_url(self, study_id: str, period: str) -> str | None:
        """Retourne la première URL UI auditée/inférée pour une étude et une période."""
        month_id = self.month_id(self.period_label(period))
        rows = study_catalog.get_ui_routes_for(study_id, month_id, self.base_url)
        return str(rows[0]["url"]) if rows else None

    def _click_search_result_dom(self, page: Page, label: str, study_id: str) -> bool:
        """Clique ou ouvre une cible de résultat trouvée par inspection DOM directe."""
        label_norm = self._norm_ui_text(label)
        token_list = self._label_tokens(label)
        token_expr = " ".join(token_list[:4])
        try:
            result = page.evaluate(r"""
            ({label, tokens, studyId}) => {
                const norm = (v) => (v || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
                const visible = (el) => {
                    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll('*')).filter(visible);
                const scored = [];
                for (const el of nodes) {
                    const txt = norm(el.innerText || el.textContent || '');
                    if (!txt) continue;
                    const attrs = [
                        el.getAttribute('href'), el.getAttribute('routerlink'), el.getAttribute('ng-reflect-router-link'),
                        el.getAttribute('data-study-id'), el.getAttribute('data-studyid'), el.getAttribute('data-id'),
                        el.getAttribute('value')
                    ].filter(Boolean).join(' ').toLocaleLowerCase();
                    let score = 0;
                    if (studyId && attrs.includes(studyId.toLocaleLowerCase())) score += 100;
                    if (label && txt.includes(label)) score += 90;
                    const hits = tokens.filter(t => txt.includes(t)).length;
                    if (tokens.length && hits >= Math.max(2, Math.ceil(tokens.length * 0.6))) score += 50 + hits;
                    if (!score) continue;
                    const target = el.closest('a,button,[role="link"],[role="button"],[routerlink],[ng-reflect-router-link]') || el;
                    const href = target.getAttribute('href') || target.getAttribute('routerlink') || target.getAttribute('ng-reflect-router-link') || '';
                    scored.push({score, href, tag: target.tagName, text:(target.innerText||target.textContent||'').trim().slice(0,300)});
                }
                scored.sort((a,b)=>b.score-a.score);
                return scored.slice(0,15);
            }
            """, {"label": label_norm, "tokens": token_list[:5], "studyId": study_id})
        except Exception as exc:
            self.logger.debug("Inspection DOM recherche échouée pour %s : %s", study_id, exc)
            return False

        for cand in result or []:
            href = str(cand.get("href") or "").strip()
            self.logger.debug("Achat UI | candidat recherche : %s", cand)
            try:
                if href and href not in ("#", "javascript:void(0)"):
                    full = urljoin(self.base_url + "/", href.lstrip("/"))
                    if self.base_url in full:
                        page.goto(full, wait_until="domcontentloaded", timeout=self.timeout)
                        page.wait_for_timeout(1000)
                        if self._click_buy_visible(page):
                            return True
                else:
                    text_loc = page.get_by_text(re.compile(re.escape(label), re.I) if label else re.compile(re.escape(token_expr), re.I)).last
                    if text_loc.is_visible():
                        text_loc.click(timeout=5000, force=True)
                        page.wait_for_timeout(1000)
                        if self._click_buy_visible(page):
                            return True
            except Exception:
                continue
        return False

    def _search_global_study(self, page: Page, study_id: str, label: str) -> bool:
        """Recherche une étude via le moteur global Angular, avec fallbacks DOM et navigation directe."""
        queries: list[str] = []
        raw = " ".join(str(label or "").split())
        tokens = self._label_tokens(raw)
        variants = [
            raw,
            " ".join(tokens[:5]),
            " ".join(tokens[:3]),
            " ".join(tokens[-3:]),
            tokens[0] if tokens else "",
            study_id,
        ]
        for q in variants:
            q = " ".join(str(q or "").split())
            if q and q not in queries:
                queries.append(q)

        inp = page.locator("input#search-query").first
        try:
            inp.wait_for(state="visible", timeout=7000)
        except Exception as exc:
            self.logger.debug("Champ recherche globale indisponible : %s", exc)
            return False

        for query in queries:
            try:
                self.logger.debug("Achat UI | requête globale : %s", query)
                inp.click()
                inp.fill("")
                # Déclenche les événements Angular même si le contrôle est un composant custom.
                inp.dispatch_event("input")
                inp.fill(query)
                inp.dispatch_event("input")
                inp.press("End")
                page.wait_for_timeout(2200)

                # Attendre plus longtemps sur le réseau distant avant d'abandonner la requête.
                deadline = time.time() + 6
                while time.time() < deadline:
                    if self._click_search_result_dom(page, label, study_id):
                        return True
                    try:
                        container = page.locator("okw-main-header-filtered-studies-list:visible").first
                        if container.count() > 0 and container.inner_text().strip():
                            if self._click_search_result_candidate(page, label, study_id):
                                return True
                    except Exception:
                        pass
                    if self._click_buy_visible(page):
                        return True
                    page.wait_for_timeout(500)

                # Fallback clavier : certains composants matérialisent le résultat sans lien DOM.
                try:
                    inp.press("ArrowDown")
                    page.wait_for_timeout(250)
                    inp.press("Enter")
                    page.wait_for_timeout(1600)
                    if self._click_buy_visible(page):
                        return True
                except Exception:
                    pass

                try:
                    inp.click()
                    inp.press("Control+A")
                    inp.press("Backspace")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
            except Exception as exc:
                self.logger.debug("Recherche globale échouée pour %s : %s", query, exc)
                continue
        return False

    def _session_expired(self, page: Page) -> bool:
        """Détecte une session portail expirée (login à refaire).

        Une zone Études introuvable en pleine série vient souvent de là :
        sans session, le menu ne se rend plus et tout échoue en cascade.
        """
        try:
            if "login" in (page.url or "").lower():
                return True
        except Exception:
            pass
        try:
            pwd = page.locator('input[type="password"]')
            if pwd.count() > 0 and pwd.first.is_visible():
                return True
        except Exception:
            pass
        return False

    def _goto_dashboard(self, page: Page) -> None:
        """Replace sur le dashboard (point de départ de toute navigation zone)."""
        url = (self.dashboard_url or f"{self.base_url}/companies").rstrip("/")
        try:
            current = (page.url or "").rstrip("/")
        except Exception:
            current = ""
        if current.lower() != url.lower():
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            page.wait_for_timeout(700)

    def _study_section_links(self, page: Page, limit: int = 6) -> list[str]:
        """Liens visibles vers des sections d'études sœurs (hors page courante)."""
        found: list[str] = []
        try:
            links = page.locator("a[href], a[routerlink], a[ng-reflect-router-link]")
            for i in range(min(links.count(), 200)):
                node = links.nth(i)
                try:
                    if not node.is_visible():
                        continue
                    href = (
                        str(node.get_attribute("href") or "")
                        or str(node.get_attribute("routerlink") or "")
                        or str(node.get_attribute("ng-reflect-router-link") or "")
                    )
                    try:
                        txt = node.inner_text() or ""
                    except Exception:
                        txt = ""
                    low = f"{href} {txt}".lower()
                    if not any(k in low for k in ("stud", "document", "comptab", "accountant",
                                                  "banque", "bank", "financ", "assur",
                                                  "fournisseur", "concurren", "expert")):
                        continue
                    full = urljoin(self.base_url + "/", href.lstrip("/")) if href.startswith("/") else href
                    if not full.startswith(self.base_url) or full in found:
                        continue
                    try:
                        if full.rstrip("/") == (page.url or "").rstrip("/"):
                            continue
                        # Page profil utilisateur, pas une section d'études.
                        if full.rstrip("/").lower().endswith("/account"):
                            continue
                    except Exception:
                        pass
                    found.append(full)
                    if len(found) >= limit:
                        break
                except Exception:
                    continue
        except Exception as exc:
            self.logger.debug("Énumération sections études : %s", exc)
        return found

    def _buy_from_marketplace(self, page: Page, study_id: str, label: str, period: str | None) -> tuple[bool, dict[str, Any]]:
        """Achète via la zone Études du portail : dashboard, zone, période, sections.

        La zone atterrit sur une section (ex. études de marché) qui n'est pas
        forcément celle de l'étude : les sections sœurs sont visitées ensuite.
        Retourne (succès, diagnostic).
        """
        detail: dict[str, Any] = {"zone_ouverte": False, "periode_ok": False,
                                 "carte": False, "sections": 0, "via": "",
                                 "sections_vues": [], "session": "active"}
        try:
            self._goto_dashboard(page)
        except Exception as exc:
            detail["erreur"] = f"dashboard inaccessible : {exc}"
            return False, detail
        if self._session_expired(page):
            detail["session"] = "expirée"
            detail["erreur"] = ("session portail expirée : reconnecte-toi (relance l'acquisition, "
                                "un nouveau login sera établi)")
            return False, detail
        # Sections collectées sur le dashboard (navigation complète),
        # avant que la zone ne restreigne la vue à une seule section.
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        dash_sections = self._study_section_links(page)
        try:
            self._open_study_area(page)
        except Exception as exc:
            detail["erreur"] = f"zone Études inaccessible : {exc}"
            return False, detail
        detail["zone_ouverte"] = True

        def _select() -> None:
            if period:
                try:
                    self._select_purchase_period(page, period)
                    detail["periode_ok"] = True
                except Exception as exc:
                    detail["erreur"] = f"sélecteur de période : {exc}"

        def _try_here() -> bool:
            self._wait_for_study_content(page, label, study_id)
            if self._find_study_candidates(page, label, study_id):
                detail["carte"] = True
                try:
                    detail["carte_page"] = page.url
                except Exception:
                    pass
            if self._click_search_result_candidate(page, label, study_id):
                try:
                    detail["via"] = page.url
                except Exception:
                    pass
                return True
            return False

        _select()
        if _try_here():
            return True, detail
        seen: set[str] = set()
        try:
            seen.add((page.url or "").rstrip("/").lower())
        except Exception:
            pass
        for url in list(dash_sections) + self._study_section_links(page):
            if detail["sections"] >= 6:
                break
            try:
                if url.rstrip("/").lower() in seen:
                    continue
            except Exception:
                pass
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                page.wait_for_timeout(1000)
            except Exception as exc:
                self.logger.debug("Section études inexploitable %s : %s", url, exc)
                continue
            try:
                seen.add((page.url or "").rstrip("/").lower())
            except Exception:
                pass
            detail["sections"] += 1
            detail["sections_vues"].append(url)
            _select()
            if _try_here():
                return True, detail
        if not detail.get("erreur"):
            detail["erreur"] = "carte d'étude introuvable (zone, période et sections sœurs visitées)"
        return False, detail

    def _open_study(self, page: Page, study_id: str, label: str, period: str | None = None) -> None:
        """Ouvre une étude et son bouton Acheter.

        Ordre : routes directes (rapide) → zone Études du portail avec période
        (chemin d'achat normal) → recherche globale → DOM courant.
        """
        if period and self._navigate_to_study_url_candidates(page, study_id, period):
            return
        market_ok, market_detail = self._buy_from_marketplace(page, study_id, label, period)
        if market_ok:
            return
        if self._search_global_study(page, study_id, label):
            return

        # Dernier recours : DOM courant (par exemple si la recherche a déjà navigué).
        if self._click_buy_visible(page):
            return
        if self._click_search_result_candidate(page, label, study_id):
            return

        tried = self._study_url_candidates(study_id, period)
        try:
            search_present = bool(page.locator("input#search-query").first.is_visible())
        except Exception:
            search_present = False
        try:
            current_url = page.url
        except Exception:
            current_url = "?"
        raise SubakouaAPIError(
            f"Étude introuvable dans l'interface : {label or study_id} ({study_id}). "
            f"{len(tried)} route(s) directe(s) testée(s), zone Études : {market_detail}, "
            f"champ de recherche global "
            f"{'présent' if search_present else 'absent'}, page courante : {current_url}."
        )

    def probe_purchase_target(self, study_id: str, period: str,
                              max_sections: int = 6) -> dict[str, Any]:
        """Dry-run SANS achat : vérifie qu'une cible d'achat est atteignable.

        Teste les routes directes PUIS la zone Études (ouverture, période,
        carte, bouton visible). Ne clique JAMAIS sur Acheter/Confirmer.
        Retourne un diagnostic structuré affichable.
        """
        if self.page is None:
            raise SubakouaAPIError("Aucune page Playwright attachée : diagnostic UI impossible.")
        page = self.page
        tried = self._study_url_candidates(study_id, period)
        via: str | None = None
        via_texte = ""
        for url in tried[:8]:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                page.wait_for_timeout(1200)
                try:
                    self._select_purchase_period(page, period)
                except Exception:
                    pass
                self._wait_for_study_ready(page)
                if self._buy_button_visible(page):
                    via = url
                    break
                if not via_texte:
                    try:
                        body = page.locator("body").inner_text(timeout=5000)
                        via_texte = self._norm_ui_text(body)[:400]
                    except Exception:
                        pass
            except Exception as exc:
                self.logger.debug("Sonde route inexploitable %s : %s", url, exc)
        try:
            search_present = bool(page.locator("input#search-query").first.is_visible())
        except Exception:
            search_present = False
        out: dict[str, Any] = {
            "period": self.period_label(period),
            "study_id": study_id,
            "trouvée": via is not None,
            "via": via or "",
            "routes_testées": len(tried),
            "via_texte": via_texte,
            "recherche_globale": "présente" if search_present else "absente",
        }
        out.update(self.probe_marketplace(study_id, period, max_sections=max_sections))
        try:
            out["page_courante"] = page.url
        except Exception:
            out["page_courante"] = "?"
        return out

    def probe_marketplace(self, study_id: str, period: str,
                            max_sections: int = 6) -> dict[str, Any]:
        """Dry-run zone Études SANS achat : zone, période, carte, bouton visible."""
        out: dict[str, Any] = {
            "marche_zone": False, "marche_periode": False,
            "marche_carte": False, "marche_acheter": False,
            "marche_sections": 0, "marche_via": "", "marche_sections_vues": [],
            "marche_session": "active", "marche_texte": "", "marche_carte_page": "",
            "marche_detail": False,
        }
        if self.page is None:
            raise SubakouaAPIError("Aucune page Playwright attachée : diagnostic UI impossible.")
        page = self.page
        try:
            label = ""
            try:
                profile = optimizer.load_profiles().get(study_id)
                label = profile.label if profile else ""
            except Exception:
                label = ""
            self._goto_dashboard(page)
            if self._session_expired(page):
                out["marche_session"] = "expirée"
                return out
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            dash_sections = self._study_section_links(page)
            self._open_study_area(page)
            out["marche_zone"] = True
            if period:
                try:
                    self._select_purchase_period(page, period)
                    out["marche_periode"] = True
                except Exception as exc:
                    self.logger.debug("Sonde période %s : %s", study_id, exc)

            def _check() -> bool:
                self._wait_for_study_content(page, label, study_id)
                cards = self._find_study_candidates(page, label, study_id)
                if cards:
                    out["marche_carte"] = True
                    try:
                        out["marche_carte_page"] = page.url
                    except Exception:
                        pass
                found = any(
                    self._buy_button_visible(page, scope=node) for node in cards[:3]
                ) if cards else False
                if found:
                    try:
                        out["marche_via"] = page.url
                    except Exception:
                        pass
                    return True
                # La carte peut mener à une page détail qui porte le bouton :
                # on l'ouvre (sans jamais cliquer Acheter) et on revérifie.
                for node in cards[:2]:
                    try:
                        node.click(timeout=5000)
                        page.wait_for_timeout(1200)
                    except Exception:
                        continue
                    out["marche_detail"] = True
                    if self._buy_button_visible(page):
                        try:
                            out["marche_via"] = page.url
                        except Exception:
                            pass
                        return True
                try:
                    body = page.locator("body").inner_text(timeout=5000)
                    out["marche_texte"] = self._norm_ui_text(body)[:400]
                except Exception:
                    pass
                return False

            if _check():
                out["marche_acheter"] = True
                return out
            seen: set[str] = set()
            try:
                seen.add((page.url or "").rstrip("/").lower())
            except Exception:
                pass
            for url in list(dash_sections) + self._study_section_links(page):
                if out["marche_sections"] >= max_sections:
                    break
                try:
                    if url.rstrip("/").lower() in seen:
                        continue
                except Exception:
                    pass
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                    page.wait_for_timeout(1000)
                except Exception:
                    continue
                try:
                    seen.add((page.url or "").rstrip("/").lower())
                except Exception:
                    pass
                out["marche_sections"] += 1
                out["marche_sections_vues"].append(url)
                if period:
                    try:
                        self._select_purchase_period(page, period)
                        out["marche_periode"] = True
                    except Exception:
                        pass
                if _check():
                    out["marche_acheter"] = True
                    return out
        except Exception as exc:
            self.logger.debug("Sonde marketplace %s : %s", study_id, exc)
        return out

    def _find_visible_purchase_dialog(self, page: Page):
        """Retourne le dialogue d'achat réellement visible, indépendamment de l'accessibility tree."""
        dialogs = page.locator('[role="dialog"]')
        visible = []
        try:
            count = min(dialogs.count(), 30)
        except Exception:
            count = 0
        for i in range(count):
            dlg = dialogs.nth(i)
            try:
                if dlg.is_visible():
                    visible.append(dlg)
            except Exception:
                continue
        return visible[-1] if visible else None

    def _click_purchase_confirm(self, page: Page, dialog) -> bool:
        """Clique le bouton Confirmer avec fallbacks DOM/JS pour PrimeNG/Angular."""
        patterns = [
            re.compile(r"^\s*Confirmer\s*$", re.I),
            re.compile(r"Confirmer", re.I),
        ]
        for pattern in patterns:
            try:
                buttons = dialog.locator('button').filter(has_text=pattern)
                count = min(buttons.count(), 10)
                for i in range(count - 1, -1, -1):
                    btn = buttons.nth(i)
                    if not btn.is_visible():
                        continue
                    try:
                        btn.click(timeout=5000)
                        return True
                    except Exception:
                        try:
                            btn.click(timeout=3000, force=True)
                            return True
                        except Exception:
                            pass
            except Exception:
                pass

        # Le HTML d'audit montre un <button type="button"><span class="p-button-label">Confirmer</span>.
        # Si les locators Playwright échouent à cause d'Angular, déclenche le clic DOM directement.
        try:
            clicked = page.evaluate("""() => {
                const dialogs = Array.from(document.querySelectorAll('[role=\"dialog\"]')).filter(el => {
                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                });
                const dlg = dialogs[dialogs.length - 1];
                if (!dlg) return false;
                const btn = Array.from(dlg.querySelectorAll('button')).find(b =>
                    (b.innerText || b.textContent || '').trim().toLocaleLowerCase().includes('confirmer')
                );
                if (!btn) return false;
                btn.click();
                return true;
            }""")
            return bool(clicked)
        except Exception:
            return False

    def purchase_study(
        self,
        purchase_period: str,
        study_id: str,
        *,
        label: str = "",
        expected_price: float | None = None,
    ) -> dict[str, Any]:
        """Achète une étude via l'interface réelle de Subakoua.

        La navigation est volontairement instrumentée étape par étape : un achat ne
        doit jamais rester silencieux plusieurs minutes après le login.
        """
        if self.page is None:
            raise SubakouaAPIError("Aucune page Playwright attachée au client : achat UI impossible.")

        page = self.page
        wanted_period = self.period_label(purchase_period)
        self.logger.info("Achat UI démarré | étude=%s | libellé=%s | période=%s", study_id, label or study_id, wanted_period)

        # Après le login, la page est déjà /companies. Ne recharge surtout pas cette
        # page inutilement : cela pouvait bloquer le premier achat alors que la session
        # était correctement établie. Pour les achats suivants, on revient au dashboard.
        try:
            if "/companies" not in (page.url or "").lower():
                self.logger.info("Achat UI | retour dashboard : %s", self.dashboard_url)
                page.goto(self.dashboard_url, wait_until="domcontentloaded", timeout=self.timeout)
                page.wait_for_timeout(700)
            else:
                self.logger.info("Achat UI | dashboard déjà ouvert : %s", page.url)
        except Exception as exc:
            raise SubakouaAPIError(f"Impossible d'ouvrir le dashboard avant achat {study_id}: {exc}") from exc

        self.logger.info(
            "Achat UI | navigation directe par URL | étude=%s | month_id=%s",
            study_id, self.month_id(wanted_period),
        )

        try:
            self._open_study(page, study_id, label, wanted_period)
        except Exception as exc:
            self.logger.error("Achat UI | ouverture étude échouée | étude=%s | %s", study_id, exc)
            raise

        self.logger.info("Achat UI | bouton Acheter exécuté, attente de la confirmation")
        # Attente robuste : on cherche le dialog réel et, à défaut, le texte de la demande.
        dialog = None
        deadline = time.time() + 12
        while time.time() < deadline:
            dialog = self._find_visible_purchase_dialog(page)
            if dialog is not None:
                break
            try:
                body = page.locator('body').inner_text(timeout=1000)
                if "Confirmez-vous l'achat" in body:
                    dialog = self._find_visible_purchase_dialog(page)
                    if dialog is not None:
                        break
            except Exception:
                pass
            page.wait_for_timeout(250)

        if dialog is None:
            raise SubakouaAPIError(
                f"La fenêtre de confirmation d'achat n'est pas apparue pour {study_id}. URL={page.url}"
            )

        body = dialog.inner_text()
        self.logger.info("Achat UI | dialogue détecté : %s", body.replace("\n", " ")[:250])

        if expected_price is not None:
            candidates = {
                f"{expected_price:,.2f}".replace(",", "§").replace(".", ",").replace("§", "."),
                f"{expected_price:.2f}",
                f"{expected_price:g}",
            }
            if not any(value in body for value in candidates):
                raise SubakouaAPIError(
                    f"Prix affiché différent pour {study_id} : attendu {expected_price:.2f} €. Dialogue={body!r}"
                )

        self.logger.info("Achat UI | clic Confirmer : %s", study_id)
        if not self._click_purchase_confirm(page, dialog):
            raise SubakouaAPIError(
                f"Bouton Confirmer introuvable dans la fenêtre d'achat de {study_id}. Dialogue={body!r}"
            )

        self.logger.info("Achat UI | Confirmer cliqué, vérification de l'achat côté catalogue")
        try:
            dialog.wait_for(state="hidden", timeout=10000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(1200)

        live = None
        for attempt in range(1, 7):
            try:
                live = next((x for x in self.catalog(wanted_period, [study_id]) if x.study_id == study_id), None)
                if live is not None and live.bought_by_team:
                    break
            except Exception as exc:
                self.logger.warning("Achat UI | vérification %s/6 impossible : %s", attempt, exc)
            page.wait_for_timeout(700)

        if live is None or not live.bought_by_team:
            raise SubakouaAPIError(
                f"Le clic 'Confirmer' a été effectué mais l'achat de {study_id} n'est pas confirmé par le catalogue live."
            )

        self.logger.info(
            "Achat UI confirmé | étude=%s période=%s prix=%s",
            study_id,
            self.period_code(wanted_period),
            live.price,
        )
        return {
            "studyId": study_id,
            "period": wanted_period,
            "price": live.price,
            "boughtByTeam": True,
            "purchaseConfirmedByUI": True,
        }

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
    api = SubakouaAPIClient(context, logger=log, base_url=legacy.DOMAINE_BASE, timeout_ms=config.timeout_ms, page=page, dashboard_url=legacy.URL_DASHBOARD)
    api.get_player_context(refresh=True)
    log.info("Client Subakoua prêt | page=%s | moteur d'achat disponible", page.url)
    return pw, browser, context, page, api
