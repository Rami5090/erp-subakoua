"""Prévision des cours : actions, obligations et prix de vente.

Le module prédit la valeur d'une série de prix à la période suivante à partir
de son historique — le « cours du jour » des périodes précédentes. Les
données d'entrée sont :

* les **cours des titres** (actions A1/A2/A3, obligations O1/O2/O3) publiés
  chaque mois par l'étude « Portefeuille actions / obligations » ;
* les **prix de vente** des anciennes décisions de l'entreprise
  (`Decisions_Marketing`), qui fixent le prix « du jour » sur le marché.

Choix de méthode
----------------
Les séries de prix sont courtes et souvent plates : un modèle_statistique
lourd serait instable et illisible. On compare donc des méthodes **simples et
explicables**, et la méthode retenue est choisie par **validation
hors-échantillon** (backtest à un pas, comme pour les volumes) :

===============  ==========================================================
Méthode          Principe
===============  ==========================================================
``dernier``      le dernier cours se maintient (référence naïve)
``derive``       la variation moyenne des derniers cours est reconduite
``derive_amortie`` idem, mais la tendance est amortie (évite l'emballement)
``moyenne``      la moyenne des 3 derniers cours
``niveau``       la moyenne de toute la série (retour à la moyenne)
===============  ==========================================================

L'intervalle affiché n'est **pas** un intervalle de confiance statistique :
c'est une **bande d'erreur empirique** (± erreur absolue moyenne du backtest
de la méthode retenue). Aucune valeur n'est inventée : sans historique
suffisant, aucune prévision n'est produite et l'avertissement le dit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
import re

from constants import period_to_seq

#: Mois sans accent (forme normalisée utilisée par le cache des études).
_MOIS_NORMALISES = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}

#: Méthodes candidates, dans l'ordre de préférence à égalité de performance.
METHODES = ("dernier", "derive", "derive_amortie", "moyenne", "niveau")

#: Nombre de points minimum pour/backtester.
MIN_POINTS_BACKTEST = 3

#: Facteur d'amortissement de la tendance (0 < f < 1).
FACTEUR_AMORTISSEMENT = 0.6

#: Nombre de points utilisés par la méthode « moyenne ».
FENETRE_MOYENNE = 3


def _seq_periode(periode: str) -> int | None:
    """Indice chronologique d'un libellé de période.

    Le libellé peut être « Année 1 - Août » (données brutes) ou sa forme
    normalisée « annee 1 - aout » (cache interne des études) : les deux
    doivent donner le même indice, sinon l'historique est trié dans le désordre
    et la prévision perd tout sens.
    """
    try:
        return period_to_seq(periode)
    except Exception:
        pass
    m = re.search(r"annee\s*(\d+)\s*-\s*([a-z]+)", str(periode or "").casefold())
    if not m:
        return None
    mois = _MOIS_NORMALISES.get(m.group(2))
    if mois is None:
        return None
    return (int(m.group(1)) - 1) * 12 + mois


def _cle_tri(periode: str) -> tuple[int, str]:
    """Clé de tri chronologique d'un libellé de période (« Année 1 - Mars »).

    Le tri alphabétique serait faux (« Février » avant « Janvier ») : la série
    doit être dans l'ordre du temps, sinon la prévision est nonsensicale.
    """
    seq = _seq_periode(periode)
    if seq is None:
        return (10 ** 6, periode)
    return (seq, periode)


def _num(value: Any, default: float | None = None) -> float | None:
    """Nombre lu de façon tolérante : « 1 234,56 € », « 1234.56 », 12.5…"""
    if value is None or value == "" or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        out = float(value)
        return default if out != out else out
    texte = (
        str(value).replace("\u202f", "").replace("\xa0", "").replace(" ", "")
        .replace("€", "").replace("%", "")
    )
    if texte in ("", "-", "--"):
        return default
    if "," in texte and "." in texte:
        texte = texte.replace(".", "").replace(",", ".")
    else:
        texte = texte.replace(",", ".")
    try:
        out = float(texte)
    except ValueError:
        return default
    return default if out != out else out


@dataclass(frozen=True)
class PricePoint:
    """Un cours observé : une période et sa valeur."""

    periode: str
    valeur: float


@dataclass(frozen=True)
class CoursForecast:
    """Prévision d'un cours à la période suivante."""

    cle: str
    libelle: str
    serie: tuple[PricePoint, ...] = ()
    methode: str = "dernier"
    prevision: float | None = None
    borne_basse: float | None = None
    borne_haute: float | None = None
    confiance: str = "faible"
    derive_relative: float | None = None
    erreur_moyenne: float | None = None
    avertissements: tuple[str, ...] = ()

    @property
    def dernier(self) -> float | None:
        return self.serie[-1].valeur if self.serie else None

    @property
    def variation_prevue(self) -> float | None:
        if self.dernier is None or self.prevision is None:
            return None
        return self.prevision - self.dernier


