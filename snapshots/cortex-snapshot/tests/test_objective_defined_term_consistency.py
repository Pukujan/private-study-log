"""Frozen tests for the objective defined-term-consistency checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib defined-term resolver (checker_defterm.resolve / check_record), never a
model/judge. These tests pin the checker on hand-picked cases (independent of the fixture file),
then sweep every fixture asserting the checker agrees with its declared expected_label and that the
fixture set is well-formed (count range, unique ids, label balance, all failure classes, every
INCORRECT states a mutation, and mutation-integrity: each INCORRECT shares its scenario with a
CORRECT sibling).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_defined_term_consistency.checker_defterm import (  # noqa: E402
    check_record,
    compute_answer,
    resolve,
)
from evals.objective_defined_term_consistency.fixtures_defterm import FIXTURES  # noqa: E402


def _d(*terms):
    return [{"term": t} for t in terms]


# --- hand-picked cases, independent of the fixture file --------------------------------------

def test_clean_document_is_consistent():
    assert compute_answer(_d("Confidential Information"), _d("Confidential Information")) == "CONSISTENT"


def test_clean_document_correct_label():
    assert check_record(_d("Term A"), _d("Term A"), "CONSISTENT").objective_label == "CORRECT"


def test_undefined_term_used_is_inconsistent():
    r = resolve(_d("Confidential Information"), _d("Receiving Party"))
    assert r.answer == "INCONSISTENT"
    assert any(v.type == "undefined_term_used" and v.term == "Receiving Party" for v in r.violations)


def test_undefined_term_claimed_consistent_is_incorrect():
    assert check_record(_d("Confidential Information"), _d("Receiving Party"),
                        "CONSISTENT").objective_label == "INCORRECT"


def test_duplicate_definition_is_inconsistent_when_used():
    r = resolve(_d("Agreement", "Agreement"), _d("Agreement"))
    assert r.answer == "INCONSISTENT"
    assert any(v.type == "duplicate_definition" and v.term == "Agreement" for v in r.violations)


def test_duplicate_definition_is_fatal_even_when_unused():
    # rule (b): no term defined more than once -- fatal regardless of use
    r = resolve(_d("Territory", "Territory"), _d())
    assert r.answer == "INCONSISTENT"
    assert any(v.type == "duplicate_definition" for v in r.violations)


def test_repeated_use_of_single_definition_is_consistent():
    # repeated USE is fine; only repeated DEFINITION is fatal
    assert compute_answer(_d("Agreement"), _d("Agreement", "Agreement", "Agreement")) == "CONSISTENT"


def test_case_sensitivity_lowercase_use_does_not_resolve():
    r = resolve(_d("Confidential Information"), _d("confidential information"))
    assert r.answer == "INCONSISTENT"
    assert any(v.type == "undefined_term_used" and v.term == "confidential information"
               for v in r.violations)


def test_case_sensitivity_allcaps_use_does_not_resolve():
    assert compute_answer(_d("Receiving Party"), _d("RECEIVING PARTY")) == "INCONSISTENT"


def test_defined_but_unused_is_non_fatal():
    r = resolve(_d("Confidential Information", "Effective Date"), _d("Confidential Information"))
    assert r.answer == "CONSISTENT"
    assert r.violations == []
    assert r.unused_definitions == ["Effective Date"]


def test_multiword_exact_case_use_resolves():
    assert compute_answer(_d("Intellectual Property Rights"),
                          _d("Intellectual Property Rights")) == "CONSISTENT"


def test_undefined_use_deduped_in_violations():
    # the same undefined term used twice is reported once
    r = resolve(_d("Services"), _d("Deliverables", "Deliverables"))
    undef = [v for v in r.violations if v.type == "undefined_term_used"]
    assert len(undef) == 1


def test_candidate_answer_must_be_valid_token():
    import pytest
    with pytest.raises(ValueError):
        check_record(_d("A"), _d("A"), "MAYBE")


def test_malformed_entry_rejected():
    import pytest
    with pytest.raises(ValueError):
        resolve([{"not_term": "x"}], _d())


def test_self_test_passes():
    from evals.objective_defined_term_consistency.checker_defterm import self_test
    self_test()  # asserts internally; raises on failure


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["definitions"], fx["uses"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {"undefined_term_used", "duplicate_definition", "case_sensitivity"}
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_consistent_baseline_present():
    assert any(fx["failure_class"] == "none" for fx in FIXTURES)


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_correct_fixtures_have_no_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == "", f"{fx['id']} is CORRECT but states a mutation"


def test_candidate_answers_are_valid_tokens():
    for fx in FIXTURES:
        assert fx["candidate_answer"] in ("CONSISTENT", "INCONSISTENT"), fx["id"]


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    def key(fx):
        return (json.dumps(fx["definitions"], sort_keys=True),
                json.dumps(fx["uses"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
        # the sibling's candidate_answer must be the opposite decision
        correct_sib = next(s for s in siblings if s["expected_label"] == "CORRECT")
        assert fx["candidate_answer"] != correct_sib["candidate_answer"], fx["id"]
