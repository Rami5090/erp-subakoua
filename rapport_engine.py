"""Rapport professionnel Subakoua — audit stratégique et opérationnel de l'année.

Moteur PUR : aucune dépendance Streamlit ni SQL. Il reçoit les données déjà
lues en base (modules ``erp_donnees``, études ``erp_etudes``, tables de saisie)
et produit tout ce que le rapport professionnel doit montrer :

* les **séries mensuelles** de la période auditée (finance, commerce,
  production, RH), chaque valeur portant sa **source** ;
* une **synthèse annuelle**, l'analyse **par produit** et la **décomposition
  prix / volume / mix** de l'évolution du chiffre d'affaires ;
* la **chronologie des décisions** réellement prises par l'équipe ;
* des **constats** automatiques — description factuelle + piste d'analyse —
  et une **lecture stratégique proposée**, à valider par l'équipe ;
* la liste des **données manquantes** et un **contrôle de complétude** du
  rapport au regard des attendus de l'évaluation (partie collective et
  partie individuelle).

Doctrine (identique au reste de l'ERP) : aucune valeur n'est inventée ; une
donnée absente reste absente et est signalée ; chaque chiffre est traçable.
Les montants sont convertis en ``float`` pour la présentation (arrondis à
l'affichage) : les calculs comptables restent faits en ``Decimal`` dans
``finance_engine``, qui est réutilisé tel quel pour que le rapport affiche
les mêmes chiffres que le cockpit.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from constants import MONTH_NAMES, PERIOD_INDEX, PRODUCTS, PRODUCT_KEYS, PRODUCT_KEY_TO_NAME, TOURS_PERIODES
from decisions_parser import parse_decisions
from finance_engine import extract_financial_state
from forecast_engine import _json_load, _unwrap_api_payload, extract_competitive_snapshot
from livrable_finance import couts_from_cost_study, prix_marche_par_produit, table_bilan_detaille
from period_context import normalize_period
import study_ops

# ---------------------------------------------------------------------------
# Indicateurs mensuels : clé -> (libellé, unité)
# ---------------------------------------------------------------------------
CA = "ca"
RESULTAT = "resultat_net"
CAF = "caf"
TRESORERIE = "tresorerie"
TRESORERIE_OUVERTURE = "tresorerie_ouverture"
EPARGNE = "epargne"
CAPITAUX_PROPRES = "capitaux_propres"
DETTE = "dette_bancaire"
BFR = "bfr"
PDM = "pdm"
PUB_MARQUE = "pub_marque"
PUB_PRODUITS = "pub_produits"
EFFECTIFS = "effectifs"
MASSE_SALARIALE = "masse_salariale"
VENTES_TOTALES = "ventes_totales"
PRODUCTION_TOTALE = "production_totale"
STOCKS_PRODUITS = "stocks_produits"
STOCKS_MATIERES = "stocks_matieres"
CA_CUMULE_PUBLIE = "ca_cumule_publie"

INDICATEURS: dict[str, tuple[str, str]] = {
    CA: ("Chiffre d'affaires", "€"),
    RESULTAT: ("Résultat net", "€"),
    CAF: ("Capacité d'autofinancement", "€"),
    TRESORERIE: ("Trésorerie (banque, fin de mois)", "€"),
    TRESORERIE_OUVERTURE: ("Trésorerie d'ouverture", "€"),
    EPARGNE: ("Compte épargne", "€"),
    CAPITAUX_PROPRES: ("Capitaux propres", "€"),
    DETTE: ("Dette bancaire", "€"),
    BFR: ("Besoin en fonds de roulement", "€"),
    PDM: ("Part de marché", "%"),
    PUB_MARQUE: ("Publicité de marque", "€"),
    PUB_PRODUITS: ("Publicité produits", "€"),
    EFFECTIFS: ("Effectif total", "pers."),
    MASSE_SALARIALE: ("Masse salariale brute mensuelle", "€"),
    VENTES_TOTALES: ("Ventes totales", "u"),
    PRODUCTION_TOTALE: ("Production totale", "u"),
    STOCKS_PRODUITS: ("Stock de produits finis", "€"),
    STOCKS_MATIERES: ("Stock de matières", "€"),
    CA_CUMULE_PUBLIE: ("CA cumulé publié par le jeu", "€"),
}

ATELIERS = ("Découpe", "Assemblage", "Conditionnement")
_ALIAS_ATELIERS = {
    "cutting": "Découpe", "decoupe": "Découpe", "découpe": "Découpe",
    "assembly": "Assemblage", "assemblage": "Assemblage",
    "packaging": "Conditionnement", "conditionnement": "Conditionnement", "cond": "Conditionnement",
}
#: Colonnes de `Parc_Machines_Mensuel` par atelier : (parc, acquisitions, cessions).
_COLONNES_PARC = {
    "Découpe": ("Nb_Machines_Decoupe", "Acq_Decoupe", "Ced_Decoupe"),
    "Assemblage": ("Nb_Machines_Assemblage", "Acq_Assemblage", "Ced_Assemblage"),
    "Conditionnement": ("Nb_Machines_Cond", "Acq_Cond", "Ced_Cond"),
}

#: Études conseillées quand une donnée manque (identifiant, libellé, prix indicatif en €).
#: Le prix réel est relu dans le catalogue live par la page quand il est disponible.
ETUDES_CONSEILLEES: dict[str, tuple[tuple[str, str, float], ...]] = {
    CA: (("monitoring", "Tableau de bord", 0.0), ("companyResults", "Résultats", 100.0)),
    RESULTAT: (("companyResults", "Résultats", 100.0), ("peeedscrsm", "Compte de résultat simplifié", 100.0)),
    TRESORERIE: (("bankStatements", "Relevé bancaire", 0.0),),
    VENTES_TOTALES: (("ensaacvm", "Ventes mensuelles", 0.0),),
    PDM: (("monitoring", "Tableau de bord", 0.0),),
    CAPITAUX_PROPRES: (("companyResults", "Résultats", 100.0), ("peeedsbidt", "Bilan version détaillée", 100.0)),
    "utilisation": (("enprgeqtatdb", "Ateliers : disponibilités et besoins théoriques", 100.0),),
    "scores_rh": (("enprgeqlevss", "Audit du service / production", 200.0),),
    "couts": (("productionCostDetailStudy", "Calcul des coûts de production", 50.0),),
}

_SOURCE_COMPTA = "Dossier comptable (module expert-comptable / études comptables achetées)"


# ---------------------------------------------------------------------------
# Outils de l'ERP (restitution « outils et méthodes » du rapport)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OutilERP:
    nom: str
    page: str
    besoin: str
    methode: str
    apport: str


OUTILS_ERP: tuple[OutilERP, ...] = (
    OutilERP("Ingestion & achats documentaires", "Scraper",
             "Disposer des données du jeu sans ressaisie et acheter les bonnes études.",
             "Client API authentifié + robot Playwright ; plan d'achat par utilité marginale sous budget (sac à dos).",
             "Données fiables et traçables ; budget d'études maîtrisé."),
    OutilERP("Centre de décision (simulateur intégré)", "Cockpit",
             "Mesurer l'effet d'une décision sur toute l'entreprise avant de la valider.",
             "Propagation marché → production → MRP2 → RH → compte de résultat, BFR et trésorerie (calcul en Decimal).",
             "Décisions testées sur la trésorerie et la capacité avant envoi."),
    OutilERP("Pilotage stratégique 12 mois", "Pilotage 12M",
             "Anticiper la demande et l'écart à l'objectif de part de marché.",
             "Lissage exponentiel (Holt-Winters / Holt amorti) choisi par validation hors-échantillon (MAE, WAPE).",
             "Objectifs de volume réalistes, sans fuite d'information future."),
    OutilERP("Optimiseur stratégique", "Optimiseur",
             "Trouver la combinaison prix / qualité / publicité qui maximise la part de marché réalisable.",
             "Calibration ridge descriptive + exploration d'une grille sous contraintes de budget et de capacité.",
             "Scénarios classés, demande non servie et taux de service visibles."),
    OutilERP("Plan d'approvisionnement (MRP2)", "Cockpit — Achats",
             "Commander la bonne quantité de matières au bon moment.",
             "Besoins bruts par nomenclature, stock de sécurité, arbitrage fournisseur prix / délai, coût de possession.",
             "Ruptures évitées sans sur-stockage."),
    OutilERP("Analyse quantitative (seuil de rentabilité)", "Analyse quantitative",
             "Savoir combien vendre pour couvrir les charges fixes et quel mix privilégier.",
             "Point mort, marge de sécurité, mix sous contrainte de capacité par atelier, sensibilité au prix.",
             "Prix planchers et volumes cibles chiffrés."),
    OutilERP("Prévision des cours", "Prévision des cours",
             "Arbitrer les placements (actions, obligations) et suivre les prix de marché.",
             "Cinq méthodes simples comparées par backtest ; bande d'erreur empirique.",
             "Placements de trésorerie argumentés."),
    OutilERP("Livrable Finance mensuel", "Livrable Finance",
             "Produire la mission spécifique Finance sans ressaisie.",
             "Diagnostic bilan / résultat / marges calculé depuis les études ; remplissage automatique du PDF.",
             "Livrable mensuel juste et à l'heure."),
    OutilERP("Rapport professionnel (ce document)", "Rapport professionnel",
             "Restituer l'audit stratégique et opérationnel de l'année.",
             "Consolidation mensuelle traçable, constats automatiques, graphiques et tableaux commentés, export Word.",
             "Un rapport chiffré, cohérent avec le cockpit, prêt à être complété par l'équipe."),
)

#: Outils et méthodes de gestion de projet proposés à l'équipe (descriptions éditables).
OUTILS_GP: dict[str, str] = {
    "Rétroplanning / diagramme de Gantt": "Planification des tours de décision et des livrables à partir des échéances.",
    "Tableau Kanban (Trello, Planner…)": "Suivi visuel des tâches « à faire / en cours / fait » par fonction.",
    "Matrice RACI": "Clarification de qui Réalise, Approuve, est Consulté ou Informé pour chaque décision.",
    "Réunion de décision à chaque tour": "Arbitrage collectif des décisions avant la clôture du mois.",
    "Tableau de bord partagé (ERP)": "Indicateurs communs consultés par toute l'équipe avant de décider.",
    "Versionnage du code (Git / GitHub)": "Historique traçable des évolutions de l'ERP et retour arrière possible.",
    "Messagerie d'équipe (Teams, Discord…)": "Coordination entre les séances et partage des documents.",
    "Retour d'expérience (REX) mensuel": "Analyse des écarts entre prévu et réalisé pour corriger le tir.",
}


# ---------------------------------------------------------------------------
# Conversions & formats (français)
# ---------------------------------------------------------------------------
_NBSP = " "


def _float(value: Any) -> float | None:
    """Nombre lisible ou None (bool, NaN, texte vide et illisible exclus)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        return _float(value.get("value"))
    try:
        if isinstance(value, str):
            texte = value.replace(" ", "").replace(_NBSP, "").replace(" ", "").replace("€", "").replace("%", "")
            if not texte or texte == "-":
                return None
            if "," in texte and "." in texte:
                texte = texte.replace(".", "").replace(",", ".")
            else:
                texte = texte.replace(",", ".")
            out = float(texte)
        else:
            out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return None if math.isnan(out) or math.isinf(out) else out


def _non_nul(value: Any) -> float | None:
    """Saisie manuelle : 0 signifie « non saisi » (convention de l'écran de saisie)."""
    v = _float(value)
    return v if v not in (None, 0.0) else None


def _grouper(entier: str) -> str:
    groupes = []
    while len(entier) > 3:
        groupes.insert(0, entier[-3:])
        entier = entier[:-3]
    groupes.insert(0, entier)
    return _NBSP.join(groupes)


def fmt_nombre(value: Any, decimales: int = 0) -> str:
    """1186400.5 -> '1 186 400' (espaces insécables, virgule décimale)."""
    v = _float(value)
    if v is None:
        return "—"
    signe = "-" if v < 0 else ""
    texte = f"{abs(v):.{decimales}f}"
    entier, _, dec = texte.partition(".")
    return signe + _grouper(entier) + ("," + dec if dec else "")


