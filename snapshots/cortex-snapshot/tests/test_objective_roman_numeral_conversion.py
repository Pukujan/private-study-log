"""Frozen tests for the objective Roman-numeral conversion checker (Stage-2 style lane).

LABEL AUTHORITY: the fixed subtractive Roman-numeral spec algorithm via pure stdlib
(checker_roman.int_to_roman / roman_to_int_strict / is_valid / check_record), never a
model/judge. These tests pin the checker on hand-picked cases (independent of the fixture file)
plus a full sweep over every fixture in fixtures_roman.py, asserting the checker's objective_label
always matches the fixture's declared expected_label, plus structural invariants.

Written before checker_roman.py was trusted: this file defines the contract.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from evals.objective_roman_numeral_conversion.checker_roman import (  # noqa: E402
    check_record,
    int_to_roman,
    is_valid,
    roman_to_int_answer,
    roman_to_int_strict,
)
from evals.objective_roman_numeral_conversion.fixtures_roman import FIXTURES  # noqa: E402


# --- hand-picked oracle cases, independent of the fixture file -------------------------------

def test_subtractive_forms():
    assert int_to_roman(4) == "IV"
    assert int_to_roman(9) == "IX"
    assert int_to_roman(40) == "XL"
    assert int_to_roman(90) == "XC"
    assert int_to_roman(400) == "CD"
    assert int_to_roman(900) == "CM"


def test_composite_numbers():
    assert int_to_roman(1994) == "MCMXCIV"
    assert int_to_roman(2024) == "MMXXIV"
    assert int_to_roman(3999) == "MMMCMXCIX"


def test_range_bounds_enforced():
    with pytest.raises(ValueError):
        int_to_roman(0)
    with pytest.raises(ValueError):
        int_to_roman(4000)
    with pytest.raises(ValueError):
        int_to_roman(-5)


def test_bool_rejected_as_value():
    with pytest.raises(ValueError):
        int_to_roman(True)


def test_illegal_repeats_invalid():
    assert is_valid("IIII") == "INVALID"
    assert is_valid("VV") == "INVALID"
    assert is_valid("LL") == "INVALID"
    assert is_valid("DD") == "INVALID"
    assert is_valid("MMMM") == "INVALID"


def test_illegal_subtractives_invalid():
    for bad in ("IL", "IC", "ID", "IM", "XD", "XM", "VX"):
        assert is_valid(bad) == "INVALID", bad


def test_empty_and_case_invalid():
    assert is_valid("") == "INVALID"
    assert is_valid("iv") == "INVALID"
    assert is_valid("Iv") == "INVALID"


def test_valid_canonical_numerals():
    for good in ("I", "IV", "IX", "XL", "MCMLXXXIV", "MMMCMXCIX"):
        assert is_valid(good) == "VALID", good


def test_parse_strict_rejects_noncanonical():
    assert roman_to_int_answer("IIII") == "INVALID"
    assert roman_to_int_answer("IL") == "INVALID"
    with pytest.raises(ValueError):
        roman_to_int_strict("VV")


def test_parse_correct_values():
    assert roman_to_int_answer("MCMXCIV") == "1994"
    assert roman_to_int_answer("MMMCMXCIX") == "3999"


def test_roundtrip_every_value_1_to_3999():
    for n in range(1, 4000):
        r = int_to_roman(n)
        assert is_valid(r) == "VALID", (n, r)
        assert roman_to_int_strict(r) == n, (n, r)


def test_check_record_correct_and_incorrect_labels():
    assert check_record("int_to_roman", {"value": 4}, "IV").objective_label == "CORRECT"
    assert check_record("int_to_roman", {"value": 4}, "IIII").objective_label == "INCORRECT"


def test_check_record_rejects_unknown_op():
    with pytest.raises(ValueError):
        check_record("nonsense_op", {}, "x")


def test_self_test_passes():
    from evals.objective_roman_numeral_conversion import checker_roman
    checker_roman.self_test()


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["args"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 30


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {"subtractive_notation", "illegal_repeat", "illegal_subtractive",
                "out_of_range", "wrong_case", "none"}
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_three_ops_present():
    ops = {fx["op"] for fx in FIXTURES}
    assert ops == {"int_to_roman", "roman_to_int", "is_valid"}


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} INCORRECT but no mutation"


def test_every_correct_fixture_has_empty_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == "", f"{fx['id']} CORRECT but carries a mutation"


def test_mutation_integrity_incorrect_shares_scenario_with_correct_sibling():
    def key(fx):
        return (fx["op"], json.dumps(fx["args"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
        correct = next(s for s in siblings if s["expected_label"] == "CORRECT")
        assert correct["candidate_answer"] != fx["candidate_answer"], fx["id"]
