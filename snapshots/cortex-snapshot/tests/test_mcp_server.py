"""Tests for the Phase 3.1 MCP server skeleton (cortex_core/mcp.py).

Five tools, each a thin wrapper over existing, separately-tested code:
cortex_register, cortex_status, cortex_search, cortex_fetch_doc,
cortex_write_log. These tests cover the wiring (argument/response shape,
workspace threading) -- they don't re-test search/fetch/audit correctness,
which already has dedicated suites.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

import cortex_core.mcp as mcp_mod
from cortex_core.mcp import (
    cortex_closeout,
    cortex_contract,
    cortex_doc,
    cortex_doc_uri,
    cortex_fetch_doc,
    cortex_preflight,
    cortex_register,
    cortex_register_source,
    cortex_search,
    cortex_status,
    cortex_write_log,
    mcp,
)
from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _seed_docs(workspace: Path) -> None:
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "widgets.md").write_text(
        "# Widgets\n\nThis document discusses widgets and gears.\n", encoding="utf-8"
    )


def test_all_tools_registered() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "cortex_register",
        "cortex_status",
        "cortex_search",
        "cortex_fetch_doc",
        "cortex_write_log",
        "cortex_contract",  # Phase 4.1
        "cortex_scope_pack",  # Phase 5.2
        "cortex_research",  # G5 (2026-07-14): consolidates deep_research/research_status/register_source
        "cortex_tasks",     # G5: consolidates tasks_list/tasks_claim/tasks_update (GAP-CORTEX-0016)
        "cortex_ontology_query",  # Phase 7 living ontology
        "cortex_fingerprint",  # GAP-CORTEX-0013 stale-context detection
        "cortex_run_start",   # GAP-CORTEX-0020 state machine
        "cortex_run_step",    # GAP-CORTEX-0020 state machine
        "cortex_run_state",   # GAP-CORTEX-0020 state machine
        "cortex_phase_state",       # durable phase runtime: state/lease/resume
        "cortex_phase_heartbeat",   # durable phase runtime: heartbeat
        "cortex_phase_checkpoint",  # durable phase runtime: checkpoint
        "cortex_phase_resume",      # durable phase runtime: resume
        "cortex_report_empty_output",  # durable phase runtime: blank-output retry/escalate
        "cortex_spawn_mission",    # mission layer: orchestrator-over-choreography (2026-07-07)
        "cortex_mission_status",   # mission layer: live completion view a dashboard polls
        "cortex_acquire_claims",   # mission layer: atomic per-worker partition claim
        "cortex_submit_mission_contract",  # mission-track: INTAKE -> PARTITION
        "cortex_submit_partition",         # mission-track: PARTITION -> DISPATCH
        "cortex_dispatch_mission",         # mission-track: DISPATCH -> MONITOR
        "cortex_submit_merge",             # mission-track: MONITOR -> MERGE
        "cortex_onboarding",  # server-served operating guide (harness self-description)
        "cortex_key",    # G5: consolidates issue/rotate/revoke/list_keys (H2b browser-extension auth)
        "cortex_playbook",  # G5: consolidates playbook_lookup/playbook_report (browser learning loop)
        "cortex_dispatch_tier",    # 2026-07-07: LOCAL-ONLY judge/dispatch-tier completion passthrough
    }


def test_g5_consolidated_tools_replace_the_twelve_family_members() -> None:
    """G5: the four action-dispatchers are registered and the twelve former
    always-loaded family tools are NOT (surface shrank 12 -> 4)."""
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"cortex_key", "cortex_tasks", "cortex_research", "cortex_playbook"} <= names
    retired = {
        "cortex_issue_key", "cortex_rotate_key", "cortex_revoke_key", "cortex_list_keys",
        "cortex_tasks_list", "cortex_tasks_claim", "cortex_tasks_update",
        "cortex_deep_research", "cortex_research_status", "cortex_register_source",
        "cortex_playbook_lookup", "cortex_playbook_report",
    }
    assert not (retired & names), f"retired family tools still registered: {retired & names}"


def test_g5_dispatchers_route_and_reject_unknown_actions() -> None:
    """Each dispatcher routes to a real op and returns a clean error on a bad action
    (without touching a workspace for the error path)."""
    for tool, bad in [(mcp_mod.cortex_key, "cortex_key"),
                      (mcp_mod.cortex_tasks, "cortex_tasks"),
                      (mcp_mod.cortex_research, "cortex_research"),
                      (mcp_mod.cortex_playbook, "cortex_playbook")]:
        out = asyncio.run(tool(action="nope"))
        assert out["error"] == "unknown_action" and out["valid_actions"], bad


def test_g5_cortex_key_dispatch_is_behavior_equivalent_to_the_original() -> None:
    """Routing changes only the tool NAME: cortex_key(action='list') returns exactly
    what the (now-unregistered) cortex_list_keys helper returns in the same context."""
    via_dispatch = asyncio.run(mcp_mod.cortex_key(action="list"))
    direct = mcp_mod.cortex_list_keys()
    assert via_dispatch == direct
    assert via_dispatch.get("error") != "unknown_action"  # it really routed


def test_register_returns_session_id_and_stores_declared_model() -> None:
    result = cortex_register(agent_id="test-agent", model="claude-sonnet-5", role="builder")
    assert "session_id" in result and result["session_id"]
    assert result["next"] == "cortex_status"
    stored = mcp_mod._sessions[result["session_id"]]
    assert stored == {
        "agent_id": "test-agent",
        "model": "claude-sonnet-5",
        "role": "builder",          # unprivileged self-claim -> accepted as authoritative role
        "claimed_role": "builder",  # what the caller claimed (recorded verbatim)
        "role_authenticated": False,  # no signed role credential -> unauthenticated (but usable)
        "calls": [],
        "is_admin": False,  # ownership gate (docs/CORTEX-ROUTES-AND-OWNERSHIP.md): no admin token -> not admin
        "scope": None,      # no api_key presented -> unscoped session
        "tenant_id": None,
        # GAP G6 per-tenant no-log: an unkeyed owner/CLI session is never suppressed (no_log False);
        # data_capture resolves to the documented default (silence is not consent) but is inert
        # without a tenant_id.
        "data_capture": "opt-out",
        "no_log": False,
    }


def test_status_on_fresh_workspace_hints_no_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = asyncio.run(cortex_status(workspace=str(workspace)))

    assert result["index_exists"] is False
    assert "no index" in result["next"]
    assert "doctor" in result and result["doctor"]["workspace"] == str(workspace)
    assert result["doctor"]["git_hygiene"] == {
        "skipped": True,
        "reason": "excluded from latency-bounded MCP status",
        "how_to_run": "cortex-doctor --json",
    }
    assert result["model_catalog"]["summary"]["known_lanes"] > 0
    assert result["model_catalog"]["roster"]
    assert all(row["availability"] == "UNPROBED"
               for row in result["model_catalog"]["roster"])


def test_dispatch_catalog_is_read_only_and_immediately_visible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = mcp_mod.cortex_dispatch_tier(action="catalog", workspace=str(workspace))

    assert result["ok"] is True
    assert result["catalog"]["schema"] == "cortex.model_catalog/1"
    assert result["catalog"]["summary"]["known_lanes"] > 0
    assert not (workspace / "model_availability.json").exists()


def test_status_does_not_launch_git_hygiene(tmp_path: Path, monkeypatch) -> None:
    """The MCP activation check must not inherit the CLI doctor's subprocess risk."""
    import importlib

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    def fail_if_called(_workspace):
        raise AssertionError("MCP status launched git hygiene")

    doctor_module = importlib.import_module("cortex_core.doctor")
    monkeypatch.setattr(doctor_module, "git_hygiene", fail_if_called)

    result = asyncio.run(cortex_status(workspace=str(workspace)))

    assert result["doctor"]["git_hygiene"]["skipped"] is True


