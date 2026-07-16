from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_core.doctor import doctor, main as doctor_main
from cortex_core.project_state import canonical_json
from cortex_core.project_state_cli import inspect_project_state, main
from cortex_core.project_state_store import ProjectStateStore, build_closeout_event_bundle


AS_OF = "2026-07-15T12:00:00+00:00"


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library/cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"name": "cli-project", "version": 1, "paths": {"workspace_fallback": ""}}),
        encoding="utf-8",
    )
    return ws


def _event(event_id: str = "event-1") -> dict:
    return build_closeout_event_bundle(
        event_id=event_id,
        project_id="cli-project",
        run_id="run-1",
        task_id="task-1",
        subject_id="task-1",
        subject_type="OPERATIONAL",
        scope={"kind": "TASK", "id": "task-1"},
        authority={
            "actor_id": "agent-1",
            "authority_class": "AGENT",
            "authority_role": "task-driver",
        },
        expected_prior_revision=0,
        observed_at=AS_OF,
        valid_from=AS_OF,
        appended_at=AS_OF,
        lifecycle_state="COMPLETED",
        source={"repository": "cli-project", "commit": "abc", "config_version": "1"},
        claims=["task completed"],
        next_actions=["review independently"],
    )


def _seed(ws: Path) -> ProjectStateStore:
    store = ProjectStateStore(ws)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)
    return store


def test_default_status_is_human_readable_clean_and_non_assuring(
    tmp_path: Path, capsys,
) -> None:
    ws = _workspace(tmp_path)
    _seed(ws)

    assert main(["--workspace", str(ws)]) == 0
    output = capsys.readouterr().out
    assert "project-state: CLEAN" in output
    assert "replay anchor: verified" in output
    assert "assurance: not minted" in output


def test_status_json_distinguishes_unavailable_dirty_and_invalid(
    tmp_path: Path, capsys,
) -> None:
    ws = _workspace(tmp_path)
    assert main(["status", "--workspace", str(ws), "--json"]) == 2
    unavailable = json.loads(capsys.readouterr().out)
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["assurance_minted"] is False

    store = _seed(ws)
    (store.paths.projections / "documents.json").unlink()
    assert main(["status", "--workspace", str(ws), "--json"]) == 1
    dirty = json.loads(capsys.readouterr().out)
    assert dirty["status"] == "DIRTY"
    assert dirty["severity"] == "WARN"

    store.recover_if_dirty(as_of=AS_OF)
    replacement = _event("replacement")
    store.paths.events.write_text(canonical_json(replacement) + "\n", encoding="utf-8")
    assert main(["status", "--workspace", str(ws), "--json"]) == 1
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["status"] == "INVALID"
    assert invalid["severity"] == "FAIL"
    assert invalid["replay_anchored"] is False


@pytest.mark.parametrize(
    ("command", "payload_key"),
    [("current", "current"), ("resume-pack", "resume_pack"), ("history", "events")],
)
def test_read_commands_return_replay_labeled_json(
    tmp_path: Path, capsys, command: str, payload_key: str,
) -> None:
    ws = _workspace(tmp_path)
    _seed(ws)

    assert main([command, "--workspace", str(ws), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "CLEAN"
    assert report["trusted"] is True
    assert report["assurance_minted"] is False
    assert report[payload_key]


def test_rebuild_and_recover_require_explicit_time_and_reject_rewind(
    tmp_path: Path, capsys,
) -> None:
    ws = _workspace(tmp_path)
    store = _seed(ws)
    with pytest.raises(SystemExit) as caught:
        main(["rebuild", "--workspace", str(ws)])
    assert caught.value.code == 2
    capsys.readouterr()

    (store.paths.projections / "documents.json").unlink()
    assert main(["recover", "--workspace", str(ws), "--as-of", AS_OF, "--json"]) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["status"] == "CLEAN"
    assert recovered["operation"] == "recover"

    earlier = "2026-07-15T11:00:00+00:00"
    assert main(["rebuild", "--workspace", str(ws), "--as-of", earlier, "--json"]) == 1
    rewind = json.loads(capsys.readouterr().out)
    assert rewind["status"] == "INVALID"
    assert "precedes committed" in rewind["reason"]


def test_doctor_omits_uninitialized_state_and_passes_only_replay_clean(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    report = doctor(workspace=ws, json_output=True, include_git_hygiene=False)
    assert "project_state" not in report

    store = _seed(ws)
    clean = doctor(workspace=ws, json_output=True, include_git_hygiene=False)
    assert clean["project_state"]["status"] == "CLEAN"
    assert clean["project_state"]["level"] == "PASS"
    assert clean["project_state"]["ok"] is True

    (store.paths.projections / "agent-resume-pack.json").unlink()
    dirty = doctor(workspace=ws, json_output=True, include_git_hygiene=False)
    assert dirty["project_state"]["status"] == "DIRTY"
    assert dirty["project_state"]["level"] == "WARN"
    assert dirty["project_state"]["ok"] is False


def test_doctor_fails_replay_mismatch_and_time_inconsistency(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    store = _seed(ws)
    replacement = _event("replacement")
    store.paths.events.write_text(canonical_json(replacement) + "\n", encoding="utf-8")
    mismatch = doctor(workspace=ws, json_output=True, include_git_hygiene=False)
    assert mismatch["project_state"]["status"] == "INVALID"
    assert mismatch["project_state"]["level"] == "FAIL"

    store.paths.events.write_text(canonical_json(_event()) + "\n", encoding="utf-8")
    current = store.read_current()
    current["as_of"] = "not-a-time"
    store.paths.current.write_text(canonical_json(current), encoding="utf-8")
    inconsistent = doctor(workspace=ws, json_output=True, include_git_hygiene=False)
    assert inconsistent["project_state"]["status"] == "INVALID"
    assert any(
        "replay-anchored" in reason
        for reason in inconsistent["project_state"]["reasons"]
    )


def test_doctor_cli_returns_nonzero_for_present_dirty_state(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    ws = _workspace(tmp_path)
    store = _seed(ws)
    (store.paths.projections / "documents.json").unlink()
    monkeypatch.setenv("CORTEX_WORKSPACE", str(ws))

    assert doctor_main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["project_state"]["status"] == "DIRTY"


def test_inspection_flags_metadata_that_omits_required_generated_view(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    store = _seed(ws)
    metadata_path = store.paths.projections / "projection-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["output_sha256"].pop("documents.json")
    metadata_path.write_text(canonical_json(metadata), encoding="utf-8")

    report = inspect_project_state(ws)
    assert report["status"] == "DIRTY"
    assert "projection metadata omits generated view: documents.json" in report["reasons"]
