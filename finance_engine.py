"""Moteur financier pur pour l'ERP Subakoua.

Aucune dépendance Streamlit ici : les fonctions peuvent être rétro-testées
sur le dump JSON ou appelées depuis l'interface Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


TVA_STANDARD = Decimal("0.20")
ACOMPTE_CREDIT_FOURNISSEUR = Decimal("0.30")
DUREE_AMORTISSEMENT_MOIS = Decimal("60")
TAUX_RENDEMENT_EPARGNE_ANNUEL = Decimal("0.012")
NOMBRE_PARTS = Decimal("40000")
TAUX_CHARGES_SOCIALES = Decimal("0.50")

TOURS_PERIODES = {
    1: "Année 1 - Janvier", 2: "Année 1 - Février", 3: "Année 1 - Mars",
    4: "Année 1 - Avril", 5: "Année 1 - Mai", 6: "Année 1 - Juin",
    7: "Année 1 - Juillet", 8: "Année 1 - Août", 9: "Année 1 - Septembre",
    10: "Année 1 - Octobre", 11: "Année 1 - Novembre", 12: "Année 1 - Décembre",
    13: "Année 2 - Janvier", 14: "Année 2 - Février", 15: "Année 2 - Mars",
}


@dataclass(frozen=True)
class FinancialState:
    periode: str
    solde_bancaire_initial: Optional[Decimal] = None
    solde_bancaire_final: Optional[Decimal] = None
    solde_epargne: Optional[Decimal] = None
    solde_epargne_ouverture: Optional[Decimal] = None
    aace: Optional[Decimal] = None
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


def _rows_at(data: Mapping[str, Any], path: Sequence[str]) -> list[Mapping[str, Any]]:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def _mapping_at(data: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any]:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping):
            return {}
        node = node.get(key)
    return node if isinstance(node, Mapping) else {}


def _first_row_value(rows: Iterable[Mapping[str, Any]], key: str, default: Any = None) -> Any:
    for row in rows:
        if key in row:
            return row[key]
    return default


def extract_bank_balances(period_data: Mapping[str, Any]) -> tuple[Optional[Decimal], Optional[Decimal]]:
    rows = _rows_at(period_data, ("etat_actuel", "banque_assurance", "Banque", "Extrait bancaire", "Soldes bancaires"))
    if not rows:
        return None, None
    row = rows[0]
    return to_decimal(row.get("Solde initial")), to_decimal(row.get("Solde final"))


def extract_savings_balance(period_data: Mapping[str, Any]) -> Optional[Decimal]:
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    for row in rows:
        if str(row.get("Actif", "")).strip().lower() == "compte épargne":
            return to_decimal(row.get("Net exercice N", row.get("Brut")))
    return None


def extract_savings_opening_balance(period_data: Mapping[str, Any], final_balance: Optional[Decimal] = None) -> Optional[Decimal]:
    """Reconstruit le solde du compte épargne à l'ouverture de la période.

    Le tableau de trésorerie détaille les placements/retraits en impact banque.
    Un placement de -500000 € côté banque correspond donc à +500000 € côté épargne.
    Les revenus financiers sont volontairement exclus de la reconstruction du
    stock d'épargne : ils apparaissent comme produit/flux distinct dans Subakoua.
    """
    final_value = final_balance if final_balance is not None else extract_savings_balance(period_data)
    if final_value is None:
        return None
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
    return money(total) if found else None


def extract_tax_liabilities(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    """Extrait les dettes fiscales directement du bilan détaillé.

    Ces montants sont des passifs de clôture, distincts des flux de règlement
    observés sur le relevé bancaire.
    """
    rows = _rows_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Bilan détaillé", "Tableau_1"))
    out: dict[str, Optional[Decimal]] = {"tva_collectee": None, "impot_benefices": None}
    for row in rows:
        label = str(row.get("Passif", "")).strip().lower()
        value = to_decimal(row.get("Exercice N", row.get("Montant")))
        if label == "tva collectée":
            out["tva_collectee"] = value
        elif label == "impôt sur les bénéfices":
            out["impot_benefices"] = value
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
    return result


def extract_ratios(period_data: Mapping[str, Any]) -> dict[str, Optional[Decimal]]:
    ratios = _mapping_at(period_data, ("etat_actuel", "expert_comptable", "Synthèse", "Ratios", "Ratios et Indicateurs"))
    monthly = ratios.get("Indicateurs_MENSUEL")
    cumulative = ratios.get("Indicateurs_CUMULÉ", ratios.get("Indicateurs_CUMULE"))
    out: dict[str, Optional[Decimal]] = {}
    for prefix, rows in (("mensuel", monthly), ("cumule", cumulative)):
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            out[f"{prefix}_ca"] = to_decimal(rows[0].get("Chiffre d'affaires"))
            out[f"{prefix}_resultat_avant_impot"] = to_decimal(rows[0].get("Résultat avant impôt"))
    return out



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
        return {}

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
    return out


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
    return {"mensualite": None, "interets": None, "amortissement": None, "capital_fin": None}


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
    total_charges = (
        achats_d + sal + sociales + mkg + dotations_totales + assurances_d +
        maintenance_nouvelles_machines + aace + deprec
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
