# Évaluation du niveau de compétence de l'ERP Subakoua

**Objet** : évaluer le niveau de compétence technique et métier atteint par l'application,
sur une échelle dont le **maximum est « Expert junior »**.

**Date** : 21 octobre 2026 — **Version évaluée** : `3b68786` (158 commits, 18 624 lignes de Python)

> ⚠️ **Nature de l'évaluation** : auto-évaluation fondée sur des **preuves vérifiables dans le dépôt**
> (fichiers, lignes, tests, commits, données). Elle n'a pas été validée par un jury externe, sur des
> données de production réelles ni auprès d'utilisateurs métier : les points assignés sont donc des
> **estimations argumentées**, pas des certifications. Le chapitre 6 liste explicitement ce qui n'a
> pas pu être vérifié.

---

## 1. Grille d'évaluation

Quatre niveaux ; **le niveau 4 est le maximum de la grille** (conformément à la commande).

| Niveau | Intitulé | Ce qui le caractérise |
|---|---|---|
| **1** | Prototype | Le calcul tourne, en local. Pas de persistance fiable, pas de tests, une seule commande. |
| **2** | Fonctionnel | Les fonctions métier existent et sont persistées. Cas nominaux traités, erreurs non gérées. |
| **3** | Industriel | Architecture testable, tests automatisés, sécurité, gestion d'erreur, documentation, exécution reproductible. |
| **4** | **Expert junior** (max) | Pertinence métier démontrée, **méthode validée** (hypothèses justifiées, incertitude mesurée), code maintenable par un tiers, **traçabilité de chaque chiffre**, limites connues et assumées, exploitation reproductible et outillée. |

**Règle de notation** : un domaine est noté 4 uniquement si les quatre conditions de la ligne
« Expert junior » sont réunies **pour ce domaine**. Une note de 3 signifie « le niveau industriel
est atteint, la rigueur du niveau 4 reste à prouver ».

---

## 2. Synthèse

