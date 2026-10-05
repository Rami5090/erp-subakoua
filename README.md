# ERP Subakoua — Streamlit + Aiven MySQL

## Application
- `erp_engine.py` : application Streamlit / cockpit stratégique
- `finance_engine.py` : moteur financier
- `requirements.txt` : dépendances du déploiement Streamlit
- `.streamlit/config.toml` : configuration Streamlit

## Scénarios
Le simulateur permet maintenant de :
- sauvegarder un scénario pour un Tour ;
- recharger toutes les décisions d'un scénario ;
- supprimer définitivement le scénario sélectionné, avec confirmation ;
- conserver les scénarios des autres Tours.

## Secrets Streamlit Cloud
Dans Streamlit Community Cloud → App → Settings → Secrets :

```toml
[mysql]
host = "TON_HOST_AIVEN"
port = TON_PORT_AIVEN
user = "TON_UTILISATEUR_AIVEN"
password = "TON_MOT_DE_PASSE_AIVEN"
database = "TON_NOM_DE_BASE_AIVEN"
```

Le port et le nom de base doivent être ceux affichés dans Aiven > Overview > Connection information.
Ne jamais pousser `.streamlit/secrets.toml` ni les dumps de données sur GitHub.

## Scraper
`scraper_subakoua.py` est un outil local séparé : il automatise l'extraction Subakoua puis synchronise les données dans MySQL/Aiven.
Il ne doit pas être lancé depuis Streamlit Cloud.

### Installation locale
```bash
python -m pip install -r requirements-scraper.txt
python -m playwright install chromium
```

Copier `.env.example` vers `.env` puis renseigner les identifiants Subakoua et MySQL.

Depuis la version 2.1.0, si `SUBAKOUA_USER` / `SUBAKOUA_PASS` ne sont pas présents, le scraper les demande directement dans le terminal (`getpass` pour le mot de passe). Le mot de passe n'est jamais écrit dans les logs. Le `.env` est recherché à côté de `scraper_subakoua.py`, même si le programme est lancé depuis un autre dossier.

### Exemples
```bash
# Janvier à Mars
python scraper_subakoua.py --periods 1,2,3

# Toute l'année 1 + année 2 disponible
python scraper_subakoua.py --all

# Refaire une extraction complète même si les données existent déjà
python scraper_subakoua.py --periods 1-15 --force

# Uniquement certains modules
python scraper_subakoua.py --periods 1-5 --modules finance,banque_assurance

# Afficher le navigateur pour diagnostiquer une page
python scraper_subakoua.py --periods 1 --show-browser
```

### Améliorations du scraper
- mode incrémental : les périodes déjà présentes sont ignorées, sauf données `SPECIMEN` ;
- synchronisation MySQL après chaque module pour éviter de perdre toute une extraction si le robot s'arrête ;
- retries automatiques sur navigation et extraction ;
- session Playwright réutilisable via `scraper_output/auth_state.json` ;
- détection de connexion expirée ;
- timeout configurables ;
- screenshots + HTML de diagnostic en cas d'erreur ;
- extraction des nombres français plus robuste (`1 234,56`, `1.234,56`, parenthèses, valeurs négatives) ;
- liens de documents dédupliqués et normalisés en URLs absolues ;
- modules, périodes, mode headless et force pilotables en ligne de commande ;
- journal horodaté dans `scraper_output/scraper.log` et rapport JSON par exécution.

Les fichiers de session et sorties du scraper restent locaux et sont ignorés par Git.
