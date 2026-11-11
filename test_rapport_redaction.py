"""Rédaction assistée du rapport : un paragraphe de contexte → un brouillon des textes du rapport.

Invariants protégés (`rapport_redaction`) :
* seuls les champs vides sont demandés, sauf « remplacer » ; les textes existants sont
  transmis comme contexte et jamais écrasés ;
* le modèle reçoit le contexte, les paramètres et les chiffres de l'audit, formatés comme
  dans le Word ; la réponse est imposée par un schéma JSON (un texte par champ) ;
* un chiffre des textes produits absent des sources est signalé à l'équipe ;
* refus des garde-fous, réponse tronquée ou illisible, clé refusée : message clair,
  rien n'est écrit ;
* la requête réellement émise par le SDK `anthropic` (transport HTTP simulé, aucun
  réseau) porte le modèle, l'effort, le schéma, le cache de l'invite système et le
  repli serveur sur refus.
"""
import json
import re
import types

import pytest

import rapport_engine as R
import rapport_redaction as RR
from rapport_jeu_essai import jeu_essai_annee

CONTEXTE = ("Nous avons choisi une stratégie de volume : prix sous le marché, focalisation sur l'Integral 7, "
            "objectif de 20 % de part de marché. Alex a piloté la trésorerie et l'emprunt de mars ; "
            "Sam a géré la saturation de l'atelier de découpe.")
CLES = ["alex_martin", "sam_leroy"]


@pytest.fixture(scope="module")
def audit():
    jeu = jeu_essai_annee()
    sources = R.indexer_sources(jeu["erp_donnees"], jeu["erp_etudes"], jeu["tables"])
    return R.construire_audit(sources, "Année 1 - Janvier", "Année 1 - Décembre", 3, "Néoprène & Co")


def _contenu(**sup):
    membres = (
        R.Membre(nom="Alex Martin", role="Responsable financier", missions="Trésorerie et financement",
                 outils_erp=("Livrable Finance mensuel",), forces="Rigueur dans le suivi de trésorerie."),
        R.Membre(nom="Sam Leroy", role="Responsable production et logistique", missions="Capacité et MRP"),
    )
    base = dict(entreprise="Néoprène & Co", equipe="Groupe 4", contexte=CONTEXTE,
                strategie="Stratégie déjà rédigée par l'équipe.", outils_gp=("Matrice RACI",), membres=membres)
    base.update(sup)
    return R.ContenuRapport(**base)


def _bloc(message: str, balise: str) -> str:
    return message.split(f"<{balise}>", 1)[1].split(f"</{balise}>", 1)[0]


# --------------------------------------------------------------------------- demande
def test_seuls_les_champs_vides_sont_demandes_et_les_autres_servent_de_contexte(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)

    assert [c.cle for c in demande.collectif] == [c for c, _, _ in R.CHAMPS_COLLECTIFS if c != "strategie"]
    alex, sam = demande.membres
    assert (alex.cle_json, alex.cle_session) == ("membre_1", "alex_martin")
    assert "forces" not in [c.cle for c in alex.champs]          # déjà rédigé par Alex
    assert len(sam.champs) == len(R.CHAMPS_INDIVIDUELS)
    deja = json.loads(_bloc(demande.message, "textes_deja_rediges"))
    assert deja["collectif"] == {"strategie": "Stratégie déjà rédigée par l'équipe."}
    assert deja["membres"] == {"membre_1 (Alex Martin)": {"forces": "Rigueur dans le suivi de trésorerie."}}


def test_remplacer_redemande_tous_les_champs(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, remplacer=True)

    assert demande.nb_champs == len(R.CHAMPS_COLLECTIFS) + 2 * len(R.CHAMPS_INDIVIDUELS)
    assert "<textes_deja_rediges>" not in demande.message


def test_une_partie_non_selectionnee_n_est_pas_demandee(audit):
    seulement_collectif = RR.preparer_demande(audit, _contenu(), CLES, individuel=False)
    seulement_individuel = RR.preparer_demande(audit, _contenu(), CLES, collectif=False)

    assert list(seulement_collectif.schema["properties"]) == ["collectif"]
    assert list(seulement_individuel.schema["properties"]) == ["membres"]
    assert seulement_collectif.membres == () and seulement_individuel.collectif == ()


