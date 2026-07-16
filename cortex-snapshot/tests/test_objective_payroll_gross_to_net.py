"""Frozen tests for the objective payroll gross-to-net checker (Stage-2 style lane, grade B).

LABEL AUTHORITY: a stdlib-only Decimal recompute over tables encoded in the checker
(checker_payroll.compute_net / check_record), never a model/judge. These tests pin the checker on
hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants
(balance, unique ids, taxonomy coverage, mutation-integrity, non-empty abstain quarantine). No judge
anywhere in the verdict path.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_payroll_gross_to_net.checker_payroll import (  # noqa: E402
    ENCODED,
    FAILURE_CLASSES,
    check_record,
    compute_net,
    is_encoded,
)
from evals.objective_payroll_gross_to_net.run_payroll import FIXTURES  # noqa: E402


# --- hand-picked checker cases, independent of the runner's fixture list -----------------------

def test_bracketed_income_tax():
    # NORTHLAND $50,000 -> taxable $38,000: 10%*$20k + 20%*$18k = $2,000 + $3,600 = $5,600
    r = compute_net(5000000, "NORTHLAND", "annual")
    assert r["income_tax"] == 560000
    assert r["social"] == 300000            # 6% of $50,000 (< $60,000 cap)
    assert r["net"] == 4140000


def test_standard_deduction_applied():
    # taxing the full gross (no deduction) must yield strictly more income tax
    with_ded = compute_net(4000000, "NORTHLAND", "annual")["income_tax"]
    no_ded = {**ENCODED, "NORTHLAND": {**ENCODED["NORTHLAND"], "standard_deduction": 0}}
    without = compute_net(4000000, "NORTHLAND", "annual", tables=no_ded)["income_tax"]
    assert without > with_ded


def test_social_flat_rate_below_cap():
    # SOUTHISLE 8% of $30,000 = $2,400 (well below the $40,000 cap)
    assert compute_net(3000000, "SOUTHISLE", "annual")["social"] == 240000


def test_social_cap_applied():
    # NORTHLAND $75,000 gross, cap $60,000: 6% of $60,000 = $3,600, NOT 6% of $75,000 ($4,500)
    assert compute_net(7500000, "NORTHLAND", "annual")["social"] == 360000


def test_zero_gross():
    assert compute_net(0, "EASTMARK", "annual") == {"income_tax": 0, "social": 0, "net": 0}


def test_round_half_even_to_the_cent():
    # 6% of $1.75 = 10.5c -> 10 (tie to even, down); 6% of $2.25 = 13.5c -> 14 (tie to even, up)
    assert compute_net(175, "NORTHLAND", "annual")["social"] == 10
    assert compute_net(225, "NORTHLAND", "annual")["social"] == 14


def test_net_identity_holds():
    r = compute_net(6000000, "EASTMARK", "annual")
    assert r["net"] == 6000000 - r["income_tax"] - r["social"]


def test_period_scaling_monthly():
    # monthly grades against 1/12-scaled tables; social 6% of $5,000 = $300
    m = compute_net(500000, "NORTHLAND", "monthly")
    assert m["social"] == 30000
    assert m["net"] == 500000 - m["income_tax"] - m["social"]


def test_unencoded_jurisdiction_abstains():
    assert is_encoded("NORTHLAND") is True
    assert is_encoded("WESTVALE") is False
    r = check_record(5000000, "WESTVALE", "annual",
                     {"income_tax": 0, "social": 0, "net": 5000000})
    assert r.abstained and r.objective_label == "UNVERIFIABLE" and r.computed_answer is None


def test_check_record_exact_match_grading():
    correct = compute_net(4000000, "NORTHLAND", "annual")
    assert check_record(4000000, "NORTHLAND", "annual", correct).objective_label == "CORRECT"
    # any single field off -> INCORRECT
    for k in ("income_tax", "social", "net"):
        bad = {**correct, k: correct[k] + 1}
        assert check_record(4000000, "NORTHLAND", "annual", bad).objective_label == "INCORRECT"


def test_check_record_rejects_bad_candidate_and_float():
    good = compute_net(4000000, "NORTHLAND", "annual")
    with pytest.raises(ValueError):
        check_record(4000000, "NORTHLAND", "annual", {"income_tax": 1, "social": 2})  # missing net
    with pytest.raises(TypeError):
        check_record(4000.0, "NORTHLAND", "annual", good)                             # float gross
    with pytest.raises(TypeError):
        check_record(4000000, "NORTHLAND", "annual", {**good, "net": 1.5})            # float field


def test_computed_answer_is_the_breakdown():
    r = check_record(5000000, "NORTHLAND", "annual",
                     {"income_tax": 0, "social": 0, "net": 5000000})
    assert r.computed_answer == compute_net(5000000, "NORTHLAND", "annual")


# --- full fixture sweep ------------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["gross"], fx["jurisdiction"], fx["period"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_recompute():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == compute_net(fx["gross"], fx["jurisdiction"], fx["period"]), fx["id"]


# --- structural invariants ---------------------------------------------------------------------

def _promotable():
    return [fx for fx in FIXTURES if fx["failure_class"] != "abstain"]


def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 28, len(FIXTURES)


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in _promotable())
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    present = {fx["failure_class"] for fx in _promotable()}
    for cls in FAILURE_CLASSES:
        assert cls in present, f"failure class {cls} not covered"


def test_quarantine_slice_non_empty_and_unencoded():
    abst = [fx for fx in FIXTURES if fx["failure_class"] == "abstain"]
    assert len(abst) >= 2
    for fx in abst:
        assert not is_encoded(fx["jurisdiction"])
        assert fx["expected_label"] == "UNVERIFIABLE"


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but names no mutation"
        else:
            assert fx.get("mutation", "") == "", f"{fx['id']} is not INCORRECT yet names a mutation"


def test_mutation_integrity_incorrect_shares_scenario_with_correct_sibling():
    def key(fx):
        return (fx["gross"], fx["jurisdiction"], fx["period"])

    by_key = {}
    for fx in FIXTURES:
        if fx["failure_class"] == "abstain":
            continue
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_recompute():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != compute_net(fx["gross"], fx["jurisdiction"], fx["period"]), fx["id"]


def test_run_quarantines_only_the_abstain_set():
    import evals.objective_payroll_gross_to_net.run_payroll as runmod
    manifest = runmod.run()
    n_abstain = sum(1 for fx in FIXTURES if fx["failure_class"] == "abstain")
    assert manifest["quarantine"] == n_abstain
    assert manifest["quarantine_reasons"] == {"unencoded_jurisdiction": n_abstain}
    assert manifest["hard_gold"] == len(FIXTURES) - n_abstain
    assert set(manifest["label_dist"]) <= {"CORRECT", "INCORRECT"}