@dataclass(frozen=True)
class CoursForecastSet:
    """Prévisions d'un ensemble de cours (actions, obligations, produits)."""

    cours: tuple[CoursForecast, ...] = ()
    methode_par_cle: Mapping[str, str] = field(default_factory=dict)
    avertissements: tuple[str, ...] = ()

    def par_cle(self, cle: str) -> CoursForecast | None:
        for c in self.cours:
            if c.cle == cle:
                return c
        return None


# --------------------------------------------------------------------------- méthodes
def _appliquer(methode: str, serie: Sequence[float]) -> float | None:
    """Valeur prédite par la méthode, à partir de la série d'observation."""
    if not serie:
        return None
    dernier = serie[-1]
    if methode == "dernier":
        return dernier
    if methode == "moyenne":
        fenetre = serie[-FENETRE_MOYENNE:]
        return sum(fenetre) / len(fenetre)
    if methode == "niveau":
        return sum(serie) / len(serie)
    if methode in ("derive", "derive_amortie"):
        if len(serie) < 2:
            return dernier
        variations = [serie[i] - serie[i - 1] for i in range(1, len(serie))]
        derive = sum(variations) / len(variations)
        if methode == "derive_amortie":
            derive *= FACTEUR_AMORTISSEMENT
        return dernier + derive
    return dernier


def _backtest(serie: Sequence[float], methode: str) -> tuple[float | None, int]:
    """Erreur absolue moyenne en validation hors-échantillon (un pas d'avance).

    Pour chaque point à partir de `MIN_POINTS_BACKTEST`, la méthode est
    appliquée sur l'historique court et comparée au cours réellement observé.
    """
    if len(serie) < MIN_POINTS_BACKTEST:
        return None, 0
    erreurs: list[float] = []
    for i in range(MIN_POINTS_BACKTEST - 1, len(serie)):
        historique = serie[:i]
        predit = _appliquer(methode, historique)
        if predit is None:
            continue
        erreurs.append(abs(serie[i] - predit))
    if not erreurs:
        return None, 0
    return sum(erreurs) / len(erreurs), len(erreurs)


def _confiance(erreur: float | None, serie: Sequence[float]) -> str:
    """Confiance déduite du nombre d'observations et de la qualité du backtest."""
    n = len(serie)
    if n < MIN_POINTS_BACKTEST or erreur is None:
        return "faible"
    niveau = sum(abs(v) for v in serie) / len(serie)
    if niveau <= 0:
        return "faible"
    mape = erreur / niveau
    if n >= 6 and mape <= 0.05:
        return "bonne"
    if n >= 4 and mape <= 0.15:
        return "moyenne"
    return "faible"


