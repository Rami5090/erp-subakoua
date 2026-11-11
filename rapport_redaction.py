"""Rédaction assistée du rapport professionnel : un paragraphe de contexte → les textes du rapport.

L'équipe décrit son année en un paragraphe. Le module rédige, avec l'API Claude
(Anthropic), un **brouillon** de chaque champ textuel du rapport — partie
collective et parties individuelles — à partir de ce contexte, des paramètres
saisis (entreprise, équipe, membres et rôles, outils retenus) et des faits
chiffrés de l'audit (`rapport_engine.AuditAnnuel`).

Garde-fous, dans la doctrine du rapport (aucune valeur inventée, chaque chiffre
traçable) :

* le modèle ne reçoit que des chiffres déjà calculés par l'audit, formatés comme
  dans le Word, avec pour consigne de les recopier tels quels et de n'en
  calculer aucun autre ;
* une information absente des sources n'est pas inventée : le texte porte un
  marqueur « [À COMPLÉTER : …] », surligné dans le Word ;
* chaque nombre des textes produits est confronté aux sources envoyées
  (`chiffres_non_verifies`) : un chiffre introuvable est signalé à l'équipe ;
* le résultat remplit les champs de la page, que l'équipe relit et corrige
  avant de générer le Word ; par défaut, un champ déjà rédigé n'est pas
  remplacé — il est transmis au modèle pour que le reste reste cohérent.

Deux modes, mêmes consignes et mêmes garde-fous :

* **automatique** (`rediger`) : appel à l'API Claude, qui demande une clé API et
  des crédits ; le format JSON est imposé par un schéma ;
* **manuel** (`texte_manuel` puis `lire_reponse_manuelle`) : sans clé API, l'équipe
  colle la demande dans une conversation claude.ai (offre gratuite ou abonnement),
  puis colle la réponse dans la page ; la lecture tolère le texte autour du JSON.

Tout est pur (testable sans réseau) sauf `rediger`, dont le client est injectable.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import rapport_engine as R

MODELE_PAR_DEFAUT = "claude-opus-5-5"
EFFORT_PAR_DEFAUT = "medium"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
#: Modèles pour lesquels un refus des garde-fous est rejoué sur le modèle de repli
#: recommandé par Anthropic (`fallbacks="default"`, en-tête bêta ci-dessous).
MODELES_AVEC_REPLI = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
                                "claude-fable-5-2", "claude-fable-5-1"})
BETA_REPLI = "server-side-fallback-2026-07-01"
#: Plafond de sortie (réflexion comprise) ; la requête est diffusée en flux, sans risque de délai.
MAX_TOKENS = 64_000
#: Longueur visée (en mots) des champs individuels, sans repère dans la page.
MOTS_INDIVIDUELS = {"outils": 70, "forces": 70, "faiblesses": 70, "reussites": 80, "erreurs": 80,
                    "mission_specifique": 70, "bilan": 70}

SYSTEME = """Tu aides une équipe d'étudiants à rédiger son rapport professionnel de fin d'année. L'équipe a dirigé une entreprise industrielle dans une simulation de gestion : chaque mois, plusieurs entreprises concurrentes y décident de leurs prix, de leur publicité, de leur production, de leurs effectifs et de leur financement sur un même marché. Le rapport est un audit stratégique et opérationnel de l'année, avec une partie collective et une partie individuelle par membre.

Tu rédiges un brouillon des parties textuelles. L'équipe le relira, le corrigera et se l'appropriera avant de le rendre : ton texte doit être exact, sobre et facile à reprendre.

Tes seules sources sont celles du message :
- le contexte rédigé par l'équipe (<contexte_equipe>) : sa stratégie, son organisation et les faits marquants, vus de l'intérieur ;
- les paramètres du rapport (<parametres_du_rapport>) : l'entreprise, les membres, leurs rôles, leurs missions et leurs outils ;
- les faits de l'audit (<faits_de_l_audit>), calculés par l'ERP de l'équipe à partir des données du jeu ;
- le cas échéant, les textes déjà rédigés par l'équipe (<textes_deja_rediges>) : tes textes doivent rester cohérents avec eux sans les répéter.

