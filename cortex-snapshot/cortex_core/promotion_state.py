"""Explicit promotion LIFECYCLE state machine (gap J3).

`cortex_core/promotion.py` already names the shared *trust-tier* model (hard_gold /
cross_vendor_synthetic_gold / weak_candidate_exemplar / non_human_verified / quarantine)
and the pure-predicate GATES that decide a tier. What was still *ad-hoc* is the **ordered
lifecycle** an artifact walks as evidence accumulates -- observed -> ... -> trainable_gold
-- and the fact that each STEP has its own required evidence. J3 makes that walk a real,
testable state machine:

    observed -> kedb -> provisional -> calibrated -> oracle_backed -> hard_gold
    -> trainable_gold        (+ deprecated, reachable from any state: supersede-not-delete)

Each forward edge carries a required-evidence gate (`validate_transition`). Two edges are
load-bearing anti-circular guards:

  * oracle_backed -> hard_gold  requires an INDEPENDENT SECOND AUTHORITY -- a second,
    *distinct-instrument* checker that is a DIFFERENT FAMILY or an OBJECTIVE checker, and
    that AGREES. A single instrument can never self-promote its own output to hard gold
    (GAP-CLOSURE-PLAN J3 anti-circular guard; the same law as J1 mutation-by-construction
    and J5 order-reversal -- ground truth must be independent of the instrument).
  * hard_gold -> trainable_gold  enforces the anti-distillation rule: the producer must be
    non-Anthropic / non-proprietary (reuses `distill._PROPRIETARY_BLOCKLIST`, the single
    source of truth: docs/COMPLIANCE-ANTI-DISTILLATION.md) and the record must carry a
    server-signed attestation (the registry trainable chokepoint, registry.py).

## Reconciliation with prior art (cited)
* `docs/research/DESIGN-tiered-lifecycle-pipeline-2026-07-06.md` + its review
  (`reviewed/DESIGN-tiered-pipeline-review-2026-07-06.md`) design a *task-execution*
  pipeline (a 0-7 EXECUTE state machine, per-stage model/effort routing). That is a
  DIFFERENT AXIS from this artifact-*trust* lifecycle; they compose (a pipeline produces
  artifacts that then walk this lifecycle) and do not conflict. The review's central rule
  -- "the trust anchor is the server-controlled deterministic checker, never a self-report"
  -- is exactly what the oracle_backed/hard_gold edges encode here.
* `promotion.py` mints the *tier* name `hard_gold` from a SINGLE objective checker. Under
  the stricter J3 lifecycle that single-checker state is `oracle_backed`; lifecycle
  `hard_gold` is reserved for oracle_backed PLUS an independent second authority. So
  `lifecycle_state_for_tier("hard_gold") == ORACLE_BACKED` (intentional -- see below): no
  single-instrument `promotion.decide()` call can ever yield lifecycle hard_gold. The
  `cross_vendor_synthetic_gold` tier (>=3 independent judge families agreeing) already
  satisfies the second-authority principle by construction, so it maps to lifecycle
  `hard_gold`.

Stdlib only. Pure functions over an evidence dict, same shape as promotion.py's gates.
"""

from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    OBSERVED = "observed"            # raw signal captured (INTAKE)
    KEDB = "kedb"                    # recorded as a known-error/pattern candidate (occ. floor >=2)
    PROVISIONAL = "provisional"      # single-source authored; usable but unverified
    CALIBRATED = "calibrated"        # a judge instrument measured kappa >= floor vs independent gold
    ORACLE_BACKED = "oracle_backed"  # a deterministic checker (never a judge) decided pass
    HARD_GOLD = "hard_gold"          # oracle_backed + an INDEPENDENT SECOND AUTHORITY agrees
    TRAINABLE_GOLD = "trainable_gold"  # hard_gold + non-proprietary producer + attestation
    DEPRECATED = "deprecated"        # retired (supersede-not-delete); reachable from any state


# The forward progression (deprecated is off-track -- reachable from any state).
LIFECYCLE_ORDER = (
    LifecycleState.OBSERVED, LifecycleState.KEDB, LifecycleState.PROVISIONAL,
    LifecycleState.CALIBRATED, LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD,
    LifecycleState.TRAINABLE_GOLD,
)

# Calibration floor: Cohen's kappa >= 0.6 == "substantial agreement" (Landis-Koch), the
# de-facto trust line already used in cortex_core/judge.py (qwen 0.604 = "calibrated
# bulk-screen lane"; 0.459 / 0.405 / 0.227 judges are NOT trusted / "never a judge").
CALIBRATION_KAPPA_FLOOR = 0.6


