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

from playwright.sync_api import Page
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

    def _select_purchase_period(self, page: Page, period: str) -> None:
        selector = "okw-select-period span[role='combobox']"
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=self.timeout)
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
        """Ouvre la zone documentaire du portail avec plusieurs sélecteurs robustes."""
        labels = ["Études", "Etudes", "Documents", "Études disponibles", "Etudes disponibles"]
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
        raise SubakouaAPIError("Zone Études/Documents introuvable dans le portail Subakoua.")

    def _click_buy_visible(self, page: Page, scope=None) -> bool:
        """Clique sur le premier bouton Acheter visible dans le scope donné."""
        root = scope or page
        for pattern in (
            r"acheter",
            r"acheter l[’\']étude",
        ):
            try:
                loc = root.get_by_role("button", name=re.compile(pattern, re.I))
                if self._click_first_visible(loc, timeout=5000):
                    page.wait_for_timeout(600)
                    return True
            except Exception:
                pass
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

    def _click_search_result_candidate(self, page: Page, label: str, study_id: str) -> bool:
        """Cherche une étude dans le DOM visible puis clique sa cible exploitable.

        Angular/PrimeNG peut rendre le résultat de la recherche hors du composant
        ``okw-main-header-filtered-studies-list``. On cherche donc dans tout le
        DOM visible, en scorant les candidats sur les mots du titre et l'ID.
        """
        tokens = self._label_tokens(label)
        label_norm = self._norm_ui_text(label)
        id_norm = self._norm_ui_text(study_id)

        candidates = page.locator(
            'a, button, [role="link"], [role="button"], li, [tabindex="0"], '
            '[routerlink], [ng-reflect-router-link]'
        )
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

        for _, idx in sorted(scored, reverse=True):
            node = candidates.nth(idx)
            try:
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

    def _search_global_study(self, page: Page, study_id: str, label: str) -> bool:
        """Ouvre une étude via la recherche globale réelle du portail.

        Le résultat de recherche est dynamique et peut être rendu hors du
        composant ``okw-main-header-filtered-studies-list``. On déclenche donc
        une vraie saisie clavier, attend le rendu Angular, puis cherche la cible
        dans tout le DOM visible. Plusieurs variantes du titre servent de
        fallback lorsque le libellé contient de la ponctuation ou une apostrophe.
        """
        queries: list[str] = []
        for q in (label, " ".join(self._label_tokens(label)[:5]), study_id):
            q = " ".join(str(q or "").split())
            if q and q not in queries:
                queries.append(q)

        inp = page.locator("input#search-query").first
        try:
            inp.wait_for(state="visible", timeout=7000)
        except Exception:
            return False

        for query in queries:
            try:
                inp.click()
                inp.press("Control+A")
                # `fill()` ne suffit pas sur certaines versions Angular : le
                # composant du portail écoute également les événements clavier.
                inp.press_sequentially(query, delay=25)
                page.wait_for_timeout(1400)

                # Le résultat peut apparaître dans le composant dédié ou dans un
                # overlay global ; dans les deux cas, on tente une cible exacte.
                if label:
                    try:
                        page.wait_for_function(
                            "([q]) => document.body && document.body.innerText.toLocaleLowerCase().includes(q.toLocaleLowerCase())",
                            arg=[label],
                            timeout=3500,
                        )
                    except Exception:
                        pass

                # 1) Composant de recherche dédié.
                container = page.locator("okw-main-header-filtered-studies-list:visible").first
                try:
                    if container.count() > 0:
                        exactish = container.get_by_text(label, exact=False) if label else container.get_by_text(query, exact=False)
                        count = min(exactish.count(), 30)
                        for i in range(count):
                            node = exactish.nth(i)
                            if not node.is_visible():
                                continue
                            ancestor = node
                            for _ in range(10):
                                try:
                                    tag = ancestor.evaluate(
                                        "el => ({tag: el.tagName, role: el.getAttribute('role'), href: el.getAttribute('href'), router: el.getAttribute('routerlink')})"
                                    )
                                    if tag and (tag.get("tag") in ("A", "BUTTON") or tag.get("role") in ("link", "button") or tag.get("href") or tag.get("router")):
                                        ancestor.click(timeout=5000)
                                        page.wait_for_timeout(1000)
                                        if self._click_buy_visible(page):
                                            return True
                                        break
                                except Exception:
                                    pass
                                ancestor = ancestor.locator("xpath=..")
                except Exception:
                    pass

                # 2) Toute cible visible de la page, y compris les overlays.
                if self._click_search_result_candidate(page, label, study_id):
                    return True

                # 3) Certains composants gèrent encore la sélection au clavier.
                try:
                    inp.press("ArrowDown")
                    page.wait_for_timeout(250)
                    inp.press("Enter")
                    page.wait_for_timeout(1200)
                    if self._click_buy_visible(page):
                        return True
                except Exception:
                    pass

                # Efface avant la variante suivante.
                try:
                    inp.click()
                    inp.press("Control+A")
                    inp.press("Backspace")
                    page.wait_for_timeout(250)
                except Exception:
                    pass
            except Exception:
                continue
        return False

    def _open_study(self, page: Page, study_id: str, label: str) -> None:
        """Ouvre une étude et son bouton Acheter.

        Le catalogue API peut connaître une étude alors que son libellé n'est pas
        présent comme texte exact dans le DOM (cartes Angular, traduction,
        titre tronqué, etc.). On cherche donc d'abord l'identifiant dans les
        attributs/href, puis le libellé de façon souple, puis une recherche UI.
        """
        # 0) Voie prioritaire observée dans l'audit : recherche globale.
        if self._search_global_study(page, study_id, label):
            if self._click_buy_visible(page):
                return

        # 1) Cas le plus fiable : l'identifiant est présent dans href/data-* du DOM.
        id_selectors = [
            f'[data-study-id="{study_id}"]',
            f'[data-studyid="{study_id}"]',
            f'[data-id="{study_id}"]',
            f'a[href*="{study_id}"]',
            f'button[value="{study_id}"]',
            f'[id*="{study_id}"]',
        ]
        for selector in id_selectors:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 20)
            except Exception:
                count = 0
            for i in range(count):
                item = loc.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    # Le bouton peut être directement sur la carte.
                    if self._click_buy_visible(page, item):
                        return
                    item.click(timeout=5000)
                    page.wait_for_timeout(700)
                    if self._click_buy_visible(page):
                        return
                except Exception:
                    continue

        # 2) Libellé souple (exact=False), puis remontée de plusieurs niveaux
        # pour couvrir les structures Angular profondément imbriquées.
        item = self._visible_text_locator(page, label)
        if item is not None:
            ancestor = item
            for _ in range(12):
                try:
                    if self._click_buy_visible(page, ancestor):
                        return
                except Exception:
                    pass
                try:
                    ancestor.click(timeout=5000)
                    page.wait_for_timeout(700)
                    if self._click_buy_visible(page):
                        return
                except Exception:
                    pass
                ancestor = ancestor.locator("xpath=..")

        # 3) Recherche éventuelle dans un champ de recherche du catalogue.
        if label:
            try:
                inputs = page.locator("input")
                for i in range(min(inputs.count(), 30)):
                    inp = inputs.nth(i)
                    if not inp.is_visible():
                        continue
                    meta = " ".join([
                        str(inp.get_attribute("placeholder") or ""),
                        str(inp.get_attribute("aria-label") or ""),
                        str(inp.get_attribute("name") or ""),
                    ]).lower()
                    if any(token in meta for token in ("recherch", "search", "étude", "etude", "document")):
                        inp.fill(label)
                        page.wait_for_timeout(900)
                        item = self._visible_text_locator(page, label)
                        if item is not None:
                            ancestor = item
                            for _ in range(12):
                                if self._click_buy_visible(page, ancestor):
                                    return
                                try:
                                    ancestor.click(timeout=5000)
                                    page.wait_for_timeout(600)
                                    if self._click_buy_visible(page):
                                        return
                                except Exception:
                                    pass
                                ancestor = ancestor.locator("xpath=..")
            except Exception:
                pass

        # 4) Dernier essai : tout élément visible contenant l'identifiant ou le titre.
        try:
            all_clickables = page.locator("a, button, [role='button'], [role='link']")
            for i in range(min(all_clickables.count(), 200)):
                node = all_clickables.nth(i)
                if not node.is_visible():
                    continue
                text = " ".join(node.inner_text().split()).lower()
                attrs = " ".join([
                    str(node.get_attribute("href") or ""),
                    str(node.get_attribute("data-study-id") or ""),
                    str(node.get_attribute("data-id") or ""),
                ]).lower()
                if study_id.lower() in attrs or (label and label.lower() in text):
                    try:
                        node.click(timeout=5000)
                        page.wait_for_timeout(700)
                        if self._click_buy_visible(page):
                            return
                    except Exception:
                        continue
        except Exception:
            pass

        raise SubakouaAPIError(
            f"Étude introuvable dans l'interface : {label or study_id} ({study_id}). "
            "Le catalogue API la connaît mais aucun lien/carte exploitable n'a été trouvé."
        )

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

        self.logger.info("Achat UI | sélection période : %s", wanted_period)
        self._select_purchase_period(page, wanted_period)
        self.logger.info("Achat UI | recherche de l'étude : %s", study_id)

        try:
            self._open_study(page, study_id, label)
        except SubakouaAPIError:
            self.logger.info("Achat UI | recherche primaire échouée, ouverture de la zone Études/Documents")
            self._open_study_area(page)
            self._select_purchase_period(page, wanted_period)
            self._open_study(page, study_id, label)

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
