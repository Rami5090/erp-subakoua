# ERP Subakoua — v2.9.2

## 1. Correctifs de l'optimiseur stratégique

**Symptôme (capture d'écran)** : `PDM segment observée = —` et message « Aucune PDM segmentielle exploitable pour l'ancrage », alors que 8 concurrents étaient reconnus. Le moteur s'arrêtait avant d'évaluer le moindre scénario.

| # | Cause | Correctif |
|---|-------|-----------|
| 1 | La PDM segmentielle n'était lue que dans la colonne « part de marché » de la ligne de l'équipe. Si l'équipe était absente du benchmark ou sans colonne PDM, le résultat était `None`. | `resolve_reference_share` : PDM du benchmark → PDM reconstruite (ventes ÷ total benchmark) → dernière PDM propre observée ≤ ancrage → PDM globale du tableau de bord (signalée comme approximative). La source est affichée à l'écran. |
| 2 | La PDM **globale** du tableau de bord pouvait être prise pour une PDM **segmentielle** dès qu'une étude `pevc*` existait pour le mois. | Drapeau explicite `own_is_synthetic` : une ligne propre fabriquée depuis le monitoring n'est plus jamais présentée comme segmentielle. |
| 3 | Les concurrents n'étaient recherchés dans les mois antérieurs que dans `erp_etudes`, jamais dans `erp_donnees`. | Repli sur l'historique legacy. |
| 4 | Sans colonne de PDM, toutes les lignes étaient éliminées de l'historique → calibration « Non disponible ». | La PDM est reconstruite par période à partir des ventes. |
| 5 | La ligne **« Réel »** de l'ancrage était incluse dans le forecast d'optimisation : 13 mois évalués au lieu de 12, capacité calculée sur un mois déjà réalisé, dépense annuelle faussée. | Seules les lignes « Prévision » sont utilisées. |
| 6 | Le produit du segment retombait silencieusement sur « Shorty 3 » s'il n'était pas identifié. | `detect_segment_product` : sinon l'effet PDM est appliqué à toute la gamme. |
| 7 | Prix / qualité / publicité de départ remplacés silencieusement par 115 / 57,2 / 55 000 / 3 000. | `resolve_own_levers` : snapshot → dernière observation de l'historique → valeur par défaut **signalée**. |
| 8 | Colonne `rang` = index d'évaluation, pas le rang après tri. | Rang séquentiel 1…n. |
| 9 | Le message WAPE affichait le nombre de mois réels, pas de mois testés. | Affiche les mois réellement testés et le WAPE de la méthode retenue. |

## 2. Interface Visiteur / Utilisateur

- **Visiteur (défaut)** : lecture seule. Voit l'état des lieux, le pilotage 12 mois, l'optimiseur, et peut simuler **sans rien enregistrer**.
- **Utilisateur** : après saisie du mot de passe (barre latérale → « 🔐 Connexion utilisateur »), accès complet : saisie des données réelles, sauvegarde de scénarios, scraper / achat d'études.

Protections **côté serveur** (pas seulement des boutons masqués) :

- `pages/2_Scraper.py` (achats d'études à plusieurs milliers d'euros + écriture en base) : `require_editor()` en tête de page.
- Module « Données réelles » : retiré du menu visiteur **et** bloqué par `require_editor()`.
- `sauvegarder_scenario_seed` refuse l'écriture pour un visiteur, et le bouton « Sauver » est désactivé.
- Création de tables (`init_db_simu`) réservée aux utilisateurs.
- Fail-closed : sans mot de passe configuré, personne ne peut devenir utilisateur.
- Comparaison à temps constant, verrouillage 60 s après 5 échecs, expiration de session à 8 h.
- Le rôle n'est jamais lu depuis l'URL.
- **Recommandé** : section `[mysql_readonly]` (utilisateur MySQL SELECT-only). Les visiteurs et les pages de consultation l'utilisent, donc même une faille applicative ne permettrait pas d'écrire.

### Configuration (Streamlit Cloud → Settings → Secrets)

Voir `.streamlit/secrets.toml.example`. Minimum :

```toml
[auth]
user_password = "choisis-un-mot-de-passe-solide"
```

> Sur un dépôt **public**, ne mets jamais de mot de passe dans le code ; uniquement dans les Secrets.

### Créer l'utilisateur MySQL lecture seule (Aiven)

```sql
CREATE USER 'erp_readonly'@'%' IDENTIFIED BY '...';
GRANT SELECT ON ta_base.* TO 'erp_readonly'@'%';
```

## 3. Nouveaux fichiers

`auth_core.py` (logique pure testée), `auth.py` (UI + garde), `db_utils.py` (moteur lecture seule), `.streamlit/secrets.toml.example`, `requirements-dev.txt`, `test_segment_reference_v292.py`, `test_auth_core.py`, `test_auth_roles.py`.

## 4. Vérification

Les tests qui dépendent du dump local `/mnt/data/subakoua_full_database_dump.json` sont désormais ignorés (skip) si le fichier est absent. Lancer : `pip install -r requirements-dev.txt && pytest -q`.

## 5. Déploiement

```powershell
cd "C:\Users\ramih\Documents\erp-subakoua-git"
git add .
git commit -m "v2.9.2 : fix PDM optimiseur + roles visiteur/utilisateur"
git push
```

Puis ajouter `[auth]` (et idéalement `[mysql_readonly]`) dans les Secrets Streamlit Cloud, et **redémarrer** l'app.
