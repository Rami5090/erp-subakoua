"""Moteur d'exécution du scraper intelligent.

Sépare trois opérations critiques :
1) planification documentaire,
2) achat explicitement autorisé,
3) lecture API puis synchronisation Aiven.

Aucun achat réel n'est exécuté par la planification seule.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import document_optimizer as optimizer
from subakoua_api import StudyAvailability, StudyFetchResult, SubakouaAPIClient


MODULE_BY_STUDY: dict[str, str] = {
    "monitoring": "donnees_internes",
    "ensaacvm": "donnees_internes",
    "companyStock": "production",
    "potentialMarketStudy": "etudes_marche",
    "peeumreusreprv": "etudes_marche",
    "pevcvipm": "veille_concurrentielle",
    "pevcvicppv": "veille_concurrentielle",
    "pevcecprpf": "veille_concurrentielle",
    "pevcvicpve": "veille_concurrentielle",
    "pevcfcpfquca": "veille_concurrentielle",
    "bankStatements": "banque_assurance",
    "peeedsftt": "expert_comptable",
    "peeedsbidt": "expert_comptable",
    "peeedscrsm": "expert_comptable",
    "peeedscrdl": "expert_comptable",
    "peeefstvp": "expert_comptable",
    "peeefsisp": "expert_comptable",
    "peeedccscl": "expert_comptable",
    "pebaemecta": "banque_assurance",
    "initialBalanceSheet": "expert_comptable",
    "ifrsStatementOfCashFlow": "expert_comptable",
    "ifrsStatementOfFinancialPosition": "expert_comptable",
    "ifrsStatementOfProfitAndLoss": "expert_comptable",
    "enprgeqtpp": "production",
    "enprgeqtmpdb": "production",
    "enprgeqtatdb": "production",
    "enprgeqtcuespf": "production",
    "enprgeqlquespf": "production",
    "enprgeqlccqp": "production",
    "enprgeqlevss": "rh",
    "enapgeqlquesmp": "approvisionnement",
    "enapgeqtcuesmp": "approvisionnement",
    "enapgeqlevss": "rh",
    "enafgeqlevss": "rh",
    "pefrmppofr": "fournisseurs",
    "pefrpffh": "fournisseurs",
    "pefrmpfh": "fournisseurs",
    "pefrmpcv": "fournisseurs",
    "pefrmpta": "fournisseurs",
}


@dataclass(frozen=True)
class PlannedPeriod:
    target_period: str
    purchase_period: str
    live_rows: int
    already_available_count: int
    free_count: int
    baseline_coverage_percent: float = 0.0
    proposed_purchase_count: int = 0
    proposed_cost: float = 0.0
    recommended_ids: tuple[str, ...] = ()


def load_local_catalog() -> list[dict[str, str]]:
    import csv
    with optimizer.CATALOG_PATH.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def availability_rows(availability: Sequence[StudyAvailability]) -> list[dict[str, object]]:
    return [asdict(x) for x in availability]


def plan_for_period(
    api: SubakouaAPIClient,
    target_period: str,
    *,
    budget: float,
    allow_5000: bool = False,
) -> tuple[PlannedPeriod, list[optimizer.PlanItem], list[dict[str, object]]]:
    """Construit le plan à partir du catalogue LIVE, pas du snapshot historique."""
    live = api.catalog(target_period, optimizer.load_profiles().keys())
    live_rows = availability_rows(live)
    profiles = optimizer.load_profiles()
    plan = optimizer.build_purchase_plan(
        profiles,
        live_rows,
        target_period=optimizer.period_code_to_label(api.period_code(target_period)),
        budget=budget,
        allow_5000=allow_5000,
    )
    purchase_period = optimizer.next_period(optimizer.period_code_to_label(api.period_code(target_period)), -1)
    coverage = optimizer.build_coverage_snapshot(profiles, live_rows)
    summary = PlannedPeriod(
        target_period=optimizer.period_code_to_label(api.period_code(target_period)),
        purchase_period=purchase_period,
        live_rows=len(live_rows),
        already_available_count=sum(1 for r in live_rows if bool(r.get("bought_by_team", r.get("boughtByTeam", False)))),
        free_count=sum(1 for r in live_rows if float(r.get("price", 0) or 0) <= 0),
        baseline_coverage_percent=float(coverage["coverage_percent"]),
        proposed_purchase_count=len(plan),
        proposed_cost=round(sum(x.price for x in plan), 2),
        recommended_ids=tuple(x.study_id for x in plan),
    )
    return summary, plan, live_rows


def build_plan_for_periods(
    api: SubakouaAPIClient,
    target_periods: Sequence[str],
    *,
    budget_per_period: float,
    allow_5000: bool = False,
) -> tuple[list[PlannedPeriod], list[optimizer.PlanItem]]:
    summaries: list[PlannedPeriod] = []
    items: list[optimizer.PlanItem] = []
    for period in target_periods:
        summary, plan, _ = plan_for_period(api, period, budget=budget_per_period, allow_5000=allow_5000)
        summaries.append(summary)
        items.extend(plan)
    return summaries, items


def only_purchasable_now(plan: Sequence[optimizer.PlanItem], current_period: str) -> list[optimizer.PlanItem]:
    current_label = optimizer.period_code_to_label(current_period) if str(current_period).isdigit() else current_period
    return [x for x in plan if x.purchase_period == current_label]


def execute_purchase_plan(
    api: SubakouaAPIClient,
    plan: Sequence[optimizer.PlanItem],
    *,
    current_period: str,
    allow_real_purchases: bool,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """Exécute uniquement les achats autorisés et exigibles maintenant."""
    log = logger or logging.getLogger("subakoua_smart_scraper")
    if not allow_real_purchases:
        return [{"study_id": x.study_id, "status": "SIMULATION_SEULEMENT", "purchase_period": x.purchase_period} for x in plan]

    current_label = optimizer.period_code_to_label(current_period) if str(current_period).isdigit() else current_period
    results: list[dict[str, Any]] = []
    live_by_id: dict[str, StudyAvailability] = {}
    try:
        for row in api.catalog(current_label):
            live_by_id[row.study_id] = row
    except Exception as exc:
        raise RuntimeError(f"Impossible de revalider le catalogue avant achat : {exc}") from exc

    for item in plan:
        if item.purchase_period != current_label:
            results.append({"study_id": item.study_id, "status": "A_PLANIFIER", "purchase_period": item.purchase_period, "target_period": item.target_period})
            continue
        live = live_by_id.get(item.study_id)
        if live is None:
            results.append({"study_id": item.study_id, "status": "REFUSE", "purchase_period": item.purchase_period, "reason": "Étude absente du catalogue live."})
            continue
        if live.bought_by_team:
            results.append({"study_id": item.study_id, "status": "DEJA_ACHETE", "purchase_period": item.purchase_period, "target_period": item.target_period})
            continue
        if abs(live.price - item.price) > 0.01:
            results.append({"study_id": item.study_id, "status": "REFUSE", "purchase_period": item.purchase_period, "reason": f"Prix live différent : {live.price} € au lieu de {item.price} €. Replanifier."})
            continue
        try:
            response = api.purchase_study(item.purchase_period, item.study_id)
            results.append({"study_id": item.study_id, "status": "ACHETE", "purchase_period": item.purchase_period, "target_period": item.target_period, "response": response})
            log.warning("ACHAT RÉEL Subakoua : %s | %s -> %s | prix=%s", item.study_id, item.purchase_period, item.target_period, response.get("price"))
        except Exception as exc:
            results.append({"study_id": item.study_id, "status": "ERREUR", "purchase_period": item.purchase_period, "error": str(exc)})
            log.exception("Achat étude en erreur: %s", item.study_id)
    return results


def ensure_smart_tables(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS erp_etudes (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                periode VARCHAR(50) NOT NULL,
                study_id VARCHAR(120) NOT NULL,
                label VARCHAR(255) NULL,
                module VARCHAR(80) NULL,
                price DECIMAL(12,2) DEFAULT 0,
                source_endpoint TEXT NULL,
                read_allowed BOOLEAN DEFAULT FALSE,
                payload JSON NOT NULL,
                fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_erp_etude (periode, study_id)
            ) ENGINE=InnoDB
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS erp_scraper_runs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                run_id VARCHAR(50) NOT NULL UNIQUE,
                started_at DATETIME NOT NULL,
                finished_at DATETIME NULL,
                mode VARCHAR(50) NOT NULL,
                selected_periods JSON NULL,
                selected_studies JSON NULL,
                estimated_cost DECIMAL(12,2) DEFAULT 0,
                actual_cost DECIMAL(12,2) DEFAULT 0,
                status VARCHAR(50) NOT NULL,
                details JSON NULL
            ) ENGINE=InnoDB
        """)
    connection.commit()


