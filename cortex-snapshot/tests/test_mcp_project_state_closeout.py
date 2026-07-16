from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

import cortex_core.mcp as mcp_mod
import cortex_core.project_state_store as store_mod
from cortex_core.mcp import (
    cortex_register,
    cortex_run_start,
    cortex_run_state,
    cortex_run_step,
    cortex_write_log,
)
from cortex_core.project_state_store import ProjectStateStore, build_closeout_event_bundle


def _workspace(root: Path) -> Path:
    ws = root / "workspace"
    (ws / "library/cortex-library").mkdir(parents=True)
    (ws / "audit/audit-log-1/agent").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({
            "name": "closeout-integration-project",
            "version": 7,
            "paths": {"workspace_fallback": ""},
        }),
        encoding="utf-8",
    )
    return ws


def _drive_done(ws: Path) -> tuple[str, dict]:
    registered = cortex_register(
        agent_id="driver-agent", model="test-model", role="builder", workspace=str(ws),
    )
    session_id = registered["session_id"]
    env = asyncio.run(cortex_run_start(
        {"seeking": "exercise closeout integration"},
        session_id=session_id,
        workspace=str(ws),
    ))
    task_id = env["task_id"]
    for _ in range(20):
        current = asyncio.run(cortex_run_state(
            task_id, session_id=session_id, workspace=str(ws),
        ))
        if current["state"] == "DONE":
            break
        asyncio.run(cortex_run_step(
            task_id,
            current["legal_tools"][0],
            current["seq"],
            payload={"evidence": [{"claim": "checked", "source": "test"}], "result": "done"},
            session_id=session_id,
            workspace=str(ws),
        ))
    else:
        raise AssertionError("state-machine run did not reach DONE")
    return session_id, dict(mcp_mod._sessions[session_id]["completed_run"])


@pytest.fixture(autouse=True)
def _isolated_mcp_state(monkeypatch):
    monkeypatch.setenv("CORTEX_FORCED_PIPELINE", "0")
    monkeypatch.setenv("CORTEX_MANDATORY_STATE_MACHINE", "0")
    monkeypatch.setenv("CORTEX_CONTRACT_GATE", "0")
    monkeypatch.setenv("CORTEX_WRITE_POLICY", "0")
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    with mcp_mod._sessions_lock:
        mcp_mod._sessions.clear()
    yield
    with mcp_mod._run_engines_lock:
        engines = list(mcp_mod._run_engines.values())
        mcp_mod._run_engines.clear()
    for engine in engines:
        engine.close()
    with mcp_mod._sessions_lock:
        mcp_mod._sessions.clear()


def _trusted_owner(event: dict) -> bool:
    return event.get("event_id") == "owner-project-authority"


def _seed_project_authority(ws: Path) -> None:
    instant = "2026-07-15T12:00:00+00:00"
    event = build_closeout_event_bundle(
        event_id="owner-project-authority",
        project_id="closeout-integration-project",
        run_id="owner-run",
        task_id="owner-task",
        subject_id="locked-project-outcome",
        subject_type="NORMATIVE",
        scope={"kind": "PROJECT", "id": "closeout-integration-project"},
        authority={
            "actor_id": "owner",
            "authority_class": "HUMAN_OWNER",
            "authority_role": "project-outcome-owner",
        },
        expected_prior_revision=0,
        observed_at=instant,
        valid_from=instant,
        appended_at=instant,
        lifecycle_state="ACTIVE",
        source={"repository": "closeout-integration-project", "commit": "owner", "config_version": "7"},
        claims=["The project-wide outcome remains owner-controlled."],
    )
    ProjectStateStore(
        ws, authority_verifier=_trusted_owner,
    ).compare_and_append(event, expected_revision=0, as_of=instant)


