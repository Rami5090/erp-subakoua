"""Navigation d'achat : diagnostic d'échec + dry-run sans achat (page factice)."""
import logging

import pytest

from subakoua_api import SubakouaAPIClient, SubakouaAPIError


class FakeNode:
    def __init__(self, visible=False, text="", attrs=None, scoped_buy=False, on_click=None):
        self._visible = visible
        self._text = text
        self._attrs = attrs or {}
        self._scoped_buy = scoped_buy
        self._on_click = on_click
        self.clicked = False

    def is_visible(self):
        return self._visible

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def click(self, timeout=None, force=False):
        self.clicked = True
        if self._on_click is not None:
            self._on_click()
        return True

    def wait_for(self, state=None, timeout=None):
        pass

    def get_by_role(self, role, name=None):
        if role == "button" and self._scoped_buy:
            return FakeLocator([FakeNode(True, text="Acheter")], wait_raises=False)
        return FakeLocator([], wait_raises=False)


class FakeLocator:
    def __init__(self, nodes=(), wait_raises=True):
        self._nodes = list(nodes)
        self._wait_raises = wait_raises

    def count(self):
        return len(self._nodes)

    def nth(self, i):
        return self._nodes[i]

    @property
    def first(self):
        return self._nodes[0] if self._nodes else FakeNode(False)

    @property
    def last(self):
        return self._nodes[-1] if self._nodes else FakeNode(False)

    def wait_for(self, state=None, timeout=None):
        if self._wait_raises:
            raise RuntimeError("timeout")

    def filter(self, **kwargs):
        want = str(kwargs.get("has_text", ""))
        return FakeLocator([n for n in self._nodes if want in n.inner_text()],
                           wait_raises=False)

    def inner_text(self, timeout=None):
        return ""


class FakePage:
    def __init__(self, *, buy_visible=False, search_visible=False, links=(),
                 card=None, card_on=None, sections=(),
                 period_current="Année 1 - Janvier",
                 start_url="https://subakoua.arkhe.com/companies",
                 links_after_calls=0, password_visible=False,
                 evaluate_result="__raise__", link_buy_visible=False):
        self.url = start_url
        self.visited = []
        self._buy_visible = buy_visible
        self._search_visible = search_visible
        self._password_visible = password_visible
        self._links = list(links)
        self._links_after_calls = links_after_calls
        self._role_calls = 0
        self._card = card
        self._card_on = card_on
        self._sections = list(sections)
        self._period_current = period_current
        self._evaluate_result = evaluate_result
        self._link_buy_visible = link_buy_visible

    def evaluate(self, expr, arg=None):
        if self._evaluate_result == "__raise__":
            raise RuntimeError("evaluate indisponible")
        return self._evaluate_result

    def _links_ready(self):
        return self._role_calls > self._links_after_calls

    def _card_here(self):
        if not self._card:
            return []
        if self._card_on is None or any(s in (self.url or "") for s in self._card_on):
            return [FakeNode(True, text=self._card.get("text", ""),
                             scoped_buy=bool(self._card.get("buy")),
                             on_click=self._fire_card_click)]
        return []

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def _nav_nodes(self):
        return [FakeNode(True, text=t) for t in self._links]

    def _card_nodes(self):
        return self._card_here()

    def _fire_card_click(self):
        self.reveal_buy = True

    def _section_nodes(self):
        return [FakeNode(True, text=t, attrs={"href": h}) for h, t in self._sections]

    def get_by_role(self, role, name=None):
        import re as _re
        if role in ("link", "button"):
            self._role_calls += 1
            pat = name.pattern if hasattr(name, "pattern") else str(name or "")
            try:
                rx = _re.compile(pat, _re.I)
            except Exception:
                rx = None
            nodes = [n for n in self._nav_nodes()
                     if self._links_ready() and (rx is None or rx.search(n.inner_text()))]
            if role == "button" and self._buy_visible:
                nodes = nodes + [FakeNode(True, text="Acheter")]
            if role == "button" and getattr(self, "reveal_buy", False):
                nodes = nodes + [FakeNode(True, text="Acheter")]
            if role == "link" and self._link_buy_visible:
                nodes = nodes + [FakeNode(True, text="Acheter l'étude")]
            return FakeLocator(nodes, wait_raises=False)
        return FakeLocator([], wait_raises=False)

    def get_by_text(self, label, exact=False):
        nodes = [n for n in self._nav_nodes()
                 if self._links_ready() and (n.inner_text() == label if exact else label in n.inner_text())]
        return FakeLocator(nodes, wait_raises=False)

    def locator(self, sel):
        if sel == "input#search-query":
            if self._search_visible:
                return FakeLocator([FakeNode(True)], wait_raises=False)
            return FakeLocator([FakeNode(False)], wait_raises=True)
        if sel == 'input[type="password"]':
            if self._password_visible:
                return FakeLocator([FakeNode(True)], wait_raises=False)
            return FakeLocator([], wait_raises=False)
        if "a[href]" in sel:
            return FakeLocator(self._section_nodes(), wait_raises=False)
        if "okw-select-period" in sel:
            return FakeLocator([FakeNode(True, text=self._period_current)],
                               wait_raises=False)
        if "p-select-option-label" in sel:
            return FakeLocator([FakeNode(True, text=self._period_current)],
                               wait_raises=False)
        return FakeLocator(self._nav_nodes() + self._card_nodes(),
                           wait_raises=False)