| # | Domaine de compétence | Niveau | Verdict en une phrase |
|---|---|---|---|
| 1 | Cadrage métier & connaissance du domaine | **4** | Maîtrise réelle du jeu (comptabilité, MRP, BFR, point d'équilibre) et de ses règles. |
| 2 | Architecture & conception logicielle | **3** | Séparation nette moteurs purs / UI / SQL, mais le cockpit reste un script de 2 500 lignes à état global. |
| 3 | Qualité logicielle & tests | **3** | 169 tests et des invariants métier protégés, mais pas de mesure de couverture ni d'intégration continue. |
| 4 | Sécurité & maîtrise des accès | **3** | Fail-closed, comparaison à temps constant, utilisateur SQL en lecture seule, contrôles serveur ; pas de journal d'audit ni de rotation des secrets. |
| 5 | Données, ingestion & intégrité | **4** | Chaîne d'ingestion complète, 91 études pilotées, dégradation gracieuse, zéro fuite temporelle. |
| 6 | Modélisation & analyse quantitative | **3** | Prévision, calibration, MRP2, point d'équilibre, capacité : méthode solide, mais **incertitude non propagée** et optimisations heuristiques. |
| 7 | Interface & expérience utilisateur | **3** | Écrans organisés selon lechemin de décision, chiffres explicables, diagnostics ; pas de test utilisateur ni d'audit d'accessibilité. |
| 8 | Documentation & traçabilité | **4** | Chaque chiffre affiche sa source et son mode de repli ; invariants documentés ; README de 441 lignes. |
| 9 | Industrialisation, livraison & exploitation | **2** | Git propre et versionné, mais **ni CI, ni conteneur, ni migrations versionnées, ni supervision**. |
| 10 | Veille, réutilisation & openness | **3** | Méthodes standard maîtrisées et outillées ; projet non empaqueté, sans licence, sans changelog. |

### Verdict global

**Niveau 3 — Industriel, avec un profil « Expert junior » sur trois axes (métier, données, documentation).**

Le projet **n'est pas un prototype** (niveau 1) ni une simple application fonctionnelle (niveau 2) :
il est industriellement tenu — tests, sécurité, traçabilité, découplage. Il **n'est pas encore
« Expert junior »** sur l'ensemble, parce que **deux conditions du niveau 4 ne sont pas réunies** :

1. **La méthode n'est pas encore validée au sens probabiliste** : les incertitudes de prévision ne sont
   pas propagées jusqu'au point d'équilibre, et deux optimisations reposent sur des heuristiques
   (« mix optimal » glouton, plafond de capacité proportionnel) ;
2. **L'exploitation n'est pas outillée** : pas d'intégration continue, pas de migrations versionnées,
   pas de supervision — un déploiement reste une action manuelle.

**Écart estimé : 2 axes bloquants** (industrialisation, validation méthodologique) sur 10 domaines.

---

## 3. Évaluation détaillée

### 1. Cadrage métier & connaissance du domaine — **Niveau 4**

| Preuve | Fichier |
|---|---|
| Modèle complet du jeu : marché → production → MRP → RH → finance → trésorerie | `erp_engine.py` (5 onglets) |
| Comptabilité prévisionnelle en `Decimal`, TVA, BFR, dette, amortissements | `finance_engine.py` (1 889 l., 55 fonctions) |
| MRP2 avec stock de sécurité, arbitrage fournisseur prix/délai, possession de stock | `mrp_engine.py`, `study_ops.py` |
| Point d'équilibre, imputation des charges fixes, mix sous contrainte | `break_even_engine.py` |
| Règles du jeu documentées (nominal, remises A/B, unsur stock) | `livrable_finance.py`, `DOCUMENT_STRATEGY.md` |

**Atout** : la connaissance n'est pas superficielle — l'application reproduit les règles réelles du jeu
(cost accounting, coûts de possession, arbitrage fournisseur, cycle de TVA), ce qui suppose d'avoir lu
et compris la documentation du portail et les dumps comptables.

**Limite pour le niveau 4** : la validation est faite sur un **jeu de simulation**, pas sur une
entreprise réelle ; le référentiel métier n'est pas écrit (pas de dictionnaire de données métier
formel, pas de « processus métier » documenté de bout en bout).

### 2. Architecture & conception logicielle — **Niveau 3**

**Acquis**
- **11 moteurs purs** (7 767 lignes) sans Streamlit ni SQL : `forecast_engine`, `optimizer_engine`,
  `finance_engine`, `break_even_engine`, `mrp_engine`, `livrable_finance`, `period_context`,
  `strategic_engine`, `document_optimizer`… → testables et réutilisables.
- **Contrats explicites** : dataclasses (`CapacityProfile`, `FinancialProjection`, `BreakEvenResult`,
  `MachineDisposalImpact`…), valeurs par défaut, typage progressif.
- **Invariants structurels** : absence de fuite temporelle (`period_context`), purification numérique
  (`Decimal`), découplage UI/moteur, découplage lecture/écriture (`db_utils`).

**Limites (justifient le 3)**
- `erp_engine.py` reste un **script de ~2 500 lignes** avec état global (variables de module,
  `session_state` partagé) : le même nombre d'onglets et de leviers est mélangé à la logique
  d'affichage et d'accès aux données.
- Les pages dupliquent des helpers de lecture (`_contenu`, `_etude`) au lieu d'un **repository**.
- Le SQL est écrit dans la couche UI (f-strings historique, paramètres liés pour les nouveaux blocs).
- Pas de **couches d'inversion de dépendance** : les moteurs ne reçoivent pas de port d'accès aux
  données.

### 3. Qualité logicielle & tests — **Niveau 3**

**Acquis**
- **169 tests, 1 ignoré**, 1 976 lignes réparties sur 21 fichiers, exécutés en ~8 s.
- Les tests ne vérifient pas des implémentations mais des **invariants métier** : pas de fuite
  temporelle, pas d'URL inventée, capacité plafonnée et non éliminatoire, parc jamais négatif, cession
  bridée au parc détenu, point d'équilibre exact, mix toujours réalisable, migration de schéma
  idempotente, fail-closed de l'authentification.
