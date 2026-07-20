"""Stage 1 of the server-driven state-machine engine: the hard core.

Built from Fable's design (docs/research/
STATE-MACHINE-DESIGN-fable-research-2026-07-06.md) to satisfy the executable
spec in tests/test_state_engine.py -- a Harel-statechart-shaped track chart per
task, interpreted server-side as an **event-sourced, single-writer state
machine** on SQLite. The chart is *data* (a dict you can load/version); a
~200-line interpreter walks it. The `transitions` library was considered
per the brief but is not installed here, and the interpreter serves
"chart is data you load" more directly, so this module has zero deps beyond
the stdlib.

Correctness discipline (the reason this file exists at all):

- **One tool call = one superstep = one `BEGIN IMMEDIATE` transaction.**
  The task row is read *after* the write lock is taken, so two concurrent
  calls on one task serialize; the loser re-reads the bumped seq and gets
  `REJECTED_STALE` + a fresh envelope (the "reject" double-texting strategy).
  Belt-and-braces: `event` PK `(task_id, seq)` makes a double-apply at the
  same seq an IntegrityError even if fencing were somehow bypassed.
- **Idempotency** by `(task_id, idem_key)` unique index on the event log; a
  duplicate submission replays the *stored* envelope verbatim (checked before
  the seq fence, because a retried call is stale by construction).
- **Claims are exclusive by construction**: `(kind, key)` PRIMARY KEY, plus a
  same-kind glob-overlap check (fnmatch both directions -- approximate, but
  strictly safer than PK-equality alone). Acquisition is atomic
  all-or-nothing inside one ordered transaction: no hold-and-wait, so no
  deadlock, structurally.
- **Lease + reaper** (Temporal heartbeat-details as a *pattern*, not a dep):
  every applied step renews the lease; `reap()` moves expired-lease tasks to
  STALLED, preserving the intent record so a replacement resumes instead of
  restarting.
- **Rework cap -> ESCALATE -> ABANDONED, always via CLOSEOUT.** The
  abandonment closeout is written *server-side in the same superstep* -- a
  runaway or vanished client cannot skip the audit record.
- **Event-sourced**: every applied superstep appends an event that snapshots
  its fold target (`to_state`), so `replay()` can rebuild (state, seq) from
  the log alone. The log IS the audit-closeout source.

Refusals are guidance, not walls: `{ok: false, code, reason, do_instead,
legal_tools}` with the current state/seq so a dumb client can resync. State
and seq are UNCHANGED on any refusal (refusal paths write nothing).

Stage 1 scope (per inbox/FABLE-BUILD-BRIEF-state-engine.md): this engine +
the single "build" track. The supervisor/MISSION layer, extra tracks, and the
versioned-bundle loader are later stages.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from fnmatch import fnmatchcase
from typing import Any, Callable, Iterator

__all__ = [
    "StateEngine", "BUILD_TRACK", "RESEARCH_TRACK", "ASSURED_BUILD_TRACK",
    "ASSURED_RESEARCH_TRACK", "MISSION_TRACK", "APP_BUILD_TRACK",
    "phase_legal_tools", "register_track", "default_gate", "review_scope_gate",
    "partition_coverage_gate", "smoke_verdict_gate", "research_sufficiency_gate",
    "make_universal_gate",
    "ADVISORY_SEMI_GOLD", "advisory_semi_gold",
    "build_grounding_gate",
]

# ---------------------------------------------------------------------------
# GAP B2 (2026-07-14): the abstain-default REVIEW exit. When an auto task reaches a
# review phase that declares an `abstain_exit` and NO deterministic oracle backed the
# verdict AND no human is available, the engine defaults to ABSTAIN + flag-for-human --
# a first-class LOGGED SUCCESS, never a confident-but-unverified pass. Any arbitration /
# council / judge verdict that feeds that decision is advisory only, so it is wrapped in
# this HARD non-trainable, non-promotable data class: an advisory (non-oracle) verdict
# must never leak into trainable gold or a promotion ledger.
# ---------------------------------------------------------------------------
ADVISORY_SEMI_GOLD = "advisory_semi_gold"


def advisory_semi_gold(verdict: Any) -> dict[str, Any]:
    """Wrap an advisory (non-deterministic) verdict in the hard non-promotable envelope.

    `promotable`/`trainable` are False BY CONSTRUCTION here -- callers do not get to flip
    them. This is the type contract B2 requires for any council/arbitration output that
    feeds an ABSTAIN decision: it is directional guidance for a human, not ground truth.
    """
    return {
        "data_class": ADVISORY_SEMI_GOLD,
        "promotable": False,
        "trainable": False,
        "verdict": verdict,
    }


def _verdict_is_oracle_backed(verdict: dict[str, Any]) -> bool:
    """A verdict counts as oracle-backed ONLY if a deterministic checker minted it and
    said so (`deterministic: True`). The permissive default_gate / advisory judges do NOT
    set this, so their `pass` is treated as un-oracle-able -- the whole point of B2."""
    return verdict.get("deterministic") is True


def _no_human_available(intent: dict[str, Any]) -> bool:
    """No human is on call when the task is running AUTO/unattended and has not declared a
    reachable human. A human-in-the-loop task (auto absent, or human_available true) never
    abstains -- the human is the reviewer, so existing interactive flows are unchanged."""
    return bool(intent.get("auto")) and not bool(intent.get("human_available"))

# ---------------------------------------------------------------------------
# The "build" track chart -- data, not code. Non-terminal states declare their
# single *advance* tool (submitting it triggers the gate and, on pass, the
# `next` transition), optional in-phase `extra_tools` (legal, recorded, no
# transition), and one imperative instruction (small-model doctrine: one
# sentence, never "you may either"). REVIEW declares `rework_to`: a gate fail
# there loops back, counted against `rework_cap`, then `esc_cap`.
# ---------------------------------------------------------------------------

BUILD_TRACK: dict[str, Any] = {
    "track": "build",
    "version": "1",
    "initial": "SEARCH_BRAIN",
    # Rework/escalation caps: rework_cap gate-fails per escalation level, then
    # esc_level++; past esc_cap the task is abandoned (via CLOSEOUT, always).
    "rework_cap": 2,
    "esc_cap": 2,
    "states": {
        "SEARCH_BRAIN": {
            "advance_tool": "cortex_report_findings",
            "extra_tools": ["cortex_search"],
            "next": "RESEARCH",
            "instruction": "Search the corpus with cortex_search, then call "
                           "cortex_report_findings with the evidence you found.",
        },
        "RESEARCH": {
            "advance_tool": "cortex_report_findings",
            "extra_tools": ["cortex_search"],
            "next": "PLAN",
            "instruction": "Close the open questions, then call "
                           "cortex_report_findings with cited evidence.",
        },
        "PLAN": {
            "advance_tool": "cortex_submit_plan",
            "next": "SPEC",
            "instruction": "Call cortex_submit_plan with the step-by-step plan.",
        },
        "SPEC": {
            "advance_tool": "cortex_submit_spec",
            "next": "IMPLEMENT",
            "instruction": "Call cortex_submit_spec with the success conditions.",
        },
        "IMPLEMENT": {
            "advance_tool": "cortex_submit_patch",
            "next": "REVIEW",
            "instruction": "Apply the change, then call cortex_submit_patch "
                           "with the patch.",
        },
        "REVIEW": {
            "advance_tool": "cortex_submit_review",
            "next": "CLOSEOUT",
            "rework_to": "IMPLEMENT",
            # GAP B2: when an AUTO task's REVIEW would "pass" with no deterministic oracle
            # and no human on call, the engine routes here instead of a fake-confident
            # advance -- ABSTAIN + flag-for-human, logged as a handled success.
            "abstain_exit": "ABSTAINED",
            "instruction": "Call cortex_submit_review with the review verdict "
                           "and evidence.",
        },
        "CLOSEOUT": {
            "advance_tool": "cortex_write_closeout",
            "next": "DONE",
            "is_closeout": True,
            "instruction": "Call cortex_write_closeout with task, result, and "
                           "test status.",
        },
        "DONE": {"terminal": True, "instruction": "Task is complete."},
        "ABANDONED": {"terminal": True, "instruction": "Task was abandoned."},
        "ABSTAINED": {"terminal": True,
                      "instruction": "Task abstained: no oracle, no human -- flagged for "
                                     "human review (a closeout was recorded)."},
        # Reaper target. Holds the pre-stall state in task.stalled_from; the
        # single legal tool resumes there with a fresh lease.
        "STALLED": {
            "resume": True,
            "instruction": "Call cortex_resume to take over this stalled task.",
        },
    },
}

# ---------------------------------------------------------------------------
# The "research" track chart -- data, not code, exactly like BUILD_TRACK. Maps
# the existing linear deep-research pipeline (research.py: frame -> seed ->
# fetch -> gather_evidence -> cite_check -> summarize -> write_report) onto the
# SAME event-sourced interpreter with ZERO engine changes (D1 audit,
# research/deep_research_state_machine/notes_D1_internal.md:187-205,314-315).
# This is the chart + wiring only: the linear research.py/deep_research.py path
# is deliberately left intact as the working fallback (that migration is a
# follow-up, out of scope here).
#
# CITE_CHECK is the one reworkable phase: a coverage-gate failure (a sub-
# question left unanswered / under-corroborated) loops back to FETCH to pull
# more sources -- the research analogue of BUILD_TRACK's REVIEW->IMPLEMENT
# rework, and the gate-refusal-and-recovery path the tests exercise. Past the
# rework/escalation caps the task still abandons via the server-written
# CLOSEOUT audit record (engine invariant, unchanged).
# ---------------------------------------------------------------------------

RESEARCH_TRACK: dict[str, Any] = {
    "track": "research",
    "version": "1",
    "initial": "FRAME",
    "rework_cap": 2,
    "esc_cap": 2,
    "states": {
        "FRAME": {
            "advance_tool": "cortex_submit_framing",
            "extra_tools": ["cortex_search"],
            "next": "SEED",
            "instruction": "Decompose the question into sub-questions, then call "
                           "cortex_submit_framing with them.",
        },
        "SEED": {
            "advance_tool": "cortex_submit_seeds",
            "extra_tools": ["cortex_search"],
            "next": "FETCH",
            "instruction": "Select candidate sources from the registry, then call "
                           "cortex_submit_seeds with the chosen source list.",
        },
        "FETCH": {
            "advance_tool": "cortex_submit_fetch_report",
            "extra_tools": ["cortex_fetch_doc"],
            "next": "EVIDENCE",
            "instruction": "Fetch the seeded sources, then call "
                           "cortex_submit_fetch_report with fetched/failed/skipped.",
        },
        "EVIDENCE": {
            "advance_tool": "cortex_submit_evidence",
            "extra_tools": ["cortex_search"],
            "next": "CITE_CHECK",
            "instruction": "Gather corpus-backed chunks per sub-question, then call "
                           "cortex_submit_evidence with the evidence map.",
        },
        "CITE_CHECK": {
            "advance_tool": "cortex_submit_coverage",
            "next": "SUMMARIZE",
            "rework_to": "FETCH",
            "instruction": "Call cortex_submit_coverage with the coverage/"
                           "corroboration report; unanswered sub-questions loop "
                           "back to FETCH.",
        },
        "SUMMARIZE": {
            "advance_tool": "cortex_submit_findings",
            "next": "REPORT",
            "instruction": "Write the prose findings, then call "
                           "cortex_submit_findings with them.",
        },
        "REPORT": {
            "advance_tool": "cortex_write_research_report",
            "next": "DONE",
            "is_closeout": True,
            "instruction": "Call cortex_write_research_report with the cited "
                           "report; citations resolve to corpus paths.",
        },
        "DONE": {"terminal": True, "instruction": "Research task is complete."},
        "ABANDONED": {"terminal": True, "instruction": "Research task was abandoned."},
        "STALLED": {
            "resume": True,
            "instruction": "Call cortex_resume to take over this stalled research task.",
        },
    },
}

# Fail-closed production variants. The legacy charts remain available for compatibility, but
# drivers that claim the research-driven Cortex behavior contract must select these tracks.
# The extra state is chart data; the shared interpreter and event model remain unchanged.
ASSURED_BUILD_TRACK: dict[str, Any] = json.loads(json.dumps(BUILD_TRACK))
ASSURED_BUILD_TRACK["track"] = "assured_build"
ASSURED_BUILD_TRACK["version"] = "2"
ASSURED_BUILD_TRACK["states"]["RESEARCH"]["next"] = "RESEARCH_DECISION"
ASSURED_BUILD_TRACK["states"]["RESEARCH_DECISION"] = {
    "advance_tool": "cortex_submit_research_sufficiency",
    "next": "PLAN",
    "rework_to": "RESEARCH",
    "bound_gate": "research_sufficiency",
    "abstain_exit": "ABSTAINED",
    "instruction": "Submit the server-stored research sufficiency receipt. Only a "
                   "decision-bound SUFFICIENT_FOR_DECISION receipt advances to planning; "
                   "UNRESOLVED reworks research and ABSTAIN exits honestly.",
}

ASSURED_RESEARCH_TRACK: dict[str, Any] = json.loads(json.dumps(RESEARCH_TRACK))
ASSURED_RESEARCH_TRACK["track"] = "assured_research"
ASSURED_RESEARCH_TRACK["version"] = "2"
ASSURED_RESEARCH_TRACK["states"]["REPORT"]["next"] = "SUFFICIENCY"
ASSURED_RESEARCH_TRACK["states"]["REPORT"].pop("is_closeout", None)
ASSURED_RESEARCH_TRACK["states"]["SUFFICIENCY"] = {
    "advance_tool": "cortex_submit_research_sufficiency",
    "next": "DONE",
    "rework_to": "FETCH",
    "is_closeout": True,
    "bound_gate": "research_sufficiency",
    "abstain_exit": "ABSTAINED",
    "instruction": "Submit the server-stored decision-bound sufficiency receipt. "
                   "SUFFICIENT_FOR_DECISION completes; UNRESOLVED reopens bounded research; "
                   "ABSTAIN closes honestly without unlocking dependent work.",
}

# The mission-layer orchestration track (Phase 5.2, 2026-07-08): supervisory state machine that
# coordinates parallel workers via declared reducers. See docs/research/MISSION-MERGE-DESIGN-2026-07-08.md.
# Workers are spun up in DISPATCH (via the existing spawn_mission logic), drive in parallel through
# their own build/research charts, and reconverge at MERGE to apply declared reducers over their
# final artifacts. Review scope-checks the assembled whole against the mission intent.
MISSION_TRACK: dict[str, Any] = {
    "track": "mission",
    "version": "1",
    "initial": "INTAKE",
    "rework_cap": 2,
    "esc_cap": 2,
    "states": {
        "INTAKE": {
            "advance_tool": "cortex_submit_mission_contract",
            "next": "PARTITION",
            "instruction": "Submit the mission contract with coverage_spec, reducers, "
                           "and acceptance_criteria.",
        },
        "PARTITION": {
            "advance_tool": "cortex_submit_partition",
            "next": "DISPATCH",
            "instruction": "Submit the partition with workers and their owns_units; "
                           "the coverage gate validates collective exhaustiveness.",
        },
        "DISPATCH": {
            "advance_tool": "cortex_dispatch_mission",
            "next": "MONITOR",
            "instruction": "Trigger worker spawn (atomic claim acquisition); workers "
                           "run their own charts in parallel.",
        },
        "MONITOR": {
            "advance_tool": "cortex_submit_merge",
            "next": "MERGE",
            "instruction": "Once all workers are DONE (poll cortex_mission_status), "
                           "submit the merge to fold their artifacts per declared reducers.",
        },
        "MERGE": {
            "advance_tool": "cortex_submit_review",
            "next": "REVIEW",
            "rework_to": "MERGE",
            "instruction": "Review the merged artifact against the acceptance_criteria; "
                           "gate failures loop back to MERGE (rework_cap-bounded).",
        },
        "REVIEW": {
            "advance_tool": "cortex_submit_review",
            "next": "CLOSEOUT",
            "rework_to": "MERGE",
            "instruction": "Final scope check: compare the merged whole against the "
                           "original mission intent (seeking field).",
        },
        "CLOSEOUT": {
            "advance_tool": "cortex_write_closeout",
            "next": "DONE",
            "is_closeout": True,
            "instruction": "Write the mission closeout audit record.",
        },
        "DONE": {"terminal": True, "instruction": "Mission complete."},
        "ABANDONED": {"terminal": True, "instruction": "Mission was abandoned."},
        "STALLED": {
            "resume": True,
            "instruction": "Call cortex_resume to take over this stalled mission.",
        },
    },
}

# ---------------------------------------------------------------------------
# The "app_build" track chart (2026-07-11, director-cascade plan §1.1) -- data
# only, ZERO engine changes. One small chart reused per conversational chunk:
# SCAFFOLD -> SMOKE -> SHOW -> CLOSEOUT. The SMOKE gate is the DETERMINISTIC
# app_gates verdict (subprocess behavioral checks) passed through
# `smoke_verdict_gate` -- never a model judge. A SMOKE failure reworks to
# SCAFFOLD, bounded by the chart's rework/esc caps AND (review fix #5) the
# project-level attempt budget enforced by cortex_core/hybrid_build.py above
# this chart (per-chunk child tasks alone would reset the cap -- the livelock
# bypass GLM-5.2 flagged). The model never names a state: the driver submits
# the DECLARED advance tool for the current state, and anything else is
# refused by the engine's legality gate (routing-as-data, DEEP-RESEARCH
# 2026-07-08: "the model selects among declared transitions, never invents").
# ---------------------------------------------------------------------------

APP_BUILD_TRACK: dict[str, Any] = {
    "track": "app_build",
    "version": "1",
    "initial": "SCAFFOLD",
    "rework_cap": 2,
    "esc_cap": 2,
    "states": {
        "SCAFFOLD": {
            "advance_tool": "cortex_submit_artifact",
            "next": "SMOKE",
            # terra RE-REVIEW #1: on advance, the engine persists a server-computed digest
            # of the submitted artifact dir + its checks onto the task, so SMOKE can require
            # the verdict receipt was minted over THIS task's artifact/checks (not any other).
            "persist_artifact": True,
            "instruction": "Execute the injected build step, then call "
                           "cortex_submit_artifact with the artifact manifest.",
        },
        "SMOKE": {
            "advance_tool": "cortex_submit_smoke",
            "next": "SHOW",
            "rework_to": "SCAFFOLD",
            # terra fix #1: the SMOKE gate is BOUND in the chart data itself. The engine
            # sees this marker and ALWAYS routes the phase through smoke_verdict_gate --
            # a default-gate StateEngine on app_build fails closed instead of open, and a
            # caller cannot un-bind it by constructing the engine without
            # make_universal_gate.
            "bound_gate": "smoke_verdict",
            "instruction": "Submit the verdict_id minted by the server-side deterministic "
                           "gate run via cortex_submit_smoke; a failing or missing verdict "
                           "loops back to SCAFFOLD.",
        },
        "SHOW": {
            "advance_tool": "cortex_submit_reaction",
            "next": "CLOSEOUT",
            "instruction": "Show the artifact to the human, then call "
                           "cortex_submit_reaction with their verbatim reaction.",
        },
        "CLOSEOUT": {
            "advance_tool": "cortex_write_closeout",
            "next": "DONE",
            "is_closeout": True,
            "instruction": "Call cortex_write_closeout with route, gate "
                           "verdict, and reaction.",
        },
        "DONE": {"terminal": True, "instruction": "Chunk complete."},
        "ABANDONED": {"terminal": True, "instruction": "Chunk was abandoned."},
        "STALLED": {
            "resume": True,
            "instruction": "Call cortex_resume to take over this stalled chunk.",
        },
    },
}

# States every chart must have for the engine's own transitions (reaper,
# abandonment). Injected into custom charts if absent -- forgiving-load.
_ENGINE_STATES: dict[str, dict[str, Any]] = {
    "STALLED": {"resume": True, "instruction": "Call cortex_resume to take over."},
    "ABANDONED": {"terminal": True, "instruction": "Task was abandoned."},
    "DONE": {"terminal": True, "instruction": "Task is complete."},
    # GAP B2: the terminal abstain sink, injected into every chart so an `abstain_exit`
    # reference always resolves (a chart may still override its instruction).
    "ABSTAINED": {"terminal": True,
                  "instruction": "Task abstained: no oracle, no human -- flagged for human."},
}

GateFn = Callable[[str, dict[str, Any], Any], dict[str, Any]]

_TRACKS: dict[str, dict[str, Any]] = {
    "build": BUILD_TRACK,
    "research": RESEARCH_TRACK,
    "assured_build": ASSURED_BUILD_TRACK,
    "assured_research": ASSURED_RESEARCH_TRACK,
    "mission": MISSION_TRACK,
    "app_build": APP_BUILD_TRACK,
}


def register_track(chart: dict[str, Any]) -> dict[str, Any]:
    """Additive module-level chart registration: validate (fail-at-load, the
    `_validate_chart` no-drift rule) and expose the chart to `phase_legal_tools`'
    `_TRACKS`. Charts are DATA -- registering one changes no engine code. New
    StateEngine instances pick registered tracks up via the built-in dict copy;
    an existing instance can call `StateEngine.register_track` for the same."""
    loaded = _validate_chart(chart)
    _TRACKS[loaded["track"]] = loaded
    return loaded


def phase_legal_tools(track: str, state: str) -> list[str]:
    """Return the deterministic, phase-relevant tool names for a given chart state.

    This is the server's disclosure controller: the state machine knows the phase,
    and this function exposes exactly the tools the current phase needs (advance
    tool + optional extras). Terminal and resume states are handled consistently
    with the engine's own ``_legal_tools``.
    """
    chart = _TRACKS[track]
    spec = chart["states"][state]
    if spec.get("terminal"):
        return []
    if spec.get("resume"):
        return ["cortex_resume"]
    return [spec["advance_tool"], *spec.get("extra_tools", [])]


def partition_coverage_gate(phase: str, task: dict[str, Any], payload: Any,
                            base: GateFn | None = None) -> dict[str, Any]:
    """Mission planning gate: validates the partition is MECE (mutually exclusive, collectively
    exhaustive). Runs only at PARTITION phase and only if the base gate passes. Checks:
    1. required_units are collectively covered by workers' declared owns_units (exhaustiveness)
    2. no required_unit is owned by >1 worker (exclusivity among required units)
    3. worker count does not exceed max_workers bound (fan-out guard)

    See MISSION-MERGE-DESIGN-2026-07-08.md Piece 1 for the design."""
    if base is None:
        base = default_gate
    v = base(phase, task, payload)
    if not v.get("pass") or phase != "PARTITION":
        return v
    spec = (task.get("intent") or {}).get("coverage_spec") or {}
    required = set(spec.get("required_units") or [])
    workers = payload.get("workers", []) if isinstance(payload, dict) else []
    owned = [set(w.get("owns_units", []) or []) for w in workers if isinstance(w, dict)]
    union = set().union(*owned) if owned else set()

    missing = required - union
    if missing:
        return {"pass": False, "code": "MISSING_COVERAGE",
                "reason": f"units not owned by any worker: {sorted(missing)}"}
    dupes = [u for u in required if sum(u in o for o in owned) > 1]
    if dupes:
        return {"pass": False, "code": "UNIT_DOUBLE_OWNED",
                "reason": f"units owned by >1 worker (duplication risk): {sorted(dupes)}"}
    max_w = spec.get("max_workers", 8)
    if len(workers) > max_w:
        return {"pass": False, "code": "FANOUT_EXCEEDED",
                "reason": f"{len(workers)} workers > max_workers {max_w}"}
    # deterministic: MECE coverage is a deterministic set check, not a judge (B2 oracle marker).
    return {**v, "coverage": "ok", "units": len(required), "workers": len(workers),
            "deterministic": True}


def default_gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Default exit-criteria evaluator: permit any well-formed phase report.

    Real deployments plug in deterministic checks first (tests pass, schema
    valid, N sources cited), rubric judges second -- this is deliberately the
    weakest link in the pluggable chain, not the ceiling.
    """
    if payload is not None and not isinstance(payload, dict):
        return {"pass": False, "reason": "phase report must be a JSON object"}
    return {"pass": True}


