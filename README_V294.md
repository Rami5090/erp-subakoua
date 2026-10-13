# ERP Subakoua — v2.9.4

Fait suite à la v2.9.3 : l'encart « Période de référence utilisée » a montré que la base ne
contenait aucun état financier avant Août et que les tables de décisions s'arrêtaient en Mai/Juin.

## 1. Trésorerie : lecture de « Banque et assurance → Extrait bancaire »

| Cause | Correctif |
|---|---|
| Chemin JSON codé en dur (`Banque → Extrait bancaire → Soldes bancaires`) : si la page n'a pas de menu d'onglets, le niveau « Banque » n'existe pas et la lecture échouait. | `_walk_path` : casse/accents tolérés, un niveau manquant toléré (jamais l'avant-dernier, pour ne pas lire le tableau d'un autre document). Dernier recours : tout bloc contenant « Solde initial » / « Solde final » dans le module Banque. S'applique aussi aux extracteurs « Expert-comptable ». |
| Un état financier M-1 sans bilan mais avec extrait bancaire était jugé vide. | Un solde bancaire seul suffit à rendre l'état exploitable. |
| Mois simulé déjà clos mais M-1 absent de la base. | Chaîne d'ouverture : solde **final** de l'extrait M-1 → `Finances_Mensuelles` → solde **initial** de l'extrait du mois simulé. La source utilisée est indiquée. |
| Aucune issue si la base est incomplète. | Champs **« Trésorerie bancaire d'ouverture »** et **« Dette bancaire d'ouverture »** saisissables dans le simulateur (pré-remplis quand la base les connaît). |
| Impossible de savoir ce que contient la base. | Si une alerte existe, l'encart liste les modules × périodes réellement présents dans `erp_donnees`. |

## 2. Scraper : décisions de l'accueil Subakoua (tuiles DÉCISIONS)

Constat sur l'ancien extracteur (reproduit sur une page factice) : valeurs **dupliquées** (`[115, 115]`),
et valeurs **lues perdues** quand la décision est clôturée (affichage en lecture seule, sans `<input>`).

- `ouvrir_decision` : clic sur la tuile « DÉCISIONS <module> » (libellés sur plusieurs lignes gérés), repli sur la recherche par texte.
- `JS_EXTRACTEUR_DECISIONS` : en plus de l'extraction historique, ajoute `__lignes__` (valeurs propres par ligne), `__champs__` (chaque champ : section, ligne, label, nom, valeur, lecture seule) et `__page__`. Les valeurs en lecture seule sont lues.
- Période : si la page n'a pas de sélecteur de période, seule la **période courante du jeu** (lue dans le bandeau en haut à gauche) est extraite, jamais une période erronée.
- Mode incrémental : les anciennes extractions de décisions (sans `__champs__`) sont **ré-extraites automatiquement**.
- CLI : `--modules decisions` = marketing, production, approvisionnement, rh, finance.

```bash
python scraper_subakoua.py --modules decisions,banque_assurance,expert_comptable --periods 1-8 --show-browser
```

## 3. Décisions scrapées → simulateur

`decisions_parser.py` convertit les décisions en valeurs M-1 (prix, budget marque, PLV, axes, effectifs/salaires par
catégorie, quantités à produire). Priorité : **ligne SQL saisie à la main pour M-1** > décisions scrapées > repli sur
un tour antérieur. Contrôles de vraisemblance ; un module présent mais non reconnu est signalé « format à calibrer ».

## 4. Vérification

- `simulator_smoke_runner.py` exécute réellement `erp_engine.py` avec un Streamlit factice et une base SQLite : scénarios
  « Juillet absent », « tiret – dans le libellé », « décisions scrapées », « base vide » (`test_simulator_smoke_v294.py`).
- `test_bank_balances_v294.py`, `test_decisions_parser_v294.py`, `test_period_context_v293.py`.
- Les extracteurs JavaScript ont été exécutés dans Chrome sur une page factice (tuiles, champs actifs, lecture seule).

## 5. Limites connues

Le DOM réel des pages de décisions de Subakoua n'a pas pu être inspecté : le parseur est heuristique. Si l'encart indique
« format à calibrer », envoyer le résultat de
`SELECT periode, module, contenu FROM erp_donnees WHERE module IN ('marketing','rh','production') LIMIT 3;`.
Le pont complet (dette, BFR, AACE) exige aussi le module **expert_comptable** pour la période M-1.
