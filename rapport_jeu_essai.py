"""Jeu d'essai annuel déterministe pour le rapport professionnel (tests uniquement).

Il imite une année de jeu complète (Année 1, janvier → décembre) avec les
formats réels des sources : études du portail (`erp_etudes`), modules
`erp_donnees` et tables de saisie. Les valeurs sont **fictives** : elles ne
servent qu'à vérifier que le rapport se construit de bout en bout, que les
sources sont bien hiérarchisées et que les calculs (sommes, variations,
décomposition prix / volume / mix) sont exacts.

Particularités volontaires :
* les ventes par produit ne sont publiées que par l'étude « Ventes
  mensuelles » de **décembre** (tableau de 12 mois) — le rapport doit
  reconstituer toute l'année à partir de ce seul document ;
* janvier est **déficitaire** ; deux machines de découpe sont achetées en
  mars (financées par un emprunt) ; le prix de l'Integral 7 baisse en avril ;
* le benchmark concurrentiel (étude « Comparaison des parts de marché ») est
  publié en décembre.
"""
from __future__ import annotations

import json
from typing import Any

from constants import MONTH_NAMES, PRODUCT_ID_TO_NAME

ANNEE = 1
NUMERO = 3
PRODUITS_REF = ("SHORTY_C", "MONO_C", "SHORTY_T", "MONO_T", "MONO_F")
SAISON = (0.70, 0.75, 0.90, 1.00, 1.15, 1.30, 1.40, 1.35, 1.10, 0.90, 0.75, 0.70)
BASE_UNITES = {"SHORTY_C": 60, "MONO_C": 450, "SHORTY_T": 120, "MONO_T": 1300, "MONO_F": 2100}
PRIX_MARCHE = {"Shorty 3": 130.0, "Integral 3": 205.0, "Shorty 5": 240.0, "Integral 5": 310.0, "Integral 7": 360.0}
COUTS = {"SHORTY_C": 62.0, "MONO_C": 95.0, "SHORTY_T": 84.0, "MONO_T": 125.0, "MONO_F": 155.0}
TAUX_REMISE_MOYEN = 0.08


def periode(mois: int) -> str:
    return f"Année {ANNEE} - {MONTH_NAMES[mois - 1]}"


def id_mois(mois: int) -> int:
    """Identifiant de mois du portail (A1-Janvier = 13)."""
    return 12 * ANNEE + mois


def prix_du_mois(mois: int) -> dict[str, float]:
    prix = {"Shorty 3": 115.0, "Integral 3": 190.0, "Shorty 5": 220.0, "Integral 5": 280.0, "Integral 7": 375.0}
    if mois >= 4:
        prix["Integral 7"] = 360.0
    if mois >= 7:
        prix["Shorty 5"] = 230.0
    return prix


def unites_du_mois(mois: int) -> dict[str, int]:
    croissance = 1.0 + 0.02 * (mois - 1)
    return {ref: int(round(base * SAISON[mois - 1] * croissance)) for ref, base in BASE_UNITES.items()}


def ca_du_mois(mois: int) -> float:
    prix = prix_du_mois(mois)
    unites = unites_du_mois(mois)
    brut = sum(prix[PRODUCT_ID_TO_NAME[ref]] * q for ref, q in unites.items())
    return round(brut * (1 - TAUX_REMISE_MOYEN), 2)


def resultat_du_mois(mois: int) -> float:
    return round(0.32 * ca_du_mois(mois) - 270_000.0, 2)


def machines_du_mois(mois: int) -> dict[str, int]:
    return {"Decoupe": 10 + (2 if mois >= 3 else 0), "Assemblage": 16 + (1 if mois >= 6 else 0), "Cond": 4}


def pdm_du_mois(mois: int) -> float:
    valeurs = (12.5, 12.8, 13.1, 13.6, 13.2, 13.9, 14.3, 14.6, 14.8, 15.0, 15.1, 15.2)
    return valeurs[mois - 1]


def trajectoire_financiere() -> dict[int, dict[str, float]]:
    """Banque, dette et capitaux propres mois par mois (cohérents entre eux)."""
    banque, dette, capitaux = 1_200_000.0, 400_000.0, 2_000_000.0
    out: dict[int, dict[str, float]] = {}
    for mois in range(1, 13):
        initial = banque
        variation_bfr = 0.15 * (ca_du_mois(mois) - (ca_du_mois(mois - 1) if mois > 1 else ca_du_mois(1)))
        flux = resultat_du_mois(mois) + 40_000.0 - variation_bfr - 8_000.0
        dette -= 8_000.0
        if mois == 3:
            flux += 250_000.0 - 300_000.0     # emprunt + achat TTC de 2 machines de découpe
            dette += 250_000.0
        if mois == 6:
            flux -= 180_000.0                  # achat TTC d'une machine d'assemblage
        banque = round(banque + flux, 2)
        capitaux = round(capitaux + resultat_du_mois(mois), 2)
        out[mois] = {"initial": round(initial, 2), "final": banque, "dette": round(dette, 2), "capitaux": capitaux}
    return out


