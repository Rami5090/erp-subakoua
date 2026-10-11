"""Rôles Visiteur / Utilisateur pour l'ERP Subakoua (Streamlit).

- Visiteur (par défaut) : lecture seule. Aucune écriture en base, aucun achat d'étude,
  aucun scraping, aucune sauvegarde de scénario.
- Utilisateur : après saisie du mot de passe configuré, accès complet.

Le contrôle est appliqué CÔTÉ SERVEUR (``require_editor`` / ``is_editor`` aux points d'écriture) ;
masquer des boutons ne sert que de confort visuel. Le rôle n'est jamais lu depuis l'URL.

Configuration (Streamlit Cloud → Settings → Secrets) :

    [auth]
    user_password = "un-mot-de-passe-solide"
    # ou, mieux : user_password_sha256 = "<hash SHA-256 hexadécimal>"

Variables d'environnement équivalentes : ERP_USER_PASSWORD / ERP_USER_PASSWORD_SHA256.
"""
from __future__ import annotations

import os
import time

import streamlit as st

from auth_core import (
    ROLE_USER,
    ROLE_VISITOR,
    is_configured,
    lock_remaining,
    register_failure,
    register_success,
    session_expired,
    verify_password,
)

_ROLE_KEY = "_erp_role"
_LOGIN_TS_KEY = "_erp_login_ts"
_THROTTLE_KEY = "_erp_login_throttle"

# Pages réservées aux utilisateurs : masquées (confort visuel) dans le menu pour les visiteurs.
EDITOR_ONLY_PAGE_HINTS = ("Scraper",)


def _secret_auth(key: str, env: str) -> str:
    try:
        if "auth" in st.secrets and key in st.secrets["auth"]:
            return str(st.secrets["auth"][key])
    except Exception:
        pass
    return os.getenv(env, "")


def _configured_secrets() -> tuple[str, str]:
    return (
        _secret_auth("user_password", "ERP_USER_PASSWORD"),
        _secret_auth("user_password_sha256", "ERP_USER_PASSWORD_SHA256"),
    )


def auth_configured() -> bool:
    plain, digest = _configured_secrets()
    return is_configured(plain, digest)


def current_role() -> str:
    """Rôle courant ; une session utilisateur expirée retombe en visiteur."""
    role = st.session_state.get(_ROLE_KEY, ROLE_VISITOR)
    if role == ROLE_USER and session_expired(st.session_state.get(_LOGIN_TS_KEY), time.time()):
        logout()
        return ROLE_VISITOR
    return ROLE_USER if role == ROLE_USER else ROLE_VISITOR


def is_editor() -> bool:
    return current_role() == ROLE_USER


def login(password: str) -> tuple[bool, str]:
    throttle = st.session_state.setdefault(_THROTTLE_KEY, {})
    now = time.time()
    wait = lock_remaining(throttle, now)
    if wait:
        return False, f"Trop de tentatives. Réessayez dans {wait} s."
    plain, digest = _configured_secrets()
    if not is_configured(plain, digest):
        return False, "Aucun mot de passe utilisateur n'est configuré : le mode lecture seule reste actif."
    if verify_password(password, plain, digest):
        register_success(throttle)
        st.session_state[_ROLE_KEY] = ROLE_USER
        st.session_state[_LOGIN_TS_KEY] = now
        return True, "Connecté en tant qu'utilisateur."
    locked = register_failure(throttle, now)
    if locked:
        return False, f"Mot de passe incorrect. Connexion verrouillée {locked} s."
    return False, "Mot de passe incorrect."


def logout() -> None:
    st.session_state[_ROLE_KEY] = ROLE_VISITOR
    st.session_state.pop(_LOGIN_TS_KEY, None)


def _apply_role_css(editor: bool) -> None:
    st.markdown(
        """
<style>
  .role-badge { display:inline-block; padding:.28rem .7rem; border-radius:999px; font-size:.8rem; font-weight:600; }
  .role-visitor { background:rgba(100,116,139,.15); color:#475569; border:1px solid rgba(100,116,139,.35); }
  .role-user { background:rgba(22,163,74,.12); color:#15803d; border:1px solid rgba(22,163,74,.40); }
  .readonly-card { border:1px dashed rgba(100,116,139,.55); border-radius:.8rem; padding:1rem 1.2rem; background:rgba(100,116,139,.06); }
</style>
""",
        unsafe_allow_html=True,
    )
    if not editor:
        # Confort visuel uniquement : la vraie protection est côté serveur (require_editor).
        hide = ", ".join(f'[data-testid="stSidebarNav"] a[href*="{h}"]' for h in EDITOR_ONLY_PAGE_HINTS)
        st.markdown(f"<style>{hide} {{ display:none !important; }}</style>", unsafe_allow_html=True)


def setup(show_login: bool = True) -> str:
    """À appeler UNE fois par page, juste après ``st.set_page_config``. Retourne le rôle."""
    role = current_role()
    editor = role == ROLE_USER
    _apply_role_css(editor)
    with st.sidebar:
        if editor:
            st.markdown('<span class="role-badge role-user">✅ Mode utilisateur</span>', unsafe_allow_html=True)
            st.caption("Accès complet : saisie, sauvegarde, scraper.")
            if st.button("Se déconnecter", key="erp_logout_btn", width="stretch"):
                logout()
                st.rerun()
        else:
            st.markdown('<span class="role-badge role-visitor">👁️ Mode visiteur · lecture seule</span>', unsafe_allow_html=True)
            st.caption("Consultation et simulation locale. Aucune donnée n'est modifiée.")
            if show_login:
                with st.expander("🔐 Connexion utilisateur"):
                    if not auth_configured():
                        st.info("Aucun mot de passe utilisateur configuré (section [auth] des secrets). Le site reste en lecture seule.")
                    with st.form("erp_login_form", clear_on_submit=True):
                        pwd = st.text_input("Mot de passe", type="password")
                        submitted = st.form_submit_button("Se connecter", width="stretch")
                    if submitted:
                        ok, msg = login(pwd)
                        if ok:
                            st.rerun()
                        else:
                            st.error(msg)
    return role


def require_editor(feature: str = "cette fonctionnalité") -> None:
    """Bloque l'exécution du script pour un visiteur (à placer avant toute action sensible)."""
    if is_editor():
        return
    st.title("🔒 Accès réservé aux utilisateurs")
    st.markdown(
        f'<div class="readonly-card">Vous êtes en <b>mode visiteur</b> : {feature} est réservé aux utilisateurs connectés, '
        "car elle peut modifier des données ou engager des dépenses.<br>Connectez-vous via le panneau "
        "<b>🔐 Connexion utilisateur</b> dans la barre latérale.</div>",
        unsafe_allow_html=True,
    )
    st.stop()


def readonly_notice(message: str = "Mode visiteur : consultation uniquement, aucune sauvegarde possible.") -> None:
    st.info(f"👁️ {message}")
