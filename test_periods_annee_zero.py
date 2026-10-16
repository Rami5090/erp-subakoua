"""Année 0 : le socle documentaire remonte à Année 0 - Janvier (tour 0 = Décembre 0)."""
from constants import (
    PERIOD_INDEX,
    TOURS_PERIODES,
    next_period,
    try_next_period,
    period_label_to_code,
    period_code_to_label,
    period_month_id,
)


def test_annee_zero_dans_index_et_tours():
    assert PERIOD_INDEX["Année 0 - Janvier"] < PERIOD_INDEX["Année 0 - Décembre"]
    assert PERIOD_INDEX["Année 0 - Décembre"] < PERIOD_INDEX["Année 1 - Janvier"]
    assert TOURS_PERIODES[0] == "Année 0 - Décembre"


def test_fenetre_achat_janvier_passe_par_decembre_zero():
    assert try_next_period("Année 1 - Janvier", -1) == "Année 0 - Décembre"


def test_codes_annee_zero_aller_retour():
    assert period_label_to_code("Année 0 - Janvier") == "0001"
    assert period_label_to_code("Année 0 - Décembre") == "0012"
    assert period_code_to_label("0001") == "Année 0 - Janvier"
    assert period_code_to_label("0012") == "Année 0 - Décembre"
    assert period_month_id("Année 0 - Décembre") == 12


def test_plancher_annee_zero_janvier():
    try:
        next_period("Année 0 - Janvier", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("sous Année 0 - Janvier : ValueError attendue")
