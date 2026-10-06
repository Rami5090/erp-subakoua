# v2.8.1 — correction période réelle / état Streamlit

Cette version corrige un problème d'état persistant Streamlit : après une nouvelle synchronisation (par exemple Janvier -> Juin), les sélecteurs pouvaient conserver Janvier alors que la base contenait une période plus récente.

Corrections :
- le scraper réinitialise ses sélections lorsque la liste des périodes découvertes change ;
- le pilotage 12 mois et l'optimiseur recalculent et resélectionnent automatiquement le dernier mois réellement observé ;
- affichage du nombre de périodes de ventes réellement reconnues par le moteur.

Important : un achat documentaire seul ne fait pas avancer la période réelle. La période réelle est déterminée par les ventes effectivement synchronisées et exploitables par le moteur.