Règles de rédaction :
1. Chiffres : n'écris que des nombres présents dans ces sources, recopiés exactement comme ils y sont écrits (mêmes décimales, même unité). Ne calcule ni écart, ni ratio, ni moyenne : le rapport est évalué et chaque chiffre doit pouvoir être retrouvé dans l'audit.
2. Faits : n'invente aucune décision, aucun événement, aucune anecdote, aucune qualité ni difficulté personnelle. Quand un champ demande une information que les sources ne donnent pas (une situation concrète, une erreur précise, un ressenti), écris à sa place un marqueur « [À COMPLÉTER : ce que l'équipe doit préciser] » et poursuis la rédaction autour.
3. Interprétations : la « lecture_strategique » et les pistes d'analyse des constats sont des hypothèses de l'ERP. Reprends-les seulement si le contexte de l'équipe va dans le même sens, et présente-les comme des interprétations.
4. Voix : la partie collective parle au nom de l'équipe (« nous ») ; chaque partie individuelle est écrite à la première personne (« je ») par le membre concerné, à partir de son rôle, de ses missions, de ses outils et des indicateurs de son périmètre.
5. Consignes et longueur : chaque champ a une consigne et une longueur visée en mots ; respecte la consigne et reste proche de cette longueur. Chaque champ est lu dans sa propre section du rapport, entourée des tableaux et des graphiques de l'ERP : explique et argumente plutôt que d'aligner des chiffres, et ne répète pas ce qu'un autre champ dit déjà.
6. Forme : français professionnel, phrases claires, sans formules creuses. Texte brut : un paragraphe par ligne, « - » en début de ligne pour une puce, **gras** pour quelques mots clés au plus. Pas de titre ni d'autre balisage.

Rends l'objet JSON demandé, avec un texte par champ."""


class ErreurRedaction(Exception):
    """Échec de la rédaction assistée ; le message s'adresse à l'utilisateur."""


# --------------------------------------------------------------------------- configuration
@dataclass(frozen=True)
class Configuration:
    api_key: str | None = None
    modele: str = MODELE_PAR_DEFAUT
    effort: str = EFFORT_PAR_DEFAUT
    source_cle: str = "aucune"   # « secrets », « environnement » ou « aucune »


def lire_configuration(secrets: Any = None, environ: Mapping[str, str] | None = None) -> Configuration:
    """Section ``[anthropic]`` des secrets Streamlit (``api_key``, ``model``, ``effort``), sinon
    variables ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_MODEL`` / ``ANTHROPIC_EFFORT``.

    Sans clé, le SDK essaiera ses autres sources d'identifiants (profil ``ant auth login``…).
    """
    environ = os.environ if environ is None else environ
    section: dict[str, Any] = {}
    try:
        if secrets is not None and "anthropic" in secrets:
            section = dict(secrets["anthropic"])
    except Exception:  # pas de fichier de secrets : configuration par l'environnement
        section = {}
    cle = str(section.get("api_key") or "").strip()
    source = "secrets" if cle else "aucune"
    if not cle:
        cle = str(environ.get("ANTHROPIC_API_KEY") or "").strip()
        source = "environnement" if cle else "aucune"
    modele = str(section.get("model") or environ.get("ANTHROPIC_MODEL") or MODELE_PAR_DEFAUT).strip()
    effort = str(section.get("effort") or environ.get("ANTHROPIC_EFFORT") or EFFORT_PAR_DEFAUT).strip().lower()
    return Configuration(cle or None, modele or MODELE_PAR_DEFAUT,
                         effort if effort in EFFORTS else EFFORT_PAR_DEFAUT, source)


def sdk_disponible() -> bool:
    try:
        import anthropic  # noqa: F401
    except Exception:
        return False
    return True


# --------------------------------------------------------------------------- demande
@dataclass(frozen=True)
class Champ:
    cle: str        # nom du champ (clé JSON et suffixe de la clé de session)
    libelle: str
    consigne: str
    mots: int


@dataclass(frozen=True)
class MembreDemande:
    cle_json: str       # « membre_1 », « membre_2 »… dans la réponse
    cle_session: str    # clé du membre dans la page (rp_m_<cle>_<champ>)
    nom: str
    role: str
    champs: tuple[Champ, ...]


@dataclass(frozen=True)
class Demande:
    systeme: str
    message: str
    schema: dict[str, Any]
    collectif: tuple[Champ, ...]
    membres: tuple[MembreDemande, ...]

    @property
    def nb_champs(self) -> int:
        return len(self.collectif) + sum(len(m.champs) for m in self.membres)


def _sans_vide(valeurs: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in valeurs.items() if v not in (None, "", "—", [], {}, ())}


def _avec_mois(valeur_mois: tuple[str, float] | None, fmt) -> str | None:
    if not valeur_mois:
        return None
    periode, valeur = valeur_mois
    return f"{fmt(valeur)} en {R.mois_long(periode)}"


def _debut_fin(debut: float | None, fin: float | None, fmt) -> str | None:
    if debut is None and fin is None:
        return None
    return f"{fmt(debut)} en début de période, {fmt(fin)} en fin de période"