def test_status_on_built_index_hints_search(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    CortexSearchIndex(workspace).rebuild()

    result = asyncio.run(cortex_status(workspace=str(workspace)))

    assert result["index_exists"] is True
    assert result["stale"] is False
    assert result["next"] == "cortex_search"


def test_search_returns_real_hits_and_next_hint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)

    result = asyncio.run(cortex_search(query="widgets", workspace=str(workspace)))

    assert result["hits"] == 1
    assert result["results"][0]["path"].endswith("widgets.md")
    assert "cortex_write_log" in result["next"]


def test_search_zero_hits_suggests_fetch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)

    result = asyncio.run(cortex_search(query="zzznonexistentzzz", workspace=str(workspace)))

    assert result["hits"] == 0
    assert "cortex_fetch_doc" in result["next"]


def test_search_does_not_block_event_loop_during_rebuild(tmp_path: Path, monkeypatch) -> None:
    """A slow rebuild must not stall a concurrent coroutine on the same loop
    -- the exact gate-3.1 pitfall (sync tool bodies run directly on FastMCP's
    event loop; verified by reading FuncMetadata.call_fn_with_arg_validation).
    Proven, not assumed: a background counter must keep incrementing while a
    deliberately slowed rebuild is in flight."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)

    real_rebuild = index.rebuild

    def slow_rebuild():
        import time

        time.sleep(0.3)
        return real_rebuild()

    monkeypatch.setattr(index, "rebuild", slow_rebuild)
    monkeypatch.setattr(index, "needs_rebuild", lambda: True)
    monkeypatch.setattr(mcp_mod, "CortexSearchIndex", lambda workspace=None: index)

    async def scenario():
        ticks = {"n": 0}

        async def ticker():
            while True:
                ticks["n"] += 1
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        await cortex_search(query="widgets", workspace=str(workspace))
        ticker_task.cancel()
        return ticks["n"]

    tick_count = asyncio.run(scenario())
    assert tick_count > 5, (
        f"event loop only advanced {tick_count} ticks during a 0.3s rebuild -- "
        "the rebuild is blocking the loop instead of running in a thread"
    )


def test_fetch_doc_wires_through_to_fetch_document(tmp_path: Path, monkeypatch) -> None:
    """Wiring test: cortex_fetch_doc must call fetch_document with the given
    url/name/workspace and shape its return path into the response -- SSRF
    guarding itself is fetch_document's own, separately-tested job."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    calls: list[tuple[str, str]] = []
    fake_path = workspace / "docs" / "cortex-1" / "fetched.md"

    def fake_fetch_document(url, name, workspace=None, backend=None):
        calls.append((url, name))
        return fake_path

    monkeypatch.setattr(mcp_mod, "fetch_document", fake_fetch_document)

    result = asyncio.run(
        cortex_fetch_doc(url="https://example.com/doc", name="doc", workspace=str(workspace))
    )

    assert calls == [("https://example.com/doc", "doc")]
    assert result["path"] == str(fake_path)
    assert "cortex_search" in result["next"]


def test_next_actions_escalates_after_repeated_reads_with_no_write(
    tmp_path: Path, monkeypatch
) -> None:
    """A session that only ever calls cortex_status/cortex_search, never
    cortex_fetch_doc/cortex_write_log, must get an escalated hint once the
    read-only streak crosses the threshold -- not the same generic default
    forever (gate 3.2: hints must respond to state, and guidance loops must
    be capped, not silently allowed to repeat)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)

    reg = cortex_register(agent_id="loop-agent", model="claude-sonnet-5", role="builder")
    session_id = reg["session_id"]

    r1 = asyncio.run(cortex_status(session_id=session_id, workspace=str(workspace)))
    assert "you've read" not in r1["next"]

    r2 = asyncio.run(cortex_search(query="widgets", session_id=session_id, workspace=str(workspace)))
    assert "you've read" not in r2["next"]

    r3 = asyncio.run(cortex_search(query="widgets", session_id=session_id, workspace=str(workspace)))
    assert "you've read" in r3["next"], (
        f"expected the 3rd consecutive read-only call to escalate, got: {r3['next']!r}"
    )


def test_next_actions_does_not_escalate_once_a_write_happens(tmp_path: Path, monkeypatch) -> None:
    """CONTROL: the same read-only streak must NOT escalate if a write
    (cortex_fetch_doc or cortex_write_log) already happened this session --
    the loop check looks at the whole session, not just the recent window."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # this control isolates the read-loop nudge, not the state-machine gate
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)

    reg = cortex_register(agent_id="writer-agent", model="claude-sonnet-5", role="builder")
    session_id = reg["session_id"]

    # An ALLOWED write (via the override escape hatch, since this session has no
    # contract) counts as progress; a *refused* write would not (review L2).
    asyncio.run(
        cortex_write_log(
            task="prior work", result="done", session_id=session_id, workspace=str(workspace),
            contract_override_reason="test: a legitimate prior write",
        )
    )
    for _ in range(3):
        r = asyncio.run(cortex_status(session_id=session_id, workspace=str(workspace)))
    assert "you've read" not in r["next"]