def fmt_euros(value: Any, decimales: int = 0) -> str:
    texte = fmt_nombre(value, decimales)
    return texte if texte == "—" else f"{texte}{_NBSP}€"


def fmt_euros_signe(value: Any, decimales: int = 0) -> str:
    """Variation en euros avec son signe : '+1 777 679 €' / '-25 875 €'."""
    v = _float(value)
    if v is None:
        return "—"
    return ("+" if v > 0 else "") + fmt_euros(v, decimales)


def fmt_pct(value: Any, decimales: int = 1, signe: bool = False) -> str:
    """Valeur déjà exprimée en pourcentage (12.5 -> '12,5 %')."""
    v = _float(value)
    if v is None:
        return "—"
    texte = fmt_nombre(v, decimales)
    if signe and v > 0:
        texte = "+" + texte
    return f"{texte}{_NBSP}%"


def fmt_compact_euros(value: Any) -> str:
    """Montant compact : 1 186 400 -> '1,19 M€' ; 85 000 -> '85 k€'."""
    v = _float(value)
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1_000_000:
        return f"{fmt_nombre(v / 1_000_000, 2)}{_NBSP}M€"
    if a >= 10_000:
        return f"{fmt_nombre(v / 1_000, 0)}{_NBSP}k€"
    return fmt_euros(v)


def fmt_valeur(cle: str, value: Any) -> str:
    unite = INDICATEURS.get(cle, ("", ""))[1]
    if unite == "€":
        return fmt_euros(value)
    if unite == "%":
        return fmt_pct(value, 2)
    if unite:
        return f"{fmt_nombre(value)}{_NBSP}{unite}" if _float(value) is not None else "—"
    return fmt_nombre(value)


_MOIS_COURTS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def mois_court(periode: str, avec_annee: bool = True) -> str:
    """'Année 1 - Février' -> 'févr. A1'."""
    m = re.fullmatch(r"Année\s+(\d+)\s*-\s*(.+)", str(periode).strip())
    if not m:
        return str(periode)
    try:
        idx = [x.casefold() for x in MONTH_NAMES].index(m.group(2).strip().casefold())
    except ValueError:
        return str(periode)
    return f"{_MOIS_COURTS[idx]} A{m.group(1)}" if avec_annee else _MOIS_COURTS[idx]


def mois_long(periode: str) -> str:
    """'Année 1 - Février' -> 'février (année 1)'."""
    m = re.fullmatch(r"Année\s+(\d+)\s*-\s*(.+)", str(periode).strip())
    if not m:
        return str(periode)
    return f"{m.group(2).strip().lower()} (année {m.group(1)})"


# ---------------------------------------------------------------------------
# Périodes
# ---------------------------------------------------------------------------
_CANON: dict[str, str] = {normalize_period(p): p for p in PERIOD_INDEX}
_TOUR_PAR_PERIODE: dict[str, int] = {normalize_period(p): t for t, p in TOURS_PERIODES.items()}


def periode_canonique(label: Any) -> str | None:
    """Libellé canonique (« Année 1 - Août ») d'un libellé quelconque, ou None."""
    return _CANON.get(normalize_period(label))


def tour_de_periode(periode: str) -> int | None:
    return _TOUR_PAR_PERIODE.get(normalize_period(periode))


def periodes_entre(debut: str, fin: str) -> list[str]:
    """Périodes canoniques de ``debut`` à ``fin`` inclus, dans l'ordre."""
    a, b = PERIOD_INDEX.get(periode_canonique(debut) or ""), PERIOD_INDEX.get(periode_canonique(fin) or "")
    if a is None or b is None:
        return []
    if a > b:
        a, b = b, a
    return [p for p, i in sorted(PERIOD_INDEX.items(), key=lambda kv: kv[1]) if a <= i <= b]


def periode_precedente(periode: str) -> str | None:
    i = PERIOD_INDEX.get(periode_canonique(periode) or "")
    if i is None:
        return None
    for p, j in PERIOD_INDEX.items():
        if j == i - 1:
            return p
    return None


def _periode_de_id_mois(mois: Any) -> str | None:
    """Période d'un identifiant de mois du portail (A1-Janvier = 13, cf. ``period_month_id``)."""
    v = _float(mois)
    if v is None:
        return None
    cible = int(v) - 13
    for p, i in PERIOD_INDEX.items():
        if i == cible:
            return p
    return None


# ---------------------------------------------------------------------------
# Indexation des sources
# ---------------------------------------------------------------------------
def _decoder(value: Any) -> Any:
    for _ in range(3):
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return {}
        else:
            break
    return value


@dataclass(frozen=True)
class SourcesERP:
    """Données brutes indexées par période (clé normalisée) et par tour."""

    modules: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    etudes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    tables: Mapping[str, Mapping[int, Mapping[str, Any]]] = field(default_factory=dict)

    def periodes(self) -> list[str]:
        """Périodes canoniques pour lesquelles au moins une source existe."""
        cles = set(self.modules) | set(self.etudes)
        trouvees = {_CANON[c] for c in cles if c in _CANON}
        for lignes in self.tables.values():
            for tour in lignes:
                if tour in TOURS_PERIODES:
                    trouvees.add(TOURS_PERIODES[tour])
        return sorted(trouvees, key=lambda p: PERIOD_INDEX[p])

    def modules_de(self, periode: str) -> Mapping[str, Any]:
        return self.modules.get(normalize_period(periode), {})

    def etudes_de(self, periode: str) -> Mapping[str, Any]:
        return self.etudes.get(normalize_period(periode), {})

    def ligne(self, table: str, tour: int | None) -> dict[str, Any]:
        if tour is None:
            return {}
        return dict(self.tables.get(table, {}).get(int(tour)) or {})


