"""Schémas du rapport professionnel (matplotlib, rendu PNG pour Word).

Module PUR : ni Streamlit ni SQL. Là où ``rapport_graphiques`` trace des
données, ces schémas donnent à voir une **structure** : le parcours du
rapport, l'organisation de l'équipe, la chaîne des outils de l'ERP et le cycle
de décision mensuel. Ils ne portent que ce que l'équipe a saisi (membres,
rôles, outils retenus) ou ce qu'impose le jeu (un tour de décision par mois) :
aucun chiffre, aucune information inventée.

Direction artistique : le thème du jeu — une entreprise industrielle de
combinaisons de plongée. Le plan du rapport se lit comme un profil de
plongée : on descend de la stratégie vers les résultats, puis on remonte par
la prise de recul et les paliers individuels jusqu'à la conclusion.
"""
from __future__ import annotations

import textwrap
from typing import Sequence

from rapport_graphiques import (
    CHARTE_DEFAUT,
    ENCRE,
    ENCRE_SECONDAIRE,
    MATPLOTLIB_DISPONIBLE,
    Charte,
    Graphique,
    _rendre,
    _rgb,
    couleur_lisible,
    texte_sur,
)

if MATPLOTLIB_DISPONIBLE:
    from matplotlib.figure import Figure
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

_LARGEUR = 6.3   # pouces : largeur utile de la page (16 cm)
SOURCE_SCHEMA = "Source : équipe (paramètres du rapport) — schéma ERP Subakoua"


def eclaircir(hexa: str, part_blanc: float) -> str:
    """Teinte claire : la couleur mélangée à ``part_blanc`` (0 à 1) de blanc."""
    r, g, b = _rgb(hexa)
    return "#{:02X}{:02X}{:02X}".format(*(int(round((c + (1 - c) * part_blanc) * 255)) for c in (r, g, b)))


def _repere(fig: "Figure", hauteur: float):
    """Axes en pouces (1 unité = 1 pouce, aspect égal) : boîtes arrondies non déformées."""
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, _LARGEUR)
    ax.set_ylim(0, hauteur)
    ax.set_aspect("equal")
    ax.axis("off")
    return ax


def _boite(ax, x: float, y: float, l: float, h: float, fond: str, bord: str | None = None,
           epaisseur: float = 0.8, arrondi: float = 0.06) -> None:
    ax.add_patch(FancyBboxPatch((x, y), l, h, boxstyle=f"round,pad=0,rounding_size={arrondi}",
                                facecolor=fond, edgecolor=bord or fond, linewidth=epaisseur, zorder=2))


def _fleche(ax, depart: tuple[float, float], arrivee: tuple[float, float], couleur: str,
            courbure: float = 0.0, epaisseur: float = 1.2) -> None:
    ax.add_patch(FancyArrowPatch(depart, arrivee, arrowstyle="-|>", mutation_scale=9, color=couleur,
                                 linewidth=epaisseur, connectionstyle=f"arc3,rad={courbure}", zorder=1,
                                 shrinkA=2, shrinkB=2))


def _couper(texte: str, largeur: int) -> str:
    return "\n".join(textwrap.wrap(str(texte or ""), largeur, break_long_words=False)) or "—"


