# ERP Subakoua — v2.7.2 Optimiseur stratégique exploratoire

## Nouveautés
- Nouvelle page `pages/4_Optimiseur_Strategique.py`.
- Calibration descriptive régularisée sur l'historique concurrentiel : part de marché, prix, qualité, publicité de marque et publicité produit.
- Projection de scénario ancrée sur la PDM segmentielle réellement observée : le modèle ne fabrique pas un niveau absolu de PDM.
- Recherche de scénarios sur prix / publicité / qualité sous contrainte de budget marketing.
- Contrôle de capacité mensuelle des ateliers avec temps produits et parc machines depuis la BDD, avec fallback explicite.
- Correction de l'extraction concurrentielle : publicité de marque et publicité produit sont désormais stockées séparément.
- Les périodes historiques multiples sont utilisées automatiquement pour améliorer la calibration dès qu'elles sont disponibles.

## Interprétation scientifique
Avec une seule période historique, les coefficients sont uniquement descriptifs et la confiance est faible. Les scénarios servent à l'analyse de sensibilité et au classement, pas à prétendre une causalité prix/publicité/qualité.

La couche suivante devra intégrer explicitement le MRP matières, les achats fournisseurs, le BFR, la trésorerie et la dette avant de déclarer un scénario « exécutable ».

## Déploiement
Ne jamais pousser `.env` ou des dumps de données. Après copie dans le dépôt GitHub :

```powershell
git add .
git commit -m "Ajout optimiseur strategique v2.7.2"
git push
```
