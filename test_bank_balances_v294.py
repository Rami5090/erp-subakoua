from decimal import Decimal

from finance_engine import extract_bank_balances, extract_financial_state, extract_debt_balance

SOLDES = [{"Solde initial": 1200000.0, "Solde final": 951413.5}]


def wrap(banque):
    return {"etat_actuel": {"banque_assurance": banque}}


def test_nominal_structure():
    d = wrap({"Banque": {"Extrait bancaire": {"Soldes bancaires": SOLDES}}})
    assert extract_bank_balances(d) == (Decimal("1200000.0"), Decimal("951413.5"))


def test_missing_tab_level():
    d = wrap({"Extrait bancaire": {"Soldes bancaires": SOLDES}})
    assert extract_bank_balances(d)[1] == Decimal("951413.5")


def test_accents_case_and_renamed_table():
    d = wrap({"BANQUE": {"extrait  bancaire": {"Tableau_1": [{"solde INITIAL": "1 200 000,00", "Solde Final": "951 413,50"}]}}})
    assert extract_bank_balances(d) == (Decimal("1200000.00"), Decimal("951413.50"))


def test_unknown_layout_last_resort():
    d = wrap({"Autre onglet": {"Document X": {"Soldes": [{"Solde initial": 10, "Solde final": 20}]}}})
    assert extract_bank_balances(d) == (Decimal("10"), Decimal("20"))


def test_absent_returns_none():
    assert extract_bank_balances(wrap({})) == (None, None)
    assert extract_bank_balances({}) == (None, None)


def test_financial_state_usable_with_bank_only():
    st = extract_financial_state("Année 1 - Juillet", wrap({"Extrait bancaire": {"Soldes bancaires": SOLDES}}), target_tour_id=8)
    assert st.solde_bancaire_final == Decimal("951413.5")
    assert st.solde_bancaire_initial == Decimal("1200000.0")


def test_expert_comptable_path_tolerant():
    d = {"etat_actuel": {"expert_comptable": {"Bilan détaillé": {"Tableau_1": [
        {"Passif": "Emprunts mensualisés contractés", "Exercice N": 500000}]}}}}
    assert extract_debt_balance(d) == Decimal("500000.00")


def test_no_cross_document_leak_on_missing_levels():
    from finance_engine import extract_savings_balance
    d = {"etat_actuel": {"expert_comptable": {"Autre doc": {"Tableau_1": [
        {"Actif": "Compte épargne", "Net exercice N": 999}]}}}}
    assert extract_savings_balance(d) is None   # « Bilan détaillé » absent : pas de lecture d'un autre tableau
