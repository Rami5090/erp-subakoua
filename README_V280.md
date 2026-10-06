# ERP Subakoua — v2.8 Calibration historique & décision roulante

## Objectif
La simulation est maintenant pilotée par le dernier mois **réellement observé** : toutes les données futures présentes par erreur dans la base sont exclues de la calibration.

## Nouveautés
- Ancrage automatique sur le dernier mois de ventes réellement reconnu.
- Prévision roulante de 12 mois : mois réel d'ancrage + 12 mois futurs.
- Backtest hors-échantillon un pas en avant sur les périodes historiques disponibles.
- Comparaison de trois modèles lorsque les données le permettent :
  - niveau désaisonnalisé + saisonnalité structurelle ;
  - tendance amortie + saisonnalité structurelle ;
  - Holt amorti + saisonnalité structurelle.
- Sélection automatique du modèle par WAPE hors-échantillon.
- Affichage de MAE, WAPE, MAPE et du modèle retenu dans le pilotage.
- L'optimiseur stratégique utilise le même ancrage et le même forecast roulant.
- La décision préparée est explicitement affichée (ex. juin réel → décision juillet).

## Discipline scientifique
Avec six mois réels, le système peut déjà effectuer un backtest temporel, mais cela ne suffit pas à identifier proprement une saisonnalité statistique annuelle 12 mois. La saisonnalité structurelle Subakoua reste donc utilisée comme information externe ; les effets prix/publicité/qualité restent des sensibilités descriptives tant qu'un historique suffisant ne permet pas une validation hors-échantillon robuste.

## Déploiement
Ne jamais pousser `.env`, dumps de données, sauvegardes ou secrets Streamlit.

```powershell
git add .
git commit -m "Ajout calibration backtest et decision roulante v2.8"
git push
```
