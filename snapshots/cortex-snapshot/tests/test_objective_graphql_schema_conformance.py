"""Frozen tests for the objective GraphQL-schema-conformance checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only subset-SDL parse + recursive conformance walk
(checker_graphql.validate / decide / check_record), never a model/judge. These tests pin the checker
on hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
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

from evals.objective_graphql_schema_conformance.checker_graphql import (  # noqa: E402
    check_record,
    decide,
    parse_sdl,
    parse_type_ref,
    validate,
)
from evals.objective_graphql_schema_conformance.run_graphql import FIXTURES  # noqa: E402

_USER = """
type User {
  id: ID!
  name: String!
  age: Int
  active: Boolean!
}
"""
_POST = """
type Author { id: ID! name: String! }
type Post {
  id: ID!
  title: String!
  author: Author!
  tags: [String!]
}
"""
_CART = "type Cart { id: ID! amounts: [Int!]! }"


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_conforming_response_is_valid():
    assert decide(_USER, "User", {"id": "u1", "name": "A", "age": 30, "active": True}) == "VALID"
    # nullable field may be absent
    assert decide(_USER, "User", {"id": "u1", "name": "A", "active": True}) == "VALID"
    r = check_record(_USER, "User", {"id": "u1", "name": "A", "active": True}, "VALID")
    assert r.objective_label == "CORRECT"


def test_missing_nonnull_field_is_invalid():
    assert decide(_USER, "User", {"id": "u1", "active": True}) == "INVALID"
    assert check_record(_USER, "User", {"id": "u1", "active": True}, "INVALID").objective_label == "CORRECT"
    assert check_record(_USER, "User", {"id": "u1", "active": True}, "VALID").objective_label == "INCORRECT"


def test_wrong_scalar_type_is_invalid():
    assert decide(_USER, "User", {"id": "u1", "name": "A", "age": "30", "active": True}) == "INVALID"
    assert decide(_USER, "User", {"id": "u1", "name": 5, "active": True}) == "INVALID"


def test_null_in_nonnull_is_invalid():
    assert decide(_USER, "User", {"id": "u1", "name": None, "active": True}) == "INVALID"


def test_non_list_for_list_is_invalid():
    ok = {"id": "p1", "title": "T", "author": {"id": "a1", "name": "B"}, "tags": ["x"]}
    bad = {**ok, "tags": "x"}
    assert decide(_POST, "Post", ok) == "VALID"
    assert decide(_POST, "Post", bad) == "INVALID"


def test_nested_type_violation_is_invalid():
    # nested Author missing its required name
    assert decide(_POST, "Post", {"id": "p1", "title": "T", "author": {"id": "a1"}}) == "INVALID"


def test_extra_field_is_invalid():
    assert decide(_USER, "User", {"id": "u1", "name": "A", "active": True, "role": "x"}) == "INVALID"
    # extra field on a nested object is also caught
    assert decide(_POST, "Post",
                  {"id": "p1", "title": "T", "author": {"id": "a1", "name": "B", "z": 1}}) == "INVALID"


def test_boolean_vs_int_distinction():
    # int 1 does NOT satisfy Boolean
    assert decide(_USER, "User", {"id": "u1", "name": "A", "active": 1}) == "INVALID"
    # bool True does NOT satisfy Int
    assert decide(_USER, "User", {"id": "u1", "name": "A", "age": True, "active": True}) == "INVALID"


def test_id_accepts_str_and_int_float_accepts_int():
    assert decide(_USER, "User", {"id": 7, "name": "A", "active": True}) == "VALID"
    assert decide(_USER, "User", {"id": "u1", "name": "A", "active": True}) == "VALID"
    fsdl = "type M { x: Float! }"
    assert decide(fsdl, "M", {"x": 3}) == "VALID"
    assert decide(fsdl, "M", {"x": 3.5}) == "VALID"
    assert decide(fsdl, "M", {"x": True}) == "INVALID"


def test_list_element_null_is_invalid():
    assert decide(_CART, "Cart", {"id": "c1", "amounts": [1, None, 3]}) == "INVALID"
    assert decide(_CART, "Cart", {"id": "c1", "amounts": [1, 2, 3]}) == "VALID"


def test_parser_produces_expected_type_map():
    t = parse_sdl(_USER)
    assert t["User"]["id"] == {"kind": "named", "name": "ID", "non_null": True}
    assert t["User"]["age"] == {"kind": "named", "name": "Int", "non_null": False}
    assert parse_type_ref("[String!]") == {
        "kind": "list", "non_null": False,
        "of": {"kind": "named", "name": "String", "non_null": True},
    }
    ok, problems = validate({"id": "u1", "name": "A", "active": True}, "User", t)
    assert ok and problems == []


def test_computed_answer_carries_the_decision():
    r = check_record(_USER, "User", {"id": "u1", "active": True}, "INVALID")
    assert r.computed_answer == "INVALID"
    assert r.problems  # at least one problem recorded


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["sdl"], fx["root_type"], fx["response"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == decide(fx["sdl"], fx["root_type"], fx["response"]), fx["id"]


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
        "none", "missing_nonnull_field", "wrong_scalar_type",
        "null_in_nonnull", "non_list_for_list", "nested_type_violation",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _scenario_key(fx):
    return (fx["sdl"], fx["root_type"], json.dumps(fx["response"], sort_keys=True))


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
        assert fx["candidate"] != decide(fx["sdl"], fx["root_type"], fx["response"]), fx["id"]
