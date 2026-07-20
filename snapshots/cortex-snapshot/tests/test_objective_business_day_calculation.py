"""Frozen tests for the objective business-day-calculation checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only day-by-day date recompute (checker_bizday.compute / check_record),
never a model/judge. These tests pin the checker on hand-picked cases (independent of the runner's
fixture list), sweep every fixture asserting the checker agrees with its declared expected_label, and
assert the lane's structural invariants (balance, unique ids, taxonomy coverage, both ops present,
mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_business_day_calculation.checker_bizday import (  # noqa: E402
    add_business_days,
    business_days_between,
    check_record,
    compute,
)
from evals.objective_business_day_calculation.run_bizday import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_add_skips_a_weekend():
    # Fri 2026-01-02 + 1 business day -> Mon 2026-01-05 (Sat/Sun skipped)
    assert add_business_days("2026-01-02", 1, []) == "2026-01-05"
    assert check_record("add", {"start": "2026-01-02", "n": 1, "holidays": []},
                        "2026-01-05").objective_label == "CORRECT"
    # a candidate that lands on the Saturday is INCORRECT
    assert check_record("add", {"start": "2026-01-02", "n": 1, "holidays": []},
                        "2026-01-03").objective_label == "INCORRECT"


def test_add_skips_a_holiday():
    # Mon 2026-01-05 + 1, with Tue 2026-01-06 a holiday -> Wed 2026-01-07
    assert add_business_days("2026-01-05", 1, ["2026-01-06"]) == "2026-01-07"
    assert check_record("add", {"start": "2026-01-05", "n": 1, "holidays": ["2026-01-06"]},
                        "2026-01-06").objective_label == "INCORRECT"  # counted the holiday
    assert check_record("add", {"start": "2026-01-05", "n": 1, "holidays": ["2026-01-06"]},
                        "2026-01-07").objective_label == "CORRECT"


def test_negative_n_moves_backward():
    # Mon 2026-01-12 - 2 business days -> Thu 2026-01-08 (weekend skipped)
    assert add_business_days("2026-01-12", -2, []) == "2026-01-08"
    # moving forward instead is INCORRECT
    assert check_record("add", {"start": "2026-01-12", "n": -2, "holidays": []},
                        "2026-01-14").objective_label == "INCORRECT"
    assert check_record("add", {"start": "2026-01-12", "n": -2, "holidays": []},
                        "2026-01-08").objective_label == "CORRECT"


def test_negative_n_skips_holiday_backward():
    # Thu 2026-01-08 - 1 with Wed 2026-01-07 a holiday -> Tue 2026-01-06
    assert add_business_days("2026-01-08", -1, ["2026-01-07"]) == "2026-01-06"


def test_add_zero_adjusts_to_business_day():
    assert add_business_days("2026-01-05", 0, []) == "2026-01-05"          # Mon -> unchanged
    assert add_business_days("2026-01-03", 0, []) == "2026-01-05"          # Sat -> next Mon
    assert add_business_days("2026-01-05", 0, ["2026-01-05"]) == "2026-01-06"  # holiday -> next day


def test_between_excludes_weekends():
    # [09,14) spans a weekend: Fri09, Mon12, Tue13 = 3
    assert business_days_between("2026-01-09", "2026-01-14", []) == 3
    assert check_record("between", {"start": "2026-01-09", "end": "2026-01-14", "holidays": []},
                        5).objective_label == "INCORRECT"  # counted the weekend
    assert check_record("between", {"start": "2026-01-09", "end": "2026-01-14", "holidays": []},
                        3).objective_label == "CORRECT"


def test_between_excludes_holidays():
    # [05,09) with Wed 2026-01-07 a holiday -> 05,06,08 = 3
    assert business_days_between("2026-01-05", "2026-01-09", ["2026-01-07"]) == 3
    assert check_record("between", {"start": "2026-01-05", "end": "2026-01-09", "holidays": ["2026-01-07"]},
                        4).objective_label == "INCORRECT"  # counted the holiday


def test_between_is_half_open_start_included_end_excluded():
    # [05,09): 05,06,07,08 = 4 (Fri 09 excluded)
    assert business_days_between("2026-01-05", "2026-01-09", []) == 4
    # including the exclusive end date is wrong
    assert check_record("between", {"start": "2026-01-05", "end": "2026-01-09", "holidays": []},
                        5).objective_label == "INCORRECT"
    # excluding the inclusive start date is wrong: [06,09) is really 06,07,08 = 3
    assert business_days_between("2026-01-06", "2026-01-09", []) == 3
    assert check_record("between", {"start": "2026-01-06", "end": "2026-01-09", "holidays": []},
                        2).objective_label == "INCORRECT"


def test_between_reversed_interval_is_negative():
    assert business_days_between("2026-01-09", "2026-01-05", []) == -4


def test_computed_answer_is_the_recomputed_truth():
    r = check_record("add", {"start": "2026-01-02", "n": 1, "holidays": []}, "2026-01-05")
    assert r.computed_answer == "2026-01-05"
    r2 = check_record("between", {"start": "2026-01-05", "end": "2026-01-09", "holidays": []}, 4)
    assert r2.computed_answer == 4


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["args"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == compute(fx["op"], fx["args"]), fx["id"]


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
        "none", "counted_weekend", "counted_holiday",
        "off_by_one", "wrong_direction", "boundary_inclusion",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_both_ops_present():
    ops = {fx["op"] for fx in FIXTURES}
    assert ops == {"add", "between"}, ops


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
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


def test_incorrect_candidate_differs_from_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != compute(fx["op"], fx["args"]), fx["id"]
