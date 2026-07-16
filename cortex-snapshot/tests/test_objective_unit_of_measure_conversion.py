"""Frozen tests for the objective unit-of-measure-conversion checker (Stage 2 lane).

These lock the checker's verdicts: hand-picked conversions across length/mass/volume/temperature
(including the affine and prefix cases), a full sweep of every fixture (checker label must equal the
authored expected label, and every INCORRECT record must isolate its named failure class), the
exact-vs-tolerance behaviour, cross-dimension/unknown-unit rejection, and float rejection. Pure
exact rational arithmetic — no judge anywhere in the verdict path.
"""

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_unit_of_measure_conversion.checker_units import (  # noqa: E402
    ABS_TOL, CrossDimensionError, UnknownUnitError, check_record, convert, self_test)
from evals.objective_unit_of_measure_conversion.fixtures_units import (  # noqa: E402
    FIXTURES, INVALID_QUESTIONS)

_FAILURE_CLASSES = {"wrong_factor", "inverse_direction", "temperature_affine_error",
                    "prefix_error", "rounding_off_by"}


# ---- hand-picked conversions (independently hand-checked) ----

@pytest.mark.parametrize("value,from_u,to_u,expected", [
    # length (prefix + exact customary + non-terminating)
    ("5", "km", "m", "5000"),
    ("2", "ft", "in", "24"),
    ("1", "mi", "km", "1.609344"),
    ("1000", "m", "mi", "0.621371192237"),   # non-terminating -> 12 dp
    # mass
    ("3", "kg", "g", "3000"),
    ("10", "lb", "kg", "4.5359237"),
    ("32", "oz", "lb", "2"),
    # volume
    ("1.5", "L", "mL", "1500"),
    ("2", "gal", "L", "7.570823568"),
    # temperature (affine)
    ("100", "C", "F", "212"),
    ("25", "C", "K", "298.15"),
    ("32", "F", "C", "0"),
    ("212", "F", "C", "100"),
    ("100", "F", "C", "37.777777777778"),    # non-terminating -> 12 dp
])
def test_hand_picked_conversions(value, from_u, to_u, expected):
    assert convert(value, from_u, to_u) == Decimal(expected)


def test_prefix_round_trip_is_exact():
    # km -> m -> km is a pure factor round trip; must return exactly.
    assert convert(convert("7.25", "km", "m"), "m", "km") == Decimal("7.25")


def test_temperature_offset_is_the_only_difference_c_k():
    assert convert("0", "C", "K") == Decimal("273.15")
    assert convert("0", "K", "C") == Decimal("-273.15")


# ---- full fixture sweep: checker label == authored label, and class isolation ----

def test_every_fixture_label_matches_checker():
    for fx in FIXTURES:
        r = check_record(fx["value"], fx["from_unit"], fx["to_unit"], fx["candidate_answer"])
        assert r.objective_label == fx["expected_label"], (
            f"{fx['id']}: expected {fx['expected_label']}, checker says {r.objective_label}")


def test_correct_records_carry_none_class_incorrect_carry_a_real_class():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx["failure_class"] == "none", fx["id"]
        else:
            assert fx["failure_class"] in _FAILURE_CLASSES, fx["id"]


def test_correct_answers_are_computed_by_the_checker():
    # every CORRECT candidate must be exactly the checker's canonical answer.
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            true = convert(fx["value"], fx["from_unit"], fx["to_unit"])
            assert Decimal(fx["candidate_answer"]) == true, fx["id"]


# ---- structural / balance invariants ----

def test_balanced_and_sized():
    labels = Counter(fx["expected_label"] for fx in FIXTURES)
    assert labels["CORRECT"] == 12
    assert labels["INCORRECT"] == 12
    assert 18 <= len(FIXTURES) <= 24


def test_each_failure_class_has_at_least_two_records():
    dist = Counter(fx["failure_class"] for fx in FIXTURES if fx["expected_label"] == "INCORRECT")
    for cls in _FAILURE_CLASSES:
        assert dist[cls] >= 2, f"{cls}: only {dist[cls]} record(s)"


def test_all_four_dimensions_represented():
    dims = {fx["dimension"] for fx in FIXTURES}
    assert {"length", "mass", "volume", "temperature"} <= dims


# ---- exact vs tolerance behaviour ----

def test_exact_conversion_requires_exact_equality():
    r_ok = check_record("5", "km", "m", "5000")
    assert r_ok.objective_label == "CORRECT" and r_ok.exact and r_ok.tolerance == "0"
    # one cent off an exact conversion is INCORRECT (no tolerance for terminating results).
    assert check_record("5", "km", "m", "5000.01").objective_label == "INCORRECT"


def test_non_terminating_conversion_uses_tolerance():
    r = check_record("1000", "m", "mi", "0.621371192237")
    assert r.objective_label == "CORRECT" and not r.exact and r.tolerance == str(ABS_TOL)
    # within tolerance still CORRECT, but a coarse rounding is INCORRECT.
    assert check_record("1000", "m", "mi", "0.6213711922").objective_label == "CORRECT"
    assert check_record("1000", "m", "mi", "0.62").objective_label == "INCORRECT"


# ---- rejection paths (cross-dimension / unknown unit / float) ----

def test_cross_dimension_is_rejected():
    with pytest.raises(CrossDimensionError):
        convert("5", "m", "kg")
    with pytest.raises(CrossDimensionError):
        check_record("20", "C", "m", "0")
    # every declared INVALID_QUESTION must raise (never be labelled).
    for _qid, value, from_u, to_u, _reason, _note in INVALID_QUESTIONS:
        with pytest.raises(CrossDimensionError):
            check_record(value, from_u, to_u, "0")


def test_unknown_unit_is_rejected():
    with pytest.raises(UnknownUnitError):
        convert("5", "m", "furlong")


def test_float_value_and_candidate_rejected():
    with pytest.raises(TypeError):
        convert(5.0, "km", "m")
    with pytest.raises(TypeError):
        check_record("5", "km", "m", 5000.0)


# ---- the checker's own self_test is part of the frozen suite ----

def test_checker_self_test_passes():
    self_test()
