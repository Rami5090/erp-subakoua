import os
import streamlit as st
import pandas as pd
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from finance_engine import (
    TOURS_PERIODES,
    NOMBRE_PARTS,
    TAUX_CHARGES_SOCIALES,
    calculate_projection,
    build_bfr_projection,
    complete_bfr_from_previous_state,
    opening_bfr_from_current_state,
    extract_financial_state,
    extract_client_receivable_schedule,
    infer_client_payment_mix,
    project_operating_cash_schedule,
    period_for_tour,
    calculate_vat_projection,
    calculate_fiscal_social_projection,
    project_bank_debt,
)

load_dotenv()

st.set_page_config(page_title="ERP Subakoua - Cockpit Stratégique", layout="wide", initial_sidebar_state="expanded")

@st.cache_resource
def init_connection():
    """Initialise la connexion MySQL et renvoie (engine, diagnostic).

    Le code accepte les variables d'environnement ou les secrets Streamlit
    [mysql]. Une URL SQLAlchemy complète peut également être fournie via
    [mysql].url. Le mot de passe n'est jamais affiché dans le diagnostic.
    """
    host = user = password = database = None
    port = None
    try:
        # 1) Variables d'environnement (utile pour un déploiement générique)
        host = os.getenv("DB_HOST")
        user = os.getenv("DB_USER")
        password = os.getenv("DB_PASSWORD")
        database = os.getenv("DB_NAME")
        port_raw = os.getenv("DB_PORT")

        # 2) Secrets Streamlit [mysql] (Community Cloud)
        if not host and "mysql" in st.secrets:
            cfg = st.secrets["mysql"]
            # Supporte user et username pour éviter les erreurs de nommage.
            host = cfg.get("host")
            user = cfg.get("user") or cfg.get("username")
            password = cfg.get("password")
            database = cfg.get("database") or cfg.get("db_name")
            port_raw = cfg.get("port")

            # Cas encore plus simple : on peut fournir directement l'URL.
            direct_url = cfg.get("url")
        else:
            direct_url = None

        if direct_url:
            engine = create_engine(
                str(direct_url),
                connect_args={"ssl": {}},
                pool_pre_ping=True,
                pool_recycle=3600,
            )
        else:
            # Aucun port/base par défaut pour éviter de pointer silencieusement
            # vers une mauvaise base Aiven.
            if not host or not user or not password or not database or not port_raw:
                return None, (
                    "Configuration MySQL incomplète. Dans Streamlit Cloud, "
                    "renseigne [mysql] avec host, port, user/username, password et database."
                )
            port = int(port_raw)
            url = URL.create(
                drivername="mysql+pymysql",
                username=str(user),
                password=str(password),
                host=str(host),
                port=port,
                database=str(database),
            )
            engine = create_engine(
                url,
                connect_args={"ssl": {}},
                pool_pre_ping=True,
                pool_recycle=3600,
            )

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine, None

    except Exception as exc:
        # Diagnostic non sensible : le mot de passe est masqué s'il apparaît
        # exceptionnellement dans le message du driver.
        msg = f"{type(exc).__name__}: {exc}"
        if password:
            msg = msg.replace(str(password), "***")
        return None, msg

engine, db_error = init_connection()

