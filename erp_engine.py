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
    except Exception:
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
# 2. CHARGEMENT BDD & FONCTIONS UTILITAIRES
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
                eff_ep = st.number_input("Nb Employés Prod", value=get_rh_e('Eff_Employes_Prod', 15), step=1)
                sal_ep = st.number_input("Sal. Employé Prod (€)", value=get_rh_v('Sal_Employes_Prod', 2000.0), step=100.0)
                eff_cp = st.number_input("Nb Cadres Prod", value=get_rh_e('Eff_Cadres_Prod', 3), step=1)
                sal_cp = st.number_input("Sal. Cadre Prod (€)", value=get_rh_v('Sal_Cadres_Prod', 3333.33), step=100.0)
                eff_dp = st.number_input("Nb Directeurs Prod", value=get_rh_e('Eff_Directeurs_Prod', 1), step=1)
                sal_dp = st.number_input("Sal. Directeur Prod (€)", value=get_rh_v('Sal_Directeurs_Prod', 4500.0), step=100.0)
            with c2:
                eff_ea = st.number_input("Nb Employés Appro", value=get_rh_e('Eff_Employes_Appro', 7), step=1)
                sal_ea = st.number_input("Sal. Employé Appro (€)", value=get_rh_v('Sal_Employes_Appro', 2000.0), step=100.0)
                eff_ca = st.number_input("Nb Cadres Appro", value=get_rh_e('Eff_Cadres_Appro', 2), step=1)
                sal_ca = st.number_input("Sal. Cadre Appro (€)", value=get_rh_v('Sal_Cadres_Appro', 2250.0), step=100.0)
                eff_da = st.number_input("Nb Directeurs Appro", value=get_rh_e('Eff_Directeurs_Appro', 1), step=1)
                sal_da = st.number_input("Sal. Directeur Appro (€)", value=get_rh_v('Sal_Directeurs_Appro', 2000.0), step=100.0)
            with c3:
                eff_ef = st.number_input("Nb Employés Admin", value=get_rh_e('Eff_Employes_Admin', 7), step=1)
                sal_ef = st.number_input("Sal. Employé Admin (€)", value=get_rh_v('Sal_Employes_Admin', 2000.0), step=100.0)
                eff_cf = st.number_input("Nb Cadres Admin", value=get_rh_e('Eff_Cadres_Admin', 2), step=1)
                sal_cf = st.number_input("Sal. Cadre Admin (€)", value=get_rh_v('Sal_Cadres_Admin', 2200.0), step=100.0)
                eff_df = st.number_input("Nb Directeurs Admin", value=get_rh_e('Eff_Directeurs_Admin', 1), step=1)
                sal_df = st.number_input("Sal. Directeur Admin (€)", value=get_rh_v('Sal_Directeurs_Admin', 2000.0), step=100.0)
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
        for mat, b_brut in besoins_bruts.items():
            s_ini = stocks_initiaux[mat]
            secu = b_brut * (coeff_stock_secu / 10.0)
            b_net = max(0.0, (b_brut + secu) - s_ini)
            if b_net > 0:
                opt = optimiser_fournisseur_matiere(mat, qualite_visee=qualite_strategique, critere=critere_arbitrage)
                if opt:
                    cout_achats_total_sim += b_net * opt['prix']

    with tab_sim_rh:
        st.subheader("4. Pilotage RH Interconnecté")
        sim_ep_eff = sim_number("Nb Employés Prod", 'sp_e', get_rh_e('Eff_Employes_Prod', 15))
        sim_ep_sal = sim_number("Sal. Employé Prod (€)", 'sp_es', get_rh_v('Sal_Employes_Prod', 2000.0), step=100.0)
        sim_cp_eff = sim_number("Nb Cadres Prod", 'sp_c', get_rh_e('Eff_Cadres_Prod', 3))
        sim_cp_sal = sim_number("Sal. Cadre Prod (€)", 'sp_cs', get_rh_v('Sal_Cadres_Prod', 3333.33), step=100.0)
        sim_dp_eff = sim_number("Nb Directeurs Prod", 'sp_d', get_rh_e('Eff_Directeurs_Prod', 1))
        sim_dp_sal = sim_number("Sal. Directeur Prod (€)", 'sp_ds', get_rh_v('Sal_Directeurs_Prod', 4500.0), step=100.0)
        tot_p = (sim_ep_eff * sim_ep_sal) + (sim_cp_eff * sim_cp_sal) + (sim_dp_eff * sim_dp_sal)

        sim_ea_eff = sim_number("Nb Employés Appro", 'sa_e', get_rh_e('Eff_Employes_Appro', 7))
        sim_ea_sal = sim_number("Sal. Employé Appro (€)", 'sa_es', get_rh_v('Sal_Employes_Appro', 2000.0), step=100.0)
        sim_ca_eff = sim_number("Nb Cadres Appro", 'sa_c', get_rh_e('Eff_Cadres_Appro', 2))
        sim_ca_sal = sim_number("Sal. Cadre Appro (€)", 'sa_cs', get_rh_v('Sal_Cadres_Appro', 2250.0), step=100.0)
        sim_da_eff = sim_number("Nb Directeurs Appro", 'sa_d', get_rh_e('Eff_Directeurs_Appro', 1))
        sim_da_sal = sim_number("Sal. Directeur Appro (€)", 'sa_ds', get_rh_v('Sal_Directeurs_Appro', 2000.0), step=100.0)
        tot_a = (sim_ea_eff * sim_ea_sal) + (sim_ca_eff * sim_ca_sal) + (sim_da_eff * sim_da_sal)

        sim_ef_eff = sim_number("Nb Employés Admin", 'sf_e', get_rh_e('Eff_Employes_Admin', 7))
        sim_ef_sal = sim_number("Sal. Employé Admin (€)", 'sf_es', get_rh_v('Sal_Employes_Admin', 2000.0), step=100.0)
        sim_cf_eff = sim_number("Nb Cadres Admin", 'sf_c', get_rh_e('Eff_Cadres_Admin', 2))
        sim_cf_sal = sim_number("Sal. Cadre Admin (€)", 'sf_cs', get_rh_v('Sal_Cadres_Admin', 2200.0), step=100.0)
        sim_df_eff = sim_number("Nb Directeurs Admin", 'sf_d', get_rh_e('Eff_Directeurs_Admin', 1))
        sim_df_sal = sim_number("Sal. Directeur Admin (€)", 'sf_ds', get_rh_v('Sal_Directeurs_Admin', 2000.0), step=100.0)
        tot_f = (sim_ef_eff * sim_ef_sal) + (sim_cf_eff * sim_cf_sal) + (sim_df_eff * sim_df_sal)

    with tab_sim_fin:
        st.subheader("5. Décisions Financières & Situation de l'Entreprise")
        
        donnees_fin_act = charger_donnees_fin_bdd(tour_id_precedent)
        dotations_prev = float(donnees_fin_act.get('Dotations_Amortissements', 0.0))
        dette_bancaire = float(donnees_fin_act.get('Emprunts_Bancaires', 0.0))
        res_net_historique = float(donnees_fin_act.get('Resultat_Net', 0.0))
        treso_initiale = float(donnees_fin_act.get('Disponibilites_Banque', get_h('Tresorerie_Initiale', 0.0)))
        capitaux_propres = float(donnees_fin_act.get('Total_Capitaux_Propres', 0.0))
        
        # Initialisation par défaut sécurisée
        aace_historique = 1104787.0
        deprec_historique = 793362.66 
        report_a_nouveau = 0.0
        resultat_exercice_cumule = res_net_historique
        ca_cumule_historique = 0.0
        res_avant_impot_cumule_historique = 0.0
        bfr_precedent_m1 = 295476.0

        try:
            if engine is not None:
                df_ec = pd.read_sql("SELECT contenu FROM erp_donnees WHERE module = 'expert_comptable' ORDER BY id DESC LIMIT 1", engine)
                if not df_ec.empty:
                    data_ec = json.loads(df_ec.iloc[0]['contenu'])
                    
                    # 1. Extraction stricte des charges fixes réelles depuis le Bilan / Compte de résultat JSON
                    cr_lignes = data_ec.get("Synthèse", {}).get("Compte de résultat détaillé", {}).get("Tableau_1", [])
                    if not cr_lignes:
                        cr_lignes = data_ec.get("Synthèse", {}).get("Compte de résultat simplifié", {}).get("Tableau_1", [])
                    
                    v_aace = 0.0
                    v_deprec = 0.0
                    for row in cr_lignes:
                        libelle = str(row.get("Colonne_0", "")).lower()
                        val_brute = row.get("Colonne_1", row.get("Montants (€)", 0.0))
                        montant = parse_french_float(val_brute)
                        if "autres achats et charges externes" in libelle: v_aace = montant
                        elif "dotations aux dépréciations" in libelle or "autres charges d'exploitation" in libelle: v_deprec += montant

                    if v_aace > 0.0: aace_historique = v_aace
                    if v_deprec > 0.0: deprec_historique = v_deprec
                    
                    # 2. Extraction du BFR et du solde initial M-1
                    tab_treso = data_ec.get("Synthèse", {}).get("Tableau de trésorerie méthode indirecte", {}).get("Tableau_1", [])
                    for row in tab_treso:
                        rub = str(row.get("Rubrique", row.get("Colonne_0", ""))).lower()
                        if "solde de trésorerie final" in rub or "solde trésorerie final" in rub:
                            val_tb = parse_french_float(row.get("Montant (€)", row.get("Colonne_6", 0.0)))
                            if val_tb != 0.0: treso_initiale = val_tb
                        if "résultat net" in rub: res_net_historique = parse_french_float(row.get("Montant (€)", res_net_historique))
                        if "dotations aux amortissements" in rub: dotations_prev = parse_french_float(row.get("Montant (€)", dotations_prev))
                        if "montant du bfr" in rub:
                            val_bfr = parse_french_float(row.get("Colonne_5", row.get("Colonne_6", 0.0)))
                            if val_bfr != 0.0: bfr_precedent_m1 = val_bfr

                    # 3. Extraction des données CUMULÉES réelles depuis le JSON de l'expert-comptable
                    ratios_section = data_ec.get("Synthèse", {}).get("Ratios", {}).get("Ratios et Indicateurs", {})
                    if ratios_section:
                        cumul_list = ratios_section.get("Indicateurs_CUMULÉ", ratios_section.get("Indicateurs_CUMULE", []))
                        if cumul_list and isinstance(cumul_list, list):
                            c_item = cumul_list[0]
                            ca_cumule_historique = parse_french_float(c_item.get("Chiffre d'affaires", 0.0))
                            res_avant_impot_cumule_historique = parse_french_float(c_item.get("Résultat avant impôt", 0.0))
        except Exception: pass

        solde_initial_ep = sim_number("Solde initial", "sim_solde_ep", 1500000.0, step=1000.0)
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
        if ass_rc and ass_db and ass_pe: cout_assurances -= 1000

        div_par_part = sim_number("Dividende versé par part (€)", "sim_div_part", 0.0, step=0.50)
        total_div = div_par_part * 40000

        ms_prev_brute = tot_p + tot_a + tot_f
        charges_sociales_prev = ms_prev_brute * 0.50 
        budget_mkg_prev = budget_pub_marque + sum(budgets_p_dict.values())
        
        dotations_base = dotations_prev if dotations_prev > 0 else 250000.0
        dotations_totales = dotations_base + (cout_invest_machines / 60)

        taux_maintenance_mensuel = 0.005
        maintenance_nouvelles_machines = cout_invest_machines * taux_maintenance_mensuel

        # 🎯 CALCUL MAÎTRE : INCLUSION FORCÉE ET EXPLICITE DES CHARGES FIXES EXTRAITES DU BILAN (1.17 M€)
        total_charges = (
            cout_achats_total_sim + 
            ms_prev_brute + 
            charges_sociales_prev + 
            budget_mkg_prev + 
            dotations_totales + 
            cout_assurances + 
            maintenance_nouvelles_machines + 
            aace_historique + 
            deprec_historique
        )
        
        res_financier = (solde_initial_ep * (0.012 / 12)) - (dette_bancaire * 0.005)
        res_avant_impot = (ca_prev_sim - total_charges) + res_financier
        
        deficit_cumule = report_a_nouveau + resultat_exercice_cumule
        if deficit_cumule > 0: deficit_cumule = 0 
        
        assiette_fiscale = res_avant_impot + deficit_cumule
        impot_is = assiette_fiscale * 0.25 if assiette_fiscale > 0 else 0.0
        res_net_prev = res_avant_impot - impot_is

        taux_profitabilite = (res_avant_impot / ca_prev_sim * 100) if ca_prev_sim > 0 else 0.0
        taux_rentabilite = (res_avant_impot / capitaux_propres * 100) if capitaux_propres > 0 else 0.0

        # 🎯 CALCULS CUMULÉS ROBUSTES ET DISTINCTS
        ca_cumule_sim = ca_cumule_historique + ca_prev_sim
        res_avant_impot_cumule_sim = res_avant_impot_cumule_historique + res_avant_impot
        taux_profitabilite_cumule = (res_avant_impot_cumule_sim / ca_cumule_sim * 100) if ca_cumule_sim > 0 else taux_profitabilite
        taux_rentabilite_cumule = (res_avant_impot_cumule_sim / capitaux_propres * 100) if capitaux_propres > 0 else taux_rentabilite

        achats_titres = (a_a1 * 224.58) + (a_a2 * 200.64) + (a_a3 * 163.84) + (a_o1 * 109.09) + (a_o2 * 114.89) + (a_o3 * 114.78)
        ventes_titres = (v_a1 * 224.58) + (v_a2 * 200.64) + (v_a3 * 163.84) + (v_o1 * 109.09) + (v_o2 * 114.89) + (v_o3 * 114.78)
        
        if "Crédit Fournisseur" in mode_financement_machines:
            decaissement_machines = cout_invest_machines * 0.30 * 1.20
            nouvelle_dette_fournisseur = cout_invest_machines * 0.70 * 1.20
        else:
            decaissement_machines = cout_invest_machines * 1.20
            nouvelle_dette_fournisseur = 0.0

        stock_matieres_prev = cout_achats_total_sim * 0.15
        stock_produits_prev = ca_prev_sim * 0.20
        creances_clients_prev = ca_prev_sim * 0.55
        dettes_fournisseurs_prev = cout_achats_total_sim * 0.40
        
        bfr_simule = stock_matieres_prev + stock_produits_prev + creances_clients_prev - dettes_fournisseurs_prev
        variation_bfr = bfr_simule - bfr_precedent_m1
        
        caf_prev = res_net_prev + dotations_totales + deprec_historique
        flux_treso_exploitation = caf_prev - variation_bfr
        flux_treso_investissement = - decaissement_machines
        flux_treso_financement = ventes_titres - achats_titres - placement_ep + retrait_ep - total_div + nouvelle_dette_fournisseur - 21503.51
        
        treso_finale = treso_initiale + flux_treso_exploitation + flux_treso_investissement + flux_treso_financement
        
        st.divider()
        st.markdown("##### 💶 Synthèse Financière & Situation Globale de l'Entreprise")
        
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.markdown("##### 📈 Produits & Charges")
            st.metric("Chiffre d'Affaires Prévisionnel (CA)", f"{ca_prev_sim:,.2f} €")
            st.metric("Total des Charges d'Exploitation (Fixes + Variables)", f"{total_charges:,.2f} €")
            st.metric("Achat Nouvelles Machines (TTC)", f"{-decaissement_machines:,.2f} €")
            st.metric("Impôt sur les Sociétés (IS)", f"{-impot_is:,.2f} €")
            
        with col_f2:
            st.markdown("##### 🎯 Résultat Prévisionnel")
            st.metric("Résultat Net Mensuel Prévisionnel", f"{res_net_prev:,.2f} €", delta=f"{res_net_prev - res_net_historique:+,.2f} € vs M-1")
            
            st.markdown("##### 📊 Ratios de Performance")
            c_rm1, c_rm2 = st.columns(2)
            c_rm1.metric("Profitabilité Mensuelle", f"{taux_profitabilite:.2f} %")
            c_rm2.metric("Rentabilité Mensuelle", f"{taux_rentabilite:.2f} %")
            
            c_rc1, c_rc2 = st.columns(2)
            c_rc1.metric("Profitabilité CUMULÉE", f"{taux_profitabilite_cumule:.2f} %")
            c_rc2.metric("Rentabilité CUMULÉE", f"{taux_rentabilite_cumule:.2f} %")

        st.divider()
        st.markdown("##### 🏦 Situation de l'Entreprise")
        st.caption(f"Solde Bancaire Initial de référence (M-1) : {treso_initiale:,.2f} €")
        if treso_finale >= 0: 
            st.info(f"Trésorerie Fin de Mois Estimée : **{treso_finale:,.2f} €**")
        else: 
            st.error(f"⚠️ DÉCOUVERT BANCAIRE ESTIMÉ : **{treso_finale:,.2f} €**")