def _parc(parc: Mapping[str, float]) -> str | None:
    return ", ".join(f"{a} : {R.fmt_nombre(n)}" for a, n in parc.items()) if parc else None


def _pct2(valeur: Any) -> str:
    return R.fmt_pct(valeur, 2)


def faits_audit(audit: R.AuditAnnuel) -> dict[str, Any]:
    """Faits chiffrés de l'audit, formatés comme dans le Word (seuls chiffres autorisés)."""
    s = audit.synthese
    synthese = _sans_vide({
        "periode_auditee": f"de {R.mois_long(audit.debut)} à {R.mois_long(audit.fin)}, soit {s.n_mois} mois",
        "chiffre_affaires_total": (f"{R.fmt_euros(s.ca_total)} (somme de {s.ca_mois} mois renseignés)"
                                   if s.ca_total is not None else None),
        "resultat_net_total": (f"{R.fmt_euros(s.resultat_total)} (somme de {s.resultat_mois} mois renseignés)"
                               if s.resultat_total is not None else None),
        "marge_nette": R.fmt_pct(s.marge_nette) if s.marge_nette is not None else None,
        "evolution_du_ca_mensuel": (f"{R.fmt_pct(s.croissance_ca, 1, signe=True)} du premier au dernier mois renseigné"
                                    if s.croissance_ca is not None else None),
        "meilleur_mois_de_ca": _avec_mois(s.meilleur_mois, R.fmt_euros),
        "moins_bon_mois_de_ca": _avec_mois(s.pire_mois, R.fmt_euros),
        "mois_deficitaires": [R.mois_long(p) for p in s.mois_deficitaires],
        "tresorerie_d_ouverture": _avec_mois(s.tresorerie_ouverture, R.fmt_euros),
        "tresorerie_de_cloture": _avec_mois(s.tresorerie_cloture, R.fmt_euros),
        "tresorerie_la_plus_basse": _avec_mois(s.tresorerie_min, R.fmt_euros),
        "capitaux_propres": _debut_fin(s.capitaux_propres_debut, s.capitaux_propres_fin, R.fmt_euros),
        "dette_bancaire": _debut_fin(s.dette_debut, s.dette_fin, R.fmt_euros),
        "part_de_marche_debut": _avec_mois(s.pdm_debut, _pct2),
        "part_de_marche_fin": _avec_mois(s.pdm_fin, _pct2),
        "part_de_marche_maximale": _avec_mois(s.pdm_max, _pct2),
        "unites_vendues": (f"{R.fmt_nombre(s.ventes_totales)} sur {s.ventes_mois} mois renseignés"
                           if s.ventes_totales is not None else None),
        "publicite_totale": R.fmt_euros(s.pub_totale) if s.pub_totale is not None else None,
        "publicite_en_pourcentage_du_ca": R.fmt_pct(s.ratio_pub_ca) if s.ratio_pub_ca is not None else None,
        "effectifs": _debut_fin(s.effectifs_debut, s.effectifs_fin, R.fmt_nombre),
        "machines_debut": _parc(s.machines_debut),
        "machines_fin": _parc(s.machines_fin),
        "machines_achetees": _parc(s.acquisitions_total),
        "machines_cedees": _parc(s.cessions_total),
        "utilisation_des_ateliers": [
            f"{a} : {R.fmt_pct(moy * 100, 0)} en moyenne"
            + (f", au plus {R.fmt_pct(s.utilisation_max[a][1] * 100, 0)} en {R.mois_long(s.utilisation_max[a][0])}"
               if a in s.utilisation_max else "")
            for a, moy in s.utilisation_moyenne.items()
        ],
    })
    produits = [_sans_vide({
        "produit": l.produit,
        "unites_vendues": R.fmt_nombre(l.ventes) if l.ventes else None,
        "part_des_volumes": R.fmt_pct(l.part_volumes) if l.part_volumes is not None else None,
        "part_du_ca": R.fmt_pct(l.part_ca) if l.part_ca is not None else None,
        "prix_debut": R.fmt_euros(l.prix_debut) if l.prix_debut is not None else None,
        "prix_fin": R.fmt_euros(l.prix_fin) if l.prix_fin is not None else None,
        "prix_du_marche": R.fmt_euros(l.prix_marche) if l.prix_marche is not None else None,
        "indice_de_prix": R.fmt_nombre(l.indice_prix * 100, 0) if l.indice_prix is not None else None,
        "marge_unitaire": R.fmt_euros(l.marge_unitaire) if l.marge_unitaire is not None else None,
        "taux_de_marge": R.fmt_pct(l.taux_marge) if l.taux_marge is not None else None,
        "evolution_des_ventes": (R.fmt_pct(l.evolution_ventes, 1, signe=True)
                                 if l.evolution_ventes is not None else None),
    }) for l in audit.produits if l.ventes or l.prix_fin is not None]
    d = audit.decomposition
    decomposition = _sans_vide({
        "comparaison": f"{R.mois_long(d.periode_a)} → {R.mois_long(d.periode_b)}",
        "ca_theorique_depart": R.fmt_euros(d.ca_a),
        "ca_theorique_arrivee": R.fmt_euros(d.ca_b),
        "ecart": R.fmt_euros_signe(d.ecart),
        "effet_volume": R.fmt_euros_signe(d.effet_volume),
        "effet_mix": R.fmt_euros_signe(d.effet_mix),
        "effet_prix": R.fmt_euros_signe(d.effet_prix),
    }) if d is not None else {}
    concurrence = []
    for r in audit.concurrence:
        numero = R._float(r.get("entreprise"))
        if numero is None:
            continue
        concurrence.append(_sans_vide({
            "entreprise": f"Entreprise {int(numero)}" + (" (nous)" if numero == float(audit.numero) else ""),
            "part_de_marche": _pct2(r.get("part_marche")) if R._float(r.get("part_marche")) is not None else None,
            "ventes": R.fmt_nombre(r.get("ventes")) if R._float(r.get("ventes")) is not None else None,
            "prix": R.fmt_euros(r.get("prix")) if R._float(r.get("prix")) is not None else None,
        }))
    return _sans_vide({
        "synthese": synthese,
        "produits": produits,
        "decomposition_prix_volume_mix_du_ca": decomposition,
        "benchmark_concurrentiel": ({"periode": R.mois_long(audit.concurrence_periode or audit.fin),
                                     "entreprises": concurrence} if concurrence else {}),
        "chronologie_des_decisions": [f"{R.mois_long(e.periode)} — {e.domaine} : {e.texte}"
                                      for e in audit.chronologie],
        "constats": [_sans_vide({"theme": k.theme, "nature": k.nature, "constat": k.texte,
                                 "piste_d_analyse": k.analyse}) for k in audit.constats],
        "lecture_strategique": list(audit.lecture_strategique),
        "donnees_manquantes": [{"donnee": m.element, "mois": [R.mois_long(p) for p in m.periodes],
                                "consequence": m.consequence} for m in audit.manques],
    })