def init_db_simu():
    if engine is None: return
    try:
        with engine.begin() as conn:
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS simulations_seeds (
                Token_Seed VARCHAR(50),
                Tour_ID INT,
                Nom_Scenario VARCHAR(100),
                Parametres_JSON JSON,
                PRIMARY KEY (Token_Seed, Tour_ID)
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS Historique_Equipe (
                Tour_ID INT PRIMARY KEY,
                Stock_Neo3 FLOAT, Stock_Neo5 FLOAT, Stock_Neo7 FLOAT, 
                Stock_Renforts FLOAT, Stock_Manchons FLOAT, Stock_Fermetures FLOAT, 
                Tresorerie_Initiale FLOAT
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS Parc_Machines_Mensuel (
                Tour_ID INT PRIMARY KEY, Mois VARCHAR(50),
                Nb_Machines_Decoupe INT, Nb_Machines_Assemblage INT, Nb_Machines_Cond INT,
                Acquisition_Faite INT, Acq_Decoupe INT, Acq_Assemblage INT, Acq_Cond INT
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS Ventes_Historique (
                Tour_ID INT PRIMARY KEY, Mois VARCHAR(50),
                Ventes_S3 INT, Ventes_I3 INT, Ventes_S5 INT, Ventes_I5 INT, Ventes_I7 INT
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS Decisions_Marketing (
                Tour_ID INT PRIMARY KEY, Mois VARCHAR(50),
                Prix_S3 FLOAT, Prix_I3 FLOAT, Prix_S5 FLOAT, Prix_I5 FLOAT, Prix_I7 FLOAT,
                Budget_Marque FLOAT, Axe_Principal VARCHAR(100), Axe_Accessoire VARCHAR(100),
                Pub_S3 FLOAT, Pub_I3 FLOAT, Pub_S5 FLOAT, Pub_I5 FLOAT, Pub_I7 FLOAT
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS Decisions_RH_Mensuel (
                Tour_ID INT PRIMARY KEY, Mois VARCHAR(50),
                Eff_Employes_Prod INT, Sal_Employes_Prod FLOAT, Eff_Cadres_Prod INT, Sal_Cadres_Prod FLOAT, Eff_Directeurs_Prod INT, Sal_Directeurs_Prod FLOAT,
                Eff_Employes_Appro INT, Sal_Employes_Appro FLOAT, Eff_Cadres_Appro INT, Sal_Cadres_Appro FLOAT, Eff_Directeurs_Appro INT, Sal_Directeurs_Appro FLOAT,
                Eff_Employes_Admin INT, Sal_Employes_Admin FLOAT, Eff_Cadres_Admin INT, Sal_Cadres_Admin FLOAT, Eff_Directeurs_Admin INT, Sal_Directeurs_Admin FLOAT
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS Finances_Mensuelles (
                Tour_ID INT PRIMARY KEY, Mois VARCHAR(50),
                CA_Net FLOAT, Achats_Matieres FLOAT, Autres_Charges_Externes FLOAT, Remuneration_Personnel FLOAT, Charges_Sociales FLOAT, 
                Dotations_Amortissements FLOAT, Resultat_Net FLOAT, Total_Actif_Immobilise FLOAT, Total_Actif_Circulant FLOAT, 
                Total_Capitaux_Propres FLOAT, Emprunts_Bancaires FLOAT, Disponibilites_Banque FLOAT
            )
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS erp_donnees (
                id INT AUTO_INCREMENT PRIMARY KEY,
                periode VARCHAR(50),
                type_donnee VARCHAR(50),
                module VARCHAR(50),
                contenu JSON
            )
            """))
    except Exception:
        pass

init_db_simu()

# ==========================================
# 2. CHARGEMENT BDD & FONCTIONS GLOBALES
# ==========================================
def parse_french_float(val):
    if isinstance(val, (int, float)): return float(val)
    try:
        clean_str = str(val).replace(" ", "").replace("\u202f", "").replace("\xa0", "").replace("€", "").replace(",", ".").strip()
        if clean_str == "" or clean_str == "-": return 0.0
        return float(clean_str)
    except: return 0.0

@st.cache_data
def charger_nomenclature_bdd():
    nom_secours = {
        'Shorty 3': {'Néoprène 3': {'minimum': 1.0, 'normal': 1.1, 'maximum': 1.2}, 'Renforts': {'minimum': 10.0, 'normal': 12.0, 'maximum': 15.0}, 'Manchons': {'minimum': 2.0, 'normal': 2.0, 'maximum': 2.0}, 'Fermetures': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Integral 3': {'Néoprène 3': {'minimum': 1.5, 'normal': 1.6, 'maximum': 1.8}, 'Renforts': {'minimum': 15.0, 'normal': 18.0, 'maximum': 20.0}, 'Manchons': {'minimum': 4.0, 'normal': 4.0, 'maximum': 4.0}, 'Fermetures': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Shorty 5': {'Néoprène 5': {'minimum': 1.0, 'normal': 1.1, 'maximum': 1.2}, 'Renforts': {'minimum': 10.0, 'normal': 12.0, 'maximum': 15.0}, 'Manchons': {'minimum': 2.0, 'normal': 2.0, 'maximum': 2.0}, 'Fermetures': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Integral 5': {'Néoprène 5': {'minimum': 1.5, 'normal': 1.6, 'maximum': 1.8}, 'Renforts': {'minimum': 15.0, 'normal': 18.0, 'maximum': 20.0}, 'Manchons': {'minimum': 4.0, 'normal': 4.0, 'maximum': 4.0}, 'Fermetures': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Integral 7': {'Néoprène 7': {'minimum': 1.5, 'normal': 1.6, 'maximum': 1.8}, 'Renforts': {'minimum': 20.0, 'normal': 22.0, 'maximum': 25.0}, 'Manchons': {'minimum': 4.0, 'normal': 4.0, 'maximum': 4.0}, 'Fermetures': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}}
    }
    if engine is None: return nom_secours
    try:
        query = "SELECT produit, matiere, coeff_min, coeff_normal, coeff_max FROM Parametres_Nomenclature"
        df = pd.read_sql(query, engine)
        if df.empty: return nom_secours
        nom_dict = {}
        for prod, group in df.groupby('produit'):
            nom_dict[prod] = {}
            for _, row in group.iterrows():
                nom_dict[prod][row['matiere']] = {
                    'minimum': float(row['coeff_min']),
                    'normal': float(row['coeff_normal']),
                    'maximum': float(row['coeff_max'])
                }
        return nom_dict
    except Exception: return nom_secours

@st.cache_data
def charger_temps_ateliers_bdd():
    temps_secours = {
        'Shorty 3': {'Decoupe': {'minimum': 2.0, 'normal': 2.0, 'maximum': 2.0}, 'Assemblage': {'minimum': 4.0, 'normal': 4.0, 'maximum': 4.0}, 'Cond': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Integral 3': {'Decoupe': {'minimum': 3.0, 'normal': 3.0, 'maximum': 3.0}, 'Assemblage': {'minimum': 6.0, 'normal': 6.0, 'maximum': 6.0}, 'Cond': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Shorty 5': {'Decoupe': {'minimum': 2.5, 'normal': 2.5, 'maximum': 2.5}, 'Assemblage': {'minimum': 5.0, 'normal': 5.0, 'maximum': 5.0}, 'Cond': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Integral 5': {'Decoupe': {'minimum': 3.5, 'normal': 3.5, 'maximum': 3.5}, 'Assemblage': {'minimum': 7.0, 'normal': 7.0, 'maximum': 7.0}, 'Cond': {'minimum': 1.0, 'normal': 1.0, 'maximum': 1.0}},
        'Integral 7': {'Decoupe': {'minimum': 4.0, 'normal': 4.0, 'maximum': 4.0}, 'Assemblage': {'minimum': 8.0, 'normal': 8.0, 'maximum': 8.0}, 'Cond': {'minimum': 1.5, 'normal': 1.5, 'maximum': 1.5}}
    }
    if engine is None: return temps_secours
    try:
        query = "SELECT produit, atelier, temps_min, temps_normal, temps_max FROM Parametres_TempsAteliers"
        df = pd.read_sql(query, engine)
        if df.empty: return temps_secours
        temps_dict = {}
        for prod, group in df.groupby('produit'):
            temps_dict[prod] = {}
            for _, row in group.iterrows():
                temps_dict[prod][row['atelier']] = {
                    'minimum': float(row['temps_min']),
                    'normal': float(row['temps_normal']),
                    'maximum': float(row['temps_max'])
                }
        return temps_dict
    except Exception: return temps_secours

@st.cache_data
def charger_parametre_global(nom_param, defaut=0.0):
    if engine is None: return float(defaut)
    try:
        query = text("SELECT valeur FROM Parametres_Globaux WHERE parametre = :parametre")
        df = pd.read_sql(query, engine, params={"parametre": nom_param})
        if not df.empty: return float(df['valeur'].iloc[0])
    except Exception: pass
    return float(defaut)

@st.cache_data
def charger_norme_ms(table_name, activite_actuelle):
    if engine is None: return 27971.0
    try:
        query = f"SELECT activite_seuil, ms_satisfaisante FROM {table_name} ORDER BY activite_seuil ASC"
        df = pd.read_sql(query, engine)
        if df.empty: return 25000.0
        seuils = df['activite_seuil'].tolist()
        ms_vals = df['ms_satisfaisante'].tolist()
        if activite_actuelle <= seuils[0]: return ms_vals[0] * (activite_actuelle / seuils[0]) if seuils[0] > 0 else ms_vals[0]
        if activite_actuelle >= seuils[-1]:
            pente = (ms_vals[-1] - ms_vals[-2]) / (seuils[-1] - seuils[-2]) if len(seuils) > 1 else 0.025
            return ms_vals[-1] + pente * (activite_actuelle - seuils[-1])
        for i in range(len(seuils) - 1):
            if seuils[i] <= activite_actuelle <= seuils[i+1]:
                pente = (ms_vals[i+1] - ms_vals[i]) / (seuils[i+1] - seuils[i])
                return ms_vals[i] + pente * (activite_actuelle - seuils[i])
    except Exception: pass
    return 27971.0

@st.cache_data
def optimiser_fournisseur_matiere(matiere, qualite_visee, critere="prix"):
    if engine is None: return None
    try:
        col_qualite = f"qualite_{qualite_visee}"
        query = f"SELECT fournisseur, {col_qualite} as prix, delai_mois FROM Parametres_TarifsFournisseurs WHERE matiere = '{matiere}' AND {col_qualite} IS NOT NULL"
        df_tarifs = pd.read_sql(query, engine)
        if df_tarifs.empty: return None
        if critere == "prix": df_tarifs = df_tarifs.sort_values(by=["prix", "delai_mois"], ascending=[True, True])
        elif critere == "delai": df_tarifs = df_tarifs.sort_values(by=["delai_mois", "prix"], ascending=[True, True])
        return df_tarifs.iloc[0].to_dict()
    except Exception: return None

def charger_donnees_rh_bdd(tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(text("SELECT * FROM Decisions_RH_Mensuel WHERE Tour_ID = :tour_id"), engine, params={"tour_id": int(tour_id)})
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_donnees_mkg_bdd(tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(text("SELECT * FROM Decisions_Marketing WHERE Tour_ID = :tour_id"), engine, params={"tour_id": int(tour_id)})
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_donnees_fin_bdd(tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(text("SELECT * FROM Finances_Mensuelles WHERE Tour_ID = :tour_id"), engine, params={"tour_id": int(tour_id)})
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def lister_scenarios(tour_id):
    if engine is None: return []
    try:
        df = pd.read_sql(text("SELECT Token_Seed, Nom_Scenario FROM simulations_seeds WHERE Tour_ID = :tour_id"), engine, params={"tour_id": int(tour_id)})
        return df.to_dict('records')
    except Exception: return []

def charger_scenario_seed(token_seed, tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(text("SELECT * FROM simulations_seeds WHERE Token_Seed = :token_seed AND Tour_ID = :tour_id"), engine, params={"token_seed": token_seed, "tour_id": int(tour_id)})
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def sauvegarder_scenario_seed(token_seed, tour_id, nom, parametres_dict):
    if engine is None: return
    try:
        clean_params = {k: v for k, v in parametres_dict.items() if isinstance(v, (int, float, str, bool))}
        json_data = json.dumps(clean_params, ensure_ascii=False)
        query = text("""
            INSERT INTO simulations_seeds (Token_Seed, Tour_ID, Nom_Scenario, Parametres_JSON)
            VALUES (:token_seed, :tour_id, :nom, :json_data)
            ON DUPLICATE KEY UPDATE Nom_Scenario=:nom_update, Parametres_JSON=:json_update
        """)
        with engine.begin() as conn:
            conn.execute(query, {
                "token_seed": token_seed, "tour_id": int(tour_id), "nom": nom, "json_data": json_data,
                "nom_update": nom, "json_update": json_data,
            })
    except Exception as e:
        st.error(f"Erreur sauvegarde seed : {e}")

# Fonctions d'aide globale sécurisées
def get_rh_v(donnees_rh, k, def_v): return float(donnees_rh.get(k, def_v))
def get_rh_e(donnees_rh, k, def_v): return int(donnees_rh.get(k, def_v))

# Fonctions d'affichage pour l'État des Lieux Global
def afficher_tableau_dynamique(donnees):
    try:
        if isinstance(donnees, list) and len(donnees) > 0 and isinstance(donnees[0], dict):
            df = pd.DataFrame(donnees)
            for col in df.select_dtypes(include=['object']).columns: df[col] = df[col].astype(str)
            st.dataframe(df, use_container_width=True, hide_index=True)
            return True
        if isinstance(donnees, dict) and all(isinstance(v, (list, int, float, str)) for v in donnees.values()):
            df = pd.DataFrame.from_dict(donnees, orient='index')
            for col in df.select_dtypes(include=['object']).columns: df[col] = df[col].astype(str)
            st.dataframe(df, use_container_width=True)
            return True
    except Exception: pass
    return False

def parcourir_structure_json(donnees, niveau=4):
    if afficher_tableau_dynamique(donnees): return
    if isinstance(donnees, dict):
        for titre, sous_donnees in donnees.items():
            if not str(titre).startswith("Tableau_") and titre != "Général":
                if niveau == 4: st.markdown(f"#### {titre}")
                elif niveau == 5: st.markdown(f"##### {titre}")
                else: st.markdown(f"**{titre}**")
            parcourir_structure_json(sous_donnees, niveau + 1)
    else: st.write(donnees)

def rendre_module_etat_des_lieux(donnees_module):
    if not donnees_module:
        st.info("Aucune donnée disponible pour cette sélection.")
        return
    if donnees_module == "SPECIMEN":
        st.warning("🚫 Ce document n'a pas encore été acheté (SPECIMEN). Données non disponibles.")
        return
    if not isinstance(donnees_module, dict):
        st.write(donnees_module)
        return
    noms_onglets = list(donnees_module.keys())
    if not noms_onglets:
        st.info("Structure JSON vide.")
        return
    tabs = st.tabs(noms_onglets)
    for i, nom_onglet in enumerate(noms_onglets):
        with tabs[i]:
            contenu_onglet = donnees_module[nom_onglet]
            if contenu_onglet == "SPECIMEN": st.warning("🚫 Document non acheté (SPECIMEN).")
            else:
                st.markdown("<br>", unsafe_allow_html=True)
                parcourir_structure_json(contenu_onglet, niveau=4)

# Utilitaires Simulateur
def sim_number(label, key, default_val, step=None):
    if key not in st.session_state: st.session_state[key] = default_val
    return st.number_input(label, key=key, step=step)
def sim_text(label, key, default_val):
    if key not in st.session_state: st.session_state[key] = default_val
    return st.text_input(label, key=key)
def sim_select(label, options, key, default_val):
    if key not in st.session_state: st.session_state[key] = default_val
    return st.selectbox(label, options, key=key)
def sim_radio(label, options, key, default_val, horizontal=False):
    if key not in st.session_state: st.session_state[key] = default_val
    return st.radio(label, options, key=key, horizontal=horizontal)
def sim_slider(label, min_v, max_v, key, default_val, step=None):
    if key not in st.session_state: st.session_state[key] = default_val
    return st.slider(label, min_value=min_v, max_value=max_v, key=key, step=step)
def sim_checkbox(label, key, default_val):
    if key not in st.session_state: st.session_state[key] = default_val
    return st.checkbox(label, key=key)

nomenclature_dynamique = charger_nomenclature_bdd()
temps_dynamique = charger_temps_ateliers_bdd()
capacite_ref_machine = charger_parametre_global('capacite_min_machine', 9600.0)
ratio_optimum_rh = charger_parametre_global('ratio_structure_optimum', 0.45)

def predire_notes_publicite(budget_marq, axe_princ, axe_acc, budgets_prods):
    eval_produits = {}
    if budget_marq >= 70000: noto = 4.0
    elif budget_marq >= 60000: noto = 3.5
    elif budget_marq >= 55000: noto = 3.0
    elif budget_marq >= 45000: noto = 2.5
    else: noto = 2.0

    if axe_princ == "Durée de vie" and axe_acc == "Souplesse": image = 3.5
    elif axe_princ == "Durée de vie" and axe_acc == "Enfilage": image = 3.0
    elif axe_princ == "Protection thermique" and axe_acc == "Durée de vie": image = 3.5
    elif axe_princ == "Esthétique" and axe_acc == "Durée de vie": image = 3.5
    else: image = 2.5

    moyenne = (noto + image) / 2.0
    reste = moyenne % 1
    if reste == 0.25 or reste == 0.75: synth = moyenne + 0.25
    else: synth = moyenne

    for prod, budg_plv in budgets_prods.items():
        if budg_plv >= 4300: n_prod = 3.5
        elif budg_plv >= 3600: n_prod = 3.0
        elif budg_plv >= 2500: n_prod = 2.5
        elif budg_plv >= 1000: n_prod = 2.0
        else: n_prod = 1.0
        eval_produits[prod] = {'notoriete': noto, 'image': image, 'synthese': synth, 'eval_prod': n_prod}
    return eval_produits

def afficher_etoiles(note):
    nb_pleines = int(note)
    reste = note - nb_pleines
    etoiles = "⭐" * nb_pleines
    if 0.3 <= reste < 0.8:
        etoiles += "⯨"
        nb_vides = 5 - nb_pleines - 1
    elif reste >= 0.8:
        etoiles = "⭐" * (nb_pleines + 1)
        nb_vides = 5 - (nb_pleines + 1)
    else:
        nb_vides = 5 - nb_pleines
    etoiles += "☆" * max(0, nb_vides)
    return etoiles

def calculer_score_rh_prod_dynamique(donnees_rh_defaut, ratio_opt):
    e_p = st.session_state.get('sp_e', int(donnees_rh_defaut.get('Eff_Employes_Prod', 15)))
    s_ep = st.session_state.get('sp_es', float(donnees_rh_defaut.get('Sal_Employes_Prod', 2000.0)))
    c_p = st.session_state.get('sp_c', int(donnees_rh_defaut.get('Eff_Cadres_Prod', 3)))
    s_cp = st.session_state.get('sp_cs', float(donnees_rh_defaut.get('Sal_Cadres_Prod', 3333.33)))
    d_p = st.session_state.get('sp_d', int(donnees_rh_defaut.get('Eff_Directeurs_Prod', 1)))
    s_dp = st.session_state.get('sp_ds', float(donnees_rh_defaut.get('Sal_Directeurs_Prod', 4500.0)))

    ms_ep = e_p * s_ep
    ms_cp = c_p * s_cp
    ms_dp = d_p * s_dp
    tot_p = ms_ep + ms_cp + ms_dp

    q_s3 = st.session_state.get('ord_s3', 50)
    q_i3 = st.session_state.get('ord_i3', 500)
    q_s5 = st.session_state.get('ord_s5', 100)
    q_i5 = st.session_state.get('ord_i5', 1500)
    q_i7 = st.session_state.get('ord_i7', 2500)
    couts_prod_sim = (q_s3 + q_i3 + q_s5 + q_i5 + q_i7) * 150.0

    ms_satisfaisante_prod = charger_norme_ms("Normes_MS_Prod", couts_prod_sim)
    ratio_actuel_p = (ms_cp + ms_dp) / ms_ep if ms_ep > 0 else 2.0
    ind_p_actuel = ratio_actuel_p / ratio_opt
    
    denom_prod = max(ms_satisfaisante_prod, 1.0)
    s_ms_p = max(0.0, min(100.0, 100.0 - (abs(tot_p - ms_satisfaisante_prod) / denom_prod) * 100))
    s_disp_p = max(0.0, 100.0 - abs(1.0 - ind_p_actuel) * 200.0)
    return (s_ms_p + s_disp_p) / 2.0


def charger_contenu_erp_periode(periode, module):
    """Charge la dernière version du module pour UNE période précise.

    Le filtre de période est volontairement obligatoire : le moteur financier
    ne doit jamais lire « le dernier document inséré » d'une autre période.
    """
    if engine is None:
        return {}
    try:
        query = text("""
            SELECT contenu
            FROM erp_donnees
            WHERE periode = :periode
              AND type_donnee = 'etat_actuel'
              AND module = :module
            ORDER BY id DESC
            LIMIT 1
        """)
        with engine.connect() as conn:
            row = conn.execute(query, {"periode": periode, "module": module}).mappings().first()
        if not row:
            return {}
        contenu = row.get("contenu", {})
        if isinstance(contenu, str):
            return json.loads(contenu)
        return contenu if isinstance(contenu, dict) else {}
    except Exception:
        return {}


def charger_etat_financier_m1(tour_id_precedent, tour_id_cible):
    """Retourne l'état financier réel de M-1 et les éventuelles saisies manuelles.

    Priorités :
      1. Banque / Expert-comptable extrait depuis erp_donnees pour la période M-1.
      2. Finances_Mensuelles si des données réelles y ont été saisies.
      3. Aucun fallback silencieux vers une constante comptable.
    """
    # Tour 1 : le vrai M-1 est l'ouverture d'Année 1. Le dump ne contient pas
    # une période "Année 0 - Décembre" complète, mais l'état de janvier contient
    # directement le solde bancaire d'ouverture et le détail nécessaire au BFR d'ouverture.
    if tour_id_cible == 1:
        periode_ouverture = period_for_tour(1)
        expert = charger_contenu_erp_periode(periode_ouverture, "expert_comptable")
        banque = charger_contenu_erp_periode(periode_ouverture, "banque_assurance")
        period_data = {"etat_actuel": {"expert_comptable": expert, "banque_assurance": banque}}
        state = extract_financial_state(periode_ouverture, period_data, target_tour_id=1)
        bfr_ouverture = opening_bfr_from_current_state(state)
        if bfr_ouverture is not None:
            from dataclasses import replace as dc_replace
            state = dc_replace(state, bfr_reel=bfr_ouverture)
        manuel = charger_donnees_fin_bdd(1)
        return state, manuel, "Ouverture Année 1"

    periode_m1 = period_for_tour(tour_id_precedent)
    expert = charger_contenu_erp_periode(periode_m1, "expert_comptable")
    banque = charger_contenu_erp_periode(periode_m1, "banque_assurance")
    period_data = {
        "etat_actuel": {
            "expert_comptable": expert,
            "banque_assurance": banque,
        }
    }
    state = extract_financial_state(periode_m1, period_data, target_tour_id=tour_id_cible)

    # Pour mars/avril et toute période où Subakoua ne fournit qu'une variation
    # du BFR via l'IFRS, on reconstitue le niveau de BFR de M-1 à partir du
    # BFR de la période précédente + ΔBFR comptable.
    if tour_id_precedent > 1 and state.variation_bfr_reelle is not None:
        periode_m2 = period_for_tour(tour_id_precedent - 1)
        expert_m2 = charger_contenu_erp_periode(periode_m2, "expert_comptable")
        banque_m2 = charger_contenu_erp_periode(periode_m2, "banque_assurance")
        period_data_m2 = {
            "etat_actuel": {
                "expert_comptable": expert_m2,
                "banque_assurance": banque_m2,
            }
        }
        state_m2 = extract_financial_state(periode_m2, period_data_m2)
        state = complete_bfr_from_previous_state(state, state_m2)

    manuel = charger_donnees_fin_bdd(tour_id_precedent)
    return state, manuel, periode_m1

# ==========================================
# 4. NAVIGATION & PILOTAGE TEMPOREL
# ==========================================
st.sidebar.title("🏢 ERP Subakoua")

mois_mapping = {
    "Janvier N+1": "Année 1 - Janvier", "Février N+1": "Année 1 - Février", "Mars N+1": "Année 1 - Mars", 
    "Avril N+1": "Année 1 - Avril", "Mai N+1": "Année 1 - Mai", "Juin N+1": "Année 1 - Juin", 
    "Juillet N+1": "Année 1 - Juillet", "Août N+1": "Année 1 - Août", "Septembre N+1": "Année 1 - Septembre", 
    "Octobre N+1": "Année 1 - Octobre", "Novembre N+1": "Année 1 - Novembre", "Décembre N+1": "Année 1 - Décembre", 
    "Janvier N+2": "Année 2 - Janvier", "Février N+2": "Année 2 - Février", "Mars N+2": "Année 2 - Mars"
}

mois_selectionne = st.sidebar.selectbox("Sélectionnez la période (Mois) :", list(mois_mapping.keys()))
periode_db = mois_mapping[mois_selectionne]

tour_mapping_id = {
    "Janvier N+1": 1, "Février N+1": 2, "Mars N+1": 3, "Avril N+1": 4, "Mai N+1": 5, 
    "Juin N+1": 6, "Juillet N+1": 7, "Août N+1": 8, "Septembre N+1": 9, "Octobre N+1": 10, 
    "Novembre N+1": 11, "Décembre N+1": 12, "Janvier N+2": 13, "Février N+2": 14, "Mars N+2": 15
}
tour_id_actif = tour_mapping_id[mois_selectionne]

st.sidebar.divider()
module_principal = st.sidebar.radio("Choisissez le module :", [
    "📊 État des lieux global", 
    "📥 Saisie des Données Réelles", 
    "🧠 Simulateur & Décision Stratégique"
])

# ==========================================
# MODULE 1 : ÉTAT DES LIEUX GLOBAL
# ==========================================
if module_principal == "📊 État des lieux global":
    st.title("📊 Tableaux de Bord & État des Lieux de l'ERP")
    st.info(f"Analyse des données extraites pour la période : **{periode_db}**")
    st.divider()

    if engine is None:
        st.warning("Système hors ligne : Impossible d'afficher les données Cloud.")
        if db_error:
            st.error(f"Diagnostic connexion MySQL : {db_error}")
        st.info(
            "Vérifie dans Streamlit Cloud → Settings → Secrets que [mysql] contient "
            "les valeurs exactes affichées dans Aiven (host, port, user, password, database)."
        )
    else:
        try:
            query_erp = f"SELECT module, contenu FROM erp_donnees WHERE periode = '{periode_db}' AND type_donnee = 'etat_actuel'"
            df_erp = pd.read_sql(query_erp, engine)
            modules_disponibles_dict = dict(zip(df_erp['module'], df_erp['contenu'])) if not df_erp.empty else {}
        except Exception:
            modules_disponibles_dict = {}

        if not modules_disponibles_dict:
            st.warning(f"Aucune donnée valide n'a été trouvée en base pour la période **{periode_db}**.")
        else:
            liste_modules_ui = sorted(list(modules_disponibles_dict.keys()))
            module_choisi = st.selectbox("Sélectionnez le département / module à consulter :", liste_modules_ui, format_func=lambda x: x.replace('_', ' ').capitalize())
            
            st.divider()
            donnee_json_brute = modules_disponibles_dict.get(module_choisi, {})
            
            if isinstance(donnee_json_brute, str):
                try: donnees_module = json.loads(donnee_json_brute)
                except Exception: 
                    st.error("❌ Le fichier JSON extrait pour ce mois est corrompu et illisible.")
                    donnees_module = {}
            else: donnees_module = donnee_json_brute

            rendre_module_etat_des_lieux(donnees_module)

# ==========================================
# MODULE 2 : SAISIE DES DONNÉES RÉELLES
# ==========================================
elif module_principal == "📥 Saisie des Données Réelles":
    st.title("📥 Saisie & Alimentation des Données Réelles")
    mois_saisie = st.selectbox("Sélectionnez le mois à renseigner :", list(mois_mapping.keys()))
    tour_saisie = tour_mapping_id[mois_saisie]

    df_h, df_m, d_rh, d_mkg, d_ventes, dfin = pd.DataFrame(), pd.DataFrame(), {}, {}, {}, {}
    if engine is not None:
        try:
            df_h = pd.read_sql(f"SELECT * FROM Historique_Equipe WHERE Tour_ID = {tour_saisie}", engine)
            df_m = pd.read_sql(f"SELECT * FROM Parc_Machines_Mensuel WHERE Tour_ID = {tour_saisie}", engine)
            d_rh = charger_donnees_rh_bdd(tour_saisie)
            d_mkg = charger_donnees_mkg_bdd(tour_saisie)
            dfin = pd.read_sql(f"SELECT * FROM Finances_Mensuelles WHERE Tour_ID = {tour_saisie}", engine)
        except Exception: pass

    def get_h(col, def_v): return float(df_h[col].iloc[0]) if not df_h.empty and col in df_h.columns else def_v
    def get_m(col, def_v=0): return int(df_m[col].iloc[0]) if not df_m.empty and col in df_m.columns else def_v

    tab_r1, tab_r2, tab_r3, tab_r4, tab_r5, tab_r6 = st.tabs(["📦 Stocks", "🏭 Machines", "👥 RH", "🎯 Marketing", "📈 Ventes Réelles", "💶 Finance"])
    
    with tab_r1:
        with st.form("f_stocks"):
            st.subheader("Saisie des Stocks & Trésorerie")
            s_neo3 = st.number_input("Néoprène 3", value=get_h('Stock_Neo3', 2.36))
            s_neo5 = st.number_input("Néoprène 5", value=get_h('Stock_Neo5', 1.33))
            s_neo7 = st.number_input("Néoprène 7", value=get_h('Stock_Neo7', 0.80))
            s_renf = st.number_input("Renforts", value=get_h('Stock_Renforts', 1458.0))
            s_man = st.number_input("Manchons", value=get_h('Stock_Manchons', 113.0))
            s_ferm = st.number_input("Fermetures", value=get_h('Stock_Fermetures', 539.0))
            treso = st.number_input("Trésorerie", value=get_h('Tresorerie_Initiale', 100000.0))
            if st.form_submit_button("Enregistrer les Stocks"):
                if engine:
                    with engine.begin() as conn:
                        conn.execute(text(f"INSERT INTO Historique_Equipe (Tour_ID, Stock_Neo3, Stock_Neo5, Stock_Neo7, Stock_Renforts, Stock_Manchons, Stock_Fermetures, Tresorerie_Initiale) VALUES ({tour_saisie}, {s_neo3}, {s_neo5}, {s_neo7}, {s_renf}, {s_man}, {s_ferm}, {treso}) ON DUPLICATE KEY UPDATE Stock_Neo3={s_neo3}, Stock_Neo5={s_neo5}, Stock_Neo7={s_neo7}, Stock_Renforts={s_renf}, Stock_Manchons={s_man}, Stock_Fermetures={s_ferm}, Tresorerie_Initiale={treso}"))
                    st.success("Stocks enregistrés !")

    with tab_r2:
        with st.form("f_mach"):
            st.subheader("Saisie du Parc Machines & Immobilisations")
            m_dec = st.number_input("Total Découpe", value=get_m('Nb_Machines_Decoupe', 10), step=1)
            m_ass = st.number_input("Total Assemblage", value=get_m('Nb_Machines_Assemblage', 16), step=1)
            m_cond = st.number_input("Total Conditionnement", value=get_m('Nb_Machines_Cond', 4), step=1)
            acquis_check = st.checkbox("Des machines ont-elles été acquises ce mois-ci ?", value=bool(get_m('Acquisition_Faite', 0)))
            acq_dec = st.number_input("Nombre de machines Découpe acquises", value=get_m('Acq_Decoupe', 0), step=1)
            acq_ass = st.number_input("Nombre de machines Assemblage acquises", value=get_m('Acq_Assemblage', 0), step=1)
            acq_cond = st.number_input("Nombre de machines Conditionnement acquises", value=get_m('Acq_Cond', 0), step=1)
            if st.form_submit_button("Enregistrer les Machines et Acquisitions"):
                acquis_val = 1 if acquis_check else 0
                if engine:
                    with engine.begin() as conn:
                        conn.execute(text(f"INSERT INTO Parc_Machines_Mensuel (Tour_ID, Mois, Nb_Machines_Decoupe, Nb_Machines_Assemblage, Nb_Machines_Cond, Acquisition_Faite, Acq_Decoupe, Acq_Assemblage, Acq_Cond) VALUES ({tour_saisie}, '{mois_saisie}', {m_dec}, {m_ass}, {m_cond}, {acquis_val}, {acq_dec}, {acq_ass}, {acq_cond}) ON DUPLICATE KEY UPDATE Nb_Machines_Decoupe={m_dec}, Nb_Machines_Assemblage={m_ass}, Nb_Machines_Cond={m_cond}, Acquisition_Faite={acquis_val}, Acq_Decoupe={acq_dec}, Acq_Assemblage={acq_ass}, Acq_Cond={acq_cond};"))
                    st.success("✅ Parc machines et acquisitions enregistrés !")

    with tab_r3:
        with st.form("form_saisie_rh_reel"):
            st.subheader("Saisie des Effectifs et Salaires par Catégorie")
            c1, c2, c3 = st.columns(3)
            with c1:
                eff_ep = st.number_input("Nb Employés Prod", value=get_rh_e(d_rh, 'Eff_Employes_Prod', 15), step=1)
                sal_ep = st.number_input("Sal. Employé Prod (€)", value=get_rh_v(d_rh, 'Sal_Employes_Prod', 2000.0), step=100.0)
                eff_cp = st.number_input("Nb Cadres Prod", value=get_rh_e(d_rh, 'Eff_Cadres_Prod', 3), step=1)
                sal_cp = st.number_input("Sal. Cadre Prod (€)", value=get_rh_v(d_rh, 'Sal_Cadres_Prod', 3333.33), step=100.0)
                eff_dp = st.number_input("Nb Directeurs Prod", value=get_rh_e(d_rh, 'Eff_Directeurs_Prod', 1), step=1)
                sal_dp = st.number_input("Sal. Directeur Prod (€)", value=get_rh_v(d_rh, 'Sal_Directeurs_Prod', 4500.0), step=100.0)
            with c2:
                eff_ea = st.number_input("Nb Employés Appro", value=get_rh_e(d_rh, 'Eff_Employes_Appro', 7), step=1)
                sal_ea = st.number_input("Sal. Employé Appro (€)", value=get_rh_v(d_rh, 'Sal_Employes_Appro', 2000.0), step=100.0)
                eff_ca = st.number_input("Nb Cadres Appro", value=get_rh_e(d_rh, 'Eff_Cadres_Appro', 2), step=1)
                sal_ca = st.number_input("Sal. Cadre Appro (€)", value=get_rh_v(d_rh, 'Sal_Cadres_Appro', 2250.0), step=100.0)
                eff_da = st.number_input("Nb Directeurs Appro", value=get_rh_e(d_rh, 'Eff_Directeurs_Appro', 1), step=1)
                sal_da = st.number_input("Sal. Directeur Appro (€)", value=get_rh_v(d_rh, 'Sal_Directeurs_Appro', 2000.0), step=100.0)
            with c3:
                eff_ef = st.number_input("Nb Employés Admin", value=get_rh_e(d_rh, 'Eff_Employes_Admin', 7), step=1)
                sal_ef = st.number_input("Sal. Employé Admin (€)", value=get_rh_v(d_rh, 'Sal_Employes_Admin', 2000.0), step=100.0)
                eff_cf = st.number_input("Nb Cadres Admin", value=get_rh_e(d_rh, 'Eff_Cadres_Admin', 2), step=1)
                sal_cf = st.number_input("Sal. Cadre Admin (€)", value=get_rh_v(d_rh, 'Sal_Cadres_Admin', 2200.0), step=100.0)
                eff_df = st.number_input("Nb Directeurs Admin", value=get_rh_e(d_rh, 'Eff_Directeurs_Admin', 1), step=1)
                sal_df = st.number_input("Sal. Directeur Admin (€)", value=get_rh_v(d_rh, 'Sal_Directeurs_Admin', 2000.0), step=100.0)
            if st.form_submit_button("💾 Enregistrer la Pyramide RH"):
                if engine:
                    with engine.begin() as conn:
                        conn.execute(text(f"INSERT INTO Decisions_RH_Mensuel (Tour_ID, Mois, Eff_Employes_Prod, Sal_Employes_Prod, Eff_Cadres_Prod, Sal_Cadres_Prod, Eff_Directeurs_Prod, Sal_Directeurs_Prod, Eff_Employes_Appro, Sal_Employes_Appro, Eff_Cadres_Appro, Sal_Cadres_Appro, Eff_Directeurs_Appro, Sal_Directeurs_Appro, Eff_Employes_Admin, Sal_Employes_Admin, Eff_Cadres_Admin, Sal_Cadres_Admin, Eff_Directeurs_Admin, Sal_Directeurs_Admin) VALUES ({tour_saisie}, '{mois_saisie}', {eff_ep}, {sal_ep}, {eff_cp}, {sal_cp}, {eff_dp}, {sal_dp}, {eff_ea}, {sal_ea}, {eff_ca}, {sal_ca}, {eff_da}, {sal_da}, {eff_ef}, {sal_ef}, {eff_cf}, {sal_cf}, {eff_df}, {sal_df}) ON DUPLICATE KEY UPDATE Eff_Employes_Prod={eff_ep}, Sal_Employes_Prod={sal_ep}, Eff_Cadres_Prod={eff_cp}, Sal_Cadres_Prod={sal_cp}, Eff_Directeurs_Prod={eff_dp}, Sal_Directeurs_Prod={sal_dp}, Eff_Employes_Appro={eff_ea}, Sal_Employes_Appro={sal_ea}, Eff_Cadres_Appro={eff_ca}, Sal_Cadres_Appro={sal_ca}, Eff_Directeurs_Appro={eff_da}, Sal_Directeurs_Appro={sal_da}, Eff_Employes_Admin={eff_ef}, Sal_Employes_Admin={sal_ef}, Eff_Cadres_Admin={eff_cf}, Sal_Cadres_Admin={sal_cf}, Eff_Directeurs_Admin={eff_df}, Sal_Directeurs_Admin={sal_df};"))
                    st.success("Pyramide RH enregistrée !")

# ==========================================
# MODULE 3 : SIMULATEUR & DÉCISION STRATÉGIQUE
# ==========================================
elif module_principal == "🧠 Simulateur & Décision Stratégique":
    st.title("🧠 Simulateur Stratégique & Interconnectivité (Seed Engine)")
    
    tour_id_precedent = max(1, tour_id_actif - 1)
    st.info(f"Simulation interactive pour le mois : **{mois_selectionne}** (Basée sur l'entreprise au Tour ID {tour_id_precedent})")

    if engine is not None:
        try:
            df_hist = pd.read_sql(f"SELECT * FROM Historique_Equipe WHERE Tour_ID = {tour_id_precedent}", engine)
            df_machines = pd.read_sql(f"SELECT * FROM Parc_Machines_Mensuel WHERE Tour_ID = {tour_id_precedent}", engine)
            donnees_rh = charger_donnees_rh_bdd(tour_id_precedent)
            donnees_mkg = charger_donnees_mkg_bdd(tour_id_precedent)
        except Exception:
            df_hist, df_machines, donnees_rh, donnees_mkg = pd.DataFrame(), pd.DataFrame(), {}, {}
    else:
        df_hist, df_machines, donnees_rh, donnees_mkg = pd.DataFrame(), pd.DataFrame(), {}, {}

    def get_h(col, def_v=0.0): return float(df_hist[col].iloc[0]) if not df_hist.empty and col in df_hist.columns else def_v
    def get_m(col, def_v=0): return int(df_machines[col].iloc[0]) if not df_machines.empty and col in df_machines.columns else def_v

    score_rh_prod = calculer_score_rh_prod_dynamique(donnees_rh, ratio_optimum_rh)

    nb_m_dec_actuel = get_m('Nb_Machines_Decoupe', 10)
    nb_m_ass_actuel = get_m('Nb_Machines_Assemblage', 16)
    nb_m_cond_actuel = get_m('Nb_Machines_Cond', 4)

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        achat_dec = sim_number(f"Découpe (Actuel: {nb_m_dec_actuel})", "sim_ach_dec", 0, step=1)
        prix_unit_dec = sim_number("Prix HT Découpe (€/u)", "sim_p_dec", 500000.0, step=1000.0)
    with col_m2:
        achat_ass = sim_number(f"Assemblage (Actuel: {nb_m_ass_actuel})", "sim_ach_ass", 0, step=1)
        prix_unit_ass = sim_number("Prix HT Assemblage (€/u)", "sim_p_ass", 600000.0, step=1000.0)
    with col_m3:
        achat_cond = sim_number(f"Conditionnement (Actuel: {nb_m_cond_actuel})", "sim_ach_cond", 0, step=1)
        prix_unit_cond = sim_number("Prix HT Conditionnement (€/u)", "sim_p_cond", 160000.0, step=1000.0)

    cout_invest_machines = (achat_dec * prix_unit_dec) + (achat_ass * prix_unit_ass) + (achat_cond * prix_unit_cond)
    nb_m_dec_sim = nb_m_dec_actuel + achat_dec
    nb_m_ass_sim = nb_m_ass_actuel + achat_ass
    nb_m_cond_sim = nb_m_cond_actuel + achat_cond

    mode_financement_machines = sim_radio(
        "Mode de règlement des nouvelles machines :", 
        ["Comptant (Impact immédiat sur la trésorerie)", "Crédit Fournisseur / Dette d'investissement (Acompte 30%)"],
        "sim_mode_reglement_mach",
        "Comptant (Impact immédiat sur la trésorerie)",
        horizontal=True
    )

    tab_sim_marche, tab_sim_prod, tab_sim_appro, tab_sim_rh, tab_sim_fin = st.tabs([
        "🎯 1. Marketing & Ventes", "🏭 2. Production & Ateliers", "📦 3. MRP2 & Achats", "👥 4. Pilotage & Scores RH", "💶 5. Budget & Situation d'Entreprise"
    ])

    with tab_sim_marche:
        st.subheader("1. Politique de Prix & Marketing")
        c_m1, c_m2 = st.columns(2)
        with c_m1:
            prix_s3 = sim_number("Prix Shorty 3 (€)", "sim_pm_s3", float(donnees_mkg.get('Prix_S3', 115.0)))
            prix_i3 = sim_number("Prix Integral 3 (€)", "sim_pm_i3", float(donnees_mkg.get('Prix_I3', 190.0)))
            prix_s5 = sim_number("Prix Shorty 5 (€)", "sim_pm_s5", float(donnees_mkg.get('Prix_S5', 220.0)))
        with c_m2:
            prix_i5 = sim_number("Prix Integral 5 (€)", "sim_pm_i5", float(donnees_mkg.get('Prix_I5', 280.0)))
            prix_i7 = sim_number("Prix Integral 7 (€)", "sim_pm_i7", float(donnees_mkg.get('Prix_I7', 375.0)))

        budget_pub_marque = sim_number("Budget Pub Marque HT (€)", "sim_pm_pub_marque", float(donnees_mkg.get('Budget_Marque', 60500.0)), step=500.0)
        pub_s3 = sim_number("Shorty 3 PLV (€)", "sim_pub_s3", float(donnees_mkg.get('Pub_S3', 3100.0)), step=100.0)
        pub_i3 = sim_number("Integral 3 PLV (€)", "sim_pub_i3", float(donnees_mkg.get('Pub_I3', 4100.0)), step=100.0)
        pub_s5 = sim_number("Shorty 5 PLV (€)", "sim_pub_s5", float(donnees_mkg.get('Pub_S5', 3200.0)), step=100.0)
        pub_i5 = sim_number("Integral 5 PLV (€)", "sim_pub_i5", float(donnees_mkg.get('Pub_I5', 4200.0)), step=100.0)
        pub_i7 = sim_number("Integral 7 PLV (€)", "sim_pub_i7", float(donnees_mkg.get('Pub_I7', 3500.0)), step=100.0)

        budgets_p_dict = {'Shorty 3': pub_s3, 'Integral 3': pub_i3, 'Shorty 5': pub_s5, 'Integral 5': pub_i5, 'Integral 7': pub_i7}

        col_vs1, col_vs2, col_vs3, col_vs4, col_vs5 = st.columns(5)
        sim_v_s3 = col_vs1.number_input("Shorty 3 (u)", key="sim_s3", value=st.session_state.setdefault("sim_s3", 50))
        sim_v_i3 = col_vs2.number_input("Integral 3 (u)", key="sim_i3", value=st.session_state.setdefault("sim_i3", 500))
        sim_v_s5 = col_vs3.number_input("Shorty 5 (u)", key="sim_s5", value=st.session_state.setdefault("sim_s5", 100))
        sim_v_i5 = col_vs4.number_input("Integral 5 (u)", key="sim_i5", value=st.session_state.setdefault("sim_i5", 1500))
        sim_v_i7 = col_vs5.number_input("Integral 7 (u)", key="sim_i7", value=st.session_state.setdefault("sim_i7", 2500))
        
        ca_prev_sim = (sim_v_s3 * prix_s3) + (sim_v_i3 * prix_i3) + (sim_v_s5 * prix_s5) + (sim_v_i5 * prix_i5) + (sim_v_i7 * prix_i7)

    with tab_sim_prod:
        st.subheader("2. Production & Ateliers")
        qualite_strategique = sim_select("Qualité Matière (Achats)", [35, 50, 70], "qual_mat_prod", 50)
        niveau_controle = sim_select("Niveau de Contrôle", ["Allégé", "Standard", "Renforcé"], "ctrl_usine", "Standard")
        
        score_mixte_matiere = qualite_strategique + (score_rh_prod * 0.5) 
        if score_mixte_matiere > 90: regime_matiere = 'minimum'
        elif score_mixte_matiere < 60: regime_matiere = 'maximum'
        else: regime_matiere = 'normal'

        ord_s3 = sim_number("Prod S3", "ord_s3", 50)
        ord_i3 = sim_number("Prod I3", "ord_i3", 500)
        ord_s5 = sim_number("Prod S5", "ord_s5", 100)
        ord_i5 = sim_number("Prod I5", "ord_i5", 1500)
        ord_i7 = sim_number("Prod I7", "ord_i7", 2500)
        ordres_prod_dict = {'Shorty 3': ord_s3, 'Integral 3': ord_i3, 'Shorty 5': ord_s5, 'Integral 5': ord_i5, 'Integral 7': ord_i7}

    with tab_sim_appro:
        st.subheader("3. MRP2 & Achats")
        pct_vente_couvert = sim_slider("Part de la prod dédiée à la couverture (%)", 0, 100, "slider_pct_vente", 100, step=5)
        coeff_stock_secu = sim_slider("Coefficient stock de sécurité", 0.0, 2.0, "slider_coeff_secu", 1.0, step=0.1)

        stocks_initiaux = {
            'Néoprène 3': get_h('Stock_Neo3', 2.36), 'Néoprène 5': get_h('Stock_Neo5', 1.33),
            'Néoprène 7': get_h('Stock_Neo7', 0.80), 'Renforts': get_h('Stock_Renforts', 1458.00),
            'Manchons': get_h('Stock_Manchons', 113.00), 'Fermetures': get_h('Stock_Fermetures', 539.00),
        }

        besoins_bruts = {mat: 0.0 for mat in stocks_initiaux.keys()}
        for prod, qte_p in ordres_prod_dict.items():
            nom_p = nomenclature_dynamique.get(prod, {})
            qte_effective = qte_p * (pct_vente_couvert / 100.0)
            for mat, coeffs in nom_p.items():
                match = next((m for m in besoins_bruts.keys() if m.lower() in mat.lower() or mat.lower() in m.lower()), None)
                if match:
                    besoins_bruts[match] += qte_effective * coeffs.get(regime_matiere, 0.0)

        critere_arbitrage = sim_radio("Critère :", ["prix", "delai"], "radio_critere_mrp", "prix", horizontal=True)
        cout_achats_total_sim = 0.0
        achats_fournisseurs_par_echeance = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
        achats_fournisseurs_detail = []
        for mat, b_brut in besoins_bruts.items():
            s_ini = stocks_initiaux[mat]
            secu = b_brut * (coeff_stock_secu / 10.0)
            b_net = max(0.0, (b_brut + secu) - s_ini)
            if b_net > 0:
                opt = optimiser_fournisseur_matiere(mat, qualite_visee=qualite_strategique, critere=critere_arbitrage)
                if opt:
                    cout_ht = b_net * float(opt['prix'])
                    cout_achats_total_sim += cout_ht
                    try:
                        delai = int(float(opt.get('delai_mois', 1)))
                    except (TypeError, ValueError):
                        delai = 1
                    delai = max(0, min(3, delai))
                    achats_fournisseurs_par_echeance[delai] += cout_ht
                    achats_fournisseurs_detail.append({
                        'matiere': mat,
                        'fournisseur': opt.get('fournisseur', 'N/D'),
                        'delai_mois': delai,
                        'montant_ht': cout_ht,
                    })

    with tab_sim_rh:
        st.subheader("4. Pilotage RH Interconnecté")
        sim_ep_eff = sim_number("Nb Employés Prod", 'sp_e', get_rh_e(donnees_rh, 'Eff_Employes_Prod', 15))
        sim_ep_sal = sim_number("Sal. Employé Prod (€)", 'sp_es', get_rh_v(donnees_rh, 'Sal_Employes_Prod', 2000.0), step=100.0)
        sim_cp_eff = sim_number("Nb Cadres Prod", 'sp_c', get_rh_e(donnees_rh, 'Eff_Cadres_Prod', 3))
        sim_cp_sal = sim_number("Sal. Cadre Prod (€)", 'sp_cs', get_rh_v(donnees_rh, 'Sal_Cadres_Prod', 3333.33), step=100.0)
        sim_dp_eff = sim_number("Nb Directeurs Prod", 'sp_d', get_rh_e(donnees_rh, 'Eff_Directeurs_Prod', 1))
        sim_dp_sal = sim_number("Sal. Directeur Prod (€)", 'sp_ds', get_rh_v(donnees_rh, 'Sal_Directeurs_Prod', 4500.0), step=100.0)
        tot_p = (sim_ep_eff * sim_ep_sal) + (sim_cp_eff * sim_cp_sal) + (sim_dp_eff * sim_dp_sal)

        sim_ea_eff = sim_number("Nb Employés Appro", 'sa_e', get_rh_e(donnees_rh, 'Eff_Employes_Appro', 7))
        sim_ea_sal = sim_number("Sal. Employé Appro (€)", 'sa_es', get_rh_v(donnees_rh, 'Sal_Employes_Appro', 2000.0), step=100.0)
        sim_ca_eff = sim_number("Nb Cadres Appro", 'sa_c', get_rh_e(donnees_rh, 'Eff_Cadres_Appro', 2))
        sim_ca_sal = sim_number("Sal. Cadre Appro (€)", 'sa_cs', get_rh_v(donnees_rh, 'Sal_Cadres_Appro', 2250.0), step=100.0)
        sim_da_eff = sim_number("Nb Directeurs Appro", 'sa_d', get_rh_e(donnees_rh, 'Eff_Directeurs_Appro', 1))
        sim_da_sal = sim_number("Sal. Directeur Appro (€)", 'sa_ds', get_rh_v(donnees_rh, 'Sal_Directeurs_Appro', 2000.0), step=100.0)
        tot_a = (sim_ea_eff * sim_ea_sal) + (sim_ca_eff * sim_ca_sal) + (sim_da_eff * sim_da_sal)

        sim_ef_eff = sim_number("Nb Employés Admin", 'sf_e', get_rh_e(donnees_rh, 'Eff_Employes_Admin', 7))
        sim_ef_sal = sim_number("Sal. Employé Admin (€)", 'sf_es', get_rh_v(donnees_rh, 'Sal_Employes_Admin', 2000.0), step=100.0)
        sim_cf_eff = sim_number("Nb Cadres Admin", 'sf_c', get_rh_e(donnees_rh, 'Eff_Cadres_Admin', 2))
        sim_cf_sal = sim_number("Sal. Cadre Admin (€)", 'sf_cs', get_rh_v(donnees_rh, 'Sal_Cadres_Admin', 2200.0), step=100.0)
        sim_df_eff = sim_number("Nb Directeurs Admin", 'sf_d', get_rh_e(donnees_rh, 'Eff_Directeurs_Admin', 1))
        sim_df_sal = sim_number("Sal. Directeur Admin (€)", 'sf_ds', get_rh_v(donnees_rh, 'Sal_Directeurs_Admin', 2000.0), step=100.0)
        tot_f = (sim_ef_eff * sim_ef_sal) + (sim_cf_eff * sim_cf_sal) + (sim_df_eff * sim_df_sal)

    with tab_sim_fin:
        st.subheader("5. Décisions Financières & Situation de l'Entreprise")

        # ================================================================
        # PONT FINANCIER M-1 -> MOIS SIMULÉ
        # ================================================================
        # Le mois de référence est toujours le Tour_ID précédent.
        # La trésorerie de départ est LE SOLDE FINAL BANCAIRE RÉEL de M-1.
        etat_fin_m1, finances_manuelles, periode_m1 = charger_etat_financier_m1(
            tour_id_precedent,
            tour_id_actif,
        )

        def manuel_decimal(cle):
            try:
                valeur = finances_manuelles.get(cle)
                return float(valeur) if valeur not in (None, "") else None
            except Exception:
                return None

        if tour_id_actif == 1:
            treso_initiale_source = etat_fin_m1.solde_bancaire_initial
            solde_epargne_source = etat_fin_m1.solde_epargne_ouverture or etat_fin_m1.solde_epargne
            bfr_precedent_m1 = float(etat_fin_m1.bfr_reel) if etat_fin_m1.bfr_reel is not None else None
        else:
            treso_initiale_source = etat_fin_m1.solde_bancaire_final
            solde_epargne_source = etat_fin_m1.solde_epargne
            bfr_precedent_m1 = float(etat_fin_m1.bfr_reel) if etat_fin_m1.bfr_reel is not None else None
        if treso_initiale_source is None:
            treso_initiale_source = manuel_decimal("Disponibilites_Banque")

        resultat_historique_source = etat_fin_m1.resultat_net
        dotations_prev_source = etat_fin_m1.dotations_amortissements
        dette_bancaire_source = etat_fin_m1.dette_bancaire
        capitaux_propres_source = etat_fin_m1.capitaux_propres

        # Une saisie manuelle réelle reste utilisable lorsqu'elle existe ;
        # elle ne remplace pas le solde bancaire source Banque pour le pont de trésorerie.
        resultat_historique = manuel_decimal("Resultat_Net") if manuel_decimal("Resultat_Net") is not None else resultat_historique_source
        dotations_prev = manuel_decimal("Dotations_Amortissements") if manuel_decimal("Dotations_Amortissements") is not None else dotations_prev_source
        dette_bancaire = manuel_decimal("Emprunts_Bancaires") if manuel_decimal("Emprunts_Bancaires") is not None else (float(dette_bancaire_source) if dette_bancaire_source is not None else 0.0)
        capitaux_propres = manuel_decimal("Total_Capitaux_Propres") if manuel_decimal("Total_Capitaux_Propres") is not None else (float(capitaux_propres_source) if capitaux_propres_source is not None else 0.0)

        ca_cumule_historique = float(etat_fin_m1.chiffre_affaires_cumule or 0.0)
        res_avant_impot_cumule_historique = float(etat_fin_m1.resultat_avant_impot_cumule or 0.0)

        aace_historique = float(etat_fin_m1.aace) if etat_fin_m1.aace is not None else None
        deprec_historique = float(etat_fin_m1.depreciations) if etat_fin_m1.depreciations is not None else None

        # Transparence : aucune constante de calage comptable n'est injectée.
        for warning in etat_fin_m1.warnings:
            st.warning(f"⚠️ Source financière : {warning}")
        if tour_id_actif == 1:
            st.info(
                "ℹ️ Tour 1 : le moteur utilise l'ouverture réelle de janvier pour la banque, l'épargne, la dette et le BFR ; "
                "la période de référence affichée est technique car décembre N-1 n'est pas extrait dans le dump."
            )

        # Compte épargne : repris de M-1, avec possibilité de décision dans le simulateur.
        solde_epargne_defaut = solde_epargne_source if solde_epargne_source is not None else 0.0
        solde_epargne_initial = sim_number(
            f"Solde compte épargne initial (M-1 : {periode_m1})",
            "sim_solde_ep",
            float(solde_epargne_defaut),
            step=1000.0,
        )
        placement_ep = sim_number("Placement", "sim_plac_ep", 0.0, step=1000.0)
        retrait_ep = sim_number("Retrait", "sim_retr_ep", 0.0, step=1000.0)

        a_a1 = sim_number("Achat A1", "sim_ach_a1", 0)
        v_a1 = sim_number("Vente A1", "sim_ven_a1", 0)
        a_a2 = sim_number("Achat A2", "sim_ach_a2", 0)
        v_a2 = sim_number("Vente A2", "sim_ven_a2", 0)
        a_a3 = sim_number("Achat A3", "sim_ach_a3", 0)
        v_a3 = sim_number("Vente A3", "sim_ven_a3", 0)
        a_o1 = sim_number("Achat O1", "sim_ach_o1", 0)
        v_o1 = sim_number("Vente O1", "sim_ven_o1", 0)
        a_o2 = sim_number("Achat O2", "sim_ach_o2", 0)
        v_o2 = sim_number("Vente O2", "sim_ven_o2", 0)
        a_o3 = sim_number("Achat O3", "sim_ach_o3", 0)
        v_o3 = sim_number("Vente O3", "sim_ven_o3", 0)

        ass_rc = sim_checkbox("Responsabilité civile", "sim_ass_rc", True)
        ass_db = sim_checkbox("Dommages aux biens", "sim_ass_db", True)
        ass_pe = sim_checkbox("Pertes d'exploitation", "sim_ass_pe", True)
        cout_assurances = (2000 if ass_rc else 0) + (2000 if ass_db else 0) + (2000 if ass_pe else 0)
        if ass_rc and ass_db and ass_pe:
            cout_assurances -= 1000

        div_par_part = sim_number("Dividende versé par part (€)", "sim_div_part", 0.0, step=0.50)
        total_div = div_par_part * float(NOMBRE_PARTS)

        ms_prev_brute = tot_p + tot_a + tot_f
        charges_sociales_prev = ms_prev_brute * float(TAUX_CHARGES_SOCIALES)
        budget_mkg_prev = budget_pub_marque + sum(budgets_p_dict.values())

        dotations_base = float(dotations_prev) if dotations_prev is not None else 0.0

        # ================================================================
        # Investissement : TTC + acompte 30% TTC en crédit fournisseur.
        # La dette fournisseur créée n'est PAS une entrée de trésorerie.
        # ================================================================
        achats_titres = (a_a1 * 224.58) + (a_a2 * 200.64) + (a_a3 * 163.84) + (a_o1 * 109.09) + (a_o2 * 114.89) + (a_o3 * 114.78)
        ventes_titres = (v_a1 * 224.58) + (v_a2 * 200.64) + (v_a3 * 163.84) + (v_o1 * 109.09) + (v_o2 * 114.89) + (v_o3 * 114.78)

        credit_fournisseur = "Crédit Fournisseur" in mode_financement_machines

        # ================================================================
        # BFR / CASH OPÉRATIONNEL : CLIENTS + FOURNISSEURS PAR ÉCHÉANCE
        # ================================================================
        stock_matieres_prev = cout_achats_total_sim * 0.15
        stock_produits_prev = ca_prev_sim * 0.20

        # Le profil clients est déduit des créances M-1. Dans le dump, Clients
        # = CA TTC et 100% des ventes sont actuellement à 1 mois. Le moteur
        # conserve cette règle observée mais permet de la modifier explicitement.
        client_data_m1 = {
            "etat_actuel": {
                "expert_comptable": charger_contenu_erp_periode(periode_m1, "expert_comptable"),
            }
        }
        client_mix_source = infer_client_payment_mix(
            client_data_m1,
            periode_m1,
            etat_fin_m1.chiffre_affaires,
        )

        with st.expander("💳 Échéancier clients", expanded=False):
            st.caption(
                "Profil initialisé automatiquement depuis les créances clients de M-1. "
                "Les pourcentages portent sur le CA TTC et sont utilisés uniquement "
                "pour calculer les créances de clôture et les encaissements attendus."
            )
            ccli1, ccli2, ccli3, ccli4 = st.columns(4)
            mix_pct_0 = ccli1.number_input("Comptant (%)", min_value=0.0, max_value=100.0, value=float(client_mix_source[0] * 100), step=5.0, key="sim_client_mix_0")
            mix_pct_1 = ccli2.number_input("À 1 mois (%)", min_value=0.0, max_value=100.0, value=float(client_mix_source[1] * 100), step=5.0, key="sim_client_mix_1")
            mix_pct_2 = ccli3.number_input("À 2 mois (%)", min_value=0.0, max_value=100.0, value=float(client_mix_source[2] * 100), step=5.0, key="sim_client_mix_2")
            mix_pct_3 = ccli4.number_input("À 3 mois (%)", min_value=0.0, max_value=100.0, value=float(client_mix_source[3] * 100), step=5.0, key="sim_client_mix_3")
        mix_pct_total = mix_pct_0 + mix_pct_1 + mix_pct_2 + mix_pct_3
        if abs(mix_pct_total - 100.0) > 0.01:
            st.error(f"⚠️ Répartition clients invalide : {mix_pct_total:.1f}% au lieu de 100%. Profil M-1 conservé pour le calcul.")
            client_mix = client_mix_source
        else:
            client_mix = {0: mix_pct_0 / 100.0, 1: mix_pct_1 / 100.0, 2: mix_pct_2 / 100.0, 3: mix_pct_3 / 100.0}

        previous_client_buckets = extract_client_receivable_schedule(client_data_m1, periode_m1)
        if not previous_client_buckets:
            # Le bilan reste un fallback, mais sans réintroduire un ratio arbitraire.
            prior_clients_ttc = float(etat_fin_m1.bfr_creances_clients_reel or 0.0)
            previous_client_buckets = {1: prior_clients_ttc, 2: 0.0, 3: 0.0}

        schedule_projection = project_operating_cash_schedule(
            chiffre_affaires_net=ca_prev_sim,
            achats_ht_by_lag=achats_fournisseurs_par_echeance,
            client_payment_mix=client_mix,
            previous_client_buckets=previous_client_buckets,
            previous_supplier_payables=float(etat_fin_m1.bfr_dettes_fournisseurs_reel or 0.0),
        )
        for warning in schedule_projection.warnings:
            st.warning(f"⚠️ Échéancier : {warning}")

        creances_clients_prev = float(schedule_projection.creances_clients_cloture)
        dettes_fournisseurs_prev = float(schedule_projection.dettes_fournisseurs_cloture)

        encours_m1 = float(etat_fin_m1.bfr_stocks_encours_reel or 0.0)
        acomptes_is_m1 = float(etat_fin_m1.bfr_acomptes_is_reel or 0.0)
        tva_deductible_m1 = float(etat_fin_m1.bfr_tva_deductible_hors_immo_reel or 0.0)
        dettes_fiscales_sociales_m1 = float(etat_fin_m1.bfr_dettes_fiscales_sociales_reel or 0.0)
        tva_collectee_m1 = float(etat_fin_m1.tva_collectee_reelle or 0.0)

        # ================================================================
        # TVA / FISCAL & SOCIAL : cycle de règlement observé dans Subakoua
        # ================================================================
        tva_ouverture_a_payer = tva_collectee_m1 - tva_deductible_m1
        tva_deductible_mois = sim_number(
            "TVA déductible d'exploitation du mois HT (€)",
            "sim_tva_deductible_mois",
            tva_deductible_m1,
            step=1000.0,
        )
        tva_immo_mois = cout_invest_machines * 0.20
        tva_info = calculate_vat_projection(
            chiffre_affaires_ht=ca_prev_sim,
            tva_deductible_hors_immo_mois=tva_deductible_mois,
            tva_deductible_immobilisations_mois=tva_immo_mois,
            tva_nette_ouverture_a_payer=tva_ouverture_a_payer,
        )

        dette_fiscale_sociale_hors_tva_ouverture = max(
            0.0, dettes_fiscales_sociales_m1 - tva_collectee_m1
        )
        with st.expander("🧾 TVA, impôts & charges sociales", expanded=False):
            st.caption(
                "TVA collectée = 20 % du CA HT. La TVA nette d'exploitation du mois "
                "est réglée le mois suivant, règle observée dans le dump. La TVA sur "
                "immobilisations reste liée au décaissement TTC de l'investissement et "
                "n'est pas remise dans le BFR d'exploitation."
            )
            ctax1, ctax2, ctax3 = st.columns(3)
            ctax1.metric("TVA collectée", f"{float(tva_info.tva_collectee_mois):,.2f} €")
            ctax2.metric("TVA à payer ce mois", f"{-float(tva_info.tva_a_payer_ce_mois):,.2f} €")
            ctax3.metric("TVA nette du mois", f"{float(tva_info.tva_nette_mois):,.2f} €")
            tva_immo_visible = float(tva_info.tva_deductible_immobilisations_mois)
            st.caption(f"TVA sur immobilisations déjà incluse dans le TTC investi : {tva_immo_visible:,.2f} €")

            paiement_social = sim_number(
                "Paiement charges sociales du mois (€)",
                "sim_paiement_social",
                charges_sociales_prev,
                step=1000.0,
            )
            paiement_is = sim_number(
                "Paiement IS / liquidation du mois (€)",
                "sim_paiement_is",
                0.0,
                step=1000.0,
            )
            acomptes_is_decision = sim_number(
                "Nouvel acompte IS du mois (€)",
                "sim_acompte_is",
                0.0,
                step=1000.0,
            )

            dette_fiscale_sociale_hors_tva_proj_base = calculate_fiscal_social_projection(
                dette_ouverture=dette_fiscale_sociale_hors_tva_ouverture,
                nouvelles_charges=charges_sociales_prev,
                paiements=paiement_social + paiement_is,
            )

        # Les postes d'exploitation circulants restent explicites.
        with st.expander("🧮 BFR prévisionnel détaillé", expanded=False):
            st.caption(
                "Créances et fournisseurs sont issus des échéances ; TVA et passif "
                "fiscal/social sont désormais reconstruits séparément."
            )
            c_bfr1, c_bfr2 = st.columns(2)
            with c_bfr1:
                encours_proj = sim_number("Stocks d'en-cours (€)", "sim_bfr_encours", encours_m1, step=1000.0)
                acomptes_is_proj = sim_number(
                    "Acomptes sur IS en clôture (€)",
                    "sim_bfr_acomptes_is",
                    acomptes_is_m1 + acomptes_is_decision,
                    step=1000.0,
                )
            with c_bfr2:
                st.metric("Stocks matières", f"{stock_matieres_prev:,.2f} €")
                st.metric("Stocks produits finis", f"{stock_produits_prev:,.2f} €")
                st.metric("Créances clients projetées", f"{creances_clients_prev:,.2f} €")
                st.metric("Dettes fournisseurs projetées", f"{dettes_fournisseurs_prev:,.2f} €")
                st.metric("Encaissements clients", f"{float(schedule_projection.encaissements_clients):,.2f} €")
                st.metric("Règlements fournisseurs", f"{float(schedule_projection.reglements_fournisseurs):,.2f} €")

        # Premier passage : permet d'obtenir l'IS courant avant de construire
        # définitivement le passif fiscal/social de clôture.
        dette_fiscale_sociale_proj_pre = float(
            tva_info.tva_collectee_cloture + dette_fiscale_sociale_hors_tva_proj_base.dette_cloture
        )
        bfr_projection_pre = build_bfr_projection(
            stocks_matieres=stock_matieres_prev,
            stocks_encours=encours_proj,
            stocks_produits=stock_produits_prev,
            creances_clients=creances_clients_prev,
            acomptes_is=acomptes_is_proj,
            tva_deductible_hors_immo=tva_info.tva_deductible_hors_immo_cloture,
            dettes_fournisseurs=dettes_fournisseurs_prev,
            dettes_fiscales_sociales=dette_fiscale_sociale_proj_pre,
            bfr_m1=bfr_precedent_m1,
            warnings=tuple(schedule_projection.warnings),
        )

        remboursement_capital = float(etat_fin_m1.remboursement_capital_mois_cible or 0.0)
        interets_emprunt = float(etat_fin_m1.interets_emprunt_mois_cible or 0.0)

        with st.expander("🏦 Financement bancaire", expanded=True):
            cdebt1, cdebt2, cdebt3 = st.columns(3)
            cdebt1.metric("Dette bancaire M-1", f"{float(dette_bancaire):,.2f} €")
            cdebt2.metric("Capital remboursé", f"{-remboursement_capital:,.2f} €")
            cdebt3.metric("Intérêts du mois", f"{-interets_emprunt:,.2f} €")
            nouvel_emprunt = sim_number("Nouvel emprunt encaissé (€)", "sim_nouvel_emprunt", 0.0, step=1000.0)
            st.caption("Le nouvel emprunt augmente la trésorerie et la dette ; le remboursement du capital diminue la dette mais n'impacte pas le résultat.")

        projection_pre = calculate_projection(
            chiffre_affaires=ca_prev_sim, achats=cout_achats_total_sim,
            masse_salariale=ms_prev_brute, charges_sociales=charges_sociales_prev,
            marketing=budget_mkg_prev, assurances=cout_assurances,
            resultat_historique=resultat_historique, aace_historique=aace_historique,
            depreciations_historique=deprec_historique, dotations_amortissements_historique=dotations_base,
            solde_tresorerie_initial=treso_initiale_source if treso_initiale_source is not None else 0.0,
            solde_epargne_initial=solde_epargne_initial, dette_bancaire=dette_bancaire,
            investissement_ht=cout_invest_machines, credit_fournisseur=credit_fournisseur,
            bfr_mois=float(bfr_projection_pre.bfr), bfr_m1=bfr_precedent_m1,
            achats_titres=achats_titres, ventes_titres=ventes_titres, placement=placement_ep,
            retrait=retrait_ep, dividendes=total_div, nouvelles_entrees_dette_cash=nouvel_emprunt,
            autres_flux_financement=0.0, taux_is=0.25, report_a_nouveau=0.0,
            deficit_precedent=resultat_historique if resultat_historique is not None else 0.0,
            interets_emprunt=interets_emprunt, remboursement_capital=remboursement_capital,
            tva_deductible_hors_immo_mois=tva_deductible_mois,
            tva_deductible_immobilisations_mois=tva_immo_mois,
            tva_nette_ouverture_a_payer=tva_ouverture_a_payer,
            dette_fiscale_sociale_hors_tva_ouverture=dette_fiscale_sociale_hors_tva_ouverture,
            nouvelles_charges_fiscales_sociales=charges_sociales_prev + float(projection_pre.impot_is),
            paiements_fiscaux_sociaux_hors_tva=paiement_social + paiement_is,
        )

        # Second passage : l'IS calculé devient une dette fiscale de clôture,
        # sauf pour la part effectivement payée dans le mois.
        fiscal_social_final = calculate_fiscal_social_projection(
            dette_ouverture=dette_fiscale_sociale_hors_tva_ouverture,
            nouvelles_charges=charges_sociales_prev + float(projection_pre.impot_is),
            paiements=paiement_social + paiement_is,
        )
        dette_fiscale_sociale_proj = float(
            tva_info.tva_collectee_cloture + fiscal_social_final.dette_cloture
        )
        bfr_projection_detail = build_bfr_projection(
            stocks_matieres=stock_matieres_prev, stocks_encours=encours_proj,
            stocks_produits=stock_produits_prev, creances_clients=creances_clients_prev,
            acomptes_is=acomptes_is_proj,
            tva_deductible_hors_immo=tva_info.tva_deductible_hors_immo_cloture,
            dettes_fournisseurs=dettes_fournisseurs_prev,
            dettes_fiscales_sociales=dette_fiscale_sociale_proj,
            bfr_m1=bfr_precedent_m1, warnings=tuple(schedule_projection.warnings),
        )
        bfr_simule = float(bfr_projection_detail.bfr)

        projection = calculate_projection(
            chiffre_affaires=ca_prev_sim, achats=cout_achats_total_sim,
            masse_salariale=ms_prev_brute, charges_sociales=charges_sociales_prev,
            marketing=budget_mkg_prev, assurances=cout_assurances,
            resultat_historique=resultat_historique, aace_historique=aace_historique,
            depreciations_historique=deprec_historique, dotations_amortissements_historique=dotations_base,
            solde_tresorerie_initial=treso_initiale_source if treso_initiale_source is not None else 0.0,
            solde_epargne_initial=solde_epargne_initial, dette_bancaire=dette_bancaire,
            investissement_ht=cout_invest_machines, credit_fournisseur=credit_fournisseur,
            bfr_mois=bfr_simule, bfr_m1=bfr_precedent_m1,
            achats_titres=achats_titres, ventes_titres=ventes_titres, placement=placement_ep,
            retrait=retrait_ep, dividendes=total_div, nouvelles_entrees_dette_cash=nouvel_emprunt,
            autres_flux_financement=0.0, taux_is=0.25, report_a_nouveau=0.0,
            deficit_precedent=resultat_historique if resultat_historique is not None else 0.0,
            interets_emprunt=interets_emprunt, remboursement_capital=remboursement_capital,
            tva_deductible_hors_immo_mois=tva_deductible_mois,
            tva_deductible_immobilisations_mois=tva_immo_mois,
            tva_nette_ouverture_a_payer=tva_ouverture_a_payer,
            dette_fiscale_sociale_hors_tva_ouverture=dette_fiscale_sociale_hors_tva_ouverture,
            nouvelles_charges_fiscales_sociales=charges_sociales_prev + float(projection_pre.impot_is),
            paiements_fiscaux_sociaux_hors_tva=paiement_social + paiement_is,
        )

        for warning in projection.warnings:
            st.warning(f"⚠️ Projection : {warning}")

        ca_cumule_sim = ca_cumule_historique + ca_prev_sim
        res_avant_impot_cumule_sim = res_avant_impot_cumule_historique + float(projection.resultat_avant_impot)
        taux_profitabilite = (float(projection.resultat_avant_impot) / ca_prev_sim * 100) if ca_prev_sim > 0 else 0.0
        taux_rentabilite = (float(projection.resultat_avant_impot) / capitaux_propres * 100) if capitaux_propres > 0 else 0.0
        taux_profitabilite_cumule = (res_avant_impot_cumule_sim / ca_cumule_sim * 100) if ca_cumule_sim > 0 else taux_profitabilite
        taux_rentabilite_cumule = (res_avant_impot_cumule_sim / capitaux_propres * 100) if capitaux_propres > 0 else taux_rentabilite

        st.divider()
        st.markdown("##### 💶 Synthèse Financière & Situation Globale de l'Entreprise")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.markdown("##### 📈 Produits & Charges")
            st.metric("Chiffre d'Affaires Prévisionnel (CA)", f"{ca_prev_sim:,.2f} €")
            st.metric("Total des Charges d'Exploitation (Fixes + Variables)", f"{float(projection.total_charges):,.2f} €")
            st.metric("Achat Nouvelles Machines (TTC)", f"{-float(projection.acompte_ttc):,.2f} €")
            st.metric("Impôt sur les Sociétés (IS)", f"{-float(projection.impot_is):,.2f} €")
            st.metric("TVA collectée du mois", f"{float(projection.tva_collectee):,.2f} €")
            st.metric("TVA payée ce mois", f"{-float(projection.tva_payee_ce_mois):,.2f} €")
            st.metric("Intérêts emprunt ciblés", f"{-float(projection.interets_emprunt):,.2f} €")
            st.metric("Remboursement du capital", f"{-float(projection.remboursement_capital):,.2f} €")
            st.metric("Dette bancaire fin de mois", f"{float(projection.dette_bancaire_finale):,.2f} €")
            st.metric("Nouvel emprunt encaissé", f"{float(nouvel_emprunt):,.2f} €")
            if projection.dette_fournisseur_investissement > 0:
                st.metric("Dette fournisseur immobilisation créée", f"{float(projection.dette_fournisseur_investissement):,.2f} €")

        with col_f2:
            st.markdown("##### 🎯 Résultat Prévisionnel")
            historique_delta = float(projection.resultat_net) - float(resultat_historique or 0.0)
            st.metric("Résultat Net Mensuel Prévisionnel", f"{float(projection.resultat_net):,.2f} €", delta=f"{historique_delta:+,.2f} € vs M-1")
            st.markdown("##### 📊 Ratios de Performance")
            c_rm1, c_rm2 = st.columns(2)
            c_rm1.metric("Profitabilité Mensuelle", f"{taux_profitabilite:.2f} %")
            c_rm2.metric("Rentabilité Mensuelle", f"{taux_rentabilite:.2f} %")
            c_rc1, c_rc2 = st.columns(2)
            c_rc1.metric("Profitabilité CUMULÉE", f"{taux_profitabilite_cumule:.2f} %")
            c_rc2.metric("Rentabilité CUMULÉE", f"{taux_rentabilite_cumule:.2f} %")

        st.divider()
        st.markdown("##### 🧮 Décomposition du BFR Prévisionnel")
        bfr_view = {
            "Poste": [
                "Stocks matières", "Stocks d'en-cours", "Stocks produits",
                "Créances clients", "Acomptes sur IS", "TVA déductible hors immobilisations",
                "Dettes fournisseurs", "Dettes fiscales & sociales", "BFR total", "ΔBFR vs M-1",
            ],
            "Montant (€)": [
                float(bfr_projection_detail.components.stocks_matieres),
                float(bfr_projection_detail.components.stocks_encours),
                float(bfr_projection_detail.components.stocks_produits),
                float(bfr_projection_detail.components.creances_clients),
                float(bfr_projection_detail.components.acomptes_is),
                float(bfr_projection_detail.components.tva_deductible_hors_immo),
                -float(bfr_projection_detail.components.dettes_fournisseurs),
                -float(bfr_projection_detail.components.dettes_fiscales_sociales),
                float(bfr_projection_detail.bfr),
                float(bfr_projection_detail.variation_vs_m1 or 0.0),
            ],
        }
        st.dataframe(pd.DataFrame(bfr_view), use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("##### 🏦 Pont de Trésorerie")
        st.caption(
            f"Référence M-1 : **{periode_m1}** — Solde bancaire final réel : "
            f"**{float(projection.tresorerie_initiale):,.2f} €**"
        )
        st.caption("Créances clients et dettes fournisseurs : projection par échéance. Les flux d'encaissement/règlement affichés servent à construire les soldes de BFR et ne sont pas ajoutés une seconde fois aux flux de trésorerie.")
        waterfall = pd.DataFrame([
            {"Étape": "Solde initial", "Impact (€)": float(projection.tresorerie_initiale)},
            {"Étape": "Flux exploitation", "Impact (€)": float(projection.flux_exploitation)},
            {"Étape": "Flux investissement", "Impact (€)": float(projection.flux_investissement)},
            {"Étape": "Flux financement", "Impact (€)": float(projection.flux_financement)},
            {"Étape": "Solde final", "Impact (€)": float(projection.tresorerie_finale)},
        ])
        st.dataframe(waterfall, use_container_width=True, hide_index=True)
        st.caption(
            f"Contrôle : {float(projection.tresorerie_initiale):,.2f} + "
            f"{float(projection.flux_exploitation):,.2f} + {float(projection.flux_investissement):,.2f} + "
            f"{float(projection.flux_financement):,.2f} = {float(projection.tresorerie_finale):,.2f} €"
        )
        if projection.tresorerie_finale >= 0:
            st.info(f"Trésorerie Fin de Mois Estimée : **{float(projection.tresorerie_finale):,.2f} €**")
        else:
            st.error(f"⚠️ DÉCOUVERT BANCAIRE ESTIMÉ : **{float(projection.tresorerie_finale):,.2f} €**")

