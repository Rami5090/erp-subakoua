from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

import document_optimizer as optimizer
import scraper_subakoua as legacy
import smart_scraper
import subakoua_api


st.set_page_config(page_title="Scraper Subakoua", page_icon="🕷️", layout="wide")


def secret_value(section: str | None, key: str, default: str = "") -> str:
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
    value = secret_value(section, key, "")
    return value if value else os.getenv(env_name, default)


def to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def make_config(username: str, password: str, force: bool = False) -> legacy.ScraperConfig:
    root = Path(tempfile.mkdtemp(prefix="subakoua_cloud_"))
    output_dir = root / "scraper_output"
    state_file = root / "auth_state.json"

    login_url = env_or_secret("subakoua", "login_url", "SUBAKOUA_LOGIN_URL", "https://login.arkhe.com/")
    dashboard_url = env_or_secret("subakoua", "dashboard_url", "SUBAKOUA_DASHBOARD_URL", "https://subakoua.arkhe.com/companies")
    parsed = legacy.urlparse(dashboard_url)
    legacy.URL_CONNEXION = login_url
    legacy.URL_DASHBOARD = dashboard_url
    legacy.DOMAINE_BASE = f"{parsed.scheme}://{parsed.netloc}"

    return legacy.ScraperConfig(
        username=username.strip(),
        password=password,
        db_host=env_or_secret("mysql", "host", "DB_HOST", ""),
        db_port=int(env_or_secret("mysql", "port", "DB_PORT", "3306")),
        db_user=env_or_secret("mysql", "user", "DB_USER", env_or_secret("mysql", "username", "DB_USER", "")),
        db_password=env_or_secret("mysql", "password", "DB_PASSWORD", ""),
        db_name=env_or_secret("mysql", "database", "DB_NAME", ""),
        db_ssl=to_bool(env_or_secret("mysql", "ssl", "DB_SSL", "1"), True),
        db_ssl_ca=env_or_secret("mysql", "ssl_ca", "DB_SSL_CA", "") or None,
        headless=True,
        slow_mo_ms=0,
        timeout_ms=int(env_or_secret("scraper", "timeout_ms", "SCRAPER_TIMEOUT_MS", "30000")),
        retries=max(1, int(env_or_secret("scraper", "retries", "SCRAPER_RETRIES", "3"))),
        output_dir=output_dir,
        state_file=state_file,
        save_html_on_error=True,
        save_screenshot_on_error=True,
        incremental=not force,
    )


def check_mysql(config: legacy.ScraperConfig) -> tuple[bool, str]:
    try:
        conn = legacy.connect_mysql(config)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version")
                row = cursor.fetchone()
            return True, f"MySQL OK — {row['version'] if row else 'version inconnue'}"
        finally:
            conn.close()
    except Exception as exc:
        return False, f"MySQL indisponible : {exc}"


