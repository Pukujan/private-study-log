from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .audit import evidence_theater_warning, validate_evidence, validate_handoff_field, write_closeout
from .authz import (
    authorize_config_change,
    config_change_requires_passcode,
    mutation_requires_admin,
    resolve_server_mode,
    verify_admin_token,
)
from .config import (
    BRAIN_WORKSPACE_ENV,
    resolve_brain_workspace,
    resolve_brain_workspace_override,
    resolve_workspace,
    resolve_workspace_override,
)
from .doctor import doctor
from .fetch import fetch_document
from .search import CortexSearchIndex, _rotate_log_if_large
from .state_engine import phase_legal_tools
from .write_policy import NOOP, evaluate_write, write_policy_enabled

# Phase 3, mini-phase 3.1 (docs/BUILD-PLAN.md "Reconciliation 2026-07-04"):
# five tools, each backed by code that already exists and is tested --
# register/status/search/fetch_doc/write_log. Mini-phase 3.2 (below) adds
# the next_actions engine on top. Resources/prompts are mini-phase 3.3, not
# built here.

mcp = FastMCP("cortex")

# Session registry is process-local: one server process per stdio
# connection is the standard MCP deployment shape, so an in-memory dict is
# sufficient for "register stamps session+model+role" (gate 3.1) until
# Phase 6 wires this into the cross-session model_scorecards table.
# ``calls`` is a bounded read/write-tagged history used by _next_actions to
# detect and cap guidance loops (gate 3.2) -- deliberately not a general
# flight recorder (that's Phase 0 scope, not rebuilt here); it only tracks
# what this specific engine needs to reason about.
_sessions: dict[str, dict[str, Any]] = {}
# Stability audit finding #4 (2026-07-07): `_sessions` and `_run_engines` (below) are
# module-global mutable dicts with no lock. Not exploitable through the current
# single-process/single-event-loop FastMCP deployment (no `await` straddles a
# check-then-act sequence today), but `ops/qwen_benchmark_runner.py`'s own comments
# document a REAL crash (WindowsPath/dict TypeError, NoneType subscript) the moment
# this module is driven from real OS threads (e.g. `agent_runner.py` calling these
# functions in-process) -- worked around there via separate processes, not fixed here.
# Guard the actual mutation / check-then-act sequences so the root cause is closed,
# not just currently unexercised.
_sessions_lock = threading.Lock()
# Keep the live per-session call window small: enough to detect a short
# read-only loop, not enough to hoard a long raw tail of tool calls.
_DEFAULT_CALL_HISTORY_LIMIT = 8
_LOOP_ESCALATION_THRESHOLD = 3

_READ_ONLY_TOOLS = {"cortex_status", "cortex_search", "cortex_scope_pack"}
_WRITE_TOOLS = {"cortex_fetch_doc", "cortex_write_log"}
_DOC_TOOLS = {"cortex_search", "cortex_scope_pack"}

# Forced pipeline (provisional default, 2026-07-06). A harness-less agent (Hermes) staring at
# the full tool surface (30 registered tools as of G5, 2026-07-14) with no guidance is useless
# -- the live failure this fixes. So the MCP hands every
# connecting agent an ordered, MANDATORY pipeline and enforces docs-before-write: you cannot
# write to the workspace until you have consulted the brain. The *winning* routing is being
# decided empirically (docs/research/BAKEOFF-PROTOCOL-*.md, the L1-force vs suggest vs L2
# bake-off); until that lands, FORCE is the default per the L1 hypothesis that mandatory
# doc-consultation helps weak models. Toggle: CORTEX_FORCED_PIPELINE=0 keeps the guidance but
# drops the hard gate.
# v1 = the forced tiered lifecycle (the current default). v2 = whatever the bake-off crowns
# (the L1-force vs suggest vs L2 experiment; the working prior is that FORCE wins for small
# models). The MCP serves these ordered steps as the mandatory default and enforces
# docs-before-write; the multi-model steps (TDD by a strong model, implement by a cheap one,
# review by a big one) are the client's to orchestrate, but the ORDER is forced here.
_FORCED_PIPELINE_VERSION = "v1"
_FORCED_PIPELINE_STEPS = [
    "1. SEARCH THE BRAIN FIRST (fast scan): cortex_search / cortex_scope_pack over the "
    "canonical brain, THEN your own local copy -- search brain -> audit-logs + docs.",
    "2. GAP? If no doc/audit-log covers your problem: FAN OUT -- YOU (the client/harness) spawn "
    "subagents to fetch docs (cortex_fetch_doc) in parallel while you fetch others yourself "
    "(research phase). The server does NOT launch agents; it coordinates their claims/leases.",
    "3. PLAN: cortex_contract -- decompose into phased, bounded tasks.",
    "4. PHASE + RESUME: cortex_run_start gives an 8-minute phase lease + resume_key; use "
    "cortex_phase_checkpoint/heartbeat/resume for long work and cortex_report_empty_output for blank turns.",
    "5. TDD + REASONING PASS: a strong model writes the success conditions/tests with an "
    "explicit reasoning pass (the executable spec). YOU pick/route that model -- the server calls none.",
    "6. IMPLEMENT: a lower-tier model builds against the frozen tests (if available). YOU route it.",
    "7. REVIEW: a big model reviews until satisfied; on failure ITERATE -- back to the start "
    "(or loop until the quality tests pass, then re-ground in the local research phase). YOU route it.",
    "8. cortex_write_log: closeout, ALWAYS -- even on failure (the self-learning record).",
]


# Honest capability boundary (GAP I6 doc/capability mismatch): what the SERVER actually does vs
# what the CLIENT must orchestrate. The pipeline FORCES the step ORDER and provides coordination
# primitives (claim partitioning, phase leases, mission records via cortex_spawn_mission /
# cortex_run_start). It does NOT spawn processes and does NOT call models on your behalf -- the
# multi-agent fan-out (step 2) and multi-model tiering (steps 5-7) are the client/harness's job.
_ORCHESTRATION_BOUNDARY = (
    "the server FORCES the step ORDER and provides coordination primitives "
    "(claims/leases/mission records); SPAWNING subagents and ROUTING models across tiers are the "
    "CLIENT's job -- the server launches no processes and calls no models for you"
)


