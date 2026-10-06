"""Matrice explicite document -> information -> décision pour Subakoua.

Les intitulés/prix/IDs proviennent du catalogue audité. La classification
sémantique (domaines, leviers, horizons, priorité) est une couche analytique
transparente et modifiable : elle n'est pas présentée comme une règle native
de Subakoua.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "subakoua_study_catalog.csv"
ENDPOINT_CATALOG_PATH = BASE_DIR / "study_api_catalog.json"
MATRIX_CSV_PATH = BASE_DIR / "study_information_matrix.csv"
MATRIX_JSON_PATH = BASE_DIR / "study_information_matrix.json"


def _norm(text: Any) -> str:
    s = str(text or "").lower().strip()
    s = s.replace("’", "'")
    return re.sub(r"\s+", " ", s)


def _contains(label: str, *terms: str) -> bool:
    return any(t in label for t in terms)


def _classify(label: str, study_id: str, endpoint: Mapping[str, Any] | None, price: float) -> dict[str, Any]:
    """Classification prudente, fondée sur le libellé et les clés API connues."""
    l = _norm(label)
    keys = set(str(x) for x in (endpoint or {}).get("response_keys", []))

    # Domaine principal
    if study_id.startswith("pevc"):
        domain = "concurrence"
    elif study_id.startswith("peeum") or "vente" in l or "publicit" in l or "prix" in l or "canaux de distribution" in l or "délais de paiement" in l:
        domain = "marketing"
    elif study_id.startswith("enpr") or study_id.startswith("pefrim") or "production" in l or "machines" in l or "ateliers" in l:
        domain = "production"
    elif study_id.startswith("enap") or study_id.startswith("pefrmp") or "matières" in l or "fournisseur" in l or "commandes fournisseurs" in l:
        domain = "approvisionnement"
    elif study_id.startswith("peass"):
        domain = "assurance"
    elif "ressources humaines" in l or "charges de personnel" in l or "personnel" in l or "service / administration-finance" in l or "service / approvisionnement" in l or "service / production" in l:
        domain = "rh"
    elif study_id.startswith(("pee", "ifrs", "pcgToIfrs", "peba")) or _contains(l, "trésorerie", "bilan", "résultat", "impôt", "tva", "emprunt", "actions", "obligations", "charges financières", "chiffre d'affaires", "créances"):
        domain = "finance"
    elif "marché potentiel" in l or "prévision" in l or "satisfaction clients" in l:
        domain = "etudes_marche"
    else:
        domain = "pilotage"

    # Tags métier
    tags: set[str] = set()
    if _contains(l, "part de marché", "ventes", "chiffre d'affaires", "cartographie des chiffres d'affaires") or "marketShares" in keys:
        tags |= {"sales", "market_share"}
    if _contains(l, "prévision", "marché potentiel") or "listCoefSaisonnier" in keys:
        tags |= {"forecast", "seasonality", "market_potential"}
    if _contains(l, "prix") or "price" in " ".join(keys).lower():
        tags.add("price")
    if _contains(l, "publicit") or "advert" in " ".join(keys).lower():
        tags.add("advertising")
    if _contains(l, "qualité") or "quality" in " ".join(keys).lower():
        tags.add("quality")
    if _contains(l, "production", "processus", "coût des entrées", "coûts de production") or "production" in " ".join(keys).lower():
        tags |= {"production", "cost"}
    if _contains(l, "matières", "stocks", "approvisionnement", "commandes fournisseurs") or "material" in " ".join(keys).lower():
        tags |= {"material", "stock"}
    if _contains(l, "atelier", "machines", "immobilisation", "investissement") or "workstation" in " ".join(keys).lower():
        tags |= {"capacity", "investment"}
    if _contains(l, "fournisseur", "délais de paiement") or "provider" in " ".join(keys).lower():
        tags |= {"supplier", "payment"}
    if _contains(l, "trésorerie", "banque", "bilan", "créances", "charges financières", "emprunt", "compte épargne") or any(x in keys for x in ("initialTreasury", "finalTreasury", "bankLoans", "monthlyLoanRefund")):
        tags |= {"cash", "finance"}
    if _contains(l, "bfr", "créances", "stocks", "fournisseurs", "délais de paiement") or any(x in keys for x in ("variationBfr", "fluxClients", "fluxFournisseurs")):
        tags.add("bfr")
    if _contains(l, "tva") or "vat" in " ".join(keys).lower():
        tags.add("vat")
    if _contains(l, "impôt", "résultats comptables") or "incomeTaxExpense" in keys or "corporationTaxes" in " ".join(keys):
        tags.add("tax")
    if _contains(l, "personnel", "rh", "salaires", "administration-finance", "approvisionnement", "production") and domain == "rh":
        tags |= {"hr", "payroll", "staff_quality"}

    if not tags:
        tags.add(domain)

    # Leviers principaux
    lever_map = []
    for tag, lever in [
        ("market_share", "part_de_marche"), ("forecast", "prevision"), ("price", "prix"),
        ("advertising", "marketing"), ("quality", "qualite"), ("production", "production"),
        ("material", "achats"), ("capacity", "capacite"), ("investment", "investissement"),
        ("supplier", "fournisseurs"), ("cash", "tresorerie"), ("bfr", "bfr"),
        ("vat", "tva"), ("tax", "fiscalite"), ("hr", "rh"), ("payroll", "masse_salariale"),
        ("cost", "couts"), ("stock", "stocks"), ("payment", "delais_paiement"),
    ]:
        if tag in tags:
            lever_map.append(lever)
    if not lever_map:
        lever_map = [domain]

    # Horizons : structure = long/moyen ; mesures mensuelles = court/moyen
    recurring = "mensuel"
    horizons = {"court", "moyen"}
    if _contains(l, "prévision", "marché potentiel", "fiches", "tarifs", "profil des fournisseurs", "conditions de ventes", "processus de production"):
        recurring = "structurel"
        horizons = {"moyen", "long"}
    if _contains(l, "évolution comparée", "résultats", "stocks", "ventes mensuelles", "banque", "tva", "bilan", "compte de résultat", "trésorerie"):
        recurring = "mensuel"
        horizons = {"court", "moyen"}

    # Importance stratégique : priorité conceptuelle avant prise en compte du coût.
    importance = 50.0
    if tags & {"market_share", "forecast"}:
        importance += 25
    if tags & {"capacity", "production", "material"}:
        importance += 15
    if tags & {"cash", "bfr"}:
        importance += 15
    if "quality" in tags or "supplier" in tags:
        importance += 5
    importance = min(100.0, importance)

    if price <= 0:
        purchase_class = "gratuit"
    elif price <= 100:
        purchase_class = "tactique_50_100"
    elif price <= 200:
        purchase_class = "analytique_200"
    elif price <= 250:
        purchase_class = "strategique_250"
    else:
        purchase_class = "investissement_informationnel_5000"

    # Les 5 000 € sont une proposition exceptionnelle, jamais une recommandation automatique.
    strategic_only = price >= 5000

    return {
        "domain": domain,
        "tags": sorted(tags),
        "decision_levers": lever_map,
        "horizons": sorted(horizons),
        "importance": round(importance, 2),
        "recurring": recurring,
        "purchase_class": purchase_class,
        "strategic_only": strategic_only,
        "mapping_basis": "api_keys+label" if endpoint else "label_only",
        "mapping_confidence": 0.95 if endpoint else 0.75,
    }


def build_matrix(catalog_path: Path = CATALOG_PATH, endpoint_path: Path = ENDPOINT_CATALOG_PATH) -> list[dict[str, Any]]:
    endpoint_data = json.loads(endpoint_path.read_text(encoding="utf-8")) if endpoint_path.exists() else {"known_endpoints": {}}
    endpoints = endpoint_data.get("known_endpoints", {}) or {}
    rows: list[dict[str, Any]] = []
    with catalog_path.open(encoding="utf-8-sig", newline="") as f:
        for src in csv.DictReader(f):
            sid = str(src["study_id"])
            label = str(src["label"])
            price = float(src.get("price") or 0)
            meta = _classify(label, sid, endpoints.get(sid), price)
            endpoint_path = str((endpoints.get(sid) or {}).get("path") or "")
            response_keys = list((endpoints.get(sid) or {}).get("response_keys") or [])
            rows.append({
                "study_id": sid,
                "label": label,
                "price": price,
                "endpoint_known": bool(endpoint_path),
                "endpoint_path": endpoint_path,
                "response_keys": ", ".join(response_keys),
                **meta,
            })
    return rows


def write_matrix() -> tuple[Path, Path]:
    rows = build_matrix()
    fields = list(rows[0].keys()) if rows else []
    with MATRIX_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    MATRIX_JSON_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return MATRIX_CSV_PATH, MATRIX_JSON_PATH


def matrix_by_study_id() -> dict[str, dict[str, Any]]:
    rows = build_matrix()
    return {r["study_id"]: r for r in rows}


if __name__ == "__main__":
    c, j = write_matrix()
    print(f"Matrice générée : {c.name} / {j.name} ({len(build_matrix())} études)")