- Banc d'essai permettant d'exécuter **le cockpit et les pages** sans Streamlit ni MySQL (base SQLite
  en mémoire, doubles d'objets) → les tests d'intégration existent réellement.
- Tests de page de bout en bout (`test_page_analyse_quantitative.py`,
  `test_equilibre_centre_decision.py`).

**Limites**
- **Aucune mesure de couverture** : on ne sait pas ce que les tests ne couvrent pas.
- Pas de **tests de propriété** (Hypothesis), pas de test de mutation : la solidité des tests repose
  sur la relecture, pas sur une métrique.
- Le harnais « faux Streamlit » est une **dette** : il doit être mis à jour à chaque changement
  d'API de l'interface.
- Pas d'**intégration continue** : rien n'empêche un push qui casse les tests.

### 4. Sécurité & maîtrise des accès — **Niveau 3**

**Acquis**
- `fail-closed` : sans mot de passe configuré, personne ne devient utilisateur.
- `hmac.compare_digest`, support SHA-256, limitation de débit (5 tentatives puis verrouillage),
  expiration de session, rejet d'un rôle forgé sans horodatage valide.
- **Défense en profondeur** : utilisateur MySQL distinct en lecture seule pour les visiteurs, refus de
  repli `[mysql_readonly]` sur les variables d'environnement `DB_*`.
- Contrôles **serveur** (`require_editor`) et non masquages CSS ; le rôle ne provient jamais de l'URL.
- Secret non journalisé ; `.env` et `secrets.toml` exclus par `.gitignore`.

**Limites**
- Pas de **journal d'audit** (qui a fait quoi, quand) au-delà des messages en base.
- Pas de rotation/revocation des secrets gérés par l'application ; pas d'en-têtes de sécurité
  applicatifs (CSP, HSTS).
- Le mode « lecture seule » repose sur une configuration serveur (MySQL) : l'application ne peut pas
  prouver à elle seule que l'utilisateur n'écrit pas.

### 5. Données, ingestion & intégrité — **Niveau 4**

**Acquis**
- **Chaîne complète** : client REST authentifié + robot Playwright + achat/synchronisation d'études,
  pilotés par un **catalogue de 91 études** et un **sac à dos à utilité marginale** (choix des études
  à acheter sous contrainte budgétaire).
- **Zéro invention** : aucune URL inventée, aucun payload illisible traité comme acquis, tout repli
  est signalé avec sa source.
- **Zéro fuite temporelle** : la référence M-1 ne cherche jamais vers l'avenir ; le contexte temporel
  est un module dédié, testé, avec diagnostic.
- **Dégradation gracieuse** : chaque source de données est isolée ; l'échec d'une famille n'entraîne
  pas les autres ; les NULL des lignes anciennes sont tolérés.
- **Traçabilité** : chaque cifra affiche sa source (« coût unitaire : productionCostDetailStudy —
  Août (reconducté) », « repli sur le tour 6 », « VNC inconnue → plus/minus-value non calculée »).

**Limite pour le niveau 4** : pas de **schéma validé** pour les payloads d'études (un champ qui
change dans le portail passe silencieusement au repli) ni de catalogue de données formalisé.

### 6. Modélisation & analyse quantitative — **Niveau 3**

**Acquis**
- **Prévision** : ETS / Holt-Winters / niveau saisonnier, **sélection par backtest hors-échantillon**
  (MAE / WAPE / MAPE), intervalles de confiance à 95 %.
- **PDM** : calibration ridge en forme fermée, mise à jour en logit sur la part observée,
  explicitement **descriptive** et non causale — le code et la documentation le disent.
