import pandas as pd
import pytest

from period_context import (
    ReferenceDiagnostic, context_signature, describe_row, match_period,
    normalize_period, pick_reference_row, ref_int, ref_number, stale_keys,
)


def test_normalize_tolerates_dash_accent_case_spaces():
    ref = normalize_period("Année 1 - Août")
    for variant in ("Année 1 – Août", "ANNEE  1-aout", " année 1 — août ", "Année\u00a01 - Août"):
        assert normalize_period(variant) == ref


def test_match_period_prefers_exact_then_latest_variant():
    cands = [(1, "Année 1 – Août"), (2, "Année 1 - Août"), (3, "Année 1 - Juillet")]
    assert match_period(cands, "Année 1 - Août") == 2
    assert match_period([(1, "Année 1 – Août"), (5, "année 1 - aout")], "Année 1 - Août") == 5
    assert match_period(cands, "Année 1 - Septembre") is None


def test_pick_reference_row_exact_fallback_and_never_future():
    df = pd.DataFrame({"Tour_ID": [3, 5, 9], "x": [1.0, 2.0, 3.0]})
    exact = pick_reference_row(df, 5)
    assert exact.tour_utilise == 5 and not exact.is_fallback and exact.data["x"] == 2.0
    fb = pick_reference_row(df, 8)
    assert fb.tour_utilise == 5 and fb.is_fallback
    assert not pick_reference_row(df, 2).found           # le futur est ignoré
    assert not pick_reference_row(pd.DataFrame(), 4).found
    assert not pick_reference_row(None, 4).found


def test_ref_number_handles_null_nan_and_missing():
    assert ref_number({"a": None}, "a", 7.0) == 7.0
    assert ref_number({"a": float("nan")}, "a", 7.0) == 7.0
    assert ref_number({}, "a", 7.0) == 7.0
    assert ref_number({"a": "12,5"}, "a", 1.0) == 1.0     # illisible -> défaut
    assert ref_number({"a": 3}, "a", 1.0) == 3.0
    assert ref_int({"a": 2.6}, "a", 0) == 3


def test_context_signature_changes_with_tour_or_data():
    base = context_signature(9, 8, {"p": 1})
    assert base == context_signature(9, 8, {"p": 1})
    assert base != context_signature(10, 8, {"p": 1})
    assert base != context_signature(9, 8, {"p": 2})


def test_stale_keys_only_simulator_prefixes():
    keys = ["sim_s3", "ord_i3", "ui_mode_pilotage", "_vol_ref", "sp_e", 5]
    assert stale_keys(keys, ("sim_", "sp_", "ord_")) == ["sim_s3", "ord_i3", "sp_e"]


def test_describe_row_flags_fallback_and_missing():
    d = ReferenceDiagnostic()
    df = pd.DataFrame({"Tour_ID": [5]})
    describe_row("T", pick_reference_row(df, 8), d, lambda t: f"tour{t}")
    describe_row("T", pick_reference_row(df, 5), d, lambda t: f"tour{t}")
    describe_row("T", pick_reference_row(df, 1), d, lambda t: f"tour{t}")
    assert len(d.alerts) == 2 and len(d.notes) == 1
