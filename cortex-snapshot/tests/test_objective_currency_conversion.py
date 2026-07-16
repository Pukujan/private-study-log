"""Frozen tests for the objective currency-conversion checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only (`decimal`) base-anchored rate recompute (checker_fx.convert /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy coverage,
mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_currency_conversion.checker_fx import (  # noqa: E402
    convert,
    check_record,
)
from evals.objective_currency_conversion.run_fx import FIXTURES, RATES  # noqa: E402

_R = {
    "USD": Decimal("1"),
    "EUR": Decimal("0.92"),
    "GBP": Decimal("0.79"),
    "JPY": Decimal("150.0"),
    "BHD": Decimal("0.376"),
}


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_same_currency_identity():
    assert convert(12345, "USD", "USD", _R) == 12345
    assert convert(999, "JPY", "JPY", _R) == 999
    assert check_record(12345, "USD", "USD", _R, 12345).objective_label == "CORRECT"
    assert check_record(12345, "USD", "USD", _R, 12346).objective_label == "INCORRECT"


def test_jpy_zero_decimal_target_and_source():
    # $100.00 -> JPY 15000 (0-decimal whole yen)
    assert convert(10000, "USD", "JPY", _R) == 15000
    # JPY 15000 -> USD $100.00 -> 10000 cents (round-trip)
    assert convert(15000, "JPY", "USD", _R) == 10000
    assert check_record(10000, "USD", "JPY", _R, 15000).objective_label == "CORRECT"
    # treating JPY as 2-decimal (scaling by 100) is wrong
    assert check_record(10000, "USD", "JPY", _R, 1500000).objective_label == "INCORRECT"


def test_bhd_three_decimal_target_and_source():
    # $100.00 * 0.376 = BHD 37.600 -> 37600 fils
    assert convert(10000, "USD", "BHD", _R) == 37600
    # BHD 37.600 -> USD $100.00 -> 10000 cents (round-trip)
    assert convert(37600, "BHD", "USD", _R) == 10000
    assert check_record(10000, "USD", "BHD", _R, 37600).objective_label == "CORRECT"
    # treating BHD as 2-decimal drops a factor of 10
    assert check_record(10000, "USD", "BHD", _R, 3760).objective_label == "INCORRECT"


def test_triangulated_cross_rate_eur_to_jpy():
    # EUR 100.00 -> USD 108.6957 -> JPY 16304.35 -> 16304 yen (triangulated through USD)
    assert convert(10000, "EUR", "JPY", _R) == 16304
    # applying the JPY rate directly to the EUR amount (no triangulation) gives 15000 -- wrong
    assert check_record(10000, "EUR", "JPY", _R, 15000).objective_label == "INCORRECT"
    assert check_record(10000, "EUR", "JPY", _R, 16304).objective_label == "CORRECT"


def test_half_even_banker_boundary():
    # $1.50 -> 118.5 pence -> banker's rounding to even -> 118 (HALF_UP would wrongly give 119)
    assert convert(150, "USD", "GBP", _R) == 118
    assert check_record(150, "USD", "GBP", _R, 119).objective_label == "INCORRECT"
    # $3.50 -> 276.5 pence -> even -> 276
    assert convert(350, "USD", "GBP", _R) == 276
    assert check_record(350, "USD", "GBP", _R, 277).objective_label == "INCORRECT"


def test_inverse_rate_is_incorrect():
    # correct USD->EUR is 9200; the inverse (100/0.92) rounds to 10870 -- wrong
    assert convert(10000, "USD", "EUR", _R) == 9200
    assert check_record(10000, "USD", "EUR", _R, 10870).objective_label == "INCORRECT"


def test_truncation_differs_from_half_even():
    # $100.05 -> 9204.6 minor units: half-even rounds to 9205, truncation would give 9204
    assert convert(10005, "USD", "EUR", _R) == 9205
    assert check_record(10005, "USD", "EUR", _R, 9204).objective_label == "INCORRECT"


def test_computed_answer_is_the_true_target_minor():
    r = check_record(10000, "USD", "EUR", _R, 9200)
    assert r.computed_answer == 9200


def test_no_float_in_rates_rejected():
    import pytest
    with pytest.raises(TypeError):
        convert(10000, "USD", "EUR", {"USD": 1.0, "EUR": 0.92})


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["amount_minor"], fx["src"], fx["dst"], RATES, fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == convert(fx["amount_minor"], fx["src"], fx["dst"], RATES), fx["id"]


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
        "none", "inverse_rate", "wrong_minor_units",
        "truncate_not_round", "off_by_rounding", "no_triangulation",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return (fx["amount_minor"], fx["src"], fx["dst"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    # a genuinely INCORRECT record must carry a candidate that is not the true converted amount
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != convert(fx["amount_minor"], fx["src"], fx["dst"], RATES), fx["id"]
