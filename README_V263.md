# ERP Subakoua — Pilotage stratégique 12M v2.6.3

Correctifs :
- lecture robuste des payloads API imbriqués ;
- saisonnalité structurelle `peeumreusreprv` avec plusieurs fallbacks ;
- prévision à 1 observation : niveau ancré × saisonnalité structurelle ;
- sélection concurrentielle calée sur la période d’ancrage ;
- récupération du PDM depuis `monitoring` lorsque disponible ;
- diagnostic explicite des sources manquantes.

Tests : 11 tests forecast/pilotage PASS, compilation PASS.
