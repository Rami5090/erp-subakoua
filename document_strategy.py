"""Stratégie de sélection et d'achat des documents Subakoua.

Objectif : maximiser la couverture des informations utiles au pilotage tout en
minimisant le nombre de documents achetés. Le module ne connaît pas la structure
métier interne de l'ERP ; il travaille sur un catalogue de documents découvert
sur le portail.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Any, Iterable


# Besoins de pilotage prioritaires. Les poids servent à l'optimisation de couverture.
PILOTAGE_NEEDS: dict[str, dict[str, Any]] = {
    "tresorerie": {"label": "Trésorerie / banque", "weight": 10},
    "rentabilite": {"label": "Rentabilité / résultat", "weight": 10},
    "bfr_fiscalite": {"label": "BFR / TVA / fiscalité", "weight": 9},
    "ventes_prix": {"label": "Ventes / prix / demande", "weight": 10},
    "concurrence": {"label": "Concurrence / parts de marché", "weight": 8},
    "production_mrp": {"label": "Production / capacité / stocks", "weight": 10},
    "approvisionnement": {"label": "Achats / fournisseurs", "weight": 9},
    "rh": {"label": "RH / masse salariale", "weight": 6},
    "investissement": {"label": "Investissements / machines", "weight": 5},
}

# Informations concrètes recherchées par l'ERP dans les principaux documents.
# Ces libellés servent à expliquer le choix à l'utilisateur ; ils ne prétendent
# pas décrire un champ exact du portail lorsqu'il n'est pas connu.
DOCUMENT_FACTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("banque_assurance", "banque"): (
        "solde bancaire initial et final", "flux de trésorerie", "mensualités d'emprunt",
    ),
    ("expert_comptable", "synthese"): (
        "résultat", "chiffre d'affaires", "rentabilité", "bilan", "flux de trésorerie", "BFR",
    ),
    ("expert_comptable", "detail des comptes"): (
        "CA détaillé", "charges", "créances clients",
    ),
    ("expert_comptable", "tva et impot sur les societes"): (
        "bénéfice imposable", "impôt sur les sociétés",
    ),
    ("donnees_internes", "marketing"): (
        "ventes mensuelles par produit", "qualité",
    ),
    ("donnees_internes", "production"): (
        "production", "capacité", "qualité de production",
    ),
    ("donnees_internes", "stocks"): (
        "stocks produits", "stocks matières", "niveau de stock",
    ),
    ("donnees_internes", "ressources humaines"): (
        "effectifs", "masse salariale",
    ),
    ("production", "les ordres de production"): (
        "coefficients matières", "temps ateliers", "paramètres de qualité",
    ),
    ("production", "les investissements"): (
        "capacité machines", "durée d'amortissement", "tarifs machines",
    ),
    ("fournisseurs", "matieres"): (
        "fiabilité fournisseurs", "délais de paiement",
    ),
    ("fournisseurs", "immobilisations"): (
        "offres et coûts d'immobilisations",
    ),
    ("etudes_marche", "etudes structurelles"): (
        "marché potentiel", "prévisions de ventes",
    ),
    ("etudes_marche", "etudes conjoncturelles"): (
        "conjoncture et demande",
    ),
    ("veille_concurrentielle", "performance commerciale"): (
        "ventes concurrentes", "parts de marché",
    ),
    ("veille_concurrentielle", "evolution marketing"): (
        "prix concurrents", "évolution marketing",
    ),
    ("veille_concurrentielle", "facteurs cles"): (
        "prix", "qualité", "CA des concurrents",
    ),
    ("finance", "emprunts"): (
        "dette", "échéancier", "coût de la dette",
    ),
    ("finance", "compte epargne"): (
        "solde épargne", "rendement épargne",
    ),
    ("finance", "ordres de bourse"): (
        "VMP et portefeuille",
    ),
}

PROFILE_NEEDS = {
    "Pilotage global": list(PILOTAGE_NEEDS),
    "Finance & trésorerie": ["tresorerie", "rentabilite", "bfr_fiscalite"],
    "Commercial & marketing": ["ventes_prix", "concurrence"],
    "Production & achats": ["production_mrp", "approvisionnement", "investissement"],
    "RH": ["rh"],
}


@dataclass(frozen=True)
class DocumentCandidate:
    module_display: str
    module_key: str
    title: str
    url: str
    card_text: str = ""
    price: float | None = None
    locked: bool | None = None
    owned: bool | None = None
    needs: tuple[str, ...] = ()
    score: float = 0.0
    rationale: str = ""
    facts: tuple[str, ...] = ()
    purchase_required: bool = False
    db_covered: bool = False
    coverage_source: str = "Portail Subakoua"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(value: str) -> str:
    txt = unicodedata.normalize("NFD", str(value or ""))
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", txt).strip().lower()


def parse_price(text: str) -> float | None:
    """Extrait un prix explicitement suivi d'un symbole euro / mot euros."""
    if not text:
        return None
    clean = str(text).replace("\u00a0", " ")
    patterns = [
        r"([0-9][0-9 .,'\u202f]*)\s*€",
        r"([0-9][0-9 .,'\u202f]*)\s*euros?",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, clean, flags=re.I)
        if not matches:
            continue
        raw = matches[-1].replace("\u202f", " ").replace(" ", "")
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        # Cas 1.234 = 1234 si aucun décimal évident.
        elif raw.count(".") > 1:
            raw = raw.replace(".", "")
        try:
            value = float(raw)
            return value if math.isfinite(value) and value >= 0 else None
        except ValueError:
            continue
    return None


