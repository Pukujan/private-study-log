"""Frozen tests for the objective multi-hop-QA checker (Stage-2 real-data lane, from FRAMES).

LABEL AUTHORITY: a stdlib-only normalized exact-match (checker_frames.normalize_answer /
is_verifiable / check_record), never a model/judge. These tests pin the checker on hand-picked cases
(independent of the committed fixtures), sweep every hard_gold record asserting the checker agrees
with its stored objective_label, and assert the lane's structural invariants (balance, unique ids,
mutation-integrity, committed-gold size < 1 MB, non-empty abstain slice).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_multi_hop_qa.checker_frames import (  # noqa: E402
    check_record,
    is_verifiable,
    normalize_answer,
)

LANE = ROOT / "evals" / "objective_multi_hop_qa"
HARD_GOLD = LANE / "hard_gold.jsonl"
QUARANTINE = LANE / "quarantine.jsonl"


def _load(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- hand-picked normalize cases, independent of the committed fixtures -----------------------

def test_normalize_lowercases_and_strips():
    assert normalize_answer("  FRANCE  ") == "france"


def test_normalize_collapses_internal_whitespace():
    assert normalize_answer("Jane   Ballou") == "jane ballou"


def test_normalize_drops_leading_article():
    assert normalize_answer("The Battle of Hastings") == "battle of hastings"
    assert normalize_answer("An apple") == "apple"
    assert normalize_answer("a Test") == "test"
    # a non-leading "the" is untouched
    assert normalize_answer("king of the hill") == "king of the hill"


def test_normalize_strips_surrounding_punctuation():
    assert normalize_answer("France.") == "france"
    assert normalize_answer('"Reve d\'Or"') == "reve d'or"


def test_normalize_numeric_equivalence():
    assert normalize_answer("1,000") == "1000"
    assert normalize_answer("506,000") == "506000"
    assert normalize_answer("1,234,567") == "1234567"
    # a comma-joined list is NOT a number -> commas preserved (it is quarantined upstream anyway)
    assert "," in normalize_answer("a, b, c")


def test_normalize_is_idempotent():
    for s in ["The Battle of Hastings", "1,000", "  France. ", "Jane   Ballou"]:
        once = normalize_answer(s)
        assert normalize_answer(once) == once


# --- hand-picked check_record cases -----------------------------------------------------------

def test_exact_match_is_correct():
    assert check_record("q", "Jane Ballou", "Jane Ballou").objective_label == "CORRECT"


def test_case_and_whitespace_insensitive():
    assert check_record("q", "Jane Ballou", "  jane   ballou ").objective_label == "CORRECT"


def test_article_insensitive():
    assert check_record("q", "The Battle of Hastings", "Battle of Hastings").objective_label == "CORRECT"


def test_numeric_equivalence_is_correct():
    assert check_record("q", "506,000", "506000").objective_label == "CORRECT"


def test_wrong_answer_is_incorrect():
    assert check_record("q", "Jane Ballou", "John Smith").objective_label == "INCORRECT"
    assert check_record("q", "France", "Germany").objective_label == "INCORRECT"


def test_computed_answer_is_normalized_reference():
    r = check_record("q", "The France", "Germany")
    assert r.computed_answer == "france"


# --- hand-picked is_verifiable cases ----------------------------------------------------------

def test_is_verifiable_accepts_clean_single_value():
    assert is_verifiable("Jane Ballou") == (True, "")
    assert is_verifiable("France") == (True, "")


def test_is_verifiable_accepts_thousands_separated_number():
    assert is_verifiable("506,000") == (True, "")


def test_is_verifiable_rejects_multi_valued():
    assert is_verifiable("Alice, Bob and Carol") == (False, "unverifiable_multi_valued")
    assert is_verifiable("gold and silver") == (False, "unverifiable_multi_valued")
    assert is_verifiable("a/b") == (False, "unverifiable_multi_valued")


def test_is_verifiable_rejects_too_short():
    assert is_verifiable("9") == (False, "unverifiable_too_short")


def test_is_verifiable_rejects_empty():
    assert is_verifiable("")[0] is False
    assert is_verifiable("   ")[0] is False
    assert is_verifiable(None)[0] is False


def test_is_verifiable_rejects_ambiguous_date():
    assert is_verifiable("01/02/2020") == (False, "unverifiable_ambiguous_date")


# --- full hard_gold sweep: checker must agree with every stored label -------------------------

def test_all_hard_gold_records_checker_agrees_with_stored_label():
    records = _load(HARD_GOLD)
    assert records, "hard_gold.jsonl is empty -- run run_frames.py first"
    mismatches = []
    for rec in records:
        r = check_record(rec["question"], rec["reference"], rec["candidate"])
        if r.objective_label != rec["objective_label"]:
            mismatches.append((rec["id"], rec["objective_label"], r.objective_label))
    assert mismatches == [], f"checker/gold disagreement: {mismatches[:10]}"


def test_correct_records_candidate_equals_reference_under_normalization():
    for rec in _load(HARD_GOLD):
        if rec["objective_label"] != "CORRECT":
            continue
        assert normalize_answer(rec["candidate"]) == normalize_answer(rec["reference"]), rec["id"]


# --- structural invariants --------------------------------------------------------------------

def test_label_distribution_is_balanced_within_one():
    dist = Counter(rec["objective_label"] for rec in _load(HARD_GOLD))
    assert abs(dist["CORRECT"] - dist["INCORRECT"]) <= 1, dist
    assert dist["CORRECT"] > 0 and dist["INCORRECT"] > 0


def test_ids_are_unique():
    ids = [rec["id"] for rec in _load(HARD_GOLD)]
    assert len(ids) == len(set(ids))


def test_every_incorrect_has_mutation_and_donor():
    for rec in _load(HARD_GOLD):
        if rec["objective_label"] != "INCORRECT":
            continue
        assert rec.get("mutation"), f"{rec['id']} INCORRECT but no mutation"
        assert "reference answer of record" in rec["mutation"], rec["id"]
        assert rec["failure_class"] == "wrong_entity", rec["id"]


def test_every_incorrect_candidate_differs_from_computed_under_normalization():
    for rec in _load(HARD_GOLD):
        if rec["objective_label"] != "INCORRECT":
            continue
        assert normalize_answer(rec["candidate"]) != rec["computed_answer"], rec["id"]


def test_committed_hard_gold_is_under_one_megabyte():
    assert HARD_GOLD.exists()
    assert HARD_GOLD.stat().st_size < 1_000_000, HARD_GOLD.stat().st_size


def test_abstain_slice_actually_fired():
    # Real FRAMES has multi-valued answers -- the quarantine (abstain) slice must be non-empty,
    # else is_verifiable is not doing its job.
    quarantined = _load(QUARANTINE)
    assert quarantined, "quarantine.jsonl is empty -- the abstain gate did not fire"
    reasons = {q["reason"] for q in quarantined}
    assert any(r.startswith("unverifiable_") for r in reasons), reasons


def test_hard_gold_records_carry_the_promoted_label_authority():
    for rec in _load(HARD_GOLD):
        assert rec["label_authority"] == "frames_normalized_exact_match", rec["id"]
        assert rec["provenance_tier"] == "hard_gold", rec["id"]
