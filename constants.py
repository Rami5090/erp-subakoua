"""Constantes centralisées pour l'ERP Subakoua.

Ce module regroupe toutes les constantes partagées pour éviter la duplication
entre optimizer_engine.py, erp_engine.py, forecast_engine.py, etc.
"""
from __future__ import annotations

import re
from typing import Dict

# Produits Subakoua
PRODUCTS = ["Shorty 3", "Integral 3", "Shorty 5", "Integral 5", "Integral 7"]

# Mapping ID API -> Nom produit
PRODUCT_ID_TO_NAME = {
    "SHORTY_C": "Shorty 3",
    "MONO_C": "Integral 3",
    "SHORTY_T": "Shorty 5",
    "MONO_T": "Integral 5",
    "MONO_F": "Integral 7",
}

# Mapping nom court -> nom complet
PRODUCT_KEYS = ("s3", "i3", "s5", "i5", "i7")
PRODUCT_KEY_TO_NAME = {
    "s3": "Shorty 3",
    "i3": "Integral 3",
    "s5": "Shorty 5",
    "i5": "Integral 5",
    "i7": "Integral 7",
}

# Temps d'atelier par produit (minutes)
DEFAULT_TIMES: Dict[str, Dict[str, float]] = {
    "Shorty 3": {"Decoupe": 2.0, "Assemblage": 4.0, "Conditionnement": 1.0},
    "Integral 3": {"Decoupe": 3.0, "Assemblage": 6.0, "Conditionnement": 1.0},
    "Shorty 5": {"Decoupe": 2.5, "Assemblage": 5.0, "Conditionnement": 1.0},
    "Integral 5": {"Decoupe": 3.5, "Assemblage": 7.0, "Conditionnement": 1.0},
    "Integral 7": {"Decoupe": 4.0, "Assemblage": 8.0, "Conditionnement": 1.5},
}

# Nomenclature par produit (kg par unité)
DEFAULT_NOMENCLATURE: Dict[str, Dict[str, float]] = {
    "Shorty 3": {"Néoprène 3": 1.1, "Renforts": 12.0, "Manchons": 2.0, "Fermetures": 1.0},
    "Integral 3": {"Néoprène 3": 1.6, "Renforts": 18.0, "Manchons": 4.0, "Fermetures": 1.0},
    "Shorty 5": {"Néoprène 5": 1.1, "Renforts": 12.0, "Manchons": 2.0, "Fermetures": 1.0},
    "Integral 5": {"Néoprène 5": 1.6, "Renforts": 18.0, "Manchons": 4.0, "Fermetures": 1.0},
    "Integral 7": {"Néoprène 7": 1.6, "Renforts": 22.0, "Manchons": 4.0, "Fermetures": 1.0},
}

# Capacité par machine (minutes/mois)
CAPACITY_MIN_PER_MACHINE = 9600.0

# Groupes de produits (épaisseur Néoprène)
PRODUCT_GROUP = {"Shorty 3": 3, "Integral 3": 3, "Shorty 5": 5, "Integral 5": 5, "Integral 7": 7}

# Mois
MONTH_NAMES = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]
MONTH_BY_NAME = {x.lower(): i + 1 for i, x in enumerate(MONTH_NAMES)}

# Index période (Année 1 - Janvier = 1)
PERIOD_INDEX = {f"Année {y} - {m}": (y - 1) * 12 + i for y in range(1, 5) for i, m in enumerate(MONTH_NAMES)}

# Tours -> Périodes
TOURS_PERIODES = {
    1: "Année 1 - Janvier", 2: "Année 1 - Février", 3: "Année 1 - Mars",
    4: "Année 1 - Avril", 5: "Année 1 - Mai", 6: "Année 1 - Juin",
    7: "Année 1 - Juillet", 8: "Année 1 - Août", 9: "Année 1 - Septembre",
    10: "Année 1 - Octobre", 11: "Année 1 - Novembre", 12: "Année 1 - Décembre",
    13: "Année 2 - Janvier", 14: "Année 2 - Février", 15: "Année 2 - Mars",
}