def _indicateurs_perimetre(audit: R.AuditAnnuel, role: str) -> dict[str, str]:
    """Indicateurs du périmètre d'un rôle, début → fin (comme le tableau « Mon périmètre en chiffres »)."""
    out: dict[str, str] = {}
    for cle in R.perimetre_role(role):
        serie = audit.serie(cle)
        if serie:
            (p0, v0), (p1, v1) = serie[0], serie[-1]
            out[R.INDICATEURS[cle][0]] = (f"{R.fmt_valeur(cle, v0)} en {R.mois_long(p0)} → "
                                          f"{R.fmt_valeur(cle, v1)} en {R.mois_long(p1)}")
    return out


def _schema(collectif: Sequence[Champ], membres: Sequence[MembreDemande]) -> dict[str, Any]:
    def objet(proprietes: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "properties": proprietes, "required": list(proprietes),
                "additionalProperties": False}

    proprietes: dict[str, Any] = {}
    if collectif:
        proprietes["collectif"] = objet({c.cle: {"type": "string"} for c in collectif})
    if membres:
        proprietes["membres"] = objet({m.cle_json: objet({c.cle: {"type": "string"} for c in m.champs})
                                       for m in membres})
    return objet(proprietes)


def _bloc(balise: str, contenu: Any) -> str:
    texte = contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False, indent=1)
    return f"<{balise}>\n{texte}\n</{balise}>"


def _ligne_champ(c: Champ) -> str:
    return f"  - {c.cle} — « {c.libelle} » — environ {c.mots} mots. Consigne : {c.consigne}"


