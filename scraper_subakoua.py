import os
import time
import json
import pprint
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import pymysql

# --- CONFIGURATION ---
load_dotenv()
USERNAME = os.getenv("SUBAKOUA_USER")
PASSWORD = os.getenv("SUBAKOUA_PASS")

# Identifiants BDD
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_NAME = os.getenv("DB_NAME", "subakoua_erp")

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

MOIS_DISPONIBLES = [
    "Année 1 - Janvier", "Année 1 - Février", "Année 1 - Mars", 
    "Année 1 - Avril", "Année 1 - Mai", "Année 1 - Juin", 
    "Année 1 - Juillet", "Année 1 - Août", "Année 1 - Septembre", 
    "Année 1 - Octobre", "Année 1 - Novembre", "Année 1 - Décembre",
    "Année 2 - Janvier", "Année 2 - Février", "Année 2 - Mars"
]

# --- LE CERVEAU D'EXTRACTION JAVASCRIPT ---
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

    // 2. EXTRACTION DES TABLEAUX CLASSIQUES (Compta, Veille, Etudes...)
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
    
    // 3. EXTRACTION DES RATIOS & KPI (Scanner Visuel Linéaire)
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

    // 4. NOUVEAU : EXTRACTION DES SOLDES ISOLÉS (Solde initial, Solde final)
    let soldesTrouves = {};
    let motsClesSoldes = ["Solde initial", "Solde final"];
    
    // Parcourt littéralement tout le texte de la page
    let walkerSoldes = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    let currentNode;
    let textesPage = [];
    while(currentNode = walkerSoldes.nextNode()) {
        let t = currentNode.nodeValue.trim();
        if(t.length > 0) textesPage.push(t);
    }
    
    for(let i = 0; i < textesPage.length; i++) {
        if(motsClesSoldes.includes(textesPage[i])) {
            // S'il trouve le mot, il regarde dans les 3 "bouts de texte" suivants pour attraper le montant
            for(let j = 1; j <= 3; j++) {
                if(i + j < textesPage.length) {
                    let txtCible = textesPage[i+j];
                    // Si c'est bien un chiffre (même négatif ou avec des espaces)
                    if(txtCible.match(/[0-9]/)) {
                        let numStr = txtCible.replace(/\\s/g, '').replace(/[^0-9,-]/g, '').replace(',', '.');
                        let num = parseFloat(numStr);
                        if(!isNaN(num)) {
                            soldesTrouves[textesPage[i]] = num;
                            break; // On a trouvé le chiffre, on s'arrête
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
# --- FONCTIONS BDD & FICHIERS ---

def sauvegarder_en_txt(donnees):
    """Génère le fichier avec un nom horodaté (ex: extraction_subakoua_20260927_143000.json)"""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    nom_fichier = f"extraction_subakoua_{timestamp}.json"
    
    with open(nom_fichier, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=4)
    print(f"💾 Sauvegarde locale générée avec succès : {nom_fichier}")

def synchroniser_mysql(structure_bdd):
    print("\n🗄️ Connexion à la base de données MySQL...")
    try:
        connection = pymysql.connect(
            host=DB_HOST, 
            user=DB_USER, 
            password="", # Mot de passe forcé à vide pour XAMPP
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor
        )
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
                )
            """)
            
            for periode, types_donnee in structure_bdd["periodes"].items():
                for type_donnee, modules in types_donnee.items():
                    for nom_module, donnees_module in modules.items():
                        json_data = json.dumps(donnees_module, ensure_ascii=False)
                        sql = """
                            INSERT INTO erp_donnees (periode, type_donnee, module, contenu)
                            VALUES (%s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE 
                                contenu = VALUES(contenu), 
                                date_maj = NOW()
                        """
                        cursor.execute(sql, (periode, type_donnee, nom_module, json_data))
                        
        connection.commit()
        print("✅ Données synchronisées avec succès dans MySQL !")
    except Exception as e:
        print(f"⚠️ Erreur MySQL : {e}")
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()

# --- FONCTIONS DE NAVIGATION ULTRA-RAPIDES ---

def menu_demarrage():
    print("\n" + "="*50)
    print("🤖 ASPIRATEUR SUBAKOUA ERP - DÉMARRAGE ULTRA-RAPIDE")
    print("="*50)
    print("Mois disponibles :")
    for i, mois in enumerate(MOIS_DISPONIBLES, 1):
        print(f"  {i}. {mois}")
        
    choix = input("\n👉 Entrez les numéros à aspirer (ex: 1,2,3) ou 'tout' : ")
    
    if choix.strip().lower() == 'tout':
        return MOIS_DISPONIBLES
        
    periodes_choisies = []
    for num in choix.split(','):
        try:
            index = int(num.strip()) - 1
            if 0 <= index < len(MOIS_DISPONIBLES):
                periodes_choisies.append(MOIS_DISPONIBLES[index])
        except: pass
            
    if not periodes_choisies:
        print("⚠️ Choix invalide. Aspiration par défaut sur 'Année 1 - Janvier'.")
        return ["Année 1 - Janvier"]
        
    return periodes_choisies

def verifier_specimen(page):
    avertissement = page.get_by_text("Vous n'avez pas encore acheté ce document", exact=False).is_visible()
    specimen = page.get_by_text("SPECIMEN", exact=False).is_visible()
    return avertissement or specimen

def selectionner_mois(page, periode_cible):
    try:
        page.wait_for_selector("okw-select-period span[role='combobox']", timeout=3000)
        texte_actuel = page.locator("okw-select-period span[role='combobox']").first.inner_text()
        if periode_cible in texte_actuel:
            return
        
        page.locator("okw-select-period span[role='combobox']").first.click()
        page.wait_for_timeout(300) 
        page.locator(f"div.p-select-option-label:text-is('{periode_cible}')").last.click(timeout=5000)
        
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500) # Pause stricte pour les calculs internes du jeu
    except: pass

def aspirer_page_courante(page, liste_mois):
    """Boucle sur les mois DIRECTEMENT dans la page/l'onglet en cours !"""
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
            print(f"      -> 📄 Ouverture : {doc['title']}")
            try:
                page.goto(DOMAINE_BASE + doc['url'])
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(800)
                
                # INVERSION DE BOUCLE : On extrait tous les mois d'un coup pour ce document
                for mois in liste_mois:
                    print(f"         📅 Extraction pour : {mois}...")
                    selectionner_mois(page, mois)
                    if verifier_specimen(page):
                        donnees_par_mois[mois][doc['title']] = "SPECIMEN"
                    else:
                        donnees_par_mois[mois][doc['title']] = page.evaluate(JS_EXTRACTEUR_UNIVERSEL)
                    
                page.goto(url_mosaique)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(800)
            except Exception as e:
                print(f"         ⚠️ Erreur sur le document : {e}")
                try: page.goto(url_mosaique)
                except: pass
        return donnees_par_mois
    else:
        # Aucun document imbriqué = Simple tableau. On extrait tous les mois d'un coup.
        for mois in liste_mois:
            print(f"      📅 Extraction pour : {mois}...")
            selectionner_mois(page, mois)
            if verifier_specimen(page):
                donnees_par_mois[mois] = "SPECIMEN"
            else:
                donnees_par_mois[mois] = page.evaluate(JS_EXTRACTEUR_UNIVERSEL)
        return donnees_par_mois

def aspirer_structure_intelligente(page, liste_mois):
    """Identifie les onglets et délègue l'extraction multi-mois"""
    page.wait_for_timeout(1500)
    
    onglets_elements = page.locator(".p-menubar-item-content-item-label").all_inner_texts()
    onglets = [o.strip() for o in onglets_elements if o.strip()]
    
    if onglets:
        print(f"   -> 📑 {len(onglets)} onglets principaux détectés.")
        donnees_par_mois = {mois: {} for mois in liste_mois}
        url_module = page.url 
        
        for onglet in onglets:
            print(f"   -> 🖱️ Clic sur l'onglet : {onglet}")
            try:
                page.goto(url_module) 
                page.wait_for_load_state("networkidle")
                
                page.get_by_text(onglet, exact=True).first.click(timeout=5000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(800)
                
                # Le résultat est un dictionnaire qui classe les données par mois
                resultat_onglet = aspirer_page_courante(page, liste_mois)
                
                # On réorganise les données retournées
                for mois in liste_mois:
                    donnees_par_mois[mois][onglet] = resultat_onglet[mois]
                    
            except Exception as e:
                print(f"      ⚠️ Erreur sur l'onglet '{onglet}': {e}")
        return donnees_par_mois
    else:
        print("   -> Aucun onglet principal détecté. Aspiration directe.")
        return aspirer_page_courante(page, liste_mois)

def revenir_au_dashboard(page):
    try:
        page.goto(URL_DASHBOARD)
        page.wait_for_load_state("networkidle")
    except: pass

def lancer_robot_global(liste_mois):
    structure_bdd = {
        "metadata": {
            "projet": "Subakoua ERP",
            "date_export": time.strftime("%Y-%m-%d %H:%M:%S")
        },
        "periodes": {mois: {"etat_actuel": {}, "saisies_manuelles": {}, "simulations": {}} for mois in liste_mois}
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=30)
        page = browser.new_page()

        print("🌐 1. Connexion à Subakoua...")
        page.goto(URL_CONNEXION)
        page.fill("input#username", USERNAME)
        page.fill("input#password", PASSWORD)
        page.click("button[type='submit']:has-text('Se connecter')")
        page.wait_for_load_state("networkidle")
        revenir_au_dashboard(page)

        print("\n" + "="*50)
        print(f"🚀 LANCEMENT DE L'EXTRACTION MULTI-MOIS OPTIMISÉE")
        print("="*50)

        for mot_cle_tuile, cle_dict in MODULES_A_VISITER:
            try:
                print(f"\n👉 Visite du module : {mot_cle_tuile.upper()}")
                page.get_by_text(mot_cle_tuile, exact=False).first.click()
                page.wait_for_load_state("networkidle")
                
                # L'aspiration ramène toutes les données pour tous les mois demandés d'un seul coup
                donnees_multi_mois = aspirer_structure_intelligente(page, liste_mois)
                
                # On répartit les données dans le bon tiroir de la BDD
                for mois in liste_mois:
                    structure_bdd["periodes"][mois]["etat_actuel"][cle_dict] = donnees_multi_mois.get(mois, {})
                
                revenir_au_dashboard(page)
                
            except Exception as e:
                print(f"⚠️ Erreur sur le module {mot_cle_tuile}: {e}")
                revenir_au_dashboard(page)

        print("\n" + "="*60)
        print("✅ EXTRACTION ULTRA-RAPIDE TERMINÉE !")
        print("="*60)
        
        sauvegarder_en_txt(structure_bdd)
        synchroniser_mysql(structure_bdd)
        
        browser.close()
        return structure_bdd

if __name__ == "__main__":
    periodes_choisies = menu_demarrage()
    donnees = lancer_robot_global(periodes_choisies)