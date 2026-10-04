import streamlit as st
import pandas as pd
import json
from sqlalchemy import create_engine, text
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright


# ==========================================
# 1. CONFIGURATION ET CONNEXION BDD
# ==========================================
st.set_page_config(page_title="ERP Subakoua - Cockpit Stratégique Global", layout="wide", initial_sidebar_state="expanded")

# Utilisation du coffre-fort (secrets) de Streamlit pour le déploiement
@st.cache_resource
def init_connection():
    # On récupère les identifiants cachés
    user = st.secrets["mysql"]["user"]
    password = st.secrets["mysql"]["password"]
    host = st.secrets["mysql"]["host"]
    database = st.secrets["mysql"]["database"]
    port = st.secrets["mysql"].get("port", 3306) # 3306 par défaut
    
    # Création du moteur de connexion
    return create_engine(f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}")

engine = init_connection()

# ... (le reste du code reste exactement identique) ...

def init_db_simu():
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
    except Exception:
        pass

init_db_simu()

# ==========================================
# 2. CHARGEMENT BDD & FONCTIONS UTILITAIRES
# ==========================================
def parse_french_float(val):
    """Nettoie les nombres au format français (ex: '1 026,00 €') pour Python."""
    if isinstance(val, (int, float)): 
        return float(val)
    try:
        clean_str = str(val).replace(" ", "").replace("\u202f", "").replace("\xa0", "").replace("€", "").replace(",", ".").strip()
        if clean_str == "" or clean_str == "-": 
            return 0.0
        return float(clean_str)
    except:
        return 0.0

@st.cache_data
def charger_nomenclature_bdd():
    try:
        query = "SELECT produit, matiere, coeff_min, coeff_normal, coeff_max FROM Parametres_Nomenclature"
        df = pd.read_sql(query, engine)
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
    except Exception:
        return {}

@st.cache_data
def charger_temps_ateliers_bdd():
    try:
        query = "SELECT produit, atelier, temps_min, temps_normal, temps_max FROM Parametres_TempsAteliers"
        df = pd.read_sql(query, engine)
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
    except Exception:
        return {}

@st.cache_data
def charger_stocks_securite_bdd():
    try:
        query = "SELECT matiere, stock_securite FROM Parametres_StocksSecurite"
        df = pd.read_sql(query, engine)
        return dict(zip(df['matiere'], df['stock_securite']))
    except Exception:
        return {}

@st.cache_data
def charger_parametre_global(nom_param, defaut=0.0):
    try:
        query = f"SELECT valeur FROM Parametres_Globaux WHERE parametre = '{nom_param}'"
        df = pd.read_sql(query, engine)
        if not df.empty:
            return float(df['valeur'].iloc[0])
    except Exception:
        pass
    return float(defaut)

@st.cache_data
def charger_norme_ms(table_name, activite_actuelle):
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
    try:
        col_qualite = f"qualite_{qualite_visee}"
        query = f"SELECT fournisseur, {col_qualite} as prix, delai_mois FROM Parametres_TarifsFournisseurs WHERE matiere = '{matiere}' AND {col_qualite} IS NOT NULL"
        df_tarifs = pd.read_sql(query, engine)
        if df_tarifs.empty: return None
        if critere == "prix": df_tarifs = df_tarifs.sort_values(by=["prix", "delai_mois"], ascending=[True, True])
        elif critere == "delai": df_tarifs = df_tarifs.sort_values(by=["delai_mois", "prix"], ascending=[True, True])
        return df_tarifs.iloc[0].to_dict()
    except Exception: return None