def preparer_demande(
    audit: R.AuditAnnuel,
    contenu: R.ContenuRapport,
    cles_membres: Sequence[str],
    *,
    collectif: bool = True,
    individuel: bool = True,
    remplacer: bool = False,
) -> Demande:
    """Requête de rédaction : champs à rédiger, sources et schéma de la réponse.

    ``cles_membres`` donne, dans l'ordre de ``contenu.membres``, la clé de chaque membre
    dans la page. Sans ``remplacer``, seuls les champs vides sont demandés ; les textes
    existants (et ceux d'une partie non sélectionnée) sont transmis comme contexte.
    """
    def a_rediger(texte: str) -> bool:
        return remplacer or not str(texte or "").strip()

    champs_collectifs = tuple(
        Champ(champ, libelle, R.CONSIGNES[champ], mots)
        for champ, libelle, mots in R.CHAMPS_COLLECTIFS
        if collectif and a_rediger(getattr(contenu, champ))
    )
    membres: list[MembreDemande] = []
    demandes_par_rang: dict[int, set[str]] = {}
    if individuel:
        for rang, (m, cle) in enumerate(zip(contenu.membres, cles_membres), start=1):
            champs = tuple(
                Champ(champ, libelle, R.CONSIGNES[consigne], MOTS_INDIVIDUELS.get(champ, 70))
                for champ, libelle, consigne in R.CHAMPS_INDIVIDUELS
                if a_rediger(getattr(m, champ))
            )
            if champs:
                membres.append(MembreDemande(f"membre_{rang}", cle, m.nom, m.role, champs))
                demandes_par_rang[rang] = {c.cle for c in champs}

    # Textes existants qui ne seront pas réécrits : transmis pour que le brouillon reste cohérent.
    demandes_collectives = {c.cle for c in champs_collectifs}
    deja = _sans_vide({
        "collectif": _sans_vide({champ: getattr(contenu, champ).strip() for champ, _, _ in R.CHAMPS_COLLECTIFS
                                 if champ not in demandes_collectives}),
        "membres": _sans_vide({f"membre_{rang}" + (f" ({m.nom})" if m.nom else ""): _sans_vide({
            champ: getattr(m, champ).strip() for champ, _, _ in R.CHAMPS_INDIVIDUELS
            if champ not in demandes_par_rang.get(rang, set())
        }) for rang, m in enumerate(contenu.membres, start=1)}),
    })

    retenus = set(contenu.outils_erp)
    parametres = _sans_vide({
        "entreprise": contenu.entreprise or audit.entreprise,
        "numero_de_l_entreprise_dans_le_jeu": audit.numero,
        "equipe": contenu.equipe,
        "formation": contenu.formation,
        "etablissement": contenu.etablissement,
        "periode_auditee": f"de {R.mois_long(audit.debut)} à {R.mois_long(audit.fin)}",
        "membres": [_sans_vide({
            "cle": f"membre_{i}", "nom": m.nom, "role": m.role, "missions": m.missions,
            "outils_de_l_erp_montes_ou_pilotes": list(m.outils_erp),
            "indicateurs_du_perimetre": _indicateurs_perimetre(audit, m.role),
        }) for i, m in enumerate(contenu.membres, start=1)],
        "outils_de_l_erp_presentes": [{"nom": o.nom, "besoin": o.besoin, "methode": o.methode, "apport": o.apport}
                                      for o in R.OUTILS_ERP if o.nom in retenus],
        "outils_de_gestion_de_projet": [{"nom": nom, "usage": R.OUTILS_GP[nom]}
                                        for nom in contenu.outils_gp if nom in R.OUTILS_GP],
    })

    consignes: list[str] = []
    if champs_collectifs:
        consignes.append("Partie collective (« nous ») — objet « collectif » :")
        consignes.extend(_ligne_champ(c) for c in champs_collectifs)
    if membres:
        consignes.append("Parties individuelles (« je ») — objet « membres » :")
        for m in membres:
            consignes.append(f"- {m.cle_json} : {m.nom or 'membre sans nom'}" + (f" ({m.role})" if m.role else ""))
            consignes.extend(_ligne_champ(c) for c in m.champs)

    blocs = [
        _bloc("contexte_equipe", contenu.contexte.strip() or "(aucun contexte fourni)"),
        _bloc("parametres_du_rapport", parametres),
        _bloc("faits_de_l_audit", faits_audit(audit)),
    ]
    if deja:
        blocs.append(_bloc("textes_deja_rediges", deja))
    blocs.append(_bloc("champs_a_rediger", "\n".join(consignes)))
    message = "\n\n".join(blocs) + "\n\nRédige chacun de ces champs en suivant les règles."
    return Demande(SYSTEME, message, _schema(champs_collectifs, membres), champs_collectifs, tuple(membres))


# --------------------------------------------------------------------------- réponse
@dataclass(frozen=True)
class Redaction:
    collectif: dict[str, str]
    membres: dict[str, dict[str, str]]      # clé de session du membre -> champ -> texte
    modele: str = ""
    jetons_entree: int | None = None
    jetons_sortie: int | None = None
    notes: tuple[str, ...] = ()             # remarques de lecture (mode manuel), affichées à l'équipe

    def textes(self) -> list[str]:
        return [*self.collectif.values(), *(t for champs in self.membres.values() for t in champs.values())]