def api_session(config: legacy.ScraperConfig, *, log_name: str = "subakoua_api"):
    logger = logging.getLogger(log_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return subakoua_api.start_authenticated_client(config, logger)


def discover_periods(config: legacy.ScraperConfig) -> list[str]:
    pw = browser = context = None
    page = None
    try:
        pw, browser, context, page, api = api_session(config)
        return legacy.discover_available_periods(page, config, logging.getLogger("subakoua_api"))
    finally:
        try:
            if context:
                context.close()
        finally:
            if browser:
                browser.close()
            if pw:
                pw.stop()


def period_year(period: str) -> str:
    import re
    m = re.search(r"Année\s+(\d+)", period, re.I)
    return f"Année {m.group(1)}" if m else "Autres"


def unique_periods(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        v = " ".join(str(value).split()).strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def plan_dicts(items):
    return [asdict(x) for x in items]


def plan_items_from_dicts(rows):
    return [optimizer.PlanItem(**row) for row in rows]


st.title("🕷️ Scraper Subakoua — moteur documentaire intelligent")
st.caption("API-first · achat documentaire contrôlé · extraction déterministe · synchronisation Aiven")

with st.expander("🔐 Connexions", expanded=True):
    has_subakoua_secrets = bool(secret_value("subakoua", "user")) and bool(secret_value("subakoua", "password"))
    use_secret_creds = st.checkbox(
        "Utiliser les identifiants Subakoua des Secrets Streamlit",
        value=has_subakoua_secrets,
        disabled=not has_subakoua_secrets,
    )
    if use_secret_creds:
        sub_user = secret_value("subakoua", "user")
        sub_pass = secret_value("subakoua", "password")
        st.success("Identifiants Subakoua chargés depuis les Secrets Streamlit.")
    else:
        c1, c2 = st.columns(2)
        sub_user = c1.text_input("Identifiant Subakoua", key="smart_sub_user")
        sub_pass = c2.text_input("Mot de passe Subakoua", type="password", key="smart_sub_pass")

    db_host = env_or_secret("mysql", "host", "DB_HOST", "")
    db_name = env_or_secret("mysql", "database", "DB_NAME", "")
    db_user = env_or_secret("mysql", "user", "DB_USER", env_or_secret("mysql", "username", "DB_USER", ""))
    db_pass = env_or_secret("mysql", "password", "DB_PASSWORD", "")
    if db_host and db_name and db_user and db_pass:
        st.success(f"Aiven configuré : `{db_host}` · `{db_name}` · `{db_user}`")
    else:
        st.error("Configuration Aiven incomplète dans les Secrets Streamlit ([mysql]).")

b1, b2, b3 = st.columns(3)
with b1:
    if st.button("🧪 Tester login + API", width="stretch"):
        if not sub_user or not sub_pass:
            st.error("Identifiants Subakoua manquants.")
        else:
            cfg = make_config(sub_user, sub_pass)
            pw = browser = context = None
            try:
                ok_db, msg_db = check_mysql(cfg)
                (st.success if ok_db else st.error)(msg_db)
                pw, browser, context, page, api = api_session(cfg)
                ctx = api.get_player_context(refresh=True)
                st.success(f"API Subakoua OK — équipe {ctx.team_number or '?'} · {ctx.team_name}")
            except Exception as exc:
                st.error(f"Échec login/API : {exc}")
            finally:
                try:
                    if context:
                        context.close()
                finally:
                    if browser:
                        browser.close()
                    if pw:
                        pw.stop()
with b2:
    if st.button("🔎 Découvrir les périodes", width="stretch"):
        if not sub_user or not sub_pass:
            st.error("Identifiants Subakoua manquants.")
        else:
            try:
                cfg = make_config(sub_user, sub_pass)
                live = discover_periods(cfg)
                if live:
                    st.session_state["scraper_periods"] = unique_periods(live)
                    st.session_state["scraper_periods_source"] = "live"
                    st.success(f"{len(live)} période(s) détectée(s).")
                    st.rerun()
            except Exception as exc:
                st.error(f"Découverte impossible : {exc}")
with b3:
    if st.button("↻ Périodes locales de secours", width="stretch"):
        st.session_state["scraper_periods"] = list(legacy.MOIS_DISPONIBLES)
        st.session_state["scraper_periods_source"] = "local"
        st.rerun()

available = unique_periods(st.session_state.get("scraper_periods") or list(legacy.MOIS_DISPONIBLES))
st.session_state["scraper_periods"] = available

# ---------------------------------------------------------------------------
# ONGLET 1 : PLAN DOCUMENTAIRE
# ---------------------------------------------------------------------------
tab_plan, tab_sync, tab_legacy = st.tabs(["🎯 Plan documentaire", "📥 Synchroniser", "🧱 Secours DOM"])

with tab_plan:
    st.subheader("🎯 Construire le plan d'achat documentaire")
    st.info(
        "La planification interroge le catalogue live de Subakoua. Elle ne déclenche aucun achat. "
        "Les achats sont séparés et nécessitent une confirmation explicite."
    )

    c1, c2 = st.columns(2)
    with c1:
        preset = st.selectbox("Sélection rapide des périodes", ["Personnalisée", "Toutes", "Année 1", "Année 2"], key="smart_period_preset")
        if preset == "Toutes":
            default_periods = available
        elif preset in {"Année 1", "Année 2"}:
            default_periods = [p for p in available if period_year(p) == preset]
        else:
            default_periods = st.session_state.get("smart_target_periods", available[:1])
        selected_periods = st.multiselect("Périodes cibles", available, default=default_periods, key="smart_target_periods")
    with c2:
        current_period = st.selectbox(
            "Période courante pour les achats",
            available,
            index=min(len(available) - 1, max(0, available.index(st.session_state.get("smart_current_period", available[-1])) if st.session_state.get("smart_current_period", available[-1]) in available else len(available) - 1)),
            key="smart_current_period",
            help="Une étude achetée sur la période courante est observée comme disponible sur la période suivante dans l'audit. Le moteur ne force jamais un achat hors période.",
        )
        budget = st.number_input("Budget documentaire par période cible (€)", min_value=0.0, max_value=1_000_000.0, value=600.0, step=50.0, key="smart_budget")
        allow_5000 = st.checkbox("Autoriser les études stratégiques à 5 000 €", value=False, key="smart_allow_5000")

    if selected_periods:
        st.caption("Objectif stratégique : **maximiser la part de marché**, puis couvrir prévision, production et sécurité financière.")

    if st.button("📐 Analyser le catalogue live et calculer le plan", type="primary", width="stretch", disabled=not selected_periods):
        if not sub_user or not sub_pass:
            st.error("Identifiants Subakoua manquants.")
        else:
            cfg = make_config(sub_user, sub_pass)
            pw = browser = context = None
            try:
                pw, browser, context, page, api = api_session(cfg, log_name="subakoua_plan")
                with st.status("Analyse documentaire en cours…", expanded=True) as status:
                    summaries, items = smart_scraper.build_plan_for_periods(api, selected_periods, budget_per_period=float(budget), allow_5000=allow_5000)
                    st.session_state["smart_plan"] = plan_dicts(items)
                    st.session_state["smart_plan_summaries"] = [asdict(x) for x in summaries]
                    st.session_state["smart_api_base"] = api.base_url
                    status.update(label="✅ Plan documentaire calculé", state="complete", expanded=False)
            except Exception as exc:
                st.error(f"Planification impossible : {exc}")
            finally:
                try:
                    if context:
                        context.close()
                finally:
                    if browser:
                        browser.close()
                    if pw:
                        pw.stop()

    summaries = st.session_state.get("smart_plan_summaries", [])
    plan_rows = st.session_state.get("smart_plan", [])
    if summaries:
        st.subheader("📊 Diagnostic des périodes")
        summary_rows = []
        for x in summaries:
            row = asdict(x) if hasattr(x, "__dataclass_fields__") else dict(x)
            cov = row.pop("coverage_by_need", {}) or {}
            for need_id, info in cov.items():
                row[f"Couverture · {info.get('label', need_id)}"] = info.get("coverage_percent", 0.0)
            summary_rows.append(row)
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)
        st.caption("La couverture est calculée par informations distinctes explicitement cartographiées, et non par simple recouvrement de mots-clés.")

    st.subheader("📚 Matrice documentaire")
    with st.expander("Voir le classement document → décision → valeur / €", expanded=False):
        matrix = optimizer.build_information_matrix(optimizer.load_profiles())
        st.dataframe(pd.DataFrame(matrix), width="stretch", hide_index=True)

    if plan_rows:
        plan = plan_items_from_dicts(plan_rows)
        st.subheader("💳 Études payantes recommandées")
        df = pd.DataFrame([
            {
                "Période cible": x.target_period,
                "Période achat": x.purchase_period,
                "Document": x.label,
                "Prix (€)": x.price,
                "Valeur / €": x.efficiency,
                "Endpoint API": "✅" if x.endpoint_known else "⚠️",
                "Besoins couverts": ", ".join(x.covered_needs),
                "Pourquoi": x.reason,
                "Statut": x.status,
            }
            for x in plan
        ])
        st.dataframe(df, width="stretch", hide_index=True)

        purchasable = smart_scraper.only_purchasable_now(plan, current_period)
        total_now = sum(x.price for x in purchasable)
        historical_only = any(x.status == "HORS FENÊTRE D'ACHAT" for x in plan)
        if historical_only:
            st.info("La période cible est antérieure au premier mois du jeu : elle peut être analysée, mais aucun achat documentaire ne peut être planifié en amont.")
        st.metric("Achats réellement exigibles maintenant", f"{len(purchasable)} · {total_now:,.0f} €")

        authorize = st.checkbox(
            "⚠️ J'autorise les achats réels affichés ci-dessous sur la période courante",
            value=False,
            key="smart_authorize_purchase",
            help="Cette case est indispensable. Sans elle, le bouton ne fait qu'un mode simulation.",
        )
        now_df = pd.DataFrame([{"Document": x.label, "Prix (€)": x.price, "Cible": x.target_period, "Endpoint": "✅" if x.endpoint_known else "⚠️"} for x in purchasable])
        if not now_df.empty:
            st.dataframe(now_df, width="stretch", hide_index=True)
        else:
            st.info("Aucun achat du plan n'est actuellement exigible sur la période courante. Les autres restent à planifier.")

        if st.button("💳 Acheter maintenant les études exigibles", type="secondary", width="stretch", disabled=(not purchasable or not authorize)):
            cfg = make_config(sub_user, sub_pass)
            pw = browser = context = None
            try:
                pw, browser, context, page, api = api_session(cfg, log_name="subakoua_purchase")
                results = smart_scraper.execute_purchase_plan(api, purchasable, current_period=current_period, allow_real_purchases=True)
                st.session_state["smart_purchase_results"] = results
                st.success(f"Opération terminée : {sum(1 for r in results if r.get('status') == 'ACHETE')} achat(s) confirmé(s).")
            except Exception as exc:
                st.error(f"Achat interrompu : {exc}")
            finally:
                try:
                    if context:
                        context.close()
                finally:
                    if browser:
                        browser.close()
                    if pw:
                        pw.stop()

        if st.session_state.get("smart_purchase_results"):
            st.subheader("🧾 Résultat des achats")
            st.dataframe(pd.DataFrame(st.session_state["smart_purchase_results"]), width="stretch", hide_index=True)

with tab_sync:
    st.subheader("📥 Lire les études accessibles et synchroniser Aiven")
    st.caption("Cette étape ne déclenche aucun achat. Une étude n'est acceptée que si son API retourne `readAllowed=true` et le bon `studyId`.")

    target_periods = st.multiselect(
        "Périodes à synchroniser",
        options=available,
        default=st.session_state.get("smart_target_periods", available[:1]),
        key="smart_sync_periods",
    )
    profiles = optimizer.load_profiles()
    known_profiles = [p for p in profiles.values() if p.endpoint_known]
    options = {p.study_id: f"{p.label} · {p.price:g} € · API" for p in known_profiles}
    saved_plan_ids = [r["study_id"] for r in st.session_state.get("smart_plan", []) if r.get("endpoint_known")]
    default_ids = [sid for sid in saved_plan_ids if sid in options]
    selected_ids = st.multiselect("Études API à lire", list(options), default=default_ids or list(options)[:8], format_func=lambda sid: options[sid])

    if st.button("📥 Synchroniser les études sélectionnées", type="primary", width="stretch", disabled=(not target_periods or not selected_ids)):
        if not sub_user or not sub_pass:
            st.error("Identifiants Subakoua manquants.")
        elif not db_host or not db_name or not db_user or not db_pass:
            st.error("Configuration Aiven incomplète.")
        else:
            cfg = make_config(sub_user, sub_pass)
            conn = None
            pw = browser = context = None
            try:
                conn = legacy.connect_mysql(cfg)
                pw, browser, context, page, api = api_session(cfg, log_name="subakoua_sync")
                results = smart_scraper.fetch_and_store(api, conn, target_periods, selected_ids)
                st.session_state["smart_sync_results"] = results
                ok = sum(1 for r in results if r.get("status") == "OK")
                refused = sum(1 for r in results if r.get("status") == "REFUSE")
                errors = sum(1 for r in results if r.get("status") == "ERREUR")
                st.success(f"Synchronisation terminée — OK={ok} · refus={refused} · erreurs={errors}")
            except Exception as exc:
                st.error(f"Synchronisation interrompue : {exc}")
            finally:
                try:
                    if conn:
                        conn.close()
                finally:
                    try:
                        if context:
                            context.close()
                    finally:
                        if browser:
                            browser.close()
                        if pw:
                            pw.stop()

    if st.session_state.get("smart_sync_results"):
        st.dataframe(pd.DataFrame(st.session_state["smart_sync_results"]), width="stretch", hide_index=True)

with tab_legacy:
    st.warning("Mode de secours conservé pour les études dont aucun endpoint API n'a encore été cartographié. Il ne doit pas être privilégié lorsqu'une API connue existe.")
    selected_legacy_periods = st.multiselect(
        "Périodes",
        options=available,
        default=st.session_state.get("smart_target_periods", available[:1]),
        key="legacy_periods",
    )
    module_options = [(display, key) for display, key in legacy.MODULES_A_VISITER]
    selected_legacy_labels = st.multiselect(
        "Modules DOM",
        [display for display, _ in module_options],
        default=[display for display, _ in module_options],
        key="legacy_modules",
    )
    selected_legacy_modules = [(display, key) for display, key in module_options if display in selected_legacy_labels]
    force = st.checkbox("Forcer le rescraping", value=False, key="legacy_force")

    if st.button("🧱 Lancer le secours DOM", width="stretch", disabled=(not selected_legacy_periods or not selected_legacy_modules)):
        if not sub_user or not sub_pass:
            st.error("Identifiants Subakoua manquants.")
        else:
            cfg = make_config(sub_user, sub_pass, force=force)
            logger = legacy.build_logger(cfg.output_dir)
            try:
                summary = legacy.lancer_robot_global(selected_legacy_periods, selected_legacy_modules, cfg, logger)
                st.session_state["last_scraper_summary"] = summary
                st.success("Secours DOM terminé.")
                st.json(summary)
            except Exception as exc:
                st.error(f"Secours DOM interrompu : {exc}")