- **Capacité** : modèle à goulot par atelier, plafond plutôt qu'exclusion de scénario.
- **Finance** : `Decimal` de bout en bout, cycle de TVA observé, BFR reconstitué, CAF, dette.
- **Point d'équilibre** : seuil exact, imputation des charges fixes, marge de sécurité, mix sous
  contrainte, sensibilité au prix.

**Limites (le 3, et non le 4)**
- **L'incertitude n'est pas propagée** : les intervalles de prévision existent mais n'entrent ni dans le
  point d'équilibre ni dans la décision de capacité → un seuil affiché comme un point unique est
  en réalité une fourchette.
- **Deux heuristiques, pas deux optimisations** : le mix par choix glouton et le plafond de capacité
  proportionnel. Sur 5 produits et 3 ateliers, un programme linéaire donnerait un optimum supérieur ;
  l'écart n'est pas mesuré.
- Pas de **test statistique** sur les résidus de prévision, ni de calcul de volatilité des prix.
- L'optimiseur de PDM explore une grille : c'est un plan d'expérience, pas une preuve d'optimum.

### 7. Interface & expérience utilisateur — **Niveau 3**

**Acquis**
- Les écrans suivent le **chemin de décision** (marché → production → achats → RH → finance), pas
  l'organigramme des tables.
- **Explicabilité à l'écran** : diagnostic de contexte, alerte « demande > capacité », mention
  « marge non calculée », report des écarts, avertissement « point d'équilibre hors de portée ».
- Sauvegarde/chargement d'un scénario complet en un clic ; les leviers sont purgés au changement de
  mois (pas de fuite entre contextes).
- Mode visiteur dégradé mais fonctionnel ; erreurs affichées en clair, jamais de trace technique
  exposée.

