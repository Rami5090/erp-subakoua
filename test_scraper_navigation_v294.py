"""Logique de navigation du scraper avec un Playwright factice (sous-processus isolé : stubs de modules)."""
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent

SCRIPT = textwrap.dedent('''
    import sys, types, logging
    sys.path.insert(0, %(root)r)
    for n in ["pymysql", "dotenv", "playwright", "playwright.sync_api"]:
        sys.modules[n] = types.ModuleType(n)
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    pw = sys.modules["playwright.sync_api"]
    for a in ["BrowserContext", "Locator", "Page", "TimeoutError", "sync_playwright"]:
        setattr(pw, a, object)
    import scraper_subakoua as m

    class Ctx:
        def __init__(self): self.pages = []

    class Pg:
        """Page factice. `forms` = nombre de champs de formulaire visibles ; `on_js_click` agit sur le contexte."""
        def __init__(self, ctx, url, forms=0):
            self.context, self.url, self.forms, self.closed = ctx, url, forms, False
            ctx.pages.append(self)
        def wait_for_timeout(self, ms): pass
        def wait_for_load_state(self, *a, **k): pass
        def evaluate(self, js, *args):
            if js is m.JS_FORMULAIRE_PRESENT: return self.forms
            return self.behaviour(js, args)
        def locator(self, *a, **k): raise RuntimeError("pas de locator dans ce test")
        def screenshot(self, **k): pass
        def content(self): return "<html/>"
        def goto(self, url, **k): self.url = url
        def close(self): self.closed = True

    cfg = types.SimpleNamespace(timeout_ms=500, save_screenshot_on_error=False, save_html_on_error=False, output_dir=None)
    log = logging.getLogger("t")
    m.wait_page_stable = lambda page, t: None
    m.maybe_save_debug = lambda *a, **k: None
    m.ouvrir_module = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("texte introuvable"))
    m._clic_playwright_tuile = lambda page, labels: page.on_mouse_click()
    m.attendre_formulaire = lambda page, t=0: page.forms > 0

    def run(case):
        ctx = Ctx()
        home = Pg(ctx, "https://x/companies")
        journal = []
        if case == "js_ok":
            def beh(js, args):
                home.url, home.forms = "https://x/decisions/marketing", 12; return True
            home.behaviour = beh
        elif case == "js_noop_mouse_ok":
            home.behaviour = lambda js, args: True            # clic JS « réussit » mais rien ne se passe
            def mouse():
                home.url, home.forms = "https://x/decisions/marketing", 8
            home.on_mouse_click = mouse
        elif case == "new_tab":
            home.behaviour = lambda js, args: True
            def mouse():
                Pg(ctx, "https://x/decisions/marketing", forms=5)
            home.on_mouse_click = mouse
        elif case == "nothing":
            home.behaviour = lambda js, args: True
            home.on_mouse_click = lambda: None
        try:
            res = m.ouvrir_decision(home, "marketing", cfg, log, journal=journal)
            return ("OK", res is home, res.url, [j["strategie"] for j in journal])
        except RuntimeError as exc:
            return ("ERR", str(exc)[:40], [j["strategie"] for j in journal])

    for case in ["js_ok", "js_noop_mouse_ok", "new_tab", "nothing"]:
        print(case, run(case))
''' % {"root": str(ROOT)})


def _run():
    out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return dict(line.split(" ", 1) for line in out.stdout.strip().splitlines())


def test_navigation_strategies_are_verified():
    res = _run()
    assert res["js_ok"].startswith("('OK', True, 'https://x/decisions/marketing', ['clic_js_tuile'])")
    # le clic JS « réussit » mais n'ouvre rien : on bascule sur le vrai clic souris
    assert res["js_noop_mouse_ok"].startswith("('OK', True,") and "clic_souris_tuile" in res["js_noop_mouse_ok"]
    # tuile ouvrant un nouvel onglet : la page de travail est le nouvel onglet
    assert res["new_tab"].startswith("('OK', False,")
    # aucune stratégie n'aboutit : erreur explicite, jamais de faux succès
    assert res["nothing"].startswith("('ERR'")
