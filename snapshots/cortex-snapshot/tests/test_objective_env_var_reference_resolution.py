"""Frozen tests for the objective env-var-reference-resolution checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only recursive `re`-driven reference resolution (checker_env.resolve /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy coverage,
mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_env_var_reference_resolution.checker_env import (  # noqa: E402
    check_record,
    resolve,
)
from evals.objective_env_var_reference_resolution.run_env import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_simple_braced_reference():
    assert resolve({"X": "hi"}, "${X}").resolved == "hi"


def test_bare_reference():
    assert resolve({"X": "hi"}, "$X").resolved == "hi"
    assert resolve({"PORT": "8080"}, "listen $PORT").resolved == "listen 8080"


def test_nested_recursive_expansion():
    assert resolve({"A": "${B}", "B": "world"}, "${A}").resolved == "world"
    assert resolve({"A": "${B}", "B": "${C}", "C": "deep"}, "val=${A}").resolved == "val=deep"


def test_default_used_when_missing():
    r = resolve({}, "${X:-fallback}")
    assert r.resolved == "fallback"
    assert r.unresolved == []


def test_default_ignored_when_set():
    assert resolve({"X": "set"}, "${X:-def}").resolved == "set"


def test_default_used_when_empty_posix():
    # POSIX :- fires when the var is undefined OR resolves to empty.
    assert resolve({"X": ""}, "${X:-def}").resolved == "def"


def test_undefined_var_goes_to_unresolved_set_and_empty_output():
    r = resolve({}, "a${MISS}b")
    assert r.resolved == "ab"
    assert r.unresolved == ["MISS"]


def test_unresolved_set_is_sorted():
    assert resolve({}, "${Z}${A}${M}").unresolved == ["A", "M", "Z"]


def test_mutual_cycle_is_error():
    assert resolve({"A": "${B}", "B": "${A}"}, "${A}").error == "cycle"


def test_self_cycle_is_error():
    assert resolve({"SELF": "$SELF"}, "x=$SELF").error == "cycle"


def test_diamond_is_not_a_cycle():
    r = resolve({"A": "${D}", "B": "${D}", "D": "d"}, "${A}${B}")
    assert r.error is None
    assert r.resolved == "dd"


def test_adjacent_references():
    assert resolve({"A": "x", "B": "y"}, "${A}${B}").resolved == "xy"
    assert resolve({"A": "x", "B": "y"}, "$A-$B").resolved == "x-y"


def test_dollar_without_identifier_stays_literal():
    r = resolve({}, "cost $5 and a lone $")
    assert r.resolved == "cost $5 and a lone $"
    assert r.unresolved == []


# --- record-level verdicts -------------------------------------------------------------------

def test_check_record_correct_object():
    assert check_record({"X": "hi"}, "${X}",
                        {"resolved": "hi", "unresolved": []}).objective_label == "CORRECT"


def test_check_record_wrong_resolved_is_incorrect():
    assert check_record({"X": "hi"}, "${X}",
                        {"resolved": "bye", "unresolved": []}).objective_label == "INCORRECT"


def test_check_record_wrong_unresolved_set_is_incorrect():
    assert check_record({}, "a${M}",
                        {"resolved": "a", "unresolved": []}).objective_label == "INCORRECT"


def test_check_record_unresolved_set_compared_orderless():
    # set equality, not list order
    assert check_record({}, "${Z}${A}",
                        {"resolved": "", "unresolved": ["Z", "A"]}).objective_label == "CORRECT"


def test_check_record_cycle_sentinel_is_correct():
    assert check_record({"A": "${B}", "B": "${A}"}, "${A}", "ERROR:cycle").objective_label == "CORRECT"


def test_check_record_cycle_resolved_object_is_incorrect():
    assert check_record({"A": "${B}", "B": "${A}"}, "${A}",
                        {"resolved": "", "unresolved": []}).objective_label == "INCORRECT"


def test_computed_answer_shapes():
    assert check_record({}, "a${M}",
                        {"resolved": "a", "unresolved": ["M"]}).computed_answer == {
        "resolved": "a", "unresolved": ["M"]}
    assert check_record({"A": "${B}", "B": "${A}"}, "${A}", "ERROR:cycle").computed_answer == "ERROR:cycle"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["defs"], fx["template"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        rr = resolve(fx["defs"], fx["template"])
        expected = "ERROR:cycle" if rr.error == "cycle" else {
            "resolved": rr.resolved, "unresolved": list(rr.unresolved)}
        assert fx["candidate"] == expected, fx["id"]


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
        "none", "left_reference_literal", "wrong_value", "partial_resolution",
        "cycle_not_detected", "default_ignored",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    def key(fx):
        return (json.dumps(fx["defs"], sort_keys=True), fx["template"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    # a genuinely INCORRECT record must carry a candidate that is not the computed answer
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        rr = resolve(fx["defs"], fx["template"])
        computed = "ERROR:cycle" if rr.error == "cycle" else {
            "resolved": rr.resolved, "unresolved": list(rr.unresolved)}
        assert fx["candidate"] != computed, fx["id"]
