"""Page 8 — Rapport professionnel : produire le livrable de l'évaluation Subakoua.

Lecture seule pour tous : aucune écriture en base. La page

1. consolide l'année auditée à partir des données de l'ERP (mêmes moteurs que
   le cockpit, chaque chiffre avec sa source) ;
2. recueille les textes de l'équipe — partie collective et une partie
   individuelle par membre — avec les consignes de l'évaluation ;
3. contrôle la complétude du rapport au regard des attendus ;
4. génère le rapport Word (graphiques et tableaux commentés, logo, charte
   couleurs), à finaliser puis exporter en PDF pour le dépôt.

Les textes saisis vivent dans la session : « Enregistrer le brouillon »
les sauvegarde dans un fichier JSON que l'équipe peut se partager et recharger.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import unicodedata
from dataclasses import replace
from datetime import date

import pandas as pd
import streamlit as st

from auth import is_editor, setup as auth_setup
from constants import PERIOD_INDEX, TOURS_PERIODES
from db_utils import get_read_engine
import depot_donnees as depot
import rapport_engine as R
import rapport_redaction as RR
from rapport_docx import DOCX_DISPONIBLE, compter_a_completer, generer_rapport_docx, rapport_markdown
from rapport_graphiques import CHARTE_DEFAUT, CHARTES, MATPLOTLIB_DISPONIBLE, Charte, contraste, graphiques_rapport

st.set_page_config(page_title="Rapport professionnel", layout="wide")
auth_setup()
st.title("📘 Rapport professionnel — audit de l'année")
st.caption(
    "Génère le livrable de l'évaluation : partie collective (stratégies, outils et méthodes, audit chiffré, "
    "prise de recul) et parties individuelles, avec graphiques et tableaux commentés. Aucune donnée n'est "
    "modifiée en base."
)

engine = get_read_engine()
if engine is None:
    st.error("Connexion Aiven indisponible.")
    st.stop()

COLONNES_MEMBRES = ["Nom", "Rôle", "Missions"]
NOMS_OUTILS = [o.nom for o in R.OUTILS_ERP]
CHAMPS_IDENTITE = ("titre", "sous_titre", "entreprise", "equipe", "formation", "etablissement", "enseignants", "date")
#: (champ, libellé, nombre de mots visé) — partagés avec la rédaction assistée.
CHAMPS_COLLECTIFS = R.CHAMPS_COLLECTIFS
#: (champ du membre, libellé, clé de consigne)
CHAMPS_INDIVIDUELS = R.CHAMPS_INDIVIDUELS
STATUTS = {"ok": "✅ fait", "partiel": "🔸 à compléter", "manquant": "⛔ manquant"}
NATURES = {"force": "💪 force", "faiblesse": "⚠️ faiblesse", "vigilance": "🔎 vigilance", "info": "ℹ️ constat"}


# --------------------------------------------------------------------------- utilitaires
def _texte(valeur) -> str:
    return valeur if isinstance(valeur, str) else ""


def _cellule(valeur) -> str:
    if valeur is None or (isinstance(valeur, float) and math.isnan(valeur)):
        return ""
    return str(valeur).strip()


def _slug(texte: str) -> str:
    brut = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", brut).strip("_")


def _cle_membre(nom: str, rang: int) -> str:
    return _slug(nom) or f"membre{rang + 1}"


# --------------------------------------------------------------------------- données (cache)
@st.cache_resource(show_spinner="Lecture des données de l'ERP…", ttl=300, max_entries=2)
def _charger_sources(_engine, cle: str):
    """Sources indexées, partagées entre les exécutions tant que la base ne change pas.

    La clé combine les empreintes de `erp_etudes` et `erp_donnees` (dates de
    synchronisation comprises) : un achat ou une resynchronisation invalide le
    cache. Les tables de saisie sont relues au plus tard toutes les 5 minutes.
    """
    brut = depot.charger_sources_rapport(_engine)
    sources = R.indexer_sources(brut["erp_donnees"], brut["erp_etudes"], brut["tables"])
    return sources, brut["erreurs"]


@st.cache_data(show_spinner=False, max_entries=8)
def _audit(cle: str, debut: str, fin: str, numero: int) -> R.AuditAnnuel:
    sources, _ = _charger_sources(engine, cle)
    return R.construire_audit(sources, debut, fin, numero)


@st.cache_data(show_spinner="Préparation des graphiques…", max_entries=8)
def _graphiques(cle: str, debut: str, fin: str, numero: int, couleurs: tuple[str, str, str]):
    return graphiques_rapport(_audit(cle, debut, fin, numero), Charte("Charte du rapport", *couleurs))


cle_donnees = hashlib.sha1(
    repr((depot.signature_etudes(engine), depot.signature_donnees(engine))).encode("utf-8")
).hexdigest()
sources, erreurs_lecture = _charger_sources(engine, cle_donnees)
for _source, _erreur in erreurs_lecture.items():
    if _source in ("erp_donnees", "erp_etudes"):
        st.warning(f"Lecture `{_source}` impossible ({_erreur}) : les données correspondantes manqueront au rapport.")


# --------------------------------------------------------------------------- brouillon
def _appliquer_brouillon() -> None:
    """Recharge un brouillon JSON (callback : s'exécute avant la création des widgets)."""
    fichier = st.session_state.get("rp_brouillon_fichier")
    if fichier is None:
        return
    try:
        contenu = R.ContenuRapport.from_json(fichier.getvalue())
    except Exception as exc:
        st.session_state["rp_message"] = ("error", f"Brouillon illisible ({type(exc).__name__}).")
        return
    for champ in CHAMPS_IDENTITE + ("contexte",) + tuple(c for c, _, _ in CHAMPS_COLLECTIFS):
        st.session_state[f"rp_{champ}"] = getattr(contenu, champ)
    st.session_state["rp_outils_erp"] = [o for o in contenu.outils_erp if o in NOMS_OUTILS]
    st.session_state["rp_outils_gp"] = [o for o in contenu.outils_gp if o in R.OUTILS_GP]
    st.session_state["rp_membres_base"] = pd.DataFrame(
        [{"Nom": m.nom, "Rôle": m.role, "Missions": m.missions} for m in contenu.membres], columns=COLONNES_MEMBRES)
    st.session_state.pop("rp_membres", None)
    for rang, m in enumerate(contenu.membres):
        cle = _cle_membre(m.nom, rang)
        for champ, _, _ in CHAMPS_INDIVIDUELS:
            st.session_state[f"rp_m_{cle}_{champ}"] = getattr(m, champ)
        st.session_state[f"rp_m_{cle}_outils_erp"] = [o for o in m.outils_erp if o in NOMS_OUTILS]
    st.session_state["rp_message"] = ("success", f"Brouillon chargé : {len(contenu.membres)} membre(s).")


with st.expander("💾 Brouillon partagé (reprendre un travail en cours)", expanded=False):
    st.caption("Les textes restent dans la session du navigateur. Enregistrez régulièrement le brouillon (bouton en "
               "bas de l'onglet « Générer ») ; chaque membre peut le recharger ici pour compléter sa partie.")
    st.file_uploader("Recharger un brouillon (.json)", type=["json"], key="rp_brouillon_fichier",
                     on_change=_appliquer_brouillon)
message = st.session_state.pop("rp_message", None)
if isinstance(message, tuple) and len(message) == 2:
    (st.success if message[0] == "success" else st.error)(message[1])


# --------------------------------------------------------------------------- période auditée
periodes = sorted(set(sources.periodes()) | set(TOURS_PERIODES.values()), key=lambda p: PERIOD_INDEX[p])
debut_defaut, fin_defaut = R.periode_par_defaut(sources)
c1, c2, c3, c4 = st.columns([1.3, 1.3, 0.9, 0.8])
debut = c1.selectbox("Début de la période auditée", periodes, index=periodes.index(debut_defaut), key="rp_debut")
fin = c2.selectbox("Fin de la période auditée", periodes, index=periodes.index(fin_defaut), key="rp_fin")
numero = int(c3.number_input("N° de l'entreprise", min_value=1, max_value=9, value=3, step=1, key="rp_numero") or 3)
with c4:
    st.write("")
    if st.button("🔄 Recharger les données", help="Relit la base (après une synchronisation ou une saisie)."):
        for _fonction in (_charger_sources, _audit, _graphiques):
            getattr(_fonction, "clear", lambda: None)()
        st.rerun()
if PERIOD_INDEX.get(debut, 0) > PERIOD_INDEX.get(fin, 0):
    debut, fin = fin, debut
    st.info("Début postérieur à la fin : les deux bornes ont été inversées.")

audit = _audit(cle_donnees, debut, fin, numero)
s = audit.synthese
if not audit.mois_renseignes:
    st.warning("Aucune donnée de l'ERP sur cette période : le rapport sera généré avec sa structure et ses consignes, "
               "sans chiffres. Synchronisez les études (page Scraper) ou saisissez les données réelles.")

tab_id, tab_equipe, tab_redaction, tab_coll, tab_indiv, tab_audit, tab_gen = st.tabs([
    "🏷️ Identité & charte", "👥 Équipe", "✨ Rédaction assistée", "🧭 Partie collective",
    "🙋 Parties individuelles", "📊 Aperçu de l'audit", "📄 Générer le rapport",
])

# --------------------------------------------------------------------------- identité & charte
# Valeurs par défaut posées AVANT la création des widgets : un brouillon rechargé
# écrit les mêmes clés, et Streamlit refuse qu'un widget reçoive à la fois une
# valeur par défaut et une valeur fixée par session_state.
for _cle, _defaut in {
    "rp_entreprise": f"Entreprise {numero}", "rp_titre": "Rapport professionnel",
    "rp_sous_titre": "Audit stratégique et opérationnel de l'année", "rp_formation": R.ContenuRapport().formation,
    "rp_date": date.today().strftime("%d/%m/%Y"), "rp_outils_erp": list(NOMS_OUTILS), "rp_outils_gp": [],
}.items():
    st.session_state.setdefault(_cle, _defaut)

saisies: dict[str, str] = {}
with tab_id:
    a, b = st.columns(2)
    saisies["entreprise"] = _texte(a.text_input("Nom de l'entreprise", key="rp_entreprise"))
    saisies["equipe"] = _texte(b.text_input("Nom du groupe / de l'équipe", key="rp_equipe"))
    saisies["titre"] = _texte(a.text_input("Titre du rapport", key="rp_titre"))
    saisies["sous_titre"] = _texte(b.text_input("Sous-titre", key="rp_sous_titre"))
    saisies["formation"] = _texte(a.text_input("Formation", key="rp_formation"))
    saisies["etablissement"] = _texte(b.text_input("Établissement", key="rp_etablissement"))
    saisies["enseignants"] = _texte(a.text_input("Enseignant(e)s destinataires", key="rp_enseignants"))
    saisies["date"] = _texte(b.text_input("Date du rapport", key="rp_date"))

    st.markdown("**Logo de l'entreprise**")
    fichier_logo = st.file_uploader("Logo (PNG ou JPG)", type=["png", "jpg", "jpeg"], key="rp_logo_fichier")
    logo: bytes | None = None
    if fichier_logo is not None:
        try:
            contenu_logo = fichier_logo.getvalue()
            logo = bytes(contenu_logo) if isinstance(contenu_logo, (bytes, bytearray)) else None
        except Exception:
            logo = None
    if logo:
        st.image(logo, width=160)
    else:
        st.caption("Sans logo, un emplacement « À compléter » est réservé sur la page de garde.")

    st.markdown("**Codes couleurs**")
    choix_charte = st.radio("Charte graphique", [*CHARTES, "Personnalisée"], horizontal=True, key="rp_charte")
    base = CHARTES.get(choix_charte, CHARTE_DEFAUT)
    if choix_charte == "Personnalisée":
        k1, k2, k3 = st.columns(3)
        charte = Charte("Personnalisée",
                        _texte(k1.color_picker("Couleur principale", value=base.primaire, key="rp_c1")) or base.primaire,
                        _texte(k2.color_picker("Couleur secondaire", value=base.secondaire, key="rp_c2")) or base.secondaire,
                        _texte(k3.color_picker("Couleur d'accent", value=base.accent, key="rp_c3")) or base.accent)
    else:
        charte = base
    try:
        ratio = contraste(charte.primaire)
    except ValueError:
        charte, ratio = CHARTE_DEFAUT, contraste(CHARTE_DEFAUT.primaire)
    if ratio < 4.5:
        st.warning(f"Couleur principale peu contrastée sur fond blanc ({ratio:.1f}:1 < 4,5:1) : elle sera "
                   "automatiquement assombrie pour les titres et les graphiques (lisibilité, impression).")
    st.markdown(
        " ".join(f"<span style='display:inline-block;width:2.2rem;height:1.2rem;border-radius:.3rem;"
                 f"background:{c};margin-right:.3rem;border:1px solid rgba(0,0,0,.15)'></span>"
                 for c in (charte.primaire, charte.secondaire, charte.accent)),
        unsafe_allow_html=True,
    )

# --------------------------------------------------------------------------- équipe
with tab_equipe:
    st.caption("Une ligne par membre : le rôle (ex. « Responsable financier ») oriente les indicateurs proposés "
               "dans sa partie individuelle. Le rapport crée une section individuelle par membre.")
    base_membres = st.session_state.get("rp_membres_base")
    if not isinstance(base_membres, pd.DataFrame):
        base_membres = pd.DataFrame(columns=COLONNES_MEMBRES)
        st.session_state["rp_membres_base"] = base_membres
    edite = st.data_editor(base_membres, num_rows="dynamic", key="rp_membres", width="stretch", hide_index=True)
    membres_lignes: list[dict[str, str]] = []
    if isinstance(edite, pd.DataFrame):
        for _, ligne in edite.iterrows():
            membre = {c: _cellule(ligne.get(c)) for c in COLONNES_MEMBRES}
            if membre["Nom"] or membre["Rôle"]:
                membres_lignes.append(membre)
    st.caption(f"{len(membres_lignes)} membre(s) renseigné(s).")

# --------------------------------------------------------------------------- rédaction assistée
# Ce bloc s'exécute AVANT la création des champs rédigés (onglets suivants) : il peut donc
# y écrire les textes produits dans la même exécution (Streamlit refuse de modifier la
# valeur d'un widget déjà créé).
def _texte_session(cle: str) -> str:
    return _texte(st.session_state.get(cle))


def _liste_session(cle: str, valides, defaut=()) -> tuple[str, ...]:
    valeur = st.session_state.get(cle, list(defaut))
    return tuple(o for o in (valeur if isinstance(valeur, list) else []) if o in valides)


def _contenu_courant(contexte_equipe: str) -> tuple[R.ContenuRapport, list[str]]:
    """Contenu tel que saisi à cet instant (textes lus dans la session) et clé de chaque membre."""
    cles = [_cle_membre(ligne["Nom"], rang) for rang, ligne in enumerate(membres_lignes)]
    membres_saisis = tuple(
        R.Membre(nom=ligne["Nom"], role=ligne["Rôle"], missions=ligne["Missions"],
                 outils_erp=_liste_session(f"rp_m_{cle}_outils_erp", NOMS_OUTILS),
                 **{champ: _texte_session(f"rp_m_{cle}_{champ}") for champ, _, _ in CHAMPS_INDIVIDUELS})
        for ligne, cle in zip(membres_lignes, cles)
    )
    return R.ContenuRapport(
        **{k: saisies.get(k, "") for k in CHAMPS_IDENTITE},
        contexte=contexte_equipe,
        **{c: _texte_session(f"rp_{c}") for c, _, _ in CHAMPS_COLLECTIFS},
        outils_erp=_liste_session("rp_outils_erp", NOMS_OUTILS, NOMS_OUTILS),
        outils_gp=_liste_session("rp_outils_gp", R.OUTILS_GP),
        membres=membres_saisis,
    ), cles


MODE_API = "Automatique (API Claude)"
MODE_MANUEL = "Manuel : claude.ai, sans clé API"


def _appliquer_redaction(redaction: RR.Redaction, demande: RR.Demande, remplacer: bool) -> None:
    """Écrit les textes produits dans les champs, avant leur création, et garde le bilan affiché."""
    ecritures = RR.mises_a_jour(redaction, st.session_state, remplacer)
    for cle, texte_redige in ecritures.items():
        st.session_state[cle] = texte_redige
    st.session_state["rp_redaction_bilan"] = {
        "champs": len(ecritures), "modele": redaction.modele, "notes": list(redaction.notes),
        "a_completer": RR.passages_a_completer(redaction),
        "a_verifier": RR.chiffres_non_verifies(redaction, demande),
    }


config_redaction = RR.lire_configuration(st.secrets, os.environ)
redaction_possible = is_editor() and RR.sdk_disponible()
with tab_redaction:
    st.info("Décrivez votre année en un paragraphe : Claude en rédige un **brouillon** des textes du rapport — "
            "partie collective et parties individuelles — à partir de ce contexte, des paramètres saisis "
            "(entreprise, équipe, rôles, outils) et des chiffres de l'audit. Relisez et corrigez ensuite chaque "
            "champ dans les onglets « 🧭 Partie collective » et « 🙋 Parties individuelles ».")
    contexte = _texte(st.text_area(
        "Contexte de l'année (un paragraphe)", key="rp_contexte", height=220,
        placeholder="Ex. : Notre stratégie : des prix sous le marché pour gagner du volume, centrée sur l'Integral 7. "
                    "En mars, nous avons emprunté pour acheter deux machines de découpe ; l'été, l'atelier a saturé. "
                    "Organisation : une réunion de décision à chaque tour et un Kanban partagé. Alex (finance) a piloté "
                    "la trésorerie et l'emprunt ; Sam (production) a anticipé la saturation mais a tardé à recruter…",
        help="Stratégie et intentions, faits marquants, organisation de l'équipe, et une phrase par membre "
             "(contribution, réussite, difficulté). Plus il est précis, moins il restera de passages à compléter.",
    ))
    st.caption(f"{R.nombre_mots(contexte)} mot(s) · décrivez ce que les chiffres ne disent pas : vos intentions, "
               "vos arbitrages, ce que chacun a fait. Les chiffres viennent de l'audit.")
    o1, o2, o3 = st.columns(3)
    rediger_collectif = bool(o1.checkbox("Partie collective", value=True, key="rp_red_collectif"))
    rediger_individuel = bool(o2.checkbox("Parties individuelles", value=True, key="rp_red_individuel"))
    remplacer_textes = bool(o3.checkbox(
        "Remplacer les textes déjà rédigés", value=False, key="rp_red_remplacer",
        help="Décoché : seuls les champs vides sont rédigés ; les textes existants servent de contexte."))
    mode_redaction = st.radio(
        "Mode de rédaction", [MODE_API, MODE_MANUEL], index=0 if config_redaction.api_key else 1,
        horizontal=True, key="rp_red_mode",
        help="Automatique : l'application appelle l'API Claude (clé API et crédits nécessaires). Manuel : vous "
             "copiez la demande dans une conversation claude.ai (offre gratuite ou abonnement), puis collez sa "
             "réponse ici — sans clé ni coût pour l'application.")

    if mode_redaction == MODE_API:
        if not is_editor():
            st.info("🔒 Réservé aux utilisateurs connectés : chaque rédaction est un appel facturé à l'API Claude. "
                    "Le mode manuel reste disponible.")
        elif not RR.sdk_disponible():
            st.warning("Rédaction automatique indisponible : installez le paquet « anthropic » "
                       "(pip install -r requirements.txt), ou utilisez le mode manuel.")
        clic_redaction = st.button("✨ Rédiger les paragraphes", type="primary", disabled=not redaction_possible)
        if clic_redaction and not redaction_possible:
            st.error("Rédaction automatique réservée aux utilisateurs connectés.")
        elif clic_redaction and not contexte.strip():
            st.warning("Rédigez d'abord le paragraphe de contexte : c'est la source principale du brouillon.")
        elif clic_redaction:
            contenu_saisi, cles_membres = _contenu_courant(contexte)
            demande = RR.preparer_demande(audit, contenu_saisi, cles_membres, collectif=rediger_collectif,
                                          individuel=rediger_individuel, remplacer=remplacer_textes)
            if demande.nb_champs == 0:
                st.info("Rien à rédiger : les champs sélectionnés sont déjà remplis "
                        "(cochez « Remplacer les textes déjà rédigés » pour les réécrire).")
            else:
                redaction = None
                with st.spinner(f"Rédaction de {demande.nb_champs} champ(s) par Claude…"):
                    try:
                        redaction = RR.rediger(demande, config_redaction)
                    except RR.ErreurRedaction as exc:
                        st.error(str(exc))
                if redaction is not None:
                    _appliquer_redaction(redaction, demande, remplacer_textes)
        st.caption(f"Modèle : {config_redaction.modele} · effort {config_redaction.effort}. Le contexte, les "
                   "paramètres du rapport et les chiffres de l'audit sont envoyés à l'API d'Anthropic ; rien "
                   "n'est écrit en base.")
    else:
        # Mode manuel : aucun appel ni coût pour l'application, donc ouvert à tous. La demande est
        # reconstruite à chaque exécution ; la réponse collée est lue contre elle (membres reconnus
        # par leur nom, champs rédigés depuis la copie jamais écrasés).
        demande_manuelle = None
        if contexte.strip():
            contenu_saisi, cles_membres = _contenu_courant(contexte)
            demande_manuelle = RR.preparer_demande(audit, contenu_saisi, cles_membres, collectif=rediger_collectif,
                                                   individuel=rediger_individuel, remplacer=remplacer_textes)
        st.markdown("**① Copiez la demande** (bouton en haut à droite du cadre) dans une nouvelle conversation "
                    "sur claude.ai, puis envoyez-la.")
        if demande_manuelle is None:
            st.info("Rédigez d'abord le paragraphe de contexte ci-dessus : il fait partie de la demande.")
        elif demande_manuelle.nb_champs == 0:
            st.info("Rien à rédiger : les champs sélectionnés sont déjà remplis "
                    "(cochez « Remplacer les textes déjà rédigés » pour les réécrire).")
        else:
            texte_demande = RR.texte_manuel(demande_manuelle)
            st.code(texte_demande, language=None, wrap_lines=True, height=260)
            st.download_button("⬇️ Télécharger la demande (.txt)", data=texte_demande.encode("utf-8"),
                               file_name="demande_redaction_rapport.txt", mime="text/plain")
            st.caption(f"{demande_manuelle.nb_champs} champ(s) à rédiger. Si vous modifiez ensuite l'équipe ou "
                       "les options, copiez de nouveau la demande.")
        st.markdown("**② Collez la réponse de Claude**, puis intégrez-la : les champs vides sont remplis, vos "
                    "textes conservés (sauf « Remplacer »).")
        reponse_collee = _texte(st.text_area(
            "Réponse de Claude", key="rp_red_reponse", height=180,
            placeholder='Collez ici toute la réponse : {"collectif": {…}, "membres": {…}}'))
        if st.button("📥 Intégrer la réponse"):
            if demande_manuelle is None:
                st.warning("Rédigez d'abord le paragraphe de contexte, puis copiez la demande (étape ①).")
            else:
                try:
                    redaction = RR.lire_reponse_manuelle(reponse_collee, demande_manuelle)
                except RR.ErreurRedaction as exc:
                    st.error(str(exc))
                else:
                    _appliquer_redaction(redaction, demande_manuelle, remplacer_textes)
        st.caption("Mode manuel : la demande (contexte, paramètres et chiffres de l'audit) passe par votre "
                   "conversation claude.ai ; l'application n'appelle aucune API et rien n'est écrit en base.")

    bilan_redaction = st.session_state.get("rp_redaction_bilan")
    if isinstance(bilan_redaction, dict):
        st.success(f"✨ Dernière rédaction : {bilan_redaction.get('champs', 0)} champ(s) rempli(s) par "
                   f"{bilan_redaction.get('modele') or 'Claude'}. Relisez-les dans les onglets « 🧭 Partie "
                   "collective » et « 🙋 Parties individuelles ».")
        for _note in bilan_redaction.get("notes") or []:
            st.info(_note)
        if bilan_redaction.get("a_completer"):
            st.info(f"{bilan_redaction['a_completer']} passage(s) « [À COMPLÉTER : …] » attendent une information "
                    "que seule l'équipe peut apporter (surlignés en jaune dans le Word).")
        for _champ, _nombres in (bilan_redaction.get("a_verifier") or {}).items():
            st.warning(f"Chiffre(s) absent(s) des données de l'audit, à vérifier — {_champ} : {', '.join(_nombres)}")

# --------------------------------------------------------------------------- partie collective
with tab_coll:
    st.info("Rédigez ici la partie collective. Un champ vide reste signalé « À COMPLÉTER » (surligné) dans le Word. "
            "Mise en forme : une ligne par paragraphe, « - » en début de ligne pour une puce, **gras**.")
    for champ, libelle, mini in CHAMPS_COLLECTIFS:
        saisies[champ] = _texte(st.text_area(libelle, key=f"rp_{champ}", height=170, help=R.CONSIGNES[champ],
                                             placeholder=R.CONSIGNES[champ]))
        st.caption(f"{R.nombre_mots(saisies[champ])} mot(s) · repère : au moins {mini}")
        if champ == "strategie" and (audit.lecture_strategique or audit.chronologie):
            with st.expander("💡 Repères de l'audit pour expliquer la stratégie"):
                for ligne in audit.lecture_strategique:
                    st.write("• " + ligne)
                for evt in audit.chronologie[:12]:
                    st.write(f"• {R.mois_court(evt.periode)} — {evt.texte}")
        if champ == "outils":
            outils_erp = st.multiselect("Outils de l'ERP présentés dans le rapport", NOMS_OUTILS,
                                        key="rp_outils_erp")
        if champ == "methodes":
            outils_gp = st.multiselect("Outils et méthodes de gestion de projet utilisés", list(R.OUTILS_GP),
                                       key="rp_outils_gp")
        if champ == "prise_de_recul" and audit.constats:
            with st.expander("💡 Constats chiffrés pour argumenter la prise de recul"):
                for k in audit.constats:
                    st.write(f"• **{NATURES.get(k.nature, k.nature)}** — {k.texte}")

# --------------------------------------------------------------------------- parties individuelles
membres: list[R.Membre] = []
with tab_indiv:
    if not membres_lignes:
        st.info("Ajoutez d'abord les membres dans l'onglet « 👥 Équipe ».")
    for rang, ligne in enumerate(membres_lignes):
        cle = _cle_membre(ligne["Nom"], rang)
        champs: dict[str, str] = {}
        with st.expander(f"🙋 {ligne['Nom'] or 'Membre ' + str(rang + 1)} — {ligne['Rôle'] or 'rôle à préciser'}",
                         expanded=len(membres_lignes) == 1):
            outils_membre = st.multiselect("Outils de l'ERP que j'ai montés ou pilotés", NOMS_OUTILS,
                                           key=f"rp_m_{cle}_outils_erp")
            for champ, libelle, consigne in CHAMPS_INDIVIDUELS:
                champs[champ] = _texte(st.text_area(libelle, key=f"rp_m_{cle}_{champ}", height=110,
                                                    help=R.CONSIGNES[consigne], placeholder=R.CONSIGNES[consigne]))
            perimetre = R.perimetre_role(ligne["Rôle"])
            if perimetre:
                st.caption("Indicateurs de votre périmètre ajoutés au rapport : "
                           + ", ".join(R.INDICATEURS[k][0] for k in perimetre) + ".")
        membres.append(R.Membre(
            nom=ligne["Nom"], role=ligne["Rôle"], missions=ligne["Missions"],
            outils_erp=tuple(o for o in (outils_membre if isinstance(outils_membre, list) else []) if o in NOMS_OUTILS),
            **champs,
        ))

contenu = R.ContenuRapport(
    **{k: saisies.get(k, "") for k in CHAMPS_IDENTITE},
    contexte=contexte,
    **{c: saisies.get(c, "") for c, _, _ in CHAMPS_COLLECTIFS},
    outils_erp=tuple(o for o in (outils_erp if isinstance(outils_erp, list) else NOMS_OUTILS) if o in NOMS_OUTILS),
    outils_gp=tuple(o for o in (outils_gp if isinstance(outils_gp, list) else []) if o in R.OUTILS_GP),
    membres=tuple(membres),
)

# --------------------------------------------------------------------------- aperçu de l'audit
couleurs = (charte.primaire, charte.secondaire, charte.accent)
graphiques = _graphiques(cle_donnees, debut, fin, numero, couleurs) if MATPLOTLIB_DISPONIBLE else {}
with tab_audit:
    st.subheader(f"Audit {R.mois_court(audit.debut)} → {R.mois_court(audit.fin)} · {s.n_mois} mois")
    m1, m2, m3 = st.columns(3)
    m1.metric("Chiffre d'affaires", R.fmt_euros(s.ca_total), help=f"{s.ca_mois} mois renseigné(s)")
    m2.metric("Résultat net", R.fmt_euros(s.resultat_total),
              delta=f"marge nette {R.fmt_pct(s.marge_nette)}" if s.marge_nette is not None else None, delta_color="off")
    m3.metric("Trésorerie de clôture", R.fmt_euros(s.tresorerie_cloture[1]) if s.tresorerie_cloture else "—")
    m4, m5, m6 = st.columns(3)
    m4.metric("Part de marché finale", R.fmt_pct(s.pdm_fin[1], 2) if s.pdm_fin else "—")
    m5.metric("Unités vendues", R.fmt_nombre(s.ventes_totales) if s.ventes_totales is not None else "—")
    m6.metric("Évolution du CA mensuel", R.fmt_pct(s.croissance_ca, 1, signe=True) if s.croissance_ca is not None else "—")
    if not MATPLOTLIB_DISPONIBLE:
        st.warning("Graphiques indisponibles : installez « matplotlib » (pip install -r requirements.txt).")
    elif not graphiques:
        st.info("Pas assez de données pour tracer un graphique (au moins deux mois renseignés sont nécessaires).")
    colonnes_graph = st.columns(2)
    for i, g in enumerate(graphiques.values()):
        with colonnes_graph[i % 2]:
            st.image(g.png, caption=g.titre, width="stretch")
            st.caption(g.commentaire)
    st.markdown("**Tableau de bord mensuel**")
    st.dataframe(pd.DataFrame([{
        "Mois": R.mois_court(m.periode), "CA": m.v(R.CA), "Résultat net": m.v(R.RESULTAT),
        "Trésorerie": m.v(R.TRESORERIE), "Part de marché (%)": m.v(R.PDM), "Unités vendues": m.v(R.VENTES_TOTALES),
        "Effectif": m.v(R.EFFECTIFS),
    } for m in audit.mois]), hide_index=True, width="stretch")
    st.markdown("**Constats (description + piste d'analyse)**")
    if audit.constats:
        st.dataframe(pd.DataFrame([{"Thème": k.theme, "Nature": NATURES.get(k.nature, k.nature), "Constat": k.texte,
                                    "Piste d'analyse": k.analyse} for k in audit.constats]),
                     hide_index=True, width="stretch")
    else:
        st.caption("Aucun constat : données insuffisantes.")
    if audit.lecture_strategique:
        st.markdown("**Lecture stratégique proposée (à valider par l'équipe)**")
        for ligne in audit.lecture_strategique:
            st.write("• " + ligne)
    if audit.chronologie:
        st.markdown("**Chronologie des décisions clés**")
        st.dataframe(pd.DataFrame([{"Mois": R.mois_court(e.periode), "Domaine": e.domaine, "Décision": e.texte}
                                   for e in audit.chronologie]), hide_index=True, width="stretch")
    if audit.manques:
        st.markdown("**Données manquantes**")
        st.dataframe(pd.DataFrame([{
            "Donnée": m.element, "Mois concernés": ", ".join(R.mois_court(p) for p in m.periodes),
            "Conséquence": m.consequence,
            "Étude à synchroniser": " ou ".join(f"{lib} ({'gratuite' if prix == 0 else f'{prix:g} €'})"
                                                for _sid, lib, prix in m.etudes) or "Saisie des données réelles",
        } for m in audit.manques]), hide_index=True, width="stretch")
        etudes_utiles = sorted({sid for m in audit.manques for sid, _lib, _prix in m.etudes})
        mois_manquants = sorted({p for m in audit.manques for p in m.periodes}, key=lambda p: PERIOD_INDEX[p])
        if is_editor() and etudes_utiles and mois_manquants:
            with st.form("rp_achat"):
                st.caption("Préparer la synchronisation des études manquantes (page Scraper, onglet « 🔁 Acquérir »).")
                mois_achat = st.selectbox("Mois à compléter", mois_manquants, key="rp_mois_achat")
                if st.form_submit_button("🛒 Préparer la synchronisation dans le Scraper"):
                    st.session_state["livrable_buy_request"] = {
                        "periode": mois_achat, "study_ids": etudes_utiles, "source": "Rapport professionnel"}
                    st.switch_page("pages/2_Scraper.py")
    with st.expander("🔎 Sources des données (traçabilité)"):
        for cle_ind, liste in audit.sources_par_indicateur().items():
            st.write(f"• **{R.INDICATEURS.get(cle_ind, (cle_ind,))[0]}** : {' ; '.join(liste)}")

# --------------------------------------------------------------------------- génération
with tab_gen:
    criteres = R.controle_completude(audit, contenu, logo=bool(logo), nb_graphiques=len(graphiques))
    taux = R.taux_completude(criteres)
    st.progress(min(1.0, max(0.0, taux)), text=f"Complétude au regard des attendus : {taux * 100:.0f} %")
    st.metric("Critères remplis", f"{sum(1 for c in criteres if c.statut == 'ok')} / {len(criteres)}")
    st.dataframe(pd.DataFrame([{"Partie": c.partie, "Critère": c.critere, "Statut": STATUTS.get(c.statut, c.statut),
                                "Détail": c.detail} for c in criteres]), hide_index=True, width="stretch")

    # Le paragraphe de contexte n'apparaît pas dans le Word : le modifier ne rend pas le document obsolète.
    empreinte = hashlib.sha1(
        (replace(contenu, contexte="").to_json() + repr(couleurs) + debut + fin + str(numero) + cle_donnees
         + hashlib.sha1(logo or b"").hexdigest()).encode("utf-8")).hexdigest()
    nom_fichier = f"Rapport_professionnel_{_slug(contenu.entreprise) or 'subakoua'}_{date.today():%Y%m%d}"
    if st.button("📄 Générer le rapport Word", type="primary", disabled=not DOCX_DISPONIBLE):
        try:
            octets = generer_rapport_docx(audit, contenu, graphiques, charte, logo)
            st.session_state["rp_docx"] = {"octets": octets, "nom": nom_fichier + ".docx", "empreinte": empreinte,
                                           "a_completer": compter_a_completer(octets)}
        except Exception as exc:
            st.error(f"Génération impossible : {type(exc).__name__}: {exc}")
    if not DOCX_DISPONIBLE:
        st.error("Génération Word indisponible : installez « python-docx » (pip install -r requirements.txt).")
    rapport = st.session_state.get("rp_docx")
    if isinstance(rapport, dict) and rapport.get("octets"):
        if rapport.get("empreinte") != empreinte:
            st.warning("Le contenu a changé depuis la dernière génération : cliquez de nouveau sur « Générer ».")
        else:
            st.success(f"Rapport prêt ({len(rapport['octets']) // 1024} Ko).")
        if rapport.get("a_completer"):
            st.warning(f"{rapport['a_completer']} passage(s) « [À COMPLÉTER] » surligné(s) en jaune restent dans "
                       "le document : complétez-les ici ou directement dans Word.")
        st.download_button("⬇️ Télécharger le rapport (.docx)", data=rapport["octets"], file_name=rapport["nom"],
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           type="primary")
    st.info("Finalisation : ouvrez le fichier dans Word → acceptez la mise à jour des champs (sommaire, pagination) "
            "→ complétez les passages surlignés → relisez → Fichier ▸ Exporter ▸ PDF. Un seul dépôt par groupe.")
    d1, d2 = st.columns(2)
    d1.download_button("💾 Enregistrer le brouillon (.json)", data=contenu.to_json().encode("utf-8"),
                       file_name=f"brouillon_{nom_fichier}.json", mime="application/json")
    d2.download_button("📝 Résumé chiffré de l'audit (.md)", data=rapport_markdown(audit, contenu).encode("utf-8"),
                       file_name=f"audit_{nom_fichier}.md", mime="text/markdown")