def test_session_call_history_is_trimmed_to_the_configured_window(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    monkeypatch.setenv("CORTEX_SESSION_CALL_HISTORY_MAX", "5")
    workspace = _make_workspace(tmp_path)

    reg = cortex_register(agent_id="trim-agent", model="claude-sonnet-5", role="builder")
    session_id = reg["session_id"]

    for _ in range(7):
        asyncio.run(cortex_status(session_id=session_id, workspace=str(workspace)))

    history = mcp_mod._sessions[session_id]["calls"]
    assert len(history) == 5
    assert history == ["cortex_status"] * 5


def test_calls_without_a_session_id_are_not_tracked_and_never_escalate(
    tmp_path: Path, monkeypatch
) -> None:
    """CONTROL: an unregistered/no-session_id caller must get the plain
    default hint every time -- next_actions must degrade gracefully, not
    require session tracking to function at all."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    CortexSearchIndex(workspace).rebuild()

    for _ in range(5):
        r = asyncio.run(cortex_status(workspace=str(workspace)))
    assert "you've read" not in r["next"]
    assert r["next"] == "cortex_search"


def test_tool_calls_are_logged_to_mcp_events_jsonl(tmp_path: Path, monkeypatch) -> None:
    """The Phase 3 exit criterion 'every server span carries declared_model
    + role' -- verify the event log actually carries them, not just that a
    file gets written."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)

    reg = cortex_register(
        agent_id="log-agent", model="claude-opus-4-8", role="reviewer", workspace=str(workspace)
    )
    session_id = reg["session_id"]
    asyncio.run(cortex_search(query="widgets", session_id=session_id, workspace=str(workspace)))

    events_path = workspace / "logs" / "mcp-events.jsonl"
    assert events_path.exists()
    entries = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    tools = [e["tool"] for e in entries]
    assert "cortex_register" in tools
    assert "cortex_search" in tools
    search_entry = next(e for e in entries if e["tool"] == "cortex_search")
    assert search_entry["session_id"] == session_id
    assert search_entry["declared_model"] == "claude-opus-4-8"
    assert search_entry["role"] == "reviewer"
    assert search_entry["query"] == "widgets"
    assert search_entry["hits"] == 1


def test_event_logging_failure_never_breaks_the_tool_call(tmp_path: Path, monkeypatch) -> None:
    """Fire-and-forget: a logging failure must not surface as a tool error,
    matching the existing search-telemetry convention in search.py."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)

    real_open = Path.open

    def broken_open(self, *args, **kwargs):
        if self.name == "mcp-events.jsonl":
            raise OSError("simulated disk failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)

    result = asyncio.run(cortex_search(query="widgets", workspace=str(workspace)))
    assert result["hits"] == 1


def test_write_log_creates_a_real_closeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = asyncio.run(
        cortex_write_log(
            task="mcp wiring test",
            result="verified",
            status="completed",
            workspace=str(workspace),
        )
    )

    path = Path(result["path"])
    assert path.exists()
    assert path.name.startswith("cortex-closeout__")
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["task"] == "mcp wiring test"
    assert data["status"] == "completed"


def test_resource_template_and_prompts_registered() -> None:
    templates = asyncio.run(mcp.list_resource_templates())
    assert [t.uriTemplate for t in templates] == ["cortex://doc/{encoded_path}"]
    prompts = asyncio.run(mcp.list_prompts())
    assert {p.name for p in prompts} == {"cortex_preflight", "cortex_closeout"}


def test_cortex_doc_reads_a_real_file_by_relative_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))

    content = asyncio.run(cortex_doc(encoded_path="docs/cortex-1/widgets.md"))

    assert "widgets and gears" in content


def test_cortex_doc_reads_by_absolute_path_from_search_results(tmp_path: Path, monkeypatch) -> None:
    """A client should be able to take a `path` straight out of
    cortex_search's results (which are absolute) and read it as a resource
    with no string surgery."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    absolute = str(workspace / "docs" / "cortex-1" / "widgets.md")

    content = asyncio.run(cortex_doc(encoded_path=absolute))

    assert "widgets and gears" in content


def test_cortex_doc_uri_round_trips_through_read_resource(tmp_path: Path, monkeypatch) -> None:
    """End-to-end through the actual MCP resource-read path (read_resource),
    not just calling the underlying function directly -- proves the URI
    template match + percent-decoding actually works together."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))

    uri = cortex_doc_uri("docs/cortex-1/widgets.md")
    assert uri == "cortex://doc/docs%2Fcortex-1%2Fwidgets.md"

    async def _read() -> str:
        contents = await mcp.read_resource(uri)
        chunks = list(contents)
        assert len(chunks) == 1
        return chunks[0].content

    content = asyncio.run(_read())
    assert "widgets and gears" in content


def test_cortex_doc_rejects_path_traversal_outside_workspace(tmp_path: Path, monkeypatch) -> None:
    """Security-relevant: a resolved path outside the workspace must be
    refused, not silently read -- proven with a real ../ escape and a real
    secret file outside the workspace, not just a code-reading assertion."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    secret = tmp_path / "secret.txt"
    secret.write_text("outside-the-workspace-secret", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the workspace"):
        asyncio.run(cortex_doc(encoded_path="../secret.txt"))


def test_cortex_doc_rejects_missing_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))

    with pytest.raises(ValueError, match="no such document"):
        asyncio.run(cortex_doc(encoded_path="docs/cortex-1/does-not-exist.md"))


def test_preflight_prompt_mentions_the_expected_tool_sequence() -> None:
    text = cortex_preflight(task="fix the widget bug")
    assert "cortex_register" in text
    assert "cortex_status" in text
    assert "cortex_search" in text
    assert "fix the widget bug" in text


def test_closeout_prompt_embeds_task_and_result() -> None:
    text = cortex_closeout(task="fix the widget bug", result="fixed, tests green")
    assert "cortex_write_log" in text
    assert "fix the widget bug" in text
    assert "fixed, tests green" in text


def test_cortex_contract_prefill_then_approve(tmp_path: Path, monkeypatch) -> None:
    """Phase 4.1: cortex_contract prefills a stub from the corpus in one call,
    then validates + approves once the substance fields are supplied."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    CortexSearchIndex(ws).rebuild()

    reg = cortex_register(agent_id="contract-agent", model="claude-sonnet-5", role="builder", workspace=str(ws))
    sid = reg["session_id"]

    stub = asyncio.run(cortex_contract(task="fix the widgets bug", session_id=sid, workspace=str(ws)))
    assert stub["mode"] == "prefill"
    assert stub["contract_id"]
    assert stub["task_type"] == "bugfix"

    done = asyncio.run(
        cortex_contract(
            task="fix the widgets bug",
            session_id=sid,
            planned_approach="adjust the gear ratio",
            acceptance_criteria=["widgets calibrate"],
            verification_steps=["run the widget test"],
            workspace=str(ws),
        )
    )
    assert done["mode"] == "submit"
    assert done["approved"] is True, done["errors"]
    assert done["contract_id"] == stub["contract_id"]  # same contract, now approved


def test_cortex_contract_submit_rejects_unresolvable_evidence(tmp_path: Path, monkeypatch) -> None:
    """A submitted contract citing evidence that doesn't resolve is refused."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    CortexSearchIndex(ws).rebuild()
    reg = cortex_register(agent_id="c2", model="claude-sonnet-5", role="builder", workspace=str(ws))

    done = asyncio.run(
        cortex_contract(
            task="do a thing",
            session_id=reg["session_id"],
            planned_approach="p",
            acceptance_criteria=["a"],
            verification_steps=["v"],
            evidence_refs=["docs/made-up.md"],
            workspace=str(ws),
        )
    )
    assert done["approved"] is False
    assert any("does not resolve" in e for e in done["errors"])


