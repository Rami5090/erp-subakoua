"""Accès base de données : moteur lecture seule optionnel pour les visiteurs.

Défense en profondeur : si une section ``[mysql_readonly]`` est présente dans les secrets Streamlit
(utilisateur MySQL avec uniquement le privilège SELECT), les pages de consultation l'utilisent.
Même en cas de faille applicative, un visiteur ne pourrait alors pas écrire en base.
"""
from __future__ import annotations

import os

import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.engine import URL


def _secret(section: str, key: str, default: str = "") -> str:
    try:
        if section in st.secrets and key in st.secrets[section]:
            return str(st.secrets[section][key])
    except Exception:
        pass
    return default


def _build(section: str, env_fallback: bool):
    host = _secret(section, "host") or (os.getenv("DB_HOST", "") if env_fallback else "")
    user = _secret(section, "user") or _secret(section, "username") or (os.getenv("DB_USER", "") if env_fallback else "")
    password = _secret(section, "password") or (os.getenv("DB_PASSWORD", "") if env_fallback else "")
    database = _secret(section, "database") or (os.getenv("DB_NAME", "") if env_fallback else "")
    port = _secret(section, "port") or (os.getenv("DB_PORT", "") if env_fallback else "")
    if not all([host, user, password, database, port]):
        return None
    url = URL.create("mysql+pymysql", username=user, password=password, host=host, port=int(port), database=database)
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, connect_args={"ssl": {}})


@st.cache_resource
def get_write_engine():
    return _build("mysql", env_fallback=True)


@st.cache_resource
def get_readonly_engine_if_configured():
    """Moteur [mysql_readonly] ou None s'il n'est pas configuré."""
    return _build("mysql_readonly", env_fallback=False)


def get_read_engine():
    """Moteur pour les pages de consultation : lecture seule si disponible, sinon moteur standard."""
    return get_readonly_engine_if_configured() or get_write_engine()