def indexer_sources(
    lignes_donnees: Iterable[Mapping[str, Any]] = (),
    lignes_etudes: Iterable[Mapping[str, Any]] = (),
    tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> SourcesERP:
    """Indexe les lignes lues en base.

    * ``lignes_donnees`` : ``{id, periode, module, contenu}`` (``etat_actuel``) ;
    * ``lignes_etudes`` : ``{id, periode, study_id, payload[, read_allowed]}`` —
      une étude non lisible (SPECIMEN) est ignorée ;
    * ``tables`` : ``{nom_table: [lignes avec Tour_ID]}``.

    Pour une même période et un même module (ou une même étude), la ligne dont
    le libellé de période est exactement canonique l'emporte, puis la plus
    récente (``id`` le plus élevé) — même règle que ``period_context.match_period``.
    """
    def _rang(row: Mapping[str, Any], position: int) -> tuple[int, float]:
        exact = 1 if str(row.get("periode", "")) in PERIOD_INDEX else 0
        ident = _float(row.get("id"))
        return exact, ident if ident is not None else float(position)

    modules: dict[str, dict[str, Any]] = {}
    rangs_m: dict[tuple[str, str], tuple[int, float]] = {}
    for pos, row in enumerate(lignes_donnees or ()):
        cle = normalize_period(row.get("periode"))
        module = str(row.get("module") or "")
        if not cle or not module:
            continue
        rang = _rang(row, pos)
        if rangs_m.get((cle, module), (-1, -1.0)) > rang:
            continue
        contenu = _decoder(row.get("contenu"))
        if not isinstance(contenu, (Mapping, list)):
            continue
        rangs_m[(cle, module)] = rang
        modules.setdefault(cle, {})[module] = contenu

    etudes: dict[str, dict[str, Any]] = {}
    rangs_e: dict[tuple[str, str], tuple[int, float]] = {}
    for pos, row in enumerate(lignes_etudes or ()):
        if "read_allowed" in row and not row.get("read_allowed"):
            continue
        cle = normalize_period(row.get("periode"))
        sid = str(row.get("study_id") or "")
        if not cle or not sid:
            continue
        rang = _rang(row, pos)
        if rangs_e.get((cle, sid), (-1, -1.0)) > rang:
            continue
        payload = _unwrap_api_payload(_json_load(_decoder(row.get("payload"))))
        if not isinstance(payload, Mapping) or not payload:
            continue
        rangs_e[(cle, sid)] = rang
        etudes.setdefault(cle, {})[sid] = dict(payload)

    index_tables: dict[str, dict[int, dict[str, Any]]] = {}
    for nom, lignes in (tables or {}).items():
        par_tour: dict[int, dict[str, Any]] = {}
        for row in lignes or ():
            tour = _float(row.get("Tour_ID"))
            if tour is None:
                continue
            par_tour[int(tour)] = {k: v for k, v in dict(row).items()}
        index_tables[str(nom)] = par_tour
    return SourcesERP(modules=modules, etudes=etudes, tables=index_tables)


# ---------------------------------------------------------------------------
# Lecture d'un mois
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MoisAudit:
    """Indicateurs d'un mois, chacun avec sa source."""

    periode: str
    tour: int | None
    valeurs: Mapping[str, float] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    ventes: Mapping[str, float] = field(default_factory=dict)
    prix: Mapping[str, float] = field(default_factory=dict)
    prix_marche: Mapping[str, float] = field(default_factory=dict)
    machines: Mapping[str, float] = field(default_factory=dict)
    acquisitions: Mapping[str, float] = field(default_factory=dict)
    cessions: Mapping[str, float] = field(default_factory=dict)
    utilisation: Mapping[str, float] = field(default_factory=dict)
    scores_rh: Mapping[str, float] = field(default_factory=dict)
    production: Mapping[str, float] = field(default_factory=dict)
    couts_unitaires: Mapping[str, float] = field(default_factory=dict)
    axes_publicitaires: tuple[str, ...] = ()

    def v(self, cle: str) -> float | None:
        return self.valeurs.get(cle)

    @property
    def renseigne(self) -> bool:
        return bool(self.valeurs or self.ventes or self.prix or self.machines)


def _valeur_courante(node: Any) -> float | None:
    """Dernière valeur d'une évolution publiée (nombre, liste, ou {value})."""
    v = _float(node)
    if v is not None:
        return v
    if isinstance(node, (list, tuple)):
        for item in reversed(node):
            v = _valeur_courante(item)
            if v is not None:
                return v
        return None
    if isinstance(node, Mapping):
        for cle in ("value", "amount", "valeur", "montant"):
            if cle in node:
                v = _float(node.get(cle))
                if v is not None:
                    return v
        for valeur in node.values():
            if isinstance(valeur, (list, tuple)):
                v = _valeur_courante(valeur)
                if v is not None:
                    return v
    return None


def _monitoring(etudes: Mapping[str, Any]) -> dict[str, float]:
    """Tableau de bord (étude gratuite) : PDM, CA, banque, résultat du mois."""
    payload = etudes.get("monitoring") if isinstance(etudes, Mapping) else None
    if not isinstance(payload, Mapping):
        payload = next(
            (p for p in (etudes or {}).values()
             if isinstance(p, Mapping) and ("marketShares" in p or "monitoringTurnoverData" in p)),
            None,
        )
    if not isinstance(payload, Mapping):
        return {}
    out: dict[str, float] = {}
    # La PDM du portail est publiée en pourcentage (ex. 15.06 pour 15,06 %).
    pdm = _float(payload.get("marketShares"))
    if pdm is not None:
        out[PDM] = pdm
    ca_node = payload.get("monitoringTurnoverData")
    if isinstance(ca_node, Mapping):
        ca = _valeur_courante(ca_node.get("monthlySalesTurnover"))
        if ca is not None:
            out[CA] = ca
    banque = _valeur_courante(payload.get("bankAccountAmountEvolution"))
    if banque is not None:
        out[TRESORERIE] = banque
    resultat = _valeur_courante(payload.get("monthlyResult"))
    if resultat is not None:
        out[RESULTAT] = resultat
    return out


def _poste_bilan(lignes: Sequence[Mapping[str, Any]], cote: str, *libelles: str) -> float | None:
    """Montant d'un poste du bilan détaillé (tableau « Actif » / « Passif »), ou None s'il est absent."""
    colonne = "Net exercice N" if cote == "Actif" else "Exercice N"
    cibles = {l.casefold() for l in libelles}
    for ligne in lignes:
        if str(ligne.get(cote, "")).strip().casefold() in cibles:
            return _float(ligne.get(colonne, ligne.get("Net" if cote == "Actif" else "Montant")))
    return None


def _ratios_expert(expert: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Ratios mensuels et cumulés du module expert-comptable, cherchés par contenu.

    Même règle que la page « Livrable Finance » : l'onglet peut s'appeler
    « Expert-comptable #3 » et être un dictionnaire comme une liste de tableaux.
    """
    trouves: dict[str, Mapping[str, Any]] = {}

    def chercher(node: Any, profondeur: int = 0) -> None:
        if profondeur > 7 or len(trouves) == 2:
            return
        if isinstance(node, Mapping):
            for cle, valeur in node.items():
                nom = str(cle)
                if nom == "Indicateurs_MENSUEL" and "m" not in trouves and isinstance(valeur, list) and valeur:
                    if isinstance(valeur[0], Mapping):
                        trouves["m"] = valeur[0]
                elif nom in ("Indicateurs_CUMULÉ", "Indicateurs_CUMULE") and "c" not in trouves and isinstance(valeur, list) and valeur:
                    if isinstance(valeur[0], Mapping):
                        trouves["c"] = valeur[0]
                elif isinstance(valeur, (Mapping, list)):
                    chercher(valeur, profondeur + 1)
        elif isinstance(node, list):
            for item in node:
                chercher(item, profondeur + 1)

    chercher(expert)
    return trouves.get("m", {}), trouves.get("c", {})


def ventes_publiees(sources: SourcesERP) -> dict[str, tuple[dict[str, float], str]]:
    """Ventes par produit de chaque mois, lues dans TOUTES les études « Ventes mensuelles ».

    L'étude porte un tableau de plusieurs mois : un seul document de décembre
    renseigne donc toute l'année. Pour chaque mois, la ligne publiée par
    l'étude du mois même est préférée ; à défaut, celle de l'étude la plus
    récente qui le couvre. Retour : ``{période: ({produit: unités}, source)}``.
    """
    candidats: dict[str, list[tuple[int, int, dict[str, float], str]]] = {}
    for cle, etudes in sources.etudes.items():
        periode_doc = _CANON.get(cle)
        payload = etudes.get(study_ops.STUDY_SALES) if isinstance(etudes, Mapping) else None
        if periode_doc is None or not isinstance(payload, Mapping):
            continue
        lignes = payload.get("sales")
        if not isinstance(lignes, list):
            continue
        avec_mois = [l for l in lignes if isinstance(l, Mapping) and _float(l.get("month")) is not None]
        for ligne in lignes:
            if not isinstance(ligne, Mapping):
                continue
            periode = _periode_de_id_mois(ligne.get("month"))
            if periode is None:
                # Sans identifiant de mois, seule la dernière ligne est attribuable : le mois du document.
                if avec_mois or ligne is not lignes[-1]:
                    continue
                periode = periode_doc
            valeurs: dict[str, float] = {}
            for item in ligne.get("monthlySalesByProducts") or []:
                if not isinstance(item, Mapping):
                    continue
                produit = study_ops.PRODUCT_ID_TO_NAME.get(str(item.get("productId") or ""))
                qte = _float(item.get("sales"))
                if produit and qte is not None and qte >= 0:
                    valeurs[produit] = valeurs.get(produit, 0.0) + qte
            if not valeurs:
                continue
            exact = 1 if periode == periode_doc else 0
            source = f"Étude « Ventes mensuelles » de {mois_court(periode_doc)}"
            candidats.setdefault(periode, []).append((exact, PERIOD_INDEX[periode_doc], valeurs, source))
    out: dict[str, tuple[dict[str, float], str]] = {}
    for periode, liste in candidats.items():
        exact, _, valeurs, source = max(liste, key=lambda c: (c[0], c[1]))
        out[periode] = (valeurs, source)
    return out


def _decisions_mois(sources: SourcesERP, periode: str, tour: int | None) -> tuple[dict, dict, str, str]:
    """Décisions marketing et RH du mois : saisie SQL exacte, complétée par le scraping."""
    mkt = {k: v for k, v in sources.ligne("Decisions_Marketing", tour).items() if _float(v) is not None or isinstance(v, str)}
    rh = {k: v for k, v in sources.ligne("Decisions_RH_Mensuel", tour).items() if _float(v) is not None}
    src_mkt = "Décisions saisies (Decisions_Marketing)" if mkt else ""
    src_rh = "Décisions saisies (Decisions_RH_Mensuel)" if rh else ""
    modules = sources.modules_de(periode)
    utiles = {m: modules.get(m) for m in ("marketing", "rh", "production") if modules.get(m)}
    if utiles:
        try:
            scrape = parse_decisions(utiles)
        except Exception:
            scrape = {}
        for cle, valeur in (scrape.get("marketing") or {}).items():
            if cle not in mkt:
                mkt[cle] = valeur
                src_mkt = src_mkt or "Décisions lues sur le portail (scraping)"
        for cle, valeur in (scrape.get("rh") or {}).items():
            if cle not in rh:
                rh[cle] = valeur
                src_rh = src_rh or "Décisions lues sur le portail (scraping)"
    return mkt, rh, src_mkt, src_rh


def _atelier(nom: Any) -> str | None:
    texte = str(nom or "").strip()
    return _ALIAS_ATELIERS.get(texte.casefold(), texte if texte in ATELIERS else None)


def analyser_mois(
    sources: SourcesERP,
    periode: str,
    ventes_par_mois: Mapping[str, tuple[Mapping[str, float], str]] | None = None,
) -> MoisAudit:
    """Tous les indicateurs d'un mois, chacun avec sa source (aucune valeur inventée)."""
    periode = periode_canonique(periode) or periode
    tour = tour_de_periode(periode)
    modules = sources.modules_de(periode)
    etudes = sources.etudes_de(periode)
    data: dict[str, Any] = {"etat_actuel": dict(modules)}
    data.update({f"etude_{sid}": payload for sid, payload in etudes.items()})
    try:
        state = extract_financial_state(periode, data)
    except Exception:
        state = None
    # Compte de résultat simplifié lu champ par champ : un champ absent reste
    # absent (le parseur du livrable le ramènerait à 0 €).
    cr = etudes.get("peeedscrsm") if isinstance(etudes.get("peeedscrsm"), Mapping) else {}
    ca_simplifie = _float(cr.get("biensLivraisons", cr.get("competitiveMarketTurnover")))
    resultat_simplifie = _float(cr.get("netResult"))
    caf_simplifie = None
    if resultat_simplifie is not None and _float(cr.get("amortization")) is not None:
        caf_simplifie = resultat_simplifie + _float(cr.get("amortization")) + (_float(cr.get("depreciationProvision")) or 0.0)
    tableau = _monitoring(etudes)
    fin = sources.ligne("Finances_Mensuelles", tour)
    expert = modules.get("expert_comptable")
    try:
        bilan = table_bilan_detaille(expert) if isinstance(expert, Mapping) else []
    except Exception:
        bilan = []
    ratios_m, ratios_c = _ratios_expert(expert) if expert else ({}, {})
    src_bilan = "Bilan détaillé (module expert-comptable)"
    src_ratios = "Ratios et indicateurs (module expert-comptable)"
    valeurs: dict[str, float] = {}
    srcs: dict[str, str] = {}

    def poser(cle: str, *candidats: tuple[Any, str]) -> None:
        for valeur, source in candidats:
            v = _float(valeur)
            if v is not None:
                valeurs[cle] = v
                srcs[cle] = source
                return

    st = state
    poser(CA,
          (getattr(st, "chiffre_affaires", None), _SOURCE_COMPTA),
          (_non_nul(ratios_m.get("Chiffre d'affaires")), src_ratios),
          (ca_simplifie, "Étude « Compte de résultat simplifié »"),
          (tableau.get(CA), "Étude « Tableau de bord »"),
          (_non_nul(fin.get("CA_Net")), "Saisie manuelle (Finances_Mensuelles)"))
    poser(RESULTAT,
          (getattr(st, "resultat_net", None), _SOURCE_COMPTA),
          (resultat_simplifie, "Étude « Compte de résultat simplifié »"),
          (tableau.get(RESULTAT), "Étude « Tableau de bord »"),
          (_non_nul(fin.get("Resultat_Net")), "Saisie manuelle (Finances_Mensuelles)"))
    caf_compta = None
    if st is not None and st.resultat_net is not None and st.dotations_amortissements is not None:
        caf_compta = st.resultat_net + st.dotations_amortissements + (st.depreciations or Decimal("0"))
    poser(CAF,
          (caf_compta, _SOURCE_COMPTA + " (résultat + dotations)"),
          (caf_simplifie, "Étude « Compte de résultat simplifié » (résultat + dotations)"))
    poser(TRESORERIE,
          (getattr(st, "solde_bancaire_final", None), "Relevé bancaire (module banque / étude)"),
          (_poste_bilan(bilan, "Actif", "banque"), src_bilan),
          (tableau.get(TRESORERIE), "Étude « Tableau de bord »"),
          (_non_nul(fin.get("Disponibilites_Banque")), "Saisie manuelle (Finances_Mensuelles)"))
    poser(TRESORERIE_OUVERTURE, (getattr(st, "solde_bancaire_initial", None), "Relevé bancaire (solde initial)"))
    poser(EPARGNE,
          (getattr(st, "solde_epargne", None), _SOURCE_COMPTA),
          (_poste_bilan(bilan, "Actif", "compte épargne", "compte epargne"), src_bilan))
    poser(CAPITAUX_PROPRES,
          (getattr(st, "capitaux_propres", None), _SOURCE_COMPTA),
          (_poste_bilan(bilan, "Passif", "total capitaux propres"), src_bilan),
          (_non_nul(fin.get("Total_Capitaux_Propres")), "Saisie manuelle (Finances_Mensuelles)"))
    dette_bilan = None
    emprunts = [_poste_bilan(bilan, "Passif", "emprunts mensualisés contractés", "emprunts mensualises contractes"),
                _poste_bilan(bilan, "Passif", "emprunts libres")]
    if any(e is not None for e in emprunts):
        dette_bilan = sum(e for e in emprunts if e is not None)
    poser(DETTE,
          (getattr(st, "dette_bancaire", None), _SOURCE_COMPTA),
          (dette_bilan, src_bilan),
          (_non_nul(fin.get("Emprunts_Bancaires")), "Saisie manuelle (Finances_Mensuelles)"))
    poser(BFR, (getattr(st, "bfr_reel", None), _SOURCE_COMPTA))
    poser(STOCKS_PRODUITS,
          (getattr(st, "bfr_stocks_produits_reel", None), _SOURCE_COMPTA),
          (_poste_bilan(bilan, "Actif", "produits finis"), src_bilan))
    poser(STOCKS_MATIERES,
          (getattr(st, "bfr_stocks_matieres_reel", None), _SOURCE_COMPTA),
          (_poste_bilan(bilan, "Actif", "matières", "matieres"), src_bilan))
    poser(CA_CUMULE_PUBLIE,
          (getattr(st, "chiffre_affaires_cumule", None), _SOURCE_COMPTA),
          (_non_nul(ratios_c.get("Chiffre d'affaires")), src_ratios))
    poser(PDM, (tableau.get(PDM), "Étude « Tableau de bord » (part de marché globale)"))

    # --- Ventes par produit -------------------------------------------------
    ventes: dict[str, float] = {}
    if ventes_par_mois and periode in ventes_par_mois:
        publie, source = ventes_par_mois[periode]
        ventes = {p: float(q) for p, q in publie.items()}
        srcs["ventes"] = source
    if not ventes:
        ligne_v = sources.ligne("Ventes_Historique", tour)
        lues = {PRODUCT_KEY_TO_NAME[k]: _float(ligne_v.get(f"Ventes_{k.upper()}")) for k in PRODUCT_KEYS}
        lues = {p: q for p, q in lues.items() if q is not None}
        if lues:
            ventes = lues
            srcs["ventes"] = "Saisie manuelle (Ventes_Historique)"
    if ventes:
        valeurs[VENTES_TOTALES] = float(sum(ventes.values()))
        srcs[VENTES_TOTALES] = srcs["ventes"]

    # --- Décisions marketing & RH -------------------------------------------
    mkt, rh, src_mkt, src_rh = _decisions_mois(sources, periode, tour)
    prix = {PRODUCT_KEY_TO_NAME[k]: _float(mkt.get(f"Prix_{k.upper()}")) for k in PRODUCT_KEYS}
    prix = {p: v for p, v in prix.items() if v is not None and v > 0}
    if prix:
        srcs["prix"] = src_mkt
    pub_produits = [_float(mkt.get(f"Pub_{k.upper()}")) for k in PRODUCT_KEYS]
    pub_produits = [v for v in pub_produits if v is not None]
    if pub_produits:
        valeurs[PUB_PRODUITS] = float(sum(pub_produits))
        srcs[PUB_PRODUITS] = src_mkt
    poser(PUB_MARQUE, (mkt.get("Budget_Marque"), src_mkt))
    axes = tuple(str(mkt.get(k)).strip() for k in ("Axe_Principal", "Axe_Accessoire")
                 if isinstance(mkt.get(k), str) and str(mkt.get(k)).strip())
    effectifs = [_float(v) for k, v in rh.items() if str(k).startswith("Eff_")]
    effectifs = [v for v in effectifs if v is not None]
    if effectifs:
        valeurs[EFFECTIFS] = float(sum(effectifs))
        srcs[EFFECTIFS] = src_rh
        masse = 0.0
        for k, v in rh.items():
            if str(k).startswith("Eff_"):
                sal = _float(rh.get("Sal_" + str(k)[4:]))
                eff = _float(v)
                if sal is not None and eff is not None:
                    masse += eff * sal
        if masse > 0:
            valeurs[MASSE_SALARIALE] = masse
            srcs[MASSE_SALARIALE] = src_rh + " (effectifs × salaires)"
    audits = study_ops.audits_rh(etudes)
    scores_rh = {pole: float(d.get("score", 0.0)) for pole, d in audits.items() if _float(d.get("score")) is not None}
    if scores_rh:
        srcs["scores_rh"] = "Études « Audit du service »"
    if MASSE_SALARIALE not in valeurs:
        masses = [float(d.get("masse_salariale") or 0.0) for d in audits.values()]
        if any(masses):
            valeurs[MASSE_SALARIALE] = float(sum(masses))
            srcs[MASSE_SALARIALE] = "Études « Audit du service » (masse salariale publiée)"

    # --- Parc machines, utilisation, production -----------------------------
    parc = sources.ligne("Parc_Machines_Mensuel", tour)
    machines: dict[str, float] = {}
    acquisitions: dict[str, float] = {}
    cessions: dict[str, float] = {}
    for atelier, (c_nb, c_acq, c_ced) in _COLONNES_PARC.items():
        nb = _float(parc.get(c_nb))
        if nb is not None:
            machines[atelier] = nb
        if _float(parc.get("Acquisition_Faite")):
            acq = _float(parc.get(c_acq))
            if acq:
                acquisitions[atelier] = acq
        if _float(parc.get("Cession_Faite")):
            ced = _float(parc.get(c_ced))
            if ced:
                cessions[atelier] = ced
    if machines:
        srcs["machines"] = "Saisie manuelle (Parc_Machines_Mensuel)"
    utilisation: dict[str, float] = {}
    for nom, detail in study_ops.capacites_ateliers(etudes).items():
        atelier = _atelier(nom)
        if atelier is None:
            continue
        capa = _float(detail.get("capacite_totale"))
        conso = _float(detail.get("consommation"))
        if capa and capa > 0 and conso is not None:
            utilisation[atelier] = conso / capa
        if atelier not in machines and _float(detail.get("nb_machines")):
            machines[atelier] = float(detail["nb_machines"])
            srcs.setdefault("machines", "Étude « Ateliers : disponibilités et besoins théoriques »")
    if utilisation:
        srcs["utilisation"] = "Étude « Ateliers : disponibilités et besoins théoriques »"
    production = {p: float(q) for p, q in study_ops.volumes_par_libelle(etudes).items() if p in PRODUCTS}
    if production:
        valeurs[PRODUCTION_TOTALE] = float(sum(production.values()))
        srcs[PRODUCTION_TOTALE] = "Étude « Matières : disponibilités et besoins théoriques »"

    # --- Coûts unitaires et prix de marché ----------------------------------
    couts: dict[str, float] = {}
    etude_couts = etudes.get("productionCostDetailStudy")
    if isinstance(etude_couts, Mapping) and etude_couts.get("productsProductionCostDetails"):
        try:
            lus, _warns = couts_from_cost_study(etude_couts)
            couts = {p: float(v) for p, v in lus.items() if p in PRODUCTS}
        except Exception:
            couts = {}
        if couts:
            srcs["couts"] = "Étude « Calcul des coûts de production »"
    try:
        prix_marche = prix_marche_par_produit(modules.get("expert_comptable"))
    except Exception:
        prix_marche = {}
    if prix_marche:
        srcs["prix_marche"] = "Module expert-comptable (ventes sur le marché concurrentiel)"

    return MoisAudit(
        periode=periode, tour=tour, valeurs=valeurs, sources=srcs, ventes=ventes, prix=prix,
        prix_marche=dict(prix_marche), machines=machines, acquisitions=acquisitions, cessions=cessions,
        utilisation=utilisation, scores_rh=scores_rh, production=production, couts_unitaires=couts,
        axes_publicitaires=axes,
    )


# ---------------------------------------------------------------------------
# Analyse annuelle
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Synthese:
    n_mois: int = 0
    ca_total: float | None = None
    ca_mois: int = 0
    resultat_total: float | None = None
    resultat_mois: int = 0
    marge_nette: float | None = None              # en %
    croissance_ca: float | None = None            # en %, premier -> dernier mois renseigné
    meilleur_mois: tuple[str, float] | None = None
    pire_mois: tuple[str, float] | None = None
    mois_deficitaires: tuple[str, ...] = ()
    tresorerie_ouverture: tuple[str, float] | None = None   # (mois dont c'est le solde d'ouverture, solde)
    tresorerie_debut: tuple[str, float] | None = None       # premier solde de fin de mois connu
    tresorerie_cloture: tuple[str, float] | None = None     # dernier solde de fin de mois connu
    tresorerie_min: tuple[str, float] | None = None
    capitaux_propres_debut: float | None = None
    capitaux_propres_fin: float | None = None
    dette_debut: float | None = None
    dette_fin: float | None = None
    pdm_debut: tuple[str, float] | None = None
    pdm_fin: tuple[str, float] | None = None
    pdm_max: tuple[str, float] | None = None
    ventes_totales: float | None = None
    ventes_mois: int = 0
    pub_totale: float | None = None
    ratio_pub_ca: float | None = None             # en %
    effectifs_debut: float | None = None
    effectifs_fin: float | None = None
    machines_debut: Mapping[str, float] = field(default_factory=dict)
    machines_fin: Mapping[str, float] = field(default_factory=dict)
    acquisitions_total: Mapping[str, float] = field(default_factory=dict)
    cessions_total: Mapping[str, float] = field(default_factory=dict)
    utilisation_moyenne: Mapping[str, float] = field(default_factory=dict)  # 0-1
    utilisation_max: Mapping[str, tuple[str, float]] = field(default_factory=dict)
    ca_cumule_publie: tuple[str, float] | None = None


@dataclass(frozen=True)
class LigneProduit:
    produit: str
    ventes: float = 0.0
    mois_ventes: int = 0
    part_volumes: float | None = None        # %
    ca_estime: float | None = None           # prix catalogue × volumes
    part_ca: float | None = None             # %
    prix_debut: float | None = None
    prix_fin: float | None = None
    prix_moyen: float | None = None          # pondéré par les volumes
    prix_marche: float | None = None         # dernier prix de marché connu
    indice_prix: float | None = None         # prix fin / prix marché (1 = aligné)
    cout_variable: float | None = None       # dernier coût variable unitaire connu
    marge_unitaire: float | None = None      # prix fin - coût variable
    taux_marge: float | None = None          # %
    contribution: float | None = None        # marge unitaire × ventes (estimation)
    evolution_ventes: float | None = None    # %, premier -> dernier mois vendu


@dataclass(frozen=True)
class DecompositionCA:
    """Écart de CA théorique (prix catalogue × volumes) entre deux mois."""

    periode_a: str
    periode_b: str
    ca_a: float
    ca_b: float
    effet_volume: float
    effet_mix: float
    effet_prix: float

    @property
    def ecart(self) -> float:
        return self.ca_b - self.ca_a


@dataclass(frozen=True)
class Evenement:
    periode: str
    domaine: str
    texte: str


@dataclass(frozen=True)
class Constat:
    theme: str        # Commercial | Financier | Industriel | Ressources humaines | Données
    nature: str       # force | faiblesse | vigilance | info
    texte: str        # description factuelle chiffrée
    analyse: str = ""  # piste d'analyse, à valider par l'équipe


@dataclass(frozen=True)
class Manque:
    element: str
    periodes: tuple[str, ...]
    consequence: str
    etudes: tuple[tuple[str, str, float], ...] = ()


@dataclass(frozen=True)
class AuditAnnuel:
    entreprise: str
    numero: int
    debut: str
    fin: str
    mois: tuple[MoisAudit, ...]
    ouverture: MoisAudit | None
    synthese: Synthese
    produits: tuple[LigneProduit, ...]
    decomposition: DecompositionCA | None
    chronologie: tuple[Evenement, ...]
    concurrence: tuple[Mapping[str, Any], ...]
    concurrence_periode: str | None
    constats: tuple[Constat, ...]
    lecture_strategique: tuple[str, ...]
    manques: tuple[Manque, ...]

    def serie(self, cle: str) -> list[tuple[str, float]]:
        return [(m.periode, m.valeurs[cle]) for m in self.mois if cle in m.valeurs]

    def sources_par_indicateur(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {}
        for m in self.mois:
            for cle, src in m.sources.items():
                if src and src not in out.setdefault(cle, []):
                    out[cle].append(src)
        return {k: tuple(v) for k, v in out.items()}

    @property
    def mois_renseignes(self) -> int:
        return sum(1 for m in self.mois if m.renseigne)


def _premier_dernier(serie: Sequence[tuple[str, float]]) -> tuple[tuple[str, float] | None, tuple[str, float] | None]:
    return (serie[0], serie[-1]) if serie else (None, None)


def _variation_pct(debut: float | None, fin: float | None) -> float | None:
    if debut is None or fin is None or debut == 0:
        return None
    return (fin - debut) / abs(debut) * 100.0


def calculer_synthese(mois: Sequence[MoisAudit], ouverture: MoisAudit | None = None) -> Synthese:
    def serie(cle: str) -> list[tuple[str, float]]:
        return [(m.periode, m.valeurs[cle]) for m in mois if cle in m.valeurs]

    ca = serie(CA)
    res = serie(RESULTAT)
    ca_par_mois = dict(ca)
    communs = [(p, r) for p, r in res if p in ca_par_mois]
    marge = None
    if communs:
        ca_communs = sum(ca_par_mois[p] for p, _ in communs)
        if ca_communs > 0:
            marge = sum(r for _, r in communs) / ca_communs * 100.0
    premier_ca, dernier_ca = _premier_dernier(ca)
    croissance = _variation_pct(premier_ca[1], dernier_ca[1]) if len(ca) >= 2 else None

    treso = serie(TRESORERIE)
    # Solde d'ouverture : celui du premier mois qui publie une donnée bancaire,
    # ou la clôture du mois précédant la période si le premier mois en est dépourvu.
    treso_ouv = None
    premier_bancaire = next((m for m in mois if TRESORERIE in m.valeurs or TRESORERIE_OUVERTURE in m.valeurs), None)
    if premier_bancaire is not None and TRESORERIE_OUVERTURE in premier_bancaire.valeurs:
        treso_ouv = (premier_bancaire.periode, premier_bancaire.valeurs[TRESORERIE_OUVERTURE])
    elif mois and premier_bancaire is mois[0] and ouverture is not None and TRESORERIE in ouverture.valeurs:
        treso_ouv = (mois[0].periode, ouverture.valeurs[TRESORERIE])

    cp = serie(CAPITAUX_PROPRES)
    dette = serie(DETTE)
    pdm = serie(PDM)
    ventes = serie(VENTES_TOTALES)
    eff = serie(EFFECTIFS)

    pubs = []
    for m in mois:
        parts = [m.valeurs[k] for k in (PUB_MARQUE, PUB_PRODUITS) if k in m.valeurs]
        if parts:
            pubs.append((m.periode, float(sum(parts))))
    pub_totale = sum(v for _, v in pubs) if pubs else None
    ratio_pub = None
    communs_pub = [(p, v) for p, v in pubs if p in ca_par_mois]
    if communs_pub:
        base = sum(ca_par_mois[p] for p, _ in communs_pub)
        if base > 0:
            ratio_pub = sum(v for _, v in communs_pub) / base * 100.0

    machines = [m for m in mois if m.machines]
    acq: dict[str, float] = {}
    ced: dict[str, float] = {}
    for m in mois:
        for a, n in m.acquisitions.items():
            acq[a] = acq.get(a, 0.0) + n
        for a, n in m.cessions.items():
            ced[a] = ced.get(a, 0.0) + n
    util_moy: dict[str, float] = {}
    util_max: dict[str, tuple[str, float]] = {}
    for atelier in ATELIERS:
        valeurs = [(m.periode, m.utilisation[atelier]) for m in mois if atelier in m.utilisation]
        if valeurs:
            util_moy[atelier] = sum(v for _, v in valeurs) / len(valeurs)
            util_max[atelier] = max(valeurs, key=lambda x: x[1])

    cumul = [(m.periode, m.valeurs[CA_CUMULE_PUBLIE]) for m in mois if CA_CUMULE_PUBLIE in m.valeurs]
    return Synthese(
        n_mois=len(mois),
        ca_total=sum(v for _, v in ca) if ca else None,
        ca_mois=len(ca),
        resultat_total=sum(v for _, v in res) if res else None,
        resultat_mois=len(res),
        marge_nette=marge,
        croissance_ca=croissance,
        meilleur_mois=max(ca, key=lambda x: x[1]) if ca else None,
        pire_mois=min(ca, key=lambda x: x[1]) if ca else None,
        mois_deficitaires=tuple(p for p, v in res if v < 0),
        tresorerie_ouverture=treso_ouv,
        tresorerie_debut=treso[0] if treso else None,
        tresorerie_cloture=treso[-1] if treso else None,
        tresorerie_min=min(treso, key=lambda x: x[1]) if treso else None,
        capitaux_propres_debut=cp[0][1] if cp else None,
        capitaux_propres_fin=cp[-1][1] if cp else None,
        dette_debut=dette[0][1] if dette else None,
        dette_fin=dette[-1][1] if dette else None,
        pdm_debut=pdm[0] if pdm else None,
        pdm_fin=pdm[-1] if pdm else None,
        pdm_max=max(pdm, key=lambda x: x[1]) if pdm else None,
        ventes_totales=sum(v for _, v in ventes) if ventes else None,
        ventes_mois=len(ventes),
        pub_totale=pub_totale,
        ratio_pub_ca=ratio_pub,
        effectifs_debut=eff[0][1] if eff else None,
        effectifs_fin=eff[-1][1] if eff else None,
        machines_debut=dict(machines[0].machines) if machines else {},
        machines_fin=dict(machines[-1].machines) if machines else {},
        acquisitions_total=acq,
        cessions_total=ced,
        utilisation_moyenne=util_moy,
        utilisation_max=util_max,
        ca_cumule_publie=cumul[-1] if cumul else None,
    )


def analyser_produits(mois: Sequence[MoisAudit]) -> tuple[LigneProduit, ...]:
    """Volumes, prix, marge et contribution par produit sur la période."""
    total_ventes = sum(sum(m.ventes.values()) for m in mois)
    lignes: list[LigneProduit] = []
    ca_estimes: dict[str, float | None] = {}
    for produit in PRODUCTS:
        ventes_mois = [(m.periode, m.ventes[produit]) for m in mois if produit in m.ventes]
        ventes = float(sum(q for _, q in ventes_mois))
        prix_mois = [(m.periode, m.prix[produit]) for m in mois if produit in m.prix]
        paires = [(m.prix[produit], m.ventes[produit]) for m in mois if produit in m.prix and produit in m.ventes]
        ca_estime = sum(p * q for p, q in paires) if paires else None
        q_paires = sum(q for _, q in paires)
        prix_moyen = (ca_estime / q_paires) if ca_estime is not None and q_paires > 0 else None
        prix_marche = next((m.prix_marche[produit] for m in reversed(mois) if produit in m.prix_marche), None)
        cout = next((m.couts_unitaires[produit] for m in reversed(mois) if produit in m.couts_unitaires), None)
        prix_fin = prix_mois[-1][1] if prix_mois else None
        marge = (prix_fin - cout) if prix_fin is not None and cout is not None else None
        vendus = [(p, q) for p, q in ventes_mois if q > 0]
        evolution = _variation_pct(vendus[0][1], vendus[-1][1]) if len(vendus) >= 2 else None
        ca_estimes[produit] = ca_estime
        lignes.append(LigneProduit(
            produit=produit,
            ventes=ventes,
            mois_ventes=len(ventes_mois),
            part_volumes=(ventes / total_ventes * 100.0) if total_ventes > 0 else None,
            ca_estime=ca_estime,
            prix_debut=prix_mois[0][1] if prix_mois else None,
            prix_fin=prix_fin,
            prix_moyen=prix_moyen,
            prix_marche=prix_marche,
            indice_prix=(prix_fin / prix_marche) if prix_fin is not None and prix_marche else None,
            cout_variable=cout,
            marge_unitaire=marge,
            taux_marge=(marge / prix_fin * 100.0) if marge is not None and prix_fin else None,
            contribution=(marge * ventes) if marge is not None else None,
            evolution_ventes=evolution,
        ))
    total_ca = sum(v for v in ca_estimes.values() if v is not None)
    if total_ca > 0:
        lignes = [
            LigneProduit(**{**asdict(l), "part_ca": (l.ca_estime / total_ca * 100.0) if l.ca_estime is not None else None})
            for l in lignes
        ]
    return tuple(lignes)


def decomposer_ecart_ca(
    periode_a: str,
    prix_a: Mapping[str, float],
    ventes_a: Mapping[str, float],
    periode_b: str,
    prix_b: Mapping[str, float],
    ventes_b: Mapping[str, float],
) -> DecompositionCA | None:
    """Décompose l'écart de CA théorique en effets volume, mix et prix.

    CA théorique = Σ prix catalogue × volumes (hors remises de canal).
    * effet volume = (Q_b − Q_a) × prix moyen de a ;
    * effet mix    = Σ p_a × q_b − Q_b × prix moyen de a ;
    * effet prix   = Σ (p_b − p_a) × q_b.
    La somme des trois effets est exactement l'écart CA_b − CA_a. Renvoie None
    si un produit vendu n'a pas de prix connu sur l'un des deux mois.
    """
    produits = [p for p in PRODUCTS if (ventes_a.get(p) or 0) > 0 or (ventes_b.get(p) or 0) > 0]
    if not produits:
        return None
    for p in produits:
        if p not in prix_a or p not in prix_b:
            return None
    q_a = sum(float(ventes_a.get(p) or 0) for p in produits)
    q_b = sum(float(ventes_b.get(p) or 0) for p in produits)
    if q_a <= 0:
        return None
    ca_a = sum(prix_a[p] * float(ventes_a.get(p) or 0) for p in produits)
    ca_b = sum(prix_b[p] * float(ventes_b.get(p) or 0) for p in produits)
    prix_moyen_a = ca_a / q_a
    base_b = sum(prix_a[p] * float(ventes_b.get(p) or 0) for p in produits)
    return DecompositionCA(
        periode_a=periode_a, periode_b=periode_b, ca_a=ca_a, ca_b=ca_b,
        effet_volume=(q_b - q_a) * prix_moyen_a,
        effet_mix=base_b - q_b * prix_moyen_a,
        effet_prix=ca_b - base_b,
    )


def _decomposition_annuelle(mois: Sequence[MoisAudit]) -> DecompositionCA | None:
    eligibles = [m for m in mois if m.ventes and sum(m.ventes.values()) > 0 and m.prix]
    for a in eligibles:
        for b in reversed(eligibles):
            if PERIOD_INDEX[b.periode] <= PERIOD_INDEX[a.periode]:
                break
            d = decomposer_ecart_ca(a.periode, a.prix, a.ventes, b.periode, b.prix, b.ventes)
            if d is not None:
                return d
    return None


def chronologie_decisions(mois: Sequence[MoisAudit], limite: int = 40) -> tuple[Evenement, ...]:
    """Décisions clés, mois par mois, déduites des écarts entre mois successifs."""
    evts: list[Evenement] = []
    precedent: MoisAudit | None = None
    for m in mois:
        for atelier, n in m.acquisitions.items():
            evts.append(Evenement(m.periode, "Capacité", f"Achat de {fmt_nombre(n)} machine(s) — atelier {atelier}"))
        for atelier, n in m.cessions.items():
            evts.append(Evenement(m.periode, "Capacité", f"Cession de {fmt_nombre(n)} machine(s) — atelier {atelier}"))
        if precedent is not None:
            changements = []
            for produit in PRODUCTS:
                a, b = precedent.prix.get(produit), m.prix.get(produit)
                if a is not None and b is not None and abs(b - a) >= 0.5:
                    changements.append(f"{produit} {fmt_euros(a)} → {fmt_euros(b)} ({fmt_pct(_variation_pct(a, b), 1, signe=True)})")
            if changements:
                evts.append(Evenement(m.periode, "Prix", "Prix de vente : " + " ; ".join(changements)))
            a, b = precedent.v(PUB_MARQUE), m.v(PUB_MARQUE)
            if a is not None and b is not None and (a == 0 and b > 0 or a > 0 and abs(b - a) / a >= 0.10):
                evts.append(Evenement(m.periode, "Publicité", f"Budget de publicité de marque : {fmt_euros(a)} → {fmt_euros(b)}"))
            if not m.acquisitions and not m.cessions:
                for atelier in ATELIERS:
                    a, b = precedent.machines.get(atelier), m.machines.get(atelier)
                    if a is not None and b is not None and a != b:
                        evts.append(Evenement(m.periode, "Capacité", f"Parc {atelier} : {fmt_nombre(a)} → {fmt_nombre(b)} machine(s)"))
            a, b = precedent.v(EFFECTIFS), m.v(EFFECTIFS)
            if a is not None and b is not None and a != b:
                evts.append(Evenement(m.periode, "Ressources humaines", f"Effectif total : {fmt_nombre(a)} → {fmt_nombre(b)} personnes"))
            a, b = precedent.v(DETTE), m.v(DETTE)
            if a is not None and b is not None and b - a >= 1000:
                evts.append(Evenement(m.periode, "Financement", f"Hausse de la dette bancaire : {fmt_euros(a)} → {fmt_euros(b)} (nouvel emprunt)"))
            if m.axes_publicitaires and precedent.axes_publicitaires and m.axes_publicitaires != precedent.axes_publicitaires:
                evts.append(Evenement(m.periode, "Publicité", "Axes publicitaires : " + " / ".join(m.axes_publicitaires)))
        precedent = m if m.renseigne else precedent
    return tuple(evts[:limite])


def _concurrence(sources: SourcesERP, numero: int, fin: str) -> tuple[tuple[dict[str, Any], ...], str | None]:
    """Benchmark concurrentiel publié le plus récent (≤ fin), s'il couvre ≥ 2 entreprises."""
    period_data = {_CANON[c]: dict(m) for c, m in sources.modules.items() if c in _CANON}
    study_data = {_CANON[c]: dict(e) for c, e in sources.etudes.items() if c in _CANON}
    if not period_data and not study_data:
        return (), None
    try:
        snap = extract_competitive_snapshot(period_data, int(numero), study_data=study_data, anchor_period=fin)
    except Exception:
        return (), None
    lignes = [dict(r) for r in snap.get("rows") or [] if isinstance(r, Mapping)]
    autres = [r for r in lignes if _float(r.get("entreprise")) not in (None, float(numero))]
    if not autres:
        return (), None
    lignes.sort(key=lambda r: (-(_float(r.get("part_marche")) or -1.0), -(_float(r.get("ventes")) or -1.0)))
    return tuple(lignes), snap.get("period")


# ---------------------------------------------------------------------------
# Constats et lecture stratégique
# ---------------------------------------------------------------------------
def _constats(audit_mois: Sequence[MoisAudit], s: Synthese, produits: Sequence[LigneProduit],
              decomposition: DecompositionCA | None, concurrence: Sequence[Mapping[str, Any]],
              numero: int) -> list[Constat]:
    out: list[Constat] = []
    ca = [(m.periode, m.valeurs[CA]) for m in audit_mois if CA in m.valeurs]

    # --- Commercial -------------------------------------------------------
    if s.croissance_ca is not None and len(ca) >= 2:
        (p0, v0), (p1, v1) = ca[0], ca[-1]
        nature = "force" if s.croissance_ca > 5 else ("faiblesse" if s.croissance_ca < -5 else "info")
        analyse = ("Le marché des combinaisons est saisonnier : comparez cette évolution à la saisonnalité "
                   "publiée dans les études de marché avant de conclure à un effet de vos décisions.")
        if decomposition is not None and decomposition.ecart:
            effets = {"volume": decomposition.effet_volume, "mix": decomposition.effet_mix, "prix": decomposition.effet_prix}
            principal = max(effets, key=lambda k: abs(effets[k]))
            analyse = (f"Sur le CA théorique (prix catalogue × volumes), l'écart entre {mois_court(decomposition.periode_a)} "
                       f"et {mois_court(decomposition.periode_b)} s'explique d'abord par l'effet {principal} "
                       f"({fmt_compact_euros(effets[principal])}). " + analyse)
        out.append(Constat("Commercial", nature,
                           f"Le chiffre d'affaires mensuel passe de {fmt_euros(v0)} ({mois_court(p0)}) à "
                           f"{fmt_euros(v1)} ({mois_court(p1)}), soit {fmt_pct(s.croissance_ca, 1, signe=True)}.",
                           analyse))
    if s.meilleur_mois and s.pire_mois and len(ca) >= 3:
        out.append(Constat("Commercial", "info",
                           f"Meilleur mois : {mois_court(s.meilleur_mois[0])} ({fmt_euros(s.meilleur_mois[1])}) ; "
                           f"mois le plus faible : {mois_court(s.pire_mois[0])} ({fmt_euros(s.pire_mois[1])}).",
                           "Reliez ces extrêmes aux décisions du mois (prix, publicité, capacité) et à la saisonnalité."))
    vendus = [l for l in produits if l.part_volumes is not None and l.ventes > 0]
    if vendus:
        top = max(vendus, key=lambda l: l.part_volumes or 0)
        if (top.part_volumes or 0) >= 50:
            out.append(Constat("Commercial", "vigilance",
                               f"{top.produit} concentre {fmt_pct(top.part_volumes)} des volumes vendus sur la période.",
                               "Une forte dépendance à un produit expose l'entreprise à une attaque concurrentielle sur ce "
                               "segment : était-ce un choix de spécialisation (focalisation) assumé ?"))
        progressions = [l for l in vendus if l.evolution_ventes is not None]
        if progressions:
            meilleur = max(progressions, key=lambda l: l.evolution_ventes or 0)
            pire = min(progressions, key=lambda l: l.evolution_ventes or 0)
            if (meilleur.evolution_ventes or 0) > 10:
                out.append(Constat("Commercial", "force",
                                   f"Plus forte progression : {meilleur.produit} ({fmt_pct(meilleur.evolution_ventes, 0, signe=True)} "
                                   "entre le premier et le dernier mois vendus).",
                                   "Identifiez la décision qui l'explique (prix, publicité produit, disponibilité)."))
            if pire is not meilleur and (pire.evolution_ventes or 0) < -10:
                out.append(Constat("Commercial", "faiblesse",
                                   f"Plus forte baisse : {pire.produit} ({fmt_pct(pire.evolution_ventes, 0, signe=True)}).",
                                   "Prix trop élevé, rupture de stock, capacité insuffisante ou concurrence ? À vérifier."))
    contributions = [l for l in produits if l.contribution is not None and l.ventes > 0]
    if len(contributions) >= 2:
        best = max(contributions, key=lambda l: l.contribution or 0)
        total = sum(l.contribution or 0 for l in contributions)
        if total > 0:
            out.append(Constat("Commercial", "info",
                               f"{best.produit} apporte {fmt_pct((best.contribution or 0) / total * 100)} de la marge sur coûts "
                               f"variables estimée ({fmt_euros(best.marge_unitaire)} de marge unitaire).",
                               "Estimation au prix catalogue (hors remises de canal) et au dernier coût variable connu : "
                               "comparez la part de marge à la part de volume pour juger du mix."))
    if s.pdm_debut and s.pdm_fin and s.pdm_debut[0] != s.pdm_fin[0]:
        ecart = s.pdm_fin[1] - s.pdm_debut[1]
        nature = "force" if ecart > 0.5 else ("faiblesse" if ecart < -0.5 else "info")
        out.append(Constat("Commercial", nature,
                           f"La part de marché passe de {fmt_pct(s.pdm_debut[1], 2)} ({mois_court(s.pdm_debut[0])}) à "
                           f"{fmt_pct(s.pdm_fin[1], 2)} ({mois_court(s.pdm_fin[0])}), soit "
                           f"{'+' if ecart > 0 else ''}{fmt_nombre(ecart, 2)} point{'s' if abs(ecart) >= 2 else ''}.",
                           "La part de marché mesure la position relative : une hausse du CA avec une PDM en baisse "
                           "signifie que le marché a crû plus vite que l'entreprise."))
    if concurrence:
        classement = [r for r in concurrence]
        rang = next((i + 1 for i, r in enumerate(classement) if _float(r.get("entreprise")) == float(numero)), None)
        if rang is not None:
            out.append(Constat("Commercial", "force" if rang <= 3 else "info",
                               f"Benchmark concurrentiel : l'entreprise est classée {rang}ᵉ sur {len(classement)} "
                               "entreprises observées.",
                               "Comparez prix, publicité et qualité des concurrents les mieux classés aux vôtres."))
    indices = [l.indice_prix for l in produits if l.indice_prix is not None and l.ventes > 0]
    if indices:
        moyen = sum(indices) / len(indices)
        position = ("inférieurs aux" if moyen < 0.97 else "supérieurs aux" if moyen > 1.03 else "alignés sur les")
        out.append(Constat("Commercial", "info",
                           f"En fin de période, les prix de vente sont en moyenne {position} prix du marché "
                           f"(indice {fmt_nombre(moyen * 100, 0)} pour une base 100 = marché).",
                           "Un prix sous le marché soutient les volumes mais réduit la marge unitaire ; un prix au-dessus "
                           "doit être justifié par la qualité ou l'image."))

    # --- Financier --------------------------------------------------------
    if s.resultat_total is not None:
        nature = "force" if s.resultat_total > 0 else "faiblesse"
        marge = f", soit une marge nette de {fmt_pct(s.marge_nette)} du CA" if s.marge_nette is not None else ""
        out.append(Constat("Financier", nature,
                           f"Résultat net cumulé sur {s.resultat_mois} mois renseigné(s) : {fmt_euros(s.resultat_total)}{marge}.",
                           "Distinguez ce qui relève de l'exploitation (marges, charges fixes) de l'exceptionnel "
                           "(cessions, placements)."))
    if s.mois_deficitaires:
        out.append(Constat("Financier", "vigilance",
                           f"{len(s.mois_deficitaires)} mois déficitaire(s) : "
                           + ", ".join(mois_court(p) for p in s.mois_deficitaires) + ".",
                           "Charges fixes non couvertes ce(s) mois-là : volume insuffisant, prix trop bas ou investissement ?"))
    if s.tresorerie_min is not None:
        if s.tresorerie_min[1] < 0:
            out.append(Constat("Financier", "faiblesse",
                               f"Découvert bancaire en {mois_court(s.tresorerie_min[0])} : {fmt_euros(s.tresorerie_min[1])}.",
                               "Un découvert génère des agios et signale un besoin de financement non anticipé "
                               "(BFR, investissement) : le plan de trésorerie aurait-il pu le prévoir ?"))
        depart = None
        if s.tresorerie_ouverture is not None:
            depart = (s.tresorerie_ouverture[1], f"à l'ouverture de {mois_court(s.tresorerie_ouverture[0])}")
        elif s.tresorerie_debut is not None and s.tresorerie_cloture is not None and s.tresorerie_debut[0] != s.tresorerie_cloture[0]:
            depart = (s.tresorerie_debut[1], f"fin {mois_court(s.tresorerie_debut[0])}")
        if depart is not None and s.tresorerie_cloture is not None:
            variation = s.tresorerie_cloture[1] - depart[0]
            analyse = "La trésorerie résulte du résultat, de la variation du BFR, des investissements et du financement."
            if variation < 0 and (s.resultat_total or 0) > 0:
                analyse = ("Résultat positif mais trésorerie en baisse : la différence s'explique par la hausse du BFR "
                           "(stocks, créances clients), les investissements, les impôts ou les remboursements d'emprunt.")
            out.append(Constat("Financier", "info" if variation >= 0 else "vigilance",
                               f"La trésorerie passe de {fmt_euros(depart[0])} ({depart[1]}) à "
                               f"{fmt_euros(s.tresorerie_cloture[1])} (fin {mois_court(s.tresorerie_cloture[0])}), "
                               f"soit {fmt_euros_signe(variation)}.", analyse))
    if s.capitaux_propres_debut is not None and s.capitaux_propres_fin is not None:
        variation = _variation_pct(s.capitaux_propres_debut, s.capitaux_propres_fin)
        if variation is not None and abs(variation) >= 1:
            out.append(Constat("Financier", "force" if variation > 0 else "faiblesse",
                               f"Les capitaux propres évoluent de {fmt_euros(s.capitaux_propres_debut)} à "
                               f"{fmt_euros(s.capitaux_propres_fin)} ({fmt_pct(variation, 1, signe=True)}).",
                               "Les capitaux propres intègrent les résultats successifs : ils mesurent la création de valeur."))
    if s.dette_fin is not None and s.capitaux_propres_fin:
        ratio = s.dette_fin / s.capitaux_propres_fin
        out.append(Constat("Financier", "vigilance" if ratio > 1 else "info",
                           f"Endettement en fin de période : {fmt_euros(s.dette_fin)}, soit "
                           f"{fmt_pct(ratio * 100, 0)} des capitaux propres.",
                           "Au-delà de 100 %, l'indépendance financière se dégrade ; l'emprunt reste pertinent s'il "
                           "finance une capacité rentable."))
    if s.ca_cumule_publie and s.ca_total is not None and s.ca_mois == s.n_mois and s.n_mois >= 2:
        debut_annee = audit_mois[0].periode.endswith("Janvier")
        meme_annee = audit_mois[0].periode.split(" - ")[0] == audit_mois[-1].periode.split(" - ")[0]
        if debut_annee and meme_annee and s.ca_cumule_publie[1]:
            ecart = abs(s.ca_total - s.ca_cumule_publie[1]) / abs(s.ca_cumule_publie[1]) * 100
            if ecart > 1:
                out.append(Constat("Données", "vigilance",
                                   f"La somme des CA mensuels ({fmt_euros(s.ca_total)}) diffère de {fmt_pct(ecart)} du CA "
                                   f"cumulé publié par le jeu ({fmt_euros(s.ca_cumule_publie[1])}).",
                                   "Vérifiez les mois synchronisés : un écart signale une donnée manquante ou un "
                                   "périmètre différent (produits négociés, ventes hors marché)."))
    if s.ratio_pub_ca is not None:
        out.append(Constat("Commercial", "info",
                           f"L'effort publicitaire (marque + produits) représente {fmt_pct(s.ratio_pub_ca)} du CA "
                           f"({fmt_euros(s.pub_totale)} sur la période).",
                           "Mettez cet effort en regard de l'évolution de la part de marché et de la notoriété."))

    # --- Industriel -------------------------------------------------------
    for atelier, taux in s.utilisation_moyenne.items():
        periode_max, taux_max = s.utilisation_max.get(atelier, ("", taux))
        if taux_max >= 0.95:
            out.append(Constat("Industriel", "vigilance",
                               f"Atelier {atelier} saturé : jusqu'à {fmt_pct(taux_max * 100, 0)} de sa capacité "
                               f"({mois_court(periode_max)}), {fmt_pct(taux * 100, 0)} en moyenne.",
                               "Un goulot limite les ventes possibles : l'investissement ou les heures supplémentaires "
                               "ont-ils été arbitrés à temps ?"))
        elif taux < 0.60:
            out.append(Constat("Industriel", "faiblesse",
                               f"Atelier {atelier} sous-utilisé : {fmt_pct(taux * 100, 0)} de sa capacité en moyenne.",
                               "Une surcapacité pèse sur les charges fixes (amortissements, salaires) sans produire de marge."))
        else:
            out.append(Constat("Industriel", "info",
                               f"Atelier {atelier} : {fmt_pct(taux * 100, 0)} de sa capacité utilisée en moyenne.", ""))
    if s.acquisitions_total or s.cessions_total:
        parts = [f"{fmt_nombre(n)} achetée(s) en {a}" for a, n in s.acquisitions_total.items()]
        parts += [f"{fmt_nombre(n)} cédée(s) en {a}" for a, n in s.cessions_total.items()]
        out.append(Constat("Industriel", "info", "Parc machines : " + " ; ".join(parts) + ".",
                           "Justifiez chaque investissement par la demande prévue et le taux d'utilisation observé."))
    elif s.machines_debut and s.machines_fin and s.machines_debut != s.machines_fin:
        parts = [f"{a} {fmt_nombre(s.machines_debut.get(a))} → {fmt_nombre(s.machines_fin.get(a))}" for a in ATELIERS
                 if a in s.machines_debut or a in s.machines_fin]
        out.append(Constat("Industriel", "info", "Évolution du parc machines : " + " ; ".join(parts) + ".", ""))

    # --- Ressources humaines -----------------------------------------------
    if s.effectifs_debut is not None and s.effectifs_fin is not None and s.effectifs_debut != s.effectifs_fin:
        out.append(Constat("Ressources humaines", "info",
                           f"L'effectif passe de {fmt_nombre(s.effectifs_debut)} à {fmt_nombre(s.effectifs_fin)} personnes.",
                           "Rapprochez l'évolution des effectifs de celle de l'activité (volumes produits)."))
    derniers_scores: dict[str, float] = {}
    for m in audit_mois:
        derniers_scores.update(m.scores_rh)
    for pole, score in derniers_scores.items():
        if score >= 75:
            out.append(Constat("Ressources humaines", "force", f"Score RH du pôle {pole} : {fmt_nombre(score, 0)}/100.", ""))
        elif score < 50:
            out.append(Constat("Ressources humaines", "faiblesse", f"Score RH du pôle {pole} : {fmt_nombre(score, 0)}/100.",
                               "Masse salariale éloignée de la norme ou dispersion des salaires : voir l'audit du service."))
    return out


def lecture_strategique(s: Synthese, produits: Sequence[LigneProduit]) -> tuple[str, ...]:
    """Lecture proposée des choix stratégiques, à partir des seules décisions observées."""
    lignes: list[str] = []
    indices = [l.indice_prix for l in produits if l.indice_prix is not None and l.ventes > 0]
    if indices:
        moyen = sum(indices) / len(indices)
        if moyen < 0.97:
            lignes.append(f"Positionnement prix : en moyenne {fmt_pct((1 - moyen) * 100, 0)} sous le prix de marché — "
                          "une logique de volume, proche de la domination par les coûts (Porter).")
        elif moyen > 1.03:
            lignes.append(f"Positionnement prix : en moyenne {fmt_pct((moyen - 1) * 100, 0)} au-dessus du prix de marché — "
                          "une logique de valorisation, proche de la différenciation (Porter).")
        else:
            lignes.append("Positionnement prix : aligné sur le marché — la différence se joue alors sur la qualité, "
                          "la publicité et la disponibilité des produits.")
    vendus = sorted((l for l in produits if l.part_volumes), key=lambda l: l.part_volumes or 0, reverse=True)
    if vendus:
        top = vendus[0]
        top2 = sum(l.part_volumes or 0 for l in vendus[:2])
        if (top.part_volumes or 0) >= 50:
            lignes.append(f"Politique de gamme : focalisation sur {top.produit} ({fmt_pct(top.part_volumes, 0)} des volumes).")
        elif top2 >= 75:
            lignes.append(f"Politique de gamme : gamme resserrée — {vendus[0].produit} et {vendus[1].produit} "
                          f"font {fmt_pct(top2, 0)} des volumes.")
        else:
            lignes.append("Politique de gamme : ventes réparties sur l'ensemble de la gamme.")
    if s.acquisitions_total:
        lignes.append("Politique industrielle : investissement de capacité en cours d'année ("
                      + ", ".join(f"{fmt_nombre(n)} {a}" for a, n in s.acquisitions_total.items()) + ").")
    elif s.utilisation_moyenne:
        moyenne = sum(s.utilisation_moyenne.values()) / len(s.utilisation_moyenne)
        lignes.append(f"Politique industrielle : parc stable, utilisé à {fmt_pct(moyenne * 100, 0)} en moyenne.")
    if s.dette_debut is not None and s.dette_fin is not None and s.dette_fin > s.dette_debut + 1000:
        lignes.append("Politique financière : recours à l'emprunt pendant la période.")
    elif s.dette_debut is not None and s.dette_fin is not None and s.dette_fin < s.dette_debut:
        lignes.append("Politique financière : désendettement progressif (remboursements sans nouvel emprunt).")
    if s.ratio_pub_ca is not None:
        lignes.append(f"Politique de communication : {fmt_pct(s.ratio_pub_ca)} du CA investi en publicité.")
    return tuple(lignes)


def manques_donnees(mois: Sequence[MoisAudit]) -> tuple[Manque, ...]:
    """Données manquantes regroupées par élément, avec l'étude qui les fournirait."""
    regles = (
        (CA, "Chiffre d'affaires mensuel", "Courbes et totaux de CA incomplets", lambda m: CA in m.valeurs),
        (RESULTAT, "Résultat net mensuel", "Rentabilité calculée sur une partie de l'année", lambda m: RESULTAT in m.valeurs),
        (TRESORERIE, "Trésorerie de fin de mois", "Courbe de trésorerie incomplète", lambda m: TRESORERIE in m.valeurs),
        (VENTES_TOTALES, "Ventes par produit", "Mix produit et décomposition prix/volume incomplets", lambda m: bool(m.ventes)),
        (PDM, "Part de marché", "Évolution de la position concurrentielle incomplète", lambda m: PDM in m.valeurs),
    )
    out: list[Manque] = []
    for cle, element, consequence, present in regles:
        absents = tuple(m.periode for m in mois if not present(m))
        if absents:
            out.append(Manque(element, absents, consequence, ETUDES_CONSEILLEES.get(cle, ())))
    if not any(m.prix for m in mois):
        out.append(Manque("Prix de vente décidés", tuple(m.periode for m in mois),
                          "Chronologie des prix, indice de prix et décomposition prix/volume indisponibles",
                          ()))
    if not any(m.utilisation for m in mois):
        out.append(Manque("Utilisation des ateliers", tuple(m.periode for m in mois),
                          "Analyse de capacité indisponible", ETUDES_CONSEILLEES["utilisation"]))
    return tuple(out)


def construire_audit(
    sources: SourcesERP,
    debut: str,
    fin: str,
    numero: int = 3,
    entreprise: str = "",
) -> AuditAnnuel:
    """Audit complet de ``debut`` à ``fin`` (inclus)."""
    periodes = periodes_entre(debut, fin)
    publiees = ventes_publiees(sources)
    mois = tuple(analyser_mois(sources, p, publiees) for p in periodes)
    prec = periode_precedente(periodes[0]) if periodes else None
    ouverture = analyser_mois(sources, prec, publiees) if prec else None
    if ouverture is not None and not ouverture.renseigne:
        ouverture = None
    synthese = calculer_synthese(mois, ouverture)
    produits = analyser_produits(mois)
    decomposition = _decomposition_annuelle(mois)
    concurrence, periode_concurrence = _concurrence(sources, numero, periodes[-1]) if periodes else ((), None)
    constats = _constats(mois, synthese, produits, decomposition, concurrence, numero)
    manques = manques_donnees(mois)
    if manques:
        constats.append(Constat("Données", "vigilance",
                                f"{len(manques)} type(s) de donnée incomplet(s) sur la période : "
                                + ", ".join(m.element.lower() for m in manques) + ".",
                                "Les analyses correspondantes portent sur les seuls mois renseignés (voir annexe)."))
    return AuditAnnuel(
        entreprise=entreprise or f"Entreprise {numero}",
        numero=int(numero),
        debut=periodes[0] if periodes else debut,
        fin=periodes[-1] if periodes else fin,
        mois=mois,
        ouverture=ouverture,
        synthese=synthese,
        produits=produits,
        decomposition=decomposition,
        chronologie=chronologie_decisions(mois),
        concurrence=concurrence,
        concurrence_periode=periode_concurrence,
        constats=tuple(constats),
        lecture_strategique=lecture_strategique(synthese, produits),
        manques=manques,
    )


def periode_par_defaut(sources: SourcesERP) -> tuple[str, str]:
    """Période auditée par défaut : l'année de jeu la plus récente couverte par des données.

    Le début est le premier mois renseigné de cette année (janvier en
    général), la fin le dernier mois renseigné.
    """
    disponibles = [p for p in sources.periodes() if not p.startswith("Année 0")] or sources.periodes()
    if not disponibles:
        return TOURS_PERIODES[1], TOURS_PERIODES[12]
    fin = disponibles[-1]
    annee = fin.split(" - ")[0]
    meme_annee = [p for p in disponibles if p.startswith(annee)]
    return (meme_annee[0] if meme_annee else disponibles[0]), fin


# ---------------------------------------------------------------------------
# Contenu rédigé par l'équipe
# ---------------------------------------------------------------------------
#: Consignes affichées dans l'interface et, si le champ est vide, dans le document.
CONSIGNES: dict[str, str] = {
    "synthese": "En 6 à 10 lignes : la stratégie retenue, les trois résultats clés (CA, résultat, part de marché) "
                "et le principal enseignement de l'année.",
    "presentation": "Présentez l'entreprise (produits, marché, concurrents), l'équipe et la répartition des rôles.",
    "strategie": "Expliquez la ou les stratégies mises en œuvre : positionnement (prix, qualité, gamme), politique "
                 "commerciale, industrielle, RH et financière. Justifiez chaque choix (pourquoi ? avec quelles "
                 "informations ?) et expliquez les inflexions décidées en cours d'année.",
    "outils": "Présentez les outils montés ou utilisés pour décider (modules de l'ERP, tableaux de bord, fichiers) : "
              "le besoin, le fonctionnement, l'apport concret à vos décisions.",
    "methodes": "Décrivez vos méthodes de travail : organisation, rituels de décision, outils de gestion de projet "
                "(rétroplanning, Kanban, RACI…), communication, gestion du temps et des désaccords.",
    "prise_de_recul": "Expliquez vos résultats (ce qui a fonctionné, ce qui a échoué, pourquoi), comparez-les à vos "
                      "objectifs et aux concurrents, et dites ce que vous feriez différemment. Appuyez-vous sur les "
                      "constats chiffrés de l'audit.",
    "conclusion": "Bilan global, enseignements clés et compétences mobilisées (lien avec la formation).",
    "role": "Votre fonction dans l'équipe, votre périmètre de décision et vos missions (dont la mission spécifique "
            "décrite dans les documents du jeu).",
    "outils_membre": "Les outils que vous avez montés : objectif, fonctionnement, apport à la décision.",
    "forces": "Vos points forts, illustrés par une situation concrète du jeu.",
    "faiblesses": "Vos axes de progrès, illustrés par une situation concrète du jeu.",
    "reussites": "Une ou deux décisions réussies : contexte, décision, résultat chiffré.",
    "erreurs": "Une ou deux erreurs : contexte, décision, conséquence, ce que vous en retenez.",
    "mission_specifique": "La mission spécifique de votre fonction : ce qui était demandé et ce que vous avez produit.",
    "bilan": "Ce que cette SAE vous a appris (compétences, posture professionnelle).",
}

#: Passage à compléter par l'équipe, seul (champ vide) ou au fil d'un texte rédigé :
#: « [À COMPLÉTER] » ou « [À COMPLÉTER : ce qu'il faut préciser] ». Surligné dans le Word.
A_COMPLETER = re.compile(r"\[À COMPLÉTER(?:\s*:[^\]]*)?\]", re.IGNORECASE)

#: Champs rédigés de la partie collective : (champ, libellé, nombre de mots visé).
CHAMPS_COLLECTIFS: tuple[tuple[str, str, int], ...] = (
    ("synthese", "Synthèse de l'année (résumé)", 120),
    ("presentation", "Présentation de l'entreprise et de l'équipe", 100),
    ("strategie", "Les stratégies mises en œuvre", 150),
    ("outils", "Les outils (complément à la liste des outils de l'ERP)", 60),
    ("methodes", "Les méthodes de travail et la gestion de projet", 80),
    ("prise_de_recul", "Explication et prise de recul sur les résultats", 150),
    ("conclusion", "Conclusion de la partie collective", 60),
)
#: Champs rédigés de chaque partie individuelle : (champ du membre, libellé, clé de consigne).
CHAMPS_INDIVIDUELS: tuple[tuple[str, str, str], ...] = (
    ("outils", "Les outils que j'ai montés", "outils_membre"),
    ("forces", "Mes forces", "forces"),
    ("faiblesses", "Mes faiblesses", "faiblesses"),
    ("reussites", "Mes réussites", "reussites"),
    ("erreurs", "Mes erreurs", "erreurs"),
    ("mission_specifique", "Ma mission spécifique", "mission_specifique"),
    ("bilan", "Bilan personnel", "bilan"),
)


@dataclass(frozen=True)
class Membre:
    nom: str = ""
    role: str = ""
    missions: str = ""
    outils: str = ""
    outils_erp: tuple[str, ...] = ()
    forces: str = ""
    faiblesses: str = ""
    reussites: str = ""
    erreurs: str = ""
    mission_specifique: str = ""
    bilan: str = ""


@dataclass(frozen=True)
class ContenuRapport:
    titre: str = "Rapport professionnel"
    sous_titre: str = "Audit stratégique et opérationnel de l'année"
    entreprise: str = ""
    equipe: str = ""
    formation: str = "BUT MLT — 3e année (parcours MSCC)"
    etablissement: str = ""
    enseignants: str = ""
    date: str = ""
    #: Paragraphe de contexte de l'équipe : entrée de la rédaction assistée, gardé
    #: dans le brouillon JSON mais absent du Word.
    contexte: str = ""
    synthese: str = ""
    presentation: str = ""
    strategie: str = ""
    outils: str = ""
    outils_erp: tuple[str, ...] = tuple(o.nom for o in OUTILS_ERP)
    methodes: str = ""
    outils_gp: tuple[str, ...] = ()
    prise_de_recul: str = ""
    conclusion: str = ""
    membres: tuple[Membre, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["membres"] = [asdict(m) for m in self.membres]
        return d

    def to_json(self) -> str:
        return json.dumps({"format": "rapport_subakoua", "version": 1, "contenu": self.to_dict()},
                          ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContenuRapport":
        if not isinstance(data, Mapping):
            return cls()
        data = data.get("contenu", data) if isinstance(data.get("contenu"), Mapping) else data
        champs = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        valeurs: dict[str, Any] = {}
        for cle, valeur in data.items():
            if cle not in champs:
                continue
            if cle == "membres":
                membres = []
                for m in valeur or []:
                    if isinstance(m, Mapping):
                        champs_m = Membre.__dataclass_fields__  # type: ignore[attr-defined]
                        brut = {k: v for k, v in m.items() if k in champs_m}
                        if "outils_erp" in brut:
                            brut["outils_erp"] = tuple(str(x) for x in brut["outils_erp"] or ())
                        membres.append(Membre(**{k: (v if k == "outils_erp" else str(v or "")) for k, v in brut.items()}))
                valeurs["membres"] = tuple(membres)
            elif cle in ("outils_erp", "outils_gp"):
                valeurs[cle] = tuple(str(x) for x in valeur or ())
            else:
                valeurs[cle] = "" if valeur is None else str(valeur)
        return cls(**valeurs)

    @classmethod
    def from_json(cls, texte: str | bytes) -> "ContenuRapport":
        """Brouillon relu depuis son JSON ; un fichier illisible lève ``ValueError`` (jamais un brouillon vide)."""
        if isinstance(texte, (bytes, bytearray)):
            texte = bytes(texte).decode("utf-8", errors="replace")
        try:
            brut = json.loads(texte)
        except (TypeError, ValueError) as exc:
            raise ValueError("Brouillon illisible : JSON attendu.") from exc
        if not isinstance(brut, Mapping):
            raise ValueError("Brouillon illisible : objet JSON attendu.")
        return cls.from_dict(brut)


def nombre_mots(texte: Any) -> int:
    return len(re.findall(r"\w+", str(texte or "")))


#: Fonctions -> familles d'indicateurs mises en avant dans la partie individuelle.
_PERIMETRES = (
    (("financ", "daf", "trésor", "tresor", "comptab", "gestion"), (CA, RESULTAT, TRESORERIE, CAPITAUX_PROPRES, DETTE)),
    (("market", "commercial", "vente", "communication"), (CA, VENTES_TOTALES, PDM, PUB_MARQUE)),
    (("product", "industri", "usine", "atelier"), (PRODUCTION_TOTALE, VENTES_TOTALES, STOCKS_PRODUITS)),
    (("appro", "achat", "logisti", "supply", "stock"), (STOCKS_MATIERES, PRODUCTION_TOTALE, BFR)),
    (("rh", "ressources humaines", "humain", "personnel"), (EFFECTIFS, MASSE_SALARIALE)),
    (("direct", "dg", "pdg", "manager", "chef de projet", "coordin"), (CA, RESULTAT, PDM, TRESORERIE)),
)


def perimetre_role(role: str) -> tuple[str, ...]:
    """Indicateurs du périmètre d'une fonction (« Responsable financier » -> CA, résultat…)."""
    texte = str(role or "").casefold()
    cles: list[str] = []
    for mots, indicateurs in _PERIMETRES:
        if any(m in texte for m in mots):
            for i in indicateurs:
                if i not in cles:
                    cles.append(i)
    return tuple(cles)


@dataclass(frozen=True)
class Critere:
    partie: str
    critere: str
    statut: str   # ok | partiel | manquant
    detail: str = ""


def controle_completude(audit: AuditAnnuel, contenu: ContenuRapport, logo: bool = False,
                        nb_graphiques: int = 0) -> tuple[Critere, ...]:
    """Le rapport couvre-t-il les attendus de l'évaluation ?"""
    out: list[Critere] = []

    def texte(partie: str, critere: str, valeur: str, mini: int) -> None:
        n = nombre_mots(valeur)
        statut = "ok" if n >= mini else ("partiel" if n > 0 else "manquant")
        out.append(Critere(partie, critere, statut, f"{n} mot(s) — viser au moins {mini}"))

    collectif = "Partie collective"
    texte(collectif, "Explication de la (des) stratégie(s) mise(s) en œuvre", contenu.strategie, 150)
    nb_outils = len(contenu.outils_erp) + (1 if nombre_mots(contenu.outils) >= 20 else 0)
    out.append(Critere(collectif, "Restitution des outils", "ok" if nb_outils else "manquant",
                       f"{len(contenu.outils_erp)} outil(s) de l'ERP retenu(s)"))
    n_methodes = nombre_mots(contenu.methodes)
    statut = "ok" if n_methodes >= 80 or (contenu.outils_gp and n_methodes >= 30) else ("partiel" if n_methodes or contenu.outils_gp else "manquant")
    out.append(Critere(collectif, "Méthodes de travail (gestion de projet)", statut,
                       f"{n_methodes} mot(s), {len(contenu.outils_gp)} outil(s) de GP"))
    texte(collectif, "Explication et prise de recul sur les résultats", contenu.prise_de_recul, 150)
    s = audit.synthese
    statut = "ok" if s.ca_mois >= max(1, s.n_mois - 1) and s.ca_mois >= 3 else ("partiel" if s.ca_mois else "manquant")
    out.append(Critere(collectif, "Audit chiffré de l'année (description)", statut,
                       f"CA connu sur {s.ca_mois}/{s.n_mois} mois, {len(audit.constats)} constat(s)"))
    out.append(Critere(collectif, "Analyse (constats et lecture stratégique)",
                       "ok" if len(audit.constats) >= 5 else ("partiel" if audit.constats else "manquant"),
                       f"{len(audit.constats)} constat(s), {len(audit.lecture_strategique)} élément(s) de lecture stratégique"))
    texte(collectif, "Conclusion", contenu.conclusion, 60)
    forme = "Forme"
    out.append(Critere(forme, "Graphiques commentés", "ok" if nb_graphiques >= 3 else ("partiel" if nb_graphiques else "manquant"),
                       f"{nb_graphiques} graphique(s)"))
    out.append(Critere(forme, "Tableaux", "ok" if s.ca_mois or s.ventes_mois else "manquant", "Tableau de bord mensuel et tableau produits"))
    out.append(Critere(forme, "Logo de l'entreprise", "ok" if logo else "manquant", "Ajoutez le logo dans l'onglet Identité"))
    out.append(Critere(forme, "Page de garde complète", "ok" if contenu.entreprise and contenu.membres else "partiel",
                       "Nom de l'entreprise et membres de l'équipe"))
    individuel = "Partie individuelle"
    if not contenu.membres:
        out.append(Critere(individuel, "Membres de l'équipe", "manquant", "Ajoutez chaque membre et son rôle"))
    for m in contenu.membres:
        nom = m.nom or "Membre sans nom"
        champs = {
            "rôle": m.role or m.missions, "outils montés": m.outils or ", ".join(m.outils_erp),
            "forces": m.forces, "faiblesses": m.faiblesses, "réussites": m.reussites, "erreurs": m.erreurs,
        }
        remplis = [k for k, v in champs.items() if nombre_mots(v) > 0]
        manquants = [k for k in champs if k not in remplis]
        statut = "ok" if not manquants else ("partiel" if remplis else "manquant")
        out.append(Critere(individuel, nom, statut, "complet" if not manquants else "à compléter : " + ", ".join(manquants)))
    return tuple(out)


def taux_completude(criteres: Sequence[Critere]) -> float:
    if not criteres:
        return 0.0
    points = {"ok": 1.0, "partiel": 0.5, "manquant": 0.0}
    return sum(points.get(c.statut, 0.0) for c in criteres) / len(criteres)
