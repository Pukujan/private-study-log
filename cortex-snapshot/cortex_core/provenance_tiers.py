"""CORE-DESIGN: the provenance-tier trust model — *never wait for a human*.

Owner policy (2026-07-14): the harness NEVER blocks work pending a human verifier.
It uses whatever oracle evidence exists NOW, labelled honestly by HOW it was
validated, and a human review is only an OPTIONAL later UPGRADE of the label —
never a gate. Everything below `quarantine` is USABLE immediately; the tier just
records how much to trust it, and every record stays retroactively down-weightable
by tier because the label is derived from evidence, not asserted.

This is the single canonical vocabulary the lab was missing. Before this,
`provenance_tier` carried ad-hoc values across evals/ (`hard_gold`,
`weak_candidate_exemplar`, `hard_gold_candidate`, `phase_c_label`, ...). Here they
collapse onto SIX tiers, ordered by confidence:

    human_verified      a human confirmed it (the OPTIONAL top upgrade; never required).
    hard_gold           a DETERMINISTIC CHECKER decided pass — never a judge
                        (same trust order as the evals/ objective lanes + promotion.py).
    synthetic_consensus >=3 independent judge FAMILIES agree, blinded, no bias/instability
                        flags, Prometheus not dissenting (== promotion.cross_vendor_synthetic_gold).
    advisory            an arbitration-council record (arbitrate.advisory_semi_gold):
                        usable as a SIGNAL, never as ground truth.
    non_human_verified  minted/authored now, not yet reviewed. USABLE immediately.
                        The default for honest, unverified work — the anti-block tier.
    quarantine          explicitly undecidable/unsafe (e.g. self_learning UNVERIFIABLE,
                        a gate-instability flag). The ONLY non-usable tier.

Two invariants, enforced by `derive_tier` (not by trust):
  1. USABILITY:  everything except `quarantine` is usable NOW. A `non_human_verified`
     record is NOT blocked — that is the whole point of the policy.
  2. LABEL <= EVIDENCE (no label-masquerade): a record cannot *claim* a higher tier than
     its evidence earns. `derive_tier` recomputes the strongest JUSTIFIED tier from the
     evidence (reusing promotion.py's objective-checker / consensus gates); `stamp`
     DOWNGRADES an over-claim and records it. So a label can never outrun its evidence.

SECURITY BOUNDARY (honest limit, per the 2026-07-14 sol@xhigh red-team,
`reviewed/provenance-tiers-sol-redteam-2026-07-14.md`): invariant #2 binds the LABEL to
the EVIDENCE — it does NOT prove the evidence is real. `evidence` is CALLER-POPULATED, so
a caller willing to fabricate `{checker_decided:true, objective_verdict:"pass"}` can still
mint `hard_gold`. Sealing that needs a trusted-runner ATTESTATION layer (an executor the
agent doesn't control, issuing issuer-bound signed evidence) — the top debt item, not
built here. Mitigations that ARE here: veto-first quarantine, `human_verified` reachable
only via `upgrade_to_human_verified` (real-reviewer guard), an `is_authoritative` boundary
so unreviewed data can't define tests/rubrics/promotion evidence, and `verify_stamp` for
ingestion/training-export to re-derive rather than trust a stored label. Treat
caller-derived gold as trustworthy only to the degree its evidence ISSUER is trusted.

TRAINABLE (may enter a training corpus) stays exactly the lab policy: the deterministic
+ consensus + human-confirmed tiers only. `advisory` and `non_human_verified` are USABLE
but NOT trainable — used as signal/context now, promotable later if a checker or a human
upgrades them. Stdlib only; offline; no judge/LLM/network in this module.
"""
from __future__ import annotations

from typing import Any, Optional

from . import promotion

# --------------------------------------------------------------------------- vocabulary
HUMAN_VERIFIED = "human_verified"
HARD_GOLD = "hard_gold"
SYNTHETIC_CONSENSUS = "synthetic_consensus"
ADVISORY = "advisory"
NON_HUMAN_VERIFIED = "non_human_verified"
QUARANTINE = "quarantine"

# strongest -> weakest confidence. Index == rank (lower is stronger).
TIER_ORDER: tuple[str, ...] = (
    HUMAN_VERIFIED,
    HARD_GOLD,
    SYNTHETIC_CONSENSUS,
    ADVISORY,
    NON_HUMAN_VERIFIED,
    QUARANTINE,
)
_RANK = {t: i for i, t in enumerate(TIER_ORDER)}

#: May enter a training corpus. Deterministic + cross-vendor-consensus + human-confirmed
#: only (lab policy; owner: synthetic_consensus is trainable WITHOUT a human pass).
TRAINABLE: frozenset[str] = frozenset({HARD_GOLD, SYNTHETIC_CONSENSUS, HUMAN_VERIFIED})

#: Usable NOW (as gold, signal, or context). Everything except quarantine — the
#: enforcement of "never wait for a human": non_human_verified is in here.
USABLE: frozenset[str] = frozenset(TIER_ORDER) - {QUARANTINE}

