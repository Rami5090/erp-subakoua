"""Accès en lecture aux données de l'ERP, partagé par les pages.

Chaque page réécrivait jusqu'ici ses propres helpers (`_decode`, `_rows`) :
ce module les regroupe pour que toutes lisent la base de la même façon.

* ``decoder_json`` : un champ JSON MySQL (dict, texte, octets, JSON
  doublement encodé) devient un dictionnaire ;
* ``lire_lignes`` : une requête qui échoue (table absente sur une base
  fraîche, droits insuffisants) renvoie une liste vide **et** son erreur,
  au lieu de faire planter la page ;
* ``signature_*`` : empreintes légères (sans les payloads) servant de clé de
  cache. Elles incluent la date de synchronisation (``fetched_at`` /
  ``date_maj``) : une étude resynchronisée avec un contenu modifié invalide
  donc le cache, ce que l'identifiant seul ne permettait pas (l'upsert
  conserve l'identifiant de la ligne).

Aucune dépendance Streamlit : le module se teste avec une base SQLite.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sqlalchemy import text

#: Tables de saisie manuelle lues par le rapport (indexées par Tour_ID).
TABLES_SAISIE = (
    "Decisions_Marketing", "Ventes_Historique", "Parc_Machines_Mensuel",
    "Decisions_RH_Mensuel", "Finances_Mensuelles", "Historique_Equipe",
)


def decoder_json(value: Any) -> dict:
    """Dictionnaire décodé d'un champ JSON ; ``{}`` si illisible."""
    for _ in range(3):
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return {}
        else:
            break
    return value if isinstance(value, dict) else {}


def lire_lignes(engine: Any, sql: str, params: Mapping[str, Any] | None = None) -> tuple[list[dict], str | None]:
    """Lignes d'une requête et message d'erreur éventuel (jamais d'exception)."""
    if engine is None:
        return [], "base indisponible"
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(sql), dict(params or {})).mappings().all()], None
    except Exception as exc:  # table absente, droits, réseau…
        return [], f"{type(exc).__name__}"


def _premiere_requete(engine: Any, requetes: Sequence[str]) -> list[dict]:
    for sql in requetes:
        lignes, erreur = lire_lignes(engine, sql)
        if erreur is None:
            return lignes
    return []


def signature_etudes(engine: Any) -> tuple:
    """Empreinte de `erp_etudes` : identifiants, lisibilité et date de synchronisation."""
    lignes = _premiere_requete(engine, (
        "SELECT id, periode, study_id, read_allowed, fetched_at FROM erp_etudes ORDER BY id",
        "SELECT id, periode, study_id, read_allowed FROM erp_etudes ORDER BY id",
    ))
    return tuple(tuple(str(v) for v in ligne.values()) for ligne in lignes)


def signature_donnees(engine: Any) -> tuple:
    """Empreinte de `erp_donnees` : identifiants, période, module et date de mise à jour."""
    lignes = _premiere_requete(engine, (
        "SELECT id, periode, module, date_maj FROM erp_donnees ORDER BY id",
        "SELECT id, periode, module FROM erp_donnees ORDER BY id",
    ))
    return tuple(tuple(str(v) for v in ligne.values()) for ligne in lignes)


def charger_sources_rapport(engine: Any, tables: Sequence[str] = TABLES_SAISIE) -> dict[str, Any]:
    """Tout ce que le rapport lit, en une passe (une requête par table).

    Retour : ``{"erp_donnees": [...], "erp_etudes": [...], "tables": {nom: [...]},
    "erreurs": {source: message}}``. Une table absente n'empêche pas les autres.
    """
    erreurs: dict[str, str] = {}
    donnees, err = lire_lignes(
        engine, "SELECT id, periode, module, contenu FROM erp_donnees WHERE type_donnee = 'etat_actuel'")
    if err:
        erreurs["erp_donnees"] = err
    etudes, err = lire_lignes(
        engine, "SELECT id, periode, study_id, read_allowed, payload FROM erp_etudes WHERE read_allowed = 1")
    if err:
        erreurs["erp_etudes"] = err
    lues: dict[str, list[dict]] = {}
    for nom in tables:
        lignes, err = lire_lignes(engine, f"SELECT * FROM {nom}")
        if err:
            erreurs[nom] = err
        lues[nom] = lignes
    return {"erp_donnees": donnees, "erp_etudes": etudes, "tables": lues, "erreurs": erreurs}
