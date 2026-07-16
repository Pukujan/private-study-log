"""Frozen tests for the objective JSON-Schema-conformance checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib subset JSON-Schema validator (checker_jsonschema.validate/check_record),
never a model/judge. These tests pin the checker on hand-picked cases (independent of the fixture
file), sweep every fixture asserting the checker agrees with its declared expected_label, and --
where the `jsonschema` library is importable -- assert the stdlib authority agrees with it.

Written before checker_jsonschema.py per SDD-then-TDD: this file states the contract.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_json_schema_conformance.checker_jsonschema import (  # noqa: E402
    check_record,
    crosscheck_valid,
    is_valid,
    validate,
)
from evals.objective_json_schema_conformance.fixtures_jsonschema import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the fixture file --------------------------------------

def test_integer_conforms():
    assert check_record({"type": "integer"}, 5, "CONFORMS").objective_label == "CORRECT"


def test_string_for_integer_violates():
    assert check_record({"type": "integer"}, "5", "VIOLATES").objective_label == "CORRECT"


def test_string_for_integer_claimed_conforms_is_incorrect():
    assert check_record({"type": "integer"}, "5", "CONFORMS").objective_label == "INCORRECT"


def test_bool_is_not_integer():
    # Python bool is a subclass of int, but JSON boolean is NOT an integer.
    assert not is_valid({"type": "integer"}, True)


def test_bool_is_boolean():
    assert is_valid({"type": "boolean"}, True)


def test_integer_accepts_integral_float():
    # Draft-2020-12: 1.0 is a valid integer.
    assert is_valid({"type": "integer"}, 1.0)
    assert not is_valid({"type": "integer"}, 1.5)


def test_required_missing_violates():
    s = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    assert not is_valid(s, {})
    assert is_valid(s, {"id": "x"})


def test_additional_properties_false():
    s = {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False}
    assert is_valid(s, {"a": 1})
    assert not is_valid(s, {"a": 1, "b": 2})


def test_additional_properties_true_by_default():
    s = {"type": "object", "properties": {"a": {"type": "integer"}}}
    assert is_valid(s, {"a": 1, "b": 2})


def test_enum():
    s = {"enum": ["red", "green", "blue"]}
    assert is_valid(s, "green")
    assert not is_valid(s, "purple")


def test_range_inclusive_bounds():
    s = {"type": "number", "minimum": 0, "maximum": 100}
    assert is_valid(s, 0)
    assert is_valid(s, 100)
    assert not is_valid(s, -1)
    assert not is_valid(s, 101)


def test_exclusive_range():
    s = {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 100}
    assert not is_valid(s, 0)
    assert not is_valid(s, 100)
    assert is_valid(s, 50)


def test_length_bounds_inclusive():
    s = {"type": "string", "minLength": 3, "maxLength": 8}
    assert is_valid(s, "abc")
    assert is_valid(s, "abcdefgh")
    assert not is_valid(s, "ab")
    assert not is_valid(s, "abcdefghi")


def test_pattern():
    s = {"type": "string", "pattern": "^[0-9]{3}$"}
    assert is_valid(s, "123")
    assert not is_valid(s, "12a")
    assert not is_valid(s, "1234")


def test_array_items_typed():
    s = {"type": "array", "items": {"type": "integer"}}
    assert is_valid(s, [1, 2, 3])
    assert not is_valid(s, [1, "x", 3])


def test_unique_items():
    s = {"type": "array", "uniqueItems": True}
    assert is_valid(s, [1, 2, 3])
    assert not is_valid(s, [1, 2, 2])


def test_nested_object_conforms():
    s = {"type": "object",
         "properties": {"name": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
         "required": ["name"], "additionalProperties": False}
    assert is_valid(s, {"name": "widget", "tags": ["a", "b"]})
    assert not is_valid(s, {"name": "widget", "tags": ["a", "b", "c", "d"]})  # maxItems
    assert not is_valid(s, {"name": "widget", "extra": 1})                    # additionalProperties


def test_multiple_errors_reported():
    s = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["id"],
         "additionalProperties": False}
    errs = validate(s, {"n": "x", "z": 1})
    assert len(errs) >= 2  # required 'id' missing, n wrong type, additional 'z'


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["schema"], fx["instance"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_stdlib_agrees_with_jsonschema_library_where_available():
    """Defense-in-depth: for every fixture, the stdlib authority must agree with the jsonschema
    library's verdict wherever the library is importable. Skips cleanly in a bare install."""
    disagreements = []
    checked = 0
    for fx in FIXTURES:
        lib_valid = crosscheck_valid(fx["schema"], fx["instance"])
        if lib_valid is None:
            continue
        checked += 1
        stdlib_valid = is_valid(fx["schema"], fx["instance"])
        if lib_valid != stdlib_valid:
            disagreements.append((fx["id"], stdlib_valid, lib_valid))
    assert disagreements == [], f"stdlib vs jsonschema disagreement: {disagreements}"
    # informational: if the library is present we should have actually cross-checked something
    if checked:
        assert checked == len(FIXTURES)


