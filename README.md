# ERP Subakoua — Scraper API-first + Optimiseur documentaire v2.5

Cette version poursuit la reconstruction du scraper à partir de l'audit web Subakoua.

## Nouveautés v2.5

- Matrice explicite de 91 études : document → information → levier → horizon → coût.
- 54 études avec endpoint API connu ; 37 restent volontairement en `label_only` tant que leur endpoint n'est pas cartographié.
- Le plan d'achat déduit maintenant d'abord la couverture des études gratuites ou déjà achetées avant de recommander des études payantes.
- La couverture de base est exposée par période dans le diagnostic du plan.
- Les études ≥ 5 000 € restent interdites par défaut à l'achat automatique.
- Toute correspondance sémantique non confirmée par l'API porte une confiance explicite.
- Les achats réels restent séparés du calcul du plan et nécessitent une autorisation explicite.

## Fichiers clés

- `subakoua_api.py` : client API-first.
- `smart_scraper.py` : orchestration planification/achat/lecture.
- `document_strategy.py` : matrice métier des 91 études.
- `document_optimizer.py` : couverture et optimisation sous budget.
- `pages/2_Scraper.py` : interface Streamlit.
- `study_information_matrix.csv` / `.json` : matrice générée.

## Important

Les prix, IDs et endpoints connus sont issus de l'audit web fourni. La relation étude → levier de décision est une couche analytique du projet : elle doit être revue si une nouvelle règle Subakoua ou un changement de document est constaté.

Les secrets `.env` et les données propriétaires ne doivent jamais être poussés sur GitHub.
