"""Données opérationnelles Subakoua lues dans les études achetées (`erp_etudes`).

Le centre de décision doit raisonner sur le BOM, les stocks, les prix d'achat,
les volumes et les scores RH **réellement publiés par le jeu**. Upstream, ces
valeurs étaient reconstruites à partir d'un jeu de coefficients figés
(`constants.DEFAULT_NOMENCLATURE`) écrit avec des libellés maison
(« Néoprène 3 », « Renforts »…) qui ne correspondent à aucun `objectRef`
Subakoua (« NEOPRENE_3 », « REINFORCEMENTS »…). La comparaison échouait donc
silencieusement et les besoins de production tombaient à zéro : aucun achat
n'était jamais proposé.

Ce module ne fait qu'une chose : traduire les payloads des études en
structures exploitables, en gardant pour chaque valeur sa clé d'origine.
Aucune valeur n'est inventée, aucun ratio n'est supposé. Cuando une étude est
absente, la fonction concernée renvoie un dictionnaire vide et l'appelant
repart sur sa source historique existante.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from constants import PRODUCT_ID_TO_NAME

# ---------------------------------------------------------------------------
# Identifiants d'études
# ---------------------------------------------------------------------------

STUDY_BOM = "enprgeqtmpdb"            # Matières : disponibilités et besoins théoriques
STUDY_STOCKS = "enapgeqlquesmp"       # Qualité des entrées et sorties des matières
STUDY_PURCHASES = "peeedcahm"         # Achats et variations de stocks
STUDY_COSTS = "productionCostDetailStudy"  # Calcul des coûts de production
STUDY_SALES = "ensaacvm"              # Ventes mensuelles
STUDY_ATELIERS = "enprgeqtatdb"       # Ateliers : disponibilités et besoins théoriques
STUDY_PROCESSUS = "enprgeqtpp"        # Processus de production (bornes min/norm/max)

STUDY_RH_PRODUCTION = "enprgeqlevss"
STUDY_RH_APPRO = "enapgeqlevss"
STUDY_RH_ADMIN = "enafgeqlevss"

#: Pôles RH, dans l'ordre d'affichage du simulateur.
POLES_RH = (
    ("Production", STUDY_RH_PRODUCTION),
    ("Approvisionnement", STUDY_RH_APPRO),
    ("Administration", STUDY_RH_ADMIN),
)

#: `objectRef` Subakoua -> libellé historique du simulateur.
#: Le libellé historique reste utilisé pour la table `Historique_Equipe`
#: (colonnes Stock_Neo3…) et pour `Parametres_Nomenclature` ; la clé de
#: lecture dans les études est toujours l'`objectRef`.
MATERIAL_ID_TO_NAME = {
    "NEOPRENE_3": "Néoprène 3",
    "NEOPRENE_5": "Néoprène 5",
    "NEOPRENE_7": "Néoprène 7",
    "REINFORCEMENTS": "Renforts",
    "SLEEVES": "Manchons",
    "CLOSURES": "Fermetures",
}

#: Libellé historique -> `objectRef` (sens inverse, pour les tariffaires).
NAME_TO_MATERIAL_ID = {v: k for k, v in MATERIAL_ID_TO_NAME.items()}

#: `objectRef` -> colonne de stock de la table `Historique_Equipe`.
#: Repli utilisé quand l'étude « disponibilités et besoins » n'est pas
#: encore achetée pour M-1.
MATERIAL_REF_TO_STOCK_COL = {
    "NEOPRENE_3": "Stock_Neo3",
    "NEOPRENE_5": "Stock_Neo5",
    "NEOPRENE_7": "Stock_Neo7",
    "REINFORCEMENTS": "Stock_Renforts",
    "SLEEVES": "Stock_Manchons",
    "CLOSURES": "Stock_Fermetures",
}

#: Atelier Subakoua -> libellé du simulateur.
WORKSHOP_ID_TO_NAME = {
    "CUTTING": "Découpe",
    "ASSEMBLY": "Assemblage",
    "PACKAGING": "Conditionnement",
}


# ---------------------------------------------------------------------------
# Accès de base
# ---------------------------------------------------------------------------

def _etude(etudes: Optional[Mapping[str, Any]], study_id: str) -> Mapping[str, Any]:
    """Payload d'une étude, qu'elle soit indexée par `etude_<id>` ou par son id."""
    if not isinstance(etudes, Mapping):
        return {}
    node = etudes.get(f"etude_{study_id}")
    if isinstance(node, Mapping) and node:
        return node
    for key, value in etudes.items():
        if not isinstance(value, Mapping) or not value:
            continue
        if str(value.get("studyId", "")) == study_id:
            return value
        if str(key).casefold() in {f"etude_{study_id}".casefold(), study_id.casefold()}:
            return value
    return {}


def _nombre(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("value")
        if value is None:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valeur(node: Any, cle: str) -> Optional[float]:
    """Lit un montant publié par le jeu.

    Le jeu présente tantôt un nombre brut (`85.0`), tantôt un nœud
    `{"value": 85.0, "display": true}`. Les deux formes sont acceptées.
    """
    if isinstance(node, Mapping):
        return _nombre(node.get(cle))
    return _nombre(node)


# ---------------------------------------------------------------------------
# Nomenclature (BOM)
# ---------------------------------------------------------------------------

def bom_unitaire(etudes: Optional[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Consommation unitaire réelle par produit et par matière.

    L'étude « Matières : disponibilités et besoins théoriques » publie, pour
    chaque produit, la consommation **unitaire** de chaque matière : c'est le
    seul BOM authentique du jeu, et il remplace les coefficients estimés.

    Retourne ``{objectRef produit: {objectRef matière: quantité unitaire}}``.
    """
    payload = _etude(etudes, STUDY_BOM)
    produits = payload.get("listEnprgeqtmpdbMoisEqProd")
    if not isinstance(produits, list):
        return {}
    bom: dict[str, dict[str, float]] = {}
    for produit in produits:
        if not isinstance(produit, Mapping):
            continue
        ref = str(produit.get("name") or "")
        lignes = produit.get("listEnprgeqtmpdbMoisEqMatProd")
        if not ref or not isinstance(lignes, list):
            continue
        coefficients: dict[str, float] = {}
        for ligne in lignes:
            if not isinstance(ligne, Mapping):
                continue
            mat = str(ligne.get("nom") or "")
            qte = _nombre(ligne.get("consommation_unitaire"))
            if mat and qte is not None and qte > 0:
                coefficients[mat] = qte
        if coefficients:
            bom[ref] = coefficients
    return bom


