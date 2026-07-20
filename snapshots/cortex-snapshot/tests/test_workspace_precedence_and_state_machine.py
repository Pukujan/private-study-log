"""Tests for the 2026-07-07 dual change (docs/research/
WORKSPACE-PRECEDENCE-AND-MANDATORY-STATE-MACHINE-2026-07-07.md):

Decision A -- WRITE-plane workspace-resolution precedence. An EXPLICIT ``workspace=`` override
must win over the ambient ``CORTEX_WORKSPACE`` pin in owner mode (the ``.mcp.json`` hardcoded-pin
bug), while a served-mode TENANT session stays pinned even WITH an explicit override
(GAP-CORTEX-0015 must not regress).

Decision B -- mandatory state machine. A registered session must drive a task through the server
chart (``cortex_run_start`` -> ``cortex_run_step``) to a terminal DONE before the free-standing
write tools are legal.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import cortex_core.mcp as mcp_mod
from cortex_core import authz
from cortex_core.mcp import (
    cortex_fetch_doc,
    cortex_register,
    cortex_run_start,
    cortex_run_state,
    cortex_run_step,
    cortex_write_log,
)


def _make_workspace(root: Path) -> Path:
    ws = root / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return ws


def _drive_to_done(sid: str, ws: Path) -> None:
    """Walk a build-track task through the chart to DONE (unlocks the state-machine gate)."""
    env = asyncio.run(cortex_run_start({"seeking": "x"}, session_id=sid, workspace=str(ws)))
    tid = env["task_id"]
    for _ in range(20):
        cur = asyncio.run(cortex_run_state(tid, session_id=sid, workspace=str(ws)))
        if cur["state"] == "DONE":
            return
        asyncio.run(cortex_run_step(
            tid, cur["legal_tools"][0], cur["seq"],
            payload={"evidence": [{"claim": "c", "source": "s"}], "result": "done"},
            session_id=sid, workspace=str(ws)))
    raise AssertionError("task never reached DONE")


# --- Decision A: workspace precedence ------------------------------------------------------

def test_explicit_workspace_override_wins_over_env_pin_in_owner_mode(tmp_path: Path, monkeypatch) -> None:
    """The confirmed bug: ``.mcp.json`` hardcodes ``CORTEX_WORKSPACE`` for every session, so a call
    that explicitly passes ``workspace=<override>`` was silently overridden back to the pin. In
    owner mode the explicit override must WIN -- the write lands where the caller asked."""
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    pinned = _make_workspace(tmp_path / "pinned")
    override = _make_workspace(tmp_path / "override")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(pinned))
    reg = cortex_register(agent_id="o1", model="m", role="builder", workspace=str(override))
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(override),
        contract_override_reason="bypass contract to isolate workspace routing"))
    assert res.get("refused") is not True
    assert str(override.resolve()) in res["path"]        # landed in the explicit override
    assert str(pinned.resolve()) not in res["path"]      # NOT the hardcoded env pin


def test_omitted_workspace_falls_back_to_env_pin(tmp_path: Path, monkeypatch) -> None:
    """Control: with NO workspace override, resolution still falls back env-first to
    ``CORTEX_WORKSPACE`` -- the tenant-pin behavior for the omitted case is preserved."""
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    pinned = _make_workspace(tmp_path / "pinned")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(pinned))
    reg = cortex_register(agent_id="o2", model="m", role="builder")
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"],
        contract_override_reason="bypass contract"))
    assert res.get("refused") is not True
    assert str(pinned.resolve()) in res["path"]


def test_served_tenant_cannot_escape_pin_even_with_explicit_override(tmp_path: Path, monkeypatch) -> None:
    """SECURITY (GAP-CORTEX-0015 must not regress): a served-mode, non-admin TENANT session is
    pinned to its own ``CORTEX_WORKSPACE``. Even if it passes an explicit foreign ``workspace=`` to
    try to escape, the write must land in the tenant pin, never the foreign path."""
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("adm"))
    tenant = _make_workspace(tmp_path / "tenant")
    brain = _make_workspace(tmp_path / "brain")
    foreign = _make_workspace(tmp_path / "foreign")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))   # dual-plane: tenant writes allowed
    reg = cortex_register(agent_id="tenant1", model="m")       # NO admin token -> tenant
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(foreign),
        contract_override_reason="bypass contract"))
    assert res.get("refused") is not True                      # dual-plane allows the write
    assert str(tenant.resolve()) in res["path"]                # pinned to the tenant own ws
    assert str(foreign.resolve()) not in res["path"]           # the override did NOT escape the pin


# --- Decision B: mandatory state machine ---------------------------------------------------

def test_state_machine_gate_refuses_write_without_a_completed_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "1")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")   # isolate: the state-machine gate is under test
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="sm1", model="qwen", role="builder", workspace=str(ws))
    # contract_override_reason bypasses the CONTRACT gate but NOT the state-machine gate (separate param)
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(ws),
        contract_override_reason="bypass contract"))
    assert res.get("refused") is True
    assert "state machine" in res["reason"]
    assert "cortex_run_start" in res["how_to_comply"]


def test_state_machine_gate_also_guards_fetch_doc(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "1")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="sm1b", model="qwen", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_fetch_doc(
        url="https://example.com/d", name="d", session_id=reg["session_id"], workspace=str(ws),
        contract_override_reason="bypass contract"))
    assert res.get("refused") is True
    assert "cortex_run_start" in res["how_to_comply"]


def test_state_machine_gate_opens_after_a_task_reaches_done(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "1")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="sm2", model="qwen", role="builder", workspace=str(ws))
    sid = reg["session_id"]
    _drive_to_done(sid, ws)
    completed = mcp_mod._sessions[sid].get("completed_run")
    assert completed["task_id"].startswith("t_")
    assert completed["run_id"].startswith("run_")
    assert completed["track"] == "build"
    assert completed["seeking"] == "x"
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=sid, workspace=str(ws),
        contract_override_reason="bypass contract"))
    assert res.get("refused") is not True
    assert "path" in res


def test_state_machine_gate_override_reason_is_a_logged_escape_hatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "1")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="sm3", model="qwen", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(ws),
        contract_override_reason="bypass contract",
        state_machine_override_reason="benchmark harness runs its own loop"))
    assert res.get("refused") is not True
    events = (ws / "logs" / "mcp-events.jsonl").read_text(encoding="utf-8")
    assert "state_machine_override" in events   # the bypass is logged, per the escape-hatch rule


def test_state_machine_gate_off_by_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    ws = _make_workspace(tmp_path)
    reg = cortex_register(agent_id="sm4", model="qwen", role="builder", workspace=str(ws))
    res = asyncio.run(cortex_write_log(
        task="t", result="r", session_id=reg["session_id"], workspace=str(ws),
        contract_override_reason="bypass contract"))
    assert res.get("refused") is not True   # gate disabled -> no state-machine requirement


def test_state_machine_gate_not_applied_to_sessionless_cli(tmp_path: Path, monkeypatch) -> None:
    """Same trust model as the other gates: a session-less (CLI) context is not gated."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "1")
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "1")
    ws = _make_workspace(tmp_path)
    res = asyncio.run(cortex_write_log(task="t", result="r", workspace=str(ws)))  # no session_id
    assert res.get("refused") is not True
    assert "path" in res


