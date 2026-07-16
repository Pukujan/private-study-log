"""Frozen tests for arbitration rigor (GAP J5) — cortex_core/arbitration_rigor.py.

These metrics live ALONGSIDE Cohen's kappa (they never weaken the existing
calibration κ reporting). They exist to EXPOSE the confounds a single-order,
equal-weight κ hides:

  * order-reversal consistency + position_bias  -> position bias
  * per-family FP/FN/abstention/calibration-error -> family self-preference + punt bias
  * per_judge_calibration_weight                -> not-equal votes

Anti-circular guard (load-bearing): FP/FN ground truth must be OBJECTIVE gold /
deterministic-oracle labels; where only judge labels exist the metric is stamped
`judge_referenced_only` and is EXCLUDED from any promotion gate. Arbitration output
stays `advisory_semi_gold` — never promotable to hard gold.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core.arbitration_rigor import (  # noqa: E402
    RECORD_TYPE,
    PairedJudgment,
    GroundTruth,
    order_reversal,
    position_bias,
    calibration_weight,
    weighted_vote,
    judge_ground_truth_metrics,
    build_rigor_report,
    write_rigor_report,
    eligible_for_promotion_gate,
    assert_not_promotable,
)


# --- order-reversal consistency + position bias -----------------------------

def _pair(judge, family, ab, ba):
    return PairedJudgment(case_id=f"{judge}-{ab}{ba}", judge=judge, family=family,
                          winner_ab=ab, winner_ba=ba)


def test_flipping_judge_has_low_consistency_and_nonzero_position_bias():
    """A judge that flips its winner on order -> consistency < 1 and nonzero bias."""
    # Always picks the FIRST-presented item: AB -> "A", BA -> "B".
    pairs = [_pair("flip", "zhipu", "A", "B") for _ in range(4)]
    res = order_reversal(pairs)
    assert res.consistency < 1.0
    assert res.consistency == 0.0            # winner never matches across orders
    assert position_bias(pairs) != 0.0
    assert position_bias(pairs) == 1.0       # always first position


def test_consistent_judge_is_1_0_and_zero_bias():
    """A judge that picks the same CONTENT regardless of order -> 1.0 / 0.0."""
    # Always picks content A: AB -> "A" (pos1), BA -> "A" (pos2).
    pairs = [_pair("stable", "openai", "A", "A") for _ in range(5)]
    res = order_reversal(pairs)
    assert res.consistency == 1.0
    assert res.n_pairs == 5
    assert position_bias(pairs) == 0.0


def test_order_reversal_reports_zero_coverage_when_no_second_order_data():
    """No paired data -> honest coverage 0 / None, NEVER a fabricated 1.0."""
    res = order_reversal([])
    assert res.coverage == 0.0
    assert res.n_pairs == 0
    assert res.consistency is None           # missing, not fake-perfect


# --- per_judge_calibration_weight (not equal votes) -------------------------

def test_calibration_weight_rises_with_accuracy():
    assert calibration_weight(accuracy=0.5) == 0.0          # chance -> zero weight
    assert calibration_weight(accuracy=0.9) > calibration_weight(accuracy=0.7)
    assert calibration_weight(accuracy=0.7) > calibration_weight(accuracy=0.5)
    assert calibration_weight(accuracy=1.0) == 1.0


def test_weighted_vote_lets_a_calibrated_judge_outvote_two_weak_ones():
    """One high-weight FAIL beats two low-weight PASS votes (not equal votes)."""
    votes = [
        ("supported", calibration_weight(accuracy=0.55)),   # weak
        ("supported", calibration_weight(accuracy=0.55)),   # weak
        ("unsupported", calibration_weight(accuracy=0.95)),  # strong
    ]
    decision, margin, buckets = weighted_vote(votes)
    assert decision == "fail"
    assert margin > 0
    assert buckets["fail"] > buckets["pass"]


# --- beyond-kappa report: FP / FN / abstention / calibration-error per family ---

def _gt(provenance="objective"):
    # gold: c1,c2 PASS ; c3,c4 FAIL
    return GroundTruth(
        labels={"c1": "supported", "c2": "supported",
                "c3": "unsupported", "c4": "unsupported"},
        provenance=provenance,
    )


def test_false_pass_and_false_fail_rates_are_exact():
    gt = _gt()
    # judge: credits one broken case (c3 FAIL->PASS) => FP; rejects one good (c1 PASS->FAIL) => FN
    verdicts = {
        "c1": {"verdict": "unsupported", "confidence": 0.9},   # false FAIL
        "c2": {"verdict": "supported", "confidence": 0.9},     # ok
        "c3": {"verdict": "supported", "confidence": 0.9},     # false PASS
        "c4": {"verdict": "unsupported", "confidence": 0.9},   # ok
    }
    m = judge_ground_truth_metrics("j", "zhipu", verdicts, gt)
    assert m["false_pass_rate"] == 0.5    # 1 of 2 gold-FAIL credited
    assert m["false_fail_rate"] == 0.5    # 1 of 2 gold-PASS rejected


def test_abstention_rate_captures_punt_bias():
    gt = _gt()
    verdicts = {
        "c1": {"verdict": "unverifiable", "confidence": 0.0},
        "c2": {"verdict": "unverifiable", "confidence": 0.0},
        "c3": {"verdict": "unsupported", "confidence": 0.9},
        "c4": {"verdict": "unsupported", "confidence": 0.9},
    }
    m = judge_ground_truth_metrics("punter", "qwen", verdicts, gt)
    assert m["abstention_rate"] == 0.5


def test_report_has_all_four_metrics_broken_out_per_family():
    gt = _gt()
    runs = {
        ("anthropic_j", "anthropic"): {
            "c1": {"verdict": "supported", "confidence": 0.9},
            "c2": {"verdict": "supported", "confidence": 0.9},
            "c3": {"verdict": "unsupported", "confidence": 0.9},
            "c4": {"verdict": "unsupported", "confidence": 0.9},
        },
        ("zhipu_j", "zhipu"): {
            "c1": {"verdict": "supported", "confidence": 0.6},
            "c2": {"verdict": "unverifiable", "confidence": 0.0},
            "c3": {"verdict": "supported", "confidence": 0.6},
            "c4": {"verdict": "unsupported", "confidence": 0.6},
        },
    }
    report = build_rigor_report(runs, gt)
    # every judge row carries all four beyond-kappa metrics
    for row in report["per_judge"]:
        for key in ("false_pass_rate", "false_fail_rate",
                    "abstention_rate", "calibration_error"):
            assert key in row
    # per-family breakout present
    fams = {f["family"] for f in report["per_family"]}
    assert "anthropic" in fams and "zhipu" in fams


# --- anti-circular guard ----------------------------------------------------

def test_judge_referenced_gold_marks_metric_and_excludes_promotion():
    gt = _gt(provenance="judge_referenced")
    verdicts = {"c1": {"verdict": "supported", "confidence": 0.9}}
    m = judge_ground_truth_metrics("j", "anthropic", verdicts, gt)
    assert m["judge_referenced_only"] is True
    assert eligible_for_promotion_gate(m) is False


def test_objective_gold_is_promotion_eligible():
    gt = _gt(provenance="objective")
    verdicts = {"c1": {"verdict": "supported", "confidence": 0.9}}
    m = judge_ground_truth_metrics("j", "anthropic", verdicts, gt)
    assert m["judge_referenced_only"] is False
    assert eligible_for_promotion_gate(m) is True


# --- advisory-semi-gold ceiling ---------------------------------------------

def test_report_is_advisory_semi_gold_never_hard_gold():
    report = build_rigor_report({}, _gt())
    assert report["record_type"] == RECORD_TYPE == "advisory_semi_gold"
    assert report["is_gold"] is False
    assert report["promotable"] is False
    assert report["is_hard_gold"] is False
    assert_not_promotable(report)            # must not raise


def test_assert_not_promotable_raises_on_tampered_report():
    report = build_rigor_report({}, _gt())
    report["promotable"] = True              # simulate a bad refactor
    try:
        assert_not_promotable(report)
    except Exception:
        return
    raise AssertionError("assert_not_promotable must reject a promotable report")


# --- rollup writer (mirrors calibration's leaderboard) ----------------------

def test_rollup_writer_appends_committed_report(tmp_path):
    gt = _gt()
    runs = {
        ("zhipu_j", "zhipu"): {
            "c1": {"verdict": "supported", "confidence": 0.9},
            "c3": {"verdict": "unsupported", "confidence": 0.9},
        },
    }
    md_path, jsonl_path = write_rigor_report(runs, gt, workspace=tmp_path)
    assert md_path.is_file() and jsonl_path.is_file()
    md = md_path.read_text(encoding="utf-8")
    assert "advisory_semi_gold" in md
    assert "false_pass_rate" in md or "false pass" in md.lower()
    # jsonl is re-countable from disk
    rows = [json.loads(l) for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows and all(r["record_type"] == "advisory_semi_gold" for r in rows)
