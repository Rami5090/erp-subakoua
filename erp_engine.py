import os
import streamlit as st
import pandas as pd
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Chargement optionnel du fichier .env pour le local
load_dotenv()

# ==========================================
# 1. CONFIGURATION ET CONNEXION BDD (UNIVERSELLE)
# ==========================================
st.set_page_config(page_title="ERP Subakoua - Cockpit Stratégique", layout="wide", initial_sidebar_state="expanded")

@st.cache_resource
def init_connection():
    try:
        host = os.getenv("DB_HOST")
        user = os.getenv("DB_USER")
        password = os.getenv("DB_PASSWORD")
        database = os.getenv("DB_NAME", "subakoua_erp")
        port = int(os.getenv("DB_PORT", 16869))
        
        if not host and "mysql" in st.secrets:
            host = st.secrets["mysql"]["host"]
            user = st.secrets["mysql"]["user"]
            password = st.secrets["mysql"]["password"]
            database = st.secrets["mysql"].get("database", "subakoua_erp")
            port = int(st.secrets["mysql"].get("port", 16869))
            
        if not host or not password:
            st.error("🔌 Identifiants de base de données introuvables (Vérifiez le fichier .env ou les secrets Streamlit).")
            return None
        
        engine = create_engine(
            f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}",
            connect_args={'ssl': {}},
            pool_pre_ping=True, 
            pool_recycle=3600   
        )
        with engine.connect() as conn:
            pass
        return engine
    except Exception as e:
        st.error(f"🔌 Impossible de se connecter à la base de données. Mode dégradé. Erreur : {e}")
        return None

engine = init_connection()

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
    except Exception:
        pass

init_db_simu()

# ==========================================
# 2. CHARGEMENT BDD & FILETS DE SÉCURITÉ
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
def charger_stocks_securite_bdd():
    if engine is None: return {}
    try:
        query = "SELECT matiere, stock_securite FROM Parametres_StocksSecurite"
        df = pd.read_sql(query, engine)
        return dict(zip(df['matiere'], df['stock_securite']))
    except Exception: return {}

