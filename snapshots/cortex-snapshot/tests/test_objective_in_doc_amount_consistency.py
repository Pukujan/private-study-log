"""Frozen tests for the objective in-document amount-consistency checker (Stage-2 lane).

LABEL AUTHORITY: a stdlib-only number-word parse + Decimal comparison
(checker_amount.words_to_number / check_record), never a model/judge. These tests pin the
checker on hand-picked cases (independent of the runner's fixture list), sweep every
fixture asserting the checker agrees with its declared expected_label, and assert the
lane's structural invariants (balance, unique ids, taxonomy coverage, mutation-integrity).
"""

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_in_doc_amount_consistency.checker_amount import (  # noqa: E402
    check_record,
    extract_amounts,
    words_to_number,
)
from evals.objective_in_doc_amount_consistency.run_amount import FIXTURES  # noqa: E402

D = Decimal


# --- words_to_number edge cases, independent of the runner's fixture list ----------------------

def test_words_units_tens_hundreds():
    assert words_to_number("Seven Hundred Fifty") == D(750)
    assert words_to_number("Two Hundred") == D(200)
    assert words_to_number("Twenty-One") == D(21)
    assert words_to_number("Nineteen") == D(19)


def test_words_thousands_and_millions():
    assert words_to_number("Five Thousand Two Hundred") == D(5200)
    assert words_to_number("Twelve Thousand Three Hundred Forty Five") == D(12345)
    assert words_to_number("One Million") == D(1000000)
    assert words_to_number("One Million Two Hundred Thousand") == D(1200000)


def test_words_cents_variants():
    assert words_to_number("Two Thousand Dollars and 50/100") == D("2000.50")
    assert words_to_number("Three Hundred Twenty Dollars and 25/100") == D("320.25")
    assert words_to_number("One Thousand Dollars and NO/100") == D("1000.00")
    assert words_to_number("Five Dollars and Fifty Cents") == D("5.50")
    assert words_to_number("Nine Point Two Five") == D("9.25")


def test_words_unparseable_returns_none():
    assert words_to_number("banana split") is None
    assert words_to_number("") is None


# --- extract_amounts -------------------------------------------------------------------------

def test_extract_word_and_digit():
    amts = extract_amounts("The sum of Five Thousand Two Hundred Dollars ($5,200.00) is due.")
    assert amts["words"] == D(5200)
    assert amts["digits"] == D("5200.00")
    assert amts["extra_digits"] == []


def test_extract_repeated_digits():
    amts = extract_amounts(
        "Ten Thousand Dollars ($10,000.00); said $10,000.00 is due."
    )
    assert amts["words"] == D(10000)
    assert amts["digits"] == D("10000.00")
    assert amts["extra_digits"] == [D("10000.00")]


# --- check_record: one hand case per failure type -------------------------------------------

def test_agree_is_correct():
    r = check_record("The Buyer shall pay Five Thousand Two Hundred Dollars ($5,200.00).")
    assert r.objective_label == "CORRECT"
    assert r.parseable is True
    assert r.computed_answer["agree"] is True


def test_words_digits_mismatch_is_incorrect():
    r = check_record("The fee of Five Thousand Dollars ($5,200.00) is due.")
    assert r.objective_label == "INCORRECT"


def test_cents_mismatch_is_incorrect():
    r = check_record("Rent is Two Thousand Dollars and 50/100 ($2,000.75).")
    assert r.objective_label == "INCORRECT"


def test_thousand_scale_error_is_incorrect():
    r = check_record("The grant totals Fifteen Thousand Dollars ($1,500.00).")
    assert r.objective_label == "INCORRECT"


def test_repeated_figure_disagreement_is_incorrect():
    r = check_record(
        "The loan of Ten Thousand Dollars ($10,000.00) shall be repaid; said $11,000.00 is due."
    )
    assert r.objective_label == "INCORRECT"


def test_repeated_figure_agreement_is_correct():
    r = check_record(
        "The loan of Ten Thousand Dollars ($10,000.00) shall be repaid; said $10,000.00 is due."
    )
    assert r.objective_label == "CORRECT"


def test_transposed_digits_is_incorrect():
    r = check_record("The award of Five Thousand Two Hundred Dollars ($2,500.00) is final.")
    assert r.objective_label == "INCORRECT"


def test_million_agreement_is_correct():
    r = check_record("The purchase price is One Million Dollars ($1,000,000.00).")
    assert r.objective_label == "CORRECT"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["doc"])
        if not r.parseable or r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label, r.parseable))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


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
        "none", "words_digits_mismatch", "cents_mismatch", "thousand_scale_error",
        "repeated_figure_disagreement", "transposed_digits",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_scenario = {}
    for fx in FIXTURES:
        by_scenario.setdefault(fx["scenario"], []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_scenario[fx["scenario"]]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_doc_differs_from_its_correct_sibling():
    by_scenario = {}
    for fx in FIXTURES:
        by_scenario.setdefault(fx["scenario"], []).append(fx)
    for scenario, group in by_scenario.items():
        correct = [g for g in group if g["expected_label"] == "CORRECT"]
        incorrect = [g for g in group if g["expected_label"] == "INCORRECT"]
        assert correct and incorrect, scenario
        for inc in incorrect:
            assert all(inc["doc"] != c["doc"] for c in correct), inc["id"]
