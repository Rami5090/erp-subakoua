"""Génération du rapport professionnel au format Word (.docx).

Module PUR : ni Streamlit ni SQL. Il assemble l'audit chiffré
(``rapport_engine``), les graphiques (``rapport_graphiques``), les schémas
(``rapport_schemas``) et les textes rédigés par l'équipe en un document
**éditable** — l'équipe peut ensuite le finaliser dans Word puis l'exporter en
PDF pour le dépôt.

Architecture, calquée sur les attendus des enseignantes (présentation
« Rapport professionnel Subakoua ») et sur le guide du mémoire professionnel
de BUT 3e année (introduction, conclusion, sources, annexes, 4e de couverture,
déclaration d'usage de l'IA) :

* page de garde (logos, titre, équipe, formation, encadrement) et sommaire ;
* **Introduction** : enjeux, présentation de l'entreprise et de l'équipe
  (fiche d'identité, organigramme), problématique et annonce du plan (schéma
  du parcours) ;
* **Partie 1 — travail collectif**, audit stratégique et opérationnel de
  l'année : 1. stratégies, 2. outils et méthodes, 3. résultats décrits puis
  analysés, 4. prise de recul ;
* **Partie 2 — travail individuel** : rôle, outils montés, forces et
  faiblesses, réussites et erreurs, mission spécifique, bilan — par membre ;
* **Conclusion**, **Sources et bibliographie**, **Annexes** (tableaux
  détaillés, traçabilité, limites, méthodes, déclaration d'usage de l'IA,
  glossaire des sigles) et 4e de couverture (résumé, abstract, mots-clés).

Direction artistique : le thème du jeu (combinaisons de plongée). Les titres
suivent un parcours — le cap, l'équipement, la plongée, la remontée — et le
plan se lit comme un profil de plongée. Sobriété : Arial, texte justifié,
tableaux à filets horizontaux sans trame, au plus cinq colonnes dans le corps ;
les tableaux denses sont renvoyés en annexe.

Un champ laissé vide n'est jamais rempli par un texte inventé : il apparaît
surligné en jaune avec la consigne correspondante (« À compléter »), et
``compter_a_completer`` permet de vérifier qu'il n'en reste aucun.
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from constants import PRODUCTS
from rapport_engine import (
    A_COMPLETER,
    ATELIERS,
    BFR,
    CA,
    CAF,
    CAPITAUX_PROPRES,
    CONSIGNES,
    DETTE,
    EFFECTIFS,
    INDICATEURS,
    MASSE_SALARIALE,
    OUTILS_ERP,
    OUTILS_GP,
    PDM,
    PUB_MARQUE,
    PUB_PRODUITS,
    RESULTAT,
    TRESORERIE,
    VENTES_TOTALES,
    AuditAnnuel,
    ContenuRapport,
    Membre,
    _float,
    fmt_compact_euros,
    fmt_euros,
    fmt_euros_signe,
    fmt_nombre,
    fmt_pct,
    fmt_valeur,
    mois_court,
    mois_long,
    perimetre_role,
    sigles_utilises,
)
from rapport_graphiques import CHARTE_DEFAUT, Charte, Graphique, couleur_lisible, texte_sur

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    DOCX_DISPONIBLE = True
except Exception:  # pragma: no cover - dépendance d'environnement
    DOCX_DISPONIBLE = False

MARQUEUR_A_COMPLETER = "[À COMPLÉTER]"
POLICE = "Arial"                 # guide du mémoire : Arial, Times ou Verdana, corps 10 à 12
_LARGEUR_UTILE_CM = 16.0
_GRIS_TEXTE = "52514E"
_GRIS_FILET = "D9D8D3"
PREMIER_NUMERO_INDIVIDUEL = 5    # chapitres 1 à 4 : partie collective

#: Titres du parcours (thème plongée), suivis de l'intitulé attendu par les enseignantes.
TITRES = {
    "strategies": "1. Le cap : les stratégies mises en œuvre",
    "outils": "2. L'équipement : les outils et les méthodes de travail",
    "resultats": "3. La plongée : les résultats de l'année, description et analyse",
    "recul": "4. La remontée : explication et prise de recul",
}
PARTIE_1 = "Partie 1 — Le travail collectif : audit de l'année"
PARTIE_2 = "Partie 2 — Le travail individuel"
MOTS_CLES = ("simulation de gestion ; audit stratégique et opérationnel ; pilotage par les données ; ERP ; "
             "combinaisons de plongée en néoprène")
KEYWORDS = ("business simulation; strategic and operational audit; data-driven management; ERP; "
            "neoprene diving wetsuits")

# Ordre des éléments XML (schéma WordprocessingML) : Word refuse un fichier dont les
# propriétés sont dans le désordre, d'où des insertions à la bonne place.
_APRES_TBLBORDERS = ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription",
                     "w:tblPrChange")
_APRES_TBLCELLMAR = ("w:tblLook", "w:tblCaption", "w:tblDescription", "w:tblPrChange")
_APRES_TCBORDERS = ("w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark",
                    "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge", "w:tcPrChange")
_APRES_SHD = _APRES_TCBORDERS[1:]
_APRES_PBDR = ("w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
               "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd", "w:snapToGrid",
               "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
               "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl", "w:divId",
               "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")


# ---------------------------------------------------------------------------
# Utilitaires XML / mise en forme
# ---------------------------------------------------------------------------
def _hex(couleur: str) -> str:
    return str(couleur).strip().lstrip("#").upper()


def _rgb(couleur: str) -> "RGBColor":
    return RGBColor.from_string(_hex(couleur))


def _eclaircir(couleur: str, part_blanc: float) -> str:
    h = _hex(couleur)
    canaux = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{int(round(c + (255 - c) * part_blanc)):02X}" for c in canaux)


def _remplacer(parent, element, successeurs: Sequence[str]) -> None:
    """Pose ``element`` à sa place dans ``parent`` (en remplaçant un élément de même nom)."""
    for ancien in parent.findall(element.tag):
        parent.remove(ancien)
    parent.insert_element_before(element, *successeurs)


def _ombrer(cellule, couleur: str) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), _hex(couleur))
    _remplacer(cellule._tc.get_or_add_tcPr(), shd, _APRES_SHD)


def _bordure(nom: str, val: str = "single", taille: int = 4, couleur: str = _GRIS_FILET):
    b = OxmlElement(f"w:{nom}")
    b.set(qn("w:val"), val)
    if val != "nil":
        b.set(qn("w:sz"), str(taille))
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), _hex(couleur))
    return b


def _bordures_tableau(table, cotes: Mapping[str, tuple[str, int, str]]) -> None:
    """Bordures du tableau : {côté: (val, taille en 1/8 pt, couleur)} ; un côté absent est sans trait."""
    bordures = OxmlElement("w:tblBorders")
    for cote in ("top", "left", "bottom", "right", "insideH", "insideV"):
        val, taille, couleur = cotes.get(cote, ("nil", 0, ""))
        bordures.append(_bordure(cote, val, taille, couleur or _GRIS_FILET))
    _remplacer(table._tbl.tblPr, bordures, _APRES_TBLBORDERS)


def _sans_bordures(table) -> None:
    _bordures_tableau(table, {})


def _marges_cellules(table, haut: int = 50, bas: int = 50, cote: int = 100) -> None:
    """Marges intérieures des cellules (en vingtièmes de point) : un tableau qui respire."""
    marges = OxmlElement("w:tblCellMar")
    for nom, valeur in (("top", haut), ("left", cote), ("bottom", bas), ("right", cote)):
        el = OxmlElement(f"w:{nom}")
        el.set(qn("w:w"), str(valeur))
        el.set(qn("w:type"), "dxa")
        marges.append(el)
    _remplacer(table._tbl.tblPr, marges, _APRES_TBLCELLMAR)


def _bordures_cellule(cellule, cotes: Mapping[str, tuple[str, int, str]]) -> None:
    bordures = OxmlElement("w:tcBorders")
    for cote in ("top", "left", "bottom", "right"):
        if cote in cotes:
            val, taille, couleur = cotes[cote]
            bordures.append(_bordure(cote, val, taille, couleur))
    _remplacer(cellule._tc.get_or_add_tcPr(), bordures, _APRES_TCBORDERS)


def _repeter_entete(ligne) -> None:
    tr_pr = ligne._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _ne_pas_couper(ligne) -> None:
    tr_pr = ligne._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _filet_bas(p_pr, couleur: str, taille: int = 12, espace: int = 4) -> None:
    """Filet sous un paragraphe (``p_pr`` : propriétés de paragraphe ou de style)."""
    bordure = OxmlElement("w:pBdr")
    bas = OxmlElement("w:bottom")
    bas.set(qn("w:val"), "single")
    bas.set(qn("w:sz"), str(taille))
    bas.set(qn("w:space"), str(espace))
    bas.set(qn("w:color"), _hex(couleur))
    bordure.append(bas)
    _remplacer(p_pr, bordure, _APRES_PBDR)


def _champ(paragraphe, instruction: str, texte_cache: str = "") -> None:
    """Champ Word complexe (PAGE, NUMPAGES, TOC…)."""
    def run_fld(type_: str):
        r = OxmlElement("w:r")
        fld = OxmlElement("w:fldChar")
        fld.set(qn("w:fldCharType"), type_)
        r.append(fld)
        return r

    paragraphe._p.append(run_fld("begin"))
    r_instr = OxmlElement("w:r")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    r_instr.append(instr)
    paragraphe._p.append(r_instr)
    paragraphe._p.append(run_fld("separate"))
    r_txt = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = texte_cache
    r_txt.append(t)
    paragraphe._p.append(r_txt)
    paragraphe._p.append(run_fld("end"))


def _mettre_a_jour_champs_a_l_ouverture(doc) -> None:
    """Word propose de mettre à jour le sommaire et la pagination à l'ouverture."""
    settings = doc.settings.element
    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    settings.append(el)