def _as_state(x) -> LifecycleState | None:
    if isinstance(x, LifecycleState):
        return x
    try:
        return LifecycleState(str(x))
    except ValueError:
        return None


# --------------------------------------------------------------------------- per-edge evidence gates
# Each returns (ok: bool, reason: str). Pure predicates over the evidence dict.

def _gate_kedb(ev: dict) -> tuple[bool, str]:
    c = int(ev.get("occurrence_count", 0))
    recipe = ev.get("detection_recipe")
    if c < 2:
        return False, f"kedb needs occurrence_count>=2 (KEDB floor), got {c}"
    if not recipe:
        return False, "kedb needs a detection_recipe (a pattern with no recipe is not reproducible)"
    return True, "occurrence floor + detection recipe present"


def _gate_provisional(ev: dict) -> tuple[bool, str]:
    if not ev.get("author_model"):
        return False, "provisional needs author_model (provenance) -- who authored the candidate"
    return True, f"single-source authored by {ev.get('author_model')!r}"


def _gate_calibrated(ev: dict) -> tuple[bool, str]:
    k = ev.get("cohen_kappa")
    if k is None:
        return False, "calibrated needs a measured cohen_kappa vs an independent gold"
    if not ev.get("calibration_gold_source"):
        return False, ("calibrated needs calibration_gold_source (the gold must be named and "
                       "independent of the instrument -- J3 anti-circular rule)")
    if float(k) < CALIBRATION_KAPPA_FLOOR:
        return False, (f"cohen_kappa {k} below calibration floor {CALIBRATION_KAPPA_FLOOR} "
                       "(substantial-agreement line)")
    return True, f"kappa {k} >= {CALIBRATION_KAPPA_FLOOR} vs {ev.get('calibration_gold_source')!r}"


def _gate_oracle_backed(ev: dict) -> tuple[bool, str]:
    # Reuse promotion.py's objective_checker gate verbatim -- a deterministic checker, never
    # a judge, decided pass. (Single source of truth for "what counts as an objective verdict".)
    from cortex_core.promotion import objective_checker_gate
    r = objective_checker_gate(ev)
    if not r.passed:
        return False, f"oracle_backed needs an OBJECTIVE checker verdict (not a judge): {r.detail}"
    return True, f"objective checker decided pass ({r.detail})"


def _authority_id(a) -> str:
    return str(a.get("id", "")) if isinstance(a, dict) else str(a or "")


def _authority_family(a) -> str:
    return str(a.get("family", "")) if isinstance(a, dict) else ""


def _authority_is_objective(a) -> bool:
    return isinstance(a, dict) and str(a.get("kind", "")).lower() in ("objective", "checker")


def _gate_hard_gold(ev: dict) -> tuple[bool, str]:
    """THE anti-circular gate. oracle_backed -> hard_gold requires an INDEPENDENT SECOND
    AUTHORITY that agrees: a *distinct instrument* (different id) that is EITHER a different
    family OR an objective checker. A single instrument (or a same-family peer) can never
    self-promote its own output to hard gold (GAP-CLOSURE-PLAN J3)."""
    first = ev.get("first_authority") or ev.get("label_authority")
    second = ev.get("second_authority")
    if not second:
        return False, ("hard_gold requires a named independent second authority "
                       "(second_authority) -- a single instrument cannot self-promote to hard gold")
    # must agree
    agrees = second.get("agrees", second.get("verdict")) if isinstance(second, dict) else None
    if agrees not in (True, "pass", "agree", "agrees"):
        return False, "the named second authority does not agree (agrees/verdict != pass)"
    first_id, second_id = _authority_id(first), _authority_id(second)
    if not second_id:
        return False, "second_authority must name an id"
    if second_id == first_id:
        return False, ("second authority is the SAME instrument as the first "
                       f"({second_id!r}) -- self-promotion is forbidden; need a distinct authority")
    first_fam, second_fam = _authority_family(first), _authority_family(second)
    different_family = second_fam and second_fam != first_fam
    if not (different_family or _authority_is_objective(second)):
        return False, ("second authority is neither a different family nor an objective checker "
                       f"(family={second_fam!r}) -- not an independent authority")
    return True, f"independent second authority {second_id!r} (family={second_fam!r}) agrees"


