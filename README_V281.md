# ERP Subakoua — v2.8.1

Correction du processus d'achat documentaire :

- achat effectué via l'interface Playwright réelle ;
- ouverture de la fenêtre de confirmation Subakoua ;
- clic explicite sur le bouton `Confirmer` du dialogue `role=dialog` ;
- contrôle du prix affiché avant confirmation ;
- vérification post-achat via le catalogue live (`boughtByTeam=true`) ;
- remontée en erreur si le clic de confirmation ne produit pas un achat confirmé ;
- les 31 tests existants passent.

Le reste du moteur de planification et de synchronisation est conservé.
