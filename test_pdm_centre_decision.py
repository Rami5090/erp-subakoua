"""Part de marché du centre de décision, de bout en bout (banc d'essai).

Le scénario « pdm_calibree » ajoute au jeu d'essai annuel un benchmark
concurrentiel mensuel complet (`rapport_jeu_essai.benchmark_concurrentiel`) :
la calibration aboutit (72 observations sur 12 périodes) et l'Integral 5 est
reconnu comme produit du segment. Les leviers observés de l'entreprise
(285 €, 72 000 €, 4 300 €) diffèrent volontairement de ses décisions M-1
(280 €, 70 000 €, 4 000 €). Les leviers sont forcés par SIM_DECISIONS.

Invariants protégés :
* sans changement de levier, PDM potentielle = PDM de référence — l'écart
  entre décision M-1 et observation ne crée aucune variation artificielle ;
* une baisse du prix du produit du segment fait varier la PDM selon le signe
  du coefficient de prix calibré (négatif ici : la PDM augmente) ;
* les leviers d'un autre produit ne déplacent pas la PDM du segment ;
* le cache de la préparation ne change pas le résultat.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def _run(decisions=None, **env_sup):
    env = dict(os.environ)
    for k in ("SIM_PAGE", "SIM_TOUR", "SIM_CLICK", "SIM_DECISIONS", "SIM_ENTRE_EXECUTIONS", "SIM_CACHE", "SIM_REPEAT"):
        env.pop(k, None)
    env["SIM_MONTH"] = "Janvier N+2"  # référence M-1 = Année 1 - Décembre, dernier mois du jeu d'essai
    if decisions:
        env["SIM_DECISIONS"] = json.dumps(decisions)
    env.update(env_sup)
    proc = subprocess.run([sys.executable, "simulator_smoke_runner.py", "pdm_calibree"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    lignes = [l for l in proc.stdout.splitlines() if l.startswith("##JSON##")]
    assert lignes, (proc.stdout[-2000:] + "\n" + proc.stderr[-3000:])
    data = json.loads(lignes[0][len("##JSON##"):])
    assert data["errors"] == []
    return data


def _pdm(data) -> dict[str, float]:
    metriques = {m[0]: m[1] for m in data["metrics"]}
    return {k: float(metriques[k].replace("%", "").strip())
            for k in ("PDM de référence", "PDM potentielle", "PDM réalisable")}


def _table(data, colonne):
    """Tableau du détail du calcul de PDM dont la première colonne est ``colonne``."""
    return next(t for t in data["tables"] if t and colonne in t[0])


def _levier(data, prefixe):
    return next(l for l in _table(data, "Levier") if l["Levier"].startswith(prefixe))


@pytest.fixture(scope="module")
def neutre():
    return _run()


def test_la_calibration_aboutit_et_le_produit_du_segment_est_reconnu(neutre):
    avertissements = [w for w in neutre["warnings"] if "PDM" in w]
    assert not [w for w in avertissements if "Calibration impossible" in w or "non identifiable" in w
                or "par défaut" in w], avertissements
    assert _levier(neutre, "Prix")["Levier"] == "Prix (Integral 5)"
    assert _pdm(neutre)["PDM de référence"] > 0


def test_sans_changement_de_levier_la_pdm_potentielle_egale_la_reference(neutre):
    pdm = _pdm(neutre)
    assert pdm["PDM potentielle"] == pdm["PDM de référence"]
    # ... alors même que la base observée diffère des décisions M-1.
    for prefixe in ("Prix", "Publicité de marque", "Publicité produit"):
        levier = _levier(neutre, prefixe)
        assert levier["Base observée"] != levier["Décision M-1"]
        assert levier["Candidat"] == levier["Base observée"]


def test_une_baisse_du_prix_du_segment_suit_le_signe_du_coefficient_calibre(neutre):
    beta_prix = next(c["Coefficient"] for c in _table(neutre, "Variable") if c["Variable"] == "Prix")
    assert beta_prix < 0  # le jeu d'essai est construit avec un prix qui pèse négativement

    baisse = _run({"sim_pm_i5": 252.0})  # −10 % sur l'Integral 5, produit du segment

    ecart = _pdm(baisse)["PDM potentielle"] - _pdm(neutre)["PDM de référence"]
    assert ecart != 0
    assert (ecart > 0) == (beta_prix < 0)
    prix = _levier(baisse, "Prix")
    assert prix["Candidat"] == pytest.approx(prix["Base observée"] * 252.0 / prix["Décision M-1"])


def test_les_leviers_d_un_autre_produit_ne_deplacent_pas_la_pdm_du_segment(neutre):
    autre = _run({"sim_pm_s3": 99.0, "sim_pub_s3": 20_000.0})

    assert _pdm(autre)["PDM potentielle"] == _pdm(neutre)["PDM de référence"]


def test_le_cache_ne_change_pas_la_pdm_calibree():
    decisions = {"sim_pm_i5": 252.0}
    sans_cache = _run(decisions)
    avec_cache = _run(decisions, SIM_CACHE="1", SIM_REPEAT="2")  # 2e exécution servie par le cache

    assert _pdm(avec_cache) == _pdm(sans_cache)
    candidats = lambda data: [l["Candidat"] for l in _table(data, "Levier")]  # noqa: E731
    assert candidats(avec_cache) == candidats(sans_cache)
