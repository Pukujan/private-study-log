"""Explicit promotion state machine (product consolidation).

Promotion logic in Cortex is real but *implicit* — scattered across `graded_eval` (retrieval
change gated on eval win), `_contract_gate` (write gate), `patterns.py` (occurrence floor >= 2),
the `evals/` lanes (checker -> hard_gold / quarantine), and the registry trust tiers. This makes
the shared shape explicit and testable: a candidate moves INTAKE -> {PROMOTED @ tier, QUARANTINED}
by passing a tier's gates, and PROMOTED -> SUPERSEDED later. Same law everywhere:

  * hard_gold                    <- an OBJECTIVE CHECKER decided pass (never a judge).
  * cross_vendor_synthetic_gold  <- >=3 independent judge FAMILIES agree, blinded, no bias/style/
                                    position flags. (The Prometheus veto was DROPPED 2026-07-14 --
                                    it is a non-functional arbiter: kappa=0 on every real
                                    calibration run, even with its native template. See B3 below.)
  * weak_candidate_exemplar      <- single-model/one-family authored (e.g. a Fable rubric).
  * quarantine                   <- gate failure / disagreement / instability.

Gates are pure predicates over an evidence dict, so the same machine drives the eval lanes, the
pattern KEDB, and the registry. This does NOT replace those modules' checks — it names the shared
state model they all instantiate, so promotion is consistent and auditable rather than reinvented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    INTAKE = "intake"
    PROMOTED = "promoted"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"


# strongest -> weakest; only the first two are trainable (lab policy).
# `non_human_verified` is the never-wait floor for UNATTESTED-but-usable evidence: it sits
# below the trainable tiers and below single-source exemplars, but above quarantine. Evidence
# lands here (not blocked) when no server attestation backs it -- usable now, never trainable.
# NOTE: the string literal "non_human_verified" is the canonical vocabulary constant
# provenance_tiers.NON_HUMAN_VERIFIED; it is spelled literally here only because
# provenance_tiers imports this module (a module-level reference would cycle). The
# assert_vocabulary_in_sync() guard (called by the test suite) fails loudly if the two
# ever drift apart.
TIER_ORDER = ("hard_gold", "cross_vendor_synthetic_gold", "weak_candidate_exemplar",
              "non_human_verified", "quarantine")
TRAINABLE = ("hard_gold", "cross_vendor_synthetic_gold")


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class PromotionDecision:
    item_id: str
    state: State
    tier: str
    gate_results: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def asdict(self):
        # Every decision record carries a canonical provenance tier (never-wait trust
        # model, 2026-07-14) so downstream consumers can trust/down-weight it uniformly.
        # Lazy import keeps promotion.py free of a hard dependency cycle.
        from . import provenance_tiers
        return {"item_id": self.item_id, "state": self.state.value, "tier": self.tier,
                "provenance_tier": provenance_tiers.for_promotion_tier(self.tier),
                "lifecycle_state": self.lifecycle_state,
                "trainable": self.tier in TRAINABLE and self.state == State.PROMOTED,
                "gate_results": [{"name": g.name, "passed": g.passed, "detail": g.detail}
                                 for g in self.gate_results],
                "reasons": self.reasons}

    @property
    def lifecycle_state(self):
        """The J3 lifecycle state this decision's tier corresponds to (None when quarantined
        or off the forward track). Wires the explicit promotion state machine
        (cortex_core/promotion_state.py) onto every decision record. A PROMOTED single-checker
        `hard_gold` tier maps to lifecycle ORACLE_BACKED -- lifecycle hard_gold additionally
        requires an independent second authority, which promotion.decide() alone never grants."""
        if self.state != State.PROMOTED:
            return None
        from .promotion_state import lifecycle_state_for_tier
        ls = lifecycle_state_for_tier(self.tier)
        return ls.value if ls is not None else None


# --------------------------------------------------------------------------- gates (pure predicates)
def objective_checker_gate(ev: dict) -> GateResult:
    """hard_gold's defining gate: a deterministic checker decided pass — not a judge."""
    authority = str(ev.get("label_authority", ""))
    verdict = ev.get("objective_verdict") or ev.get("checker_verdict")
    is_checker = ev.get("checker_decided", False) or (
        authority and "judge" not in authority.lower() and "llm" not in authority.lower())
    ok = bool(is_checker) and verdict in ("pass", "vulnerable", "secure", True) or (
        ev.get("checker_passed") is True)
    return GateResult("objective_checker", bool(ok),
                      f"authority={authority!r} verdict={verdict!r}")


def min_families_gate(k: int = 3):
    def g(ev: dict) -> GateResult:
        fams = set(ev.get("agreeing_families", []))
        return GateResult(f"min_{k}_families", len(fams) >= k, f"{sorted(fams)}")
    return g


