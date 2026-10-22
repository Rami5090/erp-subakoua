"""Page 7 — Prévision des cours (actions, obligations, prix de vente).

Le module prédit la valeur d'une série de prix à la période suivante, à
partir de son historique : le « cours du jour » des périodes précédentes.

Données utilisées
-----------------
* **Actions & obligations** : les cours A1/A2/A3 et O1/O2/O3 publiés chaque
  mois par les études gratuites « Portefeuille actions » et « Portefeuille
  obligations ».
* **Prix de vente** : le prix fixé chaque mois par les anciennes décisions
  (`Decisions_Marketing`) — le cours « du jour » de l'entreprise.

La méthode retenue est choisie par validation hors-échantillon parmi des
méthodes simples et explicables ; la bande affichée est une bande d'erreur
empirique (erreur du backtest), pas un intervalle de confiance statistique.
"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import text

from auth import setup as auth_setup
from constants import PRODUCT_KEY_TO_NAME, PRODUCT_KEYS, TOURS_PERIODES
from cours_engine import (
    PricePoint,
    comparer_cours,
    forecast_cours_set,
    historique_cours,
    trier_cours,
)
from db_utils import get_read_engine
from livrable_finance import PRIX_TO_PRODUIT


def _prix_marche_par_produit(prix_marche: dict[str, dict[str, float]]) -> dict[str, list[PricePoint]]:
    """Séries de prix du marché par produit (un point par période publiée)."""
    out: dict[str, list[PricePoint]] = {}
    for periode, prix in (prix_marche or {}).items():
        for produit, valeur in prix.items():
            out.setdefault(produit, []).append(PricePoint(periode=periode, valeur=valeur))
    for produit in out:
        out[produit] = trier_cours(out[produit])
    return out

st.set_page_config(page_title="Prévision des cours", layout="wide")
auth_setup()
st.title("📈 Prévision des cours — actions, obligations et prix de vente")
st.caption(
    "Quel cours attendre à la période suivante ? La méthode est choisie par "
    "validation hors-échantillon et la bande d'erreur est empirique : ce n'est "
    "pas un intervalle de confiance statistique."
)

engine = get_read_engine()
if engine is None:
    st.error("Connexion Aiven indisponible.")
    st.stop()

TOURS = sorted(TOURS_PERIODES)
tour = st.selectbox(
    "Période analysée",
    TOURS,
    index=TOURS.index(8) if 8 in TOURS else 0,
    format_func=lambda t: f"Tour {t} — {TOURS_PERIODES[t]}",
)
periode = TOURS_PERIODES[int(tour)]


def _decode(value):
    for _ in range(2):
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return {}
        else:
            break
    return value if isinstance(value, dict) else {}


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
    except Exception as exc:
        st.warning(f"Lecture impossible : {type(exc).__name__}.")
        return []


def _fmt_n(valeur, dec: int = 2) -> str:
    try:
        return f"{float(valeur):,.{dec}f}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _fmt_eur(valeur, dec: int = 2) -> str:
    return f"{_fmt_n(valeur, dec)} €"


# --------------------------------------------------------------------------- données
rows_etudes = _rows(
    "SELECT periode, study_id, label, payload, read_allowed FROM erp_etudes ORDER BY id ASC"
)
etudes_par_periode: dict[str, dict] = {}
libelles_etudes: dict[str, str] = {}
etudes_non_lisibles: list[dict] = []
for r in rows_etudes:
    cle = str(r.get("study_id") or "")
    if not r.get("read_allowed"):
        etudes_non_lisibles.append({
            "Période": str(r.get("periode") or ""),
            "Identifiant": cle,
            "Libellé": str(r.get("label") or ""),
        })
        continue
    etudes_par_periode.setdefault(str(r.get("periode") or ""), {})[cle] = _decode(r.get("payload"))
    if cle and r.get("label"):
        libelles_etudes[cle] = str(r.get("label"))

rows_mkt = _rows(
    "SELECT Mois, Prix_S3, Prix_I3, Prix_S5, Prix_I5, Prix_I7 FROM Decisions_Marketing ORDER BY Tour_ID"
)

# Prix du marché publiés chaque mois (« Ventes sur le marché concurrentiel ») :
# c'est le cours « du jour » du marché, disponible sans saisie manuelle.
rows_expert = _rows(
    "SELECT periode, contenu FROM erp_donnees "
    "WHERE type_donnee='etat_actuel' AND module='expert_comptable' ORDER BY id"
)
#: Produits rangés par prix croissant : le marché publie les prix dans cet ordre
#: (Shorty 3 < Integral 3 < Shorty 5 < Integral 5 < Integral 7). Le mapping se
#: fait donc par **rang**, et non par prix exact : une évolution du prix de
#: marché ne fait pas disparaître la série.
_ORDRE_PAR_PRIX = [produit for _p, produit in sorted(PRIX_TO_PRODUIT.items())]
prix_marche: dict[str, dict[str, float]] = {}
for _r in rows_expert:
    _expert = _decode(_r.get("contenu"))
    _detail = (_expert.get("Détail des comptes", {}) or {}).get("Expert-comptable", {}) or {}
    _globale = _detail.get("Ventes sur le marché concurrentiel", []) or []
    _valeurs = sorted({
        float(_x.get("Prix HT (€)", 0) or 0)
        for _x in _globale
        if str(_x.get("Combinaisons", "")).isdigit()
    })
    if len(_valeurs) == len(_ORDRE_PAR_PRIX):
        _per = str(_r.get("periode") or "")
        prix_marche[_per] = dict(zip(_ORDRE_PAR_PRIX, _valeurs))

series: dict[str, tuple[str, list[PricePoint]]] = {}

# --- cours des titres (études « Portefeuille actions / obligations »)
for nom, points in historique_cours(etudes_par_periode, libelles=libelles_etudes).items():
    famille = "Action" if nom.startswith("A") else ("Obligation" if nom.startswith("O") else "Titre")
    series[f"titre_{nom}"] = (f"{famille} {nom}", points)

# --- prix de vente des anciennes décisions
for cle in PRODUCT_KEYS:
    produit = PRODUCT_KEY_TO_NAME[cle]
    points = []
    for r in rows_mkt:
        libelle = str(r.get("Mois") or "")
        valeur = r.get(f"Prix_{cle.upper()}")
        try:
            valeur = float(valeur)
        except (TypeError, ValueError):
            continue
        if not libelle or valeur <= 0:
            continue
        points.append(PricePoint(periode=libelle, valeur=valeur))
    if points:
        series[f"prix_{cle}"] = (f"Prix de vente {produit}", points)

# --- prix du marché (cours « du jour » publié par le portail)
for produit, points in _prix_marche_par_produit(prix_marche).items():
    series[f"marche_{produit}"] = (f"Prix du marché {produit}", points)

if not series:
    st.info(
        "Aucun historique de cours exploitable : les études « Portefeuille actions / obligations » "
        "n'ont pas été synchronisées, et aucune décision de prix n'est enregistrée. "
        "Le module affiche les avertissements plutôt que des chiffres."
    )
    st.stop()

resultat = forecast_cours_set(series)

# --------------------------------------------------------------------------- titres
st.subheader("1 · Cours des titres")
st.caption(
    "Le cours du jour de chaque période passée provient des études « Portefeuille actions » "
    "(A1/A2/A3) et « Portefeuille obligations » (O1/O2/O3)."
)

titres = [c for c in resultat.cours if c.cle.startswith("titre_")]
if not titres:
    st.warning(
        "Aucun cours de titre exploitable : les études « Portefeuille actions » et "
        "« Portefeuille obligations » sont gratuites — achetez-les et synchronisez-les "
        "depuis la page Scraper pour alimenter ce module."
    )
    # Diagnostic : la cause peut être l'absence des études, leur structure, ou
    # le flag `read_allowed` (étude synchronisée mais non lisible).
    with st.expander("🔍 Diagnostic des données", expanded=True):
        st.write(
            f"Études en base : **{len(rows_etudes)}** · lisibles : "
            f"**{len(rows_etudes) - len(etudes_non_lisibles)}** · "
            f"périodes couvertes : **{len(etudes_par_periode)}**."
        )
        if etudes_non_lisibles:
            st.error(
                f"⚠️ **{len(etudes_non_lisibles)} étude(s) synchronisées mais non lisibles** "
                "(`read_allowed = 0`) : elles ne sont pas exploitées par le module. "
                "Resynchronisez-les depuis la page Scraper (onglet « 🔁 Acquérir »)."
            )
            st.dataframe(pd.DataFrame(etudes_non_lisibles).head(30), width="stretch", hide_index=True)
        if rows_etudes:
            apercu = pd.DataFrame([
                {
                    "Période": str(r.get("periode") or ""),
                    "Identifiant": str(r.get("study_id") or ""),
                    "Libellé": str(r.get("label") or ""),
                    "Lisible": bool(r.get("read_allowed")),
                    "Taille du payload": len(str(r.get("payload") or "")),
                }
                for r in rows_etudes
            ])
            st.dataframe(apercu.head(40), width="stretch", hide_index=True)
            st.caption(
                "Les études de portefeuille de titres sont reconnues à leur identifiant connu "
                "(« pebaifpuao », « pebaifpuoo ») ou à leur libellé (« Portefeuille actions »). "
                "Si elles sont listées ici mais sans cours exploité, leur structure diffère : "
                "le module le signale plutôt que d'inventer des valeurs."
            )
        else:
            st.info(
                "Aucune étude en base : les studies n'ont pas été synchronisées, ou "
                "`read_allowed` vaut 0."
            )
else:
    for i in range(0, len(titres), 3):
        for cours in titres[i:i + 3]:
            with st.container(border=True):
                st.markdown(f"**{cours.libelle}**")
                c1, c2, c3 = st.columns(3)
                c1.metric("Dernier cours", _fmt_eur(cours.dernier))
                c2.metric(
                    "Prévision période suivante",
                    _fmt_eur(cours.prevision) if cours.prevision is not None else "—",
                    delta=(
                        f"{cours.variation_prevue:+,.2f} €".replace(",", " ")
                        if cours.variation_prevue is not None else None
                    ),
                )
                c3.metric(
                    "Bande d'erreur",
                    f"± {_fmt_eur(cours.erreur_moyenne)}" if cours.erreur_moyenne is not None else "—",
                    help="Erreur absolue moyenne de la méthode en validation hors-échantillon.",
                )
                st.caption(
                    f"Méthode : **{cours.methode}** · confiance : **{cours.confiance}** · "
                    f"{len(cours.serie)} cours observés."
                )
                for w in cours.avertissements:
                    st.warning(f"⚠️ {w}")

    st.dataframe(pd.DataFrame([
        {
            "Titre": c.libelle,
            "Dernier cours (€)": round(c.dernier, 2) if c.dernier is not None else None,
            "Prévision (€)": round(c.prevision, 2) if c.prevision is not None else None,
            "Variation (€)": round(c.variation_prevue, 2) if c.variation_prevue is not None else None,
            "Bande d'erreur (€)": round(c.erreur_moyenne, 2) if c.erreur_moyenne is not None else None,
            "Dérive du dernier mois (%)": round(c.derive_relative, 2) if c.derive_relative is not None else None,
            "Méthode": c.methode,
            "Confiance": c.confiance,
        }
        for c in titres
    ]), width="stretch", hide_index=True)

# --------------------------------------------------------------------------- prix de vente
st.subheader("2 · Cours des prix (marché et décisions de l'entreprise)")
st.caption(
    "Le cours « du jour » du marché est publié chaque mois par le portail ; le prix de vente "
    "vient des décisions de l'entreprise. La prévision donne la trajectoire attendue."
)

prix_cours = [c for c in resultat.cours if c.cle.startswith("prix_") or c.cle.startswith("marche_")]
if not prix_cours:
    st.warning(
        "Aucun historique de prix exploitable : ni prix du marché publiés "
        "(« Ventes sur le marché concurrentiel »), ni décisions de prix saisies "
        "dans « Données réelles → Marketing »."
    )
elif all(c.prevision is None for c in prix_cours if c.cle.startswith("prix_")):
    st.info(
        "Les prix de vente de l'entreprise n'ont pas d'historique exploitable : chaque mois "
        "enregistré dans « Données réelles → Marketing » ajoute un point à la série. "
        "En attendant, la trajectoire des prix du marché reste disponible ci-dessus."
    )
else:
    st.dataframe(pd.DataFrame([
        {
            "Source": "Prix du marché" if c.cle.startswith("marche_") else "Décision de l'entreprise",
            "Produit": c.libelle.split(" ", 2)[-1] if c.cle.startswith("marche_") else c.libelle.replace("Prix de vente ", ""),
            "Dernier prix (€)": round(c.dernier, 2) if c.dernier is not None else None,
            "Prévision (€)": round(c.prevision, 2) if c.prevision is not None else None,
            "Variation (%)": round(comparer_cours(c.serie, c.prevision), 2) if comparer_cours(c.serie, c.prevision) is not None else None,
            "Bande d'erreur (€)": round(c.erreur_moyenne, 2) if c.erreur_moyenne is not None else None,
            "Méthode": c.methode,
            "Confiance": c.confiance,
        }
        for c in prix_cours
    ]), width="stretch", hide_index=True)

# --------------------------------------------------------------------------- graphique
st.subheader("3 · Trajectoire observée et prévision")

choix = st.multiselect(
    "Séries affichées",
    options=[c.libelle for c in resultat.cours],
    default=[c.libelle for c in titres][:2] or [c.libelle for c in resultat.cours][:2],
)
if choix:
    lignes = []
    for c in resultat.cours:
        if c.libelle not in choix:
            continue
        for p in c.serie:
            lignes.append({"Série": c.libelle, "Période": p.periode, "Cours (€)": p.valeur, "Type": "Observé"})
        if c.prevision is not None:
            lignes.append({
                "Série": c.libelle,
                "Période": "Période suivante",
                "Cours (€)": c.prevision,
                "Type": "Prévision",
            })
    if lignes:
        cadre = pd.DataFrame(lignes)
        graphique = (
            alt.Chart(cadre)
            .mark_line(point=True)
            .encode(
                x=alt.X("Période:N", sort=None, title=None),
                y=alt.Y("Cours (€):Q", title="Cours (€)"),
                color=alt.Color("Série:N", title=None),
                tooltip=["Série:N", "Période:N", "Cours (€):Q", "Type:N"],
            )
            .properties(height=320)
        )
        st.altair_chart(graphique, width="stretch")

# --------------------------------------------------------------------------- méthode
with st.expander("🧭 Méthode et limites", expanded=False):
    st.markdown(
        """
