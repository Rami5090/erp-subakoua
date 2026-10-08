from study_ui_routes import routes_for


def test_june_month_id_route_is_18():
    rows = routes_for('peeefsisp', 18)
    assert rows
    assert rows[0]['url'].endswith('/incomeTax/18/MONTHLY')


def test_direct_route_for_external_expenses():
    rows = routes_for('peeedcahsx', 18)
    assert rows
    assert rows[0]['url'].endswith('/otherPurchasesExternalExpenses/18/MONTHLY')


def test_inferred_concurrency_route_has_no_period_selector():
    rows = routes_for('pevcvicppq', 18)
    assert rows
    assert rows[0]['source'] == 'inferred_family'
    assert '/concurrency/marketingComparison' in rows[0]['url']
    assert '{month}' not in rows[0]['url']


def test_unknown_study_has_no_fake_url():
    assert routes_for('potentialMarketStudy', 18) == []
