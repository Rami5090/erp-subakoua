"""Schéma des tables locales de l'ERP Subakoua et migrations de colonnes.

`CREATE TABLE IF NOT EXISTS` ne crée pas les colonnes ajoutées après la
création initiale de la table : une base existante garde donc l'ancien
schéma. `ajouter_colonnes_manquantes` comble cet écart sans perte de
données, et reste compatible MySQL (Aiven) comme SQLite (banc d'essai).
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from sqlalchemy import text

#: Colonnes ajoutées à `Parc_Machines_Mensuel` pour tracer les cessions.
COLONNES_CESSION_PARC = {
    "Cession_Faite": "INT",
    "Ced_Decoupe": "INT",
    "Ced_Assemblage": "INT",
    "Ced_Cond": "INT",
    "Px_Cession_Decoupe": "FLOAT",
    "Px_Cession_Assemblage": "FLOAT",
    "Px_Cession_Cond": "FLOAT",
}

#: Colonnes ajoutées à `Finances_Mensuelles` pour l'impact comptable d'une cession.
COLONNES_CESSION_FINANCES = {
    "VNC_Cession_HT": "FLOAT",
    "TVA_Cession": "FLOAT",
    "PMV_Cession": "FLOAT",
}


def colonnes_existantes(conn: Any, table: str) -> set[str]:
    """Noms des colonnes déjà présentes dans `table`.

    MySQL expose `information_schema`, SQLite expose `PRAGMA table_info` :
    les deux sont essayés, le premier qui répond l'emporte.
    """
    for sql, index, params in (
        ("SELECT column_name FROM information_schema.columns WHERE table_name = :t", 0, {"t": table}),
        (f"PRAGMA table_info({table})", 1, {}),
    ):
        try:
            rows = conn.execute(text(sql), params).fetchall()
        except Exception:
            continue
        colonnes = {str(row[index]).lower() for row in rows}
        if colonnes:
            return colonnes
    return set()


def borner_attente_verrou(conn: Any, secondes: int = 5) -> None:
    """MySQL : borne l'attente d'un verrou de métadonnées (DDL).

    `lock_wait_timeout` vaut **une heure** par défaut : un `ALTER TABLE` dont
    le verrou est détenu par une autre session figerait l'application. On le
    ramène à quelques secondes : le DDL échoue vite et l'application reste
    utilisable.
    """
    for sql in (f"SET SESSION lock_wait_timeout = {int(max(1, secondes))}",):
        try:
            conn.execute(text(sql))
        except Exception:
            pass  # SQLite ou moteur incompatible : sans objet


def ajouter_colonnes_manquantes(
    conn: Any,
    table: str,
    colonnes: Mapping[str, str],
    budget_s: float = 10.0,
) -> list[str]:
    """Ajoute à `table` les colonnes absentes. Retourne celles qui ont été ajoutées.

    L'opération est idempotente : appeler deux fois la fonction n'ajoute
    rien la seconde fois. Une colonne qui ne peut pas être ajoutée est
    ignorée — l'appelant reste responsable de vérifier sa présence.

    L'opération est **bornée dans le temps** (`budget_s`) et n'attend jamais
    un verrou plus de quelques secondes : une base verrouillée par une autre
    session ne doit pas figer l'application.
    """
    deadline = time.monotonic() + max(0.0, float(budget_s))
    borner_attente_verrou(conn)
    existantes = colonnes_existantes(conn, table)
    ajoutees: list[str] = []
    for nom, type_sql in colonnes.items():
        if nom.lower() in existantes:
            continue
        if time.monotonic() > deadline:
            break
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {nom} {type_sql}"))
            ajoutees.append(nom)
        except Exception:
            continue
    return ajoutees