def bom_regime(etudes: Optional[Mapping[str, Any]], regime: str = "normal") -> dict[str, dict[str, float]]:
    """Nomenclature théorique par régime matière : « minimum », « normal », « maximum ».

    L'étude « Processus de production » publie, pour chaque produit et chaque
    matière, les trois bornes de consommation (quminmp / qunormp / qumaxmp).
    C'est ce sont ces bornes que le levier « Régime matière » du simulateur
    doit piloter : sans elles, le régime ne pouvait qu'appliquer des
    coefficients inventés.
    """
    payload = _etude(etudes, STUDY_PROCESSUS)
    produits = payload.get("mapEnprgeqtppProduit")
    if not isinstance(produits, Mapping):
        return {}
    champ = {"minimum": "quminmp", "normal": "qunormp", "maximum": "qumaxmp"}.get(regime)
    if champ is None:
        return {}
    out: dict[str, dict[str, float]] = {}
    for ref, definition in produits.items():
        if not isinstance(definition, Mapping):
            continue
        coefficients: dict[str, float] = {}
        for phase in ("enprgeqtppProduitMatierePH1", "enprgeqtppProduitMatierePH2", "enprgeqtppProduitMatierePHF"):
            lignes = definition.get(phase)
            if not isinstance(lignes, list):
                continue
            for ligne in lignes:
                if not isinstance(ligne, Mapping):
                    continue
                mat = str(ligne.get("nom") or "")
                qte = _nombre(ligne.get(champ))
                if mat and qte is not None and qte > 0:
                    coefficients[mat] = qte
        if coefficients:
            out[str(ref)] = coefficients
    return out


