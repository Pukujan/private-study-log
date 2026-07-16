"""Frozen tests for the explicit promotion state machine (cortex_core/promotion.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core.promotion import (  # noqa: E402
    decide, classify, supersede, State, TRAINABLE, assert_vocabulary_in_sync)


def test_hard_gold_needs_an_objective_checker():
    ok = decide("x", {"label_authority": "bfcl_ast_checker", "objective_verdict": "pass",
                      "checker_decided": True}, "hard_gold")
    assert ok.state == State.PROMOTED and ok.tier == "hard_gold" and ok.asdict()["trainable"]


def test_judge_label_cannot_reach_hard_gold():
    d = decide("x", {"label_authority": "llm_judge_fable", "objective_verdict": "pass"}, "hard_gold")
    assert d.state == State.QUARANTINED  # a judge is not an objective checker


def test_cross_vendor_gold_needs_three_families_and_no_flags():
    # B3 (2026-07-14): Prometheus veto DROPPED from the gate -- it is a
    # non-functional arbiter (kappa=0 on every real calibration run, even with
    # its native template; see calibration/results/LEADERBOARD.md). Promotion
    # to cross_vendor_synthetic_gold now rests solely on >=3 independent judge
    # families + no bias/instability flags. No prometheus_* key required.
    ev = {"agreeing_families": ["anthropic", "openai", "zhipu"]}
    assert decide("x", ev, "cross_vendor_synthetic_gold").state == State.PROMOTED


def test_cross_vendor_gold_blocked_by_two_families():
    ev = {"agreeing_families": ["anthropic", "openai"]}
    assert decide("x", ev, "cross_vendor_synthetic_gold").state == State.QUARANTINED


def test_cross_vendor_gold_blocked_by_style_flag():
    ev = {"agreeing_families": ["a", "b", "c"], "style_flag": True}
    assert decide("x", ev, "cross_vendor_synthetic_gold").state == State.QUARANTINED


def test_prometheus_is_not_a_gate_and_cannot_block_promotion():
    """B3: a dissenting/absent Prometheus MUST NOT change the verdict -- the
    dropped arbiter has zero authority over promotion. (Regression guard so the
    non-functional veto is never silently re-added to the gate chain.)"""
    from cortex_core.promotion import TIER_REQUIREMENTS
    gate_names = {g.__name__ for g in TIER_REQUIREMENTS["cross_vendor_synthetic_gold"]}
    assert "prometheus_not_dissenting_gate" not in gate_names
    base = {"agreeing_families": ["a", "b", "c"]}
    # absent prometheus -> still promoted; dissenting prometheus -> still promoted.
    assert decide("x", base, "cross_vendor_synthetic_gold").state == State.PROMOTED
    assert decide("x", {**base, "prometheus_present": True, "prometheus_strong_dissent": True},
                  "cross_vendor_synthetic_gold").state == State.PROMOTED


def test_classify_picks_strongest_tier():
    # objective checker -> hard_gold (strongest)
    d = classify("x", {"label_authority": "test_execution", "checker_passed": True})
    assert d.tier == "hard_gold"
    # only a single author -> weak_candidate_exemplar
    d2 = classify("y", {"author_model": "fable"})
    assert d2.tier == "weak_candidate_exemplar" and d2.tier not in TRAINABLE


def test_classify_quarantines_when_nothing_qualifies():
    assert classify("z", {}).state == State.QUARANTINED


def test_supersede_transition_preserves_tier():
    d = decide("x", {"author_model": "fable"}, "weak_candidate_exemplar")
    s = supersede(d)
    assert s.state == State.SUPERSEDED and s.tier == "weak_candidate_exemplar"


def test_only_gold_tiers_are_trainable():
    weak = classify("y", {"author_model": "fable"})
    assert not weak.asdict()["trainable"]


def test_promotion_vocabulary_stays_in_sync_with_provenance_tiers():
    # Trust-cluster reconciliation guard: promotion's non_human_verified floor must stay
    # exactly the canonical provenance_tiers constant (single vocabulary source of truth).
    assert_vocabulary_in_sync()


def test_unattested_trainable_evidence_is_capped_at_non_human_verified():
    # never-wait + keystone: unattested evidence that WOULD be trainable is usable now but
    # relabeled to the canonical non_human_verified tier (never trainable without attestation).
    from cortex_core import promotion, provenance_tiers
    d = promotion.derive_tier("z", {"label_authority": "bfcl_ast_checker",
                                     "objective_verdict": "pass", "checker_decided": True})
    assert d.state == State.PROMOTED
    assert d.tier == provenance_tiers.NON_HUMAN_VERIFIED
    assert not d.asdict()["trainable"]
    assert provenance_tiers.is_usable(d.tier)
