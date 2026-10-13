# ERP Subakoua — v2.9.5 (scraper 2.4.1)

## Constat sur l'export `erp_donnees` fourni

3 lignes seulement (Année 1 - Janvier : marketing, production, rh). Leur contenu ne contient que la barre de
recherche et le sélecteur de période (`__lignes__` vide, `url = …/companies`) : **le scraper v2.4.0 n'est jamais entré
dans les pages de décisions**, a sélectionné « Janvier » dans le sélecteur de l'accueil, puis a enregistré cette page
vide comme une réussite. En mode incrémental, ces lignes auraient en plus été considérées « à jour ».

## Correctifs scraper

| Problème | Correctif |
|---|---|
| Aucune vérification que la tuile a ouvert un formulaire. | `ouvrir_decision` essaie 3 stratégies (clic JS, **vrai clic souris**, recherche par texte) et n'en valide aucune sans formulaire rendu. |
| Tuile qui ouvre un **nouvel onglet** : le scraper restait sur l'accueil. | Détection du nouvel onglet, extraction dessus, fermeture ensuite. |
| Rendu Angular différé. | Attente active (8 s) d'un formulaire avant extraction. |
| Page vide stockée comme succès. | Extraction sans champ exploitable = **erreur** (jamais écrite en base) ; le rapport de run liste les mois en échec. |
| Lignes vides déjà en base jamais refaites. | La détection « ancien format » exige désormais un contenu réel : les 3 lignes vides seront ré-extraites sans `--force`. |
| Échec sans moyen de comprendre. | Capture + HTML sauvegardés à l'échec, et nouveau mode `--probe-decisions` (voir ci-dessous). |

## Mode sonde (aucune écriture en base)

```bash
python scraper_subakoua.py --probe-decisions --show-browser
```

Produit `scraper_output/probe_<date>/` : `probe_report.json` (inventaire des tuiles avec classes, cursor, href, routerlink ;
pour chaque stratégie : URL avant/après, nouvel onglet, nombre de champs), captures `.png`, pages `.html`, et l'extraction
brute des formulaires atteints. **Zippe ce dossier** : il permet de calibrer scraper et parseur sur le vrai site.

## Simulateur

Une extraction de décisions vide est signalée en alerte (« extraction VIDE en base ») au lieu d'être confondue avec un
format inconnu ; les valeurs retombent sur la dernière ligne SQL connue, sans valeur inventée.

## Tests ajoutés

`test_scraper_navigation_v294.py` (Playwright factice : clic sans effet → clic souris, nouvel onglet, échec total),
`fixtures/decisions_page_vide_reelle.json` (ligne réelle du CSV), scénario « décisions vides » du test de bout en bout.
Les scripts JavaScript de détection ont été exécutés dans Chrome sur une page factice.

## À savoir

Le DOM réel de Subakoua n'a pas été inspecté : les stratégies d'ouverture sont des hypothèses raisonnables, vérifiées
par le code mais pas encore confirmées sur le site.
