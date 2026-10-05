from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import scraper_subakoua as scraper


st.set_page_config(page_title="Scraper Subakoua", page_icon="🕷️", layout="wide")


def secret_value(section: str | None, key: str, default: str = "") -> str:
    try:
        if section:
            data = st.secrets.get(section, {})
            value = data.get(key, default)
        else:
            value = st.secrets.get(key, default)
        return str(value) if value is not None else default
    except Exception:
        return default


def env_or_secret(section: str | None, key: str, env_name: str, default: str = "") -> str:
    value = secret_value(section, key, "")
    if value:
        return value
    return os.getenv(env_name, default)


def to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def unique_periods(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        value = re.sub(r"\s+", " ", str(value)).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def period_year(period: str) -> str:
    m = re.search(r"Année\s+(\d+)", period, re.I)
    return f"Année {m.group(1)}" if m else "Autres"


def get_periods_from_db(config: scraper.ScraperConfig, periods: list[str], modules: list[str]) -> pd.DataFrame:
    if not periods or not modules:
        return pd.DataFrame(columns=["Période", "Données présentes", "Données attendues", "Couverture"])
    conn = None
    try:
        conn = scraper.connect_mysql(config)
        marks_p = ",".join(["%s"] * len(periods))
        marks_m = ",".join(["%s"] * len(modules))
        sql = f"""
            SELECT periode, module, COUNT(*) AS n
            FROM erp_donnees
            WHERE type_donnee = %s
              AND periode IN ({marks_p})
              AND module IN ({marks_m})
            GROUP BY periode, module
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, ["etat_actuel", *periods, *modules])
            rows = cursor.fetchall()
        existing = {(r["periode"], r["module"]) for r in rows}
        total_expected = len(modules)
        data = []
        for period in periods:
            count = sum((period, mod) in existing for mod in modules)
            data.append({
                "Période": period,
                "Données présentes": count,
                "Données attendues": total_expected,
                "Couverture": f"{count / total_expected:.0%}" if total_expected else "0%",
            })
        return pd.DataFrame(data)
    except Exception as exc:
        st.warning(f"Impossible de lire la couverture actuelle de la BDD : {exc}")
        return pd.DataFrame(columns=["Période", "Données présentes", "Données attendues", "Couverture"])
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


class StreamlitLogHandler(logging.Handler):
    def __init__(self, placeholder):
        super().__init__(level=logging.INFO)
        self.placeholder = placeholder
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
            self.lines = self.lines[-120:]
            self.placeholder.code("\n".join(self.lines), language="text")
        except Exception:
            pass


def make_config(username: str, password: str, force: bool) -> scraper.ScraperConfig:
    root = Path(tempfile.mkdtemp(prefix="subakoua_scraper_"))
    output_dir = root / "scraper_output"
    state_file = root / "auth_state.json"

    # Permet de surcharger les URL depuis les secrets sans modifier le code.
    login_url = env_or_secret("subakoua", "login_url", "SUBAKOUA_LOGIN_URL", "https://login.arkhe.com/")
    dashboard_url = env_or_secret("subakoua", "dashboard_url", "SUBAKOUA_DASHBOARD_URL", "https://subakoua.arkhe.com/companies")
    scraper.URL_CONNEXION = login_url
    scraper.URL_DASHBOARD = dashboard_url
    parsed = scraper.urlparse(dashboard_url)
    scraper.DOMAINE_BASE = f"{parsed.scheme}://{parsed.netloc}"

    return scraper.ScraperConfig(
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


def check_mysql(config: scraper.ScraperConfig) -> tuple[bool, str]:
    try:
        conn = scraper.connect_mysql(config)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version")
                row = cursor.fetchone()
            return True, f"MySQL OK — {row['version'] if row else 'version inconnue'}"
        finally:
            conn.close()
    except Exception as exc:
        return False, f"MySQL indisponible : {exc}"


def discover_periods(config: scraper.ScraperConfig) -> list[str]:
    with scraper.sync_playwright() as p:
        browser = scraper.launch_browser(p, config, logging.getLogger("subakoua_scraper"))
        context = scraper.create_context(browser, config, logging.getLogger("subakoua_scraper"))
        page = context.new_page()
        page.set_default_timeout(config.timeout_ms)
        page.set_default_navigation_timeout(config.timeout_ms)
        try:
            scraper.login(page, context, config, logging.getLogger("subakoua_scraper"))
            scraper.revenir_au_dashboard(page, config)
            return scraper.discover_available_periods(page, config, logging.getLogger("subakoua_scraper"))
        finally:
            try:
                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------------------
# INTERFACE
# ---------------------------------------------------------------------------

st.title("🕷️ Scraper Subakoua")
st.caption("Extraction en ligne → synchronisation directe dans MySQL / Aiven")

with st.expander("🔐 Connexions", expanded=True):
    has_subakoua_secrets = bool(secret_value("subakoua", "user")) and bool(secret_value("subakoua", "password"))
    use_secret_creds = st.checkbox(
        "Utiliser les identifiants Subakoua stockés dans les Secrets Streamlit",
        value=has_subakoua_secrets,
        disabled=not has_subakoua_secrets,
        help="Recommandé pour un scraper en ligne. Les identifiants ne sont pas affichés ni enregistrés dans le code.",
    )

    if use_secret_creds:
        sub_user = secret_value("subakoua", "user")
        sub_pass = secret_value("subakoua", "password")
        st.success("Identifiants Subakoua chargés depuis les Secrets Streamlit.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            sub_user = st.text_input("Identifiant Subakoua", key="online_sub_user")
        with col2:
            sub_pass = st.text_input("Mot de passe Subakoua", type="password", key="online_sub_pass")
        if not sub_user or not sub_pass:
            st.info("Renseigne les identifiants ici, ou configure [subakoua] dans les Secrets Streamlit.")

    db_host = env_or_secret("mysql", "host", "DB_HOST", "")
    db_name = env_or_secret("mysql", "database", "DB_NAME", "")
    db_user = env_or_secret("mysql", "user", "DB_USER", env_or_secret("mysql", "username", "DB_USER", ""))
    if db_host and db_name and db_user:
        st.success(f"Aiven configuré : `{db_host}` · base `{db_name}` · utilisateur `{db_user}`")
    else:
        st.error("La connexion Aiven est incomplète dans les Secrets Streamlit ([mysql]).")

col_a, col_b = st.columns([1, 1])
with col_a:
    if st.button("🔎 Tester la connexion & actualiser les périodes", type="secondary", use_container_width=True):
        if not sub_user or not sub_pass:
            st.error("Identifiants Subakoua manquants.")
        elif not db_host or not db_name or not db_user or not env_or_secret("mysql", "password", "DB_PASSWORD", ""):
            st.error("Identifiants Aiven manquants dans les Secrets Streamlit.")
        else:
            cfg = make_config(sub_user, sub_pass, force=False)
            ok, msg = check_mysql(cfg)
            if ok:
                st.success(msg)
            else:
                st.error(msg)
            try:
                live_periods = discover_periods(cfg)
                if live_periods:
                    st.session_state["scraper_periods"] = unique_periods(live_periods)
                    st.session_state["scraper_periods_source"] = "live"
                    st.success(f"{len(live_periods)} période(s) détectée(s) depuis Subakoua.")
                    st.rerun()
            except Exception as exc:
                st.error(f"Impossible de charger les périodes en ligne : {exc}")
with col_b:
    if st.button("↻ Utiliser les périodes connues", use_container_width=True):
        st.session_state["scraper_periods"] = list(scraper.MOIS_DISPONIBLES)
        st.session_state["scraper_periods_source"] = "local"
        st.rerun()

available = st.session_state.get("scraper_periods") or list(scraper.MOIS_DISPONIBLES)
available = unique_periods(available)
st.session_state["scraper_periods"] = available

st.subheader("📅 Périodes à extraire")
source = st.session_state.get("scraper_periods_source", "local")
st.caption("Périodes détectées en direct depuis Subakoua." if source == "live" else "Périodes de secours connues. Utilise le bouton de découverte pour synchroniser la liste avec le portail.")

preset = st.selectbox(
    "Sélection rapide",
    ["Personnalisée", "Toutes", "Année 1", "Année 2"],
    index=0,
)

if preset == "Toutes":
    st.session_state["scraper_selected_periods"] = list(available)
elif preset in {"Année 1", "Année 2"}:
    st.session_state["scraper_selected_periods"] = [p for p in available if period_year(p) == preset]
else:
    if "scraper_selected_periods" not in st.session_state:
        st.session_state["scraper_selected_periods"] = [available[0]] if available else []
    st.session_state["scraper_selected_periods"] = [p for p in st.session_state["scraper_selected_periods"] if p in available]

selected_periods = st.multiselect(
    "Périodes sélectionnées",
    options=available,
    key="scraper_selected_periods",
    placeholder="Choisis une ou plusieurs périodes...",
)

c1, c2, c3 = st.columns(3)
c1.metric("Périodes", len(selected_periods))
c2.metric("Périodes disponibles", len(available))
c3.metric("Mode", "Incrémental" if not st.session_state.get("force_scraper", False) else "Forcé")

st.subheader("🧩 Modules")
module_options = [(display, key) for display, key in scraper.MODULES_A_VISITER]
module_labels = [display for display, _ in module_options]
selected_module_labels = st.multiselect(
    "Modules à extraire",
    options=module_labels,
    default=module_labels,
    help="Tu peux limiter le scraping aux modules utiles pour accélérer une mise à jour.",
)
selected_modules = [(display, key) for display, key in module_options if display in selected_module_labels]

with st.expander("⚙️ Options avancées", expanded=False):
    force = st.checkbox(
        "Forcer le re-scraping des périodes sélectionnées",
        value=False,
        key="force_scraper",
        help="Par défaut, le scraper ignore les couples période/module déjà présents et valides dans la BDD.",
    )
    timeout_ms = st.number_input("Timeout navigation / extraction (ms)", min_value=10000, max_value=120000, value=30000, step=5000)
    retries = st.number_input("Nombre de tentatives par opération", min_value=1, max_value=6, value=3, step=1)

if selected_periods and selected_modules and db_host and db_name and db_user:
    try:
        preview_cfg = make_config(sub_user or "", sub_pass or "", force=False)
        coverage = get_periods_from_db(preview_cfg, selected_periods, [k for _, k in selected_modules])
        if not coverage.empty:
            st.subheader("📊 État avant extraction")
            st.dataframe(coverage, use_container_width=True, hide_index=True)
    except Exception:
        pass

st.divider()
run_col1, run_col2 = st.columns([2, 1])
with run_col1:
    launch = st.button(
        f"🚀 Lancer le scraping · {len(selected_periods)} période(s) × {len(selected_modules)} module(s)",
        type="primary",
        use_container_width=True,
        disabled=(not selected_periods or not selected_modules or not sub_user or not sub_pass or not db_host or not db_name or not db_user),
    )
with run_col2:
    st.info("Les données sont synchronisées dans Aiven au fur et à mesure des modules.")

if launch:
    config = make_config(sub_user, sub_pass, force=force)
    config = scraper.replace(config, timeout_ms=int(timeout_ms), retries=int(retries))
    log_placeholder = st.empty()
    status = st.status("🚀 Scraping en cours…", expanded=True)
    logger = scraper.build_logger(config.output_dir)
    ui_handler = StreamlitLogHandler(log_placeholder)
    logger.addHandler(ui_handler)

    try:
        summary = scraper.lancer_robot_global(selected_periods, selected_modules, config, logger)
        st.session_state["last_scraper_summary"] = summary
        errors = sum(len(r.get("errors", [])) for r in summary.get("module_results", []))
        if errors:
            status.update(label="⚠️ Extraction terminée avec erreurs", state="error", expanded=True)
        else:
            status.update(label="✅ Extraction terminée", state="complete", expanded=False)
        logger.removeHandler(ui_handler)
    except Exception as exc:
        status.update(label="❌ Échec du scraping", state="error", expanded=True)
        st.error(f"Scraping interrompu : {exc}")
        logger.removeHandler(ui_handler)

summary = st.session_state.get("last_scraper_summary")
if summary:
    st.subheader("🧾 Dernier run")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Statut", summary.get("status", "inconnu"))
    m2.metric("Périodes", len(summary.get("periods", [])))
    m3.metric("Modules", len(summary.get("modules", [])))
    m4.metric("Durée", f"{summary.get('duration_seconds', 0):.1f} s")

    rows = []
    for result in summary.get("module_results", []):
        rows.append({
            "Module": result.get("display", result.get("module", "")),
            "Scrapées": len(result.get("scraped_periods", [])),
            "Ignorées": len(result.get("skipped_periods", [])),
            "Erreurs": len(result.get("errors", [])),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with st.expander("🔍 Détails du run"):
        st.json(summary)
