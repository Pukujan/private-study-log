"""Frozen tests for the objective number-grounding checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only number extraction + `Decimal` value compare
(checker_numground.extract_numbers / check_record), never a model/judge. These tests pin the checker
on hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants
(balance, unique ids, taxonomy coverage, non-empty abstain slice, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_number_grounding.checker_numground import (  # noqa: E402
    check_record,
    extract_numbers,
    is_verifiable,
)
from evals.objective_number_grounding.run_numground import (  # noqa: E402
    FIXTURES,
    _NONVERIFIABLE,
    run,
)


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_all_grounded_is_correct():
    src = "The report lists 1,200,000 in revenue and 350 staff."
    assert check_record(src, "Revenue was 1,200,000 with 350 staff.").objective_label == "CORRECT"


def test_hallucinated_number_is_incorrect():
    r = check_record("The team has 5 members.", "The team has 5 members in 9 rooms.")
    assert r.objective_label == "INCORRECT"
    assert r.computed_answer == ["9"]


def test_thousands_separator_normalization_both_directions():
    assert check_record("revenue was 1,000", "revenue was 1000").objective_label == "CORRECT"
    assert check_record("revenue was 1000", "revenue was 1,000").objective_label == "CORRECT"
    assert extract_numbers("1,200,000") == [Decimal("1200000")]


def test_percentage_value_match():
    # a source "50%" grounds a summary that states the bare value 50
    assert check_record("the margin was 50%", "the margin was 50").objective_label == "CORRECT"
    assert extract_numbers("a 12% margin") == [Decimal("12")]


def test_currency_normalization():
    assert check_record("it cost $1,000", "it cost 1000").objective_label == "CORRECT"
    assert extract_numbers("$2,500") == [Decimal("2500")]


def test_integer_float_decimal_equality():
    assert check_record("the value is 5", "the value is 5.0").objective_label == "CORRECT"


def test_wrong_value_trap_is_incorrect():
    r = check_record("it is 5 m wide", "it is 50 m wide")
    assert r.objective_label == "INCORRECT" and r.computed_answer == ["50"]


def test_swapped_digits_is_incorrect():
    assert check_record("code 4821", "code 4281").objective_label == "INCORRECT"


def test_number_in_word_token_not_extracted():
    # a number glued to letters is not a standalone figure
    assert extract_numbers("abc123") == []


def test_computed_answer_lists_only_ungrounded():
    r = check_record("there are 5 and 350", "there are 5, 350 and 7")
    assert r.computed_answer == ["7"]


def test_is_verifiable_rejects_word_number():
    ok, why = is_verifiable("Three sites logged 5 events")
    assert ok is False and why == "unverifiable_word_number"


def test_is_verifiable_rejects_derived_figure():
    ok, why = is_verifiable("a combined total of 35")
    assert ok is False and why == "unverifiable_derived_figure"


def test_is_verifiable_rejects_ambiguous_rounding():
    ok, why = is_verifiable("approximately 5,000 residents")
    assert ok is False and why == "unverifiable_ambiguous_rounding"
    assert is_verifiable("~5000 residents")[0] is False


def test_is_verifiable_accepts_clean_digit_summary():
    assert is_verifiable("5 sites logged 350 events")[0] is True


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["source"], fx["summary"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_all_promoted_fixture_summaries_are_verifiable():
    # every fixture the runner promotes must sit inside the deterministic (verifiable) slice
    for fx in FIXTURES:
        assert is_verifiable(fx["summary"])[0] is True, fx["id"]


def test_correct_fixtures_have_no_ungrounded_numbers():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert check_record(fx["source"], fx["summary"]).computed_answer == [], fx["id"]


def test_incorrect_fixtures_inject_exactly_one_ungrounded_number():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert len(check_record(fx["source"], fx["summary"]).computed_answer) == 1, fx["id"]


# --- structural invariants -------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 28


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "none", "hallucinated_figure", "wrong_value", "fabricated_percentage",
        "fabricated_total", "swapped_digits",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_abstain_slice_is_non_empty():
    assert len(_NONVERIFIABLE) > 0
    for source, summary, request, expected_reason in _NONVERIFIABLE:
        ok, why = is_verifiable(summary)
        assert ok is False and why == expected_reason, (summary, why, expected_reason)


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_source_with_a_correct_sibling():
    by_source = {}
    for fx in FIXTURES:
        by_source.setdefault(fx["source"], []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_source[fx["source"]]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_run_emits_balanced_hard_gold_and_non_empty_quarantine():
    manifest = run()
    assert manifest["hard_gold"] == 22
    assert manifest["label_dist"].get("CORRECT") == 11
    assert manifest["label_dist"].get("INCORRECT") == 11
    assert manifest["quarantine"] >= 3
    assert manifest["judge_in_verdict_path"] is False