@st.cache_data
def charger_parametre_global(nom_param, defaut=0.0):
    if engine is None: return float(defaut)
    try:
        query = f"SELECT valeur FROM Parametres_Globaux WHERE parametre = '{nom_param}'"
        df = pd.read_sql(query, engine)
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
        df = pd.read_sql(f"SELECT * FROM Decisions_RH_Mensuel WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_donnees_mkg_bdd(tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(f"SELECT * FROM Decisions_Marketing WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_ventes_bdd(tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(f"SELECT * FROM Ventes_Historique WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_donnees_fin_bdd(tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(f"SELECT * FROM Finances_Mensuelles WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def lister_scenarios(tour_id):
    if engine is None: return []
    try:
        df = pd.read_sql(f"SELECT Token_Seed, Nom_Scenario FROM simulations_seeds WHERE Tour_ID = {tour_id}", engine)
        return df.to_dict('records')
    except Exception: return []

def charger_scenario_seed(token_seed, tour_id):
    if engine is None: return {}
    try:
        df = pd.read_sql(f"SELECT * FROM simulations_seeds WHERE Token_Seed = '{token_seed}' AND Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def sauvegarder_scenario_seed(token_seed, tour_id, nom, parametres_dict):
    if engine is None: return
    try:
        clean_params = {k: v for k, v in parametres_dict.items() if isinstance(v, (int, float, str, bool))}
        json_data = json.dumps(clean_params, ensure_ascii=False).replace("'", "''")
        query = f"""
            INSERT INTO simulations_seeds (Token_Seed, Tour_ID, Nom_Scenario, Parametres_JSON)
            VALUES ('{token_seed}', {tour_id}, '{nom}', '{json_data}')
            ON DUPLICATE KEY UPDATE Nom_Scenario='{nom}', Parametres_JSON='{json_data}';
        """
        with engine.begin() as conn:
            conn.execute(text(query))
    except Exception as e:
        st.error(f"Erreur sauvegarde seed : {e}")

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
stocks_securite_db = charger_stocks_securite_bdd()
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

    if engine is not None:
        try:
            df_h = pd.read_sql(f"SELECT * FROM Historique_Equipe WHERE Tour_ID = {tour_saisie}", engine)
            df_m = pd.read_sql(f"SELECT * FROM Parc_Machines_Mensuel WHERE Tour_ID = {tour_saisie}", engine)
            d_rh = charger_donnees_rh_bdd(tour_saisie)
            d_mkg = charger_donnees_mkg_bdd(tour_saisie)
            d_ventes = charger_ventes_bdd(tour_saisie)
            dfin = charger_donnees_fin_bdd(tour_saisie)
        except Exception:
            df_h, df_m, d_rh, d_mkg, d_ventes, dfin = pd.DataFrame(), pd.DataFrame(), {}, {}, {}, {}
    else:
        df_h, df_m, d_rh, d_mkg, d_ventes, dfin = pd.DataFrame(), pd.DataFrame(), {}, {}, {}, {}

    def get_h(col, def_v): return float(df_h[col].iloc[0]) if not df_h.empty and col in df_h.columns else def_v
    def get_m(col, def_v): return int(df_m[col].iloc[0]) if not df_m.empty and col in df_m.columns else def_v
    def get_rh_v(k, def_v): return float(d_rh.get(k, def_v))
    def get_rh_e(k, def_v): return int(d_rh.get(k, def_v))

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
            
            st.markdown("**Parc total en fin de mois**")
            m_dec = st.number_input("Total Découpe", value=get_m('Nb_Machines_Decoupe', 10), step=1)
            m_ass = st.number_input("Total Assemblage", value=get_m('Nb_Machines_Assemblage', 16), step=1)
            m_cond = st.number_input("Total Conditionnement", value=get_m('Nb_Machines_Cond', 4), step=1)
            
            st.divider()
            st.markdown("##### 🏗️️ Acquisitions / Investissements du mois")
            acquis_check = st.checkbox("Des machines ont-elles été acquises ce mois-ci ?", value=bool(get_m('Acquisition_Faite', 0)))
            
            acq_dec = st.number_input("Nombre de machines Découpe acquises", value=get_m('Acq_Decoupe', 0), step=1)
            acq_ass = st.number_input("Nombre de machines Assemblage acquises", value=get_m('Acq_Assemblage', 0), step=1)
            acq_cond = st.number_input("Nombre de machines Conditionnement acquises", value=get_m('Acq_Cond', 0), step=1)
            
            if st.form_submit_button("Enregistrer les Machines et Acquisitions"):
                acquis_val = 1 if acquis_check else 0
                if engine:
                    with engine.begin() as conn:
                        query_mach = f"""
                            INSERT INTO Parc_Machines_Mensuel (
                                Tour_ID, Mois, Nb_Machines_Decoupe, Nb_Machines_Assemblage, Nb_Machines_Cond, 
                                Acquisition_Faite, Acq_Decoupe, Acq_Assemblage, Acq_Cond
                            ) VALUES (
                                {tour_saisie}, '{mois_saisie}', {m_dec}, {m_ass}, {m_cond}, 
                                {acquis_val}, {acq_dec}, {acq_ass}, {acq_cond}
                            ) ON DUPLICATE KEY UPDATE 
                                Nb_Machines_Decoupe={m_dec}, Nb_Machines_Assemblage={m_ass}, Nb_Machines_Cond={m_cond},
                                Acquisition_Faite={acquis_val}, Acq_Decoupe={acq_dec}, Acq_Assemblage={acq_ass}, Acq_Cond={acq_cond};
                        """
                        conn.execute(text(query_mach))
                    st.success("✅ Parc machines et acquisitions enregistrés avec succès !")

    with tab_r3:
        with st.form("form_saisie_rh_reel"):
            st.subheader("Saisie des Effectifs et Salaires par Catégorie")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**🏭 Production**")
                eff_ep = st.number_input("Nb Employés Prod", value=get_rh_e('Eff_Employes_Prod', 15), step=1)
                sal_ep = st.number_input("Sal. Employé Prod (€)", value=get_rh_v('Sal_Employes_Prod', 2000.0), step=100.0)
                eff_cp = st.number_input("Nb Cadres Prod", value=get_rh_e('Eff_Cadres_Prod', 3), step=1)
                sal_cp = st.number_input("Sal. Cadre Prod (€)", value=get_rh_v('Sal_Cadres_Prod', 3333.33), step=100.0)
                eff_dp = st.number_input("Nb Directeurs Prod", value=get_rh_e('Eff_Directeurs_Prod', 1), step=1)
                sal_dp = st.number_input("Sal. Directeur Prod (€)", value=get_rh_v('Sal_Directeurs_Prod', 4500.0), step=100.0)
            with c2:
                st.markdown("**📦 Approvisionnement**")
                eff_ea = st.number_input("Nb Employés Appro", value=get_rh_e('Eff_Employes_Appro', 7), step=1)
                sal_ea = st.number_input("Sal. Employé Appro (€)", value=get_rh_v('Sal_Employes_Appro', 2000.0), step=100.0)
                eff_ca = st.number_input("Nb Cadres Appro", value=get_rh_e('Eff_Cadres_Appro', 2), step=1)
                sal_ca = st.number_input("Sal. Cadre Appro (€)", value=get_rh_v('Sal_Cadres_Appro', 2250.0), step=100.0)
                eff_da = st.number_input("Nb Directeurs Appro", value=get_rh_e('Eff_Directeurs_Appro', 1), step=1)
                sal_da = st.number_input("Sal. Directeur Appro (€)", value=get_rh_v('Sal_Directeurs_Appro', 2000.0), step=100.0)
            with c3:
                st.markdown("**💶 Administration**")
                eff_ef = st.number_input("Nb Employés Admin", value=get_rh_e('Eff_Employes_Admin', 7), step=1)
                sal_ef = st.number_input("Sal. Employé Admin (€)", value=get_rh_v('Sal_Employes_Admin', 2000.0), step=100.0)
                eff_cf = st.number_input("Nb Cadres Admin", value=get_rh_e('Eff_Cadres_Admin', 2), step=1)
                sal_cf = st.number_input("Sal. Cadre Admin (€)", value=get_rh_v('Sal_Cadres_Admin', 2200.0), step=100.0)
                eff_df = st.number_input("Nb Directeurs Admin", value=get_rh_e('Eff_Directeurs_Admin', 1), step=1)
                sal_df = st.number_input("Sal. Directeur Admin (€)", value=get_rh_v('Sal_Directeurs_Admin', 2000.0), step=100.0)

            if st.form_submit_button("💾 Enregistrer la Pyramide RH"):
                if engine:
                    try:
                        query_rh = f"""
                            INSERT INTO Decisions_RH_Mensuel (
                                Tour_ID, Mois, 
                                Eff_Employes_Prod, Sal_Employes_Prod, Eff_Cadres_Prod, Sal_Cadres_Prod, Eff_Directeurs_Prod, Sal_Directeurs_Prod,
                                Eff_Employes_Appro, Sal_Employes_Appro, Eff_Cadres_Appro, Sal_Cadres_Appro, Eff_Directeurs_Appro, Sal_Directeurs_Appro,
                                Eff_Employes_Admin, Sal_Employes_Admin, Eff_Cadres_Admin, Sal_Cadres_Admin, Eff_Directeurs_Admin, Sal_Directeurs_Admin
                            ) VALUES (
                                {tour_saisie}, '{mois_saisie}', 
                                {eff_ep}, {sal_ep}, {eff_cp}, {sal_cp}, {eff_dp}, {sal_dp},
                                {eff_ea}, {sal_ea}, {eff_ca}, {sal_ca}, {eff_da}, {sal_da},
                                {eff_ef}, {sal_ef}, {eff_cf}, {sal_cf}, {eff_df}, {sal_df}
                            ) ON DUPLICATE KEY UPDATE 
                                Eff_Employes_Prod={eff_ep}, Sal_Employes_Prod={sal_ep}, Eff_Cadres_Prod={eff_cp}, Sal_Cadres_Prod={sal_cp}, Eff_Directeurs_Prod={eff_dp}, Sal_Directeurs_Prod={sal_dp},
                                Eff_Employes_Appro={eff_ea}, Sal_Employes_Appro={sal_ea}, Eff_Cadres_Appro={eff_ca}, Sal_Cadres_Appro={sal_ca}, Eff_Directeurs_Appro={eff_da}, Sal_Directeurs_Appro={sal_da},
                                Eff_Employes_Admin={eff_ef}, Sal_Employes_Admin={sal_ef}, Eff_Cadres_Admin={eff_cf}, Sal_Cadres_Admin={sal_cf}, Eff_Directeurs_Admin={eff_df}, Sal_Directeurs_Admin={sal_df};
                        """
                        with engine.begin() as conn:
                            conn.execute(text(query_rh))
                        st.success("Pyramide RH enregistrée !")
                    except Exception as e:
                        st.error(f"Erreur SQL : {e}")

    with tab_r4:
        with st.form("form_saisie_mkg"):
            st.subheader("Saisie des Décisions Marketing (Réelles)")
            c_m1, c_m2 = st.columns(2)
            with c_m1:
                st.markdown("**Prix de vente décidés**")
                p_s3 = st.number_input("Prix S3", value=float(d_mkg.get('Prix_S3', 115.0)))
                p_i3 = st.number_input("Prix I3", value=float(d_mkg.get('Prix_I3', 190.0)))
                p_s5 = st.number_input("Prix S5", value=float(d_mkg.get('Prix_S5', 220.0)))
                p_i5 = st.number_input("Prix I5", value=float(d_mkg.get('Prix_I5', 280.0)))
                p_i7 = st.number_input("Prix I7", value=float(d_mkg.get('Prix_I7', 375.0)))
                st.markdown("**Budget Marque & Axes**")
                b_marq = st.number_input("Budget Marque", value=float(d_mkg.get('Budget_Marque', 61500.0)))
                axe_p = st.text_input("Axe Principal", value=str(d_mkg.get('Axe_Principal', 'Durée de vie')))
                axe_a = st.text_input("Axe Accessoire", value=str(d_mkg.get('Axe_Accessoire', 'Souplesse')))
            with c_m2:
                st.markdown("**Budgets PLV**")
                plv_s3 = st.number_input("PLV S3", value=float(d_mkg.get('Pub_S3', 3250.0)))
                plv_i3 = st.number_input("PLV I3", value=float(d_mkg.get('Pub_I3', 4200.0)))
                plv_s5 = st.number_input("PLV S5", value=float(d_mkg.get('Pub_S5', 3400.0)))
                plv_i5 = st.number_input("PLV I5", value=float(d_mkg.get('Pub_I5', 4300.0)))
                plv_i7 = st.number_input("PLV I7", value=float(d_mkg.get('Pub_I7', 3500.0)))
            
            if st.form_submit_button("💾 Enregistrer les Décisions Marketing"):
                if engine:
                    try:
                        q_mkg = f"""
                            INSERT INTO Decisions_Marketing (Tour_ID, Mois, Prix_S3, Prix_I3, Prix_S5, Prix_I5, Prix_I7, Budget_Marque, Axe_Principal, Axe_Accessoire, Pub_S3, Pub_I3, Pub_S5, Pub_I5, Pub_I7)
                            VALUES ({tour_saisie}, '{mois_saisie}', {p_s3}, {p_i3}, {p_s5}, {p_i5}, {p_i7}, {b_marq}, '{axe_p}', '{axe_a}', {plv_s3}, {plv_i3}, {plv_s5}, {plv_i5}, {plv_i7})
                            ON DUPLICATE KEY UPDATE
                            Prix_S3={p_s3}, Prix_I3={p_i3}, Prix_S5={p_s5}, Prix_I5={p_i5}, Prix_I7={p_i7}, Budget_Marque={b_marq}, Axe_Principal='{axe_p}', Axe_Accessoire='{axe_a}', Pub_S3={plv_s3}, Pub_I3={plv_i3}, Pub_S5={plv_s5}, Pub_I5={plv_i5}, Pub_I7={plv_i7};
                        """
                        with engine.begin() as conn:
                            conn.execute(text(q_mkg))
                        st.success("Décisions Marketing enregistrées !")
                    except Exception as e:
                        st.error(f"Erreur SQL : {e}")

    with tab_r5:
        with st.form("form_saisie_ventes"):
            st.subheader("Saisie des Ventes Réelles")
            c_v1, c_v2, c_v3, c_v4, c_v5 = st.columns(5)
            v_s3 = c_v1.number_input("Ventes S3 (u)", value=int(d_ventes.get('Ventes_S3', 0)), step=1)
            v_i3 = c_v2.number_input("Ventes I3 (u)", value=int(d_ventes.get('Ventes_I3', 0)), step=1)
            v_s5 = c_v3.number_input("Ventes S5 (u)", value=int(d_ventes.get('Ventes_S5', 0)), step=1)
            v_i5 = c_v4.number_input("Ventes I5 (u)", value=int(d_ventes.get('Ventes_I5', 0)), step=1)
            v_i7 = c_v5.number_input("Ventes I7 (u)", value=int(d_ventes.get('Ventes_I7', 0)), step=1)
            
            if st.form_submit_button("💾 Enregistrer les Volumes Vendus"):
                if engine:
                    try:
                        q_ventes = f"""
                            INSERT INTO Ventes_Historique (Tour_ID, Mois, Ventes_S3, Ventes_I3, Ventes_S5, Ventes_I5, Ventes_I7)
                            VALUES ({tour_saisie}, '{mois_saisie}', {v_s3}, {v_i3}, {v_s5}, {v_i5}, {v_i7})
                            ON DUPLICATE KEY UPDATE
                            Ventes_S3={v_s3}, Ventes_I3={v_i3}, Ventes_S5={v_s5}, Ventes_I5={v_i5}, Ventes_I7={v_i7};
                        """
                        with engine.begin() as conn:
                            conn.execute(text(q_ventes))
                        st.success("Volumes de ventes enregistrés !")
                    except Exception as e:
                        st.error(f"Erreur SQL : {e}")

    with tab_r6:
        with st.form("form_saisie_finance"):
            st.subheader("Saisie des États Financiers Réels")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Compte de Résultat**")
                s_ca = st.number_input("Chiffre d'Affaires Net", value=float(dfin.get('CA_Net', 0.0)), step=1000.0)
                s_achats = st.number_input("Achats Matières", value=float(dfin.get('Achats_Matieres', 0.0)), step=1000.0)
                s_ace = st.number_input("Autres Charges Externes", value=float(dfin.get('Autres_Charges_Externes', 0.0)), step=1000.0)
                s_remun = st.number_input("Rémunération du personnel", value=float(dfin.get('Remuneration_Personnel', 0.0)), step=1000.0)
                s_soc = st.number_input("Charges Sociales", value=float(dfin.get('Charges_Sociales', 0.0)), step=1000.0)
                s_dot = st.number_input("Dotations Amortissements", value=float(dfin.get('Dotations_Amortissements', 0.0)), step=1000.0)
                s_rnet = st.number_input("Résultat Net", value=float(dfin.get('Resultat_Net', 0.0)), step=1000.0)
            with c2:
                st.markdown("**Bilan**")
                s_immo = st.number_input("Total Actif Immobilisé", value=float(dfin.get('Total_Actif_Immobilise', 0.0)), step=1000.0)
                s_circ = st.number_input("Total Actif Circulant", value=float(dfin.get('Total_Actif_Circulant', 0.0)), step=1000.0)
                s_cap = st.number_input("Total Capitaux Propres", value=float(dfin.get('Total_Capitaux_Propres', 0.0)), step=1000.0)
                s_emp = st.number_input("Emprunts Bancaires", value=float(dfin.get('Emprunts_Bancaires', 0.0)), step=1000.0)
                s_bq = st.number_input("Banque (Disponibilités)", value=float(dfin.get('Disponibilites_Banque', 0.0)), step=1000.0)

            if st.form_submit_button("💾 Enregistrer les Données Financières"):
                if engine:
                    try:
                        query_f = f"""
                            INSERT INTO Finances_Mensuelles (Tour_ID, Mois, CA_Net, Achats_Matieres, Autres_Charges_Externes, Remuneration_Personnel, Charges_Sociales, Dotations_Amortissements, Resultat_Net, Total_Actif_Immobilise, Total_Actif_Circulant, Total_Capitaux_Propres, Emprunts_Bancaires, Disponibilites_Banque) 
                            VALUES ({tour_saisie}, '{mois_saisie}', {s_ca}, {s_achats}, {s_ace}, {s_remun}, {s_soc}, {s_dot}, {s_rnet}, {s_immo}, {s_circ}, {s_cap}, {s_emp}, {s_bq})
                            ON DUPLICATE KEY UPDATE 
                            CA_Net={s_ca}, Achats_Matieres={s_achats}, Autres_Charges_Externes={s_ace}, Remuneration_Personnel={s_remun}, Charges_Sociales={s_soc}, Dotations_Amortissements={s_dot}, Resultat_Net={s_rnet}, Total_Actif_Immobilise={s_immo}, Total_Actif_Circulant={s_circ}, Total_Capitaux_Propres={s_cap}, Emprunts_Bancaires={s_emp}, Disponibilites_Banque={s_bq};
                        """
                        with engine.begin() as conn:
                            conn.execute(text(query_f))
                        st.success("✅ Données financières enregistrées !")
                    except Exception as e:
                        st.error(f"Erreur SQL : {e}")

# ==========================================
# MODULE 3 : SIMULATEUR & DÉCISION STRATÉGIQUE
# ==========================================
elif module_principal == "🧠 Simulateur & Décision Stratégique":
    st.title("🧠 Simulateur Stratégique & Interconnectivité (Seed Engine)")
    
    tour_id_precedent = max(1, tour_id_actif - 1)
    st.info(f"Simulation interactive pour le mois : **{mois_selectionne}** (Basée sur l'état de l'entreprise au Tour ID {tour_id_precedent})")

    if 'last_loaded_scenario' not in st.session_state:
        st.session_state['last_loaded_scenario'] = "--- Nouvelle Simulation ---"

    scenarios_dispos = lister_scenarios(tour_id_actif)
    options_scenarios = ["--- Nouvelle Simulation ---"] + [f"{s['Token_Seed']} - {s['Nom_Scenario']}" for s in scenarios_dispos]
    
    with st.expander("🔑 Gestionnaire de Scénario (Seed)", expanded=True):
        scenario_choisi = st.selectbox("📥 Charger une Seed existante :", options_scenarios, key="seed_selector_ui")
        
        if scenario_choisi != st.session_state['last_loaded_scenario']:
            st.session_state['last_loaded_scenario'] = scenario_choisi
            keys_to_clear = [k for k in st.session_state.keys() if k.startswith('sim_') or k.startswith('sp_') or k.startswith('sa_') or k.startswith('sf_') or k.startswith('ord_') or k.startswith('qual_') or k.startswith('ctrl_') or k.startswith('slider_') or k.startswith('radio_') or k.startswith('input_')]
            for k in keys_to_clear:
                del st.session_state[k]
                
            if scenario_choisi != "--- Nouvelle Simulation ---":
                token_select = scenario_choisi.split(" - ")[0]
                seed_loaded = charger_scenario_seed(token_select, tour_id_actif)
                try:
                    params_seed = json.loads(seed_loaded.get('Parametres_JSON', '{}'))
                    for k, v in params_seed.items():
                        st.session_state[k] = v
                    st.session_state["input_token"] = token_select
                    st.session_state["input_nom_scenario"] = seed_loaded.get('Nom_Scenario', '')
                except Exception:
                    pass
            else:
                st.session_state["input_token"] = "SEED-BASE-01"
                st.session_state["input_nom_scenario"] = "Stratégie Standard"
            
            st.rerun() 

        st.markdown("**💾 Enregistrer la configuration actuelle**")
        col_s1, col_s2, col_s3 = st.columns([2, 2, 1])
        with col_s1: token_actif = sim_text("Token / Seed :", "input_token", "SEED-BASE-01")
        with col_s2: nom_scenario = sim_text("Nom du scénario :", "input_nom_scenario", "Stratégie Standard")
        with col_s3:
            st.write("")
            if st.button("Enregistrer", use_container_width=True):
                keys_to_save = [k for k in st.session_state.keys() if k.startswith('sim_') or k.startswith('sp_') or k.startswith('sa_') or k.startswith('sf_') or k.startswith('ord_') or k.startswith('qual_') or k.startswith('ctrl_') or k.startswith('slider_') or k.startswith('radio_')]
                current_state = {k: st.session_state[k] for k in keys_to_save}
                sauvegarder_scenario_seed(token_actif, tour_id_actif, nom_scenario, current_state)
                st.success("Seed sauvegardée avec TOUS les paramètres !")

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
    def get_rh_v(k, def_v): return float(donnees_rh.get(k, def_v))
    def get_rh_e(k, def_v): return int(donnees_rh.get(k, def_v))

    score_rh_prod = calculer_score_rh_prod_dynamique(donnees_rh, ratio_optimum_rh)

    # Récupération du parc de machines actuel
    nb_m_dec_actuel = get_m('Nb_Machines_Decoupe', 10)
    nb_m_ass_actuel = get_m('Nb_Machines_Assemblage', 16)
    nb_m_cond_actuel = get_m('Nb_Machines_Cond', 4)

    # Configuration centralisée des investissements machines (en amont pour la finance)
    st.markdown("##### 🛒 Investissements & Acquisitions de Machines (Global Simulateur)")
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

    # Choix du mode de règlement pour la gestion des dettes fournisseurs
    mode_financement_machines = sim_radio(
        "Mode de règlement des nouvelles machines :", 
        ["Comptant (Impact immédiat sur la trésorerie)", "Crédit Fournisseur / Dette d'investissement (Délai)"],
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

        st.divider()
        col_pub_saisie, col_pub_eval = st.columns([1, 1])
        with col_pub_saisie:
            budget_pub_marque = sim_number("Budget Pub Marque HT (€)", "sim_pm_pub_marque", float(donnees_mkg.get('Budget_Marque', 60500.0)), step=500.0)
            liste_axes = ["Durée de vie", "Protection thermique", "Souplesse", "Enfilage", "Finitions", "Esthétique", "Prix bas"]
            axe_principal = sim_select("Axe principal", liste_axes, "sim_axe_p", donnees_mkg.get('Axe_Principal', "Durée de vie"))
            
            liste_axes_acc = liste_axes + ["Aucun"]
            axe_secondaire = sim_select("Axe secondaire", liste_axes_acc, "sim_axe_s", donnees_mkg.get('Axe_Accessoire', "Enfilage"))

            pub_s3 = sim_number("Shorty 3 PLV (€)", "sim_pub_s3", float(donnees_mkg.get('Pub_S3', 3100.0)), step=100.0)
            pub_i3 = sim_number("Integral 3 PLV (€)", "sim_pub_i3", float(donnees_mkg.get('Pub_I3', 4100.0)), step=100.0)
            pub_s5 = sim_number("Shorty 5 PLV (€)", "sim_pub_s5", float(donnees_mkg.get('Pub_S5', 3200.0)), step=100.0)
            pub_i5 = sim_number("Integral 5 PLV (€)", "sim_pub_i5", float(donnees_mkg.get('Pub_I5', 4200.0)), step=100.0)
            pub_i7 = sim_number("Integral 7 PLV (€)", "sim_pub_i7", float(donnees_mkg.get('Pub_I7', 3500.0)), step=100.0)

        budgets_p_dict = {'Shorty 3': pub_s3, 'Integral 3': pub_i3, 'Shorty 5': pub_s5, 'Integral 5': pub_i5, 'Integral 7': pub_i7}
        notes_predites = predire_notes_publicite(budget_marq=budget_pub_marque, axe_princ=axe_principal, axe_acc=axe_secondaire, budgets_prods=budgets_p_dict)

        with col_pub_eval:
            st.markdown("#### 🌟 Évaluation Notoriété & Image (Simulée)")
            for produit, notes in notes_predites.items():
                st.markdown(f"* **{produit}** — Notor.: {afficher_etoiles(notes['notoriete'])} | Image: {afficher_etoiles(notes['image'])} | Synthèse Marque: {afficher_etoiles(notes['synthese'])} | **Éval. Produit (PLV)**: {afficher_etoiles(notes['eval_prod'])}")

        st.divider()
        st.subheader("Volumes de Ventes Cibles")
        col_vs1, col_vs2, col_vs3, col_vs4, col_vs5 = st.columns(5)
        sim_v_s3 = col_vs1.number_input("Shorty 3 (u)", key="sim_s3", value=st.session_state.setdefault("sim_s3", 50))
        sim_v_i3 = col_vs2.number_input("Integral 3 (u)", key="sim_i3", value=st.session_state.setdefault("sim_i3", 500))
        sim_v_s5 = col_vs3.number_input("Shorty 5 (u)", key="sim_s5", value=st.session_state.setdefault("sim_s5", 100))
        sim_v_i5 = col_vs4.number_input("Integral 5 (u)", key="sim_i5", value=st.session_state.setdefault("sim_i5", 1500))
        sim_v_i7 = col_vs5.number_input("Integral 7 (u)", key="sim_i7", value=st.session_state.setdefault("sim_i7", 2500))
        previsions_ventes = {'Shorty 3': sim_v_s3, 'Integral 3': sim_v_i3, 'Shorty 5': sim_v_s5, 'Integral 5': sim_v_i5, 'Integral 7': sim_v_i7}
        
        ca_prev_sim = (sim_v_s3 * prix_s3) + (sim_v_i3 * prix_i3) + (sim_v_s5 * prix_s5) + (sim_v_i5 * prix_i5) + (sim_v_i7 * prix_i7)

    with tab_sim_prod:
        st.subheader("2. Production & Ateliers")
        st.info(f"Parc machine actif : Découpe ({nb_m_dec_sim}), Assemblage ({nb_m_ass_sim}), Conditionnement ({nb_m_cond_sim}).")

        c_levier1, c_levier2, c_levier3 = st.columns(3)
        with c_levier1:
            qualite_strategique = sim_select("Qualité Matière (Achats)", [35, 50, 70], "qual_mat_prod", 50)
        with c_levier2:
            niveau_controle = sim_select("Niveau de Contrôle", ["Allégé", "Standard", "Renforcé"], "ctrl_usine", "Standard")
        with c_levier3:
            st.metric("Score RH Production", f"{score_rh_prod:.1f} / 100")

        score_mixte_matiere = qualite_strategique + (score_rh_prod * 0.5) 
        if score_mixte_matiere > 90: regime_matiere = 'minimum'
        elif score_mixte_matiere < 60: regime_matiere = 'maximum'
        else: regime_matiere = 'normal'

        val_controle = {"Allégé": 1, "Standard": 2, "Renforcé": 3}[niveau_controle]
        if val_controle == 3 and score_rh_prod < 50: regime_temps = 'maximum'
        elif val_controle == 1 and score_rh_prod > 65: regime_temps = 'minimum'
        elif val_controle == 2 and score_rh_prod < 40: regime_temps = 'maximum'
        elif val_controle == 2 and score_rh_prod > 80: regime_temps = 'minimum'
        else: regime_temps = 'normal'

        st.caption(f"⚙️ Barème actif nomenclature : **{regime_matiere.upper()}** | Barème temps : **{regime_temps.upper()}**")
        st.divider()

        ord_s3 = sim_number("Prod S3", "ord_s3", 50)
        ord_i3 = sim_number("Prod I3", "ord_i3", 500)
        ord_s5 = sim_number("Prod S5", "ord_s5", 100)
        ord_i5 = sim_number("Prod I5", "ord_i5", 1500)
        ord_i7 = sim_number("Prod I7", "ord_i7", 2500)
        ordres_prod_dict = {'Shorty 3': ord_s3, 'Integral 3': ord_i3, 'Shorty 5': ord_s5, 'Integral 5': ord_i5, 'Integral 7': ord_i7}

        tot_dec = sum(q * temps_dynamique.get(p, {}).get('Decoupe', {}).get(regime_temps, 0.0) for p, q in ordres_prod_dict.items())
        tot_ass = sum(q * temps_dynamique.get(p, {}).get('Assemblage', {}).get(regime_temps, 0.0) for p, q in ordres_prod_dict.items())
        tot_cond = sum(q * temps_dynamique.get(p, {}).get('Cond', {}).get(regime_temps, 0.0) for p, q in ordres_prod_dict.items())

        cap_dec = max(1.0, float(nb_m_dec_sim)) * capacite_ref_machine
        cap_ass = max(1.0, float(nb_m_ass_sim)) * capacite_ref_machine
        cap_cond = max(1.0, float(nb_m_cond_sim)) * capacite_ref_machine

        st.markdown("##### 🏭 Analyse de la Charge Ateliers (Avec Achats Prévus)")
        ca1, ca2, ca3 = st.columns(3)
        ca1.metric("Découpe", f"{(tot_dec / cap_dec * 100) if cap_dec > 0 else 0.0:.1f}%", f"{nb_m_dec_sim} mach.")
        ca2.metric("Assemblage", f"{(tot_ass / cap_ass * 100) if cap_ass > 0 else 0.0:.1f}%", f"{nb_m_ass_sim} mach.")
        ca3.metric("Conditionnement", f"{(tot_cond / cap_cond * 100) if cap_cond > 0 else 0.0:.1f}%", f"{nb_m_cond_sim} mach.")

        st.divider()
        st.markdown("##### 📦 Couverture des Ventes par la Production")
        couverture_data = []
        for prod, qte_prod in ordres_prod_dict.items():
            qte_vente_cible = previsions_ventes.get(prod, 0)
            couverture_pct = (qte_prod / qte_vente_cible * 100) if qte_vente_cible > 0 else 0.0
            if qte_prod < qte_vente_cible: statut = "⚠️ Insuffisant (Rupture)"
            elif qte_prod > qte_vente_cible * 1.2: statut = "📦 Surproduction"
            else: statut = "✅ Équilibre optimal"

            couverture_data.append({
                "Produit": prod, "Ventes Cibles (u)": qte_vente_cible,
                "Production Prévue (u)": qte_prod, "Couverture (%)": f"{couverture_pct:.1f}%", "Diagnostic": statut
            })
        st.dataframe(pd.DataFrame(couverture_data).set_index("Produit"), use_container_width=True)

    with tab_sim_appro:
        st.subheader("3. MRP2 & Achats (Calcul des besoins & Arbitrage)")
        col_opt1, col_opt2 = st.columns(2)
        with col_opt1:
            pct_vente_couvert = sim_slider("Part de la prod dédiée à la couverture (%)", 0, 100, "slider_pct_vente", 100, step=5)
        with col_opt2:
            coeff_stock_secu = sim_slider("Coefficient stock de sécurité (x10 = % prod)", 0.0, 2.0, "slider_coeff_secu", 1.0, step=0.1)

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

        mrp_lignes = []
        recos_achats = []
        cout_achats_total_sim = 0.0

        for mat, b_br
