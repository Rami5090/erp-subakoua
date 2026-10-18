"""Moteur financier pur pour l'ERP Subakoua.

Aucune dépendance Streamlit ici : les fonctions peuvent être rétro-testées
sur le dump JSON ou appelées depuis l'interface Streamlit.
"""

from __future__ import annotations

import re
import unicodedata

from dataclasses import dataclass, asdict, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from constants import (
    TVA_STANDARD as TVA_STANDARD_F,
    ACOMPTE_CREDIT_FOURNISSEUR as ACOMPTE_CREDIT_FOURNISSEUR_F,
    DUREE_AMORTISSEMENT_MOIS as DUREE_AMORTISSEMENT_MOIS_F,
    TAUX_RENDEMENT_EPARGNE_ANNUEL as TAUX_RENDEMENT_EPARGNE_ANNUEL_F,
    NOMBRE_PARTS as NOMBRE_PARTS_F,
    TAUX_CHARGES_SOCIALES as TAUX_CHARGES_SOCIALES_F,
    TOURS_PERIODES,
)

TVA_STANDARD = Decimal(str(TVA_STANDARD_F))
ACOMPTE_CREDIT_FOURNISSEUR = Decimal(str(ACOMPTE_CREDIT_FOURNISSEUR_F))
DUREE_AMORTISSEMENT_MOIS = Decimal(str(DUREE_AMORTISSEMENT_MOIS_F))
TAUX_RENDEMENT_EPARGNE_ANNUEL = Decimal(str(TAUX_RENDEMENT_EPARGNE_ANNUEL_F))
NOMBRE_PARTS = Decimal(str(NOMBRE_PARTS_F))
TAUX_CHARGES_SOCIALES = Decimal(str(TAUX_CHARGES_SOCIALES_F))


@dataclass(frozen=True)
class FinancialState:
    periode: str
    solde_bancaire_initial: Optional[Decimal] = None
    solde_bancaire_final: Optional[Decimal] = None
    solde_epargne: Optional[Decimal] = None
    solde_epargne_ouverture: Optional[Decimal] = None
    aace: Optional[Decimal] = None
    achats_matieres: Optional[Decimal] = None
    aace_inclut_achats: bool = False
    depreciations: Optional[Decimal] = None
    dotations_amortissements: Optional[Decimal] = None
    resultat_net: Optional[Decimal] = None
    chiffre_affaires: Optional[Decimal] = None
    chiffre_affaires_cumule: Optional[Decimal] = None
    resultat_avant_impot_cumule: Optional[Decimal] = None
    capitaux_propres: Optional[Decimal] = None
    dette_bancaire: Optional[Decimal] = None
    dette_bancaire_ouverture: Optional[Decimal] = None
    remboursement_capital_mois_cible: Optional[Decimal] = None
    interets_emprunt_mois_cible: Optional[Decimal] = None
    mensualite_emprunt_mois_cible: Optional[Decimal] = None
    bfr_reel: Optional[Decimal] = None
    bfr_actifs_exploitation_reel: Optional[Decimal] = None
    bfr_passifs_exploitation_reel: Optional[Decimal] = None
    bfr_stocks_matieres_reel: Optional[Decimal] = None
    bfr_stocks_encours_reel: Optional[Decimal] = None
    bfr_stocks_produits_reel: Optional[Decimal] = None
    bfr_creances_clients_reel: Optional[Decimal] = None
    bfr_acomptes_is_reel: Optional[Decimal] = None
    bfr_tva_deductible_hors_immo_reel: Optional[Decimal] = None
    tva_collectee_reelle: Optional[Decimal] = None
    impot_benefices_reel: Optional[Decimal] = None
    bfr_dettes_fournisseurs_reel: Optional[Decimal] = None
    bfr_dettes_fiscales_sociales_reel: Optional[Decimal] = None
    variation_bfr_reelle: Optional[Decimal] = None
    variation_bfr_cashflow_reelle: Optional[Decimal] = None
    flux_treso_exploitation_reel: Optional[Decimal] = None
    flux_treso_investissement_reel: Optional[Decimal] = None
    flux_treso_financement_reel: Optional[Decimal] = None
    flux_treso_net_reel: Optional[Decimal] = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BFRComponents:
    """Composantes prévisionnelles du BFR Subakoua.

    Les actifs sont positifs ; les passifs sont positifs et sont soustraits
    dans le calcul. Les postes fiscaux/en-cours restent optionnels tant que
    leur règle de projection n'est pas explicitement fournie par Subakoua.
    """
    stocks_matieres: Decimal = Decimal("0")
    stocks_encours: Decimal = Decimal("0")
    stocks_produits: Decimal = Decimal("0")
    creances_clients: Decimal = Decimal("0")
    acomptes_is: Decimal = Decimal("0")
    tva_deductible_hors_immo: Decimal = Decimal("0")
    dettes_fournisseurs: Decimal = Decimal("0")
    dettes_fiscales_sociales: Decimal = Decimal("0")

    @property
    def actifs_exploitation(self) -> Decimal:
        return money(
            self.stocks_matieres + self.stocks_encours + self.stocks_produits +
            self.creances_clients + self.acomptes_is + self.tva_deductible_hors_immo
        )

    @property
    def passifs_exploitation(self) -> Decimal:
        return money(self.dettes_fournisseurs + self.dettes_fiscales_sociales)

    @property
    def bfr(self) -> Decimal:
        return money(self.actifs_exploitation - self.passifs_exploitation)


@dataclass(frozen=True)
class BFRProjectionResult:
    components: BFRComponents
    bfr: Decimal
    variation_vs_m1: Optional[Decimal]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CashScheduleProjection:
    """Projection opérationnelle des encaissements clients / règlements fournisseurs.

    Les montants de créances et dettes sont TTC, conformément aux états Subakoua
    observés dans le dump : le poste Clients correspond au CA TTC et les dettes
    fournisseurs aux achats TTC.
    """
    encaissements_clients: Decimal
    creances_clients_cloture: Decimal
    reglements_fournisseurs: Decimal
    dettes_fournisseurs_cloture: Decimal
    client_buckets_cloture: tuple[Decimal, Decimal, Decimal]
    fournisseur_buckets_cloture: tuple[Decimal, Decimal, Decimal]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class VATProjectionResult:
    """Projection mensuelle de TVA conforme à la mécanique observée dans Subakoua.

    La TVA collectée du mois M est générée par le CA HT de M.
    La TVA déductible est fournie séparément car le dump ne permet pas de
    déduire de manière sûre quelles charges sont intégralement récupérables.
    La TVA nette de M est normalement décaissée en M+1 : le règlement du mois
    courant correspond donc à la TVA nette exigible à l'ouverture.
    """
    tva_collectee_mois: Decimal
    tva_deductible_hors_immo_mois: Decimal
    tva_deductible_immobilisations_mois: Decimal
    tva_nette_mois: Decimal
    tva_a_payer_ce_mois: Decimal
    tva_credit_ce_mois: Decimal
    tva_collectee_cloture: Decimal
    tva_deductible_hors_immo_cloture: Decimal
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FiscalSocialProjectionResult:
    """Projection du passif fiscal/social non lié à la TVA.

    Le moteur conserve explicitement la dette d'ouverture et distingue
    l'accroissement de dette du paiement de trésorerie. Aucune règle de
    calendrier social n'est inventée : le paiement attendu est un paramètre.
    """
    dette_ouverture: Decimal
    nouvelles_charges: Decimal
    paiements: Decimal
    dette_cloture: Decimal
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineInvestmentResult:
    cout_ht: Decimal
    cout_ttc: Decimal
    decaissement_ttc: Decimal
    dette_fournisseur: Decimal


@dataclass(frozen=True)
class DebtProjectionResult:
    dette_ouverture: Decimal
    nouveaux_emprunts: Decimal
    remboursement_capital: Decimal
    interets: Decimal
    mensualite: Decimal
    dette_cloture: Decimal
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinancialProjection:
    total_charges: Decimal
    resultat_financier: Decimal
    resultat_avant_impot: Decimal
    impot_is: Decimal
    resultat_net: Decimal
    dotations_totales: Decimal
    caf: Decimal
    bfr_projection: Optional[Decimal]
    variation_bfr: Optional[Decimal]
    flux_exploitation: Decimal
    flux_investissement: Decimal
    flux_financement: Decimal
    tresorerie_initiale: Decimal
    tresorerie_finale: Decimal
    solde_epargne_initial: Decimal
    solde_epargne_final: Decimal
    dette_bancaire_ouverture: Decimal
    dette_bancaire_finale: Decimal
    investissement_ttc: Decimal
    acompte_ttc: Decimal
    dette_fournisseur_investissement: Decimal
    interets_emprunt: Decimal
    remboursement_capital: Decimal
    tva_collectee: Decimal = Decimal("0")
    tva_deductible: Decimal = Decimal("0")
    tva_nette: Decimal = Decimal("0")
    tva_payee_ce_mois: Decimal = Decimal("0")
    dette_fiscale_sociale_cloture: Decimal = Decimal("0")
    warnings: tuple[str, ...] = ()


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        text = str(value).strip().replace("\u202f", "").replace("\xa0", "").replace("€", "")
        if text == "" or text == "-":
            return default
        # Les exports Subakoua peuvent employer la virgule comme séparateur décimal.
        text = text.replace(" ", "").replace(",", ".")
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return default


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def period_for_tour(tour_id: int) -> str:
    return TOURS_PERIODES.get(int(tour_id), TOURS_PERIODES[max(TOURS_PERIODES)])


def target_schedule_code(tour_id: int) -> int:
    """Code de la colonne/ligne 'Mois / Année' de Subakoua.

    Le dump utilise 101, 201, ..., 1201 pour janvier, février, ...
    de l'année 1, puis 102, 202, ... pour l'année 2.
    """
    tour_id = int(tour_id)
    if not 1 <= tour_id <= 15:
        raise ValueError(f"Tour_ID non supporté: {tour_id}")
    if tour_id <= 12:
        month = tour_id
        year = 1
    else:
        month = tour_id - 12
        year = 2
    return month * 100 + year