# --- REVIEW-stage scope-vs-intent gate (task05 / Discord-scrape failure mode) --------------

def test_review_scope_gate_fails_on_declared_mismatch() -> None:
    """A worker that itself reports the deliverable does not match the request is looped to rework
    at REVIEW -- a well-formed answer to the WRONG ask must not sail through."""
    from cortex_core.state_engine import review_scope_gate
    task = {"intent": {"seeking": "scrape other people's forum tools and posts"}}
    verdict = review_scope_gate("REVIEW", task,
                                {"result": "done", "scope_check": {"matches_request": False,
                                 "delivered": "scraped our own Cortex post ranking"}})
    assert verdict["pass"] is False
    assert "does not match" in verdict["reason"]


def test_review_scope_gate_fails_on_zero_overlap_target() -> None:
    """A gross target mismatch (delivered scope shares no content words with the request) fails."""
    from cortex_core.state_engine import review_scope_gate
    task = {"intent": {"seeking": "scrape competitor forum tools"}}
    verdict = review_scope_gate("REVIEW", task,
                                {"scope_check": {"delivered": "rewrote the billing invoice module"}})
    assert verdict["pass"] is False
    assert "NO overlap" in verdict["reason"]


def test_review_scope_gate_passes_matching_scope() -> None:
    from cortex_core.state_engine import review_scope_gate
    task = {"intent": {"seeking": "scrape competitor forum tools and posts"}}
    verdict = review_scope_gate("REVIEW", task,
                                {"scope_check": {"matches_request": True,
                                 "delivered": "scraped competitor forum tools with post links"}})
    assert verdict["pass"] is True


def test_review_scope_gate_warns_when_no_scope_check_but_does_not_break_the_chart() -> None:
    """A REVIEW with no scope_check still PASSES (so it can't break an existing chart drive) but
    surfaces a warning naming the failure mode -- the structural nudge to compare deliverable-vs-ask."""
    from cortex_core.state_engine import review_scope_gate
    task = {"intent": {"seeking": "scrape competitor forum tools"}}
    verdict = review_scope_gate("REVIEW", task, {"result": "done"})
    assert verdict["pass"] is True
    assert "scope_warning" in verdict
    assert "wrong ask" in verdict["scope_warning"].lower()


def test_review_scope_gate_is_noop_outside_review_phases() -> None:
    from cortex_core.state_engine import review_scope_gate
    task = {"intent": {"seeking": "anything"}}
    # IMPLEMENT is not a scope-check phase: a zero-overlap payload here must still pass.
    verdict = review_scope_gate("IMPLEMENT", task, {"scope_check": {"delivered": "zzz totally other"}})
    assert verdict["pass"] is True
