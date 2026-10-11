# ERP Subakoua — v2.9.3

## Simulateur stratégique : prise en compte de la période antérieure (M-1)

**Symptôme** : au « Septembre N+1 », le simulateur affichait Trésorerie finale = Résultat net, Dette = 0 €,
« +951 413 € vs M-1 » et des volumes identiques à des constantes, comme si la base ne contenait rien avant.

| # | Cause | Correctif |
|---|-------|-----------|
| 1 | Les décisions (`sim_*`, `ord_*`, `sp_*`…) restaient dans `st.session_state` d'un mois à l'autre : les valeurs M-1 de la base n'étaient lues qu'à la toute première ouverture. | Empreinte de contexte (mois + données de référence) ; si elle change, les décisions sont purgées et le simulateur repart de M-1. |
| 2 | Volumes vendus et quantités à produire codés en dur (50 / 500 / 100 / 1500 / 2500). | Valeurs par défaut = ventes M-1 (`Ventes_Historique`, sinon « Ventes mensuelles » de `erp_donnees`). |
| 3 | `erp_donnees` lu par égalité stricte sur le libellé : un espace, un tiret « – », un accent ou la casse rendait M-1 invisible → solde bancaire, dette, BFR = 0 sans alerte. | `normalize_period` / `match_period` tolèrent ces variantes ; JSON doublement encodé géré ; erreurs SQL signalées au lieu d'être avalées. |
| 4 | `WHERE Tour_ID = M-1` : une ligne manquante (RH, marketing, machines, stocks) retombait sur des constantes. | Repli sur le **dernier tour antérieur** disponible (jamais le futur), signalé à l'écran. |
| 5 | Un état financier M-1 vide produisait des KPI trompeurs (« Trésorerie projetée positive »). | Repli sur le dernier état exploitable antérieur (alerte : mois intermédiaires non projetés) ; sinon avertissement « Pont M-1 incomplet ». Le delta « vs M-1 » n'est plus calculé contre 0 si M-1 est inconnu. |
| 6 | Valeurs NULL en base → `float(None)` / NaN. | `ref_number` / `ref_int` retombent proprement sur la valeur par défaut. |

Nouvel expander **« 🧭 Période de référence utilisée »** sous le tableau de bord : tour réellement lu par table,
alertes de repli ou de donnée absente.

### Fichiers
`period_context.py` (nouveau, logique pure), `erp_engine.py` (modifié), `test_period_context_v293.py` (nouveau).

### Points non modifiés volontairement
`deficit_precedent` reçoit toujours le résultat net M-1 (règle IS du jeu non vérifiable sans le dump) :
à revoir si le report déficitaire doit être cumulé sur l'exercice.
