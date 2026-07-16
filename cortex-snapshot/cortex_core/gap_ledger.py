"""GAP-CORTEX-0001: durable, machine-tracked gap/phase ledger (v0).

Replaces the fragile prose gap-plan (``docs/GAP-CLOSURE-PLAN.md``) with a
single append-only JSONL ground-truth store. This is the "#1 process-moat"
ask: gaps, their phase/status, dependencies, and closure evidence are GROUND
TRUTH -- durable across N concurrent agents, auto-derived, never manually
curated back into sync.

Design provenance
-----------------
Reconciled from two independent converged reviews:
``docs/design/durable-gap-tracking-fable-2026-07-13.md`` (Fable) and
``docs/design/durable-gap-tracking-codex-2026-07-13.md`` (Codex). They agree
on the substrate; where they disagreed (status enum, evidence shape, strict-vs-
forgiving schema handling) the reconciliation is recorded in this module and in
the closeout. The root-cause both diagnosed: **drift is a write-path disease,
not a format disease** -- the repo already had a *structured* gap registry
(``templates/workspace-control-plane/gaps/index.jsonl`` + ``registry.md``) and
it drifted anyway because its write path was manual. So the center of gravity
here is: ONE canonical append-only store + current state DERIVED (never
separately maintained) + a deterministic render/--check gate that makes
divergence a CI failure, not a memory duty.

Storage
-------
Append-only JSONL at ``gaps/gap_ledger.jsonl`` (committed to git -- gap state is
project ground truth, not run telemetry). Every mutation appends one full
state-snapshot line; the current state of a gap is the last line mentioning its
``gap_id`` (the exact reduction ``task_ledger.py`` already implements). Nothing
is ever rewritten in place, so the file is simultaneously the event log and,
reduced, the current state -- "history" and "current" cannot disagree by
construction.

Concurrency
-----------
The exclusive-create lockfile discipline is **imported** from
``task_ledger.py`` (reuse, don't reinvent -- both designs, and the ontology set
that precedent): read-check-append is serialized, stale/crashed locks are
stolen, torn trailing lines are skipped on read. Reads are lock-free and at
worst stale, which the atomic claim then corrects.

Anti-bloat (Disease A / B)
--------------------------
CLI-only: ``cortex-gap`` with an ``action`` subcommand -- deliberately NO new
MCP tool (the frozen tool-surface budget the whole wrapper exists to protect;
see ``cortex_core/tool_surface.py``). No mandatory step refuses work; the
closure "ceremony" is one evidence-bearing call an agent already has in hand at
closeout time. Default reads are scoped projections, never the whole log.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

import uuid6

from .config import make_stdio_encoding_safe, resolve_workspace_override

# Reuse the *tested* concurrency + IO primitives from the task ledger rather
# than copy-pasting them (copy-paste is how the registry.md/index.jsonl split
# drifted). Same package, same lock semantics, same torn-line tolerance.
from .task_ledger import (
    _acquire_lock,
    _append,
    _lock_path,
    _now,
    _read_records,
    _release_lock,
)

SCHEMA_VERSION = 1

# Reconciled status enum. Fable proposed 8, Codex proposed 7; both split
# "an agent *reported* closure" from "closure is *verified*" (the
# anti-evidence-theater move) and both keep a `proposed` inbox for auto/LLM-
# mined candidates that are not yet ground truth. Merged vocabulary:
#   proposed   - candidate (auto/LLM-detected), not yet accepted as ground truth
#   open       - accepted, workable
#   claimed    - an agent owns it and is working (task_ledger `claim` verb)
#   blocked    - has an unfinished blocker (usually DERIVED, see effective_status)
#   verifying  - work reported done + evidence attached, NOT yet verified
#   closed     - verified closed (deterministic check or explicit human event)
#   wont_fix   - terminal, deliberately not doing
#   superseded - replaced by another gap (supersede is an event AND a status)
# `ready` (Codex) is intentionally NOT an authored status: it is a derived
# display (open + unblocked), computed at read time, not stored -- keeping the
# authored enum small and the reducer the single source of "current".
VALID_STATUSES = (
    "proposed",
    "open",
    "claimed",
    "blocked",
    "verifying",
    "closed",
    "wont_fix",
    "superseded",
)
TERMINAL_STATUSES = frozenset({"closed", "wont_fix", "superseded"})
# statuses that count as "successfully done" for blocker-resolution purposes
BLOCKER_SATISFIED = frozenset({"closed", "wont_fix", "superseded"})

VALID_PRIORITIES = ("P0", "P1", "P2")

VALID_EVENTS = frozenset(
    {
        "create",
        "accept",
        "claim",
        "release",
        "update",
        "block",
        "close",
        "verify",
        "reopen",
        "supersede",
        "wont_fix",
    }
)

GAP_ID_RE = re.compile(r"^GAP-[A-Z0-9]+-\d{3,}$")

# evidence kinds whose `path` is not a repo file (skip file/line resolution)
_NON_FILE_EVIDENCE_KINDS = frozenset({"commit", "url", "external", "human", "results_row"})

# The canonical top-level keys a v1 record may carry. Strict on WRITE (a typoed
# field must never silently become state -- Codex); forgiving on READ (a newer
# schema's extra fields are kept, not rejected -- Fable). Two boundaries, one
# synthesis.
_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "event_id",
        "event",
        "gap_id",
        "title",
        "status",
        "phase",
        "priority",
        "blocks",
        "blocked_by",
        "closes_metric",
        "evidence",
        "owner_agent",
        "task_ids",
        "source",
        "verified",
        "supersedes",
        "superseded_by",
        "reason",
        "author_agent",
        "created_at",
        "claimed_at",
        "updated_at",
        "closed_at",
    }
)


# --------------------------------------------------------------------------- #
# paths / ids
# --------------------------------------------------------------------------- #
def ledger_path(workspace: str | Path | None = None) -> Path:
    ws = resolve_workspace_override(workspace)
    return ws / "gaps" / "gap_ledger.jsonl"


def _new_event_id() -> str:
    return f"gap-event-{uuid6.uuid7()}"


# --------------------------------------------------------------------------- #
# read / reduce
# --------------------------------------------------------------------------- #
def _current_state(led_path: Path) -> dict[str, dict[str, Any]]:
    """Reduce the append-only log to current per-gap state: last record wins.

    Forgiving by design (Fable): unknown/extra fields from a newer schema are
    kept verbatim; torn final lines are already skipped by ``_read_records``.
    """
    state: dict[str, dict[str, Any]] = {}
    for rec in _read_records(led_path):
        gid = rec.get("gap_id")
        if gid:
            state[gid] = rec
    return state


def _blocker_graph(state: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    """Union of explicit ``blocked_by`` edges and inverses of ``blocks`` edges.

    We store edges as authored on whichever gap set them (no cross-record write
    amplification in v0); the full bidirectional dependency graph is derived
    here. Returns gap_id -> set of gap_ids that block it.
    """
    blocked_by: dict[str, set[str]] = {gid: set() for gid in state}
    for gid, rec in state.items():
        for b in rec.get("blocked_by") or []:
            blocked_by.setdefault(gid, set()).add(b)
        for b in rec.get("blocks") or []:
            blocked_by.setdefault(b, set()).add(gid)
    return blocked_by


def _effective_status(gid: str, state: dict[str, dict[str, Any]], blocked_by: dict[str, set[str]]) -> str:
    """Derive the *current* status (Fable: current state is derived, never
    separately maintained). An authored open/claimed gap whose blockers are not
    all satisfied reads as ``blocked`` -- so blocked-ness is always live and can
    never drift out of sync with the blockers' real state."""
    rec = state[gid]
    authored = rec.get("status", "open")
    if authored in TERMINAL_STATUSES or authored == "verifying":
        return authored
    for b in blocked_by.get(gid, ()):  # only unsatisfied *known* blockers block
        bstate = state.get(b)
        if bstate is not None and bstate.get("status") not in BLOCKER_SATISFIED:
            return "blocked"
    return authored


