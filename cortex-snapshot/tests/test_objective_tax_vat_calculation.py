"""Frozen tests for the objective tax / VAT-calculation checker (Stage 2 lane, grade B).

Hand-picked checker cases (independent of the fixtures) lock the encoded tables' arithmetic; a full
fixture sweep asserts the checker agrees with every fixture's expected_label; structural tests lock
count range, unique ids, label balance, taxonomy coverage, mutation presence + mutation-integrity;
a Decimal-vs-integer-cents agreement test guards the label authority; and an abstain test proves the
unencoded-jurisdiction fixtures are quarantined, never promoted. No judge anywhere in the verdict
path.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_tax_vat_calculation.checker_tax import (  # noqa: E402
    FAILURE_CLASSES,
    TABLES,
    check_record,
    compute_true_tax,
    compute_true_tax_intcents,
    flat_vat,
    progressive_income,
)
from evals.objective_tax_vat_calculation.fixtures_tax import FIXTURES  # noqa: E402


# ---- hand-picked checker units (independent of fixtures) ---------------------------------------

def test_flat_vat_cent_rounding():
    assert compute_true_tax("ALDERAAN_VAT", 20000) == 4000     # 20% of $200
    assert compute_true_tax("BESPIN_VAT", 30000) == 2100       # 7% of $300
    # 20% of $99.99 = $19.998 -> rounds half-up to $20.00
    assert compute_true_tax("ALDERAAN_VAT", 9999) == 2000


def test_flat_vat_whole_unit_rounding():
    # 19% of $10,050 = $1,909.50 -> nearest whole unit (half-up) = $1,910.00
    assert compute_true_tax("CORUSCANT_VAT", 1005000) == 191000
    # the cent-rounded (wrong-rule) figure differs, which is what the rounding_error fixture uses
    assert flat_vat(1005000, 19, 100, "cent") == 190950


def test_progressive_income_threshold_and_brackets():
    # $15,000 in DAGOBAH: only $5,000 above the $10k threshold, taxed at 10% -> $500
    assert compute_true_tax("DAGOBAH_INCOME", 1500000) == 50000
    # $45,000: $2,000 (10% band) + $3,000 (20% band) = $5,000
    assert compute_true_tax("DAGOBAH_INCOME", 4500000) == 500000
    # below the threshold -> zero tax
    assert compute_true_tax("DAGOBAH_INCOME", 500000) == 0


def test_progressive_income_surcharge():
    # $150,000 in ENDOR: $4,500 (15% band) + $25,000 (25% band) = $29,500 base tax
    assert progressive_income(15000000, TABLES["ENDOR_INCOME"]["brackets"]) == 2950000
    # over $100k -> +5% surcharge = +$1,475 -> $30,975
    assert compute_true_tax("ENDOR_INCOME", 15000000) == 3097500
    # just under the threshold -> no surcharge
    assert compute_true_tax("ENDOR_INCOME", 5000000) == 450000


def test_income_half_up_vs_truncate_differ():
    base = 6512343  # $65,123.43 in ENDOR -> exact $8,280.8575
    assert compute_true_tax("ENDOR_INCOME", base) == 828086            # half-up
    assert progressive_income(base, TABLES["ENDOR_INCOME"]["brackets"],
                              round_mode="truncate") == 828085         # truncated (the wrong figure)


def test_check_record_decision_grading():
    # correct claim, VALID decision -> CORRECT
    assert check_record("ALDERAAN_VAT", 20000, 4000, "VALID").objective_label == "CORRECT"
    # correct claim, INVALID decision -> INCORRECT
    assert check_record("ALDERAAN_VAT", 20000, 4000, "INVALID").objective_label == "INCORRECT"
    # wrong claim ($60 at 20% when BESPIN is 7%), INVALID decision -> CORRECT
    assert check_record("BESPIN_VAT", 30000, 6000, "INVALID").objective_label == "CORRECT"
    # wrong claim, VALID decision -> INCORRECT
    assert check_record("BESPIN_VAT", 30000, 6000, "VALID").objective_label == "INCORRECT"


def test_check_record_rejects_bad_candidate_and_float():
    with pytest.raises(ValueError):
        check_record("ALDERAAN_VAT", 20000, 4000, "MAYBE")
    with pytest.raises(TypeError):
        check_record("ALDERAAN_VAT", 200.0, 4000, "VALID")   # float base rejected
    with pytest.raises(TypeError):
        check_record("ALDERAAN_VAT", 20000, 40.0, "VALID")   # float claim rejected


# ---- full fixture sweep: checker agrees with every fixture's expected_label ---------------------

def test_checker_agrees_with_every_fixture_expected_label():
    for fx in FIXTURES:
        r = check_record(fx["jurisdiction"], fx["base_amount_cents"],
                         fx["claimed_tax_cents"], fx["candidate_answer"])
        assert r.objective_label == fx["expected_label"], \
            f"{fx['id']}: expected {fx['expected_label']}, got {r.objective_label}"


# ---- structural / integrity tests --------------------------------------------------------------

def _promotable():
    return [fx for fx in FIXTURES if fx["failure_class"] != "abstain"]


def test_count_in_range():
    assert 18 <= len(FIXTURES) <= 26, f"unexpected fixture count {len(FIXTURES)}"


def test_ids_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_label_balance():
    prom = _promotable()
    n_correct = sum(1 for fx in prom if fx["expected_label"] == "CORRECT")
    n_incorrect = sum(1 for fx in prom if fx["expected_label"] == "INCORRECT")
    assert n_correct >= 8 and n_incorrect >= 8, (n_correct, n_incorrect)


def test_all_failure_classes_covered():
    seen = {fx["failure_class"] for fx in _promotable()}
    for cls in FAILURE_CLASSES:
        assert cls in seen, f"failure class {cls} not covered"
    assert "none" in seen  # clean baselines present


def test_every_incorrect_states_a_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx["mutation"], f"{fx['id']} is INCORRECT but names no mutation"
        else:
            assert fx["mutation"] == "", f"{fx['id']} is not INCORRECT yet names a mutation"


def test_mutation_integrity_each_incorrect_shares_scenario_with_correct_sibling():
    def scenario_key(fx):
        return (fx["jurisdiction"], fx["base_amount_cents"], fx["claimed_tax_cents"])

    correct_scenarios = Counter(scenario_key(fx) for fx in FIXTURES
                                if fx["expected_label"] == "CORRECT")
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert correct_scenarios[scenario_key(fx)] >= 1, \
                f"{fx['id']} has no CORRECT sibling sharing its identical scenario"
            # and the only field that differs is candidate_answer
            sib = next(s for s in FIXTURES if s["expected_label"] == "CORRECT"
                       and scenario_key(s) == scenario_key(fx))
            assert sib["candidate_answer"] != fx["candidate_answer"]


# ---- Decimal vs independent integer-cents authority --------------------------------------------

def test_decimal_and_intcents_agree_on_every_promotable_fixture():
    for fx in _promotable():
        dec = compute_true_tax(fx["jurisdiction"], fx["base_amount_cents"])
        intc = compute_true_tax_intcents(fx["jurisdiction"], fx["base_amount_cents"])
        assert dec == intc, f"{fx['id']}: Decimal {dec} != int-cents {intc}"


def test_decimal_and_intcents_agree_across_encoded_table_sweep():
    for jur in TABLES:
        for base in (0, 999, 12345, 1000000, 2500000, 5000001, 6512343, 15000000):
            assert compute_true_tax(jur, base) == compute_true_tax_intcents(jur, base), (jur, base)


# ---- abstain set is quarantined, never promoted ------------------------------------------------

def test_abstain_fixtures_present_and_unencoded():
    abst = [fx for fx in FIXTURES if fx["failure_class"] == "abstain"]
    assert len(abst) >= 2
    for fx in abst:
        assert fx["jurisdiction"] not in TABLES
        assert fx["expected_label"] == "UNVERIFIABLE"


def test_abstain_fixtures_make_checker_abstain():
    for fx in FIXTURES:
        if fx["failure_class"] == "abstain":
            r = check_record(fx["jurisdiction"], fx["base_amount_cents"],
                             fx["claimed_tax_cents"], fx["candidate_answer"])
            assert r.abstained and r.objective_label == "UNVERIFIABLE"
            assert r.computed_answer is None and r.true_tax_cents is None


def test_run_quarantines_only_the_abstain_set():
    import evals.objective_tax_vat_calculation.run_tax as runmod
    manifest = runmod.run()
    n_abstain = sum(1 for fx in FIXTURES if fx["failure_class"] == "abstain")
    assert manifest["quarantine"] == n_abstain
    assert manifest["quarantine_reasons"] == {"unencoded_jurisdiction": n_abstain}
    assert manifest["hard_gold"] == len(FIXTURES) - n_abstain
    # every promoted record carries the trainable label and both paths agreed on all of them
    assert manifest["decimal_intcents_cross_validation"]["rate"] == 1.0
    assert set(manifest["label_dist"]) <= {"CORRECT", "INCORRECT"}