def test_le_message_porte_le_contexte_les_parametres_et_les_chiffres_de_l_audit(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)

    assert _bloc(demande.message, "contexte_equipe").strip() == CONTEXTE
    parametres = json.loads(_bloc(demande.message, "parametres_du_rapport"))
    assert parametres["entreprise"] == "Néoprène & Co"
    alex = parametres["membres"][0]
    assert (alex["nom"], alex["role"]) == ("Alex Martin", "Responsable financier")
    assert alex["outils_de_l_erp_montes_ou_pilotes"] == ["Livrable Finance mensuel"]
    assert "Trésorerie (banque, fin de mois)" in alex["indicateurs_du_perimetre"]   # périmètre du rôle
    assert list(parametres["outils_de_gestion_de_projet"]) == ["Matrice RACI"]
    faits = json.loads(_bloc(demande.message, "faits_de_l_audit"))
    s = audit.synthese
    assert faits["synthese"]["chiffre_affaires_total"].startswith(R.fmt_euros(s.ca_total))   # « 15 272 032 € »
    assert faits["synthese"]["part_de_marche_fin"].startswith(R.fmt_pct(s.pdm_fin[1], 2))    # « 15,20 % »
    assert faits["lecture_strategique"] == list(audit.lecture_strategique)
    assert faits["indicateurs_debut_fin"]["Trésorerie (banque, fin de mois)"].count("→") == 1
    consignes = _bloc(demande.message, "champs_a_rediger")
    assert "environ 100 mots" in consignes and R.CONSIGNES["synthese"] in consignes
    assert "membre_2 : Sam Leroy (Responsable production et logistique) — tous les champs" in consignes
    assert "membre_1 : Alex Martin (Responsable financier) — champs : outils, faiblesses" in consignes


# --------------------------------------------------------------------------- budget de jetons
def test_la_demande_est_compacte(audit):
    """Gains rapides : JSON sans indentation, consignes individuelles une seule fois, sans pistes d'analyse."""
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    message = demande.message

    for balise in ("parametres_du_rapport", "faits_de_l_audit"):
        bloc = _bloc(message, balise).strip()
        assert "\n" not in bloc and '": ' not in bloc               # une ligne, sans espace après « : »
    assert message.count(R.CONSIGNES["bilan"]) == 1                 # deux membres, une seule consigne
    assert all(k.analyse not in message for k in audit.constats if k.analyse)
    # Les outils se résument à leur apport ; un outil absent des choix de l'équipe n'est pas envoyé.
    outils = json.loads(_bloc(message, "parametres_du_rapport"))["outils_de_l_erp"]
    assert outils["Livrable Finance mensuel"] == next(o.apport for o in R.OUTILS_ERP if o.nom == "Livrable Finance mensuel")


def test_rediger_un_seul_membre_n_envoie_que_ce_qui_lui_sert(audit):
    complet = RR.preparer_demande(audit, _contenu(), CLES)
    sam = RR.preparer_demande(audit, _contenu(), CLES, collectif=False, membres_cibles={"sam_leroy"})

    assert [m.cle_session for m in sam.membres] == ["sam_leroy"] and sam.collectif == ()
    alex, sam_parametres = json.loads(_bloc(sam.message, "parametres_du_rapport"))["membres"]
    assert set(alex) == {"cle", "nom", "role"}                      # ni missions ni outils d'Alex
    assert sam_parametres["missions"] == "Capacité et MRP"
    deja = json.loads(_bloc(sam.message, "textes_deja_rediges"))
    assert deja == {"collectif": {"strategie": "Stratégie déjà rédigée par l'équipe."}}   # pas les textes d'Alex
    assert len(sam.message) < len(complet.message)
    # Préfixe mis en cache : les faits de l'audit sont identiques quel que soit le périmètre demandé.
    assert sam.faits == complet.faits == RR.preparer_demande(audit, _contenu(contexte="Autre."), CLES).faits