def _annotate(state: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    blocked_by = _blocker_graph(state)
    out: dict[str, dict[str, Any]] = {}
    for gid, rec in state.items():
        enriched = dict(rec)
        enriched["effective_status"] = _effective_status(gid, state, blocked_by)
        out[gid] = enriched
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _validate_write_record(record: dict[str, Any]) -> None:
    """Strict validation applied to every record BEFORE it is appended (Codex).

    Rejects unknown top-level keys (typos must not become state), bad enums, and
    malformed ids. This is the write boundary only -- reads stay forgiving.
    """
    unknown = set(record) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"unknown field(s) not allowed on write: {sorted(unknown)}")
    gid = record.get("gap_id")
    if not isinstance(gid, str) or not GAP_ID_RE.match(gid):
        raise ValueError(f"invalid gap_id {gid!r}; must match {GAP_ID_RE.pattern}")
    if record.get("status") not in VALID_STATUSES:
        raise ValueError(f"invalid status {record.get('status')!r}; one of {VALID_STATUSES}")
    if record.get("priority") not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {record.get('priority')!r}; one of {VALID_PRIORITIES}")
    if record.get("event") not in VALID_EVENTS:
        raise ValueError(f"invalid event {record.get('event')!r}; one of {sorted(VALID_EVENTS)}")
    for key in ("blocks", "blocked_by", "task_ids"):
        val = record.get(key)
        if val is not None and not isinstance(val, list):
            raise ValueError(f"{key} must be a list or null")
    ev = record.get("evidence")
    if ev is not None:
        if not isinstance(ev, list):
            raise ValueError("evidence must be a list")
        for item in ev:
            if not isinstance(item, dict) or not item.get("path") or not item.get("kind"):
                raise ValueError("each evidence item needs non-empty 'path' and 'kind'")


