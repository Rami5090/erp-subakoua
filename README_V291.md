# ERP Subakoua — v2.9.1

## Problèmes corrigés

### 1. Concurrent affiché à zéro
La source affichée par l’optimiseur pouvait ne contenir que la ligne de l’équipe (par exemple via le monitoring), ce qui empêchait le chargement des autres entreprises. L’extraction cherche désormais les études API comparatives `pevc*` et fusionne les lignes par numéro d’entreprise. Pour le calibrage et le décompte affiché, les entreprises distinctes sont reconstruites à partir de la dernière observation disponible au plus tard à l’ancrage, en excluant l’équipe choisie.

Un expander `Diagnostic des concurrents et des sources` indique maintenant la période sélectionnée, les lignes concurrentielles brutes, le nombre de concurrents exploitables et la couverture de l’historique.

### 2. Capacité en fallback et aucun scénario réalisable
Le code demandait `ORDER BY id` sur `Parc_Machines_Mensuel`, alors que la clé de cette table est `Tour_ID`. Cette exception entraînait un retour à la capacité de secours et empêchait aussi la lecture des temps d’atelier, car les deux requêtes étaient dans le même bloc d’erreur.

Le chargement utilise `Tour_ID DESC` et isole les deux requêtes. Les valeurs d’atelier et les produits sont normalisés.

L’optimiseur distingue maintenant la demande théorique de la production possible : il plafonne le volume servi selon le poste contraignant au lieu de supprimer systématiquement un scénario dont la demande dépasserait la capacité. L’écran indique la PDM potentielle et la PDM réalisable, le taux de service, la demande non servie et la pression de capacité. Le budget marketing reste une contrainte stricte.

### 3. Portée des tests

`pytest -q` : **37 tests réussis**.
