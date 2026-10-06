"""Indicateurs stratégiques non causaux : position, cible, alertes."""
from __future__ import annotations
from typing import Any, Mapping
import pandas as pd


def current_competitive_metrics(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(snapshot.get("rows", []))
    own = snapshot.get("own") or {}
    prices = [r["prix"] for r in rows if r.get("prix") is not None]
    qualities = [r["qualite"] for r in rows if r.get("qualite") is not None]
    return {
        "own_share": snapshot.get("share"),
        "own_price": own.get("prix") or None,
        "median_competitor_price": float(pd.Series(prices).median()) if prices else None,
        "own_quality": own.get("qualite") or None,
        "median_competitor_quality": float(pd.Series(qualities).median()) if qualities else None,
        "competitor_count": max(0, len(rows) - (1 if snapshot.get("own") else 0)),
        "market_total": snapshot.get("total_market"),
    }


def strategic_alerts(baseline_share: list[float], target_share: float, current_share: float | None) -> list[str]:
    alerts=[]
    if current_share is not None and target_share > current_share + 1e-9:
        alerts.append("La cible de part de marché est supérieure à la position actuelle.")
    if baseline_share and baseline_share[-1] < target_share:
        alerts.append("Le forecast de base n'atteint pas la cible : un levier commercial et/ou industriel devra être calibré.")
    return alerts