# Constantes financières
TVA_STANDARD = 0.20
ACOMPTE_CREDIT_FOURNISSEUR = 0.30
DUREE_AMORTISSEMENT_MOIS = 60
TAUX_RENDEMENT_EPARGNE_ANNUEL = 0.012
NOMBRE_PARTS = 40000
TAUX_CHARGES_SOCIALES = 0.50

# Volumes par défaut (pour initialisation)
VOLUMES_PAR_DEFAUT = {"s3": 50, "i3": 500, "s5": 100, "i5": 1500, "i7": 2500}

# Patterns de détection produit (pour le parseur de décisions)
PRODUCT_PATTERNS = [
    ("s3", r"\bshorty\s*(3|c)\b|\bshorty_c\b|\bs\s*3\b"),
    ("i3", r"\bintegral\s*(3|c)\b|\bmono_c\b|\bi\s*3\b"),
    ("s5", r"\bshorty\s*(5|t)\b|\bshorty_t\b|\bs\s*5\b"),
    ("i5", r"\bintegral\s*(5|t)\b|\bmono_t\b|\bi\s*5\b"),
    ("i7", r"\bintegral\s*(7|f)\b|\bmono_f\b|\bi\s*7\b"),
]


def period_to_seq(period: str) -> int:
    """Convertit 'Année 1 - Janvier' -> séquence 1-based."""
    import re
    m = re.fullmatch(r"Année\s+(\d+)\s*-\s*(.+)", str(period).strip(), re.I)
    if not m:
        raise ValueError(f"Période invalide: {period!r}")
    month = MONTH_BY_NAME.get(m.group(2).strip().lower())
    if not month:
        raise ValueError(f"Mois inconnu: {period!r}")
    return (int(m.group(1)) - 1) * 12 + month


def period_label_to_code(period: str) -> str:
    """Convertit 'Année 1 - Janvier' -> '0101'."""
    seq = period_to_seq(period)
    year = (seq - 1) // 12 + 1
    month = (seq - 1) % 12 + 1
    return f"{year:02d}{month:02d}"


def period_code_to_label(code: str) -> str:
    """Convertit '0101' -> 'Année 1 - Janvier'."""
    code = str(code).strip()
    if not re.fullmatch(r"\d{4}", code):
        raise ValueError(f"Code période invalide: {code!r}")
    year = int(code[:2])
    month = int(code[2:])
    if year < 1 or month not in range(1, 13):
        raise ValueError(f"Code période invalide: {code!r}")
    return f"Année {year} - {MONTH_NAMES[month - 1]}"


def period_month_id(period: str) -> int:
    """IDs `/months/{month}` observés sur le portail : A1-Janvier = 13."""
    return 12 + period_to_seq(period)


def next_period(period: str, offset: int = 1) -> str:
    """Période suivante/précédente."""
    seq = period_to_seq(period) + offset
    if seq < 1:
        raise ValueError("La période résultante est antérieure à Année 1 - Janvier")
    year = (seq - 1) // 12 + 1
    month = (seq - 1) % 12 + 1
    return f"Année {year} - {MONTH_NAMES[month - 1]}"


def try_next_period(period: str, offset: int = 1) -> str | None:
    """Version non bloquante de next_period."""
    try:
        return next_period(period, offset)
    except ValueError:
        return None


def parse_french_float(val) -> float:
    """Parse un nombre au format français (espaces, virgules, €)."""
    if isinstance(val, (int, float)):
        return float(val)
    try:
        clean_str = str(val).replace(" ", "").replace("\u202f", "").replace("\xa0", "").replace("€", "").replace(",", ".").strip()
        if clean_str == "" or clean_str == "-":
            return 0.0
        return float(clean_str)
    except Exception:
        return 0.0