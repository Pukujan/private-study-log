"""Frozen tests for the objective cross-reference-integrity checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib reference-graph resolver (checker_xref.resolve/check_record), never a
model/judge. These tests pin the checker on hand-picked cases (independent of the fixture file),
sweep every fixture asserting the checker agrees with its declared expected_label, and assert the
structural invariants (count range, unique ids, label balance, every failure class covered, every
INCORRECT states a mutation, mutation-integrity pairing).

Written before checker_xref.py per SDD-then-TDD: this file states the contract.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_cross_reference_integrity.checker_xref import (  # noqa: E402
    check_record,
    is_valid,
    resolve,
)
from evals.objective_cross_reference_integrity.fixtures_xref import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the fixture file --------------------------------------

def test_all_references_resolve_is_valid():
    a = [{"type": "Section", "id": "1"}, {"type": "Section", "id": "2"}]
    assert is_valid(a, [{"type": "Section", "id": "1"}, {"type": "Section", "id": "2"}])


def test_dangling_reference_is_invalid():
    a = [{"type": "Section", "id": "1"}]
    assert not is_valid(a, [{"type": "Section", "id": "2"}])
    v = resolve(a, [{"type": "Section", "id": "2"}])
    assert v[0]["kind"] == "dangling_reference"


def test_dangling_reference_claimed_valid_is_incorrect():
    a = [{"type": "Section", "id": "1"}]
    r = check_record(a, [{"type": "Section", "id": "2"}], "VALID")
    assert r.objective_label == "INCORRECT"
    assert r.computed_answer == "INVALID"


def test_duplicate_anchor_is_invalid_even_when_reference_resolves():
    a = [{"type": "Exhibit", "id": "A"}, {"type": "Exhibit", "id": "A"}]
    assert not is_valid(a, [{"type": "Exhibit", "id": "A"}])
    v = resolve(a, [{"type": "Exhibit", "id": "A"}])
    assert any(x["kind"] == "duplicate_anchor_definition" for x in v)


def test_same_id_different_types_are_not_duplicates():
    a = [{"type": "Section", "id": "A"}, {"type": "Exhibit", "id": "A"}]
    assert is_valid(a, [])


def test_wrong_type_reference_is_invalid_and_classified():
    a = [{"type": "Exhibit", "id": "A"}]
    v = resolve(a, [{"type": "Section", "id": "A"}])
    assert v[0]["kind"] == "wrong_type_reference"
    assert "exhibit" in v[0]["defined_under_types"]


def test_case_and_whitespace_normalization_resolves():
    a = [{"type": "Appendix", "id": "B"}]
    assert is_valid(a, [{"type": "appendix", "id": " b "}])
    assert is_valid(a, [{"type": " APPENDIX ", "id": "b"}])


def test_trailing_period_id_is_a_distinct_id():
    # normalization is only case-fold + surrounding-whitespace-trim; "3.2." != "3.2"
    a = [{"type": "Section", "id": "3.2"}]
    assert not is_valid(a, [{"type": "Section", "id": "3.2."}])


def test_forward_reference_resolves():
    a = [{"type": "Section", "id": "1"}, {"type": "Section", "id": "2"}]
    assert is_valid(a, [{"type": "Section", "id": "2"}])  # order-independent


def test_no_references_is_vacuously_valid():
    assert is_valid([{"type": "Section", "id": "1"}], [])
    assert is_valid([], [])


def test_repeated_references_allowed():
    a = [{"type": "Section", "id": "1"}]
    assert is_valid(a, [{"type": "Section", "id": "1"}, {"type": "Section", "id": "1"}])


def test_check_record_rejects_bad_token():
    try:
        check_record([], [], "MAYBE")
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-VALID/INVALID candidate_answer")


def test_multiple_violations_reported():
    a = [{"type": "Section", "id": "1"}, {"type": "Section", "id": "1"}]  # duplicate
    v = resolve(a, [{"type": "Section", "id": "9"}])                      # + dangling
    kinds = {x["kind"] for x in v}
    assert "duplicate_anchor_definition" in kinds
    assert "dangling_reference" in kinds


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["anchors"], fx["references"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 26


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "dangling_reference", "duplicate_anchor_definition", "wrong_type_reference",
        "format_normalization",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_clean_baseline_present():
    assert any(fx["failure_class"] == "none" for fx in FIXTURES)


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_correct_fixtures_have_no_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == "", f"{fx['id']} is CORRECT but names a mutation"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    def key(fx):
        return (json.dumps(fx["anchors"], sort_keys=True),
                json.dumps(fx["references"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
        # the CORRECT sibling must carry the OPPOSITE candidate_answer on the same question
        correct = next(s for s in siblings if s["expected_label"] == "CORRECT")
        assert correct["candidate_answer"] != fx["candidate_answer"], fx["id"]
