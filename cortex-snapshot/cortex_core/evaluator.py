"""Phase 4.4 evaluator: grades atomic claims against evidence without the actor's
prose (MARCH asymmetry). Uses a rubric-based approach: the actor claims what
they did, the evaluator independently scores whether the evidence supports it,
ignoring self-report, to compute the self_report_vs_verified_gap metric.

This module implements the mechanism. The decision on LLM-judge cost/model is
deferred (Phase 4.4 gate); this version uses rule-based scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .audit import validate_evidence

# Task types split by how strictly evidence must relate to the claim.
# SUBSTANTIVE types make a concrete claim ("fixed X", "added feature Y"), so
# evidence must be semantically relevant or it's evidence-theater. LENIENT types
# mirror contract.py's explore/chore exemption: trivial or exploratory work
# whose evidence needn't lexically echo the task. (Keep in sync with
# contract.TASK_TYPES.)
_SUBSTANTIVE_TYPES = frozenset({"bugfix", "test", "feature", "refactor", "research", "docs"})
_LENIENT_TYPES = frozenset({"chore", "explore"})

_STOPWORDS = frozenset(
    {"the", "a", "an", "and", "or", "is", "was", "to", "for", "of", "in", "on", "at"}
)

# --- Gap J4: closeout = *index* of evidence, not evidence --------------------
# A closeout's structured evidence is only trustworthy if each checkable claim
# links to a MECHANICALLY RECORDED artifact -- an exit code from a real run, a
# file content sha256, a git diff ref, an objective-oracle output id, or a trace
# span id. Prose, a `detail` string, or an LLM's verdict are NOT mechanical: the
# closeout narration must never over-state past what the trace mechanically
# shows (the anti-circular property). There is deliberately NO LLM in this path.
#
# Versioned + backward compatible: the requirement is enforced only at closeout
# `schema_version >= EVIDENCE_REF_SCHEMA_VERSION`. Legacy closeouts (older or
# absent version) grade exactly as before, so the historical audit log is never
# retroactively failed; the requirement applies going forward.
EVIDENCE_REF_SCHEMA_VERSION = 4

# The mechanical ref kinds an `evidence_ref` may attest, in the order checked.
# Each names an artifact a machine RECORDED, not one a model ASSERTED.
_MECHANICAL_REF_KINDS = ("exit_code", "sha256", "git_diff", "oracle_id", "span_id")

# Evidence item `type`s whose claim is mechanically checkable -- so at v4 each
# such item must carry a mechanical ref. (Everything the rubric grades is here;
# there is no non-checkable evidence type today, but keeping the set explicit
# lets a future advisory/context type opt out.)
_CHECKABLE_EVIDENCE_TYPES = frozenset({"test", "command", "eval", "file"})


def mechanical_ref_kind(item: dict[str, Any]) -> str | None:
    """Return the kind of mechanically-recorded artifact backing this evidence
    item, or ``None`` if its only backing is prose / an assertion.

    Accepted (mechanical): an ``exit_code`` from a real run, a file content
    ``sha256``, a ``git_diff`` ref, an objective-oracle ``oracle_id``, or a
    trace ``span_id`` -- whether supplied as top-level fields (as
    ``audit.test_evidence`` already does for ``exit_code``) or under an explicit
    ``evidence_ref`` dict.

    NOT accepted: a ``detail`` string, any other prose, or an ``evidence_ref``
    that carries only non-mechanical keys (e.g. ``llm_verdict``/``model``) -- an
    LLM's say-so is not a mechanical artifact. A bare-string ``evidence_ref`` is
    accepted as an opaque id (span) ONLY when it is a single whitespace-free
    token, so a prose sentence can never masquerade as a span id.
    """
    ref = item.get("evidence_ref")
    if isinstance(ref, dict):
        for key in _MECHANICAL_REF_KINDS:
            if str(ref.get(key, "")).strip():
                return key
    elif isinstance(ref, str):
        token = ref.strip()
        if token and not any(ch.isspace() for ch in token):
            return "span_id"
    # Top-level mechanical fields already emitted by helpers / objective lanes.
    for key in _MECHANICAL_REF_KINDS:
        if str(item.get(key, "")).strip():
            return key
    return None


def unmechanized_evidence(
    evidence_list: list[dict[str, Any]], schema_version: int | None
) -> list[dict[str, Any]]:
    """Evidence items that make a checkable claim but carry no mechanical ref.

    Enforced only at ``schema_version >= EVIDENCE_REF_SCHEMA_VERSION``; a legacy
    (lower or ``None``) version returns ``[]`` (backward compatible)."""
    if schema_version is None or schema_version < EVIDENCE_REF_SCHEMA_VERSION:
        return []
    return [
        item
        for item in (evidence_list or [])
        if item.get("type") in _CHECKABLE_EVIDENCE_TYPES
        and mechanical_ref_kind(item) is None
    ]


class Verdict(str, Enum):
    """Evaluator grade: how well evidence supports the claim."""

    SUPPORTED = "supported"
    STRONGLY_SUPPORTED = "strongly_supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    VERIFIABLE_BUT_FLAWED = "verifiable_but_flawed"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"  # claim is too vague or evidence is missing


@dataclass
class AtomicClaim:
    """A single claim from a closeout to be graded (MARCH asymmetry).

    The evaluator receives ONLY the claim and evidence — never the actor's
    self-report prose. This prevents bias from the actor's reasoning.

    Extracted from: closeout.task (high-level goal), closeout.task_type,
    and closeout.evidence. The closeout.result (actor's prose) is explicitly
    NOT extracted into the claim — it would violate MARCH asymmetry.
    """

    claim_id: str
    task_type: str  # bugfix, feature, refactor, research, docs, test, chore, etc
    description: str  # the claim itself (what was done or what's true)


@dataclass
class EvaluatorGrade:
    """Result of grading a claim against evidence."""

    claim_id: str
    verdict: Verdict
    confidence: float  # 0.0–1.0
    reasoning: str
    evidence_count: int
    gaps: list[str] = None  # what's missing to strengthen the grade

    def __post_init__(self):
        if self.gaps is None:
            self.gaps = []

    def asdict(self) -> dict[str, Any]:
        """Serialize grade to dict for scorecard consumption."""
        return {
            "claim_id": self.claim_id,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "evidence_count": self.evidence_count,
            "gaps": self.gaps,
        }


def extract_claims_from_closeout(closeout: dict[str, Any]) -> list[AtomicClaim]:
    """Extract atomic claims from a closeout dict (the v2 schema).

    MARCH asymmetry: extracts ONLY the claim (task + task_type), never the
    actor's result prose. The actor's self-report is kept separate so the
    evaluator grades purely based on evidence.

    A closeout can contain one or more verifiable claims:
    - The primary claim: the task itself (task_type + description)
    - Secondary claims: assertions in the result prose (not parsed here, v0)

    Returns [] if: status != completed, task is missing, or task_type is missing.
    """
    task = closeout.get("task", "")
    task_type = closeout.get("task_type", None)
    status = closeout.get("status", "")

    if not task or status != "completed" or not task_type:
        return []

    claim = AtomicClaim(
        claim_id=f"{closeout.get('timestamp', 'unknown')}:{task_type}",
        task_type=task_type,
        description=task,
    )
    return [claim]


def _has_evidence_of_type(evidence_list: list[dict[str, Any]], types: set[str]) -> bool:
    """Check if the evidence list includes at least one item of a given type."""
    return any(item.get("type") in types for item in evidence_list)


def _count_evidence_of_type(evidence_list: list[dict[str, Any]], types: set[str]) -> int:
    """Count how many evidence items are of the given types."""
    return sum(1 for item in evidence_list if item.get("type") in types)


def _claim_keywords(claim: AtomicClaim) -> set[str]:
    """Extract keywords from a claim for semantic relevance checks.

    Honest limits (v0): this is lexical, not semantic. Tokens are matched as
    substrings, so 'parser' matches 'parsers.py' but NOT a file named 'search.py'
    where the parser actually lives, and an agent can pass the check by echoing
    the task's words in a test name. It catches gross mismatches (config evidence
    for a parser claim), not subtle ones. A real semantic check is the LLM-judge
    job (Phase 4.4 go-live), not this scaffolding.
    """
    tokens = re.findall(r"\b\w+\b", claim.description.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}


def _evidence_has_semantic_relevance(evidence: dict[str, Any], claim_keywords: set[str]) -> bool:
    """True if any claim keyword appears in the evidence ref/detail (lexical)."""
    if not claim_keywords:
        # No usable keywords (e.g. a claim that is all stopwords/short tokens) —
        # can't judge relevance, so don't penalize: treat as relevant.
        return True
    ref = (str(evidence.get("ref", "")) + " " + str(evidence.get("detail", ""))).lower()
    return any(kw in ref for kw in claim_keywords)


def _relevant_count(
    evidence_list: list[dict[str, Any]], ev_type: str, claim_keywords: set[str]
) -> int:
    """Count evidence items of ``ev_type`` that are semantically relevant to the claim."""
    return sum(
        1
        for e in evidence_list
        if e.get("type") == ev_type and _evidence_has_semantic_relevance(e, claim_keywords)
    )


def _theater_grade(claim: AtomicClaim, evidence_list: list[dict[str, Any]], kind: str) -> EvaluatorGrade:
    """Verdict for evidence-theater: evidence of the right shape is present but
    none of it is semantically relevant to the claim."""
    return EvaluatorGrade(
        claim_id=claim.claim_id,
        verdict=Verdict.UNSUPPORTED,
        confidence=0.15,
        reasoning=f"Evidence present but none relevant to the {kind} claim",
        evidence_count=len(evidence_list),
        gaps=[f"Evidence does not appear to relate to the claimed {kind}"],
    )


def grade_claim_rule_based(
    claim: AtomicClaim,
    evidence_list: list[dict[str, Any]],
    workspace: Path | None = None,
    schema_version: int | None = None,
) -> EvaluatorGrade:
    """Rule-based grading: score a claim against evidence using a deterministic rubric.

    MARCH asymmetry: this function receives only the claim and evidence, not the
    actor's self-report or reasoning. It grades based on evidence alone.

    Rubric (verdicts):
    - SUPPORTED: the task-type's required *relevant* evidence is present
    - PARTIALLY_SUPPORTED: some relevant evidence but a required piece is missing
    - UNSUPPORTED: evidence present but none relevant (theater), or none at all
    - UNVERIFIABLE: no evidence / file evidence can't resolve / no rubric for type

    Semantic relevance (a claim keyword must appear in the evidence ref/detail)
    gates every SUBSTANTIVE task type uniformly; LENIENT types (chore/explore)
    accept any evidence, per contract.py's exemption.

    Args:
        claim: the claim to grade
        evidence_list: list of {type, ref, detail} dicts (v2 schema)
        workspace: required for evidence validation. If None, file evidence can't
                  be validated and returns UNVERIFIABLE
        schema_version: the closeout's schema_version. At >= EVIDENCE_REF_SCHEMA_VERSION
                  (v4, gap J4), any checkable evidence item lacking a mechanical
                  evidence_ref makes the grade UNVERIFIABLE (the closeout must
                  INDEX mechanically-recorded artifacts, not merely narrate).
                  Legacy/absent version -> unchanged behavior (backward compatible).

    Returns:
        EvaluatorGrade with verdict, confidence, and reasoning
    """
    # Gap J4 (schema_version >= v4): every checkable claim must link to a
    # MECHANICALLY RECORDED artifact. An item backed only by prose / an LLM's
    # say-so is abstained on -- the narration can't stand in for the trace. This
    # gate runs BEFORE the rubric so an unlinked "tests passed" never earns a
    # positive verdict. Legacy closeouts are exempt (unmechanized_evidence -> []).
    unmech = unmechanized_evidence(evidence_list, schema_version)
    if unmech:
        return EvaluatorGrade(
            claim_id=claim.claim_id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            reasoning=(
                f"schema v{schema_version} requires a mechanical evidence_ref "
                f"(one of {', '.join(_MECHANICAL_REF_KINDS)}) for each checkable claim; "
                f"{len(unmech)} evidence item(s) have only prose backing"
            ),
            evidence_count=len(evidence_list),
            gaps=[
                f"{it.get('type', '?')} evidence {it.get('ref', '?')!r} has no mechanical "
                "evidence_ref (exit code / file sha256 / git diff / oracle id / span id)"
                for it in unmech
            ],
        )

    # REQUIRED: Validate evidence references when file evidence is present
    has_file_evidence = any(e.get("type") == "file" for e in evidence_list)
    if has_file_evidence and not workspace:
        return EvaluatorGrade(
            claim_id=claim.claim_id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            reasoning="File evidence present but workspace not provided for validation",
            evidence_count=len(evidence_list),
            gaps=["Workspace required to validate file references"],
        )

    # Validate evidence references (file paths resolve, etc)
    if workspace:
        bad_refs = validate_evidence(evidence_list, workspace)
        if bad_refs:
            return EvaluatorGrade(
                claim_id=claim.claim_id,
                verdict=Verdict.UNVERIFIABLE,
                confidence=0.0,
                reasoning=f"Evidence references do not resolve: {bad_refs}",
                evidence_count=len(evidence_list),
                gaps=[f"Evidence {ref} cannot be found" for ref in bad_refs],
            )

    # Empty evidence: unverifiable
    if not evidence_list:
        gaps = ["No evidence provided"]
        if claim.task_type in ("bugfix", "test", "feature"):
            gaps.append("Expected test results for this task type")
        return EvaluatorGrade(
            claim_id=claim.claim_id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            reasoning="No evidence provided",
            evidence_count=0,
            gaps=gaps,
        )

    # Count evidence by type, both raw and semantically-relevant. The relevant
    # counts drive every SUBSTANTIVE branch so the anti-evidence-theater check is
    # applied uniformly, not just to bugfix (the old six-branch inconsistency).
    claim_keywords = _claim_keywords(claim)
    test_count = _count_evidence_of_type(evidence_list, {"test"})
    file_count = _count_evidence_of_type(evidence_list, {"file"})
    eval_count = _count_evidence_of_type(evidence_list, {"eval"})
    cmd_count = _count_evidence_of_type(evidence_list, {"command"})
    rel_test = _relevant_count(evidence_list, "test", claim_keywords)
    rel_file = _relevant_count(evidence_list, "file", claim_keywords)
    tt = claim.task_type

    # LENIENT types (chore/explore): mirror contract.py's exemption — any
    # evidence is accepted, relevance not required.
    if tt in _LENIENT_TYPES:
        if test_count or file_count or eval_count or cmd_count:
            return EvaluatorGrade(
                claim_id=claim.claim_id,
                verdict=Verdict.SUPPORTED,
                confidence=0.7,
                reasoning=f"{tt} claim evidenced: {len(evidence_list)} evidence item(s)",
                evidence_count=len(evidence_list),
                gaps=[],
            )
        return EvaluatorGrade(
            claim_id=claim.claim_id,
            verdict=Verdict.PARTIALLY_SUPPORTED,
            confidence=0.4,
            reasoning="Evidence present but type unrecognized",
            evidence_count=len(evidence_list),
            gaps=["Unclear if evidence is relevant to claim"],
        )

    if tt == "bugfix":
        # Needs a relevant test AND a relevant file change.
        if rel_test >= 1 and rel_file >= 1:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.SUPPORTED, confidence=0.9,
                reasoning=f"Bugfix demonstrated: {rel_test} relevant test(s), {rel_file} relevant file(s)",
                evidence_count=len(evidence_list), gaps=[],
            )
        if rel_test >= 1 or rel_file >= 1:
            gap_list = []
            if rel_test < 1:
                gap_list.append("Missing relevant test evidence")
            if rel_file < 1:
                gap_list.append("Missing relevant file change evidence")
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.PARTIALLY_SUPPORTED, confidence=0.55,
                reasoning=f"Bugfix partially evidenced: {rel_test} relevant test(s), {rel_file} relevant file(s)",
                evidence_count=len(evidence_list), gaps=gap_list,
            )
        if test_count >= 1 or file_count >= 1:
            return _theater_grade(claim, evidence_list, "bugfix")
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.UNSUPPORTED, confidence=0.1,
            reasoning="No test/file evidence for bugfix", evidence_count=len(evidence_list),
            gaps=["Bugfix requires test evidence and file changes"],
        )

    if tt in ("feature", "refactor"):
        # Needs a relevant file change AND a verification (test or eval).
        if rel_file >= 1 and (test_count >= 1 or eval_count >= 1):
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.SUPPORTED, confidence=0.85,
                reasoning=f"{tt} delivered: {rel_file} relevant file(s), {test_count + eval_count} verification(s)",
                evidence_count=len(evidence_list), gaps=[],
            )
        if rel_file >= 1:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.PARTIALLY_SUPPORTED, confidence=0.6,
                reasoning=f"{tt} file change present ({rel_file} relevant file(s)) but no verification",
                evidence_count=len(evidence_list), gaps=["Missing test or eval evidence"],
            )
        if file_count >= 1:
            return _theater_grade(claim, evidence_list, tt)
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.UNSUPPORTED, confidence=0.2,
            reasoning=f"No file evidence of {tt}", evidence_count=len(evidence_list),
            gaps=["No relevant file or verification evidence"],
        )

    if tt == "test":
        # A test-writing task needs test evidence AND a relevant (test) file.
        if test_count >= 1 and rel_file >= 1:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.SUPPORTED, confidence=0.9,
                reasoning=f"Tests added: {test_count} test result(s), {rel_file} relevant file(s)",
                evidence_count=len(evidence_list), gaps=[],
            )
        if rel_file >= 1:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.PARTIALLY_SUPPORTED, confidence=0.7,
                reasoning=f"Test file changed ({rel_file} relevant file(s)) but no test execution result",
                evidence_count=len(evidence_list), gaps=["Missing test execution evidence"],
            )
        if file_count >= 1 or test_count >= 1:
            return _theater_grade(claim, evidence_list, "test")
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.UNSUPPORTED, confidence=0.2,
            reasoning="No evidence of test addition", evidence_count=len(evidence_list),
            gaps=["No test or file evidence"],
        )

    if tt == "research":
        # Needs a relevant output file; an eval strengthens it to SUPPORTED.
        if rel_file >= 1:
            has_eval = eval_count >= 1
            return EvaluatorGrade(
                claim_id=claim.claim_id,
                verdict=Verdict.SUPPORTED if has_eval else Verdict.PARTIALLY_SUPPORTED,
                confidence=0.9 if has_eval else 0.75,
                reasoning=f"Research output: {rel_file} relevant file(s), {eval_count} eval(s)",
                evidence_count=len(evidence_list),
                gaps=[] if has_eval else ["No validation/eval evidence"],
            )
        if file_count >= 1:
            return _theater_grade(claim, evidence_list, "research")
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.UNSUPPORTED, confidence=0.1,
            reasoning="No research output file", evidence_count=len(evidence_list),
            gaps=["Missing research file output"],
        )

    if tt == "docs":
        # Needs a relevant documentation file.
        if rel_file >= 1:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.SUPPORTED, confidence=0.85,
                reasoning=f"Documentation added/updated: {rel_file} relevant file(s)",
                evidence_count=len(evidence_list), gaps=[],
            )
        if file_count >= 1:
            return _theater_grade(claim, evidence_list, "docs")
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.UNSUPPORTED, confidence=0.0,
            reasoning="No documentation file evidence", evidence_count=len(evidence_list),
            gaps=["Missing documentation file"],
        )

    # Unknown substantive type not in either set: accept any evidence but flag it.
    if test_count or file_count or eval_count or cmd_count:
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.PARTIALLY_SUPPORTED, confidence=0.5,
            reasoning=f"Unrecognized task_type {tt!r}; evidence present but ungraded by rubric",
            evidence_count=len(evidence_list),
            gaps=[f"No rubric for task_type {tt!r}"],
        )
    return EvaluatorGrade(
        claim_id=claim.claim_id, verdict=Verdict.UNVERIFIABLE, confidence=0.0,
        reasoning=f"Unrecognized task_type {tt!r} and no evidence",
        evidence_count=len(evidence_list), gaps=[f"No rubric for task_type {tt!r}"],
    )


def grade_claim(
    claim: AtomicClaim,
    evidence_list: list[dict[str, Any]],
    workspace: Path | None = None,
    judge_tier: str | None = None,
    schema_version: int | None = None,
    **judge_kwargs: Any,
) -> EvaluatorGrade:
    """Unified grading entry point.

    Default (``judge_tier=None``): the deterministic rule-based rubric — cheap,
    offline, the structural scaffolding. Pass a ``judge_tier`` (e.g. "glm5.2",
    "deepseek", "ollama") to use the semantic LLM judge instead, for the
    claim<->evidence relevance judgment the rubric can't make. Rule-based stays the
    default so nothing that doesn't ask for a judge pays for one.

    ``schema_version`` carries the closeout's version so the J4 mechanical
    evidence_ref gate applies to v4+ closeouts. The gate is deterministic and
    runs regardless of judge_tier: the LLM judge is a downstream refinement, so
    an unlinked checkable claim is abstained on BEFORE any judge is invoked --
    keeping the anti-circular property (no LLM in the mechanical-ref decision).
    """
    if schema_version is not None:
        gate = unmechanized_evidence(evidence_list, schema_version)
        if gate:
            return grade_claim_rule_based(
                claim, evidence_list, workspace, schema_version=schema_version
            )
    if judge_tier is None:
        return grade_claim_rule_based(
            claim, evidence_list, workspace, schema_version=schema_version
        )
    # Lazy import: judge.py imports from this module, so importing it at module load
    # would be circular.
    from .judge import llm_judge

    return llm_judge(
        claim, evidence_list, tier=judge_tier, workspace=workspace, **judge_kwargs
    )


def compute_verified_gap(
    closeouts: list[dict[str, Any]],
    workspace: Path | None = None,
    judge_tier: str | None = None,
) -> dict[str, Any]:
    """Compute the self_report_vs_verified_gap metric from a set of closeouts.

    For each closeout, grade the primary claim against the evidence. Aggregate
    into a metric that tracks:
    - verified_count: closeouts where verdict is SUPPORTED
    - partial_count: PARTIALLY_SUPPORTED
    - unverified_count: UNSUPPORTED or UNVERIFIABLE
    - total: total closeouts graded
    - gap_fraction: unverified / total (the main metric)

    Returns:
        {
            "verified_count": int,
            "partial_count": int,
            "unverified_count": int,
            "total": int,
            "gap_fraction": float,  # unverified / total (0.0 = no gap, 1.0 = all unverified)
            "grades": [EvaluatorGrade as dict, ...],
        }
    """
    if not closeouts:
        return {
            "verified_count": 0,
            "partial_count": 0,
            "unverified_count": 0,
            "total": 0,
            "gap_fraction": 0.0,
            "grades": [],
        }

    grades: list[EvaluatorGrade] = []
    for closeout in closeouts:
        claims = extract_claims_from_closeout(closeout)
        evidence = closeout.get("evidence", [])
        # Read the closeout's OWN schema_version so a legacy row and a v4 row in
        # the same batch are each graded under their own rules (gap J4).
        schema_version = closeout.get("schema_version")
        for claim in claims:
            grade = grade_claim(
                claim, evidence, workspace, judge_tier=judge_tier,
                schema_version=schema_version,
            )
            grades.append(grade)

    verified = sum(1 for g in grades if g.verdict == Verdict.SUPPORTED)
    partial = sum(1 for g in grades if g.verdict == Verdict.PARTIALLY_SUPPORTED)
    unverified = sum(1 for g in grades if g.verdict in (Verdict.UNSUPPORTED, Verdict.UNVERIFIABLE))

    total = len(grades)
    gap_fraction = unverified / total if total > 0 else 0.0

    return {
        "verified_count": verified,
        "partial_count": partial,
        "unverified_count": unverified,
        "total": total,
        "gap_fraction": gap_fraction,
        "grades": [g.asdict() for g in grades],
    }
