"""Page d'achat de documents documentaires pour le centre de décision.

Permet d'acheter les études manquantes (BFR, épargne, AACEE, dotations,
échéance de remboursement, etc.) pour alimenter le simulateur et le livrable
Finance. Accès réservé aux utilisateurs (acheteurs).
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from db_utils import get_read_engine
from livrable_finance import REQUIRED_STUDIES

st.set_page_config(page_title="Achat de documents", page_icon="🛒", layout="wide")

from auth import setup as auth_setup, require_editor

auth_setup()
require_editor("l'achat de documents")


def _secret_value(section: str | None, key: str, default: str = "") -> str:
    try:
        if section and section in st.secrets:
            value = st.secrets[section].get(key)
            if value is not None:
                return str(value)
        if section is None:
            value = st.secrets.get(key)
            if value is not None:
                return str(value)
    except Exception:
        pass
    return default


def env_or_secret(section: str | None, key: str, env_name: str, default: str = "") -> str:
    value = _secret_value(section, key, "")
    return value if value else os.getenv(env_name, default)


def to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


st.title("🛒 Achat de documents documentaires")
st.caption(
    "Achetez les études manquantes pour alimenter le simulateur et le livrable Finance. "
    "Les documents non achetés apparaissent comme SPECIMEN (champs vides) dans les tableaux."
)

# ================================================================
# Connexions
# ================================================================
with st.expander("🔐 Connexions", expanded=True):
    sub_user = st.text_input("Identifiant Subakoua", key="buy_sub_user")
    sub_pass = st.text_input("Mot de passe Subakoua", type="password", key="buy_sub_pass")

    db_host = env_or_secret("mysql", "host", "DB_HOST", "")
    db_name = env_or_secret("mysql", "database", "DB_NAME", "")

    col_ok, col_err = st.columns(2)
    if db_host and db_name:
        col_ok.success(f"Aiven configuré : `{db_host}` · `{db_name}`")
    else:
        col_err.error("Configuration Aiven incomplète dans les Secrets Streamlit ([mysql]).")

# ================================================================
# Sélecteur de période
# ================================================================
from constants import TOURS_PERIODES

st.divider()
st.subheader("📅 Période cible")

tour = st.selectbox(
    "Période du livrable à compléter",
    sorted(TOURS_PERIODES),
    index=7,  # Août par défaut
    format_func=lambda t: f"Tour {t} — {TOURS_PERIODES[t]}",
    key="buy_tour",
)
periode = TOURS_PERIODES[int(tour)]

# ================================================================
# Détection des documents manquants
# ================================================================
st.divider()
st.subheader("📋 Documents manquants pour cette période")

# Construire le catalogue depuis le document_optimizer
import document_optimizer as doc_optimizer

profiles = doc_optimizer.load_profiles()
catalog = {sid: {"label": p.label, "price": p.price} for sid, p in profiles.items()}

missing = []
for req in REQUIRED_STUDIES:
    sid = req["study_id"]
    info = catalog.get(sid, {})
    price = float(info.get("price", 0) or 0)
    missing.append({
        "study_id": sid,
        "label": str(info.get("label", sid)),
        "price": price,
        "pourquoi": req["pourquoi"],
    })

missing = sorted(missing, key=lambda x: x["price"])

total_price = sum(m["price"] for m in missing)

col1, col2, col3 = st.columns(3)
col1.metric("Documents requis", f"{len(missing)}")
col2.metric("Coût total estimé", f"{total_price:,.0f} €".replace(",", " "))
col3.metric("Déjà achetés", "— (à vérifier en base)")

st.divider()

# Tableau des documents manquants
st.markdown("### Documents à acheter")

for _, row in missing:
    c1, c2, c3, c4 = st.columns([4, 1.5, 2.5, 1])
    with c1:
        st.write(f"**{row['label']}**")
        st.caption(row["pourquoi"])
    with c2:
        st.write(f"{row['price']:,.0f} €".replace(",", " "))
    with c3:
        st.button(
            "🛒 Acheter",
            key=f"buy_doc_{row['study_id']}",
            help=f"Acheter {row['label']} pour la période {periode}",
            disabled=not (st.session_state.get("buy_sub_user") and st.session_state.get("buy_sub_pass")),
        )
    with c4:
        st.caption(f"ID : {row['study_id']}")

st.divider()

# ================================================================
# Bloc d'achat groupé
# ================================================================
st.subheader("📦 Achat groupé")

st.markdown(
    "Sélectionnez les documents à acheter en une seule fois. "
    "Le scraper se connectera au portail Subakoua, achètera chaque étude et "
    "synchronisera les données dans Aiven."
)

selected_studies = st.multiselect(
    "Documents à acheter",
    options=[m["study_id"] for m in missing],
    format_func=lambda sid: next(
        m["label"] for m in missing if m["study_id"] == sid
    ),
    key="buy_selected_studies",
)

if selected_studies:
    selected_price = sum(
        m["price"] for m in missing if m["study_id"] in selected_studies
    )
    st.metric("Coût de la sélection", f"{selected_price:,.0f} €".replace(",", " "))

    if st.button(
        "🚀 Lancer l'achat groupé",
        type="primary",
        width="stretch",
        disabled=not (st.session_state.get("buy_sub_user") and st.session_state.get("buy_sub_pass")),
    ):
        if not db_host or not db_name or not db_user or not db_password:
            st.error("Configuration Aiven incomplète.")
        else:
            try:
                from subakoua_api import SubakouaAPIClient
                from scraper_subakoua import ScraperConfig

                # Construire la configuration manuellement
                cfg = ScraperConfig(
                    username=st.session_state["buy_sub_user"].strip(),
                    password=st.session_state["buy_sub_pass"],
                    db_host=db_host,
                    db_port=int(db_port) if db_port else 16869,
                    db_user=db_user,
                    db_password=db_password,
                    db_name=db_name,
                    db_ssl=to_bool(env_or_secret("mysql", "ssl", "DB_SSL", "1"), True),
                    db_ssl_ca=env_or_secret("mysql", "ssl_ca", "DB_SSL_CA", "") or None,
                    headless=True,
                    slow_mo_ms=0,
                    timeout_ms=int(env_or_secret("scraper", "timeout_ms", "SCRAPER_TIMEOUT_MS", "30000")),
                    retries=max(1, int(env_or_secret("scraper", "retries", "SCRAPER_RETRIES", "3"))),
                    output_dir=Path(tempfile.mkdtemp(prefix="subakoua_cloud_")) / "scraper_output",
                    state_file=Path(tempfile.mkdtemp(prefix="subakoua_cloud_")) / "auth_state.json",
                    save_html_on_error=True,
                    save_screenshot_on_error=True,
                    incremental=not False,
                )
                api = SubakouaAPIClient(cfg)

                results = []
                for sid in selected_studies:
                    try:
                        api.purchase_study(tour, sid)
                        results.append({
                            "study_id": sid,
                            "status": "ACHETÉ",
                            "prix": next(m["price"] for m in missing if m["study_id"] == sid),
                        })
                    except Exception as exc:
                        results.append({
                            "study_id": sid,
                            "status": f"ERREUR : {exc}",
                            "prix": 0,
                        })

                st.session_state["buy_results"] = results
                bought_count = sum(1 for r in results if r["status"] == "ACHETÉ")
                st.success(f"Achat terminé : {bought_count} / {len(results)}")
            except Exception as exc:
                st.error(f"Échec de l'achat : {exc}")

if st.session_state.get("buy_results"):
    st.markdown("### Résultats de l'achat")
    results_df = pd.DataFrame(st.session_state["buy_results"])
    st.dataframe(results_df, width="stretch", hide_index=True)

    st.info(
        "Après achat, utilisez la page **Scraper** (onglet Synchroniser) pour "
        "récupérer les données achetées dans Aiven."
    )

# ================================================================
# Guide
# ================================================================
st.divider()
st.subheader("ℹ️ Guide")

st.markdown("""
### Comment ça marche ?

1. **Sélectionnez la période** du livrable à compléter (par défaut Août).
2. **Consultez la liste** des documents manquants et leur utilité.
3. **Achetez** les documents dont vous avez besoin (unitairement ou par groupe).
4. **Synchronisez** les données via la page **Scraper** → onglet **Synchroniser**.
5. **Revenez** au Centre de décision : les données financières (épargne, BFR, AACEE, etc.) seront désormais disponibles.

### Pourquoi certains champs sont vides ?

Les documents non achetés apparaissent comme **SPECIMEN** dans le portail Subakoua. 
Le simulateur ne peut donc pas lire :
- **Épargne** : besoin de `bankStatements`
- **BFR opérationnel** : besoin de `peeedsftt` (tableau de trésorerie)
- **AACEE** : besoin de `peeedscrdl` (compte de résultat détaillé)
- **Dotations** : besoin de `peeedscrdl` (compte de résultat détaillé)
- **Échéance de remboursement** : besoin de `bankStatements` ou `peeedsftt`

### Coût

Les prix sont ceux affichés dans le catalogue Subakoua. L'achat est irréversible.
""")
