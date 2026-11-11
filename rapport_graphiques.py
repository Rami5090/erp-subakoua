"""Graphiques du rapport professionnel (matplotlib, rendu PNG pour Word et l'aperçu).

Module PUR : ni Streamlit ni SQL. Chaque fonction reçoit un ``AuditAnnuel`` et
une ``Charte`` (codes couleurs choisis par l'équipe) et renvoie un
``Graphique`` — image PNG + titre + commentaire de lecture + source — ou
``None`` quand les données ne suffisent pas (aucun graphique n'est tracé sur
des valeurs inventées).

Règles de lisibilité appliquées partout :
* une seule échelle par graphique (jamais de double axe) ;
* une série unique prend la couleur principale de la charte ; plusieurs
  séries prennent une palette catégorielle **validée** (contrastes et
  daltonisme), toujours dans le même ordre — un produit garde sa couleur
  d'un graphique à l'autre ;
* marques fines, grille en filet discret, libellés directs sélectifs
  (extrêmes et fin de série), légende dès deux séries ;
* chaque graphique est doublé d'un tableau dans le rapport (lecture des
  valeurs exactes, accessibilité).

Le rendu utilise l'API objet de matplotlib (``Figure`` + ``FigureCanvasAgg``),
sans ``pyplot`` : aucun état global, compatible avec un serveur Streamlit.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

from constants import PRODUCTS
from rapport_engine import (
    ATELIERS,
    CA,
    PDM,
    RESULTAT,
    TRESORERIE,
    VENTES_TOTALES,
    AuditAnnuel,
    _float,
    fmt_compact_euros,
    fmt_euros,
    fmt_euros_signe,
    fmt_nombre,
    fmt_pct,
    mois_court,
)

try:  # dépendance optionnelle : le rapport reste générable sans graphiques
    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    MATPLOTLIB_DISPONIBLE = True
except Exception:  # pragma: no cover - dépendance d'environnement
    MATPLOTLIB_DISPONIBLE = False


# ---------------------------------------------------------------------------
# Charte graphique
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Charte:
    nom: str
    primaire: str      # titres, bandeaux, série principale
    secondaire: str    # sous-titres
    accent: str        # filets et mises en avant (jamais porteur d'une donnée)


CHARTES: dict[str, Charte] = {
    "Océan (Subakoua)": Charte("Océan (Subakoua)", "#0B4F6C", "#1B7FA3", "#F28C28"),
    "Charte du sujet (cyan & framboise)": Charte("Charte du sujet (cyan & framboise)", "#3B3B3B", "#1793B0", "#E0115F"),
    "Sobre (marine & or)": Charte("Sobre (marine & or)", "#1F3A5F", "#4A6FA5", "#C9A227"),
}
CHARTE_DEFAUT = CHARTES["Océan (Subakoua)"]

#: Palette catégorielle validée (contraste, séparation pour les daltonismes) —
#: un produit garde toujours la même couleur, dans l'ordre de ``PRODUCTS``.
PALETTE_PRODUITS = ("#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4")
COULEUR_PRODUIT = dict(zip(PRODUCTS, PALETTE_PRODUITS))
COULEUR_ATELIER = dict(zip(ATELIERS, PALETTE_PRODUITS[:3]))
COULEUR_NEGATIF = "#D03B3B"      # perte / découvert (statut « critique »)
COULEUR_NEUTRE = "#C3C2B7"       # séries de contexte (concurrents)
ENCRE = "#1F2328"
ENCRE_SECONDAIRE = "#52514E"
GRILLE = "#E1E0D9"
AXE = "#C3C2B7"


def _rgb(hexa: str) -> tuple[float, float, float]:
    h = str(hexa).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Couleur invalide : {hexa!r}")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(hexa: str) -> float:
    def canal(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (canal(c) for c in _rgb(hexa))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(c1: str, c2: str = "#FFFFFF") -> float:
    """Rapport de contraste WCAG 2.x entre deux couleurs (1 à 21)."""
    l1, l2 = sorted((_luminance(c1), _luminance(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def assombrir(hexa: str, facteur: float) -> str:
    r, g, b = _rgb(hexa)
    return "#{:02X}{:02X}{:02X}".format(*(int(round(c * (1 - facteur) * 255)) for c in (r, g, b)))


def couleur_lisible(hexa: str, fond: str = "#FFFFFF", minimum: float = 3.0) -> str:
    """La couleur, assombrie si besoin pour atteindre ``minimum`` de contraste sur ``fond``."""
    try:
        couleur = "#" + str(hexa).strip().lstrip("#").upper()
        _rgb(couleur)
    except ValueError:
        return CHARTE_DEFAUT.primaire
    pas = 0.0
    while contraste(couleur, fond) < minimum and pas < 0.95:
        pas += 0.05
        couleur = assombrir(hexa, pas)
    return couleur


def texte_sur(fond: str) -> str:
    """Blanc ou quasi-noir, selon ce qui se lit le mieux sur ``fond``."""
    return "#FFFFFF" if contraste("#FFFFFF", fond) >= contraste("#111111", fond) else "#111111"


# ---------------------------------------------------------------------------
# Graphique produit
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Graphique:
    cle: str
    titre: str
    png: bytes
    commentaire: str
    source: str


_LARGEUR_POUCES = 6.3     # 16 cm : largeur utile d'une page A4 avec marges de 2,5 cm
_HAUTEUR_POUCES = 2.75
_DPI = 220
_VERROU = threading.Lock()
#: Arial, comme le corps du rapport (guide du mémoire : Arial, Times ou Verdana) ; Liberation Sans
#: en a les mêmes métriques (paquet « fonts-liberation » sur le serveur).
_POLICES = ["Arial", "Liberation Sans", "Carlito", "DejaVu Sans"]


def _rc() -> dict:
    return {
        "font.family": "sans-serif",
        "font.sans-serif": _POLICES,
        "font.size": 8.5,
        "axes.edgecolor": AXE,
        "axes.labelcolor": ENCRE_SECONDAIRE,
        "axes.linewidth": 0.6,
        "xtick.color": ENCRE_SECONDAIRE,
        "ytick.color": ENCRE_SECONDAIRE,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "svg.fonttype": "none",
    }


def _rendre(dessin: Callable[["Figure"], None], hauteur: float = _HAUTEUR_POUCES, ajuster: bool = True) -> bytes:
    """PNG de la figure ; ``ajuster=False`` garde la mise en page du dessin (schémas en pleine page)."""
    with _VERROU, matplotlib.rc_context(_rc()):
        fig = Figure(figsize=(_LARGEUR_POUCES, hauteur), dpi=_DPI, facecolor="#FFFFFF")
        FigureCanvasAgg(fig)
        dessin(fig)
        if ajuster:
            fig.tight_layout(pad=0.6)
        tampon = io.BytesIO()
        fig.savefig(tampon, format="png", dpi=_DPI, facecolor="#FFFFFF")
        return tampon.getvalue()


def _sans_zeros(v: float, decimales: int = 2) -> str:
    """Graduation exacte : 0,25 reste « 0,25 » (jamais arrondi à « 0,2 »), 1,50 devient « 1,5 »."""
    texte = fmt_nombre(v, decimales)
    if "," in texte:
        texte = texte.rstrip("0").rstrip(",")
    return texte


def _axe_euros(maxi: float) -> FuncFormatter:
    def fmt(v: float, _pos: int) -> str:
        if not v:
            return "0"
        if maxi >= 1_000_000:
            return f"{_sans_zeros(v / 1_000_000)}\u00a0M€"
        if maxi >= 10_000:
            return f"{_sans_zeros(v / 1_000, 1)}\u00a0k€"
        return f"{_sans_zeros(v)}\u00a0€"
    return FuncFormatter(fmt)


def _axe_unites(maxi: float) -> FuncFormatter:
    def fmt(v: float, _pos: int) -> str:
        if maxi >= 10_000 and v:
            return f"{_sans_zeros(v / 1_000, 1)}\u00a0k"
        return _sans_zeros(v, 1)
    return FuncFormatter(fmt)


def _axe_pct() -> FuncFormatter:
    return FuncFormatter(lambda v, _p: f"{_sans_zeros(v, 1)}\u00a0%")


def _graduations() -> "MaxNLocator":
    """Pas « ronds » (1, 2, 5, 10 × 10ⁿ) : pas de graduation à 2,5 ou 7,5 difficile à lire."""
    return MaxNLocator(nbins=5, steps=[1, 2, 5, 10], min_n_ticks=3)


def _style_axes(ax, maxi: float, formatter: FuncFormatter, mini: float = 0.0) -> None:
    ax.yaxis.set_major_locator(_graduations())
    ax.yaxis.set_major_formatter(formatter)
    ax.grid(axis="y", color=GRILLE, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=3)
    if mini >= 0:
        ax.set_ylim(bottom=0, top=maxi * 1.15 if maxi > 0 else 1)


def _etiquettes_mois(periodes: Sequence[str]) -> list[str]:
    annees = {p.split(" - ")[0] for p in periodes}
    if len(annees) <= 1:
        return [mois_court(p, avec_annee=False) for p in periodes]
    out, precedente = [], None
    for p in periodes:
        annee = p.split(" - ")[0]
        out.append(mois_court(p) if annee != precedente else mois_court(p, avec_annee=False))
        precedente = annee
    return out


def _source(audit: AuditAnnuel, cle: str) -> str:
    sources = audit.sources_par_indicateur().get(cle, ())
    return "Source : " + " ; ".join(sources) if sources else "Source : ERP Subakoua"


# ---------------------------------------------------------------------------
# Graphiques
# ---------------------------------------------------------------------------
def graphique_ca_mensuel(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    serie = audit.serie(CA)
    if len(serie) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)
    periodes = [p for p, _ in serie]
    valeurs = [v for _, v in serie]
    maxi = max(valeurs)
    i_max = valeurs.index(maxi)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = range(len(valeurs))
        ax.bar(x, valeurs, width=0.58, color=couleur, edgecolor="#FFFFFF", linewidth=0.8, zorder=2)
        for i in sorted({i_max, len(valeurs) - 1}):
            ax.annotate(fmt_compact_euros(valeurs[i]), (i, valeurs[i]), xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=7.5, color=ENCRE)
        ax.set_xticks(list(x), _etiquettes_mois(periodes))
        _style_axes(ax, maxi, _axe_euros(maxi))

    total = sum(valeurs)
    commentaire = (f"Le chiffre d'affaires cumule {fmt_euros(total)} sur {len(valeurs)} mois. Le pic est atteint en "
                   f"{mois_court(periodes[i_max])} ({fmt_euros(maxi)}) ; le mois le plus faible est "
                   f"{mois_court(periodes[valeurs.index(min(valeurs))])} ({fmt_euros(min(valeurs))}).")
    return Graphique("ca_mensuel", "Chiffre d'affaires mensuel", _rendre(dessin), commentaire, _source(audit, CA))


def graphique_ca_cumule(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    serie = audit.serie(CA)
    if len(serie) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)
    periodes = [p for p, _ in serie]
    cumul, total = [], 0.0
    for _, v in serie:
        total += v
        cumul.append(total)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = list(range(len(cumul)))
        ax.fill_between(x, cumul, color=couleur, alpha=0.10, linewidth=0, zorder=1)
        ax.plot(x, cumul, color=couleur, linewidth=1.8, solid_capstyle="round", zorder=2)
        ax.plot(x, cumul, "o", color=couleur, markersize=4, markeredgecolor="#FFFFFF", markeredgewidth=1.2, zorder=3)
        ax.annotate(fmt_compact_euros(cumul[-1]), (x[-1], cumul[-1]), xytext=(-4, 6), textcoords="offset points",
                    ha="right", va="bottom", fontsize=7.5, color=ENCRE)
        ax.set_xticks(x, _etiquettes_mois(periodes))
        _style_axes(ax, cumul[-1], _axe_euros(cumul[-1]))

    moitie = next((periodes[i] for i, c in enumerate(cumul) if c >= total / 2), periodes[-1])
    commentaire = (f"La moitié du chiffre d'affaires de la période est atteinte dès {mois_court(moitie)} ; "
                   f"la pente de la courbe traduit le rythme mensuel des ventes (plus raide en haute saison).")
    return Graphique("ca_cumule", "Évolution du chiffre d'affaires cumulé", _rendre(dessin), commentaire, _source(audit, CA))


def graphique_ventes_produits(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    mois = [m for m in audit.mois if m.ventes]
    if len(mois) < 2:
        return None
    periodes = [m.periode for m in mois]
    produits = [p for p in PRODUCTS if any(m.ventes.get(p) for m in mois)]
    totaux = [sum(m.ventes.values()) for m in mois]
    maxi = max(totaux) if totaux else 0.0

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = list(range(len(mois)))
        bas = [0.0] * len(mois)
        for produit in produits:
            hauteurs = [float(m.ventes.get(produit, 0.0)) for m in mois]
            ax.bar(x, hauteurs, width=0.58, bottom=bas, color=COULEUR_PRODUIT[produit], edgecolor="#FFFFFF",
                   linewidth=0.8, label=produit, zorder=2)
            bas = [b + h for b, h in zip(bas, hauteurs)]
        ax.set_xticks(x, _etiquettes_mois(periodes))
        _style_axes(ax, maxi, _axe_unites(maxi))
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(produits), handlelength=1.0,
                  handleheight=0.8, columnspacing=1.2, borderaxespad=0.2)

    principal = max(produits, key=lambda p: sum(m.ventes.get(p, 0.0) for m in mois))
    toujours_premier = all(max(m.ventes, key=lambda p: m.ventes[p]) == principal for m in mois if m.ventes)
    commentaire = (f"Les ventes totalisent {fmt_nombre(sum(totaux))} unités ; {principal} est le premier produit "
                   + ("chaque mois. " if toujours_premier else "sur l'ensemble de la période. ")
                   + f"Le mois le plus fort est {mois_court(periodes[totaux.index(max(totaux))])} "
                   f"({fmt_nombre(max(totaux))} unités), le plus faible {mois_court(periodes[totaux.index(min(totaux))])} "
                   f"({fmt_nombre(min(totaux))} unités).")
    return Graphique("ventes_produits", "Ventes mensuelles par produit (unités)", _rendre(dessin, 3.0),
                     commentaire, _source(audit, VENTES_TOTALES))


def graphique_mix_produits(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    lignes = sorted((l for l in audit.produits if l.ventes > 0 and l.part_volumes is not None),
                    key=lambda l: l.part_volumes or 0)
    if len(lignes) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        y = list(range(len(lignes)))
        parts = [l.part_volumes or 0.0 for l in lignes]
        ax.barh(y, parts, height=0.55, color=couleur, edgecolor="#FFFFFF", linewidth=0.8, zorder=2)
        for i, l in enumerate(lignes):
            ax.annotate(f"{fmt_pct(l.part_volumes)} · {fmt_nombre(l.ventes)} u", (parts[i], i), xytext=(4, 0),
                        textcoords="offset points", ha="left", va="center", fontsize=7.5, color=ENCRE)
        ax.set_yticks(y, [l.produit for l in lignes])
        ax.set_xlim(0, max(parts) * 1.35)
        ax.xaxis.set_major_locator(_graduations())
        ax.xaxis.set_major_formatter(_axe_pct())
        ax.grid(axis="x", color=GRILLE, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(length=0)
        ax.spines["bottom"].set_visible(False)

    top = lignes[-1]
    commentaire = (f"{top.produit} représente {fmt_pct(top.part_volumes)} des unités vendues sur la période ; "
                   "le classement permet de juger la concentration de la gamme.")
    return Graphique("mix_produits", "Répartition des ventes par produit (part des unités)", _rendre(dessin, 2.4),
                     commentaire, _source(audit, VENTES_TOTALES))


def graphique_resultat(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    serie = audit.serie(RESULTAT)
    if len(serie) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)
    periodes = [p for p, _ in serie]
    valeurs = [v for _, v in serie]
    maxi = max(max(valeurs), 0.0)
    mini = min(min(valeurs), 0.0)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = list(range(len(valeurs)))
        couleurs = [couleur if v >= 0 else COULEUR_NEGATIF for v in valeurs]
        ax.bar(x, valeurs, width=0.58, color=couleurs, edgecolor="#FFFFFF", linewidth=0.8, zorder=2)
        ax.axhline(0, color=ENCRE_SECONDAIRE, linewidth=0.8, zorder=3)
        for i, v in enumerate(valeurs):
            if v < 0 or i == valeurs.index(max(valeurs)):
                ax.annotate(fmt_compact_euros(v), (i, v), xytext=(0, 3 if v >= 0 else -3), textcoords="offset points",
                            ha="center", va="bottom" if v >= 0 else "top", fontsize=7.5, color=ENCRE)
        ax.set_xticks(x, _etiquettes_mois(periodes))
        _style_axes(ax, maxi, _axe_euros(max(abs(maxi), abs(mini))), mini=mini)
        marge = (maxi - mini) * 0.15 or 1.0
        ax.set_ylim(mini - (marge if mini < 0 else 0), maxi + marge)
        if mini < 0:
            ax.spines["bottom"].set_visible(False)

    pertes = [p for p, v in serie if v < 0]
    commentaire = (f"Résultat net cumulé : {fmt_euros(sum(valeurs))}. "
                   + (f"{len(pertes)} mois en perte ({', '.join(mois_court(p) for p in pertes)}), en rouge sous l'axe."
                      if pertes else "Aucun mois déficitaire sur la période."))
    return Graphique("resultat", "Résultat net mensuel", _rendre(dessin), commentaire, _source(audit, RESULTAT))


def graphique_tresorerie(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    serie = audit.serie(TRESORERIE)
    if len(serie) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)
    periodes = [p for p, _ in serie]
    valeurs = [v for _, v in serie]
    maxi, mini = max(valeurs), min(valeurs)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = list(range(len(valeurs)))
        ax.plot(x, valeurs, color=couleur, linewidth=1.8, solid_capstyle="round", zorder=2)
        ax.plot(x, valeurs, "o", color=couleur, markersize=4, markeredgecolor="#FFFFFF", markeredgewidth=1.2, zorder=3)
        if mini < 0:
            ax.axhline(0, color=COULEUR_NEGATIF, linewidth=0.8, zorder=1)
            ax.spines["bottom"].set_visible(False)
        ax.annotate(fmt_compact_euros(valeurs[-1]), (x[-1], valeurs[-1]), xytext=(-4, 6), textcoords="offset points",
                    ha="right", va="bottom", fontsize=7.5, color=ENCRE)
        ax.set_xticks(x, _etiquettes_mois(periodes))
        _style_axes(ax, maxi, _axe_euros(max(abs(maxi), abs(mini))), mini=mini)
        if mini >= 0:
            ax.set_ylim(0, maxi * 1.18)
        else:
            ax.set_ylim(mini * 1.2, maxi * 1.18)

    commentaire = (f"La trésorerie de fin de mois évolue de {fmt_euros(valeurs[0])} ({mois_court(periodes[0])}) à "
                   f"{fmt_euros(valeurs[-1])} ({mois_court(periodes[-1])}) ; point bas : {fmt_euros(mini)} en "
                   f"{mois_court(periodes[valeurs.index(mini)])}.")
    return Graphique("tresorerie", "Trésorerie de fin de mois", _rendre(dessin), commentaire, _source(audit, TRESORERIE))


def graphique_pdm(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    serie = audit.serie(PDM)
    if len(serie) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)
    periodes = [p for p, _ in serie]
    valeurs = [v for _, v in serie]

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = list(range(len(valeurs)))
        ax.plot(x, valeurs, color=couleur, linewidth=1.8, solid_capstyle="round", zorder=2)
        ax.plot(x, valeurs, "o", color=couleur, markersize=4, markeredgecolor="#FFFFFF", markeredgewidth=1.2, zorder=3)
        for i in (0, len(valeurs) - 1):
            ax.annotate(fmt_pct(valeurs[i], 1), (i, valeurs[i]), xytext=(0, 6), textcoords="offset points",
                        ha="center", va="bottom", fontsize=7.5, color=ENCRE)
        ax.set_xticks(x, _etiquettes_mois(periodes))
        ax.yaxis.set_major_locator(_graduations())
        ax.yaxis.set_major_formatter(_axe_pct())
        ax.grid(axis="y", color=GRILLE, linewidth=0.6)
        ax.set_axisbelow(True)
        bas, haut = min(valeurs), max(valeurs)
        ecart = max(haut - bas, 1.0)
        ax.set_ylim(max(0.0, bas - ecart * 0.6), haut + ecart * 0.6)

    variation = valeurs[-1] - valeurs[0]
    commentaire = (f"La part de marché passe de {fmt_pct(valeurs[0], 2)} à {fmt_pct(valeurs[-1], 2)} "
                   f"({'+' if variation > 0 else ''}{fmt_nombre(variation, 2)} point(s)). L'axe ne part pas de zéro "
                   "pour rendre visibles les variations : lire les valeurs indiquées.")
    return Graphique("pdm", "Part de marché globale", _rendre(dessin), commentaire, _source(audit, PDM))


def graphique_ateliers(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    mois = [m for m in audit.mois if m.utilisation]
    if len(mois) < 2:
        return None
    periodes = [m.periode for m in mois]
    ateliers = [a for a in ATELIERS if any(a in m.utilisation for m in mois)]

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        x = list(range(len(mois)))
        ax.axhline(100, color=ENCRE_SECONDAIRE, linewidth=0.8, zorder=1)
        ax.annotate("100 % = saturation", (0, 100), xytext=(0, 3), textcoords="offset points", fontsize=7,
                    color=ENCRE_SECONDAIRE, va="bottom")
        for atelier in ateliers:
            valeurs = [m.utilisation.get(atelier) for m in mois]
            xs = [i for i, v in zip(x, valeurs) if v is not None]
            ys = [v * 100 for v in valeurs if v is not None]
            ax.plot(xs, ys, color=COULEUR_ATELIER[atelier], linewidth=1.8, label=atelier, zorder=2)
            ax.plot(xs[-1:], ys[-1:], "o", color=COULEUR_ATELIER[atelier], markersize=4.5,
                    markeredgecolor="#FFFFFF", markeredgewidth=1.2, zorder=3)
        ax.set_xticks(x, _etiquettes_mois(periodes))
        ax.set_ylim(0, 115)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
        ax.yaxis.set_major_formatter(_axe_pct())
        ax.grid(axis="y", color=GRILLE, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(ateliers), handlelength=1.4, borderaxespad=0.2)

    s = audit.synthese
    goulot = max(s.utilisation_moyenne, key=lambda a: s.utilisation_moyenne[a]) if s.utilisation_moyenne else ateliers[0]
    commentaire = (f"L'atelier {goulot} est le plus chargé ({fmt_pct(s.utilisation_moyenne.get(goulot, 0) * 100, 0)} en "
                   "moyenne) : c'est lui qui borne la production lorsque la demande augmente.")
    return Graphique("ateliers", "Taux d'utilisation des ateliers", _rendre(dessin), commentaire,
                     _source(audit, "utilisation"))


def graphique_concurrence(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    lignes = [r for r in audit.concurrence if _float(r.get("part_marche")) is not None]
    if len(lignes) < 2:
        return None
    couleur = couleur_lisible(charte.primaire)
    lignes = sorted(lignes, key=lambda r: _float(r.get("part_marche")) or 0.0)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        y = list(range(len(lignes)))
        parts = [_float(r.get("part_marche")) or 0.0 for r in lignes]
        propres = [_float(r.get("entreprise")) == float(audit.numero) for r in lignes]
        ax.barh(y, parts, height=0.55, color=[couleur if p else COULEUR_NEUTRE for p in propres],
                edgecolor="#FFFFFF", linewidth=0.8, zorder=2)
        for i, v in enumerate(parts):
            ax.annotate(fmt_pct(v, 1), (v, i), xytext=(4, 0), textcoords="offset points", ha="left", va="center",
                        fontsize=7.5, color=ENCRE)
        ax.set_yticks(y, [f"Entreprise {int(_float(r.get('entreprise')) or 0)}" + (" (nous)" if p else "")
                          for r, p in zip(lignes, propres)])
        for etiquette, p in zip(ax.get_yticklabels(), propres):
            if p:
                etiquette.set_fontweight("bold")
                etiquette.set_color(ENCRE)
        ax.set_xlim(0, max(parts) * 1.25)
        ax.xaxis.set_major_locator(_graduations())
        ax.xaxis.set_major_formatter(_axe_pct())
        ax.grid(axis="x", color=GRILLE, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set_visible(False)

    classement = sorted(lignes, key=lambda r: -(_float(r.get("part_marche")) or 0.0))
    rang = next((i + 1 for i, r in enumerate(classement) if _float(r.get("entreprise")) == float(audit.numero)), None)
    commentaire = (f"L'entreprise (en couleur) se classe {rang}ᵉ sur {len(lignes)} entreprises observées en "
                   f"{mois_court(audit.concurrence_periode or audit.fin)}." if rang else
                   "Parts de marché publiées par le benchmark concurrentiel.")
    return Graphique("concurrence", "Parts de marché des entreprises du marché", _rendre(dessin, 2.4), commentaire,
                     "Source : études de veille concurrentielle")


def graphique_positionnement(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    """Carte de positionnement : prix moyen et part de marché de chaque entreprise du benchmark."""
    points = [(int(_float(r.get("entreprise")) or 0), _float(r.get("prix")), _float(r.get("part_marche")))
              for r in audit.concurrence]
    points = [(e, p, m) for e, p, m in points if e and p is not None and m is not None]
    if len(points) < 3 or not any(e == audit.numero for e, _, _ in points):
        return None
    couleur = couleur_lisible(charte.primaire)
    prix = sorted(p for _, p, _ in points)
    parts = sorted(m for _, _, m in points)
    mediane_prix = prix[len(prix) // 2] if len(prix) % 2 else (prix[len(prix) // 2 - 1] + prix[len(prix) // 2]) / 2
    mediane_pdm = parts[len(parts) // 2] if len(parts) % 2 else (parts[len(parts) // 2 - 1] + parts[len(parts) // 2]) / 2
    nous = next((p, m) for e, p, m in points if e == audit.numero)

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        ax.axvline(mediane_prix, color=GRILLE, linewidth=1.0, linestyle=(0, (4, 3)), zorder=1)
        ax.axhline(mediane_pdm, color=GRILLE, linewidth=1.0, linestyle=(0, (4, 3)), zorder=1)
        for e, p, m in points:
            propre = e == audit.numero
            ax.scatter([p], [m], s=70 if propre else 34, color=couleur if propre else COULEUR_NEUTRE,
                       edgecolors="#FFFFFF", linewidths=1.0, zorder=3 if propre else 2)
            ax.annotate(f"Entreprise {e} (nous)" if propre else f"E{e}", (p, m), xytext=(6, 0),
                        textcoords="offset points", va="center", fontsize=7.5 if propre else 7,
                        color=ENCRE if propre else ENCRE_SECONDAIRE, fontweight="bold" if propre else "normal")
        ecart_p = (max(prix) - min(prix)) or max(prix) * 0.1 or 1.0
        ax.set_xlim(min(prix) - ecart_p * 0.12, max(prix) + ecart_p * 0.3)
        ecart_m = (max(parts) - min(parts)) or 1.0
        ax.set_ylim(max(0.0, min(parts) - ecart_m * 0.15), max(parts) + ecart_m * 0.15)
        ax.xaxis.set_major_formatter(_axe_euros(max(prix)))
        ax.yaxis.set_major_formatter(_axe_pct())
        ax.xaxis.set_major_locator(_graduations())
        ax.yaxis.set_major_locator(_graduations())
        ax.set_xlabel("Prix moyen de vente", fontsize=7.5)
        ax.set_ylabel("Part de marché", fontsize=7.5)
        ax.tick_params(length=0)
        ax.spines["left"].set_visible(True)
        ax.text(1.0, 1.0, "pointillés : médianes du marché", transform=ax.transAxes, ha="right", va="bottom",
                fontsize=6.5, color=ENCRE_SECONDAIRE, style="italic")

    position_prix = "au-dessus de" if nous[0] > mediane_prix else ("en dessous de" if nous[0] < mediane_prix else "égal à")
    position_pdm = "au-dessus de" if nous[1] > mediane_pdm else ("en dessous de" if nous[1] < mediane_pdm else "égale à")
    commentaire = (f"En {mois_court(audit.concurrence_periode or audit.fin)}, notre prix moyen ({fmt_euros(nous[0])}) est "
                   f"{position_prix} la médiane du marché ({fmt_euros(mediane_prix)}) et notre part de marché "
                   f"({fmt_pct(nous[1], 2)}) {position_pdm} la médiane ({fmt_pct(mediane_pdm, 2)}) : la carte situe le "
                   "positionnement choisi face aux concurrents.")
    return Graphique("positionnement", "Carte de positionnement : prix et part de marché", _rendre(dessin, 3.0),
                     commentaire, "Source : études de veille concurrentielle")


def graphique_decomposition(audit: AuditAnnuel, charte: Charte) -> Graphique | None:
    d = audit.decomposition
    if d is None or abs(d.ecart) < 1:
        return None
    couleur = couleur_lisible(charte.primaire)
    etapes = [("CA " + mois_court(d.periode_a), d.ca_a, "total"), ("Effet volume", d.effet_volume, "delta"),
              ("Effet mix", d.effet_mix, "delta"), ("Effet prix", d.effet_prix, "delta"),
              ("CA " + mois_court(d.periode_b), d.ca_b, "total")]

    def dessin(fig: Figure) -> None:
        ax = fig.add_subplot(111)
        niveau = 0.0
        for i, (nom, valeur, nature) in enumerate(etapes):
            if nature == "total":
                ax.bar(i, valeur, width=0.55, color=ENCRE_SECONDAIRE, zorder=2)
                niveau = valeur
                texte = fmt_compact_euros(valeur)
                y_txt = valeur
            else:
                bas = niveau if valeur >= 0 else niveau + valeur
                ax.bar(i, abs(valeur), bottom=bas, width=0.55, color=couleur if valeur >= 0 else COULEUR_NEGATIF, zorder=2)
                niveau += valeur
                texte = fmt_euros_signe(valeur)
                y_txt = max(niveau, niveau - valeur)
            ax.annotate(texte, (i, y_txt), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.5, color=ENCRE)
        ax.set_xticks(range(len(etapes)), [e[0] for e in etapes])
        hauts = [d.ca_a, d.ca_b, d.ca_a + d.effet_volume, d.ca_a + d.effet_volume + d.effet_mix]
        _style_axes(ax, max(hauts), _axe_euros(max(hauts)))

    commentaire = (f"Entre {mois_court(d.periode_a)} et {mois_court(d.periode_b)}, le CA théorique (prix catalogue × "
                   f"volumes) varie de {fmt_euros_signe(d.ecart)} : effet volume {fmt_euros_signe(d.effet_volume)}, "
                   f"effet mix {fmt_euros_signe(d.effet_mix)}, effet prix {fmt_euros_signe(d.effet_prix)}.")
    return Graphique("decomposition", "Décomposition de l'évolution du CA (prix / volume / mix)", _rendre(dessin),
                     commentaire, "Source : prix décidés et ventes par produit (calcul ERP, hors remises de canal)")


GRAPHIQUES: tuple[Callable[[AuditAnnuel, Charte], Graphique | None], ...] = (
    graphique_ca_mensuel,
    graphique_ca_cumule,
    graphique_ventes_produits,
    graphique_mix_produits,
    graphique_decomposition,
    graphique_pdm,
    graphique_concurrence,
    graphique_positionnement,
    graphique_resultat,
    graphique_tresorerie,
    graphique_ateliers,
)


def graphiques_rapport(audit: AuditAnnuel, charte: Charte = CHARTE_DEFAUT) -> dict[str, Graphique]:
    """Tous les graphiques calculables, indexés par clé (``{}`` si matplotlib manque)."""
    if not MATPLOTLIB_DISPONIBLE:
        return {}
    out: dict[str, Graphique] = {}
    for fabrique in GRAPHIQUES:
        try:
            g = fabrique(audit, charte)
        except Exception:
            g = None
        if g is not None:
            out[g.cle] = g
    return out