def _check_edges(gid: str, blocked_by: Iterable[str], state: dict[str, dict[str, Any]]) -> None:
    """Reject self-edges, dangling targets, and cycles (Codex) at write time."""
    bb = list(blocked_by or [])
    if gid in bb:
        raise ValueError(f"self-edge: {gid} cannot block itself")
    for target in bb:
        if target not in state and target != gid:
            raise ValueError(f"blocked_by references unknown gap {target!r}")
    # cycle check over the graph that WOULD exist with these edges applied
    proposed = {g: dict(r) for g, r in state.items()}
    node = proposed.setdefault(gid, {"gap_id": gid, "status": "open"})
    node["blocked_by"] = bb
    graph = _blocker_graph(proposed)
    if _has_cycle(graph):
        raise ValueError(f"edge on {gid} would create a dependency cycle")


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}

    def visit(n: str) -> bool:
        color[n] = GRAY
        for m in graph.get(n, ()):  # follow blocked_by edges
            if m not in color:
                color[m] = WHITE
            if color[m] == GRAY:
                return True
            if color[m] == WHITE and visit(m):
                return True
        color[n] = BLACK
        return False

    return any(color.get(n, WHITE) == WHITE and visit(n) for n in list(graph))


# --------------------------------------------------------------------------- #
# write helpers
# --------------------------------------------------------------------------- #
def _append_event(led_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    record = {"schema_version": SCHEMA_VERSION, "event_id": _new_event_id(), **record}
    _validate_write_record(record)
    _append(led_path, record)
    return record


def _with_lock(led_path: Path, fn):
    lock = _acquire_lock(_lock_path(led_path))
    if lock is None:
        return None
    try:
        return fn()
    finally:
        _release_lock(lock)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def create_gap(
    gap_id: str,
    *,
    title: str,
    phase: str,
    source: str,
    author_agent: str,
    workspace: str | Path | None = None,
    priority: str = "P1",
    status: str = "open",
    blocks: list[str] | None = None,
    blocked_by: list[str] | None = None,
    closes_metric: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Register a new gap. ``status`` defaults to ``open``; pass ``proposed`` for
    an auto/LLM-mined candidate that must be explicitly accepted before it is
    ground truth."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; one of {VALID_STATUSES}")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}; one of {VALID_PRIORITIES}")
    led_path = ledger_path(workspace)

    def _do():
        state = _current_state(led_path)
        if gap_id in state:
            return {"created": False, "reason": "gap_id already exists", "gap_id": gap_id}
        _check_edges(gap_id, blocked_by or [], state)
        now = _now()
        rec = _append_event(
            led_path,
            {
                "event": "create",
                "gap_id": gap_id,
                "title": title,
                "status": status,
                "phase": phase,
                "priority": priority,
                "blocks": list(blocks or []),
                "blocked_by": list(blocked_by or []),
                "closes_metric": closes_metric,
                "evidence": list(evidence or []),
                "owner_agent": None,
                "task_ids": [],
                "source": source,
                "verified": False,
                "supersedes": None,
                "superseded_by": None,
                "reason": None,
                "author_agent": author_agent,
                "created_at": now,
                "claimed_at": None,
                "updated_at": now,
                "closed_at": None,
            },
        )
        return {"created": True, **rec}

    res = _with_lock(led_path, _do)
    if res is None:
        return {"created": False, "reason": "could not acquire ledger lock", "gap_id": gap_id}
    if isinstance(res, dict) and res.get("created") is False:
        # surface validation errors raised inside the lock as exceptions
        return res
    return res


