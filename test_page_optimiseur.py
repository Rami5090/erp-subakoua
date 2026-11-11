"""Page « Optimiseur stratégique » : la calibration ne lit aucune période postérieure à l'ancrage.

L'aide du sélecteur d'ancrage l'affirme (« le moteur interdit toute fuite vers
des périodes futures lors de la calibration ») ; l'historique de calibration
n'était pourtant pas borné. La page est exécutée dans le banc d'essai (sans
Streamlit ni MySQL) sur le scénario « pdm_calibree_futur » : benchmark
concurrentiel mensuel de janvier à décembre, plus un benchmark déjà publié pour
Année 2 - Janvier. L'ancrage est décembre, seul mois où le jeu d'essai publie
les ventes par produit — sans elles, la page s'arrête avant la calibration.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run_page(ancrage):
    env = dict(os.environ)
    for k in ("SIM_TOUR", "SIM_CLICK", "SIM_ENTRE_EXECUTIONS", "SIM_CACHE", "SIM_REPEAT"):
        env.pop(k, None)
    env["SIM_PAGE"] = "4_Optimiseur_Strategique.py"
    # Widgets sans clé : surcharges par libellé (numéro d'entreprise, période d'ancrage).
    env["SIM_DECISIONS"] = json.dumps({"N° entreprise": 3, "ancrage": ancrage}, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, "simulator_smoke_runner.py", "pdm_calibree_futur"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-2000:])
    data = json.loads(lignes[0][len("##JSON##"):])
    assert data["errors"] == []
    return data


def test_la_calibration_de_l_optimiseur_s_arrete_a_l_ancrage():
    data = _run_page("Année 1 - Décembre")
    metriques = {m[0]: m[1] for m in data["metrics"]}

    # 6 entreprises × 12 benchmarks (janvier → décembre). Celui d'Année 2 - Janvier,
    # postérieur à l'ancrage, n'entre pas dans la calibration (78 obs / 13 périodes sinon).
    assert metriques["Calibration"] == "72 obs / 12 période(s)"
    assert metriques["Concurrents reconnus"] == "5"
