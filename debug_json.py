import os
import pymysql
import json
from dotenv import load_dotenv

load_dotenv()

def extraire_structure_comptable():
    connexion = pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 16869)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "subakoua_erp"),
        ssl={'ssl': {}}
    )

    with connexion.cursor() as cursor:
        # On va chercher le dernier compte de résultat sauvegardé par votre robot
        cursor.execute("SELECT periode, contenu FROM erp_donnees WHERE module = 'expert_comptable' ORDER BY id DESC LIMIT 1")
        resultat = cursor.fetchone()

        if resultat:
            periode, contenu = resultat
            
            # Formate proprement le JSON pour qu'il soit lisible
            donnees_json = json.loads(contenu) if isinstance(contenu, str) else contenu
            
            with open("structure_comptable.json", "w", encoding="utf-8") as fichier:
                json.dump(donnees_json, fichier, indent=4, ensure_ascii=False)
                
            print(f"✅ Succès ! Les données de la période '{periode}' ont été sauvegardées.")
            print("👉 Ouvrez le fichier 'structure_comptable.json' qui vient d'apparaître dans votre dossier et copiez-collez-moi son contenu.")
        else:
            print("❌ Aucune donnée trouvée dans la table erp_donnees pour le module 'expert_comptable'.")

    connexion.close()

if __name__ == "__main__":
    extraire_structure_comptable()