def _etude(mois: int, study_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"periode": periode(mois), "study_id": study_id, "read_allowed": 1,
            "payload": json.dumps({"studyId": study_id, "readAllowed": True, **payload}, ensure_ascii=False)}


def jeu_essai_annee() -> dict[str, Any]:
    """Lignes prêtes à insérer : ``{"erp_donnees": [...], "erp_etudes": [...], "tables": {...}}``."""
    finances = trajectoire_financiere()
    etudes: list[dict[str, Any]] = []
    donnees: list[dict[str, Any]] = []
    tables: dict[str, list[dict[str, Any]]] = {
        "Decisions_Marketing": [], "Parc_Machines_Mensuel": [], "Decisions_RH_Mensuel": [],
        "Finances_Mensuelles": [], "Ventes_Historique": [], "Historique_Equipe": [],
    }
    for mois in range(1, 13):
        f = finances[mois]
        ca = ca_du_mois(mois)
        cumul_ca = sum(ca_du_mois(m) for m in range(1, mois + 1))
        etudes.append(_etude(mois, "monitoring", {
            "marketShares": pdm_du_mois(mois),
            "monitoringTurnoverData": {"monthlySalesTurnover": [ca_du_mois(m) for m in range(1, mois + 1)]},
            "bankAccountAmountEvolution": [finances[m]["final"] for m in range(1, mois + 1)],
        }))
        etudes.append(_etude(mois, "bankStatements", {"initialBankAmount": f["initial"], "finalBankAmount": f["final"]}))
        etudes.append(_etude(mois, "companyResults", {
            "turnover": ca, "results": resultat_du_mois(mois), "equity": f["capitaux"],
            "cumTurnover": round(cumul_ca, 2), "cumResults": round(sum(resultat_du_mois(m) for m in range(1, mois + 1)), 2),
        }))
        etudes.append(_etude(mois, "peeedsbidt", {"empr16": f["dette"], "emprLibre": 0.0, "po215Net": 150_000.0}))
        # Ateliers : la consommation suit la saisonnalité ; la découpe sature en été.
        machines = machines_du_mois(mois)
        capa_machine = 9600.0
        etudes.append(_etude(mois, "enprgeqtatdb", {"listEnprgeqtatdbMoisEqAt": [
            {"nom": "CUTTING", "capa": capa_machine, "nbpo": machines["Decoupe"],
             "scapat": capa_machine * machines["Decoupe"],
             "consommationTotale": round(capa_machine * machines["Decoupe"] * min(0.99, 0.55 + 0.32 * SAISON[mois - 1] * (10 / machines["Decoupe"])), 1)},
            {"nom": "ASSEMBLY", "capa": capa_machine, "nbpo": machines["Assemblage"],
             "scapat": capa_machine * machines["Assemblage"],
             "consommationTotale": round(capa_machine * machines["Assemblage"] * (0.50 + 0.25 * SAISON[mois - 1]), 1)},
            {"nom": "PACKAGING", "capa": capa_machine, "nbpo": machines["Cond"],
             "scapat": capa_machine * machines["Cond"],
             "consommationTotale": round(capa_machine * machines["Cond"] * (0.30 + 0.15 * SAISON[mois - 1]), 1)},
        ]}))
        if mois in (3, 6, 9, 12):
            details = []
            for ref, cout in COUTS.items():
                n = unites_du_mois(mois)[ref]
                details.append({
                    "productRef": ref, "producedProductsNumber": n,
                    "directCostDetail": {"phasesCostDetails": [{
                        "materialsCosts": [{"objectRef": "NEOPRENE_5", "totalCost": round(cout * n * 0.62, 2)}],
                        "workshopsCosts": [{"objectRef": "CUTTING", "totalCost": round(cout * n * 0.28, 2)}],
                    }]},
                    "finalProductionPhaseCostDetail": {"workshopsCosts": [{"objectRef": "PACKAGING", "totalCost": round(cout * n * 0.10, 2)}]},
                })
            etudes.append(_etude(mois, "productionCostDetailStudy", {"productsProductionCostDetails": details}))
        for pole, sid, base in (("Production", "enprgeqlevss", 72.0), ("Approvisionnement", "enapgeqlevss", 65.0),
                                ("Administration", "enafgeqlevss", 46.0)):
            etudes.append(_etude(mois, sid, {"totalStaffQuality": {"value": base + (6.0 if mois >= 6 and pole == "Production" else 0.0)},
                                             "teamPayroll": 40_000.0, "standardPayroll": 42_000.0}))
        # Prix du marché publiés dans le module expert-comptable (structure réelle : liste de tableaux).
        hausse = 1 + 0.01 * ((mois - 1) // 3)
        prix_m = sorted(round(v * hausse, 2) for v in PRIX_MARCHE.values())
        donnees.append({"periode": periode(mois), "module": "expert_comptable", "contenu": json.dumps({
            "Détail des comptes": {"Expert-comptable": [{"Ventes sur le marché concurrentiel": [
                {"Combinaisons": str(i + 1), "Prix HT (€)": v} for i, v in enumerate(prix_m)]}]},
        }, ensure_ascii=False)})

        prix = prix_du_mois(mois)
        tables["Decisions_Marketing"].append({
            "Tour_ID": mois, "Mois": periode(mois),
            "Prix_S3": prix["Shorty 3"], "Prix_I3": prix["Integral 3"], "Prix_S5": prix["Shorty 5"],
            "Prix_I5": prix["Integral 5"], "Prix_I7": prix["Integral 7"],
            "Budget_Marque": 60_000.0 if mois < 5 else 70_000.0,
            "Axe_Principal": "Durée de vie", "Axe_Accessoire": "Souplesse",
            "Pub_S3": 1_000.0, "Pub_I3": 3_000.0, "Pub_S5": 1_500.0, "Pub_I5": 4_000.0, "Pub_I7": 4_500.0,
        })
        tables["Parc_Machines_Mensuel"].append({
            "Tour_ID": mois, "Mois": periode(mois),
            "Nb_Machines_Decoupe": machines["Decoupe"], "Nb_Machines_Assemblage": machines["Assemblage"],
            "Nb_Machines_Cond": machines["Cond"],
            "Acquisition_Faite": 1 if mois in (3, 6) else 0,
            "Acq_Decoupe": 2 if mois == 3 else 0, "Acq_Assemblage": 1 if mois == 6 else 0, "Acq_Cond": 0,
            "Cession_Faite": 0, "Ced_Decoupe": 0, "Ced_Assemblage": 0, "Ced_Cond": 0,
        })
        tables["Decisions_RH_Mensuel"].append({
            "Tour_ID": mois, "Mois": periode(mois),
            "Eff_Employes_Prod": 15 + (3 if mois >= 6 else 0), "Sal_Employes_Prod": 2000.0,
            "Eff_Cadres_Prod": 3, "Sal_Cadres_Prod": 3333.33, "Eff_Directeurs_Prod": 1, "Sal_Directeurs_Prod": 4500.0,
            "Eff_Employes_Appro": 4, "Sal_Employes_Appro": 1900.0, "Eff_Cadres_Appro": 1, "Sal_Cadres_Appro": 3200.0,
            "Eff_Directeurs_Appro": 1, "Sal_Directeurs_Appro": 4300.0,
            "Eff_Employes_Admin": 5, "Sal_Employes_Admin": 1950.0, "Eff_Cadres_Admin": 2, "Sal_Cadres_Admin": 3300.0,
            "Eff_Directeurs_Admin": 1, "Sal_Directeurs_Admin": 5000.0,
        })

    # Ventes : un seul document, celui de décembre, publie les 12 mois.
    etudes.append(_etude(12, "ensaacvm", {"sales": [
        {"month": id_mois(m), "monthlySalesByProducts": [
            {"productId": ref, "sales": q} for ref, q in unites_du_mois(m).items()]}
        for m in range(1, 13)
    ]}))
    # Benchmark concurrentiel de décembre (6 entreprises).
    concurrents = [
        {"Entreprise": "1", "Ventes": 5200, "part de marché": 16.1, "Prix (€)": 300.0},
        {"Entreprise": "2", "Ventes": 3900, "part de marché": 12.0, "Prix (€)": 275.0},
        {"Entreprise": str(NUMERO), "Ventes": sum(unites_du_mois(12).values()), "part de marché": pdm_du_mois(12), "Prix (€)": 285.0},
        {"Entreprise": "4", "Ventes": 3100, "part de marché": 9.6, "Prix (€)": 290.0},
        {"Entreprise": "5", "Ventes": 2800, "part de marché": 8.7, "Prix (€)": 260.0},
        {"Entreprise": "6", "Ventes": 2400, "part de marché": 7.4, "Prix (€)": 310.0},
    ]
    etudes.append(_etude(12, "pevcvipm", {"Comparaison des parts de marché": concurrents}))
    for i, row in enumerate(etudes, start=1):
        row["id"] = i
    for i, row in enumerate(donnees, start=1):
        row["id"] = i
        row["type_donnee"] = "etat_actuel"
    return {"erp_donnees": donnees, "erp_etudes": etudes, "tables": tables}
