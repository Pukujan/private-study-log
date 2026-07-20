"""Frozen tests for the objective datetime-correctness checker (Stage-2 style lane).

LABEL AUTHORITY: stdlib datetime/zoneinfo computation, never a model/judge. These tests pin the
checker's behavior on hand-picked cases (independent of the fixture file) plus a full sweep over
every fixture in fixtures_datetime.py, asserting the checker's objective_label always matches the
fixture's declared expected_label (the same self-validation gate every other Stage-2 lane uses).
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_datetime_correctness.checker_datetime import check_record  # noqa: E402
from evals.objective_datetime_correctness.fixtures_datetime import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the fixture file --------------------------------------

def test_month_end_rollover_clamped_correct():
    r = check_record("add_months", {"start": "2023-01-31", "months": 1}, "2023-02-28")
    assert r.objective_label == "CORRECT"


def test_month_end_rollover_spillover_incorrect():
    r = check_record("add_months", {"start": "2023-01-31", "months": 1}, "2023-03-03")
    assert r.objective_label == "INCORRECT"


def test_leap_year_century_rule_not_leap():
    r = check_record("is_leap_year", {"year": 1900}, "not_leap")
    assert r.objective_label == "CORRECT"


def test_leap_year_century_rule_wrongly_called_leap():
    r = check_record("is_leap_year", {"year": 1900}, "leap")
    assert r.objective_label == "INCORRECT"


def test_leap_year_divisible_by_400_is_leap():
    r = check_record("is_leap_year", {"year": 2000}, "leap")
    assert r.objective_label == "CORRECT"


def test_weekday_correct():
    r = check_record("weekday", {"date": "2000-01-01"}, "Saturday")
    assert r.objective_label == "CORRECT"


def test_weekday_off_by_one_incorrect():
    r = check_record("weekday", {"date": "2000-01-01"}, "Sunday")
    assert r.objective_label == "INCORRECT"


def test_day_diff_exclusive_default():
    r = check_record("day_diff", {"start": "2024-01-01", "end": "2024-01-10", "inclusive": False}, 9)
    assert r.objective_label == "CORRECT"


def test_day_diff_inclusive_convention():
    r = check_record("day_diff", {"start": "2024-01-01", "end": "2024-01-10", "inclusive": True}, 10)
    assert r.objective_label == "CORRECT"


def test_day_diff_inclusive_but_candidate_used_exclusive_is_incorrect():
    r = check_record("day_diff", {"start": "2024-01-01", "end": "2024-01-10", "inclusive": True}, 9)
    assert r.objective_label == "INCORRECT"


def test_add_days_off_by_one_incorrect():
    r = check_record("add_days", {"start": "2024-06-01", "days": 9}, "2024-06-11")
    assert r.objective_label == "INCORRECT"


def test_add_days_correct():
    r = check_record("add_days", {"start": "2024-06-01", "days": 9}, "2024-06-10")
    assert r.objective_label == "CORRECT"


def test_iso_week_year_boundary_trap():
    # 2018-12-31 is a Monday whose ISO week belongs to 2019, not 2018 -- the classic ISO-week trap.
    r = check_record("iso_week", {"date": "2018-12-31"}, "2019-W01")
    assert r.objective_label == "CORRECT"


def test_iso_week_naive_calendar_year_is_incorrect():
    r = check_record("iso_week", {"date": "2018-12-31"}, "2018-W53")
    assert r.objective_label == "INCORRECT"


def test_dst_gap_canonical_fold0_pre_transition_offset():
    r = check_record(
        "tz_convert",
        {"local_dt": "2024-03-10T02:30:00", "from_tz": "America/New_York", "to_tz": "UTC", "fold": 0},
        "2024-03-10T07:30:00+00:00",
    )
    assert r.objective_label == "CORRECT"


def test_dst_gap_post_transition_offset_is_incorrect_under_convention():
    r = check_record(
        "tz_convert",
        {"local_dt": "2024-03-10T02:30:00", "from_tz": "America/New_York", "to_tz": "UTC", "fold": 0},
        "2024-03-10T06:30:00+00:00",
    )
    assert r.objective_label == "INCORRECT"


def test_dst_overlap_canonical_fold0_first_occurrence():
    r = check_record(
        "tz_convert",
        {"local_dt": "2024-11-03T01:30:00", "from_tz": "America/New_York", "to_tz": "UTC", "fold": 0},
        "2024-11-03T05:30:00+00:00",
    )
    assert r.objective_label == "CORRECT"


def test_dst_overlap_second_occurrence_is_incorrect_under_convention():
    r = check_record(
        "tz_convert",
        {"local_dt": "2024-11-03T01:30:00", "from_tz": "America/New_York", "to_tz": "UTC", "fold": 0},
        "2024-11-03T06:30:00+00:00",
    )
    assert r.objective_label == "INCORRECT"


def test_tz_offset_plain_summer_conversion():
    r = check_record(
        "tz_convert",
        {"local_dt": "2024-07-15T15:00:00", "from_tz": "America/New_York", "to_tz": "UTC"},
        "2024-07-15T19:00:00+00:00",
    )
    assert r.objective_label == "CORRECT"


def test_tz_offset_wrong_offset_is_incorrect():
    r = check_record(
        "tz_convert",
        {"local_dt": "2024-07-15T15:00:00", "from_tz": "America/New_York", "to_tz": "UTC"},
        "2024-07-15T20:00:00+00:00",
    )
    assert r.objective_label == "INCORRECT"


def test_unknown_op_raises():
    import pytest

    with pytest.raises(ValueError):
        check_record("not_a_real_op", {}, "x")


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["inputs"], fx["candidate_answer"])
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
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_nine_failure_classes_covered():
    required = {
        "month_end_rollover", "leap_year", "dst_gap", "dst_overlap", "tz_offset",
        "iso_week", "weekday", "inclusive_exclusive_count", "off_by_one",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_op_and_inputs_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-op/same-inputs CORRECT sibling (same question,
    only the candidate answer differs) -- proof it perturbs exactly one trap, not the question."""
    by_key = {}
    for fx in FIXTURES:
        key = (fx["op"], tuple(sorted(fx["inputs"].items())))
        by_key.setdefault(key, []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        key = (fx["op"], tuple(sorted(fx["inputs"].items())))
        siblings = by_key[key]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