def _norm_key(value: Any) -> str:
    """Clé normalisée : sans accent, minuscule, espaces/tirets/underscores écrasés."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s_\-]+", " ", text).strip().casefold()


def _walk_path(node: Any, path: Sequence[str], accept) -> Any:
    """Résolution tolérante d'un chemin dans un JSON scrapé.

    Tolère : casse/accents/espaces différents, niveaux EN TROP dans les données, et UN niveau
    manquant (le scraper n'ajoute le niveau « onglet » que si la page a un menu d'onglets).
    Garde-fous : le saut d'un niveau manquant n'est autorisé que pour un niveau situé avant
    l'avant-dernier élément du chemin, afin de ne jamais lire le « Tableau_1 » d'un autre document.
    """
    wanted = [_norm_key(p) for p in path]
    last_skippable = len(wanted) - 3

    def dfs(current: Any, idx: int, skips: int) -> Any:
        if idx == len(wanted):
            return current if accept(current) else None
        if not isinstance(current, Mapping):
            return None
        for key, child in current.items():
            if _norm_key(key) == wanted[idx]:
                found = dfs(child, idx + 1, skips)
                if found is not None:
                    return found
        for child in current.values():
            if isinstance(child, Mapping):
                found = dfs(child, idx, skips)
                if found is not None:
                    return found
        if skips < 1 and idx <= last_skippable:
            return dfs(current, idx + 1, skips + 1)
        return None

    return dfs(node, 0, 0)


def _rows_at(data: Mapping[str, Any], path: Sequence[str]) -> list[Mapping[str, Any]]:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping):
            node = None
            break
        node = node.get(key)
    if isinstance(node, list) and node:
        return node
    found = _walk_path(data, path, lambda n: isinstance(n, list) and bool(n))
    return found if isinstance(found, list) else []


def _mapping_at(data: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any]:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping):
            node = None
            break
        node = node.get(key)
    if isinstance(node, Mapping) and node:
        return node
    found = _walk_path(data, path, lambda n: isinstance(n, Mapping) and bool(n))
    return found if isinstance(found, Mapping) else {}


def _first_row_value(rows: Iterable[Mapping[str, Any]], key: str, default: Any = None) -> Any:
    for row in rows:
        if key in row:
            return row[key]
    return default


def _find_balance_mapping(node: Any, depth: int = 0) -> Optional[Mapping[str, Any]]:
    """Dernier recours : premier dict portant « Solde initial » ou « Solde final », où qu'il soit."""
    if depth > 12:
        return None
    if isinstance(node, Mapping):
        keys = {_norm_key(k) for k in node.keys()}
        if keys & {"solde initial", "solde final"}:
            return node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_balance_mapping(child, depth + 1)
        if found is not None:
            return found
    return None


def _balance_value(row: Mapping[str, Any], label: str) -> Optional[Decimal]:
    for key, value in row.items():
        if _norm_key(key) == label:
            return to_decimal(value)
    return None


# =============================================================
# SOURCES « ÉTUDES ACHETÉES » (erp_etudes)
# -------------------------------------------------------------
# Les études achetées sur Subakoua sont des payloads d'API plats, indexés par
# code PCG (« mp310 », « cl411 », « detfr401 », « po215 »…). Elles constituent
# le dossier comptable complet de l'entreprise : elles sont donc lues ici comme
# une source native, au même titre que le dump historique, et non comme un
# simple complément. Chaque poste reste rattaché à la clé qui le porte dans
# l'étude : aucune valeur n'est inventée, aucune constante n'est injectée.
# =============================================================

STUDY_BILAN_DETAILLE = "peeedsbidt"
STUDY_BILAN_INITIAL = "initialBalanceSheet"
STUDY_RESULTAT_DETAILLE = "peeedscrdl"
STUDY_RESULTAT_SIMPLIFIE = "peeedscrsm"
STUDY_TRESORERIE = "peeedsftt"
STUDY_IFRS = "ifrsStatementOfCashFlow"
STUDY_BANQUE = "bankStatements"
STUDY_EMPRUNT = "pebaemecta"
STUDY_ACHATS = "peeedcahm"
STUDY_CHARGES_EXTERNES = "peeedcahsx"
STUDY_DEPRECIATIONS = "pcgToIfrsDepreciation"
STUDY_RESULTATS_ENTREPRISE = "companyResults"
STUDY_IMPOT_SOCIETES = "peeefsisp"
STUDY_TVA = "peeefstvp"


def _pick_path(node: Any, path: Sequence[str]) -> Any:
    """Valeur au chemin fourni, ou None. Les nœuds « {value, display} » sont aplatis."""
    current = node
    for part in path:
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    if isinstance(current, Mapping) and "value" in current and "display" in current:
        return current["value"]
    return current


def study_payload(period_data: Any, *study_ids: str) -> Mapping[str, Any]:
    """Payload brut d'une étude achetée, retrouvée par son identifiant `studyId`."""
    if not isinstance(period_data, Mapping) or not study_ids:
        return {}
    for study_id in study_ids:
        node = period_data.get(f"etude_{study_id}")
        if isinstance(node, Mapping) and node:
            return node
    wanted = {s.casefold() for s in study_ids}
    for key, node in period_data.items():
        if not str(key).startswith("etude_") or not isinstance(node, Mapping):
            continue
        found = _pick_path(node, ("studyId",))
        if isinstance(found, str) and found.casefold() in wanted:
            return node
    return {}


def _study_num(node: Any, *paths: Sequence[str]) -> Optional[Decimal]:
    """Premier montant chiffré trouvé parmi les chemins proposés."""
    for path in paths:
        value = _pick_path(node, path)
        if value is None or isinstance(value, bool):
            continue
        parsed = to_decimal(value)
        if parsed is not None:
            return parsed
    return None


def _study_sum(node: Any, *paths: Sequence[str]) -> Optional[Decimal]:
    """Somme des montants trouvés ; None si aucun n'est lisible."""
    total = Decimal("0")
    found = False
    for path in paths:
        value = _study_num(node, path)
        if value is not None:
            total += value
            found = True
    return money(total) if found else None


def _income_detail(period_data: Any) -> Mapping[str, Any]:
    """Bloc « compte de résultat » de l'exercice courant dans les études achetées."""
    for study_id in (STUDY_RESULTAT_DETAILLE, STUDY_RESULTAT_SIMPLIFIE):
        node = study_payload(period_data, study_id)
        if not node:
            continue
        detail = _pick_path(node, ("incomeStatementDetailed",))
        return detail if isinstance(detail, Mapping) and detail else node
    return {}


