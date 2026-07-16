from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cortex_core.mcp import (
    cortex_phase_checkpoint,
    cortex_phase_resume,
    cortex_report_empty_output,
    cortex_run_start,
    cortex_run_state,
)


def _make_workspace(root: Path) -> Path:
    ws = root / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return ws


def test_run_start_returns_phase_plan_and_short_lease(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)

    env = asyncio.run(cortex_run_start({"seeking": "bounded task"}, workspace=str(ws)))

    assert env["phase_plan"]["phase_seconds"] == 480
    assert env["resume_key"] == env["phase_plan"]["resume_key"]
    assert env["phase_plan"]["lease_until_epoch"] - env["updated_at"] <= 480
    assert env["phase_policy"]["checkpoint_tool"] == "cortex_phase_checkpoint"


def test_phase_checkpoint_resumes_across_sessions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "resume me"}, session_id="s1", workspace=str(ws)))
    resume_key = env["resume_key"]

    checkpoint = asyncio.run(cortex_phase_checkpoint(
        resume_key=resume_key,
        checkpoint_state={"completed": ["plan"], "next": "execute"},
        partial_outputs=[{"artifact": "plan.md"}],
        session_id="s1",
        workspace=str(ws),
    ))
    resumed = asyncio.run(cortex_phase_resume(
        resume_key=resume_key,
        session_id="s2",
        workspace=str(ws),
    ))

    assert checkpoint["ok"] is True
    assert resumed["ok"] is True
    assert resumed["phase_state"]["checkpoint_state"]["next"] == "execute"
    assert resumed["phase_state"]["partial_outputs"] == [{"artifact": "plan.md"}]


def test_run_state_includes_phase_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "state includes phase"}, workspace=str(ws)))

    state = asyncio.run(cortex_run_state(env["task_id"], workspace=str(ws)))

    assert state["phase_state"]["resume_key"] == env["resume_key"]
    assert state["phase_state"]["phase_id"] == "plan"


def test_report_empty_output_escalates_and_does_not_complete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "blank output"}, workspace=str(ws)))

    first = asyncio.run(cortex_report_empty_output(
        task_id=env["task_id"], model_id="m", raw_output="", workspace=str(ws)
    ))
    second = asyncio.run(cortex_report_empty_output(
        task_id=env["task_id"], model_id="m", raw_output=" ", workspace=str(ws)
    ))
    third = asyncio.run(cortex_report_empty_output(
        task_id=env["task_id"], model_id="m", raw_output="{}", workspace=str(ws)
    ))

    assert first["action"] == "retry_tightened_prompt"
    assert second["action"] == "switch_backend_or_lane"
    assert third["action"] == "escalate"
    assert third["phase_state"]["status"] == "escalated"
    assert third["never_mark_complete"] is True
