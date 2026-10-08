# ERP Subakoua — v2.9.0

## Achat documentaire par navigation URL directe

Le processus d'achat UI ne sélectionne plus le mois dans le sélecteur de période avant
la recherche du document. Pour les études possédant une route UI auditée ou reconnue,
le client calcule directement le `month_id` et ouvre l'URL documentaire.

Exemples observés dans l'audit :
- Impôts sur les sociétés : `.../incomeTax/{month}/MONTHLY`
- Autres achats et charges externes : `.../otherPurchasesExternalExpenses/{month}/MONTHLY`
- Prévision des ventes : `.../salesForecastElements/{month}/MONTHLY`
- Ventes mensuelles : `.../monthlySales/{month}/MONTHLY`

Les routes de concurrence sans segment mensuel sont conservées comme routes de famille
inférées et sont explicitement marquées `inferred_family` dans `study_ui_routes.json`.
Une étude sans route UI sûre n'est pas associée à une URL inventée : le moteur utilise
alors son fallback de recherche global.

## Achat

Le clic final `Confirmer` et la vérification `boughtByTeam=true` restent inchangés.
La navigation directe ne contourne pas la confirmation d'achat.