def _langue_francaise(style) -> None:
    r_pr = style.element.get_or_add_rPr()
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "fr-FR")
    lang.set(qn("w:eastAsia"), "fr-FR")
    r_pr.append(lang)


def _police(style, nom: str = POLICE) -> None:
    style.font.name = nom
    r_pr = style.element.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), nom)
    # Les polices de thème (titres du modèle Word) priment sur les polices nommées : on les retire.
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if fonts.get(qn(attr)) is not None:
            del fonts.attrib[qn(attr)]


def annee_universitaire(jour: date | None = None) -> str:
    """« 2026-2027 » : l'année universitaire commence en septembre."""
    jour = jour or date.today()
    debut = jour.year if jour.month >= 9 else jour.year - 1
    return f"{debut}-{debut + 1}"


# ---------------------------------------------------------------------------
# Rédacteur
# ---------------------------------------------------------------------------
class _Redacteur:
    def __init__(self, charte: Charte):
        self.charte = charte
        self.primaire = couleur_lisible(charte.primaire, minimum=4.5)
        self.secondaire = couleur_lisible(charte.secondaire, minimum=4.5)
        self.accent = couleur_lisible(charte.accent, minimum=3.0)
        self.ecume = _eclaircir(charte.secondaire, 0.9)     # fond clair des encadrés et des tuiles
        self.doc = Document()
        self.n_figure = 0
        self.n_tableau = 0
        self._styles()

    # --- styles -----------------------------------------------------------
    def _styles(self) -> None:
        doc = self.doc
        section = doc.sections[0]
        section.page_height, section.page_width = Cm(29.7), Cm(21.0)
        section.left_margin = section.right_margin = Cm(2.5)
        section.top_margin, section.bottom_margin = Cm(2.2), Cm(2.0)
        section.header_distance, section.footer_distance = Cm(1.0), Cm(1.0)
        normal = doc.styles["Normal"]
        _police(normal)
        _langue_francaise(normal)
        normal.font.size = Pt(11)
        normal.font.color.rgb = RGBColor(0x1F, 0x23, 0x28)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.15
        for nom, taille, couleur, avant, apres in (
            ("Heading 1", 20, self.primaire, 0, 14),
            ("Heading 2", 14, self.primaire, 18, 6),
            ("Heading 3", 11.5, self.secondaire, 10, 4),
        ):
            st = doc.styles[nom]
            _police(st)
            st.font.size = Pt(taille)
            st.font.bold = True
            st.font.italic = False
            st.font.color.rgb = _rgb(couleur)
            st.paragraph_format.space_before = Pt(avant)
            st.paragraph_format.space_after = Pt(apres)
            st.paragraph_format.keep_with_next = True
        doc.styles["Heading 1"].paragraph_format.page_break_before = True
        _filet_bas(doc.styles["Heading 1"].element.get_or_add_pPr(), self.accent, 12, 6)
        legende = doc.styles["Caption"]
        _police(legende)
        legende.font.size = Pt(9)
        legende.font.bold = True
        legende.font.italic = False
        legende.font.color.rgb = _rgb(self.secondaire)
        legende.paragraph_format.space_after = Pt(2)
        for nom in ("List Bullet", "List Number"):
            st = doc.styles[nom]
            _police(st)
            st.font.size = Pt(11)
            st.paragraph_format.space_after = Pt(3)

    # --- briques ------------------------------------------------------------
    def titre(self, texte: str, niveau: int = 1, saut_de_page: bool | None = None):
        p = self.doc.add_heading(texte, level=niveau)
        if saut_de_page is not None:
            p.paragraph_format.page_break_before = saut_de_page
        return p

    def paragraphe(self, texte: str = "", italique: bool = False, taille: float | None = None,
                   couleur: str | None = None, gras: bool = False, aligne: str = "justifie", apres: float | None = None):
        p = self.doc.add_paragraph()
        if aligne == "justifie":
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        elif aligne == "centre":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._runs(p, texte, italique=italique, taille=taille, couleur=couleur, gras=gras)
        if apres is not None:
            p.paragraph_format.space_after = Pt(apres)
        return p

    @staticmethod
    def _runs(p, texte: str, italique: bool = False, taille: float | None = None, couleur: str | None = None,
              gras: bool = False) -> None:
        """Texte avec mise en forme légère : **mot** en gras, *titre* en italique (références) ;
        « [À COMPLÉTER : …] » est surligné."""
        morceaux = re.split(r"(\*\*[^*]+\*\*|\*(?=\S)[^*\n]+?(?<=\S)\*|" + A_COMPLETER.pattern + ")",
                            str(texte or ""), flags=re.IGNORECASE)
        for m in morceaux:
            if not m:
                continue
            est_gras = m.startswith("**") and m.endswith("**") and len(m) > 4
            est_italique = not est_gras and len(m) > 2 and m.startswith("*") and m.endswith("*")
            a_completer = not est_gras and not est_italique and A_COMPLETER.fullmatch(m) is not None
            run = p.add_run(m[2:-2] if est_gras else m[1:-1] if est_italique else m)
            run.bold = gras or est_gras or a_completer
            if a_completer:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            run.italic = italique or est_italique
            if taille:
                run.font.size = Pt(taille)
            if couleur:
                run.font.color.rgb = _rgb(couleur)

    def puces(self, elements: Iterable[str]) -> None:
        for e in elements:
            p = self.doc.add_paragraph(style="List Bullet")
            self._runs(p, e)

    def texte_redige(self, texte: str, consigne: str) -> None:
        """Texte de l'équipe (paragraphes, puces « - ») ou, à défaut, la consigne surlignée."""
        lignes = [l.rstrip() for l in str(texte or "").splitlines()]
        if not any(l.strip() for l in lignes):
            self.a_completer(consigne)
            return
        for ligne in lignes:
            brut = ligne.strip()
            if not brut:
                continue
            if re.match(r"^[-•*]\s+", brut):
                p = self.doc.add_paragraph(style="List Bullet")
                self._runs(p, re.sub(r"^[-•*]\s+", "", brut))
            else:
                self.paragraphe(brut)

    def a_completer(self, consigne: str) -> None:
        p = self.doc.add_paragraph()
        r = p.add_run(MARQUEUR_A_COMPLETER + " ")
        r.bold = True
        r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        r2 = p.add_run(consigne)
        r2.italic = True
        r2.font.highlight_color = WD_COLOR_INDEX.YELLOW

    def note(self, texte: str) -> None:
        self.paragraphe(texte, italique=True, taille=9, couleur=_GRIS_TEXTE, aligne="gauche")

    def espace(self, points: float = 2) -> None:
        self.doc.add_paragraph().paragraph_format.space_after = Pt(points)

    def chapeau(self, texte: str) -> None:
        """Phrase d'ouverture d'une partie : annonce ce qu'elle contient (fil conducteur)."""
        p = self.paragraphe(texte, italique=True, taille=11.5, couleur=self.secondaire, apres=12)
        p.paragraph_format.left_indent = Cm(0.4)

    def encadre(self, titre: str, lignes: Sequence[str] = (), texte: str = "", brut: bool = False) -> None:
        """Encadré « à retenir » : fond clair, filet d'accent à gauche. ``brut`` : texte recopié tel
        quel (copie d'une requête), sans interprétation du gras ni des marqueurs."""
        table = self.doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        _sans_bordures(table)
        _marges_cellules(table, 90, 90, 160)
        cellule = table.rows[0].cells[0]
        cellule.width = Cm(_LARGEUR_UTILE_CM)
        _bordures_cellule(cellule, {"left": ("single", 24, self.accent)})
        _ombrer(cellule, self.ecume)
        p = cellule.paragraphs[0]
        p.paragraph_format.space_after = Pt(3)
        r = p.add_run(titre)
        r.bold = True
        r.font.color.rgb = _rgb(self.primaire)
        if texte and brut:
            for ligne in texte.splitlines():
                q = cellule.add_paragraph()
                q.paragraph_format.space_after = Pt(2)
                rq = q.add_run(ligne)
                rq.font.size = Pt(8.5)
        elif texte:
            q = cellule.add_paragraph()
            q.paragraph_format.space_after = Pt(2)
            self._runs(q, texte)
        for ligne in lignes:
            q = cellule.add_paragraph(style="List Bullet")
            q.paragraph_format.space_after = Pt(2)
            self._runs(q, ligne)
        self.espace(4)

    def tableau(self, titre: str | None, entetes: Sequence[str], lignes: Sequence[Sequence[Any]],
                largeurs: Sequence[float] | None = None, numeriques: Iterable[int] = (),
                taille: float = 9, note: str | None = None, vide: str = "—") -> None:
        """Tableau sobre : en-tête plein, filets horizontaux, sans trame ni trait vertical."""
        if titre:
            self.n_tableau += 1
            leg = self.doc.add_paragraph(style="Caption")
            leg.add_run(f"Tableau {self.n_tableau} — {titre}")
            leg.paragraph_format.keep_with_next = True
        numeriques = set(numeriques)
        table = self.doc.add_table(rows=1, cols=len(entetes))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _bordures_tableau(table, {"top": ("single", 8, self.primaire), "bottom": ("single", 8, self.primaire),
                                  "insideH": ("single", 4, _GRIS_FILET)})
        _marges_cellules(table)
        if largeurs is None:
            largeurs = [_LARGEUR_UTILE_CM / len(entetes)] * len(entetes)
        encre_entete = texte_sur(self.primaire)
        ligne_entete = table.rows[0]
        _repeter_entete(ligne_entete)
        for i, entete in enumerate(entetes):
            c = ligne_entete.cells[i]
            c.width = Cm(largeurs[i])
            _ombrer(c, self.primaire)
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i in numeriques else WD_ALIGN_PARAGRAPH.LEFT
            r = p.add_run(str(entete))
            r.bold = True
            r.font.size = Pt(taille)
            r.font.color.rgb = _rgb(encre_entete)
        for valeurs in lignes:
            row = table.add_row()
            _ne_pas_couper(row)
            for i, valeur in enumerate(valeurs):
                c = row.cells[i]
                c.width = Cm(largeurs[i])
                p = c.paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i in numeriques else WD_ALIGN_PARAGRAPH.LEFT
                texte = vide if valeur is None or valeur == "" else str(valeur)
                gras = texte.startswith("**") and texte.endswith("**")
                r = p.add_run(texte.strip("*") if gras else texte)
                r.bold = gras
                r.font.size = Pt(taille)
        if note:
            self.note(note)
        else:
            self.espace()

    def fiche(self, lignes: Sequence[tuple[str, str]]) -> None:
        """Fiche d'identité : libellé en couleur à gauche, valeur à droite, filets légers."""
        table = self.doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _bordures_tableau(table, {"top": ("single", 8, self.primaire), "bottom": ("single", 8, self.primaire),
                                  "insideH": ("single", 4, _GRIS_FILET)})
        _marges_cellules(table, 60, 60, 100)
        for libelle, valeur in lignes:
            row = table.add_row()
            _ne_pas_couper(row)
            gauche, droite = row.cells
            gauche.width, droite.width = Cm(4.4), Cm(_LARGEUR_UTILE_CM - 4.4)
            for cellule, texte, gras in ((gauche, libelle, True), (droite, valeur, False)):
                p = cellule.paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                r = p.add_run(texte)
                r.bold = gras
                r.font.size = Pt(10)
                if gras:
                    r.font.color.rgb = _rgb(self.primaire)
        self.espace(4)

    def figure(self, g: Graphique | None, tableau_associe: str | None = None) -> None:
        if g is None:
            return
        self.doc.add_picture(io.BytesIO(g.png), width=Cm(_LARGEUR_UTILE_CM))
        image = self.doc.paragraphs[-1]
        image.alignment = WD_ALIGN_PARAGRAPH.CENTER
        image.paragraph_format.keep_with_next = True
        image.paragraph_format.space_after = Pt(2)
        self.n_figure += 1
        leg = self.doc.add_paragraph(style="Caption")
        leg.add_run(f"Figure {self.n_figure} — {g.titre}")
        leg.paragraph_format.keep_with_next = True
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r = p.add_run("Lecture : ")
        r.bold = True
        r.font.size = Pt(9.5)
        r2 = p.add_run(g.commentaire)
        r2.font.size = Pt(9.5)
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.keep_with_next = True
        src = g.source + (f" — valeurs détaillées : {tableau_associe}." if tableau_associe else ".")
        self.note(src)

    def tuiles(self, indicateurs: Sequence[tuple[str, str, str]]) -> None:
        """Rangée d'indicateurs clés : (libellé, valeur, précision), filet de couleur en tête."""
        colonnes = 3
        lignes = (len(indicateurs) + colonnes - 1) // colonnes
        table = self.doc.add_table(rows=lignes, cols=colonnes)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _bordures_tableau(table, {"insideH": ("single", 36, "FFFFFF"), "insideV": ("single", 36, "FFFFFF")})
        _marges_cellules(table, 70, 70, 120)
        for ligne in table.rows:
            _ne_pas_couper(ligne)
        for i, (libelle, valeur, precision) in enumerate(indicateurs):
            c = table.rows[i // colonnes].cells[i % colonnes]
            c.width = Cm(_LARGEUR_UTILE_CM / colonnes)
            _bordures_cellule(c, {"top": ("single", 18, self.secondaire)})
            _ombrer(c, self.ecume)
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(libelle)
            r.font.size = Pt(8.5)
            r.font.color.rgb = _rgb(_GRIS_TEXTE)
            q = c.add_paragraph()
            q.paragraph_format.space_after = Pt(0)
            rv = q.add_run(valeur)
            rv.bold = True
            rv.font.size = Pt(17)
            rv.font.color.rgb = _rgb(self.primaire)
            if precision:
                s = c.add_paragraph()
                s.paragraph_format.space_after = Pt(2)
                rs = s.add_run(precision)
                rs.font.size = Pt(8)
                rs.font.color.rgb = _rgb(_GRIS_TEXTE)
        self.espace(4)

    def deux_colonnes(self, gauche: tuple[str, str, str], droite: tuple[str, str, str],
                      couleurs: tuple[str, str] | None = None) -> None:
        """Deux textes face à face (forces / faiblesses…), chacun sous un filet de couleur."""
        couleurs = couleurs or (self.secondaire, self.accent)
        table = self.doc.add_table(rows=2, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _sans_bordures(table)
        _marges_cellules(table, 40, 40, 110)
        for j, ((titre, texte, consigne), couleur) in enumerate(zip((gauche, droite), couleurs)):
            entete = table.rows[0].cells[j]
            entete.width = Cm(8.0)
            _bordures_cellule(entete, {"bottom": ("single", 18, couleur)})
            pe = entete.paragraphs[0]
            pe.paragraph_format.space_after = Pt(0)
            r = pe.add_run(titre)
            r.bold = True
            r.font.color.rgb = _rgb(couleur_lisible("#" + _hex(couleur), minimum=4.5))
            corps = table.rows[1].cells[j]
            corps.width = Cm(8.0)
            lignes = [l.strip() for l in str(texte or "").splitlines() if l.strip()]
            p = corps.paragraphs[0]
            if not lignes:
                r = p.add_run(MARQUEUR_A_COMPLETER + " ")
                r.bold = True
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW
                r2 = p.add_run(consigne)
                r2.italic = True
                r2.font.size = Pt(9.5)
                r2.font.highlight_color = WD_COLOR_INDEX.YELLOW
                continue
            for k, ligne in enumerate(lignes):
                q = p if k == 0 else corps.add_paragraph()
                q.paragraph_format.space_after = Pt(3)
                self._runs(q, re.sub(r"^[-•*]\s+", "• ", ligne))
        self.espace(4)

    # --- en-têtes, pied de page, sommaire --------------------------------
    def entete_pied(self, entreprise: str, logo: bytes | None) -> None:
        section = self.doc.sections[0]
        section.different_first_page_header_footer = True
        entete = section.header.paragraphs[0]
        entete.text = ""
        if logo:
            try:
                entete.add_run().add_picture(io.BytesIO(logo), height=Cm(0.8))
                entete.add_run("   ")
            except Exception:
                pass
        r = entete.add_run(f"{entreprise} — Rapport professionnel Subakoua")
        r.font.size = Pt(8.5)
        r.font.color.rgb = _rgb(_GRIS_TEXTE)
        _filet_bas(entete._p.get_or_add_pPr(), self.accent, 6)
        pied = section.footer.paragraphs[0]
        pied.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for morceau, instruction in (("Page ", "PAGE"), (" sur ", "NUMPAGES")):
            rp = pied.add_run(morceau)
            rp.font.size = Pt(8.5)
            rp.font.color.rgb = _rgb(_GRIS_TEXTE)
            _champ(pied, instruction, "1")

    def sommaire(self, entrees: Sequence[tuple[int, str]]) -> None:
        p = self.doc.add_paragraph()
        r = p.add_run("Sommaire")
        r.bold = True
        r.font.size = Pt(20)
        r.font.color.rgb = _rgb(self.primaire)
        p.paragraph_format.page_break_before = True
        p.paragraph_format.space_after = Pt(12)
        _filet_bas(p._p.get_or_add_pPr(), self.accent, 12, 6)
        champ = self.doc.add_paragraph()
        _champ(champ, 'TOC \\o "1-2" \\h \\z \\u',
               "Sommaire automatique : dans Word, clic droit ici puis « Mettre à jour les champs ».")
        self.note("Le sommaire et la pagination se mettent à jour à l'ouverture dans Word (accepter la mise à jour "
                  "des champs). Plan du document :")
        for niveau, texte in entrees:
            q = self.doc.add_paragraph()
            q.paragraph_format.left_indent = Cm(0.6 * (niveau - 1))
            q.paragraph_format.space_after = Pt(1)
            rq = q.add_run(texte)
            rq.bold = niveau == 1
            rq.font.size = Pt(10 if niveau == 1 else 9.5)

    def texte_complet(self) -> str:
        """Tout le texte déjà écrit (paragraphes et tableaux) : sert au glossaire des sigles."""
        morceaux = [p.text for p in self.doc.paragraphs]
        for table in self.doc.tables:
            for ligne in table.rows:
                morceaux += [c.text for c in ligne.cells]
        return "\n".join(morceaux)

    def octets(self) -> bytes:
        tampon = io.BytesIO()
        self.doc.save(tampon)
        return tampon.getvalue()


# ---------------------------------------------------------------------------
# Sections du rapport
# ---------------------------------------------------------------------------
def _periode_lisible(audit: AuditAnnuel) -> str:
    return f"de {mois_long(audit.debut)} à {mois_long(audit.fin)}"


def _nom_membre(m: Membre) -> str:
    return f"{m.nom or 'Membre'}" + (f" — {m.role}" if m.role else "")


def _page_de_garde(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport, logo: bytes | None,
                   logo_etablissement: bytes | None) -> None:
    """Page de garde : les éléments exigés par le guide (noms, année, formation, titre, logos, encadrement)."""
    doc = w.doc
    logos = [x for x in (logo, logo_etablissement) if x]
    if logos:
        table = doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        _sans_bordures(table)
        for j, (image, aligne) in enumerate(((logo, WD_ALIGN_PARAGRAPH.LEFT),
                                             (logo_etablissement, WD_ALIGN_PARAGRAPH.RIGHT))):
            cellule = table.rows[0].cells[j]
            cellule.width = Cm(_LARGEUR_UTILE_CM / 2)
            p = cellule.paragraphs[0]
            p.alignment = aligne
            if image:
                try:
                    p.add_run().add_picture(io.BytesIO(image), height=Cm(2.6))
                except Exception:
                    w._runs(p, f"{MARQUEUR_A_COMPLETER} Logo illisible : utilisez une image PNG ou JPG.")
    if not logo:
        w.a_completer("Insérez le logo de l'entreprise (onglet « Identité » de la page Rapport professionnel).")
    for _ in range(2):
        doc.add_paragraph()
    bandeau = doc.add_table(rows=1, cols=1)
    bandeau.alignment = WD_TABLE_ALIGNMENT.CENTER
    _sans_bordures(bandeau)
    cellule = bandeau.rows[0].cells[0]
    cellule.width = Cm(_LARGEUR_UTILE_CM)
    _ombrer(cellule, w.primaire)
    _bordures_cellule(cellule, {"bottom": ("single", 36, w.accent)})
    encre = texte_sur(w.primaire)
    p = cellule.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(20)
    r = p.add_run((c.titre or "Rapport professionnel").upper())
    r.bold = True
    r.font.size = Pt(26)
    r.font.color.rgb = _rgb(encre)
    q = cellule.add_paragraph()
    q.alignment = WD_ALIGN_PARAGRAPH.CENTER
    q.paragraph_format.space_after = Pt(20)
    rq = q.add_run(c.sous_titre or "Audit stratégique et opérationnel de l'année")
    rq.font.size = Pt(14)
    rq.font.color.rgb = _rgb(encre)
    doc.add_paragraph()
    nom = doc.add_paragraph()
    nom.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rn = nom.add_run(c.entreprise or audit.entreprise)
    rn.bold = True
    rn.font.size = Pt(24)
    rn.font.color.rgb = _rgb(w.primaire)
    jeu = doc.add_paragraph()
    jeu.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rj = jeu.add_run(f"Jeu d'entreprise Subakoua — entreprise n° {audit.numero}\nPériode auditée : {_periode_lisible(audit)}")
    rj.font.size = Pt(12)
    rj.font.color.rgb = _rgb(w.secondaire)
    doc.add_paragraph()
    if c.membres:
        eq = doc.add_paragraph()
        eq.alignment = WD_ALIGN_PARAGRAPH.CENTER
        eq.paragraph_format.space_after = Pt(2)
        re_ = eq.add_run(("Présenté par l'équipe " + c.equipe) if c.equipe else "Présenté par")
        re_.bold = True
        re_.font.size = Pt(11.5)
        for m in c.membres:
            lm = doc.add_paragraph()
            lm.alignment = WD_ALIGN_PARAGRAPH.CENTER
            lm.paragraph_format.space_after = Pt(1)
            rm = lm.add_run(m.nom or "Membre")
            rm.font.size = Pt(11)
            if m.role:
                rr = lm.add_run(f" — {m.role}")
                rr.font.size = Pt(11)
                rr.font.color.rgb = _rgb(_GRIS_TEXTE)
    else:
        w.a_completer("Ajoutez les membres de l'équipe et leurs rôles (onglet « Équipe »).")
    for _ in range(2):
        doc.add_paragraph()
    infos = [x for x in (c.formation, c.etablissement, f"Année universitaire {annee_universitaire()}",
                         ("Sous la direction de : " + c.enseignants) if c.enseignants else "",
                         c.date or date.today().strftime("%d/%m/%Y")) if x]
    pied = doc.add_paragraph()
    pied.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rp = pied.add_run("\n".join(infos))
    rp.font.size = Pt(10)
    rp.font.color.rgb = _rgb(_GRIS_TEXTE)


def _plan_annonce(c: ContenuRapport) -> str:
    """Annonce du plan, écrite d'après la structure réelle du document (aucun texte à rédiger)."""
    n = len(c.membres)
    noms = ", ".join(m.nom for m in c.membres if m.nom)
    chapitres = (f"au chapitre {PREMIER_NUMERO_INDIVIDUEL}" if n == 1 else
                 f"aux chapitres {PREMIER_NUMERO_INDIVIDUEL} à {PREMIER_NUMERO_INDIVIDUEL + n - 1}") if n else ""
    individuel = (f"La seconde partie présente la mission de chaque membre de l'équipe{' (' + noms + ')' if noms else ''}"
                  f"{' ' + chapitres if chapitres else ''}.")
    return ("Pour y répondre, la première partie restitue notre travail collectif sous la forme d'un audit de "
            "l'année : les stratégies mises en œuvre (chapitre 1), les outils et les méthodes de travail (chapitre 2), "
            "les résultats décrits puis analysés (chapitre 3) et notre prise de recul (chapitre 4). "
            f"{individuel} La conclusion dresse le bilan et ouvre des perspectives.")


def _introduction(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport, schemas: Mapping[str, Graphique]) -> None:
    s = audit.synthese
    w.titre("Introduction", 1)
    w.titre("Enjeux de l'exercice", 3)
    w.texte_redige(c.introduction, CONSIGNES["introduction"])
    w.titre("L'entreprise et l'équipe", 3)
    w.texte_redige(c.presentation, CONSIGNES["presentation"])
    lignes = [
        ("Entreprise", f"{c.entreprise or audit.entreprise} (n° {audit.numero} du jeu Subakoua)"),
        ("Activité", "Conception, fabrication et vente de combinaisons de plongée en néoprène"),
        ("Gamme", ", ".join(PRODUCTS)),
        ("Outil industriel", " → ".join(ATELIERS) + (
            " ; parc : " + ", ".join(f"{a} {fmt_nombre(s.machines_debut.get(a))} → {fmt_nombre(s.machines_fin.get(a))}"
                                    for a in ATELIERS if a in s.machines_debut or a in s.machines_fin)
            if s.machines_debut or s.machines_fin else "")),
    ]
    if audit.concurrence:
        lignes.append(("Marché", f"{len(audit.concurrence)} entreprises observées dans le benchmark concurrentiel"))
    if s.effectifs_debut is not None:
        lignes.append(("Effectif", f"{fmt_nombre(s.effectifs_debut)} → {fmt_nombre(s.effectifs_fin)} personnes"))
    if s.ca_total is not None:
        lignes.append(("Chiffre d'affaires", f"{fmt_euros(s.ca_total)} sur la période" + (
            f" ; {fmt_pct(s.croissance_ca, 1, signe=True)} du premier au dernier mois" if s.croissance_ca is not None else "")))
    lignes.append(("Période auditée", f"{_periode_lisible(audit)} ({s.n_mois} mois)"))
    w.fiche(lignes)
    if "organigramme" in schemas:
        w.figure(schemas["organigramme"])
    elif c.membres:
        w.tableau("Répartition des rôles dans l'équipe", ["Membre", "Rôle", "Missions principales"],
                  [(m.nom or "—", m.role or "—", " ".join(m.missions.split()) or "—") for m in c.membres],
                  largeurs=[4.0, 4.5, 7.5])
    w.titre("Problématique et annonce du plan", 3)
    if c.problematique.strip():
        w.encadre("Problématique", texte=c.problematique.strip())
    else:
        w.a_completer(CONSIGNES["problematique"])
    w.paragraphe(_plan_annonce(c))
    w.figure(schemas.get("parcours"))


def _strategies(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport, graphiques: Mapping[str, Graphique]) -> None:
    w.titre(TITRES["strategies"], 2, saut_de_page=False)
    w.texte_redige(c.strategie, CONSIGNES["strategie"])
    if audit.lecture_strategique:
        w.encadre("Lecture de l'ERP, à partir des décisions réellement prises — à confirmer ou nuancer par l'équipe",
                  list(audit.lecture_strategique))
    produits = [l for l in audit.produits if l.prix_fin is not None]
    if produits or "positionnement" in graphiques:
        w.titre("Positionnement prix face au marché", 3)
        w.figure(graphiques.get("positionnement"))
        if produits:
            w.tableau("Prix de vente décidés et prix du marché (€ HT)",
                      ["Produit", "Prix en début de période", "Prix en fin de période", "Prix du marché", "Indice prix"],
                      [(l.produit, fmt_euros(l.prix_debut), fmt_euros(l.prix_fin), fmt_euros(l.prix_marche),
                        fmt_nombre(l.indice_prix * 100, 0) if l.indice_prix is not None else "—") for l in produits],
                      largeurs=[3.2, 3.4, 3.4, 3.2, 2.8], numeriques=range(1, 5),
                      note="Indice prix = prix de vente / prix du marché × 100 (100 = aligné sur le marché).")
    if audit.chronologie:
        w.titre("Les décisions clés de l'année", 3)
        w.tableau("Chronologie des décisions clés (déduites des données saisies et scrapées)",
                  ["Mois", "Domaine", "Décision"],
                  [(mois_court(e.periode), e.domaine, e.texte) for e in audit.chronologie],
                  largeurs=[2.2, 3.0, 10.8])


def _outils_methodes(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport, schemas: Mapping[str, Graphique]) -> None:
    w.titre(TITRES["outils"], 2)
    w.titre("2.1 Les outils", 3)
    w.texte_redige(c.outils, CONSIGNES["outils"])
    w.figure(schemas.get("chaine_outils"))
    retenus = [o for o in OUTILS_ERP if o.nom in set(c.outils_erp)]
    if retenus:
        w.tableau("Ce que chaque outil de l'ERP apporte à la décision", ["Outil", "Besoin couvert", "Apport à la décision"],
                  [(f"**{o.nom}**", o.besoin, o.apport) for o in retenus], largeurs=[4.4, 6.0, 5.6])
    w.titre("2.2 Les méthodes de travail et la gestion de projet", 3)
    w.texte_redige(c.methodes, CONSIGNES["methodes"])
    w.figure(schemas.get("cycle"))
    if c.outils_gp:
        w.tableau("Outils et rituels de gestion de projet utilisés", ["Outil / méthode", "Usage dans l'équipe"],
                  [(nom, OUTILS_GP.get(nom, "")) for nom in c.outils_gp], largeurs=[5.5, 10.5])


def _resultats(w: _Redacteur, audit: AuditAnnuel, graphiques: Mapping[str, Graphique]) -> None:
    s = audit.synthese
    w.titre(TITRES["resultats"], 2)
    w.paragraphe(f"Ce chapitre décrit puis analyse l'activité de l'entreprise {_periode_lisible(audit)}. Chaque chiffre "
                 "provient des données du jeu consolidées par l'ERP (sources en annexe B) ; un mois sans donnée n'est "
                 "jamais estimé. Les tableaux mensuels détaillés figurent en annexe A.")
    w.titre("L'année en chiffres", 3)
    w.tuiles([
        ("Chiffre d'affaires de la période", fmt_compact_euros(s.ca_total), f"{s.ca_mois} mois renseigné(s)"),
        ("Résultat net cumulé", fmt_compact_euros(s.resultat_total),
         f"marge nette {fmt_pct(s.marge_nette)}" if s.marge_nette is not None else ""),
        ("Évolution du CA mensuel", fmt_pct(s.croissance_ca, 1, signe=True) if s.croissance_ca is not None else "—",
         "premier → dernier mois renseigné"),
        ("Trésorerie de clôture", fmt_compact_euros(s.tresorerie_cloture[1]) if s.tresorerie_cloture else "—",
         f"fin {mois_court(s.tresorerie_cloture[0])}" if s.tresorerie_cloture else ""),
        ("Part de marché finale", fmt_pct(s.pdm_fin[1], 2) if s.pdm_fin else "—",
         (f"{'+' if s.pdm_fin[1] >= s.pdm_debut[1] else ''}{fmt_nombre(s.pdm_fin[1] - s.pdm_debut[1], 2)} pt sur la période")
         if s.pdm_fin and s.pdm_debut else ""),
        ("Unités vendues", fmt_nombre(s.ventes_totales) if s.ventes_totales is not None else "—",
         f"{s.ventes_mois} mois renseigné(s)"),
    ])
    # --- Commercial -----------------------------------------------------
    w.titre("3.1 La performance commerciale", 3)
    w.figure(graphiques.get("ca_mensuel"), "annexe A")
    w.figure(graphiques.get("ca_cumule"))
    w.figure(graphiques.get("ventes_produits"), "annexe A")
    w.figure(graphiques.get("mix_produits"), "tableau des produits ci-dessous")
    produits = [l for l in audit.produits if l.ventes > 0 or l.prix_fin is not None]
    if produits:
        w.tableau("Analyse par produit sur la période",
                  ["Produit", "Unités vendues", "Part des volumes", "Part du CA", "Marge unitaire"],
                  [(l.produit, fmt_nombre(l.ventes), fmt_pct(l.part_volumes), fmt_pct(l.part_ca),
                    fmt_euros(l.marge_unitaire)) for l in produits],
                  largeurs=[3.6, 3.1, 3.1, 3.1, 3.1], numeriques=range(1, 5),
                  note="Part du CA : CA estimé au prix catalogue décidé (hors remises de canal). Marge unitaire = dernier "
                       "prix − dernier coût variable connu.")
    w.figure(graphiques.get("decomposition"))
    w.figure(graphiques.get("pdm"), "annexe A")
    w.figure(graphiques.get("concurrence"), "annexe A")
    _analyse_theme(w, audit, "Commercial")
    # --- Financier ------------------------------------------------------
    w.titre("3.2 La performance financière", 3)
    w.figure(graphiques.get("resultat"), "annexe A")
    w.figure(graphiques.get("tresorerie"), "annexe A")
    _analyse_theme(w, audit, "Financier")
    # --- Industriel -----------------------------------------------------
    w.titre("3.3 La performance industrielle et logistique", 3)
    w.figure(graphiques.get("ateliers"))
    lignes = []
    for a in ATELIERS:
        if a in s.machines_debut or a in s.machines_fin or a in s.utilisation_moyenne:
            maxi = s.utilisation_max.get(a)
            lignes.append((a, f"{fmt_nombre(s.machines_debut.get(a))} → {fmt_nombre(s.machines_fin.get(a))}",
                           f"+{fmt_nombre(s.acquisitions_total.get(a, 0))} / −{fmt_nombre(s.cessions_total.get(a, 0))}",
                           fmt_pct((s.utilisation_moyenne.get(a) or 0) * 100, 0) if a in s.utilisation_moyenne else "—",
                           f"{fmt_pct(maxi[1] * 100, 0)} ({mois_court(maxi[0])})" if maxi else "—"))
    if lignes:
        w.tableau("Capacité de production par atelier",
                  ["Atelier", "Machines (début → fin)", "Achats / cessions", "Utilisation moyenne", "Pic"],
                  lignes, largeurs=[3.0, 3.6, 3.2, 3.0, 3.2], numeriques=range(1, 5))
    _analyse_theme(w, audit, "Industriel")
    # --- RH ---------------------------------------------------------------
    w.titre("3.4 Les ressources humaines", 3)
    if s.effectifs_debut is not None or audit.serie(MASSE_SALARIALE):
        masse = audit.serie(MASSE_SALARIALE)
        morceaux = []
        if s.effectifs_debut is not None:
            morceaux.append(f"l'effectif passe de {fmt_nombre(s.effectifs_debut)} à {fmt_nombre(s.effectifs_fin)} personnes")
        if masse:
            morceaux.append(f"la masse salariale brute mensuelle de {fmt_euros(masse[0][1])} ({mois_court(masse[0][0])}) "
                            f"à {fmt_euros(masse[-1][1])} ({mois_court(masse[-1][0])})")
        w.paragraphe("Sur la période, " + " et ".join(morceaux) + ". Le détail mensuel (effectif, masse salariale, "
                     "scores RH) figure en annexe A.")
    else:
        w.note("Aucune donnée RH disponible pour la période (décisions RH ni audits du service).")
    _analyse_theme(w, audit, "Ressources humaines")
    # --- Bilan ----------------------------------------------------------
    w.titre("3.5 Le bilan de l'audit : forces et points de vigilance", 3)
    forces = [k.texte for k in audit.constats if k.nature == "force"]
    faibles = [k.texte for k in audit.constats if k.nature in ("faiblesse", "vigilance")]
    if forces or faibles:
        w.deux_colonnes(("Forces", "\n".join(f"- {f}" for f in forces), "Aucune force mesurée."),
                        ("Faiblesses et points de vigilance", "\n".join(f"- {f}" for f in faibles),
                         "Aucun point de vigilance mesuré."))
    else:
        w.note("Données insuffisantes pour établir les constats de l'audit.")


def _analyse_theme(w: _Redacteur, audit: AuditAnnuel, theme: str) -> None:
    constats = [k for k in audit.constats if k.theme == theme]
    if not constats:
        return
    w.paragraphe("**Ce que montrent les chiffres**", aligne="gauche", apres=2)
    for k in constats:
        p = w.doc.add_paragraph(style="List Bullet")
        w._runs(p, k.texte)
        if k.analyse:
            r = p.add_run(" " + k.analyse)
            r.italic = True
            r.font.color.rgb = _rgb(_GRIS_TEXTE)


def _prise_de_recul(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre(TITRES["recul"], 2)
    w.texte_redige(c.prise_de_recul, CONSIGNES["prise_de_recul"])
    pistes = [k.analyse for k in audit.constats if k.analyse and k.nature in ("faiblesse", "vigilance")][:5]
    if pistes:
        w.encadre("Questions de prise de recul suggérées par l'audit", pistes)


def _partie_individuelle(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre(PARTIE_2, 1)
    w.chapeau("Chaque membre présente sa mission dans la SAE : son rôle dans l'équipe, les outils qu'il a montés, ses "
              "forces et ses faiblesses, ses réussites et ses erreurs, et sa mission spécifique.")
    if not c.membres:
        w.a_completer("Ajoutez les membres de l'équipe (onglet « Équipe ») : une section individuelle est créée "
                      "pour chacun.")
        return
    for i, m in enumerate(c.membres, start=PREMIER_NUMERO_INDIVIDUEL):
        _membre(w, audit, m, i)


def _membre(w: _Redacteur, audit: AuditAnnuel, m: Membre, numero: int) -> None:
    w.titre(f"{numero}. {_nom_membre(m)}", 2, saut_de_page=numero > PREMIER_NUMERO_INDIVIDUEL)
    w.titre("Mon rôle dans l'équipe", 3)
    w.texte_redige("\n".join(x for x in (m.role and f"**Fonction :** {m.role}", " ".join(m.missions.split())) if x),
                   CONSIGNES["role"])
    w.titre("Les outils que j'ai montés", 3)
    w.texte_redige(m.outils, CONSIGNES["outils_membre"])
    retenus = [o for o in OUTILS_ERP if o.nom in set(m.outils_erp)]
    if retenus:
        w.tableau(None, ["Outil de l'ERP", "Ce qu'il apporte"], [(o.nom, o.apport) for o in retenus],
                  largeurs=[6.0, 10.0])
    w.titre("Mes forces et mes faiblesses", 3)
    w.deux_colonnes(("Forces", m.forces, CONSIGNES["forces"]), ("Faiblesses", m.faiblesses, CONSIGNES["faiblesses"]))
    w.titre("Mes réussites et mes erreurs", 3)
    w.deux_colonnes(("Réussites", m.reussites, CONSIGNES["reussites"]), ("Erreurs", m.erreurs, CONSIGNES["erreurs"]))
    w.titre("Ma mission spécifique", 3)
    w.texte_redige(m.mission_specifique, CONSIGNES["mission_specifique"])
    lignes = []
    for cle in perimetre_role(m.role):
        serie = audit.serie(cle)
        if serie:
            (p0, v0), (p1, v1) = serie[0], serie[-1]
            lignes.append((INDICATEURS[cle][0], f"{fmt_valeur(cle, v0)} ({mois_court(p0)})",
                           f"{fmt_valeur(cle, v1)} ({mois_court(p1)})"))
    if lignes:
        w.titre("Mon périmètre en chiffres", 3)
        w.tableau(None, ["Indicateur", "Début de période", "Fin de période"], lignes, largeurs=[6.0, 5.0, 5.0],
                  numeriques=[1, 2])
    w.titre("Bilan personnel", 3)
    w.texte_redige(m.bilan, CONSIGNES["bilan"])


def _conclusion(w: _Redacteur, c: ContenuRapport) -> None:
    w.titre("Conclusion", 1)
    w.texte_redige(c.conclusion, CONSIGNES["conclusion"])


def _modeles_ia(c: ContenuRapport) -> list[str]:
    """Noms des modèles déclarés (« Claude Opus 5.5 »…) d'après les lignes d'usage enregistrées."""
    vus: list[str] = []
    for ligne in c.usages_ia:
        nom = ligne.split(" (", 1)[0].split(" — ", 1)[0].strip()
        if nom and nom not in vus:
            vus.append(nom)
    return vus


def _sources(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("Sources et bibliographie", 1)
    w.titre("Données de l'entreprise (sources internes)", 3)
    sources = sorted({src for liste in audit.sources_par_indicateur().values() for src in liste})
    w.puces([f"Jeu d'entreprise Subakoua — {s}" for s in sources] or
            ["Jeu d'entreprise Subakoua — dossier comptable, tableaux de bord et études de la période auditée."])
    w.puces(["ERP Subakoua de l'équipe : consolidation des données, calculs et graphiques de ce rapport (méthodes en "
             "annexe D)."])
    w.titre("Ouvrage", 3)
    w.puces(["Porter M. E. (1980), *Competitive Strategy: Techniques for Analyzing Industries and Competitors*, "
             "New York, Free Press."])
    if c.usages_ia:
        w.titre("Intelligence artificielle générative", 3)
        annee = date.today().year
        references = []
        for nom in _modeles_ia(c):
            version = nom[len("Claude "):].strip() if nom.startswith("Claude ") else ""
            references.append(f"Anthropic. ({annee}). *Claude* ({version or 'version à préciser'}) [Logiciel]. "
                              "https://claude.ai/")
        w.puces(references or [f"Anthropic. ({annee}). *Claude* (version à préciser) [Logiciel]. https://claude.ai/"])
        w.note("Format APA demandé par le guide du mémoire ; l'année est celle de l'utilisation. Usage détaillé en "
               "annexe E.")


def _annexes(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("Annexes", 1)
    w.note("A. Tableaux mensuels détaillés — B. Sources des données et traçabilité — C. Données manquantes et "
           "limites de l'audit — D. Méthodes de calcul — E. Déclaration de l'utilisation de l'IA générative — "
           "F. Glossaire des sigles.")
    w.titre("A. Tableaux mensuels détaillés", 2, saut_de_page=False)
    lignes = [(mois_court(m.periode), fmt_nombre(m.v(VENTES_TOTALES)), fmt_euros(m.v(CA)), fmt_pct(m.v(PDM), 2),
               fmt_euros((m.v(PUB_MARQUE) or 0) + (m.v(PUB_PRODUITS) or 0)) if (PUB_MARQUE in m.valeurs or PUB_PRODUITS in m.valeurs) else "—",
               fmt_euros(m.v(BFR)), fmt_nombre(m.v(EFFECTIFS)))
              for m in audit.mois]
    w.tableau("Activité mensuelle", ["Mois", "Unités vendues", "CA", "Part de marché", "Publicité", "BFR", "Effectif"],
              lignes, largeurs=[1.7, 2.3, 2.7, 2.3, 2.5, 2.6, 1.9], numeriques=range(1, 7), taille=8)
    financiers = [(mois_court(m.periode), fmt_euros(m.v(CA)), fmt_euros(m.v(RESULTAT)), fmt_euros(m.v(CAF)),
                   fmt_euros(m.v(TRESORERIE)), fmt_euros(m.v(CAPITAUX_PROPRES)), fmt_euros(m.v(DETTE)))
                  for m in audit.mois if any(k in m.valeurs for k in (CA, RESULTAT, TRESORERIE, CAPITAUX_PROPRES, DETTE))]
    if financiers:
        w.tableau("Indicateurs financiers mensuels (€)",
                  ["Mois", "CA", "Résultat net", "CAF", "Trésorerie", "Capitaux propres", "Dette bancaire"],
                  financiers, largeurs=[1.6, 2.5, 2.3, 2.2, 2.5, 2.5, 2.4], numeriques=range(1, 7), taille=8)
    produits_mois = [(mois_court(m.periode), *[fmt_nombre(m.ventes.get(p)) if p in m.ventes else "—" for p in PRODUCTS])
                     for m in audit.mois]
    w.tableau("Ventes mensuelles par produit (unités)", ["Mois", *PRODUCTS], produits_mois,
              largeurs=[2.0, 2.8, 2.8, 2.8, 2.8, 2.8], numeriques=range(1, 6), taille=8)
    prix_mois = [(mois_court(m.periode), *[fmt_euros(m.prix.get(p)) if p in m.prix else "—" for p in PRODUCTS])
                 for m in audit.mois if m.prix]
    if prix_mois:
        w.tableau("Prix de vente décidés (€ HT)", ["Mois", *PRODUCTS], prix_mois,
                  largeurs=[2.0, 2.8, 2.8, 2.8, 2.8, 2.8], numeriques=range(1, 6), taille=8)
    if audit.concurrence:
        w.tableau(f"Benchmark concurrentiel ({mois_court(audit.concurrence_periode or audit.fin)})",
                  ["Rang", "Entreprise", "Part de marché", "Ventes (u)", "Prix moyen"],
                  [(i + 1, (f"**Entreprise {int(_float(r.get('entreprise')) or 0)} (nous)**"
                            if _float(r.get("entreprise")) == float(audit.numero)
                            else f"Entreprise {int(_float(r.get('entreprise')) or 0)}"),
                    fmt_pct(r.get("part_marche"), 2), fmt_nombre(r.get("ventes")), fmt_euros(r.get("prix")))
                   for i, r in enumerate(audit.concurrence)],
                  largeurs=[1.6, 5.0, 3.2, 3.0, 3.2], numeriques=[0, 2, 3, 4], taille=8)
    d = audit.decomposition
    if d is not None and abs(d.ecart) >= 1:
        w.tableau("Décomposition de l'évolution du CA théorique", ["Composante", "Montant", "Lecture"],
                  [(f"CA théorique {mois_court(d.periode_a)}", fmt_euros(d.ca_a), "Point de départ"),
                   ("Effet volume", fmt_euros_signe(d.effet_volume), "Plus ou moins d'unités vendues, au prix moyen initial"),
                   ("Effet mix", fmt_euros_signe(d.effet_mix), "Déformation des ventes vers des produits plus ou moins chers"),
                   ("Effet prix", fmt_euros_signe(d.effet_prix), "Changements de prix appliqués aux volumes finaux"),
                   (f"**CA théorique {mois_court(d.periode_b)}**", f"**{fmt_euros(d.ca_b)}**", "Point d'arrivée")],
                  largeurs=[4.5, 3.2, 8.3], numeriques=[1], taille=8)
    poles: list[str] = []
    for m in audit.mois:
        for pole in m.scores_rh:
            if pole not in poles:
                poles.append(pole)
    rh = [(mois_court(m.periode), fmt_nombre(m.v(EFFECTIFS)), fmt_euros(m.v(MASSE_SALARIALE)),
           *[fmt_nombre(m.scores_rh.get(p)) if p in m.scores_rh else "—" for p in poles])
          for m in audit.mois if EFFECTIFS in m.valeurs or m.scores_rh]
    if rh:
        reste = _LARGEUR_UTILE_CM - 2.0 - 2.0 - 3.8
        abreges = {"Production": "Prod.", "Approvisionnement": "Appro.", "Administration": "Admin."}
        w.tableau("Effectifs, masse salariale et scores RH (sur 100)",
                  ["Mois", "Effectif", "Masse salariale brute", *[f"Score {abreges.get(p, p)}" for p in poles]], rh,
                  largeurs=[2.0, 2.0, 3.8, *([reste / len(poles)] * len(poles))] if poles else [2.0, 2.0, 12.0],
                  numeriques=range(1, 3 + len(poles)), taille=8)
    w.titre("B. Sources des données et traçabilité", 2)
    libelles = {**{k: v[0] for k, v in INDICATEURS.items()}, "ventes": "Ventes par produit", "prix": "Prix de vente",
                "machines": "Parc machines", "utilisation": "Utilisation des ateliers", "scores_rh": "Scores RH",
                "couts": "Coûts variables unitaires", "prix_marche": "Prix du marché"}
    sources = audit.sources_par_indicateur()
    w.tableau("Origine de chaque donnée", ["Donnée", "Source(s) utilisée(s)"],
              [(libelles.get(k, k), " ; ".join(v)) for k, v in sources.items()], largeurs=[5.0, 11.0], taille=8,
              note="Ordre de priorité : dossier comptable et études du jeu, puis tableau de bord, puis saisies "
                   "manuelles de l'ERP. Aucune valeur n'est extrapolée.")
    w.titre("C. Données manquantes et limites de l'audit", 2)
    if audit.manques:
        w.tableau("Données incomplètes sur la période", ["Donnée", "Mois sans donnée", "Conséquence", "Étude à synchroniser"],
                  [(m.element, ", ".join(mois_court(p) for p in m.periodes), m.consequence,
                    " ou ".join(f"{lib} ({'gratuite' if prix == 0 else f'{prix:g} €'})" for _sid, lib, prix in m.etudes) or "—")
                   for m in audit.manques], largeurs=[3.2, 4.0, 4.6, 4.2], taille=8)
    else:
        w.paragraphe("Toutes les données clés sont disponibles sur la période auditée.")
    w.titre("D. Méthodes de calcul", 2)
    w.puces([
        "**Chiffre d'affaires, résultat, trésorerie** : valeurs publiées par le jeu (compte de résultat, relevé "
        "bancaire, tableau de bord), sommées sur les mois renseignés.",
        "**Marge nette** = résultat net cumulé / CA cumulé, sur les mois où les deux sont connus.",
        "**CAF** = résultat net + dotations aux amortissements et dépréciations.",
        "**Part de marché** : part globale publiée par le tableau de bord (en %).",
        "**Décomposition prix / volume / mix** : effet volume = (Q₂ − Q₁) × prix moyen₁ ; effet mix = Σ p₁·q₂ − "
        "Q₂ × prix moyen₁ ; effet prix = Σ (p₂ − p₁)·q₂. La somme des trois effets est égale à l'écart de CA théorique.",
        "**Indice de prix** = prix de vente décidé / prix du marché publié × 100.",
        "**Carte de positionnement** : prix moyen et part de marché publiés par la veille concurrentielle ; les "
        "pointillés marquent les médianes des entreprises observées.",
        "**Taux d'utilisation d'un atelier** = minutes consommées / minutes disponibles (étude « Ateliers »).",
        "**Positionnement stratégique** : lecture inspirée des stratégies génériques de M. E. Porter "
        "(Competitive Strategy, 1980) — domination par les coûts, différenciation, focalisation.",
    ])
    _declaration_ia(w, c)
    w.titre("F. Glossaire des sigles", 2)
    sigles = sigles_utilises(w.texte_complet())
    if sigles:
        w.tableau(None, ["Sigle", "Signification"], sigles, largeurs=[3.0, 13.0])
    else:
        w.paragraphe("Aucun sigle à développer.")


#: Natures d'usage du formulaire de déclaration du guide (annexe du mémoire professionnel).
NATURES_USAGE_IA = (
    "Aide à la recherche, exploration d'un sujet, comprendre un concept",
    "Génération d'un plan pour aider à structurer un texte",
    "Génération de texte partiel (ex. : phrases complètes, paragraphes, résumés)",
    "Traduction d'un texte",
    "Reformulation ou amélioration de mon texte et révision linguistique",
    "Création d'images, graphiques, illustrations",
    "Génération de sons, vidéos ou autre contenu multimédia",
    "Analyse, calculs mathématiques et visualisation de données",
    "Production de code informatique",
    "Autre(s) utilisation(s)",
)


def _declaration_ia(w: _Redacteur, c: ContenuRapport) -> None:
    """Annexe E : formulaire de déclaration de l'IA générative, pré-rempli par la rédaction assistée."""
    from rapport_redaction import SYSTEME   # consignes réellement envoyées au modèle

    utilise = bool(c.usages_ia)
    case = {True: "☒", False: "☐"}
    w.titre("E. Déclaration de l'utilisation de l'intelligence artificielle générative", 2)
    w.note("Formulaire du guide du mémoire professionnel : chaque membre le complète, coche les vérifications et "
           "le signe. Les lignes pré-remplies viennent de la rédaction assistée de l'ERP.")
    w.paragraphe("**Déclaration**", aligne="gauche", apres=2)
    w.paragraphe(f"{case[utilise]} J'ai utilisé un outil d'IA générative (ex. ChatGPT, Gemini, Copilot, Claude, etc.) "
                 "dans le cadre de ce travail.", aligne="gauche")
    w.paragraphe("**Vérification**", aligne="gauche", apres=2)
    for ligne in ("J'ai vérifié l'exactitude des informations fournies par l'IA.",
                  "Dans le corps de mon travail, j'ai mentionné ou cité mon utilisation de l'IAg conformément aux "
                  "consignes académiques, lorsque requis.",
                  "Dans le cadre de mon utilisation de l'IAg pour ce travail, j'ai respecté la confidentialité et les "
                  "droits d'auteur."):
        w.paragraphe(f"☐ {ligne}", aligne="gauche", apres=2)
    texte_partiel = NATURES_USAGE_IA[2]
    outils = ", ".join(_modeles_ia(c))
    lignes = []
    for nature in NATURES_USAGE_IA:
        if nature == texte_partiel and utilise:
            lignes.append((nature, "☒", "☐", outils,
                           "Brouillons des textes du rapport (dont l'abstract) rédigés à partir du contexte écrit par "
                           "l'équipe et des chiffres de l'audit : " + " ; ".join(c.usages_ia)))
        else:
            lignes.append((nature, "☐", "☐", "", ""))
    w.tableau("Documentation de l'usage de l'IA générative",
              ["Nature de l'usage", "Utilisé", "Modifié ou adapté avant usage", "Outil d'IAg", "Information sur l'usage"],
              lignes, largeurs=[4.4, 1.5, 2.3, 2.6, 5.2], taille=8, vide="")
    if utilise:
        w.paragraphe("**Mise en contexte**", aligne="gauche", apres=2)
        w.paragraphe("L'IA générative a servi à rédiger un premier jet des textes du rapport, à partir d'un paragraphe "
                     "de contexte écrit par l'équipe et des chiffres calculés par l'ERP. Garde-fous : le modèle ne "
                     "recopie que des chiffres présents dans les sources, chaque nombre produit est vérifié "
                     "automatiquement, et une information absente est signalée « à compléter » plutôt qu'inventée.")
        w.paragraphe("**Copie des requêtes**", aligne="gauche", apres=2)
        w.encadre("Consignes données au modèle", texte=SYSTEME.replace("\n\n", "\n"), brut=True)
        w.encadre("Contexte rédigé par l'équipe", texte=c.contexte.strip() or "(aucun contexte enregistré)", brut=True)
        w.note("Le reste de la requête — chiffres de l'audit, paramètres du rapport et consigne de chaque champ — est "
               "généré par l'ERP à partir des données présentées dans ce rapport (fichier « demande_redaction_rapport.txt » "
               "téléchargeable depuis l'ERP). Les réponses obtenues ont été intégrées aux champs du rapport ; leur "
               "relecture et leurs modifications sont déclarées dans le tableau ci-dessus.")
    w.paragraphe("**Signatures**", aligne="gauche", apres=2)
    w.tableau(None, ["Membre", "Date", "Signature"],
              [(m.nom or "", "", "") for m in c.membres] or [("", "", "")], largeurs=[6.0, 3.5, 6.5], vide="")


def _quatrieme_de_couverture(w: _Redacteur, c: ContenuRapport) -> None:
    """Dos du rapport (guide) : résumé de 8 lignes au plus, en français et en anglais, et mots-clés."""
    p = w.doc.add_paragraph()
    p.paragraph_format.page_break_before = True
    for titre, texte, consigne, cles, libelle in (
        ("Résumé", c.synthese, CONSIGNES["synthese"], MOTS_CLES, "Mots-clés : "),
        ("Abstract", c.abstract, CONSIGNES["abstract"], KEYWORDS, "Keywords: "),
    ):
        t = w.doc.add_paragraph()
        rt = t.add_run(titre)
        rt.bold = True
        rt.font.size = Pt(14)
        rt.font.color.rgb = _rgb(w.primaire)
        t.paragraph_format.space_before = Pt(18)
        _filet_bas(t._p.get_or_add_pPr(), w.accent, 8, 4)
        w.texte_redige(texte, consigne)
        q = w.paragraphe(f"**{libelle}**{cles}", taille=9.5, couleur=_GRIS_TEXTE, aligne="gauche")
        q.paragraph_format.space_before = Pt(4)


def generer_rapport_docx(
    audit: AuditAnnuel,
    contenu: ContenuRapport,
    graphiques: Mapping[str, Graphique] | None = None,
    charte: Charte = CHARTE_DEFAUT,
    logo: bytes | None = None,
    logo_etablissement: bytes | None = None,
) -> bytes:
    """Le rapport complet, au format .docx (octets prêts au téléchargement)."""
    if not DOCX_DISPONIBLE:
        raise RuntimeError("La génération Word nécessite « python-docx » : pip install -r requirements.txt.")
    from rapport_schemas import schemas_rapport

    graphiques = graphiques or {}
    entreprise = contenu.entreprise or audit.entreprise
    schemas = schemas_rapport(entreprise, contenu.equipe, [(m.nom, m.role) for m in contenu.membres],
                              contenu.outils_erp, charte, PREMIER_NUMERO_INDIVIDUEL)
    w = _Redacteur(charte)
    core = w.doc.core_properties
    core.title = f"{contenu.titre} — {entreprise}"
    core.subject = contenu.sous_titre
    core.author = ", ".join(m.nom for m in contenu.membres if m.nom) or entreprise
    core.keywords = "Subakoua; rapport professionnel; audit; SAE"
    core.language = "fr-FR"
    w.entete_pied(entreprise, logo)
    _page_de_garde(w, audit, contenu, logo, logo_etablissement)
    plan: list[tuple[int, str]] = [
        (1, "Introduction"),
        (1, PARTIE_1), *[(2, TITRES[k]) for k in ("strategies", "outils", "resultats", "recul")],
        (1, PARTIE_2),
        *[(2, f"{i}. {_nom_membre(m)}") for i, m in enumerate(contenu.membres, start=PREMIER_NUMERO_INDIVIDUEL)],
        (1, "Conclusion"), (1, "Sources et bibliographie"), (1, "Annexes"),
    ]
    w.sommaire(plan)
    _introduction(w, audit, contenu, schemas)
    w.titre(PARTIE_1, 1)
    w.chapeau("Cette partie restitue notre travail collectif comme un audit stratégique et opérationnel de l'année : "
              "le cap que nous nous sommes fixé, les outils et les méthodes qui nous ont servi à piloter, la description "
              "et l'analyse des résultats, puis notre prise de recul.")
    _strategies(w, audit, contenu, graphiques)
    _outils_methodes(w, audit, contenu, schemas)
    _resultats(w, audit, graphiques)
    _prise_de_recul(w, audit, contenu)
    _partie_individuelle(w, audit, contenu)
    _conclusion(w, contenu)
    _sources(w, audit, contenu)
    _annexes(w, audit, contenu)
    _quatrieme_de_couverture(w, contenu)
    _mettre_a_jour_champs_a_l_ouverture(w.doc)
    return w.octets()


def compter_a_completer(docx: bytes) -> int:
    """Nombre de passages « À compléter » restant dans un rapport généré."""
    if not DOCX_DISPONIBLE:
        return 0
    doc = Document(io.BytesIO(docx))
    n = sum(len(A_COMPLETER.findall(p.text)) for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                n += sum(len(A_COMPLETER.findall(p.text)) for p in cell.paragraphs)
    return n


def rapport_markdown(audit: AuditAnnuel, contenu: ContenuRapport) -> str:
    """Version texte (Markdown) de l'audit chiffré : pratique pour relire ou partager."""
    s = audit.synthese
    lignes = [f"# {contenu.titre} — {contenu.entreprise or audit.entreprise}", "",
              f"Période auditée : {_periode_lisible(audit)} ({s.n_mois} mois)", "", "## Chiffres clés", "",
              f"- Chiffre d'affaires : {fmt_euros(s.ca_total)} ({s.ca_mois} mois renseignés)",
              f"- Résultat net : {fmt_euros(s.resultat_total)} (marge nette {fmt_pct(s.marge_nette)})",
              f"- Trésorerie de clôture : {fmt_euros(s.tresorerie_cloture[1]) if s.tresorerie_cloture else '—'}",
              f"- Part de marché finale : {fmt_pct(s.pdm_fin[1], 2) if s.pdm_fin else '—'}",
              f"- Unités vendues : {fmt_nombre(s.ventes_totales)}", "", "## Constats", ""]
    for k in audit.constats:
        lignes.append(f"- **{k.theme} — {k.nature}** : {k.texte}" + (f" _{k.analyse}_" if k.analyse else ""))
    if audit.lecture_strategique:
        lignes += ["", "## Lecture stratégique (à valider par l'équipe)", ""]
        lignes += [f"- {l}" for l in audit.lecture_strategique]
    if audit.chronologie:
        lignes += ["", "## Chronologie des décisions", "", "| Mois | Domaine | Décision |", "|---|---|---|"]
        lignes += [f"| {mois_court(e.periode)} | {e.domaine} | {e.texte} |" for e in audit.chronologie]
    if audit.manques:
        lignes += ["", "## Données manquantes", ""]
        lignes += [f"- {m.element} : {', '.join(mois_court(p) for p in m.periodes)}" for m in audit.manques]
    return "\n".join(lignes) + "\n"