# The reworkable review phases where a scope-vs-intent check belongs: BUILD's REVIEW and
# RESEARCH's CITE_CHECK. A failure here loops back to rework (IMPLEMENT / FETCH) with the reason.
_SCOPE_CHECK_PHASES = frozenset({"REVIEW", "CITE_CHECK"})
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "onto", "your", "their",
    "about", "over", "under", "then", "than", "them", "they", "task", "make", "build",
    "using", "used", "also", "some", "have", "will", "should", "must", "does", "done",
})


def _content_tokens(text: str) -> set[str]:
    """Lowercased content words (len>=4, non-stopword) -- a cheap, deterministic overlap signal."""
    out: set[str] = set()
    for raw in "".join(c if c.isalnum() else " " for c in str(text).lower()).split():
        if len(raw) >= 4 and raw not in _STOPWORDS:
            out.add(raw)
    return out


def smoke_verdict_gate(phase: str, task: dict[str, Any], payload: Any,
                       base: GateFn = default_gate,
                       workspace: Any = None) -> dict[str, Any]:
    """APP_BUILD SMOKE gate, SERVER-OWNED (terra HIGH #1, 2026-07-11 rework).

    The old version accepted a caller payload {"verdict": {"passed": bool}} and only
    type-checked it -- a model calling the legal cortex_submit_smoke step could forge a
    pass. Now the ONLY accepted payload is {"verdict_id": "<opaque id>"} minted by
    `cortex_core.receipts.run_and_record_smoke_verdict()` -- which RUNS the deterministic
    gate (`app_gates.run_done_checks`, or an injected offline test gate) and takes the
    passing bit from the returned GateVerdict (no caller `passed`). This gate LOOKS THE
    RECEIPT UP and re-validates it: task binding, artifact content digest bound to the
    TASK's own SCAFFOLD artifact + required checks, gate identity, and gate version. Missing /
    unknown / task-mismatched / artifact-or-checks-mismatched / tampered / inauthentic-gate =>
    fail-CLOSED, which in the app_build chart is a rework to SCAFFOLD. NO LLM -- and now no
    CALLER -- is ever in this verdict path.

    terra RE-REVIEW-2 #1: a task whose SCAFFOLD never bound a real artifact digest (no/
    unreadable app_dir => intent.scaffold_artifact_digest is None) can never advance here on
    ANY receipt: validate_smoke_receipt FAILS a None expected digest (NO_ARTIFACT) instead of
    skipping the comparison. Missing artifact = fail CLOSED, never open."""
    v = base(phase, task, payload)
    if not v.get("pass"):
        return v
    if isinstance(payload, dict) and "verdict" in payload and "verdict_id" not in payload:
        # The pre-receipt payload shape: name the violation honestly instead of a generic miss.
        return {"pass": False, "code": "VERDICT_NOT_SERVER_OWNED",
                "reason": "SMOKE no longer trusts a caller-supplied verdict boolean; submit "
                          "the verdict_id minted by the server-side deterministic gate run"}
    vid = payload.get("verdict_id") if isinstance(payload, dict) else None
    from cortex_core import receipts
    # terra RE-REVIEW #1: bind the receipt to the TASK's own SCAFFOLD artifact + required
    # checks (persisted server-side at SCAFFOLD advance), so a genuine passing receipt minted
    # over a different artifact/checks cannot pass THIS task's SMOKE.
    intent = task.get("intent") or {}
    res = receipts.validate_smoke_receipt(
        vid, task_id=task.get("task_id"),
        expected_artifact_digest=intent.get("scaffold_artifact_digest"),
        expected_checks_digest=intent.get("required_checks_digest"),
        workspace=workspace)
    if not res.get("ok"):
        return {"pass": False, "code": res.get("code", "NO_VERDICT_RECEIPT"),
                "reason": res.get("reason", "smoke verdict receipt did not validate")}
    if res["passed"]:
        # deterministic: this pass came from a server-owned deterministic checker (B2 oracle marker).
        return {**v, "smoke": "ok", "verdict_id": vid, "deterministic": True}
    fc = res.get("failure_class")
    return {"pass": False, "code": "SMOKE_FAIL", "failure_class": fc,
            "reason": f"deterministic done-checks failed ({fc or 'unclassified'})"}


