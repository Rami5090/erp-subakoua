"""Navigation d'achat : diagnostic d'échec + dry-run sans achat (page factice)."""
import logging

import pytest

from subakoua_api import SubakouaAPIClient, SubakouaAPIError


class FakeNode:
    def __init__(self, visible=False, text="", attrs=None):
        self._visible = visible
        self._text = text
        self._attrs = attrs or {}
        self.clicked = False

    def is_visible(self):
        return self._visible

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def click(self, timeout=None, force=False):
        self.clicked = True
        return True


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

    def wait_for(self, state=None, timeout=None):
        if self._wait_raises:
            raise RuntimeError("timeout")

    def filter(self, **kwargs):
        return self

    def inner_text(self, timeout=None):
        return ""


class FakePage:
    def __init__(self, *, buy_visible=False, search_visible=False):
        self.url = "https://subakoua.arkhe.com/companies"
        self.visited = []
        self._buy_visible = buy_visible
        self._search_visible = search_visible

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def get_by_role(self, role, name=None):
        if role == "button" and self._buy_visible:
            return FakeLocator([FakeNode(True, text="Acheter")], wait_raises=False)
        return FakeLocator([], wait_raises=False)

    def locator(self, sel):
        if sel == "input#search-query":
            if self._search_visible:
                return FakeLocator([FakeNode(True)], wait_raises=False)
            return FakeLocator([FakeNode(False)], wait_raises=True)
        return FakeLocator([], wait_raises=False)


def make_client(page):
    client = SubakouaAPIClient.__new__(SubakouaAPIClient)
    client.base_url = "https://subakoua.arkhe.com"
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