# --- Fonctions DYNAMIQUES sans Cache ---
def charger_donnees_rh_bdd(tour_id):
    try:
        df = pd.read_sql(f"SELECT * FROM Decisions_RH_Mensuel WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_donnees_mkg_bdd(tour_id):
    try:
        df = pd.read_sql(f"SELECT * FROM Decisions_Marketing WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_ventes_bdd(tour_id):
    try:
        df = pd.read_sql(f"SELECT * FROM Ventes_Historique WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def charger_donnees_fin_bdd(tour_id):
    try:
        df = pd.read_sql(f"SELECT * FROM Finances_Mensuelles WHERE Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def lister_scenarios(tour_id):
    try:
        df = pd.read_sql(f"SELECT Token_Seed, Nom_Scenario FROM simulations_seeds WHERE Tour_ID = {tour_id}", engine)
        return df.to_dict('records')
    except Exception: return {}

def charger_scenario_seed(token_seed, tour_id):
    try:
        df = pd.read_sql(f"SELECT * FROM simulations_seeds WHERE Token_Seed = '{token_seed}' AND Tour_ID = {tour_id}", engine)
        if not df.empty: return df.iloc[0].to_dict()
    except Exception: pass
    return {}

def sauvegarder_scenario_seed(token_seed, tour_id, nom, parametres_dict):
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

# --- Moteur Récursif Etat des lieux ---
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
    tabs = st.tabs(noms_onglets)
    for i, nom_onglet in enumerate(noms_onglets):
        with tabs[i]:
            contenu_onglet = donnees_module[nom_onglet]
            if contenu_onglet == "SPECIMEN": st.warning("🚫 Document non acheté (SPECIMEN).")
            else:
                st.markdown("<br>", unsafe_allow_html=True)
                parcourir_structure_json(contenu_onglet, niveau=4)

# --- Utilitaires de simulation sécurisés ---
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

# Global params
nomenclature_dynamique = charger_nomenclature_bdd()
temps_dynamique = charger_temps_ateliers_bdd()
stocks_securite_db = charger_stocks_securite_bdd()
capacite_ref_machine = charger_parametre_global('capacite_min_machine', 9600.0)
ratio_optimum_rh = charger_parametre_global('ratio_structure_optimum', 0.45)

# ==========================================
# 3. FONCTIONS MÉTIER & ALGORITHMES
# ==========================================
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
    "🧠 Simulateur & Décision Stratégique",
    "🕷️ Extracteur Web (Scraper)" # <-- Ajoutez cette ligne
])

# ==========================================
# MODULE 1 : ÉTAT DES LIEUX GLOBAL
# ==========================================
if module_principal == "📊 État des lieux global":
    st.title("📊 Tableaux de Bord & État des Lieux de l'ERP")
    st.info(f"Analyse des données extraites pour la période : **{periode_db}**")
    st.divider()

    try:
        query_erp = f"SELECT module, contenu FROM erp_donnees WHERE periode = '{periode_db}' AND type_donnee = 'etat_actuel'"
        df_erp = pd.read_sql(query_erp, engine)
        modules_disponibles_dict = dict(zip(df_erp['module'], df_erp['contenu'])) if not df_erp.empty else {}
    except Exception:
        modules_disponibles_dict = {}

    if not modules_disponibles_dict:
        st.warning(f"Aucune donnée d'état actuel n'a été trouvée en base de données pour la période **{periode_db}**.")
    else:
        liste_modules_ui = sorted(list(modules_disponibles_dict.keys()))
        module_choisi = st.selectbox("Sélectionnez le département / module à consulter :", liste_modules_ui, format_func=lambda x: x.replace('_', ' ').capitalize())
        
        st.divider()
        donnee_json_brute = modules_disponibles_dict.get(module_choisi, {})
        
        if isinstance(donnee_json_brute, str):
            try: donnees_module = json.loads(donnee_json_brute)
            except Exception: donnees_module = {}
        else: donnees_module = donnee_json_brute

        rendre_module_etat_des_lieux(donnees_module)

# ==========================================
# MODULE 2 : SAISIE DES DONNÉES RÉELLES
# ==========================================
elif module_principal == "📥 Saisie des Données Réelles":
    st.title("📥 Saisie & Alimentation des Données Réelles")
    mois_saisie = st.selectbox("Sélectionnez le mois à renseigner :", list(mois_mapping.keys()))
    tour_saisie = tour_mapping_id[mois_saisie]

    try:
        df_h = pd.read_sql(f"SELECT * FROM Historique_Equipe WHERE Tour_ID = {tour_saisie}", engine)
        df_m = pd.read_sql(f"SELECT * FROM Parc_Machines_Mensuel WHERE Tour_ID = {tour_saisie}", engine)
        d_rh = charger_donnees_rh_bdd(tour_saisie)
        d_mkg = charger_donnees_mkg_bdd(tour_saisie)
        d_ventes = charger_ventes_bdd(tour_saisie)
        dfin = charger_donnees_fin_bdd(tour_saisie)
    except Exception:
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
                with engine.begin() as conn:
                    conn.execute(text(f"INSERT INTO Historique_Equipe (Tour_ID, Stock_Neo3, Stock_Neo5, Stock_Neo7, Stock_Renforts, Stock_Manchons, Stock_Fermetures, Tresorerie_Initiale) VALUES ({tour_saisie}, {s_neo3}, {s_neo5}, {s_neo7}, {s_renf}, {s_man}, {s_ferm}, {treso}) ON DUPLICATE KEY UPDATE Stock_Neo3={s_neo3}, Stock_Neo5={s_neo5}, Stock_Neo7={s_neo7}, Stock_Renforts={s_renf}, Stock_Manchons={s_man}, Stock_Fermetures={s_ferm}, Tresorerie_Initiale={treso}"))
                st.success("Stocks enregistrés !")

    with tab_r2:
        with st.form("f_mach"):
            st.subheader("Saisie du Parc Machines")
            m_dec = st.number_input("Découpe", value=get_m('Nb_Machines_Decoupe', 8))
            m_ass = st.number_input("Assemblage", value=get_m('Nb_Machines_Assemblage', 13))
            m_cond = st.number_input("Conditionnement", value=get_m('Nb_Machines_Cond', 3))
            if st.form_submit_button("Enregistrer les Machines"):
                with engine.begin() as conn:
                    conn.execute(text(f"INSERT INTO Parc_Machines_Mensuel (Tour_ID, Mois, Nb_Machines_Decoupe, Nb_Machines_Assemblage, Nb_Machines_Cond) VALUES ({tour_saisie}, '{mois_saisie}', {m_dec}, {m_ass}, {m_cond}) ON DUPLICATE KEY UPDATE Nb_Machines_Decoupe={m_dec}, Nb_Machines_Assemblage={m_ass}, Nb_Machines_Cond={m_cond}"))
                st.success("Machines enregistrées !")

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
                st.markdown("**Budgets PLV (Publicité sur Lieu de Vente)**")
                plv_s3 = st.number_input("PLV S3", value=float(d_mkg.get('Pub_S3', 3250.0)))
                plv_i3 = st.number_input("PLV I3", value=float(d_mkg.get('Pub_I3', 4200.0)))
                plv_s5 = st.number_input("PLV S5", value=float(d_mkg.get('Pub_S5', 3400.0)))
                plv_i5 = st.number_input("PLV I5", value=float(d_mkg.get('Pub_I5', 4300.0)))
                plv_i7 = st.number_input("PLV I7", value=float(d_mkg.get('Pub_I7', 3500.0)))
            
            if st.form_submit_button("💾 Enregistrer les Décisions Marketing"):
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
            v_s3 = c_v1.number_input("Ventes Shorty 3 (u)", value=int(d_ventes.get('Ventes_S3', 0)), step=1)
            v_i3 = c_v2.number_input("Ventes Integral 3 (u)", value=int(d_ventes.get('Ventes_I3', 0)), step=1)
            v_s5 = c_v3.number_input("Ventes Shorty 5 (u)", value=int(d_ventes.get('Ventes_S5', 0)), step=1)
            v_i5 = c_v4.number_input("Ventes Integral 5 (u)", value=int(d_ventes.get('Ventes_I5', 0)), step=1)
            v_i7 = c_v5.number_input("Ventes Integral 7 (u)", value=int(d_ventes.get('Ventes_I7', 0)), step=1)
            
            if st.form_submit_button("💾 Enregistrer les Volumes Vendus"):
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

    try:
        df_hist = pd.read_sql(f"SELECT * FROM Historique_Equipe WHERE Tour_ID = {tour_id_precedent}", engine)
        df_machines = pd.read_sql(f"SELECT * FROM Parc_Machines_Mensuel WHERE Tour_ID = {tour_id_precedent}", engine)
        donnees_rh = charger_donnees_rh_bdd(tour_id_precedent)
        donnees_mkg = charger_donnees_mkg_bdd(tour_id_precedent)
    except Exception:
        df_hist, df_machines, donnees_rh, donnees_mkg = pd.DataFrame(), pd.DataFrame(), {}, {}

    def get_h(col, def_v=0.0): return float(df_hist[col].iloc[0]) if not df_hist.empty and col in df_hist.columns else def_v
    def get_m(col, def_v=0): return int(df_machines[col].iloc[0]) if not df_machines.empty and col in df_machines.columns else def_v
    def get_rh_v(k, def_v): return float(donnees_rh.get(k, def_v))
    def get_rh_e(k, def_v): return int(donnees_rh.get(k, def_v))

    score_rh_prod = calculer_score_rh_prod_dynamique(donnees_rh, ratio_optimum_rh)

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
        
        st.markdown("##### 🛒 Investissements (Achat de nouvelles machines)")
        nb_m_dec_actuel = get_m('Nb_Machines_Decoupe', 8)
        nb_m_ass_actuel = get_m('Nb_Machines_Assemblage', 13)
        nb_m_cond_actuel = get_m('Nb_Machines_Cond', 3)
        
        col_m1, col_m2, col_m3 = st.columns(3)
        achat_dec = sim_number(f"Achat Découpe (8 000€/u) - Actuel: {nb_m_dec_actuel}", "sim_ach_dec", 0)
        achat_ass = sim_number(f"Achat Assemblage (8 000€/u) - Actuel: {nb_m_ass_actuel}", "sim_ach_ass", 0)
        achat_cond = sim_number(f"Achat Conditionnement (6 000€/u) - Actuel: {nb_m_cond_actuel}", "sim_ach_cond", 0)
        
        nb_m_dec_sim = nb_m_dec_actuel + achat_dec
        nb_m_ass_sim = nb_m_ass_actuel + achat_ass
        nb_m_cond_sim = nb_m_cond_actuel + achat_cond
        cout_invest_machines = (achat_dec * 8000) + (achat_ass * 8000) + (achat_cond * 6000)

        st.divider()

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

        col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
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

        for mat, b_brut in besoins_bruts.items():
            s_ini = stocks_initiaux[mat]
            secu = b_brut * (coeff_stock_secu / 10.0)
            b_net = max(0.0, (b_brut + secu) - s_ini)

            mrp_lignes.append({"Matière": mat, "Stock Initial": s_ini, "Besoins Prod": b_brut, "Stock Sécurité": secu, "👉 Besoin Net": b_net})

            if b_net > 0:
                opt = optimiser_fournisseur_matiere(mat, qualite_visee=qualite_strategique, critere=critere_arbitrage)
                if opt:
                    cout_ligne = b_net * opt['prix']
                    cout_achats_total_sim += cout_ligne
                    recos_achats.append({"Matière": mat, "Besoin Net": b_net, "Fournisseur": opt['fournisseur'], "Délai (mois)": opt['delai_mois'], "Prix Unitaire": opt['prix'], "Coût Total Estimé": cout_ligne})

        st.markdown("##### 📊 Tableau MRP2")
        st.dataframe(pd.DataFrame(mrp_lignes).set_index("Matière").style.format("{:.2f}"), use_container_width=True)
        if recos_achats:
            st.markdown("##### 💡 Recommandations d'Achats")
            st.dataframe(pd.DataFrame(recos_achats).set_index("Matière").style.format({"Besoin Net": "{:.2f}", "Prix Unitaire": "{:.2f} €", "Coût Total Estimé": "{:,.2f} €"}), use_container_width=True)

    with tab_sim_rh:
        st.subheader("4. Pilotage RH Interconnecté (Pyramide Hiérarchique & Normes BDD)")
        def calculer_indice_structure(ms_emp, ms_cad, ms_dir, cible):
            return (ms_cad + ms_dir) / ms_emp / cible if ms_emp > 0 else 2.0

        c_rh1, c_rh2, c_rh3 = st.columns(3)
        with c_rh1:
            st.markdown("##### 🏭 Production")
            sim_ep_eff = sim_number("Nb Employés", 'sp_e', get_rh_e('Eff_Employes_Prod', 15))
            sim_ep_sal = sim_number("Sal. Employé (€)", 'sp_es', get_rh_v('Sal_Employes_Prod', 2000.0), step=100.0)
            sim_cp_eff = sim_number("Nb Cadres", 'sp_c', get_rh_e('Eff_Cadres_Prod', 3))
            sim_cp_sal = sim_number("Sal. Cadre (€)", 'sp_cs', get_rh_v('Sal_Cadres_Prod', 3333.33), step=100.0)
            sim_dp_eff = sim_number("Nb Directeurs", 'sp_d', get_rh_e('Eff_Directeurs_Prod', 1))
            sim_dp_sal = sim_number("Sal. Directeur (€)", 'sp_ds', get_rh_v('Sal_Directeurs_Prod', 4500.0), step=100.0)
            
            tot_p = (sim_ep_eff * sim_ep_sal) + (sim_cp_eff * sim_cp_sal) + (sim_dp_eff * sim_dp_sal)
            ind_p = calculer_indice_structure(sim_ep_eff*sim_ep_sal, sim_cp_eff*sim_cp_sal, sim_dp_eff*sim_dp_sal, ratio_optimum_rh)
            couts_prod_sim = sum(ordres_prod_dict.values()) * 150.0
            ms_satisfaisante_prod = charger_norme_ms("Normes_MS_Prod", couts_prod_sim)
            
            s_ms_p = max(0.0, min(100.0, 100.0 - (abs(tot_p - ms_satisfaisante_prod) / max(ms_satisfaisante_prod, 1.0)) * 100))
            s_disp_p = max(0.0, 100.0 - abs(1.0 - ind_p) * 200.0)
            st.metric("Score MS Prod", f"{(s_ms_p+s_disp_p)/2.0:.2f} / 100", delta=f"Norme MS: {ms_satisfaisante_prod:,.0f} €")

        with c_rh2:
            st.markdown("##### 📦 Approvisionnement")
            sim_ea_eff = sim_number("Nb Employés", 'sa_e', get_rh_e('Eff_Employes_Appro', 7))
            sim_ea_sal = sim_number("Sal. Employé (€)", 'sa_es', get_rh_v('Sal_Employes_Appro', 2000.0), step=100.0)
            sim_ca_eff = sim_number("Nb Cadres", 'sa_c', get_rh_e('Eff_Cadres_Appro', 2))
            sim_ca_sal = sim_number("Sal. Cadre (€)", 'sa_cs', get_rh_v('Sal_Cadres_Appro', 2250.0), step=100.0)
            sim_da_eff = sim_number("Nb Directeurs", 'sa_d', get_rh_e('Eff_Directeurs_Appro', 1))
            sim_da_sal = sim_number("Sal. Directeur (€)", 'sa_ds', get_rh_v('Sal_Directeurs_Appro', 2000.0), step=100.0)
            
            tot_a = (sim_ea_eff * sim_ea_sal) + (sim_ca_eff * sim_ca_sal) + (sim_da_eff * sim_da_sal)
            ind_a = calculer_indice_structure(sim_ea_eff*sim_ea_sal, sim_ca_eff*sim_ca_sal, sim_da_eff*sim_da_sal, ratio_optimum_rh)
            ms_satisfaisante_appro = charger_norme_ms("Normes_MS_Appro", cout_achats_total_sim)
            
            s_ms_a = max(0.0, min(100.0, 100.0 - (abs(tot_a - ms_satisfaisante_appro) / max(ms_satisfaisante_appro, 1.0)) * 100))
            s_disp_a = max(0.0, 100.0 - abs(1.0 - ind_a) * 200.0)
            st.metric("Score MS Appro", f"{(s_ms_a+s_disp_a)/2.0:.2f} / 100", delta=f"Norme MS: {ms_satisfaisante_appro:,.0f} €")

        with c_rh3:
            st.markdown("##### 💶 Administration")
            sim_ef_eff = sim_number("Nb Employés", 'sf_e', get_rh_e('Eff_Employes_Admin', 7))
            sim_ef_sal = sim_number("Sal. Employé (€)", 'sf_es', get_rh_v('Sal_Employes_Admin', 2000.0), step=100.0)
            sim_cf_eff = sim_number("Nb Cadres", 'sf_c', get_rh_e('Eff_Cadres_Admin', 2))
            sim_cf_sal = sim_number("Sal. Cadre (€)", 'sf_cs', get_rh_v('Sal_Cadres_Admin', 2200.0), step=100.0)
            sim_df_eff = sim_number("Nb Directeurs", 'sf_d', get_rh_e('Eff_Directeurs_Admin', 1))
            sim_df_sal = sim_number("Sal. Directeur (€)", 'sf_ds', get_rh_v('Sal_Directeurs_Admin', 2000.0), step=100.0)
            
            tot_f = (sim_ef_eff * sim_ef_sal) + (sim_cf_eff * sim_cf_sal) + (sim_df_eff * sim_df_sal)
            ind_f = calculer_indice_structure(sim_ef_eff*sim_ef_sal, sim_cf_eff*sim_cf_sal, sim_df_eff*sim_df_sal, ratio_optimum_rh)
            ms_satisfaisante_admin = charger_norme_ms("Normes_MS_Admin", ca_prev_sim)
            
            s_ms_f = max(0.0, min(100.0, 100.0 - (abs(tot_f - ms_satisfaisante_admin) / max(ms_satisfaisante_admin, 1.0)) * 100))
            s_disp_f = max(0.0, 100.0 - abs(1.0 - ind_f) * 200.0)
            st.metric("Score MS Admin", f"{(s_ms_f+s_disp_f)/2.0:.2f} / 100", delta=f"Norme MS: {ms_satisfaisante_admin:,.0f} €")

    with tab_sim_fin:
        st.subheader("5. Décisions Financières & Situation de l'Entreprise")
        
        # --- RECUPERATION 100% BDD (M-1) ---
        donnees_fin_act = charger_donnees_fin_bdd(tour_id_precedent)
        dotations_prev = float(donnees_fin_act.get('Dotations_Amortissements', 0.0))
        dette_bancaire = float(donnees_fin_act.get('Emprunts_Bancaires', 0.0))
        res_net_historique = float(donnees_fin_act.get('Resultat_Net', 0.0))
        treso_initiale = float(donnees_fin_act.get('Disponibilites_Banque', get_h('Tresorerie_Initiale', 0.0)))
        capitaux_propres = float(donnees_fin_act.get('Total_Capitaux_Propres', 0.0))
        
        report_a_nouveau = 0.0
        resultat_exercice_cumule = res_net_historique
        
        ca_cumule_historique = 0.0
        res_avant_impot_cumule_historique = 0.0

        # 🧠 SMART BRIDGE BDD : Aspiration robuste depuis les tables erp_donnees
        try:
            periode_m1_nom_ui = [k for k, v in tour_mapping_id.items() if v == tour_id_precedent][0]
            periode_m1_db = mois_mapping[periode_m1_nom_ui]
            
            # 1. Trésorerie Banque
            df_bq = pd.read_sql(f"SELECT contenu FROM erp_donnees WHERE periode = '{periode_m1_db}' AND module = 'banque_assurance' AND type_donnee = 'etat_actuel'", engine)
            if not df_bq.empty:
                data_bq = json.loads(df_bq.iloc[0]['contenu'])
                if "Soldes bancaires" in data_bq:
                    for acc in data_bq["Soldes bancaires"]:
                        if "Solde final" in acc:
                            treso_initiale = parse_french_float(acc["Solde final"])
            
            # 2. Données Bilan & Compte de Résultat (Expert-Comptable)
            df_ec = pd.read_sql(f"SELECT contenu FROM erp_donnees WHERE periode = '{periode_m1_db}' AND module = 'expert_comptable' AND type_donnee = 'etat_actuel'", engine)
            if not df_ec.empty:
                data_ec = json.loads(df_ec.iloc[0]['contenu'])
                
                tab_treso = data_ec.get("Synthèse", {}).get("Tableau de trésorerie méthode indirecte", {}).get("Tableau_1", [])
                for row in tab_treso:
                    rub = str(row.get("Rubrique", "")).lower()
                    if "résultat net" in rub:
                        res_net_historique = parse_french_float(row.get("Montant (€)", res_net_historique))
                    if "dotations aux amortissements" in rub:
                        dotations_prev = parse_french_float(row.get("Montant (€)", dotations_prev))
                    if "solde de trésorerie final" in rub or "solde de tresorerie final" in rub:
                        treso_initiale = parse_french_float(row.get("Montant (€)", treso_initiale))
                        
                tab_bilan = data_ec.get("Synthèse", {}).get("Bilan détaillé", {}).get("Tableau_1", [])
                for row in tab_bilan:
                    pas = str(row.get("Passif", "")).lower()
                    act = str(row.get("Actif", "")).lower()
                    
                    if "total capitaux propres" in pas:
                        val_cap = parse_french_float(row.get("Exercice N", 0.0))
                        if val_cap != 0.0: capitaux_propres = val_cap
                    if "report à nouveau" in pas or "report a nouveau" in pas:
                        val_ran = parse_french_float(row.get("Exercice N", 0.0))
                        if val_ran != 0.0: report_a_nouveau = val_ran
                    if "résultat de l'exercice" in pas or "resultat de l'exercice" in pas:
                        val_res = parse_french_float(row.get("Exercice N", 0.0))
                        if val_res != 0.0: resultat_exercice_cumule = val_res
                        
                    # NOUVEAU : Fallback infaillible pour la trésorerie via le Bilan Actif
                    if act.strip() == "banque" or "disponibilités" in act:
                        val_bq = parse_french_float(row.get("Net exercice N", row.get("Exercice N", 0.0)))
                        if val_bq != 0.0 and treso_initiale == 0.0: 
                            treso_initiale = val_bq

                # 3. Extraction intelligente de tous les tableaux pour dénicher les cumuls
                def scan_cumuls_recur(node):
                    ca, res = 0.0, 0.0
                    if isinstance(node, dict):
                        for k, v in node.items():
                            if "CUMULÉ" in str(k).upper() or "CUMULE" in str(k).upper():
                                if isinstance(v, list) and len(v) > 0:
                                    item = v[0]
                                    if "Chiffre d'affaires" in item: ca = parse_french_float(item["Chiffre d'affaires"])
                                    if "Résultat avant impôt" in item: res = parse_french_float(item["Résultat avant impôt"])
                                    return ca, res
                            sub_ca, sub_res = scan_cumuls_recur(v)
                            if sub_ca != 0.0: ca = sub_ca
                            if sub_res != 0.0: res = sub_res
                    elif isinstance(node, list):
                        for item in node:
                            if isinstance(item, dict):
                                rub = str(item.get("Rubrique", item.get("Libellé", ""))).lower()
                                if "chiffre d'affaires" in rub:
                                    for col_k, col_v in item.items():
                                        if "cumul" in str(col_k).lower() or "exercice" in str(col_k).lower():
                                            c_val = parse_french_float(col_v)
                                            if c_val > ca: ca = c_val
                                if "résultat avant impôt" in rub or "resultat avant impot" in rub:
                                    for col_k, col_v in item.items():
                                        if "cumul" in str(col_k).lower() or "exercice" in str(col_k).lower():
                                            r_val = parse_french_float(col_v)
                                            if r_val != 0.0: res = r_val
                    return ca, res

                c_json, r_json = scan_cumuls_recur(data_ec)
                if c_json != 0.0: ca_cumule_historique = c_json
                if r_json != 0.0: res_avant_impot_cumule_historique = r_json
                        
        except Exception:
            pass

        # 4. SÉCURITÉ SQL : Si le JSON ne remonte rien, on somme la table Finances_Mensuelles
        if ca_cumule_historique == 0.0 and tour_id_precedent > 1:
            try:
                df_sql_hist = pd.read_sql(f"SELECT SUM(CA_Net) as sum_ca, SUM(Resultat_Net) as sum_res FROM Finances_Mensuelles WHERE Tour_ID <= {tour_id_precedent}", engine)
                if not df_sql_hist.empty:
                    val_sca = df_sql_hist['sum_ca'].iloc[0]
                    val_sres = df_sql_hist['sum_res'].iloc[0]
                    if val_sca is not None: ca_cumule_historique = float(val_sca)
                    if val_sres is not None: res_avant_impot_cumule_historique = float(val_sres)
            except Exception:
                pass

        st.markdown("##### 📁 Saisie des Décisions Administration / Finance")
        tab_f1, tab_f2, tab_f3, tab_f4 = st.tabs(["Compte épargne", "Ordres de bourse", "Assurances", "Actionnariat"])
        
        with tab_f1:
            st.info("Le compte épargne permet de placer la trésorerie au taux de 1,20 %. Un placement ne génère des intérêts qu'au mois suivant.")
            c_e1, c_e2, c_e3 = st.columns(3)
            solde_initial_ep = sim_number("Solde initial", "sim_solde_ep", 1500000.0, step=1000.0)
            placement_ep = sim_number("Placement", "sim_plac_ep", 0.0, step=1000.0)
            retrait_ep = sim_number("Retrait", "sim_retr_ep", 0.0, step=1000.0)
            nouveau_solde_epargne = solde_initial_ep + placement_ep - retrait_ep
            st.metric("Nouveau solde compte épargne", f"{nouveau_solde_epargne:,.2f} €")

        with tab_f2:
            st.info("Saisissez le nombre d'unités (u) achetées ou vendues.")
            c_b1, c_b2 = st.columns(2)
            with c_b1:
                st.markdown("**Actions**")
                a_a1 = sim_number("Achat A1 (224,58 €)", "sim_ach_a1", 0)
                v_a1 = sim_number("Vente A1", "sim_ven_a1", 0)
                a_a2 = sim_number("Achat A2 (200,64 €)", "sim_ach_a2", 0)
                v_a2 = sim_number("Vente A2", "sim_ven_a2", 0)
                a_a3 = sim_number("Achat A3 (163,84 €)", "sim_ach_a3", 0)
                v_a3 = sim_number("Vente A3", "sim_ven_a3", 0)
            with c_b2:
                st.markdown("**Obligations**")
                a_o1 = sim_number("Achat O1 (109,09 €)", "sim_ach_o1", 0)
                v_o1 = sim_number("Vente O1", "sim_ven_o1", 0)
                a_o2 = sim_number("Achat O2 (114,89 €)", "sim_ach_o2", 0)
                v_o2 = sim_number("Vente O2", "sim_ven_o2", 0)
                a_o3 = sim_number("Achat O3 (114,78 €)", "sim_ach_o3", 0)
                v_o3 = sim_number("Vente O3", "sim_ven_o3", 0)

        with tab_f3:
            st.info("Les contrats sont reconduits par tacite reconduction.")
            c_a1, c_a2, c_a3 = st.columns(3)
            ass_rc = sim_checkbox("Responsabilité civile (2 000,00 €)", "sim_ass_rc", True)
            ass_db = sim_checkbox("Dommages aux biens (2 000,00 €)", "sim_ass_db", True)
            ass_pe = sim_checkbox("Pertes d'exploitation (2 000,00 €)", "sim_ass_pe", True)
            
            cout_assurances = 0
            if ass_rc: cout_assurances += 2000
            if ass_db: cout_assurances += 2000
            if ass_pe: cout_assurances += 2000
            if ass_rc and ass_db and ass_pe:
                cout_assurances -= 1000
                st.success("Remise de 1 000,00 € appliquée pour la souscription aux 3 contrats.")
            st.metric("Total des contrats d'assurance", f"{cout_assurances:,.2f} €")

        with tab_f4:
            st.info("Capital Initial: 4 000 000,00 € | Nombre de parts Initial: 40 000 parts | Prix d'une part: 100,00 €")
            div_par_part = sim_number("Dividende versé par part (€)", "sim_div_part", 0.0, step=0.50)
            total_div = div_par_part * 40000
            st.metric("Total dividendes versés", f"{total_div:,.2f} €")

        st.divider()
        st.markdown("##### 💶 Synthèse Financière & Situation Globale de l'Entreprise")
        
        masse_salariale_prod = tot_p if 'tot_p' in locals() else 44500.0
        masse_salariale_appro = tot_a if 'tot_a' in locals() else 20500.0
        masse_salariale_admin = tot_f if 'tot_f' in locals() else 20400.0
        ms_prev_brute = masse_salariale_prod + masse_salariale_appro + masse_salariale_admin

        charges_sociales_prev = ms_prev_brute * 0.50 
        budget_mkg_prev = budget_pub_marque + sum(budgets_p_dict.values())
        
        amortissement_nouveau = cout_invest_machines / 60
        dotations_totales = dotations_prev + amortissement_nouveau

        total_charges = cout_achats_total_sim + ms_prev_brute + charges_sociales_prev + budget_mkg_prev + dotations_totales + cout_assurances
        rex_prev = ca_prev_sim - total_charges
        
        interets_epargne = solde_initial_ep * (0.012 / 12) 
        frais_financiers = dette_bancaire * 0.005 
        res_financier = interets_epargne - frais_financiers
        
        res_avant_impot = rex_prev + res_financier
        
        deficit_cumule = report_a_nouveau + resultat_exercice_cumule
        if deficit_cumule > 0: deficit_cumule = 0 
        
        assiette_fiscale = res_avant_impot + deficit_cumule
        impot_is = assiette_fiscale * 0.25 if assiette_fiscale > 0 else 0.0
        
        res_net_prev = res_avant_impot - impot_is

        # --- CALCULS DES RATIOS (BDD PURE) ---
        taux_profitabilite = (res_avant_impot / ca_prev_sim * 100) if ca_prev_sim > 0 else 0.0
        taux_rentabilite = (res_avant_impot / capitaux_propres * 100) if capitaux_propres > 0 else 0.0

        ca_cumule_sim = ca_cumule_historique + ca_prev_sim
        res_avant_impot_cumule_sim = res_avant_impot_cumule_historique + res_avant_impot
        
        taux_profitabilite_cumule = (res_avant_impot_cumule_sim / ca_cumule_sim * 100) if ca_cumule_sim > 0 else 0.0
        taux_rentabilite_cumule = (res_avant_impot_cumule_sim / capitaux_propres * 100) if capitaux_propres > 0 else 0.0

        achats_titres = (a_a1 * 224.58) + (a_a2 * 200.64) + (a_a3 * 163.84) + (a_o1 * 109.09) + (a_o2 * 114.89) + (a_o3 * 114.78)
        ventes_titres = (v_a1 * 224.58) + (v_a2 * 200.64) + (v_a3 * 163.84) + (v_o1 * 109.09) + (v_o2 * 114.89) + (v_o3 * 114.78)
        
        delta_ca = ca_prev_sim - (ca_cumule_historique / max(1, tour_id_precedent - 1)) if tour_id_precedent > 1 else ca_prev_sim
        variation_bfr = delta_ca * 0.15
        
        flux_financier = ventes_titres - achats_titres - placement_ep + retrait_ep - total_div
        flux_investissement = - cout_invest_machines
        flux_exploitation = res_net_prev + dotations_totales - variation_bfr
        treso_finale = treso_initiale + flux_exploitation + flux_financier + flux_investissement
        
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.markdown("##### 📈 Produits d'Exploitation")
            st.metric("Chiffre d'Affaires Prévisionnel (CA)", f"{ca_prev_sim:,.2f} €")
            st.markdown("##### 📉 Charges d'Exploitation")
            st.metric("Achats Matières (MRP2)", f"{cout_achats_total_sim:,.2f} €")
            st.metric("Budget Marketing & PLV", f"{budget_mkg_prev:,.2f} €")
            st.metric("Rémunération du personnel (Brut)", f"{ms_prev_brute:,.2f} €")
            st.metric("Charges sociales patronales (50%)", f"{charges_sociales_prev:,.2f} €")
            st.metric("Frais d'Assurances", f"{cout_assurances:,.2f} €")
            st.metric("Amortissements (Anciens + Nouveaux)", f"{dotations_totales:,.2f} €")
            st.metric("Total des Charges d'Exploitation", f"{total_charges:,.2f} €")
            
            st.markdown("##### 🛒 Investissements (Décaissements)")
            st.metric("Achat Nouvelles Machines", f"{flux_investissement:,.2f} €")
            
        with col_f2:
            st.markdown("##### 🎯 Résultat Prévisionnel")
            st.metric("Résultat d'Exploitation (REX)", f"{rex_prev:,.2f} €")
            st.metric("Résultat Financier (Estimé)", f"{res_financier:,.2f} €")
            
            if deficit_cumule < 0:
                st.caption(f"🛡️ *Bouclier fiscal actif : Pertes cumulées de {deficit_cumule:,.2f} € à éponger avant impôt.*")
            
            if impot_is > 0:
                st.metric("Impôt sur les Sociétés (25%)", f"- {impot_is:,.2f} €")
            else:
                st.metric("Impôt sur les Sociétés", "0.00 € (Exonéré ou Déficit)")
            
            delta_res_net = res_net_prev - res_net_historique
            st.metric(
                "Résultat Net Prévisionnel", 
                f"{res_net_prev:,.2f} €", 
                delta=f"{delta_res_net:+,.2f} € vs M-1", 
                delta_color="normal"
            )
            
            st.markdown("##### 📊 Répartition des Charges Prévisionnelles")
            df_charts = pd.DataFrame({
                "Poste": ["Achats", "Marketing", "Salaires + Charges", "Assurances", "Amortissements"],
                "Montant (€)": [cout_achats_total_sim, budget_mkg_prev, ms_prev_brute + charges_sociales_prev, cout_assurances, dotations_totales]
            }).set_index("Poste")
            st.bar_chart(df_charts)

            st.markdown("##### 📊 Ratios de Performance")
            tab_rat_mensuel, tab_rat_cumule = st.tabs(["Indicateurs Mensuels", "Indicateurs Cumulés"])
            
            with tab_rat_mensuel:
                c_rm1, c_rm2 = st.columns(2)
                c_rm1.metric("Taux de Profitabilité", f"{taux_profitabilite:.2f} %", help="Résultat avant impôt mensuel / CA mensuel")
                c_rm2.metric("Taux de Rentabilité", f"{taux_rentabilite:.2f} %", help="Résultat avant impôt mensuel / Capitaux propres")
                
            with tab_rat_cumule:
                c_rc1, c_rc2 = st.columns(2)
                c_rc1.metric("Taux de Profitabilité (Cumulé)", f"{taux_profitabilite_cumule:.2f} %", help="Résultat avant impôt cumulé / CA cumulé")
                c_rc2.metric("Taux de Rentabilité (Cumulé)", f"{taux_rentabilite_cumule:.2f} %", help="Résultat avant impôt cumulé / Capitaux propres")
                st.caption(f"CA Cumulé M-1: {ca_cumule_historique:,.2f} € | Res. Avant Impôt Cumulé M-1: {res_avant_impot_cumule_historique:,.2f} €")

            st.markdown("##### 🏦 Situation de l'Entreprise & BFR")
            st.write(f"**Trésorerie Initiale (Mois M-1) :** {treso_initiale:,.2f} €")
            st.write(f"**Cash-Flow (Résultat Net + Amortissements) :** {flux_exploitation + variation_bfr:,.2f} €")
            st.write(f"**Variation estimée du BFR :** {-variation_bfr:,.2f} €")
            st.write(f"**Investissements (Machines) :** {flux_investissement:,.2f} €")
            st.write(f"**Mouvements Hors-Exploitation :** {flux_financier:,.2f} €")
            
            if treso_finale >= 0:
                st.info(f"Trésorerie Fin de Mois Estimée : **{treso_finale:,.2f} €**")
            else:
                st.error(f"⚠️ DÉCOUVERT BANCAIRE ESTIMÉ : **{treso_finale:,.2f} €**")

            st.markdown("##### 🤖 Audit & Conseil Stratégique IA")
            conseils = []
            if treso_finale < 0:
                conseils.append("🔴 **Alerte Trésorerie :** Vos choix mènent à un découvert bancaire. Envisagez de réduire vos investissements en machines ou d'augmenter vos prix.")
            if taux_profitabilite < 0:
                conseils.append("⚠️ **Rentabilité Mensuelle Négative :** Vos charges dépassent votre chiffre d'affaires ce mois-ci. Vérifiez vos volumes de production et vos dépenses marketing.")
            if deficit_cumule < 0 and res_net_prev > 0:
                conseils.append("🛡️ **Optimisation Fiscale :** Le bouclier fiscal absorbe vos impôts ce mois-ci grâce aux pertes passées. Profitez-en pour consolider vos marges.")
            if not conseils:
                conseils.append("✅ **Situation Stable :** Vos voyants financiers sont au vert. La trajectoire de rentabilité et la trésorerie sont saines.")
            
            for c in conseils:
                st.markdown(c)

# ==========================================
# MODULE 4 : EXTRACTEUR WEB (SCRAPER CLOUD)
# ==========================================
elif module_principal == "🕷️ Extracteur Web (Scraper)":
    st.title("🕷️ Centre de Contrôle du Scraper Subakoua")
    st.info("Lancez le robot d'aspiration directement depuis les serveurs Cloud. L'opération prendra quelques dizaines de secondes.")

    # --- PARAMÈTRES ET CONSTANTES DU SCRAPER ---
    URL_CONNEXION = "https://login.arkhe.com/" 
    URL_DASHBOARD = "https://subakoua.arkhe.com/companies"
    DOMAINE_BASE = f"https://{urlparse(URL_DASHBOARD).netloc}"

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
        ("Fournisseurs", "fournisseurs")
    ]

    JS_EXTRACTEUR_UNIVERSEL = """
    () => {
        let data = {};
        
        // 1. EXTRACTION DES FORMULAIRES DE DÉCISION
        document.querySelectorAll('okw-base-block').forEach(bloc => {
            let serviceTitleEl = bloc.querySelector('.subSectionTitle-subtitle');
            let sectionName = serviceTitleEl ? serviceTitleEl.innerText.trim() : "Général";
            let sectionData = {};
            
            bloc.querySelectorAll('.formBlock, .formProductBlock').forEach(row => {
                let titleEl = row.querySelector('.formBlock-title');
                if(!titleEl) return;
                let title = titleEl.innerText.trim();
                let values = [];
                
                row.querySelectorAll('ui-labeled-data, ui-input-number').forEach(cell => {
                    let input = cell.querySelector('input');
                    if (input && input.hasAttribute('aria-valuenow')) {
                        values.push(parseFloat(input.getAttribute('aria-valuenow') || 0));
                    } else {
                        let text = cell.innerText.replace(/[^0-9,-]/g, '').replace(',', '.');
                        if(text !== "") values.push(parseFloat(text));
                        else values.push(0);
                    }
                });
                
                let ratings = Array.from(row.querySelectorAll('ui-rating')).map(rating => {
                    let score = 0;
                    rating.querySelectorAll('svg').forEach(svg => {
                        let paths = svg.querySelectorAll('path');
                        if (paths.length === 1 && paths[0].getAttribute('fill') === '#FDB022') score += 1;   
                        else if (svg.querySelector('path[clip-rule="evenodd"][fill="#FDB022"]')) score += 0.5; 
                    });
                    return score;
                });
                
                if(ratings.length > 0) sectionData[title + " (Notes)"] = ratings;
                if(values.length > 0) sectionData[title] = values;
            });
            
            bloc.querySelectorAll('.grid-x.cell.auto').forEach(row => {
                if (!row.closest('.formBlock') && !row.closest('.formProductBlock')) {
                    let labelEl = row.querySelector('.uiLabel-text');
                    let valueEl = row.querySelector('.ui-tag-info, .ui-tag-label, ._number');
                    if(labelEl && valueEl) {
                        let label = labelEl.innerText.trim();
                        let valText = valueEl.innerText.replace(/[^0-9,-]/g, '').replace(',', '.');
                        if (valText !== "") sectionData[label] = parseFloat(valText);
                        else sectionData[label] = 0;
                    }
                }
            });
            
            if (Object.keys(sectionData).length > 0) {
                data[sectionName] = sectionData;
            }
        });

        // 2. EXTRACTION DES TABLEAUX CLASSIQUES
        document.querySelectorAll('table').forEach((table, index) => {
            let titleEl = table.closest('.card, div').querySelector('.subSectionTitle-subtitle, .headerMainCard-title');
            let tableName = titleEl ? titleEl.innerText.trim() : "Tableau_" + (index + 1);

            let headers = Array.from(table.querySelectorAll('thead th')).map(th => th.innerText.trim());
            let tableData = [];
            
            table.querySelectorAll('tbody tr').forEach(tr => {
                let rowData = {};
                Array.from(tr.querySelectorAll('td')).forEach((cell, i) => {
                    let colName = headers[i] || 'Colonne_' + i;
                    let rawText = cell.innerText.trim();
                    let numText = rawText.replace(/[^0-9,-]/g, '').replace(',', '.');
                    
                    if (numText !== "" && !isNaN(numText) && rawText.match(/[0-9]/)) {
                        rowData[colName] = parseFloat(numText);
                    } else {
                        rowData[colName] = rawText;
                    }
                });
                if (Object.keys(rowData).length > 0) tableData.push(rowData);
            });
            
            if (tableData.length > 0) {
                data[tableName] = tableData;
            }
        });
        
        // 3. EXTRACTION DES RATIOS & KPI
        document.querySelectorAll('okw-base-block').forEach(bloc => {
            let serviceTitleEl = bloc.querySelector('.subSectionTitle-subtitle, .headerMainCard-title');
            let sectionName = serviceTitleEl ? serviceTitleEl.innerText.trim() : "Ratios et Indicateurs";
            
            let walker = document.createTreeWalker(bloc, NodeFilter.SHOW_TEXT, null, false);
            let node;
            let texts = [];
            while(node = walker.nextNode()) {
                let t = node.nodeValue.trim();
                if(t.length > 0 && t !== '+' && t !== '-' && t !== '€' && t !== '%') {
                    texts.push(t);
                }
            }
            
            let ratiosTrouves = {};
            let currentCat = "Valeurs";
            let hasNewRatios = false;
            
            for(let i=0; i<texts.length; i++) {
                let txt = texts[i];
                if(txt.toUpperCase() === "MENSUEL" || txt.toUpperCase() === "CUMULÉ" || txt.toUpperCase() === "ANNUEL") {
                    currentCat = txt.toUpperCase();
                }
                else if(txt.match(/[0-9]/) && (txt.includes('€') || txt.includes('%') || txt.match(/^[-+]?[0-9\\s]+[,.][0-9]+$/))) {
                    if (i > 0) {
                        let label = texts[i-1];
                        if (!label.match(/^[0-9\\s,.-]+$/)) {
                            let numText = txt.replace(/[^0-9,-]/g, '').replace(',', '.');
                            let num = parseFloat(numText);
                            if(!isNaN(num)) {
                                if(!ratiosTrouves[currentCat]) ratiosTrouves[currentCat] = {};
                                ratiosTrouves[currentCat][label] = num;
                                hasNewRatios = true;
                            }
                        }
                    }
                }
            }
            
            if(hasNewRatios) {
                if(!data[sectionName]) data[sectionName] = {};
                for(let cat in ratiosTrouves) {
                    if (Object.keys(ratiosTrouves[cat]).length > 0) {
                        data[sectionName][`Indicateurs_${cat}`] = [ ratiosTrouves[cat] ];
                    }
                }
            }
        });

        // 4. EXTRACTION DES SOLDES ISOLÉS
        let soldesTrouves = {};
        let motsClesSoldes = ["Solde initial", "Solde final"];
        let walkerSoldes = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
        let currentNode;
        let textesPage = [];
        while(currentNode = walkerSoldes.nextNode()) {
            let t = currentNode.nodeValue.trim();
            if(t.length > 0) textesPage.push(t);
        }
        
        for(let i = 0; i < textesPage.length; i++) {
            if(motsClesSoldes.includes(textesPage[i])) {
                for(let j = 1; j <= 3; j++) {
                    if(i + j < textesPage.length) {
                        let txtCible = textesPage[i+j];
                        if(txtCible.match(/[0-9]/)) {
                            let numStr = txtCible.replace(/\\s/g, '').replace(/[^0-9,-]/g, '').replace(',', '.');
                            let num = parseFloat(numStr);
                            if(!isNaN(num)) {
                                soldesTrouves[textesPage[i]] = num;
                                break;
                            }
                        }
                    }
                }
            }
        }
        
        if (Object.keys(soldesTrouves).length > 0) {
            data["Soldes bancaires"] = [soldesTrouves];
        }

        return data;
    }
    """

    # --- FONCTIONS DU SCRAPER ---
    def verifier_specimen(page):
        return page.get_by_text("Vous n'avez pas encore acheté ce document", exact=False).is_visible() or page.get_by_text("SPECIMEN", exact=False).is_visible()

    def selectionner_mois(page, periode_cible):
        try:
            page.wait_for_selector("okw-select-period span[role='combobox']", timeout=3000)
            if periode_cible in page.locator("okw-select-period span[role='combobox']").first.inner_text():
                return
            page.locator("okw-select-period span[role='combobox']").first.click()
            page.wait_for_timeout(300) 
            page.locator(f"div.p-select-option-label:text-is('{periode_cible}')").last.click(timeout=5000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2500)
        except: pass

    def aspirer_page_courante(page, liste_mois):
        donnees_par_mois = {mois: {} for mois in liste_mois}
        documents = page.evaluate("""
            () => {
                let docs = [];
                document.querySelectorAll('a.linkItemCard').forEach(a => {
                    let textEls = a.querySelectorAll('.text-regular-md, .linkItemCard-title');
                    let title = textEls.length > 0 ? textEls[textEls.length - 1].innerText.trim() : "Document";
                    let url = a.getAttribute('href');
                    if(url) docs.push({ title: title, url: url });
                });
                return docs;
            }
        """)
        
        if documents and len(documents) > 0:
            url_mosaique = page.url
            for doc in documents:
                try:
                    page.goto(DOMAINE_BASE + doc['url'])
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(800)
                    for mois in liste_mois:
                        selectionner_mois(page, mois)
                        donnees_par_mois[mois][doc['title']] = "SPECIMEN" if verifier_specimen(page) else page.evaluate(JS_EXTRACTEUR_UNIVERSEL)
                    page.goto(url_mosaique)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(800)
                except Exception:
                    try: page.goto(url_mosaique)
                    except: pass
            return donnees_par_mois
        else:
            for mois in liste_mois:
                selectionner_mois(page, mois)
                donnees_par_mois[mois] = "SPECIMEN" if verifier_specimen(page) else page.evaluate(JS_EXTRACTEUR_UNIVERSEL)
            return donnees_par_mois

    def aspirer_structure_intelligente(page, liste_mois):
        page.wait_for_timeout(1500)
        onglets_elements = page.locator(".p-menubar-item-content-item-label").all_inner_texts()
        onglets = [o.strip() for o in onglets_elements if o.strip()]
        
        if onglets:
            donnees_par_mois = {mois: {} for mois in liste_mois}
            url_module = page.url 
            for onglet in onglets:
                try:
                    page.goto(url_module) 
                    page.wait_for_load_state("networkidle")
                    page.get_by_text(onglet, exact=True).first.click(timeout=5000)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(800)
                    resultat_onglet = aspirer_page_courante(page, liste_mois)
                    for mois in liste_mois:
                        donnees_par_mois[mois][onglet] = resultat_onglet[mois]
                except Exception: pass
            return donnees_par_mois
        else:
            return aspirer_page_courante(page, liste_mois)

    def revenir_au_dashboard(page):
        try:
            page.goto(URL_DASHBOARD)
            page.wait_for_load_state("networkidle")
        except: pass

    # --- INTERFACE UTILISATEUR STREAMLIT ---
    with st.form("form_scraper"):
        st.subheader("🔐 Identifiants Subakoua")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            sub_user = st.text_input("Adresse email (Subakoua)")
        with col_c2:
            sub_pass = st.text_input("Mot de passe", type="password")

        st.subheader("📅 Paramètres d'extraction")
        mois_a_scraper = st.multiselect("Sélectionnez le(s) mois à extraire :", list(mois_mapping.keys()), default=[mois_selectionne])

        st.subheader("⚙ Traitement des données")
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            opt_bdd = st.checkbox("💾 Enregistrer automatiquement dans la base de données (Aiven)", value=True)
        with col_o2:
            opt_export = st.checkbox("📄 Générer un fichier de sauvegarde (JSON) disponible au téléchargement", value=True)

        bouton_lancer = st.form_submit_button("🚀 Lancer l'Aspiration Cloud")

    if bouton_lancer:
        if not sub_user or not sub_pass:
            st.error("⚠️ Veuillez renseigner vos identifiants Subakoua.")
        elif not mois_a_scraper:
            st.error("⚠️ Veuillez sélectionner au moins un mois.")
        else:
            with st.spinner("🤖 Démarrage du robot... Installation du navigateur fantôme (prend environ 30 secondes la première fois)..."):
                import os
                # Installation automatique de Playwright sur le serveur Streamlit Cloud
                os.system("playwright install chromium")
                os.system("playwright install-deps chromium")
                
                try:
                    structure_bdd = {
                        "metadata": {
                            "projet": "Subakoua ERP",
                            "date_export": time.strftime("%Y-%m-%d %H:%M:%S")
                        },
                        "periodes": {mois: {"etat_actuel": {}, "saisies_manuelles": {}, "simulations": {}} for mois in mois_a_scraper}
                    }

                    # Lancement du navigateur EN MODE FANTÔME (headless=True)
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True) 
                        page = browser.new_page()

                        page.goto(URL_CONNEXION)
                        page.fill("input#username", sub_user)
                        page.fill("input#password", sub_pass)
                        page.click("button[type='submit']:has-text('Se connecter')")
                        page.wait_for_load_state("networkidle")
                        
                        if "login" in page.url:
                            raise Exception("Identifiants incorrects ou protection anti-bot déclenchée par Subakoua.")
                        
                        revenir_au_dashboard(page)

                        for mot_cle_tuile, cle_dict in MODULES_A_VISITER:
                            try:
                                page.get_by_text(mot_cle_tuile, exact=False).first.click()
                                page.wait_for_load_state("networkidle")
                                donnees_multi_mois = aspirer_structure_intelligente(page, mois_a_scraper)
                                
                                for mois in mois_a_scraper:
                                    structure_bdd["periodes"][mois]["etat_actuel"][cle_dict] = donnees_multi_mois.get(mois, {})
                                    
                                revenir_au_dashboard(page)
                            except Exception:
                                revenir_au_dashboard(page)

                        browser.close()

                    st.success("✅ Aspiration terminée avec succès !")

                    # Action 1 : Envoi direct et sécurisé dans Aiven via SQLAlchemy
                    if opt_bdd:
                        with st.spinner("💾 Enregistrement dans Aiven..."):
                            with engine.begin() as conn:
                                sql_insert = text("""
                                    INSERT INTO erp_donnees (periode, type_donnee, module, contenu)
                                    VALUES (:periode, :type_donnee, :module, :contenu)
                                    ON DUPLICATE KEY UPDATE 
                                        contenu = VALUES(contenu), 
                                        date_maj = NOW()
                                """)
                                for periode, types_donnee in structure_bdd["periodes"].items():
                                    for type_donnee, modules in types_donnee.items():
                                        for nom_module, donnees_module in modules.items():
                                            json_data = json.dumps(donnees_module, ensure_ascii=False)
                                            conn.execute(sql_insert, {"periode": periode, "type_donnee": type_donnee, "module": nom_module, "contenu": json_data})
                        st.success("☁️ Données intégrées avec succès à l'ERP (Base de données Aiven).")

                    # Action 2 : Création du bouton de téléchargement JSON
                    if opt_export:
                        json_str = json.dumps(structure_bdd, indent=4, ensure_ascii=False)
                        st.download_button(
                            label="📥 Télécharger la sauvegarde (JSON)",
                            data=json_str,
                            file_name=f"subakoua_export_{time.strftime('%Y%m%d_%H%M%S')}.json",
                            mime="application/json"
                        )
                
                except Exception as e:
                    st.error(f"❌ Une erreur est survenue lors du scraping : {e}")