def test_done_closeout_uses_exact_run_ids_and_cannot_replace_project_authority(
    tmp_path: Path, monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    owner_handoff = "# Owner continuation\n\nDo not replace this with a task closeout.\n"
    (ws / "HANDOFF.md").write_text(owner_handoff, encoding="utf-8")
    _seed_project_authority(ws)
    real_store = ProjectStateStore

    def trusted_store(workspace, *args, **kwargs):
        kwargs.setdefault("authority_verifier", _trusted_owner)
        return real_store(workspace, *args, **kwargs)

    monkeypatch.setattr(store_mod, "ProjectStateStore", trusted_store)
    session_id, completed = _drive_done(ws)

    assert completed == {
        "task_id": completed["task_id"],
        "run_id": completed["run_id"],
        "track": "build",
        "seeking": "exercise closeout integration",
    }
    result = asyncio.run(cortex_write_log(
        task="Human-friendly implementation closeout label",
        result="The task-scoped implementation closeout was written.",
        status="completed",
        session_id=session_id,
        workspace=str(ws),
        handoff={
            "locations": ["src/feature.py", "docs/feature.md"],
            "continuation": "Run independent acceptance review.",
        },
    ))

    project_state = result["project_state"]
    assert project_state["status"] == "APPLIED"
    assert project_state["transaction_model"] == "RECOVERABLE_TWO_RECORD_RECONCILIATION"
    assert project_state["audit_committed"] is True
    assert project_state["assurance_minted"] is False
    assert project_state["task_id"] == completed["task_id"]
    assert project_state["run_id"] == completed["run_id"]
    assert project_state["track"] == completed["track"]
    assert project_state["scope"] == {"kind": "TASK", "id": completed["task_id"]}
    assert project_state["task_label_mismatch"] is True
    assert "server task/run ids" in project_state["task_label_warning"]

    store = ProjectStateStore(ws, authority_verifier=_trusted_owner)
    events = store.read_events()
    assert len(events) == 2
    closeout = events[1]
    assert closeout["task_id"] == completed["task_id"]
    assert closeout["run_id"] == completed["run_id"]
    assert closeout["scope"] == {"kind": "TASK", "id": completed["task_id"]}
    assert closeout["subject_type"] == "OPERATIONAL"
    assert closeout["authority"] == {
        "actor_id": "driver-agent",
        "authority_class": "AGENT",
        "authority_role": "self-reported-task-closeout",
    }
    assert closeout["supersedes"] == []
    assert closeout["invalidates"] == []
    assert closeout["affected_document_ids"] == ["src/feature.py", "docs/feature.md"]
    assert closeout["next_actions"] == ["Run independent acceptance review."]

    evidence = closeout["evidence_refs"]
    closeout_path = Path(result["path"])
    assert len(evidence) == 1
    assert evidence[0]["authority_class"] == "DOCUMENTARY"
    assert evidence[0]["independence_class"] == "SELF_REPORTED"
    assert evidence[0]["provenance_class"] == "CONTENT_ADDRESSED"
    assert evidence[0]["sha256"] == hashlib.sha256(closeout_path.read_bytes()).hexdigest()

    reduced = store.read_current()
    project_subject = next(
        subject for subject in reduced["subjects"]
        if subject["subject_id"] == "locked-project-outcome"
    )
    assert project_subject["scope"]["kind"] == "PROJECT"
    assert project_subject["lifecycle_state"] == "ACTIVE"
    assert project_subject["authority_owner"]["authority_class"] == "HUMAN_OWNER"
    assert (ws / "HANDOFF.md").read_text(encoding="utf-8") == owner_handoff


@pytest.mark.parametrize("mode", ["sessionless", "no_completed_run", "override"])
def test_non_run_bound_closeouts_remain_audit_only(
    tmp_path: Path, mode: str,
) -> None:
    ws = _workspace(tmp_path)
    kwargs = {"workspace": str(ws)}
    task = "audit-only-task"
    if mode == "no_completed_run":
        registered = cortex_register("unmatched-agent", "test-model", workspace=str(ws))
        kwargs["session_id"] = registered["session_id"]
    elif mode == "override":
        registered = cortex_register("override-agent", "test-model", workspace=str(ws))
        kwargs["session_id"] = registered["session_id"]
        kwargs["state_machine_override_reason"] = "external harness owns its own state"

    result = asyncio.run(cortex_write_log(task=task, result="recorded", **kwargs))

    assert Path(result["path"]).is_file()
    assert result["project_state"]["status"] == "NOT_APPLICABLE"
    assert result["project_state"]["assurance_minted"] is False
    assert not (ws / "project-state/events.jsonl").exists()


def test_starting_a_new_root_run_invalidates_the_prior_completed_run(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    session_id, completed = _drive_done(ws)
    assert mcp_mod._sessions[session_id]["completed_run"] == completed

    asyncio.run(cortex_run_start(
        {"seeking": "a different active task"},
        session_id=session_id,
        workspace=str(ws),
    ))

    assert "completed_run" not in mcp_mod._sessions[session_id]
    result = asyncio.run(cortex_write_log(
        task="late closeout for the prior task",
        result="audit record only",
        session_id=session_id,
        workspace=str(ws),
    ))
    assert Path(result["path"]).is_file()
    assert result["project_state"]["status"] == "NOT_APPLICABLE"
    assert not (ws / "project-state/events.jsonl").exists()


def test_projection_failure_surfaces_dirty_after_event_and_closeout_are_durable(
    tmp_path: Path, monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    session_id, completed = _drive_done(ws)
    real_store = store_mod.ProjectStateStore

    def fail_render(_state, *, include_history=False):
        raise RuntimeError("projection renderer unavailable")

    monkeypatch.setattr(
        store_mod,
        "ProjectStateStore",
        lambda workspace: real_store(workspace, renderer=fail_render),
    )
    result = asyncio.run(cortex_write_log(
        task="projection-failure closeout",
        result="audit closeout survived projection failure",
        session_id=session_id,
        workspace=str(ws),
        handoff={"locations": ["artifact.txt"], "continuation": "recover projections"},
    ))

    assert Path(result["path"]).is_file()
    project_state = result["project_state"]
    assert project_state["status"] == "DIRTY"
    assert project_state["transaction_model"] == "RECOVERABLE_TWO_RECORD_RECONCILIATION"
    assert project_state["audit_committed"] is True
    assert project_state["dirty"] is True
    assert project_state["event_committed"] is True
    assert project_state["assurance_minted"] is False
    assert len(real_store(ws).read_events()) == 1
    assert (ws / "project-state/projections-dirty.json").is_file()