def test_write_gate_refuses_registered_session_without_contract(tmp_path: Path, monkeypatch) -> None:
    """Phase 4.2: a registered session with no approved contract is refused a
    write, with a message that says exactly how to comply."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_CONTRACT_GATE", "1")  # opt in to contract coercion
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")  # isolate the contract gate from the forced-docs gate
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # isolate the contract gate from the state-machine gate
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="g1", model="claude-sonnet-5", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_write_log(task="t", result="r", session_id=reg["session_id"], workspace=str(ws)))
    assert res.get("refused") is True
    assert "cortex_contract" in res["how_to_comply"]


def test_write_gate_allows_after_contract_approved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")  # isolate the contract gate from the forced-docs gate
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # ...and from the state-machine gate
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    CortexSearchIndex(ws).rebuild()
    reg = cortex_register(agent_id="g2", model="claude-sonnet-5", role="builder", workspace=str(ws))
    sid = reg["session_id"]
    # Realistic flow: prefill (fills evidence_refs from the corpus), then submit.
    asyncio.run(cortex_contract(task="fix widgets", session_id=sid, workspace=str(ws)))
    approved = asyncio.run(cortex_contract(
        task="fix widgets", session_id=sid, planned_approach="p",
        acceptance_criteria=["a"], verification_steps=["v"], workspace=str(ws),
    ))
    assert approved["approved"] is True, approved["errors"]  # evidence came from prefill
    res = asyncio.run(cortex_write_log(task="fix widgets", result="done", session_id=sid, workspace=str(ws)))
    assert res.get("refused") is not True
    assert "path" in res


def test_state_machine_run_drives_a_task_to_done(tmp_path: Path, monkeypatch) -> None:
    """GAP-0020: the state machine is exposed over MCP -- cortex_run_start/step/state walk a
    task through the pipeline to DONE, with the engine gating each phase."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_run_start, cortex_run_state, cortex_run_step

    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "do a thing"}, workspace=str(ws)))
    assert env["state"] == "SEARCH_BRAIN" and env["legal_tools"]
    tid = env["task_id"]
    for _ in range(20):
        cur = asyncio.run(cortex_run_state(tid, workspace=str(ws)))
        if cur["state"] in ("DONE", "ABANDONED"):
            break
        tool = cur["legal_tools"][0]  # the advance tool for this phase
        env = asyncio.run(cortex_run_step(tid, tool, cur["seq"],
                                          payload={"evidence": [{"claim": "c", "source": "s"}],
                                                   "result": "done"}, workspace=str(ws)))
    assert asyncio.run(cortex_run_state(tid, workspace=str(ws)))["state"] == "DONE"


def test_state_machine_step_refuses_illegal_tool_with_guidance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_run_start, cortex_run_step

    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "x"}, workspace=str(ws)))
    bad = asyncio.run(cortex_run_step(env["task_id"], "cortex_submit_patch", env["seq"],
                                      workspace=str(ws)))  # illegal in SEARCH_BRAIN
    assert bad["ok"] is False and bad["code"] == "ILLEGAL_IN_STATE"
    assert bad["legal_tools"] and "do_instead" in bad


def test_forced_pipeline_refuses_write_before_any_search(tmp_path: Path, monkeypatch) -> None:
    """Forced pipeline v1 (default): a registered session that has not consulted the brain
    (search/scope_pack) is refused a write, told to search first."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "1")
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # isolate the forced-docs gate from the state-machine gate
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="fp1", model="qwen-35b", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_write_log(task="t", result="r", session_id=reg["session_id"], workspace=str(ws)))
    assert res.get("refused") is True
    assert "consult the brain" in res["reason"]
    assert any("cortex_search" in s for s in res["pipeline"])


def test_forced_pipeline_allows_write_after_search(tmp_path: Path, monkeypatch) -> None:
    """After the session consults the brain, the forced-docs gate opens (the contract gate
    is separately disabled here to isolate the forced-docs behavior)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "1")
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # isolate the forced-docs gate from the state-machine gate
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    CortexSearchIndex(ws).rebuild()
    reg = cortex_register(agent_id="fp2", model="qwen-35b", role="builder", workspace=str(ws))
    sid = reg["session_id"]
    asyncio.run(cortex_search(query="widgets", session_id=sid, workspace=str(ws)))  # consult the brain
    # forced-docs gate now satisfied; bypass the separate contract gate to isolate this behavior
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=sid, workspace=str(ws),
        contract_override_reason="isolating the forced-docs gate",
    ))
    assert res.get("refused") is not True
    assert "path" in res


def test_forced_pipeline_off_by_env(tmp_path: Path, monkeypatch) -> None:
    """CORTEX_FORCED_PIPELINE=0 drops the hard gate (guidance may remain)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # this test isolates the forced-docs gate
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="fp3", model="qwen-35b", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(ws),
        contract_override_reason="bypass contract too",
    ))
    assert res.get("refused") is not True  # forced-docs gate absent when env=0


def test_substantive_contract_without_evidence_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """Review M2: a substantive task type must cite >=1 resolving evidence ref."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="m2", model="claude-sonnet-5", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_contract(
        task="build a feature", session_id=reg["session_id"], task_type="feature",
        planned_approach="p", acceptance_criteria=["a"], verification_steps=["v"],
        evidence_refs=[], workspace=str(ws),
    ))
    assert res["approved"] is False
    assert any("evidence_refs is empty" in e for e in res["errors"])


def test_failed_resubmit_revokes_prior_approval(tmp_path: Path, monkeypatch) -> None:
    """Review M1: a later FAILED submit must revoke a prior approval."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_CONTRACT_GATE", "1")  # opt in to contract coercion
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # isolate the contract-revocation path from the state-machine gate
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    CortexSearchIndex(ws).rebuild()
    reg = cortex_register(agent_id="m1", model="claude-sonnet-5", role="builder", workspace=str(ws))
    sid = reg["session_id"]
    asyncio.run(cortex_contract(task="fix widgets", session_id=sid, workspace=str(ws)))
    approved = asyncio.run(cortex_contract(
        task="fix widgets", session_id=sid, planned_approach="p",
        acceptance_criteria=["a"], verification_steps=["v"], workspace=str(ws),
    ))
    assert approved["approved"] is True
    bad = asyncio.run(cortex_contract(
        task="other", session_id=sid, planned_approach="p", acceptance_criteria=["a"],
        verification_steps=["v"], evidence_refs=["docs/nope.md"], workspace=str(ws),
    ))
    assert bad["approved"] is False
    refused = asyncio.run(cortex_write_log(task="x", result="y", session_id=sid, workspace=str(ws)))
    assert refused.get("refused") is True, "a failed re-submit must revoke prior approval"


def test_write_gate_override_reason_allows_and_is_escape_hatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")  # this test isolates the contract override escape hatch
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="g3", model="claude-sonnet-5", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(ws),
        contract_override_reason="emergency hotfix, contract to follow",
    ))
    assert res.get("refused") is not True
    assert "path" in res


def test_write_gate_does_not_apply_without_session_id(tmp_path: Path, monkeypatch) -> None:
    """Human/CLI context (no session_id) is not gated."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    res = asyncio.run(cortex_write_log(task="t", result="r", workspace=str(ws)))
    assert res.get("refused") is not True
    assert "path" in res