def store_fetch_result(connection, result: StudyFetchResult, *, label: str = "") -> None:
    if not result.valid or not result.read_allowed:
        raise ValueError(f"Payload refusé pour {result.study_id}/{result.period}: {result.reason or 'readAllowed=false'}")
    module = MODULE_BY_STUDY.get(result.study_id, "etudes")
    payload_json = json.dumps(result.payload, ensure_ascii=False)
    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO erp_etudes
                (periode, study_id, label, module, price, source_endpoint, read_allowed, payload, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON DUPLICATE KEY UPDATE
                label=VALUES(label), module=VALUES(module), price=VALUES(price),
                source_endpoint=VALUES(source_endpoint), read_allowed=VALUES(read_allowed),
                payload=VALUES(payload), fetched_at=NOW()
        """, (
            result.period,
            result.study_id,
            label[:255],
            module,
            float(result.payload.get("price") or 0),
            result.endpoint,
            int(result.read_allowed),
            payload_json,
        ))
    connection.commit()


def fetch_and_store(
    api: SubakouaAPIClient,
    connection,
    target_periods: Sequence[str],
    study_ids: Sequence[str],
    *,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    log = logger or logging.getLogger("subakoua_smart_scraper")
    profiles = optimizer.load_profiles()
    ensure_smart_tables(connection)
    results: list[dict[str, Any]] = []
    for period in target_periods:
        for sid in study_ids:
            if not profiles.get(sid):
                continue
            try:
                result = api.fetch_study(sid, period, require_read_allowed=True)
                if not result.valid:
                    results.append({"period": period, "study_id": sid, "status": "REFUSE", "reason": result.reason})
                    continue
                store_fetch_result(connection, result, label=profiles[sid].label)
                results.append({"period": period, "study_id": sid, "status": "OK", "endpoint": result.endpoint})
            except Exception as exc:
                log.exception("Lecture API en erreur %s / %s", period, sid)
                results.append({"period": period, "study_id": sid, "status": "ERREUR", "error": str(exc)})
    return results


def build_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def register_run(connection, run_id: str, *, mode: str, periods: Sequence[str], studies: Sequence[str], estimated_cost: float, status: str = "started", details: Mapping[str, Any] | None = None) -> None:
    ensure_smart_tables(connection)
    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO erp_scraper_runs
                (run_id, started_at, mode, selected_periods, selected_studies, estimated_cost, status, details)
            VALUES (%s,NOW(),%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE status=VALUES(status), details=VALUES(details)
        """, (
            run_id, mode, json.dumps(list(periods), ensure_ascii=False), json.dumps(list(studies), ensure_ascii=False),
            float(estimated_cost), status, json.dumps(details or {}, ensure_ascii=False),
        ))
    connection.commit()