def volumes_produits(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Volumes produits dans la période de l'étude, par `objectRef` produit."""
    payload = _etude(etudes, STUDY_BOM)
    produits = payload.get("listEnprgeqtmpdbMoisEqProd")
    if not isinstance(produits, list):
        return {}
    out: dict[str, float] = {}
    for produit in produits:
        if isinstance(produit, Mapping):
            ref = str(produit.get("name") or "")
            qte = _nombre(produit.get("obj"))
            if ref and qte is not None:
                out[ref] = qte
    return out


# ---------------------------------------------------------------------------
# Stocks et prix
# ---------------------------------------------------------------------------

def stocks_cloture(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Stock de matière restant en clôture de la période de l'étude.

    Pour le mois M, c'est le stock d'ouverture de M — donc la base sur laquelle
    le MRP calcule le besoin net. Le jeu le publie de deux façons
    concordantes dans « Matières : disponibilités et besoins théoriques » :
    le champ `ecarts` (solde restant) et `qenmp` (disponibilité totale)
    moins `consommationTotale` (sorties de fabrication). On lit `ecarts` et on
    ne retient une valeur que si les deux formulations concordent, pour éviter
    de propager une incohérence du document.

    À défaut, on reconstruit depuis « Qualité des entrées et sorties des
    matières » : stock initial + achats fournisseurs − sorties.

    Les matières écartées pour incohérence sont listées par
    ``stocks_incoherentes`` : le simulateur les affiche au lieu de les traiter
    silencieusement comme si leur stock était nul.
    """
    besoins = _etude(etudes, STUDY_BOM).get("listEnprgeqtmpdbMoisEqMat")
    stocks: dict[str, float] = {}
    incoherentes: list[str] = []
    if isinstance(besoins, list):
        for ligne in besoins:
            if not isinstance(ligne, Mapping):
                continue
            mat = str(ligne.get("nom") or "")
            ecarts = _nombre(ligne.get("ecarts"))
            dispo = _nombre(ligne.get("qenmp"))
            conso = _nombre(ligne.get("consommationTotale"))
            if not mat or ecarts is None:
                continue
            if dispo is not None and conso is not None:
                if abs((dispo - conso) - ecarts) > max(1.0, abs(ecarts) * 0.01):
                    # Les deux formulations du jeu se contredisent : plutôt que
                    # de trancher en silence, on écarte la matière et on laisse
                    # l'appelant décider du repli (et prévenir l'utilisateur).
                    incoherentes.append(mat)
                    continue
            if ecarts >= 0:
                stocks[mat] = ecarts
    if stocks:
        return stocks
    qualites = _etude(etudes, STUDY_STOCKS).get("mapEnapgeqlquesmpResMoisEqMat")
    if isinstance(qualites, Mapping):
        for mat, detail in qualites.items():
            if not isinstance(detail, Mapping):
                continue
            entrees = _valeur(detail.get("totalIns"), "quantity")
            sorties = _nombre(detail.get("qsomp"))
            if entrees is None:
                continue
            solde = entrees - (sorties or 0.0)
            if solde >= 0:
                stocks[str(mat)] = solde
    return stocks


def couts_unitaires_matieres(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Coût unitaire consommé par matière, d'après l'étude de coûts de production.

    L'étude publie, pour chaque produit et chaque phase, le coût unitaire réel
    de la matière consommée. La valeur est constante pour une matière donnée
    (le premier relevé fait foi) : elle sert de valuation de repli lorsque le
    prix d'achat moyen n'est pas renseigné — ce qui est le cas des pièces
    achetées au jeu (`prixAchatMoyenFournisseur` à 0) alors qu'elles consomment
    bien du stock.
    """
    payload = _etude(etudes, STUDY_COSTS)
    produits = payload.get("productsProductionCostDetails")
    if not isinstance(produits, list):
        return {}
    out: dict[str, float] = {}
    for produit in produits:
        if not isinstance(produit, Mapping):
            continue
        detail = produit.get("directCostDetail") or {}
        phases = detail.get("phasesCostDetails")
        if not isinstance(phases, list):
            continue
        for phase in phases:
            if not isinstance(phase, Mapping):
                continue
            materiaux = phase.get("materialsCosts")
            if not isinstance(materiaux, list):
                continue
            for ligne in materiaux:
                if not isinstance(ligne, Mapping):
                    continue
                mat = str(ligne.get("objectRef") or "")
                cout = _nombre(ligne.get("unitCost"))
                if mat and cout and cout > 0 and mat not in out:
                    out[mat] = cout
    return out


def stocks_incoherentes(etudes: Optional[Mapping[str, Any]]) -> list[str]:
    """Matières dont les deux formulations de stock du jeu se contredisent."""
    besoins = _etude(etudes, STUDY_BOM).get("listEnprgeqtmpdbMoisEqMat")
    out: list[str] = []
    if not isinstance(besoins, list):
        return out
    for ligne in besoins:
        if not isinstance(ligne, Mapping):
            continue
        mat = str(ligne.get("nom") or "")
        ecarts = _nombre(ligne.get("ecarts"))
        dispo = _nombre(ligne.get("qenmp"))
        conso = _nombre(ligne.get("consommationTotale"))
        if not mat or ecarts is None or dispo is None or conso is None:
            continue
        if abs((dispo - conso) - ecarts) > max(1.0, abs(ecarts) * 0.01):
            out.append(mat)
    return out


def prix_achat_unitaire(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Prix d'achat unitaire moyen réellement payé, par matière.

    Lue dans « Achats et variations de stocks » (`prixAchatMoyenFournisseur`).
    Sert de repli lorsque le tarifaire fournisseurs ne couvre pas une matière :
    sans ce repli, une ligne sans tarif disparaissait silencieusement du plan
    d'approvisionnement.
    """
    payload = _etude(etudes, STUDY_PURCHASES)
    lignes = payload.get("listePeeedcahMatiere")
    out: dict[str, float] = {}
    if isinstance(lignes, list):
        for ligne in lignes:
            if not isinstance(ligne, Mapping):
                continue
            mat = str(ligne.get("nom") or "")
            prix = _nombre(ligne.get("prixAchatMoyenFournisseur")) or _nombre(ligne.get("prixAchatMoy"))
            if mat and prix and prix > 0:
                out[mat] = prix
    # Valorisation de repli pour les matières sans prix d'achat moyen publié.
    for mat, cout in couts_unitaires_matieres(etudes).items():
        if mat not in out and cout > 0:
            out[mat] = cout
    return out


def sources_prix(etudes: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """Origine du prix retenu par matière, pour l'affichage du plan d'achat."""
    sources: dict[str, str] = {}
    payload = _etude(etudes, STUDY_PURCHASES)
    lignes = payload.get("listePeeedcahMatiere")
    if isinstance(lignes, list):
        for ligne in lignes:
            if not isinstance(ligne, Mapping):
                continue
            mat = str(ligne.get("nom") or "")
            prix = _nombre(ligne.get("prixAchatMoyenFournisseur")) or _nombre(ligne.get("prixAchatMoy"))
            if mat and prix and prix > 0:
                sources[mat] = "Prix d'achat moyen M-1"
    for mat in couts_unitaires_matieres(etudes):
        sources.setdefault(mat, "Coût unitaire consommé M-1")
    return sources


# ---------------------------------------------------------------------------
# Ventes
# ---------------------------------------------------------------------------

def ventes_mensuelles(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Volumes vendus sur le mois couvert par l'étude, par `objectRef` produit.

    L'étude « Ventes mensuelles » porte un tableau de 12 mois ; seule sa
    dernière ligne — le mois même du document — est retenue.
    """
    payload = _etude(etudes, STUDY_SALES)
    ventes = payload.get("sales")
    if not isinstance(ventes, list) or not ventes:
        return {}
    dernier = max(
        (v for v in ventes if isinstance(v, Mapping) and v.get("month") is not None),
        key=lambda v: v.get("month"),
        default=None,
    )
    if dernier is None:
        return {}
    lignes = dernier.get("monthlySalesByProducts")
    if not isinstance(lignes, list):
        return {}
    out: dict[str, float] = {}
    for ligne in lignes:
        if not isinstance(ligne, Mapping):
            continue
        ref = str(ligne.get("productId") or "")
        qte = _nombre(ligne.get("sales"))
        if ref and qte is not None:
            out[ref] = qte
    return out


# ---------------------------------------------------------------------------
# Ateliers
# ---------------------------------------------------------------------------

def capacites_ateliers(etudes: Optional[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Capacité et consommation des ateliers pour la période de l'étude."""
    payload = _etude(etudes, STUDY_ATELIERS)
    lignes = payload.get("listEnprgeqtatdbMoisEqAt")
    if not isinstance(lignes, list):
        return {}
    out: dict[str, dict[str, float]] = {}
    for ligne in lignes:
        if not isinstance(ligne, Mapping):
            continue
        atelier = str(ligne.get("nom") or "")
        capa = _nombre(ligne.get("capa"))
        nb = _nombre(ligne.get("nbpo"))
        conso = _nombre(ligne.get("consommationTotale"))
        if not atelier or capa is None:
            continue
        out[atelier] = {
            "capacite_machine": capa,
            "nb_machines": nb or 0.0,
            "capacite_totale": _nombre(ligne.get("scapat")) or capa * (nb or 0.0),
            "consommation": conso or 0.0,
        }
    return out


# ---------------------------------------------------------------------------
# RH
# ---------------------------------------------------------------------------

def audits_rh(etudes: Optional[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Audit RH par pôle : score qualité du personnel et éléments de calcul.

    L'étude « Audit du service » publie pour chaque pôle un score
    `totalStaffQuality` ainsi que sa décomposition (`payrollRating`,
    `dispersionRating`, masse salariale, norme, activité réelle). On restitue
    l'ensemble pour que le simulateur affiche le score **de chaque pôle** et
    puisse comparer une décision de masse salariale à la norme du jeu.
    """
    out: dict[str, dict[str, float]] = {}
    for pole, study_id in POLES_RH:
        payload = _etude(etudes, study_id)
        if not payload:
            continue
        score = _valeur(payload.get("totalStaffQuality"), "value")
        detail = {
            "score": score if score is not None else 0.0,
            "masse_salariale": _nombre(payload.get("teamPayroll")) or 0.0,
            "masse_salariale_norme": _nombre(payload.get("standardPayroll")) or 0.0,
            "note_masse_salariale": _nombre(payload.get("payrollRating")) or 0.0,
            "note_dispersion": _nombre(payload.get("dispersionRating")) or 0.0,
            "indice_dispersion": _nombre(payload.get("dispersionIndex")) or 0.0,
            "activite_reelle": _nombre(payload.get("realActivity")) or 0.0,
        }
        satisfaction = payload.get("staffSatisfaction") or {}
        if isinstance(satisfaction, Mapping):
            detail["satisfaction_service"] = _nombre(satisfaction.get("serviceQuality")) or 0.0
            detail["satisfaction_personnel"] = _nombre(satisfaction.get("serviceSatisfaction")) or 0.0
        out[pole] = detail
    return out


# ---------------------------------------------------------------------------
# Aides de conversion
# ---------------------------------------------------------------------------

def bom_par_libelle(etudes: Optional[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """BOM indexé par le libellé du simulateur (« Shorty 3 »…)."""
    out: dict[str, dict[str, float]] = {}
    for ref, coefficients in bom_unitaire(etudes).items():
        nom = PRODUCT_ID_TO_NAME.get(ref, ref)
        out[nom] = {MATERIAL_ID_TO_NAME.get(m, m): qte for m, qte in coefficients.items()}
    return out


def bom_regime_par_libelle(
    etudes: Optional[Mapping[str, Any]], regime: str = "normal"
) -> dict[str, dict[str, float]]:
    """BOM de régime indexé par le libellé du simulateur."""
    out: dict[str, dict[str, float]] = {}
    for ref, coefficients in bom_regime(etudes, regime).items():
        nom = PRODUCT_ID_TO_NAME.get(ref, ref)
        out[nom] = {MATERIAL_ID_TO_NAME.get(m, m): qte for m, qte in coefficients.items()}
    return out


def bom_effective_par_libelle(
    etudes: Optional[Mapping[str, Any]], regime: str = "normal"
) -> tuple[dict[str, dict[str, float]], str]:
    """Nomenclature à utiliser, avec l'origine de la valeur.

    Le jeu publie deux nomenclatures complémentaires : les bornes
    min/normal/max du « Processus de production » (celles que pilote le
    régime matière) et la consommation réellement appliquée dans
    « Matières :abilités et besoins théoriques ». On préfère les bornes, mais
    si cette étude n'a pas été achetée on retombe sur la consommation réelle
    plutôt que de n'afficher aucun besoin.
    """
    bornes = bom_regime_par_libelle(etudes, regime)
    if bornes:
        return bornes, "bornes min/normal/max (Processus de production)"
    reel = bom_par_libelle(etudes)
    if reel:
        return reel, "consommation réelle (disponibilités et besoins théoriques)"
    return {}, ""


def volumes_par_libelle(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Volumes produits indexés par le libellé du simulateur."""
    return {PRODUCT_ID_TO_NAME.get(ref, ref): qte for ref, qte in volumes_produits(etudes).items()}


def ventes_par_libelle(etudes: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """Ventes du mois indexées par le libellé du simulateur."""
    return {PRODUCT_ID_TO_NAME.get(ref, ref): qte for ref, qte in ventes_mensuelles(etudes).items()}
