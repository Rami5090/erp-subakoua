# ERP Subakoua — Streamlit + Aiven MySQL

## Fichiers
- `erp_engine.py` : application Streamlit / ERP
- `finance_engine.py` : moteur financier
- `requirements.txt` : dépendances Python
- `.streamlit/config.toml` : configuration Streamlit
- `.gitignore` : protection des secrets et fichiers locaux

## Secrets Streamlit
Dans Streamlit Community Cloud → App → Settings → Secrets :

```toml
[mysql]
host = "TON_HOST_AIVEN"
port = 16869
user = "TON_UTILISATEUR_AIVEN"
password = "TON_MOT_DE_PASSE_AIVEN"
database = "subakoua_erp"
```

Ne jamais pousser `.streamlit/secrets.toml` ni les dumps de données sur GitHub.

## Entrypoint
`erp_engine.py`
