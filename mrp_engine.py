"""Moteur MRP2 : besoins bruts → besoins nets → plan d'achat → coûts.

Ce module est volontairement **pur** (aucune dépendance Streamlit / SQL) : il
reçoit des données déjà chargées et renvoie des résultats calculés. Les tests
unitaires (`test_mrp_engine.py`) s'appuient sur cette séparation.

Vocabulaire
-----------
* **besoin brut**     : consommation théorique du mois (volumes produits × nomenclature)
* **stock ouverture** : stock disponible en début de mois (clôture M-1)
* **stock sécurité**  : niveau visé pour absorber un aléa de demande/appro.
  Il combine la cible ERP (`parametres_stockssecurite`, en absolu) et le
  coefficient de sécurité choisi par l'utilisateur (% du besoin brut).
* **besoin net**      : quantité à acheter = max(0, besoin brut + sécurité − ouverture)
* **stock final**     : stock restant après consommation et réceptions
* **couverture**      : nombre de mois de production que le stock final couvre

Économie du stock
-----------------
Le module ne compare pas seulement le coût d'achat : un stock plus élevé
immobilise du cash et vieillit. Deux grandeurs complètent donc le coût :

* **coût de possession** : valeur du stock final × taux de possession annuel / 12
* **valeur à risque**    : part du stock final qui ne sera pas consommée avant
  la fin de vie de la matière (`taux_vieillissement_mois`), donc perdue.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ParametresMRP:
    """Données d'entrée du MRP. Volatiles : utiliser `replace` pour varier."""

    # produit -> matière -> coefficient unitaire
    nomenclature: Mapping[str, Mapping[str, float]]
    # matière -> quantité en stock au début du mois
    stocks_ouverture: Mapping[str, float]
    # matière -> stock de sécurité cible de l'ERP (absolu, en unités)
    stocks_securite_base: Mapping[str, float]
    # matière -> {"fournisseur", "prix", "delai_mois", "tarifaire"}
    tarifs: Mapping[str, Mapping[str, Any]]
    # matière -> durée de vie en mois (avant péremption)
    taux_vieillissement_mois: Mapping[str, float]
    # volumes produits par produit
    volumes: Mapping[str, float]
    # part de la production affectée à la couverture de la demande (%)
    part_couverture_pct: float = 100.0
    # coefficient de sécurité appliqué au besoin brut (%)
    coeff_stock_secu_pct: float = 0.0
    # taux de possession annuel du stock (%)
    taux_possession_annuel_pct: float = 8.0


@dataclass(frozen=True)
class LigneMRP:
    matiere: str
    besoin_brut: float
    stock_ouverture: float
    stock_securite: float
    besoin_net: float
    stock_final: float
    couverture_mois: float | None
    prix_unitaire: float
    montant_ht: float
    fournisseur: str
    delai_mois: int
    origine_prix: str
    volume_a_risque: float
    valeur_a_risque: float


@dataclass(frozen=True)
class ResultatMRP:
    lignes: tuple[LigneMRP, ...]
    volume_total_produit: float
    cout_achat_ht: float
    cout_possession_mensuel: float
    valeur_stock_final: float
    valeur_a_risque: float
    cout_total_mensuel: float
    alertes: tuple[str, ...] = ()


def besoins_bruts(
    nomenclature: Mapping[str, Mapping[str, float]],
    volumes: Mapping[str, float],
    part_couverture_pct: float = 100.0,
) -> dict[str, float]:
    """Consommation théorique du mois, matière par matière."""
    ratio = max(0.0, float(part_couverture_pct)) / 100.0
    besoins: dict[str, float] = {}
    for produit, quantite in (volumes or {}).items():
        coefficients = (nomenclature or {}).get(produit) or {}
        volume_effectif = max(0.0, float(quantite or 0.0)) * ratio
        for matiere, coefficient in coefficients.items():
            try:
                qte = float(coefficient or 0.0)
            except (TypeError, ValueError):
                qte = 0.0
            besoins[matiere] = besoins.get(matiere, 0.0) + volume_effectif * qte
    return besoins


def stock_securite_effectif(
    base: float,
    besoin_brut: float,
    coeff_stock_secu_pct: float,
) -> float:
    """Stock de sécurité visé = cible ERP (absolue) + tampon proportionnel.

    La cible ERP (`parametres_stockssecurite`) est un plancher : elle est
    toujours respectée, quelle que soit la jauge. Le coefficient choisi par
    l'utilisateur s'ajoute en proportion du besoin brut (0 % = pas de tampon
    supplémentaire au-delà de la cible ERP).
    """
    try:
        cible = max(0.0, float(base or 0.0))
    except (TypeError, ValueError):
        cible = 0.0
    try:
        proportion = max(0.0, float(besoin_brut or 0.0)) * max(0.0, float(coeff_stock_secu_pct or 0.0)) / 100.0
    except (TypeError, ValueError):
        proportion = 0.0
    return cible + proportion


