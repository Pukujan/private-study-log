"""Frozen tests for the objective pagination-cursor-consistency checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only page reassembly + cursor-termination check
(checker_pagination.check_pages / check_record), never a model/judge. These tests pin the checker on
hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants
(balance, unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_pagination_cursor_consistency.checker_pagination import (  # noqa: E402
    check_pages,
    check_record,
)
from evals.objective_pagination_cursor_consistency.run_pagination import FIXTURES  # noqa: E402

_FULL = ["a", "b", "c", "d", "e"]


def _computed(full_dataset, pages):
    ok, _ = check_pages(full_dataset, pages)
    return "VALID" if ok else "INVALID"


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_clean_walk_is_valid():
    pages = [
        {"items": ["a", "b"], "next_cursor": "c1"},
        {"items": ["c", "d"], "next_cursor": "c2"},
        {"items": ["e"], "next_cursor": None},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is True
    assert info["order_ok"] and info["terminates"]
    assert check_record(_FULL, pages, "VALID").objective_label == "CORRECT"
    assert check_record(_FULL, pages, "INVALID").objective_label == "INCORRECT"


def test_single_page_is_valid():
    pages = [{"items": _FULL, "next_cursor": None}]
    assert check_pages(_FULL, pages)[0] is True
    assert check_record(_FULL, pages, "VALID").objective_label == "CORRECT"


def test_duplicate_item_is_invalid():
    pages = [
        {"items": ["a", "b", "c"], "next_cursor": "c1"},
        {"items": ["a", "d", "e"], "next_cursor": None},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is False and "a" in info["duplicates"]
    assert check_record(_FULL, pages, "INVALID").objective_label == "CORRECT"
    assert check_record(_FULL, pages, "VALID").objective_label == "INCORRECT"


def test_missing_item_is_invalid():
    pages = [
        {"items": ["a", "b"], "next_cursor": "c1"},
        {"items": ["d", "e"], "next_cursor": None},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is False and "c" in info["missing"]


def test_wrong_order_is_invalid():
    pages = [
        {"items": ["a", "c"], "next_cursor": "c1"},
        {"items": ["b", "d", "e"], "next_cursor": None},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is False and info["order_ok"] is False
    assert info["duplicates"] == [] and info["missing"] == []


def test_boundary_overlap_is_invalid():
    pages = [
        {"items": ["a", "b", "c"], "next_cursor": "c1"},
        {"items": ["c", "d", "e"], "next_cursor": None},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is False and "c" in info["duplicates"]


def test_non_terminating_is_invalid():
    pages = [
        {"items": ["a", "b"], "next_cursor": "c1"},
        {"items": ["c", "d", "e"], "next_cursor": "c2"},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is False and info["terminates"] is False
    # items reassemble in order, so only the termination check flags it
    assert info["order_ok"] and info["duplicates"] == [] and info["missing"] == []


def test_premature_termination_is_invalid():
    pages = [
        {"items": ["a", "b"], "next_cursor": None},
        {"items": ["c", "d", "e"], "next_cursor": None},
    ]
    ok, info = check_pages(_FULL, pages)
    assert ok is False and info["terminates"] is False


def test_computed_answer_is_valid_invalid_token():
    good = [{"items": _FULL, "next_cursor": None}]
    assert check_record(_FULL, good, "VALID").computed_answer == "VALID"
    bad = [{"items": ["a", "b"], "next_cursor": None}]
    assert check_record(_FULL, bad, "INVALID").computed_answer == "INVALID"


def test_check_record_rejects_non_decision_candidate():
    pages = [{"items": _FULL, "next_cursor": None}]
    try:
        check_record(_FULL, pages, "maybe")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a non VALID/INVALID candidate")


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["full_dataset"], fx["pages"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == _computed(fx["full_dataset"], fx["pages"]), fx["id"]


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
        "none", "duplicate_item", "missing_item", "wrong_order",
        "boundary_overlap", "bad_termination",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return (json.dumps(fx["full_dataset"]), json.dumps(fx["pages"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    # a genuinely INCORRECT record must carry the decision that disagrees with the checker's verdict
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != _computed(fx["full_dataset"], fx["pages"]), fx["id"]