def infer_access_state(text: str) -> tuple[bool | None, bool | None]:
    n = normalize_text(text)
    locked_markers = (
        "vous n'avez pas encore achete ce document",
        "specimen",
        "acheter ce document",
        "acheter",
        "debloquer",
        "acceder au document",
    )
    owned_markers = (
        "document achete",
        "deja achete",
        "acces",
        "telecharger",
    )
    locked = any(m in n for m in locked_markers)
    owned = any(m in n for m in owned_markers)
    if locked:
        return True, False
    if owned:
        return False, True
    return None, None


def document_needs(module_key: str, title: str) -> tuple[str, ...]:
    m = normalize_text(module_key)
    t = normalize_text(title)

    tags: set[str] = set()

    exact = {
        ("banque_assurance", "banque"): {"tresorerie", "bfr_fiscalite"},
        ("expert_comptable", "synthese"): {"tresorerie", "rentabilite", "bfr_fiscalite"},
        ("expert_comptable", "detail des comptes"): {"rentabilite", "bfr_fiscalite", "ventes_prix"},
        ("expert_comptable", "tva et impot sur les societes"): {"bfr_fiscalite"},
        ("donnees_internes", "tableau de bord"): {"tresorerie", "rentabilite", "ventes_prix", "production_mrp"},
        ("donnees_internes", "marketing"): {"ventes_prix", "concurrence"},
        ("donnees_internes", "production"): {"production_mrp"},
        ("donnees_internes", "stocks"): {"production_mrp", "approvisionnement", "tresorerie"},
        ("donnees_internes", "ressources humaines"): {"rh"},
        ("production", "les ordres de production"): {"production_mrp", "approvisionnement"},
        ("production", "les investissements"): {"investissement", "production_mrp"},
        ("fournisseurs", "matieres"): {"approvisionnement"},
        ("fournisseurs", "immobilisations"): {"investissement"},
        ("etudes_marche", "etudes structurelles"): {"ventes_prix", "concurrence"},
        ("etudes_marche", "etudes conjoncturelles"): {"ventes_prix"},
        ("veille_concurrentielle", "performance commerciale"): {"concurrence", "ventes_prix"},
        ("veille_concurrentielle", "decisions marketing"): {"concurrence", "ventes_prix"},
        ("veille_concurrentielle", "evolution marketing"): {"concurrence", "ventes_prix"},
        ("veille_concurrentielle", "comparaison financiere"): {"rentabilite", "concurrence"},
        ("veille_concurrentielle", "facteurs cles"): {"ventes_prix", "concurrence", "rentabilite"},
        ("rh", "ressources humaines"): {"rh"},
        ("finance", "emprunts"): {"tresorerie"},
        ("finance", "compte epargne"): {"tresorerie"},
        ("finance", "ordres de bourse"): {"tresorerie"},
        ("finance", "assurances"): {"rentabilite"},
    }
    key = (m, t)
    if key in exact:
        tags |= exact[key]

    # Fallbacks tolérants aux libellés légèrement différents.
    if "prix" in t or "vente" in t or "commercial" in t or "marketing" in t:
        tags |= {"ventes_prix"}
    if "concurrent" in t or "marche" in t or "parts de marche" in t:
        tags |= {"concurrence"}
    if "stock" in t or "production" in t or "ordre" in t:
        tags |= {"production_mrp"}
    if "fournisseur" in t or "matiere" in t or "approvisionnement" in m:
        tags |= {"approvisionnement"}
    if "rh" in m or "ressources humaines" in t:
        tags |= {"rh"}
    if "banque" in t or "emprunt" in t or "epargne" in t or "tresorerie" in t:
        tags |= {"tresorerie"}
    if "tva" in t or "impot" in t or "bfr" in t or "fiscal" in t or "comptable" in m:
        tags |= {"bfr_fiscalite"}
    if "synthese" in t or "resultat" in t or "comparaison financiere" in t:
        tags |= {"rentabilite"}
    if "investissement" in t or "immobilisation" in t or "machine" in t:
        tags |= {"investissement"}
    return tuple(sorted(tags))


