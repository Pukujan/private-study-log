"""Frozen tests for the objective gRPC/protobuf-conformance checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only .proto-subset parse + a recursive conformance type checker
(checker_proto.parse_proto / validate / check_record), never a model/judge. These tests pin the checker
on hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants (balance,
unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_grpc_protobuf_conformance.checker_proto import (  # noqa: E402
    check_record,
    parse_proto,
    validate,
)
from evals.objective_grpc_protobuf_conformance.run_proto import FIXTURES  # noqa: E402

_PROTO = """
enum Color { RED = 0; GREEN = 1; BLUE = 2; }
message Point { int32 x = 1; int32 y = 2; }
message Shape {
  string name = 1;
  Color color = 2;
  Point origin = 3;
  repeated int32 sides = 4;
  bool filled = 5;
  double area = 6;
}
"""
_DEFS = parse_proto(_PROTO)
_CONFORM = {"name": "tri", "color": "RED", "origin": {"x": 1, "y": 2},
            "sides": [3, 4, 5], "filled": True, "area": 6.0}


def _valid(instance, root="Shape"):
    return validate(instance, root, _DEFS)[0]


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_parser_extracts_messages_enums_and_fields():
    assert set(_DEFS["messages"]) == {"Point", "Shape"}
    assert _DEFS["enums"]["Color"] == {"RED", "GREEN", "BLUE"}
    fld = _DEFS["messages"]["Shape"]["fields"]
    assert fld["sides"]["repeated"] is True and fld["sides"]["type"] == "int32"
    assert fld["name"]["type"] == "string" and fld["origin"]["type"] == "Point"


def test_conforming_instance_is_valid():
    assert _valid(_CONFORM) is True
    assert check_record(_PROTO, "Shape", _CONFORM, "VALID").objective_label == "CORRECT"
    assert check_record(_PROTO, "Shape", _CONFORM, "INVALID").objective_label == "INCORRECT"


def test_wrong_scalar_type_is_invalid():
    assert _valid({**_CONFORM, "name": 123}) is False
    assert check_record(_PROTO, "Shape", {**_CONFORM, "name": 123}, "INVALID").objective_label == "CORRECT"
    assert check_record(_PROTO, "Shape", {**_CONFORM, "name": 123}, "VALID").objective_label == "INCORRECT"


def test_bool_is_not_int32():
    # bool is a Python int subclass but proto int32 must reject it
    assert _valid({"x": True, "y": 2}, root="Point") is False
    assert _valid({"x": 1, "y": 2}, root="Point") is True


def test_int_is_not_bool():
    # a bool field must reject an int (0/1 is not a bool)
    assert _valid({**_CONFORM, "filled": 1}) is False
    assert _valid({**_CONFORM, "filled": False}) is True


def test_double_accepts_int_rejects_bool():
    assert _valid({**_CONFORM, "area": 7}) is True
    assert _valid({**_CONFORM, "area": True}) is False


def test_repeated_field_must_be_a_list():
    assert _valid({**_CONFORM, "sides": 5}) is False
    assert _valid({**_CONFORM, "sides": [1, 2]}) is True


def test_repeated_element_type_is_checked():
    assert _valid({**_CONFORM, "sides": [1, "two", 3]}) is False


def test_nested_message_violation_is_invalid():
    assert _valid({**_CONFORM, "origin": {"x": "nope", "y": 2}}) is False
    assert _valid({**_CONFORM, "origin": {"x": 1, "y": 2}}) is True


def test_nested_unknown_field_is_invalid():
    assert _valid({**_CONFORM, "origin": {"x": 1, "y": 2, "z": 9}}) is False


def test_enum_value_must_be_declared_member_case_sensitive():
    assert _valid({**_CONFORM, "color": "PURPLE"}) is False
    assert _valid({**_CONFORM, "color": "red"}) is False
    assert _valid({**_CONFORM, "color": "BLUE"}) is True


def test_unknown_field_is_invalid():
    assert _valid({**_CONFORM, "bogus": 1}) is False


def test_absent_optional_scalars_are_valid():
    assert _valid({"name": "dot"}) is True
    assert _valid({}) is True


def test_computed_answer_carries_decision_and_problems():
    r = check_record(_PROTO, "Shape", {**_CONFORM, "name": 123}, "INVALID")
    assert r.computed_decision == "INVALID"
    assert r.computed_answer.startswith("INVALID:")
    assert check_record(_PROTO, "Shape", _CONFORM, "VALID").computed_answer == "VALID"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["proto_text"], fx["root_msg"], fx["instance"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        defs = parse_proto(fx["proto_text"])
        conforms, _ = validate(fx["instance"], fx["root_msg"], defs)
        assert fx["candidate"] == ("VALID" if conforms else "INVALID"), fx["id"]


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
        "none", "wrong_scalar_type", "repeated_not_list",
        "nested_violation", "enum_value_invalid", "unknown_field",
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
        return (fx["proto_text"], fx["root_msg"], json.dumps(fx["instance"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    # a genuinely INCORRECT record must carry a candidate that is not the computed decision
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        defs = parse_proto(fx["proto_text"])
        conforms, _ = validate(fx["instance"], fx["root_msg"], defs)
        computed = "VALID" if conforms else "INVALID"
        assert fx["candidate"] != computed, fx["id"]
