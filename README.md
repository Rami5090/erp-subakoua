# ERP Subakoua — Pilotage stratégique 12 mois v2.6.1

Correctif du moteur de prévision : la page de pilotage sait désormais exploiter les payloads API-first stockés dans `erp_etudes` (notamment `ensaacvm` pour les ventes mensuelles et `peeumreusreprv` pour la saisonnalité / marché potentiel), tout en conservant un fallback vers l'ancien schéma `erp_donnees`.

## Correctif principal

La version précédente lisait uniquement `erp_donnees` et attendait une structure DOM/normalisée de type :

`donnees_internes -> Marketing -> Ventes mensuelles -> Tableau_1`

Or le nouveau scraper API-first stocke les payloads bruts dans :

`erp_etudes -> study_id=ensaacvm -> payload.sales`

Le moteur v2.6.1 accepte désormais les deux formats.

## Tests

- Tests forecast : 7/7 PASS
- Compilation Python : PASS

## Déploiement

Remplacer les fichiers du dépôt Git sans toucher au `.env`, puis :

```powershell
git add .
git commit -m "Correction forecast API-first"
git push
```

Streamlit Cloud redéploiera l'application.