def _seed_ontology(ws: Path) -> None:
    """Give a workspace a real ontology schema + a tiny superseding graph so the
    query tool has something to resolve."""
    import shutil

    from cortex_core import ontology as ont

    real_schema = Path(__file__).resolve().parent.parent / "docs" / "ontology" / "schema.yaml"
    (ws / "docs" / "ontology").mkdir(parents=True, exist_ok=True)
    shutil.copy(real_schema, ws / "docs" / "ontology" / "schema.yaml")
    (ws / "docs" / "PHASE-GATES.md").write_text("# gates", encoding="utf-8")
    ont.upsert_entity("doc", "old", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    ont.upsert_entity("doc", "new", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    ont.supersede_entity("doc:old", "doc:new", reason="v2 replaces v1", workspace=ws)


def test_ontology_query_stats_and_current(tmp_path: Path, monkeypatch) -> None:
    """Wiring test: cortex_ontology_query dispatches to the ontology module for
    stats and the headline "which is current" resolution."""
    from cortex_core.mcp import cortex_ontology_query

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_ontology(ws)

    stats = asyncio.run(cortex_ontology_query(op="stats", workspace=str(ws)))
    assert stats["entities"] == 2
    assert stats["relations_by_predicate"].get("supersedes") == 1

    cur = asyncio.run(cortex_ontology_query(op="current", ref="old", workspace=str(ws)))
    assert cur["current"]["entity_id"] == "doc:new"
    assert cur["is_current"] is False

    got = asyncio.run(cortex_ontology_query(op="get", ref="doc:new", workspace=str(ws)))
    assert got["found"] is True and got["entity"]["status"] == "active"

    nb = asyncio.run(cortex_ontology_query(op="neighbors", ref="doc:new", workspace=str(ws)))
    assert any(n["predicate"] == "supersedes" for n in nb["neighbors"])


def test_ontology_query_unknown_op_and_missing_ref(tmp_path: Path, monkeypatch) -> None:
    from cortex_core.mcp import cortex_ontology_query

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_ontology(ws)

    bad = asyncio.run(cortex_ontology_query(op="bogus", workspace=str(ws)))
    assert "unknown op" in bad["error"]
    no_ref = asyncio.run(cortex_ontology_query(op="get", workspace=str(ws)))
    assert "requires a ref" in no_ref["error"]


# --- Stability audit regressions (docs/research/MCP-STABILITY-AUDIT-2026-07-07.md) -----------
# Each test below reproduces the EXACT crash the audit found (a real call, not a mock) and
# proves it now degrades to a clean refusal instead of an uncaught traceback.


def test_status_missing_workspace_returns_clean_refusal_not_crash(tmp_path: Path, monkeypatch) -> None:
    """Finding #1 (HIGH): an explicit workspace= that doesn't exist crashed with an uncaught
    FileNotFoundError from config.find_repo_root -- reproduced verbatim from the audit's own
    repro (`cortex_status(workspace=r"D:\\this\\does\\not\\exist_probe_xyz")`)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    missing = tmp_path / "this_does_not_exist_probe_xyz"

    result = asyncio.run(cortex_status(workspace=str(missing)))

    assert result["refused"] is True
    assert result["tool"] == "cortex_status"
    assert "workspace resolution failed" in result["reason"]
    assert "how_to_comply" in result


def test_write_log_stale_env_workspace_returns_clean_refusal_not_crash(tmp_path: Path, monkeypatch) -> None:
    """Finding #1 (HIGH), the ambient-env-var path: CORTEX_WORKSPACE pointing at a path that
    no longer exists (a renamed dir, an unmounted drive, a cleaned-up benchmark run-dir mid
    session) crashed EVERY workspace-touching tool, not just the ones passed an explicit bad
    path."""
    stale = tmp_path / "was_here_but_got_deleted"
    stale.mkdir()
    monkeypatch.setenv("CORTEX_WORKSPACE", str(stale))
    stale.rmdir()  # now CORTEX_WORKSPACE points at a path that no longer exists

    result = asyncio.run(cortex_write_log(task="t", result="r"))

    assert result["refused"] is True
    assert result["tool"] == "cortex_write_log"
    assert "workspace resolution failed" in result["reason"]


def test_write_log_rejects_non_string_task_with_clean_refusal(tmp_path: Path, monkeypatch) -> None:
    """Finding #2 (HIGH): task=123 crashed with `TypeError: 'int' object is not iterable` deep
    inside audit._slugify, before any file was written. Must now refuse cleanly and write
    nothing."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = asyncio.run(cortex_write_log(task=123, result="r", workspace=str(workspace)))

    assert result["refused"] is True
    assert "task" in result["reason"]
    assert not list((workspace / "audit" / "audit-log-1" / "agent").glob("*.md"))


def test_write_log_rejects_evidence_as_dict_instead_of_list(tmp_path: Path, monkeypatch) -> None:
    """Finding #2 (HIGH): evidence={"type": "file"} (a dict instead of a list of dicts)
    crashed with `AttributeError: 'str' object has no attribute 'get'` inside
    validate_evidence. Must now refuse cleanly."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = asyncio.run(
        cortex_write_log(
            task="t", result="r", workspace=str(workspace), evidence={"type": "file"}
        )
    )

    assert result["refused"] is True
    assert "evidence" in result["reason"]
    assert not list((workspace / "audit" / "audit-log-1" / "agent").glob("*.md"))


def test_write_log_rejects_handoff_as_string_instead_of_dict(tmp_path: Path, monkeypatch) -> None:
    """Finding #2 (HIGH): handoff="a string" (instead of the required {locations,
    continuation} dict) crashed with `AttributeError: 'str' object has no attribute 'get'`.
    Must now refuse cleanly."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = asyncio.run(
        cortex_write_log(task="t", result="r", workspace=str(workspace), handoff="a string")
    )

    assert result["refused"] is True
    assert "handoff" in result["reason"]
    assert not list((workspace / "audit" / "audit-log-1" / "agent").glob("*.md"))


def test_write_log_redacts_credential_shaped_strings(tmp_path: Path, monkeypatch) -> None:
    """Community tools survey 2026-07-07 §2.B (VEIL's redact-at-ingress pattern): a closeout's
    task/result/evidence fields must never carry a credential-shaped string verbatim into the
    permanent audit trail. Reuses the existing cortex_core.playbooks redaction utility (already
    used by cortex_playbook_report) rather than a new mechanism."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    secret = "Bearer sk-live-abcdefghijklmnop12345"

    result = asyncio.run(
        cortex_write_log(
            task="rotate the key",
            result=f"used {secret} to authenticate",
            workspace=str(workspace),
        )
    )

    assert result.get("redaction_notice")
    path = Path(result["path"])
    body = path.read_text(encoding="utf-8")
    assert secret not in body
    assert "REDACTED" in body


def test_run_start_malformed_intent_returns_clean_refusal(tmp_path: Path, monkeypatch) -> None:
    """Finding #5 (MEDIUM): cortex_run_start(intent=None) crashed with an uncaught
    `ValueError: intent must be a dict` -- inconsistent with the same tool's clean
    UNKNOWN_TRACK refusal for a bad `track`."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    result = asyncio.run(mcp_mod.cortex_run_start(intent=None, workspace=str(workspace)))

    assert result["ok"] is False
    assert result["code"] == "BAD_INTENT"


