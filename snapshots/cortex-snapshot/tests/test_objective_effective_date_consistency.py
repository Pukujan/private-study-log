"""Frozen tests for the objective effective-date-consistency checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only `datetime.date` ordering evaluation (checker_dates.check_constraints /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy coverage,
mutation-integrity).
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_effective_date_consistency.checker_dates import (  # noqa: E402
    check_constraints,
    check_record,
)
from evals.objective_effective_date_consistency.run_dates import FIXTURES  # noqa: E402

_ORD_SEW = [
    ("<=", "signed_date", "effective_date"),
    ("<=", "effective_date", "expiry_date"),
]
_ORD_REN = [
    ("<=", "effective_date", "expiry_date"),
    ("between", "renewal_date", "effective_date", "expiry_date"),
]
_ORD_TERM = [
    ("<=", "effective_date", "expiry_date"),
    (">=", "termination_date", "effective_date"),
]


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_fully_valid_ordering_is_valid():
    d = {"signed_date": "2024-01-01", "effective_date": "2024-02-01", "expiry_date": "2025-02-01"}
    ok, viol = check_constraints(d, _ORD_SEW)
    assert ok is True and viol == []
    assert check_record(d, _ORD_SEW, "VALID").objective_label == "CORRECT"
    assert check_record(d, _ORD_SEW, "INVALID").objective_label == "INCORRECT"


def test_effective_after_expiry_is_invalid():
    d = {"signed_date": "2024-01-01", "effective_date": "2025-03-01", "expiry_date": "2025-01-01"}
    ok, _ = check_constraints(d, _ORD_SEW)
    assert ok is False
    assert check_record(d, _ORD_SEW, "INVALID").objective_label == "CORRECT"
    assert check_record(d, _ORD_SEW, "VALID").objective_label == "INCORRECT"


def test_signed_after_effective_is_invalid():
    d = {"signed_date": "2024-03-01", "effective_date": "2024-02-01", "expiry_date": "2025-02-01"}
    assert check_constraints(d, _ORD_SEW)[0] is False
    assert check_record(d, _ORD_SEW, "INVALID").objective_label == "CORRECT"


def test_renewal_out_of_range_after_expiry_is_invalid():
    d = {"effective_date": "2024-02-01", "expiry_date": "2025-02-01", "renewal_date": "2025-06-01"}
    assert check_constraints(d, _ORD_REN)[0] is False


def test_renewal_out_of_range_before_effective_is_invalid():
    d = {"effective_date": "2024-02-01", "expiry_date": "2025-02-01", "renewal_date": "2023-12-01"}
    assert check_constraints(d, _ORD_REN)[0] is False


def test_termination_before_effective_is_invalid():
    d = {"effective_date": "2024-02-01", "expiry_date": "2025-02-01", "termination_date": "2024-01-10"}
    assert check_constraints(d, _ORD_TERM)[0] is False
    assert check_record(d, _ORD_TERM, "INVALID").objective_label == "CORRECT"


def test_boundary_equal_signed_effective_is_valid():
    # signed <= effective is inclusive: equal dates satisfy it.
    d = {"signed_date": "2024-02-01", "effective_date": "2024-02-01", "expiry_date": "2025-02-01"}
    assert check_constraints(d, _ORD_SEW)[0] is True
    assert check_record(d, _ORD_SEW, "VALID").objective_label == "CORRECT"


def test_boundary_equal_renewal_expiry_is_valid():
    # 'between' is inclusive at both ends: renewal == expiry is valid.
    d = {"effective_date": "2024-02-01", "expiry_date": "2025-02-01", "renewal_date": "2025-02-01"}
    assert check_constraints(d, _ORD_REN)[0] is True


def test_strict_less_than_rejects_equal_dates():
    assert check_constraints({"a": "2024-01-01", "b": "2024-01-01"}, [("<", "a", "b")])[0] is False
    assert check_constraints({"a": "2024-01-01", "b": "2024-01-01"}, [("<=", "a", "b")])[0] is True


def test_reversed_interval_fails_between():
    ok, _ = check_constraints(
        {"x": "2024-05-01", "lo": "2024-06-01", "hi": "2024-04-01"},
        [("between", "x", "lo", "hi")],
    )
    assert ok is False


def test_unknown_date_key_raises():
    import pytest
    with pytest.raises(KeyError):
        check_constraints({"a": "2024-01-01"}, [("<=", "a", "missing")])


def test_computed_answer_surfaces_the_verdict():
    d = {"signed_date": "2024-01-01", "effective_date": "2025-03-01", "expiry_date": "2025-01-01"}
    r = check_record(d, _ORD_SEW, "VALID")
    assert r.computed_answer == "INVALID" and r.objective_label == "INCORRECT"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["dates"], fx["constraints"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        all_hold, _ = check_constraints(fx["dates"], fx["constraints"])
        computed = "VALID" if all_hold else "INVALID"
        assert fx["candidate"] == computed, fx["id"]


# --- structural invariants -------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "none", "effective_after_expiry", "signed_after_effective",
        "renewal_out_of_range", "termination_before_effective", "boundary_equal_misjudged",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _key(fx):
    return (json.dumps(fx["dates"], sort_keys=True), json.dumps(fx["constraints"]))


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(_key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[_key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        all_hold, _ = check_constraints(fx["dates"], fx["constraints"])
        computed = "VALID" if all_hold else "INVALID"
        assert fx["candidate"] != computed, fx["id"]
