# ERP Subakoua — v2.9.1

## Routage direct des études UI

Cette version finalise le registre `study_ui_routes.json` à partir des liens observés dans l'audit du portail Subakoua.

- 91 `study_id` du catalogue local disposent maintenant d'une entrée de routage.
- Les routes mensuelles utilisent `{month}` avec : 13=A1-Janvier, ..., 18=A1-Juin, 19=A1-Juillet, ..., 24=A1-Décembre, 25=A2-Janvier, etc.
- Le moteur d'achat tente d'abord la route directe et ne sélectionne plus la période dans le sélecteur UI.
- Les routes sont construites à partir des chemins `/companies/partners/...` observés dans l'audit.
- `companyResults` est explicitement conservé comme `ui_supported=false` car aucun lien UI exploitable pour cette entrée n'a été observé dans l'audit fourni. Aucune URL fictive n'est générée pour ce cas.
- Le fallback catalogue/API/DOM reste disponible pour les cas sans route UI exploitable.

## Validation

`validate_study_routes.py` contrôle que les 91 identifiants du catalogue ont une entrée de registre et qu'aucune URL non-portail n'est introduite.