def _gate_trainable_gold(ev: dict) -> tuple[bool, str]:
    """Anti-distillation + attestation. Producer must be non-Anthropic / non-proprietary
    (reuse distill._PROPRIETARY_BLOCKLIST -- single source of truth) and the record must
    carry a server-signed attestation (the registry trainable chokepoint)."""
    from cortex_core.distill import _PROPRIETARY_BLOCKLIST
    producer = str(ev.get("producer", "")).lower()
    if not producer:
        return False, "trainable_gold needs a named producer (provenance for the anti-distillation check)"
    hit = next((v for v in _PROPRIETARY_BLOCKLIST if v in producer), None)
    if hit:
        return False, (f"producer {producer!r} is proprietary/{hit!r} -- anti-distillation forbids "
                       "a proprietary (Anthropic/OpenAI/Google) model as a trainable producer")
    if not ev.get("attestation"):
        return False, "trainable_gold needs a server-signed attestation (the trainable chokepoint)"
    return True, f"open producer {producer!r} + attestation present"


_EDGE_GATES = {
    (LifecycleState.OBSERVED, LifecycleState.KEDB): _gate_kedb,
    (LifecycleState.KEDB, LifecycleState.PROVISIONAL): _gate_provisional,
    (LifecycleState.PROVISIONAL, LifecycleState.CALIBRATED): _gate_calibrated,
    (LifecycleState.CALIBRATED, LifecycleState.ORACLE_BACKED): _gate_oracle_backed,
    (LifecycleState.ORACLE_BACKED, LifecycleState.HARD_GOLD): _gate_hard_gold,
    (LifecycleState.HARD_GOLD, LifecycleState.TRAINABLE_GOLD): _gate_trainable_gold,
}


def _gate_deprecate(ev: dict) -> tuple[bool, str]:
    if ev.get("deprecation_reason") or ev.get("superseded_by"):
        return True, "deprecation reason recorded (supersede-not-delete)"
    return False, "deprecation needs a deprecation_reason or superseded_by (never a silent delete)"


def validate_transition(frm, to, evidence: dict | None = None) -> tuple[bool, str]:
    """Is the lifecycle transition frm -> to permitted by the evidence?

    Returns (ok, reason). Rules:
      * both states must be known (else unknown-state error);
      * DEPRECATED is reachable from ANY state (supersede-not-delete) given a reason;
      * otherwise `to` must be the IMMEDIATE forward successor of `frm` (no skipping,
        no backward), and the edge's required-evidence gate must pass.
    """
    ev = evidence or {}
    s_from, s_to = _as_state(frm), _as_state(to)
    if s_from is None:
        return False, f"unknown from-state {frm!r}"
    if s_to is None:
        return False, f"unknown to-state {to!r}"
    if s_from == s_to:
        return False, f"no-op transition ({s_from.value} -> {s_to.value})"

    if s_to == LifecycleState.DEPRECATED:
        return _gate_deprecate(ev)
    if s_from == LifecycleState.DEPRECATED:
        return False, "deprecated is terminal (supersede-not-delete); re-register a new artifact instead"

    # forward-adjacency check against LIFECYCLE_ORDER
    try:
        i_from = LIFECYCLE_ORDER.index(s_from)
        i_to = LIFECYCLE_ORDER.index(s_to)
    except ValueError:
        return False, f"{s_from.value} or {s_to.value} is off the forward track"
    if i_to <= i_from:
        return False, f"backward transition not allowed ({s_from.value} -> {s_to.value})"
    if i_to != i_from + 1:
        skipped = [s.value for s in LIFECYCLE_ORDER[i_from + 1:i_to]]
        return False, (f"cannot skip states {skipped} -- must advance to the adjacent "
                       f"{LIFECYCLE_ORDER[i_from + 1].value} first")

    gate = _EDGE_GATES[(s_from, s_to)]
    return gate(ev)


# --------------------------------------------------------------------------- promotion.py bridge
# Map a promotion.py trust TIER onto the lifecycle state it corresponds to. See the module
# docstring's reconciliation note: the single-checker `hard_gold` TIER is lifecycle
# ORACLE_BACKED (not lifecycle hard_gold -- that needs a second authority); the
# >=3-family `cross_vendor_synthetic_gold` tier satisfies the second-authority principle
# by construction and maps to lifecycle HARD_GOLD.
_TIER_TO_LIFECYCLE = {
    "weak_candidate_exemplar": LifecycleState.PROVISIONAL,
    "non_human_verified": LifecycleState.ORACLE_BACKED,
    "hard_gold": LifecycleState.ORACLE_BACKED,
    "cross_vendor_synthetic_gold": LifecycleState.HARD_GOLD,
    # 'quarantine' and unknown tiers -> None (not on the forward track).
}


def lifecycle_state_for_tier(tier: str) -> LifecycleState | None:
    """The lifecycle state a promotion.py trust tier corresponds to (None for quarantine)."""
    return _TIER_TO_LIFECYCLE.get(tier)
