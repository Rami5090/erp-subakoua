# v2.8.6 — achat UI instrumenté et anti-blocage

## Correctifs
- Ne recharge plus inutilement `/companies` juste après l’authentification.
- Réutilise la page déjà authentifiée pour le premier achat.
- Ajoute des logs avant/après chaque étape de l’achat pour identifier immédiatement le point de blocage.
- Attente robuste de la vraie fenêtre `role="dialog"` et du texte de confirmation.
- Clic `Confirmer` avec fallbacks Playwright puis DOM/JavaScript pour PrimeNG/Angular.
- Vérification `boughtByTeam=true` après achat avec plusieurs tentatives.
- Journalise la disponibilité du moteur d’achat juste après le login.

## Tests
31/31 tests passent.
