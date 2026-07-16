"""Server-served onboarding: the Cortex brain self-describes HOW TO OPERATE IT, in-band.

The original goal of the "server is the harness" design: a cold-connecting agent shouldn't need a
handoff doc to learn what tools exist, when to use each, how the RAG flow works, which reasoning tier
fits which stage, and the disciplines. It should ask the server. This module assembles that guide
FROM THE ACTUAL SOURCES OF TRUTH (the live tool list, the forced pipeline, the capacity policy, the
task taxonomy) so it can't rot into a stale doc -- and `coverage_gap` + a test make adding an
undocumented tool a failure, not silent drift.

Kept free of any `mcp` import (no cycle): the caller passes in the live tool names + pipeline steps;
everything else is read from `capacity`/`patterns`, which don't import `mcp`.
"""
from __future__ import annotations

from typing import Any

from cortex_core.patterns import TASK_TYPES

# What each tool is FOR and WHEN to reach for it. Every registered MCP tool must appear here
# (enforced by test_onboarding's anti-drift check) -- that's what keeps this guide honest.
TOOL_GUIDANCE: dict[str, str] = {
    "cortex_register": "FIRST call. Announce agent_id/model/role; get a session_id (pass it to every "
                       "later call) + the pipeline. In served mode, pass admin_token only if you own the brain.",
    "cortex_onboarding": "THIS guide. If you're a fresh agent, call it before acting to learn what/when/how.",
    "cortex_status": "Orient: workspace/index health, whether you can WRITE here, and a next hint. Call after register.",
    "cortex_search": "RAG step 1 -- hybrid BM25+vector search of the brain corpus. Search HERE before the web; "
                     "guessing instead of checking is the exact failure Cortex exists to prevent.",
    "cortex_scope_pack": "Get a minimal, task-scoped context pack (large context cut) instead of dumping whole docs.",
    "cortex_fetch_doc": "Bring an external doc into the corpus (HTML->text, SSRF-guarded). Research/gap phase; "
                        "cheap models can fan out on this in parallel.",
    "cortex_contract": "PLAN gate. Submit an approach contract (phased, bounded tasks). Required before writes in the gated pipeline.",
    "cortex_write_log": "CLOSEOUT -- record task/result/tests-passed at the END of every non-trivial task, even on "
                        "failure. This is the self-learning audit record.",
    "cortex_research": "Deep-research pipeline (action=run|status|register_source). run: async corpus-first -> fetch -> "
                       "cite-check -> report, returns a task_id immediately (runs in the SERVER process). status: poll a "
                       "task_id (state='died' -> re-issue; a `needs_sources` gap -> register_source). register_source "
                       "(owner/admin): add an agent-discovered URL (via YOUR WebSearch/WebFetch), SSRF-guarded, then re-run.",
    "cortex_fingerprint": "Capture {size,mtime,sha256} when you READ a file; compare before you ACT, to catch a stale read.",
    "cortex_run_start": "Begin a tracked run through the server-side state machine (the pipeline as states).",
    "cortex_run_step": "Advance a tracked run by one superstep (one legal transition).",
    "cortex_run_state": "Inspect a tracked run's current state/history.",
    "cortex_phase_state": "Inspect the durable phase/checkpoint wrapper for a run (phase, lease, resume_key).",
    "cortex_phase_heartbeat": "Extend the active phase lease and optionally attach small partial outputs/checkpoints.",
    "cortex_phase_checkpoint": "Persist resume-safe progress for the active phase; advance only when the phase output is done.",
    "cortex_phase_resume": "Resume a task after timeout/max-turn/session loss using task_id or resume_key.",
    "cortex_report_empty_output": "Report blank/marker-only model output; returns retry, lane-switch, or escalation guidance.",
    "cortex_spawn_mission": "Fan out N INDEPENDENT workers under one supervisor, each with a DISJOINT "
                            "claim partition (atomic, all-or-nothing -- no overlap, no deadlock). Use "
                            "instead of hand-tracking a todo list of parallel background agents.",
    "cortex_mission_status": "Live completion view of a mission's workers (done/running, all_done). "
                             "THIS is what a dashboard polls for in-flight visibility; read-only, poll-safe.",
    "cortex_acquire_claims": "A worker claims its resource partition ATOMICALLY (no double-claims across "
                             "parallel workers) -- pass your worker task_id, the claims, and the seq fence.",
    "cortex_submit_mission_contract": "Submit a mission's INTAKE contract (seeking, acceptance criteria, "
                             "coverage spec, reducer policy) to advance INTAKE -> PARTITION.",
    "cortex_submit_partition": "Submit the mission's worker partition (who owns which units) to advance "
                             "PARTITION -> DISPATCH; gated for MECE coverage (no gaps, no overlaps).",
    "cortex_dispatch_mission": "Checkpoint a mission from DISPATCH -> MONITOR after cortex_spawn_mission "
                             "has created the actual workers.",
    "cortex_submit_merge": "Submit the supervisor's folded merged_artifact at MONITOR -> MERGE, once all "
                             "mission workers are DONE.",
    "cortex_tasks": "Multi-agent task ledger (action=list|claim|update). list: open/claimed tasks; claim: take one "
                    "EXCLUSIVELY (no double-claims across parallel agents); update: set a claimed task's status/result.",
    "cortex_ontology_query": "Query the living ontology (entities/relations) distilled over the corpus.",
    "cortex_key": "Owner/admin API-key management (action=issue|rotate|revoke|list). issue: mint a scoped key for a client "
                  "(raw key returned ONCE); rotate: revoke+re-mint same scope/tenant; revoke: kill a leaked/retired key; "
                  "list: metadata only, never the raw key or hash.",
    "cortex_playbook": "Self-learning browser navigation playbook (action=lookup|report; Cortex never runs a browser). "
                       "lookup (read-only): stored per-site locator INTENT (not CSS) + pitfalls + verification oracle. "
                       "report (owner/admin): record what a browser-controlling agent learned (confidence/quarantine/"
                       "corroboration); credential-redacted before it's ever logged.",
    "cortex_dispatch_tier": "LOCAL-ONLY: run one completion against a configured judge/dispatch tier "
                            "(e.g. opencode-zen) without holding its API key yourself -- credentials stay in "
                            "this repo's .env. Refuses to run in served mode.",
}

