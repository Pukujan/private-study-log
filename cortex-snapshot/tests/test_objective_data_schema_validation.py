"""Frozen tests for the objective data-schema-validation checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic execution of the declared per-column expectations (dtype, non_null,
unique, min/max, enum, regex, fk) against the table -- never a model/judge. These tests pin the
checker's behavior on hand-picked cases (independent of the fixture file) plus a full sweep over
every fixture in fixtures_data_schema.py, asserting the checker's objective_label always matches
the fixture's declared expected_label (the same self-validation gate every other Stage-2 lane
uses), plus mutation-integrity checks.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_data_schema_validation.checker_data_schema import check_table  # noqa: E402
from evals.objective_data_schema_validation.fixtures_data_schema import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the fixture file ---------------------------------------

def test_valid_table_no_violations():
    schema = [{"column": "id", "dtype": "int", "unique": True, "non_null": True}]
    table = [{"id": 1}, {"id": 2}, {"id": 3}]
    r = check_table(schema, table)
    assert r.objective_label == "VALID"
    assert r.violations == []


def test_dtype_violation_wrong_python_type():
    schema = [{"column": "quantity", "dtype": "int"}]
    table = [{"quantity": 5}, {"quantity": "3"}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "dtype_violation"
    assert r.violations[0]["row"] == 1


def test_dtype_bool_is_not_int_even_though_isinstance_would_say_so():
    schema = [{"column": "score", "dtype": "int"}]
    table = [{"score": 7}, {"score": True}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "dtype_violation"


def test_dtype_bool_column_accepts_actual_bool():
    schema = [{"column": "flag", "dtype": "bool"}]
    table = [{"flag": True}, {"flag": False}]
    r = check_table(schema, table)
    assert r.objective_label == "VALID"


def test_dtype_date_requires_parseable_iso_string():
    schema = [{"column": "d", "dtype": "date"}]
    table = [{"d": "2024-05-01"}, {"d": "2024-13-40"}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "dtype_violation"


def test_null_violation_when_non_null_required():
    schema = [{"column": "email", "dtype": "str", "non_null": True}]
    table = [{"email": "a@example.com"}, {"email": None}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "null_violation"


def test_null_allowed_when_non_null_not_declared():
    schema = [{"column": "note", "dtype": "str"}]
    table = [{"note": "hi"}, {"note": None}, {}]
    r = check_table(schema, table)
    assert r.objective_label == "VALID"


def test_missing_key_treated_as_null():
    schema = [{"column": "email", "dtype": "str", "non_null": True}]
    table = [{"email": "a@example.com"}, {}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "null_violation"


def test_uniqueness_violation_on_duplicate():
    schema = [{"column": "id", "dtype": "int", "unique": True}]
    table = [{"id": 1}, {"id": 2}, {"id": 1}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "uniqueness_violation"


def test_range_violation_below_min():
    schema = [{"column": "age", "dtype": "int", "min": 0, "max": 120}]
    table = [{"age": 34}, {"age": -5}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "range_violation"


def test_range_violation_above_max():
    schema = [{"column": "age", "dtype": "int", "min": 0, "max": 120}]
    table = [{"age": 34}, {"age": 200}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "range_violation"


def test_range_bounds_are_inclusive():
    schema = [{"column": "age", "dtype": "int", "min": 0, "max": 120}]
    table = [{"age": 0}, {"age": 120}]
    r = check_table(schema, table)
    assert r.objective_label == "VALID"


def test_enum_violation():
    schema = [{"column": "status", "dtype": "str", "enum": ["open", "closed", "pending"]}]
    table = [{"status": "open"}, {"status": "archived"}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "enum_violation"


def test_regex_violation():
    schema = [{"column": "email", "dtype": "str", "regex": r"[^@\s]+@[^@\s]+\.[^@\s]+"}]
    table = [{"email": "a@example.com"}, {"email": "not-an-email"}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "regex_violation"


def test_fk_violation_self_referencing_column():
    schema = [
        {"column": "emp_id", "dtype": "int", "unique": True, "non_null": True},
        {"column": "manager_id", "dtype": "int", "fk": {"ref_column": "emp_id"}},
    ]
    table = [
        {"emp_id": 1, "manager_id": None},
        {"emp_id": 2, "manager_id": 1},
        {"emp_id": 3, "manager_id": 999},
    ]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "fk_violation"


def test_fk_null_value_is_not_a_violation():
    schema = [
        {"column": "emp_id", "dtype": "int", "unique": True},
        {"column": "manager_id", "dtype": "int", "fk": {"ref_column": "emp_id"}},
    ]
    table = [{"emp_id": 1, "manager_id": None}, {"emp_id": 2, "manager_id": 1}]
    r = check_table(schema, table)
    assert r.objective_label == "VALID"


def test_dtype_failure_suppresses_downstream_checks_on_same_cell():
    """A cell that fails dtype must not ALSO trigger enum/range/regex for the same cell --
    cascade suppression per SPEC.md."""
    schema = [{"column": "status", "dtype": "str", "enum": ["open", "closed"]}]
    table = [{"status": "open"}, {"status": 42}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    types = {v["type"] for v in r.violations if v["row"] == 1}
    assert types == {"dtype_violation"}


def test_date_min_max_lexicographic_iso_comparison():
    schema = [{"column": "d", "dtype": "date", "min": "2024-01-01", "max": "2024-12-31"}]
    table = [{"d": "2024-06-15"}, {"d": "2025-01-01"}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    assert r.violations[0]["type"] == "range_violation"


def test_undeclared_columns_are_ignored():
    schema = [{"column": "id", "dtype": "int"}]
    table = [{"id": 1, "extra_junk": "whatever, not checked"}]
    r = check_table(schema, table)
    assert r.objective_label == "VALID"


def test_unknown_dtype_raises():
    import pytest

    with pytest.raises(ValueError):
        check_table([{"column": "x", "dtype": "not_a_real_dtype"}], [{"x": 1}])


def test_multiple_violations_all_collected_not_short_circuited():
    schema = [
        {"column": "id", "dtype": "int", "unique": True, "non_null": True},
        {"column": "age", "dtype": "int", "min": 0},
    ]
    table = [{"id": 1, "age": 10}, {"id": 1, "age": -5}]
    r = check_table(schema, table)
    assert r.objective_label == "INVALID"
    types = {v["type"] for v in r.violations}
    assert "uniqueness_violation" in types
    assert "range_violation" in types


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_table(fx["schema"], fx["table"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 20


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["VALID"] >= 8
    assert dist["INVALID"] >= 8


def test_all_seven_failure_classes_covered():
    required = {
        "dtype_violation", "null_violation", "uniqueness_violation", "range_violation",
        "enum_violation", "regex_violation", "fk_violation",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_invalid_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INVALID":
            assert fx.get("mutation"), f"{fx['id']} is INVALID but has no mutation description"


def test_mutation_integrity_pairs_share_schema_and_differ_by_exactly_one_cell():
    """Every INVALID fixture must have a same-id-prefix VALID sibling: identical schema, identical
    table shape (same row count, same columns per row), and exactly one (row, column) cell value
    differs -- proof the mutation perturbs exactly one trap, not the question."""
    by_prefix = {}
    for fx in FIXTURES:
        prefix = fx["id"].rsplit("__", 1)[0]
        by_prefix.setdefault(prefix, {})[fx["expected_label"]] = fx

    for prefix, pair in by_prefix.items():
        assert "VALID" in pair and "INVALID" in pair, f"{prefix} missing a VALID/INVALID sibling"
        valid_fx, invalid_fx = pair["VALID"], pair["INVALID"]
        assert valid_fx["schema"] == invalid_fx["schema"], f"{prefix}: schema must be identical"
        vt, it = valid_fx["table"], invalid_fx["table"]
        assert len(vt) == len(it), f"{prefix}: row count must match"
        diffs = 0
        for vrow, irow in zip(vt, it):
            keys = set(vrow) | set(irow)
            for k in keys:
                if vrow.get(k) != irow.get(k):
                    diffs += 1
        assert diffs == 1, f"{prefix}: expected exactly 1 differing cell, found {diffs}"


def test_invalid_fixture_violations_are_confined_to_its_declared_failure_class():
    """Cascade suppression (SPEC.md) means every violation the checker reports for an INVALID
    fixture must carry that fixture's own declared failure_class -- no unrelated class leaks in."""
    for fx in FIXTURES:
        if fx["expected_label"] != "INVALID":
            continue
        r = check_table(fx["schema"], fx["table"])
        types = {v["type"] for v in r.violations}
        assert types == {fx["failure_class"]}, f"{fx['id']}: violation types {types} != " \
            f"{{{fx['failure_class']!r}}}"
