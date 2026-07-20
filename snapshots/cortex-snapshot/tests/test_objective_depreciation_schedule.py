"""Frozen tests for the objective depreciation-schedule checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only per-year depreciation recompute (checker_depr.depreciate /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy + method
coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_depreciation_schedule.checker_depr import (  # noqa: E402
    check_record,
    depreciate,
)
from evals.objective_depreciation_schedule.run_depr import FIXTURES  # noqa: E402

_REQUIRED_FAILURE_CLASSES = {
    "none", "wrong_method", "below_salvage", "wrong_accumulated", "off_by_cent", "wrong_final_year",
}
_METHODS = {"straight_line", "declining_balance", "sum_of_years_digits"}


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_straight_line_even_split():
    sched = depreciate(1_000_000, 100_000, 5, "straight_line")
    assert [r["depreciation"] for r in sched] == [180000, 180000, 180000, 180000, 180000]
    assert [r["book_value"] for r in sched] == [820000, 640000, 460000, 280000, 100000]


def test_straight_line_final_book_equals_salvage_and_absorbs_remainder():
    sched = depreciate(1_000_000, 0, 3, "straight_line")
    assert sched[0]["depreciation"] == 333333
    assert sched[-1]["depreciation"] == 333334          # final row absorbs the rounding remainder
    assert sched[-1]["book_value"] == 0                 # ends exactly at salvage
    assert sum(r["depreciation"] for r in sched) == 1_000_000


def test_declining_balance_halves():
    sched = depreciate(1_000_000, 0, 4, "declining_balance", rate="0.5")
    assert sched[0]["book_value"] == 500000
    assert sched[1]["book_value"] == 250000
    assert sched[1]["book_value"] * 2 == sched[0]["book_value"]


def test_declining_balance_never_below_salvage():
    sched = depreciate(1_000_000, 100_000, 5, "declining_balance", rate="0.4")
    assert all(r["book_value"] >= 100_000 for r in sched)
    assert sched[-1]["book_value"] == 100_000


def test_sum_of_years_digits_weights_and_schedule():
    life = 4
    soyd = life * (life + 1) // 2
    assert sum(life - k + 1 for k in range(1, life + 1)) == soyd == 10
    sched = depreciate(1_000_000, 100_000, 4, "sum_of_years_digits")
    # $9,000 base * [4/10, 3/10, 2/10] for the non-final years
    assert [sched[i]["depreciation"] for i in range(3)] == [360000, 270000, 180000]
    assert sched[-1]["book_value"] == 100_000


def test_book_value_monotonic_non_increasing_all_methods():
    for sched in (
        depreciate(1_000_000, 100_000, 5, "straight_line"),
        depreciate(1_000_000, 100_000, 5, "declining_balance", rate="0.4"),
        depreciate(1_000_000, 100_000, 4, "sum_of_years_digits"),
    ):
        assert all(sched[i]["book_value"] >= sched[i + 1]["book_value"] for i in range(len(sched) - 1))


def test_accumulated_equals_cost_minus_book_all_methods():
    for cost, salv, life, method, rate in (
        (1_000_000, 100_000, 5, "straight_line", None),
        (1_000_000, 100_000, 5, "declining_balance", "0.4"),
        (1_000_000, 100_000, 4, "sum_of_years_digits", None),
    ):
        for r in depreciate(cost, salv, life, method, rate):
            assert r["accumulated"] == cost - r["book_value"]


def test_check_record_correct_and_incorrect():
    asset = {"cost": 1_000_000, "salvage_value": 100_000, "useful_life_years": 5, "method": "straight_line"}
    truth = depreciate(1_000_000, 100_000, 5, "straight_line")[0]
    assert check_record(asset, 1, dict(truth)).objective_label == "CORRECT"
    wrong = dict(truth); wrong["depreciation"] += 1; wrong["book_value"] -= 1; wrong["accumulated"] += 1
    assert check_record(asset, 1, wrong).objective_label == "INCORRECT"


def test_below_salvage_candidate_is_incorrect():
    asset = {"cost": 1_000_000, "salvage_value": 100_000, "useful_life_years": 5,
             "method": "declining_balance", "rate": "0.4"}
    truth = depreciate(1_000_000, 100_000, 5, "declining_balance", rate="0.4")[3]
    below = dict(truth)
    drop = below["book_value"] - 90_000
    below["book_value"] = 90_000; below["depreciation"] += drop; below["accumulated"] += drop
    assert below["book_value"] < 100_000
    assert check_record(asset, 4, below).objective_label == "INCORRECT"


def test_computed_answer_is_the_true_row():
    asset = {"cost": 1_000_000, "salvage_value": 0, "useful_life_years": 3, "method": "sum_of_years_digits"}
    r = check_record(asset, 2, {"year": 2, "depreciation": 0, "accumulated": 0, "book_value": 0})
    assert r.computed_answer == depreciate(1_000_000, 0, 3, "sum_of_years_digits")[1]


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["asset"], fx["year"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_recompute():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        truth = depreciate(
            fx["asset"]["cost"], fx["asset"]["salvage_value"], fx["asset"]["useful_life_years"],
            fx["asset"]["method"], fx["asset"].get("rate"),
        )[fx["year"] - 1]
        assert fx["candidate"] == truth, fx["id"]


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
    present = {fx["failure_class"] for fx in FIXTURES}
    assert _REQUIRED_FAILURE_CLASSES.issubset(present), _REQUIRED_FAILURE_CLASSES - present


def test_all_methods_present():
    present = {fx["asset"]["method"] for fx in FIXTURES}
    assert _METHODS.issubset(present), _METHODS - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _key(fx):
    return (json.dumps(fx["asset"], sort_keys=True), fx["year"])


def test_mutation_integrity_incorrect_shares_asset_year_with_a_correct_sibling():
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(_key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[_key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        truth = depreciate(
            fx["asset"]["cost"], fx["asset"]["salvage_value"], fx["asset"]["useful_life_years"],
            fx["asset"]["method"], fx["asset"].get("rate"),
        )[fx["year"] - 1]
        assert fx["candidate"] != truth, fx["id"]