def forecast_cours(
    cle: str,
    libelle: str,
    serie: Sequence[PricePoint] | Iterable[Mapping[str, Any]],
) -> CoursForecast:
    """Prévoit le cours suivant d'une série (historique par période)."""
    avertissements: list[str] = []
    points: list[PricePoint] = []
    for item in serie or []:
        if isinstance(item, PricePoint):
            points.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        valeur = _num(item.get("valeur", item.get("prix", item.get("cours"))))
        periode = str(item.get("periode", item.get("mois", "")) or "")
        if valeur is None or not periode:
            continue
        points.append(PricePoint(periode=periode, valeur=valeur))
    # Ordre chronologique explicite : l'ordre de lecture de la base n'est pas
    # garanti, et une série mal ordonnée fausse toute prévision.
    points.sort(key=lambda p: _cle_tri(p.periode))
    valeurs = [p.valeur for p in points]

    if len(valeurs) < 2:
        return CoursForecast(
            cle=cle, libelle=libelle, serie=tuple(points), methode="dernier",
            prevision=None, confiance="faible",
            avertissements=(
                "Historique insuffisant (2 cours minimum) : aucune prévision produite.",
            ),
        )

    resultats: dict[str, tuple[float | None, int]] = {}
    for methode in METHODES:
        resultats[methode] = _backtest(valeurs, methode)

    eligibles = {m: r for m, r in resultats.items() if r[0] is not None}
    if eligibles:
        methode = min(eligibles, key=lambda m: (eligibles[m][0], METHODES.index(m)))
        erreur, n_tests = eligibles[methode]
    else:
        methode = "dernier"
        erreur, n_tests = None, 0
        avertissements.append(
            "Série trop courte pour un backtest : la méthode « dernier cours » est utilisée "
            "par défaut, sans intervalle."
        )

    prevision = _appliquer(methode, valeurs)
    if prevision is None:
        return CoursForecast(
            cle=cle, libelle=libelle, serie=tuple(points), methode=methode,
            prevision=None, confiance="faible", avertissements=tuple(avertissements),
        )

    if prevision < 0:
        avertissements.append(
            f"Prévision négative ({prevision:,.2f}) : ramenée à zéro (un cours ne peut pas être négatif)."
        )
        prevision = 0.0

    derive = None
    if len(valeurs) >= 2 and valeurs[-2] != 0:
        derive = (valeurs[-1] - valeurs[-2]) / abs(valeurs[-2])
        derive = derive * 100.0

    bande = erreur
    return CoursForecast(
        cle=cle,
        libelle=libelle,
        serie=tuple(points),
        methode=methode,
        prevision=prevision,
        borne_basse=(prevision - bande) if bande is not None else None,
        borne_haute=(prevision + bande) if bande is not None else None,
        confiance=_confiance(erreur, valeurs),
        derive_relative=derive,
        erreur_moyenne=erreur,
        avertissements=tuple(avertissements),
    )


def forecast_cours_set(
    series: Mapping[str, tuple[str, Sequence[PricePoint]]],
) -> CoursForecastSet:
    """Prévoit un ensemble de cours (clé -> (libellé, historique))."""
    previsions: list[CoursForecast] = []
    methodes: dict[str, str] = {}
    avertissements: list[str] = []
    for cle, (libelle, serie) in (series or {}).items():
        prevision = forecast_cours(cle, libelle, serie)
        previsions.append(prevision)
        methodes[cle] = prevision.methode
        avertissements.extend(f"{libelle} : {w}" for w in prevision.avertissements)
    return CoursForecastSet(
        cours=tuple(previsions),
        methode_par_cle=methodes,
        avertissements=tuple(avertissements),
    )


def trier_cours(points: Iterable[PricePoint]) -> list[PricePoint]:
    """Trie une série de cours dans l'ordre chronologique des périodes."""
    return sorted(points, key=lambda p: _cle_tri(p.periode))


def comparer_cours(
    serie: Sequence[PricePoint],
    prevision: float | None,
) -> float | None:
    """Écart en % entre le dernier cours observé et la prévision."""
    if not serie or prevision is None or not serie[-1].valeur:
        return None
    return (prevision - serie[-1].valeur) / abs(serie[-1].valeur) * 100.0


# --------------------------------------------------------------------------- lecture des études
#: Préfixes des cours publiés par le portail.
PREFIXES_COURS = ("action", "obligation")

#: Clés possibles pour la valeur d'un cours, du plus explicite au plus générique.
CLES_VALEUR_COURS = ("price", "cours", "value", "amount", "lastPrice", "prix", "montant")


def _valeur_cours(node: Any) -> float | None:
    """Valeur numérique d'un cours, qu'elle soit directe ou imbriquée."""
    direct = _num(node)
    if direct is not None:
        return direct
    if isinstance(node, Mapping):
        for cle in CLES_VALEUR_COURS:
            if cle in node:
                valeur = _num(node.get(cle))
                if valeur is not None:
                    return valeur
    return None


#: Études de portefeuille de titres connues -> famille attendue.
#: Les identifiants du portail sont opaques (« pebaifpuao ») : ils sont donc
#: explicités ici, la détection par nom ne servant que de repli.
ETUDES_TITRES: Mapping[str, tuple[str, ...]] = {
    "pebaifpuao": ("A",),
    "pebaifpuoo": ("O",),
}


