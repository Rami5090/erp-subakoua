# ERP Subakoua — ERP en ligne + scraper API-first

Cette version conserve l'ERP Streamlit et remplace progressivement le scraping DOM/Gemini par un moteur API-first basé sur les endpoints JSON observés pendant l'audit du portail Subakoua.

## Architecture scraper

- `subakoua_api.py` : session Playwright authentifiée + appels API directs.
- `document_optimizer.py` : matrice document → information → décision et optimisation d'achat sous budget.
- `smart_scraper.py` : planification, achats explicites, lecture API et synchronisation Aiven.
- `pages/2_Scraper.py` : interface « Plan documentaire / Synchroniser / Secours DOM ».
- `study_api_catalog.json` : mapping des endpoints connus issu de l'audit.
- `subakoua_study_catalog.csv` : catalogue des études auditées (ID, libellé, prix, périodes observées).

## Règle d'achat

La planification ne déclenche aucun achat. La page affiche les études recommandées, leur coût, leur valeur documentaire et leur période d'achat. Un deuxième contrôle (« autoriser les achats réels ») est obligatoire avant l'appel PUT d'achat.

Le moteur utilise le catalogue live de Subakoua (`boughtByTeam`) comme source de vérité pour la période cible et ne considère pas un ancien achat comme encore disponible automatiquement.

## API-first

Les études dont l'endpoint est connu sont lues directement en JSON. Une réponse n'est acceptée que si `readAllowed=true` et si le `studyId` retourné correspond à l'étude demandée. Les études sans endpoint connu restent accessibles via le mode DOM de secours jusqu'à cartographie complète.

## Déploiement Streamlit

Secrets recommandés :

```toml
[mysql]
host = "..."
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

Ne committe jamais `.env`, les cookies de session ou les secrets.

## Tests

```bash
python -m pytest -q
python -m py_compile *.py pages/2_Scraper.py
```
