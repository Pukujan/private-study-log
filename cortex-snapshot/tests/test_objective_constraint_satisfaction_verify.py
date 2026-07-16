"""Frozen tests for the objective constraint-satisfaction VERIFY checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only CSP constraint check (checker_csp.verify / check_record), never a
model/judge. These tests pin the checker on hand-picked cases per instance kind (independent of the
runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy + kind
coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_constraint_satisfaction_verify.checker_csp import (  # noqa: E402
    check_record,
    verify,
)
from evals.objective_constraint_satisfaction_verify.run_csp import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

# sudoku
def test_sudoku_valid_grid_is_correct():
    grid = [[1, 2, 3, 4], [3, 4, 1, 2], [2, 1, 4, 3], [4, 3, 2, 1]]
    assert check_record({"kind": "sudoku", "n": 4}, grid).objective_label == "CORRECT"


def test_sudoku_row_duplicate_is_incorrect():
    grid = [[1, 2, 3, 3], [3, 4, 1, 2], [2, 1, 4, 3], [4, 3, 2, 1]]
    r = check_record({"kind": "sudoku", "n": 4}, grid)
    assert r.objective_label == "INCORRECT" and "row" in r.detail


def test_sudoku_box_duplicate_with_valid_rows_and_columns_is_incorrect():
    latin = [[1, 2, 3, 4], [2, 3, 4, 1], [3, 4, 1, 2], [4, 1, 2, 3]]
    r = check_record({"kind": "sudoku", "n": 4}, latin)
    assert r.objective_label == "INCORRECT" and "box" in r.detail


def test_sudoku_out_of_domain_is_incorrect():
    grid = [[1, 5, 3, 4], [3, 4, 1, 2], [2, 1, 4, 3], [4, 3, 2, 1]]
    r = check_record({"kind": "sudoku", "n": 4}, grid)
    assert r.objective_label == "INCORRECT" and "domain" in r.detail


def test_sudoku_incomplete_grid_is_incorrect():
    grid = [[1, 2, 3, 4], [3, 4, 1, 2], [2, 1, 4, 3]]  # missing a row
    assert check_record({"kind": "sudoku", "n": 4}, grid).objective_label == "INCORRECT"


def test_sudoku_9x9_valid_grid_is_correct():
    grid = [
        [5, 3, 4, 6, 7, 8, 9, 1, 2],
        [6, 7, 2, 1, 9, 5, 3, 4, 8],
        [1, 9, 8, 3, 4, 2, 5, 6, 7],
        [8, 5, 9, 7, 6, 1, 4, 2, 3],
        [4, 2, 6, 8, 5, 3, 7, 9, 1],
        [7, 1, 3, 9, 2, 4, 8, 5, 6],
        [9, 6, 1, 5, 3, 7, 2, 8, 4],
        [2, 8, 7, 4, 1, 9, 6, 3, 5],
        [3, 4, 5, 2, 8, 6, 1, 7, 9],
    ]
    assert check_record({"kind": "sudoku", "n": 9}, grid).objective_label == "CORRECT"


# n_queens
def test_n_queens_valid_placement_is_correct():
    assert check_record({"kind": "n_queens", "n": 4}, [1, 3, 0, 2]).objective_label == "CORRECT"


def test_n_queens_diagonal_attack_is_incorrect():
    r = check_record({"kind": "n_queens", "n": 4}, [0, 1, 3, 2])
    assert r.objective_label == "INCORRECT" and "diagonal" in r.detail


def test_n_queens_column_clash_is_incorrect():
    r = check_record({"kind": "n_queens", "n": 4}, [1, 1, 0, 2])
    assert r.objective_label == "INCORRECT" and "column" in r.detail


def test_n_queens_out_of_range_column_is_incorrect():
    r = check_record({"kind": "n_queens", "n": 4}, [1, 3, 0, 4])
    assert r.objective_label == "INCORRECT" and "range" in r.detail


def test_n_queens_wrong_length_is_incorrect():
    assert check_record({"kind": "n_queens", "n": 4}, [1, 3, 0]).objective_label == "INCORRECT"


# graph_coloring
_TRI = {"kind": "graph_coloring", "nodes": ["A", "B", "C"],
        "edges": [["A", "B"], ["B", "C"], ["C", "A"]], "k": 3}


def test_graph_coloring_valid_is_correct():
    assert check_record(_TRI, {"A": 0, "B": 1, "C": 2}).objective_label == "CORRECT"


def test_graph_coloring_adjacent_same_color_is_incorrect():
    r = check_record(_TRI, {"A": 0, "B": 0, "C": 2})
    assert r.objective_label == "INCORRECT" and "same color" in r.detail


def test_graph_coloring_incomplete_is_incorrect():
    r = check_record(_TRI, {"A": 0, "B": 1})
    assert r.objective_label == "INCORRECT" and "uncolored" in r.detail


def test_graph_coloring_out_of_domain_color_is_incorrect():
    r = check_record(_TRI, {"A": 0, "B": 1, "C": 3})
    assert r.objective_label == "INCORRECT" and "domain" in r.detail


def test_verify_unknown_kind_raises():
    try:
        verify({"kind": "tsp"}, [])
    except ValueError:
        return
    assert False, "unknown kind should raise ValueError"


def test_computed_answer_restates_valid_solution():
    r = check_record({"kind": "n_queens", "n": 4}, [1, 3, 0, 2])
    assert r.computed_answer == "valid n_queens solution"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["instance"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_verify_as_correct():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        ok, _ = verify(fx["instance"], fx["candidate"])
        assert ok, fx["id"]


def test_incorrect_fixtures_verify_as_incorrect():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        ok, _ = verify(fx["instance"], fx["candidate"])
        assert not ok, fx["id"]


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
        "none", "row_duplicate", "box_duplicate", "queen_diagonal_attack",
        "out_of_domain_value", "adjacent_same_color", "incomplete_assignment",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_three_kinds_present():
    kinds = {fx["kind"] for fx in FIXTURES}
    assert {"sudoku", "n_queens", "graph_coloring"}.issubset(kinds), kinds


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_instance_with_a_correct_sibling():
    def key(fx):
        return json.dumps(fx["instance"], sort_keys=True)

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