RAG_FLOW = [
    "1. cortex_search the brain (BM25+vector) -- corpus-first, never guess.",
    "2. If context is thin, cortex_scope_pack for a task-scoped pack (not whole docs).",
    "3. Still a gap? cortex_fetch_doc (fan out cheap models) or cortex_research(action='run') for external, cite-checked sources.",
    "4. cortex_contract to plan (phased, bounded).",
    "5. cortex_run_start creates a durable phase plan; checkpoint/heartbeat before the 8-minute lease expires.",
    "6. Do the work; a strong model writes tests, a cheaper one implements, a strong one reviews.",
    "7. If output is empty, cortex_report_empty_output decides retry/switch/escalate; never silently advance.",
    "8. cortex_write_log to close out -- always, even on failure.",
]

DISCIPLINES = [
    "Search the brain before the web -- this repo IS the corpus (plan docs, every prior review, the evidence base).",
    "Persist subagent output to the repo the moment it returns (reviewed/ for reviews, docs/research/ for research) -- "
    "never leave findings only in a chat or temp transcript.",
    "Write a closeout (cortex_write_log) at the end of every non-trivial task, even on failure.",
    "Deterministic checks decide pass/fail; an LLM never issues the verdict (anti-circularity).",
    "Match model tier to the task: cheap models fetch/implement, strong models plan/review/write tests.",
    "Long-running work must be phase-bounded: checkpoint/resume by resume_key instead of hoping one session survives.",
]


def coverage_gap(tool_names) -> list[str]:
    """Registered tools with no entry in TOOL_GUIDANCE -- the drift signal (should be empty)."""
    return sorted(n for n in tool_names if n not in TOOL_GUIDANCE)


def _reasoning_tiers() -> dict[str, Any]:
    """Per-stage recommended model tier, read from the capacity policy (best-effort)."""
    try:
        from cortex_core import capacity
        policy = capacity.load_policy()
        stages = policy.get("stages", {}) if isinstance(policy, dict) else {}
        out = {}
        for stage in stages:
            try:
                out[stage] = capacity.recommend(stage, policy)
            except Exception:  # noqa: BLE001 -- a bad stage entry must not break onboarding
                continue
        return {"note": "match model tier to the stage (cheap to fetch/implement, strong to plan/review)",
                "by_stage": out}
    except Exception:  # noqa: BLE001 -- capacity policy optional; degrade to guidance-only
        return {"note": "capacity policy unavailable; match tier to task manually", "by_stage": {}}


def build_onboarding(tool_names, pipeline_steps) -> dict[str, Any]:
    """Assemble the operating guide from the live tool list + pipeline + policy + taxonomy.
    `undocumented_tools` is surfaced honestly rather than hidden -- an empty list is the healthy state."""
    return {
        "summary": "How to operate this Cortex brain: search the corpus before the web; plan via a "
                   "contract; a strong model writes tests, a cheaper one implements, a strong one reviews; "
                   "always close out. Engage this for TASKS; skip it for ordinary conversation.",
        "start_here": ["cortex_register (get a session_id)", "cortex_status (orient)",
                       "cortex_search the brain BEFORE anything else"],
        "pipeline": list(pipeline_steps),
        "rag_flow": RAG_FLOW,
        "tools": {name: TOOL_GUIDANCE.get(name, "(undocumented -- see coverage_gap)")
                  for name in sorted(tool_names)},
        "reasoning_tiers": _reasoning_tiers(),
        "task_types": sorted(TASK_TYPES),
        "disciplines": DISCIPLINES,
        "undocumented_tools": coverage_gap(tool_names),
    }