def rank_score(module_key: str, title: str, price: float | None) -> tuple[float, str]:
    needs = document_needs(module_key, title)
    if not needs:
        return 0.0, "Aucun besoin de pilotage identifié automatiquement."
    raw = sum(float(PILOTAGE_NEEDS[n]["weight"]) for n in needs)
    # Petit bonus aux documents multi-besoins ; pas de pénalisation forte du prix ici,
    # le ratio valeur/coût est géré par l'optimiseur.
    bonus = max(0.0, (len(needs) - 1) * 2.0)
    score = raw + bonus
    labels = ", ".join(PILOTAGE_NEEDS[n]["label"] for n in needs)
    return score, f"Couvre : {labels}."


def make_candidate(module_display: str, module_key: str, raw: dict[str, Any]) -> DocumentCandidate:
    title = str(raw.get("title") or "Document").strip()
    card_text = str(raw.get("card_text") or "").strip()
    price = raw.get("price")
    if price is None:
        price = parse_price(card_text)
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    locked = raw.get("locked")
    owned = raw.get("owned")
    if locked is None or owned is None:
        inferred_locked, inferred_owned = infer_access_state(card_text)
        locked = locked if locked is not None else inferred_locked
        owned = owned if owned is not None else inferred_owned
    needs = document_needs(module_key, title)
    score, rationale = rank_score(module_key, title, price)
    purchase_required = bool(locked is True and owned is not True)
    m = normalize_text(module_key)
    t = normalize_text(title)
    facts = DOCUMENT_FACTS.get((m, t), ())
    return DocumentCandidate(
        module_display=module_display,
        module_key=module_key,
        title=title,
        url=str(raw.get("url") or ""),
        card_text=card_text,
        price=price,
        locked=locked,
        owned=owned,
        needs=needs,
        score=score,
        rationale=rationale,
        facts=facts,
        purchase_required=purchase_required,
        db_covered=bool(raw.get("db_covered", False)),
        coverage_source=str(raw.get("coverage_source", "Portail Subakoua")),
    )


def optimize_document_plan(
    candidates: Iterable[DocumentCandidate],
    profile: str = "Pilotage global",
    budget: float | None = None,
) -> dict[str, Any]:
    """Sélectionne des documents par couverture marginale / coût.

    Le résultat ne déclenche aucun achat. Il constitue un plan que l'UI ou le
    scraper peut ensuite faire valider avant toute dépense.
    """
    candidates = [c for c in candidates if c.score > 0]
    needs = list(PROFILE_NEEDS.get(profile, PROFILE_NEEDS["Pilotage global"]))
    remaining = set(needs)
    selected: list[DocumentCandidate] = []
    rejected: list[dict[str, Any]] = []
    spent = 0.0

    # Les documents déjà possédés sont ajoutés à la couverture gratuitement.
    for c in candidates:
        if c.owned is True and any(n in remaining for n in c.needs):
            selected.append(c)
            remaining -= set(c.needs)

    pool = [c for c in candidates if c.owned is not True]

    while remaining:
        best = None
        best_ratio = -1.0
        for c in pool:
            if c in selected:
                continue
            marginal = [n for n in c.needs if n in remaining]
            if not marginal:
                continue
            value = sum(float(PILOTAGE_NEEDS[n]["weight"]) for n in marginal)
            cost = c.price
            if cost is None:
                # Prix inconnu : le plan le signale mais ne l'achète jamais automatiquement.
                ratio = 0.0
            else:
                ratio = value / max(cost, 0.01)
            # Bonus fort pour document indispensable à la couverture d'un besoin lourd.
            if len(marginal) >= 2:
                ratio *= 1.15
            if ratio > best_ratio:
                best_ratio = ratio
                best = (c, marginal, value)

        if best is None:
            break
        c, marginal, value = best
        cost = float(c.price) if c.price is not None else math.inf
        if math.isfinite(cost) and (budget is None or spent + cost <= budget):
            selected.append(c)
            spent += cost
            remaining -= set(marginal)
        else:
            rejected.append({
                **c.to_dict(),
                "reason": "prix inconnu" if c.price is None else "budget insuffisant",
                "marginal_needs": marginal,
            })
            pool.remove(c)
            if not pool:
                break

    selected_urls = {(c.module_key, c.title) for c in selected}
    for c in candidates:
        if (c.module_key, c.title) not in selected_urls and c.owned is not True and c.score > 0:
            if not any(r.get("module_key") == c.module_key and r.get("title") == c.title for r in rejected):
                rejected.append({**c.to_dict(), "reason": "non prioritaire"})

    coverage = 1.0 - (len(remaining) / max(len(needs), 1))
    return {
        "profile": profile,
        "budget": budget,
        "spent_estimate": spent,
        "remaining_budget": None if budget is None else max(0.0, budget - spent),
        "coverage_ratio": coverage,
        "covered_needs": sorted(set(needs) - remaining),
        "uncovered_needs": sorted(remaining),
        "selected": [c.to_dict() for c in selected],
        "rejected": rejected,
        "requires_manual_price_check": [c.to_dict() for c in candidates if c.price is None and c.owned is not True],
    }
