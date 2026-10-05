# ERP Subakoua — Streamlit + Aiven MySQL

## Fichiers
- `erp_engine.py` : application Streamlit / ERP
- `finance_engine.py` : moteur financier
- `requirements.txt` : dépendances Python
- `.streamlit/config.toml` : configuration Streamlit
- `.gitignore` : protection des secrets et fichiers locaux

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

Le port et le nom de base doivent être ceux affichés dans Aiven > Overview > Connection information. Ne pas reprendre une valeur d'exemple.

Le code accepte aussi `username` à la place de `user`, `db_name` à la place de `database`, ou une `url` SQLAlchemy complète dans `[mysql]`.

Ne jamais pousser `.streamlit/secrets.toml` ni les dumps de données sur GitHub.

## Déploiement
- Entrypoint : `erp_engine.py`
- Branche : `main`
- Dépendances : `requirements.txt`

## Diagnostic connexion
Si l'ERP affiche « Système hors ligne », l'application affiche désormais le diagnostic non sensible de la connexion MySQL. Cela permet de distinguer une configuration de secrets incomplète, un mauvais port/base, un refus réseau, un problème TLS ou un problème d'authentification.
