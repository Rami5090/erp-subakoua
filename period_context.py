"""Résolution de la période de référence (M-1) du simulateur stratégique.

Module volontairement sans dépendance Streamlit / SQL afin d'être testé seul.

Problèmes corrigés (v2.9.3)
---------------------------
1. Les valeurs par défaut des champs du simulateur étaient mémorisées dans
   ``st.session_state`` sans être rattachées à une période : changer de mois
   conservait les réglages du mois précédent au lieu de repartir des données M-1
   enregistrées en base. ``context_signature`` permet de détecter ce changement.
2. Une ligne M-1 absente (ex. ``Decisions_RH_Mensuel`` non saisie pour ce tour)
   faisait retomber silencieusement sur des constantes. ``pick_reference_row``
   remonte au dernier tour disponible <= M-1 et indique le tour réellement utilisé.
3. Le libellé de période était comparé en égalité stricte : une différence
   d'espace, d'accent, de tiret (« - » / « – ») ou de casse rendait M-1 invisible
   et tout le pont financier retombait à 0. ``normalize_period`` / ``match_period``
   tolèrent ces variantes.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

import pandas as pd

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")


def normalize_period(label: Any) -> str:
    """Forme canonique d'un libellé de période : « annee 1 - aout »."""
    if label is None:
        return ""
    text = unicodedata.normalize("NFKC", str(label)).translate(_DASHES)
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def match_period(candidates: Iterable[tuple[Any, Any]], wanted: str) -> Optional[Any]:
    """Retourne l'identifiant de la ligne dont la période correspond à ``wanted``.

    ``candidates`` : couples ``(identifiant, libellé_période)`` classés du plus
    ancien au plus récent ; en cas de doublon on garde le plus récent (dernier).
    La correspondance exacte est prioritaire sur la correspondance normalisée.
    """
    exact = None
    fuzzy = None
    target = normalize_period(wanted)
    for ident, label in candidates:
        if label == wanted:
            exact = ident
        elif normalize_period(label) == target:
            fuzzy = ident
    return exact if exact is not None else fuzzy


@dataclass(frozen=True)
class ReferenceRow:
    """Ligne de référence résolue pour un tour donné."""
    data: dict
    tour_demande: int
    tour_utilise: Optional[int]

    @property
    def found(self) -> bool:
        return self.tour_utilise is not None

    @property
    def is_fallback(self) -> bool:
        return self.found and self.tour_utilise != self.tour_demande


def pick_reference_row(df: Optional[pd.DataFrame], tour_demande: int, tour_col: str = "Tour_ID") -> ReferenceRow:
    """Ligne du tour demandé, sinon du dernier tour antérieur disponible.

    Ne regarde jamais vers le futur : un tour > ``tour_demande`` est ignoré.
    """
    tour_demande = int(tour_demande)
    if df is None or df.empty or tour_col not in df.columns:
        return ReferenceRow({}, tour_demande, None)
    tours = pd.to_numeric(df[tour_col], errors="coerce")
    eligible = df[tours <= tour_demande].copy()
    if eligible.empty:
        return ReferenceRow({}, tour_demande, None)
    eligible["_t"] = pd.to_numeric(eligible[tour_col], errors="coerce")
    best = eligible.sort_values("_t").iloc[-1]
    data = best.drop(labels=["_t"]).to_dict()
    return ReferenceRow(data, tour_demande, int(best["_t"]))


def _clean(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def ref_number(row: Mapping[str, Any] | None, key: str, default: float) -> float:
    """Lit un nombre dans la ligne de référence ; ``default`` si absent / NULL / illisible."""
    if not row:
        return float(default)
    value = _clean(row.get(key))
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def ref_int(row: Mapping[str, Any] | None, key: str, default: int) -> int:
    return int(round(ref_number(row, key, float(default))))


def context_signature(tour_actif: int, *parts: Any) -> str:
    """Empreinte du contexte de simulation (tour + données de référence chargées).

    Quand elle change (autre mois sélectionné, ou données M-1 modifiées en base),
    les décisions mémorisées de l'écran doivent être purgées pour repartir de M-1.
    """
    payload = json.dumps([int(tour_actif), *parts], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def stale_keys(state_keys: Iterable[str], prefixes: Sequence[str]) -> list[str]:
    """Clés de ``session_state`` appartenant au simulateur (à purger)."""
    prefixes = tuple(prefixes)
    return [k for k in state_keys if isinstance(k, str) and k.startswith(prefixes)]


@dataclass
class ReferenceDiagnostic:
    """Cumul de messages de traçabilité affichés en haut du simulateur."""
    notes: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def alert(self, msg: str) -> None:
        self.alerts.append(msg)


def describe_row(table: str, ref: ReferenceRow, diag: ReferenceDiagnostic, label_tour) -> None:
    """Alimente le diagnostic selon la qualité de la ligne trouvée."""
    if not ref.found:
        diag.alert(f"`{table}` : aucune donnée pour le tour {ref.tour_demande} ni pour un tour antérieur — valeurs par défaut utilisées.")
    elif ref.is_fallback:
        diag.alert(
            f"`{table}` : tour {ref.tour_demande} ({label_tour(ref.tour_demande)}) absent — "
            f"repli sur le tour {ref.tour_utilise} ({label_tour(ref.tour_utilise)})."
        )
    else:
        diag.note(f"`{table}` : tour {ref.tour_utilise} ({label_tour(ref.tour_utilise)}).")


def merge_reference(ref: ReferenceRow, parsed: Mapping[str, Any] | None, tour_demande: int) -> tuple[ReferenceRow, bool]:
    """Complète une ligne de référence SQL par des valeurs issues du scraper.

    - une ligne SQL EXACTE pour le tour demandé (saisie manuelle) reste prioritaire ;
    - sinon (absente ou repli sur un tour plus ancien), les valeurs scrapées du tour demandé
      remplacent celles du repli, champ par champ.
    Retourne (ligne, scraper_utilise).
    """
    if not parsed:
        return ref, False
    if ref.found and not ref.is_fallback:
        return ref, False
    data = {**ref.data, **dict(parsed)}
    return ReferenceRow(data, int(tour_demande), int(tour_demande)), True
