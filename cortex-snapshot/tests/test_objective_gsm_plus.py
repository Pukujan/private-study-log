"""Frozen tests for the objective GSM-Plus numeric-answer checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic exact numeric equality (checker_gsm.check_record / normalize_answer),
never a model/judge/execution. These tests pin the checker on hand-picked normalization and
equality cases (independent of the generated file), then load the generated hard_gold.jsonl and
assert the checker reproduces every record's objective_label plus the structural invariants
(counts, balance, every INCORRECT candidate truly != reference, mutation named).
"""

import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_gsm_plus.checker_gsm import (  # noqa: E402
    Unparseable,
    answers_equal,
    check_record,
    is_parseable_reference,
    normalize_answer,
)

HARD_GOLD = ROOT / "evals" / "objective_gsm_plus" / "hard_gold.jsonl"
MANIFEST = ROOT / "evals" / "objective_gsm_plus" / "run_manifest.json"
PROMOTION = ROOT / "evals" / "objective_gsm_plus" / "PROMOTION.jsonl"

MUTATION_CLASSES = {"off_by_one", "order_of_magnitude", "sign_flip", "digit_error", "wrong_operation"}


# --- checker self-test ------------------------------------------------------------------------

def test_checker_self_test():
    from evals.objective_gsm_plus.checker_gsm import self_test
    self_test()  # raises on any failed assertion


# --- hand-picked normalization cases ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("18", Decimal("18")),
    ("18.0", Decimal("18")),
    ("$1,200", Decimal("1200")),
    ("1,200", Decimal("1200")),
    ("14.4", Decimal("14.4")),
    ("-5", Decimal("-5")),
    ("0", Decimal("0")),
    ("25000.25", Decimal("25000.25")),
    ("  27  ", Decimal("27")),
])
def test_normalize_strict(raw, expected):
    assert normalize_answer(raw) == expected


@pytest.mark.parametrize("raw", ["None", "3/5", "", "abc", "n/a"])
def test_unparseable_reference_rejected(raw):
    assert not is_parseable_reference(raw)
    with pytest.raises(Unparseable):
        normalize_answer(raw)


@pytest.mark.parametrize("raw,expected", [
    ("The answer is 27.", Decimal("27")),
    ("#### 18", Decimal("18")),
    ("27 dollars", Decimal("27")),
    ("She makes 14.4", Decimal("14.4")),
])
def test_lenient_candidate_extraction(raw, expected):
    assert normalize_answer(raw, extract=True) == expected


# --- exact-equality cases ---------------------------------------------------------------------

@pytest.mark.parametrize("a,b,eq", [
    ("18", "18", True),
    ("18", "18.0", True),
    ("18", "19", False),
    ("14.4", "14.40", True),
    ("14.4", "14.5", False),
    ("0", "0", True),
    ("-5", "5", False),
])
def test_answers_equal(a, b, eq):
    assert answers_equal(normalize_answer(a), normalize_answer(b)) is eq


@pytest.mark.parametrize("candidate,reference,label", [
    ("27", "27", "CORRECT"),
    ("28", "27", "INCORRECT"),
    ("270", "27", "INCORRECT"),
    ("-27", "27", "INCORRECT"),
    ("14.4", "14.4", "CORRECT"),
    ("The answer is 27.", "27", "CORRECT"),
    ("not a number", "27", "INCORRECT"),
])
def test_check_record_verdicts(candidate, reference, label):
    assert check_record(candidate, reference).objective_label == label


def test_unparseable_reference_raises_in_grade():
    # grading against a bad ground truth is a caller bug (should have been quarantined) -> raises
    with pytest.raises(Unparseable):
        check_record("5", "None")


# --- generated hard-gold artifact -------------------------------------------------------------

def _load_hard_gold():
    assert HARD_GOLD.exists(), "hard_gold.jsonl missing — run run_gsm.py first"
    rows = [json.loads(l) for l in HARD_GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows, "hard_gold.jsonl is empty"
    return rows


def test_checker_reproduces_every_objective_label():
    for rec in _load_hard_gold():
        r = check_record(rec["candidate_answer"], rec["reference_answer"])
        assert r.objective_label == rec["objective_label"], rec["id"]
        assert r.computed_answer == rec["computed_answer"], rec["id"]


def test_balance_and_counts():
    rows = _load_hard_gold()
    dist = Counter(r["objective_label"] for r in rows)
    assert dist["CORRECT"] == dist["INCORRECT"], dist
    assert dist["CORRECT"] >= 8 and dist["INCORRECT"] >= 8, dist
    assert set(dist) == {"CORRECT", "INCORRECT"}


def test_every_incorrect_candidate_truly_differs_and_names_mutation():
    for rec in _load_hard_gold():
        if rec["objective_label"] == "INCORRECT":
            cand = normalize_answer(rec["candidate_answer"], extract=True)
            ref = normalize_answer(rec["reference_answer"])
            assert not answers_equal(cand, ref), rec["id"]
            assert rec["mutation"] in MUTATION_CLASSES, rec
            assert rec["failure_class"] == rec["mutation"], rec
        else:
            assert rec["mutation"] == "" and rec["failure_class"] == "none", rec


def test_pair_integrity_same_question_differs_only_in_candidate():
    rows = _load_hard_gold()
    by_task = {}
    for r in rows:
        by_task.setdefault(r["source_task_id"], {})[r["objective_label"]] = r
    for task_id, pair in by_task.items():
        assert set(pair) == {"CORRECT", "INCORRECT"}, task_id
        c, i = pair["CORRECT"], pair["INCORRECT"]
        assert c["prompt"] == i["prompt"] and c["reference_answer"] == i["reference_answer"], task_id
        assert c["candidate_answer"] != i["candidate_answer"], task_id


def test_all_records_carry_label_authority_and_tier():
    for rec in _load_hard_gold():
        assert rec["label_authority"] == "exact_numeric_answer"
        assert rec["provenance_tier"] == "hard_gold"
        assert "objective_label" in rec


def test_promotion_record_shape():
    rec = json.loads(PROMOTION.read_text(encoding="utf-8").splitlines()[0])
    assert rec["lane"] == "gsm_plus"
    assert rec["label_authority"] == "exact_numeric_answer"
    assert rec["label_field"] == "objective_label"
    assert rec["judge_in_verdict_path"] is False
    assert rec["trainable"] is True
    assert rec["count"] == sum(rec["label_dist"].values())


def test_manifest_quarantine_reasons():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # the source has non-numeric references that MUST be quarantined, never fabricated.
    assert m["quarantine"] >= 1
    assert "unparseable_reference" in m["quarantine_reasons"]
    assert m["hard_gold"] == m["used_records"] * 2