def no_flags_gate(ev: dict) -> GateResult:
    flags = [f for f in ("style_flag", "verbosity_flag", "position_unstable",
                         "provider_residual_flag", "rubric_ambiguity_flag") if ev.get(f)]
    return GateResult("no_bias_or_instability_flags", not flags, f"flags={flags}")


# B3 (2026-07-14) -- Prometheus veto DROPPED from every gate path.
# Prometheus scored Cohen's kappa = 0 on every real calibration run, including
# WITH its native [RESULT] N template (which IS implemented -- see
# cortex_core/judge.py _build_prometheus_prompt and evals/exemplar_grade.py):
#   * calibration/results/LEADERBOARD.md          -> prometheus accuracy 0.1667, kappa 0.0
#   * calibration/results/BIAS-AUDIT.md           -> prometheus kappa 0.000
#   * calibration/CAPSTONE-bias-and-versioning... -> "Prometheus-7B ... scored kappa=0.0"
# A required veto whose agreement with gold is chance-level is not a functional
# arbiter -- it can only add noise (or silently block valid promotions). The B1
# arbitration service (cortex_core/arbitrate.py) already excludes Prometheus for
# exactly this reason ("kappa=0 in the one real run; unproven"); dropping it here
# makes the whole codebase consistent: no non-functional arbiter in ANY gate.
# The native-template dispatch is retained in judge.py so Prometheus can still be
# run as a NON-gating auxiliary signal, and re-promoted to a gate only if a real
# run ever demonstrates kappa > 0.


def occurrence_floor_gate(n: int = 2):
    def g(ev: dict) -> GateResult:
        c = int(ev.get("occurrence_count", 0))
        return GateResult(f"occurrence_floor_{n}", c >= n, f"count={c}")
    return g


def single_source_gate(ev: dict) -> GateResult:
    return GateResult("single_source_ok", bool(ev.get("author_model")),
                      f"author={ev.get('author_model')!r}")


def not_public_contaminated_gate(ev: dict) -> GateResult:
    """Benchmark-contamination gate for the TRAINABLE tiers (gap J6). A candidate whose INPUT
    hash matches a known-public benchmark sha is REFUSED trainable gold -- dedup vs an
    INDEPENDENT public-sha manifest (`evals/contamination/known_public_manifest.jsonl`), never
    vs the oracle's own verdicts (the anti-circular property). Absent an `input`/`input_hash`
    in the evidence there is nothing to check, so the gate passes: it can only ever refuse a
    positive public-collision, never fabricate contamination. The manifest is lazily imported
    (no load-time cortex_core -> evals dependency); if the module is unavailable the gate
    passes rather than blocking legitimate promotions."""
    try:
        from evals.contamination import not_public_contaminated_gate as _gate
    except Exception as exc:  # pragma: no cover - defensive: never block on an import failure
        return GateResult("not_public_contaminated", True, f"contamination module unavailable: {exc}")
    r = _gate(ev, manifest_path=ev.get("contamination_manifest_path"))
    return GateResult(r.name, r.passed, r.detail)


# --------------------------------------------------------------------------- tier requirements
# The two TRAINABLE tiers additionally carry the J6 contamination gate: a row whose input
# collides with a known-public benchmark sha can never become trainable gold (it would teach
# the test). The weak/non-trainable tiers are exempt (they are never used for training).
TIER_REQUIREMENTS = {
    "hard_gold": [objective_checker_gate, not_public_contaminated_gate],
    "cross_vendor_synthetic_gold": [min_families_gate(3), no_flags_gate, not_public_contaminated_gate],
    "weak_candidate_exemplar": [single_source_gate],
}


def decide(item_id: str, evidence: dict, target_tier: str) -> PromotionDecision:
    """Evaluate one candidate against a specific tier's gates."""
    gates = TIER_REQUIREMENTS.get(target_tier)
    if gates is None:
        return PromotionDecision(item_id, State.QUARANTINED, "quarantine",
                                 reasons=[f"unknown tier {target_tier!r}"])
    results = [g(evidence) for g in gates]
    failed = [r for r in results if not r.passed]
    if failed:
        return PromotionDecision(item_id, State.QUARANTINED, "quarantine", results,
                                 reasons=[f"{r.name}: {r.detail}" for r in failed])
    return PromotionDecision(item_id, State.PROMOTED, target_tier, results)


def classify(item_id: str, evidence: dict) -> PromotionDecision:
    """Promote to the STRONGEST tier the evidence qualifies for; else quarantine."""
    for tier in TIER_ORDER[:-1]:                       # skip 'quarantine' itself
        d = decide(item_id, evidence, tier)
        if d.state == State.PROMOTED:
            return d
    return PromotionDecision(item_id, State.QUARANTINED, "quarantine",
                             reasons=["no tier's gates passed"])