def research_sufficiency_gate(phase: str, task: dict[str, Any], payload: Any,
                              base: GateFn = default_gate,
                              workspace: Any = None) -> dict[str, Any]:
    """Validate a server-stored, task/policy/decision-bound research receipt.

    The task's expected decision and policy digests are persisted in its intent. The caller
    submits only an opaque receipt ID; caller-supplied receipt bodies and outcome booleans are
    ignored. Only SUFFICIENT_FOR_DECISION passes. UNRESOLVED reworks and ABSTAIN is surfaced as
    a first-class non-unlocking outcome by the engine.
    """
    v = base(phase, task, payload)
    if not v.get("pass"):
        return v
    if not isinstance(payload, dict):
        return {"pass": False, "code": "RESEARCH_RECEIPT_REQUIRED",
                "reason": "payload must contain the server-stored receipt_id"}
    receipt_id = payload.get("receipt_id")
    intent = task.get("intent") or {}
    if isinstance(intent, str):
        try:
            intent = json.loads(intent)
        except (json.JSONDecodeError, TypeError):
            intent = {}
    decision_id = intent.get("research_decision_id")
    policy_sha = intent.get("research_policy_sha256")
    assurance_task_id = intent.get("assurance_task_id")
    run_id = intent.get("run_id")
    if (not _nonempty_str(decision_id) or not _nonempty_str(policy_sha)
            or not _nonempty_str(assurance_task_id) or not _nonempty_str(run_id)):
        return {"pass": False, "code": "RESEARCH_BINDING_MISSING",
                "reason": "task intent lacks server run_id/assurance_task_id/"
                          "research_decision_id/research_policy_sha256"}
    if workspace is None:
        return {"pass": False, "code": "RESEARCH_RECEIPT_STORE_MISSING",
                "reason": "engine has no workspace for receipt lookup"}
    from cortex_core.research_sufficiency import validate_sufficiency_receipt
    checked = validate_sufficiency_receipt(
        receipt_id,
        expected_task_id=assurance_task_id,
        expected_research_task_id=str(task.get("task_id") or ""),
        expected_run_id=run_id,
        expected_decision_id=decision_id,
        expected_policy_sha256=policy_sha,
        workspace=workspace,
        now=datetime.now(timezone.utc).isoformat(),
    )
    if not checked.get("valid"):
        return {"pass": False, "code": "RESEARCH_RECEIPT_INVALID",
                "reason": checked.get("reason", "receipt validation failed")}
    outcome = checked.get("outcome")
    if outcome == "SUFFICIENT_FOR_DECISION":
        return {**v, "oracle_backed": True, "receipt_id": receipt_id,
                "outcome": outcome,
                "unlocked_work": checked["receipt"].get("unlocked_work", [])}
    if outcome == "ABSTAIN":
        return {"pass": False, "code": "RESEARCH_ABSTAINED", "outcome": "ABSTAIN",
                "receipt_id": receipt_id,
                "reason": "qualified research authority abstained for this decision"}
    return {"pass": False, "code": "RESEARCH_UNRESOLVED", "outcome": "UNRESOLVED",
            "receipt_id": receipt_id,
            "reason": "research remains unresolved for this decision"}


def make_universal_gate(base: GateFn = default_gate,
                        visual_gate: GateFn | None = None,
                        extra: GateFn | None = None) -> GateFn:
    """Compose gates for all tracks: phase and track-specific logic.
    - mission track PARTITION: partition_coverage_gate
    - mission/build/research REVIEW/CITE_CHECK: review_scope_gate (optionally with visual_gate)
    - all others: base gate

    NOTE (terra fix #1): app_build's SMOKE gate is no longer composed here. It is BOUND in
    the chart data (`bound_gate: "smoke_verdict"`) and enforced by StateEngine._run_gate
    itself, so a caller that skips make_universal_gate -- or injects any other gate --
    still fails CLOSED at SMOKE. The composed gate here runs as the bound gate's `base`.

    `visual_gate` (if provided) wraps review_scope_gate for rubric-based visual verification.

    `extra` (if provided) runs AFTER all built-in gates and may block a phase that the
    built-in gates would otherwise pass. Used by research_prereq_gate to enforce that
    app_build tasks created via advance_to_app_build carry research evidence at SCAFFOLD.
    """
    def universal_gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
        track = task.get("track")
        if track == "mission" and phase == "PARTITION":
            v = partition_coverage_gate(phase, task, payload, base=base)
        elif phase in ("REVIEW", "CITE_CHECK"):
            sg = review_scope_gate(phase, task, payload, base=base)
            if visual_gate is not None and sg.get("pass"):
                v = visual_gate(phase, task, payload)
            else:
                v = sg
        else:
            v = base(phase, task, payload)
        if extra is not None and v.get("pass"):
            v = extra(phase, task, payload, base=lambda ph, t, p: v)
        return v
    return universal_gate


def review_scope_gate(phase: str, task: dict[str, Any], payload: Any,
                      base: GateFn = default_gate) -> dict[str, Any]:
    """REVIEW-stage scope-vs-intent check (2026-07-07). The task05 / Discord-scrape failure mode:
    a deliverable that is well-formed and self-reported "done" but answers the WRONG ask (scraped
    the customer's own posts instead of other people's tools) -- nothing in the loop compared the
    deliverable's actual target against the original request before it reported done.

    This wraps a base gate and, at the reworkable REVIEW/CITE_CHECK phase, compares the submitted
    review against the task's `intent.seeking`:
      - `scope_check.matches_request is False`  -> FAIL (loop to rework at the requested scope).
      - a `scope_check.delivered` that shares ZERO content-word overlap with the request -> FAIL
        (a gross target mismatch the worker itself surfaced).
      - no `scope_check` at all -> PASS, but the envelope carries a `scope_warning` that names this
        exact failure mode and asks the worker to compare deliverable-vs-ask before closing.

    HONEST LIMIT: token overlap is a coarse deterministic proxy; it catches gross target mismatch
    and a self-declared mismatch, NOT subtle semantic drift ("scraped forums, but the wrong
    forums"). Robust scope verification needs the semantic LLM-judge (Phase 4.4) plugged in as the
    base gate -- this is the structural surfacing that makes the comparison a required review step,
    not the semantic ceiling."""
    verdict = base(phase, task, payload)
    if not verdict.get("pass") or phase not in _SCOPE_CHECK_PHASES:
        return verdict
    seeking = str((task.get("intent") or {}).get("seeking", "")).strip()
    sc = payload.get("scope_check") if isinstance(payload, dict) else None
    if isinstance(sc, dict):
        if sc.get("matches_request") is False:
            return {"pass": False,
                    "reason": (f"scope_check: the deliverable does not match the original request "
                               f"({seeking!r}) -- rework to the requested scope/target, do not close "
                               "a well-formed answer to the wrong ask")}
        delivered = str(sc.get("delivered", ""))
        want, got = _content_tokens(seeking), _content_tokens(delivered)
        if want and got and not (want & got):
            return {"pass": False,
                    "reason": (f"scope_check: the delivered scope ({delivered!r}) shares NO overlap "
                               f"with the request ({seeking!r}) -- verify you built what was asked, "
                               "not an adjacent thing (the task05 / Discord-scrape failure mode)")}
        return {**verdict, "scope_check": "ok"}
    if not seeking:
        return verdict  # no intent to compare against -- nothing to surface
    out = dict(verdict)
    out["scope_warning"] = (
        "REVIEW passed but no scope_check was supplied. Before closing, explicitly compare the "
        "deliverable's actual target/scope against the original request -- pass "
        "scope_check={'delivered': '<what you actually produced>', 'matches_request': true/false}. "
        "A well-formed deliverable that answers the WRONG ask is the task05 / Discord-scrape "
        f"failure mode (asked: {seeking!r}).")
    return out


# ---------------------------------------------------------------------------
# Build-track grounding gate (Plane-2 external-driver enforcement, 2026-07-13).
# ---------------------------------------------------------------------------
# The state engine already makes phase ORDER unskippable: a state only accepts its declared
# advance_tool, and the ONLY path to DONE is CLOSEOUT's advance (state is server-owned; the
# caller never sets it). What order alone does NOT guarantee is that each phase pass carried
# real content -- a weak external model driven through the chart (plane2_driver) could advance
# every phase with EMPTY payloads and reach a hollow "done". This gate is the deterministic
# content floor the Plane-2 driver runs ON TOP of the ordered walk, so the coercion is
# "every phase in order AND grounded", not merely "in order".
#
# terra's Plane-2 endorsement is exactly this: the engine owns every transition and the model
# never names a state, so skipping is not expressible -- this gate closes the residual "advance
# with nothing" gap so reaching DONE requires a closeout grounded in evidence the walk produced.

# The build-track findings phases whose advance (cortex_report_findings) must carry evidence.
_GROUNDED_FINDINGS_PHASES = frozenset({"SEARCH_BRAIN", "RESEARCH"})

