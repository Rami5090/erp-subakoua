"""Logique d'authentification pure (sans Streamlit) : testable et réutilisable.

Principes de sécurité :
- Fail-closed : sans mot de passe configuré, personne ne peut passer en mode « utilisateur ».
- Comparaison à temps constant (hmac.compare_digest).
- Le mot de passe peut être fourni en clair (secrets Streamlit) ou sous forme de hash SHA-256.
- Verrouillage temporaire après plusieurs échecs consécutifs.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import MutableMapping

ROLE_VISITOR = "visiteur"
ROLE_USER = "utilisateur"

MAX_ATTEMPTS = 5
LOCK_SECONDS = 60
SESSION_TTL_SECONDS = 8 * 3600


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_configured(plain: str | None, sha256: str | None) -> bool:
    return bool((plain or "").strip() or (sha256 or "").strip())


def verify_password(candidate: str | None, plain: str | None = None, sha256: str | None = None) -> bool:
    """Vrai uniquement si le candidat correspond au mot de passe configuré."""
    if not candidate or not is_configured(plain, sha256):
        return False
    ok = False
    if (sha256 or "").strip():
        ok = hmac.compare_digest(sha256_hex(candidate), sha256.strip().lower()) or ok
    if (plain or "").strip():
        ok = hmac.compare_digest(candidate.encode("utf-8"), plain.encode("utf-8")) or ok
    return bool(ok)


def lock_remaining(state: MutableMapping, now: float) -> int:
    """Secondes restantes avant la fin du verrouillage (0 si non verrouillé)."""
    until = float(state.get("locked_until", 0.0) or 0.0)
    return max(0, int(round(until - now))) if until > now else 0


def register_failure(state: MutableMapping, now: float, max_attempts: int = MAX_ATTEMPTS, lock_seconds: int = LOCK_SECONDS) -> int:
    """Enregistre un échec ; renvoie la durée de verrouillage déclenchée (0 sinon)."""
    state["failures"] = int(state.get("failures", 0) or 0) + 1
    if state["failures"] >= max_attempts:
        state["failures"] = 0
        state["locked_until"] = now + lock_seconds
        return lock_seconds
    return 0


def register_success(state: MutableMapping) -> None:
    state["failures"] = 0
    state["locked_until"] = 0.0


def session_expired(login_ts: float | None, now: float, ttl: int = SESSION_TTL_SECONDS) -> bool:
    return login_ts is None or (now - float(login_ts)) > ttl
