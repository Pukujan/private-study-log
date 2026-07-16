from __future__ import annotations

from pathlib import Path

from cortex_core.phase_runtime import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_PHASE_SECONDS,
    checkpoint_phase,
    create_phase_plan,
    get_phase_state,
    heartbeat_phase,
    report_empty_output,
    resume_phase,
)


def test_create_phase_plan_defaults_to_bounded_resumeable_phases(tmp_path: Path) -> None:
    plan = create_phase_plan(tmp_path, "t_1", {"seeking": "ship feature"}, "build", "s1")

    assert plan["phase_seconds"] == DEFAULT_PHASE_SECONDS
    assert plan["heartbeat_seconds"] == DEFAULT_HEARTBEAT_SECONDS
    assert plan["resume_key"]
    assert [p["phase_id"] for p in plan["phases"]] == ["plan", "execute", "verify", "finalize"]
    assert plan["phase_id"] == "plan"


def test_checkpoint_and_resume_survive_new_call_context(tmp_path: Path) -> None:
    plan = create_phase_plan(tmp_path, "t_2", {"seeking": "long job"})
    checkpoint_phase(
        tmp_path,
        resume_key=plan["resume_key"],
        checkpoint_state={"done": ["research"], "next": "implement"},
        partial_outputs=[{"path": "notes.md"}],
    )

    resumed = resume_phase(tmp_path, resume_key=plan["resume_key"])

    assert resumed["ok"] is True
    assert resumed["phase_state"]["checkpoint_state"]["next"] == "implement"
    assert resumed["phase_state"]["partial_outputs"] == [{"path": "notes.md"}]


def test_heartbeat_extends_lease_and_keeps_phase_running(tmp_path: Path) -> None:
    plan = create_phase_plan(tmp_path, "t_3", {"seeking": "loop"})
    state = heartbeat_phase(tmp_path, task_id="t_3", phase_id=plan["phase_id"])

    assert state["status"] == "running"
    assert state["phase_status"] == "running"
    assert state["lease_until_epoch"] > 0


def test_empty_output_policy_retries_switches_then_escalates(tmp_path: Path) -> None:
    create_phase_plan(tmp_path, "t_4", {"seeking": "avoid blank turn"})

    first = report_empty_output(tmp_path, task_id="t_4", model_id="m", raw_output="")
    second = report_empty_output(tmp_path, task_id="t_4", model_id="m", raw_output="   ")
    third = report_empty_output(tmp_path, task_id="t_4", model_id="m", raw_output="<!-- SECTION:x:PENDING -->")

    assert first["action"] == "retry_tightened_prompt"
    assert second["action"] == "switch_backend_or_lane"
    assert third["action"] == "escalate"
    assert third["never_mark_complete"] is True
    assert get_phase_state(tmp_path, task_id="t_4")["status"] == "escalated"
