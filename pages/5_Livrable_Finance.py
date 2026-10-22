"""Page 5 — Livrable Finance mensuel (Mission spécifique Finance).

Lecture seule pour tous : génère le pré-rempli du document mensuel
(bilan, résultat, marges, coordination, budget) depuis Aiven, sans saisie.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal

import pandas as pd
import streamlit as st

from livrable_finance import (
    build_bilan,
    build_marges,
    couts_from_cost_study,
    couts_detail_from_cost_study,
    missing_studies,
    parse_simplified_result,
    pdf_field_values,
    fill_pdf,
    LivrableFinance,
)
import document_optimizer as doc_optimizer
from constants import TOURS_PERIODES, period_label_to_code
from period_context import match_period
from db_utils import get_read_engine
from auth import setup as auth_setup, is_editor

st.set_page_config(page_title="Livrable Finance mensuel", layout="wide")
auth_setup()
st.title("📄 Livrable Finance mensuel — Mission spécifique")
st.caption("Pré-rempli automatique depuis Aiven : bilan, résultat, marges, coordination. Les hypothèses et SPECIMEN sont toujours signalés.")

engine = get_read_engine()
if engine is None:
    st.error("Connexion Aiven indisponible.")
    st.stop()


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


def _contenu(periode: str, module: str):
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, periode FROM erp_donnees WHERE type_donnee='etat_actuel' AND module=:m ORDER BY id"),
                {"m": module},
            ).all()
            ident = match_period([(r[0], r[1]) for r in rows], periode)
            if ident is None:
                return {}
            row = conn.execute(text("SELECT contenu FROM erp_donnees WHERE id=:i"), {"i": ident}).mappings().first()
            return _decode(row.get("contenu")) if row else {}
    except Exception as exc:
        st.warning(f"Lecture `erp_donnees` impossible : {type(exc).__name__}.")
        return {}


def _etude(periode: str, study_id: str):
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT payload FROM erp_etudes WHERE study_id=:s AND periode=:p ORDER BY id DESC LIMIT 1"),
                {"s": study_id, "p": periode},
            ).all()
            # match_period tolérant si le libellé diffère légèrement
            if not rows:
                all_rows = conn.execute(text("SELECT periode, payload FROM erp_etudes WHERE study_id=:s"), {"s": study_id}).all()
                cands = [(i, r[0]) for i, r in enumerate(all_rows)]
                idx = match_period(cands, periode)
                if idx is None:
                    return {}
                return _decode(all_rows[idx][1])
            return _decode(rows[0][0])
    except Exception as exc:
        st.warning(f"Lecture `erp_etudes` impossible : {type(exc).__name__}.")
        return {}


_tours = sorted(TOURS_PERIODES)
tour = st.selectbox(
    "Période du livrable",
    _tours,
    index=_tours.index(8),
    format_func=lambda t: f"Tour {t} — {TOURS_PERIODES[t]}",
)
periode = TOURS_PERIODES[int(tour)]

expert = _contenu(periode, "expert_comptable")
if not expert:
    st.warning(f"Aucun module expert_comptable en base pour {periode}.")
    st.stop()

# --- Bilan : Synthèse / Expert-comptable #3 / Tableau_1 (nom d'onglet variable) ---
bilan_rows: list = []
syn = expert.get("Synthèse", {}) if isinstance(expert, dict) else {}
for tab, content in syn.items():
    if isinstance(content, dict) and isinstance(content.get("Tableau_1"), list):
        rows = content["Tableau_1"]
        if rows and isinstance(rows[0], dict) and "Actif" in rows[0] and "Passif" in rows[0]:
            bilan_rows = rows
            st.caption(f"Bilan source : Synthèse / {tab} / Tableau_1 ({len(rows)} lignes).")
            break
bilan = build_bilan(bilan_rows or [])

# --- Ratios ---
ratios_m = ratios_c = {}
for tab, content in syn.items():
    if isinstance(content, dict):
        if isinstance(content.get("Ratios_1"), dict):
            r = content["Ratios_1"].get("Indicateurs_MENSUEL", [{}])[0]
            ratios_m = r
        if isinstance(content.get("Ratios_2"), dict):
            r = content["Ratios_2"].get("Indicateurs_CUMULÉ", content["Ratios_2"].get("Indicateurs_CUMULE", [{}]))[0]
            ratios_c = r

# --- Ventes par canal (Détail des comptes) ---
prix = {"Shorty 3": Decimal("130"), "Integral 3": Decimal("205"), "Shorty 5": Decimal("240"),
        "Integral 5": Decimal("310"), "Integral 7": Decimal("360")}
prix_src = "prix catalogue observés Août (130/205/240/310/360)"
try:
    det = expert.get("Détail des comptes", {}).get("Expert-comptable", {})
    # table globale : prix + remise totale par combinaison (ordre prix croissant)
    glob = det.get("Ventes sur le marché concurrentiel", [])
    if isinstance(glob, list) and len(glob) >= 5:
        vals = sorted([float(x.get("Prix HT (€)", 0) or 0) for x in glob if str(x.get("Combinaisons", "")).isdigit()])
        if vals == [130.0, 205.0, 240.0, 310.0, 360.0]:
            prix = {"Shorty 3": Decimal("130"), "Integral 3": Decimal("205"), "Shorty 5": Decimal("240"),
                    "Integral 5": Decimal("310"), "Integral 7": Decimal("360")}
            prix_src = "Détail des comptes / Ventes sur le marché concurrentiel (période)"
except Exception:
    pass

# --- Coûts : étude du mois, sinon dernier mois antérieur ---
couts, cout_warns, couts_src = {}, [], periode
couts_detail: dict = {}
cost_payload = _etude(periode, "productionCostDetailStudy")
if not cost_payload or not cost_payload.get("productsProductionCostDetails"):
    for t in range(int(tour) - 1, -1, -1):
        if t not in TOURS_PERIODES:
            continue
        p = _etude(TOURS_PERIODES[t], "productionCostDetailStudy")
        if p and p.get("productsProductionCostDetails"):
            cost_payload = p
            couts_src = f"{TOURS_PERIODES[t]} (reconducté, {periode} non achetée)"
            break
if cost_payload:
    couts, cout_warns = couts_from_cost_study(cost_payload)
    det, det_warns = couts_detail_from_cost_study(cost_payload)
    couts_detail = det
    cout_warns = tuple(list(cout_warns) + [w for w in det_warns if w not in cout_warns])
    if couts_src == periode:
        couts_src = f"{periode}"

marges, marge_warns = build_marges(prix, Decimal("0"), Decimal("0.20"), couts)

# --- Flux / créances / résultat ---
bank = _etude(periode, "bankStatements")
cre = _etude(periode, "peeedccscl")
simplifie = parse_simplified_result(_etude(periode, "peeedscrsm"))
res_warns = [] if simplifie is not None else ["Compte de résultat non acheté (SPECIMEN) : net, CAF et ratios vides."]

liv = LivrableFinance(
    periode=periode, bilan=bilan,
    ca_mensuel=Decimal(str(ratios_m.get("Chiffre d'affaires", 0) or 0)),
    resultat_avant_impot_mensuel=Decimal(str(ratios_m.get("Résultat avant impôt", 0) or 0)),
    ca_cumule=Decimal(str(ratios_c.get("Chiffre d'affaires", 0) or 0)),
    resultat_cumule=Decimal(str(ratios_c.get("Résultat avant impôt", 0) or 0)),
    marges=tuple(marges),
    resultat=simplifie,
    flux_banque={"initial": Decimal(str(bank.get("initialBankAmount", 0) if bank else 0)),
                 "final": Decimal(str(bank.get("finalBankAmount", 0) if bank else 0))},
    creances={"total": Decimal(str(cre.get("totalcl", 0) if cre else 0)),
              "encaissement": Decimal(str(cre.get("encaissement", 0) if cre else 0))},
    couts_source_periode=couts_src,
    warnings=tuple(list(bilan.warnings) + list(cout_warns) + list(marge_warns) + res_warns),
)

if simplifie is not None:
    c0, c00 = st.columns(2)
    c0.metric("Résultat net mensuel", f"{simplifie.resultat_net:,.0f} €".replace(",", " "))
    c00.metric("CAF mensuelle", f"{simplifie.caf:,.0f} €".replace(",", " "))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Banque finale", f"{bilan.banque:,.0f} €".replace(",", " "))
c2.metric("BFRE", f"{bilan.bfre:,.0f} €".replace(",", " "))
c3.metric("CA mensuel", f"{liv.ca_mensuel:,.0f} €".replace(",", " "))
c4.metric("Coûts source", couts_src)

st.subheader("Marges sur coûts variables unitaires")
st.dataframe(pd.DataFrame([{
    "Produit": m.produit, "Prix HT": float(m.prix_ht), "Remise A": float(m.remise_a),
    "Remise B": float(m.remise_b), "Coût var.": float(m.cout_variable),
    "Marge A": float(m.marge_a), "Marge B": float(m.marge_b)} for m in marges]),
    hide_index=True, width="stretch")

st.subheader("Coordination (réponses en base)")
st.write(f"- TVA déductible : {bilan.tva_deductible} € / collectée : {bilan.tva_collectee} € "
         f"/ nette à payer mois+1 : {bilan.tva_collectee - bilan.tva_deductible} €")
st.write(f"- Créances clients : {liv.creances.get('total')} €, encaissé : {liv.creances.get('encaissement')} €")
st.write(f"- Banque : {liv.flux_banque.get('initial')} → {liv.flux_banque.get('final')} € "
         f"(TVA payée : {bank.get('vatPayment') if bank else '—'} €, investissements machines : "
         f"{bank.get('workstationPurchases') if bank else '—'})")

if liv.warnings:
    st.warning("Points de vigilance :\n- " + "\n- ".join(liv.warnings))
st.caption(f"Prix source : {prix_src}.")
st.download_button("⬇️ Télécharger le livrable (Markdown)", liv.markdown().encode("utf-8"),
                   file_name=f"livrable_finance_{periode.replace(' ', '_')}.md")

st.divider()
st.subheader("📕 Remplir le PDF « Mission spécifique Finance »")
st.caption("Remplit directement les cellules du PDF : diagnostic, marges, taux. "
           "Le rédactionnel (Réponses, Commentaires, Nos conclusions, équipe) n'est jamais écrasé. "
           "Les résultats nets/CAF non achetés (SPECIMEN) sont vidés pour ne pas laisser un ancien mois.")
default_template = "C:/Users/ramih/OneDrive/BUT MLT3/Redoublement/Mission specifique Finance.pdf"
uploaded = st.file_uploader("Template PDF « Mission spécifique Finance »", type=["pdf"],
                            help="Choisis le PDF vierge. Sinon, le chemin local par défaut est utilisé s'il existe.")
template_bytes: bytes | None = None
template_source = ""
if uploaded is not None:
    try:
        _lu = uploaded.read()
        template_bytes = _lu if isinstance(_lu, (bytes, bytearray)) else None
        template_source = f"fichier choisi : {uploaded.name}"
    except Exception:
        template_bytes = None
        template_source = ""
elif os.path.exists(default_template):
    with open(default_template, "rb") as _f:
        template_bytes = _f.read()
    template_source = "chemin local par défaut"
if template_source:
    st.caption(f"Template : {template_source}.")
if st.button("Générer le PDF rempli", type="primary", disabled=template_bytes is None):
    import tempfile
    alias = period_label_to_code(periode)  # ex. Année 1 - Août -> 0108
    values = pdf_field_values(liv, couts_detail, prix)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        out_path = tmp.name
    try:
        n, missing = fill_pdf(template_bytes, out_path, values)
        with open(out_path, "rb") as f:
            pdf_bytes = f.read()
        st.success(f"PDF rempli : {n} champs renseignés."
                   + (f" Champs non trouvés : {len(missing)}." if missing else ""))
        if missing:
            with st.expander("Champs non matchés (info)"):
                st.write(sorted(missing))
        st.download_button(
            "⬇️ Télécharger le PDF rempli",
            pdf_bytes,
            file_name=f"Mission_specifique_Finance_{alias}_rempli.pdf",
            mime="application/pdf",
        )
    except RuntimeError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Remplissage du PDF impossible : {type(exc).__name__}: {exc}")
    finally:
        try:
            os.unlink(out_path)
        except Exception:
            pass

st.divider()
st.subheader("📚 Documents manquants pour un livrable complet")
st.caption("Chaque étude manquante explique quelles cellules du PDF restent vides ou en fallback. "
           "Coche celles à acheter, puis le bouton t'emmène page Scraper avec la sélection pré-remplie "
           "(onglet « 🔁 Acquérir » : achat si besoin + synchronisation en un passage).")

try:
    with engine.connect() as _conn:
        from sqlalchemy import text as _text
        _have_rows = _conn.execute(
            _text("SELECT study_id FROM erp_etudes WHERE periode=:p AND read_allowed=1"),
            {"p": periode},
        ).all()
    _have_ids = {str(r[0]) for r in _have_rows}
except Exception as exc:
    st.warning(f"Lecture `erp_etudes` impossible : {type(exc).__name__} — la liste des documents manquants est indisponible.")
    _have_ids = set()
_profiles = doc_optimizer.load_profiles()
_catalog = {sid: {"label": p.label, "price": p.price} for sid, p in _profiles.items()}
_missing = missing_studies(_have_ids, _catalog)

if not _missing:
    st.success("Aucun document manquant : le livrable est complet pour cette période.")
else:
    _md = pd.DataFrame([{"Document": m["label"], "Prix (€)": m["price"],
                         "Sert à": m["pourquoi"]} for m in _missing])
    st.dataframe(_md, hide_index=True, width="stretch")
    if not is_editor():
        st.warning("Mode visiteur (lecture seule) : l'achat/synchronisation exige le mode Utilisateur.")
    else:
        _cols = st.columns(2)
        _picked: list[str] = []
        for _i, _m in enumerate(_missing):
            if _cols[_i % 2].checkbox(f"{_m['label']} · {_m['price']:g} €",
                                      value=True, key=f"liv_missing_{periode}_{_m['study_id']}"):
                _picked.append(_m["study_id"])
        _total = sum(_m["price"] for _m in _missing if _m["study_id"] in _picked)
        st.metric("Sélection", f"{len(_picked)} document(s) · {_total:,.0f} €".replace(",", " "))
        if st.button("🛒 Acheter ces documents dans l'ERP", type="primary", disabled=not _picked):
            st.session_state["livrable_buy_request"] = {
                "periode": periode, "study_ids": _picked, "source": "Livrable Finance",
            }
            st.switch_page("pages/2_Scraper.py")