def supersede(decision: PromotionDecision) -> PromotionDecision:
    """Move a promoted item to SUPERSEDED (never deleted) — corpus supersede-not-delete rule."""
    return PromotionDecision(decision.item_id, State.SUPERSEDED, decision.tier,
                             decision.gate_results, ["superseded"])


# --------------------------------------------------------------------------- attestation-aware tiering
def derive_tier(item_id: str, evidence: dict, *, verifier=None,
                nonce_store=None, now=None, verifier_kwargs: dict | None = None) -> PromotionDecision:
    """The authenticated provenance boundary (trusted-runner attestation layer).

    `classify` reads a caller-supplied dict and so is forgeable: a caller can name any tier.
    `derive_tier` closes that hole while preserving the owner's never-wait policy:

      * evidence carries a VALID server-signed attestation  -> classify() (trainable reachable)
      * evidence carries an INVALID attestation (forged / expired / replayed / wrong-issuer)
        -> QUARANTINE (a laundering attempt is never silently trusted OR downgraded to usable)
      * evidence carries NO attestation -> classify(), but any TRAINABLE result is relabeled to
        `non_human_verified`: usable NOW (never-wait) but capped below the trainable tiers.

    The attestation lives at `evidence["attestation"]` and is bound to the evidence content via
    `subject_sha` (sha256 of the evidence minus the attestation itself). `verifier` defaults to
    `cortex_core.attestation.verify_attestation`; inject a stub in tests.
    """
    att = (evidence or {}).get("attestation")
    if att is not None:
        if verifier is None:
            from cortex_core.attestation import verify_attestation as verifier
        subject_sha = _evidence_subject_sha(evidence)
        kw = dict(verifier_kwargs or {})
        kw.setdefault("expected_subject_sha", subject_sha)
        ok, reason = verifier(att, nonce_store=nonce_store, now=now, **kw)
        if not ok:
            return PromotionDecision(item_id, State.QUARANTINED, "quarantine",
                                     reasons=[f"attestation not verified: {reason}"])
        # Authenticated: trust the bound claim -> normal tiering, trainable reachable.
        return classify(item_id, evidence)

    # Unattested: usable now, but can never reach a trainable tier. The cap tier is the
    # canonical never-wait vocabulary constant (provenance_tiers is the source of truth);
    # lazy import avoids the promotion<->provenance_tiers module cycle.
    from . import provenance_tiers
    d = classify(item_id, evidence)
    if d.state == State.PROMOTED and d.tier in TRAINABLE:
        return PromotionDecision(
            item_id, State.PROMOTED, provenance_tiers.NON_HUMAN_VERIFIED, d.gate_results,
            reasons=[f"unattested: usable now (never-wait) but capped below trainable; "
                     f"attach a valid server attestation to reach {d.tier}"])
    return d


def _evidence_subject_sha(evidence: dict) -> str:
    """sha256 of the evidence with the attestation field removed, so an attestation binds the
    exact claim it travels with (defeats attaching a valid attestation to swapped evidence).

    Canonicalization is STRICT (sol@xhigh P1 #5): non-JSON-native values and non-string keys are
    rejected rather than coerced with `default=str`, so a custom object stringifying as "pass"
    can't collide with the literal, and `{1: ...}` can't alias `{"1": ...}`."""
    import hashlib
    import json
    subject = {k: v for k, v in (evidence or {}).items() if k != "attestation"}

    def _reject(o):
        raise TypeError(f"non-JSON-native value in evidence ({type(o).__name__}); "
                        "attested evidence must be strict JSON to bind unambiguously")

    def _assert_str_keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if not isinstance(k, str):  # int keys would alias to their string form
                    raise TypeError(f"non-string key {k!r} in attested evidence")
                _assert_str_keys(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                _assert_str_keys(v)

    _assert_str_keys(subject)
    return hashlib.sha256(
        json.dumps(subject, sort_keys=True, separators=(",", ":"),
                   allow_nan=False, default=_reject).encode("utf-8")).hexdigest()


def assert_vocabulary_in_sync() -> None:
    """Trust-cluster reconciliation guard (never-wait + keystone attestation, 2026-07-14).

    `provenance_tiers` is the single vocabulary source of truth. This module spells the
    `non_human_verified` floor as a literal (it cannot import provenance_tiers at module
    level -- provenance_tiers imports THIS module, so a module-level reference or an
    import-time call would hit a partially-initialized module). Call this from a TEST to
    assert the literal in TIER_ORDER stays exactly the canonical constant -- never at
    import time. Lazy import breaks the cycle."""
    from . import provenance_tiers
    assert provenance_tiers.NON_HUMAN_VERIFIED in TIER_ORDER, (
        "promotion.TIER_ORDER lost the canonical non_human_verified floor "
        f"({provenance_tiers.NON_HUMAN_VERIFIED!r}); never-wait/keystone vocabulary drift")