**Méthodes comparées** (la meilleure en validation hors-échantillon est retenue) :

| Méthode | Principe |
|---|---|
| `dernier` | le dernier cours se maintient — référence naïve |
| `derive` | la variation moyenne des derniers cours est reconduite |
| `derive_amortie` | idem, tendance amortie (évite l'emballement) |
| `moyenne` | moyenne des 3 derniers cours |
| `niveau` | moyenne de toute la série (retour à la moyenne) |

**Validation** : pour chaque point à partir du troisième, la méthode est appliquée sur
l'historique court puis comparée au cours réellement observé ; l'erreur absolue moyenne
(MAE) sert de bande d'erreur empirique.

**Limites assumées** :
- la bande affichée n'est **pas** un intervalle de confiance statistique ;
- une série de moins de 3 cours ne produit **aucune** prévision (avertissement affiché) ;
- un cours ne peut pas être négatif : une prévision négative est ramenée à zéro ;
- les cours sont ceux publiés par le portail : aucune donnée externe n'est introduite.
"""
    )
    if resultat.avertissements:
        st.warning(
            "⚠️ **Avertissements du module**\n\n"
            + "\n\n".join(f"- {w}" for w in resultat.avertissements)
        )

st.caption(
    f"Période analysée : {periode} · {len(etudes_par_periode)} périodes d'études · "
    f"{len(rows_mkt)} décisions de prix enregistrées."
)