def _nettoyer(valeur: Any) -> str:
    if not isinstance(valeur, str):
        return ""
    texte = valeur.replace("\r\n", "\n").strip()
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    return re.sub(r"\[\s*à\s+compléter", "[À COMPLÉTER", texte, flags=re.IGNORECASE)


def _nom_normalise(nom: Any) -> str:
    brut = unicodedata.normalize("NFKD", str(nom or "")).encode("ascii", "ignore").decode()
    return " ".join(brut.casefold().split())


def _charger(texte: str, conseil: str) -> Mapping[str, Any]:
    try:
        # strict=False : un retour à la ligne brut dans un texte (réponse collée) reste lisible.
        brut = json.loads(texte, strict=False)
    except (TypeError, ValueError) as exc:
        raise ErreurRedaction(f"Réponse illisible : l'objet JSON attendu est invalide ({exc}). {conseil}") from exc
    if not isinstance(brut, Mapping):
        raise ErreurRedaction(f"Réponse illisible : un objet JSON était attendu. {conseil}")
    return brut


def _lire(brut: Mapping[str, Any], demande: Demande, par_nom: bool) -> Redaction:
    """Textes des seuls champs demandés. Avec ``par_nom``, un membre est reconnu à son « nom »
    (réponse collée : l'équipe a pu changer d'ordre entre la copie et le collage)."""
    bloc = brut.get("collectif") if isinstance(brut.get("collectif"), Mapping) else {}
    collectif = {c.cle: t for c in demande.collectif if (t := _nettoyer(bloc.get(c.cle)))}
    bloc_membres = brut.get("membres") if isinstance(brut.get("membres"), Mapping) else {}
    par_cle = {m.cle_json: m for m in demande.membres}
    par_nom_normalise = {_nom_normalise(m.nom): m for m in demande.membres if m.nom}
    membres: dict[str, dict[str, str]] = {}
    notes: list[str] = []
    for cle, champs in bloc_membres.items():
        if not isinstance(champs, Mapping):
            continue
        nom = champs.get("nom")
        if par_nom and isinstance(nom, str) and nom.strip():
            cible = par_nom_normalise.get(_nom_normalise(nom))
            if cible is None:
                notes.append(f"Textes de « {nom.strip()} » ignorés : membre absent de l'équipe ou champs déjà rédigés.")
                continue
        else:
            cible = par_cle.get(str(cle))
        if cible is None:
            continue
        textes = {c.cle: t for c in cible.champs if (t := _nettoyer(champs.get(c.cle)))}
        if textes:
            membres.setdefault(cible.cle_session, {}).update(textes)
    return Redaction(collectif, membres, notes=tuple(notes))


def analyser_reponse(texte: str, demande: Demande) -> Redaction:
    """Textes de la réponse JSON de l'API, pour les seuls champs demandés."""
    return _lire(_charger(texte, "Relancez la rédaction."), demande, par_nom=False)


# --------------------------------------------------------------------------- mode manuel (sans clé API)
def gabarit_reponse(demande: Demande) -> dict[str, Any]:
    """Forme exacte de la réponse attendue, avec le nom de chaque membre à recopier."""
    gabarit: dict[str, Any] = {}
    if demande.collectif:
        gabarit["collectif"] = {c.cle: "…" for c in demande.collectif}
    if demande.membres:
        gabarit["membres"] = {m.cle_json: {"nom": m.nom, **{c.cle: "…" for c in m.champs}}
                              for m in demande.membres}
    return gabarit


def texte_manuel(demande: Demande) -> str:
    """Demande complète à coller dans une conversation claude.ai (mode manuel, sans clé API).

    Mêmes consignes et mêmes sources que l'appel à l'API ; le format JSON, que l'API impose
    par un schéma, est ici décrit par un gabarit à remplir.
    """
    gabarit = json.dumps(gabarit_reponse(demande), ensure_ascii=False, indent=2)
    return (
        f"{demande.systeme}\n\n---\n\n{demande.message}\n\n"
        "Format de la réponse : réponds uniquement par un objet JSON valide, sans texte avant ni après, "
        "avec exactement les clés ci-dessous. Remplace chaque « … » par le texte du champ, recopie tel quel "
        "le « nom » de chaque membre, et sépare les paragraphes d'un texte par \\n.\n"
        f"{gabarit}"
    )


_BLOC_DE_CODE = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.DOTALL)