def familles_de_l_etude(cle: str, libelle: str = "") -> tuple[str, ...]:
    """Famille de titres attendue pour une étude (« Portefeuille actions »…).

    L'identifiant d'étude n'est pas figé : on reconnaît l'étude par son
    identifiant connu, puis à son nom (« pebaifpuao », « Portefeuille
    actions »…) et on en déduit la famille.
    """
    for candidat in (str(cle or ""), str(libelle or "")):
        nom = candidat.casefold().replace("etude_", "")
        for ident, familles in ETUDES_TITRES.items():
            if ident in nom:
                return familles
    for candidat in (str(cle or ""), str(libelle or "")):
        libelle_cible = candidat.casefold()
        if "obligation" in libelle_cible:
            return ("O",)
        if "action" in libelle_cible:
            return ("A",)
    return ()


def _code_titre(cle: str, familles: Sequence[str]) -> str | None:
    """« actionA1 » -> « A1 », « A1 » -> « A1 » (si la famille le permet)."""
    libelle = str(cle or "").strip()
    for prefixe in PREFIXES_COURS:
        if libelle.casefold().startswith(prefixe) and len(libelle) > len(prefixe):
            libelle = libelle[len(prefixe):]
            break
    libelle = libelle.strip().upper()
    if len(libelle) >= 2 and libelle[0] in familles and libelle[1:].isdigit():
        return libelle
    return None


def _collecter_cours(
    node: Any,
    familles: Sequence[str],
    out: dict[str, float],
    profondeur: int = 0,
) -> None:
    """Parcourt un payload à toute profondeur pour en extraire les cours."""
    if profondeur > 4:
        return
    if isinstance(node, Mapping):
        for cle, valeur in node.items():
            code = _code_titre(cle, familles)
            if code:
                nombre = _valeur_cours(valeur)
                if nombre is not None:
                    out[code] = nombre
            else:
                _collecter_cours(valeur, familles, out, profondeur + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            if isinstance(item, Mapping):
                trouve = False
                for cle in ("name", "code", "label", "title", "id", "reference", "symbol"):
                    code = _code_titre(str(item.get(cle, "") or ""), familles)
                    if code:
                        nombre = _valeur_cours(item)
                        if nombre is not None:
                            out[code] = nombre
                        trouve = True
                        break
                if not trouve:
                    _collecter_cours(item, familles, out, profondeur + 1)
            else:
                _collecter_cours(item, familles, out, profondeur + 1)


def extraire_cours_titres(payload: Any, familles: Sequence[str] = ("A", "O")) -> dict[str, float]:
    """Extrait les cours des titres (A1..A3, O1..O3) d'un payload d'étude.

    La structure n'étant pas garantie, la lecture est tolérante : plat,
    imbriqué, liste d'objets, clés préfixées (« actionA1 ») ou nues (« A1 »).
    Un payload non reconnu ne produit aucun cours — jamais de valeur inventée.
    """
    out: dict[str, float] = {}
    _collecter_cours(payload, tuple(familles), out)
    return out


def historique_cours(
    etudes_par_periode: Mapping[str, Mapping[str, Any]],
    study_ids: Sequence[str] | None = None,
    libelles: Mapping[str, str] | None = None,
) -> dict[str, list[PricePoint]]:
    """Construit l'historique de cours par titre à partir des études lues.

    `etudes_par_periode` : période -> {study_id (ou `etude_<id>`) -> payload}.
    `libelles` : study_id -> libellé lisible (« Portefeuille actions »), utile
    pour reconnaître une étude dont l'identifiant est opaque. Les études de
    portefeuille de titres sont retenues, les autres ignorées.
    """
    libelles = libelles or {}
    series: dict[str, list[PricePoint]] = {}
    for periode in sorted((etudes_par_periode or {}).keys(), key=_cle_tri):
        payloads = etudes_par_periode.get(periode) or {}
        for study_key, payload in payloads.items():
            if study_ids and not any(
                str(study_key) in (sid, f"etude_{sid}") for sid in study_ids
            ):
                continue
            ident = str(study_key).replace("etude_", "")
            familles = familles_de_l_etude(ident, libelles.get(ident, ""))
            if not familles:
                continue
            for nom, valeur in extraire_cours_titres(payload, familles).items():
                series.setdefault(nom, []).append(PricePoint(periode=periode, valeur=valeur))
    for nom in series:
        series[nom].sort(key=lambda p: _cle_tri(p.periode))
    return series
