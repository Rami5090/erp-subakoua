"""Génération du rapport professionnel au format Word (.docx).

Module PUR : ni Streamlit ni SQL. Il assemble l'audit chiffré
(``rapport_engine``), les graphiques (``rapport_graphiques``) et les textes
rédigés par l'équipe en un document **éditable** — l'équipe peut ensuite le
finaliser dans Word puis l'exporter en PDF pour le dépôt.

Structure (calquée sur les attendus de l'évaluation) :

* page de garde (logo, entreprise, équipe, formation) et sommaire ;
* **Synthèse** ;
* **Partie 1 — Travail collectif** : présentation, stratégies mises en œuvre,
  outils et méthodes, audit de l'année (description et analyse), prise de
  recul, conclusion ;
* **Partie 2 — Travail individuel** : rôle, outils montés, forces et
  faiblesses, réussites et erreurs, mission spécifique — pour chaque membre ;
* **Annexes** : tableau de bord mensuel, sources, données manquantes,
  méthodes de calcul.

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
)
from rapport_graphiques import CHARTE_DEFAUT, Charte, Graphique, couleur_lisible, texte_sur

try:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    DOCX_DISPONIBLE = True
except Exception:  # pragma: no cover - dépendance d'environnement
    DOCX_DISPONIBLE = False

MARQUEUR_A_COMPLETER = "[À COMPLÉTER]"
_LARGEUR_UTILE_CM = 16.0
_GRIS_TEXTE = "52514E"
_GRIS_FILET = "D9D8D3"
_FOND_ALTERNE = "F4F4F1"


# ---------------------------------------------------------------------------
# Utilitaires XML / mise en forme
# ---------------------------------------------------------------------------
def _hex(couleur: str) -> str:
    return str(couleur).strip().lstrip("#").upper()


def _rgb(couleur: str) -> "RGBColor":
    return RGBColor.from_string(_hex(couleur))


def _ombrer(cellule, couleur: str) -> None:
    tc_pr = cellule._tc.get_or_add_tcPr()
    for ancien in tc_pr.findall(qn("w:shd")):
        tc_pr.remove(ancien)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), _hex(couleur))
    tc_pr.append(shd)


def _bordures_tableau(table, couleur: str = _GRIS_FILET, taille: int = 4) -> None:
    tbl_pr = table._tbl.tblPr
    bordures = OxmlElement("w:tblBorders")
    for cote in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{cote}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), str(taille))
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), _hex(couleur))
        bordures.append(b)
    tbl_pr.append(bordures)


def _sans_bordures(table) -> None:
    tbl_pr = table._tbl.tblPr
    bordures = OxmlElement("w:tblBorders")
    for cote in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{cote}")
        b.set(qn("w:val"), "nil")
        bordures.append(b)
    tbl_pr.append(bordures)


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


def _filet_bas(paragraphe, couleur: str, taille: int = 12) -> None:
    p_pr = paragraphe._p.get_or_add_pPr()
    bordure = OxmlElement("w:pBdr")
    bas = OxmlElement("w:bottom")
    bas.set(qn("w:val"), "single")
    bas.set(qn("w:sz"), str(taille))
    bas.set(qn("w:space"), "4")
    bas.set(qn("w:color"), _hex(couleur))
    bordure.append(bas)
    p_pr.append(bordure)


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


def _police(style, nom: str = "Calibri") -> None:
    style.font.name = nom
    r_pr = style.element.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), nom)


# ---------------------------------------------------------------------------
# Rédacteur
# ---------------------------------------------------------------------------
class _Redacteur:
    def __init__(self, charte: Charte):
        self.charte = charte
        self.primaire = couleur_lisible(charte.primaire, minimum=4.5)
        self.secondaire = couleur_lisible(charte.secondaire, minimum=4.5)
        self.accent = couleur_lisible(charte.accent, minimum=3.0)
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
        normal.font.size = Pt(10.5)
        normal.font.color.rgb = RGBColor(0x1F, 0x23, 0x28)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.15
        for nom, taille, couleur, avant, apres in (
            ("Heading 1", 20, self.primaire, 0, 10),
            ("Heading 2", 14, self.primaire, 16, 6),
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
            st.font.size = Pt(10.5)
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
        """Texte avec mise en gras légère : **mot** devient gras."""
        morceaux = re.split(r"(\*\*[^*]+\*\*)", str(texte or ""))
        for m in morceaux:
            if not m:
                continue
            est_gras = m.startswith("**") and m.endswith("**") and len(m) > 4
            run = p.add_run(m[2:-2] if est_gras else m)
            run.bold = gras or est_gras
            run.italic = italique
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

    def encadre(self, titre: str, lignes: Sequence[str]) -> None:
        """Encadré coloré (lecture de l'ERP, à valider par l'équipe)."""
        table = self.doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        _sans_bordures(table)
        cellule = table.rows[0].cells[0]
        cellule.width = Cm(_LARGEUR_UTILE_CM)
        _ombrer(cellule, "EEF4F7")
        p = cellule.paragraphs[0]
        r = p.add_run(titre)
        r.bold = True
        r.font.color.rgb = _rgb(self.primaire)
        for ligne in lignes:
            q = cellule.add_paragraph(style="List Bullet")
            self._runs(q, ligne)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def tableau(self, titre: str | None, entetes: Sequence[str], lignes: Sequence[Sequence[Any]],
                largeurs: Sequence[float] | None = None, numeriques: Iterable[int] = (),
                taille: float = 8.5, note: str | None = None, vide: str = "—") -> None:
        if titre:
            self.n_tableau += 1
            leg = self.doc.add_paragraph(style="Caption")
            leg.add_run(f"Tableau {self.n_tableau} — {titre}")
            leg.paragraph_format.keep_with_next = True
        numeriques = set(numeriques)
        table = self.doc.add_table(rows=1, cols=len(entetes))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _bordures_tableau(table)
        if largeurs is None:
            largeurs = [_LARGEUR_UTILE_CM / len(entetes)] * len(entetes)
        fond_entete = self.primaire
        encre_entete = texte_sur(fond_entete)
        ligne_entete = table.rows[0]
        _repeter_entete(ligne_entete)
        for i, entete in enumerate(entetes):
            c = ligne_entete.cells[i]
            c.width = Cm(largeurs[i])
            _ombrer(c, fond_entete)
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i in numeriques else WD_ALIGN_PARAGRAPH.LEFT
            r = p.add_run(str(entete))
            r.bold = True
            r.font.size = Pt(taille)
            r.font.color.rgb = _rgb(encre_entete)
        for n, valeurs in enumerate(lignes):
            row = table.add_row()
            _ne_pas_couper(row)
            for i, valeur in enumerate(valeurs):
                c = row.cells[i]
                c.width = Cm(largeurs[i])
                if n % 2 == 1:
                    _ombrer(c, _FOND_ALTERNE)
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
            self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

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
        """Rangée d'indicateurs clés : (libellé, valeur, précision)."""
        colonnes = 3
        lignes = (len(indicateurs) + colonnes - 1) // colonnes
        table = self.doc.add_table(rows=lignes, cols=colonnes)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _sans_bordures(table)
        for i, (libelle, valeur, precision) in enumerate(indicateurs):
            c = table.rows[i // colonnes].cells[i % colonnes]
            c.width = Cm(_LARGEUR_UTILE_CM / colonnes)
            _ombrer(c, "F4F4F1")
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(libelle)
            r.font.size = Pt(8.5)
            r.font.color.rgb = _rgb(_GRIS_TEXTE)
            q = c.add_paragraph()
            q.paragraph_format.space_after = Pt(0)
            rv = q.add_run(valeur)
            rv.bold = True
            rv.font.size = Pt(16)
            rv.font.color.rgb = _rgb(self.primaire)
            if precision:
                s = c.add_paragraph()
                s.paragraph_format.space_after = Pt(2)
                rs = s.add_run(precision)
                rs.font.size = Pt(8)
                rs.font.color.rgb = _rgb(_GRIS_TEXTE)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

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
        _filet_bas(entete, self.accent, 6)
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

    def octets(self) -> bytes:
        tampon = io.BytesIO()
        self.doc.save(tampon)
        return tampon.getvalue()


# ---------------------------------------------------------------------------
# Sections du rapport
# ---------------------------------------------------------------------------
def _periode_lisible(audit: AuditAnnuel) -> str:
    return f"de {mois_long(audit.debut)} à {mois_long(audit.fin)}"


def _page_de_garde(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport, logo: bytes | None) -> None:
    doc = w.doc
    if logo:
        try:
            doc.add_picture(io.BytesIO(logo), height=Cm(3.2))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception:
            w.a_completer("Logo illisible : utilisez une image PNG ou JPG.")
    else:
        w.a_completer("Insérez le logo de l'entreprise (onglet « Identité » de la page Rapport professionnel).")
    for _ in range(2):
        doc.add_paragraph()
    bandeau = doc.add_table(rows=1, cols=1)
    bandeau.alignment = WD_TABLE_ALIGNMENT.CENTER
    _sans_bordures(bandeau)
    cellule = bandeau.rows[0].cells[0]
    cellule.width = Cm(_LARGEUR_UTILE_CM)
    _ombrer(cellule, w.primaire)
    encre = texte_sur(w.primaire)
    p = cellule.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(18)
    r = p.add_run((c.titre or "Rapport professionnel").upper())
    r.bold = True
    r.font.size = Pt(26)
    r.font.color.rgb = _rgb(encre)
    q = cellule.add_paragraph()
    q.alignment = WD_ALIGN_PARAGRAPH.CENTER
    q.paragraph_format.space_after = Pt(18)
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
    _filet_bas(nom, w.accent, 18)
    jeu = doc.add_paragraph()
    jeu.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rj = jeu.add_run(f"Jeu d'entreprise Subakoua — entreprise n° {audit.numero}\nPériode auditée : {_periode_lisible(audit)}")
    rj.font.size = Pt(12)
    rj.font.color.rgb = _rgb(w.secondaire)
    doc.add_paragraph()
    if c.membres:
        eq = doc.add_paragraph()
        eq.alignment = WD_ALIGN_PARAGRAPH.CENTER
        re_ = eq.add_run(("Équipe " + c.equipe) if c.equipe else "Équipe")
        re_.bold = True
        re_.font.size = Pt(12)
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
    for _ in range(3):
        doc.add_paragraph()
    infos = [x for x in (c.formation, c.etablissement,
                         ("Enseignant(e)s : " + c.enseignants) if c.enseignants else "",
                         c.date or date.today().strftime("%d/%m/%Y")) if x]
    pied = doc.add_paragraph()
    pied.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rp = pied.add_run("\n".join(infos))
    rp.font.size = Pt(10)
    rp.font.color.rgb = _rgb(_GRIS_TEXTE)


def _synthese(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    s = audit.synthese
    w.titre("Synthèse", 1)
    tuiles = [
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
    ]
    w.tuiles(tuiles)
    w.titre("L'essentiel en une page", 3)
    w.texte_redige(c.synthese, CONSIGNES["synthese"])
    forces = [k.texte for k in audit.constats if k.nature == "force"][:4]
    vigilances = [k.texte for k in audit.constats if k.nature in ("faiblesse", "vigilance") and k.theme != "Données"][:4]
    if forces or vigilances:
        w.titre("Repères chiffrés de l'audit", 3)
        if forces:
            w.paragraphe("**Points forts**", aligne="gauche", apres=2)
            w.puces(forces)
        if vigilances:
            w.paragraphe("**Points de vigilance**", aligne="gauche", apres=2)
            w.puces(vigilances)


def _presentation(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("1. Présentation de l'entreprise et de l'équipe", 2, saut_de_page=False)
    w.texte_redige(c.presentation, CONSIGNES["presentation"])
    s = audit.synthese
    lignes = [
        ("Entreprise", f"{c.entreprise or audit.entreprise} (n° {audit.numero})"),
        ("Activité", "Conception, fabrication et vente de combinaisons en néoprène"),
        ("Gamme", ", ".join(PRODUCTS)),
        ("Ateliers", " → ".join(ATELIERS)),
        ("Période auditée", f"{_periode_lisible(audit)} ({s.n_mois} mois)"),
    ]
    if s.machines_debut or s.machines_fin:
        lignes.append(("Parc machines", " ; ".join(
            f"{a} : {fmt_nombre(s.machines_debut.get(a))} → {fmt_nombre(s.machines_fin.get(a))}" for a in ATELIERS
            if a in s.machines_debut or a in s.machines_fin)))
    if s.effectifs_debut is not None:
        lignes.append(("Effectif", f"{fmt_nombre(s.effectifs_debut)} → {fmt_nombre(s.effectifs_fin)} personnes"))
    if audit.concurrence:
        lignes.append(("Marché", f"{len(audit.concurrence)} entreprises observées dans le benchmark concurrentiel"))
    w.tableau("Fiche d'identité de l'entreprise", ["Rubrique", "Description"], lignes, largeurs=[4.0, 12.0])
    if c.membres:
        w.tableau("Répartition des rôles dans l'équipe", ["Membre", "Rôle", "Missions principales"],
                  [(m.nom or "—", m.role or "—", m.missions or "—") for m in c.membres], largeurs=[4.0, 4.5, 7.5])


def _strategies(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("2. Les stratégies mises en œuvre", 2)
    w.titre("2.1 La stratégie de l'équipe", 3)
    w.texte_redige(c.strategie, CONSIGNES["strategie"])
    if audit.lecture_strategique:
        w.titre("2.2 Lecture des décisions observées", 3)
        w.encadre("Lecture proposée par l'ERP à partir des décisions réellement prises — à confirmer ou nuancer "
                  "par l'équipe", list(audit.lecture_strategique))
    if audit.chronologie:
        w.titre("2.3 Chronologie des décisions clés", 3)
        w.tableau("Décisions clés de la période (déduites des données saisies et scrapées)",
                  ["Mois", "Domaine", "Décision"],
                  [(mois_court(e.periode), e.domaine, e.texte) for e in audit.chronologie],
                  largeurs=[2.0, 3.0, 11.0])
    produits = [l for l in audit.produits if l.prix_fin is not None]
    if produits:
        w.titre("2.4 Politique de prix et positionnement", 3)
        w.tableau("Prix de vente décidés et prix du marché (€ HT)",
                  ["Produit", "Prix début", "Prix fin", "Prix moyen vendu", "Prix du marché", "Indice prix"],
                  [(l.produit, fmt_euros(l.prix_debut), fmt_euros(l.prix_fin), fmt_euros(l.prix_moyen),
                    fmt_euros(l.prix_marche),
                    fmt_nombre(l.indice_prix * 100, 0) if l.indice_prix is not None else "—") for l in produits],
                  largeurs=[3.0, 2.4, 2.4, 3.0, 2.8, 2.4], numeriques=range(1, 6),
                  note="Indice prix = prix de vente / prix du marché × 100 (100 = aligné sur le marché). "
                       "Prix moyen vendu = moyenne pondérée par les volumes.")


def _outils_methodes(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("3. Les outils et méthodes mis en œuvre", 2)
    w.titre("3.1 Les outils", 3)
    w.texte_redige(c.outils, CONSIGNES["outils"])
    retenus = [o for o in OUTILS_ERP if o.nom in set(c.outils_erp)]
    if retenus:
        w.tableau("Outils de pilotage construits dans l'ERP Subakoua",
                  ["Outil", "Besoin couvert", "Méthode", "Apport à la décision"],
                  [(f"**{o.nom}**", o.besoin, o.methode, o.apport) for o in retenus],
                  largeurs=[3.4, 4.0, 4.8, 3.8], taille=8)
    w.titre("3.2 Les méthodes de travail et la gestion de projet", 3)
    w.texte_redige(c.methodes, CONSIGNES["methodes"])
    if c.outils_gp:
        w.tableau("Outils et rituels de gestion de projet utilisés", ["Outil / méthode", "Usage dans l'équipe"],
                  [(nom, OUTILS_GP.get(nom, "")) for nom in c.outils_gp], largeurs=[5.5, 10.5])


def _audit(w: _Redacteur, audit: AuditAnnuel, graphiques: Mapping[str, Graphique]) -> None:
    s = audit.synthese
    w.titre("4. Audit de l'année : description et analyse", 2)
    w.paragraphe(f"Cette partie décrit puis analyse l'activité de l'entreprise {_periode_lisible(audit)}. "
                 "Chaque chiffre provient des données du jeu consolidées par l'ERP (sources en annexe B) ; "
                 "un mois sans donnée n'est jamais estimé.")
    # --- Commercial -----------------------------------------------------
    w.titre("4.1 Performance commerciale", 3)
    w.figure(graphiques.get("ca_mensuel"), "annexe A")
    w.figure(graphiques.get("ca_cumule"))
    w.figure(graphiques.get("ventes_produits"), "tableau des produits ci-dessous")
    w.figure(graphiques.get("mix_produits"))
    produits = [l for l in audit.produits if l.ventes > 0 or l.prix_fin is not None]
    if produits:
        w.tableau("Analyse par produit sur la période",
                  ["Produit", "Unités vendues", "Part volumes", "CA estimé", "Part CA", "Marge unit.", "Contribution"],
                  [(l.produit, fmt_nombre(l.ventes), fmt_pct(l.part_volumes), fmt_euros(l.ca_estime),
                    fmt_pct(l.part_ca), fmt_euros(l.marge_unitaire), fmt_euros(l.contribution)) for l in produits],
                  largeurs=[2.6, 2.2, 1.9, 2.8, 1.8, 2.1, 2.6], numeriques=range(1, 7),
                  note="CA estimé = prix catalogue décidé × unités vendues (hors remises de canal). Marge unitaire = "
                       "dernier prix − dernier coût variable connu ; contribution = marge unitaire × unités (estimation).")
    d = audit.decomposition
    if d is not None and abs(d.ecart) >= 1:
        w.figure(graphiques.get("decomposition"))
        w.tableau("Décomposition de l'évolution du CA théorique",
                  ["Composante", "Montant", "Lecture"],
                  [(f"CA théorique {mois_court(d.periode_a)}", fmt_euros(d.ca_a), "Point de départ"),
                   ("Effet volume", fmt_euros_signe(d.effet_volume), "Plus ou moins d'unités vendues, au prix moyen initial"),
                   ("Effet mix", fmt_euros_signe(d.effet_mix), "Déformation des ventes vers des produits plus ou moins chers"),
                   ("Effet prix", fmt_euros_signe(d.effet_prix), "Changements de prix appliqués aux volumes finaux"),
                   (f"**CA théorique {mois_court(d.periode_b)}**", f"**{fmt_euros(d.ca_b)}**", "Point d'arrivée")],
                  largeurs=[4.5, 3.2, 8.3], numeriques=[1])
    w.figure(graphiques.get("pdm"), "annexe A")
    if audit.concurrence:
        w.figure(graphiques.get("concurrence"))
        w.tableau(f"Benchmark concurrentiel ({mois_court(audit.concurrence_periode or audit.fin)})",
                  ["Rang", "Entreprise", "Part de marché", "Ventes (u)", "Prix moyen"],
                  [(i + 1, (f"**Entreprise {int(_float(r.get('entreprise')) or 0)} (nous)**"
                            if _float(r.get("entreprise")) == float(audit.numero)
                            else f"Entreprise {int(_float(r.get('entreprise')) or 0)}"),
                    fmt_pct(r.get("part_marche"), 2), fmt_nombre(r.get("ventes")), fmt_euros(r.get("prix")))
                   for i, r in enumerate(audit.concurrence)],
                  largeurs=[1.6, 5.0, 3.2, 3.0, 3.2], numeriques=[0, 2, 3, 4])
    _analyse_theme(w, audit, "Commercial")
    # --- Financier ------------------------------------------------------
    w.titre("4.2 Performance financière", 3)
    w.figure(graphiques.get("resultat"), "tableau ci-dessous")
    w.figure(graphiques.get("tresorerie"), "tableau ci-dessous")
    lignes = []
    for m in audit.mois:
        if any(k in m.valeurs for k in (CA, RESULTAT, TRESORERIE, CAPITAUX_PROPRES, DETTE)):
            lignes.append((mois_court(m.periode), fmt_euros(m.v(CA)), fmt_euros(m.v(RESULTAT)), fmt_euros(m.v(CAF)),
                           fmt_euros(m.v(TRESORERIE)), fmt_euros(m.v(CAPITAUX_PROPRES)), fmt_euros(m.v(DETTE))))
    if lignes:
        w.tableau("Indicateurs financiers mensuels (€)",
                  ["Mois", "CA", "Résultat net", "CAF", "Trésorerie", "Capitaux propres", "Dette bancaire"],
                  lignes, largeurs=[1.6, 2.5, 2.3, 2.2, 2.5, 2.5, 2.4], numeriques=range(1, 7), taille=8)
    _analyse_theme(w, audit, "Financier")
    # --- Industriel -----------------------------------------------------
    w.titre("4.3 Performance industrielle et logistique", 3)
    w.figure(graphiques.get("ateliers"))
    lignes = []
    for a in ATELIERS:
        if a in s.machines_debut or a in s.machines_fin or a in s.utilisation_moyenne:
            maxi = s.utilisation_max.get(a)
            lignes.append((a, fmt_nombre(s.machines_debut.get(a)), fmt_nombre(s.machines_fin.get(a)),
                           fmt_nombre(s.acquisitions_total.get(a, 0)), fmt_nombre(s.cessions_total.get(a, 0)),
                           fmt_pct((s.utilisation_moyenne.get(a) or 0) * 100, 0) if a in s.utilisation_moyenne else "—",
                           f"{fmt_pct(maxi[1] * 100, 0)} ({mois_court(maxi[0])})" if maxi else "—"))
    if lignes:
        w.tableau("Capacité de production par atelier",
                  ["Atelier", "Machines début", "Machines fin", "Achats", "Cessions", "Utilisation moy.", "Pic"],
                  lignes, largeurs=[2.8, 2.1, 2.0, 1.6, 1.7, 2.6, 3.2], numeriques=range(1, 7))
    _analyse_theme(w, audit, "Industriel")
    # --- RH ---------------------------------------------------------------
    w.titre("4.4 Ressources humaines", 3)
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
        w.tableau("Effectifs, masse salariale et scores RH (sur 100)",
                  ["Mois", "Effectif", "Masse salariale brute", *[f"Score {p}" for p in poles]], rh,
                  largeurs=[2.0, 2.0, 3.8, *([reste / len(poles)] * len(poles))],
                  numeriques=range(1, 3 + len(poles)), taille=8)
    else:
        w.note("Aucune donnée RH disponible pour la période (décisions RH ni audits du service).")
    _analyse_theme(w, audit, "Ressources humaines")
    # --- Bilan ----------------------------------------------------------
    w.titre("4.5 Bilan : forces et points de vigilance", 3)
    forces = [k.texte for k in audit.constats if k.nature == "force"]
    faibles = [k.texte for k in audit.constats if k.nature in ("faiblesse", "vigilance")]
    if forces or faibles:
        n = max(len(forces), len(faibles))
        w.tableau("Synthèse des constats de l'audit", ["Forces", "Faiblesses et points de vigilance"],
                  [(forces[i] if i < len(forces) else "", faibles[i] if i < len(faibles) else "") for i in range(n)],
                  largeurs=[8.0, 8.0], taille=8.5, vide="")


def _analyse_theme(w: _Redacteur, audit: AuditAnnuel, theme: str) -> None:
    constats = [k for k in audit.constats if k.theme == theme]
    if not constats:
        return
    w.paragraphe("**Description et analyse**", aligne="gauche", apres=2)
    for k in constats:
        p = w.doc.add_paragraph(style="List Bullet")
        w._runs(p, k.texte)
        if k.analyse:
            r = p.add_run(" " + k.analyse)
            r.italic = True
            r.font.color.rgb = _rgb(_GRIS_TEXTE)


def _prise_de_recul(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("5. Explication et prise de recul sur les résultats", 2)
    w.texte_redige(c.prise_de_recul, CONSIGNES["prise_de_recul"])
    pistes = [k.analyse for k in audit.constats if k.analyse and k.nature in ("faiblesse", "vigilance")][:5]
    if pistes:
        w.encadre("Questions de prise de recul suggérées par l'audit", pistes)
    w.titre("6. Conclusion de la partie collective", 2)
    w.texte_redige(c.conclusion, CONSIGNES["conclusion"])


def _partie_individuelle(w: _Redacteur, audit: AuditAnnuel, c: ContenuRapport) -> None:
    w.titre("Partie 2 — Travail individuel", 1)
    w.paragraphe("Chaque membre présente sa mission dans la SAE : son rôle, les outils qu'il a montés, ses forces "
                 "et faiblesses, ses réussites et ses erreurs, ainsi que sa mission spécifique.")
    if not c.membres:
        w.a_completer("Ajoutez les membres de l'équipe (onglet « Équipe ») : une section individuelle est créée "
                      "pour chacun.")
        return
    for i, m in enumerate(c.membres, start=7):
        _membre(w, audit, m, i)


def _membre(w: _Redacteur, audit: AuditAnnuel, m: Membre, numero: int) -> None:
    w.titre(f"{numero}. {m.nom or 'Membre'}" + (f" — {m.role}" if m.role else ""), 2, saut_de_page=numero > 7)
    w.titre("Mon rôle dans l'équipe", 3)
    w.texte_redige("\n".join(x for x in (m.role and f"**Fonction :** {m.role}", m.missions) if x), CONSIGNES["role"])
    w.titre("Les outils que j'ai montés", 3)
    w.texte_redige(m.outils, CONSIGNES["outils_membre"])
    retenus = [o for o in OUTILS_ERP if o.nom in set(m.outils_erp)]
    if retenus:
        w.tableau(None, ["Outil de l'ERP", "Ce qu'il apporte"], [(o.nom, o.apport) for o in retenus],
                  largeurs=[6.0, 10.0], taille=8.5)
    w.titre("Mes forces et mes faiblesses", 3)
    _deux_colonnes(w, ("Forces", m.forces, CONSIGNES["forces"]), ("Faiblesses", m.faiblesses, CONSIGNES["faiblesses"]))
    w.titre("Mes réussites et mes erreurs", 3)
    _deux_colonnes(w, ("Réussites", m.reussites, CONSIGNES["reussites"]), ("Erreurs", m.erreurs, CONSIGNES["erreurs"]))
    w.titre("Ma mission spécifique", 3)
    w.texte_redige(m.mission_specifique, CONSIGNES["mission_specifique"])
    cles = perimetre_role(m.role)
    lignes = []
    for cle in cles:
        serie = audit.serie(cle)
        if serie:
            (p0, v0), (p1, v1) = serie[0], serie[-1]
            lignes.append((INDICATEURS[cle][0], f"{fmt_valeur(cle, v0)} ({mois_court(p0)})",
                           f"{fmt_valeur(cle, v1)} ({mois_court(p1)})"))
    if lignes:
        w.titre("Mon périmètre en chiffres", 3)
        w.tableau(None, ["Indicateur", "Début de période", "Fin de période"], lignes, largeurs=[6.0, 5.0, 5.0])
    w.titre("Bilan personnel", 3)
    w.texte_redige(m.bilan, CONSIGNES["bilan"])


def _deux_colonnes(w: _Redacteur, gauche: tuple[str, str, str], droite: tuple[str, str, str]) -> None:
    table = w.doc.add_table(rows=2, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _bordures_tableau(table)
    for j, (titre, texte, consigne) in enumerate((gauche, droite)):
        entete = table.rows[0].cells[j]
        entete.width = Cm(8.0)
        _ombrer(entete, w.primaire)
        r = entete.paragraphs[0].add_run(titre)
        r.bold = True
        r.font.color.rgb = _rgb(texte_sur(w.primaire))
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
            q.paragraph_format.space_after = Pt(2)
            w._runs(q, re.sub(r"^[-•*]\s+", "• ", ligne))
    w.doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _annexes(w: _Redacteur, audit: AuditAnnuel) -> None:
    w.titre("Annexes", 1)
    w.titre("A. Tableau de bord mensuel détaillé", 2, saut_de_page=False)
    lignes = [(mois_court(m.periode), fmt_nombre(m.v(VENTES_TOTALES)), fmt_euros(m.v(CA)), fmt_pct(m.v(PDM), 2),
               fmt_euros((m.v(PUB_MARQUE) or 0) + (m.v(PUB_PRODUITS) or 0)) if (PUB_MARQUE in m.valeurs or PUB_PRODUITS in m.valeurs) else "—",
               fmt_euros(m.v(BFR)), fmt_nombre(m.v(EFFECTIFS)))
              for m in audit.mois]
    w.tableau("Activité mensuelle", ["Mois", "Unités vendues", "CA", "Part de marché", "Publicité", "BFR", "Effectif"],
              lignes, largeurs=[1.7, 2.3, 2.7, 2.3, 2.5, 2.6, 1.9], numeriques=range(1, 7), taille=8)
    produits_mois = [(mois_court(m.periode), *[fmt_nombre(m.ventes.get(p)) if p in m.ventes else "—" for p in PRODUCTS])
                     for m in audit.mois]
    w.tableau("Ventes mensuelles par produit (unités)", ["Mois", *PRODUCTS], produits_mois,
              largeurs=[2.0, 2.8, 2.8, 2.8, 2.8, 2.8], numeriques=range(1, 6), taille=8)
    prix_mois = [(mois_court(m.periode), *[fmt_euros(m.prix.get(p)) if p in m.prix else "—" for p in PRODUCTS])
                 for m in audit.mois if m.prix]
    if prix_mois:
        w.tableau("Prix de vente décidés (€ HT)", ["Mois", *PRODUCTS], prix_mois,
                  largeurs=[2.0, 2.8, 2.8, 2.8, 2.8, 2.8], numeriques=range(1, 6), taille=8)
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
        "**Taux d'utilisation d'un atelier** = minutes consommées / minutes disponibles (étude « Ateliers »).",
        "**Positionnement stratégique** : lecture inspirée des stratégies génériques de M. E. Porter "
        "(Competitive Strategy, 1980) — domination par les coûts, différenciation, focalisation.",
    ])


def generer_rapport_docx(
    audit: AuditAnnuel,
    contenu: ContenuRapport,
    graphiques: Mapping[str, Graphique] | None = None,
    charte: Charte = CHARTE_DEFAUT,
    logo: bytes | None = None,
) -> bytes:
    """Le rapport complet, au format .docx (octets prêts au téléchargement)."""
    if not DOCX_DISPONIBLE:
        raise RuntimeError("La génération Word nécessite « python-docx » : pip install -r requirements.txt.")
    graphiques = graphiques or {}
    w = _Redacteur(charte)
    entreprise = contenu.entreprise or audit.entreprise
    core = w.doc.core_properties
    core.title = f"{contenu.titre} — {entreprise}"
    core.subject = contenu.sous_titre
    core.author = ", ".join(m.nom for m in contenu.membres if m.nom) or entreprise
    core.keywords = "Subakoua; rapport professionnel; audit; SAE"
    core.language = "fr-FR"
    w.entete_pied(entreprise, logo)
    _page_de_garde(w, audit, contenu, logo)
    plan: list[tuple[int, str]] = [
        (1, "Synthèse"),
        (1, "Partie 1 — Travail collectif"),
        (2, "1. Présentation de l'entreprise et de l'équipe"), (2, "2. Les stratégies mises en œuvre"),
        (2, "3. Les outils et méthodes mis en œuvre"), (2, "4. Audit de l'année : description et analyse"),
        (2, "5. Explication et prise de recul sur les résultats"), (2, "6. Conclusion de la partie collective"),
        (1, "Partie 2 — Travail individuel"),
        *[(2, f"{i}. {m.nom or 'Membre'}" + (f" — {m.role}" if m.role else "")) for i, m in enumerate(contenu.membres, start=7)],
        (1, "Annexes"),
    ]
    w.sommaire(plan)
    _synthese(w, audit, contenu)
    w.titre("Partie 1 — Travail collectif", 1)
    _presentation(w, audit, contenu)
    _strategies(w, audit, contenu)
    _outils_methodes(w, audit, contenu)
    _audit(w, audit, graphiques)
    _prise_de_recul(w, audit, contenu)
    _partie_individuelle(w, audit, contenu)
    _annexes(w, audit)
    _mettre_a_jour_champs_a_l_ouverture(w.doc)
    return w.octets()


def compter_a_completer(docx: bytes) -> int:
    """Nombre de passages « À compléter » restant dans un rapport généré."""
    if not DOCX_DISPONIBLE:
        return 0
    doc = Document(io.BytesIO(docx))
    n = sum(p.text.count(MARQUEUR_A_COMPLETER) for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                n += sum(p.text.count(MARQUEUR_A_COMPLETER) for p in cell.paragraphs)
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