def extraire_json(texte: str) -> str:
    """Objet JSON d'une réponse collée : bloc de code ```json, ou du premier « { » au dernier « } »."""
    texte = str(texte or "").strip()
    for bloc in _BLOC_DE_CODE.findall(texte):
        if "{" in bloc:
            texte = bloc
            break
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut < 0 or fin <= debut:
        raise ErreurRedaction("Aucun objet JSON dans la réponse collée : demandez à Claude de répondre "
                              "uniquement par l'objet JSON demandé, puis copiez toute sa réponse.")
    return texte[debut:fin + 1]


def lire_reponse_manuelle(texte: str, demande: Demande) -> Redaction:
    """Réponse de claude.ai collée par l'équipe → textes des champs demandés."""
    if not str(texte or "").strip():
        raise ErreurRedaction("Collez d'abord la réponse de Claude.")
    if demande.nb_champs == 0:
        raise ErreurRedaction("Rien à intégrer : les champs sélectionnés sont déjà rédigés.")
    brut = _charger(extraire_json(texte), "Vérifiez que toute la réponse a été copiée.")
    redaction = _lire(brut, demande, par_nom=True)
    if not redaction.textes():
        raise ErreurRedaction("La réponse collée ne contient aucun des champs demandés : copiez la demande "
                              "affichée (étape ①) dans une nouvelle conversation, puis collez la nouvelle réponse.")
    return replace(redaction, modele="claude.ai (mode manuel)")


# --------------------------------------------------------------------------- vérifications
_NOMBRE = re.compile(
    r"(?<![\w,.])"
    r"([+\-−]?\d{1,3}(?:[   ]\d{3})+(?!\d)|[+\-−]?\d+)"   # entier (groupé ou non)
    r"(?:[,.](\d+))?"                                                       # décimales
    r"(?:[   ]?(%|k€|M€|€))?"                                     # unité
)


def _nombres(texte: str) -> list[tuple[str, float, str]]:
    """(écriture, valeur absolue, unité) de chaque nombre d'un texte."""
    out = []
    for m in _NOMBRE.finditer(str(texte or "")):
        entier, decimales, unite = m.group(1), m.group(2), m.group(3) or ""
        chiffres = re.sub(r"\D", "", entier)
        if not chiffres:
            continue
        valeur = float(chiffres + ("." + decimales if decimales else ""))
        out.append((m.group(0).strip(), round(valeur, 4), unite))
    return out


def chiffres_non_verifies(redaction: Redaction, demande: Demande) -> dict[str, list[str]]:
    """Nombres des textes produits introuvables dans les sources envoyées, par champ.

    Sont vérifiés les montants et pourcentages, et tout nombre d'au moins 100 ; les petits
    nombres sans unité (mois, machines, membres) et les marqueurs « [À COMPLÉTER] » sont
    ignorés. Un nombre signalé n'est pas forcément faux : il est à vérifier par l'équipe.
    """
    references = {valeur for _, valeur, _ in _nombres(demande.message)}

    def inconnus(texte: str) -> list[str]:
        out: list[str] = []
        for brut, valeur, unite in _nombres(R.A_COMPLETER.sub(" ", texte)):
            if (unite or valeur >= 100) and valeur not in references and brut not in out:
                out.append(brut)
        return out

    resultat: dict[str, list[str]] = {}
    for c in demande.collectif:
        if (liste := inconnus(redaction.collectif.get(c.cle, ""))):
            resultat[c.libelle] = liste
    for m in demande.membres:
        for c in m.champs:
            if (liste := inconnus(redaction.membres.get(m.cle_session, {}).get(c.cle, ""))):
                resultat[f"{m.nom or 'Membre'} — {c.libelle}"] = liste
    return resultat


def passages_a_completer(redaction: Redaction) -> int:
    return sum(len(R.A_COMPLETER.findall(t)) for t in redaction.textes())


def mises_a_jour(redaction: Redaction, etat: Mapping[str, Any], remplacer: bool = False) -> dict[str, str]:
    """Valeurs à écrire dans la session de la page (clés ``rp_<champ>`` et ``rp_m_<membre>_<champ>``).

    Un champ déjà rédigé n'est jamais écrasé, sauf si ``remplacer``.
    """
    out: dict[str, str] = {}

    def poser(cle: str, texte: str) -> None:
        if remplacer or not str(etat.get(cle) or "").strip():
            out[cle] = texte

    for champ, texte in redaction.collectif.items():
        poser(f"rp_{champ}", texte)
    for cle_membre, textes in redaction.membres.items():
        for champ, texte in textes.items():
            poser(f"rp_m_{cle_membre}_{champ}", texte)
    return out


