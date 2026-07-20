"""GAP B1 — multi-model arbitration as a SHADOW / QUARANTINE-ONLY lane.

What this is
------------
A DAFE-pattern adjudicator (2 independent cross-family jurors + a *conditional*
third tiebreaker) for tasks that have **no deterministic oracle and no available
human**. It is the middle rung of Cortex's evidence hierarchy —
`external oracle > primary-source grounding > heterogeneous arbitration >
homogeneous debate > self-validation` — and it is deliberately the WEAKEST thing
we will act on, so its defaults are conservative.

What this is NOT (the hard guarantees these tests freeze)
---------------------------------------------------------
This lane **never mints trainable gold.** Every output is a hard-quarantined
`advisory_semi_gold` record that:
  * cannot train a model,
  * cannot be promoted to any gold tier,
  * cannot mutate server/state-engine state,
  * cannot authorize an action,
and the lane **defaults to ABSTAIN on disagreement.** The owner is a non-expert,
which — per the Codex verification (`docs/design/multi-model-arbitration-verify-
codex-2026-07-13.md`) — *increases* the need for abstention: model agreement is
not authoritative, so we abstain unless there is strong cross-vendor agreement,
and we escalate a confident contradiction to a human rather than split the
difference.

Design provenance (research-first, per CLAUDE.md)
-------------------------------------------------
  * `docs/design/multi-model-arbitration-in-cortex-2026-07-13.md` §4 (five-phase
    protocol) + §5 (abstain-first default).
  * `docs/design/multi-model-arbitration-critique-fable-2026-07-13.md` §3
    (DAFE 2-start + conditional third; exclude Prometheus; exclude any
    council->gold promotion; log changed-correct-to-incorrect from day one).
  * `docs/design/multi-model-arbitration-verify-codex-2026-07-13.md` §2/§3
    (hard `advisory_semi_gold` type; never trainable/promotable/action-
    authorizing; ABSTAIN unless decisive evidence; no Prometheus; no state
    transition; no gold/training path).

The cross_vendor_synthetic_gold minting path
--------------------------------------------
Codex flagged that live code (the calibration panel's cross-vendor gold writer)
STILL emits files whose names carry the cross-vendor gold tier token on >=3-family
agreement, and the promotion module still classifies that tier as trainable. That
path is a *different* mechanism (the calibration panel) and is untouched by this
module. This lane is entirely separate: it imports neither the panel writer nor
the promotion module, and its only sink is the arbitration quarantine dir. The
`test_module_source_never_references_gold_sinks` test enforces that separation
statically (the module never names those callables).

Anti-bloat: CLI-only (`cortex-arbitrate`). No new MCP tool.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

# NOTE: we import the judge dispatch + evaluator types, but deliberately NOT
# cortex_core.promotion and NOT ops.calibration_panel — this lane must not be able
# to reach any trainable-gold / promotion sink.
from .evaluator import AtomicClaim, EvaluatorGrade, Verdict


# ---------------------------------------------------------------------------
# Hard, frozen constants — the quarantine contract.
# ---------------------------------------------------------------------------

#: The ONLY record type this lane ever emits. Not a gold tier; not trainable.
RECORD_TYPE = "advisory_semi_gold"

#: Trainable-gold / promotion sinks this module must NEVER write to. Named here
#: only so tests can assert we stay away from them (and so a future reader knows
#: exactly what is off-limits). This lane touches none of these.
FORBIDDEN_GOLD_SINKS = frozenset({
    "cross_vendor_synthetic_gold",
    "hard_gold",
    "exemplar_seed",
    "golden",
    "promotion_decisions",
})

#: Confidence a juror must clear for its verdict to count toward strong agreement.
#: Conservative on purpose (owner is non-expert -> abstain-leaning). No corpus
#: decision pre-existed for this exact threshold; 0.7 mirrors the "confident
#: decisive verdict" bar used throughout the calibration write-ups. Tunable.
DEFAULT_MIN_CONFIDENCE = 0.7

#: Verdicts that are *directional and decisive* enough to be resolvable. The soft
#: verdicts (partially_supported / verifiable_but_flawed / unverifiable) are NOT
#: resolvable — they route to ABSTAIN, matching the abstain-first floor.
DECISIVE_VERDICTS = frozenset({
    Verdict.SUPPORTED,
    Verdict.STRONGLY_SUPPORTED,
    Verdict.UNSUPPORTED,
})

#: max_tokens floor honored for reasoning tiers (the recorded 12000 floor in
#: judge.MIN_MAX_TOKENS_BY_TIER — below it those models silently return content="").
DEFAULT_MAX_TOKENS = 12000

#: Tiers we consider as arbitration jurors. One entry per family for clean cross-
#: vendor diversity; mirrors ops.calibration_panel.PANEL_FAMILIES but excludes
#: Prometheus (kappa=0 in the one real run; unproven) per the Fable/Codex design.
JUROR_TIERS: list[tuple[str, str]] = [
    ("glm5.2", "zhipu"),
    ("9r-gpt-oss-120b", "openai"),
    ("9r-gemini-3.5-flash", "google"),
    ("9r-deepseek-3.2", "deepseek"),
    ("qwen35b", "qwen"),
    ("9r-sonnet-4.6", "anthropic"),  # excluded when the artifact is Anthropic-authored
]


class ArbitrationVerdict(str, Enum):
    """The ONLY terminal outcomes. There is no 'gold' / 'promote' outcome.

    RESOLVED_WITH_EVIDENCE is still advisory — it means the council agreed with
    evidence, not that the answer became ground truth.
    """

    RESOLVED_WITH_EVIDENCE = "resolved_with_evidence"
    ABSTAIN = "abstain"
    NEEDS_HUMAN_BINARY = "needs_human_binary"


@dataclass
class JurorOpinion:
    """One juror's blinded verdict on the claim. `tier`/`family` are audit-only;
    the arbiter is never told model identities."""

    tier: str
    family: str
    verdict: Verdict
    confidence: float
    reasoning: str
    gaps: list[str] = field(default_factory=list)

    @classmethod
    def from_grade(cls, tier: str, family: str, grade: EvaluatorGrade) -> "JurorOpinion":
        return cls(
            tier=tier,
            family=family,
            verdict=grade.verdict,
            confidence=float(grade.confidence),
            reasoning=grade.reasoning,
            gaps=list(grade.gaps or []),
        )

    def to_dict(self, *, blind: bool = False) -> dict[str, Any]:
        d = {
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "gaps": self.gaps,
        }
        if not blind:
            d["tier"] = self.tier
            d["family"] = self.family
        return d


# ---------------------------------------------------------------------------
# Pure decision logic (network-free; this is what the frozen tests hammer).
# ---------------------------------------------------------------------------

def strong_agreement(
    opinions: list[JurorOpinion], *, min_confidence: float = DEFAULT_MIN_CONFIDENCE
) -> bool:
    """True iff >=2 opinions from DISTINCT families share the SAME decisive verdict,
    each at or above `min_confidence`.

    Deliberately strict: same soft verdict, low confidence, or two of the same
    family do NOT count. This is the anti-sycophancy / disagree-by-default gate —
    weak or shallow 'agreement' falls through to ABSTAIN.
    """
    by_verdict: dict[Verdict, set[str]] = {}
    for op in opinions:
        if op.verdict in DECISIVE_VERDICTS and op.confidence >= min_confidence:
            by_verdict.setdefault(op.verdict, set()).add(op.family)
    return any(len(fams) >= 2 for fams in by_verdict.values())


def _confident_decisive_contradiction(
    opinions: list[JurorOpinion], *, min_confidence: float
) -> bool:
    """True iff two opinions confidently assert OPPOSING decisive verdicts
    (e.g. SUPPORTED vs UNSUPPORTED) — a real dispute a human must break."""
    confident = {
        op.verdict
        for op in opinions
        if op.verdict in DECISIVE_VERDICTS and op.confidence >= min_confidence
    }
    positive = {Verdict.SUPPORTED, Verdict.STRONGLY_SUPPORTED}
    return bool(confident & positive) and (Verdict.UNSUPPORTED in confident)


def decide(
    jurors: list[JurorOpinion],
    arbiter: Optional[JurorOpinion] = None,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> tuple[ArbitrationVerdict, str]:
    """Adjudicate. Returns (verdict, human-readable reason). Never mints gold.

    Rules (abstain-first):
      1. Two jurors strongly agree (same decisive verdict, distinct families,
         confident) -> RESOLVED_WITH_EVIDENCE. (DAFE: no third model needed.)
      2. No arbiter yet and jurors don't strongly agree -> ABSTAIN
         (the conditional third simply hasn't run; the floor is abstention).
      3. With an arbiter: if the arbiter forms a >=2 decisive, distinct-family,
         confident majority -> RESOLVED_WITH_EVIDENCE.
      4. Else, if there is a confident decisive contradiction the arbiter did NOT
         break -> NEEDS_HUMAN_BINARY.
      5. Else (weak / unverifiable / insufficient) -> ABSTAIN.
    """
    if not jurors:
        return ArbitrationVerdict.ABSTAIN, "no jurors ran"

    # 1. Jurors alone strongly agree.
    if strong_agreement(jurors, min_confidence=min_confidence):
        return (
            ArbitrationVerdict.RESOLVED_WITH_EVIDENCE,
            "two independent families agree on a confident, decisive verdict",
        )

    # 2. Conditional third has not run — abstain is the floor.
    if arbiter is None:
        return (
            ArbitrationVerdict.ABSTAIN,
            "jurors disagree and no arbiter ran; abstaining (the safe floor)",
        )

    all_opinions = [*jurors, arbiter]

    # 3. Arbiter breaks the tie into a real cross-family majority.
    if strong_agreement(all_opinions, min_confidence=min_confidence):
        return (
            ArbitrationVerdict.RESOLVED_WITH_EVIDENCE,
            "arbiter formed a confident, decisive, cross-family majority",
        )

    # 3.5. The arbiter itself could not verify (threw up its hands) -> ABSTAIN.
    # An explicitly-unverifiable tiebreaker means the evidence is too thin to
    # resolve; that is honest uncertainty, not a human-needed dispute.
    if arbiter.verdict is Verdict.UNVERIFIABLE:
        return (
            ArbitrationVerdict.ABSTAIN,
            "arbiter could not verify the dispute; evidence insufficient — abstaining",
        )

    # 4. Confident, decisive contradiction the (engaged) arbiter could not resolve.
    if _confident_decisive_contradiction(jurors, min_confidence=min_confidence):
        return (
            ArbitrationVerdict.NEEDS_HUMAN_BINARY,
            "jurors confidently contradict on a decisive verdict; arbiter did not "
            "form a majority — a human must break the tie",
        )

    # 5. Insufficient / weak / unverifiable evidence.
    return (
        ArbitrationVerdict.ABSTAIN,
        "no confident cross-family majority and no decisive contradiction; "
        "evidence is insufficient to resolve — abstaining",
    )


def changed_correct_to_incorrect(
    jurors: list[JurorOpinion],
    arbiter: Optional[JurorOpinion],
    reference_verdict: Optional[Verdict],
) -> Optional[bool]:
    """Sycophancy metric (the design demands it 'from day one'): did the arbiter
    round flip a juror that WAS correct to an incorrect final answer?

    Only computable when a reference (ground-truth) verdict is supplied — in the
    live no-oracle path there is none, so this returns None (honestly unknown),
    never a fabricated False.
    """
    if reference_verdict is None or arbiter is None:
        return None
    any_juror_correct = any(op.verdict == reference_verdict for op in jurors)
    arbiter_correct = arbiter.verdict == reference_verdict
    return bool(any_juror_correct and not arbiter_correct)


# ---------------------------------------------------------------------------
# Advisory record (the quarantined, non-gold output type).
# ---------------------------------------------------------------------------

@dataclass
class AdvisoryRecord:
    question: str
    task_type: str
    verdict: ArbitrationVerdict
    jurors: list[JurorOpinion]
    arbiter: Optional[JurorOpinion]
    reason: str
    abstention_reason: Optional[str]
    changed_correct_to_incorrect: Optional[bool] = None
    targeted_research_done: bool = False
    artifact_authored_by: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize. The trust flags are HARD-CODED false — an advisory record
        is structurally incapable of claiming gold/trainable status."""
        return {
            # --- provenance / quarantine contract (frozen) ---
            "record_type": RECORD_TYPE,        # "advisory_semi_gold"
            "is_gold": False,
            "trainable": False,
            "promotable": False,
            "can_mutate_state": False,
            "can_authorize_action": False,
            "quarantined": True,
            "note": (
                "SHADOW/QUARANTINE-ONLY advisory adjudication. NOT ground truth, "
                "NOT trainable, NOT promotable. Only an explicit human binary can "
                "turn this into state, training, or a permanent check."
            ),
            # --- the adjudication ---
            "timestamp": self.timestamp,
            "question": self.question,
            "task_type": self.task_type,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "abstention_reason": self.abstention_reason,
            "changed_correct_to_incorrect": self.changed_correct_to_incorrect,
            "targeted_research_done": self.targeted_research_done,
            "artifact_authored_by": self.artifact_authored_by,
            # jurors keep identities for audit; the arbiter was run blind at dispatch
            "jurors": [j.to_dict() for j in self.jurors],
            "arbiter": self.arbiter.to_dict() if self.arbiter else None,
        }


def quarantine_dir(workspace: str | Path) -> Path:
    """The ONE and ONLY sink for this lane: <workspace>/arbitration/quarantine/.

    Note the name: 'quarantine', under 'arbitration' — deliberately NOT under
    'calibration/results' (where the panel mints cross_vendor_synthetic_gold) and
    deliberately NOT under 'evals/promotion_decisions'.
    """
    return Path(workspace) / "arbitration" / "quarantine"


def write_advisory(record: AdvisoryRecord, workspace: str | Path) -> Path:
    """Append the advisory record to the quarantine ledger. Returns the file path.

    Guard: refuses to write anything whose serialized record_type is not the
    frozen advisory type, or that is flagged gold/trainable — belt-and-suspenders
    against a future refactor accidentally routing gold through here.
    """
    d = record.to_dict()
    if d["record_type"] != RECORD_TYPE or d["is_gold"] or d["trainable"]:
        raise RuntimeError(
            "refusing to write: arbitration lane may only persist non-gold, "
            "non-trainable advisory_semi_gold records"
        )
    qdir = quarantine_dir(workspace)
    qdir.mkdir(parents=True, exist_ok=True)
    out = qdir / f"advisory-{record.timestamp}.jsonl"
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return out


# ---------------------------------------------------------------------------
# Juror selection (cross-family, anti-circular, no Prometheus).
# ---------------------------------------------------------------------------

def pick_juror_tiers(
    n: int = 2, exclude_families: Optional[set[str]] = None
) -> list[tuple[str, str]]:
    """Pick `n` juror (tier, family) pairs, one per family, skipping excluded
    families. Prometheus is never eligible (not in JUROR_TIERS)."""
    exclude = exclude_families or set()
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tier, fam in JUROR_TIERS:
        if fam in exclude or fam in seen or "prometheus" in tier:
            continue
        picked.append((tier, fam))
        seen.add(fam)
        if len(picked) >= n:
            break
    if len(picked) < n:
        raise RuntimeError(
            f"could not select {n} cross-family jurors excluding {sorted(exclude)}; "
            f"only {len(picked)} available"
        )
    return picked


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------

def _default_judge_fn(claim: AtomicClaim, evidence: list[dict], tier: str, **kw):
    """Real dispatch via judge.llm_judge, honoring the per-tier max_tokens floor."""
    from . import judge as J

    max_tokens = J.apply_min_max_tokens(tier, kw.pop("max_tokens", DEFAULT_MAX_TOKENS))
    return J.llm_judge(claim, evidence, tier=tier, max_tokens=max_tokens, **kw)


def arbitrate(
    question: str,
    task_type: str,
    evidence: list[dict[str, Any]],
    *,
    workspace: str | Path,
    judge_fn: Optional[Callable[..., EvaluatorGrade]] = None,
    research_fn: Optional[Callable[[str, list[dict]], list[dict]]] = None,
    artifact_authored_by: Optional[str] = None,
    reference_verdict: Optional[Verdict] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    persist: bool = True,
) -> AdvisoryRecord:
    """Run the DAFE arbitration protocol and return a quarantined advisory record.

    Phases (design §4, minimal build):
      1. Two blind cross-family jurors grade the claim+evidence.
      2. Strong agreement -> RESOLVED (stop; no third model). [DAFE cost win]
      3. Disagreement -> ONE targeted research round (if research_fn given) + ONE
         different-family arbiter, run blind (no model identities). It may only
         resolve a confident majority or fall to ABSTAIN/NEEDS_HUMAN_BINARY.

    `judge_fn`/`research_fn` are injection seams so the whole flow is testable
    offline. Output is ALWAYS advisory_semi_gold — never gold, never trainable.
    """
    judge_fn = judge_fn or _default_judge_fn
    claim = AtomicClaim(claim_id="arb", task_type=task_type, description=question)

    exclude = {artifact_authored_by} if artifact_authored_by else set()
    juror_tiers = pick_juror_tiers(n=2, exclude_families=exclude)

    jurors: list[JurorOpinion] = []
    for tier, fam in juror_tiers:
        grade = judge_fn(claim, evidence, tier=tier, workspace=workspace)
        jurors.append(JurorOpinion.from_grade(tier, fam, grade))

    arbiter: Optional[JurorOpinion] = None
    targeted_research_done = False
    used_evidence = evidence

    if not strong_agreement(jurors, min_confidence=min_confidence):
        # Phase 3: ONE targeted research round on the dispute (Tool-MAD: >1 hurts).
        if research_fn is not None:
            try:
                used_evidence = research_fn(question, evidence) or evidence
                targeted_research_done = True
            except Exception:  # noqa: BLE001 — research is best-effort enrichment
                used_evidence = evidence
        # Third arbiter from a family neither juror used (still cross-family, blind).
        used_families = {fam for _, fam in juror_tiers} | exclude
        arb_tiers = pick_juror_tiers(n=1, exclude_families=used_families)
        arb_tier, arb_fam = arb_tiers[0]
        arb_grade = judge_fn(claim, used_evidence, tier=arb_tier, workspace=workspace)
        arbiter = JurorOpinion.from_grade(arb_tier, arb_fam, arb_grade)

    verdict, reason = decide(jurors, arbiter=arbiter, min_confidence=min_confidence)
    abstention_reason = reason if verdict is ArbitrationVerdict.ABSTAIN else None
    cctoi = changed_correct_to_incorrect(jurors, arbiter, reference_verdict)

    record = AdvisoryRecord(
        question=question,
        task_type=task_type,
        verdict=verdict,
        jurors=jurors,
        arbiter=arbiter,
        reason=reason,
        abstention_reason=abstention_reason,
        changed_correct_to_incorrect=cctoi,
        targeted_research_done=targeted_research_done,
        artifact_authored_by=artifact_authored_by,
    )
    if persist:
        write_advisory(record, workspace)
    return record


# ---------------------------------------------------------------------------
# CLI (`cortex-arbitrate`) — no MCP tool (anti-bloat).
# ---------------------------------------------------------------------------

def _load_evidence(args) -> list[dict[str, Any]]:
    if args.evidence_file:
        raw = Path(args.evidence_file).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SystemExit("--evidence-file must contain a JSON array of evidence items")
        return data
    if args.evidence_json:
        data = json.loads(args.evidence_json)
        if not isinstance(data, list):
            raise SystemExit("--evidence-json must be a JSON array of evidence items")
        return data
    return []


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="cortex-arbitrate",
        description=(
            "Multi-model arbitration (SHADOW/QUARANTINE-ONLY). Two blind cross-"
            "family jurors + a conditional third tiebreaker. Defaults to ABSTAIN "
            "on disagreement. Output is advisory_semi_gold — NEVER trainable gold, "
            "never a state transition, never an action authorization."
        ),
    )
    p.add_argument("--question", required=True, help="the claim/question to adjudicate")
    p.add_argument("--task-type", default="research", help="task type (research, feature, ...)")
    p.add_argument("--evidence-file", help="path to a JSON array of {type,ref,detail} items")
    p.add_argument("--evidence-json", help="inline JSON array of evidence items")
    p.add_argument(
        "--authored-by",
        help="vendor family that authored the artifact (excluded as a juror; "
        "anti-circular rule, e.g. 'anthropic')",
    )
    p.add_argument("--workspace", default=".", help="workspace root (quarantine written here)")
    p.add_argument(
        "--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
        help=f"strong-agreement confidence floor (default {DEFAULT_MIN_CONFIDENCE})",
    )
    p.add_argument(
        "--no-persist", action="store_true",
        help="do not write the advisory record to the quarantine ledger",
    )
    args = p.parse_args(argv)

    evidence = _load_evidence(args)
    try:
        record = arbitrate(
            question=args.question,
            task_type=args.task_type,
            evidence=evidence,
            workspace=args.workspace,
            artifact_authored_by=args.authored_by,
            min_confidence=args.min_confidence,
            persist=not args.no_persist,
        )
    except Exception as exc:  # noqa: BLE001 — surface config/network errors honestly
        print(f"arbitration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    out = record.to_dict()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    banner = {
        ArbitrationVerdict.RESOLVED_WITH_EVIDENCE: "RESOLVED (advisory only — NOT gold)",
        ArbitrationVerdict.ABSTAIN: "ABSTAIN (the safe floor)",
        ArbitrationVerdict.NEEDS_HUMAN_BINARY: "NEEDS HUMAN BINARY",
    }[record.verdict]
    print(f"\n=> {banner}", file=sys.stderr)
    if not args.no_persist:
        print(f"   quarantined at: {quarantine_dir(args.workspace)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
