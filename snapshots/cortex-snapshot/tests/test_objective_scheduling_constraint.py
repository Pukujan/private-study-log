"""Frozen tests for the objective scheduling-constraint checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic interval/constraint arithmetic on integer minutes
(checker_scheduling.find_violations / check_record), never a model/judge. These tests pin the
checker's behavior on hand-picked cases (independent of the fixture file) plus a full sweep over
every fixture in fixtures_scheduling.py, asserting the checker's objective_label always matches the
fixture's declared expected_label (the same self-validation gate every other Stage-2 lane uses).

Written before checker_scheduling.py existed (SDD then TDD): this file defines the contract first.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_scheduling_constraint.checker_scheduling import (  # noqa: E402
    check_record,
    find_violations,
)
from evals.objective_scheduling_constraint.fixtures_scheduling import FIXTURES  # noqa: E402

WORKING_HOURS = {"start": 540, "end": 1020}  # 09:00-17:00
DAY_LENGTH = 1440


def _constraints(capacity=None):
    c = {"working_hours": WORKING_HOURS, "day_length": DAY_LENGTH}
    if capacity:
        c["capacity"] = capacity
    return c


# --- hand-picked cases, independent of the fixture file --------------------------------------

def test_true_overlap_is_invalid():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 570, "end": 630},
    ]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "CORRECT"


def test_true_overlap_claimed_valid_is_incorrect():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 570, "end": 630},
    ]
    r = check_record("schedule_validity", bookings, None, _constraints(), "VALID")
    assert r.objective_label == "INCORRECT"


def test_touching_intervals_do_not_overlap_valid():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 600, "end": 660},
    ]
    r = check_record("schedule_validity", bookings, None, _constraints(), "VALID")
    assert r.objective_label == "CORRECT"


def test_touching_intervals_claimed_invalid_is_incorrect():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 600, "end": 660},
    ]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "INCORRECT"


def test_different_resources_never_conflict():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-B", "start": 540, "end": 600},
    ]
    r = check_record("schedule_validity", bookings, None, _constraints(), "VALID")
    assert r.objective_label == "CORRECT"


def test_outside_working_hours_start_too_early_invalid():
    bookings = [{"id": "b1", "resource": "room-A", "start": 500, "end": 600}]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "CORRECT"


def test_within_working_hours_inclusive_bounds_valid():
    # starts exactly at opening, ends exactly at closing -- inclusive on both ends.
    bookings = [{"id": "b1", "resource": "room-A", "start": 540, "end": 1020}]
    r = check_record("schedule_validity", bookings, None, _constraints(), "VALID")
    assert r.objective_label == "CORRECT"


def test_ends_one_minute_after_closing_invalid():
    bookings = [{"id": "b1", "resource": "room-A", "start": 540, "end": 1021}]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "CORRECT"


def test_day_boundary_crossing_invalid():
    bookings = [{"id": "b1", "resource": "room-A", "start": 1410, "end": 1470}]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "CORRECT"


def test_booking_ending_exactly_at_midnight_does_not_cross_day():
    bookings = [{"id": "b1", "resource": "room-A", "start": 1380, "end": 1440}]
    c = _constraints()
    c["working_hours"] = {"start": 540, "end": 1440}
    r = check_record("schedule_validity", bookings, None, c, "VALID")
    assert r.objective_label == "CORRECT"


def test_zero_length_booking_invalid():
    bookings = [{"id": "b1", "resource": "room-A", "start": 600, "end": 600}]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "CORRECT"


def test_negative_length_booking_invalid():
    bookings = [{"id": "b1", "resource": "room-A", "start": 700, "end": 600}]
    r = check_record("schedule_validity", bookings, None, _constraints(), "INVALID")
    assert r.objective_label == "CORRECT"


def test_capacity_two_third_overlap_exceeds():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 550, "end": 610},
        {"id": "b3", "resource": "room-A", "start": 560, "end": 620},
    ]
    r = check_record("schedule_validity", bookings, None,
                      _constraints(capacity={"room-A": 2}), "INVALID")
    assert r.objective_label == "CORRECT"


def test_capacity_two_two_concurrent_ok():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 550, "end": 610},
    ]
    r = check_record("schedule_validity", bookings, None,
                      _constraints(capacity={"room-A": 2}), "VALID")
    assert r.objective_label == "CORRECT"


def test_booking_decision_accept_no_conflict():
    existing = [{"id": "b1", "resource": "room-A", "start": 540, "end": 600}]
    candidate = {"id": "new1", "resource": "room-A", "start": 600, "end": 660}
    r = check_record("booking_decision", existing, candidate, _constraints(), "ACCEPT")
    assert r.objective_label == "CORRECT"


def test_booking_decision_reject_true_overlap():
    existing = [{"id": "b1", "resource": "room-A", "start": 540, "end": 600}]
    candidate = {"id": "new1", "resource": "room-A", "start": 570, "end": 630}
    r = check_record("booking_decision", existing, candidate, _constraints(), "REJECT")
    assert r.objective_label == "CORRECT"


def test_booking_decision_wrongly_accepted_overlap_is_incorrect():
    existing = [{"id": "b1", "resource": "room-A", "start": 540, "end": 600}]
    candidate = {"id": "new1", "resource": "room-A", "start": 570, "end": 630}
    r = check_record("booking_decision", existing, candidate, _constraints(), "ACCEPT")
    assert r.objective_label == "INCORRECT"


def test_find_violations_empty_for_clean_schedule():
    bookings = [
        {"id": "b1", "resource": "room-A", "start": 540, "end": 600},
        {"id": "b2", "resource": "room-A", "start": 600, "end": 660},
    ]
    assert find_violations(bookings, _constraints()) == []


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["bookings"], fx.get("candidate_booking"),
                          fx["constraints"], fx["candidate_answer"])
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


def test_all_six_failure_classes_covered():
    required = {
        "overlap_missed", "adjacency_as_overlap", "outside_hours",
        "tz_day_bound", "capacity_exceeded", "zero_length",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-op/same-bookings/same-candidate_booking/
    same-constraints CORRECT sibling (same question, only the candidate_answer differs) -- proof
    it perturbs exactly the decision, not the scenario."""
    def key(fx):
        import json
        return (
            fx["op"],
            json.dumps(fx["bookings"], sort_keys=True),
            json.dumps(fx.get("candidate_booking"), sort_keys=True),
            json.dumps(fx["constraints"], sort_keys=True),
        )

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_adjacency_is_not_overlap_invariant_across_all_touching_fixtures():
    """Belt-and-suspenders: no CORRECT-labeled fixture with candidate_answer VALID/ACCEPT should
    have any pair of same-resource bookings that truly overlap (as opposed to merely touch)."""
    for fx in FIXTURES:
        if fx["candidate_answer"] not in ("VALID", "ACCEPT"):
            continue
        if fx["expected_label"] != "CORRECT":
            continue
        bookings = fx["bookings"] + ([fx["candidate_booking"]] if fx.get("candidate_booking") else [])
        by_resource = {}
        for b in bookings:
            by_resource.setdefault(b["resource"], []).append(b)
        for resource, blist in by_resource.items():
            cap = fx["constraints"].get("capacity", {}).get(resource, 1)
            for i in range(len(blist)):
                for j in range(i + 1, len(blist)):
                    a, b = blist[i], blist[j]
                    truly_overlaps = a["start"] < b["end"] and b["start"] < a["end"]
                    if truly_overlaps and cap <= 1:
                        assert False, (
                            f"{fx['id']}: bookings {a['id']}/{b['id']} truly overlap but fixture "
                            "is labeled CORRECT with a VALID/ACCEPT answer under capacity 1"
                        )
