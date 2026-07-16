"""Frozen tests for the explicit promotion LIFECYCLE state machine
(cortex_core/promotion_state.py, gap J3).

The lifecycle is the artifact-TRUST progression:
    observed -> kedb -> provisional -> calibrated -> oracle_backed -> hard_gold
    -> trainable_gold -> deprecated
Each edge carries a required evidence gate; the CRITICAL edge oracle_backed->hard_gold
demands an INDEPENDENT SECOND AUTHORITY (a single instrument can never self-promote its
own output to hard gold), and ->trainable_gold enforces the anti-distillation rule
(non-Anthropic / non-proprietary producer)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core.promotion_state import (  # noqa: E402
    LifecycleState, LIFECYCLE_ORDER, validate_transition, lifecycle_state_for_tier,
    CALIBRATION_KAPPA_FLOOR)
from cortex_core import promotion  # noqa: E402
from cortex_core.promotion import State  # noqa: E402


# --- evidence fixtures for the legal happy-path chain -----------------------------------
_KEDB_EV = {"occurrence_count": 3, "detection_recipe": "grep AttributeError in trace"}
_PROVISIONAL_EV = {"author_model": "fable"}
_CALIBRATED_EV = {"cohen_kappa": 0.72, "calibration_gold_source": "objective_bfcl_holdout"}
_ORACLE_EV = {"label_authority": "bfcl_ast_checker", "objective_verdict": "pass",
              "checker_decided": True}
_HARD_GOLD_EV = {
    "first_authority": {"id": "bfcl_ast_checker", "family": "bfcl", "kind": "objective"},
    "second_authority": {"id": "local_ast_crosscheck", "family": "cortex",
                         "kind": "objective", "agrees": True}}
_TRAINABLE_EV = {"producer": "qwen", "attestation": {"sig": "..."}}


def test_full_legal_chain_every_step_passes():
    steps = [
        (LifecycleState.OBSERVED, LifecycleState.KEDB, _KEDB_EV),
        (LifecycleState.KEDB, LifecycleState.PROVISIONAL, _PROVISIONAL_EV),
        (LifecycleState.PROVISIONAL, LifecycleState.CALIBRATED, _CALIBRATED_EV),
        (LifecycleState.CALIBRATED, LifecycleState.ORACLE_BACKED, _ORACLE_EV),
        (LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD, _HARD_GOLD_EV),
        (LifecycleState.HARD_GOLD, LifecycleState.TRAINABLE_GOLD, _TRAINABLE_EV),
    ]
    for frm, to, ev in steps:
        ok, reason = validate_transition(frm, to, ev)
        assert ok, f"{frm}->{to} should be legal, got: {reason}"


def test_order_tuple_matches_enum_chain():
    assert LIFECYCLE_ORDER[0] == LifecycleState.OBSERVED
    assert LIFECYCLE_ORDER[-1] == LifecycleState.TRAINABLE_GOLD  # deprecated is off-track


def test_skipping_a_state_fails():
    ok, reason = validate_transition(LifecycleState.OBSERVED, LifecycleState.PROVISIONAL,
                                     {**_KEDB_EV, **_PROVISIONAL_EV})
    assert not ok and "skip" in reason.lower() or "adjacent" in reason.lower()


def test_backward_transition_fails():
    ok, reason = validate_transition(LifecycleState.CALIBRATED, LifecycleState.PROVISIONAL, {})
    assert not ok


def test_unknown_state_fails():
    ok, reason = validate_transition("banana", LifecycleState.KEDB, {})
    assert not ok and "unknown" in reason.lower()


# --- the anti-circular gate (CRITICAL) -------------------------------------------------
def test_hard_gold_without_a_named_second_authority_fails():
    ok, reason = validate_transition(LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD,
                                     {"first_authority": {"id": "bfcl_ast_checker",
                                                          "family": "bfcl"}})
    assert not ok and "second authority" in reason.lower()


def test_hard_gold_with_same_instrument_second_authority_fails():
    """A single instrument can NEVER self-promote its own output to hard gold."""
    ev = {"first_authority": {"id": "bfcl_ast_checker", "family": "bfcl", "kind": "objective"},
          "second_authority": {"id": "bfcl_ast_checker", "family": "bfcl",
                               "kind": "objective", "agrees": True}}
    ok, reason = validate_transition(LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD, ev)
    assert not ok and ("same" in reason.lower() or "distinct" in reason.lower()
                       or "self" in reason.lower())


def test_hard_gold_with_same_family_judge_second_authority_fails():
    """Second authority must be a DIFFERENT family or an objective checker — a same-family
    peer is not an independent authority."""
    ev = {"first_authority": {"id": "llm_judge_fable", "family": "anthropic", "kind": "judge"},
          "second_authority": {"id": "llm_judge_haiku", "family": "anthropic",
                               "kind": "judge", "agrees": True}}
    ok, reason = validate_transition(LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD, ev)
    assert not ok


def test_hard_gold_with_second_authority_that_disagrees_fails():
    ev = {"first_authority": {"id": "bfcl_ast_checker", "family": "bfcl", "kind": "objective"},
          "second_authority": {"id": "local_ast_crosscheck", "family": "cortex",
                               "kind": "objective", "agrees": False}}
    ok, reason = validate_transition(LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD, ev)
    assert not ok


def test_hard_gold_with_distinct_second_authority_passes():
    ok, reason = validate_transition(LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD,
                                     _HARD_GOLD_EV)
    assert ok, reason


# --- the calibration floor -------------------------------------------------------------
def test_calibration_below_kappa_floor_fails():
    ev = {"cohen_kappa": CALIBRATION_KAPPA_FLOOR - 0.05,
          "calibration_gold_source": "objective_bfcl_holdout"}
    ok, reason = validate_transition(LifecycleState.PROVISIONAL, LifecycleState.CALIBRATED, ev)
    assert not ok and "kappa" in reason.lower()


def test_calibration_at_floor_with_independent_gold_passes():
    ev = {"cohen_kappa": CALIBRATION_KAPPA_FLOOR,
          "calibration_gold_source": "objective_bfcl_holdout"}
    ok, reason = validate_transition(LifecycleState.PROVISIONAL, LifecycleState.CALIBRATED, ev)
    assert ok, reason


# --- the anti-distillation gate --------------------------------------------------------
def test_trainable_gold_with_anthropic_producer_fails():
    ev = {"producer": "claude", "attestation": {"sig": "..."}}
    ok, reason = validate_transition(LifecycleState.HARD_GOLD, LifecycleState.TRAINABLE_GOLD, ev)
    assert not ok and ("anthropic" in reason.lower() or "proprietary" in reason.lower()
                       or "distill" in reason.lower())


def test_trainable_gold_with_proprietary_producer_fails():
    for producer in ("gpt", "openai", "gemini", "google"):
        ok, reason = validate_transition(LifecycleState.HARD_GOLD,
                                         LifecycleState.TRAINABLE_GOLD,
                                         {"producer": producer, "attestation": {"sig": "x"}})
        assert not ok, f"proprietary producer {producer!r} must be blocked"


def test_trainable_gold_requires_attestation():
    ok, reason = validate_transition(LifecycleState.HARD_GOLD, LifecycleState.TRAINABLE_GOLD,
                                     {"producer": "qwen"})
    assert not ok and "attest" in reason.lower()


def test_trainable_gold_with_open_producer_and_attestation_passes():
    ok, reason = validate_transition(LifecycleState.HARD_GOLD, LifecycleState.TRAINABLE_GOLD,
                                     _TRAINABLE_EV)
    assert ok, reason


# --- deprecation (supersede-not-delete: reachable from anywhere) ------------------------
def test_any_state_can_deprecate_with_a_reason():
    for frm in LIFECYCLE_ORDER:
        ok, reason = validate_transition(frm, LifecycleState.DEPRECATED,
                                         {"deprecation_reason": "superseded by v2"})
        assert ok, f"{frm}->deprecated should be allowed: {reason}"


def test_deprecate_without_a_reason_fails():
    ok, reason = validate_transition(LifecycleState.HARD_GOLD, LifecycleState.DEPRECATED, {})
    assert not ok


# --- integration with promotion.py -----------------------------------------------------
def test_single_objective_checker_maps_to_oracle_backed_not_hard_gold():
    """A single-instrument promotion.decide() can never yield lifecycle hard_gold —
    the strict J3 anti-circular gate reserves hard_gold for a second authority."""
    d = promotion.decide("x", _ORACLE_EV, "hard_gold")
    assert d.state == State.PROMOTED
    ls = d.asdict()["lifecycle_state"]
    assert ls == LifecycleState.ORACLE_BACKED.value
    assert ls != LifecycleState.HARD_GOLD.value


def test_weak_candidate_exemplar_maps_to_provisional():
    d = promotion.classify("y", {"author_model": "fable"})
    assert d.asdict()["lifecycle_state"] == LifecycleState.PROVISIONAL.value


def test_quarantine_has_no_forward_lifecycle_state():
    d = promotion.classify("z", {})
    assert d.state == State.QUARANTINED
    assert d.asdict()["lifecycle_state"] is None


def test_lifecycle_state_for_tier_mapping():
    assert lifecycle_state_for_tier("weak_candidate_exemplar") == LifecycleState.PROVISIONAL
    assert lifecycle_state_for_tier("non_human_verified") == LifecycleState.ORACLE_BACKED
    assert lifecycle_state_for_tier("hard_gold") == LifecycleState.ORACLE_BACKED
    assert lifecycle_state_for_tier("cross_vendor_synthetic_gold") == LifecycleState.HARD_GOLD
    assert lifecycle_state_for_tier("quarantine") is None
