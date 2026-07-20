"""Frozen tests for the objective numbering-sequence-integrity checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only sequence-integrity check (checker_numbering.check_sequence /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy + scheme
coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_numbering_sequence_integrity.checker_numbering import (  # noqa: E402
    check_record,
    check_sequence,
)
from evals.objective_numbering_sequence_integrity.run_numbering import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

# clean sequences, one per scheme
def test_clean_integer_is_valid():
    assert check_sequence(["1", "2", "3", "4"], "integer")[0] is True
    assert check_record(["1", "2", "3", "4"], "integer", "VALID").objective_label == "CORRECT"
    assert check_record(["1", "2", "3", "4"], "integer", "INVALID").objective_label == "INCORRECT"


def test_clean_dotted_hierarchy_is_valid():
    assert check_sequence(["1", "1.1", "1.2", "2", "2.1"], "dotted")[0] is True
    assert check_record(["1", "1.1", "1.2", "2", "2.1"], "dotted", "VALID").objective_label == "CORRECT"


def test_clean_letter_is_valid():
    assert check_sequence(["A", "B", "C"], "letter")[0] is True
    assert check_record(["A", "B", "C"], "letter", "VALID").objective_label == "CORRECT"


# each violation class
def test_gap_is_invalid():
    assert check_sequence(["1", "2", "4"], "integer")[0] is False
    assert check_sequence(["A", "B", "D"], "letter")[0] is False
    assert check_record(["1", "2", "4"], "integer", "VALID").objective_label == "INCORRECT"
    assert check_record(["1", "2", "4"], "integer", "INVALID").objective_label == "CORRECT"


def test_duplicate_is_invalid():
    assert check_sequence(["1", "2", "2", "3"], "integer")[0] is False
    assert check_sequence(["1", "1.1", "1.1", "2"], "dotted")[0] is False


def test_out_of_order_is_invalid():
    assert check_sequence(["1", "2", "3", "2"], "integer")[0] is False
    assert check_sequence(["A", "B", "C", "B"], "letter")[0] is False


def test_bad_nesting_skipped_child_is_invalid():
    assert check_sequence(["1", "1.1", "1.3"], "dotted")[0] is False


def test_bad_nesting_orphan_child_is_invalid():
    assert check_sequence(["1", "2.1"], "dotted")[0] is False


def test_wrong_scheme_break_is_invalid():
    # a dotted token in a flat integer scheme, and a digit in a letter scheme
    assert check_sequence(["1", "2", "2.1"], "integer")[0] is False
    assert check_sequence(["A", "3"], "letter")[0] is False


def test_bad_start_is_invalid():
    assert check_sequence(["2", "3", "4"], "integer")[0] is False
    assert check_sequence(["B", "C"], "letter")[0] is False
    assert check_sequence(["2", "2.1"], "dotted")[0] is False


def test_computed_answer_reports_valid_or_invalid_with_problems():
    assert check_record(["1", "2", "3"], "integer", "VALID").computed_answer == "VALID"
    r = check_record(["1", "2", "4"], "integer", "INVALID")
    assert r.computed_answer.startswith("INVALID (")
    assert r.problems  # non-empty problem list


def test_unknown_scheme_raises():
    import pytest

    with pytest.raises(ValueError):
        check_sequence(["1", "2"], "roman")


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["identifiers"], fx["scheme"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_matches_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        valid, _ = check_sequence(fx["identifiers"], fx["scheme"])
        truth = "VALID" if valid else "INVALID"
        assert fx["candidate"] == truth, fx["id"]


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
        "none", "gap", "duplicate", "out_of_order", "bad_nesting", "wrong_scheme_break",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_schemes_covered():
    required = {"integer", "dotted", "letter"}
    present = {fx["scheme"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    def key(fx):
        return (json.dumps(fx["identifiers"]), fx["scheme"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        valid, _ = check_sequence(fx["identifiers"], fx["scheme"])
        truth = "VALID" if valid else "INVALID"
        assert fx["candidate"] != truth, fx["id"]
