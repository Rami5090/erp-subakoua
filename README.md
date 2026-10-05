# ERP Subakoua — version en ligne avec scraper intégré

Cette version conserve l'ERP Streamlit et ajoute une page `🕷️ Scraper` dans le même déploiement.

## Ce qui est ajouté

- Sélection interactive des périodes à scraper.
- Bouton de découverte des périodes directement depuis Subakoua.
- Préréglages `Toutes`, `Année 1`, `Année 2` ou sélection personnalisée.
- Sélection des modules.
- Mode incrémental par défaut pour éviter de rescraper les couples période/module déjà valides.
- Option de re-scraping forcé.
- Aperçu de la couverture déjà présente dans `erp_donnees` avant lancement.
- Progression et logs du scraping affichés dans l'interface.
- Synchronisation Aiven au fur et à mesure des modules.
- Chromium système utilisé sur Streamlit Cloud.
- Connexion Subakoua via Secrets Streamlit ou saisie temporaire dans l'interface.

## Secrets Streamlit

Dans `Settings → Secrets` de l'application Streamlit :

```toml
[mysql]
host = "xxxxx.aivencloud.com"
port = 12345
user = "avnadmin"
password = "..."
database = "defaultdb"
ssl = true

[subakoua]
user = "..."
password = "..."
login_url = "https://login.arkhe.com/"
dashboard_url = "https://subakoua.arkhe.com/companies"
```

Ne committe jamais de `.env` ou de mot de passe dans GitHub.

## Déploiement

Le dépôt doit être déployé avec `erp_engine.py` comme fichier principal.
Le dossier `pages/` ajoute automatiquement la page du scraper dans la navigation Streamlit.

Le fichier `packages.txt` installe Chromium sur l'environnement Linux de Streamlit Community Cloud.
`playwright` est épinglé à une version compatible avec l'environnement Cloud utilisé par ce projet.

## Utilisation locale du scraper

```bash
python -m pip install -r requirements-scraper.txt
python -m playwright install chromium
python scraper_subakoua.py --periods 1,2,3
```

Le fichier `.env` local peut contenir les identifiants Subakoua et Aiven.

## Vérification

- 24 tests financiers passent.
- Compilation Python validée pour l'ERP, le moteur financier, le scraper et la page Streamlit.
