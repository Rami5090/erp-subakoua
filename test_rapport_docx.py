"""Rendu du rapport professionnel : graphiques (PNG) et document Word.

On vérifie le livrable tel que l'enseignant le recevra : structure calquée sur
les attendus (synthèse, partie collective, partie individuelle par membre,
annexes), graphiques et tableaux présents, logo et charte appliqués, champs
vides signalés « À COMPLÉTER » — et plus aucun quand tout est rempli.
"""
import io

import pytest

docx = pytest.importorskip("docx")
pytest.importorskip("matplotlib")

import rapport_engine as R  # noqa: E402
import rapport_graphiques as G  # noqa: E402
from rapport_docx import MARQUEUR_A_COMPLETER, compter_a_completer, generer_rapport_docx, rapport_markdown  # noqa: E402
from rapport_jeu_essai import jeu_essai_annee  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def audit():
    jeu = jeu_essai_annee()
    sources = R.indexer_sources(jeu["erp_donnees"], jeu["erp_etudes"], jeu["tables"])
    return R.construire_audit(sources, "Année 1 - Janvier", "Année 1 - Décembre", 3)


@pytest.fixture(scope="module")
def graphiques(audit):
    return G.graphiques_rapport(audit, G.CHARTE_DEFAUT)


def _logo() -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    fig = Figure(figsize=(1.5, 1), dpi=80)
    FigureCanvasAgg(fig)
    fig.add_subplot(111).axis("off")
    tampon = io.BytesIO()
    fig.savefig(tampon, format="png")
    return tampon.getvalue()


def _complet() -> R.ContenuRapport:
    membre = R.Membre(nom="Alex Martin", role="Responsable financier", missions="Trésorerie et financement.",
                      outils="Plan de trésorerie.", outils_erp=("Livrable Finance mensuel",), forces="Rigueur.",
                      faiblesses="Délégation.", reussites="Emprunt de mars.", erreurs="Prix de janvier.",
                      mission_specifique="Mission spécifique Finance.", bilan="Montée en compétence.")
    return R.ContenuRapport(entreprise="Néoprène & Co", equipe="G3", synthese="Synthèse.", presentation="Présentation.",
                            strategie="Stratégie de volume.\n- Prix sous le marché", outils="Outils.", methodes="Méthodes.",
                            outils_gp=("Matrice RACI",), prise_de_recul="Recul.", conclusion="Conclusion.",
                            membres=(membre,))


def _textes(document) -> str:
    morceaux = [p.text for p in document.paragraphs]
    for table in document.tables:
        for ligne in table.rows:
            morceaux += [c.text for c in ligne.cells]
    return "\n".join(morceaux)


# --------------------------------------------------------------------------- graphiques
def test_tous_les_graphiques_sont_produits(graphiques):
    attendus = {"ca_mensuel", "ca_cumule", "ventes_produits", "mix_produits", "decomposition", "pdm",
                "concurrence", "resultat", "tresorerie", "ateliers"}
    assert set(graphiques) == attendus
    for g in graphiques.values():
        assert g.png.startswith(PNG) and len(g.png) > 5_000
        assert g.commentaire and g.source.startswith("Source")


def test_aucun_graphique_sans_donnees():
    vide = R.construire_audit(R.indexer_sources(), "Année 1 - Janvier", "Année 1 - Décembre")
    assert G.graphiques_rapport(vide) == {}


def test_couleurs_lisibles_et_graduations_exactes():
    assert G.contraste("#000000", "#FFFFFF") == pytest.approx(21.0)
    jaune = G.couleur_lisible("#FFE600")
    assert G.contraste(jaune) >= 3.0
    assert G.couleur_lisible("pas une couleur") == G.CHARTE_DEFAUT.primaire
    assert G._sans_zeros(0.25) == "0,25" and G._sans_zeros(1.5) == "1,5" and G._sans_zeros(2.0) == "2"


