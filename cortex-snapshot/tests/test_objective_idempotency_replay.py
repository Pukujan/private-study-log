"""Frozen tests for the objective API-idempotency-replay checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only deterministic re-implementation of the operation (checker_idem.
apply_once / check_replay / check_record), never a model/judge. These tests pin the checker on
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

from evals.objective_idempotency_replay.checker_idem import (  # noqa: E402
    apply_once,
    apply_n,
    check_record,
    check_replay,
)
from evals.objective_idempotency_replay.run_idem import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_increment_deduped_is_idempotent():
    req = {"idempotency_key": "i", "op": "increment", "field": "n", "amount": 5}
    assert check_replay({"n": 10}, req, 3, {"n": 15})[0] == "IDEMPOTENT"
    assert check_record({"n": 10}, req, 3, {"n": 15}, "IDEMPOTENT").objective_label == "CORRECT"


def test_increment_applied_k_times_is_violated():
    req = {"idempotency_key": "i", "op": "increment", "field": "n", "amount": 5}
    assert check_replay({"n": 10}, req, 3, {"n": 25})[0] == "VIOLATED"  # 10 + 3*5
    assert check_record({"n": 10}, req, 3, {"n": 25}, "VIOLATED").objective_label == "CORRECT"


def test_create_once_vs_k_duplicates():
    req = {"idempotency_key": "c", "op": "create", "id": "u1",
           "record": {"name": "Al"}, "collection": "users"}
    assert check_replay({"users": []}, req, 3, {"users": [{"id": "u1", "name": "Al"}]})[0] == "IDEMPOTENT"
    dup = {"users": [{"id": "u1", "name": "Al"}] * 3}
    assert check_replay({"users": []}, req, 3, dup)[0] == "VIOLATED"


def test_charge_once_vs_k():
    req = {"idempotency_key": "h", "op": "charge", "account": "a", "amount": 100}
    assert check_replay({"a": 500}, req, 3, {"a": 400})[0] == "IDEMPOTENT"
    assert check_replay({"a": 500}, req, 3, {"a": 200})[0] == "VIOLATED"  # 500 - 3*100


def test_set_is_naturally_idempotent():
    req = {"idempotency_key": "s", "op": "set", "field": "status", "value": "active"}
    assert apply_n({"status": "x"}, req, 5) == apply_once({"status": "x"}, req)
    assert check_replay({"status": "pending"}, req, 5, {"status": "active"})[0] == "IDEMPOTENT"
    # a stale value that never changed is a violation of the expected write-through
    assert check_replay({"status": "pending"}, req, 5, {"status": "pending"})[0] == "VIOLATED"


def test_append_duplicated_is_violated():
    req = {"idempotency_key": "a", "op": "append", "list_field": "log", "item": "deploy"}
    assert check_replay({"log": ["s"]}, req, 2, {"log": ["s", "deploy"]})[0] == "IDEMPOTENT"
    assert check_replay({"log": ["s"]}, req, 2, {"log": ["s", "deploy", "deploy"]})[0] == "VIOLATED"


def test_apply_once_does_not_mutate_initial():
    req = {"idempotency_key": "i", "op": "increment", "field": "n", "amount": 1}
    base = {"n": 1}
    _ = apply_n(base, req, 9)
    assert base == {"n": 1}


def test_wrong_decision_is_incorrect():
    req = {"idempotency_key": "i", "op": "increment", "field": "n", "amount": 5}
    assert check_record({"n": 10}, req, 3, {"n": 15}, "VIOLATED").objective_label == "INCORRECT"
    assert check_record({"n": 10}, req, 3, {"n": 25}, "IDEMPOTENT").objective_label == "INCORRECT"


def test_computed_answer_is_the_decision():
    req = {"idempotency_key": "h", "op": "charge", "account": "a", "amount": 100}
    r = check_record({"a": 500}, req, 3, {"a": 200}, "VIOLATED")
    assert r.computed_answer == "VIOLATED"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["initial"], fx["request"], fx["k"], fx["observed"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        decision = check_replay(fx["initial"], fx["request"], fx["k"], fx["observed"])[0]
        assert fx["candidate"] == decision, fx["id"]


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
        "none", "double_increment", "duplicate_create",
        "repeated_charge", "appended_twice", "key_ignored",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _key(fx):
    return json.dumps(
        {"initial": fx["initial"], "request": fx["request"], "k": fx["k"], "observed": fx["observed"]},
        sort_keys=True,
    )


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
    # a genuinely INCORRECT record must carry a decision that is not the computed answer
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        decision = check_replay(fx["initial"], fx["request"], fx["k"], fx["observed"])[0]
        assert fx["candidate"] != decision, fx["id"]