def test_run_step_malformed_seq_returns_clean_refusal(tmp_path: Path, monkeypatch) -> None:
    """Finding #5 (MEDIUM): cortex_run_step(seq="abc") crashed with an uncaught
    `ValueError: invalid literal for int() with base 10: 'abc'`, despite the function's own
    "forgiving parse" comment implying it should coerce or refuse gracefully."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    start = asyncio.run(
        mcp_mod.cortex_run_start(intent={"seeking": "test"}, workspace=str(workspace))
    )
    task_id = start["task_id"]

    result = asyncio.run(
        mcp_mod.cortex_run_step(
            task_id=task_id, tool="cortex_search", seq="abc", workspace=str(workspace)
        )
    )

    assert result["ok"] is False
    assert result["code"] == "BAD_STEP_INPUT"


def test_run_engine_concurrent_creation_is_race_free(tmp_path: Path, monkeypatch) -> None:
    """Finding #4 (MEDIUM/HIGH): `_run_engines` is a module-global dict with no lock;
    ops/qwen_benchmark_runner.py's own comments document a real crash (WindowsPath/dict
    TypeError, NoneType subscript) from two threads racing this exact
    check-then-create-then-store sequence. Mirrors the existing 20-thread StateEngine
    contention test (commit 419170cd): barrier-released concurrent calls for the SAME
    workspace must produce exactly one StateEngine instance, never raise, and never leak a
    second orphaned engine."""
    import threading

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    mcp_mod._run_engines.clear()

    n_threads = 20
    barrier = threading.Barrier(n_threads)
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            eng = mcp_mod._run_engine(str(workspace), None)
        except BaseException as exc:  # noqa: BLE001 -- capturing for the assertion below
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(eng)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent _run_engine calls raised: {errors}"
    assert len(results) == n_threads
    assert len({id(r) for r in results}) == 1  # exactly one StateEngine instance was built


# ---- cortex_register_source (agent-assisted source discovery, 2026-07-07) --------------------

def test_register_source_owner_mode_succeeds_and_persists(tmp_path: Path, monkeypatch) -> None:
    """Owner (local, default) mode: the local owner is implicitly admin -- no token needed."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core import authz
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    workspace = _make_workspace(tmp_path)
    (workspace / "research").mkdir(parents=True, exist_ok=True)

    result = cortex_register_source(
        url="https://arxiv.org/abs/1234.56789",
        title="A discovered paper",
        topics=["novel topic"],
        trust_tier="T2",
        discovered_via="WebSearch",
        workspace=str(workspace),
    )
    assert result["registered"] is True
    from cortex_core import research as R
    reg = R.load_registry(workspace)
    assert any(s.url == "https://arxiv.org/abs/1234.56789" for s in reg)


def test_register_source_served_mode_refuses_non_admin(tmp_path: Path, monkeypatch) -> None:
    """Served mode without admin auth: registering a source must be refused, matching
    the same owner/admin gating as cortex_issue_key -- a registry anyone could write
    unvetted URLs into is a real corpus-poisoning risk."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core import authz
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("s3cret"))
    workspace = _make_workspace(tmp_path)

    result = cortex_register_source(
        url="https://arxiv.org/abs/1234.56789", title="x", topics=["y"],
        workspace=str(workspace),
    )
    assert result.get("error") == "admin_required"
    from cortex_core import research as R
    assert R.load_registry(workspace) == []  # refused before ever touching the registry


def test_register_source_served_mode_admin_token_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core import authz
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("s3cret"))
    workspace = _make_workspace(tmp_path)
    (workspace / "research").mkdir(parents=True, exist_ok=True)

    result = cortex_register_source(
        url="https://arxiv.org/abs/1234.56789", title="x", topics=["y"],
        admin_token="s3cret", workspace=str(workspace),
    )
    assert result["registered"] is True


def test_register_source_rejects_ssrf_target_via_mcp(tmp_path: Path, monkeypatch) -> None:
    """The MCP tool must surface the SSRF-guard ValueError as a refusal, not crash."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core import authz
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    workspace = _make_workspace(tmp_path)
    (workspace / "research").mkdir(parents=True, exist_ok=True)

    result = cortex_register_source(
        url="http://127.0.0.1/admin", title="x", topics=[], workspace=str(workspace),
    )
    assert result["registered"] is False
    from cortex_core import research as R
    assert R.load_registry(workspace) == []


# --- Self-restart on stale code (2026-07-07) ---------------------------------------------------


def test_scan_py_mtime_returns_zero_for_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    mtime, path = mcp_mod._scan_py_mtime(missing)
    assert mtime == 0.0
    assert path is None