**Limites**
- **Aucun test utilisateur** : l'ergonomie est celle de l'auteur, pas celle d'un utilisateur métier.
- Pas d'audit d'**accessibilité** (contrastes, navigation clavier, lecteurs d'écran) ni de responsive.
- Densité de widgets élevée dans le centre de décision ; pas de « vues par profil » (pilote/expert
  existe mais reste cosmétique sur certains blocs).
- Pas d'internationalisation ni de formatage localisé (montants en euros, dates en français).

### 8. Documentation & traçabilité — **Niveau 4**

**Acquis**
- **README de 441 lignes** : architecture (diagramme), méthodes, sécurité, installation, structure,
  tests, FAQ, résumé anglais.
- **Règle d'or appliquée** : *aucune constante de calage comptable n'est injectée*, *aucun chiffre sans
  source*, *aucun repli silencieux*.
- 17 modules documentés par des docstrings explicatifs, décisions techniques commentées en ligne.
- Stratégie d'achat documentaire formalisée (`DOCUMENT_STRATEGY.md`) et `version_info.py` (SHA git
  affiché dans l'interface → vérification du redéploiement).

**Limite pour le niveau 4** : pas de **décisions d'architecture tracées** (ADR), pas de changelog, la
documentation n'est pas versionnée avec le code, le catalogue des 91 études n'est pas documenté
endpoint par endpoint dans le dépôt.

### 9. Industrialisation, livraison & exploitation — **Niveau 2**

**Acquis**
- Historique Git lisible (158 commits), identité configurée, branches, `.gitignore` propre.
- `requirements.txt` / `requirements-dev.txt` / `packages.txt` / `.env.example` : l'environnement est
  reproductible.
- Banc d'essai automatisé exécutable sans infrastructure.

**Limites (le 2 — le point le plus faible du projet)**
- **Aucune intégration continue** : les 169 tests ne sont exécutés par aucun service ; rien ne bloque
  un push cassé.
- **Pas de conteneur ni de déploiement outillé** : la mise en ligne (Streamlit Cloud) est manuelle.
- **Pas de migrations versionnées** : le schéma est complété à l'exécution par `ALTER TABLE`
  (idempotent, mais non versionné, non réversible, non daté).
- **Pas de supervision** : aucun logging structuré, aucune métrique de latence/erreur, aucune alerte.
- Pas de mesure de performance : la page de prévision recalcule des backtests à chaque interaction.

### 10. Veille, réutilisation & ouverture — **Niveau 3**

**Acquis**
- Pile **open source** maîtrisée (Streamlit, pandas, statsmodels, SQLAlchemy, Playwright) plutôt que
  développement ex nihilo.
- **Méthodes standard** outillées et comparées (ETS vs Holt vs niveau saisonnier ; ridge vs
  alternatives ; sac à dos à utilité marginale), avec leurs limites énoncées.
- Projet **public** sur GitHub, historique lisible.

**Limites**
- Code **non empaqueté** : pas de `pyproject.toml`, pas de points d'entrée, pas de version de
  bibliothèque → le projet n'est pas réutilisable en dehors de son contexte.
- **Aucune licence** dans le dépôt, alors que le README renvoie à une autorisation écrite.
- Pas de changelog, pas de suivi d'issues structuré.

---

## 4. Ce qui distingue ce projet d'un simple « application fonctionnelle »

1. **Une doctrine explicite et appliquée** : zéro invention, zéro fuite temporelle, dégradation
   gracieuse, purification numérique, fail-closed. Ces règles sont des *tests*, pas des intentions.
2. **La donnée comme source de vérité** : ingestion complète, catalogue piloté, achats
   budgétaires, et non pas de copier-coller depuis le portail.
3. **Un modèle décisionnel complet** : la décision de prix se propage jusqu'à la trésorerie, au BFR et
   à la dette, avec les alertes correspondantes.
4. **Une explicabilité sans equal** : l'utilisateur sait toujours d'où vient un chiffre et ce qu'il
   signifie quand il manque une pièce.

## 5. Les 5 chantiers pour atteindre « Expert junior »

| # | Chantier | Domaine visé | Effort indicatif |
|---|---|---|---|
| 1 | **CI + conteneur + migrations versionnées** (GitHub Actions, `pyproject.toml`, Alembic ou table `schema_version`, health-check) | 9 → 4 | 1 semaine |
| 2 | **Propagation de l'incertitude** : intervalles de prévision → fourchette du point d'équilibre et de la capacité ; tests statistiques sur les résidus | 6 → 4 | 1 semaine |
| 3 | **Optimisation exacte** : remplacer les deux heuristiques (mix, plafond de capacité) par un programme linéaire résolu (scipy/HiGHS), en conservant l'heuristique comme repli | 6 → 4 | 3–5 jours |
| 4 | **Architecture** : sortir le cockpit d'un script de 2 500 lignes (repository + services + état de session typé), pages sans helpers dupliqués | 2 → 4 | 2–3 semaines |
| 5 | **Preuve d'usage** : protocole de test avec 3–5 utilisateurs métier, mesures d'ergonomie, ADR + changelog + licence | 7, 10 → 4 | 1 semaine + terus |

**Ordre recommandé** : 1 (industrialisation) d'abord — c'est le seul domaine où le projet peut
régresser sans que personne ne le voie ; puis 2 et 3 (la rigueur méthodologique, qui est le cœur
intellectuel du projet) ; 4 ensuite (il consolide 1–3) ; 5 en continu.

## 6. Limites de cette auto-évaluation

- Évaluation réalisée par l'auteur du code : **biais de familiarité** probable, notamment sur les
  domaines 1, 5 et 8.
- **Aucune validation externe** : ni jury, ni relecture par un pair, ni audit de sécurité.
- **Données non réelles** : le jeu de simulation remplace les données de production ; la robustesse
  réelle aux volumes, à la concurrence et aux pannes de l'API de production n'est pas démontrée.
- **Aucun utilisateur métier n'a validé l'ergonomie** ni la pertinence du point d'équilibre pour la
  prise de décision.
- La note est un **diagnostic de maturity logicielle**, pas une certification de compétence
  professionnelle.

---

*Évaluation réalisée sur la version `3b68786` — reproductible : chaque affirmation renvoie à un
fichier, un test ou un commit du dépôt.*