#: AUTHORITATIVE = may DEFINE ground truth for others: tests, rubrics, promotion
#: evidence, or instructions. Only the deterministic/consensus/human-confirmed tiers
#: qualify. `non_human_verified` and `advisory` are USABLE as context/signal but must
#: NOT define authority — the anti-laundering boundary (sol@xhigh #1): unreviewed data
#: can inform, but cannot become the yardstick that promotes other data to gold.
AUTHORITATIVE: frozenset[str] = frozenset({HARD_GOLD, SYNTHETIC_CONSENSUS, HUMAN_VERIFIED})

#: The field name every record carries. Aligns with the 40+ existing `provenance_tier`
#: uses across evals/; `provenance` is accepted as a read alias.
FIELD = "provenance_tier"

# Legacy / lane-specific label -> canonical tier. Anything a checker hasn't actually
# decided is only a *candidate*, so it lands at non_human_verified (usable now,
# upgradeable), NEVER at hard_gold on the strength of its name alone.
_LEGACY = {
    "hard_gold": HARD_GOLD,
    "cross_vendor_synthetic_gold": SYNTHETIC_CONSENSUS,
    "synthetic_consensus": SYNTHETIC_CONSENSUS,
    "advisory_semi_gold": ADVISORY,
    "advisory": ADVISORY,
    "weak_candidate_exemplar": NON_HUMAN_VERIFIED,
    "hard_gold_candidate": NON_HUMAN_VERIFIED,
    "ai_discovered": NON_HUMAN_VERIFIED,
    "human_verified": HUMAN_VERIFIED,
    "quarantine": QUARANTINE,
    "unverifiable": QUARANTINE,
}


# --------------------------------------------------------------------------- policy predicates
def is_valid_tier(tier: Any) -> bool:
    return tier in _RANK


def is_usable(record_or_tier: Any) -> bool:
    """The anti-block check: is this record usable NOW? True for every tier except
    quarantine — including `non_human_verified`. A human review never gates use."""
    return _tier_of(record_or_tier) in USABLE


def is_trainable(record_or_tier: Any) -> bool:
    """Convenience read of the STORED tier. NOTE (sol@xhigh #3): this trusts the label.
    Any training-export or registry ingestion must re-derive from evidence via
    `verify_stamp` and reject an over-claim — do not export gold on a stored label alone."""
    return _tier_of(record_or_tier) in TRAINABLE


def is_authoritative(record_or_tier: Any) -> bool:
    """May this record DEFINE ground truth for others (a test, rubric, promotion evidence,
    or instruction)? Only AUTHORITATIVE tiers. The anti-laundering guard: a
    `non_human_verified`/`advisory` record is usable as context but must not become a
    yardstick that certifies other records."""
    return _tier_of(record_or_tier) in AUTHORITATIVE


def confidence_rank(record_or_tier: Any) -> int:
    """Lower == stronger. An unknown/absent tier ranks below quarantine."""
    return _RANK.get(_tier_of(record_or_tier), len(TIER_ORDER))


def normalize(tier: Any) -> str:
    """Map any legacy/lane label to a canonical tier. Unknown -> non_human_verified
    (usable now; the honest default is 'use it, just don't call it gold')."""
    if tier in _RANK:
        return str(tier)
    return _LEGACY.get(str(tier).strip().lower(), NON_HUMAN_VERIFIED)


def _tier_of(record_or_tier: Any) -> str:
    if isinstance(record_or_tier, dict):
        raw = record_or_tier.get(FIELD, record_or_tier.get("provenance"))
        return normalize(raw) if raw is not None else NON_HUMAN_VERIFIED
    return normalize(record_or_tier)


# --------------------------------------------------------------------------- enforcement core
def derive_tier(evidence: Optional[dict] = None) -> str:
    """The strongest tier the EVIDENCE actually earns — recomputed, never trusted.

    Precedence: an explicit unverifiable/undecidable/quarantine signal VETOes FIRST
    (fail-safe: contradictory evidence — a checker "pass" alongside an undecidable flag —
    resolves to `quarantine`, never gold; sol@xhigh #5). Then an objective checker verdict
    -> hard_gold; >=3-family blinded consensus (no flags, Prometheus present & not
    dissenting) -> synthetic_consensus; an advisory-council record -> advisory; otherwise
    the honest default -> non_human_verified (usable now, unreviewed).

    `human_verified` is deliberately NOT derivable here: a caller-supplied
    `human_verified:true` boolean is not proof a human reviewed anything (sol@xhigh #4).
    That tier is reachable ONLY through `upgrade_to_human_verified`.

    SECURITY BOUNDARY (sol@xhigh #2/#6): `evidence` is CALLER-POPULATED. `derive_tier`
    only prevents a *label* from exceeding its *evidence*; it does NOT prove the evidence
    is real. A caller willing to fabricate `{checker_decided:true, objective_verdict:"pass"}`
    can still mint `hard_gold`. Closing that requires a trusted-runner attestation layer
    (issuer-bound signatures) — recorded as the top debt item, not built here. So a
    caller-derived gold tier is trustworthy only to the degree its evidence ISSUER is.
    """
    ev = evidence or {}

    # 0. VETO FIRST: an explicit unsafe/undecidable signal quarantines before any gold
    # check, so it can never be overridden by a co-present (possibly contradictory) pass.
    if ev.get("unverifiable") is True or ev.get("undecidable") is True:
        return QUARANTINE
    if normalize(ev.get("label")) == QUARANTINE:
        return QUARANTINE

    # 1. hard_gold: promotion.py's objective-checker gate is the single source of truth
    # for "a deterministic checker decided pass" (never a judge).
    if promotion.objective_checker_gate(ev).passed:
        return HARD_GOLD

    # 2. synthetic_consensus: exactly promotion.cross_vendor_synthetic_gold's gate stack.
    if all(g(ev).passed for g in promotion.TIER_REQUIREMENTS["cross_vendor_synthetic_gold"]):
        return SYNTHETIC_CONSENSUS

    if ev.get("advisory") is True or normalize(ev.get("record_type")) == ADVISORY:
        return ADVISORY

    # 3. Absence of gold evidence is NOT quarantine — it is the honest usable default.
    return NON_HUMAN_VERIFIED