def _forced_pipeline_on() -> bool:
    import os
    return (os.environ.get("CORTEX_FORCED_PIPELINE", "0").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _has_consulted_docs(session: dict[str, Any] | None) -> bool:
    return bool(session) and any(c in _DOC_TOOLS for c in session.get("calls", []))


def _forced_docs_gate(
    session_id: str | None, tool: str, workspace: str | None, override_reason: str
) -> dict[str, Any] | None:
    """Forced-pipeline default: a registered session must consult the brain
    (cortex_search / cortex_scope_pack) before it may write. Refusable via an
    override reason (logged); session-less (CLI) contexts are not gated -- same
    trust model as _contract_gate. Off when CORTEX_FORCED_PIPELINE=0."""
    if not _forced_pipeline_on() or not session_id:
        return None
    if _has_consulted_docs(_sessions.get(session_id)):
        return None
    if override_reason:
        _log_event(session_id, "forced_docs_override", workspace, gated_tool=tool, reason=override_reason)
        return None
    _log_event(session_id, "forced_docs_refused", workspace, gated_tool=tool)
    return {
        "refused": True,
        "tool": tool,
        "reason": "forced pipeline default: consult the brain before writing",
        "how_to_comply": (
            "call cortex_search(query=...) or cortex_scope_pack(task=...) first so your work is "
            "grounded in the corpus, then retry this write -- or pass contract_override_reason "
            "to bypass in an emergency (it is logged)"
        ),
        "pipeline": _FORCED_PIPELINE_STEPS,
    }


def _read_ws(workspace: str | None, session_id: str | None = None) -> str:
    """READ-plane workspace resolution -- dual-plane brain routing WITH explicit-override
    precedence, done with the SAME care as ``_write_ws``.

    Two independent axes:

    - Dual-plane brain routing (GAP-CORTEX-0015 H2a): when ``CORTEX_BRAIN_WORKSPACE`` is set,
      reads (search / scope_pack / status / ontology / playbook) resolve to the canonical brain
      while writes stay on the tenant's ``CORTEX_WORKSPACE``. Unset => single-plane. This axis is
      preserved exactly and must NOT regress.

    - Explicit-override precedence (this fix): ``.mcp.json`` hardcodes ``CORTEX_WORKSPACE`` for
      every ``cortex-local`` session, and env-first ``resolve_brain_workspace`` silently overrode
      an explicit ``workspace=<path>`` back to the pinned env value -- so an explicit
      ``cortex_ontology_query(workspace=<repo>)`` read the wrong (empty) corpus. The read-plane
      twin of the write-plane bug.

    Resolution:
    - ``workspace`` given AND the session is NOT tenant-pinned (owner mode, or a served ADMIN) ->
      the explicit override wins over both the brain env and the ``CORTEX_WORKSPACE`` pin
      (``resolve_brain_workspace_override``).
    - ``workspace`` omitted, OR a tenant-pinned (served, non-admin) session -> env-first
      ``resolve_brain_workspace`` (a served tenant reads the canonical brain and can NEVER use an
      explicit ``workspace=`` to escape into a foreign corpus -- GAP-CORTEX-0015 on the read plane).
    """
    if workspace is not None and not _tenant_pinned(session_id):
        return str(resolve_brain_workspace_override(workspace))
    return str(resolve_brain_workspace(workspace))


def _dual_plane() -> bool:
    """True when a distinct brain (read) plane is configured -- so tenant writes land in
    their own CORTEX_WORKSPACE and can never touch the brain."""
    import os
    return bool((os.environ.get(BRAIN_WORKSPACE_ENV) or "").strip())


def _tenant_pinned(session_id: str | None) -> bool:
    """True when THIS session's writes must stay pinned to CORTEX_WORKSPACE and may NOT be
    redirected by an explicit ``workspace=`` override -- i.e. a served-mode, non-admin (tenant)
    session. In owner mode (default, local) there is no tenant pin: the owner may point writes
    anywhere. A served-mode ADMIN owns the box and is likewise unpinned. This is the
    GAP-CORTEX-0015 tenant-isolation guard for the WRITE plane: an OMITTED workspace already
    falls back to the pin (resolve_workspace is env-first); this is what stops an EXPLICIT
    override from letting a served tenant escape it."""
    if not mutation_requires_admin():  # owner mode -- no tenant pin
        return False
    session = _sessions.get(session_id or "")
    return not bool(session and session.get("is_admin"))


def _write_ws(workspace: str | None, session_id: str | None) -> Path:
    """WRITE-plane workspace resolution with EXPLICIT-OVERRIDE precedence -- done safely.

    The bug this fixes: ``.mcp.json`` hardcodes ``CORTEX_WORKSPACE`` for every session on the
    ``cortex-local`` server, and plain ``resolve_workspace(workspace)`` is env-first, so a call
    that explicitly passes ``workspace=<run_dir>`` (agent_runner's per-task sandbox, an ontology
    update, etc.) is silently overridden back to the pinned env value -- the opposite of what the
    caller asked for.

    Resolution:
    - ``workspace`` given AND the session is NOT tenant-pinned -> the explicit override wins over
      ``CORTEX_WORKSPACE`` (``resolve_workspace_override``).
    - ``workspace`` omitted, OR a tenant-pinned (served, non-admin) session -> env-first
      ``resolve_workspace`` (the pin holds; a served tenant can never escape its
      ``CORTEX_WORKSPACE`` -- GAP-CORTEX-0015 must not regress).
    """
    if workspace is not None and not _tenant_pinned(session_id):
        return resolve_workspace_override(workspace)
    return resolve_workspace(workspace)


# --- Workspace-resolution crash guard (stability audit finding #1, 2026-07-07) ----------------
# `resolve_workspace`/`find_repo_root` (config.py) raise a bare `FileNotFoundError` when
# `CORTEX_WORKSPACE` points at a missing path, or an explicit `workspace=` isn't a real Cortex
# checkout, and nothing caught it: a stale env var or a bad explicit path took down EVERY
# workspace-touching tool call with a raw traceback instead of the guided refusal every other
# gate in this file already produces. Wrap tool bodies (below `@mcp.tool()`, so the signature
# FastMCP introspects is unaffected -- `functools.wraps` makes `inspect.signature` follow
# `__wrapped__` through to the real function) so this degrades cleanly instead.
def _workspace_refusal(tool: str, workspace: str | None, exc: FileNotFoundError) -> dict[str, Any]:
    return {
        "refused": True,
        "tool": tool,
        "reason": f"workspace resolution failed: {exc}",
        "how_to_comply": (
            "the configured workspace could not be resolved -- either CORTEX_WORKSPACE points "
            "at a path that no longer exists, or an explicit workspace= argument isn't a real "
            "Cortex checkout (needs cortex.json + library/cortex-library, or docs/audit/library "
            "present). Pass a valid workspace= explicitly, or fix/unset CORTEX_WORKSPACE."
        ),
    }


def _guard_workspace(tool: str):
    """Catch `FileNotFoundError` raised anywhere in the wrapped tool body (typically from
    `_read_ws`/`_write_ws`/`_run_engine`/`resolve_workspace*`) and return a clean refusal dict
    instead of letting it propagate as an uncaught crash."""

    def deco(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except FileNotFoundError as exc:
                    return _workspace_refusal(tool, kwargs.get("workspace"), exc)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except FileNotFoundError as exc:
                return _workspace_refusal(tool, kwargs.get("workspace"), exc)

        return wrapper

    return deco


# --- Mandatory state machine (Decision B, 2026-07-07) -----------------------------------------
# Weak models cannot reliably self-drive a task loop; leaving "use the state machine or don't" as
# a judgment call harms exactly the customers Cortex is for. So, mirroring the docs-before-write
# gate (_forced_docs_gate), a registered session must have driven at least one unit of work
# through the server chart (cortex_run_start -> cortex_run_step) to a terminal DONE before the
# free-standing write tools (cortex_write_log / cortex_fetch_doc) are legal. The chart walk IS the
# grounded, gated closeout. Toggle: CORTEX_MANDATORY_STATE_MACHINE=0 keeps the chart available but
# drops the hard gate. Escape hatch: state_machine_override_reason (logged), for legitimate
# callers that genuinely cannot go through the chart (e.g. the benchmark harness, which runs its
# own loop). KNOWN LIMITATION: this enforces that SOME task reached DONE this session, not that
# THIS write corresponds to real work done via the chart -- a session can drive a trivial/fake
# task to DONE to unlock writes. It raises the floor for weak models; it is not tamper-proof.


def _mandatory_state_machine_on() -> bool:
    import os
    return (os.environ.get("CORTEX_MANDATORY_STATE_MACHINE", "0").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _contract_gate_on() -> bool:
    """Phase 4.2 contract gate toggle. Default OFF — the contract gate
    manufactures tool-call loops by refusing writes until the agent has
    called cortex_contract. Opt in with CORTEX_CONTRACT_GATE=1."""
    import os
    return (os.environ.get("CORTEX_CONTRACT_GATE", "0").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _admin_gate_on() -> bool:
    """Admin coercion gate toggle. Default OFF — the admin gate's coercion
    behavior (refusing writes in served mode, instructing the model to call
    more tools) manufactures tool-call loops. Opt in with CORTEX_ADMIN_GATE=1.

    NOTE: Security enforcement (read-scope key refusal, tenant isolation)
    runs REGARDLESS of this toggle — only the coercion refusals are gated.
    See _admin_gate implementation for the split."""
    import os
    return (os.environ.get("CORTEX_ADMIN_GATE", "0").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _session_completed_run(session: dict[str, Any] | None) -> bool:
    return bool(session) and bool(session.get("completed_run"))


def _state_machine_gate(
    session_id: str | None, tool: str, workspace: str | None, override_reason: str
) -> dict[str, Any] | None:
    """Mandatory-state-machine default: a registered session must have driven a task through the
    server chart to a terminal DONE before it may use the free-standing write tools. Same trust
    model as _forced_docs_gate / _contract_gate: session-less (CLI) contexts are NOT gated;
    refusable via a logged override reason. Off when CORTEX_MANDATORY_STATE_MACHINE=0."""
    if not _mandatory_state_machine_on() or not session_id:
        return None
    if _session_completed_run(_sessions.get(session_id)):
        return None
    if override_reason:
        _log_event(session_id, "state_machine_override", workspace, gated_tool=tool,
                   reason=override_reason)
        return None
    _log_event(session_id, "state_machine_refused", workspace, gated_tool=tool)
    return {
        "refused": True,
        "tool": tool,
        "reason": ("mandatory state machine: drive this unit of work through the server chart to "
                   "a terminal DONE before writing"),
        "how_to_comply": (
            "call cortex_run_start(intent={'seeking': '<this task>'}) then cortex_run_step "
            "repeatedly -- follow each envelope's legal_tools/instruction -- until the task "
            "reaches state DONE (that walk IS the grounded closeout). Then retry this write. For a "
            "case that genuinely cannot go through the chart, pass "
            "state_machine_override_reason='...' to bypass (it is logged)."
        ),
        "chart_tracks": ["build", "research"],
    }


def _session_no_log(session: dict[str, Any] | None) -> bool:
    """GAP G6 per-tenant no-log enforcement. True => suppress ALL usage logging/mirroring for this
    session (the event log AND, downstream, the R2 session-record mirror + search telemetry).

    Rule (matches DATA-USE.md / consent_status.py -- silence is not consent):
      * No tenant_id (owner/local/CLI/unkeyed session, or an unknown session): NEVER suppressed --
        this is the owner's own brain on the owner's machine; the self-learning corpus depends on it.
      * A keyed tenant (collaborator): suppressed if the owner set the key's `no_log` flag
        (server-enforced -- a lying client can't turn logging back on), OR the tenant has not
        affirmatively consented. Logs only when the session's effective capture signal is 'consent';
        an absent/opt-out/DO_NOT_TRACK signal defaults to opt-out."""
    if not session or not session.get("tenant_id"):
        return False
    if session.get("no_log"):
        return True
    return session.get("data_capture") != "consent"


def _log_event(session_id: str | None, tool: str, workspace: str | None, **detail: Any) -> None:
    """Fire-and-forget event log (Phase 3's own exit criteria: "every server
    span carries declared_model + role") -- a tool call must never fail
    because logging did. Deliberately scoped to MCP tool calls, not a
    general flight recorder."""
    try:
        session = _sessions.get(session_id or "", {})
        # GAP G6: a no-log tenant's usage is never recorded (and so never mirrored to R2, which
        # reads only from this event log). Returns before any file is touched.
        if _session_no_log(session):
            return
        # Route the event log to the same WRITE plane the call actually resolved to, so a
        # sandboxed run's events land in its own workspace (not the pinned CORTEX_WORKSPACE).
        ws = _write_ws(workspace, session_id)
        log_path = ws / "logs" / "mcp-events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Same size-based rotation as search telemetry (gate 0.7): bound the
        # event log so it can't grow without limit.
        _rotate_log_if_large(log_path)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "agent_id": session.get("agent_id"),
            "declared_model": session.get("model"),
            "role": session.get("role"),
            "tool": tool,
            **detail,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _record_call(session_id: str | None, tool: str) -> None:
    with _sessions_lock:
        session = _sessions.get(session_id or "")
        if session is None:
            return
        history = session.setdefault("calls", [])
        history.append(tool)
        del history[:-_call_history_limit()]


def _call_history_limit() -> int:
    import os

    raw = os.environ.get("CORTEX_SESSION_CALL_HISTORY_MAX", "").strip()
    if not raw:
        return _DEFAULT_CALL_HISTORY_LIMIT
    try:
        return max(3, int(raw))
    except ValueError:
        return _DEFAULT_CALL_HISTORY_LIMIT


def _contract_gate(
    session_id: str | None, tool: str, workspace: str | None, override_reason: str
) -> dict[str, Any] | None:
    """Phase 4.2 write gate. Returns a refusal dict if the write must be blocked,
    or None to allow it.

    Trust model (matches BUILD-PLAN Phase 4): the gate binds the *registered
    agent session* -- the context where accountability lives. A call with no
    session_id is a human/CLI context and is not gated (that bypass isn't
    pretended-away: it's caught by the closeout-coverage-vs-git SLI, per the
    gate's own pitfall). A registered session must have an APPROVED contract, or
    supply an explicit `override_reason` (the anti-deadlock escape hatch, logged).

    Default OFF (CORTEX_CONTRACT_GATE defaults "0") — the contract gate
    manufactures tool-call loops by refusing writes until the agent has called
    cortex_contract, each refusal instructing the model to call MORE tools.
    Opt in with CORTEX_CONTRACT_GATE=1."""
    if not _contract_gate_on() or not session_id:
        return None  # gate off, or human/CLI context -- not the gated agent flow
    session = _sessions.get(session_id)
    if session is not None and session.get("contract_approved"):
        return None
    if override_reason:
        _log_event(session_id, "contract_override", workspace, gated_tool=tool, reason=override_reason)
        return None
    _log_event(session_id, "contract_refused", workspace, gated_tool=tool)
    return {
        "refused": True,
        "tool": tool,
        "reason": "no approved approach contract for this session (Phase 4 verified write path)",
        "how_to_comply": (
            "call cortex_contract(task=...) to get a corpus-prefilled stub, fill in "
            "planned_approach + acceptance_criteria[] + verification_steps[], and call "
            "cortex_contract again to approve it -- then retry this write. For genuinely "
            "trivial work pass a short contract; to bypass in an emergency pass "
            "contract_override_reason='...' (it is logged)."
        ),
    }


def _admin_gate(session_id: str | None, tool: str, workspace: str | None) -> dict[str, Any] | None:
    """Ownership / immutability gate (docs/CORTEX-ROUTES-AND-OWNERSHIP.md).

    Distinct from `_contract_gate` (which binds per-session accountability): this
    gate answers *ownership*. In `owner` mode (default -- the machine that owns
    the files) mutations flow as before; the owner is implicitly admin. In
    `served` mode (the canonical instruction server exposed to connected agents)
    the corpus/rubrics/gold are IMMUTABLE without admin authentication: a
    mutation is allowed only if this session presented a valid admin token at
    register time. Reads (status/search/scope_pack) are never gated here.

    Runs BEFORE `_contract_gate` on every write tool, so an un-authenticated
    connected agent gets the ownership refusal (the real boundary) rather than a
    contract prompt it could never satisfy on a corpus it doesn't own."""
    # A READ-scoped API key is read-only EVERYWHERE (defense in depth), regardless of server mode --
    # a read key must never write even in owner mode. tenant_write/admin keys and the owner fall through.
    session = _sessions.get(session_id or "")
    if session is not None and session.get("scope") == "read":
        _log_event(session_id, "scope_refused", workspace, gated_tool=tool, scope="read")
        return {"refused": True, "tool": tool,
                "reason": "this session authenticated with a READ-scoped API key; writes are not permitted",
                "how_to_comply": "obtain a tenant_write-scoped key (owner: cortex_rotate_key can re-scope)"}
    if not mutation_requires_admin():
        return None  # owner mode: local owner is implicitly admin
    if _dual_plane():
        # DUAL-PLANE (GAP-0015 H2a): a distinct brain (read) plane is configured, so this
        # write resolves to the TENANT's own CORTEX_WORKSPACE, never the brain. Allow it --
        # "read my brain, write your folder." The brain is protected by SEPARATION here,
        # not by refusal. (Single-plane served mode below still refuses, since there is no
        # separate tenant plane to redirect the write to.)
        return None
    session = _sessions.get(session_id or "")
    if session is not None and session.get("is_admin"):
        return None
    # Coercion gate (default OFF): the served-mode write refusal that instructs
    # the model to re-register with admin_token manufactures tool-call loops.
    # Security checks above (read-scope key refusal, dual-plane redirect) still run.
    # Opt in with CORTEX_ADMIN_GATE=1 for the original served-mode behavior.
    if not _admin_gate_on():
        return None  # coercion off -- security already enforced above
    _log_event(session_id, "admin_refused", workspace, gated_tool=tool)
    return {
        "refused": True,
        "tool": tool,
        "reason": (
            "this Cortex is a served, admin-owned instruction server; its "
            "corpus/rubrics/gold are immutable without admin authentication"
        ),
        "how_to_comply": (
            "re-register with a valid admin token -- cortex_register(agent_id=..., "
            "model=..., admin_token='...') -- to gain write access. Without it you "
            "have full READ access (cortex_search / cortex_status / cortex_scope_pack) "
            "to the canonical instruction set; that read surface is the point. To make "
            "your OWN writes, point CORTEX_WORKSPACE at your own per-user workspace "
            "instead of this canonical one."
        ),
    }


def _next_actions(session_id: str | None, *, default: str) -> str:
    """Centralized hint generator (gate 3.2: "generate hints from state,
    don't hardcode prose in two places"). Every tool passes its own
    state-derived default; this only overrides it to break a detected
    read-only guidance loop, capped so the escalation can't itself loop --
    it fires once per streak, not once per call.

    Not verified from a full flight recorder (none exists -- that was
    unbuilt Phase 0 scope); this reasons only from the bounded per-session
    call history mini-phase 3.2 needs, which is proportionate to what's
    actually being decided here."""
    session = _sessions.get(session_id or "")
    if session is None:
        return default
    # Forced pipeline (v1): until the session has consulted the brain, the only next step is
    # to search it -- the "docs first" spine of the forced default.
    if _forced_pipeline_on() and not _has_consulted_docs(session):
        return ("cortex_search / cortex_scope_pack FIRST -- forced pipeline: ground yourself "
                "in the brain before acting (search brain -> audit-logs + docs)")
    history: list[str] = session.get("calls", [])
    if len(history) < _LOOP_ESCALATION_THRESHOLD:
        return default
    recent = history[-_LOOP_ESCALATION_THRESHOLD:]
    if all(call in _READ_ONLY_TOOLS for call in recent) and not any(
        call in _WRITE_TOOLS for call in history
    ):
        return (
            f"{default} -- but you've read {len(recent)}+ times this session with no "
            "cortex_fetch_doc/cortex_write_log yet; either act on what you've found or "
            "write a closeout now, don't keep polling"
        )
    return default


@mcp.tool()
def cortex_register(
    agent_id: str, model: str, role: str = "agent", workspace: str | None = None,
    admin_token: str | None = None, config_passcode: str | None = None,
    api_key: str | None = None, data_capture: str | None = None,
    do_not_track: bool = False, role_credential: dict | None = None,
) -> dict[str, Any]:
    """Register this session's agent id, declared model, and role. Call once
    at connect time, before any other cortex_* tool. Pass the returned
    session_id to every subsequent call so next-step guidance can track
    what this session has already done. workspace is optional (register
    doesn't need one to function) but pass it if known, so this event lands
    in the same per-workspace log as the calls that follow it.

    admin_token: only relevant when this Cortex runs in `served` mode (a shared,
    admin-owned instruction server). Present the admin token to authenticate for
    WRITE access to the canonical corpus; it's verified against a server-side
    hash and never logged (only the boolean result is). Omit it for normal
    read-only access. In `owner` mode (default, local) it's ignored -- the
    machine owner already has write access. See docs/CORTEX-ROUTES-AND-OWNERSHIP.md.

    api_key: an issued per-tenant key (cortex_issue_key). Present it so this session is
    scoped to that key -- a `read` key is read-only (writes refused everywhere), a
    `tenant_write` key may write to its own tenant plane. Distinct from admin_token.

    data_capture / do_not_track: GAP G6 client-honored data-capture choice (see DATA-USE.md).
    A collaborator's wrapper transmits its effective consent here: data_capture='consent' opts in
    to usage logging; anything else (or absent) defaults to opt-out -- silence is not consent. If
    do_not_track is true (the DO_NOT_TRACK convention) capture is forced to opt-out regardless. This
    applies only to keyed tenants; an unkeyed owner/CLI session logs as before. Server-side, the
    owner can also force no-log per tenant via `cortex-key no-log --set` -- that always wins.

    config_passcode: only relevant when a local config passcode is configured
    (CORTEX_CONFIG_PASSCODE_SHA256). Repointing this session at a `workspace` override is a
    LOCAL CONFIG CHANGE; when the passcode is set it must be presented here, else the register
    is refused. Default (no passcode configured) is unchanged -- a connected agent registers
    normally. This stops an agent from silently re-pointing the harness at another workspace."""
    # Local config guard (opt-in, backward-compatible): a workspace override is only gated when
    # the owner has configured a config passcode; otherwise behavior is exactly as before.
    if workspace is not None and config_change_requires_passcode():
        allowed, reason = authorize_config_change(config_passcode)
        if not allowed:
            return {"error": "config_change_refused", "reason": reason,
                    "server_mode": resolve_server_mode()}
    session_id = str(uuid.uuid4())
    is_admin = verify_admin_token(admin_token)
    # If the client authenticated with an issued API key, stamp the session with its scope + tenant
    # so the write gate can enforce read-only keys and (later) route writes to the tenant's plane.
    scope, tenant_id, key_no_log = None, None, False
    key_info = None
    if api_key:
        from cortex_core.keys import verify_key
        info = verify_key(api_key)
        if info:
            scope, tenant_id = info["scope"], info["tenant_id"]
            key_no_log = bool(info.get("no_log", False))
            key_info = info
    # GAP G6: resolve this session's effective capture choice. DO_NOT_TRACK forces opt-out; else the
    # client's data_capture signal (only 'consent' opts in). The owner's per-key no_log always wins.
    capture = "opt-out" if do_not_track else (
        "consent" if str(data_capture).strip().lower() == "consent" else "opt-out")
    session_no_log = key_no_log or (tenant_id is not None and capture != "consent")
    # Authenticate the role instead of trusting the self-claim: a PRIVILEGED role
    # (admin/gold_author/trainer) is only granted with a valid server-signed credential bound to
    # the presenting key; an unprivileged claim passes through as-is. Closes the "trusts an
    # arbitrary self-claimed role" hole. Never-wait preserved -- a rejected privileged claim is
    # downgraded to a working unprivileged session, not blocked.
    from cortex_core.attestation import authenticate_role
    auth_role, role_authenticated, role_reason = authenticate_role(
        role, role_credential, key_info=key_info)
    with _sessions_lock:
        _sessions[session_id] = {
            "agent_id": agent_id, "model": model, "role": auth_role, "claimed_role": role,
            "role_authenticated": role_authenticated, "calls": [], "is_admin": is_admin,
            "scope": scope, "tenant_id": tenant_id,
            "data_capture": capture, "no_log": session_no_log,
        }
    # Log only the boolean outcome, never the token itself.
    _log_event(session_id, "cortex_register", workspace, agent_id=agent_id, is_admin=is_admin)
    # Bootstrap the file/folder governance contract (AGENTS.md + MANIFEST.md/manifest.json)
    # into this workspace if it doesn't have one yet. Idempotent, never overwrites an existing
    # one. This is what stops agents from dumping scratch output straight into the workspace
    # root -- fixed by hand once in one workspace, now applies to every workspace this server
    # ever registers a session against.
    scaffold_created: list[str] = []
    swept: list[str] = []
    if workspace:
        try:
            from cortex_core.workspace_scaffold import ensure_workspace_scaffold
            scaffold_created = ensure_workspace_scaffold(workspace).get("created", [])
            # Enforcement backstop, not just documentation: an agent writing raw files via
            # its own tooling (never touching an MCP call) will never read AGENTS.md -- observed
            # live, twice, same night. Sweep whatever landed in the root outside the naming
            # contract on every register, so the contract holds even when nothing cooperates.
            from cortex_core.workspace_sweep import sweep_workspace_root
            swept = sweep_workspace_root(workspace).get("moved", [])
        except OSError:
            pass
    return {
        "session_id": session_id,
        "next": "cortex_status",
        "server_mode": resolve_server_mode(),
        "is_admin": is_admin,
        "role": auth_role,
        "role_authenticated": role_authenticated,
        "role_note": role_reason,
        "workspace_scaffold_created": scaffold_created,
        "workspace_swept": swept,
        # Forced pipeline default (v1). Hand every connecting agent the mandatory ordered
        # pipeline up front. Task-scoped: for casual conversation you do not need this server;
        # engage the pipeline when you have a TASK to do.
        "pipeline_version": _FORCED_PIPELINE_VERSION,
        "pipeline": _FORCED_PIPELINE_STEPS if _forced_pipeline_on() else None,
        "orchestration_boundary": _ORCHESTRATION_BOUNDARY,
        "onboarding": "new here? call cortex_onboarding for the full operating guide (what each tool "
                      "is for, when to use it, the RAG flow, reasoning tiers, disciplines)",
        "when_to_use": "engage this pipeline for TASKS; skip it for ordinary conversation",
    }


@mcp.tool()
async def cortex_onboarding(session_id: str | None = None) -> dict[str, Any]:
    """The server's self-describing operating guide: what each tool is for and WHEN to use it, the
    canonical pipeline, the RAG flow, per-stage reasoning tiers, and the disciplines. If you're a
    fresh agent, call this before acting -- the brain tells you how to operate it, in-band. The guide
    is generated from the live tool list + pipeline + policy, so it can't drift from what's real."""
    from cortex_core.onboarding import build_onboarding
    _record_call(session_id, "cortex_onboarding")
    tool_names = [t.name for t in await mcp.list_tools()]
    return build_onboarding(tool_names, _FORCED_PIPELINE_STEPS)


@mcp.resource("cortex://onboarding")
async def onboarding_resource() -> str:
    """The operating guide as a fetchable RESOURCE (same content as the cortex_onboarding tool), for
    clients that prefer resources over tool calls. Generated from live state, so it can't drift."""
    from cortex_core.onboarding import build_onboarding
    tool_names = [t.name for t in await mcp.list_tools()]
    return json.dumps(build_onboarding(tool_names, _FORCED_PIPELINE_STEPS), indent=2)


def _key_admin_ok(session_id: str | None, admin_token: str | None) -> bool:
    """Minting/managing API keys is owner/admin-only. Owner mode (local) = allowed; served mode
    requires an admin session or a valid admin token."""
    if resolve_server_mode() != "served":
        return True
    session = _sessions.get(session_id or "")
    return bool(session and session.get("is_admin")) or verify_admin_token(admin_token)


def cortex_issue_key(label: str, scope: str = "read", tenant_id: str | None = None,
                     admin_token: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """Mint a scoped API key for a client (a browser extension, any connecting agent). Owner/admin
    only. Returns the raw key ONCE -- store it now; only its hash is kept. scope: 'read' (search +
    guidance) or 'tenant_write' (writes land in that key's own tenant plane) -- never admin. Rotate or
    revoke it independently with cortex_rotate_key / cortex_revoke_key."""
    if not _key_admin_ok(session_id, admin_token):
        return {"error": "admin_required", "note": "issuing keys is owner/admin only"}
    from cortex_core import keys
    key_id, raw = keys.issue_key(label, scope=scope, tenant_id=tenant_id)
    _log_event(session_id, "cortex_issue_key", None, key_id=key_id, scope=scope)  # never log the raw key
    return {"key_id": key_id, "api_key": raw, "scope": scope,
            "warning": "shown ONCE -- store it now; only its SHA-256 is kept server-side"}


def cortex_rotate_key(key_id: str, admin_token: str | None = None,
                      session_id: str | None = None) -> dict[str, Any]:
    """Rotate an API key: revoke the old one and mint a fresh key with the same scope/tenant. Returns
    the new raw key ONCE. Owner/admin only."""
    if not _key_admin_ok(session_id, admin_token):
        return {"error": "admin_required"}
    from cortex_core import keys
    try:
        new_id, raw = keys.rotate_key(key_id)
    except KeyError:
        return {"error": "no_such_key", "key_id": key_id}
    _log_event(session_id, "cortex_rotate_key", None, rotated_from=key_id, key_id=new_id)
    return {"key_id": new_id, "api_key": raw, "rotated_from": key_id,
            "warning": "shown ONCE; the old key is now revoked"}


def cortex_revoke_key(key_id: str, admin_token: str | None = None,
                      session_id: str | None = None) -> dict[str, Any]:
    """Revoke an API key immediately (kill a leaked/retired key). Owner/admin only."""
    if not _key_admin_ok(session_id, admin_token):
        return {"error": "admin_required"}
    from cortex_core import keys
    ok = keys.revoke_key(key_id)
    _log_event(session_id, "cortex_revoke_key", None, key_id=key_id, revoked=ok)
    return {"revoked": ok, "key_id": key_id}


def cortex_list_keys(admin_token: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """List issued API keys -- METADATA ONLY (key_id, label, scope, tenant, status), never the raw
    key or its hash. Owner/admin only."""
    if not _key_admin_ok(session_id, admin_token):
        return {"error": "admin_required"}
    from cortex_core import keys
    return {"keys": keys.list_keys()}


@mcp.tool()
@_guard_workspace("cortex_status")
async def cortex_status(session_id: str | None = None, workspace: str | None = None) -> dict[str, Any]:
    """Report workspace health: index state, doctor diagnostics, and a
    next-step hint. Call after cortex_register to orient before acting.
    Pass your session_id so guidance can track what you've already done."""
    _record_call(session_id, "cortex_status")
    index = CortexSearchIndex(_read_ws(workspace, session_id))
    # Threaded (gate 3.1 pitfall: sync tool bodies run directly on FastMCP's
    # event loop -- verified by reading FuncMetadata.call_fn_with_arg_validation,
    # which does not dispatch sync functions to a thread itself -- so any call
    # here that touches disk/SQLite must opt in via asyncio.to_thread).
    status = await asyncio.to_thread(index.status)
    # Keep the activation/status path subprocess-free.  ``doctor`` normally
    # runs ``git status``; under a Windows stdio MCP process a timed-out Git
    # child can retain a pipe handle and make subprocess cleanup wait for
    # minutes.  Full Git hygiene remains available through ``cortex-doctor``
    # and this response carries an explicit skipped receipt.
    diagnostics = await asyncio.to_thread(
        doctor, workspace, json_output=True, include_git_hygiene=False
    )
    from .model_catalog import build_model_catalog, compact_model_catalog
    model_catalog = await asyncio.to_thread(
        build_model_catalog, _read_ws(workspace, session_id)
    )
    if not status.get("index_exists"):
        default = "no index yet -- run cortex_search once, or cortex_fetch_doc to add a source first"
    elif status.get("stale"):
        default = "index is stale -- cortex_search will rebuild it automatically"
    else:
        default = "cortex_search"
    _log_event(session_id, "cortex_status", workspace, index_exists=status.get("index_exists"))
    # Ownership self-orientation (docs/CORTEX-ROUTES-AND-OWNERSHIP.md): tell the
    # caller which mode this Cortex serves and whether *this* session may write,
    # so an agent knows up front if it's read-only here rather than discovering
    # it on a refused write.
    mode = resolve_server_mode()
    session = _sessions.get(session_id or "")
    can_write = (mode != "served") or bool(session and session.get("is_admin"))
    access = {
        "server_mode": mode,
        "is_admin": bool(session and session.get("is_admin")),
        "can_write": can_write,
        "note": (
            "read-only access to this canonical instruction server; register with an "
            "admin_token to write, or use your own CORTEX_WORKSPACE for your own writes"
            if not can_write else
            "full read/write access in this workspace"
        ),
    }
    return {**status, "doctor": diagnostics, "access": access,
            "model_catalog": compact_model_catalog(model_catalog),
            "next": _next_actions(session_id, default=default)}


@mcp.tool()
async def cortex_fingerprint(path: str, session_id: str | None = None) -> dict[str, Any]:
    """Fingerprint a file -- {size, mtime, sha256} -- so you can detect a STALE read
    (GAP-CORTEX-0013): capture this when you READ a file, then compare before you ACT on that
    read. If it changed, another process edited the file and your context is stale -- re-read
    before writing. Read-only; hashes bytes, touches nothing."""
    from .fingerprint import fingerprint as _fp
    _record_call(session_id, "cortex_fingerprint")
    return await asyncio.to_thread(_fp, path)


@mcp.tool()
def cortex_dispatch_tier(
    tier: str = "", prompt: str = "", session_id: str | None = None, max_tokens: int = 1500,
    action: str = "dispatch", requirements: dict[str, Any] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Model dispatcher. ``catalog`` returns the key-free roster, probe freshness,
    stage fit, and blockers without a model call. ``route`` creates a no-spend,
    capability-qualified route receipt or returns UNRESOLVED. ``dispatch`` makes one
    explicit local-owner completion against ``tier``; it never returns credentials and
    is refused in served mode. Unknown or misconfigured lanes fail explicitly."""
    if action == "catalog":
        from .model_catalog import build_model_catalog
        try:
            catalog = build_model_catalog(_read_ws(workspace, session_id))
        except ValueError as exc:
            return {"ok": False, "code": "MODEL_CATALOG_INVALID", "reason": str(exc)}
        _log_event(session_id, "cortex_dispatch_tier", workspace, action="catalog",
                   known_lanes=catalog["summary"]["known_lanes"])
        return {"ok": True, "catalog": catalog}
    if action == "route":
        from .capability_router import route_model
        try:
            route = route_model(requirements or {}, workspace=_write_ws(workspace, session_id))
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "code": "MODEL_ROUTE_UNRESOLVED", "reason": str(exc)}
        _log_event(session_id, "cortex_dispatch_tier", workspace, action="route",
                   route_id=route["route_id"], outcome=route["outcome"])
        return {"ok": route["outcome"] == "ROUTED", "route": route}
    if action != "dispatch":
        return {"ok": False, "code": "UNKNOWN_DISPATCH_ACTION",
                "valid_actions": ["catalog", "route", "dispatch"]}
    mode = resolve_server_mode()
    if mode == "served":
        return {
            "ok": False,
            "error": (
                "cortex_dispatch_tier is LOCAL-ONLY and refuses to run in served mode "
                "(CORTEX_SERVER_MODE=served). These dispatch tiers have not been reviewed "
                "for multi-tenant exposure -- this is an explicit scope boundary, not a bug. "
                "Run this Cortex server in owner mode to use it."
            ),
        }
    try:
        from .judge import get_tier_config
        get_tier_config(tier)  # validates url/key/model + allowlist before spending a call
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": (
                f"tier {tier!r} is not usable: {exc} Check cortex_core/judge.py's tier table "
                "and this repo's .env -- fix the config there, never guess an endpoint or "
                "model id."
            ),
        }
    try:
        from .agent_runner import qwen_complete
        text = qwen_complete(prompt, max_tokens=max_tokens, tier=tier)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"dispatch to tier {tier!r} failed: {type(exc).__name__}: {exc}"}
    if not text:
        return {"ok": False, "error": f"tier {tier!r} returned empty output (call may have timed out)"}
    _log_event(session_id, "cortex_dispatch_tier", None, tier=tier)
    return {"ok": True, "tier": tier, "output": text}


# --- the state machine, exposed to connecting agents (GAP-CORTEX-0020) -----------------------
# A per-workspace StateEngine drives a task through the pipeline (SEARCH->...->CLOSEOUT). This is
# the real server-driven harness the forced-pipeline-v1 guidance only gestured at: the agent
# calls cortex_run_step and the engine returns the next envelope (state, legal_tools,
# instruction) or a refusal that names the legal move -- a dumb client just follows it. State is
# a WRITE concern, so it lives in the TENANT workspace, never the brain.
_run_engines: dict[str, Any] = {}
# Stability audit finding #4: guards the check-then-create-then-store sequence in
# `_run_engine` below, so two threads racing to build the first `StateEngine` for the same
# workspace can't both win and leave one `StateEngine`/SQLite connection orphaned (a leak,
# not a crash today per the audit -- but the cheap fix closes it outright).
_run_engines_lock = threading.Lock()


def _scope_gate_on() -> bool:
    """REVIEW-stage scope-vs-intent check (2026-07-07). Default ON; CORTEX_SCOPE_GATE=0 disables.
    Safe as a default because a missing scope_check PASSES with a surfaced warning (it does not
    break an existing chart drive); only a self-declared mismatch or a gross zero-overlap fails."""
    import os
    return (os.environ.get("CORTEX_SCOPE_GATE", "1").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _visual_gate_on() -> bool:
    """Scoped visual-review gate (2026-07-07). Default ON; CORTEX_VISUAL_GATE=0 disables.
    Safe as a default: `make_scoped_review_gate` only ever RUNS the Playwright/vision-judge
    layer on tasks `is_visual_deliverable` flags as UI deliverables (fail-toward-skip), and
    degrades to a non-blocking warning if the verification tooling/rubric isn't available --
    it never adds cost or risk to a non-visual task."""
    import os
    return (os.environ.get("CORTEX_VISUAL_GATE", "1").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _run_engine(workspace: str | None, session_id: str | None = None):
    from .state_engine import StateEngine, review_scope_gate, make_universal_gate, default_gate
    ws = str(_write_ws(workspace, session_id))
    with _run_engines_lock:
        eng = _run_engines.get(ws)
        if eng is None:
            db_dir = Path(ws) / "logs"
            db_dir.mkdir(parents=True, exist_ok=True)
            # Build the gate pipeline for all tracks (build/research/mission).
            # Base gate is review_scope_gate (if scope-checking is enabled) or default_gate.
            base_gate = review_scope_gate if _scope_gate_on() else default_gate
            # Optionally layer visual-verification gate on top (2026-07-07), which wraps
            # review_scope_gate for rubric-based visual deliverable checking.
            visual_gate = None
            if base_gate is review_scope_gate and _visual_gate_on():
                from .rubric_gate import make_scoped_review_gate
                visual_gate = make_scoped_review_gate(base=review_scope_gate, workspace=ws)
            # Compose universal gate that handles partition_coverage_gate for mission track PARTITION,
            # review_scope_gate for REVIEW/CITE_CHECK across all tracks, and default for others.
            gate = make_universal_gate(base=base_gate, visual_gate=visual_gate)
            # workspace is passed so the app_build SMOKE state's chart-bound server-owned
            # verdict gate (terra fix #1) resolves the receipt store of THIS tenant --
            # a generic MCP caller submitting {"verdict": {"passed": true}} fails closed.
            eng = StateEngine(str(db_dir / "state_engine.sqlite"), gate=gate, workspace=ws)
            _run_engines[ws] = eng
        return eng


def _annotate_assurance_mode(env: dict[str, Any], track: str | None = None) -> dict[str, Any]:
    """Keep the assured/legacy truth visible after start, step, and resume."""
    active_track = track or env.get("track")
    env["track"] = active_track
    if active_track in {"assured_build", "assured_research"}:
        env["assurance_mode"] = "ASSURED"
        env["assurance_note"] = (
            "The chart has a server-owned research-sufficiency receipt gate. Missing, invented, "
            "unresolved, expired, or wrongly bound receipts cannot unlock dependent work."
        )
    else:
        env["assurance_mode"] = "LEGACY_UNASSURED"
        env["assurance_note"] = (
            "Compatibility route: this track does not enforce the decision-specific research "
            "receipt. Use assured_build or assured_research for a Cortex-governed claim."
        )
    return env


def cortex_assurance(
    action: str,
    execution_contract: dict[str, Any] | None = None,
    observation: dict[str, Any] | None = None,
    signature_envelope: dict[str, Any] | None = None,
    receipt_id: str = "",
    expected_task_id: str = "",
    expected_run_id: str = "",
    expected_execution_contract_sha256: str = "",
    expected_success_contract_sha256: str = "",
    session_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Verify external assurance evidence; never mint it.

    ``action="driver_preflight"`` verifies an externally signed live-driver
    observation against the frozen execution contract and returns the exact
    activation status. ``action="receipt_status"`` re-verifies a stored,
    externally signed assurance-result receipt and all expected bindings.

    There is deliberately no action that registers trust roots, signs an
    observation/result, or stores an evaluator result. Those operations belong
    to the operator/evaluator service outside the builder-facing MCP.
    """
    _record_call(session_id, "cortex_assurance")
    if action == "driver_preflight":
        from .driver_preflight import evaluate_driver_activation
        result = evaluate_driver_activation(
            execution_contract or {}, observation or {}, signature_envelope,
        )
        _log_event(
            session_id, "cortex_assurance", workspace, action=action,
            run_id=result.get("run_id"), route_id=result.get("route_id"),
            outcome=result.get("status"),
        )
        return {"ok": bool(result.get("can_claim_governed")), "preflight": result}
    if action == "receipt_status":
        from .assurance_evaluator import validate_assurance_receipt
        try:
            receipt = validate_assurance_receipt(
                receipt_id,
                expected_task_id=expected_task_id,
                expected_run_id=expected_run_id,
                expected_execution_contract_sha256=expected_execution_contract_sha256,
                expected_success_contract_sha256=expected_success_contract_sha256,
                workspace=_write_ws(workspace, session_id),
            )
        except ValueError as exc:
            return {"ok": False, "code": "ASSURANCE_RECEIPT_INVALID", "reason": str(exc)}
        _log_event(
            session_id, "cortex_assurance", workspace, action=action,
            run_id=receipt.get("run_id"), outcome=receipt.get("overall_verdict"),
        )
        return {"ok": True, "receipt": receipt}
    return {
        "ok": False,
        "code": "UNKNOWN_ASSURANCE_ACTION",
        "valid_actions": ["driver_preflight", "receipt_status"],
        "note": "This builder-facing tool verifies external records and never mints them.",
    }


@mcp.tool()
@_guard_workspace("cortex_run_start")
async def cortex_run_start(intent: dict[str, Any], track: str = "build",
                           phase_seconds: int = 480, heartbeat_seconds: int = 60,
                           phases: list[dict[str, Any]] | None = None,
                           session_id: str | None = None,
                           workspace: str | None = None) -> dict[str, Any]:
    """Start a task through the server-driven pipeline (the state machine). `intent` is
    {"seeking": "<what this task is>"}. `track` selects the chart: "build" (default) walks
    search -> research -> plan -> spec -> implement -> review -> closeout; "research" walks
    frame -> seed -> fetch -> evidence -> cite_check -> summarize -> report. Returns the first
    envelope: state, legal_tools, and the one instruction for this phase. Every run also gets a
    durable phase plan with an 8-minute default lease, heartbeat/checkpoint guidance, and a
    resume_key so a new session can continue after timeout/max-turn loss. Then call cortex_run_step
    to advance."""
    _record_call(session_id, "cortex_run_start")
    try:
        phase_seconds = max(60, min(3600, int(phase_seconds or 480)))
        heartbeat_seconds = max(15, min(600, int(heartbeat_seconds or 60)))
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_PHASE_BUDGET",
                "reason": "phase_seconds and heartbeat_seconds must be integers",
                "how_to_comply": "use phase_seconds=480 and heartbeat_seconds=60 unless you need a bounded override"}
    eng = _run_engine(workspace, session_id)
    try:
        tid = await asyncio.to_thread(eng.create_task, intent, track, phase_seconds, actor=session_id)
    except KeyError:
        return {"ok": False, "code": "UNKNOWN_TRACK", "track": track,
                "reason": f"no chart registered for track {track!r}",
                "legal_tracks": ["build", "research", "assured_build", "assured_research",
                                 "mission", "app_build"]}
    except ValueError as exc:
        # Stability audit finding #5: `StateEngine.create_task` raises ValueError on a
        # malformed `intent` (must be a dict) -- was uncaught, crashing the call, unlike the
        # clean UNKNOWN_TRACK refusal two lines above for the same function.
        return {"ok": False, "code": "BAD_INTENT", "reason": str(exc),
                "how_to_comply": "intent must be a dict, e.g. {'seeking': '<what this task is>'}"}
    env = await asyncio.to_thread(eng.get, tid)
    authoritative_intent = env.get("intent") if isinstance(env.get("intent"), dict) else intent
    # A new root run makes a prior DONE receipt in this session ineligible for
    # future closeouts.  Otherwise unrelated work could start and still promote
    # against the previous run's identity.
    with _sessions_lock:
        session = _sessions.get(session_id or "")
        if session is not None:
            session.pop("completed_run", None)
    try:
        from .phase_runtime import create_phase_plan
        phase_plan = await asyncio.to_thread(
            create_phase_plan, _write_ws(workspace, session_id), tid, authoritative_intent, track,
            session_id,
            phases, phase_seconds, heartbeat_seconds,
        )
    except ValueError as exc:
        return {"ok": False, "code": "BAD_PHASE_PLAN", "reason": str(exc),
                "how_to_comply": "phases must be a list of dicts with unique phase_id values"}
    _log_event(session_id, "cortex_run_start", workspace, task_id=tid,
               run_id=env.get("run_id"), track=track)
    _annotate_assurance_mode(env, track)
    env["phase_plan"] = phase_plan
    env["resume_key"] = phase_plan["resume_key"]
    env["phase_policy"] = {
        "max_phase_seconds": phase_plan["phase_seconds"],
        "heartbeat_seconds": phase_plan["heartbeat_seconds"],
        "checkpoint_tool": "cortex_phase_checkpoint",
        "resume_tool": "cortex_phase_resume",
        "empty_output_tool": "cortex_report_empty_output",
    }
    env["phase_instruction"] = (
        "Work in bounded phases. Heartbeat/checkpoint before the lease expires; if the model returns "
        "empty output, call cortex_report_empty_output and follow its action instead of advancing."
    )
    return env


@mcp.tool()
@_guard_workspace("cortex_run_step")
async def cortex_run_step(task_id: str, tool: str, seq: int, payload: dict[str, Any] | None = None,
                          idem_key: str | None = None, session_id: str | None = None,
                          workspace: str | None = None, rationale: str | None = None) -> dict[str, Any]:
    """Take ONE step: submit `tool` for the task's current phase, with `seq` (from the last
    envelope -- optimistic-concurrency fence). Returns the next envelope on success, or a refusal
    {ok:false, code, do_instead, legal_tools} if the tool is illegal in this state (guidance, not
    a wall -- do what it says and retry). Idempotent on `idem_key`. `rationale` is an OPTIONAL
    free-text "why this step" trace persisted alongside the event (retrievable via
    `cortex_run_state`'s `events`); omitting it behaves exactly as before this field existed."""
    _record_call(session_id, "cortex_run_step")
    eng = _run_engine(workspace, session_id)
    try:
        env = await asyncio.to_thread(eng.step, task_id, tool, payload, seq, idem_key, session_id, rationale)
    except ValueError as exc:
        # Stability audit finding #5: `StateEngine.step`'s own "forgiving parse" comment
        # (`seq = int(seq)`) implies malformed input should degrade gracefully, but a
        # non-numeric `seq` (or a missing one) raised ValueError uncaught -- inconsistent with
        # the clean {"ok": False, "code": "UNKNOWN_TRACK", ...} pattern this same file already
        # uses two tools over. No state/seq is touched before this raise (confirmed by reading
        # `step`: the fence is parsed before any transaction opens), so this is safe to just
        # refuse and let the caller retry with a corrected seq.
        return {"ok": False, "code": "BAD_STEP_INPUT", "reason": str(exc),
                "how_to_comply": "seq must be an integer (or a numeric string) from the last "
                "envelope's seq field; call cortex_run_state to re-fetch the current seq if unsure"}
    current = await asyncio.to_thread(eng.get, task_id)
    env["run_id"] = current.get("run_id")
    _annotate_assurance_mode(env, current.get("track"))
    # Mandatory-state-machine bookkeeping (Decision B): the first time THIS session drives a task
    # to a terminal DONE, stamp the session so _state_machine_gate unlocks the free-standing write
    # tools. Only a genuine ok-advance into DONE counts (a refusal leaves state/seq unchanged).
    if env.get("ok") and env.get("state") == "DONE":
        with _sessions_lock:
            sess = _sessions.get(session_id or "")
            if sess is not None:
                sess["completed_run"] = {
                    "task_id": task_id,
                    "run_id": current.get("run_id"),
                    "track": current.get("track"),
                    "seeking": (
                        current.get("intent", {}).get("seeking")
                        if isinstance(current.get("intent"), dict) else None
                    ),
                }
    _log_event(session_id, "cortex_run_step", workspace, task_id=task_id, submitted_tool=tool,
               run_id=env.get("run_id"), ok=env.get("ok"), to_state=env.get("state"))
    return env


@mcp.tool()
@_guard_workspace("cortex_run_state")
async def cortex_run_state(task_id: str, session_id: str | None = None,
                           workspace: str | None = None) -> dict[str, Any]:
    """Current state of a pipeline task: state, seq, legal_tools, instruction, intent -- so a
    reconnecting or resuming agent can pick up exactly where the task is. Also includes
    `events`: the task's event-log history (oldest-first), each with any `rationale` a caller
    attached via `cortex_run_step(..., rationale=...)` -- the retrieval path for that trace
    field, so a stored rationale is never write-only/orphaned."""
    _record_call(session_id, "cortex_run_state")
    eng = _run_engine(workspace, session_id)
    state = await asyncio.to_thread(eng.get, task_id)
    _annotate_assurance_mode(state)
    state["events"] = await asyncio.to_thread(eng.event_history, task_id)
    try:
        from .phase_runtime import get_phase_state
        state["phase_state"] = await asyncio.to_thread(
            get_phase_state, _write_ws(workspace, session_id), task_id, None,
        )
    except KeyError:
        state["phase_state"] = None
    return state


@mcp.tool()
@_guard_workspace("cortex_phase_state")
async def cortex_phase_state(task_id: str = "", resume_key: str = "",
                             session_id: str | None = None,
                             workspace: str | None = None) -> dict[str, Any]:
    """Return the durable phase/checkpoint state for a task. Use task_id while in the same
    run, or resume_key after a timeout/max-turn/new-session recovery."""
    _record_call(session_id, "cortex_phase_state")
    from .phase_runtime import get_phase_state
    try:
        state = await asyncio.to_thread(
            get_phase_state, _write_ws(workspace, session_id), task_id or None, resume_key or None,
        )
    except KeyError:
        return {"ok": False, "code": "UNKNOWN_PHASE_RUN",
                "how_to_comply": "pass the task_id from cortex_run_start or its resume_key"}
    _log_event(session_id, "cortex_phase_state", workspace, task_id=state.get("task_id"))
    return {"ok": True, "phase_state": state}


@mcp.tool()
@_guard_workspace("cortex_phase_heartbeat")
async def cortex_phase_heartbeat(task_id: str = "", resume_key: str = "", phase_id: str = "",
                                 partial_outputs: list[dict[str, Any]] | None = None,
                                 checkpoint_state: dict[str, Any] | None = None,
                                 session_id: str | None = None,
                                 workspace: str | None = None) -> dict[str, Any]:
    """Extend the active phase lease and optionally attach small partial outputs/checkpoint
    state. Call this during long phases so the supervisor knows the worker is alive."""
    _record_call(session_id, "cortex_phase_heartbeat")
    from .phase_runtime import heartbeat_phase
    try:
        state = await asyncio.to_thread(
            heartbeat_phase, _write_ws(workspace, session_id), task_id or None, resume_key or None,
            phase_id or None, partial_outputs, checkpoint_state,
        )
    except (KeyError, ValueError) as exc:
        return {"ok": False, "code": "BAD_PHASE_HEARTBEAT", "reason": str(exc)}
    _log_event(session_id, "cortex_phase_heartbeat", workspace, task_id=state.get("task_id"),
               phase_id=state.get("phase_id"))
    return {"ok": True, "phase_state": state}


@mcp.tool()
@_guard_workspace("cortex_phase_checkpoint")
async def cortex_phase_checkpoint(task_id: str = "", resume_key: str = "", phase_id: str = "",
                                  checkpoint_state: dict[str, Any] | None = None,
                                  partial_outputs: list[dict[str, Any]] | None = None,
                                  advance: bool = False,
                                  session_id: str | None = None,
                                  workspace: str | None = None) -> dict[str, Any]:
    """Persist a resume-safe checkpoint for the active phase. Set advance=true only when the
    phase's expected outputs are complete and the next phase should become active."""
    _record_call(session_id, "cortex_phase_checkpoint")
    from .phase_runtime import checkpoint_phase
    try:
        state = await asyncio.to_thread(
            checkpoint_phase, _write_ws(workspace, session_id), task_id or None, resume_key or None,
            phase_id or None, checkpoint_state, partial_outputs, advance,
        )
    except (KeyError, ValueError) as exc:
        return {"ok": False, "code": "BAD_PHASE_CHECKPOINT", "reason": str(exc)}
    _log_event(session_id, "cortex_phase_checkpoint", workspace, task_id=state.get("task_id"),
               phase_id=state.get("phase_id"), advance=advance)
    return {"ok": True, "phase_state": state}


@mcp.tool()
@_guard_workspace("cortex_phase_resume")
async def cortex_phase_resume(task_id: str = "", resume_key: str = "",
                              session_id: str | None = None,
                              workspace: str | None = None) -> dict[str, Any]:
    """Resume a timed-out/max-turn task from its durable checkpoint. Prefer resume_key when a
    new agent session is taking over."""
    _record_call(session_id, "cortex_phase_resume")
    from .phase_runtime import resume_phase
    try:
        res = await asyncio.to_thread(
            resume_phase, _write_ws(workspace, session_id), task_id or None, resume_key or None,
        )
    except KeyError:
        return {"ok": False, "code": "UNKNOWN_PHASE_RUN",
                "how_to_comply": "pass the task_id from cortex_run_start or its resume_key"}
    _log_event(session_id, "cortex_phase_resume", workspace, task_id=res.get("task_id"),
               next_action=res.get("next_action"))
    return res


@mcp.tool()
@_guard_workspace("cortex_report_empty_output")
async def cortex_report_empty_output(task_id: str = "", resume_key: str = "",
                                     model_id: str = "", prompt_hash: str = "",
                                     raw_output: Any = "",
                                     session_id: str | None = None,
                                     workspace: str | None = None) -> dict[str, Any]:
    """Report a blank/whitespace/marker-only model response for the current phase. First empty
    output asks for a tighter retry, second asks for a lane/backend switch, third escalates. It
    never marks the task complete."""
    _record_call(session_id, "cortex_report_empty_output")
    from .phase_runtime import report_empty_output
    try:
        res = await asyncio.to_thread(
            report_empty_output, _write_ws(workspace, session_id), task_id or None,
            resume_key or None, model_id, prompt_hash, raw_output,
        )
    except KeyError:
        return {"ok": False, "code": "UNKNOWN_PHASE_RUN",
                "how_to_comply": "pass the task_id from cortex_run_start or its resume_key"}
    task = (res.get("phase_state") or {}).get("task_id")
    _log_event(session_id, "cortex_report_empty_output", workspace, task_id=task,
               action=res.get("action"), count=res.get("empty_output_count"))
    return res


# --- the MISSION layer, exposed to connecting agents (orchestrator-over-choreography) ----------
# StateEngine.spawn_mission already implements the "orchestrator supervising independent,
# event-sourced choreographed workers" pattern: it atomically partitions DISJOINT claims across
# workers (all-or-nothing, no hold-and-wait -> no deadlock), runs each worker as its own tracked
# pipeline, and gives ONE queryable completion view. Until now that layer had no MCP surface --
# so tonight's 5+ parallel background agents were coordinated by a human hand-tracking a todo list
# (choreography with a human as the event bus, the exact cost the research predicted). These three
# tools wire the existing engine methods up so a supervising agent (or a dashboard) can spawn a
# mission, atomically claim a partition, and poll live in-flight status.


@mcp.tool()
@_guard_workspace("cortex_spawn_mission")
async def cortex_spawn_mission(mission_intent: dict[str, Any], workers: list[dict[str, Any]],
                               track: str = "build", lease_s: int = 600,
                               session_id: str | None = None,
                               workspace: str | None = None) -> dict[str, Any]:
    """Spawn a MISSION: one supervisor over N independent workers, each with its own DISJOINT
    partition of claims. NOTE (honest capability boundary): this does NOT launch any agent
    processes -- it atomically creates the mission + worker RECORDS and partitions their claims;
    YOU (the client/harness) start the actual worker agents and hand each its worker_id. Use this
    to fan out parallel, non-overlapping sub-tasks (the alternative
    to a human hand-tracking a todo list of background agents). `mission_intent` is
    {"seeking": "<the overall mission>"}; `workers` is a list of
    [{"intent": {"seeking": "<sub-task>"}, "claims": [{"kind": "path", "key": "src/auth/**"}, ...]}, ...].
    Partitioning is atomic and all-or-nothing: if ANY two workers' claims overlap (glob, both
    directions) or collide with a live claim, NOTHING is created and you get
    {ok:false, code:'CLAIM_CONFLICT', conflicts:[...]} -- fix the overlap and retry (no partial
    mission, no deadlock by construction). On success returns {ok:true, mission_id, worker_ids};
    each worker starts fresh in its chart's initial state. Poll cortex_mission_status for live
    completion, and hand each worker_id to its agent, which claims its slice via
    cortex_acquire_claims and drives it with cortex_run_step."""
    _record_call(session_id, "cortex_spawn_mission")
    eng = _run_engine(workspace, session_id)
    try:
        res = await asyncio.to_thread(eng.spawn_mission, mission_intent, workers,
                                      track=track, lease_s=lease_s, actor=session_id)
    except KeyError:
        return {"ok": False, "code": "UNKNOWN_TRACK", "track": track,
                "reason": f"no chart registered for track {track!r}",
                "legal_tracks": ["build", "research", "mission"]}
    except (ValueError, TypeError) as exc:
        # spawn_mission raises ValueError on an empty `workers` list, and TypeError on a
        # malformed worker entry (missing kind/key). (A KeyError is already handled above as
        # UNKNOWN_TRACK.) Refuse cleanly instead of crashing, mirroring cortex_run_start's
        # BAD_INTENT path.
        return {"ok": False, "code": "BAD_MISSION", "reason": str(exc),
                "how_to_comply": ("workers must be a non-empty list of "
                                  "{'intent': {...}, 'claims': [{'kind': ..., 'key': ...}, ...]}")}
    _log_event(session_id, "cortex_spawn_mission", workspace,
               ok=res.get("ok"), mission_id=res.get("mission_id"),
               n_workers=len(res.get("worker_ids", [])), code=res.get("code"))
    return res


@mcp.tool()
@_guard_workspace("cortex_mission_status")
async def cortex_mission_status(mission_id: str, session_id: str | None = None,
                                workspace: str | None = None) -> dict[str, Any]:
    """Live completion view of a mission's workers -- THE tool a dashboard polls for in-flight
    visibility. Given a mission_id (from cortex_spawn_mission), returns {mission_id, workers:
    [{task_id, state, seq}, ...], n, done, all_done}. `done` counts workers in terminal DONE;
    `all_done` is true only once every worker is DONE (the merge signal for the supervisor). An
    unknown/empty mission returns n=0, all_done=false honestly (it fabricates nothing). Read-only:
    safe to poll on an interval."""
    _record_call(session_id, "cortex_mission_status")
    eng = _run_engine(workspace, session_id)
    status = await asyncio.to_thread(eng.mission_status, mission_id)
    _log_event(session_id, "cortex_mission_status", workspace, mission_id=mission_id,
               n=status.get("n"), done=status.get("done"), all_done=status.get("all_done"))
    return status


@mcp.tool()
@_guard_workspace("cortex_acquire_claims")
async def cortex_acquire_claims(task_id: str, claims: list[dict[str, Any]], seq: int,
                                session_id: str | None = None,
                                workspace: str | None = None) -> dict[str, Any]:
    """Atomically claim a worker's partition of resources so no two parallel workers ever write
    the same slice. `task_id` is your worker_id (from cortex_spawn_mission); `claims` is
    [{"kind": "path", "key": "src/auth/**"}, ...]; `seq` is the fence from your last envelope
    (optimistic concurrency). All-or-nothing: if any (kind, key) overlaps a claim another live
    task already holds (glob, both directions), NOTHING is granted and you get
    {ok:false, code:'CLAIM_CONFLICT', conflicts:[...]} -- pick a disjoint slice and retry. A stale
    seq returns {ok:false, code:'REJECTED_STALE'} with the current seq. On success returns the
    task envelope with the granted claims; you then own that partition until the task terminates."""
    _record_call(session_id, "cortex_acquire_claims")
    eng = _run_engine(workspace, session_id)
    try:
        res = await asyncio.to_thread(eng.acquire_claims, task_id, claims, seq)
    except ValueError as exc:
        # acquire_claims raises ValueError on a missing seq or an empty claims list -- refuse
        # cleanly (mirrors cortex_run_step's BAD_STEP_INPUT), no state touched before the raise.
        return {"ok": False, "code": "BAD_CLAIMS_INPUT", "reason": str(exc),
                "how_to_comply": ("seq must be an integer from your last envelope, and claims a "
                                  "non-empty list of {'kind': ..., 'key': ...}")}
    _log_event(session_id, "cortex_acquire_claims", workspace, task_id=task_id,
               ok=res.get("ok"), code=res.get("code"), n_claims=len(claims or []))
    return res


# --- MISSION_TRACK tools (Phase 5.2, 2026-07-08) ---
# The MISSION_TRACK is an orchestrator state machine that coordinates parallel workers.
# These three tools drive the mission through its phases: INTAKE -> PARTITION -> DISPATCH ->
# MONITOR -> MERGE -> REVIEW -> CLOSEOUT -> DONE. Workers are spawned via the existing
# cortex_spawn_mission, and the mission track tracks the orchestration state and validates
# the folded output.


@mcp.tool()
@_guard_workspace("cortex_submit_mission_contract")
async def cortex_submit_mission_contract(task_id: str, contract: dict[str, Any], seq: int,
                                         session_id: str | None = None,
                                         workspace: str | None = None) -> dict[str, Any]:
    """Submit a mission contract at INTAKE phase (INTAKE -> PARTITION). The contract declares
    what the mission is seeking, acceptance criteria for the merged whole, the coverage spec
    (what units must be collectively owned), and the reducer policy (how to fold worker outputs).
    `contract` is a MissionContract dict with fields: mission_id, mission_task, task_type,
    acceptance_criteria, coverage_spec, reducers, evidence_refs. Returns the updated envelope;
    the contract is stored in the mission task's intent for later phases to reference."""
    _record_call(session_id, "cortex_submit_mission_contract")
    eng = _run_engine(workspace, session_id)
    try:
        res = await asyncio.to_thread(eng.step, task_id, "cortex_submit_mission_contract",
                                      {"contract": contract}, seq, None, session_id, None)
    except ValueError as exc:
        return {"ok": False, "code": "BAD_STEP_INPUT", "reason": str(exc),
                "how_to_comply": "seq must be an integer; contract must be a dict"}
    _log_event(session_id, "cortex_submit_mission_contract", workspace, task_id=task_id,
               ok=res.get("ok"), to_state=res.get("state"))
    return res


@mcp.tool()
@_guard_workspace("cortex_submit_partition")
async def cortex_submit_partition(task_id: str, workers: list[dict[str, Any]], seq: int,
                                  session_id: str | None = None,
                                  workspace: str | None = None) -> dict[str, Any]:
    """Submit a partition at PARTITION phase (PARTITION -> DISPATCH if coverage valid).
    The partition declares which workers exist and which units each owns. The partition_coverage_gate
    validates that required_units are collectively covered, no unit is owned by >1 worker, and
    worker count is bounded (MECE: mutually exclusive, collectively exhaustive). `workers` is
    [{"owns_units": ["unit1", "unit2"], ...}, ...]. On gate failure, refusal guides the caller
    to fix the partition and resubmit."""
    _record_call(session_id, "cortex_submit_partition")
    eng = _run_engine(workspace, session_id)
    try:
        res = await asyncio.to_thread(eng.step, task_id, "cortex_submit_partition",
                                      {"workers": workers}, seq, None, session_id, None)
    except ValueError as exc:
        return {"ok": False, "code": "BAD_STEP_INPUT", "reason": str(exc),
                "how_to_comply": "seq must be an integer; workers must be a list of dicts"}
    _log_event(session_id, "cortex_submit_partition", workspace, task_id=task_id,
               ok=res.get("ok"), to_state=res.get("state"), n_workers=len(workers or []))
    return res


@mcp.tool()
@_guard_workspace("cortex_dispatch_mission")
async def cortex_dispatch_mission(task_id: str, seq: int,
                                  session_id: str | None = None,
                                  workspace: str | None = None) -> dict[str, Any]:
    """Advance DISPATCH -> MONITOR, ATOMICALLY creating the mission's build-track worker
    children under THIS mission task (sol #6 / S4a topology fix).

    The workers are NOT supplied here -- they are materialized inside the engine superstep from
    the worker manifest you already submitted (and the coverage gate already validated) at
    PARTITION via cortex_submit_partition. Each manifest worker must carry its disjoint `claims`
    = [{"kind","key"}, ...]; the engine creates one build-track child per entry under `task_id`
    (this mission's parent_id) and grants its claims all-or-nothing, IN THE SAME transaction as
    the DISPATCH->MONITOR transition. A claim overlap (or a claimless/absent partition) fails the
    advance CLOSED: nothing is created and the mission stays at DISPATCH to be re-partitioned --
    no orphan children can be left behind. Because the children hang off this exact mission,
    cortex_mission_status(task_id) now sees them and the MONITOR->MERGE gate can fire once they
    are all DONE. The response envelope carries the created `worker_ids`."""
    _record_call(session_id, "cortex_dispatch_mission")
    eng = _run_engine(workspace, session_id)
    try:
        res = await asyncio.to_thread(eng.step, task_id, "cortex_dispatch_mission",
                                      {}, seq, None, session_id, None)
    except ValueError as exc:
        return {"ok": False, "code": "BAD_STEP_INPUT", "reason": str(exc),
                "how_to_comply": "seq must be an integer from your last envelope"}
    _log_event(session_id, "cortex_dispatch_mission", workspace, task_id=task_id,
               ok=res.get("ok"), to_state=res.get("state"),
               n_workers=len(res.get("worker_ids", [])))
    return res


@mcp.tool()
@_guard_workspace("cortex_submit_merge")
async def cortex_submit_merge(task_id: str, merged_artifact: dict[str, Any], seq: int,
                              session_id: str | None = None,
                              workspace: str | None = None) -> dict[str, Any]:
    """Submit the merged artifact at MONITOR phase (MONITOR -> MERGE if all workers done).
    This is a checkpoint that verifies all workers are in DONE state before allowing the
    transition to MERGE. The merged_artifact is the supervisor's folded output (applying
    declared reducers over all workers' final artifacts). Returns a refusal if any worker
    is not yet DONE (guidance: wait and retry); on success transitions to MERGE for validation."""
    _record_call(session_id, "cortex_submit_merge")
    eng = _run_engine(workspace, session_id)
    # Checkpoint guard: all workers must be DONE before merge can proceed
    try:
        # Existence check: _fetch_task raises if task_id is unknown (result unused).
        await asyncio.to_thread(eng._fetch_task, task_id)
        mission_id = task_id
        status = await asyncio.to_thread(eng.mission_status, mission_id)
        if not status.get("all_done"):
            pending = [w["task_id"] for w in status.get("workers", []) if w["state"] != "DONE"]
            return {"ok": False, "code": "WORKERS_PENDING",
                    "reason": f"not all workers are DONE; pending: {pending}",
                    "mission_id": mission_id, "workers": status.get("workers"),
                    "n_done": status.get("done"), "n_total": status.get("n")}
    except KeyError:
        # Task not found or not a mission
        return {"ok": False, "code": "UNKNOWN_MISSION", "reason": f"mission {task_id} not found"}
    # All workers done; proceed to merge
    try:
        res = await asyncio.to_thread(eng.step, task_id, "cortex_submit_merge",
                                      {"merged_artifact": merged_artifact}, seq, None, session_id, None)
    except ValueError as exc:
        return {"ok": False, "code": "BAD_STEP_INPUT", "reason": str(exc),
                "how_to_comply": "seq must be an integer; merged_artifact must be a dict"}
    _log_event(session_id, "cortex_submit_merge", workspace, task_id=task_id,
               ok=res.get("ok"), to_state=res.get("state"), all_workers_done=status.get("all_done"))
    return res


@mcp.tool()
@_guard_workspace("cortex_search")
async def cortex_search(
    query: str, session_id: str | None = None, workspace: str | None = None, limit: int = 20,
    composite: bool = True,
) -> dict[str, Any]:
    """Search local Cortex knowledge before the web.

    By default this is a COMPOSITE query over the canonical Brain, tenant/project corpus,
    KEDB incidents, reviewed gold catalog, and oracle catalog. Every source returns an explicit
    coverage record (`hits`, `no_hits`, `absent`, or `error`) so an empty store cannot be mistaken
    for adequate research. Set ``composite=false`` only for a compatibility/debug read of the
    resolved Brain corpus. Hybrid corpus retrieval degrades from vector+BM25 to BM25 automatically.
    Pass ``session_id`` so the coverage event is attributable."""
    _record_call(session_id, "cortex_search")
    from .knowledge import composite_search as _composite_search

    brain_ws = _read_ws(workspace, session_id)
    tenant_ws = str(_write_ws(workspace, session_id)) if composite else brain_ws
    no_log = _session_no_log(_sessions.get(session_id or "", {}))
    try:
        result = await asyncio.to_thread(
            _composite_search,
            query,
            brain_workspace=brain_ws,
            tenant_workspace=tenant_ws,
            limit=limit,
            include_structured=bool(composite),
            log_telemetry=not no_log,
            index_factory=CortexSearchIndex,
        )
    except ValueError as exc:
        return {"ok": False, "code": "BAD_SEARCH_INPUT", "reason": str(exc)}
    default = (
        "cortex_write_log once you've used this to close the loop"
        if result["results"]
        else "local coverage is insufficient -- inspect knowledge_gaps, then call cortex_fetch_doc or start research/register an external source"
    )
    _log_event(
        session_id, "cortex_search", workspace, query=query, hits=result["hits"],
        composite=result["composite"], knowledge_gaps=result["gaps"],
    )
    return {
        "query": query,
        "hits": result["hits"],
        "results": result["results"],
        "composite": result["composite"],
        "workspaces": result["workspaces"],
        "coverage": result["coverage"],
        "knowledge_gaps": result["gaps"],
        "queried_at": result["queried_at"],
        "next": _next_actions(session_id, default=default),
    }


@mcp.tool()
@_guard_workspace("cortex_scope_pack")
async def cortex_scope_pack(
    task: str,
    session_id: str | None = None,
    workspace: str | None = None,
    token_budget: int = 4000,
    task_type: str | None = None,
    escalation_reason: str = "",
) -> dict[str, Any]:
    """Assemble a budget-capped, score-ranked SCOPE PACK for a task (Phase 5.2):
    the most relevant patterns + doc chunks + closeouts, each carrying its
    retrieval score, greedily packed under a TOKEN budget. Prefer this over a raw
    cortex_search when you're about to load context to *do* a task -- it serves
    exactly what the task needs and says why each item is there, instead of
    drowning the signal by dumping everything relevant (Lost-in-the-Middle).

    The budget is in tokens. If a pack isn't enough, ESCALATE (Phase 5.4): call
    again with a larger token_budget AND an escalation_reason. Escalation is one
    call, always granted -- there is no gatekeeper -- and always logged with your
    reason (the reasons are the curriculum used to retune default budgets). State
    honestly why the smaller pack fell short."""
    from . import packs

    _record_call(session_id, "cortex_scope_pack")
    escalated = bool(escalation_reason.strip())
    # Read-only assembly, but it can trigger an index rebuild + re-embed on a
    # changed corpus -- threaded so it can't block the event loop (gate 3.1).
    no_log = _session_no_log(_sessions.get(session_id or "", {}))
    pack = await asyncio.to_thread(
        packs.build_composite_scope_pack,
        task,
        task_type,
        brain_workspace=_read_ws(workspace, session_id),
        tenant_workspace=str(_write_ws(workspace, session_id)),
        token_budget=token_budget,
        log_telemetry=not no_log,
    )
    # Gate 5.4: an escalation is always granted (we just built at the requested
    # budget above) and always logged WITH its reason -- that pairing is the
    # whole mechanism. `escalated`/`escalation_reason` land in the event so the
    # escalation-rate SLI (packs.escalation_sli) can compute the <20% health
    # signal and surface the reasons.
    _log_event(
        session_id, "cortex_scope_pack", workspace,
        task=task, n_items=pack["n_items"], tokens_used=pack["tokens_used"],
        token_budget=token_budget, escalated=escalated, escalation_reason=escalation_reason,
    )
    if escalated:
        default = "escalation granted -- act on the larger pack, then cortex_write_log to close the loop"
    elif pack["items"]:
        default = "you have a scoped pack -- act on it, then cortex_write_log to close the loop"
    else:
        default = "empty pack -- try a broader task phrasing, or cortex_fetch_doc to add a source"
    return {**pack, "escalation_granted": escalated, "next": _next_actions(session_id, default=default)}


@mcp.tool()
@_guard_workspace("cortex_fetch_doc")
async def cortex_fetch_doc(
    url: str,
    name: str,
    session_id: str | None = None,
    workspace: str | None = None,
    contract_override_reason: str = "",
    backend: str | None = None,
    state_machine_override_reason: str = "",
) -> dict[str, Any]:
    """Fetch a document into the Cortex corpus. SSRF-guarded (global-IP-only,
    connection pinned to the validated address) -- refuses private/loopback/
    link-local targets and non-http(s) schemes. A registered session needs an
    approved cortex_contract first (Phase 4.2); pass contract_override_reason to
    bypass (logged). It must also have driven a task through the server chart to
    DONE (mandatory state machine, 2026-07-07); pass state_machine_override_reason
    to bypass (logged). In served mode, mutating the canonical corpus also requires
    admin authentication (docs/CORTEX-ROUTES-AND-OWNERSHIP.md).

    backend: "native" (default, urllib) or "playwright" (headless-Chromium render
    for JS-heavy/SPA pages whose real content only appears after JS runs; needs the
    optional [browser] extra and degrades to native if it isn't installed). Also
    settable via the CORTEX_FETCH_BACKEND env var."""
    refusal = _admin_gate(session_id, "cortex_fetch_doc", workspace)
    if refusal is not None:
        return refusal
    refusal = _state_machine_gate(session_id, "cortex_fetch_doc", workspace,
                                  state_machine_override_reason)
    if refusal is not None:
        return refusal
    refusal = _contract_gate(session_id, "cortex_fetch_doc", workspace, contract_override_reason)
    if refusal is not None:
        return refusal
    # L2 (review): record only an ALLOWED write, so a refused attempt doesn't
    # count as "progress" and wrongly suppress the read-only-loop nudge.
    _record_call(session_id, "cortex_fetch_doc")
    # Resolve the WRITE plane with explicit-override precedence (an explicit workspace= wins over
    # the ambient CORTEX_WORKSPACE pin in owner mode; a served tenant stays pinned).
    ws = _write_ws(workspace, session_id)
    # A real network fetch (up to DEFAULT_FETCH_TIMEOUT) is the slowest single
    # call in this server -- must not block the event loop (gate 3.1).
    path = await asyncio.to_thread(fetch_document, url, name, str(ws), backend=backend)
    _log_event(session_id, "cortex_fetch_doc", workspace, url=url, path=str(path))
    return {
        "path": str(path),
        "next": _next_actions(session_id, default="cortex_search to confirm it's indexed"),
    }


def _validate_closeout_shape(task: Any, result: Any, evidence: Any, handoff: Any) -> list[str]:
    """Stability audit finding #2 (2026-07-07): `cortex_write_log` crashed UNCAUGHT
    (TypeError deep in `_slugify` on `task=123`; AttributeError deep in `validate_evidence`/
    `validate_handoff_field` on `evidence={"type": "file"}` -- a dict instead of a list of
    dicts -- or `handoff="a string"` instead of the required {locations, continuation} dict)
    -- exactly the three fields a weaker model is most likely to get wrong. `cortex_ontology_query`
    (above) already validates input shape this way -- explicit checks returning a friendly
    error, never a raw crash -- this mirrors that established discipline for the write path
    instead of inventing a new one. Called BEFORE any gate or `write_closeout`, so malformed
    input never reaches `_slugify`/`validate_evidence`/`validate_handoff_field` at all."""
    problems: list[str] = []
    if not isinstance(task, str) or not task.strip():
        problems.append(f"task must be a non-empty string, got {type(task).__name__}")
    if not isinstance(result, str):
        problems.append(f"result must be a string, got {type(result).__name__}")
    if evidence is not None and (
        not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence)
    ):
        problems.append(
            "evidence must be a LIST of objects like {'type': 'file', 'ref': '...'} "
            f"(got a {type(evidence).__name__}, not a list -- wrap a single item in a list)"
        )
    if handoff is not None and not isinstance(handoff, dict):
        problems.append(
            "handoff must be an OBJECT like {'locations': [...], 'continuation': '...'} "
            f"(got a {type(handoff).__name__}, not an object)"
        )
    return problems


def _project_state_outcome(
    status: str,
    reason: str,
    *,
    audit_committed: bool = True,
    **detail: Any,
) -> dict[str, Any]:
    """One non-assuring response shape for recoverable two-record reconciliation."""
    return {
        "status": status,
        "reason": reason,
        "transaction_model": "RECOVERABLE_TWO_RECORD_RECONCILIATION",
        "audit_committed": audit_committed,
        "assurance_minted": False,
        **detail,
    }


def _closeout_project_state_event(
    ws: Path,
    path: Path,
    *,
    task: str,
    result: str,
    status: str,
    handoff: dict[str, Any] | None,
    session: dict[str, Any] | None,
    state_machine_override_reason: str,
) -> dict[str, Any]:
    """Submit a run-bound task closeout to the project-state reconciler.

    The audit write has already succeeded when this runs.  Eligibility is
    deliberately narrow: an ordinary registered session, a structured DONE
    receipt, and an exact match between the closeout's existing ``task`` field
    and that receipt's task id.  Session-less, override, and unrelated writes
    remain audit history and cannot silently become project authority.
    """
    if session is None:
        return _project_state_outcome(
            "NOT_APPLICABLE", "session-less closeout remains audit-only",
        )
    if state_machine_override_reason.strip():
        return _project_state_outcome(
            "NOT_APPLICABLE", "state-machine override closeout remains audit-only",
        )
    completed = session.get("completed_run")
    if not isinstance(completed, dict):
        return _project_state_outcome(
            "NOT_APPLICABLE", "session has no structured completed run",
        )
    task_id = completed.get("task_id")
    run_id = completed.get("run_id")
    track = completed.get("track")
    seeking = completed.get("seeking")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (task_id, run_id, track, seeking)
    ):
        return _project_state_outcome(
            "UNRESOLVED", "completed run is missing task_id, run_id, track, or intent.seeking",
        )
    task_label_mismatch = task.strip() != seeking.strip()

    config_path = ws / "cortex.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _project_state_outcome(
            "UNRESOLVED", f"cannot read project identity from cortex.json: {exc}",
        )
    project_id = config.get("name") if isinstance(config, dict) else None
    if not isinstance(project_id, str) or not project_id.strip():
        return _project_state_outcome(
            "UNRESOLVED", "cortex.json requires a non-empty name for project-state ingestion",
        )

    sidecar_path = path.with_suffix(".json")
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        observed_at = sidecar["timestamp"]
        version = sidecar.get("cortex_version") or {}
        closeout_bytes = path.read_bytes()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        return _project_state_outcome(
            "UNRESOLVED", f"written closeout cannot be content-addressed: {exc}",
        )
    if not isinstance(observed_at, str) or not observed_at.strip():
        return _project_state_outcome(
            "UNRESOLVED", "written closeout has no usable timestamp",
        )
    closeout_sha256 = hashlib.sha256(closeout_bytes).hexdigest()
    try:
        closeout_uri = path.resolve().relative_to(ws.resolve()).as_posix()
    except ValueError:
        return _project_state_outcome(
            "UNRESOLVED", "written closeout resolved outside its workspace",
        )

    locations = (handoff or {}).get("locations")
    affected_documents = list(dict.fromkeys(
        str(value).strip() for value in (locations if isinstance(locations, list) else [])
        if str(value).strip()
    ))
    continuation = (handoff or {}).get("continuation")
    next_actions = [continuation.strip()] if isinstance(continuation, str) and continuation.strip() else []
    normalized_status = status.strip().lower()
    if normalized_status in {"completed", "complete", "success", "succeeded", "done"}:
        lifecycle_state = "COMPLETED"
        blockers: list[str] = []
    elif normalized_status in {"blocked"}:
        lifecycle_state = "BLOCKED"
        blockers = [result.strip() or "task closeout reports a blocker"]
    else:
        lifecycle_state = "UNRESOLVED"
        blockers = [result.strip() or f"task closeout status is {status}"]

    from .project_state import RevisionConflictError
    from .project_state_store import (
        ProjectStateStore,
        ProjectionCommitError,
        StoreRevisionConflictError,
        build_closeout_event_bundle,
    )

    event_id = f"closeout:{closeout_sha256}"
    store = ProjectStateStore(ws)
    for attempt in range(3):
        try:
            committed_events = store.read_events()
            existing = next(
                (event for event in committed_events if event.get("event_id") == event_id),
                None,
            )
            revision = len(committed_events)
            if existing is not None:
                event = existing
            else:
                commit = version.get("commit") if isinstance(version, dict) else None
                event = build_closeout_event_bundle(
                    event_id=event_id,
                    project_id=project_id,
                    run_id=run_id,
                    task_id=task_id,
                    subject_id=task_id,
                    subject_type="OPERATIONAL",
                    scope={"kind": "TASK", "id": task_id},
                    authority={
                        "actor_id": str(session.get("agent_id") or session.get("session_id") or task_id),
                        "authority_class": "AGENT",
                        "authority_role": "self-reported-task-closeout",
                    },
                    expected_prior_revision=revision,
                    observed_at=observed_at,
                    valid_from=observed_at,
                    appended_at=datetime.now(timezone.utc).isoformat(),
                    lifecycle_state=lifecycle_state,
                    source={
                        "repository": project_id,
                        "commit": str(commit or "unknown"),
                        "config_version": str(config.get("version", "unknown")),
                    },
                    claims=list(dict.fromkeys([
                        result.strip() or f"task closeout status is {status}",
                        f"self-reported closeout task label: {task.strip()}",
                        f"server-recorded run intent: {seeking.strip()}",
                    ])),
                    blockers=blockers,
                    next_actions=next_actions,
                    affected_document_ids=affected_documents,
                    evidence_refs=[{
                        "evidence_id": f"closeout-sha256:{closeout_sha256}",
                        "uri": closeout_uri,
                        "sha256": closeout_sha256,
                        "authority_class": "DOCUMENTARY",
                        "independence_class": "SELF_REPORTED",
                        "provenance_class": "CONTENT_ADDRESSED",
                        "observed_at": observed_at,
                        "expires_at": None,
                    }],
                )
            reconciled = store.compare_and_append(
                event,
                expected_revision=revision,
                as_of=datetime.now(timezone.utc).isoformat(),
            )
            return _project_state_outcome(
                "APPLIED",
                "task-scoped operational closeout event reconciled",
                event_id=event_id,
                task_id=task_id,
                run_id=run_id,
                track=track,
                scope={"kind": "TASK", "id": task_id},
                revision=reconciled.revision,
                dirty=False,
                event_committed=True,
                task_label_mismatch=task_label_mismatch,
                task_label_warning=(
                    "human closeout task label differs from the completed run intent; "
                    "the event remains bound to the server task/run ids and mints no assurance"
                    if task_label_mismatch else None
                ),
            )
        except ProjectionCommitError as exc:
            try:
                event_committed = any(
                    event.get("event_id") == event_id for event in store.read_events()
                )
            except Exception:
                event_committed = False
            return _project_state_outcome(
                "DIRTY",
                str(exc),
                event_id=event_id,
                task_id=task_id,
                run_id=run_id,
                track=track,
                scope={"kind": "TASK", "id": task_id},
                revision=exc.committed_revision,
                dirty=True,
                event_committed=event_committed,
                task_label_mismatch=task_label_mismatch,
            )
        except (RevisionConflictError, StoreRevisionConflictError):
            if attempt < 2:
                continue
            return _project_state_outcome(
                "UNRESOLVED",
                "project-state revision changed repeatedly during closeout reconciliation",
                event_id=event_id,
                task_id=task_id,
                run_id=run_id,
                track=track,
                dirty=True,
            )
        except Exception as exc:  # noqa: BLE001 -- audit write is already durable
            return _project_state_outcome(
                "UNRESOLVED",
                f"project-state reconciliation failed: {type(exc).__name__}: {exc}",
                event_id=event_id,
                task_id=task_id,
                run_id=run_id,
                track=track,
                dirty=True,
            )

    return _project_state_outcome("UNRESOLVED", "project-state reconciliation did not run")


@mcp.tool()
@_guard_workspace("cortex_write_log")
async def cortex_write_log(
    task: str,
    result: str,
    session_id: str | None = None,
    status: str = "completed",
    tests: str = "",
    scripts: str = "",
    workspace: str | None = None,
    contract_override_reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    handoff: dict[str, Any] | None = None,
    state_machine_override_reason: str = "",
) -> dict[str, Any]:
    """Write an audit closeout -- the permanent record of what was done and
    why. Write one at the end of every task; this is the evidence trail the
    self-learning loop is built on. A registered session needs an approved
    cortex_contract first (Phase 4.2); pass contract_override_reason to bypass
    (logged). `evidence` is a list of machine-oriented items (Phase 4.3), each
    `{"type": "test|file|command|eval", "ref": "...", "detail": "..."}` -- a
    `file` ref (path or path:line) should resolve to a real corpus file.

    REQUIRED (2026-07-07 standing rule): pass `handoff`, an object with two
    parts making the outcome unambiguous to the next reader --
      `handoff = {"locations": ["<concrete path to the real artifact>", ...],
                  "continuation": "<what happens next>"}`
    `locations` is the actual path(s) to what this closeout produced (not a vague
    description a future reader has to guess or search for). `continuation` is a
    real, specific next-step statement -- "done, no follow-up" / "feeds into
    phase X" / "blocked on Y" / "hand to Z" -- not a placeholder. This applies to
    EVERY closeout: the MCP's own internal features AND external tasks another
    agent builds through the MCP. Omitting it does not fail the write, but the
    response carries a `handoff_warning`.

    A registered session must also have driven a task through the server chart to
    DONE before this write is legal (mandatory state machine, 2026-07-07); pass
    state_machine_override_reason to bypass (logged).

    In served mode, writing to the canonical corpus also requires admin
    authentication (docs/CORTEX-ROUTES-AND-OWNERSHIP.md).

    The audit closeout is committed first, then a separate recoverable project-
    state event is reconciled. This is deliberately not presented as one atomic
    transaction: the response's ``project_state`` field reports APPLIED,
    NOT_APPLICABLE, UNRESOLVED, or DIRTY while the successful audit record remains
    durable. Neither record mints assurance."""
    shape_problems = _validate_closeout_shape(task, result, evidence, handoff)
    if shape_problems:
        return {
            "refused": True,
            "tool": "cortex_write_log",
            "reason": "malformed input: " + "; ".join(shape_problems),
            "how_to_comply": (
                "call again with task/result as plain strings, evidence as a LIST of "
                "{'type': 'test|file|command|eval', 'ref': '...', 'detail': '...'} objects "
                "(omit it if you have none -- never pass a bare dict), and handoff as an "
                "OBJECT {'locations': [...], 'continuation': '...'} (never a plain string)"
            ),
        }
    refusal = _admin_gate(session_id, "cortex_write_log", workspace)
    if refusal is not None:
        return refusal
    refusal = _state_machine_gate(session_id, "cortex_write_log", workspace,
                                  state_machine_override_reason)
    if refusal is not None:
        return refusal
    refusal = _forced_docs_gate(session_id, "cortex_write_log", workspace, contract_override_reason)
    if refusal is not None:
        return refusal
    refusal = _contract_gate(session_id, "cortex_write_log", workspace, contract_override_reason)
    if refusal is not None:
        return refusal
    _record_call(session_id, "cortex_write_log")  # L2: only record allowed writes
    ws = _write_ws(workspace, session_id)
    with _sessions_lock:
        registered = _sessions.get(session_id or "")
        session = dict(registered) if registered is not None else None
    contract_id = (session or {}).get("contract_id", "")  # link the closeout to its contract
    unresolved = await asyncio.to_thread(validate_evidence, evidence or [], ws)
    theater = evidence_theater_warning(status, result, tests, evidence)
    handoff_problems = validate_handoff_field(handoff)
    # Redact-at-ingress (community tools survey 2026-07-07, VEIL's pattern; mirrors the
    # existing cortex_playbook_report redaction below rather than inventing a new mechanism):
    # `task`/`result`/`evidence` are written verbatim to the permanent audit trail, so scrub
    # credential-shaped substrings (JWTs, bearer tokens, key=value secrets, long hex blobs)
    # BEFORE they reach `write_closeout` -- not after. Runs after validate_evidence/theater/
    # handoff checks above (which need the original values) but before the actual write.
    from . import playbooks as _pb
    redact_payload = {"task": task, "result": result, "evidence": evidence}
    clean_payload, redacted = _pb.redact_obj(redact_payload)
    task, result, evidence = clean_payload["task"], clean_payload["result"], clean_payload["evidence"]
    # GAP G4: reconcile-on-write + memory-write policy (default ON; CORTEX_WRITE_POLICY=0 restores
    # the old blind-append). This makes the store a decision procedure with a security boundary,
    # not a blind log. Runs on the redacted values (so dedup matches what's actually written) and,
    # like the gates above, is scoped to the MCP write tool -- the session-less CLI path
    # (audit.write_closeout) is deliberately left as-is. See cortex_core/write_policy.py.
    reconcile_action: str | None = None
    supersedes: list[str] = []
    if write_policy_enabled():
        policy, decision = await asyncio.to_thread(
            evaluate_write, ws, task, result, status, tests, scripts)
        if not policy.allowed:
            _log_event(session_id, "write_policy_refused", workspace, task=task,
                       violations=policy.violations)
            # GAP G1: a deterministic gate refusal populates gate_failures.jsonl and feeds the
            # self-learning flywheel a quarantined anti_pattern candidate. Fail-open by contract.
            try:
                from . import self_learning
                await asyncio.to_thread(
                    self_learning.record_gate_failure, ws,
                    gate="write_policy", tool="cortex_write_log",
                    detail="; ".join(policy.violations), session_id=session_id or "")
            except Exception:  # noqa: BLE001 -- the flywheel is additive, never blocks the gate
                pass
            return {
                "refused": True,
                "tool": "cortex_write_log",
                "reason": "memory-write policy rejected this input: " + "; ".join(policy.violations),
                "how_to_comply": (
                    "a stored memory must not carry override/injection directives or an oversized/"
                    "empty subject -- rephrase the task/result to describe the work plainly and retry"
                ),
            }
        reconcile_action = decision.action
        supersedes = decision.supersedes
        if decision.action == NOOP:
            # Exact duplicate already on record -- don't blindly append. Point at the record we
            # deduplicated against instead of writing a second identical closeout.
            existing_path = (decision.target or {}).get("_file")
            _log_event(session_id, "write_reconcile_noop", workspace, task=task,
                       existing=existing_path)
            return {"path": existing_path, "contract_id": contract_id, "reconcile": NOOP,
                    "deduplicated": True, "reconcile_reason": decision.reason,
                    "project_state": _project_state_outcome(
                        "NOT_APPLICABLE", "no new audit closeout was written",
                        audit_committed=False,
                    )}
    path = await asyncio.to_thread(
        write_closeout, ws, task, result,
        status=status, tests=tests, scripts=scripts,
        contract_id=contract_id, evidence=evidence, handoff=handoff,
        supersedes=supersedes,
    )
    _log_event(session_id, "cortex_write_log", workspace, task=task, status=status,
               contract_id=contract_id, reconcile=reconcile_action, supersedes=supersedes)
    out: dict[str, Any] = {"path": str(path), "contract_id": contract_id}
    out["project_state"] = await asyncio.to_thread(
        _closeout_project_state_event,
        ws,
        Path(path),
        task=task,
        result=result,
        status=status,
        handoff=handoff,
        session=session,
        state_machine_override_reason=state_machine_override_reason,
    )
    if reconcile_action is not None:
        out["reconcile"] = reconcile_action
        if supersedes:
            out["supersedes"] = supersedes
    if unresolved:
        out["evidence_warning"] = f"these evidence refs don't resolve to a real file: {unresolved}"
    if theater:
        out["evidence_theater_warning"] = theater
    if handoff_problems:
        out["handoff_warning"] = (
            "closeout is missing a complete handoff (where the artifacts are + what "
            "happens next): " + "; ".join(handoff_problems)
        )
    if redacted:
        out["redaction_notice"] = (
            "credential-shaped text was detected and redacted from task/result/evidence before "
            "logging; do NOT put secrets/tokens/passwords in a closeout"
        )
    return out


@mcp.tool()
@_guard_workspace("cortex_contract")
async def cortex_contract(
    task: str = "",
    session_id: str | None = None,
    planned_approach: str = "",
    acceptance_criteria: list[str] | None = None,
    verification_steps: list[str] | None = None,
    task_type: str | None = None,
    evidence_refs: list[str] | None = None,
    workspace: str | None = None,
    action: str = "approach",
    assurance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contract dispatcher. Default ``action="approach"`` creates or approves an
    approach contract for this task (Phase 4). Call with
    only ``task`` to get a corpus-prefilled STUB -- ``evidence_refs`` filled from
    a corpus search, ``task_type`` inferred -- then fill in ``planned_approach``,
    ``acceptance_criteria`` and ``verification_steps`` and call again to validate.
    ``action="driver_preflight"`` or ``"receipt_status"`` verifies external
    assurance data supplied in the single ``assurance`` object; it never mints it."""
    if action in {"driver_preflight", "receipt_status"}:
        payload = assurance if isinstance(assurance, dict) else {}
        return cortex_assurance(
            action=action,
            execution_contract=payload.get("execution_contract"),
            observation=payload.get("observation"),
            signature_envelope=payload.get("signature_envelope"),
            receipt_id=str(payload.get("receipt_id") or ""),
            expected_task_id=str(payload.get("expected_task_id") or ""),
            expected_run_id=str(payload.get("expected_run_id") or ""),
            expected_execution_contract_sha256=str(
                payload.get("expected_execution_contract_sha256") or ""),
            expected_success_contract_sha256=str(
                payload.get("expected_success_contract_sha256") or ""),
            session_id=session_id,
            workspace=workspace,
        )
    if action != "approach":
        return {
            "ok": False,
            "code": "UNKNOWN_CONTRACT_ACTION",
            "valid_actions": ["approach", "driver_preflight", "receipt_status"],
        }
    if not task.strip():
        return {"ok": False, "code": "CONTRACT_TASK_REQUIRED"}
    from . import contract as _contract

    _record_call(session_id, "cortex_contract")
    ws = _write_ws(workspace, session_id)
    session = _sessions.get(session_id or "")

    submitting = bool(planned_approach or acceptance_criteria or verification_steps)
    if not submitting:
        con = await asyncio.to_thread(
            _contract.prefill_contract, task, ws,
            (session or {}).get("model", "auto"), (session or {}).get("role", "builder"),
        )
        await asyncio.to_thread(_contract.save_contract, con, ws)
        if session is not None:
            session["contract_id"] = con.contract_id
            session["contract_approved"] = False
        _log_event(session_id, "cortex_contract", workspace, mode="prefill", task=task)
        return {
            "mode": "prefill",
            "contract_id": con.contract_id,
            "task_type": con.task_type,
            "evidence_refs": con.evidence_refs,
            "next": "fill planned_approach + acceptance_criteria[] + verification_steps[] "
            "and call cortex_contract again to approve it",
        }

    prior = _contract.load_contract((session or {}).get("contract_id", ""), ws) if session else None
    con = _contract.Contract(
        contract_id=(prior.contract_id if prior else _contract.new_contract_id()),
        task=task,
        task_type=task_type or (prior.task_type if prior else _contract._infer_task_type(task)),
        evidence_refs=evidence_refs if evidence_refs is not None else (prior.evidence_refs if prior else []),
        planned_approach=planned_approach,
        acceptance_criteria=acceptance_criteria or [],
        verification_steps=verification_steps or [],
        model=(session or {}).get("model", "auto"),
        role=(session or {}).get("role", "builder"),
        created_at=(prior.created_at if prior else _contract._now()),
    )
    # M1 (review): a re-submit revokes prior approval until this one validates,
    # so a failed re-submit can't leave a stale `True` authorizing writes.
    if session is not None:
        session["contract_approved"] = False
    ok, errors = _contract.validate_contract(con, ws)
    if ok:
        await asyncio.to_thread(_contract.save_contract, con, ws)
        if session is not None:
            session["contract_id"] = con.contract_id
            session["contract_approved"] = True
    _log_event(session_id, "cortex_contract", workspace, mode="submit", approved=ok)
    if not ok:
        nxt = "fix the errors and resubmit"
    elif session is None:
        # L4: a valid contract with no registered session isn't attached to
        # anything -- don't claim the write tools are unlocked.
        nxt = "contract is valid but no registered session to attach it to; call cortex_register, then resubmit before writes are allowed"
    else:
        nxt = "you may now use the write tools"
    return {
        "mode": "submit",
        "approved": ok and session is not None,
        "contract_id": con.contract_id,
        "errors": errors,
        "next": nxt,
    }


@_guard_workspace("cortex_deep_research")
async def cortex_deep_research(
    question: str, topics: list[str] | None = None, session_id: str | None = None,
    workspace: str | None = None, do_fetch: bool = True, frame: bool = False,
    summarize: bool = False,
) -> dict[str, Any]:
    """Start a deep-research run and return a task handle IMMEDIATELY (async task-handoff).

    A long research fan-out inside one blocking tool call is fragile across MCP clients
    (default 60s timeout), so this returns a task_id right away; the work runs in the
    background. Poll cortex_research_status(task_id) for progress and the finished report.
    Set do_fetch=False for corpus-only (no network); frame/summarize opt into Haiku
    decomposition/prose (GAP-CORTEX-0003 v1)."""
    # Deep research FETCHES + WRITES a report into the workspace, so it is a mutation and
    # must pass the ownership gate too (was an un-gated write hole). In dual-plane the write
    # lands in the tenant's own workspace (allowed); in single-plane served it's admin-gated.
    refusal = _admin_gate(session_id, "cortex_deep_research", workspace)
    if refusal is not None:
        return refusal
    from cortex_core import deep_research as DR
    _record_call(session_id, "cortex_deep_research")
    ws = str(_write_ws(workspace, session_id))  # explicit-override precedence, tenant-pin safe
    brain_ws = _read_ws(workspace, session_id)
    handle = await asyncio.to_thread(
        DR.start_deep_research, question, ws, background=True,
        topics=topics or [], do_fetch=do_fetch, do_frame=frame, do_summarize=summarize,
        brain_workspace=brain_ws)
    _log_event(session_id, "cortex_deep_research", workspace, task_id=handle.get("task_id"))
    return {**handle, "next": _next_actions(session_id, default="cortex_research_status")}


@_guard_workspace("cortex_research_status")
async def cortex_research_status(
    task_id: str, session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Poll a cortex_deep_research task by its task_id. Returns state
    (pending|running|done|failed); when done, includes the report_path, cite-check
    (coverage/corroboration/unanswered), and a faithfulness grounding score of the report
    against its fetched sources."""
    from cortex_core import deep_research as DR
    rec = await asyncio.to_thread(DR.research_status, task_id, str(_write_ws(workspace, session_id)))
    nxt = "cortex_write_log" if rec.get("state") == "done" else "cortex_research_status"
    return {**rec, "next": _next_actions(session_id, default=nxt)}


@_guard_workspace("cortex_register_source")
def cortex_register_source(
    url: str, title: str, topics: list[str] | None = None, trust_tier: str = "T3",
    discovered_via: str = "", admin_token: str | None = None,
    session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Register a newly agent-discovered URL as a permanent research-source candidate --
    the zero-new-dependency, agent-assisted alternative to a paid search-API integration
    (see docs/research/AGENT-ASSISTED-SOURCE-DISCOVERY-2026-07-07.md). Call this when a
    cortex_deep_research report/status carries a `needs_sources` gap: use WebSearch/WebFetch
    (tools this server does NOT itself have -- only the CALLER, e.g. Claude Code, has them)
    to find candidate URLs for the uncovered topics, register each one here, then re-issue
    cortex_deep_research so the pipeline fetches and cites them. A weak local model with no
    web-search tool can surface the gap but cannot fill it -- an accepted limitation, not a
    bug. Appends to the SAME research/sources.yaml registry select_sources() reads from, so
    today's discovery is tomorrow's registry hit (no re-discovery needed); deduped by URL.
    The URL is validated against the same SSRF/scheme host guard fetch_document already
    enforces -- registering a private-network or non-http(s) target is refused, not silently
    allowed. Owner/admin only, same gating as cortex_issue_key: a registry anyone could write
    unvetted URLs into is a real corpus-poisoning risk (the fable-sources.md pollution lesson,
    CLAUDE.md)."""
    if not _key_admin_ok(session_id, admin_token):
        return {"error": "admin_required", "note": "registering sources is owner/admin only"}
    from cortex_core import research as R
    ws = str(_write_ws(workspace, session_id))
    _record_call(session_id, "cortex_register_source")
    try:
        result = R.register_source(url, title, topics or [], trust_tier, discovered_via, ws)
    except ValueError as exc:
        result = {"registered": False, "reason": str(exc), "url": url}
    _log_event(session_id, "cortex_register_source", workspace, url=url,
              registered=result.get("registered"))
    return {**result, "next": _next_actions(session_id, default="cortex_deep_research")}


@_guard_workspace("cortex_tasks_list")
async def cortex_tasks_list(
    status: str | None = None, session_id: str | None = None, workspace: str | None = None
) -> dict[str, Any]:
    """List the shared task-coordination ledger (GAP-0016): what tasks exist and
    who owns each. Reads are open to any connected peer -- call this BEFORE
    starting work to see if a peer already owns the task, and to find an
    unclaimed (`pending`) one to pick up. Optionally filter by status
    (pending/active/done/failed)."""
    from . import task_ledger as _tl

    _record_call(session_id, "cortex_tasks_list")
    tasks = await asyncio.to_thread(_tl.list_tasks, str(_write_ws(workspace, session_id)), status)
    _log_event(session_id, "cortex_tasks_list", workspace, status=status, n_tasks=len(tasks))
    pending = [t for t in tasks if t.get("status") == "pending"]
    default = (
        f"{len(pending)} pending task(s) -- cortex_tasks_claim one before you start"
        if pending
        else "no pending tasks -- nothing unclaimed to pick up right now"
    )
    return {"tasks": tasks, "n_tasks": len(tasks),
            "next": _next_actions(session_id, default=default)}


@_guard_workspace("cortex_tasks_claim")
async def cortex_tasks_claim(
    task_id: str, owner: str | None = None,
    session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Atomically claim a pending task (GAP-0016). Exactly one of two racing
    agents wins -- the claim runs under an exclusive lock, so a peer that claimed
    first keeps it and you're told who owns it. The claim is bound to your
    registered agent_id (from cortex_register); pass `owner` only to override it
    (e.g. an unregistered/CLI context). Returns claimed=True on success."""
    from . import task_ledger as _tl

    _record_call(session_id, "cortex_tasks_claim")
    session = _sessions.get(session_id or "")
    resolved_owner = owner or (session or {}).get("agent_id")
    if not resolved_owner:
        return {
            "claimed": False,
            "reason": "no owner: register first (cortex_register) or pass owner=...",
            "task_id": task_id,
        }
    result = await asyncio.to_thread(_tl.claim_task, task_id, resolved_owner,
                                     str(_write_ws(workspace, session_id)))
    _log_event(session_id, "cortex_tasks_claim", workspace,
               task_id=task_id, owner=resolved_owner, claimed=result.get("claimed"))
    if result.get("claimed"):
        default = "task claimed -- do the work, then cortex_tasks_update to mark it done/failed"
    else:
        default = f"claim refused ({result.get('reason')}) -- pick a different pending task"
    return {**result, "next": _next_actions(session_id, default=default)}


@_guard_workspace("cortex_tasks_update")
async def cortex_tasks_update(
    task_id: str, status: str | None = None, result: str | None = None,
    owner: str | None = None, session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Update a task you're working on (GAP-0016): mark it `done` or `failed`,
    attach a short `result`, or hand off `owner`. Runs under the ledger lock so
    it builds on the live record. Call cortex_tasks_claim first to take
    ownership; update to close the loop when the work lands."""
    from . import task_ledger as _tl

    _record_call(session_id, "cortex_tasks_update")
    res = await asyncio.to_thread(
        _tl.update_task, task_id, str(_write_ws(workspace, session_id)), status, owner, result
    )
    _log_event(session_id, "cortex_tasks_update", workspace,
               task_id=task_id, status=status, updated=res.get("updated"))
    if res.get("updated") and status in ("done", "failed"):
        default = "task closed -- cortex_write_log a closeout for the permanent audit trail"
    elif res.get("updated"):
        default = "task updated"
    else:
        default = f"update refused ({res.get('reason')})"
    return {**res, "next": _next_actions(session_id, default=default)}


@mcp.tool()
@_guard_workspace("cortex_ontology_query")
async def cortex_ontology_query(
    op: str,
    ref: str | None = None,
    entity_type: str | None = None,
    status: str | None = None,
    name_contains: str | None = None,
    predicate: str | None = None,
    direction: str = "both",
    session_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Query the living ontology (Phase 7): the structured knowledge graph that
    IS the current state of the project -- entities (models, rubrics, benchmarks,
    checkers, gaps, phases, docs, patterns, modules) and their relations. Prefer
    this over guessing which doc/rubric/gap is CURRENT; that is the exact question
    the graph answers. Read-only. Pick an ``op``:

    - ``stats``: topology summary (entity counts by type/status, live-edge counts
      by predicate).
    - ``find``: filter current entities by ``entity_type`` / ``status`` /
      ``name_contains``.
    - ``get``: resolve one entity by ``ref`` (exact id, name, or alias).
    - ``neighbors``: one-hop edges from the entity ``ref`` resolves to, optionally
      filtered by ``predicate`` and ``direction`` (in/out/both).
    - ``current``: given ``ref``, follow ``supersedes`` edges to the entity that
      is currently live, and return the supersession chain."""
    from . import ontology as _ont

    _record_call(session_id, "cortex_ontology_query")

    def _run() -> dict[str, Any]:
        bw = _read_ws(workspace, session_id)  # ontology READS resolve to the brain plane (dual-plane)
        if op == "stats":
            return _ont.graph_stats(bw)
        if op == "find":
            found = _ont.find_entities(
                type=entity_type, status=status, name_contains=name_contains, workspace=bw
            )
            return {"entities": [e.to_dict() for e in found], "count": len(found)}
        if op in ("get", "neighbors", "current"):
            if not ref:
                return {"error": f"op {op!r} requires a ref"}
            if op == "current":
                return _ont.current_version(ref, bw)
            entity = _ont.resolve_entity(ref, bw)
            if entity is None:
                return {"found": False, "ref": ref}
            if op == "get":
                return {"found": True, "entity": entity.to_dict()}
            return {
                "entity": entity.entity_id,
                "neighbors": _ont.neighbors(
                    entity.entity_id, predicate=predicate, direction=direction, workspace=bw
                ),
            }
        return {"error": f"unknown op {op!r}; use stats|find|get|neighbors|current"}

    result = await asyncio.to_thread(_run)
    _log_event(session_id, "cortex_ontology_query", workspace, op=op, ref=ref)
    return {**result, "next": _next_actions(
        session_id, default="ontology is the current-state graph -- act on it, then cortex_write_log")}


@_guard_workspace("cortex_playbook_lookup")
async def cortex_playbook_lookup(
    site: str, session_id: str | None = None, workspace: str | None = None
) -> dict[str, Any]:
    """Look up the self-learning navigation PLAYBOOK for a site (per-site exploration
    knowledge for the customer's OWN browser automation -- Cortex serves the knowledge,
    it NEVER runs a browser). Pass a URL or bare site id. Read-only.

    Returns the current playbook if one exists (status, confidence, key_locators as
    role+name INTENT not CSS, known_pitfalls, the verification_check success oracle), or a
    clear 'no playbook yet -- explore with robust primitives and report back' response if
    none exists. A degraded/quarantined status means the knowledge is stale: re-explore
    rather than replay. Playbook READS resolve on the brain plane (dual-plane)."""
    from . import playbooks as _pb

    _record_call(session_id, "cortex_playbook_lookup")
    result = await asyncio.to_thread(_pb.lookup, site, _read_ws(workspace, session_id))
    _log_event(session_id, "cortex_playbook_lookup", workspace,
               site=result.get("site_id"), exists=result.get("exists"))
    nxt = ("act with your own browser automation, then cortex_playbook_report what happened"
           if result.get("exists")
           else "explore the site yourself, then cortex_playbook_report so the next agent inherits it")
    return {**result, "next": _next_actions(session_id, default=nxt)}


@_guard_workspace("cortex_playbook_report")
async def cortex_playbook_report(
    site: str,
    action_taken: str,
    locator_strategy_used: str,
    outcome: str,
    verification_result: str | None = None,
    new_locator: dict[str, Any] | None = None,
    pitfall: str | None = None,
    entry_point: str | None = None,
    verification_check: dict[str, Any] | None = None,
    auth_note: str | None = None,
    handoff: dict[str, Any] | None = None,
    session_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """The customer's browser automation reports what it did on a site and whether it
    worked. Cortex is PASSIVE here: it receives a report about a connection the customer
    already owns -- it never launches/connects/holds a browser session.

    This call (a) writes a REAL audit closeout via the normal write_closeout path (with a
    handoff field), so every reported action is genuinely logged, and (b) updates the
    site's playbook per the learning loop: a new working locator (self-heal succeeded) ->
    v+1 with a change_log entry (stays uncorroborated until a 2nd success); a verified
    failure -> confidence decay -> degraded -> quarantine + fresh-exploration flag; a
    verified success -> confidence up + corroboration.

    `verification_result` (pass|fail) is the success oracle and is authoritative over the
    self-reported `outcome`. `new_locator` stores INTENT ({intent, role, name, anchors,
    visual_fallback}) -- NEVER a raw CSS string or any credential/token. Any
    credential-shaped text in the payload is REDACTED before it is written to the audit
    trail (do not send session cookies/tokens -- the schema has no place for them)."""
    from . import playbooks as _pb

    # A report WRITES (playbook + closeout), so it must pass the ownership gate like other
    # write tools: a READ-scoped key is refused; served single-plane needs admin; dual-plane
    # routes the write to the tenant's own plane. Deliberately NOT contract-gated -- a playbook
    # report is its own always-log flow (a specialized closeout), not general task work.
    refusal = _admin_gate(session_id, "cortex_playbook_report", workspace)
    if refusal is not None:
        return refusal

    _record_call(session_id, "cortex_playbook_report")

    # Redact customer-supplied data BEFORE it touches the playbook or the audit trail.
    payload = {
        "action_taken": action_taken, "locator_strategy_used": locator_strategy_used,
        "outcome": outcome, "verification_result": verification_result,
        "new_locator": new_locator, "pitfall": pitfall, "entry_point": entry_point,
        "verification_check": verification_check, "auth_note": auth_note,
    }
    clean, redacted = _pb.redact_obj(payload)

    # WRITE plane with explicit-override precedence (tenant-pin safe): resolve once and thread the
    # concrete path through so the playbook, the closeout, and the returned path all agree.
    ws = _write_ws(workspace, session_id)
    ws_str = str(ws)
    pb, summary = await asyncio.to_thread(
        _pb.apply_report,
        site, clean["action_taken"], clean["locator_strategy_used"], clean["outcome"],
        verification_result=clean["verification_result"], new_locator=clean["new_locator"],
        pitfall=clean["pitfall"], entry_point=clean["entry_point"],
        verification_check=clean["verification_check"], auth_note=clean["auth_note"],
        workspace=ws_str,  # WRITE plane: the tenant's own workspace, never the brain
    )

    # Write a REAL closeout via the normal path. A default handoff is synthesized when the
    # caller doesn't supply one, so this always satisfies the standing handoff requirement.
    pb_path = _pb.playbooks_dir(ws_str) / f"{_pb._slug(pb.site_id)}.md"
    if not handoff:
        handoff = {
            "locations": [str(pb_path)],
            "continuation": (
                f"playbook for {pb.site_id} now v{pb.playbook_version}, status {pb.status}, "
                f"confidence {pb.confidence:.2f}"
                + (" -- QUARANTINED, schedule fresh exploration" if pb.needs_exploration else "")
            ),
        }
    handoff_problems = validate_handoff_field(handoff)
    task = f"browser playbook report: {clean['action_taken']} on {pb.site_id}"
    result = (
        f"outcome={clean['outcome']} verification={clean['verification_result']}; "
        + "; ".join(summary["changes"])
    )
    path = await asyncio.to_thread(
        write_closeout, ws, task, result,
        status=("completed" if summary["success_recorded"] else "failed"),
        handoff=handoff,
    )
    _log_event(session_id, "cortex_playbook_report", workspace,
               site=pb.site_id, status=pb.status, redacted=redacted)

    out: dict[str, Any] = {
        **summary,
        "playbook_path": str(pb_path),
        "closeout_path": str(path),
        "redacted": redacted,
        "next": _next_actions(session_id, default="playbook + closeout written -- continue or re-explore if quarantined"),
    }
    if redacted:
        out["redaction_notice"] = (
            "credential-shaped text was detected and redacted from the report before logging; "
            "do NOT send session cookies/tokens -- Cortex has no place to store them and never should"
        )
    if handoff_problems:
        out["handoff_warning"] = "; ".join(handoff_problems)
    return out


# =============================================================================
# G5 (2026-07-14): action-arg dispatchers -- tool-surface consolidation.
#
# Four low-coupling tool FAMILIES (12 always-loaded @mcp.tool()s) are folded
# into four `action`-routed dispatchers below. The per-action implementations
# above are unchanged (they kept their bodies, guards, telemetry, and logged
# names -- only the @mcp.tool() registration was removed), so behavior and the
# `_log_event(...)` sub-action names are byte-identical; only the EAGER-LOADED
# MCP tool surface shrinks. Net: 12 registered tools -> 4 (see
# docs/MCP-CONTEXT-BUDGET.md and tests/test_mcp_context_budget.py, re-frozen).
# None of these families is phase-gated (they are absent from state_engine's
# phase_legal_tools), so this touches only the all-schemas eager surface, not
# the per-phase disclosure budget. Adding more families (mission/phase/run)
# would require rewiring the chart and is deliberately left as a follow-up.
# =============================================================================

@mcp.tool()
async def cortex_key(
    action: str, label: str = "", scope: str = "read", tenant_id: str | None = None,
    key_id: str = "", admin_token: str | None = None, session_id: str | None = None,
) -> dict[str, Any]:
    """Owner/admin API-key management (consolidated). `action` selects the op:
      * issue  -- mint a scoped key (label, scope='read'|'tenant_write', tenant_id);
                  returns the raw key ONCE (only its SHA-256 is kept).
      * rotate -- revoke key_id and mint a fresh key with the same scope/tenant.
      * revoke -- kill key_id immediately.
      * list   -- metadata only (key_id/label/scope/tenant/status), never the raw key.
    All are owner/admin only (admin_token or an admin session)."""
    if action == "issue":
        return cortex_issue_key(label, scope=scope, tenant_id=tenant_id,
                                admin_token=admin_token, session_id=session_id)
    if action == "rotate":
        return cortex_rotate_key(key_id, admin_token=admin_token, session_id=session_id)
    if action == "revoke":
        return cortex_revoke_key(key_id, admin_token=admin_token, session_id=session_id)
    if action == "list":
        return cortex_list_keys(admin_token=admin_token, session_id=session_id)
    return {"error": "unknown_action", "action": action,
            "valid_actions": ["issue", "rotate", "revoke", "list"]}


@mcp.tool()
async def cortex_tasks(
    action: str, task_id: str = "", status: str | None = None, owner: str | None = None,
    result: str | None = None, session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Shared task-coordination ledger (GAP-0016, consolidated). `action`:
      * list   -- what tasks exist and who owns each (optional status filter:
                  pending/active/done/failed). Call BEFORE starting work.
      * claim  -- atomically claim a pending task_id (exactly one racer wins);
                  bound to your registered agent_id, or pass owner to override.
      * update -- mark task_id done/failed, attach a short result, or hand off owner."""
    if action == "list":
        return await cortex_tasks_list(status=status, session_id=session_id, workspace=workspace)
    if action == "claim":
        return await cortex_tasks_claim(task_id, owner=owner, session_id=session_id,
                                        workspace=workspace)
    if action == "update":
        return await cortex_tasks_update(task_id, status=status, result=result, owner=owner,
                                         session_id=session_id, workspace=workspace)
    return {"error": "unknown_action", "action": action,
            "valid_actions": ["list", "claim", "update"]}


@mcp.tool()
async def cortex_research(
    action: str, question: str = "", task_id: str = "", topics: list[str] | None = None,
    url: str = "", title: str = "", trust_tier: str = "T3", discovered_via: str = "",
    do_fetch: bool = True, frame: bool = False, summarize: bool = False,
    proposal: dict[str, Any] | None = None, policy: dict[str, Any] | None = None,
    proposal_sha256: str = "", attestation_ids: list[str] | None = None,
    receipt_id: str = "", expected_task_id: str = "", expected_decision_id: str = "",
    expected_policy_sha256: str = "", expected_research_task_id: str = "",
    expected_run_id: str = "",
    admin_token: str | None = None, session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Deep-research pipeline (GAP-CORTEX-0003, consolidated). `action`:
      * run      -- start a deep-research run on `question`; returns a task_id
                    IMMEDIATELY (work runs in the background). topics/do_fetch/
                    frame/summarize as before. Poll with action='status'.
      * status   -- poll a run by task_id; when done, report_path + cite-check +
                    faithfulness grounding score.
      * register_source -- register an agent-discovered url/title/topics as a
                     permanent research-source candidate (owner/admin only; SSRF-guarded)
                     to fill a `needs_sources` gap, then re-run.
      * propose_sufficiency -- freeze a report/evidence proposal against an already-registered
                     trusted policy. This surface cannot register or weaken the policy.
      * finalize_sufficiency -- mechanically assess the frozen proposal and reference opaque
                     attestations minted on the separate trusted evaluator/human surface.
      * receipt_status -- inspect or validate a stored receipt binding. Only a bound
                     SUFFICIENT_FOR_DECISION receipt unlocks an assured state-machine track.

    Deliberately absent: policy registration and attestation minting. A builder evaluating its
    own evidence through this tool is never treated as independent review."""
    if action == "run":
        return await cortex_deep_research(question, topics=topics, session_id=session_id,
                                          workspace=workspace, do_fetch=do_fetch, frame=frame,
                                          summarize=summarize)
    if action == "status":
        return await cortex_research_status(task_id, session_id=session_id, workspace=workspace)
    if action == "register_source":
        return cortex_register_source(url, title, topics=topics, trust_tier=trust_tier,
                                      discovered_via=discovered_via, admin_token=admin_token,
                                      session_id=session_id, workspace=workspace)
    if action == "propose_sufficiency":
        if not isinstance(proposal, dict) or not isinstance(policy, dict):
            return {"ok": False, "code": "BAD_SUFFICIENCY_PROPOSAL",
                    "reason": "proposal and policy must be objects"}
        from .research_sufficiency import freeze_proposal
        try:
            frozen = await asyncio.to_thread(
                freeze_proposal, proposal, policy,
                workspace=_write_ws(workspace, session_id),
            )
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "code": "SUFFICIENCY_PROPOSAL_REFUSED", "reason": str(exc)}
        return {"ok": True, **frozen,
                "next": "obtain attestations through the separate trusted evaluator/human surface; "
                        "the builder-facing MCP cannot mint them"}
    if action == "finalize_sufficiency":
        if not proposal_sha256 or not isinstance(policy, dict):
            return {"ok": False, "code": "BAD_SUFFICIENCY_FINALIZE",
                    "reason": "proposal_sha256 and policy are required"}
        from .research_sufficiency import finalize_sufficiency
        try:
            receipt = await asyncio.to_thread(
                finalize_sufficiency, proposal_sha256, policy, attestation_ids or [],
                workspace=_write_ws(workspace, session_id),
                assessed_at=datetime.now(timezone.utc).isoformat(),
            )
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "code": "SUFFICIENCY_FINALIZE_REFUSED", "reason": str(exc)}
        return {"ok": True, "receipt": receipt,
                "may_unlock": receipt["outcome"] == "SUFFICIENT_FOR_DECISION"}
    if action == "receipt_status":
        from .research_sufficiency import lookup_sufficiency_receipt, validate_sufficiency_receipt
        ws = _write_ws(workspace, session_id)
        if expected_task_id and expected_decision_id and expected_policy_sha256:
            checked = await asyncio.to_thread(
                validate_sufficiency_receipt, receipt_id,
                expected_task_id=expected_task_id,
                expected_decision_id=expected_decision_id,
                expected_policy_sha256=expected_policy_sha256,
                expected_research_task_id=expected_research_task_id or None,
                expected_run_id=expected_run_id or None,
                workspace=ws, now=datetime.now(timezone.utc).isoformat(),
            )
            return {"ok": checked.get("valid", False), **checked}
        receipt = await asyncio.to_thread(lookup_sufficiency_receipt, receipt_id, ws)
        return ({"ok": True, "receipt": receipt} if receipt else
                {"ok": False, "code": "UNKNOWN_RECEIPT"})
    return {"error": "unknown_action", "action": action,
            "valid_actions": ["run", "status", "register_source", "propose_sufficiency",
                              "finalize_sufficiency", "receipt_status"]}


@mcp.tool()
async def cortex_playbook(
    action: str, site: str = "", action_taken: str = "", locator_strategy_used: str = "",
    outcome: str = "", verification_result: str | None = None,
    new_locator: dict[str, Any] | None = None, pitfall: str | None = None,
    entry_point: str | None = None, verification_check: dict[str, Any] | None = None,
    auth_note: str | None = None, handoff: dict[str, Any] | None = None,
    session_id: str | None = None, workspace: str | None = None,
) -> dict[str, Any]:
    """Self-learning browser navigation PLAYBOOK (consolidated; Cortex serves the
    knowledge, it NEVER runs a browser). `action`:
      * lookup -- read the current per-site playbook for `site` (locators as
                  role+name INTENT, pitfalls, the verification_check oracle), or a
                  'no playbook yet -- explore' response. Read-only.
      * report -- the customer's own automation reports what it did (action_taken,
                  locator_strategy_used, outcome, verification_result=pass|fail the
                  authoritative oracle, optional new_locator INTENT/pitfall/handoff);
                  writes a real audit closeout and updates the learning loop."""
    if action == "lookup":
        return await cortex_playbook_lookup(site, session_id=session_id, workspace=workspace)
    if action == "report":
        return await cortex_playbook_report(
            site, action_taken, locator_strategy_used, outcome,
            verification_result=verification_result, new_locator=new_locator, pitfall=pitfall,
            entry_point=entry_point, verification_check=verification_check, auth_note=auth_note,
            handoff=handoff, session_id=session_id, workspace=workspace)
    return {"error": "unknown_action", "action": action,
            "valid_actions": ["lookup", "report"]}


@mcp.resource("cortex://doc/{encoded_path}")
async def cortex_doc(encoded_path: str) -> str:
    """Read a corpus document directly by its path (as returned in
    cortex_search results, absolute or workspace-relative), bypassing
    tool-call overhead for a plain content read. Path-traversal safe: the
    resolved file must stay within the workspace root."""
    workspace = resolve_workspace(None).resolve()
    raw_path = urllib.parse.unquote(encoded_path)
    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"path {raw_path!r} resolves outside the workspace; refusing to read")
    if not resolved.is_file():
        raise ValueError(f"no such document: {raw_path!r}")
    return await asyncio.to_thread(resolved.read_text, encoding="utf-8", errors="replace")


def cortex_doc_uri(path: str) -> str:
    """Build the cortex://doc/... URI for a given path -- the encoding
    counterpart to cortex_doc, so callers don't have to hand-roll it."""
    return f"cortex://doc/{urllib.parse.quote(path, safe='')}"


@mcp.prompt()
def cortex_preflight(task: str = "") -> str:
    """Preflight: orient in the Cortex corpus before starting a task."""
    task_line = f" for: {task}" if task else ""
    return (
        f"Before starting{task_line}: call cortex_register if you haven't this "
        "session, then cortex_status, then cortex_search for anything relevant "
        "already in the corpus. Check here before the web -- that's the whole "
        "point of Cortex."
    )


@mcp.prompt()
def cortex_closeout(task: str, result: str, tests: str = "") -> str:
    """Closeout: write the audit record for what was just done."""
    tests_part = f", tests={tests!r}" if tests else ""
    return (
        f"Call cortex_write_log now with task={task!r}, result={result!r}{tests_part} "
        "-- this is the permanent record the self-learning loop depends on."
    )


# --- Self-restart on stale code (2026-07-07) --------------------------------------------------
# `mcp.run()` below blocks forever in a stdio serve loop -- confirmed live tonight: a source edit
# to cortex_core/*.py after the process starts is invisible to it (Python doesn't hot-reload), so
# a connecting agent silently gets a stale tool list/behavior until a human manually reconnects.
# This closes that gap operationally: a background thread records the newest .py mtime under
# cortex_core/ at startup, polls periodically, and on detecting a newer mtime than the baseline,
# exits the process cleanly so the next tool call forces a respawn.
#
# IMPORTANT, verified by reading Claude Code's own source (see
# docs/research/SERVER-SELF-RESTART-ON-STALE-CODE-2026-07-07.md for the full citation): Claude
# Code does NOT auto-reconnect a stdio MCP server after its process exits -- it marks the server
# `failed` and the user must run `/mcp` to relaunch it. This mechanism therefore does not fully
# close the original gap for that client on its own; what it buys is a fast, correct FAILURE
# signal (the server visibly drops, on a bounded poll interval, without a human noticing hours of
# stale behavior first) instead of a slow silent one. Other stdio MCP clients may behave
# differently -- some conformant clients do lazily respawn on the next request against a closed
# pipe -- so this is still the right default; the doc is explicit that the "client relaunches on
# next use" assumption is unverified in general, and FALSE for Claude Code as of 2026-07.

_SELF_RESTART_POLL_SECONDS = 45
# Best-effort in-flight guard (not exhaustive -- see docs/research doc): a restart should not
# land mid-mutation of the two known shared, lock-guarded structures in this module
# (_sessions_lock / _run_engines_lock, stability audit finding #4). This bounds how long the
# watcher waits for those specific locks to go quiet before restarting anyway.
_SELF_RESTART_LOCK_WAIT_RETRIES = 3
_SELF_RESTART_LOCK_WAIT_SECONDS = 2


def _self_restart_on() -> bool:
    return (os.environ.get("CORTEX_SELF_RESTART_ON_STALE_CODE", "1").strip().lower()
            not in ("0", "false", "no", "off", ""))


def _scan_py_mtime(root: Path) -> tuple[float, str | None]:
    """Pure scan: the max mtime across every ``*.py`` file under ``root`` (recursive), and the
    path of the newest one. ``(0.0, None)`` if ``root`` doesn't exist or has no ``.py`` files.
    No side effects -- safe to call from a test or a hot poll loop."""
    latest_mtime = 0.0
    latest_path: str | None = None
    if not root.exists():
        return latest_mtime, latest_path
    for path in root.rglob("*.py"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue  # a file can vanish between rglob listing it and stat (editor save-rename)
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_path = str(path)
    return latest_mtime, latest_path


def _code_is_stale(baseline_mtime: float, root: Path) -> tuple[bool, float, str | None]:
    """Pure staleness check (unit-testable without threads/process exit): rescans ``root`` and
    compares its current newest-.py mtime against ``baseline_mtime``. Returns
    ``(is_stale, current_max_mtime, path_of_newest_file)``."""
    current_mtime, newest_path = _scan_py_mtime(root)
    return current_mtime > baseline_mtime, current_mtime, newest_path


def _self_restart_safe_now() -> bool:
    """Non-blocking probe of the two lock-guarded shared structures this module already has
    (``_sessions_lock`` / ``_run_engines_lock``). True only if both are currently free. This is
    deliberately NOT exhaustive -- it can't see an in-flight tool call that holds neither lock
    (e.g. a slow network fetch inside ``cortex_fetch_doc``); that's an accepted, documented risk
    (worst case: one in-flight call errors and the client retries after reconnecting), not a
    correctness gap, since all durable state here is file/SQLite-based."""
    got_sessions = _sessions_lock.acquire(blocking=False)
    if got_sessions:
        _sessions_lock.release()
    got_engines = _run_engines_lock.acquire(blocking=False)
    if got_engines:
        _run_engines_lock.release()
    return got_sessions and got_engines


def _self_restart_watch_loop(root: Path, stop_event: threading.Event) -> None:
    """Runs on a daemon thread. Blocks on ``stop_event`` between polls so tests (or a future
    graceful-shutdown path) can stop it without waiting out a full interval."""
    baseline_mtime, _ = _scan_py_mtime(root)
    while not stop_event.wait(_SELF_RESTART_POLL_SECONDS):
        stale, _current_mtime, newest_path = _code_is_stale(baseline_mtime, root)
        if not stale:
            continue
        for _ in range(_SELF_RESTART_LOCK_WAIT_RETRIES):
            if _self_restart_safe_now():
                break
            time.sleep(_SELF_RESTART_LOCK_WAIT_SECONDS)
        # stderr only -- stdout is the live MCP stdio transport; anything but protocol frames
        # there would corrupt the connection the moment it's written.
        print(
            f"[cortex-mcp] stale code detected ({newest_path} changed after server start) -- "
            "exiting so the next connection picks up fresh code. Claude Code does not "
            "auto-reconnect a stdio MCP server on process exit -- run /mcp to relaunch it.",
            file=sys.stderr,
            flush=True,
        )
        # os._exit, not sys.exit: this runs on a background thread, and sys.exit()/SystemExit
        # only unwinds that one thread -- it would not stop the main thread's blocking mcp.run()
        # event loop. os._exit() terminates the whole process immediately (no atexit/finally
        # handlers), which is what a clean "the process is gone, please reconnect" signal needs.
        os._exit(0)


def _start_self_restart_watch() -> threading.Thread | None:
    """Start the watcher thread if enabled. Returns the thread (for tests/introspection) or None
    if disabled via CORTEX_SELF_RESTART_ON_STALE_CODE=0."""
    if not _self_restart_on():
        return None
    root = Path(__file__).resolve().parent
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_self_restart_watch_loop,
        args=(root, stop_event),
        daemon=True,
        name="cortex-self-restart-watch",
    )
    thread.start()
    return thread


# --- Phase-toolkit surface (per-phase deterministic injection) -------------------------------
# The state machine knows the phase → this returns only the schemas the current phase needs.
# NOT the 3-level discover→load→call pattern — deterministic injection at injection time.
# The state machine IS the disclosure controller. No list_changed, no load_tool primitive.

# Always-present operational tools (needed regardless of phase)
_BASELINE_TOOLS = {"cortex_register", "cortex_status"}

# Tools that are registered MCP @mcp.tool() functions (not chart-internal advance_tools).
# G5 (2026-07-14): the 12 key/tasks/research/playbook family tools were folded into the four
# action-arg dispatchers below (cortex_key/cortex_tasks/cortex_research/cortex_playbook).
_REGISTERED_MCP_TOOLS = frozenset({
    "cortex_register", "cortex_onboarding", "cortex_key", "cortex_status", "cortex_fingerprint",
    "cortex_dispatch_tier", "cortex_run_start", "cortex_run_step", "cortex_run_state",
    "cortex_phase_state", "cortex_phase_heartbeat", "cortex_phase_checkpoint",
    "cortex_phase_resume", "cortex_report_empty_output", "cortex_spawn_mission",
    "cortex_mission_status", "cortex_acquire_claims", "cortex_submit_mission_contract",
    "cortex_submit_partition", "cortex_dispatch_mission", "cortex_submit_merge",
    "cortex_search", "cortex_scope_pack", "cortex_fetch_doc", "cortex_write_log",
    "cortex_contract", "cortex_research", "cortex_tasks", "cortex_ontology_query",
    "cortex_playbook",
})


def _phase_tool_names(track: str, state: str) -> list[str]:
    """Return the MCP tool names a phase exposes. Chart advance_tools that aren't
    registered MCP tools (cortex_report_findings, cortex_submit_plan, etc.) are
    step types handled by cortex_run_step — so we always include cortex_run_step,
    plus any extra_tools that ARE registered MCP tools, plus baseline ops."""
    raw = phase_legal_tools(track, state)
    # Always include cortex_run_step (how the agent advances the chart) + baseline
    tools = {"cortex_run_step"}
    tools.update(_BASELINE_TOOLS)
    for t in raw:
        if t in _REGISTERED_MCP_TOOLS:
            tools.add(t)
    return sorted(tools)


def phase_tool_schemas(track: str, state: str) -> list[dict[str, Any]]:
    """Return the JSON schemas for only the tools the current phase needs.
    This is what gets injected into the system prompt instead of all 30 schemas.
    Uses FastMCP tool registry to get the real schemas."""
    tool_names = set(_phase_tool_names(track, state))
    schemas: list[dict[str, Any]] = []
    try:
        for tool in mcp._tool_manager.list_tools():
            if tool.name in tool_names:
                schemas.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                })
    except Exception:
        pass
    return schemas


def phase_tools_token_count(track: str, state: str) -> int:
    """Count tokens of the phase-injected schemas (cl100k if available, else char/4)."""
    schemas = phase_tool_schemas(track, state)
    raw = json.dumps(schemas)
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(raw))
    except Exception:
        return len(raw) // 4


def full_tool_surface_token_count() -> int:
    """Count tokens of ALL tool schemas (for comparison/baseline)."""
    try:
        tools = mcp._tool_manager.list_tools()
        raw = json.dumps([{
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        } for t in tools])
    except Exception:
        return 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(raw))
    except Exception:
        return len(raw) // 4


def main() -> None:
    _start_self_restart_watch()
    mcp.run()


if __name__ == "__main__":
    main()