def make_client(page):
    client = SubakouaAPIClient.__new__(SubakouaAPIClient)
    client.base_url = "https://subakoua.arkhe.com"
    client.dashboard_url = "https://subakoua.arkhe.com/companies"
    client.timeout = 5000
    client.logger = logging.getLogger("test_probe")
    client.page = page
    client._catalog_rows = {
        ("0101", "peeedccg"): {
            "studyId": "peeedccg",
            "link": "/companies/partners/internalData/stocks/companyStock/20/MONTHLY",
        }
    }
    client.catalog = lambda period, ids=None: []
    return client


def _no_wait(client):
    """Attente instantanée : le mécanisme d'attente est couvert ailleurs,
    ici on valide les chemins sans payer les timeouts négatifs."""
    real = client._wait_for_study_content
    client._wait_for_study_content = lambda *a, **k: real(*a, **{**k, "timeout_ms": 50})
    return client


def test_open_study_erreur_diagnostique():
    page = FakePage()
    client = make_client(page)
    with pytest.raises(SubakouaAPIError) as exc_info:
        client._open_study(page, "peeedccg", "Chiffre d'affaires", "Année 1 - Janvier")
    msg = str(exc_info.value)
    assert "route(s) directe(s) testée(s)" in msg
    assert "champ de recherche global absent" in msg
    assert "page courante" in msg


def test_probe_trouve_sans_cliquer():
    page = FakePage(buy_visible=True)
    client = make_client(page)
    client = _no_wait(client)
    out = client.probe_purchase_target("peeedccg", "Année 1 - Janvier")
    assert out["trouvée"] is True
    assert out["via"]
    assert out["routes_testées"] >= 1


def test_probe_introuvable_structure():
    page = FakePage()
    client = make_client(page)
    out = client.probe_purchase_target("peeedccg", "Année 1 - Janvier")
    assert out["trouvée"] is False
    assert out["via"] == ""
    assert out["recherche_globale"] == "absente"