def test_le_budget_plafonne_la_sortie_d_apres_les_longueurs_demandees(audit):
    complet = RR.preparer_demande(audit, _contenu(), CLES)
    un_membre = RR.preparer_demande(audit, _contenu(), CLES, collectif=False, membres_cibles={"sam_leroy"})

    budget = RR.estimer_budget(complet, "medium")
    mots = sum(c.mots for c in complet.champs)
    assert budget.jetons_texte == int(mots * RR.JETONS_PAR_MOT) + RR.JETONS_PAR_CHAMP * complet.nb_champs
    assert budget.max_tokens == -(-budget.jetons_texte * 3 // 2000) * 1000 + RR.REFLEXION["medium"]
    assert budget.max_tokens < RR.PLAFOND_SORTIE
    assert RR.estimer_budget(complet, "low").max_tokens < budget.max_tokens
    assert RR.estimer_budget(un_membre, "medium").max_tokens < budget.max_tokens
    assert budget.jetons_entree == RR.estimer_jetons(RR.SYSTEME) + RR.estimer_jetons(complet.message)
    assert RR.depassements(complet, budget) == []
    probable, maximum = RR.cout_estime(budget, "claude-opus-5-5")
    assert probable == pytest.approx((budget.jetons_entree * 4 + budget.jetons_texte * 20) / 1e6)
    assert maximum == pytest.approx((budget.jetons_entree * 4 + budget.max_tokens * 20) / 1e6)
    assert RR.cout_estime(budget, "modele-inconnu") is None


def test_une_demande_hors_budget_est_refusee_sans_appel(audit):
    trop_long = RR.preparer_demande(audit, _contenu(contexte="x" * (RR.CONTEXTE_MAX + 1)), CLES)
    client = _ClientFactice()

    with pytest.raises(RR.ErreurRedaction, match="Contexte trop long"):
        RR.rediger(trop_long, RR.Configuration(), client=client)
    assert client.requetes == []                                     # rien n'est facturé
    gros = RR.Budget(jetons_entree=RR.BUDGET_ENTREE + 1, jetons_texte=1, max_tokens=1)
    assert "partie par partie" in RR.depassements(RR.preparer_demande(audit, _contenu(), CLES), gros)[0]


def test_le_cout_reel_compte_le_cache():
    redaction = RR.Redaction({}, {}, modele="claude-opus-5-5", jetons_entree=4_000, jetons_sortie=3_000,
                             jetons_cache_lus=2_000, jetons_cache_ecrits=1_000)
    assert RR.cout_reel(redaction) == pytest.approx((4_000 * 4 + 1_000 * 4 * 1.25 + 2_000 * 0.20 + 3_000 * 20) / 1e6)
    assert RR.cout_reel(RR.Redaction({}, {}, modele="claude.ai (mode manuel)")) is None


def test_la_declaration_d_usage_de_l_ia_nomme_l_outil_et_le_mode():
    api = RR.Redaction({}, {}, modele="claude-opus-5-5")
    manuel = RR.Redaction({}, {}, modele="claude.ai (mode manuel)")
    assert RR.declaration_usage(api, 24, "11/11/2026") == ("Claude Opus 5.5 (claude-opus-5-5), API Anthropic — "
                                                          "génération de texte partiel : 24 champ(s) rédigé(s) le 11/11/2026")
    assert RR.declaration_usage(manuel, 7, "12/11/2026").startswith("Claude (claude.ai, conversation copiée-collée")


def test_le_schema_impose_un_texte_par_champ(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    schema = demande.schema

    def verifier(objet):
        assert objet["type"] == "object" and objet["additionalProperties"] is False
        assert objet["required"] == list(objet["properties"])
        for sous in objet["properties"].values():
            if sous["type"] == "object":
                verifier(sous)
            else:
                assert sous == {"type": "string"}

    verifier(schema)
    assert list(schema["properties"]["membres"]["properties"]) == ["membre_1", "membre_2"]


def test_l_invite_systeme_interdit_les_chiffres_et_faits_inventes():
    assert "n'écris que des nombres présents dans ces sources" in RR.SYSTEME
    assert "[À COMPLÉTER : ce que l'équipe doit préciser]" in RR.SYSTEME
    assert R.A_COMPLETER.search("[À COMPLÉTER : ce que l'équipe doit préciser]")


# --------------------------------------------------------------------------- réponse
def test_la_reponse_est_lue_champ_par_champ(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, individuel=False)
    reponse = {"collectif": {"synthese": "  Notre année.\r\n\n\n\nDeuxième paragraphe [à compléter : la date]  ",
                             "conclusion": "", "inconnu": "ignoré"},
               "membres": {"membre_1": {"forces": "non demandé"}}}

    redaction = RR.analyser_reponse(json.dumps(reponse), demande)

    assert redaction.collectif == {"synthese": "Notre année.\n\nDeuxième paragraphe [À COMPLÉTER : la date]"}
    assert redaction.membres == {}                                 # partie non demandée : ignorée
    assert RR.passages_a_completer(redaction) == 1


def test_une_reponse_illisible_leve_une_erreur_claire(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    with pytest.raises(RR.ErreurRedaction, match="JSON"):
        RR.analyser_reponse("Voici le rapport :", demande)
    with pytest.raises(RR.ErreurRedaction, match="objet JSON"):
        RR.analyser_reponse("[1, 2]", demande)


# --------------------------------------------------------------------------- mode manuel (sans clé API)
def test_la_demande_manuelle_reprend_les_consignes_les_sources_et_le_gabarit(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    texte = RR.texte_manuel(demande)

    assert texte.startswith(RR.SYSTEME) and demande.message in texte
    gabarit = json.dumps(RR.gabarit_reponse(demande), ensure_ascii=False, indent=2)
    assert texte.endswith(gabarit)
    attendu = json.loads(gabarit)
    assert list(attendu["collectif"]) == [c.cle for c in demande.collectif]
    assert attendu["membres"]["membre_1"]["nom"] == "Alex Martin"
    assert "forces" not in attendu["membres"]["membre_1"]           # déjà rédigé : pas redemandé
    assert set(attendu["membres"]["membre_2"]) == {"nom", *(c for c, _, _ in R.CHAMPS_INDIVIDUELS)}


def test_une_reponse_collee_est_lue_malgre_le_texte_autour(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, individuel=False)
    colle = ("Voici les textes demandés :\n```json\n"
             '{"collectif": {"synthese": "Premier paragraphe.\nSecond paragraphe.", "conclusion": "Fin."}}\n'
             "```\nN'hésitez pas à me demander des ajustements.")   # retour à la ligne brut dans le texte

    redaction = RR.lire_reponse_manuelle(colle, demande)

    assert redaction.collectif == {"synthese": "Premier paragraphe.\nSecond paragraphe.", "conclusion": "Fin."}
    assert redaction.modele == "claude.ai (mode manuel)"
    assert RR.extraire_json('```text\nsans objet\n```\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert RR.extraire_json('Réponse : {"a": {"b": 2}} — voilà.') == '{"a": {"b": 2}}'


def test_les_membres_d_une_reponse_collee_sont_reconnus_a_leur_nom(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, collectif=False)
    colle = json.dumps({"membres": {
        "membre_1": {"nom": "  sam LEROY ", "bilan": "Bilan de Sam"},          # ordre inversé, casse, espaces
        "membre_2": {"nom": "Alex Martín", "bilan": "Bilan d'Alex"},           # accent ajouté
        "membre_3": {"nom": "Camille Inconnue", "bilan": "Ignoré"},
    }}, ensure_ascii=False)

    redaction = RR.lire_reponse_manuelle(colle, demande)

    assert redaction.membres == {"sam_leroy": {"bilan": "Bilan de Sam"}, "alex_martin": {"bilan": "Bilan d'Alex"}}
    assert redaction.notes == ("Textes de « Camille Inconnue » ignorés : membre absent de la demande ou champs "
                               "déjà rédigés.",)
    sans_nom = RR.lire_reponse_manuelle('{"membres": {"membre_2": {"bilan": "Par la clé"}}}', demande)
    assert sans_nom.membres == {"sam_leroy": {"bilan": "Par la clé"}}


@pytest.mark.parametrize("colle, attendu", [
    ("   ", "Collez d'abord la réponse"),
    ("Je ne peux pas rédiger ce rapport.", "Aucun objet JSON"),
    ('{"collectif": {"synthese": "x",}}', "objet JSON attendu est invalide"),
    ('{"collectif": {"inconnu": "x"}, "membres": {}}', "aucun des champs demandés"),
])
def test_une_reponse_collee_inexploitable_donne_un_message_clair(audit, colle, attendu):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    with pytest.raises(RR.ErreurRedaction, match=re.escape(attendu)):
        RR.lire_reponse_manuelle(colle, demande)


# --------------------------------------------------------------------------- vérifications
def test_un_chiffre_absent_des_sources_est_signale(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, individuel=False)
    ca = R.fmt_euros(audit.synthese.ca_total)
    redaction = RR.Redaction(collectif={
        "synthese": f"Nous réalisons {ca}, avec une marge de 10,8 % et 99,9 % de clients satisfaits.",
        "strategie": "Objectif de 20 % de part de marché, sur 12 mois, avec 3 machines de plus.",
        "conclusion": "Effet prix de 25 875 € [À COMPLÉTER : 123 456 € à confirmer] ; ventes de 7 777 unités.",
    }, membres={})

    a_verifier = RR.chiffres_non_verifies(redaction, demande)

    assert a_verifier == {
        "Résumé (4e de couverture)": ["99,9 %"],                   # inventé (le CA et la marge sont dans l'audit)
        "Conclusion du rapport": ["7 777"],                        # inventé ; l'écart de signe et le marqueur ignorés
    }                                                              # « 20 % » vient du contexte, « 12 », « 3 » sont petits


def test_les_textes_existants_ne_sont_jamais_ecrases():
    redaction = RR.Redaction(collectif={"synthese": "Nouveau", "conclusion": "Nouvelle conclusion"},
                             membres={"alex_martin": {"forces": "Nouvelles forces", "bilan": "Nouveau bilan"}})
    etat = {"rp_synthese": "Texte de l'équipe", "rp_m_alex_martin_forces": "  ", "rp_conclusion": ""}

    assert RR.mises_a_jour(redaction, etat) == {
        "rp_conclusion": "Nouvelle conclusion",
        "rp_m_alex_martin_forces": "Nouvelles forces", "rp_m_alex_martin_bilan": "Nouveau bilan",
    }
    assert RR.mises_a_jour(redaction, etat, remplacer=True)["rp_synthese"] == "Nouveau"


# --------------------------------------------------------------------------- appel à l'API (client factice)
class _ClientFactice:
    def __init__(self, texte="", arret="end_turn", categorie=None):
        self.requetes = []
        message = types.SimpleNamespace(
            stop_reason=arret, stop_details=types.SimpleNamespace(category=categorie), model="claude-opus-5-5",
            content=[types.SimpleNamespace(type="thinking", thinking=""), types.SimpleNamespace(type="text", text=texte)],
            usage=types.SimpleNamespace(input_tokens=9000, output_tokens=2500))

        class _Flux:
            def __enter__(flux): return flux
            def __exit__(flux, *a): return False
            def get_final_message(flux): return message

        def stream(**params):
            self.requetes.append(params)
            return _Flux()

        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream))


def _reponse(demande, texte="Texte."):
    return json.dumps({"collectif": {c.cle: texte for c in demande.collectif},
                       "membres": {m.cle_json: {c.cle: texte for c in m.champs} for m in demande.membres}})


def test_rediger_envoie_une_requete_complete_et_rend_les_textes(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    client = _ClientFactice(_reponse(demande))

    redaction = RR.rediger(demande, RR.Configuration(api_key="sk-test"), client=client)

    (params,) = client.requetes
    assert params["model"] == "claude-opus-5-5"
    assert params["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": demande.schema}}
    assert params["betas"] == ["server-side-fallback-2026-07-01"] and params["fallbacks"] == "default"
    assert params["system"] == [{"type": "text", "text": RR.SYSTEME, "cache_control": {"type": "ephemeral"}}]
    # Préfixe stable (consignes puis faits de l'audit) mis en cache ; la partie variable vient après.
    assert params["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": demande.faits, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": demande.variable}]}]
    assert params["max_tokens"] == RR.estimer_budget(demande, "medium").max_tokens
    assert "thinking" not in params                                  # réflexion adaptative par défaut
    assert len(redaction.collectif) == len(demande.collectif)
    assert set(redaction.membres) == set(CLES)
    assert (redaction.modele, redaction.jetons_entree, redaction.jetons_sortie) == ("claude-opus-5-5", 9000, 2500)


@pytest.mark.parametrize("arret, categorie, attendu", [
    ("refusal", "cyber", "déclinée par les garde-fous du modèle (catégorie « cyber »)"),
    ("max_tokens", None, "Réponse tronquée"),
])
def test_un_arret_anormal_ne_produit_aucun_texte(audit, arret, categorie, attendu):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    with pytest.raises(RR.ErreurRedaction, match=re.escape(attendu)):
        RR.rediger(demande, RR.Configuration(), client=_ClientFactice(_reponse(demande), arret, categorie))


def test_rien_a_rediger_ne_lance_aucun_appel(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, collectif=False, individuel=False)
    client = _ClientFactice()
    with pytest.raises(RR.ErreurRedaction, match="Aucun champ"):
        RR.rediger(demande, RR.Configuration(), client=client)
    assert client.requetes == []


def test_le_repli_sur_refus_n_est_active_que_pour_les_modeles_qui_l_acceptent(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    params = RR.parametres_requete(demande, RR.Configuration(modele="claude-haiku-5-5", effort="low"))

    assert "fallbacks" not in params and "betas" not in params
    assert params["output_config"]["effort"] == "low"


def test_configuration_par_les_secrets_puis_l_environnement():
    secrets = {"anthropic": {"api_key": "sk-secrets", "model": "claude-sonnet-5-5", "effort": "HIGH"}}
    env = {"ANTHROPIC_API_KEY": "sk-env", "ANTHROPIC_EFFORT": "inconnu"}

    assert RR.lire_configuration(secrets, env) == RR.Configuration("sk-secrets", "claude-sonnet-5-5", "high", "secrets")
    assert RR.lire_configuration({}, env) == RR.Configuration("sk-env", RR.MODELE_PAR_DEFAUT, "medium", "environnement")
    assert RR.lire_configuration(None, {}) == RR.Configuration(None, RR.MODELE_PAR_DEFAUT, "medium", "aucune")


def test_le_contexte_est_garde_dans_le_brouillon_json():
    contenu = R.ContenuRapport.from_json(_contenu().to_json())
    assert contenu.contexte == CONTEXTE


# --------------------------------------------------------------------------- SDK réel, transport simulé
def _client_reel(reponse_http):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    capture = {}

    def handler(request):
        capture["requete"] = request
        return reponse_http(httpx2)

    client = anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return client, capture


def test_la_requete_reelle_du_sdk_et_la_lecture_du_flux(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES, individuel=False)
    texte = _reponse(demande, "Paragraphe rédigé.")
    evenements = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                               "model": "claude-opus-5-5", "content": [], "stop_reason": None,
                                               "stop_sequence": None, "usage": {"input_tokens": 9000, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": texte[:30]}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": texte[30:]}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 800}},
        {"type": "message_stop"},
    ]
    flux = "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in evenements)
    client, capture = _client_reel(lambda httpx2: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=flux.encode("utf-8")))

    redaction = RR.rediger(demande, RR.Configuration(api_key="sk-ant-test"), client=client)

    requete = capture["requete"]
    corps = json.loads(requete.content)
    assert str(requete.url) == "https://api.anthropic.com/v1/messages"
    assert requete.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert corps["stream"] is True and corps["fallbacks"] == "default" and corps["model"] == "claude-opus-5-5"
    assert corps["output_config"]["format"]["schema"] == demande.schema
    assert corps["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert corps["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert corps["max_tokens"] == RR.estimer_budget(demande, "medium").max_tokens
    assert redaction.collectif == {c.cle: "Paragraphe rédigé." for c in demande.collectif}
    assert (redaction.jetons_entree, redaction.jetons_sortie) == (9000, 800)


@pytest.mark.parametrize("statut, type_erreur, attendu", [
    (401, "authentication_error", "Clé API Anthropic refusée"),
    (429, "rate_limit_error", "Limite de débit"),
    (404, "not_found_error", "Modèle « claude-opus-5-5 » introuvable"),
    (529, "overloaded_error", "indisponible (erreur 529)"),
])
def test_les_erreurs_de_l_api_sont_traduites(audit, statut, type_erreur, attendu):
    demande = RR.preparer_demande(audit, _contenu(), CLES)
    client, _ = _client_reel(lambda httpx2: httpx2.Response(
        statut, json={"type": "error", "error": {"type": type_erreur, "message": "erreur"}}))

    with pytest.raises(RR.ErreurRedaction, match=re.escape(attendu)):
        RR.rediger(demande, RR.Configuration(api_key="sk-ant-test"), client=client)


def test_sans_identifiants_le_message_explique_la_configuration(audit):
    demande = RR.preparer_demande(audit, _contenu(), CLES)

    class _SansCle:
        class beta:
            class messages:
                @staticmethod
                def stream(**params):
                    raise TypeError("Could not resolve authentication method. Expected one of api_key, ...")

    with pytest.raises(RR.ErreurRedaction, match=r"Aucune clé API Anthropic.*\[anthropic\]"):
        RR.rediger(demande, RR.Configuration(), client=_SansCle())