def calculer_mrp(params: ParametresMRP) -> ResultatMRP:
    """Calcule le plan d'approvisionnement complet et ses coûts."""
    besoins = besoins_bruts(params.nomenclature, params.volumes, params.part_couverture_pct)
    taux_possession_mensuel = max(0.0, float(params.taux_possession_annuel_pct or 0.0)) / 100.0 / 12.0

    lignes: list[LigneMRP] = []
    cout_achat = 0.0
    cout_possession = 0.0
    valeur_stock_final = 0.0
    valeur_a_risque = 0.0
    alertes: list[str] = []

    for matiere in sorted(besoins):
        besoin = max(0.0, besoins[matiere])
        ouverture = max(0.0, float((params.stocks_ouverture or {}).get(matiere, 0.0) or 0.0))
        securite = stock_securite_effectif(
            (params.stocks_securite_base or {}).get(matiere, 0.0),
            besoin,
            params.coeff_stock_secu_pct,
        )
        besoin_net = max(0.0, besoin + securite - ouverture)
        stock_final = max(ouverture - besoin, securite)
        couverture = (stock_final / besoin) if besoin > 0 else None

        tarif = (params.tarifs or {}).get(matiere) or {}
        try:
            prix = float(tarif.get("prix") or 0.0)
        except (TypeError, ValueError):
            prix = 0.0
        try:
            delai = int(float(tarif.get("delai_mois") or 0))
        except (TypeError, ValueError):
            delai = 0
        fournisseur = str(tarif.get("fournisseur") or "N/D")
        origine = "Tarifaire" if tarif.get("tarifaire") else str(tarif.get("origine") or "Estimation")

        # Part du stock final qui ne sera pas consommée avant péremption.
        try:
            duree_vie = float((params.taux_vieillissement_mois or {}).get(matiere, 0.0) or 0.0)
        except (TypeError, ValueError):
            duree_vie = 0.0
        consommable = besoin * duree_vie
        volume_a_risque = max(0.0, stock_final - consommable) if duree_vie > 0 else 0.0
        risque = volume_a_risque * prix

        montant = besoin_net * prix
        cout_achat += montant
        cout_possession += stock_final * prix * taux_possession_mensuel
        valeur_stock_final += stock_final * prix
        valeur_a_risque += risque

        lignes.append(
            LigneMRP(
                matiere=matiere,
                besoin_brut=besoin,
                stock_ouverture=ouverture,
                stock_securite=securite,
                besoin_net=besoin_net,
                stock_final=stock_final,
                couverture_mois=couverture,
                prix_unitaire=prix,
                montant_ht=montant,
                fournisseur=fournisseur,
                delai_mois=delai,
                origine_prix=origine,
                volume_a_risque=volume_a_risque,
                valeur_a_risque=risque,
            )
        )

        if besoin_net > 0 and prix <= 0:
            alertes.append(f"{matiere} : besoin net de {besoin_net:,.0f} unité(s) non chiffrable (aucun prix de référence).")

    volume_total = sum(max(0.0, float(v or 0.0)) for v in (params.volumes or {}).values())
    return ResultatMRP(
        lignes=tuple(lignes),
        volume_total_produit=volume_total,
        cout_achat_ht=cout_achat,
        cout_possession_mensuel=cout_possession,
        valeur_stock_final=valeur_stock_final,
        valeur_a_risque=valeur_a_risque,
        cout_total_mensuel=cout_achat + cout_possession,
        alertes=tuple(alertes),
    )


@dataclass(frozen=True)
class Scenario:
    """Un scénario de décision : libellé + réglages."""

    libelle: str
    coeff_stock_secu_pct: float
    part_couverture_pct: float = 100.0


def comparer_scenarios(
    params: ParametresMRP,
    scenarios: Sequence[Scenario],
) -> list[dict[str, Any]]:
    """Calcule chaque scénario et renvoie une ligne de comparaison par scénario.

    Le scénario le moins cher est signalé (`"recommandé"`), ce qui répond
    directement à la question « qu'est-ce qui est le plus rentable ? ».
    """
    resultats: list[dict[str, Any]] = []
    for scenario in scenarios:
        variante = replace(
            params,
            coeff_stock_secu_pct=scenario.coeff_stock_secu_pct,
            part_couverture_pct=scenario.part_couverture_pct,
        )
        resultat = calculer_mrp(variante)
        resultats.append(
            {
                "libelle": scenario.libelle,
                "coeff_stock_secu_pct": scenario.coeff_stock_secu_pct,
                "part_couverture_pct": scenario.part_couverture_pct,
                "volume_produit": resultat.volume_total_produit,
                "cout_achat_ht": resultat.cout_achat_ht,
                "cout_possession": resultat.cout_possession_mensuel,
                "valeur_stock_final": resultat.valeur_stock_final,
                "valeur_a_risque": resultat.valeur_a_risque,
                "cout_total": resultat.cout_total_mensuel,
            }
        )
    if resultats:
        moins_cher = min(resultats, key=lambda r: r["cout_total"])
        for ligne in resultats:
            ligne["recommandé"] = ligne is moins_cher
    return resultats
