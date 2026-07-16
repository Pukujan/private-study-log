"""Frozen tests for the objective recurrence-rule-expansion checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only RRULE-subset expander (checker_recurrence.expand_rule /
check_record) decides pass/fail via exact ordered datetime-set equality, never a model/judge. These
tests pin the expander on hand-picked cases (independent of the fixture file), sweep every fixture
in fixtures_recurrence.py asserting the checker's objective_label matches the fixture's declared
expected_label (the same self-validation gate every Stage-2 lane uses), assert the structural
invariants (counts, balance, failure-class coverage, mutation-integrity), and -- when dateutil is
importable -- cross-check that the stdlib authority equals dateutil.rrule on every fixture.

Written before checker_recurrence.py was trusted (SDD then TDD): this file pins the contract.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_recurrence_rule_expansion.checker_recurrence import (  # noqa: E402
    check_record,
    crosscheck_expand,
    expand_rule,
    parse_rule,
)
from evals.objective_recurrence_rule_expansion.fixtures_recurrence import FIXTURES  # noqa: E402


# --- hand-picked expansion cases, independent of the fixture file ----------------------------

def test_daily_count_expansion():
    assert expand_rule("FREQ=DAILY;COUNT=4", "2024-01-01T09:00:00") == [
        "2024-01-01T09:00:00", "2024-01-02T09:00:00",
        "2024-01-03T09:00:00", "2024-01-04T09:00:00"]


def test_daily_interval_expansion():
    assert expand_rule("FREQ=DAILY;INTERVAL=3;COUNT=3", "2024-02-01T08:00:00") == [
        "2024-02-01T08:00:00", "2024-02-04T08:00:00", "2024-02-07T08:00:00"]


def test_weekly_byday_biweekly_expansion():
    assert expand_rule("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=4", "2024-01-01T09:00:00") == [
        "2024-01-01T09:00:00", "2024-01-03T09:00:00",
        "2024-01-15T09:00:00", "2024-01-17T09:00:00"]


def test_weekly_start_not_in_byday_is_not_forced():
    # start is Wednesday 2024-01-03; BYDAY is MO,FR -> dtstart is NOT emitted, first hit is Fri.
    assert expand_rule("FREQ=WEEKLY;BYDAY=MO,FR;COUNT=3", "2024-01-03T09:00:00") == [
        "2024-01-05T09:00:00", "2024-01-08T09:00:00", "2024-01-12T09:00:00"]


def test_monthly_expansion():
    assert expand_rule("FREQ=MONTHLY;COUNT=3", "2024-01-15T09:00:00") == [
        "2024-01-15T09:00:00", "2024-02-15T09:00:00", "2024-03-15T09:00:00"]


def test_monthly_skips_months_without_the_day():
    # Day 31 exists only in Jan, Mar, May, Jul (within this span) -- Feb/Apr/Jun are SKIPPED,
    # never clamped to the 28th/30th (RFC-5545 / dateutil semantics).
    assert expand_rule("FREQ=MONTHLY;COUNT=4", "2024-01-31T09:00:00") == [
        "2024-01-31T09:00:00", "2024-03-31T09:00:00",
        "2024-05-31T09:00:00", "2024-07-31T09:00:00"]


def test_until_is_inclusive_boundary():
    occ = expand_rule("FREQ=DAILY;UNTIL=2024-01-05T09:00:00", "2024-01-01T09:00:00")
    assert occ[-1] == "2024-01-05T09:00:00"
    assert len(occ) == 5


def test_unbounded_rule_raises():
    with pytest.raises(ValueError):
        expand_rule("FREQ=DAILY", "2024-01-01T09:00:00")


def test_parse_rule_rejects_ordinal_byday():
    with pytest.raises(ValueError):
        parse_rule("FREQ=MONTHLY;BYDAY=1MO;COUNT=3")


def test_check_record_correct_and_incorrect():
    good = expand_rule("FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4", "2024-01-01T09:00:00")
    assert check_record("FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4",
                        "2024-01-01T09:00:00", good).objective_label == "CORRECT"
    assert check_record("FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4",
                        "2024-01-01T09:00:00", good[:-1]).objective_label == "INCORRECT"


def test_check_record_normalizes_formatting():
    # A candidate with a microsecond-suffixed spelling of the same instant must still be CORRECT.
    good = expand_rule("FREQ=DAILY;COUNT=2", "2024-01-01T09:00:00")
    spelled = [s + ".000000" for s in good]  # 09:00:00 == 09:00:00.000000
    assert check_record("FREQ=DAILY;COUNT=2", "2024-01-01T09:00:00",
                        spelled).objective_label == "CORRECT"


def test_check_record_order_matters():
    good = expand_rule("FREQ=DAILY;COUNT=3", "2024-01-01T09:00:00")
    shuffled = [good[0], good[2], good[1]]
    assert check_record("FREQ=DAILY;COUNT=3", "2024-01-01T09:00:00",
                        shuffled).objective_label == "INCORRECT"


def test_self_test_runs():
    from evals.objective_recurrence_rule_expansion.checker_recurrence import self_test
    self_test()


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["rule"], fx["start"], fx["candidate"])
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
        "wrong_interval", "missing_occurrence", "extra_occurrence",
        "wrong_byday", "off_by_one_count", "none",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_rule_and_start_with_correct_sibling():
    """Every INCORRECT fixture must have a same-rule/same-start CORRECT sibling (same recurrence
    question, only the candidate list differs) -- proof it perturbs the answer, not the scenario."""
    def key(fx):
        return (fx["rule"], fx["start"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_actually_differs_from_correct_sibling():
    """A perturbation that accidentally equals the true expansion would be mislabeled; assert every
    INCORRECT candidate really differs from its CORRECT sibling's candidate."""
    correct_by_key = {}
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            correct_by_key[(fx["rule"], fx["start"])] = fx["candidate"]
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            sib = correct_by_key[(fx["rule"], fx["start"])]
            assert fx["candidate"] != sib, fx["id"]


# --- dateutil corroboration (skipped in a bare install) --------------------------------------

def test_stdlib_matches_dateutil_on_every_fixture():
    if crosscheck_expand("FREQ=DAILY;COUNT=1", "2024-01-01T09:00:00") is None:
        pytest.skip("dateutil not importable; stdlib authority is uncorroborated but authoritative")
    disagreements = []
    for fx in FIXTURES:
        stdlib = expand_rule(fx["rule"], fx["start"])
        du = crosscheck_expand(fx["rule"], fx["start"])
        if du != stdlib:
            disagreements.append((fx["id"], stdlib, du))
    assert disagreements == [], f"stdlib != dateutil: {disagreements}"
