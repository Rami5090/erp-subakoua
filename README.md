v2.8.9 - Fix NameError diagnostic missing_sales_periods and stabilize anchor-period diagnostics.

# ERP Subakoua — v2.8

## Stack
- Streamlit
- MySQL Aiven
- SQLAlchemy + PyMySQL
- Scraper API-first Subakoua
- Prévision + backtest + optimiseur stratégique exploratoire

## v2.8 — calibration historique & décision roulante
- Le pilotage s'ancre automatiquement sur le dernier mois de ventes réellement observé.
- Les périodes futures éventuellement présentes dans la base sont exclues de la calibration.
- Backtest hors-échantillon un pas en avant des modèles de forecast.
- Sélection du modèle par WAPE : niveau saisonnier, tendance amortie ou Holt amorti.
- Forecast roulant : mois réel d'ancrage + 12 mois futurs.
- La prochaine décision est explicitement calculée (ex. juin réel → juillet à décider).
- L'optimiseur stratégique réutilise le même ancrage et le même forecast roulant.

## Interprétation
Avec six mois d'historique, le système peut comparer les modèles hors-échantillon mais ne dispose pas encore d'un historique suffisant pour identifier proprement une saisonnalité annuelle statistique 12 mois. La saisonnalité structurelle fournie par Subakoua reste donc une information explicite du modèle. Les effets prix/publicité/qualité restent des sensibilités descriptives tant qu'ils ne sont pas validés statistiquement.

## Secrets
Ne jamais pousser `.env` ou les secrets Streamlit. Utiliser Streamlit Secrets en production.

## Déploiement
```powershell
git add .
git commit -m "Ajout calibration backtest et decision roulante v2.8"
git push
```
# v2.8.3 — robust study purchase UI lookup