def test_scan_py_mtime_finds_newest_file_recursively(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    (root / "sub").mkdir(parents=True)
    old_file = root / "a.py"
    new_file = root / "sub" / "b.py"
    old_file.write_text("x = 1\n", encoding="utf-8")
    new_file.write_text("y = 2\n", encoding="utf-8")

    import os as _os

    old_time = time.time() - 100
    new_time = time.time()
    _os.utime(old_file, (old_time, old_time))
    _os.utime(new_file, (new_time, new_time))

    mtime, path = mcp_mod._scan_py_mtime(root)
    assert mtime == pytest.approx(new_time, abs=1.0)
    assert path == str(new_file)


def test_scan_py_mtime_ignores_non_python_files(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "notes.txt").write_text("later\n", encoding="utf-8")

    import os as _os

    now = time.time()
    _os.utime(root / "a.py", (now - 10, now - 10))
    _os.utime(root / "notes.txt", (now, now))

    mtime, path = mcp_mod._scan_py_mtime(root)
    assert path == str(root / "a.py")


def test_code_is_stale_false_when_nothing_changed(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    baseline_mtime, _ = mcp_mod._scan_py_mtime(root)

    stale, current_mtime, newest_path = mcp_mod._code_is_stale(baseline_mtime, root)
    assert stale is False
    assert current_mtime == baseline_mtime


def test_code_is_stale_true_after_a_file_is_touched_forward(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    baseline_mtime, _ = mcp_mod._scan_py_mtime(root)

    import os as _os

    future = time.time() + 1000
    _os.utime(target, (future, future))

    stale, current_mtime, newest_path = mcp_mod._code_is_stale(baseline_mtime, root)
    assert stale is True
    assert current_mtime > baseline_mtime
    assert newest_path == str(target)


def test_code_is_stale_true_for_a_newly_added_file(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    baseline_mtime, _ = mcp_mod._scan_py_mtime(root)

    time.sleep(0.05)
    new_file = root / "b.py"
    new_file.write_text("y = 2\n", encoding="utf-8")
    import os as _os

    now = time.time() + 5
    _os.utime(new_file, (now, now))

    stale, current_mtime, newest_path = mcp_mod._code_is_stale(baseline_mtime, root)
    assert stale is True
    assert newest_path == str(new_file)


def test_self_restart_on_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_SELF_RESTART_ON_STALE_CODE", raising=False)
    assert mcp_mod._self_restart_on() is True


@pytest.mark.parametrize("value", ["0", "false", "No", "OFF", ""])
def test_self_restart_toggle_disables_on_falsey_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("CORTEX_SELF_RESTART_ON_STALE_CODE", value)
    assert mcp_mod._self_restart_on() is False


@pytest.mark.parametrize("value", ["1", "true", "Yes", "on"])
def test_self_restart_toggle_stays_on_for_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("CORTEX_SELF_RESTART_ON_STALE_CODE", value)
    assert mcp_mod._self_restart_on() is True


def test_start_self_restart_watch_returns_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_SELF_RESTART_ON_STALE_CODE", "0")
    assert mcp_mod._start_self_restart_watch() is None


def test_self_restart_safe_now_true_when_locks_are_free() -> None:
    assert mcp_mod._self_restart_safe_now() is True


def test_self_restart_safe_now_false_while_sessions_lock_is_held() -> None:
    mcp_mod._sessions_lock.acquire()
    try:
        assert mcp_mod._self_restart_safe_now() is False
    finally:
        mcp_mod._sessions_lock.release()


def test_self_restart_safe_now_false_while_run_engines_lock_is_held() -> None:
    mcp_mod._run_engines_lock.acquire()
    try:
        assert mcp_mod._self_restart_safe_now() is False
    finally:
        mcp_mod._run_engines_lock.release()


def test_self_restart_watch_loop_stops_cleanly_without_reaching_stale_exit(
    tmp_path: Path, monkeypatch
) -> None:
    """Exercises the loop's normal (non-stale) iteration and clean shutdown via stop_event,
    without ever reaching the os._exit() branch -- that branch is deliberately NOT unit-tested
    (it would kill the test runner)."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(mcp_mod, "_SELF_RESTART_POLL_SECONDS", 0.01)
    stop_event = threading.Event()
    thread = threading.Thread(
        target=mcp_mod._self_restart_watch_loop, args=(root, stop_event), daemon=True
    )
    thread.start()
    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


# --- mission layer over MCP (orchestrator-over-choreography, 2026-07-07) -----------------------
# These exercise the MCP TOOL WRAPPERS (workspace resolution, refusal shapes, response envelopes),
# not just the underlying StateEngine methods (which have their own coverage in test_state_engine).


def _drive_worker_to_done(tid: str, ws: Path) -> None:
    """Walk one worker task through the build chart to terminal DONE via the MCP step tool."""
    from cortex_core.mcp import cortex_run_state, cortex_run_step

    for _ in range(20):
        cur = asyncio.run(cortex_run_state(tid, workspace=str(ws)))
        if cur["state"] in ("DONE", "ABANDONED"):
            return
        tool = cur["legal_tools"][0]
        asyncio.run(cortex_run_step(tid, tool, cur["seq"],
                                    payload={"evidence": [{"claim": "c", "source": "s"}],
                                             "result": "done", "patch": "x"}, workspace=str(ws)))


def test_spawn_mission_partitions_disjoint_workers_over_mcp(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_spawn_mission, cortex_run_state

    ws = _make_workspace(tmp_path)
    res = asyncio.run(cortex_spawn_mission(
        {"seeking": "build feature"},
        [{"intent": {"seeking": "auth"}, "claims": [{"kind": "path", "key": "src/auth/**"}]},
         {"intent": {"seeking": "api"}, "claims": [{"kind": "path", "key": "src/api/**"}]}],
        workspace=str(ws)))
    assert res["ok"] is True
    assert res["mission_id"] and len(res["worker_ids"]) == 2
    # each worker is a real, fresh pipeline task in the same workspace engine
    for wid in res["worker_ids"]:
        assert asyncio.run(cortex_run_state(wid, workspace=str(ws)))["state"] == "SEARCH_BRAIN"


def test_spawn_mission_overlapping_claims_refused_all_or_nothing_over_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_spawn_mission

    ws = _make_workspace(tmp_path)
    res = asyncio.run(cortex_spawn_mission(
        {"seeking": "m"},
        [{"intent": {}, "claims": [{"kind": "path", "key": "src/**"}]},
         {"intent": {}, "claims": [{"kind": "path", "key": "src/auth/x.py"}]}],  # inside src/**
        workspace=str(ws)))
    assert res["ok"] is False and res["code"] == "CLAIM_CONFLICT" and res["conflicts"]


def test_spawn_mission_empty_workers_returns_clean_refusal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_spawn_mission

    ws = _make_workspace(tmp_path)
    res = asyncio.run(cortex_spawn_mission({"seeking": "m"}, [], workspace=str(ws)))
    assert res["ok"] is False and res["code"] == "BAD_MISSION" and "how_to_comply" in res


def test_dispatch_mission_creates_build_children_under_mission_over_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    """S4a fix over MCP: the MISSION_TRACK mission stays on its own chart while
    cortex_dispatch_mission atomically creates >=3 build-track workers under THAT exact
    mission (parent_id), so cortex_mission_status sees them and DISPATCH->MONITOR fires."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import (cortex_run_start, cortex_run_state,
                                 cortex_submit_mission_contract, cortex_submit_partition,
                                 cortex_dispatch_mission, cortex_mission_status)

    ws = _make_workspace(tmp_path)
    # Mission on its OWN chart (track="mission"), with a 3-unit coverage spec.
    mid = asyncio.run(cortex_run_start(
        {"seeking": "organize corpus",
         "coverage_spec": {"required_units": ["re", "ingest", "index"], "max_workers": 3}},
        track="mission", workspace=str(ws)))["task_id"]
    seq = asyncio.run(cortex_run_state(mid, workspace=str(ws)))["seq"]
    r1 = asyncio.run(cortex_submit_mission_contract(mid, {}, seq, workspace=str(ws)))
    assert r1["state"] == "PARTITION"
    # PARTITION carries owns_units (gate-validated MECE) AND each worker's disjoint claims,
    # which the engine persists and DISPATCH materializes from (no re-supply at DISPATCH).
    r2 = asyncio.run(cortex_submit_partition(mid, [
        {"owns_units": ["re"], "intent": {"seeking": "re"}, "claims": [{"kind": "path", "key": "research/**"}]},
        {"owns_units": ["ingest"], "intent": {"seeking": "ingest"}, "claims": [{"kind": "path", "key": "library/**"}]},
        {"owns_units": ["index"], "intent": {"seeking": "index"}, "claims": [{"kind": "path", "key": "index/**"}]},
    ], r1["seq"], workspace=str(ws)))
    assert r2["state"] == "DISPATCH"
    # DISPATCH: engine atomically creates the build children from the persisted partition.
    r3 = asyncio.run(cortex_dispatch_mission(mid, r2["seq"], workspace=str(ws)))
    assert r3["ok"] and r3["state"] == "MONITOR"
    assert len(r3["worker_ids"]) == 3
    status = asyncio.run(cortex_mission_status(mid, workspace=str(ws)))
    assert status["n"] == 3 and status["all_done"] is False
    assert status["cohort_consistent"] is True and set(status["cohort"]) == set(r3["worker_ids"])
    for w in status["workers"]:
        st = asyncio.run(cortex_run_state(w["task_id"], workspace=str(ws)))
        assert st["state"] == "SEARCH_BRAIN" and st["track"] == "build"


def test_dispatch_mission_claim_conflict_does_not_advance_over_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    """A worker partition with overlapping claims must refuse (CLAIM_CONFLICT) and leave the
    mission at DISPATCH -- the topology can never race two workers into the same slice."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import (cortex_run_start, cortex_run_state,
                                 cortex_submit_mission_contract, cortex_submit_partition,
                                 cortex_dispatch_mission, cortex_mission_status)

    ws = _make_workspace(tmp_path)
    mid = asyncio.run(cortex_run_start(
        {"seeking": "m", "coverage_spec": {"required_units": ["x"], "max_workers": 3}},
        track="mission", workspace=str(ws)))["task_id"]
    seq = asyncio.run(cortex_run_state(mid, workspace=str(ws)))["seq"]
    r1 = asyncio.run(cortex_submit_mission_contract(mid, {}, seq, workspace=str(ws)))
    # A partition whose worker claims OVERLAP passes the owns_units MECE gate but must fail
    # the DISPATCH materialization CLOSED (no orphan children, mission stays at DISPATCH).
    r2 = asyncio.run(cortex_submit_partition(mid, [
        {"owns_units": ["x"], "claims": [{"kind": "path", "key": "src/**"}]},
        {"owns_units": ["y"], "claims": [{"kind": "path", "key": "src/a.py"}]},  # overlaps src/**
    ], r1["seq"], workspace=str(ws)))
    assert r2["state"] == "DISPATCH"
    bad = asyncio.run(cortex_dispatch_mission(mid, r2["seq"], workspace=str(ws)))
    # DISPATCH advance failed closed: gate did not pass, mission stayed at DISPATCH, no children.
    assert bad["state"] == "DISPATCH"
    assert not bad.get("gate", {}).get("pass", True)
    assert "CLAIM_CONFLICT" in str(bad.get("gate", {}))
    assert asyncio.run(cortex_mission_status(mid, workspace=str(ws)))["n"] == 0


def test_acquire_claims_prevents_double_claim_across_two_workers_over_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    """The core anti-double-claim guarantee, through the MCP wrapper: two workers, same partition,
    exactly one wins."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_run_start, cortex_run_state, cortex_acquire_claims

    ws = _make_workspace(tmp_path)
    t1 = asyncio.run(cortex_run_start({"seeking": "worker-1"}, workspace=str(ws)))["task_id"]
    t2 = asyncio.run(cortex_run_start({"seeking": "worker-2"}, workspace=str(ws)))["task_id"]
    seq1 = asyncio.run(cortex_run_state(t1, workspace=str(ws)))["seq"]
    seq2 = asyncio.run(cortex_run_state(t2, workspace=str(ws)))["seq"]

    first = asyncio.run(cortex_acquire_claims(
        t1, [{"kind": "path", "key": "src/auth/**"}], seq1, workspace=str(ws)))
    assert first["ok"] is True
    # worker 2 tries to claim the SAME partition -> refused, nothing granted
    clash = asyncio.run(cortex_acquire_claims(
        t2, [{"kind": "path", "key": "src/auth/**"}], seq2, workspace=str(ws)))
    assert clash["ok"] is False and clash["code"] == "CLAIM_CONFLICT" and clash["conflicts"]
    # a DISJOINT partition still succeeds for worker 2
    disjoint = asyncio.run(cortex_acquire_claims(
        t2, [{"kind": "path", "key": "src/api/**"}], seq2, workspace=str(ws)))
    assert disjoint["ok"] is True


def test_acquire_claims_missing_seq_returns_clean_refusal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_run_start, cortex_acquire_claims

    ws = _make_workspace(tmp_path)
    tid = asyncio.run(cortex_run_start({"seeking": "w"}, workspace=str(ws)))["task_id"]
    res = asyncio.run(cortex_acquire_claims(
        tid, [{"kind": "path", "key": "src/**"}], None, workspace=str(ws)))  # type: ignore[arg-type]
    assert res["ok"] is False and res["code"] == "BAD_CLAIMS_INPUT" and "how_to_comply" in res


def test_mission_status_reflects_partial_then_full_completion_over_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    """The live completion view a dashboard polls: partial (some workers DONE) then all_done."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_spawn_mission, cortex_mission_status

    ws = _make_workspace(tmp_path)
    res = asyncio.run(cortex_spawn_mission(
        {"seeking": "mission"},
        [{"intent": {"seeking": "a"}, "claims": [{"kind": "path", "key": "a/**"}]},
         {"intent": {"seeking": "b"}, "claims": [{"kind": "path", "key": "b/**"}]},
         {"intent": {"seeking": "c"}, "claims": [{"kind": "path", "key": "c/**"}]}],
        workspace=str(ws)))
    mid, (w1, w2, w3) = res["mission_id"], res["worker_ids"]

    # nothing done yet
    s0 = asyncio.run(cortex_mission_status(mid, workspace=str(ws)))
    assert s0["n"] == 3 and s0["done"] == 0 and s0["all_done"] is False

    # drive one worker to DONE -> partial completion is visible live
    _drive_worker_to_done(w1, ws)
    s1 = asyncio.run(cortex_mission_status(mid, workspace=str(ws)))
    assert s1["done"] == 1 and s1["all_done"] is False
    assert sum(1 for w in s1["workers"] if w["state"] == "DONE") == 1

    # drive the rest -> all_done flips true
    _drive_worker_to_done(w2, ws)
    _drive_worker_to_done(w3, ws)
    s2 = asyncio.run(cortex_mission_status(mid, workspace=str(ws)))
    assert s2["done"] == 3 and s2["all_done"] is True


def test_mission_status_unknown_mission_is_honest_empty_not_fabricated(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_mission_status

    ws = _make_workspace(tmp_path)
    s = asyncio.run(cortex_mission_status("t_does_not_exist", workspace=str(ws)))
    assert s["n"] == 0 and s["workers"] == [] and s["all_done"] is False
