"""Frozen tests for the objective referential-integrity (foreign-key) checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only set-membership + value-count over row dicts (checker_fk.check_integrity
/ check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy coverage,
mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_referential_integrity_fk.checker_fk import (  # noqa: E402
    check_integrity,
    check_record,
)
from evals.objective_referential_integrity_fk.run_fk import FIXTURES  # noqa: E402


def _fk(ct, cc, pt, pc, nullable=False):
    return {"child_table": ct, "child_col": cc,
            "parent_table": pt, "parent_col": pc, "nullable": nullable}


_USERS = [{"id": 1}, {"id": 2}]
_FK_ORDERS = [_fk("orders", "user_id", "users", "id")]


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_clean_dataset_is_valid():
    tables = {"users": _USERS, "orders": [{"id": 10, "user_id": 1}, {"id": 11, "user_id": 2}]}
    ok, v = check_integrity(tables, _FK_ORDERS)
    assert ok is True
    assert v == {"orphans": [], "dup_parent_keys": [], "null_violations": []}
    assert check_record(tables, _FK_ORDERS, "VALID").objective_label == "CORRECT"
    assert check_record(tables, _FK_ORDERS, "INVALID").objective_label == "INCORRECT"


def test_orphan_fk_is_invalid():
    tables = {"users": _USERS, "orders": [{"id": 10, "user_id": 1}, {"id": 12, "user_id": 3}]}
    ok, v = check_integrity(tables, _FK_ORDERS)
    assert ok is False
    assert [o["value"] for o in v["orphans"]] == [3]
    assert not v["dup_parent_keys"] and not v["null_violations"]
    assert check_record(tables, _FK_ORDERS, "INVALID").objective_label == "CORRECT"
    assert check_record(tables, _FK_ORDERS, "VALID").objective_label == "INCORRECT"


def test_duplicate_parent_key_is_invalid():
    tables = {"users": [{"id": 1}, {"id": 1}, {"id": 2}],
              "orders": [{"id": 10, "user_id": 1}, {"id": 11, "user_id": 2}]}
    ok, v = check_integrity(tables, _FK_ORDERS)
    assert ok is False
    assert v["dup_parent_keys"][0]["value"] == 1 and v["dup_parent_keys"][0]["count"] == 2
    assert not v["orphans"] and not v["null_violations"]
    assert check_record(tables, _FK_ORDERS, "INVALID").objective_label == "CORRECT"


def test_null_in_nonnullable_fk_is_invalid():
    tables = {"users": _USERS, "orders": [{"id": 10, "user_id": 1}, {"id": 13, "user_id": None}]}
    ok, v = check_integrity(tables, _FK_ORDERS)
    assert ok is False
    assert v["null_violations"] == [{"child_table": "orders", "child_col": "user_id", "row_index": 1}]
    assert not v["orphans"] and not v["dup_parent_keys"]
    assert check_record(tables, _FK_ORDERS, "INVALID").objective_label == "CORRECT"


def test_null_in_nullable_fk_is_valid():
    fk_nullable = [_fk("orders", "user_id", "users", "id", nullable=True)]
    tables = {"users": _USERS, "orders": [{"id": 10, "user_id": 1}, {"id": 13, "user_id": None}]}
    ok, v = check_integrity(tables, fk_nullable)
    assert ok is True
    assert not v["orphans"] and not v["dup_parent_keys"] and not v["null_violations"]
    assert check_record(tables, fk_nullable, "VALID").objective_label == "CORRECT"


def test_multi_fk_one_broken_is_invalid():
    tables = {"users": _USERS, "products": [{"sku": "p1"}, {"sku": "p2"}],
              "line_items": [{"id": 1, "user_id": 1, "sku": "p1"},
                             {"id": 2, "user_id": 2, "sku": "p9"}]}
    fks = [_fk("line_items", "user_id", "users", "id"),
           _fk("line_items", "sku", "products", "sku")]
    ok, v = check_integrity(tables, fks)
    assert ok is False
    assert len(v["orphans"]) == 1 and v["orphans"][0]["child_col"] == "sku"
    assert check_record(tables, fks, "INVALID").objective_label == "CORRECT"


def test_multi_fk_all_resolve_is_valid():
    tables = {"users": _USERS, "products": [{"sku": "p1"}, {"sku": "p2"}],
              "line_items": [{"id": 1, "user_id": 1, "sku": "p1"},
                             {"id": 2, "user_id": 2, "sku": "p2"}]}
    fks = [_fk("line_items", "user_id", "users", "id"),
           _fk("line_items", "sku", "products", "sku")]
    ok, _ = check_integrity(tables, fks)
    assert ok is True


def test_self_referential_orphan_is_invalid():
    tables = {"emps": [{"id": 1, "mgr": None}, {"id": 2, "mgr": 1}, {"id": 3, "mgr": 99}]}
    fks = [_fk("emps", "mgr", "emps", "id", nullable=True)]
    ok, v = check_integrity(tables, fks)
    assert ok is False
    assert v["orphans"][0]["value"] == 99
    assert check_record(tables, fks, "INVALID").objective_label == "CORRECT"


def test_computed_answer_surfaces_decision_string():
    tables = {"users": _USERS, "orders": [{"id": 10, "user_id": 1}, {"id": 12, "user_id": 3}]}
    assert check_record(tables, _FK_ORDERS, "VALID").computed_answer == "INVALID"


def test_decision_token_is_normalized_and_validated():
    tables = {"users": _USERS, "orders": [{"id": 10, "user_id": 1}, {"id": 11, "user_id": 2}]}
    assert check_record(tables, _FK_ORDERS, "  valid ").objective_label == "CORRECT"
    for bad in ("MAYBE", "", "true"):
        try:
            check_record(tables, _FK_ORDERS, bad)
            raise AssertionError(f"expected ValueError for decision {bad!r}")
        except ValueError:
            pass


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["tables"], fx["fks"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        ok, _ = check_integrity(fx["tables"], fx["fks"])
        computed = "VALID" if ok else "INVALID"
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
        "none", "orphan_fk", "duplicate_parent_key", "null_in_nonnullable_fk",
        "multi_fk_one_broken", "self_referential_orphan",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _scenario_key(fx):
    return (json.dumps(fx["tables"], sort_keys=True), json.dumps(fx["fks"], sort_keys=True))


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(_scenario_key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[_scenario_key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        ok, _ = check_integrity(fx["tables"], fx["fks"])
        computed = "VALID" if ok else "INVALID"
        assert fx["candidate"] != computed, fx["id"]
