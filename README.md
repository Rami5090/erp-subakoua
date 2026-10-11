# ERP Subakoua — v2.9.2

> Voir `README_V292.md` : correctifs PDM de l'optimiseur et interface Visiteur / Utilisateur (lecture seule vs accès complet).


## Correctifs de l’optimiseur stratégique

- Le benchmark concurrentiel fusionne maintenant les payloads des études comparatives `pevc*` au lieu de s’arrêter à une première ligne correspondant seulement à l’entreprise propre.
- Le nombre de concurrents est calculé à partir des entreprises distinctes exploitées, en excluant le numéro d’entreprise sélectionné. Le tableau s’appuie sur la dernière observation connue avant l’ancrage.
- L’historique de calibration peut inclure les périodes disponibles uniquement dans `erp_etudes`.
- Le chargement de capacité utilise `Tour_ID` (clé réelle de `Parc_Machines_Mensuel`) au lieu d’une colonne `id` inexistante. Les requêtes machines et temps d’atelier sont isolées : une erreur dans l’une ne force plus les deux familles en fallback.
- Les noms `Cond` / `Conditionnement`, accents et identifiants produit API sont normalisés. Les paramètres réellement lus en base sont indiqués dans la source de capacité à l’écran.
- La demande excédant la capacité est maintenant plafonnée au volume industriel réalisable au lieu d’éliminer tous les scénarios. Le résultat expose la PDM potentielle, la PDM réalisable, le taux de service, la pression sur les ateliers et la demande non servie. Le dépassement du budget reste une contrainte bloquante.
- Le diagnostic du benchmark indique la période du snapshot, les lignes brutes et le nombre de concurrents réellement exploitables.

## Achat documentaire et routage URL

La logique URL directe des études, configurée dans `study_ui_routes.json`, est conservée de la v2.9.0. Ce correctif ne modifie pas le parcours d’achat.

## Vérification

Tests automatisés : **37/37 réussis**.

## Déploiement

```powershell
cd "C:\Users\ramih\Documents\erp-subakoua-git"
git add .
git commit -m "Fix concurrents et contraintes capacite optimiseur v2.9.1"
git push
```

Ne jamais inclure `.env` ni des identifiants de connexion dans Git.
