"""Frozen tests for the objective cron next-fire checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only cron-subset next-fire expander (checker_cron.next_fires /
check_record) decides pass/fail via exact ordered datetime-set equality, never a model/judge. These
tests pin the expander on hand-picked cases (independent of the fixture file: step/range/list/dow/
dom-OR-dow/month-rollover), sweep every fixture in fixtures_cron.py asserting the checker's
objective_label matches the fixture's declared expected_label (the same self-validation gate every
Stage-2 lane uses), assert the structural invariants (counts, balance, failure-class coverage,
mutation-integrity), and -- when croniter is importable -- cross-check that the stdlib authority
equals croniter on every fixture.

Written before checker_cron.py was trusted (SDD then TDD): this file pins the contract.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_cron_expression_nextfire.checker_cron import (  # noqa: E402
    check_record,
    crosscheck_fires,
    next_fires,
    parse_cron,
)
from evals.objective_cron_expression_nextfire.fixtures_cron import FIXTURES  # noqa: E402


# --- hand-picked next-fire cases, independent of the fixture file -----------------------------

def test_step_minute_expansion():
    # */15 in the minute field, hour pinned to 9; start lands exactly on a fire -> strictly after.
    assert next_fires("*/15 9 * * *", "2024-01-01T09:00:00", 4) == [
        "2024-01-01T09:15:00", "2024-01-01T09:30:00",
        "2024-01-01T09:45:00", "2024-01-02T09:00:00"]


def test_range_and_list_fields():
    # hours 8 and 17 on weekdays (dow range 1-5).
    assert next_fires("0 8,17 * * 1-5", "2024-01-01T00:00:00", 4) == [
        "2024-01-01T08:00:00", "2024-01-01T17:00:00",
        "2024-01-02T08:00:00", "2024-01-02T17:00:00"]


def test_dow_monday_expansion():
    assert next_fires("0 9 * * 1", "2024-01-01T00:00:00", 3) == [
        "2024-01-01T09:00:00", "2024-01-08T09:00:00", "2024-01-15T09:00:00"]


def test_dow_sunday_is_zero():
    assert next_fires("0 0 * * 0", "2024-01-01T00:00:00", 2) == [
        "2024-01-07T00:00:00", "2024-01-14T00:00:00"]


def test_dow_seven_aliases_sunday():
    assert next_fires("0 0 * * 7", "2024-01-01T00:00:00", 2) == \
        next_fires("0 0 * * 0", "2024-01-01T00:00:00", 2)


def test_dom_or_dow_union_semantics():
    # both dom (1) and dow (Monday=1) restricted -> a day matches EITHER: the 1st OR any Monday.
    assert next_fires("0 0 1 * 1", "2024-01-01T00:00:00", 4) == [
        "2024-01-08T00:00:00", "2024-01-15T00:00:00",
        "2024-01-22T00:00:00", "2024-01-29T00:00:00"]


def test_dom_only_restricted_ignores_weekday():
    # only dom restricted -> every 15th regardless of weekday.
    assert next_fires("0 12 15 * *", "2024-01-01T00:00:00", 3) == [
        "2024-01-15T12:00:00", "2024-02-15T12:00:00", "2024-03-15T12:00:00"]


def test_day31_skips_short_months_never_clamps():
    # day 31 exists only in Jan/Mar/May/Jul within this span; Feb/Apr/Jun are SKIPPED, not clamped.
    assert next_fires("0 0 31 * *", "2024-01-15T00:00:00", 4) == [
        "2024-01-31T00:00:00", "2024-03-31T00:00:00",
        "2024-05-31T00:00:00", "2024-07-31T00:00:00"]


def test_month_and_year_rollover():
    # quarterly first-of-month crosses Oct(2024) -> Jan(2025): the year must roll.
    assert next_fires("0 0 1 1,4,7,10 *", "2024-02-10T00:00:00", 4) == [
        "2024-04-01T00:00:00", "2024-07-01T00:00:00",
        "2024-10-01T00:00:00", "2025-01-01T00:00:00"]


def test_leap_day_hour_rollover():
    assert next_fires("15,45 * * * *", "2024-02-29T23:30:00", 3) == [
        "2024-02-29T23:45:00", "2024-03-01T00:15:00", "2024-03-01T00:45:00"]


def test_strictly_after_boundary():
    # a start landing exactly on a fire yields the FOLLOWING fire, not the start itself.
    assert next_fires("0 9 * * *", "2024-01-01T09:00:00", 1) == ["2024-01-02T09:00:00"]


def test_start_with_seconds_ignored_but_excludes_same_minute():
    # 09:00:30 start: the 09:00:00 fire is already past; next is 09:01 (every-minute expr).
    assert next_fires("* 9 1 1 *", "2024-01-01T09:00:30", 1) == ["2024-01-01T09:01:00"]


# --- parser guards ----------------------------------------------------------------------------

def test_parse_rejects_wrong_field_count():
    with pytest.raises(ValueError):
        parse_cron("0 9 * *")          # 4 fields
    with pytest.raises(ValueError):
        parse_cron("0 9 * * * *")      # 6 fields (seconds not supported)


def test_parse_rejects_out_of_bounds():
    with pytest.raises(ValueError):
        parse_cron("60 * * * *")       # minute 60 out of [0,59]
    with pytest.raises(ValueError):
        parse_cron("* 24 * * *")       # hour 24 out of [0,23]
    with pytest.raises(ValueError):
        parse_cron("* * 0 * *")        # dom 0 out of [1,31]


def test_parse_rejects_named_macro_and_unsupported_syntax():
    with pytest.raises(ValueError):
        parse_cron("@weekly")          # macros unsupported
    with pytest.raises(ValueError):
        parse_cron("0 0 L * *")        # L unsupported
    with pytest.raises(ValueError):
        parse_cron("0 0 * * 1#2")      # nth-weekday unsupported


def test_next_fires_requires_positive_n():
    with pytest.raises(ValueError):
        next_fires("* * * * *", "2024-01-01T00:00:00", 0)


def test_impossible_expression_raises():
    with pytest.raises(ValueError):
        next_fires("0 0 30 2 *", "2024-01-01T00:00:00", 1)   # Feb 30 never exists


# --- verdict behaviour ------------------------------------------------------------------------

def test_check_record_correct_and_incorrect():
    good = next_fires("*/15 9 * * *", "2024-01-01T09:00:00", 4)
    assert check_record("*/15 9 * * *", "2024-01-01T09:00:00", 4,
                        good).objective_label == "CORRECT"
    assert check_record("*/15 9 * * *", "2024-01-01T09:00:00", 4,
                        good[:-1]).objective_label == "INCORRECT"


def test_check_record_normalizes_formatting():
    good = next_fires("0 0 * * *", "2024-01-01T12:00:00", 2)
    spelled = [s + ".000000" for s in good]   # 00:00:00 == 00:00:00.000000
    assert check_record("0 0 * * *", "2024-01-01T12:00:00", 2,
                        spelled).objective_label == "CORRECT"


def test_check_record_order_matters():
    good = next_fires("0 0 * * *", "2024-01-01T12:00:00", 3)
    shuffled = [good[0], good[2], good[1]]
    assert check_record("0 0 * * *", "2024-01-01T12:00:00", 3,
                        shuffled).objective_label == "INCORRECT"


def test_self_test_runs():
    from evals.objective_cron_expression_nextfire.checker_cron import self_test
    self_test()


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["expr"], fx["start"], fx["n"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "wrong_step", "missing_fire", "extra_fire",
        "wrong_dow_dom", "month_rollover", "none",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_correct_sibling():
    """Every INCORRECT fixture must have a same-expr/same-start/same-n CORRECT sibling (same
    next-fire question, only the candidate list differs) -- proof it perturbs the answer, not the
    scenario."""
    def key(fx):
        return (fx["expr"], fx["start"], fx["n"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_actually_differs_from_correct_sibling():
    """A perturbation that accidentally equals the true fire list would be mislabeled; assert every
    INCORRECT candidate really differs from its CORRECT sibling's candidate."""
    correct_by_key = {}
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            correct_by_key[(fx["expr"], fx["start"], fx["n"])] = fx["candidate"]
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            sib = correct_by_key[(fx["expr"], fx["start"], fx["n"])]
            assert fx["candidate"] != sib, fx["id"]


# --- croniter corroboration (skipped in a bare install) ---------------------------------------

def test_stdlib_matches_croniter_on_every_fixture():
    if crosscheck_fires("0 0 * * *", "2024-01-01T00:00:00", 1) is None:
        pytest.skip("croniter not importable; stdlib authority is uncorroborated but authoritative")
    disagreements = []
    for fx in FIXTURES:
        stdlib = next_fires(fx["expr"], fx["start"], fx["n"])
        cr = crosscheck_fires(fx["expr"], fx["start"], fx["n"])
        if cr != stdlib:
            disagreements.append((fx["id"], stdlib, cr))
    assert disagreements == [], f"stdlib != croniter: {disagreements}"
