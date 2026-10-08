from study_ui_routes import routes_for
import pandas as pd
from pathlib import Path


def test_june_month_id_route_is_18():
    rows = routes_for('peeefsisp', 18)
    assert rows
    assert rows[0]['url'].endswith('/incomeTax/18/MONTHLY')


def test_direct_route_for_external_expenses():
    rows = routes_for('peeedcahsx', 18)
    assert rows
    assert rows[0]['url'].endswith('/otherPurchasesExternalExpenses/18/MONTHLY')


def test_direct_route_for_potential_market_study():
    rows = routes_for('potentialMarketStudy', 18)
    assert rows
    assert rows[0]['url'].endswith('/potentialMarketStudy/18/MONTHLY')


def test_direct_route_for_competitor_brand_ads():
    rows = routes_for('pevcvicppq', 18)
    assert rows
    assert rows[0]['url'].endswith('/marketingComparison/brandAdvertisingComparison/18/MONTHLY')
    assert rows[0]['source'] == 'audit'


def test_direct_route_for_july():
    rows = routes_for('pevcfcpfpuca', 19)
    assert rows
    assert rows[0]['url'].endswith('/keyFactors/pricesAdvertisingTurnover/19/MONTHLY')


def test_catalog_all_studies_have_route_record_or_explicit_ui_exception():
    root = Path(__file__).resolve().parent
    cat = pd.read_csv(root / 'subakoua_study_catalog.csv')
    routes_json = __import__('json').loads((root / 'study_ui_routes.json').read_text(encoding='utf-8'))['routes']
    missing = []
    for sid in cat['study_id'].astype(str):
        if not routes_for(sid, 18) and routes_json.get(sid, {}).get('ui_supported', True):
            missing.append(sid)
    assert missing == []


def test_company_results_explicitly_marked_unresolved_ui_route():
    rows = routes_for('companyResults', 18)
    assert rows == []