def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 26


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "type_mismatch", "required_missing", "additional_property", "enum_violation",
        "range_violation", "length_violation", "pattern_violation", "items_violation",
        "uniqueitems_violation",
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
        return (json.dumps(fx["schema"], sort_keys=True), json.dumps(fx["instance"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


# --- regression tests for real-suite-discovered oracle bugs (2026-07-12) ----------------------

def test_boolean_is_not_number_in_enum_const():
    # JSON Schema: false != 0, true != 1 (Python's == leaks the opposite). Real-suite bug.
    assert not is_valid({"enum": [False]}, 0)
    assert not is_valid({"enum": [True]}, 1)
    assert not is_valid({"const": False}, 0)
    assert not is_valid({"const": True}, 1)
    assert not is_valid({"enum": [0]}, False)
    assert is_valid({"enum": [False]}, False)
    assert is_valid({"const": True}, True)


def test_number_integer_interchangeable_in_enum_const():
    assert is_valid({"enum": [1]}, 1.0)
    assert is_valid({"const": 1.0}, 1)


def test_nested_boolean_equality_in_enum():
    assert not is_valid({"enum": [[False]]}, [0])
    assert not is_valid({"const": {"a": True}}, {"a": 1})
    assert is_valid({"const": {"a": True}}, {"a": True})


def test_unique_items_type_aware():
    # [1, true] and [0, false] are UNIQUE under JSON Schema (distinct types). Real-suite bug.
    assert is_valid({"uniqueItems": True}, [1, True])
    assert is_valid({"uniqueItems": True}, [0, False])
    assert not is_valid({"uniqueItems": True}, [1, 1])
    assert not is_valid({"uniqueItems": True}, [1, 1.0])  # 1 == 1.0 -> genuinely duplicate


def test_multiple_of_no_overflow_on_huge_magnitude():
    # Real-suite bug: float `instance / multipleOf` overflowed to inf and crashed round().
    # Should not raise; Decimal modulo decides exactly.
    assert is_valid({"multipleOf": 0.5}, 1e308) in (True, False)  # just must not crash
    assert is_valid({"multipleOf": 2}, 10)
    assert not is_valid({"multipleOf": 3}, 10)
    assert is_valid({"multipleOf": 0.1}, 0.3)  # exact via Decimal(str())


def test_uncompilable_ecma_pattern_does_not_crash():
    # \p{...} unicode-property escapes are valid ECMA-262 but not Python re. Must not crash.
    r = validate({"type": "string", "pattern": r"\p{Letter}"}, "abc")
    assert isinstance(r, list)  # honest limit: pattern not enforced, but no crash


def test_real_suite_tier_reproduces_labels():
    """If the vendored real-data tier exists, the checker must reproduce every promoted record's
    verdict exactly (stdlib-only regression pin over the 350 JSON-Schema-Test-Suite cases)."""
    import json
    real = ROOT / "evals" / "objective_json_schema_conformance" / "hard_gold_real.jsonl"
    if not real.exists():
        import pytest
        pytest.skip("real-data tier not built (run ops/build_jsonschema_real_tier.py)")
    n = 0
    for line in real.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        computed = "CONFORMS" if is_valid(rec["schema"], rec["instance"]) else "VIOLATES"
        assert computed == rec["computed_answer"], rec["id"]
        assert rec["objective_label"] == "CORRECT"
        n += 1
    assert n >= 300, f"expected the full real tier, got {n}"