# --------------------------------------------------------------------------- document Word
def test_structure_conforme_aux_attendus(audit, graphiques):
    octets = generer_rapport_docx(audit, _complet(), graphiques, G.CHARTE_DEFAUT, _logo())
    document = docx.Document(io.BytesIO(octets))
    titres = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    for attendu in ("Synthèse", "Partie 1 — Travail collectif", "2. Les stratégies mises en œuvre",
                    "3. Les outils et méthodes mis en œuvre", "4. Audit de l'année : description et analyse",
                    "5. Explication et prise de recul sur les résultats", "Partie 2 — Travail individuel",
                    "7. Alex Martin — Responsable financier", "Mes forces et mes faiblesses",
                    "Mes réussites et mes erreurs", "Annexes", "B. Sources des données et traçabilité"):
        assert attendu in titres, attendu
    # 10 graphiques + logo de couverture ; des tableaux commentés.
    assert len(document.inline_shapes) == len(graphiques) + 1
    assert len(document.tables) >= 15
    texte = _textes(document)
    assert "818 073 €" in texte                 # CA de janvier, format français
    assert "Entreprise 3 (nous)" in texte                  # benchmark concurrentiel
    assert "Néoprène & Co — Rapport professionnel Subakoua" in document.sections[0].header.paragraphs[0].text


def test_aucun_a_completer_quand_tout_est_rempli(audit, graphiques):
    octets = generer_rapport_docx(audit, _complet(), graphiques, G.CHARTE_DEFAUT, _logo())
    assert compter_a_completer(octets) == 0


def test_les_champs_vides_sont_signales_jamais_inventes(audit, graphiques):
    octets = generer_rapport_docx(audit, R.ContenuRapport(entreprise="N&Co"), graphiques)
    texte = _textes(docx.Document(io.BytesIO(octets)))
    assert MARQUEUR_A_COMPLETER in texte
    assert R.CONSIGNES["strategie"] in texte
    assert compter_a_completer(octets) >= 8               # stratégie, outils, méthodes, recul, conclusion, logo…


def test_un_passage_a_completer_dans_un_texte_redige_est_surligne_et_compte(audit, graphiques):
    """Brouillons de la rédaction assistée : le marqueur peut porter une précision."""
    from dataclasses import replace
    contenu = _complet()
    membre = replace(contenu.membres[0], forces="Rigueur. [À COMPLÉTER : une situation concrète du jeu]")
    contenu = replace(contenu, strategie="Volume. [À COMPLÉTER : la date de la baisse de prix] Puis **qualité**.",
                      membres=(membre,))
    octets = generer_rapport_docx(audit, contenu, graphiques, G.CHARTE_DEFAUT, _logo())

    assert compter_a_completer(octets) == 2
    document = docx.Document(io.BytesIO(octets))
    runs = [r for p in document.paragraphs for r in p.runs]
    runs += [r for t in document.tables for ligne in t.rows for c in ligne.cells for p in c.paragraphs for r in p.runs]
    surlignes = {r.text for r in runs if r.font.highlight_color == docx.enum.text.WD_COLOR_INDEX.YELLOW}
    assert surlignes == {"[À COMPLÉTER : la date de la baisse de prix]", "[À COMPLÉTER : une situation concrète du jeu]"}


def test_sommaire_et_champs_mis_a_jour_a_l_ouverture(audit, graphiques):
    octets = generer_rapport_docx(audit, _complet(), graphiques)
    document = docx.Document(io.BytesIO(octets))
    corps = document.element.body.xml
    assert "TOC \\o" in corps
    assert "updateFields" in document.settings.element.xml
    assert "NUMPAGES" in document.sections[0].footer._element.xml


def test_charte_appliquee_aux_titres(audit):
    charte = G.CHARTES["Sobre (marine & or)"]
    octets = generer_rapport_docx(audit, _complet(), {}, charte)
    style = docx.Document(io.BytesIO(octets)).styles["Heading 1"]
    assert str(style.font.color.rgb) == charte.primaire.lstrip("#").upper()


def test_rapport_sans_donnees_reste_generable():
    vide = R.construire_audit(R.indexer_sources(), "Année 1 - Janvier", "Année 1 - Mars")
    octets = generer_rapport_docx(vide, R.ContenuRapport())
    assert octets[:2] == b"PK"                             # archive .docx valide
    assert compter_a_completer(octets) > 0


def test_resume_markdown(audit):
    texte = rapport_markdown(audit, _complet())
    assert texte.startswith("# Rapport professionnel — Néoprène & Co")
    assert "## Constats" in texte and "| mars A1 | Capacité |" in texte