# --------------------------------------------------------------------------- appel à l'API
def parametres_requete(demande: Demande, config: Configuration) -> dict[str, Any]:
    """Paramètres de ``client.beta.messages.stream`` : sortie JSON imposée par le schéma, effort
    explicite, invite système mise en cache et, pour les modèles concernés, repli serveur
    sur un autre modèle si les garde-fous déclinent la demande."""
    params: dict[str, Any] = {
        "model": config.modele,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": demande.systeme, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": demande.message}],
        "output_config": {"effort": config.effort,
                          "format": {"type": "json_schema", "schema": demande.schema}},
    }
    if config.modele in MODELES_AVEC_REPLI:
        params["betas"] = [BETA_REPLI]
        params["fallbacks"] = "default"
    return params


def creer_client(config: Configuration) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise ErreurRedaction("Rédaction assistée indisponible : installez le paquet « anthropic » "
                              "(pip install -r requirements.txt).") from exc
    return anthropic.Anthropic(api_key=config.api_key) if config.api_key else anthropic.Anthropic()


def _message_erreur(exc: Exception, config: Configuration) -> str:
    configurer = ("renseignez `api_key` dans la section [anthropic] des secrets Streamlit "
                  "(ou la variable d'environnement ANTHROPIC_API_KEY)")
    if isinstance(exc, TypeError) and "authentication" in str(exc).lower():
        return f"Aucune clé API Anthropic n'est configurée : {configurer}."
    try:
        import anthropic
    except ImportError:
        return f"Rédaction impossible : {type(exc).__name__}: {exc}"
    if isinstance(exc, anthropic.AuthenticationError):
        return f"Clé API Anthropic refusée : {configurer}."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"La clé API n'a pas accès au modèle « {config.modele} »."
    if isinstance(exc, anthropic.NotFoundError):
        return f"Modèle « {config.modele} » introuvable : vérifiez `model` dans la section [anthropic] des secrets."
    if isinstance(exc, anthropic.RateLimitError):
        return "Limite de débit de l'API Claude atteinte : réessayez dans une minute."
    if isinstance(exc, anthropic.BadRequestError):
        return f"Requête refusée par l'API Claude : {getattr(exc, 'message', exc)}"
    if isinstance(exc, anthropic.APIStatusError):
        return (f"Le service de rédaction est indisponible (erreur {getattr(exc, 'status_code', '?')}) : "
                "réessayez plus tard.")
    if isinstance(exc, anthropic.APITimeoutError):
        return "L'API Claude n'a pas répondu à temps : réessayez, ou rédigez moins de champs à la fois."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Connexion à l'API Claude impossible : vérifiez l'accès réseau du serveur."
    return f"Rédaction impossible : {type(exc).__name__}: {exc}"


def rediger(demande: Demande, config: Configuration, client: Any = None) -> Redaction:
    """Rédige les champs de ``demande`` ; lève ``ErreurRedaction`` avec un message lisible."""
    if demande.nb_champs == 0:
        raise ErreurRedaction("Aucun champ à rédiger : ils sont tous déjà remplis.")
    try:
        client = client if client is not None else creer_client(config)
        with client.beta.messages.stream(**parametres_requete(demande, config)) as flux:
            message = flux.get_final_message()
    except ErreurRedaction:
        raise
    except Exception as exc:
        raise ErreurRedaction(_message_erreur(exc, config)) from exc

    arret = getattr(message, "stop_reason", None)
    if arret == "refusal":
        categorie = getattr(getattr(message, "stop_details", None), "category", None)
        raise ErreurRedaction("La demande a été déclinée par les garde-fous du modèle"
                              + (f" (catégorie « {categorie} »)" if categorie else "")
                              + " : reformulez le contexte, puis relancez.")
    if arret == "max_tokens":
        raise ErreurRedaction("Réponse tronquée (longueur maximale atteinte) : rédigez la partie collective "
                              "et les parties individuelles séparément.")
    textes = [b.text for b in getattr(message, "content", None) or [] if getattr(b, "type", None) == "text"]
    if not textes:
        raise ErreurRedaction("Réponse vide : relancez la rédaction.")
    try:
        redaction = analyser_reponse("".join(textes), demande)
    except ErreurRedaction:
        if len(textes) < 2:
            raise
        # Repli en cours de réponse : seul le dernier bloc de texte porte la réponse complète.
        redaction = analyser_reponse(textes[-1], demande)
    usage = getattr(message, "usage", None)
    return replace(redaction, modele=str(getattr(message, "model", "") or config.modele),
                   jetons_entree=getattr(usage, "input_tokens", None),
                   jetons_sortie=getattr(usage, "output_tokens", None))
