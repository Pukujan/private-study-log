"""Frozen tests for the objective boolean-SAT-verification checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only CNF clause-satisfaction check (checker_sat.satisfies / check_record),
never a model/judge. These tests pin the checker on hand-picked cases (independent of the runner's
fixture list), sweep every fixture asserting the checker agrees with its declared expected_label, and
assert the lane's structural invariants (balance, unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_boolean_sat_verification.checker_sat import (  # noqa: E402
    check_record,
    find_satisfying_assignment,
    satisfies,
)
from evals.objective_boolean_sat_verification.run_sat import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_full_model_is_correct():
    assert satisfies([[1, 2], [-1, 3]], {1: True, 2: False, 3: True}) is True
    assert check_record([[1, 2], [-1, 3]], {1: True, 2: False, 3: True}).objective_label == "CORRECT"


def test_unsatisfied_clause_is_incorrect():
    # clause (not x1 or not x2) has no satisfied literal under all-true
    assert satisfies([[1, 2], [-1, -2]], {1: True, 2: True}) is False
    assert check_record([[1, 2], [-1, -2]], {1: True, 2: True}).objective_label == "INCORRECT"


def test_missing_variable_is_incorrect():
    # x3 appears in the formula but is unassigned -> not a complete model
    assert satisfies([[1, 2], [-3]], {1: True, 2: True}) is False
    assert check_record([[1, 2], [-3]], {1: True, 2: True}).objective_label == "INCORRECT"


def test_unit_clause_forces_its_variable():
    assert check_record([[1], [-1, 2]], {1: True, 2: True}).objective_label == "CORRECT"
    assert check_record([[1], [-1, 2]], {1: False, 2: True}).objective_label == "INCORRECT"


def test_all_false_trap_is_incorrect():
    formula = [[-1, -2], [1, 2, 3]]
    assert check_record(formula, {1: False, 2: False, 3: False}).objective_label == "INCORRECT"
    # a real model exists (so the formula is satisfiable, not a degenerate case)
    assert find_satisfying_assignment(formula) is not None


def test_find_returns_a_real_model():
    formula = [[1, 2], [-1, 3], [-2, -3]]
    w = find_satisfying_assignment(formula)
    assert w is not None
    assert satisfies(formula, w)


def test_find_returns_none_for_unsat():
    assert find_satisfying_assignment([[1], [-1]]) is None


def test_find_is_capped_at_20_variables():
    big = [list(range(1, 22))]  # 21 distinct variables
    try:
        find_satisfying_assignment(big)
        raise AssertionError("expected ValueError for >20 variables")
    except ValueError:
        pass


def test_computed_answer_is_a_witness_or_unsat():
    r = check_record([[1, 2], [-1, 3], [-2, -3]], {1: True, 2: True, 3: True})
    assert isinstance(r.computed_answer, dict)
    assert satisfies([[1, 2], [-1, 3], [-2, -3]], r.computed_answer)
    assert check_record([[1], [-1]], {1: True}).computed_answer == "UNSAT"


def test_str_and_int_keys_grade_identically():
    formula = [[1, 2], [-1, 3]]
    a_int = {1: True, 2: False, 3: True}
    a_str = {"1": True, "2": False, "3": True}
    assert check_record(formula, a_int).objective_label == check_record(formula, a_str).objective_label


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["formula"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_is_a_satisfying_model():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert satisfies(fx["formula"], fx["candidate"]), fx["id"]
        # the CORRECT candidate is exactly the computed canonical witness
        assert fx["candidate"] == find_satisfying_assignment(fx["formula"]), fx["id"]


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
        "none", "one_clause_unsat", "missing_var_assignment",
        "unit_clause_violation", "all_false_trap", "flip_breaks_last_clause",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_formula_with_a_correct_sibling():
    def key(fx):
        return json.dumps(fx["formula"], sort_keys=True)

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_is_not_a_satisfying_model():
    # a genuinely INCORRECT record must carry a candidate that is not a valid satisfying model
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert not satisfies(fx["formula"], fx["candidate"]), fx["id"]
