"""Optimisation déterministe des achats documentaires Subakoua.

Le moteur traite l'achat documentaire comme un problème de couverture sous
contrainte budgétaire. Il ne dépend d'aucun LLM : les profils sont construits
à partir du catalogue audité + des endpoints API connus, puis les besoins de
pilotage sont couverts par ordre de valeur marginale / euro.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import document_strategy

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "subakoua_study_catalog.csv"
ENDPOINT_CATALOG_PATH = BASE_DIR / "study_api_catalog.json"

MONTH_NAMES = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]
MONTH_BY_NAME = {x.lower(): i + 1 for i, x in enumerate(MONTH_NAMES)}


@dataclass(frozen=True)
class StudyProfile:
    study_id: str
    label: str
    price: float
    domain: str
    tags: tuple[str, ...]
    decision_levers: tuple[str, ...]
    horizons: tuple[str, ...]
    importance: float
    recurring: str = "monthly"
    strategic_only: bool = False
    endpoint_known: bool = False
    response_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionNeed:
    need_id: str
    label: str
    weight: float
    tags: tuple[str, ...]
    horizons: tuple[str, ...] = ("court", "moyen", "long")
    mandatory: bool = False


@dataclass(frozen=True)
class PlanItem:
    study_id: str
    label: str
    price: float
    purchase_period: str
    target_period: str
    already_available: bool
    endpoint_known: bool
    utility: float
    efficiency: float
    reason: str
    status: str
    covered_needs: tuple[str, ...] = ()


DEFAULT_NEEDS = (
    # L'objectif stratégique de l'ERP est volontairement dominant.
    DecisionNeed(
        "market_share", "Maximiser la part de marché", 40.0,
        ("market_share", "competitor", "sales", "price", "quality", "advertising"), mandatory=True,
    ),
    DecisionNeed(
        "forecast", "Prévoir les ventes et la saisonnalité", 22.0,
        ("forecast", "seasonality", "sales", "market_potential"), mandatory=True,
    ),
    DecisionNeed(
        "production", "Sécuriser production, capacité et matières", 15.0,
        ("capacity", "production", "material", "stock", "quality"), mandatory=True,
    ),
    DecisionNeed(
        "cash", "Maîtriser trésorerie, BFR et fiscalité", 12.0,
        ("cash", "bfr", "vat", "tax", "debt", "working_capital"), mandatory=True,
    ),
    DecisionNeed(
        "profit", "Améliorer profitabilité et coûts", 7.0,
        ("profit", "cost", "margin", "finance", "payroll"),
    ),
    DecisionNeed(
        "hr", "Piloter les ressources humaines", 4.0,
        ("hr", "payroll", "staff_quality", "satisfaction"),
    ),
)

# Profils explicites des études les plus structurantes.
OVERRIDES: dict[str, dict[str, Any]] = {
    # Pilotage / marché
    "monitoring": dict(domain="pilotage", tags=("cash", "market_share", "sales", "profit", "stock", "satisfaction"), decision_levers=("pilotage",), importance=100, recurring="monthly"),
    "companyResults": dict(domain="finance", tags=("profit", "sales", "equity"), decision_levers=("profit", "cash"), importance=85),
    "companyStock": dict(domain="production", tags=("stock", "production"), decision_levers=("production", "stock"), importance=88),
    "ensaacvm": dict(domain="marketing", tags=("sales", "forecast", "seasonality"), decision_levers=("sales", "forecast"), importance=92),
    "potentialMarketStudy": dict(domain="marketing", tags=("market_potential", "sales", "market_share"), decision_levers=("market", "forecast", "market_share"), importance=95),
    "peeumreusreprv": dict(domain="marketing", tags=("forecast", "seasonality", "market_potential", "sales"), decision_levers=("forecast",), importance=90, recurring="annual", strategic_only=True),
    # Concurrence
    "pevcvipm": dict(domain="concurrence", tags=("market_share", "competitor", "sales"), decision_levers=("market_share",), importance=100),
    "pevcvicppv": dict(domain="concurrence", tags=("competitor", "price"), decision_levers=("price",), importance=94, recurring="monthly"),
    "pevcecprpf": dict(domain="concurrence", tags=("competitor", "price", "trend"), decision_levers=("price", "trend"), importance=91),
    "pevcvicpve": dict(domain="concurrence", tags=("competitor", "sales", "market_share"), decision_levers=("market_share",), importance=91),
    "pevcfcpfquca": dict(domain="concurrence", tags=("competitor", "price", "quality", "sales", "market_share"), decision_levers=("price", "quality", "market_share"), importance=98),
    "pevcvfrc": dict(domain="concurrence", tags=("competitor", "profit"), decision_levers=("profit",), importance=65),
    "pevcvfsn": dict(domain="concurrence", tags=("competitor", "equity"), decision_levers=("finance",), importance=55),
    # Production
    "enprgeqtpp": dict(domain="production", tags=("production", "material", "capacity"), decision_levers=("production",), importance=96, recurring="structural"),
    "enprgeqtmpdb": dict(domain="production", tags=("material", "stock", "capacity", "production"), decision_levers=("purchasing", "production"), importance=98),
    "enprgeqtatdb": dict(domain="production", tags=("capacity", "production", "stock", "investment"), decision_levers=("capacity", "investment"), importance=99),
    "enprgeqtcuespf": dict(domain="production", tags=("cost", "stock", "production", "margin"), decision_levers=("cost", "production"), importance=88),
    "enprgeqlquespf": dict(domain="production", tags=("quality", "production", "stock"), decision_levers=("quality",), importance=88),
    "enprgeqlccqp": dict(domain="production", tags=("quality", "production", "market_share"), decision_levers=("quality",), importance=93),
    "enprgeqlevss": dict(domain="production", tags=("production", "staff_quality", "payroll"), decision_levers=("hr", "production"), importance=65),
    "productionCostDetailStudy": dict(domain="production", tags=("cost", "margin", "production", "profit"), decision_levers=("cost", "price"), importance=94),
    "pefrpffh": dict(domain="production", tags=("product", "quality"), decision_levers=("product",), importance=75, recurring="structural"),
    # Approvisionnement
    "enapgeqlquesmp": dict(domain="approvisionnement", tags=("material", "quality", "supplier"), decision_levers=("purchasing", "quality"), importance=80),
    "enapgeqtcuesmp": dict(domain="approvisionnement", tags=("material", "cost", "supplier"), decision_levers=("purchasing", "cost"), importance=84),
    "enapgeqtcofr": dict(domain="approvisionnement", tags=("supplier", "material", "risk"), decision_levers=("supplier",), importance=62, strategic_only=True),
    "enapgeqlevss": dict(domain="approvisionnement", tags=("supplier", "staff_quality", "payroll"), decision_levers=("hr",), importance=55),
    "pefrmppofr": dict(domain="approvisionnement", tags=("supplier", "risk", "cash"), decision_levers=("supplier", "cash"), importance=88, recurring="structural"),
    "pefrmpfh": dict(domain="approvisionnement", tags=("material", "supplier", "cost"), decision_levers=("purchasing",), importance=90, recurring="structural"),
    "pefrmpcv": dict(domain="approvisionnement", tags=("supplier", "payment", "cash"), decision_levers=("cash", "purchasing"), importance=82, recurring="structural"),
    "pefrmpta": dict(domain="approvisionnement", tags=("supplier", "cost", "material"), decision_levers=("purchasing", "cost"), importance=87, recurring="structural"),
    # Finance
    "bankStatements": dict(domain="finance", tags=("cash", "debt", "vat"), decision_levers=("cash", "debt"), importance=100),
    "pebaemecta": dict(domain="finance", tags=("debt", "cash", "finance"), decision_levers=("debt",), importance=82, recurring="structural"),
    "peeedsftt": dict(domain="finance", tags=("cash", "bfr", "working_capital", "investment", "debt"), decision_levers=("cash", "bfr"), importance=100),
    "peeedsbidt": dict(domain="finance", tags=("bfr", "cash", "debt", "working_capital", "equity"), decision_levers=("cash", "bfr", "debt"), importance=100),
    "peeedsbiak": dict(domain="finance", tags=("bfr", "cash", "debt", "equity"), decision_levers=("cash",), importance=82),
    "peeedscrsm": dict(domain="finance", tags=("profit", "sales", "cost", "margin"), decision_levers=("profit",), importance=92),
    "peeedscrdl": dict(domain="finance", tags=("profit", "sales", "cost", "margin", "detail"), decision_levers=("profit", "cost"), importance=90),
    "peeefstvp": dict(domain="finance", tags=("vat", "cash"), decision_levers=("cash", "vat"), importance=97),
    "peeefsisp": dict(domain="finance", tags=("tax", "cash", "profit"), decision_levers=("tax", "cash"), importance=88),
    "peeedccscl": dict(domain="finance", tags=("bfr", "working_capital", "cash"), decision_levers=("cash", "customer_credit"), importance=91),
    "peeedccg": dict(domain="finance", tags=("sales", "profit", "vat"), decision_levers=("sales", "profit"), importance=88),
    "peeedcahm": dict(domain="finance", tags=("cost", "stock", "working_capital"), decision_levers=("stock", "purchasing"), importance=82),
    "peeedcahsx": dict(domain="finance", tags=("cost", "profit"), decision_levers=("cost",), importance=76),
    "peeedcitesm": dict(domain="finance", tags=("payroll", "hr", "profit"), decision_levers=("hr", "cost"), importance=76),
    "peeedcdadpm": dict(domain="finance", tags=("cost", "finance"), decision_levers=("investment", "profit"), importance=72),
    "peeedcchfi": dict(domain="finance", tags=("finance", "debt", "cash"), decision_levers=("debt",), importance=74),
    "peeedcpofi": dict(domain="finance", tags=("finance", "cash"), decision_levers=("cash",), importance=60),
    "peeedcpehc": dict(domain="finance", tags=("profit", "stock", "working_capital"), decision_levers=("profit",), importance=62),
    "ifrsStatementOfCashFlow": dict(domain="finance", tags=("cash", "bfr", "investment", "debt"), decision_levers=("cash",), importance=86),
    "ifrsStatementOfFinancialPosition": dict(domain="finance", tags=("bfr", "debt", "equity", "cash"), decision_levers=("cash",), importance=84),
    "ifrsStatementOfProfitAndLoss": dict(domain="finance", tags=("profit", "cost", "sales"), decision_levers=("profit",), importance=82),
    "initialBalanceSheet": dict(domain="finance", tags=("bfr", "debt", "equity", "cash"), decision_levers=("cash",), importance=70, recurring="structural"),
    # RH
    "enafgeqlevss": dict(domain="rh", tags=("hr", "payroll", "staff_quality", "satisfaction"), decision_levers=("hr",), importance=75),
}


def _normalise_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _heuristic_tags(label: str, response_keys: Sequence[str]) -> tuple[str, ...]:
    text = _normalise_label(label) + " " + " ".join(response_keys).lower()
    mapping = [
        ("prix", "price"), ("vente", "sales"), ("marché", "market_share"),
        ("concurr", "competitor"), ("qualité", "quality"), ("production", "production"),
        ("matière", "material"), ("stock", "stock"), ("atelier", "capacity"),
        ("capacité", "capacity"), ("coût", "cost"), ("marge", "margin"),
        ("trésorerie", "cash"), ("bank", "cash"), ("vat", "vat"), ("tva", "vat"),
        ("impôt", "tax"), ("corporationtax", "tax"), ("emprunt", "debt"),
        ("loan", "debt"), ("personnel", "payroll"), ("ressources", "hr"),
        ("staff", "hr"), ("publicité", "advertising"), ("prévision", "forecast"),
        ("forecast", "forecast"), ("saisonn", "seasonality"), ("payment", "payment"),
        ("paiement", "payment"), ("créance", "working_capital"), ("bfr", "bfr"),
        ("fournisseur", "supplier"), ("satisfaction", "satisfaction"),
        ("risque", "risk"), ("investissement", "investment"), ("machine", "investment"),
    ]
    tags: list[str] = []
    for needle, tag in mapping:
        if needle in text and tag not in tags:
            tags.append(tag)
    return tuple(tags or ["reference"])


def _domain_from_label(label: str) -> str:
    l = _normalise_label(label)
    if any(x in l for x in ("marché", "vente", "prix", "publicité", "concurrent", "distribution")):
        return "marketing"
    if any(x in l for x in ("production", "matière", "atelier", "stock", "qualité")):
        return "production"
    if any(x in l for x in ("bilan", "résultat", "tva", "impôt", "trésorerie", "créance", "charge", "financier", "emprunt", "comptable")):
        return "finance"
    if any(x in l for x in ("ressource", "personnel", "service / administration", "service / production", "service / approvisionnement")):
        return "rh"
    return "autre"


def load_profiles() -> dict[str, StudyProfile]:
    endpoint_data = json.loads(ENDPOINT_CATALOG_PATH.read_text(encoding="utf-8")) if ENDPOINT_CATALOG_PATH.exists() else {"known_endpoints": {}}
    known_endpoints = endpoint_data.get("known_endpoints") or {}
    profiles: dict[str, StudyProfile] = {}
    with CATALOG_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sid = str(row["study_id"])
            label = str(row["label"])
            price = float(row.get("price") or 0)
            endpoint_known = sid in known_endpoints
            response_keys = tuple(known_endpoints.get(sid, {}).get("response_keys", []))
            base_tags = _heuristic_tags(label, response_keys)
            base = StudyProfile(
                study_id=sid,
                label=label,
                price=price,
                domain=_domain_from_label(label),
                tags=base_tags,
                decision_levers=tuple(),
                horizons=("court", "moyen", "long"),
                importance=max(20.0, 80.0 - price / 75.0),
                recurring="monthly" if any(x in _normalise_label(label) for x in ("évolution", "mensuel", "stock", "résultat", "tva", "vente")) else "structural",
                strategic_only=price >= 5000,
                endpoint_known=endpoint_known,
                response_keys=response_keys,
            )
            strategy = document_strategy.matrix_by_study_id().get(sid, {})
            merged = dict(strategy)
            merged.update(OVERRIDES.get(sid, {}))
            profiles[sid] = StudyProfile(
                study_id=sid,
                label=label,
                price=price,
                domain=str(merged.get("domain", base.domain)),
                tags=tuple(merged.get("tags", base.tags)),
                decision_levers=tuple(merged.get("decision_levers", base.decision_levers)),
                horizons=tuple(merged.get("horizons", base.horizons)),
                importance=float(merged.get("importance", base.importance)),
                recurring=str(merged.get("recurring", base.recurring)),
                strategic_only=bool(merged.get("strategic_only", base.strategic_only)),
                endpoint_known=endpoint_known,
                response_keys=response_keys,
            )
    return profiles


def period_to_seq(period: str) -> int:
    """Séquence humaine Année 1-Janvier = 1."""
    m = re.fullmatch(r"Année\s+(\d+)\s*-\s*(.+)", str(period).strip(), re.I)
    if not m:
        raise ValueError(f"Période invalide: {period!r}")
    month = MONTH_BY_NAME.get(m.group(2).strip().lower())
    if not month:
        raise ValueError(f"Mois inconnu: {period!r}")
    return (int(m.group(1)) - 1) * 12 + month


def period_label_to_code(period: str) -> str:
    """Convertit Année 1 - Janvier -> 0101 ; Année 2 - Janvier -> 0201."""
    seq = period_to_seq(period)
    year = (seq - 1) // 12 + 1
    month = (seq - 1) % 12 + 1
    return f"{year:02d}{month:02d}"


def period_code_to_label(code: str) -> str:
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
    seq = period_to_seq(period) + offset
    if seq < 1:
        year, month = 0, 12 + seq
        raise ValueError("La période résultante est antérieure à Année 1 - Janvier")
    year = (seq - 1) // 12 + 1
    month = (seq - 1) % 12 + 1
    return f"Année {year} - {MONTH_NAMES[month - 1]}"


def _profile_coverages(profile: StudyProfile, needs: Sequence[DecisionNeed]) -> dict[str, float]:
    tags = set(profile.tags)
    out: dict[str, float] = {}
    for need in needs:
        overlap = tags.intersection(need.tags)
        if not overlap:
            continue
        # Couverture partielle par document. Plus un document répond à des dimensions
        # distinctes du besoin, plus sa contribution est forte, bornée à 1.
        ratio = min(1.0, len(overlap) / max(1.0, min(4, len(need.tags))))
        out[need.need_id] = ratio
    return out


def score_profile(profile: StudyProfile, needs: Sequence[DecisionNeed] = DEFAULT_NEEDS) -> tuple[float, str]:
    coverage = _profile_coverages(profile, needs)
    score = sum(next(n.weight for n in needs if n.need_id == k) * v for k, v in coverage.items())
    score *= 0.8 + profile.importance / 500.0
    if profile.endpoint_known:
        score *= 1.05
    covered = [next(n.label for n in needs if n.need_id == k) for k in coverage]
    reason = "; ".join(covered[:3]) if covered else "Référence de pilotage secondaire"
    return score, reason


def _candidate_utility(candidate: StudyProfile, covered: Mapping[str, float], needs: Sequence[DecisionNeed]) -> tuple[float, tuple[str, ...], str]:
    local = _profile_coverages(candidate, needs)
    marginal = 0.0
    newly: list[str] = []
    for need in needs:
        old = float(covered.get(need.need_id, 0.0))
        inc = min(1.0, old + local.get(need.need_id, 0.0)) - old
        if inc > 0:
            marginal += need.weight * inc
            newly.append(need.label)
    # Petit bonus documentaire pour favoriser les études riches sans écraser la couverture.
    marginal *= 0.8 + candidate.importance / 500.0
    if candidate.endpoint_known:
        marginal *= 1.05
    return marginal, tuple(newly), "; ".join(newly[:3]) or "Complément de couverture"


def build_purchase_plan(
    profiles: Mapping[str, StudyProfile],
    catalog_rows: Sequence[Mapping[str, object]],
    *,
    target_period: str,
    budget: float,
    needs: Sequence[DecisionNeed] = DEFAULT_NEEDS,
    purchase_lead_periods: int = 1,
    allow_5000: bool = False,
) -> list[PlanItem]:
    """Construit un plan par période avec un glouton à utilité marginale.

    `catalog_rows` doit idéalement être le catalogue API *de la période cible*.
    `boughtByTeam` est alors la source de vérité de disponibilité.
    """
    rows_by_id = {str(r["study_id"]): r for r in catalog_rows}
    budget_left = max(0.0, float(budget))
    covered: dict[str, float] = {}
    chosen: set[str] = set()
    candidates: list[StudyProfile] = []

    # 1) Socle de couverture : études déjà achetées OU gratuites.
    #    Leur contribution doit être déduite avant toute recommandation payante.
    for sid, profile in profiles.items():
        row = rows_by_id.get(sid)
        if not row:
            continue
        price = float(row.get("price", profile.price) or 0)
        bought = bool(row.get("boughtByTeam", row.get("bought_by_team", row.get("bought_any_period", False))))
        if bought or price <= 0:
            local = _profile_coverages(profile, needs)
            for need_id, cov in local.items():
                covered[need_id] = min(1.0, covered.get(need_id, 0.0) + cov)

    # 2) Candidats payants restants.
    for sid, profile in profiles.items():
        row = rows_by_id.get(sid)
        if not row:
            continue
        price = float(row.get("price", profile.price) or 0)
        bought = bool(row.get("boughtByTeam", row.get("bought_by_team", row.get("bought_any_period", False))))
        if bought or price <= 0 or sid in chosen:
            continue
        if profile.strategic_only and not allow_5000:
            continue
        if score_profile(profile, needs)[0] > 0:
            candidates.append(profile)

    while candidates and budget_left > 0:
        feasible: list[tuple[float, float, StudyProfile, tuple[str, ...], str]] = []
        for p in candidates:
            if p.price > budget_left:
                continue
            marginal, new_needs, reason = _candidate_utility(p, covered, needs)
            if marginal <= 0:
                continue
            efficiency = marginal / p.price if p.price else 0.0
            feasible.append((efficiency, marginal, p, new_needs, reason))
        if not feasible:
            break
        feasible.sort(key=lambda x: (-x[0], -x[1], x[2].price, x[2].label))
        _, marginal, picked, new_needs, reason = feasible[0]
        chosen.add(picked.study_id)
        budget_left -= picked.price
        local = _profile_coverages(picked, needs)
        for need_id, cov in local.items():
            covered[need_id] = min(1.0, covered.get(need_id, 0.0) + cov)
        candidates = [p for p in candidates if p.study_id != picked.study_id]

    plan: list[PlanItem] = []
    purchase_period = next_period(target_period, -purchase_lead_periods)
    for sid in chosen:
        profile = profiles[sid]
        row = rows_by_id[sid]
        utility, reason = score_profile(profile, needs)
        plan.append(
            PlanItem(
                study_id=sid,
                label=profile.label,
                price=float(row.get("price", profile.price) or 0),
                purchase_period=purchase_period,
                target_period=target_period,
                already_available=False,
                endpoint_known=profile.endpoint_known,
                utility=round(utility, 3),
                efficiency=round(utility / max(1.0, profile.price), 5),
                reason=reason,
                status="RECOMMANDÉ" if profile.endpoint_known else "RECOMMANDÉ · endpoint à résoudre",
                covered_needs=tuple(n.need_id for n in needs if n.need_id in _profile_coverages(profile, needs)),
            )
        )
    plan.sort(key=lambda x: (-x.utility, x.price, x.label))
    return plan


def build_information_matrix(profiles: Mapping[str, StudyProfile], needs: Sequence[DecisionNeed] = DEFAULT_NEEDS) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    strategy = document_strategy.matrix_by_study_id()
    for p in profiles.values():
        coverage = _profile_coverages(p, needs)
        s = strategy.get(p.study_id, {})
        utility = score_profile(p, needs)[0]
        rows.append({
            "study_id": p.study_id,
            "label": p.label,
            "price": p.price,
            "classe_achat": s.get("purchase_class", ""),
            "domaine": p.domain,
            "endpoint_api": "✅" if p.endpoint_known else "⚠️",
            "base_mapping": s.get("mapping_basis", ""),
            "confiance_mapping": s.get("mapping_confidence", ""),
            "leviers": ", ".join(p.decision_levers),
            "horizons": ", ".join(p.horizons),
            "besoins_couverts": ", ".join(k for k in coverage),
            "utilite": round(utility, 3),
            "utilite_par_euro": round(utility / p.price, 5) if p.price else None,
            "strategique_seulement": p.strategic_only,
        })
    return sorted(rows, key=lambda r: (-(r["utilite"] or 0), r["price"], r["label"]))


def build_coverage_snapshot(
    profiles: Mapping[str, StudyProfile],
    catalog_rows: Sequence[Mapping[str, object]],
    needs: Sequence[DecisionNeed] = DEFAULT_NEEDS,
) -> dict[str, object]:
    """Mesure la couverture du socle gratuit/déjà acheté avant tout achat payant."""
    rows_by_id = {str(r["study_id"]): r for r in catalog_rows}
    covered: dict[str, float] = {}
    available_ids: list[str] = []
    for sid, profile in profiles.items():
        row = rows_by_id.get(sid)
        if not row:
            continue
        price = float(row.get("price", profile.price) or 0)
        bought = bool(row.get("boughtByTeam", row.get("bought_by_team", row.get("bought_any_period", False))))
        if price <= 0 or bought:
            available_ids.append(sid)
            for need_id, cov in _profile_coverages(profile, needs).items():
                covered[need_id] = min(1.0, covered.get(need_id, 0.0) + cov)
    weighted_total = sum(n.weight for n in needs) or 1.0
    weighted_covered = sum(n.weight * min(1.0, covered.get(n.need_id, 0.0)) for n in needs)
    return {
        "covered": {k: round(v, 4) for k, v in covered.items()},
        "coverage_percent": round(100.0 * weighted_covered / weighted_total, 2),
        "available_document_count": len(available_ids),
        "available_study_ids": tuple(available_ids),
    }


def serialize_profiles() -> list[dict[str, object]]:
    return [asdict(x) for x in load_profiles().values()]


if __name__ == "__main__":
    profiles = load_profiles()
    print(f"Profils chargés : {len(profiles)}")
    print(f"Endpoints connus : {sum(p.endpoint_known for p in profiles.values())}")
    print("A1-Janvier =>", period_label_to_code("Année 1 - Janvier"), period_month_id("Année 1 - Janvier"))
