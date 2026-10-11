"""Tests du contrôle d'accès Visiteur / Utilisateur avec un faux module streamlit."""
import importlib
import sys
import time
import types

import pytest


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStreamlit(types.ModuleType):
    def __init__(self, secrets):
        super().__init__("streamlit")
        self.session_state = {}
        self.secrets = secrets
        self.sidebar = _Ctx()
        self.stopped = False

    def markdown(self, *a, **k): pass
    def caption(self, *a, **k): pass
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def title(self, *a, **k): pass
    def button(self, *a, **k): return False
    def expander(self, *a, **k): return _Ctx()
    def form(self, *a, **k): return _Ctx()
    def text_input(self, *a, **k): return ""
    def form_submit_button(self, *a, **k): return False
    def rerun(self): pass

    def stop(self):
        self.stopped = True
        raise SystemExit


@pytest.fixture()
def auth_env(monkeypatch):
    def make(secrets):
        fake = _FakeStreamlit(secrets)
        monkeypatch.setitem(sys.modules, "streamlit", fake)
        monkeypatch.delenv("ERP_USER_PASSWORD", raising=False)
        monkeypatch.delenv("ERP_USER_PASSWORD_SHA256", raising=False)
        sys.modules.pop("auth", None)
        return fake, importlib.import_module("auth")
    yield make
    sys.modules.pop("auth", None)


def test_default_role_is_visitor_and_write_pages_are_blocked(auth_env):
    st, auth = auth_env({"auth": {"user_password": "pw"}})
    assert auth.current_role() == "visiteur" and not auth.is_editor()
    with pytest.raises(SystemExit):
        auth.require_editor("test")
    assert st.stopped


def test_login_success_grants_editor_and_logout_revokes(auth_env):
    st, auth = auth_env({"auth": {"user_password": "pw"}})
    ok, _ = auth.login("wrong")
    assert not ok and not auth.is_editor()
    ok, _ = auth.login("pw")
    assert ok and auth.is_editor()
    auth.require_editor("test")  # ne lève pas
    auth.logout()
    assert not auth.is_editor()


def test_no_password_configured_is_fail_closed(auth_env):
    st, auth = auth_env({})
    ok, msg = auth.login("anything")
    assert not ok and not auth.is_editor()


def test_lockout_blocks_even_correct_password(auth_env):
    st, auth = auth_env({"auth": {"user_password": "pw"}})
    for _ in range(5):
        auth.login("bad")
    ok, msg = auth.login("pw")
    assert not ok and not auth.is_editor()


def test_editor_session_expires(auth_env):
    st, auth = auth_env({"auth": {"user_password": "pw"}})
    auth.login("pw")
    st.session_state["_erp_login_ts"] = time.time() - 9 * 3600
    assert auth.current_role() == "visiteur"


def test_forged_session_role_without_timestamp_is_rejected(auth_env):
    st, auth = auth_env({"auth": {"user_password": "pw"}})
    st.session_state["_erp_role"] = "utilisateur"  # pas d'horodatage de connexion valide
    assert auth.current_role() == "visiteur"


def test_role_is_never_read_from_url(auth_env):
    import inspect
    _, auth = auth_env({})
    assert "query_params" not in inspect.getsource(auth)
