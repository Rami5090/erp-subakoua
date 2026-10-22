"""Exécute réellement erp_engine.py (module « Centre de décision ») sans Streamlit ni MySQL.

Usage : python simulator_smoke_runner.py <scenario>   → imprime un JSON de synthèse.

Streamlit est remplacé par un double qui renvoie les valeurs par défaut des widgets ;
MySQL par une base SQLite en mémoire alimentée selon le scénario. Lancé en sous-processus par
test_simulator_smoke_v294.py pour ne jamais polluer la session de tests.
"""
from __future__ import annotations

import json
import os
import runpy
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "aout_sans_juillet"
LOG: dict = {"metrics": [], "warnings": [], "errors": [], "success": [], "captions": [], "inputs": {},
             "tables": [], "charts": 0}

#: Leviers du simulateur à surcharger (clé session_state -> valeur), ex.
#: SIM_DECISIONS='{"sim_ced_dec": 2}'. S'applique comme valeur par défaut des
#: widgets : le simulateur purge ses leviers au changement de contexte, une
#: valeur injectée dans session_state serait donc effacée avant d'être lue.
OVERRIDES: dict = {}


# --------------------------------------------------------------------------- Streamlit factice
class Stub:
    def __init__(self, state, sidebar=False):
        self._state, self._sidebar = state, sidebar

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(())
    def __call__(self, *a, **k): return self

    def __getattr__(self, name):
        st = self

        def noop(*a, **k): return st

        table = {
            "number_input": st._number, "text_input": st._text, "selectbox": st._select,
            "radio": st._radio, "slider": st._slider, "checkbox": lambda l, *a, **k: st._chk(l, k),
            "button": lambda *a, **k: False,
            "form_submit_button": lambda *a, **k: os.environ.get("SIM_SUBMIT") == "1",
            "columns": st._columns, "tabs": lambda labels, *a, **k: [Stub(st._state) for _ in labels],
            "metric": st._metric, "warning": st._msg("warnings"), "error": st._msg("errors"),
            "dataframe": st._dataframe, "altair_chart": st._chart,
            "success": st._msg("success"), "caption": st._msg("captions"),
            "stop": lambda: (_ for _ in ()).throw(SystemExit()),
        }
        return table.get(name, noop)

    # --- widgets
    def _override_par_label(self, label):
        """Surcharge dont la clé apparaît dans le libellé (widgets sans `key`)."""
        for _key, _value in OVERRIDES.items():
            if _key and _key in str(label):
                return _value
        return None

    def _key_val(self, k, default):
        key = k.get("key")
        if key is not None:
            override = OVERRIDES.get(key)
            if override is not None:
                self._state[key] = override
                return override
            if key not in self._state:
                self._state[key] = default
            return self._state[key]
        return default

    def _number(self, label, *a, **k):
        override = OVERRIDES.get(k.get("key")) if k.get("key") else self._override_par_label(label)
        if override is not None:
            self._state[k.get("key") or label] = override
            LOG["inputs"][k.get("key") or label] = override
            return override
        v = self._key_val(k, k.get("value", 0))
        LOG["inputs"][k.get("key") or label] = v
        return v

    def _text(self, label, *a, **k): return self._key_val(k, k.get("value", ""))
    def _slider(self, label, *a, **k): return self._key_val(k, k.get("value", a[0] if a else 0))

    def _chk(self, label, k):
        override = OVERRIDES.get(k.get("key")) if k.get("key") else self._override_par_label(label)
        if override is not None:
            return override
        return self._key_val(k, k.get("value", False))

    def _select(self, label, options=(), *a, **k):
        options = list(options)
        if self._sidebar and "période" in label:
            return os.environ.get("SIM_MONTH", options[0])
        if "Période analysée" in str(label):
            # SIM_TOUR=8 : choisit le tour de la page d'analyse quantitative.
            tour = os.environ.get("SIM_TOUR", "")
            if tour.isdigit() and int(tour) in options:
                return int(tour)
        return self._key_val(k, options[0] if options else None)

    def _radio(self, label, options=(), *a, **k):
        options = list(options)
        if self._sidebar:
            # SIM_WORKSPACE permet de choisir l'espace de travail ("Données réelles"...).
            choisi = os.environ.get("SIM_WORKSPACE", "")
            for _opt in options:
                if choisi and choisi in str(_opt):
                    return _opt
            return options[-1]
        return self._key_val(k, options[0])

    def _columns(self, spec, *a, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [Stub(self._state) for _ in range(n)]

    def _metric(self, label, value=None, delta=None, *a, **k):
        LOG["metrics"].append([str(label), str(value), None if delta is None else str(delta)])
        return self

    def _dataframe(self, data, *a, **k):
        try:
            rows = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
        except Exception:
            rows = []
        LOG["tables"].append(rows)
        return self

    def _chart(self, chart=None, *a, **k):
        LOG["charts"] += 1
        return self

    def _msg(self, bucket):
        def f(text="", *a, **k):
            LOG[bucket].append(str(text))
            return self
        return f


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.secrets = {}
        self._main = Stub(self.session_state)
        self.sidebar = Stub(self.session_state, sidebar=True)

    def cache_resource(self, *a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

    cache_data = cache_resource

    def __getattr__(self, name):
        return getattr(self._main, name)


# --------------------------------------------------------------------------- MySQL factice (SQLite)
class FakeResult:
    def __init__(self, cur): self._rows = cur.fetchall()
    def all(self): return [tuple(r) for r in self._rows]
    def mappings(self):
        rows = self._rows
        return types.SimpleNamespace(first=lambda: dict(rows[0]) if rows else None,
                                     all=lambda: [dict(r) for r in rows])


def _sql_pour_sqlite(sql: str) -> str:
    """MySQL -> SQLite : `ON DUPLICATE KEY UPDATE` devient `ON CONFLICT ... DO UPDATE`.

    Les tables du simulateur ont toutes `Tour_ID` en clé primaire : la cible de
    conflit est donc la même pour toutes.
    """
    if "ON DUPLICATE KEY UPDATE" not in sql:
        return sql
    tete, _, queue = sql.partition("ON DUPLICATE KEY UPDATE")
    return f"{tete}ON CONFLICT(Tour_ID) DO UPDATE SET {queue}"


class FakeConn:
    def __init__(self, db): self.db = db
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        return FakeResult(self.db.execute(_sql_pour_sqlite(str(sql)), params or {}))


class FakeEngine(sqlite3.Connection):
    def connect(self): return FakeConn(self)
    begin = connect


def build_db() -> FakeEngine:
    db = sqlite3.connect(":memory:", factory=FakeEngine)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE erp_donnees (id INTEGER PRIMARY KEY AUTOINCREMENT, periode TEXT, type_donnee TEXT, module TEXT, contenu TEXT);
        CREATE TABLE Historique_Equipe (Tour_ID INT PRIMARY KEY, Stock_Neo3 REAL, Stock_Neo5 REAL, Stock_Neo7 REAL, Stock_Renforts REAL, Stock_Manchons REAL, Stock_Fermetures REAL, Tresorerie_Initiale REAL);
        CREATE TABLE Parc_Machines_Mensuel (Tour_ID INT PRIMARY KEY, Mois TEXT, Nb_Machines_Decoupe INT, Nb_Machines_Assemblage INT, Nb_Machines_Cond INT, Acquisition_Faite INT, Acq_Decoupe INT, Acq_Assemblage INT, Acq_Cond INT, Cession_Faite INT, Ced_Decoupe INT, Ced_Assemblage INT, Ced_Cond INT, Px_Cession_Decoupe REAL, Px_Cession_Assemblage REAL, Px_Cession_Cond REAL);
        CREATE TABLE Ventes_Historique (Tour_ID INT PRIMARY KEY, Mois TEXT, Ventes_S3 INT, Ventes_I3 INT, Ventes_S5 INT, Ventes_I5 INT, Ventes_I7 INT);
        CREATE TABLE Decisions_Marketing (Tour_ID INT PRIMARY KEY, Mois TEXT, Prix_S3 REAL, Prix_I3 REAL, Prix_S5 REAL, Prix_I5 REAL, Prix_I7 REAL, Budget_Marque REAL, Axe_Principal TEXT, Axe_Accessoire TEXT, Pub_S3 REAL, Pub_I3 REAL, Pub_S5 REAL, Pub_I5 REAL, Pub_I7 REAL);
        CREATE TABLE Decisions_RH_Mensuel (Tour_ID INT PRIMARY KEY, Mois TEXT, Eff_Employes_Prod INT, Sal_Employes_Prod REAL, Eff_Cadres_Prod INT, Sal_Cadres_Prod REAL, Eff_Directeurs_Prod INT, Sal_Directeurs_Prod REAL, Eff_Employes_Appro INT, Sal_Employes_Appro REAL, Eff_Cadres_Appro INT, Sal_Cadres_Appro REAL, Eff_Directeurs_Appro INT, Sal_Directeurs_Appro REAL, Eff_Employes_Admin INT, Sal_Employes_Admin REAL, Eff_Cadres_Admin INT, Sal_Cadres_Admin REAL, Eff_Directeurs_Admin INT, Sal_Directeurs_Admin REAL);
        CREATE TABLE Finances_Mensuelles (Tour_ID INT PRIMARY KEY, Mois TEXT, CA_Net REAL, Achats_Matieres REAL, Autres_Charges_Externes REAL, Remuneration_Personnel REAL, Charges_Sociales REAL, Dotations_Amortissements REAL, Resultat_Net REAL, Total_Actif_Immobilise REAL, Total_Actif_Circulant REAL, Total_Capitaux_Propres REAL, Emprunts_Bancaires REAL, Disponibilites_Banque REAL, VNC_Cession_HT REAL, TVA_Cession REAL, PMV_Cession REAL);
        CREATE TABLE simulations_seeds (Token_Seed TEXT, Tour_ID INT, Nom_Scenario TEXT, Parametres_JSON TEXT);
        CREATE TABLE Normes_MS_Prod (x INT); CREATE TABLE Normes_MS_Appro (x INT); CREATE TABLE Normes_MS_Admin (x INT);
        CREATE TABLE erp_etudes (id INTEGER PRIMARY KEY AUTOINCREMENT, periode TEXT, study_id TEXT, label TEXT, module TEXT, price REAL DEFAULT 0, source_endpoint TEXT, read_allowed INT DEFAULT 1, payload TEXT, fetched_at TEXT);
    """)
    return db


def erp(db, periode, module, contenu):
    db.execute("INSERT INTO erp_donnees (periode, type_donnee, module, contenu) VALUES (?, 'etat_actuel', ?, ?)",
               (periode, module, json.dumps(contenu, ensure_ascii=False)))


def seed(db, scenario):
    # Historique SQL saisi jusqu'en Juin (tour 6) ; marketing jusqu'en Mai (tour 5) : comme sur ta capture.
    for t in range(1, 7):
        db.execute("INSERT INTO Historique_Equipe VALUES (?,2.0,1.0,0.5,1000,100,500,0)", (t,))
        db.execute("INSERT INTO Parc_Machines_Mensuel (Tour_ID, Mois, Nb_Machines_Decoupe, Nb_Machines_Assemblage, Nb_Machines_Cond) VALUES (?,?,10,16,4)", (t, f"m{t}"))
        db.execute("INSERT INTO Decisions_RH_Mensuel (Tour_ID, Mois, Eff_Employes_Prod, Sal_Employes_Prod) VALUES (?,?,15,2000)", (t, f"m{t}"))
    for t in range(1, 6):
        db.execute("INSERT INTO Decisions_Marketing VALUES (?,?,115,190,220,280,375,60500,'Prix','Qualité',3100,4100,3200,4200,3500)", (t, f"m{t}"))
        db.execute("INSERT INTO Ventes_Historique VALUES (?,?,40,400,90,1200,2000)", (t, f"m{t}"))
    # Une cession de machines (1 Assemblage) sur le mois saisi et sur le mois de
    # référence : exerce de bout en bout le chemin « cession » de l'application.
    db.execute(
        "UPDATE Parc_Machines_Mensuel SET Cession_Faite=1, Ced_Assemblage=1, Px_Cession_Assemblage=150000 "
        "WHERE Tour_ID IN (1, 6)"
    )
    # Étude de coûts de production : coûts variables unitaires (€/unité).
    _couts = {"SHORTY_C": 60.0, "MONO_C": 90.0, "SHORTY_T": 80.0, "MONO_T": 120.0, "MONO_F": 150.0}
    _details = []
    for _ref, _cout in _couts.items():
        _n = 100
        _details.append({
            "productRef": _ref,
            "producedProductsNumber": _n,
            "directCostDetail": {"phasesCostDetails": [{
                "materialsCosts": [{"totalCost": _cout * _n * 0.6}],
                "workshopsCosts": [{"totalCost": _cout * _n * 0.3}],
            }]},
            "finalProductionPhaseCostDetail": {"workshopsCosts": [{"totalCost": _cout * _n * 0.1}]},
        })
    db.execute(
        "INSERT INTO erp_etudes (periode, study_id, payload, read_allowed) VALUES (?,?,?,1)",
        ("Année 1 - Janvier", "productionCostDetailStudy",
         json.dumps({"productsProductionCostDetails": _details}, ensure_ascii=False)),
    )

    soldes = lambda ini, fin: {"Banque": {"Extrait bancaire": {"Soldes bancaires": [{"Solde initial": ini, "Solde final": fin}]}}}
    if scenario == "aout_sans_juillet":
        # Rien pour Juillet ; l'extrait d'Août (mois simulé, déjà clos) donne l'ouverture.
        erp(db, "Année 1 - Août", "banque_assurance", soldes(1_250_000.0, 900_000.0))
    elif scenario == "juillet_present":
        erp(db, "Année 1 – Juillet", "banque_assurance", soldes(1_100_000.0, 1_250_000.0))   # tiret « – » volontaire
    elif scenario == "decisions_scrapees":
        erp(db, "Année 1 - Juillet", "banque_assurance", {"Extrait bancaire": {"Soldes bancaires": [{"Solde initial": 1.0, "Solde final": 2_000_000.0}]}})
        erp(db, "Année 1 - Juillet", "marketing", {"__lignes__": [
            {"section": "Prix de vente", "ligne": "Shorty 3", "valeurs": [130]},
            {"section": "Budget publicitaire", "ligne": "Budget Pub Marque HT", "valeurs": [70000]}]})
        erp(db, "Année 1 - Juillet", "production", {"__lignes__": [
            {"section": "Quantités à produire", "ligne": "Integral 7", "valeurs": [1800]}]})
    elif scenario == "decisions_vides":
        # Lignes réelles produites par le scraper v2.4.0 : page d'accueil prise pour une page de décisions.
        fx = json.loads((ROOT / "fixtures" / "decisions_page_vide_reelle.json").read_text(encoding="utf-8"))
        for mod in ("marketing", "rh", "production"):
            erp(db, "Année 1 - Juillet", mod, fx["contenu"])
    elif scenario == "rien":
        pass
    db.commit()


def main():
    # La synthèse contient des € et des emojis : cp1252 ne sait pas les encoder.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    db = build_db()
    seed(db, SCENARIO)

    fake = FakeStreamlit()
    OVERRIDES.update(json.loads(os.environ.get("SIM_DECISIONS", "{}")))
    sys.modules["streamlit"] = fake
    sa = types.ModuleType("sqlalchemy")
    sa.create_engine = lambda *a, **k: db
    sa.text = lambda s: s
    sa.bindparam = lambda *a, **k: None
    sa_engine = types.ModuleType("sqlalchemy.engine")
    sa_engine.URL = types.SimpleNamespace(create=lambda *a, **k: "url")
    sys.modules.update({"sqlalchemy": sa, "sqlalchemy.engine": sa_engine})
    dotenv = types.ModuleType("dotenv"); dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = dotenv
    auth = types.ModuleType("auth")
    auth.setup = lambda: None
    # SIM_EDITOR=1 ouvre les espaces réservés aux utilisateurs (saisie des données).
    auth.is_editor = lambda: os.environ.get("SIM_EDITOR") == "1"
    auth.require_editor = lambda *a, **k: None
    auth.readonly_notice = lambda *a, **k: None
    sys.modules["auth"] = auth
    dbu = types.ModuleType("db_utils")
    dbu.get_readonly_engine_if_configured = lambda: None
    dbu.get_read_engine = lambda: db
    dbu.get_write_engine = lambda: db
    sys.modules["db_utils"] = dbu
    for k, v in {"DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p", "DB_NAME": "n", "DB_PORT": "3306"}.items():
        os.environ[k] = v

    os.environ.setdefault("SIM_MONTH", "Août N+1")
    try:
        # SIM_PAGE=6_Analyse_Quantitative.py exécute une page de `pages/`
        # au lieu du cockpit, avec la même base SQLite en mémoire.
        page = os.environ.get("SIM_PAGE", "")
        if page:
            runpy.run_path(str(ROOT / "pages" / page), run_name="__main__")
        else:
            runpy.run_path(str(ROOT / "erp_engine.py"), run_name="__main__")
    except SystemExit:
        pass
    LOG["session_keys"] = sorted(fake.session_state)
    try:
        LOG["finances"] = [
            dict(r) for r in db.execute(
                "SELECT Tour_ID, Mois, Resultat_Net, Total_Actif_Immobilise, Disponibilites_Banque, "
                "VNC_Cession_HT, TVA_Cession, PMV_Cession FROM Finances_Mensuelles"
            ).fetchall()
        ]
        LOG["parc_cessions"] = [
            dict(r) for r in db.execute(
                "SELECT Tour_ID, Mois, Cession_Faite, Ced_Decoupe, Ced_Assemblage, Ced_Cond, "
                "Px_Cession_Assemblage FROM Parc_Machines_Mensuel WHERE Cession_Faite=1"
            ).fetchall()
        ]
    except Exception as exc:
        LOG["db_error"] = f"{type(exc).__name__}: {exc}"
    print("##JSON##" + json.dumps(LOG, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
