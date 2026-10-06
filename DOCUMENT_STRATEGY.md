# Matrice documentaire Subakoua — v2.5

## Principe

Le scraper distingue désormais trois couches :

1. **Socle disponible** : études gratuites ou déjà achetées pour la période.
2. **Informations manquantes** : besoins nécessaires au pilotage courant.
3. **Achats payants** : sélectionnés par utilité marginale sous contrainte budgétaire.

## Sources et confiance

- 54/91 études disposent d'un endpoint API connu issu de l'audit web.
- 37/91 sont actuellement classées sémantiquement à partir du libellé ; elles restent marquées `label_only` avec une confiance de 0,75.
- Les prix et identifiants viennent du catalogue audité ; la correspondance vers les leviers de décision est une couche analytique du projet et non une règle native déclarée par Subakoua.

## Classes de coût

- `gratuit` : 0 €
- `tactique_50_100` : 50–100 €
- `analytique_200` : 200 €
- `strategique_250` : 250 €
- `investissement_informationnel_5000` : ≥ 5 000 €

## Règle de sécurité

Les études à 5 000 € ne sont jamais recommandées automatiquement sans l'option explicite correspondante. Un achat réel exige une validation séparée.

## Matrice générée

Le fichier `study_information_matrix.csv` contient une ligne par étude avec :

- `study_id`, `label`, `price`
- état de couverture API (`endpoint_known`, `endpoint_path`, `response_keys`)
- domaine et leviers de décision
- horizons court / moyen / long
- priorité analytique
- fréquence de réutilisation
- classe de coût
- base et confiance du mapping

Cette matrice doit devenir la source de référence de l'optimiseur documentaire.