# Per-phase required content field for the build track's intermediate phases (the sol@xhigh
# red-team, 2026-07-14 finding #4: PLAN/SPEC/IMPLEMENT accepting `{}` made the walk structurally
# hollow). The advance is refused unless this field carries MEANINGFUL content.
_BUILD_PHASE_FIELD: dict[str, str] = {
    "PLAN": "plan",
    "SPEC": "spec",
    "IMPLEMENT": "patch",
}


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _meaningful(v: Any) -> bool:
    """Deterministic "carries real content" test (sol@xhigh finding #1: `[{}]`, `["   "]`,
    `[None]`, `[False]`, `[[]]` are degenerate, not evidence). A value is meaningful iff it is a
    non-blank string, a real number, or a container with at least one meaningful member. Bools and
    empty/blank containers are NOT meaningful."""
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, dict):
        return any(_meaningful(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_meaningful(x) for x in v)
    return False


def build_grounding_gate(phase: str, task: dict[str, Any], payload: Any,
                         base: GateFn = default_gate) -> dict[str, Any]:
    """Deterministic grounding floor for the build track (the SEARCH_BRAIN -> ... -> CLOSEOUT
    pipeline), used by the Plane-2 external-model driver so a weak model cannot walk the chart
    with empty/degenerate payloads and reach a hollow DONE. Hardened per the sol@xhigh red-team
    (reviewed/plane2-enforcement-sol-xhigh-review-2026-07-14.md).

    Scoped to the build track (other tracks pass straight through `base`). Every content phase
    must carry MEANINGFUL content (`_meaningful`: non-blank string / number / non-empty container
    of same), never just a well-typed empty shell:

    - SEARCH_BRAIN / RESEARCH (cortex_report_findings): `evidence` must be a list with at least
      one meaningful entry. An honest "no corpus coverage" is a legal entry (a non-blank string),
      so this demands a report of what was searched, never a specific finding (STATE-MACHINE.md).
      These accumulate into intent.evidence server-side.
    - PLAN / SPEC / IMPLEMENT: the phase's field (plan / spec / patch) must be meaningful -- an
      empty `{}` no longer advances the walk.
    - REVIEW (cortex_submit_review): the `review` field must be meaningful, AND if a `scope_check`
      is supplied its `matches_request` must be truthy (a falsy value like `0`/`false`/`False`
      declares a mismatch and fails -- finding #4b; the coarser token-overlap scope check still
      runs in review_scope_gate as the composed base). Order-of-composition note: this runs as
      review_scope_gate's `base`, so a hollow/mismatched review is refused before the scope check.
    - CLOSEOUT (cortex_write_closeout): `task` and `result` must be non-empty strings AND the
      task's accumulated intent.evidence must contain a meaningful entry. The Plane-2 driver seeds
      the task WITHOUT caller-supplied evidence (plane2_driver.run_build strips it), so this
      intent.evidence can only have come from the research phases -- the grounding is walk-produced,
      not self-asserted by the closeout payload.

    HONEST LIMIT (narrowed per finding #2/#10): this enforces that each phase's content is PRESENT
    and non-degenerate, and that a closeout COEXISTS with walk-produced evidence. It does NOT verify
    the evidence semantically SUPPORTS the closeout (no citation/derivation relationship), nor that
    the content is true -- that needs the Phase-4.4 LLM-judge plugged in as `base`. Structural floor,
    not semantic ceiling.
    """
    v = base(phase, task, payload)
    if not v.get("pass"):
        return v
    if task.get("track") != "build":
        return v
    p = payload if isinstance(payload, dict) else {}
    if phase in _GROUNDED_FINDINGS_PHASES:
        ev = p.get("evidence")
        if not (isinstance(ev, list) and any(_meaningful(e) for e in ev)):
            return {"pass": False, "code": "UNGROUNDED_FINDINGS",
                    "reason": ("cortex_report_findings needs an 'evidence' list with at least one "
                               "meaningful entry (report what you searched; 'no corpus coverage "
                               "found' is a legal entry). Empty dicts / blank strings are not a "
                               "grounded phase pass.")}
        return {**v, "grounding": "ok"}
    if phase in _BUILD_PHASE_FIELD:
        field = _BUILD_PHASE_FIELD[phase]
        if not _meaningful(p.get(field)):
            return {"pass": False, "code": "UNGROUNDED_PHASE",
                    "reason": f"{phase} needs a meaningful '{field}' (an empty payload does not "
                              "advance the build walk)"}
        return {**v, "grounding": "ok"}
    if phase == "REVIEW":
        if not _meaningful(p.get("review")):
            return {"pass": False, "code": "UNGROUNDED_REVIEW",
                    "reason": "REVIEW needs a meaningful 'review' verdict"}
        sc = p.get("scope_check")
        if isinstance(sc, dict) and "matches_request" in sc and not sc.get("matches_request"):
            return {"pass": False, "code": "SCOPE_MISMATCH",
                    "reason": ("scope_check.matches_request is falsy -- the deliverable does not "
                               "match the request; rework to the requested scope")}
        return v  # let review_scope_gate (the composed wrapper) run its token-overlap check
    if phase == "CLOSEOUT":
        if not _nonempty_str(p.get("task")) or not _nonempty_str(p.get("result")):
            return {"pass": False, "code": "UNGROUNDED_CLOSEOUT",
                    "reason": "closeout needs non-empty 'task' and 'result' strings"}
        evidence = (task.get("intent") or {}).get("evidence") or []
        if not (isinstance(evidence, list) and any(_meaningful(e) for e in evidence)):
            return {"pass": False, "code": "UNGROUNDED_CLOSEOUT",
                    "reason": ("closeout is not grounded: the research phases produced no meaningful "
                               "evidence (intent.evidence). A grounded closeout requires the walk to "
                               "have produced evidence -- you cannot reach DONE with a hollow "
                               "closeout.")}
        return {**v, "grounding": "ok"}
    return v


# ---------------------------------------------------------------------------
# Research-first app_build: Option B — chain build → app_build
# (reviewed/app-build-research-phase-design-2026-07-15.md)
#
# The build track already enforces research-first via build_grounding_gate
# (SEARCH_BRAIN/RESEARCH require meaningful evidence). This function lets a
# driver chain a completed build task into an app_build task, carrying the
# research evidence forward. The research_prereq_gate then enforces at
# SCAFFOLD that the app_build task has that evidence — making research
# structurally unavoidable for chained app_build tasks.
# ---------------------------------------------------------------------------

def research_prereq_gate(phase: str, task: dict[str, Any], payload: Any,
                         base: GateFn = default_gate) -> dict[str, Any]:
    """Gate that enforces research evidence on app_build tasks at SCAFFOLD.

    Composed with the existing gate via make_universal_gate(extra=...). If the
    task's intent has ``researched: True``, the gate checks that
    ``research_evidence`` is a list with at least one meaningful entry. If
    ``researched`` is not set (legacy app_build tasks), the gate passes through
    (backward compatible).

    This closes the "research-first hole": an app_build task created via
    advance_to_app_build carries researched=True, so this gate blocks SCAFFOLD
    unless the build track actually produced evidence. A direct
    create_task(track="app_build") without the flag is unaffected.
    """
    v = base(phase, task, payload)
    if not v.get("pass"):
        return v
    if task.get("track") != "app_build" or phase != "SCAFFOLD":
        return v
    intent = task.get("intent") or {}
    if isinstance(intent, str):
        try:
            intent = json.loads(intent)
        except (json.JSONDecodeError, TypeError):
            intent = {}
    if not intent.get("researched"):
        return v  # legacy path, no research required
    ev = intent.get("research_evidence")
    if not (isinstance(ev, list) and any(_meaningful(e) for e in ev)):
        return {"pass": False, "code": "RESEARCH_PREREQ_NOT_MET",
                "reason": ("app_build task with researched=True requires "
                           "research_evidence with at least one meaningful entry. "
                           "Run the build track first (SEARCH_BRAIN → RESEARCH) and "
                           "chain via advance_to_app_build.")}
    return {**v, "research_prereq": "ok"}


def advance_to_app_build(engine: "StateEngine", build_tid: str, *,
                         actor: str | None = None, lease_s: int = 600) -> str:
    """Create an app_build task chained to a completed build task.

    Copies the build task's accumulated intent.evidence into the app_build
    task's intent as ``research_evidence``, and sets ``researched: True`` so
    that ``research_prereq_gate`` can enforce it at SCAFFOLD.

    The app_build task's ``parent_id`` is set to the build task's id, creating
    a provenance chain from research → build → app_build.

    Raises ``ValueError`` if the build task has not reached DONE.
    """
    env = engine.get(build_tid)
    if env["state"] != "DONE":
        raise ValueError(
            f"build task {build_tid} has not reached DONE (state={env['state']}); "
            "advance_to_app_build requires a completed build task")
    build_intent = env["intent"]
    if isinstance(build_intent, str):
        build_intent = json.loads(build_intent)
    app_intent = dict(build_intent)
    app_intent["research_evidence"] = build_intent.get("evidence", [])
    app_intent["researched"] = True
    app_tid = engine.create_task(app_intent, track="app_build",
                                 parent_id=build_tid, actor=actor, lease_s=lease_s)
    return app_tid


# Track names owned by the engine's built-in charts. Registering a chart under one of
# these names is allowed ONLY when it preserves the built-in's mandatory safety topology
# (terra MED #8: a `SCAFFOLD -> DONE` chart named "app_build" must be a load error, not a
# silent un-gating of SMOKE). Today the topology contract is defined for app_build -- the
# one track with a server-owned deterministic gate bound in its chart data.
_RESERVED_TRACKS = frozenset({
    "build", "research", "assured_build", "assured_research", "mission", "app_build",
})

# The immutable safety spine of the app_build chart: ordered required transitions plus the
# bound-gate marker the engine enforces at SMOKE. Cap values stay tunable (tests shrink
# them); the TOPOLOGY and the gate binding do not.
_APP_BUILD_REQUIRED = (
    ("SCAFFOLD", "next", "SMOKE"),
    ("SMOKE", "next", "SHOW"),
    ("SMOKE", "rework_to", "SCAFFOLD"),
    ("SMOKE", "advance_tool", "cortex_submit_smoke"),
    ("SMOKE", "bound_gate", "smoke_verdict"),
    ("SCAFFOLD", "persist_artifact", True),
    ("SHOW", "next", "CLOSEOUT"),
    ("CLOSEOUT", "next", "DONE"),
    ("CLOSEOUT", "is_closeout", True),
)

_BUILD_RESEARCH_REQUIRED = (
    ("RESEARCH", "next", "RESEARCH_DECISION"),
    ("RESEARCH_DECISION", "advance_tool", "cortex_submit_research_sufficiency"),
    ("RESEARCH_DECISION", "bound_gate", "research_sufficiency"),
    ("RESEARCH_DECISION", "next", "PLAN"),
    ("RESEARCH_DECISION", "rework_to", "RESEARCH"),
)

_RESEARCH_REQUIRED = (
    ("REPORT", "next", "SUFFICIENCY"),
    ("SUFFICIENCY", "advance_tool", "cortex_submit_research_sufficiency"),
    ("SUFFICIENCY", "bound_gate", "research_sufficiency"),
    ("SUFFICIENCY", "next", "DONE"),
    ("SUFFICIENCY", "rework_to", "FETCH"),
)


def _validate_reserved_topology(chart: dict[str, Any]) -> None:
    """Refuse (fail-at-load) any chart registered under a reserved track name that drops
    the built-in's mandatory safety topology. This is what makes the SMOKE requirement
    IMMUTABLE: register_track cannot be used to swap in an app_build chart without the
    SCAFFOLD -> SMOKE -> SHOW -> CLOSEOUT spine and the server-owned bound gate."""
    track = chart.get("track")
    if track == "assured_build":
        required = _BUILD_RESEARCH_REQUIRED
    elif track == "assured_research":
        required = _RESEARCH_REQUIRED
    elif track == "app_build":
        required = _APP_BUILD_REQUIRED
    else:
        return
    states = chart.get("states") or {}
    if track == "app_build" and chart.get("initial") != "SCAFFOLD":
        raise ValueError("app_build chart must start at SCAFFOLD (reserved-track topology)")
    for state, key, want in required:
        got = (states.get(state) or {}).get(key)
        if got != want:
            raise ValueError(
                f"{track} chart must keep {state}.{key} == {want!r} (got {got!r}): "
                "the server-owned bound-gate safety spine is immutable")


def _to_plain(obj: Any) -> Any:
    """Recursively convert an immutable chart (MappingProxyType/tuples, e.g. a previously
    frozen chart being re-validated) back into plain mutable dict/list so validation can
    add engine states / defaults."""
    from types import MappingProxyType
    if isinstance(obj, (dict, MappingProxyType)):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def _deep_freeze(obj: Any) -> Any:
    """Recursively make a chart IMMUTABLE (terra RE-REVIEW #8): every mapping becomes a
    read-only MappingProxyType and every list a tuple, so a caller holding the returned/
    stored chart cannot mutate it (`del chart['states']['SMOKE']['bound_gate']` raises
    TypeError) and therefore cannot disarm a running engine's bound gate."""
    from types import MappingProxyType
    if isinstance(obj, (dict, MappingProxyType)):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


def _validate_chart(chart: dict[str, Any]) -> dict[str, Any]:
    """Load-time referential integrity (the no-drift rule, mini version):
    every `next`/`rework_to` must name a defined state; refuse to load on any
    dangling reference so drift is a load error, not a runtime surprise.
    Reserved track names additionally must preserve their safety topology
    (`_validate_reserved_topology`). Returns a DEEP-FROZEN chart: it never aliases the
    caller's input, and the returned/stored object is immutable (terra #8) so mutating it
    can neither corrupt the built-ins nor disarm a running engine's bound gate."""
    if not isinstance(chart, (dict,)) and not hasattr(chart, "items"):
        raise ValueError("chart must be a dict with a 'states' mapping")
    chart = _to_plain(chart)  # plain mutable copy (also un-freezes a re-validated frozen chart)
    if not isinstance(chart.get("states"), dict):
        raise ValueError("chart must be a dict with a 'states' mapping")
    states = chart["states"]
    for name, spec in _ENGINE_STATES.items():
        states.setdefault(name, dict(spec))
    initial = chart.get("initial")
    if initial not in states:
        raise ValueError(f"chart initial state {initial!r} is not defined")
    for name, spec in states.items():
        if spec.get("terminal") or spec.get("resume"):
            continue
        for ref_key in ("next", "rework_to", "abstain_exit"):
            ref = spec.get(ref_key)
            if ref is not None and ref not in states:
                raise ValueError(f"state {name!r} {ref_key} -> undefined {ref!r}")
        if not spec.get("advance_tool") or not spec.get("next"):
            raise ValueError(f"non-terminal state {name!r} needs advance_tool + next")
    chart.setdefault("rework_cap", 2)
    chart.setdefault("esc_cap", 2)
    chart.setdefault("track", "build")
    if chart["track"] in _RESERVED_TRACKS:
        _validate_reserved_topology(chart)
    return _deep_freeze(chart)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY,
  parent_id TEXT,
  track TEXT NOT NULL,
  chart_ver TEXT,
  state TEXT NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,
  intent TEXT NOT NULL,
  lease_owner TEXT,
  lease_until INTEGER,
  lease_s INTEGER NOT NULL DEFAULT 600,
  esc_level INTEGER NOT NULL DEFAULT 0,
  rework_count INTEGER NOT NULL DEFAULT 0,
  closeout_written INTEGER NOT NULL DEFAULT 0,
  stalled_from TEXT,
  created_at INTEGER,
  updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS event(
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  tool TEXT,
  actor TEXT,
  payload TEXT,
  result TEXT,
  idem_key TEXT,
  ts INTEGER,
  PRIMARY KEY(task_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS event_idem ON event(task_id, idem_key);
CREATE TABLE IF NOT EXISTS claim(
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  task_id TEXT NOT NULL,
  until INTEGER,
  ts INTEGER,
  PRIMARY KEY(kind, key)
);
CREATE TABLE IF NOT EXISTS gate_verdict(
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  phase TEXT NOT NULL,
  verdict TEXT NOT NULL,
  ts INTEGER
);
"""


class StateEngine:
    """Event-sourced, single-writer-per-task state machine over SQLite.

    Contract (fixed by tests/test_state_engine.py):
        eng = StateEngine(db_path, chart=None, gate=None)
        tid = eng.create_task(intent, track="build", lease_s=600)
        env = eng.step(tid, tool, payload=None, seq=..., idem_key=None, actor=None)
        row = eng.get(tid)
        res = eng.acquire_claims(tid, claims, seq)
        ids = eng.reap(now_ts=None)
    """

    def __init__(self, db_path: str, chart: dict[str, Any] | None = None,
                 gate: GateFn | None = None,
                 workspace: str | None = None) -> None:
        self._gate: GateFn = gate or default_gate
        # Workspace root for server-owned receipt lookups (terra fix #1). None resolves
        # via cortex_core.config.resolve_workspace at lookup time.
        self._workspace = workspace
        # First-party tracks are registered built-in: "build" (forced pipeline),
        # "research" (deep-research phases), "mission" (orchestrator over parallel workers),
        # and "app_build" (the vague-build chunk chart) -- plus anything added via the
        # module-level register_track(). A caller-supplied `chart` can add or override.
        self._charts: dict[str, dict[str, Any]] = {
            name: _validate_chart(c) for name, c in _TRACKS.items()
        }
        if chart is not None:
            loaded = _validate_chart(chart)
            self._charts[loaded["track"]] = loaded
        # One connection + an RLock for in-process thread safety; cross-process
        # writers are serialized by BEGIN IMMEDIATE itself (+ busy_timeout).
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, isolation_level=None,
                                   check_same_thread=False, timeout=30.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        # NOTE: executescript() manages its own transaction (implicit COMMIT),
        # so it must not run inside _txn()'s BEGIN IMMEDIATE.
        with self._lock:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def register_track(self, chart: dict[str, Any]) -> dict[str, Any]:
        """Register (or override) a chart on THIS engine instance -- validated
        fail-at-load, data only. Mirrors the module-level register_track()."""
        loaded = _validate_chart(chart)
        with self._lock:
            self._charts[loaded["track"]] = loaded
        return loaded

    # -- transaction discipline ---------------------------------------------

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        """One superstep = one BEGIN IMMEDIATE. The write lock is taken BEFORE
        the task row is read -- that ordering is the whole seq-fencing defense:
        a concurrent loser blocks here, then reads the already-bumped seq."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    # -- helpers --------------------------------------------------------------

    def _chart(self, track: str) -> dict[str, Any]:
        return self._charts[track]

    def _legal_tools(self, chart: dict[str, Any], state: str) -> list[str]:
        spec = chart["states"][state]
        if spec.get("terminal"):
            return []
        if spec.get("resume"):
            return ["cortex_resume"]
        return [spec["advance_tool"], *spec.get("extra_tools", [])]

    def _fetch_task(self, task_id: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown task_id: {task_id}")
        return row

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        chart = self._chart(row["track"])
        intent = json.loads(row["intent"])
        return {
            "task_id": row["id"],
            "run_id": intent.get("run_id"),
            "track": row["track"],
            "state": row["state"],
            "seq": row["seq"],
            "intent": intent,
            "parent_id": row["parent_id"],
            "lease_owner": row["lease_owner"],
            "lease_until": row["lease_until"],
            "esc_level": row["esc_level"],
            "rework_count": row["rework_count"],
            "closeout_written": bool(row["closeout_written"]),
            "legal_tools": self._legal_tools(chart, row["state"]),
            "instruction": chart["states"][row["state"]].get("instruction", ""),
            "updated_at": row["updated_at"],
        }

    def _envelope(self, chart: dict[str, Any], *, ok: bool, task_id: str,
                  state: str, seq: int, lease_until: int | None, now: int,
                  **extra: Any) -> dict[str, Any]:
        spec = chart["states"][state]
        env: dict[str, Any] = {
            "ok": ok,
            "task_id": task_id,
            "state": state,
            "seq": seq,
            "legal_tools": self._legal_tools(chart, state),
            "instruction": extra.pop("instruction", None) or spec.get("instruction", ""),
            "lease_expires_in_s": max(0, (lease_until or now) - now),
        }
        env.update(extra)
        # Round-trip through JSON so the returned dict and the stored/replayed
        # idempotent copy are byte-for-byte the same structure.
        return json.loads(json.dumps(env, default=str))

    def _append_event(self, task_id: str, seq: int, kind: str, *, tool: str | None,
                      actor: str | None, payload: dict[str, Any],
                      result: dict[str, Any] | None, idem_key: str | None,
                      now: int, rationale: str | None = None) -> None:
        """Every applied superstep appends one event snapshotting its fold
        target in payload["to_state"] -- replay() rebuilds state from these.
        PK (task_id, seq) is the last-ditch double-apply backstop.

        `rationale` (2026-07-07) is an OPTIONAL "why this step" trace, folded into the stored
        payload JSON (no schema migration needed -- the column is already free-form TEXT) so
        it rides along with the event it explains and is retrievable via `event_history()`.
        Omitted (None) leaves the stored payload byte-identical to before this field existed."""
        if rationale is not None:
            payload = {**payload, "rationale": rationale}
        self._db.execute(
            "INSERT INTO event(task_id, seq, kind, tool, actor, payload, result,"
            " idem_key, ts) VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, seq, kind, tool, actor, json.dumps(payload, default=str),
             json.dumps(result, default=str) if result is not None else None,
             idem_key, now),
        )

    # -- API: create ----------------------------------------------------------

    def create_task(self, intent: dict[str, Any], track: str = "build",
                    lease_s: int = 600, parent_id: str | None = None,
                    actor: str | None = None) -> str:
        """Create a task in the track's initial state at seq=0. The intent
        record ("what am I seeking") is first-class state data -- it is what
        survives a reap and rides along to a replacement worker."""
        if not isinstance(intent, dict):
            raise ValueError("intent must be a dict")
        intent = dict(intent)
        chart = self._chart(track)  # KeyError loudly on unknown track
        task_id = "t_" + uuid.uuid4().hex
        now = int(time.time())
        lease_s = int(lease_s)
        with self._txn():
            if parent_id is not None:
                parent = self._fetch_task(parent_id)
                parent_intent = json.loads(parent["intent"])
                intent["run_id"] = parent_intent.get("run_id") or ("run_" + uuid.uuid4().hex)
            else:
                # Root run correlation is server-generated. A caller may not choose a run identity
                # that later makes unrelated MCP/native/OTel/evaluator records look joined.
                intent["run_id"] = "run_" + uuid.uuid4().hex
            self._db.execute(
                "INSERT INTO task(id, parent_id, track, chart_ver, state, seq,"
                " intent, lease_owner, lease_until, lease_s, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, parent_id, track, str(chart.get("version", "")),
                 chart["initial"], 0, json.dumps(intent, default=str), actor,
                 now + lease_s, lease_s, now, now),
            )
            self._append_event(task_id, 0, "created", tool=None, actor=actor,
                               payload={"to_state": chart["initial"], "intent": intent},
                               result=None, idem_key=None, now=now)
        return task_id

    # -- API: supervisor (STAGE 2 -- the "state agents" boundary layer) --------

    # -- supervisor internals (all run INSIDE an already-open superstep txn) ----

    def _insert_task_row(self, tid: str, parent: str | None, intent: dict[str, Any],
                         *, track: str, cver: str, lease_s: int, actor: str | None,
                         now: int, initial: str,
                         claims: list[tuple[str, str]] | None = None) -> None:
        """Low-level: one task row + its seq-0 `created` event. No txn of its own --
        the caller owns the transaction (spawn_mission / dispatch_workers).

        `claims` (S4a evidence durability, sol #5): the disjoint claims granted to this worker
        are recorded IN the created event, so post-run S4 verification can prove which slice each
        worker held even after the terminal DONE transition DELETEs the live claim rows."""
        intent = dict(intent)
        if parent is not None:
            parent_row = self._fetch_task(parent)
            parent_intent = json.loads(parent_row["intent"])
            intent["run_id"] = parent_intent.get("run_id") or ("run_" + uuid.uuid4().hex)
        else:
            intent["run_id"] = "run_" + uuid.uuid4().hex
        payload: dict[str, Any] = {"to_state": initial, "intent": intent}
        if claims:
            payload["claims"] = [{"kind": k, "key": key} for k, key in claims]
        self._db.execute(
            "INSERT INTO task(id, parent_id, track, chart_ver, state, seq, intent,"
            " lease_owner, lease_until, lease_s, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, parent, track, cver, initial, 0, json.dumps(intent, default=str),
             actor, now + lease_s, lease_s, now, now))
        self._append_event(tid, 0, "created", tool=None, actor=actor,
                           payload=payload, result=None, idem_key=None, now=now)

    def _claim_conflicts(self, workers: list[dict[str, Any]], now: int
                         ) -> tuple[list[list[tuple[str, str]]], list[dict[str, Any]]]:
        """SELECT-only, side-effect-free MECE check across ALL workers at once (runs
        before any insert so a conflict leaves no partial state). Returns
        (per_worker_claim_sets, conflicts). A conflict is any glob-overlap (both
        directions) with a live foreign claim OR with an earlier worker in this batch."""
        pending: list[tuple[str, str]] = []
        conflicts: list[dict[str, Any]] = []
        per_worker: list[list[tuple[str, str]]] = []
        for w in workers:
            wc = sorted({(str(c["kind"]), str(c["key"])) for c in (w.get("claims") or [])})
            per_worker.append(wc)
            for kind, key in wc:
                for other in self._db.execute("SELECT key, until FROM claim WHERE kind=?", (kind,)):
                    if other["until"] is not None and other["until"] < now:
                        continue  # expired -- free to take
                    if (key == other["key"] or fnmatchcase(key, other["key"])
                            or fnmatchcase(other["key"], key)):
                        conflicts.append({"kind": kind, "key": key, "held_key": other["key"]})
                for pk, pkey in pending:
                    if pk == kind and (key == pkey or fnmatchcase(key, pkey) or fnmatchcase(pkey, key)):
                        conflicts.append({"kind": kind, "key": key, "worker_conflict": pkey})
                pending.append((kind, key))
        return per_worker, conflicts

    def _insert_worker_children(self, workers: list[dict[str, Any]],
                                per_worker: list[list[tuple[str, str]]], *,
                                parent_id: str, track: str, cver: str, lease_s: int,
                                actor: str | None, now: int, initial: str) -> list[str]:
        """Insert each worker as a child task under parent_id and grant its (already
        conflict-checked) claims. Caller must have run `_claim_conflicts` first.

        HETEROGENEOUS TRACKS (2026-07-15, run_mission PARTITION seam): a worker may declare its
        OWN `track` (e.g. an app_build receipt-bearing slice), otherwise it inherits the caller's
        default `track`. This is what lets one mission dispatch children on DIFFERENT charts (the
        terra decomposer's heterogeneous split). `track`/`cver`/`initial` remain the default for
        every worker that does NOT name a track, so existing homogeneous callers (spawn_mission,
        dispatch_workers, the build-track partition) are byte-for-byte unchanged."""
        worker_ids: list[str] = []
        for w, wc in zip(workers, per_worker, strict=True):
            wtrack = w.get("track") or track
            if wtrack == track:
                wcver, winitial = cver, initial
            else:
                wchart = self._chart(wtrack)  # KeyError loudly on an unregistered track
                wcver, winitial = str(wchart.get("version", "")), wchart["initial"]
            wid = "t_" + uuid.uuid4().hex
            self._insert_task_row(wid, parent_id, w.get("intent", {}), track=wtrack,
                                  cver=wcver, lease_s=lease_s, actor=actor, now=now,
                                  initial=winitial, claims=wc)
            for kind, key in wc:
                self._db.execute("INSERT INTO claim(kind, key, task_id, until, ts)"
                                 " VALUES(?,?,?,?,?)", (kind, key, wid, None, now))
            worker_ids.append(wid)
        return worker_ids

    def _materialize_partition(self, row: sqlite3.Row, now: int,
                               actor: str | None) -> dict[str, Any]:
        """S4a: create the mission's build-track worker children from its server-persisted,
        PARTITION-validated manifest -- INSIDE the caller's already-open DISPATCH-advance txn
        (so it commits or rolls back atomically WITH the DISPATCH->MONITOR transition).

        Fails CLOSED (inserts nothing, returns ok:False) on: no persisted partition, a claimless
        worker (a worker with no claim can't hold a disjoint slice -> fake MECE), or any claim
        overlap. On success returns the created worker_ids; the DISPATCH advance then commits."""
        intent = json.loads(row["intent"])
        manifest = intent.get("partition")
        if not isinstance(manifest, list) or not manifest:
            return {"ok": False, "code": "NO_PARTITION",
                    "reason": "no PARTITION manifest persisted; submit cortex_submit_partition first"}
        for w in manifest:
            if not isinstance(w, dict) or not (w.get("claims")):
                return {"ok": False, "code": "CLAIMLESS_WORKER",
                        "reason": "every dispatched worker must declare >=1 claim "
                                  "(a claimless worker cannot own a disjoint slice)"}
        per_worker, conflicts = self._claim_conflicts(manifest, now)
        if conflicts:
            return {"ok": False, "code": "CLAIM_CONFLICT",
                    "reason": f"partition claims overlap, cannot dispatch: {conflicts}"}
        build = self._chart("build")
        worker_ids = self._insert_worker_children(
            manifest, per_worker, parent_id=row["id"], track="build",
            cver=str(build.get("version", "")), lease_s=row["lease_s"], actor=actor,
            now=now, initial=build["initial"])
        return {"ok": True, "worker_ids": worker_ids}

    # -- API: supervisor (STAGE 2 -- the "state agents" boundary layer) --------

    def spawn_mission(self, mission_intent: dict[str, Any],
                      workers: list[dict[str, Any]], *, track: str = "build",
                      lease_s: int = 600, actor: str | None = None) -> dict[str, Any]:
        """Create a MISSION task and one worker child-task per entry, atomically acquiring each
        worker's DISJOINT claims -- all-or-nothing in ONE transaction. If any worker's claim
        overlaps an existing live claim OR another worker's (glob both directions), NOTHING is
        created (no partial mission, no hold-and-wait -> no deadlock, structurally). This is the
        supervisor partition: boundaries between parallel workers are exclusive by construction.

        `workers` = [{"intent": {...}, "claims": [{"kind","key"}, ...]}, ...].
        Returns {"ok": True, "mission_id", "worker_ids"} or
        {"ok": False, "code": "CLAIM_CONFLICT", "conflicts": [...]}.

        NOTE (topology, 2026-07-14): this puts the mission row AND its workers on the SAME
        `track`. When the supervisor is itself a MISSION_TRACK task (its own mission chart),
        use `dispatch_workers(mission_id, ...)` instead so the mission stays on its chart while
        the workers run their own build chart under that EXACT parent -- see sol #6 / S4a.
        """
        if not workers:
            raise ValueError("workers must be a non-empty list")
        chart = self._chart(track)
        cver = str(chart.get("version", ""))
        initial = chart["initial"]
        now = int(time.time())
        lease_s = int(lease_s)

        with self._txn():
            # 1) overlap check across ALL workers first (SELECT-only -> no partial state).
            per_worker, conflicts = self._claim_conflicts(workers, now)
            if conflicts:  # commits an empty txn (nothing was inserted) -- all-or-nothing
                return {"ok": False, "code": "CLAIM_CONFLICT", "conflicts": conflicts}
            # 2) all disjoint -> create mission + workers + claims (same txn).
            mission_id = "t_" + uuid.uuid4().hex
            self._insert_task_row(mission_id, None, {**mission_intent, "role": "mission"},
                                  track=track, cver=cver, lease_s=lease_s, actor=actor,
                                  now=now, initial=initial)
            worker_ids = self._insert_worker_children(
                workers, per_worker, parent_id=mission_id, track=track, cver=cver,
                lease_s=lease_s, actor=actor, now=now, initial=initial)
            return {"ok": True, "mission_id": mission_id, "worker_ids": worker_ids}

    def dispatch_workers(self, mission_id: str, workers: list[dict[str, Any]], *,
                         worker_track: str = "build", lease_s: int = 600,
                         actor: str | None = None) -> dict[str, Any]:
        """Atomically create N (>=1) worker child-tasks under an EXISTING mission task,
        each on `worker_track` (default "build"), acquiring their DISJOINT claims
        all-or-nothing in ONE transaction -- the sol #6 / S4a topology fix.

        This is the low-level in-process primitive. The GOVERNED S4 path is the mission-chart
        DISPATCH advance (`cortex_dispatch_mission` -> `_materialize_partition`), which runs this
        same claim logic INSIDE the DISPATCH->MONITOR superstep, bound to the PARTITION-validated
        persisted manifest and fenced by the mission's state/seq. That is what connecting agents
        drive; this method is not exposed as an MCP tool.

        This is the piece `spawn_mission` could not express: it always minted a fresh
        mission row and forced ONE `track` onto both the mission and its workers, so a
        MISSION_TRACK supervisor could not own build-track children (mission_status found
        no children -> `all_done` never true; sol #6, state_engine.py:921 / mcp.py:1291).
        Here the mission keeps its own chart (created separately, e.g. track="mission")
        and the workers run their own build chart under `parent_id == mission_id`, so
        `mission_status(mission_id)` sees them and the MONITOR->MERGE gate can fire.

        Disjointness is enforced exactly as in spawn_mission (glob-overlap check across the
        whole batch AND against live foreign claims, before any insert). A single stale/failed
        worker that later ABANDONs releases only its own claim (terminal `_release_claims`),
        so this can be re-called for that freed slice to spawn a replacement without touching
        its siblings' claims.

        `workers` = [{"intent": {...}, "claims": [{"kind","key"}, ...]}, ...].
        Returns {"ok": True, "mission_id", "worker_ids"},
        {"ok": False, "code": "CLAIM_CONFLICT", "conflicts": [...]}, or
        {"ok": False, "code": "UNKNOWN_MISSION", ...} if mission_id does not exist.
        """
        if not workers:
            raise ValueError("workers must be a non-empty list")
        if any(not isinstance(w, dict) or not w.get("claims") for w in workers):
            return {"ok": False, "code": "CLAIMLESS_WORKER",
                    "reason": "every worker must declare >=1 claim (a claimless worker cannot "
                              "own a disjoint slice)", "mission_id": mission_id}
        chart = self._chart(worker_track)  # KeyError loudly on unknown track
        cver = str(chart.get("version", ""))
        initial = chart["initial"]
        now = int(time.time())
        lease_s = int(lease_s)

        with self._txn():
            try:
                self._fetch_task(mission_id)  # parent must exist (same txn -> no TOCTOU)
            except KeyError:
                return {"ok": False, "code": "UNKNOWN_MISSION",
                        "reason": f"no such mission task: {mission_id}"}
            per_worker, conflicts = self._claim_conflicts(workers, now)
            if conflicts:  # all-or-nothing: nothing inserted
                return {"ok": False, "code": "CLAIM_CONFLICT", "conflicts": conflicts,
                        "mission_id": mission_id}
            worker_ids = self._insert_worker_children(
                workers, per_worker, parent_id=mission_id, track=worker_track, cver=cver,
                lease_s=lease_s, actor=actor, now=now, initial=initial)
            return {"ok": True, "mission_id": mission_id, "worker_ids": worker_ids}

    def mission_status(self, mission_id: str) -> dict[str, Any]:
        """The mission's worker states -- what the supervisor monitors before it MERGEs.

        S4a (sol #5): also surfaces the authoritative `cohort` (the worker_ids the DISPATCH
        superstep atomically created, persisted on the mission) and `cohort_consistent` (the
        live children == that cohort). Post-run S4 verification binds `all_done` to that cohort
        instead of "any direct child," so a stray/extra child can't fake or poison completion."""
        with self._lock:
            m = self._db.execute("SELECT intent FROM task WHERE id=?", (mission_id,)).fetchone()
            rows = self._db.execute(
                "SELECT id, state, seq FROM task WHERE parent_id=? ORDER BY created_at",
                (mission_id,)).fetchall()
        workers = [{"task_id": r["id"], "state": r["state"], "seq": r["seq"]} for r in rows]
        done = sum(1 for w in workers if w["state"] == "DONE")
        cohort = (json.loads(m["intent"]).get("worker_cohort") if m else None) or []
        child_ids = {w["task_id"] for w in workers}
        return {"mission_id": mission_id, "workers": workers, "n": len(workers),
                "done": done, "all_done": bool(workers) and done == len(workers),
                "cohort": cohort,
                "cohort_consistent": bool(cohort) and set(cohort) == child_ids}

    # -- API: read ------------------------------------------------------------

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._fetch_task(task_id))

    def event_history(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Read-path for the event log, oldest-first, most-recent `limit` events -- the
        retrieval side of the optional `rationale` trace field (2026-07-07): `step(...,
        rationale=...)` writes it into the event payload, and without this method it would
        be write-only/orphaned. Each entry: {seq, kind, tool, actor, ts, to_state, rationale}
        -- `rationale` is None for every event that didn't supply one (including all events
        appended before this field existed, byte-for-byte backward compatible)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, kind, tool, actor, payload, ts FROM event WHERE task_id=?"
                " ORDER BY seq DESC LIMIT ?", (task_id, int(limit))).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            payload = json.loads(r["payload"] or "{}")
            out.append({
                "seq": r["seq"], "kind": r["kind"], "tool": r["tool"], "actor": r["actor"],
                "ts": r["ts"], "to_state": payload.get("to_state"),
                "rationale": payload.get("rationale"),
            })
        out.reverse()
        return out

    def replay(self, task_id: str) -> dict[str, Any]:
        """Rebuild (state, seq) from the event log alone -- the event-sourcing
        honesty check. Each event snapshots its fold target, so the fold is
        'last event wins'; disagreement with the task row means corruption."""
        with self._lock:
            last = self._db.execute(
                "SELECT seq, payload FROM event WHERE task_id=?"
                " ORDER BY seq DESC LIMIT 1", (task_id,)).fetchone()
        if last is None:
            raise KeyError(f"no events for task_id: {task_id}")
        payload = json.loads(last["payload"] or "{}")
        return {"state": payload.get("to_state"), "seq": last["seq"]}

    # -- API: step (the superstep) ---------------------------------------------

    def step(self, task_id: str, tool: str, payload: Any = None,
             seq: int | None = None, idem_key: str | None = None,
             actor: str | None = None, rationale: str | None = None) -> dict[str, Any]:
        """One tool call = one atomic superstep: fence seq -> validate legality
        against the chart -> gate -> append event -> fold state -> commit ->
        render envelope. Refusals apply NOTHING and return guidance.

        `rationale` (2026-07-07, optional): a free-text "why this step" trace persisted
        alongside the appended event (see `_append_event`) and retrievable via
        `event_history()`. Omitted -> behaves exactly as before this field existed
        (no event/envelope shape change on the None path)."""
        if seq is None:
            raise ValueError("seq is required (optimistic-concurrency fence)")
        seq = int(seq)  # forgiving parse: coerce "3" -> 3
        if payload is not None and not isinstance(payload, dict):
            payload = {"value": payload}  # forgiving parse: wrap scalars
        now = int(time.time())

        with self._txn():
            row = self._fetch_task(task_id)
            chart = self._chart(row["track"])

            # 1) Idempotency FIRST (before the fence): a retried duplicate is
            # stale by construction and must replay, not be rejected.
            if idem_key is not None:
                hit = self._db.execute(
                    "SELECT result FROM event WHERE task_id=? AND idem_key=?",
                    (task_id, idem_key)).fetchone()
                if hit is not None and hit["result"]:
                    return json.loads(hit["result"])

            # 2) Seq fence: stale caller applies nothing, gets fresh state.
            if seq != row["seq"]:
                return self._refuse(chart, row, now, code="REJECTED_STALE",
                                    reason=f"stale seq {seq}; current is {row['seq']}",
                                    instruction="Resync from this envelope's "
                                                "state/seq, then retry.")

            spec = chart["states"][row["state"]]
            legal = self._legal_tools(chart, row["state"])

            # 3) Legality gate: per-state legal tools, refusal = guidance.
            if spec.get("terminal"):
                return self._refuse(chart, row, now, code="ILLEGAL_IN_STATE",
                                    reason=f"task is terminal ({row['state']})")
            if tool not in legal:
                do_instead = {"tool": "cortex_resume" if spec.get("resume")
                              else spec["advance_tool"]}
                return self._refuse(chart, row, now, code="ILLEGAL_IN_STATE",
                                    reason=f"{tool} is not legal in {row['state']}",
                                    do_instead=do_instead)

            # 4) Boundary check: payload "writes" that fall inside ANOTHER
            # task's live claim are refused at write-time.
            violation = self._boundary_violation(task_id, payload, now)
            if violation:
                return self._refuse(chart, row, now, code="BOUNDARY_VIOLATION",
                                    reason=f"write outside your claims: {violation}",
                                    conflicts=violation)

            # 5) Dispatch: resume, in-phase tool, or the advance tool.
            if spec.get("resume"):
                return self._apply_resume(chart, row, payload, idem_key, actor, now, rationale)
            if tool != spec["advance_tool"]:
                return self._apply_note(chart, row, tool, payload, idem_key, actor, now, rationale)
            return self._apply_advance(chart, row, spec, tool, payload,
                                       idem_key, actor, now, rationale)

    # -- step internals (all run inside the superstep transaction) -------------

    def _refuse(self, chart: dict[str, Any], row: sqlite3.Row, now: int, *,
                code: str, reason: str, **extra: Any) -> dict[str, Any]:
        """Refusal = guidance envelope, ZERO writes: state and seq unchanged."""
        return self._envelope(chart, ok=False, task_id=row["id"],
                              state=row["state"], seq=row["seq"],
                              lease_until=row["lease_until"], now=now,
                              code=code, reason=reason, **extra)

    def _renew(self, row: sqlite3.Row, *, state: str, seq: int, now: int,
               actor: str | None, rework_count: int | None = None,
               esc_level: int | None = None, closeout_written: int | None = None,
               stalled_from: str | None = ...) -> int:
        """Fold the superstep into the task row; every applied step is
        implicitly a heartbeat, so the lease renews here too."""
        lease_until = now + row["lease_s"]
        self._db.execute(
            "UPDATE task SET state=?, seq=?, lease_until=?, lease_owner=?,"
            " rework_count=?, esc_level=?, closeout_written=?, stalled_from=?,"
            " updated_at=? WHERE id=?",
            (state, seq, lease_until, actor if actor is not None else row["lease_owner"],
             rework_count if rework_count is not None else row["rework_count"],
             esc_level if esc_level is not None else row["esc_level"],
             closeout_written if closeout_written is not None else row["closeout_written"],
             row["stalled_from"] if stalled_from is ... else stalled_from,
             now, row["id"]),
        )
        return lease_until

    def _apply_note(self, chart: dict[str, Any], row: sqlite3.Row, tool: str,
                    payload: Any, idem_key: str | None, actor: str | None,
                    now: int, rationale: str | None = None) -> dict[str, Any]:
        """A legal in-phase tool (e.g. cortex_search in SEARCH_BRAIN): recorded
        as an event, seq bumps, state stays."""
        new_seq = row["seq"] + 1
        lease_until = self._renew(row, state=row["state"], seq=new_seq, now=now,
                                  actor=actor)
        env = self._envelope(chart, ok=True, task_id=row["id"], state=row["state"],
                             seq=new_seq, lease_until=lease_until, now=now)
        if rationale is not None:
            env["rationale"] = rationale
        self._append_event(row["id"], new_seq, "note", tool=tool, actor=actor,
                           payload={"to_state": row["state"], "tool_payload": payload},
                           result=env, idem_key=idem_key, now=now, rationale=rationale)
        return env

    def _apply_resume(self, chart: dict[str, Any], row: sqlite3.Row, payload: Any,
                      idem_key: str | None, actor: str | None,
                      now: int, rationale: str | None = None) -> dict[str, Any]:
        """STALLED -> the pre-stall state (history-state restore), fresh lease,
        intent record intact -- the replacement resumes, not restarts."""
        target = row["stalled_from"] or chart["initial"]
        new_seq = row["seq"] + 1
        lease_until = self._renew(row, state=target, seq=new_seq, now=now,
                                  actor=actor, stalled_from=None)
        env = self._envelope(chart, ok=True, task_id=row["id"], state=target,
                             seq=new_seq, lease_until=lease_until, now=now,
                             resumed_from="STALLED")
        if rationale is not None:
            env["rationale"] = rationale
        self._append_event(row["id"], new_seq, "resume", tool="cortex_resume",
                           actor=actor,
                           payload={"to_state": target, "tool_payload": payload},
                           result=env, idem_key=idem_key, now=now, rationale=rationale)
        return env

    def _run_gate(self, row: sqlite3.Row, payload: Any) -> dict[str, Any]:
        """Run the pluggable gate FAIL-CLOSED: an exception or a malformed
        verdict counts as a fail (a broken gate must not wave work through).

        terra fix #1 (structural, not conventional): when the chart state declares
        `bound_gate: "smoke_verdict"`, the engine ITSELF wraps the configured gate in
        smoke_verdict_gate -- server-owned receipt validation runs no matter which gate
        the engine was constructed with. A default-gate StateEngine on app_build
        therefore fails CLOSED at SMOKE instead of open, and no caller wiring can
        un-bind the deterministic gate. The injected/configured gate still runs as
        `base` (its own refusals still count)."""
        try:
            chart = self._chart(row["track"])
            spec = chart["states"].get(row["state"], {})
            if spec.get("bound_gate") == "smoke_verdict":
                out = smoke_verdict_gate(row["state"], self._public(row), payload,
                                         base=self._gate, workspace=self._workspace)
            elif spec.get("bound_gate") == "research_sufficiency":
                out = research_sufficiency_gate(
                    row["state"], self._public(row), payload,
                    base=self._gate, workspace=self._workspace,
                )
            else:
                out = self._gate(row["state"], self._public(row), payload)
        except Exception as exc:  # noqa: BLE001 -- gate is arbitrary plugin code
            return {"pass": False, "reason": f"gate raised: {exc!r}"}
        if not isinstance(out, dict) or not isinstance(out.get("pass"), bool):
            return {"pass": False, "reason": "gate returned a malformed verdict"}
        return out

    def _apply_advance(self, chart: dict[str, Any], row: sqlite3.Row,
                       spec: dict[str, Any], tool: str, payload: Any,
                       idem_key: str | None, actor: str | None,
                       now: int, rationale: str | None = None) -> dict[str, Any]:
        """The phase's report tool was submitted: evaluate exit criteria via
        the gate, then advance / rework / escalate / abandon."""
        verdict = self._run_gate(row, payload)

        # S4a (sol #6, 2026-07-14): the mission DISPATCH advance ATOMICALLY materializes its
        # worker children -- IN THIS SAME superstep txn -- from the server-persisted, PARTITION-
        # gate-validated manifest (NOT a fresh caller-supplied list -> no claim/partition drift).
        # Bound in the engine here (not the MCP wrapper) so EVERY path that advances DISPATCH,
        # generic cortex_run_step included, fails CLOSED on a claim conflict instead of leaving
        # orphan children under the mission (the non-atomic MCP-first-then-step exploit sol
        # flagged). A conflict/claimless/missing-partition turns the DISPATCH advance into a gate
        # FAIL: nothing is inserted, the mission stays at DISPATCH for the supervisor to re-partition.
        dispatched: list[str] = []
        if verdict.get("pass") and row["track"] == "mission" and row["state"] == "DISPATCH":
            dres = self._materialize_partition(row, now, actor)
            if dres["ok"]:
                dispatched = dres["worker_ids"]
            else:
                verdict = {"pass": False, "code": dres["code"], "reason": dres["reason"]}

        new_seq = row["seq"] + 1
        self._db.execute(
            "INSERT INTO gate_verdict(task_id, seq, phase, verdict, ts)"
            " VALUES(?,?,?,?,?)",
            (row["id"], new_seq, row["state"], json.dumps(verdict, default=str), now))

        # Evidence submitted with a report is appended to the intent record --
        # transitions are evidence-gated, never self-reported-only. Mission contract
        # (at INTAKE phase) is also stored in intent for later phases to reference.
        intent = json.loads(row["intent"])
        if isinstance(payload, dict):
            if isinstance(payload.get("evidence"), list):
                intent.setdefault("evidence", []).extend(payload["evidence"])
            if row["state"] == "INTAKE" and "contract" in payload:
                intent["contract"] = payload["contract"]
            # S4a: persist the PARTITION-validated worker manifest (owns_units + intent + claims)
            # so DISPATCH materializes EXACTLY the gate-checked partition, not a re-supplied list.
            if verdict["pass"] and row["state"] == "PARTITION" and isinstance(payload.get("workers"), list):
                intent["partition"] = payload["workers"]
        # S4a: record the authoritative worker cohort on the mission so post-run S4 verification
        # (and mission_status) can bind `all_done` to exactly the dispatched children.
        if dispatched:
            intent["worker_cohort"] = dispatched

        # terra RE-REVIEW #1: on the SCAFFOLD advance, persist a SERVER-COMPUTED digest of
        # the submitted artifact dir + its checks onto the task. SMOKE then requires the
        # verdict receipt was minted over exactly this artifact + these checks -- a receipt
        # for a different artifact/checks cannot pass this task's SMOKE. Computed here (not
        # trusted from the payload) so the binding is the server's, not the caller's.
        if verdict["pass"] and spec.get("persist_artifact") and isinstance(payload, dict):
            from cortex_core import receipts
            app_dir = payload.get("app_dir")
            intent["scaffold_artifact_digest"] = (
                receipts.digest_dir(app_dir) if app_dir else None)
            intent["required_checks_digest"] = receipts.digest_checks(payload.get("checks"))

        # GAP B2: abstain-default review exit. A verdict that "passes" but is NOT backed by
        # a deterministic oracle is fake certainty; if this review phase declares an
        # `abstain_exit` and the task is AUTO with no human on call, default to ABSTAIN +
        # flag-for-human (a logged handled success) rather than emit the unverified pass.
        abstain_target = spec.get("abstain_exit")
        if abstain_target and verdict.get("outcome") == "ABSTAIN":
            return self._apply_abstain(chart, row, spec, tool, payload, verdict,
                                       abstain_target, idem_key, actor, now, rationale)
        if (abstain_target and verdict.get("pass")
                and not _verdict_is_oracle_backed(verdict)
                and _no_human_available(intent)):
            return self._apply_abstain(chart, row, spec, tool, payload, verdict,
                                       abstain_target, idem_key, actor, now, rationale)

        if verdict["pass"]:
            to_state = spec["next"]
            intent["phase"] = to_state
            closeout = 1 if spec.get("is_closeout") else None
            # Reset the rework counter only when the REWORKABLE state itself
            # passes (the loop is genuinely exited) -- resetting on every
            # advance would let the IMPLEMENT->REVIEW hop launder the count
            # and the rework cap would never trip (livelock).
            rework_reset = 0 if "rework_to" in spec else None
            lease_until = self._renew(row, state=to_state, seq=new_seq, now=now,
                                      actor=actor, rework_count=rework_reset,
                                      closeout_written=closeout)
            self._db.execute("UPDATE task SET intent=? WHERE id=?",
                             (json.dumps(intent, default=str), row["id"]))
            env = self._envelope(chart, ok=True, task_id=row["id"], state=to_state,
                                 seq=new_seq, lease_until=lease_until, now=now,
                                 gate=verdict)
            if dispatched:  # S4a: surface the atomically-created worker cohort to the caller
                env["worker_ids"] = dispatched
            if rationale is not None:
                env["rationale"] = rationale
            kind = "closeout" if spec.get("is_closeout") else "advance"
            self._append_event(row["id"], new_seq, kind, tool=tool, actor=actor,
                               payload={"to_state": to_state, "tool_payload": payload,
                                        "gate": verdict},
                               result=env, idem_key=idem_key, now=now, rationale=rationale)
            if chart["states"][to_state].get("terminal"):
                self._release_claims(row["id"])
            return env

        # Gate FAILED. In a state with rework_to: count it, escalate past the
        # rework cap, abandon past the escalation cap. Elsewhere: record the
        # failed attempt and stay (the client retries with better evidence).
        if "rework_to" not in spec:
            lease_until = self._renew(row, state=row["state"], seq=new_seq, now=now,
                                      actor=actor)
            env = self._envelope(chart, ok=True, task_id=row["id"],
                                 state=row["state"], seq=new_seq,
                                 lease_until=lease_until, now=now, gate=verdict,
                                 instruction="Gate failed: "
                                             + str(verdict.get("reason", "improve and resubmit.")))
            if rationale is not None:
                env["rationale"] = rationale
            self._append_event(row["id"], new_seq, "gate_failed", tool=tool,
                               actor=actor,
                               payload={"to_state": row["state"],
                                        "tool_payload": payload, "gate": verdict},
                               result=env, idem_key=idem_key, now=now, rationale=rationale)
            return env

        rework_count = row["rework_count"] + 1
        esc_level = row["esc_level"]
        if rework_count > chart["rework_cap"]:
            esc_level += 1
            rework_count = 0
            if esc_level > chart["esc_cap"]:
                return self._apply_abandon(chart, row, tool, payload, verdict,
                                           esc_level, idem_key, actor, now, rationale)

        to_state = spec["rework_to"]
        intent["phase"] = to_state
        lease_until = self._renew(row, state=to_state, seq=new_seq, now=now,
                                  actor=actor, rework_count=rework_count,
                                  esc_level=esc_level)
        self._db.execute("UPDATE task SET intent=? WHERE id=?",
                         (json.dumps(intent, default=str), row["id"]))
        # Surface the gate's CONCRETE reason in the rework instruction (not just a
        # boolean) -- a rubric-verification gate returns the specific visual defect here,
        # so the task returns to IMPLEMENT knowing WHAT to fix, not merely THAT it failed.
        _reason = str(verdict.get("reason", "")).strip()
        _rework_msg = ("Review gate failed: " + _reason + " -- rework the implementation, "
                       "then resubmit the patch.") if _reason else \
            "Review gate failed: rework the implementation, then resubmit the patch."
        env = self._envelope(chart, ok=True, task_id=row["id"], state=to_state,
                             seq=new_seq, lease_until=lease_until, now=now,
                             gate=verdict, rework_count=rework_count,
                             esc_level=esc_level,
                             instruction=_rework_msg)
        if rationale is not None:
            env["rationale"] = rationale
        self._append_event(row["id"], new_seq, "rework", tool=tool, actor=actor,
                           payload={"to_state": to_state, "tool_payload": payload,
                                    "gate": verdict, "rework_count": rework_count,
                                    "esc_level": esc_level},
                           result=env, idem_key=idem_key, now=now, rationale=rationale)
        return env

    def _apply_abandon(self, chart: dict[str, Any], row: sqlite3.Row, tool: str,
                       payload: Any, verdict: dict[str, Any], esc_level: int,
                       idem_key: str | None, actor: str | None,
                       now: int, rationale: str | None = None) -> dict[str, Any]:
        """Escalation cap exhausted -> ABANDONED, but STILL transiting CLOSEOUT
        so the audit record ALWAYS exists. Both events land in this same
        superstep, server-side -- a vanished client cannot skip the closeout."""
        seq_closeout = row["seq"] + 1
        seq_final = row["seq"] + 2
        reason = "escalation cap exhausted at REVIEW gate"
        lease_until = self._renew(row, state="ABANDONED", seq=seq_final, now=now,
                                  actor=actor, rework_count=0, esc_level=esc_level,
                                  closeout_written=1)
        env = self._envelope(chart, ok=True, task_id=row["id"], state="ABANDONED",
                             seq=seq_final, lease_until=lease_until, now=now,
                             gate=verdict, abandoned=True, reason=reason,
                             instruction="Task abandoned after the escalation "
                                         "cap; a closeout was recorded.")
        # Event 1: the failing report drives the transit into CLOSEOUT.
        self._append_event(row["id"], seq_closeout, "escalation_exhausted",
                           tool=tool, actor=actor,
                           payload={"to_state": "CLOSEOUT", "tool_payload": payload,
                                    "gate": verdict, "esc_level": esc_level,
                                    "reason": reason},
                           result=env, idem_key=idem_key, now=now, rationale=rationale)
        # Event 2: the server-written closeout, then the terminal fold.
        self._append_event(row["id"], seq_final, "closeout", tool=None, actor=None,
                           payload={"to_state": "ABANDONED", "auto": True,
                                    "reason": reason,
                                    "intent": json.loads(row["intent"])},
                           result=None, idem_key=None, now=now)
        self._release_claims(row["id"])
        return env

    def _apply_abstain(self, chart: dict[str, Any], row: sqlite3.Row,
                       spec: dict[str, Any], tool: str, payload: Any,
                       verdict: dict[str, Any], abstain_target: str,
                       idem_key: str | None, actor: str | None,
                       now: int, rationale: str | None = None) -> dict[str, Any]:
        """GAP B2: no deterministic oracle + no human -> ABSTAIN + flag-for-human.

        This is a FIRST-CLASS HANDLED SUCCESS (`ok=True`, `outcome="ABSTAIN"`), never a
        confident-but-unverified pass and never an error. Like abandonment it STILL transits
        CLOSEOUT so a server-written audit record ALWAYS exists -- but the outcome is
        "handled abstention," not "failed/abandoned." The advisory verdict that led here is
        wrapped in the hard `advisory_semi_gold` type: non-trainable, non-promotable, so it
        can never leak into gold or a promotion ledger.
        """
        seq_closeout = row["seq"] + 1
        seq_final = row["seq"] + 2
        receipt_abstain = verdict.get("outcome") == "ABSTAIN"
        reason = (str(verdict.get("reason") or "research authority abstained")
                  if receipt_abstain else
                  ("no deterministic oracle and no human available at "
                   f"{row['state']}; abstained and flagged for human rather than emit an "
                   "unverified pass"))
        advisory = advisory_semi_gold(verdict)
        lease_until = self._renew(row, state=abstain_target, seq=seq_final, now=now,
                                  actor=actor, closeout_written=1)
        env = self._envelope(chart, ok=True, task_id=row["id"], state=abstain_target,
                             seq=seq_final, lease_until=lease_until, now=now,
                             outcome="ABSTAIN", flag_human=True, abstained=True,
                             reason=reason, advisory=advisory,
                             instruction=("Research abstained for this decision; dependent work "
                                          "remains locked and a closeout was recorded."
                                          if receipt_abstain else
                                          "Abstained: no oracle and no human -- flagged for "
                                          "human review; a closeout was recorded."))
        if rationale is not None:
            env["rationale"] = rationale
        # Event 1: the review report drives the transit into CLOSEOUT (handled, not failed).
        self._append_event(row["id"], seq_closeout, "abstained", tool=tool, actor=actor,
                           payload={"to_state": "CLOSEOUT", "tool_payload": payload,
                                    "outcome": "ABSTAIN", "flag_human": True,
                                    "advisory": advisory, "reason": reason},
                           result=env, idem_key=idem_key, now=now, rationale=rationale)
        # Event 2: the server-written closeout, then the terminal abstain fold.
        self._append_event(row["id"], seq_final, "closeout", tool=None, actor=None,
                           payload={"to_state": abstain_target, "auto": True,
                                    "outcome": "ABSTAIN", "flag_human": True,
                                    "reason": reason, "advisory": advisory,
                                    "intent": json.loads(row["intent"])},
                           result=None, idem_key=None, now=now)
        self._release_claims(row["id"])
        return env

    # -- API: claims ------------------------------------------------------------

    def acquire_claims(self, task_id: str, claims: list[dict[str, Any]],
                       seq: int | None = None) -> dict[str, Any]:
        """Atomic all-or-nothing claim acquisition in one ordered transaction:
        either every (kind, key) is granted or none is -- no hold-and-wait, so
        no deadlock, structurally. Exclusivity is the (kind, key) PRIMARY KEY
        plus a same-kind glob-overlap check (fnmatch both directions)."""
        if seq is None:
            raise ValueError("seq is required (optimistic-concurrency fence)")
        seq = int(seq)
        wanted = sorted({(str(c["kind"]), str(c["key"])) for c in claims})
        if not wanted:
            raise ValueError("claims must be a non-empty list of {kind, key}")
        now = int(time.time())
        with self._txn():
            row = self._fetch_task(task_id)
            chart = self._chart(row["track"])
            if seq != row["seq"]:
                return {"ok": False, "code": "REJECTED_STALE", "task_id": task_id,
                        "state": row["state"], "seq": row["seq"]}
            conflicts: list[dict[str, Any]] = []
            for kind, key in wanted:
                for other in self._db.execute(
                        "SELECT key, task_id, until FROM claim WHERE kind=?",
                        (kind,)):
                    if other["task_id"] == task_id:
                        continue
                    if other["until"] is not None and other["until"] < now:
                        continue  # expired claim -- free to take
                    if (key == other["key"] or fnmatchcase(key, other["key"])
                            or fnmatchcase(other["key"], key)):
                        conflicts.append({"kind": kind, "key": key,
                                          "held_by": other["task_id"],
                                          "held_key": other["key"]})
            if conflicts:
                # All-or-nothing: grant NOTHING on any overlap.
                return {"ok": False, "code": "CLAIM_CONFLICT", "task_id": task_id,
                        "state": row["state"], "seq": row["seq"],
                        "conflicts": conflicts}
            for kind, key in wanted:
                # REPLACE only ever swallows our own or an expired row -- live
                # foreign rows were rejected above, inside this same txn.
                self._db.execute(
                    "INSERT OR REPLACE INTO claim(kind, key, task_id, until, ts)"
                    " VALUES(?,?,?,?,?)", (kind, key, task_id, None, now))
            new_seq = row["seq"] + 1
            lease_until = self._renew(row, state=row["state"], seq=new_seq,
                                      now=now, actor=None)
            granted = [{"kind": k, "key": key} for k, key in wanted]
            env = self._envelope(chart, ok=True, task_id=task_id,
                                 state=row["state"], seq=new_seq,
                                 lease_until=lease_until, now=now, claims=granted)
            self._append_event(task_id, new_seq, "claims", tool=None, actor=None,
                               payload={"to_state": row["state"], "claims": granted},
                               result=env, idem_key=None, now=now)
            return env

    def _release_claims(self, task_id: str) -> None:
        self._db.execute("DELETE FROM claim WHERE task_id=?", (task_id,))

    def _boundary_violation(self, task_id: str, payload: Any,
                            now: int) -> list[dict[str, Any]]:
        """If the report declares written paths, refuse any that fall inside
        ANOTHER task's live path claim (out-of-claim writes refused at
        write-time). Tasks holding no claims are only fenced off others'."""
        if not isinstance(payload, dict) or not isinstance(payload.get("writes"), list):
            return []
        hits: list[dict[str, Any]] = []
        for path in payload["writes"]:
            path = str(path)
            for other in self._db.execute(
                    "SELECT key, task_id, until FROM claim WHERE kind='path'"):
                if other["task_id"] == task_id:
                    continue
                if other["until"] is not None and other["until"] < now:
                    continue
                if path == other["key"] or fnmatchcase(path, other["key"]):
                    hits.append({"path": path, "claimed_by": other["task_id"],
                                 "claim": other["key"]})
        return hits

    # -- API: reaper --------------------------------------------------------------

    def reap(self, now_ts: int | None = None) -> list[str]:
        """Move every expired-lease, non-terminal task to STALLED, preserving
        the intent record (heartbeat-details pattern): the reaped event carries
        the pre-stall state, and cortex_resume restores it with a fresh lease."""
        now = int(now_ts if now_ts is not None else time.time())
        reaped: list[str] = []
        with self._txn():
            rows = self._db.execute(
                "SELECT * FROM task WHERE lease_until IS NOT NULL"
                " AND lease_until < ?"
                " AND state NOT IN ('DONE', 'ABANDONED', 'STALLED')",
                (now,)).fetchall()
            for row in rows:
                new_seq = row["seq"] + 1
                self._db.execute(
                    "UPDATE task SET state='STALLED', stalled_from=?, seq=?,"
                    " lease_owner=NULL, lease_until=NULL, updated_at=?"
                    " WHERE id=?",
                    (row["state"], new_seq, now, row["id"]))
                self._append_event(
                    row["id"], new_seq, "reaped", tool=None, actor=None,
                    payload={"to_state": "STALLED", "from_state": row["state"],
                             "lease_until": row["lease_until"],
                             "intent": json.loads(row["intent"])},
                    result=None, idem_key=None, now=now)
                reaped.append(row["id"])
        return reaped
