# v2.8.5 — robust study search + purchase confirmation

## Correctifs
- Recherche d'étude renforcée : saisie clavier réelle dans `#search-query`, attente du rendu Angular, recherche des résultats dans tout le DOM visible et fallback clavier.
- Les études dont le catalogue API connaît l'ID mais dont le lien n'est pas présent directement dans le DOM sont maintenant recherchées via plusieurs variantes du titre.
- Confirmation d'achat : le scraper cible explicitement le dernier `div[role="dialog"]` visible au lieu du premier dialogue monté dans le DOM.
- Bouton `Confirmer` trouvé d'abord via `button` + texte visible, puis fallback PrimeNG.
- Après confirmation, `boughtByTeam=true` est contrôlé avec plusieurs tentatives pour laisser le backend se synchroniser.
- Les erreurs indiquent maintenant plus précisément si la fenêtre, le bouton ou la confirmation métier manque.

## Tests
31/31 tests passent.