def stamp(
    record: dict,
    evidence: Optional[dict] = None,
    claimed_tier: Optional[str] = None,
) -> dict:
    """Stamp `record[provenance_tier]` (mutates + returns the record).

    The tier is the strongest the evidence earns (`derive_tier`). If `claimed_tier`
    is stronger than the evidence supports, it is DOWNGRADED to the earned tier and a
    `provenance_downgraded` breadcrumb is recorded — a low-tier record can never
    masquerade as gold. If no evidence is given, the claim is honoured only up to
    `non_human_verified` (you cannot mint gold with no evidence at all)."""
    earned = derive_tier(evidence)
    if claimed_tier is not None:
        claimed = normalize(claimed_tier)
        if confidence_rank(claimed) < confidence_rank(earned):
            record[FIELD] = earned
            record["provenance_downgraded"] = {
                "claimed": claimed,
                "granted": earned,
                "reason": "evidence does not earn the claimed tier (no-masquerade rule)",
            }
        else:
            record[FIELD] = claimed
    else:
        record[FIELD] = earned
    return record


def for_promotion_tier(promotion_tier: str) -> str:
    """Canonical provenance for a `promotion.py` decision tier — so every promotion
    decision carries a provenance stamp consistent with this vocabulary."""
    return normalize(promotion_tier)


_PLACEHOLDER_REVIEWERS = frozenset({"", "human", "agent", "auto", "unknown", "none", "system"})


def upgrade_to_human_verified(record: dict, reviewer: str) -> dict:
    """The OPTIONAL human upgrade — the ONLY path to `human_verified`. Never required for
    a record to be used; it only raises confidence after the fact. Auditable: records who
    upgraded and from which tier.

    `reviewer` must be a REAL identity — a missing/placeholder value (`human`, `agent`,
    `auto`, ...) is rejected, so an agent can't silently self-certify with the default
    (sol@xhigh #4). This is a guard, NOT authentication: full enforcement needs an
    authenticated reviewer capability + a signed review receipt bound to the record hash
    (documented debt). Do not treat `human_verified` as tamper-proof until that exists."""
    ident = str(reviewer).strip().lower()
    if ident in _PLACEHOLDER_REVIEWERS:
        raise ValueError(
            f"upgrade_to_human_verified needs a real reviewer identity, got {reviewer!r}. "
            "human_verified is a human confirmation — it cannot be self-certified by a "
            "placeholder/agent identity."
        )
    record["provenance_prior_tier"] = _tier_of(record)
    record[FIELD] = HUMAN_VERIFIED
    record["human_verified_by"] = str(reviewer).strip()
    return record


def verify_stamp(record: dict, evidence: Optional[dict] = None) -> tuple[bool, str]:
    """Re-derive the tier from `evidence` and confirm the record's STORED tier does not
    exceed what the evidence earns — the enforcement `is_trainable`/ingestion must run
    before trusting a stored gold label (sol@xhigh #3). `human_verified` is exempt only
    when a real `human_verified_by` identity is present (its authority comes from the
    review receipt, not from `evidence`). Returns (ok, reason)."""
    stored = _tier_of(record)
    if stored == HUMAN_VERIFIED:
        by = str(record.get("human_verified_by", "")).strip().lower()
        if by and by not in _PLACEHOLDER_REVIEWERS:
            return True, "human_verified with a real reviewer identity"
        return False, "human_verified without a real reviewer identity"
    earned = derive_tier(evidence)
    if confidence_rank(stored) < confidence_rank(earned):
        return False, f"stored tier {stored!r} exceeds evidence-earned {earned!r} (masquerade)"
    return True, f"stored tier {stored!r} is within evidence-earned {earned!r}"


def usable_records(records: list[dict]) -> list[dict]:
    """Filter to records usable NOW — i.e. everything except quarantine. The point:
    `non_human_verified` records are RETAINED, not filtered out waiting on a human."""
    return [r for r in records if is_usable(r)]
