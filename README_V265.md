# ERP Subakoua — Pilotage 12M v2.6.5

## Corrections
- extraction des ventes legacy prioritaire par section explicite `Ventes mensuelles`;
- les tables de stocks ne peuvent plus être confondues avec les ventes via le fallback générique;
- extraction du marché potentiel legacy plus tolérante;
- graphique 12 mois trié chronologiquement avec axe explicite;
- diagnostic concurrentiel distinguant PDM disponible et concurrence détaillée réellement synchronisée.

## Vérification
- 3 tests ciblés v2.6.5 : PASS
- py_compile : PASS
- test sur le dump de référence : janvier = 27 / 160 / 49 / 302 / 2640, total 3178; projection saisonnière non plate.

## Déploiement
Ne jamais remplacer `.env` local.