def extract_bank_balances(period_data: Mapping[str, Any]) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Soldes bancaires initial / final depuis Banque et assurance → Extrait bancaire.

    Chemin nominal d'abord, puis résolution tolérante (onglet absent, accents, casse),
    puis recherche de n'importe quel bloc contenant « Solde initial » / « Solde final »
    dans le module Banque.
    """
    rows = _rows_at(period_data, ("etat_actuel", "banque_assurance", "Banque", "Extrait bancaire", "Soldes bancaires"))
    row: Optional[Mapping[str, Any]] = rows[0] if rows else None
    if row is None or (_balance_value(row, "solde initial") is None and _balance_value(row, "solde final") is None):
        banque = _mapping_at(period_data, ("etat_actuel", "banque_assurance"))
        extrait = _walk_path(banque, ("Extrait bancaire",), lambda n: isinstance(n, (Mapping, list))) if banque else None
        row = _find_balance_mapping(extrait) if extrait is not None else None
        if row is None and banque:
            row = _find_balance_mapping(banque)
    if row is not None:
        initial, final = _balance_value(row, "solde initial"), _balance_value(row, "solde final")
        if initial is not None or final is not None:
            return initial, final
    # Étude « Extrait bancaire » achetée : soldes d'ouverture et de clôture.
    releve = study_payload(period_data, STUDY_BANQUE)
    if releve:
        return (
            _study_num(releve, ("initialBankAmount",), ("initialBank",)),
            _study_num(releve, ("finalBankAmount",), ("finalBank",)),
        )
    return None, None


def extract_savings_balance(period_data: Mapping[str, Any]) -> Optional[Decimal]:
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    for row in rows:
        if str(row.get("Actif", "")).strip().lower() == "compte épargne":
            return to_decimal(row.get("Net exercice N", row.get("Brut")))
    # Étude « Bilan version détaillée » : poste 215 « placements sur livret ».
    bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
    if bilan:
        return _study_num(bilan, ("po215Net",), ("po215",))
    # Bilan d'ouverture : l'épargne y porte son propre agrégat.
    initial = study_payload(period_data, STUDY_BILAN_INITIAL)
    if initial:
        return _study_num(
            initial,
            ("otherCurrentAssets.savings.amountAfterDepreciation",),
            ("currentAsset.savings.amountAfterDepreciation",),
            ("po215Net",),
            ("po215",),
        )
    return None


def extract_savings_opening_balance(period_data: Mapping[str, Any], final_balance: Optional[Decimal] = None) -> Optional[Decimal]:
    """Reconstruit le solde du compte épargne à l'ouverture de la période.

    Le bilan acheté publie directement le poste 215 de l'exercice précédent,
    qui est donc l'ouverture exacte. À défaut, le tableau de trésorerie
    détaillé sert de repli : un placement de -500000 € côté banque correspond
    donc à +500000 € côté épargne. Les revenus financiers sont volontairement
    exclus de la reconstruction du stock d'épargne : ils apparaissent comme
    produit/flux distinct dans Subakoua.
    """
    final_value = final_balance if final_balance is not None else extract_savings_balance(period_data)
    if final_value is None:
        return None
    bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
    if bilan:
        opening = _study_num(bilan, ("po215NetPrec",), ("po215Prec",))
        if opening is not None:
            return money(opening)
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Tableau de trésorerie méthode indirecte", "Tableau_1"))
    placement_bank = Decimal("0")
    retrait_bank = Decimal("0")
    found = False
    for row in rows:
        label = str(row.get("Colonne_0", "")).strip().lower()
        if label == "placement sur compte épargne":
            value = to_decimal(row.get("Colonne_5"))
            if value is not None:
                placement_bank = value
                found = True
        elif label == "retrait du compte épargne":
            value = to_decimal(row.get("Colonne_5"))
            if value is not None:
                retrait_bank = value
                found = True
    if not found:
        # Mouvements d'épargne observés sur le relevé bancaire acheté.
        releve = study_payload(period_data, STUDY_BANQUE)
        if releve:
            placement = _study_num(releve, ("savingsAccountInvestments",))
            retrait = _study_num(releve, ("savingsAccountWithdrawal",))
            if placement is not None or retrait is not None:
                return money(
                    final_value
                    + (placement or Decimal("0"))
                    - (retrait or Decimal("0"))
                )
        return None
    return money(final_value + placement_bank - retrait_bank)


def extract_debt_balance(period_data: Mapping[str, Any]) -> Optional[Decimal]:
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    total = Decimal("0")
    found = False
    for row in rows:
        passif = str(row.get("Passif", "")).strip().lower()
        if "emprunts mensualisés contractés" in passif or "emprunts libres" in passif:
            value = to_decimal(row.get("Exercice N"))
            if value is not None:
                total += value
                found = True
    if found:
        return money(total)
    # Étude « Bilan version détaillée » : 16 « emprunts et dettes assimilées ».
    bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
    if bilan:
        return _study_sum(bilan, ("empr16",), ("emprLibre",))
    return None


def extract_tax_liabilities(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """Extrait les dettes fiscales directement du bilan / de l'étude IS.

    Ces montants sont des passifs de clôture, distincts des flux de règlement
    observés sur le relevé bancaire.
    """
    out: dict[str, Optional[Decimal]] = {"tva_collectee": None, "impot_benefices": None}
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    for row in rows:
        label = str(row.get("Passif", "")).strip().lower()
        value = to_decimal(row.get("Exercice N", row.get("Montant")))
        if label == "tva collectée":
            out["tva_collectee"] = value
        elif label == "impôt sur les bénéfices":
            out["impot_benefices"] = value
    if out["tva_collectee"] is None:
        bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
        if bilan:
            # 4456 = TVA collectée (sortie du cash-flow Subakoua).
            out["tva_collectee"] = _study_num(bilan, ("tva4456",))
    if out["tva_collectee"] is None:
        tva = study_payload(period_data, STUDY_TVA)
        if tva:
            out["tva_collectee"] = _study_num(tva, ("totalVatOutput",))
    if out["impot_benefices"] is None:
        impot = study_payload(period_data, STUDY_IMPOT_SOCIETES)
        if impot:
            out["impot_benefices"] = _study_num(impot, ("isdu444",), ("profitLoss",))
    if out["impot_benefices"] is None:
        bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
        if bilan:
            out["impot_benefices"] = _study_num(bilan, ("isdu444",))
    return out


def calculate_vat_projection(
    *,
    chiffre_affaires_ht: Any,
    tva_deductible_hors_immo_mois: Any,
    tva_deductible_immobilisations_mois: Any = 0,
    tva_nette_ouverture_a_payer: Any = 0,
) -> VATProjectionResult:
    """Calcule la TVA du mois et le règlement de TVA du mois.

    Règle observée dans les données fournies : la TVA nette du mois M est
    payée sur le mois M+1 (ex. TVA de janvier = 64 917,11 € payée en février).
    Une TVA nette négative devient un crédit de TVA ; aucun remboursement futur
    n'est supposé automatiquement.
    """
    ca = to_decimal(chiffre_affaires_ht, Decimal("0")) or Decimal("0")
    deductible = to_decimal(tva_deductible_hors_immo_mois, Decimal("0")) or Decimal("0")
    deductible_immo = to_decimal(tva_deductible_immobilisations_mois, Decimal("0")) or Decimal("0")
    ouverture = to_decimal(tva_nette_ouverture_a_payer, Decimal("0")) or Decimal("0")
    collectee = money(max(Decimal("0"), ca) * TVA_STANDARD)
    # La TVA sur immobilisations est décaissée avec l'investissement et reste
    # donc hors du cycle mensuel de TVA d'exploitation. Le dump Subakoua la
    # présente séparément dans les flux d'investissement.
    if deductible < 0:
        deductible = Decimal("0")
    if deductible_immo < 0:
        deductible_immo = Decimal("0")
    net = money(collectee - deductible)
    paiement = money(max(Decimal("0"), ouverture))
    credit = money(max(Decimal("0"), -ouverture))
    warnings: list[str] = []
    if deductible < 0 or deductible_immo < 0:
        warnings.append("TVA déductible négative détectée : valeur ramenée à zéro.")
    return VATProjectionResult(
        tva_collectee_mois=collectee,
        tva_deductible_hors_immo_mois=money(deductible),
        tva_deductible_immobilisations_mois=money(deductible_immo),
        tva_nette_mois=money(net),
        tva_a_payer_ce_mois=paiement,
        tva_credit_ce_mois=credit,
        tva_collectee_cloture=collectee,
        tva_deductible_hors_immo_cloture=money(max(Decimal("0"), deductible)),
        warnings=tuple(warnings),
    )


def calculate_fiscal_social_projection(
    *,
    dette_ouverture: Any,
    nouvelles_charges: Any,
    paiements: Any,
) -> FiscalSocialProjectionResult:
    opening = to_decimal(dette_ouverture, Decimal("0")) or Decimal("0")
    charges = to_decimal(nouvelles_charges, Decimal("0")) or Decimal("0")
    payments = to_decimal(paiements, Decimal("0")) or Decimal("0")
    warnings: list[str] = []
    if opening < 0:
        warnings.append("Dette fiscale/sociale d'ouverture négative : valeur ramenée à zéro.")
        opening = Decimal("0")
    if charges < 0:
        warnings.append("Nouvelles charges fiscales/sociales négatives : valeur ramenée à zéro.")
        charges = Decimal("0")
    if payments < 0:
        warnings.append("Paiements fiscaux/sociaux négatifs : valeur ramenée à zéro.")
        payments = Decimal("0")
    closing = max(Decimal("0"), money(opening + charges - payments))
    return FiscalSocialProjectionResult(money(opening), money(charges), money(payments), closing, tuple(warnings))


def extract_capitaux_propres(period_data: Mapping[str, Any]) -> Optional[Decimal]:
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    for row in rows:
        if str(row.get("Passif", "")).strip().lower() == "total capitaux propres":
            return to_decimal(row.get("Exercice N"))
    bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
    if bilan:
        return _study_num(bilan, ("totalCapitauxPropres",))
    resultats = study_payload(period_data, STUDY_RESULTATS_ENTREPRISE)
    if resultats:
        return _study_num(resultats, ("equity",), ("cumEquity",))
    return None


def extract_client_receivable_schedule(period_data: Mapping[str, Any], period: str) -> dict[int, Decimal]:
    """Extrait l'échéancier des créances clients à la clôture d'une période.

    Les lignes Subakoua sont exprimées depuis la clôture :
      bucket 1 = payable dans un mois, bucket 2 = dans deux mois, etc.
    """
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Détail des comptes", "Créances clients", "Tableau_1"))
    labels = {
        "Payables dans un mois": 1,
        "Payables dans deux mois": 2,
        "Payables dans trois mois": 3,
    }
    out = {1: Decimal("0"), 2: Decimal("0"), 3: Decimal("0")}
    found = False
    for row in rows:
        label = str(row.get("Colonne_0", "")).strip()
        if label in labels:
            value = to_decimal(row.get(period))
            if value is not None:
                out[labels[label]] = money(value)
                found = True
    return out if found else {}


def infer_client_payment_mix(
    period_data: Mapping[str, Any],
    period: str,
    chiffre_affaires_net: Optional[Any],
) -> dict[int, Decimal]:
    """Infère la répartition comptant / 1 / 2 / 3 mois depuis M-1.

    Le dump montre que Clients = CA TTC et que les créances de fin de mois
    sont ventilées dans les buckets d'échéance. Lorsque le CA historique est
    connu, la part comptant est donc le reliquat entre CA TTC et les créances
    à terme. Aucun ratio arbitraire n'est injecté.
    """
    buckets = extract_client_receivable_schedule(period_data, period)
    ca_net = to_decimal(chiffre_affaires_net)
    if not buckets or ca_net is None or ca_net < 0:
        return {0: Decimal("0"), 1: Decimal("1"), 2: Decimal("0"), 3: Decimal("0")}

    ca_ttc = money(ca_net * (Decimal("1") + TVA_STANDARD))
    if ca_ttc <= 0:
        return {0: Decimal("1"), 1: Decimal("0"), 2: Decimal("0"), 3: Decimal("0")}
    terme = sum(buckets.values(), Decimal("0"))
    shares = {1: buckets[1] / ca_ttc, 2: buckets[2] / ca_ttc, 3: buckets[3] / ca_ttc}
    comptant = max(Decimal("0"), Decimal("1") - terme / ca_ttc)
    total = comptant + shares[1] + shares[2] + shares[3]
    if total <= 0:
        return {0: Decimal("0"), 1: Decimal("1"), 2: Decimal("0"), 3: Decimal("0")}
    # Recalage de sécurité sur 1.000000 sans changer la hiérarchie des buckets.
    return {k: (v / total) for k, v in [(0, comptant), (1, shares[1]), (2, shares[2]), (3, shares[3])]}


def project_operating_cash_schedule(
    *,
    chiffre_affaires_net: Any,
    achats_ht_by_lag: Mapping[int, Any],
    client_payment_mix: Mapping[int, Any],
    previous_client_buckets: Mapping[int, Any],
    previous_supplier_payables: Any,
) -> CashScheduleProjection:
    """Projette les encaissements clients et dettes fournisseurs du mois cible.

    Les flux ne sont PAS ajoutés une seconde fois à la trésorerie : ils servent
    à déterminer les soldes de créances/dettes qui alimentent le ΔBFR.
    """
    warnings: list[str] = []
    ca_net = to_decimal(chiffre_affaires_net, Decimal("0")) or Decimal("0")
    ca_ttc = money(ca_net * (Decimal("1") + TVA_STANDARD))

    mix = {i: to_decimal(client_payment_mix.get(i), Decimal("0")) or Decimal("0") for i in range(4)}
    mix_total = sum(mix.values(), Decimal("0"))
    if abs(mix_total - Decimal("1")) > Decimal("0.0001"):
        warnings.append("Répartition des règlements clients invalide : utilisation du profil M-1 100% à 1 mois.")
        mix = {0: Decimal("0"), 1: Decimal("1"), 2: Decimal("0"), 3: Decimal("0")}

    prior_c1 = to_decimal(previous_client_buckets.get(1), Decimal("0")) or Decimal("0")
    prior_c2 = to_decimal(previous_client_buckets.get(2), Decimal("0")) or Decimal("0")
    prior_c3 = to_decimal(previous_client_buckets.get(3), Decimal("0")) or Decimal("0")

    encaissements_clients = money(prior_c1 + ca_ttc * mix[0])
    # À la clôture : les anciennes échéances 2/3 mois avancent d'un cran.
    client_b1 = money(prior_c2 + ca_ttc * mix[1])
    client_b2 = money(prior_c3 + ca_ttc * mix[2])
    client_b3 = money(ca_ttc * mix[3])
    creances_clients = money(client_b1 + client_b2 + client_b3)

    achats_ttc_by_lag = {
        lag: money((to_decimal(amount, Decimal("0")) or Decimal("0")) * (Decimal("1") + TVA_STANDARD))
        for lag, amount in achats_ht_by_lag.items()
    }
    for lag in range(4):
        achats_ttc_by_lag.setdefault(lag, Decimal("0"))

    # Le dump ne fournit pas un échéancier fournisseurs aussi explicite que clients.
    # On considère donc la dette M-1 comme payable ce mois, sans l'ajouter au BFR
    # de clôture ; les achats du mois créent les nouvelles dettes selon leur délai.
    previous_ap = to_decimal(previous_supplier_payables, Decimal("0")) or Decimal("0")
    reglements_fournisseurs = money(previous_ap + achats_ttc_by_lag[0])
    supplier_b1 = achats_ttc_by_lag[1]
    supplier_b2 = achats_ttc_by_lag[2]
    supplier_b3 = achats_ttc_by_lag[3]
    dettes_fournisseurs = money(supplier_b1 + supplier_b2 + supplier_b3)
    if previous_ap > 0:
        warnings.append("Échéancier fournisseurs historique non ventilé : la dette M-1 est supposée exigible sur le mois cible.")

    return CashScheduleProjection(
        encaissements_clients=encaissements_clients,
        creances_clients_cloture=creances_clients,
        reglements_fournisseurs=reglements_fournisseurs,
        dettes_fournisseurs_cloture=dettes_fournisseurs,
        client_buckets_cloture=(client_b1, client_b2, client_b3),
        fournisseur_buckets_cloture=(supplier_b1, supplier_b2, supplier_b3),
        warnings=tuple(warnings),
    )


def extract_income_statement(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Compte de résultat détaillé", "Tableau_1"))
    if not rows:
        rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Compte de résultat simplifié", "Tableau_1"))

    result: dict[str, Optional[Decimal]] = {
        "aace": None,
        "achats_matieres": None,
        "depreciations": None,
        "dotations_amortissements": None,
        "resultat_net": None,
    }

    aliases = {
        "aace": "autres achats et charges externes",
        "depreciations": "dotations aux dépréciations",
        "dotations_amortissements": "dotations aux amortissements",
    }
    for row in rows:
        label = str(row.get("Colonne_0", "")).strip().lower()
        value = to_decimal(row.get("Colonne_1", row.get("Montants (€)")))
        for field, needle in aliases.items():
            if label == needle:
                result[field] = value
        if label.startswith("résultat net comptable"):
            result["resultat_net"] = value

    if any(value is None for value in result.values()):
        _complete_income_statement_from_studies(period_data, result)
    # Les AACE du compte de résultat Subakoua englobent les achats de matières
    # (classes 60/61) ; le libellé du dump historique, lui, ne couvre que les
    # charges externes. On le mémorise pour que la projection puisse trancher.
    result["aace_inclut_achats"] = _aace_inclut_achats(period_data, result)
    return result


def _aace_inclut_achats(period_data: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    """Les AACE lues contiennent-elles déjà les achats de matières ?

    Vrai si elles viennent du compte de résultat des études achetées (où le
    total des charges d'exploitation additionne achats, variation de stocks et
    services extérieurs). Faux si elles viennent du libellé « autres achats et
    charges externes » du dump, qui ne porte que la classe 62.
    """
    detail = _income_detail(period_data)
    if not detail:
        return False
    return _study_sum(detail, ("materialsPurchases",), ("productsPurchases",)) is not None


def _complete_income_statement_from_studies(
    period_data: Mapping[str, Any],
    result: dict[str, Optional[Decimal]],
) -> None:
    """Complète le compte de résultat depuis les études comptables achetées.

    AACE = achats de matières + achats de produits + variation des stocks de
    matières + services extérieurs + autres charges d'exploitation. Les Studies
    « Achats et variations de stocks » et « Autres achats et charges externes »
    publient les mêmes postes poste par poste ; on ne les additionne donc pas
    deux fois : le compte de résultat reste la source, les Studies détaillées
    ne servent que de repli poste par poste.
    """
    detail = _income_detail(period_data)
    if detail:
        # Les champs « productsInStock » et « materialsStockVariation » ne sont
        # pas des achats : seule la variation de stock entre dans les AACE.
        if result["aace"] is None:
            result["aace"] = _study_sum(
                detail,
                ("materialsPurchases",),
                ("productsPurchases",),
                ("materialsStockVariation",),
                ("externalServicesCharges",),
                ("otherExploitationCharges",),
            )
        if result["depreciations"] is None:
            result["depreciations"] = _study_num(detail, ("depreciationProvision",), ("dotationAmortization",))
        if result["achats_matieres"] is None:
            # Achats de matières (classes 60/61) : à distinguer des services
            # extérieurs, qui ne relèvent pas du plan d'approvisionnement.
            result["achats_matieres"] = _study_sum(detail, ("materialsPurchases",), ("productsPurchases",))
        if result["dotations_amortissements"] is None:
            result["dotations_amortissements"] = _study_num(detail, ("amortization",))
        if result["resultat_net"] is None:
            result["resultat_net"] = _study_num(detail, ("netResult",), ("totalNetResult",))

    if result["aace"] is None:
        achats = study_payload(period_data, STUDY_ACHATS)
        charges = study_payload(period_data, STUDY_CHARGES_EXTERNES)
        total_achats = _study_num(achats, ("totalTotalAchats",))
        variation_stocks = _study_num(achats, ("totalMp603",))
        if total_achats is None and variation_stocks is None:
            variation_stocks = _study_num(charges, ("variationDueToProductionServiceQuality",))
        total_externes = _study_num(charges, ("totChargesExternes",))
        parts = [v for v in (total_achats, variation_stocks, total_externes) if v is not None]
        if parts:
            result["aace"] = money(sum(parts, Decimal("0")))

    # Repli poste par poste : l'etude « Achats et variations de stocks »
    # publie le total d'achats meme quand le compte de resultat est absent.
    if result["achats_matieres"] is None:
        achats = study_payload(period_data, STUDY_ACHATS)
        result["achats_matieres"] = _study_num(achats, ("totalTotalAchats",))

    if result["depreciations"] is None or result["dotations_amortissements"] is None:
        dotations = study_payload(period_data, STUDY_DEPRECIATIONS)
        if dotations:
            if result["depreciations"] is None:
                result["depreciations"] = _study_num(
                    dotations,
                    ("pcgToIfrsAdministrationExpensesDepreciationCharges.depreciationCharges",),
                    ("pcgToIfrsCostOfSalesDepreciationCharges.stockedMaterialsDepreciation.objectsDepreciationCharges",),
                )
            if result["dotations_amortissements"] is None:
                result["dotations_amortissements"] = _study_num(
                    dotations,
                    ("pcgToIfrsCostOfSalesDepreciation.costOfSalesTotalDepreciation",),
                )

    if result["resultat_net"] is None:
        resultats = study_payload(period_data, STUDY_RESULTATS_ENTREPRISE)
        if resultats:
            result["resultat_net"] = _study_num(resultats, ("results",))


def extract_ratios(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    ratios = _mapping_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Ratios", "Ratios et Indicateurs"))
    monthly = ratios.get("Indicateurs_MENSUEL")
    cumulative = ratios.get("Indicateurs_CUMULÉ", ratios.get("Indicateurs_CUMULE"))
    out: dict[str, Optional[Decimal]] = {}
    for prefix, rows in (("mensuel", monthly), ("cumule", cumulative)):
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            out[f"{prefix}_ca"] = to_decimal(rows[0].get("Chiffre d'affaires"))
            out[f"{prefix}_resultat_avant_impot"] = to_decimal(rows[0].get("Résultat avant impôt"))
    if out.get("mensuel_ca") is not None and out.get("mensuel_resultat_avant_impot") is not None:
        return out
    out.update({k: v for k, v in _ratios_from_studies(period_data).items() if out.get(k) is None})
    return out


def _ratios_from_studies(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """CA et résultat avant impôt mensuel / cumulé depuis les études achetées.

    L'étude « Résultats » (tableau de bord entreprise) publie le CA, le résultat
    net et leurs cumulés annuels ; le compte de résultat publie le résultat avant
    impôt. On lit chaque grandeur à sa source plutôt que de la reconstituer.
    """
    detail = _income_detail(period_data)
    dashboard = study_payload(period_data, STUDY_RESULTATS_ENTREPRISE)
    out: dict[str, Optional[Decimal]] = {}
    if detail:
        out["mensuel_ca"] = _study_num(detail, ("netTurnover",), ("competitiveMarketTurnover",), ("turnover",))
        out["mensuel_resultat_avant_impot"] = _study_num(detail, ("totalResultBeforeTaxes",))
    if dashboard:
        if out.get("mensuel_ca") is None:
            out["mensuel_ca"] = _study_num(dashboard, ("turnover",))
        out["cumule_ca"] = _study_num(dashboard, ("cumTurnover",))
        out["cumule_resultat_avant_impot"] = _study_num(dashboard, ("cumResults",))
    return {k: v for k, v in out.items() if v is not None}



def extract_detailed_bfr(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """Extrait le BFR tel que construit par le tableau de trésorerie Subakoua.

    Le moteur officiel distingue :
      Actifs d'exploitation circulants
        = stocks matières + en-cours + stocks produits + clients
          + acomptes IS + TVA déductible hors immobilisations
      Passifs d'exploitation circulants
        = fournisseurs + dettes fiscales/sociales hors immobilisations

    Dans le tableau détaillé :
      Colonne_5 de "Variation du BFR et flux attaché" = ΔBFR comptable
      Colonne_6                             = impact cash (= -ΔBFR)
    """
    rows = _rows_at(
        period_data,
        ("etat_actuel", "expert_comptable", "Synthèse", "Tableau de trésorerie méthode indirecte", "Tableau_1"),
    )
    if not rows:
        return _detailed_bfr_from_studies(period_data)

    current_assets = current_liabilities = variation_comptable = variation_cash = None
    components: dict[str, Optional[Decimal]] = {}
    for row in rows:
        label = str(row.get("Colonne_0", "")).strip()
        if label == "Actifs d'exploitation circulants":
            current_assets = to_decimal(row.get("Colonne_2"))
        elif label == "Passifs d'exploitation circulants":
            current_liabilities = to_decimal(row.get("Colonne_2"))
        elif label == "Variation du BFR et flux attaché":
            variation_comptable = to_decimal(row.get("Colonne_5"))
            variation_cash = to_decimal(row.get("Colonne_6"))
        elif label in {
            "Stocks matières", "Stocks d'en-cours", "Stocks de produits", "Clients",
            "Acomptes sur IS", "Etat, TVA déductible (hors immobilisations)",
            "Fournisseurs", "Dettes fiscales et sociales (hors immobilisations)",
        }:
            # Pour les actifs, Colonne_2 représente la valeur de clôture.
            # Pour les passifs, la valeur de clôture est stockée négativement.
            components[label] = to_decimal(row.get("Colonne_2"))

    if current_assets is None or current_liabilities is None:
        return {}

    bfr = money(current_assets + current_liabilities)
    return {
        "bfr": bfr,
        "actifs": money(current_assets),
        "passifs": money(current_liabilities),
        "variation_comptable": money(variation_comptable) if variation_comptable is not None else None,
        "variation_cash": money(variation_cash) if variation_cash is not None else None,
        **components,
    }


def _detailed_bfr_from_studies(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """BFR détaillé reconstruit depuis le tableau de trésorerie et le bilan achetés.

    Les Studies « Expert-comptable » publient déjà les deux agrégats du BFR
    (actifs / passifs d'exploitation circulants) et sa variation. On ne les
    recalcule donc pas : on les lit tels quels, et on n'en dérive les postes
    élémentaires que si le tableau ne les publie pas — auquel cas on préfère
    le bilan détaillé, qui porte exactement les mêmes postes sous forme de
    soldes de clôture.

    Convention de signe Subakoua : les dotations d'actifs circulants sont
    positives, les dotations de passifs circulants sont négatives.
    """
    tresorerie = study_payload(period_data, STUDY_TRESORERIE)
    if not tresorerie:
        return {}

    aktifs = _study_num(tresorerie, ("sumActifsExpCirDot",))
    passifs = _study_num(tresorerie, ("sumPassifsExploitationCirculantsDot",))
    if aktifs is None or passifs is None:
        return {}

    bilan = study_payload(period_data, STUDY_BILAN_DETAILLE)
    stocks_matieres = stocks_produits = clients = tva_deductible = acomptes_is = None
    dettes_fournisseurs = dettes_fiscales = stocks_en_cours = None
    if bilan:
        # Nettoyage du plan de comptes Subakoua :
        #   31/37 = stocks (matières / en-cours / produits finis)
        #   411    = clients          416 = acomptes fournisseurs (0 ici)
        #   444    = État, TVA (le cash-flow la range parmi les actifs du BFR)
        #   401    = fournisseurs    4457 = TVA due aux tiers
        stocks_matieres = _study_num(bilan, ("mp310Net",), ("mp310",))
        stocks_produits = _study_num(bilan, ("pf355Net",), ("pf355",))
        clients = _study_num(bilan, ("cl411",), ("cl411Net",))
        acomptes_is = _study_num(bilan, ("aco444",), ("isdu444",))
        tva_deductible = _study_num(bilan, ("tva4456",))
        dettes_fournisseurs = _study_num(bilan, ("detfr401",))
        dettes_fiscales = _study_num(bilan, ("tva4457",))
        stocks_en_cours = money(
            sum(
                (v for v in (_study_num(bilan, ("encoursa",)), _study_num(bilan, ("encoursb",))) if v is not None),
                Decimal("0"),
            )
        )

    variation = _study_num(tresorerie, ("variationBfr",))
    return {
        "bfr": money(aktifs + passifs),
        "actifs": money(aktifs),
        "passifs": money(passifs),
        "variation_comptable": variation,
        "variation_cash": money(-variation) if variation is not None else None,
        "Stocks matières": stocks_matieres,
        "Stocks d'en-cours": stocks_en_cours,
        "Stocks de produits": stocks_produits,
        "Clients": clients,
        "Acomptes sur IS": acomptes_is,
        "Etat, TVA déductible (hors immobilisations)": tva_deductible,
        "Fournisseurs": dettes_fournisseurs,
        "Dettes fiscales et sociales (hors immobilisations)": dettes_fiscales,
    }


def extract_bfr_components_from_balance_sheet(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """Extrait les composantes BFR depuis le bilan lorsque le tableau de trésorerie détaillé manque."""
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    values: dict[str, Optional[Decimal]] = {
        "Stocks matières": None,
        "Stocks d'en-cours": Decimal("0"),
        "Stocks de produits": None,
        "Clients": None,
        "Acomptes sur IS": None,
        "Etat, TVA déductible (hors immobilisations)": None,
        "Fournisseurs": None,
        "Dettes fiscales et sociales (hors immobilisations)": Decimal("0"),
    }
    fiscal_parts: list[Decimal] = []
    for row in rows:
        actif = str(row.get("Actif", "")).strip().lower()
        passif = str(row.get("Passif", "")).strip().lower()
        va = to_decimal(row.get("Net exercice N", row.get("Net", row.get("Brut"))))
        vp = to_decimal(row.get("Exercice N", row.get("Montant")))
        if actif == "matières":
            values["Stocks matières"] = va
        elif "en-cours" in actif:
            values["Stocks d'en-cours"] = money((values["Stocks d'en-cours"] or Decimal("0")) + (va or Decimal("0")))
        elif actif == "produits finis":
            values["Stocks de produits"] = va
        elif actif == "clients":
            values["Clients"] = va
        elif actif == "acomptes sur is":
            values["Acomptes sur IS"] = va
        elif actif == "tva déductible":
            values["Etat, TVA déductible (hors immobilisations)"] = va
        if passif == "fournisseurs":
            values["Fournisseurs"] = vp
        elif passif in {"tva collectée", "impôt sur les bénéfices"}:
            if vp is not None:
                fiscal_parts.append(vp)
    if fiscal_parts:
        values["Dettes fiscales et sociales (hors immobilisations)"] = money(sum(fiscal_parts, Decimal("0")))
    return values


def extract_bfr_from_balance_sheet(period_data: Mapping[str, Any]) -> Optional[Decimal]:
    """Fallback : BFR complet reconstruit depuis le bilan détaillé.

    Ce fallback n'invente pas de ratio : il reprend les postes d'exploitation
    circulants disponibles dans le bilan. Pour la TVA, il utilise toutefois
    la ligne "TVA déductible" globale ; le tableau de trésorerie détaillé reste
    donc prioritaire quand il existe.
    """
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    actif_values = {"matières": None, "en-cours": Decimal("0"), "produits finis": None, "clients": None,
                    "tva déductible": None, "acomptes sur is": None}
    fournisseurs = dettes_fiscales = Decimal("0")
    found_fournisseurs = False
    found_fiscal = False
    for row in rows:
        actif = str(row.get("Actif", "")).strip().lower()
        passif = str(row.get("Passif", "")).strip().lower()
        value_a = to_decimal(row.get("Net exercice N", row.get("Net", row.get("Brut"))))
        value_p = to_decimal(row.get("Exercice N", row.get("Montant")))
        if actif == "matières": actif_values["matières"] = value_a
        elif "en-cours" in actif: actif_values["en-cours"] = (actif_values["en-cours"] or Decimal("0")) + (value_a or Decimal("0"))
        elif actif == "produits finis": actif_values["produits finis"] = value_a
        elif actif == "clients": actif_values["clients"] = value_a
        elif actif == "tva déductible": actif_values["tva déductible"] = value_a
        elif actif == "acomptes sur is": actif_values["acomptes sur is"] = value_a
        if passif == "fournisseurs" and value_p is not None:
            fournisseurs = value_p; found_fournisseurs = True
        if passif in {"tva collectée", "impôt sur les bénéfices"} and value_p is not None:
            dettes_fiscales += value_p; found_fiscal = True
    if not all(actif_values[k] is not None for k in ["matières", "produits finis", "clients", "tva déductible", "acomptes sur is"]) or not found_fournisseurs:
        return None
    if not found_fiscal:
        return None
    return money(
        (actif_values["matières"] or Decimal("0")) +
        (actif_values["en-cours"] or Decimal("0")) +
        (actif_values["produits finis"] or Decimal("0")) +
        (actif_values["clients"] or Decimal("0")) +
        (actif_values["tva déductible"] or Decimal("0")) +
        (actif_values["acomptes sur is"] or Decimal("0")) -
        fournisseurs - dettes_fiscales
    )


def extract_indirect_cashflow(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "IFRS", "Statement of cash flow", "Tableau_1"))
    out: dict[str, Optional[Decimal]] = {}
    for row in rows:
        label = str(row.get("Indirect method statement of cash flows", "")).strip()
        if label:
            out[label] = to_decimal(row.get("Colonne_1"))

    # Le tableau de trésorerie détaillé est plus riche et utilise une
    # convention de signe explicite : Colonne_5 = ΔBFR, Colonne_6 = cash.
    detailed = _rows_at(
        period_data,
        ("etat_actuel", "expert_comptable", "Synthèse", "Tableau de trésorerie méthode indirecte", "Tableau_1"),
    )
    for row in detailed:
        label = str(row.get("Colonne_0", "")).strip()
        if label == "Variation du BFR et flux attaché":
            delta_bfr = to_decimal(row.get("Colonne_5"))
            cash_bfr = to_decimal(row.get("Colonne_6"))
            if delta_bfr is not None:
                out["Variation du BFR comptable"] = delta_bfr
                out["Variation du BFR cash"] = cash_bfr if cash_bfr is not None else -delta_bfr
        elif label == "Flux net de trésorerie":
            val = to_decimal(row.get("Colonne_6"))
            if val is not None:
                out["Flux net de trésorerie"] = val
    if not out:
        return _cashflow_from_studies(period_data)
    out.update({k: v for k, v in _cashflow_from_studies(period_data).items() if k not in out or out[k] is None})
    return out


def _cashflow_from_studies(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """Flux de trésorerie depuis le tableau de trésorerie / l'IFRS achetés.

    L'étude IFRS publie les trois agrégats d'activité et le flux net, dans la
    nomenclature attendue par ``extract_financial_state``.
    """
    ifrs = study_payload(period_data, STUDY_IFRS)
    tresorerie = study_payload(period_data, STUDY_TRESORERIE)
    out: dict[str, Optional[Decimal]] = {}
    if ifrs:
        out["Variation du BFR"] = _study_num(ifrs, ("operatingActivities.variationsInWorkingCapital",))
        out["Cash flow sur activités courantes"] = _study_num(ifrs, ("operatingActivities.total",))
        out["Acquisition de machines en pleine propriété ou crédit-bail"] = _study_num(
            ifrs, ("investingActivities.goodsPurchases.total",)
        )
        out["Cash flow sur activités de financement"] = _study_num(ifrs, ("financingActivities.total",))
        out["Flux net de trésorerie"] = _study_num(ifrs, ("netFlow",), ("finalTreasury",))
    if tresorerie:
        if out.get("Variation du BFR") is None:
            out["Variation du BFR"] = _study_num(tresorerie, ("variationBfr",))
        if out.get("Cash flow sur activités courantes") is None:
            out["Cash flow sur activités courantes"] = _study_num(
                tresorerie,
                ("resultatNet",),
                ("fluxNetTresorerie",),
            )
        if out.get("Acquisition de machines en pleine propriété ou crédit-bail") is None:
            out["Acquisition de machines en pleine propriété ou crédit-bail"] = _study_num(
                tresorerie, ("fluxAcquisitionImmoCorporelles",)
            )
        if out.get("Cash flow sur activités de financement") is None:
            out["Cash flow sur activités de financement"] = _study_num(
                tresorerie, ("fluxNetTresorerieLieFinancement",)
            )
        if out.get("Flux net de trésorerie") is None:
            out["Flux net de trésorerie"] = _study_num(tresorerie, ("fluxNetTresorerie",))
    return {k: v for k, v in out.items() if v is not None}


def complete_bfr_from_previous_state(current: FinancialState, previous: FinancialState) -> FinancialState:
    """Complète un BFR de période lorsque seul ΔBFR est disponible."""
    if current.bfr_reel is not None:
        return current
    if previous.bfr_reel is None or current.variation_bfr_reelle is None:
        return current
    return replace(current, bfr_reel=money(previous.bfr_reel + current.variation_bfr_reelle))


def opening_bfr_from_current_state(state: FinancialState) -> Optional[Decimal]:
    """Retourne le BFR d'ouverture lorsque ΔBFR de la période est connu."""
    if state.bfr_reel is None or state.variation_bfr_reelle is None:
        return None
    return money(state.bfr_reel - state.variation_bfr_reelle)


def extract_loan_schedule(period_data: Mapping[str, Any], target_tour_id: int) -> dict[str, Optional[Decimal]]:
    vide = {"mensualite": None, "interets": None, "amortissement": None, "capital_fin": None}
    rows = _rows_at(period_data, ("etat_actuel", "banque_assurance", "Banque", "Tableau d'amortissement", "Tableau_1"))
    target_code = target_schedule_code(target_tour_id)
    for row in rows:
        if row.get("Mois / Année") == target_code:
            return {
                "mensualite": to_decimal(row.get("Mensualités (€)")),
                "interets": to_decimal(row.get("Intérêts (€)")),
                "amortissement": to_decimal(row.get("Amortissement (€)")),
                "capital_fin": to_decimal(row.get("Capital restant dû fin du mois (€)")),
            }
    return _loan_schedule_from_studies(period_data, target_tour_id) or vide


def _loan_schedule_from_studies(period_data: Mapping[str, Any], target_tour_id: int) -> dict[str, Optional[Decimal]]:
    """Échéance du mois cible depuis l'étude « Tableau d'amortissement » achetée.

    L'étude publie la grille complète des 120 échéances avec, pour chacune,
    l'annuité, les intérêts, le capital amorti et le capital restant dû. On y
    lit donc la ligne du mois simulé : l'échéance n'est jamais extrapolée.
    """
    node = study_payload(period_data, STUDY_EMPRUNT)
    if not node:
        return {}
    table = _pick_path(node, ("currentMonthlyLoanAmortizationTable",))
    if not isinstance(table, Mapping):
        return {}
    lignes = table.get("depreciationRows")
    if not isinstance(lignes, list):
        return {}
    target_code = target_schedule_code(target_tour_id)
    for ligne in lignes:
        if not isinstance(ligne, Mapping):
            continue
        code = _loan_row_code(ligne)
        if code is None or code != target_code:
            continue
        return {
            "mensualite": _study_num(ligne, ("annuite",)),
            "interets": _study_num(ligne, ("interet",)),
            "amortissement": _study_num(ligne, ("ammortissement",)),
            "capital_fin": _study_num(ligne, ("crdFin",)),
        }
    return {}


def _loan_row_code(ligne: Mapping[str, Any]) -> Optional[int]:
    """Convertit une ligne d'échéancier en code « mois / année » Subakoua.

    L'étude publie « numeroMoisFormate » au format « MM / AA » où AA est
    l'année en base 0 (00 = année 1), ce qui donne directement le même code que
    ``target_schedule_code`` (mois * 100 + année). On retombe sur le numéro de
    mois si le libellé formaté est absent.
    """
    numero = ligne.get("numeroMois")
    if numero is None:
        return None
    try:
        mois = int(numero)
    except (TypeError, ValueError):
        return None
    libelle = str(ligne.get("numeroMoisFormate", "")).strip()
    annee_txt, sep, annee0_txt = libelle.partition("/")
    if sep:
        try:
            annee = int(annee_txt) * 100 + int(annee0_txt.strip()) + 1
        except ValueError:
            return mois * 100 + 1
        return annee
    return mois * 100 + 1


def extract_financial_state(period: str, period_data: Mapping[str, Any], target_tour_id: Optional[int] = None) -> FinancialState:
    bank_initial, bank_final = extract_bank_balances(period_data)
    savings = extract_savings_balance(period_data)
    savings_opening = extract_savings_opening_balance(period_data, savings)
    debt = extract_debt_balance(period_data)
    bfr_detail = extract_detailed_bfr(period_data)
    bfr_reel = bfr_detail.get("bfr") if bfr_detail else extract_bfr_from_balance_sheet(period_data)
    income = extract_income_statement(period_data)
    ratios = extract_ratios(period_data)
    capitaux_propres = extract_capitaux_propres(period_data)
    cashflow = extract_indirect_cashflow(period_data)
    tax_liabilities = extract_tax_liabilities(period_data)
    loan = extract_loan_schedule(period_data, target_tour_id) if target_tour_id else {}
    debt_opening = None
    if debt is not None and loan.get("amortissement") is not None:
        debt_opening = money(debt + (loan.get("amortissement") or Decimal("0")))

    bfr_component_map = bfr_detail or extract_bfr_components_from_balance_sheet(period_data)
    def bfr_component(name: str, absolute: bool = False) -> Optional[Decimal]:
        value = bfr_component_map.get(name)
        if value is None:
            return None
        return money(abs(value) if absolute else value)

    warnings: list[str] = []
    if bank_initial is None:
        warnings.append("Solde bancaire initial introuvable dans Banque → Extrait bancaire → Soldes bancaires.")
    if savings is None:
        warnings.append("Solde du compte épargne introuvable dans le bilan détaillé.")
    if bfr_reel is None:
        warnings.append("BFR opérationnel introuvable : ni tableau de trésorerie détaillé ni bilan exploitable.")
    if income.get("aace") is None:
        warnings.append("AACE introuvables dans le compte de résultat.")
    if income.get("depreciations") is None:
        warnings.append("Dotations aux dépréciations introuvables dans le compte de résultat.")
    if target_tour_id and loan.get("amortissement") is None:
        warnings.append(f"Échéance de remboursement introuvable pour le Tour ID cible {target_tour_id}.")

    return FinancialState(
        periode=period,
        solde_bancaire_initial=bank_initial,
        solde_bancaire_final=bank_final,
        solde_epargne=savings,
        solde_epargne_ouverture=savings_opening,
        aace=income["aace"],
        achats_matieres=income.get("achats_matieres"),
        aace_inclut_achats=bool(income.get("aace_inclut_achats")),
        depreciations=income["depreciations"],
        dotations_amortissements=income["dotations_amortissements"],
        resultat_net=income["resultat_net"],
        chiffre_affaires=ratios.get("mensuel_ca"),
        chiffre_affaires_cumule=ratios.get("cumule_ca"),
        resultat_avant_impot_cumule=ratios.get("cumule_resultat_avant_impot"),
        capitaux_propres=capitaux_propres,
        dette_bancaire=debt,
        dette_bancaire_ouverture=debt_opening,
        bfr_reel=bfr_reel,
        bfr_actifs_exploitation_reel=bfr_detail.get("actifs") if bfr_detail else None,
        bfr_passifs_exploitation_reel=bfr_detail.get("passifs") if bfr_detail else None,
        bfr_stocks_matieres_reel=bfr_component("Stocks matières"),
        bfr_stocks_encours_reel=bfr_component("Stocks d'en-cours"),
        bfr_stocks_produits_reel=bfr_component("Stocks de produits"),
        bfr_creances_clients_reel=bfr_component("Clients"),
        bfr_acomptes_is_reel=bfr_component("Acomptes sur IS"),
        bfr_tva_deductible_hors_immo_reel=bfr_component("Etat, TVA déductible (hors immobilisations)"),
        tva_collectee_reelle=tax_liabilities.get("tva_collectee"),
        impot_benefices_reel=tax_liabilities.get("impot_benefices"),
        bfr_dettes_fournisseurs_reel=bfr_component("Fournisseurs", absolute=True),
        bfr_dettes_fiscales_sociales_reel=bfr_component("Dettes fiscales et sociales (hors immobilisations)", absolute=True),
        remboursement_capital_mois_cible=loan.get("amortissement"),
        interets_emprunt_mois_cible=loan.get("interets"),
        mensualite_emprunt_mois_cible=loan.get("mensualite"),
        variation_bfr_reelle=(bfr_detail.get("variation_comptable") if bfr_detail and bfr_detail.get("variation_comptable") is not None else (-(cashflow.get("Variation du BFR") or Decimal("0")) if cashflow.get("Variation du BFR") is not None else None)),
        variation_bfr_cashflow_reelle=(bfr_detail.get("variation_cash") if bfr_detail and bfr_detail.get("variation_cash") is not None else cashflow.get("Variation du BFR")),
        flux_treso_exploitation_reel=cashflow.get("Cash flow sur activités courantes"),
        flux_treso_investissement_reel=cashflow.get("Acquisition de machines en pleine propriété ou crédit-bail"),
        flux_treso_financement_reel=cashflow.get("Cash flow sur activités de financement"),
        flux_treso_net_reel=cashflow.get("Flux net de trésorerie"),
        warnings=tuple(warnings),
    )


def calculate_machine_investment(
    cout_ht: Any,
    credit_fournisseur: bool,
) -> MachineInvestmentResult:
    ht = to_decimal(cout_ht, Decimal("0")) or Decimal("0")
    ttc = ht * (Decimal("1") + TVA_STANDARD)
    if credit_fournisseur:
        decaissement = ttc * ACOMPTE_CREDIT_FOURNISSEUR
        dette = ttc * (Decimal("1") - ACOMPTE_CREDIT_FOURNISSEUR)
    else:
        decaissement = ttc
        dette = Decimal("0")
    return MachineInvestmentResult(money(ht), money(ttc), money(decaissement), money(dette))


def calculate_bfr(
    stock_matieres: Optional[Any],
    stock_produits_finis: Optional[Any],
    creances_clients: Optional[Any],
    dettes_fournisseurs: Optional[Any],
) -> Optional[Decimal]:
    values = [stock_matieres, stock_produits_finis, creances_clients, dettes_fournisseurs]
    if any(v is None for v in values):
        return None
    return money(sum(to_decimal(v, Decimal("0")) or Decimal("0") for v in values[:3]) - (to_decimal(dettes_fournisseurs, Decimal("0")) or Decimal("0")))


def calculate_full_bfr(components: BFRComponents) -> Decimal:
    """Calcule le BFR complet selon la structure comptable Subakoua."""
    return components.bfr


def build_bfr_projection(
    *,
    stocks_matieres: Any,
    stocks_encours: Any = 0,
    stocks_produits: Any = 0,
    creances_clients: Any = 0,
    acomptes_is: Any = 0,
    tva_deductible_hors_immo: Any = 0,
    dettes_fournisseurs: Any = 0,
    dettes_fiscales_sociales: Any = 0,
    bfr_m1: Optional[Any] = None,
    warnings: Optional[Iterable[str]] = None,
) -> BFRProjectionResult:
    """Construit un BFR prévisionnel explicite et calculable sans Streamlit.

    Cette fonction n'invente aucune échéance : tous les postes sont fournis
    par l'appelant. Cela permet de connecter progressivement les règles MRP2,
    clients, fournisseurs et fiscales du jeu sans contaminer le cœur comptable.
    """
    components = BFRComponents(
        stocks_matieres=to_decimal(stocks_matieres, Decimal("0")) or Decimal("0"),
        stocks_encours=to_decimal(stocks_encours, Decimal("0")) or Decimal("0"),
        stocks_produits=to_decimal(stocks_produits, Decimal("0")) or Decimal("0"),
        creances_clients=to_decimal(creances_clients, Decimal("0")) or Decimal("0"),
        acomptes_is=to_decimal(acomptes_is, Decimal("0")) or Decimal("0"),
        tva_deductible_hors_immo=to_decimal(tva_deductible_hors_immo, Decimal("0")) or Decimal("0"),
        dettes_fournisseurs=to_decimal(dettes_fournisseurs, Decimal("0")) or Decimal("0"),
        dettes_fiscales_sociales=to_decimal(dettes_fiscales_sociales, Decimal("0")) or Decimal("0"),
    )
    variation = calculate_variation_bfr(components.bfr, bfr_m1)
    return BFRProjectionResult(
        components=components,
        bfr=money(components.bfr),
        variation_vs_m1=variation,
        warnings=tuple(warnings or ()),
    )


def calculate_variation_bfr(bfr_mois: Optional[Any], bfr_m1: Optional[Any]) -> Optional[Decimal]:
    if bfr_mois is None or bfr_m1 is None:
        return None
    return money((to_decimal(bfr_mois) or Decimal("0")) - (to_decimal(bfr_m1) or Decimal("0")))


def project_bank_debt(
    *,
    dette_ouverture: Any,
    remboursement_capital: Any = 0,
    nouveaux_emprunts: Any = 0,
    interets: Any = 0,
    mensualite: Optional[Any] = None,
) -> DebtProjectionResult:
    """Projette l'encours bancaire sur un mois.

    Les intérêts sont une charge financière ; seul le capital remboursé
    diminue l'encours. Un nouvel emprunt est une rentrée de trésorerie et
    augmente symétriquement la dette.
    """
    opening = max(Decimal("0"), to_decimal(dette_ouverture, Decimal("0")) or Decimal("0"))
    capital = max(Decimal("0"), to_decimal(remboursement_capital, Decimal("0")) or Decimal("0"))
    new = max(Decimal("0"), to_decimal(nouveaux_emprunts, Decimal("0")) or Decimal("0"))
    interest = max(Decimal("0"), to_decimal(interets, Decimal("0")) or Decimal("0"))
    warnings: list[str] = []
    if capital > opening:
        warnings.append("Le remboursement du capital dépasse l'encours d'ouverture : remboursement plafonné à l'encours.")
        capital = opening
    closing = money(opening - capital + new)
    if mensualite is None:
        payment = money(capital + interest)
    else:
        payment = max(Decimal("0"), to_decimal(mensualite, Decimal("0")) or Decimal("0"))
    return DebtProjectionResult(
        dette_ouverture=money(opening),
        nouveaux_emprunts=money(new),
        remboursement_capital=money(capital),
        interets=money(interest),
        mensualite=money(payment),
        dette_cloture=money(closing),
        warnings=tuple(warnings),
    )


def calculate_projection(
    *,
    chiffre_affaires: Any,
    achats: Any,
    masse_salariale: Any,
    charges_sociales: Any,
    marketing: Any,
    assurances: Any,
    resultat_historique: Optional[Any],
    aace_historique: Optional[Any],
    depreciations_historique: Optional[Any],
    dotations_amortissements_historique: Optional[Any],
    solde_tresorerie_initial: Any,
    solde_epargne_initial: Any,
    dette_bancaire: Optional[Any],
    investissement_ht: Any,
    credit_fournisseur: bool,
    bfr_mois: Optional[Any],
    bfr_m1: Optional[Any],
    achats_matieres_historique: Optional[Any] = None,
    aace_inclut_achats: Optional[bool] = None,
    achats_titres: Any = 0,
    ventes_titres: Any = 0,
    placement: Any = 0,
    retrait: Any = 0,
    dividendes: Any = 0,
    nouvelles_entrees_dette_cash: Any = 0,
    autres_flux_financement: Any = 0,
    taux_is: Any = Decimal("0.25"),
    report_a_nouveau: Any = 0,
    deficit_precedent: Any = 0,
    interets_emprunt: Optional[Any] = None,
    remboursement_capital: Optional[Any] = None,
    tva_deductible_hors_immo_mois: Any = 0,
    tva_deductible_immobilisations_mois: Any = 0,
    tva_nette_ouverture_a_payer: Any = 0,
    dette_fiscale_sociale_hors_tva_ouverture: Any = 0,
    nouvelles_charges_fiscales_sociales: Any = 0,
    paiements_fiscaux_sociaux_hors_tva: Any = 0,
) -> FinancialProjection:
    ca = to_decimal(chiffre_affaires, Decimal("0")) or Decimal("0")
    achats_d = to_decimal(achats, Decimal("0")) or Decimal("0")
    sal = to_decimal(masse_salariale, Decimal("0")) or Decimal("0")
    sociales = to_decimal(charges_sociales, Decimal("0")) or Decimal("0")
    mkg = to_decimal(marketing, Decimal("0")) or Decimal("0")
    assurances_d = to_decimal(assurances, Decimal("0")) or Decimal("0")
    aace = to_decimal(aace_historique)
    deprec = to_decimal(depreciations_historique)
    dot_base = to_decimal(dotations_amortissements_historique)
    treso_initiale = to_decimal(solde_tresorerie_initial, Decimal("0")) or Decimal("0")
    epargne = to_decimal(solde_epargne_initial, Decimal("0")) or Decimal("0")
    dette = max(Decimal("0"), to_decimal(dette_bancaire, Decimal("0")) or Decimal("0"))
    invest = calculate_machine_investment(investissement_ht, credit_fournisseur)

    warnings: list[str] = []
    if aace is None:
        aace = Decimal("0")
        warnings.append("AACE manquantes : aucune valeur de secours automatique appliquée.")
    if deprec is None:
        deprec = Decimal("0")
        warnings.append("Dépréciations manquantes : aucune valeur de secours automatique appliquée.")
    if dot_base is None:
        dot_base = Decimal("0")
        warnings.append("Dotations aux amortissements manquantes : aucune valeur de secours automatique appliquée.")

    dotations_totales = dot_base + invest.cout_ht / DUREE_AMORTISSEMENT_MOIS
    maintenance_nouvelles_machines = invest.cout_ht * Decimal("0.005")

    # Les AACE de M-1 peuvent déjà englober les achats de matières (définition
    # large du compte de résultat Subakoua : classes 60 + 61 + 62) ou au
    # contraire ne porter que les charges externes (définition étroite du dump
    # historique, libellé « autres achats et charges externes »). Le montant
    # d'AACE de M-1 n'est donc comparable aux achats du MRP que si l'on sait
    # laquelle des deux définitions s'applique — c'est le rôle du paramètre
    # `aace_inclut_achats` :
    #   * large  -> on ne garde que la part hors achats et on substitue le MRP ;
    #   * étroite -> les deux montants sont disjoints, on les additionne ;
    #   * inconnu -> on additionne et on le signale, plutôt que de supprimer
    #               silencieusement la décision d'achat du joueur.
    achats_m1 = to_decimal(achats_matieres_historique)
    if achats_m1 is None:
        aace_hors_achats = aace
        warnings.append(
            "Achats de matières M-1 non identifiés : impossible de savoir si les AACE les "
            "contiennent déjà. Les achats du mois sont additionnés aux AACE ; vérifiez "
            "qu'il n'y a pas de double compte."
        )
    elif aace_inclut_achats:
        if achats_m1 > aace:
            warnings.append(
                "Achats de matières M-1 supérieurs aux AACE : le solde des AACE a été ramené à zéro."
            )
        aace_hors_achats = max(Decimal("0"), aace - achats_m1)
    elif aace_inclut_achats is False:
        aace_hors_achats = aace
        warnings.append(
            "AACE M-1 restreintes aux charges externes : les achats du mois sont "
            "additionnés séparément, sans double compte."
        )
    else:
        aace_hors_achats = aace
        warnings.append(
            "Définition des AACE M-1 inconnue : les achats du mois sont additionnés aux AACE. "
            "Vérifiez qu'il n'y a pas de double compte."
        )

    total_charges = (
        achats_d + aace_hors_achats + sal + sociales + mkg + dotations_totales +
        assurances_d + maintenance_nouvelles_machines + deprec
    )

    if interets_emprunt is None:
        interets = Decimal("0")
        warnings.append("Intérêts d'emprunt cibles introuvables : intérêts projetés = 0.")
    else:
        interets = to_decimal(interets_emprunt, Decimal("0")) or Decimal("0")
    debt_projection = project_bank_debt(
        dette_ouverture=dette,
        remboursement_capital=remboursement_capital,
        nouveaux_emprunts=nouvelles_entrees_dette_cash,
        interets=interets,
        mensualite=None,
    )
    warnings.extend(debt_projection.warnings)
    remboursements_capital_effectifs = debt_projection.remboursement_capital
    nouveaux_emprunts_effectifs = debt_projection.nouveaux_emprunts

    produits_financiers = epargne * TAUX_RENDEMENT_EPARGNE_ANNUEL / Decimal("12")
    resultat_financier = produits_financiers - interets

    vat = calculate_vat_projection(
        chiffre_affaires_ht=ca,
        tva_deductible_hors_immo_mois=tva_deductible_hors_immo_mois,
        tva_deductible_immobilisations_mois=tva_deductible_immobilisations_mois,
        tva_nette_ouverture_a_payer=tva_nette_ouverture_a_payer,
    )
    fiscal_social = calculate_fiscal_social_projection(
        dette_ouverture=dette_fiscale_sociale_hors_tva_ouverture,
        nouvelles_charges=nouvelles_charges_fiscales_sociales,
        paiements=paiements_fiscaux_sociaux_hors_tva,
    )
    warnings.extend(vat.warnings)
    warnings.extend(fiscal_social.warnings)

    resultat_avant_impot = ca - total_charges + resultat_financier
    deficit = min(Decimal("0"), (to_decimal(report_a_nouveau, Decimal("0")) or Decimal("0")) + (to_decimal(deficit_precedent, Decimal("0")) or Decimal("0")))
    assiette = resultat_avant_impot + deficit
    is_rate = to_decimal(taux_is, Decimal("0.25")) or Decimal("0.25")
    impot_is = assiette * is_rate if assiette > 0 else Decimal("0")
    resultat_net = resultat_avant_impot - impot_is

    caf = resultat_net + dotations_totales + deprec
    variation_bfr = calculate_variation_bfr(bfr_mois, bfr_m1)
    if variation_bfr is None:
        warnings.append("Variation de BFR indisponible : flux d'exploitation non calculable sans hypothèse.")
        variation_bfr_effective = Decimal("0")
    else:
        variation_bfr_effective = variation_bfr

    flux_exploitation = caf - variation_bfr_effective
    flux_investissement = -invest.decaissement_ttc
    remboursement = remboursements_capital_effectifs
    flux_financement = (
        (to_decimal(ventes_titres, Decimal("0")) or Decimal("0")) -
        (to_decimal(achats_titres, Decimal("0")) or Decimal("0")) -
        (to_decimal(placement, Decimal("0")) or Decimal("0")) +
        (to_decimal(retrait, Decimal("0")) or Decimal("0")) -
        (to_decimal(dividendes, Decimal("0")) or Decimal("0")) +
        nouveaux_emprunts_effectifs -
        remboursement +
        (to_decimal(autres_flux_financement, Decimal("0")) or Decimal("0"))
    )
    treso_finale = treso_initiale + flux_exploitation + flux_investissement + flux_financement

    return FinancialProjection(
        total_charges=money(total_charges),
        resultat_financier=money(resultat_financier),
        resultat_avant_impot=money(resultat_avant_impot),
        impot_is=money(impot_is),
        resultat_net=money(resultat_net),
        dotations_totales=money(dotations_totales),
        caf=money(caf),
        bfr_projection=to_decimal(bfr_mois),
        variation_bfr=variation_bfr,
        flux_exploitation=money(flux_exploitation),
        flux_investissement=money(flux_investissement),
        flux_financement=money(flux_financement),
        tresorerie_initiale=money(treso_initiale),
        tresorerie_finale=money(treso_finale),
        solde_epargne_initial=money(epargne),
        solde_epargne_final=money(epargne + (to_decimal(retrait, Decimal("0")) or Decimal("0")) - (to_decimal(placement, Decimal("0")) or Decimal("0"))),
        dette_bancaire_ouverture=debt_projection.dette_ouverture,
        dette_bancaire_finale=debt_projection.dette_cloture,
        investissement_ttc=invest.cout_ttc,
        acompte_ttc=invest.decaissement_ttc,
        dette_fournisseur_investissement=invest.dette_fournisseur,
        interets_emprunt=money(interets),
        remboursement_capital=money(remboursement),
        tva_collectee=vat.tva_collectee_mois,
        tva_deductible=money(vat.tva_deductible_hors_immo_mois + vat.tva_deductible_immobilisations_mois),
        tva_nette=money(vat.tva_nette_mois),
        tva_payee_ce_mois=money(vat.tva_a_payer_ce_mois),
        dette_fiscale_sociale_cloture=money(vat.tva_collectee_cloture + fiscal_social.dette_cloture),
        warnings=tuple(warnings),
    )


def as_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        data = asdict(obj)
        for key, value in data.items():
            if isinstance(value, Decimal):
                data[key] = float(value)
        return data
    raise TypeError(f"Objet non supporté: {type(obj)!r}")