def test_marketplace_succes_scoped_carte():
    page = FakePage(
        links=["Études"],
        card={"text": "Calcul des coûts de production", "buy": True},
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    ok, detail = client._buy_from_marketplace(
        page, "productionCostDetailStudy",
        "Calcul des coûts de production", "Année 1 - Janvier")
    assert ok is True
    assert detail["zone_ouverte"] is True
    assert detail["periode_ok"] is True
    assert detail["carte"] is True


def test_marketplace_sans_zone():
    page = FakePage()
    client = make_client(page)
    ok, detail = client._buy_from_marketplace(page, "x", "Y", "Année 1 - Janvier")
    assert ok is False
    assert detail["zone_ouverte"] is False
    assert "erreur" in detail


def test_open_study_replie_marketplace():
    page = FakePage(
        links=["Études"],
        card={"text": "Calcul des coûts de production", "buy": True},
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    # Ne lève pas : les routes directes échouent, la zone Études prend le relais.
    client._open_study(page, "productionCostDetailStudy",
                       "Calcul des coûts de production", "Année 1 - Janvier")


def test_probe_marketplace_cles():
    page = FakePage(
        links=["Études"],
        card={"text": "TVA", "buy": True},
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    out = client.probe_marketplace("peeefstvp", "Année 1 - Janvier")
    assert out["marche_zone"] is True
    assert out["marche_periode"] is True
    assert out["marche_carte"] is True
    assert out["marche_acheter"] is True


def test_section_soeur_trouve_la_carte():
    page = FakePage(
        links=["Études"],
        sections=[("/companies/partners/accountant/synthesis", "Comptabilité")],
        card={"text": "TVA", "buy": True},
        card_on=["accountant"],
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    client = _no_wait(client)
    ok, detail = client._buy_from_marketplace(page, "peeefstvp", "TVA", "Année 1 - Janvier")
    assert ok is True
    assert detail["sections"] == 1
    assert "accountant" in detail["via"]
    assert detail["carte"] is True


def test_repart_du_dashboard():
    page = FakePage(
        links=["Études"],
        card={"text": "TVA", "buy": True},
        period_current="Année 1 - Janvier",
        start_url="https://subakoua.arkhe.com/companies/partners/internalData/stocks/x",
    )
    client = make_client(page)
    ok, detail = client._buy_from_marketplace(page, "peeefstvp", "TVA", "Année 1 - Janvier")
    assert ok is True
    assert page.visited[0] == "https://subakoua.arkhe.com/companies"


def test_probe_sections_remontees():
    page = FakePage(
        links=["Études"],
        sections=[("/companies/partners/accountant/synthesis", "Comptabilité")],
        card={"text": "TVA", "buy": True},
        card_on=["accountant"],
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    client = _no_wait(client)
    out = client.probe_marketplace("peeefstvp", "Année 1 - Janvier")
    assert out["marche_acheter"] is True
    assert out["marche_sections"] == 1
    assert "accountant" in out["marche_via"]


def test_zone_reessayee_si_menu_lent():
    page = FakePage(links=["Études"], links_after_calls=2)
    client = make_client(page)
    client._open_study_area(page)  # ne lève pas : réessaie jusqu'au rendu du menu


def test_session_expiree_detectee():
    page = FakePage(links=["Études"], password_visible=True)
    client = make_client(page)
    assert client._session_expired(page) is True
    ok, detail = client._buy_from_marketplace(page, "x", "Y", "Année 1 - Janvier")
    assert ok is False
    assert detail["session"] == "expirée"
    out = client.probe_marketplace("x", "Année 1 - Janvier")
    assert out["marche_session"] == "expirée"
    assert out["marche_zone"] is False


def test_session_active_par_defaut():
    page = FakePage(links=["Études"])
    client = make_client(page)
    assert client._session_expired(page) is False


def test_detail_ouvert_si_carte_sans_bouton():
    page = FakePage(
        links=["Études"],
        card={"text": "TVA", "buy": False},
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    client = _no_wait(client)
    out = client.probe_marketplace("peeefstvp", "Année 1 - Janvier")
    assert out["marche_carte"] is True
    assert out["marche_detail"] is True
    assert out["marche_acheter"] is True
    assert out["marche_via"] != ""


def test_lien_acheter_detecte():
    page = FakePage(link_buy_visible=True)
    client = make_client(page)
    assert client._buy_button_visible(page) is True
    page2 = FakePage()
    assert client._buy_button_visible(page2) is False


def test_sections_dashboard_avant_zone():
    seen_urls = []

    class DashPage(FakePage):
        def locator(self, sel):
            if "a[href]" in sel and self.url.endswith("/companies"):
                return FakeLocator(
                    [FakeNode(True, text="Comptabilité",
                              attrs={"href": "/companies/partners/accountant/synthesis"})],
                    wait_raises=False)
            return super().locator(sel)

    page = DashPage(
        links=["Études"],
        card={"text": "TVA", "buy": True},
        card_on=["accountant"],
        period_current="Année 1 - Janvier",
    )
    orig_goto = page.goto
    def goto(url, **kwargs):
        seen_urls.append(url)
        orig_goto(url, **kwargs)
    page.goto = goto
    client = make_client(page)
    client = _no_wait(client)
    ok, detail = client._buy_from_marketplace(page, "peeefstvp", "TVA", "Année 1 - Janvier")
    assert ok is True
    assert seen_urls[0] == "https://subakoua.arkhe.com/companies/partners/accountant/synthesis"


def test_via_texte_remonte():
    page = FakePage()
    client = make_client(page)
    out = client.probe_purchase_target("peeedccg", "Année 1 - Janvier")
    assert "via_texte" in out


def test_scoring_js_rapide():
    page = FakePage(
        card={"text": "TVA", "buy": True},
        evaluate_result={"total": 1, "matches": [{"i": 0, "score": 95}]},
    )
    client = make_client(page)
    nodes = client._find_study_candidates(page, "TVA", "peeefstvp")
    assert len(nodes) == 1
    assert "TVA" in nodes[0].inner_text()


def test_probe_texte_remonte():
    page = FakePage(links=["Études"])
    client = make_client(page)
    client = _no_wait(client)
    out = client.probe_marketplace("x", "Année 1 - Janvier")
    assert "marche_texte" in out


def test_progression_remontee():
    from test_smart_scraper_acquire import FakeAPI, FakeConnection
    import smart_scraper as scraper
    api = FakeAPI()
    conn = FakeConnection()
    seen = []
    out = scraper.acquire_studies(api, conn, target_periods=["P"], study_ids=["a", "b"],
                                  budget=0.0, allow_real_purchases=False,
                                  progress=seen.append)
    assert len(seen) == 2
    assert all("🔎" in s for s in seen)
    assert len(out) == 2


def test_sonde_limite_sections():
    page = FakePage(
        links=["Études"],
        sections=[(f"/s{i}", f"Studies {i}") for i in range(5)],
        card_on=["jamais"],
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    client = _no_wait(client)
    out = client.probe_marketplace("x", "Année 1 - Janvier", max_sections=2)
    assert out["marche_sections"] == 2


def test_sections_vues_tracees():
    page = FakePage(
        links=["Études"],
        sections=[("/companies/partners/accountant/synthesis", "Comptabilité")],
        card_on=["jamais"],
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    client = _no_wait(client)
    ok, detail = client._buy_from_marketplace(page, "x", "Introuvable", "Année 1 - Janvier")
    assert ok is False
    assert detail["sections"] == 1
    assert detail["sections_vues"] == ["https://subakoua.arkhe.com/companies/partners/accountant/synthesis"]


def test_page_profil_exclue_des_sections():
    page = FakePage(
        links=["Études"],
        sections=[("/account", "Bank account settings"),
                  ("/companies/partners/accountant/synthesis", "Comptabilité")],
        period_current="Année 1 - Janvier",
    )
    client = make_client(page)
    links = client._study_section_links(page)
    assert links == ["https://subakoua.arkhe.com/companies/partners/accountant/synthesis"]
