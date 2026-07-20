"""Frozen tests for the objective interest-amortization-schedule checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only Decimal recompute of the full fixed-payment amortization schedule
(checker_amort.amortize / check_record), never a model/judge. These tests pin the checker on
hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants
(balance, unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_interest_amortization_schedule.checker_amort import (  # noqa: E402
    amortize,
    check_record,
    fixed_payment_cents,
)
from evals.objective_interest_amortization_schedule.run_amort import FIXTURES  # noqa: E402

_ROW_FIELDS = ("period", "payment", "interest", "principal", "balance")


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_textbook_100k_6pct_30yr_payment():
    # The classic $100,000 @ 6% APR, 30-year monthly payment is $599.55.
    assert fixed_payment_cents(10_000_000, "0.06", 360) == 59955


def test_first_row_interest_is_principal_times_monthly_rate():
    sched = amortize(10_000_000, "0.06", 360)
    # first interest = round_half_even(principal * annual_rate / 12) = round(100000 * 0.005) = $500
    expected = int((Decimal(10_000_000) * (Decimal("0.06") / 12)).quantize(Decimal(1), ROUND_HALF_EVEN))
    assert sched[0]["interest"] == expected == 50000
    assert sched[0]["period"] == 1


def test_last_row_zeroes_the_balance():
    for P, rate, n in [(10_000_000, "0.06", 360), (500_000, "0.12", 12), (3_000_000, "0.05", 60)]:
        sched = amortize(P, rate, n)
        assert sched[-1]["balance"] == 0, (P, rate, n)
        assert sched[-1]["period"] == n


def test_principal_column_sums_to_principal():
    for P, rate, n in [(10_000_000, "0.06", 360), (2_500_000, "0.045", 48), (1_200_000, "0.09", 24)]:
        sched = amortize(P, rate, n)
        assert sum(row["principal"] for row in sched) == P, (P, rate, n)


def test_zero_rate_splits_evenly_and_zeroes():
    sched = amortize(120_000, "0.00", 12)
    assert all(row["interest"] == 0 for row in sched)
    assert sum(row["principal"] for row in sched) == 120_000
    assert sched[-1]["balance"] == 0


def test_every_row_internally_consistent():
    sched = amortize(3_000_000, "0.05", 60)
    assert all(row["payment"] == row["interest"] + row["principal"] for row in sched)


def test_check_record_correct_and_incorrect():
    loan = {"principal_cents": 10_000_000, "annual_rate": "0.06", "term_months": 360}
    sched = amortize(loan["principal_cents"], loan["annual_rate"], loan["term_months"])
    assert check_record(loan, 1, dict(sched[0])).objective_label == "CORRECT"
    wrong = dict(sched[0]); wrong["interest"] += 1; wrong["principal"] -= 1
    assert check_record(loan, 1, wrong).objective_label == "INCORRECT"


def test_computed_answer_is_the_truth_row():
    loan = {"principal_cents": 500_000, "annual_rate": "0.12", "term_months": 12}
    sched = amortize(loan["principal_cents"], loan["annual_rate"], loan["term_months"])
    r = check_record(loan, 3, dict(sched[2]))
    assert r.computed_answer == sched[2]


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["loan"], fx["period"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_recomputed_row():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        sched = amortize(fx["loan"]["principal_cents"], fx["loan"]["annual_rate"], fx["loan"]["term_months"])
        assert fx["candidate"] == sched[fx["period"] - 1], fx["id"]


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
        "none", "wrong_interest_split", "wrong_payment",
        "balance_drift", "no_final_rounding", "off_by_cent",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_loan_and_period_with_a_correct_sibling():
    import json

    def key(fx):
        return (json.dumps(fx["loan"], sort_keys=True), fx["period"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_recomputed_row():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        sched = amortize(fx["loan"]["principal_cents"], fx["loan"]["annual_rate"], fx["loan"]["term_months"])
        assert fx["candidate"] != sched[fx["period"] - 1], fx["id"]
