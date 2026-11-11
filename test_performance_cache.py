"""Performance et fraîcheur des caches : le cockpit et le rapport ne relisent la
base que lorsqu'elle change, et un changement réel est toujours vu.

Le banc d'essai imite le cache de Streamlit (SIM_CACHE=1 : clé = nom de la
fonction + paramètres SANS tiret bas, cache conservé d'une exécution à l'autre)
et rejoue la page plusieurs fois (SIM_REPEAT), comme autant d'interactions.

Régressions protégées :
* le cache des études ignorait sa signature (paramètre « _signature », exclu
  de la clé par Streamlit) : une étude resynchronisée n'était jamais relue ;
* le calcul de PDM retéléchargeait `erp_donnees` et `erp_etudes` en entier et
  refaisait calibration et prévisions à chaque interaction.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(scenario, **env_sup):
    env = dict(os.environ)
    for k in ("SIM_PAGE", "SIM_TOUR", "SIM_CLICK", "SIM_DECISIONS", "SIM_ENTRE_EXECUTIONS"):
        env.pop(k, None)
    env.update({"SIM_CACHE": "1", "SIM_REPEAT": "2"}, **env_sup)
    proc = subprocess.run([sys.executable, "simulator_smoke_runner.py", scenario], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-3000:])
    return json.loads(lignes[0][len("##JSON##"):])


def _resync(periode, study_id, payload):
    texte = json.dumps(payload).replace("'", "''")
    return (f"UPDATE erp_etudes SET payload = '{texte}', fetched_at = '2026-11-11 08:00:00' "
            f"WHERE periode = '{periode}' AND study_id = '{study_id}';")


def test_une_interaction_du_cockpit_ne_relit_pas_toute_la_base():
    data = _run("rapport_annee", SIM_MONTH="Juillet N+1")
    premiere, seconde = data["sql_par_execution"]
    assert data["errors"] == []
    assert seconde < premiere
    assert seconde <= 20, f"{seconde} requêtes pour une simple interaction"


def test_le_cache_ne_change_pas_les_resultats_du_cockpit():
    sans_cache = _run("rapport_annee", SIM_MONTH="Juillet N+1", SIM_CACHE="0", SIM_REPEAT="1")
    avec_cache = _run("rapport_annee", SIM_MONTH="Juillet N+1")
    assert avec_cache["metrics"] == sans_cache["metrics"]


def test_une_etude_resynchronisee_est_relue_par_le_cockpit():
    """L'audit RH de juin passe de 72 (+6) à 90 entre deux interactions : le score affiché suit."""
    payload = {"studyId": "enprgeqlevss", "readAllowed": True, "totalStaffQuality": {"value": 90.0},
               "teamPayroll": 40000.0, "standardPayroll": 42000.0}
    data = _run("rapport_annee", SIM_MONTH="Juillet N+1",
                SIM_ENTRE_EXECUTIONS=_resync("Année 1 - Juin", "enprgeqlevss", payload))
    metriques = {m[0]: m[1] for m in data["metrics"]}
    assert metriques["Score RH Production"] == "90.0 / 100"


def test_le_rapport_relit_une_etude_resynchronisee_et_reste_econome():
    payload = {"studyId": "monitoring", "readAllowed": True, "marketShares": 20.0}
    data = _run("rapport_annee", SIM_PAGE="8_Rapport_Professionnel.py",
                SIM_DECISIONS=json.dumps({"rp_debut": "Année 1 - Janvier", "rp_fin": "Année 1 - Décembre"}),
                SIM_ENTRE_EXECUTIONS=_resync("Année 1 - Décembre", "monitoring", payload))
    metriques = {m[0]: m[1] for m in data["metrics"]}
    assert metriques["Part de marché finale"].replace(" ", " ") == "20,00 %"
    stable = _run("rapport_annee", SIM_PAGE="8_Rapport_Professionnel.py")
    assert stable["sql_par_execution"][1] <= 4                 # empreintes seulement
