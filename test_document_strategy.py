import document_strategy
import document_optimizer as optimizer


def test_strategy_matrix_has_all_catalog_studies():
    rows = document_strategy.build_matrix()
    assert len(rows) == 91
    assert len({r['study_id'] for r in rows}) == 91


def test_strategy_contains_known_endpoint_metadata():
    rows = document_strategy.build_matrix()
    known = [r for r in rows if r['endpoint_known']]
    assert len(known) == 54
    assert all(r['mapping_confidence'] >= 0.95 for r in known)


def test_free_and_bought_baseline_is_counted_before_paid_plan():
    profiles = optimizer.load_profiles()
    rows = [
        {'study_id': 'companyStock', 'price': 0, 'boughtByTeam': False},
        {'study_id': 'pevcvipm', 'price': 250, 'boughtByTeam': False},
        {'study_id': 'bankStatements', 'price': 0, 'boughtByTeam': True},
    ]
    snap = optimizer.build_coverage_snapshot(profiles, rows)
    assert snap['available_document_count'] == 2
    assert 0 <= snap['coverage_percent'] <= 100