# ---------------------------------------------------------------------------
# 1. Le parcours du rapport (profil de plongée)
# ---------------------------------------------------------------------------
def schema_parcours(noms_membres: Sequence[str], charte: Charte = CHARTE_DEFAUT,
                    premier_numero_individuel: int = 5) -> Graphique | None:
    """Le plan du rapport en profil de plongée : la figure de l'annonce du plan."""
    if not MATPLOTLIB_DISPONIBLE:
        return None
    primaire = couleur_lisible(charte.primaire, minimum=4.5)
    secondaire = couleur_lisible(charte.secondaire, minimum=3.0)
    accent = couleur_lisible(charte.accent, minimum=3.0)
    n = max(1, len(noms_membres))
    dernier = premier_numero_individuel + n - 1
    individuels = (f"{premier_numero_individuel}" if n == 1 else f"{premier_numero_individuel} à {dernier}")
    # (abscisse, profondeur, libellé au-dessus de la surface)
    etapes = [
        (0.55, 0.0, "Introduction\nenjeux et problématique"),
        (1.45, -1.0, "1 · Le cap\nstratégies"),
        (2.35, -2.0, "2 · L'équipement\noutils et méthodes"),
        (3.25, -3.0, "3 · La plongée\nrésultats de l'année"),
        (4.05, -2.0, "4 · La remontée\nprise de recul"),
        (4.95, -1.0, f"{individuels} · Les paliers\nparties individuelles"),
        (5.85, 0.0, "Conclusion\net perspectives"),
    ]
    hauteur = 2.6
    base = 1.86  # ordonnée de la surface dans le repère en pouces
    pas = 0.42   # pouces par niveau de profondeur

    def y(profondeur: float) -> float:
        return base + profondeur * pas

    def dessin(fig: "Figure") -> None:
        ax = _repere(fig, hauteur)
        # Eau : dégradé vertical de la surface vers le fond.
        import numpy as np
        degrade = np.linspace(0, 1, 64).reshape(-1, 1)
        fond_clair, fond_sombre = _rgb(eclaircir(secondaire, 0.9)), _rgb(eclaircir(primaire, 0.55))
        couleurs = np.array([[fond_clair[i] + (fond_sombre[i] - fond_clair[i]) * t for i in range(3)]
                             for t in degrade[:, 0]]).reshape(-1, 1, 3)
        ax.imshow(couleurs, extent=(0.15, _LARGEUR - 0.15, y(-3.75), base), origin="upper", aspect="auto",
                  zorder=0, interpolation="bicubic")
        ax.set_xlim(0, _LARGEUR)       # imshow recadre les axes sur l'image : on rétablit la page entière
        ax.set_ylim(0, hauteur)
        xs = np.linspace(0.15, _LARGEUR - 0.15, 200)
        ax.plot(xs, base + 0.025 * np.sin(xs * 9), color=secondaire, linewidth=1.0, zorder=1)
        ax.text(0.27, y(-1.9), "profondeur de l'analyse →", fontsize=6.3, color=eclaircir(primaire, 0.1),
                style="italic", rotation=-90, ha="center", va="center", zorder=3)
        # Profil : descente, temps au fond, remontée par paliers (un palier par membre).
        points = [(etapes[0][0], y(0.0)), (etapes[1][0], y(-1.0)), (etapes[2][0], y(-2.0)),
                  (etapes[3][0] - 0.22, y(-3.0)), (etapes[3][0] + 0.22, y(-3.0)), (etapes[4][0], y(-2.0))]
        x0, x1 = etapes[4][0] + 0.12, etapes[6][0] - 0.25
        largeur_palier = (x1 - x0) / n
        paliers = []
        for i in range(n):
            profondeur = -1.6 + 1.2 * i / max(1, n - 1) if n > 1 else -1.0
            debut, fin = x0 + i * largeur_palier, x0 + (i + 1) * largeur_palier
            points += [(debut, y(profondeur)), (fin - 0.04, y(profondeur))]
            paliers.append(((debut + fin) / 2, y(profondeur)))
        points.append((etapes[6][0], y(0.0)))
        ax.plot([p[0] for p in points], [p[1] for p in points], color=primaire, linewidth=2.0,
                solid_joinstyle="round", solid_capstyle="round", zorder=3)
        for numero, (px, py) in enumerate(paliers, start=premier_numero_individuel):
            ax.text(px, py - 0.1, str(numero), ha="center", va="top", fontsize=6.5, color=primaire, zorder=4)
        # Étapes : point sur le profil, libellé au-dessus de la surface relié en pointillé.
        for i, (x, profondeur, libelle) in enumerate(etapes):
            py = paliers[len(paliers) // 2][1] if i == 5 else y(profondeur)
            couleur = accent if i == 3 else primaire
            ax.plot([x], [py], "o", markersize=6.5, color=couleur, markeredgecolor="#FFFFFF",
                    markeredgewidth=1.3, zorder=5)
            ax.plot([x, x], [py + 0.08, base + 0.12], color=eclaircir(primaire, 0.35), linewidth=0.6,
                    linestyle=(0, (1.5, 1.8)), zorder=2)
            titre, sous_titre = libelle.split("\n")
            ax.text(x, base + 0.42, titre, ha="center", va="bottom", fontsize=7.2, fontweight="bold", color=ENCRE)
            ax.text(x, base + 0.40, sous_titre, ha="center", va="top", fontsize=6.6, color=ENCRE_SECONDAIRE)
        # Accolades des deux parties, sous le profil.
        for debut, fin, texte in ((etapes[1][0] - 0.3, etapes[4][0] + 0.3, "Partie 1 — travail collectif"),
                                  (etapes[4][0] + 0.12, etapes[6][0] - 0.25, "Partie 2 — travail individuel")):
            yb = y(-3.55)
            ax.plot([debut, debut, fin, fin], [yb + 0.07, yb, yb, yb + 0.07], color=texte_sur(fond_sombre_hex),
                    linewidth=0.8, zorder=3)
            ax.text((debut + fin) / 2, yb - 0.04, texte, ha="center", va="top", fontsize=6.8,
                    color=texte_sur(fond_sombre_hex), fontweight="bold", zorder=3)

    fond_sombre_hex = eclaircir(primaire, 0.55)
    return Graphique(
        "parcours", "Le parcours du rapport", _rendre_brut(dessin, hauteur),
        "Le rapport se lit comme une plongée : il descend du cap stratégique vers les résultats chiffrés de "
        "l'année, puis remonte par la prise de recul et les paliers individuels — un par membre — jusqu'à la "
        "conclusion.",
        "Source : plan du rapport",
    )


# ---------------------------------------------------------------------------
# 2. L'organisation de l'équipe
# ---------------------------------------------------------------------------
def schema_organigramme(entreprise: str, equipe: str, membres: Sequence[tuple[str, str]],
                        charte: Charte = CHARTE_DEFAUT) -> Graphique | None:
    """Organigramme : l'équipe dirigeante et la fonction de chaque membre (membres : (nom, rôle))."""
    membres = [(str(nom or "").strip(), str(role or "").strip()) for nom, role in membres
               if str(nom or "").strip() or str(role or "").strip()]
    if not MATPLOTLIB_DISPONIBLE or not membres or len(membres) > 6:
        return None   # au-delà de six membres, le tableau des rôles reste plus lisible
    primaire = couleur_lisible(charte.primaire, minimum=4.5)
    secondaire = couleur_lisible(charte.secondaire, minimum=3.0)
    hauteur = 2.1
    marge, ecart = 0.2, 0.12
    largeur = min(1.6, (_LARGEUR - 2 * marge - (len(membres) - 1) * ecart) / len(membres))
    car = max(13, int(largeur * 16))

    def dessin(fig: "Figure") -> None:
        ax = _repere(fig, hauteur)
        lt, ht = 2.9, 0.48
        xt, yt = (_LARGEUR - lt) / 2, hauteur - 0.14 - ht
        _boite(ax, xt, yt, lt, ht, primaire, arrondi=0.08)
        ax.text(_LARGEUR / 2, yt + ht * 0.62, entreprise or "Notre entreprise", ha="center", va="center",
                fontsize=8.5, fontweight="bold", color=texte_sur(primaire), zorder=3)
        ax.text(_LARGEUR / 2, yt + ht * 0.26, f"Équipe de direction{' — ' + equipe if equipe else ''}",
                ha="center", va="center", fontsize=6.8, color=texte_sur(primaire), zorder=3)
        total = len(membres) * largeur + (len(membres) - 1) * ecart
        x_debut = (_LARGEUR - total) / 2
        y_boite, h_boite = 0.12, 0.74
        bus = y_boite + h_boite + 0.16
        centres = [x_debut + i * (largeur + ecart) + largeur / 2 for i in range(len(membres))]
        ax.plot([_LARGEUR / 2, _LARGEUR / 2], [yt, bus], color=secondaire, linewidth=0.9, zorder=1)
        ax.plot([min(centres), max(centres)], [bus, bus], color=secondaire, linewidth=0.9, zorder=1)
        for (nom, role), cx in zip(membres, centres):
            ax.plot([cx, cx], [bus, y_boite + h_boite], color=secondaire, linewidth=0.9, zorder=1)
            _boite(ax, cx - largeur / 2, y_boite, largeur, h_boite, eclaircir(secondaire, 0.88), secondaire)
            ax.text(cx, y_boite + h_boite - 0.09, _couper(role or "Rôle à préciser", car), ha="center",
                    va="top", fontsize=6.6, fontweight="bold", color=ENCRE, zorder=3, linespacing=1.05)
            ax.text(cx, y_boite + 0.08, _couper(nom or "—", car), ha="center", va="bottom", fontsize=6.4,
                    color=ENCRE_SECONDAIRE, zorder=3, linespacing=1.05)

    return Graphique("organigramme", "L'organisation de l'équipe", _rendre_brut(dessin, hauteur),
                     "Chaque membre pilote une fonction de l'entreprise ; les décisions de chaque mois sont "
                     "arbitrées en équipe.", SOURCE_SCHEMA)


# ---------------------------------------------------------------------------
# 3. La chaîne des outils de l'ERP
# ---------------------------------------------------------------------------
#: Étape de la chaîne de pilotage et nom court de chaque outil de l'ERP.
ETAPES_OUTILS: dict[str, tuple[str, str]] = {
    "Ingestion & achats documentaires": ("Collecter", "Ingestion et achats d'études"),
    "Pilotage stratégique 12 mois": ("Prévoir et optimiser", "Pilotage stratégique 12 mois"),
    "Optimiseur stratégique": ("Prévoir et optimiser", "Optimiseur stratégique"),
    "Analyse quantitative (seuil de rentabilité)": ("Prévoir et optimiser", "Seuil de rentabilité"),
    "Prévision des cours": ("Prévoir et optimiser", "Prévision des cours"),
    "Centre de décision (simulateur intégré)": ("Simuler et décider", "Centre de décision (simulateur)"),
    "Plan d'approvisionnement (MRP2)": ("Simuler et décider", "Plan d'approvisionnement (MRP2)"),
    "Livrable Finance mensuel": ("Restituer", "Livrable Finance mensuel"),
    "Rapport professionnel (ce document)": ("Restituer", "Rapport professionnel"),
}
ETAPES = ("Collecter", "Prévoir et optimiser", "Simuler et décider", "Restituer")


def schema_chaine_outils(outils_retenus: Sequence[str], charte: Charte = CHARTE_DEFAUT) -> Graphique | None:
    """Des données du jeu aux décisions : les outils de l'ERP rangés par étape de pilotage."""
    retenus = [o for o in outils_retenus if o in ETAPES_OUTILS]
    if not MATPLOTLIB_DISPONIBLE or not retenus:
        return None
    primaire = couleur_lisible(charte.primaire, minimum=4.5)
    secondaire = couleur_lisible(charte.secondaire, minimum=3.0)
    accent = couleur_lisible(charte.accent, minimum=3.0)
    par_etape = {e: [ETAPES_OUTILS[o][1] for o in retenus if ETAPES_OUTILS[o][0] == e] for e in ETAPES}
    lignes_max = max(sum(len(textwrap.wrap(t, 19)) for t in outils) for outils in par_etape.values()) or 1
    hauteur = 1.0 + 0.115 * lignes_max + 0.05 * max(len(v) for v in par_etape.values())

    def dessin(fig: "Figure") -> None:
        ax = _repere(fig, hauteur)
        haut = hauteur - 0.12
        # Source : le jeu.
        ls = 0.95
        _boite(ax, 0.1, 0.25, ls, haut - 0.25, primaire, arrondi=0.08)
        ax.text(0.1 + ls / 2, (haut + 0.25) / 2, "Jeu\nSubakoua\n\nétudes,\ncomptes,\ntableau\nde bord", ha="center",
                va="center", fontsize=6.8, color=texte_sur(primaire), fontweight="bold", zorder=3, linespacing=1.1)
        x, lc, ecart = 0.1 + ls + 0.28, 1.08, 0.2
        _fleche(ax, (0.1 + ls, (haut + 0.25) / 2), (x, (haut + 0.25) / 2), accent)
        for i, etape in enumerate(ETAPES):
            _boite(ax, x, haut - 0.34, lc, 0.34, secondaire, arrondi=0.06)
            ax.text(x + lc / 2, haut - 0.17, etape, ha="center", va="center", fontsize=6.8, fontweight="bold",
                    color=texte_sur(secondaire), zorder=3)
            _boite(ax, x, 0.25, lc, haut - 0.34 - 0.08 - 0.25, eclaircir(secondaire, 0.9), eclaircir(secondaire, 0.5))
            outils = par_etape[etape]
            texte = "\n".join("• " + _couper(t, 19).replace("\n", "\n   ") for t in outils) if outils else "(non présenté)"
            ax.text(x + 0.07, haut - 0.34 - 0.16, texte, ha="left", va="top", fontsize=6.3,
                    color=ENCRE if outils else ENCRE_SECONDAIRE, zorder=3, linespacing=1.15)
            if i < len(ETAPES) - 1:
                _fleche(ax, (x + lc, (haut + 0.25) / 2), (x + lc + ecart, (haut + 0.25) / 2), accent)
            x += lc + ecart
        ax.text(_LARGEUR / 2, 0.06, "Une même base de données alimente chaque outil : un chiffre a la même valeur "
                "d'un module à l'autre.", ha="center", va="bottom", fontsize=6.4, style="italic",
                color=ENCRE_SECONDAIRE)

    return Graphique("chaine_outils", "La chaîne des outils de l'ERP, des données du jeu aux décisions",
                     _rendre_brut(dessin, hauteur),
                     f"{len(retenus)} outil(s) présenté(s), rangés selon l'étape de pilotage qu'ils servent : "
                     "collecter les données, prévoir et optimiser, simuler et décider, restituer.",
                     "Source : ERP Subakoua (outils retenus par l'équipe)")


# ---------------------------------------------------------------------------
# 4. Le cycle mensuel de décision
# ---------------------------------------------------------------------------
ETAPES_CYCLE = (
    "Résultats du mois écoulé\n(études, comptes)",
    "Analyse dans l'ERP,\nfonction par fonction",
    "Arbitrage\nen équipe",
    "Saisie des décisions\ndans le jeu",
    "Simulation du marché\npar le jeu",
)


def schema_cycle(charte: Charte = CHARTE_DEFAUT) -> Graphique | None:
    """Le cycle de décision imposé par le jeu (un tour par mois), tel que l'ERP l'outille."""
    if not MATPLOTLIB_DISPONIBLE:
        return None
    import math
    primaire = couleur_lisible(charte.primaire, minimum=4.5)
    secondaire = couleur_lisible(charte.secondaire, minimum=3.0)
    accent = couleur_lisible(charte.accent, minimum=3.0)
    hauteur = 2.45
    cx, cy, rx, ry = _LARGEUR / 2, hauteur / 2 - 0.02, 2.25, 0.82
    n = len(ETAPES_CYCLE)
    angles = [math.pi / 2 - 2 * math.pi * i / n for i in range(n)]
    centres = [(cx + rx * math.cos(a), cy + ry * math.sin(a)) for a in angles]
    lb, hb = 1.42, 0.46

    def bord(depart: tuple[float, float], vers: tuple[float, float], marge: float = 0.05) -> tuple[float, float]:
        """Point de sortie de la boîte centrée sur ``depart`` dans la direction de ``vers``."""
        dx, dy = vers[0] - depart[0], vers[1] - depart[1]
        t = min((lb / 2 + marge) / abs(dx) if dx else math.inf, (hb / 2 + marge) / abs(dy) if dy else math.inf)
        return depart[0] + dx * t, depart[1] + dy * t

    def dessin(fig: "Figure") -> None:
        ax = _repere(fig, hauteur)
        for i in range(n):
            a, b = centres[i], centres[(i + 1) % n]
            _fleche(ax, bord(a, b), bord(b, a), accent, courbure=-0.18, epaisseur=1.3)
        for i, ((x, y), texte) in enumerate(zip(centres, ETAPES_CYCLE), start=1):
            _boite(ax, x - lb / 2, y - hb / 2, lb, hb, eclaircir(secondaire, 0.88), secondaire, arrondi=0.1)
            ax.text(x - lb / 2 + 0.1, y, str(i), ha="center", va="center", fontsize=8, fontweight="bold",
                    color=primaire, zorder=3)
            ax.text(x + 0.06, y, texte, ha="center", va="center", fontsize=6.5, color=ENCRE, zorder=3,
                    linespacing=1.1)
        ax.text(cx, cy + 0.07, "1 mois", ha="center", va="center", fontsize=10, fontweight="bold", color=primaire)
        ax.text(cx, cy - 0.15, "= 1 tour de décision", ha="center", va="center", fontsize=7, color=ENCRE_SECONDAIRE)

    return Graphique("cycle", "Le cycle mensuel de décision", _rendre_brut(dessin, hauteur),
                     "Chaque mois, le jeu publie les résultats du tour précédent ; l'équipe les analyse dans l'ERP, "
                     "arbitre puis saisit ses décisions avant la simulation suivante. Schéma de principe, à "
                     "ajuster à l'organisation réelle de l'équipe.",
                     "Source : fonctionnement du jeu Subakoua et de l'ERP")


def _rendre_brut(dessin, hauteur: float) -> bytes:
    """Rendu sans marges automatiques : le schéma occupe exactement la largeur utile."""
    return _rendre(dessin, hauteur, ajuster=False)


def schemas_rapport(entreprise: str, equipe: str, membres: Sequence[tuple[str, str]], outils_retenus: Sequence[str],
                    charte: Charte = CHARTE_DEFAUT, premier_numero_individuel: int = 5) -> dict[str, Graphique]:
    """Tous les schémas calculables, indexés par clé (``{}`` si matplotlib manque)."""
    if not MATPLOTLIB_DISPONIBLE:
        return {}
    fabriques = (
        lambda: schema_parcours([nom for nom, _ in membres], charte, premier_numero_individuel),
        lambda: schema_organigramme(entreprise, equipe, membres, charte),
        lambda: schema_chaine_outils(outils_retenus, charte),
        lambda: schema_cycle(charte),
    )
    out: dict[str, Graphique] = {}
    for fabrique in fabriques:
        try:
            g = fabrique()
        except Exception:
            g = None
        if g is not None:
            out[g.cle] = g
    return out
