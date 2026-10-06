"""
Scraper Subakoua ERP - extraction robuste + synchronisation MySQL/Aiven.

Usage rapide (Windows / terminal):
    python scraper_subakoua.py --periods 1,2,3
    python scraper_subakoua.py --periods 1-12 --force
    python scraper_subakoua.py --all --headless

Variables .env principales:
    SUBAKOUA_USER / SUBAKOUA_PASS
    DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
    DB_SSL=1 (pour Aiven)
    DB_SSL_CA=C:\\chemin\\ca.pem (optionnel si validation CA stricte)
    HEADLESS=0|1
    SCRAPER_RETRIES=3
    SCRAPER_TIMEOUT_MS=30000
    SCRAPER_OUTPUT_DIR=scraper_output
    SCRAPER_STATE_FILE=scraper_output/auth_state.json

Le scraper n'écrit jamais le mot de passe dans les logs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import getpass
from dataclasses import dataclass, asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
import pymysql


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
# Charge en priorité le .env situé à côté du scraper, puis le .env du répertoire courant.
# Cela évite les erreurs quand le BAT est lancé depuis un autre dossier.
load_dotenv(SCRIPT_DIR / ".env")
load_dotenv()

URL_CONNEXION = os.getenv("SUBAKOUA_LOGIN_URL", "https://login.arkhe.com/")
URL_DASHBOARD = os.getenv("SUBAKOUA_DASHBOARD_URL", "https://subakoua.arkhe.com/companies")
DOMAINE_BASE = f"{urlparse(URL_DASHBOARD).scheme}://{urlparse(URL_DASHBOARD).netloc}"
EXTRACTOR_VERSION = "2.3.0"

MODULES_A_VISITER = [
    ("Marketing", "marketing"),
    ("Production", "production"),
    ("Approvisionnement", "approvisionnement"),
    ("Ressources", "rh"),
    ("Finance", "finance"),
    ("Banque", "banque_assurance"),
    ("comptable", "expert_comptable"),
    ("internes", "donnees_internes"),
    ("marché", "etudes_marche"),
    ("Veille", "veille_concurrentielle"),
    ("Fournisseurs", "fournisseurs"),
]

MOIS_DISPONIBLES = [
    "Année 1 - Janvier", "Année 1 - Février", "Année 1 - Mars",
    "Année 1 - Avril", "Année 1 - Mai", "Année 1 - Juin",
    "Année 1 - Juillet", "Année 1 - Août", "Année 1 - Septembre",
    "Année 1 - Octobre", "Année 1 - Novembre", "Année 1 - Décembre",
    "Année 2 - Janvier", "Année 2 - Février", "Année 2 - Mars",
]


@dataclass(frozen=True)
class ScraperConfig:
    username: str
    password: str
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    db_ssl: bool
    db_ssl_ca: str | None
    headless: bool
    slow_mo_ms: int
    timeout_ms: int
    retries: int
    output_dir: Path
    state_file: Path
    save_html_on_error: bool
    save_screenshot_on_error: bool
    incremental: bool


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config(args: argparse.Namespace) -> ScraperConfig:
    output_dir = Path(os.getenv("SCRAPER_OUTPUT_DIR", "scraper_output"))
    state_file = Path(os.getenv("SCRAPER_STATE_FILE", str(output_dir / "auth_state.json")))
    username = (args.user or os.getenv("SUBAKOUA_USER", "")).strip()
    password = os.getenv("SUBAKOUA_PASS", "")
    return ScraperConfig(
        username=username,
        password=password,
        db_host=os.getenv("DB_HOST", "localhost").strip(),
        db_port=int(os.getenv("DB_PORT", "3306")),
        db_user=os.getenv("DB_USER", "root").strip(),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_name=os.getenv("DB_NAME", "subakoua_erp").strip(),
        db_ssl=env_bool("DB_SSL", False),
        db_ssl_ca=os.getenv("DB_SSL_CA") or None,
        headless=args.headless if args.headless is not None else env_bool("HEADLESS", True),
        slow_mo_ms=int(os.getenv("SLOW_MO_MS", "0")),
        timeout_ms=int(os.getenv("SCRAPER_TIMEOUT_MS", "30000")),
        retries=max(1, int(os.getenv("SCRAPER_RETRIES", "3"))),
        output_dir=output_dir,
        state_file=state_file,
        save_html_on_error=env_bool("SAVE_HTML_ON_ERROR", True),
        save_screenshot_on_error=env_bool("SAVE_SCREENSHOT_ON_ERROR", True),
        incremental=not args.force,
    )


# ---------------------------------------------------------------------------
# LOGGING / FILES
# ---------------------------------------------------------------------------

def build_logger(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("subakoua_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    fh = logging.FileHandler(output_dir / "scraper.log", encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:100]


# ---------------------------------------------------------------------------
# EXTRACTION JAVASCRIPT
# ---------------------------------------------------------------------------
JS_EXTRACTEUR_UNIVERSEL = r"""
() => {
    const data = {};

    function cleanText(v) {
        return String(v ?? '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function parseNumber(raw) {
        let s = cleanText(raw);
        if (!s) return null;
        const negative = /^\s*\(.*\)\s*$/.test(s) || /^\s*-/.test(s);
        s = s.replace(/\([^)]*\)/g, m => m.slice(1, -1));
        s = s.replace(/[^0-9,\.\-+]/g, '');
        if (!s || !/[0-9]/.test(s)) return null;

        // Format FR : 1 234,56 / 1.234,56 ; format EN : 1234.56
        const comma = s.lastIndexOf(',');
        const dot = s.lastIndexOf('.');
        if (comma >= 0 && dot >= 0) {
            if (comma > dot) s = s.replace(/\./g, '').replace(',', '.');
            else s = s.replace(/,/g, '');
        } else if (comma >= 0) {
            s = s.replace(',', '.');
        }
        let n = Number(s);
        if (!Number.isFinite(n)) return null;
        if (negative && n > 0) n = -n;
        return n;
    }

    function put(section, key, value) {
        if (!section || !key) return;
        const k = cleanText(key);
        if (!k) return;
        section[k] = value;
    }

    // 1. Blocs de formulaires / décisions
    document.querySelectorAll('okw-base-block').forEach((bloc, blocIndex) => {
        const titleEl = bloc.querySelector('.subSectionTitle-subtitle, .headerMainCard-title');
        const sectionName = titleEl ? cleanText(titleEl.innerText) : `Bloc_${blocIndex + 1}`;
        const sectionData = {};

        bloc.querySelectorAll('.formBlock, .formProductBlock').forEach(row => {
            const titleEl = row.querySelector('.formBlock-title, .formProductBlock-title');
            if (!titleEl) return;
            const title = cleanText(titleEl.innerText);
            const values = [];

            row.querySelectorAll('ui-labeled-data, ui-input-number, input, textarea, select').forEach(cell => {
                const input = cell.matches('input, textarea, select') ? cell : cell.querySelector('input, textarea, select');
                if (!input) return;
                const raw = input.getAttribute('aria-valuenow') ?? input.value ?? input.innerText ?? '';
                const n = parseNumber(raw);
                values.push(n === null ? cleanText(raw) : n);
            });

            const ratings = Array.from(row.querySelectorAll('ui-rating')).map(rating => {
                let score = 0;
                rating.querySelectorAll('svg').forEach(svg => {
                    if (svg.querySelector('path[fill="#FDB022"], path[fill="#F79009"]')) score += 1;
                });
                return score;
            });

            if (values.length) put(sectionData, title, values);
            if (ratings.length) put(sectionData, `${title} (Notes)`, ratings);
        });

        // KPI / valeurs simples présentes dans le bloc
        bloc.querySelectorAll('.grid-x.cell.auto').forEach(row => {
            if (row.closest('.formBlock') || row.closest('.formProductBlock')) return;
            const labelEl = row.querySelector('.uiLabel-text, .label, .ui-label');
            const valueEl = row.querySelector('.ui-tag-info, .ui-tag-label, ._number, .value');
            if (!labelEl || !valueEl) return;
            const label = cleanText(labelEl.innerText);
            const raw = cleanText(valueEl.innerText);
            const n = parseNumber(raw);
            put(sectionData, label, n === null ? raw : n);
        });

        if (Object.keys(sectionData).length) data[sectionName] = sectionData;
    });

    // 2. Tableaux HTML
    document.querySelectorAll('table').forEach((table, index) => {
        let tableName = `Tableau_${index + 1}`;
        const titleEl = table.closest('.card, .panel, okw-base-block')?.querySelector(
            '.subSectionTitle-subtitle, .headerMainCard-title, .card-title, h2, h3, h4'
        );
        if (titleEl) tableName = cleanText(titleEl.innerText) || tableName;

        const headers = Array.from(table.querySelectorAll('thead th')).map(th => cleanText(th.innerText));
        const rows = [];
        table.querySelectorAll('tbody tr').forEach(tr => {
            const rowData = {};
            Array.from(tr.querySelectorAll('td')).forEach((cell, i) => {
                const key = headers[i] || `Colonne_${i + 1}`;
                const raw = cleanText(cell.innerText);
                const n = parseNumber(raw);
                rowData[key] = n === null ? raw : n;
            });
            if (Object.keys(rowData).length) rows.push(rowData);
        });
        if (rows.length) {
            let uniqueName = tableName;
            let suffix = 2;
            while (Object.prototype.hasOwnProperty.call(data, uniqueName)) {
                uniqueName = `${tableName} #${suffix}`;
                suffix += 1;
            }
            data[uniqueName] = rows;
        }
    });

    // 3. Ratios / KPI textuels sans structure de tableau
    document.querySelectorAll('okw-base-block').forEach((bloc, blocIndex) => {
        const titleEl = bloc.querySelector('.subSectionTitle-subtitle, .headerMainCard-title');
        const sectionName = titleEl ? cleanText(titleEl.innerText) : `Ratios_${blocIndex + 1}`;
        const walker = document.createTreeWalker(bloc, NodeFilter.SHOW_TEXT);
        const texts = [];
        let node;
        while ((node = walker.nextNode())) {
            const t = cleanText(node.nodeValue);
            if (t && !['+', '-', '€', '%'].includes(t)) texts.push(t);
        }
        const ratios = {};
        let cat = 'Valeurs';
        for (let i = 0; i < texts.length; i++) {
            const txt = texts[i];
            if (/^(MENSUEL|CUMULÉ|ANNUEL)$/i.test(txt)) {
                cat = txt.toUpperCase();
                continue;
            }
            const n = parseNumber(txt);
            if (n !== null && i > 0 && !/^[0-9\s,.+\-€%()]+$/.test(texts[i-1])) {
                ratios[cat] = ratios[cat] || {};
                ratios[cat][texts[i-1]] = n;
            }
        }
        if (Object.keys(ratios).length) {
            data[sectionName] = data[sectionName] || {};
            Object.entries(ratios).forEach(([k, v]) => data[sectionName][`Indicateurs_${k}`] = [v]);
        }
    });

    // 4. Soldes bancaires isolés
    const bodyTexts = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const t = cleanText(node.nodeValue);
        if (t) bodyTexts.push(t);
    }
    const balances = {};
    for (let i = 0; i < bodyTexts.length; i++) {
        if (!['Solde initial', 'Solde final'].includes(bodyTexts[i])) continue;
        for (let j = 1; j <= 5; j++) {
            if (i + j >= bodyTexts.length) break;
            const n = parseNumber(bodyTexts[i + j]);
            if (n !== null) {
                balances[bodyTexts[i]] = n;
                break;
            }
        }
    }
    if (Object.keys(balances).length) data['Soldes bancaires'] = [balances];

    return data;
}
"""


# ---------------------------------------------------------------------------
# UTILITAIRES
# ---------------------------------------------------------------------------

def contains_specimen(value: Any) -> bool:
    if isinstance(value, str):
        return "SPECIMEN" in value.upper()
    if isinstance(value, dict):
        return any(contains_specimen(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_specimen(v) for v in value)
    return False


def contains_scraper_error(value: Any) -> bool:
    if isinstance(value, dict):
        if "__scraper_error__" in value or "_erreur_extraction" in value:
            return True
        return any(contains_scraper_error(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_scraper_error(v) for v in value)
    return False


def payload_is_usable(value: Any) -> bool:
    if value is None or value == "":
        return False
    if contains_scraper_error(value):
        return False
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return True


def retry_call(fn, retries: int, logger: logging.Logger, label: str):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                logger.warning("%s | tentative %s/%s échouée : %s", label, attempt, retries, exc)
                time.sleep(min(2 * attempt, 5))
            else:
                logger.error("%s | échec définitif : %s", label, exc)
    raise last_error  # type: ignore[misc]


def wait_page_stable(page: Page, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(400)


def maybe_save_debug(page: Page, config: ScraperConfig, label: str, logger: logging.Logger) -> None:
    stem = f"{timestamp()}_{safe_name(label)}"
    try:
        if config.save_screenshot_on_error:
            path = config.output_dir / f"{stem}.png"
            page.screenshot(path=str(path), full_page=True)
            logger.info("Debug screenshot : %s", path)
    except Exception as exc:
        logger.warning("Impossible de sauvegarder la capture : %s", exc)
    try:
        if config.save_html_on_error:
            path = config.output_dir / f"{stem}.html"
            path.write_text(page.content(), encoding="utf-8")
            logger.info("Debug HTML : %s", path)
    except Exception as exc:
        logger.warning("Impossible de sauvegarder le HTML : %s", exc)


# ---------------------------------------------------------------------------
# BDD
# ---------------------------------------------------------------------------

def connect_mysql(config: ScraperConfig):
    kwargs: dict[str, Any] = {
        "host": config.db_host,
        "port": config.db_port,
        "user": config.db_user,
        "password": config.db_password,
        "database": config.db_name,
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 15,
        "read_timeout": 60,
        "write_timeout": 60,
        "autocommit": False,
    }
    if config.db_ssl:
        if config.db_ssl_ca:
            kwargs["ssl"] = {"ca": config.db_ssl_ca}
        else:
            kwargs["ssl"] = {}
    return pymysql.connect(**kwargs)


def ensure_erp_table(connection, logger: logging.Logger) -> bool:
    """Crée/complète la table et indique si la clé unique attendue est disponible."""
    has_unique = False
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS erp_donnees (
                id INT AUTO_INCREMENT PRIMARY KEY,
                periode VARCHAR(50) NOT NULL,
                type_donnee VARCHAR(50) NOT NULL,
                module VARCHAR(50) NOT NULL,
                contenu JSON NOT NULL,
                date_maj DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unq_periode_type_module (periode, type_donnee, module)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'erp_donnees'
              AND column_name = 'date_maj'
        """)
        if int(cursor.fetchone()["c"]) == 0:
            try:
                cursor.execute(
                    "ALTER TABLE erp_donnees ADD COLUMN date_maj DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                )
            except Exception as exc:
                logger.warning("Ajout date_maj impossible : %s", exc)

        cursor.execute("""
            SELECT index_name, COUNT(*) AS n
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'erp_donnees'
              AND non_unique = 0
              AND index_name <> 'PRIMARY'
              AND column_name IN ('periode', 'type_donnee', 'module')
            GROUP BY index_name
        """)
        for row in cursor.fetchall():
            if int(row["n"] or 0) >= 3:
                has_unique = True
                break

        if not has_unique:
            logger.warning(
                "Clé unique (periode,type_donnee,module) absente : le scraper passe en mode UPDATE/INSERT "
                "sécurisé pour éviter de créer de nouveaux doublons."
            )
    connection.commit()
    return has_unique


def existing_module_data(connection, periode: str, module: str) -> Any | None:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT contenu
            FROM erp_donnees
            WHERE periode=%s AND type_donnee=%s AND module=%s
            ORDER BY id DESC LIMIT 1
        """, (periode, "etat_actuel", module))
        row = cursor.fetchone()
    if not row:
        return None
    value = row["contenu"]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def upsert_module(
    connection,
    periode: str,
    module: str,
    contenu: Any,
    has_unique_key: bool,
) -> None:
    json_data = json.dumps(contenu, ensure_ascii=False)
    with connection.cursor() as cursor:
        if has_unique_key:
            cursor.execute("""
                INSERT INTO erp_donnees (periode, type_donnee, module, contenu, date_maj)
                VALUES (%s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    contenu=VALUES(contenu),
                    date_maj=NOW()
            """, (periode, "etat_actuel", module, json_data))
            return

        # Fallback pour une table historique sans index unique.
        cursor.execute("""
            SELECT id
            FROM erp_donnees
            WHERE periode=%s AND type_donnee=%s AND module=%s
            ORDER BY id DESC
            LIMIT 1
        """, (periode, "etat_actuel", module))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE erp_donnees SET contenu=%s, date_maj=NOW() WHERE id=%s",
                (json_data, row["id"]),
            )
        else:
            cursor.execute("""
                INSERT INTO erp_donnees (periode, type_donnee, module, contenu)
                VALUES (%s, %s, %s, %s)
            """, (periode, "etat_actuel", module, json_data))


def synchroniser_module(
    connection,
    module: str,
    donnees_par_mois: dict[str, Any],
    logger: logging.Logger,
    has_unique_key: bool,
) -> int:
    count = 0
    for periode, contenu in donnees_par_mois.items():
        if not payload_is_usable(contenu):
            logger.warning("Synchronisation ignorée | module=%s | période=%s | payload invalide", module, periode)
            continue
        upsert_module(connection, periode, module, contenu, has_unique_key)
        count += 1
    connection.commit()
    logger.info("Synchronisation DB OK | module=%s | périodes=%s", module, count)
    return count


# ---------------------------------------------------------------------------
# NAVIGATION / LOGIN
# ---------------------------------------------------------------------------

def _visible_locator(scope, selectors: list[str], timeout_ms: int = 1200):
    """Retourne le premier locator visible trouvé dans une Page ou un Frame."""
    for selector in selectors:
        try:
            loc = scope.locator(selector).first
            if loc.is_visible(timeout=timeout_ms):
                return loc
        except Exception:
            continue
    return None


def _visible_by_label(scope, patterns: list[str], timeout_ms: int = 1200):
    """Cherche un champ via son label/accessibility name."""
    for pattern in patterns:
        try:
            loc = scope.get_by_label(re.compile(pattern, re.I)).first
            if loc.is_visible(timeout=timeout_ms):
                return loc
        except Exception:
            continue
    return None


def _login_controls(scope):
    """Détecte les champs du nouveau portail de connexion, y compris les variantes DOM."""
    user = _visible_by_label(
        scope,
        [r"identifiant", r"nom d.?utilisateur", r"utilisateur", r"e.?mail"],
    )
    if user is None:
        user = _visible_locator(scope, [
            'input[autocomplete="username"]',
            'input[name*="user" i]', 'input[id*="user" i]',
            'input[name*="login" i]', 'input[id*="login" i]',
            'input[name*="email" i]', 'input[id*="email" i]',
            'input[placeholder*="identifiant" i]',
            'input[aria-label*="identifiant" i]',
            'input[placeholder*="email" i]',
            'input[type="email"]', 'input[type="text"]',
            'input:not([type])', '[contenteditable="true"][role="textbox"]',
        ])

    password = _visible_by_label(
        scope,
        [r"mot de passe", r"password"],
    )
    if password is None:
        password = _visible_locator(scope, [
            'input[autocomplete="current-password"]',
            'input[name*="pass" i]', 'input[id*="pass" i]',
            'input[placeholder*="mot de passe" i]',
            'input[aria-label*="mot de passe" i]',
            'input[placeholder*="password" i]',
            'input[type="password"]',
        ])

    button = None
    try:
        button = scope.get_by_role("button", name=re.compile(r"se\s*connecter|connexion|login", re.I)).first
        if not button.is_visible(timeout=800):
            button = None
    except Exception:
        button = None
    if button is None:
        button = _visible_locator(scope, [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Se connecter")',
            '[role="button"]:has-text("Se connecter")',
            'button:has-text("Connexion")',
            '[role="button"]:has-text("Connexion")',
        ])

    if user is not None and password is not None:
        return user, password, button
    return None, None, button


def _find_login_scope(page: Page, logger: logging.Logger):
    """Cherche le formulaire dans la page ou dans un iframe."""
    scopes = [page] + [frame for frame in page.frames if frame != page.main_frame]
    for scope in scopes:
        user, password, button = _login_controls(scope)
        if user is not None and password is not None:
            if scope is page:
                logger.info("Formulaire de connexion détecté dans la page principale.")
            else:
                logger.info("Formulaire de connexion détecté dans un iframe : %s", scope.url)
            return scope, user, password, button
    return None, None, None, None


def _login_form_visible(page: Page) -> bool:
    try:
        scope, user, password, _ = _find_login_scope(page, logging.getLogger("subakoua_scraper"))
        return user is not None and password is not None and scope is not None
    except Exception:
        return False


def _dashboard_reachable(page: Page, config: ScraperConfig) -> bool:
    """Vérifie réellement que /companies est accessible avec la session courante."""
    try:
        page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=config.timeout_ms)
        wait_page_stable(page, config.timeout_ms)
        if _login_form_visible(page):
            return False
        return "/companies" in page.url.lower() or "companies" in page.url.lower()
    except Exception:
        return False


def login(page: Page, context: BrowserContext, config: ScraperConfig, logger: logging.Logger) -> None:
    """Connexion tolérante aux évolutions du portail Arkhe/Subakoua."""
    logger.info("Connexion à Subakoua... Interface détectée automatiquement.")

    # 1) On commence par /companies : le serveur peut nous rediriger vers le
    #    nouveau portail de login, même si son URL a changé.
    page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=config.timeout_ms)
    wait_page_stable(page, config.timeout_ms)

    # Si une session existante fonctionne, inutile de refaire le login.
    if not _login_form_visible(page) and ("/companies" in page.url.lower() or _dashboard_reachable(page, config)):
        logger.info("Session existante valide : %s", page.url)
        return

    # 2) Fallback explicite vers l'URL de login configurée.
    if not _login_form_visible(page):
        try:
            page.goto(URL_CONNEXION, wait_until="domcontentloaded", timeout=config.timeout_ms)
            wait_page_stable(page, config.timeout_ms)
        except Exception as exc:
            logger.warning("Ouverture de l'URL de connexion configurée impossible : %s", exc)

    # 3) Le nouveau portail peut charger le formulaire avec un léger délai.
    deadline = time.time() + max(8, config.timeout_ms / 1000)
    scope = user_loc = pass_loc = button = None
    while time.time() < deadline:
        scope, user_loc, pass_loc, button = _find_login_scope(page, logger)
        if user_loc is not None and pass_loc is not None:
            break
        page.wait_for_timeout(300)

    if user_loc is None or pass_loc is None or scope is None:
        maybe_save_debug(page, config, "login_form_introuvable", logger)
        raise RuntimeError(
            "Formulaire de connexion introuvable. "
            f"URL actuelle={page.url!r}. La nouvelle interface ne correspond plus aux sélecteurs connus. "
            "Une capture/HTML de diagnostic a été enregistré(e)."
        )

    if not config.username or not config.password:
        raise RuntimeError("SUBAKOUA_USER / SUBAKOUA_PASS manquants dans .env")

    logger.info("Champs détectés : Identifiant / Mot de passe. Remplissage sécurisé...")
    user_loc.fill(config.username)
    pass_loc.fill(config.password)

    # 4) Soumission robuste : bouton accessible, bouton submit, puis Enter.
    submitted = False
    if button is not None:
        try:
            button.click()
            submitted = True
        except Exception as exc:
            logger.warning("Clic sur 'Se connecter' impossible : %s", exc)

    if not submitted:
        try:
            pass_loc.press("Enter")
            submitted = True
        except Exception as exc:
            raise RuntimeError(f"Impossible de soumettre le formulaire de connexion : {exc}") from exc

    # 5) Attente de disparition du formulaire / changement de route.
    end = time.time() + max(15, config.timeout_ms / 1000)
    while time.time() < end:
        wait_page_stable(page, min(config.timeout_ms, 8000))
        if not _login_form_visible(page):
            break
        page.wait_for_timeout(500)

    # Détecte explicitement les messages d'échec fréquents sans afficher le mot de passe.
    try:
        body = page.locator("body").inner_text(timeout=2000).lower()
        if any(msg in body for msg in [
            "identifiant ou mot de passe incorrect",
            "mot de passe incorrect",
            "identifiants invalides",
            "invalid credentials",
            "connexion impossible",
        ]):
            raise RuntimeError("Subakoua refuse les identifiants fournis.")
    except RuntimeError:
        raise
    except Exception:
        pass

    # 6) Validation finale : /companies doit être accessible sans revenir au login.
    try:
        page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=config.timeout_ms)
        wait_page_stable(page, config.timeout_ms)
    except Exception as exc:
        maybe_save_debug(page, config, "dashboard_apres_login_inaccessible", logger)
        raise RuntimeError(f"Navigation vers le tableau de bord impossible après connexion : {exc}") from exc

    if _login_form_visible(page):
        maybe_save_debug(page, config, "connexion_non_etablie", logger)
        raise RuntimeError(
            "Le formulaire de connexion est toujours présent après la soumission. "
            "Vérifie les identifiants ou consulte la capture de diagnostic."
        )

    logger.info("Connexion Subakoua réussie | page=%s", page.url)

    try:
        config.state_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(config.state_file))
        logger.info("Session Playwright sauvegardée : %s", config.state_file)
    except Exception as exc:
        logger.warning("Sauvegarde de session impossible : %s", exc)


def find_chromium_executable() -> str | None:
    """Cherche Chromium système (utile sur Streamlit Cloud) avant le navigateur bundle Playwright."""
    candidates = [
        os.getenv("CHROMIUM_EXECUTABLE", "").strip(),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def launch_browser(playwright, config: ScraperConfig, logger: logging.Logger):
    """Lance Chromium avec un binaire système si disponible, sinon le bundle Playwright."""
    kwargs: dict[str, Any] = {
        "headless": config.headless,
        "slow_mo": config.slow_mo_ms,
        "args": ["--disable-dev-shm-usage"],
    }
    executable = find_chromium_executable()
    if executable:
        kwargs["executable_path"] = executable
        # Les environnements cloud peuvent ne pas permettre le sandbox Chromium.
        kwargs["args"] = ["--disable-dev-shm-usage", "--no-sandbox"]
        logger.info("Chromium système utilisé : %s", executable)
    else:
        logger.info("Aucun Chromium système détecté : utilisation du navigateur Playwright installé.")
    return playwright.chromium.launch(**kwargs)


def discover_available_periods(page: Page, config: ScraperConfig, logger: logging.Logger) -> list[str]:
    """Lit les périodes réellement proposées par le sélecteur du portail après connexion."""
    selector = "okw-select-period span[role='combobox']"
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=config.timeout_ms)
    loc.click()
    page.wait_for_timeout(250)
    labels = page.locator("div.p-select-option-label").all_inner_texts()
    periods: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        value = re.sub(r"\s+", " ", str(raw)).strip()
        if value and value not in seen:
            seen.add(value)
            periods.append(value)
    try:
        loc.press("Escape")
    except Exception:
        pass
    if periods:
        logger.info("Périodes découvertes sur Subakoua : %s", ", ".join(periods))
    else:
        logger.warning("Aucune période n'a été détectée dans le sélecteur live.")
    return periods


def create_context(browser, config: ScraperConfig, logger: logging.Logger) -> BrowserContext:
    kwargs: dict[str, Any] = {
        "viewport": {"width": 1440, "height": 1000},
        "locale": "fr-FR",
        "timezone_id": "Europe/Paris",
    }
    if config.state_file.exists():
        kwargs["storage_state"] = str(config.state_file)
        logger.info("Réutilisation de la session : %s", config.state_file)
    return browser.new_context(**kwargs)


def ouvrir_module(page: Page, label: str, config: ScraperConfig, logger: logging.Logger) -> None:
    # Première stratégie : texte visible.
    try:
        loc = page.get_by_text(label, exact=False).first
        loc.wait_for(state="visible", timeout=5000)
        loc.click()
        wait_page_stable(page, config.timeout_ms)
        return
    except Exception:
        pass

    # Deuxième stratégie : lien dont le texte contient le libellé.
    href = page.evaluate(
        """(label) => {
            const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
            const target = norm(label);
            const a = Array.from(document.querySelectorAll('a[href]')).find(x => norm(x.innerText).includes(target));
            return a ? a.getAttribute('href') : null;
        }""",
        label,
    )
    if href:
        page.goto(urljoin(DOMAINE_BASE, href), wait_until="domcontentloaded", timeout=config.timeout_ms)
        wait_page_stable(page, config.timeout_ms)
        return
    raise RuntimeError(f"Module introuvable : {label}")


def selectionner_mois(page: Page, periode_cible: str, config: ScraperConfig) -> None:
    selector = "okw-select-period span[role='combobox']"
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=config.timeout_ms)
    actuel = loc.inner_text()
    if periode_cible in actuel:
        return

    loc.click()
    page.wait_for_timeout(250)
    options = page.locator("div.p-select-option-label")
    target = options.filter(has_text=periode_cible).last
    target.wait_for(state="visible", timeout=5000)
    target.click()
    wait_page_stable(page, config.timeout_ms)
    page.wait_for_timeout(900)


def verifier_specimen(page: Page) -> bool:
    try:
        if page.get_by_text("Vous n'avez pas encore acheté ce document", exact=False).is_visible():
            return True
    except Exception:
        pass
    try:
        return page.get_by_text("SPECIMEN", exact=False).is_visible()
    except Exception:
        return False


def extract_document_links(page: Page) -> list[dict[str, str]]:
    """Extrait d'abord les cartes documentelles connues, puis un fallback plus large."""
    docs = page.evaluate(r"""
        () => {
            const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
            let anchors = Array.from(document.querySelectorAll('a.linkItemCard[href]'));
            if (!anchors.length) {
                anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
                    const txt = norm(a.innerText).toLowerCase();
                    const cls = norm(a.className).toLowerCase();
                    return txt && (/document|tableau|rapport|extrait|bilan|compte/.test(txt + ' ' + cls));
                });
            }
            return anchors.map(a => ({
                title: norm(a.querySelector('.text-regular-md, .linkItemCard-title')?.innerText || a.innerText || 'Document'),
                url: a.getAttribute('href')
            })).filter(x => x.url && x.title);
        }
    """)
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for doc in docs:
        title = re.sub(r"\s+", " ", str(doc.get("title", "Document"))).strip() or "Document"
        url = urljoin(DOMAINE_BASE, str(doc.get("url", "")))
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.netloc and parsed.netloc != urlparse(DOMAINE_BASE).netloc:
            continue
        seen.add(url)
        unique.append({"title": title, "url": url})
    return unique


def extract_tabs(page: Page) -> list[str]:
    texts = page.locator(".p-menubar-item-content-item-label").all_inner_texts()
    seen: set[str] = set()
    result: list[str] = []
    for raw in texts:
        value = re.sub(r"\s+", " ", raw).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# ASPIRATION
# ---------------------------------------------------------------------------

def aspirer_page_courante(
    page: Page,
    liste_mois: list[str],
    config: ScraperConfig,
    logger: logging.Logger,
) -> dict[str, Any]:
    donnees_par_mois = {mois: {} for mois in liste_mois}
    documents = extract_document_links(page)

    if documents:
        url_mosaique = page.url
        for doc in documents:
            logger.info("Document | %s", doc["title"])
            try:
                def ouvrir():
                    page.goto(doc["url"], wait_until="domcontentloaded", timeout=config.timeout_ms)
                    wait_page_stable(page, config.timeout_ms)
                retry_call(ouvrir, config.retries, logger, f"Ouverture document {doc['title']}")

                for mois in liste_mois:
                    def extract_month():
                        selectionner_mois(page, mois, config)
                        if verifier_specimen(page):
                            return "SPECIMEN"
                        return page.evaluate(JS_EXTRACTEUR_UNIVERSEL)

                    data = retry_call(extract_month, config.retries, logger, f"Extraction {doc['title']} / {mois}")
                    unique_title = doc["title"]
                    suffix = 2
                    while unique_title in donnees_par_mois[mois]:
                        unique_title = f"{doc['title']} #{suffix}"
                        suffix += 1
                    donnees_par_mois[mois][unique_title] = data

                page.goto(url_mosaique, wait_until="domcontentloaded", timeout=config.timeout_ms)
                wait_page_stable(page, config.timeout_ms)
            except Exception as exc:
                logger.error("Document en erreur | %s | %s", doc["title"], exc)
                maybe_save_debug(page, config, f"document_{doc['title']}", logger)
                try:
                    page.goto(url_mosaique, wait_until="domcontentloaded", timeout=config.timeout_ms)
                    wait_page_stable(page, config.timeout_ms)
                except Exception:
                    pass
    else:
        for mois in liste_mois:
            logger.info("Période | %s", mois)
            def extract_month_simple():
                selectionner_mois(page, mois, config)
                if verifier_specimen(page):
                    return "SPECIMEN"
                return page.evaluate(JS_EXTRACTEUR_UNIVERSEL)
            try:
                donnees_par_mois[mois] = retry_call(
                    extract_month_simple, config.retries, logger, f"Extraction {mois}"
                )
            except Exception as exc:
                logger.error("Période en erreur | %s | %s", mois, exc)
                maybe_save_debug(page, config, f"periode_{mois}", logger)
                donnees_par_mois[mois] = {"__scraper_error__": str(exc)}

    return donnees_par_mois


def aspirer_structure_intelligente(
    page: Page,
    liste_mois: list[str],
    config: ScraperConfig,
    logger: logging.Logger,
) -> dict[str, Any]:
    page.wait_for_timeout(700)
    onglets = extract_tabs(page)
    if not onglets:
        return aspirer_page_courante(page, liste_mois, config, logger)

    logger.info("Onglets détectés : %s", ", ".join(onglets))
    donnees_par_mois = {mois: {} for mois in liste_mois}
    url_module = page.url

    for onglet in onglets:
        try:
            page.goto(url_module, wait_until="domcontentloaded", timeout=config.timeout_ms)
            wait_page_stable(page, config.timeout_ms)
            page.get_by_text(onglet, exact=True).first.click(timeout=5000)
            wait_page_stable(page, config.timeout_ms)
            resultat = aspirer_page_courante(page, liste_mois, config, logger)
            for mois in liste_mois:
                donnees_par_mois[mois][onglet] = resultat.get(mois, {})
        except Exception as exc:
            logger.error("Onglet en erreur | %s | %s", onglet, exc)
            maybe_save_debug(page, config, f"onglet_{onglet}", logger)
    return donnees_par_mois


def revenir_au_dashboard(page: Page, config: ScraperConfig) -> None:
    page.goto(URL_DASHBOARD, wait_until="domcontentloaded", timeout=config.timeout_ms)
    wait_page_stable(page, config.timeout_ms)


# ---------------------------------------------------------------------------
# SELECTION CLI
# ---------------------------------------------------------------------------

def parse_periods(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(MOIS_DISPONIBLES)
    if not args.periods:
        return [MOIS_DISPONIBLES[0]]

    parts = [p.strip() for p in args.periods.split(",") if p.strip()]
    selected: list[str] = []
    for part in parts:
        if "-" in part and part.replace("-", "").isdigit():
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            for idx in range(start, end + 1):
                if 1 <= idx <= len(MOIS_DISPONIBLES):
                    selected.append(MOIS_DISPONIBLES[idx - 1])
        elif part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(MOIS_DISPONIBLES):
                selected.append(MOIS_DISPONIBLES[idx - 1])
        else:
            matches = [m for m in MOIS_DISPONIBLES if m.lower() == part.lower()]
            if matches:
                selected.append(matches[0])
    return list(dict.fromkeys(selected)) or [MOIS_DISPONIBLES[0]]


def parse_modules(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.modules == "all" or not args.modules:
        return list(MODULES_A_VISITER)
    requested = {x.strip().lower() for x in args.modules.split(",") if x.strip()}
    result = []
    for display, key in MODULES_A_VISITER:
        if display.lower() in requested or key.lower() in requested:
            result.append((display, key))
    return result or list(MODULES_A_VISITER)


# ---------------------------------------------------------------------------
# ROBOT GLOBAL
# ---------------------------------------------------------------------------

def lancer_robot_global(
    liste_mois: list[str],
    modules: list[tuple[str, str]],
    config: ScraperConfig,
    logger: logging.Logger,
) -> dict[str, Any]:
    run_id = timestamp()
    started = datetime.now().isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started,
        "extractor_version": EXTRACTOR_VERSION,
        "periods": liste_mois,
        "modules": [key for _, key in modules],
        "mode": "force" if not config.incremental else "incremental",
        "status": "running",
        "module_results": [],
    }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    connection = None
    browser = None
    context = None
    try:
        connection = connect_mysql(config)
        has_unique_key = ensure_erp_table(connection, logger)

        with sync_playwright() as p:
            browser = launch_browser(p, config, logger)
            context = create_context(browser, config, logger)
            page = context.new_page()
            page.set_default_timeout(config.timeout_ms)
            page.set_default_navigation_timeout(config.timeout_ms)

            try:
                login(page, context, config, logger)
                revenir_au_dashboard(page, config)

                for mot_cle, cle_dict in modules:
                    logger.info("===== MODULE %s (%s) =====", mot_cle, cle_dict)
                    result_mod = {
                        "module": cle_dict,
                        "display": mot_cle,
                        "scraped_periods": [],
                        "skipped_periods": [],
                        "errors": [],
                    }
                    try:
                        a_traiter: list[str] = []
                        if config.incremental:
                            for mois in liste_mois:
                                existing = existing_module_data(connection, mois, cle_dict)
                                if existing is None or contains_specimen(existing) or contains_scraper_error(existing):
                                    a_traiter.append(mois)
                                else:
                                    result_mod["skipped_periods"].append(mois)
                            if not a_traiter:
                                logger.info("Aucun changement à aspirer pour %s", cle_dict)
                                summary["module_results"].append(result_mod)
                                continue
                        else:
                            a_traiter = liste_mois

                        ouvrir_module(page, mot_cle, config, logger)
                        donnees_multi_mois = aspirer_structure_intelligente(page, a_traiter, config, logger)
                        synchroniser_module(connection, cle_dict, donnees_multi_mois, logger, has_unique_key)
                        result_mod["scraped_periods"] = list(a_traiter)
                        revenir_au_dashboard(page, config)
                    except Exception as exc:
                        result_mod["errors"].append(str(exc))
                        logger.exception("Module en erreur | %s", cle_dict)
                        maybe_save_debug(page, config, f"module_{cle_dict}", logger)
                        try:
                            revenir_au_dashboard(page, config)
                        except Exception:
                            pass
                    summary["module_results"].append(result_mod)

                summary["status"] = "completed" if not any(r["errors"] for r in summary["module_results"]) else "completed_with_errors"
            finally:
                if context is not None:
                    try:
                        context.storage_state(path=str(config.state_file))
                    except Exception:
                        pass
                    context.close()
                if browser is not None:
                    browser.close()
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary["duration_seconds"] = (
        datetime.fromisoformat(summary["finished_at"]) - datetime.fromisoformat(summary["started_at"])
    ).total_seconds()

    json_path = config.output_dir / f"run_{run_id}.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Run terminé | %s | rapport=%s", summary["status"], json_path)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scraper robuste Subakoua ERP")
    parser.add_argument("--user", help="Identifiant Subakoua (évite de le stocker dans le fichier .env)")
    parser.add_argument("--periods", help="Périodes par index : 1,2,3 ou plage 1-12")
    parser.add_argument("--all", action="store_true", help="Scrape toutes les périodes disponibles")
    parser.add_argument("--modules", default="all", help="Modules par nom/clé séparés par des virgules, ou all")
    parser.add_argument("--force", action="store_true", help="Ignore le mode incrémental et rescrape les périodes sélectionnées")
    parser.add_argument("--headless", dest="headless", action="store_true", help="Navigateur sans interface")
    parser.add_argument("--show-browser", dest="headless", action="store_false", help="Affiche le navigateur")
    parser.set_defaults(headless=None)
    return parser


def complete_credentials(config: ScraperConfig, args: argparse.Namespace, logger: logging.Logger) -> ScraperConfig:
    """Complète les identifiants Subakoua interactivement si absents, sans jamais loguer le mot de passe."""
    username = config.username
    password = config.password

    if not username and sys.stdin.isatty():
        username = input("Identifiant Subakoua : ").strip()
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Mot de passe Subakoua : ")

    if username and not config.username:
        logger.info("Identifiant Subakoua fourni interactivement.")
    if password and not config.password:
        logger.info("Mot de passe Subakoua fourni interactivement.")

    return replace(config, username=username, password=password)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args)
    logger = build_logger(config.output_dir)
    config = complete_credentials(config, args, logger)

    periods = parse_periods(args)
    modules = parse_modules(args)
    logger.info("Extracteur %s | périodes=%s | modules=%s | mode=%s", EXTRACTOR_VERSION, len(periods), len(modules), "force" if args.force else "incremental")

    if not config.username or not config.password:
        logger.error("Identifiants Subakoua absents. Renseigne .env (SUBAKOUA_USER / SUBAKOUA_PASS) ou lance le scraper dans un terminal interactif.")
        return 2

    try:
        summary = lancer_robot_global(periods, modules, config, logger)
        errors = sum(len(r.get("errors", [])) for r in summary.get("module_results", []))
        logger.info("Résumé | modules OK/traités=%s | erreurs=%s", len(summary.get("module_results", [])), errors)
        return 0 if errors == 0 else 1
    except Exception as exc:
        logger.exception("Arrêt du scraper : %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
