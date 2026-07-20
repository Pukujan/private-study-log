"""Frozen tests for the objective aggregation-correctness checker (Stage-2 style lane).

LABEL AUTHORITY: pure-Python (decimal) recomputation of the grouped aggregation, never a
model/judge. These tests pin the checker's behavior on hand-picked cases (independent of the
fixture file) plus a full sweep over every fixture in fixtures_aggregation.py, asserting the
checker's objective_label always matches the fixture's declared expected_label (the same
self-validation gate every other Stage-2 lane uses).
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_aggregation_correctness.checker_aggregation import (  # noqa: E402
    check_record,
    compute_aggregation,
)
from evals.objective_aggregation_correctness.fixtures_aggregation import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the fixture file ---------------------------------------

def test_sum_basic_correct():
    rows = [
        {"region": "east", "amount": "10.00"},
        {"region": "east", "amount": "5.00"},
        {"region": "west", "amount": "1.00"},
    ]
    spec = {"group_by": ["region"], "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    candidate = [
        {"group": {"region": "east"}, "values": {"total": "15.00"}},
        {"group": {"region": "west"}, "values": {"total": "1.00"}},
    ]
    r = check_record(rows, spec, candidate)
    assert r.objective_label == "CORRECT"


def test_sum_wrong_total_is_incorrect():
    rows = [{"region": "east", "amount": "10.00"}, {"region": "east", "amount": "5.00"}]
    spec = {"group_by": ["region"], "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    candidate = [{"group": {"region": "east"}, "values": {"total": "14.00"}}]
    r = check_record(rows, spec, candidate)
    assert r.objective_label == "INCORRECT"


def test_avg_excludes_nulls_from_numerator_and_denominator():
    rows = [
        {"team": "x", "score": "80"},
        {"team": "x", "score": None},
        {"team": "x", "score": "60"},
    ]
    spec = {"group_by": ["team"], "aggregations": [{"output": "avg_score", "func": "avg", "column": "score"}]}
    candidate = [{"group": {"team": "x"}, "values": {"avg_score": "70.00"}}]
    r = check_record(rows, spec, candidate)
    assert r.objective_label == "CORRECT"


def test_avg_include_as_zero_convention_is_incorrect():
    rows = [
        {"team": "x", "score": "80"},
        {"team": "x", "score": None},
        {"team": "x", "score": "60"},
    ]
    spec = {"group_by": ["team"], "aggregations": [{"output": "avg_score", "func": "avg", "column": "score"}]}
    candidate = [{"group": {"team": "x"}, "values": {"avg_score": "46.67"}}]
    r = check_record(rows, spec, candidate)
    assert r.objective_label == "INCORRECT"


def test_avg_of_all_null_group_is_null_not_zero():
    rows = [{"team": "y", "score": None}, {"team": "y", "score": None}]
    spec = {"group_by": ["team"], "aggregations": [{"output": "avg_score", "func": "avg", "column": "score"}]}
    candidate_correct = [{"group": {"team": "y"}, "values": {"avg_score": None}}]
    candidate_wrong = [{"group": {"team": "y"}, "values": {"avg_score": "0.00"}}]
    assert check_record(rows, spec, candidate_correct).objective_label == "CORRECT"
    assert check_record(rows, spec, candidate_wrong).objective_label == "INCORRECT"


def test_sum_of_all_null_group_is_zero_not_null():
    rows = [{"team": "z", "bonus": None}, {"team": "z", "bonus": None}]
    spec = {"group_by": ["team"], "aggregations": [{"output": "total_bonus", "func": "sum", "column": "bonus"}]}
    candidate_correct = [{"group": {"team": "z"}, "values": {"total_bonus": "0.00"}}]
    candidate_wrong = [{"group": {"team": "z"}, "values": {"total_bonus": None}}]
    assert check_record(rows, spec, candidate_correct).objective_label == "CORRECT"
    assert check_record(rows, spec, candidate_wrong).objective_label == "INCORRECT"


def test_count_distinct_vs_count_duplicates():
    rows = [
        {"region": "east", "customer": "alice"},
        {"region": "east", "customer": "alice"},
        {"region": "east", "customer": "bob"},
    ]
    spec = {"group_by": ["region"],
            "aggregations": [{"output": "unique_customers", "func": "count_distinct", "column": "customer"}]}
    assert check_record(rows, spec, [{"group": {"region": "east"}, "values": {"unique_customers": 2}}]).objective_label == "CORRECT"
    assert check_record(rows, spec, [{"group": {"region": "east"}, "values": {"unique_customers": 3}}]).objective_label == "INCORRECT"


def test_count_star_includes_nulls_count_column_excludes():
    rows = [
        {"region": "west", "email": "a@x.com"},
        {"region": "west", "email": None},
        {"region": "west", "email": "b@x.com"},
    ]
    spec_star = {"group_by": ["region"], "aggregations": [{"output": "rows", "func": "count", "column": None}]}
    spec_col = {"group_by": ["region"], "aggregations": [{"output": "emails", "func": "count", "column": "email"}]}
    assert check_record(rows, spec_star, [{"group": {"region": "west"}, "values": {"rows": 3}}]).objective_label == "CORRECT"
    assert check_record(rows, spec_col, [{"group": {"region": "west"}, "values": {"emails": 2}}]).objective_label == "CORRECT"
    assert check_record(rows, spec_col, [{"group": {"region": "west"}, "values": {"emails": 3}}]).objective_label == "INCORRECT"


def test_filter_applied_before_grouping():
    rows = [
        {"region": "east", "status": "active", "amount": "100.00"},
        {"region": "east", "status": "cancelled", "amount": "500.00"},
        {"region": "west", "status": "active", "amount": "20.00"},
    ]
    spec = {"group_by": ["region"], "filter": {"column": "status", "op": "eq", "value": "active"},
            "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    correct = [
        {"group": {"region": "east"}, "values": {"total": "100.00"}},
        {"group": {"region": "west"}, "values": {"total": "20.00"}},
    ]
    ignored_filter = [
        {"group": {"region": "east"}, "values": {"total": "600.00"}},
        {"group": {"region": "west"}, "values": {"total": "20.00"}},
    ]
    assert check_record(rows, spec, correct).objective_label == "CORRECT"
    assert check_record(rows, spec, ignored_filter).objective_label == "INCORRECT"


def test_empty_group_omitted_not_phantom():
    rows = [
        {"region": "east", "status": "active", "amount": "100.00"},
        {"region": "west", "status": "cancelled", "amount": "500.00"},
    ]
    spec = {"group_by": ["region"], "filter": {"column": "status", "op": "eq", "value": "active"},
            "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    correct = [{"group": {"region": "east"}, "values": {"total": "100.00"}}]
    phantom = [
        {"group": {"region": "east"}, "values": {"total": "100.00"}},
        {"group": {"region": "west"}, "values": {"total": "0.00"}},
    ]
    assert check_record(rows, spec, correct).objective_label == "CORRECT"
    assert check_record(rows, spec, phantom).objective_label == "INCORRECT"


def test_min_max_compared_numerically_not_lexicographically():
    rows = [
        {"category": "a", "price": "9"},
        {"category": "a", "price": "10"},
        {"category": "a", "price": "2"},
    ]
    spec = {"group_by": ["category"], "aggregations": [{"output": "top_price", "func": "max", "column": "price"}]}
    assert check_record(rows, spec, [{"group": {"category": "a"}, "values": {"top_price": "10"}}]).objective_label == "CORRECT"
    assert check_record(rows, spec, [{"group": {"category": "a"}, "values": {"top_price": "9"}}]).objective_label == "INCORRECT"


def test_min_vs_max_function_swap_is_incorrect():
    rows = [{"category": "b", "price": "30"}, {"category": "b", "price": "5"}, {"category": "b", "price": "18"}]
    spec = {"group_by": ["category"], "aggregations": [{"output": "cheapest", "func": "min", "column": "price"}]}
    assert check_record(rows, spec, [{"group": {"category": "b"}, "values": {"cheapest": "5"}}]).objective_label == "CORRECT"
    assert check_record(rows, spec, [{"group": {"category": "b"}, "values": {"cheapest": "30"}}]).objective_label == "INCORRECT"


def test_wrong_group_key_produces_mismatched_partition():
    rows = [
        {"region": "east", "category": "a", "amount": "10.00"},
        {"region": "east", "category": "b", "amount": "20.00"},
        {"region": "west", "category": "a", "amount": "5.00"},
    ]
    spec = {"group_by": ["region"], "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    correct = [
        {"group": {"region": "east"}, "values": {"total": "30.00"}},
        {"group": {"region": "west"}, "values": {"total": "5.00"}},
    ]
    wrong_key = [
        {"group": {"category": "a"}, "values": {"total": "15.00"}},
        {"group": {"category": "b"}, "values": {"total": "20.00"}},
    ]
    assert check_record(rows, spec, correct).objective_label == "CORRECT"
    assert check_record(rows, spec, wrong_key).objective_label == "INCORRECT"


def test_group_order_insensitive():
    rows = [{"region": "east", "amount": "1.00"}, {"region": "west", "amount": "2.00"}]
    spec = {"group_by": ["region"], "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    reversed_order = [
        {"group": {"region": "west"}, "values": {"total": "2.00"}},
        {"group": {"region": "east"}, "values": {"total": "1.00"}},
    ]
    assert check_record(rows, spec, reversed_order).objective_label == "CORRECT"


def test_float_row_value_rejected():
    rows = [{"region": "east", "amount": 1.5}]
    spec = {"group_by": ["region"], "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    with pytest.raises(TypeError):
        compute_aggregation(rows, spec)


def test_unknown_func_raises():
    rows = [{"region": "east", "amount": "1.00"}]
    spec = {"group_by": ["region"], "aggregations": [{"output": "total", "func": "median", "column": "amount"}]}
    with pytest.raises(ValueError):
        compute_aggregation(rows, spec)


def test_no_group_by_single_implicit_group():
    rows = [{"amount": "1.00"}, {"amount": "2.00"}, {"amount": "3.00"}]
    spec = {"group_by": [], "aggregations": [{"output": "total", "func": "sum", "column": "amount"}]}
    r = check_record(rows, spec, [{"group": {}, "values": {"total": "6.00"}}])
    assert r.objective_label == "CORRECT"


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["rows"], fx["spec"], fx["candidate_result"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 20


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_seven_failure_classes_covered():
    required = {
        "wrong_agg_function", "wrong_group_key", "null_in_avg", "null_in_sum",
        "count_vs_distinct", "filter_timing", "empty_group",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_rows_and_spec_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-rows/same-spec CORRECT sibling (same table, same
    question) -- proof it perturbs exactly one trap (the candidate_result), not the question."""
    import json

    def key_of(fx):
        return (json.dumps(fx["rows"], sort_keys=True), json.dumps(fx["spec"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key_of(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key_of(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
