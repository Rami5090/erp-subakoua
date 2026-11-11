"""Historique de calibration de la PDM : borné à l'ancrage, une observation par benchmark réel.

Régressions protégées (`optimizer_engine.build_competitive_history`) :

* fuite temporelle — l'historique lisait toutes les périodes en base, y compris
  celles postérieures à l'ancrage (M-1 du cockpit, période d'ancrage de
  l'Optimiseur) ;
* doublons — un mois sans benchmark propre se replie sur le dernier benchmark
  antérieur ; ses lignes étaient étiquetées avec le mois interrogé, si bien
  qu'un même benchmark comptait plusieurs fois et gonflait ``n_obs``,
  ``n_periods``, donc la confiance de la calibration et le facteur de
  rétrécissement de `predict_anchored_share`.

Jeu d'essai : des données existent de janvier à avril, mais le benchmark
concurrentiel (étude « Facteurs clés ») n'est publié qu'en janvier et en mars.
"""
import pytest

from forecast_engine import extract_competitive_snapshot, ordered_periods
from optimizer_engine import build_competitive_history, calibrate_descriptive_response

JANVIER, FEVRIER, MARS, AVRIL = (f"Année 1 - {m}" for m in ("Janvier", "Février", "Mars", "Avril"))
ENTREPRISES = (1, 2, 3, 4, 5, 6)
NUMERO = 3


def _benchmark(decalage: float) -> dict:
    """Étude « Facteurs clés » : leviers et parts des six entreprises pour un mois."""
    return {"Facteurs clés": [
        {
            "Entreprise": str(e), "Ventes": 1_000 + 100 * e, "part de marché": 10.0 + e + decalage,
            "Prix (€)": 250.0 + 10 * e + decalage, "Qualité": 50.0 + e,
            "Publicité de marque": 50_000.0 + 5_000 * e, "Publicité produit (€)": 3_000.0 + 200 * e,
        }
        for e in ENTREPRISES
    ]}


ETUDES = {JANVIER: {"pevcfcpfquca": _benchmark(0.0)}, MARS: {"pevcfcpfquca": _benchmark(1.0)}}
#: Chaque mois a des données (ici un relevé bancaire), mais pas forcément de benchmark.
DONNEES = {p: {"banque_assurance": {"Soldes bancaires": [{"Solde initial": 1.0, "Solde final": 2.0}]}}
           for p in (JANVIER, FEVRIER, MARS, AVRIL)}


@pytest.fixture(params=["optimiseur", "cockpit"])
def sources(request):
    """Les deux dispositions des appelants.

    L'Optimiseur garde les études à part ; le cockpit (`_preparer_pdm`) les injecte
    en plus dans les données par période, sous forme de modules ``etude_<id>``.
    """
    donnees = {p: dict(modules) for p, modules in DONNEES.items()}
    if request.param == "cockpit":
        for p, etudes in ETUDES.items():
            for sid, payload in etudes.items():
                donnees.setdefault(p, {})[f"etude_{sid}"] = payload
    periodes = ordered_periods(set(donnees) | set(ETUDES))
    assert periodes == [JANVIER, FEVRIER, MARS, AVRIL]

    def snapshot(p):
        return extract_competitive_snapshot(donnees, own_company_number=NUMERO, study_data=ETUDES, anchor_period=p)

    return donnees, periodes, snapshot


def _historique(sources, **kwargs):
    donnees, periodes, snapshot = sources
    return build_competitive_history(donnees, NUMERO, snapshot, periods=periodes, **kwargs)


# --------------------------------------------------------------------------- doublons
def test_un_mois_sans_benchmark_ne_recopie_pas_le_benchmark_precedent(sources):
    hist = _historique(sources)

    # Janvier → avril interrogés, mais seulement deux benchmarks réels : 2 périodes, pas 4.
    assert ordered_periods(hist["periode"].unique()) == [JANVIER, MARS]
    assert len(hist) == 2 * len(ENTREPRISES)
    assert not hist.duplicated(["periode", "entreprise"]).any()


def test_chaque_observation_porte_la_periode_reelle_de_son_benchmark(sources):
    donnees, _, snapshot = sources

    # Février et avril, sans benchmark propre, se replient sur janvier et mars : interrogés
    # seuls, ils rendent les lignes de ces benchmarks, étiquetées avec la période du
    # benchmark et non avec le mois interrogé.
    hist = build_competitive_history(donnees, NUMERO, snapshot, periods=[FEVRIER, AVRIL])

    assert ordered_periods(hist["periode"].unique()) == [JANVIER, MARS]
    for periode, decalage in ((JANVIER, 0.0), (MARS, 1.0)):
        lignes = hist.loc[hist["periode"] == periode].set_index("entreprise").sort_index()
        assert list(lignes.index) == list(ENTREPRISES)
        assert list(lignes["prix"]) == [250.0 + 10 * e + decalage for e in ENTREPRISES]
    assert set(hist.loc[hist["is_own"], "entreprise"]) == {NUMERO}


def test_les_recopies_ne_gonflent_pas_la_calibration(sources):
    cal = calibrate_descriptive_response(_historique(sources))

    assert (cal.n_obs, cal.n_periods) == (12, 2)
    # Avec les recopies (24 observations sur 4 périodes), la confiance passait à « modérée ».
    assert cal.confidence == "faible"


# --------------------------------------------------------------------------- fuite temporelle
@pytest.mark.parametrize("ancrage, attendu", [
    (JANVIER, [JANVIER]),
    (FEVRIER, [JANVIER]),          # février se replie sur janvier, déjà compté
    (MARS, [JANVIER, MARS]),
    (AVRIL, [JANVIER, MARS]),
])
def test_l_historique_s_arrete_a_l_ancrage(sources, ancrage, attendu):
    hist = _historique(sources, anchor_period=ancrage)

    assert ordered_periods(hist["periode"].unique()) == attendu
    assert len(hist) == len(attendu) * len(ENTREPRISES)


def test_les_periodes_posterieures_a_l_ancrage_ne_sont_pas_interrogees(sources):
    donnees, periodes, snapshot = sources
    interrogees = []

    def espion(p):
        interrogees.append(p)
        return snapshot(p)

    build_competitive_history(donnees, NUMERO, espion, periods=periodes, anchor_period=FEVRIER)

    assert interrogees == [JANVIER, FEVRIER]  # ordre chronologique, rien après l'ancrage


def test_un_benchmark_posterieur_a_l_ancrage_est_ecarte_meme_s_il_est_renvoye(sources):
    donnees, periodes, snapshot = sources

    # Un snapshot qui se replierait vers l'avant (ex. libellé de période non reconnu, que
    # `extract_competitive_snapshot` rabat sur la dernière période en base) ne doit pas
    # réintroduire la fuite : seule compte la période réelle du benchmark.
    hist = build_competitive_history(donnees, NUMERO, lambda p: snapshot(MARS), periods=[JANVIER, FEVRIER],
                                     anchor_period=FEVRIER)

    assert hist.empty