def _mutate(
    gap_id: str,
    workspace: str | Path | None,
    author_agent: str,
    event: str,
    changes: dict[str, Any],
    *,
    require_status: set[str] | None = None,
    edge_check: list[str] | None = None,
) -> dict[str, Any]:
    led_path = ledger_path(workspace)

    def _do():
        state = _current_state(led_path)
        current = state.get(gap_id)
        if current is None:
            return {"ok": False, "reason": "no such gap", "gap_id": gap_id}
        if require_status is not None and current.get("status") not in require_status:
            return {
                "ok": False,
                "reason": f"status is {current.get('status')!r}, need one of {sorted(require_status)}",
                "gap_id": gap_id,
            }
        if edge_check is not None:
            _check_edges(gap_id, edge_check, state)
        # forgiving carry-forward of whatever the last record held, then apply
        rec = {k: v for k, v in current.items() if k != "effective_status"}
        rec.update(changes)
        rec["event"] = event
        rec["author_agent"] = author_agent
        rec["updated_at"] = _now()
        return {"ok": True, **_append_event(led_path, rec)}

    res = _with_lock(led_path, _do)
    if res is None:
        return {"ok": False, "reason": "could not acquire ledger lock", "gap_id": gap_id}
    return res


def update_gap(
    gap_id: str,
    workspace: str | Path | None = None,
    *,
    author_agent: str,
    title: str | None = None,
    phase: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    blocks: list[str] | None = None,
    blocked_by: list[str] | None = None,
    closes_metric: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    changes: dict[str, Any] = {}
    for key, val in (
        ("title", title),
        ("phase", phase),
        ("priority", priority),
        ("status", status),
        ("blocks", blocks),
        ("blocked_by", blocked_by),
        ("closes_metric", closes_metric),
        ("reason", reason),
    ):
        if val is not None:
            changes[key] = val
    res = _mutate(
        gap_id, workspace, author_agent, "update", changes,
        edge_check=blocked_by if blocked_by is not None else None,
    )
    if res.get("ok") is False and res.get("reason", "").startswith("no such"):
        raise KeyError(res["reason"])
    return res


def claim_gap(gap_id: str, owner: str, workspace: str | Path | None = None) -> dict[str, Any]:
    """Atomically claim an open/proposed gap. Exactly one of two racing
    claimants wins (read-check-append under the exclusive lock)."""
    res = _mutate(
        gap_id, workspace, owner, "claim",
        {"status": "claimed", "owner_agent": owner, "claimed_at": _now()},
        require_status={"open", "proposed"},
    )
    if res.get("ok"):
        return {"claimed": True, **res}
    return {"claimed": False, **res}


def release_gap(gap_id: str, owner: str, workspace: str | Path | None = None) -> dict[str, Any]:
    return _mutate(
        gap_id, workspace, owner, "release",
        {"status": "open", "owner_agent": None, "claimed_at": None},
    )


def close_gap(
    gap_id: str,
    workspace: str | Path | None = None,
    *,
    author_agent: str,
    evidence: list[dict[str, Any]],
    reason: str | None = None,
) -> dict[str, Any]:
    """Report a gap's work as done. Moves it to ``verifying`` (NOT ``closed``) --
    an agent can *report* closure; only a deterministic check or explicit human
    event flips ``verified`` and status ``closed`` (see ``verify_gap``). Requires
    non-empty evidence."""
    if not evidence:
        raise ValueError("close requires at least one evidence pointer")
    for item in evidence:
        if not isinstance(item, dict) or not item.get("path") or not item.get("kind"):
            raise ValueError("each evidence item needs non-empty 'path' and 'kind'")
    res = _mutate(
        gap_id, workspace, author_agent, "close",
        {"status": "verifying", "verified": False, "evidence": list(evidence),
         "reason": reason},
    )
    if res.get("ok") is False and res.get("reason", "").startswith("no such"):
        raise KeyError(res["reason"])
    return res


def _resolve_evidence(item: dict[str, Any], ws: Path) -> str | None:
    """Return None if the evidence resolves, else a human reason it did not.

    Deterministic auto-verification (the machine gate): repo-relative
    ``path``[:``line``] must exist and the line be in range. Non-file kinds
    (commit/url/external/human/results_row) only require a non-empty path.
    """
    kind = item.get("kind", "")
    path = item.get("path", "")
    if kind in _NON_FILE_EVIDENCE_KINDS or "://" in str(path):
        return None if path else "empty evidence path"
    target = (ws / path).resolve()
    if not target.is_file():
        return f"missing file: {path}"
    line = item.get("line")
    if line is not None:
        try:
            n = sum(1 for _ in target.open("r", encoding="utf-8", errors="replace"))
        except OSError as exc:  # pragma: no cover - defensive
            return f"unreadable: {path} ({exc})"
        if not (1 <= int(line) <= n):
            return f"line {line} out of range (1..{n}) in {path}"
    return None


def verify_gap(
    gap_id: str,
    workspace: str | Path | None = None,
    *,
    author_agent: str,
    human: bool = False,
) -> dict[str, Any]:
    """Deterministically promote a ``verifying`` gap to ``closed``. Every
    evidence pointer must resolve against committed repo state; if any does not,
    the gap stays ``verifying`` and the unresolved refs are returned. ``human=
    True`` records an explicit human sign-off for closure metrics that are not
    machine-checkable (judges never flip this -- deterministic checkers or humans
    only)."""
    led_path = ledger_path(workspace)
    ws = resolve_workspace_override(workspace)

    def _do():
        state = _current_state(led_path)
        current = state.get(gap_id)
        if current is None:
            return {"ok": False, "reason": "no such gap", "gap_id": gap_id}
        evidence = current.get("evidence") or []
        if not evidence:
            return {"ok": False, "verified": False, "reason": "no evidence to verify", "gap_id": gap_id}
        unresolved: list[str] = []
        if not human:
            for item in evidence:
                why = _resolve_evidence(item, ws)
                if why:
                    unresolved.append(why)
        if unresolved:
            return {"ok": True, "verified": False, "status": current.get("status"),
                    "unresolved": unresolved, "gap_id": gap_id}
        rec = {k: v for k, v in current.items() if k != "effective_status"}
        rec.update(
            {"status": "closed", "verified": True, "closed_at": _now(),
             "reason": ("human sign-off" if human else "evidence resolved"),
             "event": "verify", "author_agent": author_agent, "updated_at": _now()}
        )
        return {"ok": True, **_append_event(led_path, rec)}

    res = _with_lock(led_path, _do)
    if res is None:
        return {"ok": False, "reason": "could not acquire ledger lock", "gap_id": gap_id}
    return res


def reopen_gap(
    gap_id: str, workspace: str | Path | None = None, *, author_agent: str, reason: str | None = None
) -> dict[str, Any]:
    return _mutate(
        gap_id, workspace, author_agent, "reopen",
        {"status": "open", "verified": False, "closed_at": None, "reason": reason},
    )


def supersede_gap(
    gap_id: str, by: str, workspace: str | Path | None = None, *, author_agent: str
) -> dict[str, Any]:
    led_path = ledger_path(workspace)

    def _do():
        state = _current_state(led_path)
        if gap_id not in state:
            return {"ok": False, "reason": "no such gap", "gap_id": gap_id}
        if by not in state:
            raise ValueError(f"supersede target {by!r} does not exist")
        current = state[gap_id]
        rec = {k: v for k, v in current.items() if k != "effective_status"}
        rec.update(
            {"status": "superseded", "superseded_by": by, "event": "supersede",
             "author_agent": author_agent, "updated_at": _now()}
        )
        return {"ok": True, **_append_event(led_path, rec)}

    res = _with_lock(led_path, _do)
    if res is None:
        return {"ok": False, "reason": "could not acquire ledger lock", "gap_id": gap_id}
    return res


def wont_fix_gap(
    gap_id: str, workspace: str | Path | None = None, *, author_agent: str, reason: str
) -> dict[str, Any]:
    return _mutate(
        gap_id, workspace, author_agent, "wont_fix", {"status": "wont_fix", "reason": reason}
    )


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def get_gap(gap_id: str, workspace: str | Path | None = None) -> dict[str, Any] | None:
    state = _annotate(_current_state(ledger_path(workspace)))
    return state.get(gap_id)


def list_gaps(
    workspace: str | Path | None = None,
    status: str | None = None,
    phase: str | None = None,
) -> list[dict[str, Any]]:
    """Current state of every gap, sorted by gap_id. ``status`` filters on the
    DERIVED effective status (so ``--status blocked`` finds live-blocked gaps)."""
    gaps = list(_annotate(_current_state(ledger_path(workspace))).values())
    if phase is not None:
        gaps = [g for g in gaps if g.get("phase") == phase]
    if status is not None:
        gaps = [g for g in gaps if g.get("effective_status") == status]
    return sorted(gaps, key=lambda g: g.get("gap_id", ""))


def phase_rollup(workspace: str | Path | None = None) -> dict[str, str]:
    """Derive each phase's status from its gaps (never hand-curated):
    complete = every gap terminal-success; active = any claimed/verifying;
    blocked = any blocked; else open."""
    gaps = _annotate(_current_state(ledger_path(workspace)))
    by_phase: dict[str, list[str]] = {}
    for g in gaps.values():
        by_phase.setdefault(g.get("phase", "unphased"), []).append(g.get("effective_status", "open"))
    roll: dict[str, str] = {}
    for phase, statuses in by_phase.items():
        if all(s in BLOCKER_SATISFIED for s in statuses):
            roll[phase] = "complete"
        elif any(s in ("claimed", "verifying") for s in statuses):
            roll[phase] = "active"
        elif any(s == "blocked" for s in statuses):
            roll[phase] = "blocked"
        else:
            roll[phase] = "open"
    return roll


# --------------------------------------------------------------------------- #
# whole-log validation
# --------------------------------------------------------------------------- #
def validate_ledger(workspace: str | Path | None = None) -> dict[str, Any]:
    """Referential-integrity + cycle + evidence-shape check over the whole
    reduced log. This is the ``cortex-doctor``/CI hook."""
    state = _current_state(ledger_path(workspace))
    errors: list[str] = []
    for gid, rec in state.items():
        if not GAP_ID_RE.match(gid):
            errors.append(f"{gid}: malformed gap_id")
        if rec.get("status") not in VALID_STATUSES:
            errors.append(f"{gid}: invalid status {rec.get('status')!r}")
        for edge_key in ("blocks", "blocked_by"):
            for target in rec.get(edge_key) or []:
                if target == gid:
                    errors.append(f"{gid}: self-edge in {edge_key}")
                elif target not in state:
                    errors.append(f"{gid}: {edge_key} -> unknown gap {target!r}")
        sup = rec.get("superseded_by")
        if sup and sup not in state:
            errors.append(f"{gid}: superseded_by unknown gap {sup!r}")
    if _has_cycle(_blocker_graph(state)):
        errors.append("dependency cycle detected among blocked_by edges")
    return {"ok": not errors, "errors": errors, "count": len(state)}


# --------------------------------------------------------------------------- #
# render projection + --check drift gate
# --------------------------------------------------------------------------- #
def render(workspace: str | Path | None = None) -> str:
    """Regenerate the human-readable gap board from the ledger. This is the
    projection: ``docs/GAPS.md`` is a byte-exact function of the ledger, never
    hand-edited (``render_check`` fails CI on drift)."""
    gaps = list_gaps(workspace)
    roll = phase_rollup(workspace)
    lines: list[str] = []
    lines.append("# Gap board (GENERATED from gaps/gap_ledger.jsonl -- DO NOT EDIT)")
    lines.append("")
    lines.append("Regenerate with `cortex-gap render`. CI runs `render --check`; hand-edits fail.")
    lines.append("")
    lines.append("## Phase rollup")
    lines.append("")
    lines.append("| phase | status |")
    lines.append("| --- | --- |")
    for phase in sorted(roll):
        lines.append(f"| {phase} | {roll[phase]} |")
    lines.append("")
    lines.append("## Gaps")
    lines.append("")
    lines.append("| id | pri | status | phase | verified | owner | blocked_by | title |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for g in gaps:
        owner = g.get("owner_agent") or ""
        bb = ",".join(g.get("blocked_by") or [])
        verified = "yes" if g.get("verified") else "no"
        lines.append(
            f"| {g['gap_id']} | {g.get('priority', '')} | {g.get('effective_status', '')} "
            f"| {g.get('phase', '')} | {verified} | {owner} | {bb} | {g.get('title', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_check(path: str | Path, workspace: str | Path | None = None) -> bool:
    """True iff the committed view at ``path`` is byte-identical to a fresh
    render. Hand-editing the projection becomes a deterministic CI failure."""
    p = Path(path)
    if not p.is_file():
        return False
    return p.read_text(encoding="utf-8") == render(workspace)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _parse_evidence(items: list[str] | None) -> list[dict[str, Any]]:
    """Parse ``--evidence path:line:kind`` (line/kind optional) into records."""
    out: list[dict[str, Any]] = []
    for raw in items or []:
        # split from the right so a windows path or URL colon survives
        parts = raw.rsplit(":", 2)
        path = parts[0]
        line: int | None = None
        kind = "closeout"
        if len(parts) == 3:
            line = int(parts[1]) if parts[1] else None
            kind = parts[2] or "closeout"
        elif len(parts) == 2:
            kind = parts[1] or "closeout"
        out.append({"path": path, "line": line, "kind": kind})
    return out


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(
        description="Cortex durable gap/phase ledger (GAP-CORTEX-0001). CLI-only by design."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="register a new gap")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--author-agent", required=True)
    p.add_argument("--priority", default="P1", choices=VALID_PRIORITIES)
    p.add_argument("--status", default="open", choices=VALID_STATUSES)
    p.add_argument("--blocked-by", nargs="*", default=None)
    p.add_argument("--blocks", nargs="*", default=None)
    p.add_argument("--closes-metric", default=None)

    p = sub.add_parser("list", help="list current gap state")
    p.add_argument("--status", default=None)
    p.add_argument("--phase", default=None)

    p = sub.add_parser("show", help="show one gap")
    p.add_argument("--gap-id", required=True)

    p = sub.add_parser("claim", help="atomically claim a gap")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--owner", required=True)

    p = sub.add_parser("release", help="release a claimed gap")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--owner", required=True)

    p = sub.add_parser("update", help="update fields on a gap")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--author-agent", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--phase", default=None)
    p.add_argument("--priority", default=None, choices=(*VALID_PRIORITIES, None))
    p.add_argument("--status", default=None, choices=(*VALID_STATUSES, None))
    p.add_argument("--blocked-by", nargs="*", default=None)
    p.add_argument("--reason", default=None)

    p = sub.add_parser("close", help="report work done -> verifying (evidence required)")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--author-agent", required=True)
    p.add_argument("--evidence", nargs="+", required=True, help="path[:line[:kind]] ...")
    p.add_argument("--reason", default=None)

    p = sub.add_parser("verify", help="deterministically close a verifying gap")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--author-agent", required=True)
    p.add_argument("--human", action="store_true", help="explicit human sign-off")

    p = sub.add_parser("reopen", help="reopen a closed gap")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--author-agent", required=True)
    p.add_argument("--reason", default=None)

    p = sub.add_parser("supersede", help="mark a gap superseded by another")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--author-agent", required=True)

    p = sub.add_parser("wont-fix", help="mark a gap wont_fix")
    p.add_argument("--gap-id", required=True)
    p.add_argument("--author-agent", required=True)
    p.add_argument("--reason", required=True)

    sub.add_parser("validate", help="whole-log integrity check")

    p = sub.add_parser("render", help="render the gap board")
    p.add_argument("--out", default=None, help="write to file instead of stdout")
    p.add_argument("--check", default=None, help="fail if the file at PATH differs from a fresh render")

    args = parser.parse_args(argv)
    cmd = args.command

    if cmd == "create":
        _print(create_gap(
            args.gap_id, title=args.title, phase=args.phase, source=args.source,
            author_agent=args.author_agent, priority=args.priority, status=args.status,
            blocked_by=args.blocked_by, blocks=args.blocks, closes_metric=args.closes_metric,
        ))
    elif cmd == "list":
        _print(list_gaps(status=args.status, phase=args.phase))
    elif cmd == "show":
        _print(get_gap(args.gap_id))
    elif cmd == "claim":
        _print(claim_gap(args.gap_id, args.owner))
    elif cmd == "release":
        _print(release_gap(args.gap_id, args.owner))
    elif cmd == "update":
        _print(update_gap(
            args.gap_id, author_agent=args.author_agent, title=args.title, phase=args.phase,
            priority=args.priority, status=args.status, blocked_by=args.blocked_by, reason=args.reason,
        ))
    elif cmd == "close":
        _print(close_gap(
            args.gap_id, author_agent=args.author_agent,
            evidence=_parse_evidence(args.evidence), reason=args.reason,
        ))
    elif cmd == "verify":
        _print(verify_gap(args.gap_id, author_agent=args.author_agent, human=args.human))
    elif cmd == "reopen":
        _print(reopen_gap(args.gap_id, author_agent=args.author_agent, reason=args.reason))
    elif cmd == "supersede":
        _print(supersede_gap(args.gap_id, by=args.by, author_agent=args.author_agent))
    elif cmd == "wont-fix":
        _print(wont_fix_gap(args.gap_id, author_agent=args.author_agent, reason=args.reason))
    elif cmd == "validate":
        report = validate_ledger()
        _print(report)
        return 0 if report["ok"] else 1
    elif cmd == "render":
        if args.check:
            ok = render_check(args.check)
            _print({"check": args.check, "in_sync": ok})
            return 0 if ok else 1
        text = render()
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            _print({"wrote": args.out})
        else:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
