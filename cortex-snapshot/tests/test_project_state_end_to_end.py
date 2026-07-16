from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cortex_core import ontology
from cortex_core.doctor import doctor
from cortex_core.project_state_cli import inspect_project_state
from cortex_core.project_state_store import (
    ProjectStateStore,
    ProjectionCommitError,
    build_closeout_event_bundle,
)
from cortex_core.search import CortexSearchIndex, CurrentStateProjectionError


NOW = "2026-07-15T12:00:00+00:00"


def _event(event_id: str, revision: int, task_id: str) -> dict:
    return build_closeout_event_bundle(
        event_id=event_id,
        project_id="e2e-project",
        run_id=f"run-{task_id}",
        task_id=task_id,
        subject_id=task_id,
        subject_type="OPERATIONAL",
        scope={"kind": "TASK", "id": task_id},
        authority={
            "actor_id": "agent:driver",
            "authority_class": "AGENT",
            "authority_role": "task-closeout",
        },
        expected_prior_revision=revision,
        observed_at=NOW,
        valid_from=NOW,
        appended_at=NOW,
        lifecycle_state="COMPLETED",
        source={"repository": "e2e-project", "commit": "abc", "config_version": "1"},
        claims=[f"completed {task_id}"],
        next_actions=["independent review"],
        affected_document_ids=["docs/current.md"],
    )


def test_event_to_ontology_search_cli_doctor_and_dirty_recovery(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    (ws / "library/cortex-library").mkdir(parents=True)
    (ws / "docs/ontology").mkdir(parents=True)
    (ws / "docs/current.md").write_text("# Current\nactive-e2e-token", encoding="utf-8")
    (ws / "docs/unmanaged.md").write_text("# Unmanaged\nunmanaged-e2e-token", encoding="utf-8")
    shutil.copy(
        Path(__file__).resolve().parent.parent / "docs/ontology/schema.yaml",
        ws / "docs/ontology/schema.yaml",
    )
    (ws / "cortex.json").write_text(
        json.dumps({"name": "e2e-project", "version": 1, "paths": {"workspace_fallback": ""}}),
        encoding="utf-8",
    )

    store = ProjectStateStore(ws)
    store.compare_and_append(_event("closeout-1", 0, "task-1"), expected_revision=0, as_of=NOW)

    assert store.read_current()["revision"] == 1
    assert (store.paths.projections / "agent-resume-pack.json").is_file()
    assert (store.paths.projections / "capability-status.json").is_file()
    assert (store.paths.projections / "documents.json").is_file()
    assert store.paths.ontology_sync.is_file()
    entities = ontology.load_entities(ws)
    assert any("docs/current.md" in entity.source_paths for entity in entities.values())
    assert inspect_project_state(ws)["status"] == "CLEAN"
    assert doctor(ws, json_output=True, include_git_hygiene=False)["project_state"]["status"] == "CLEAN"
    index = CortexSearchIndex(ws)
    assert any(doc.path.name == "current.md" for doc in index.discover_documents())

    def failed_sync(state, *, workspace):
        return {
            "plan": {
                "project_id": state["project_id"], "revision": state["revision"],
                "reducer_version": state["reducer_version"],
                "event_log_sha256": state["event_log_sha256"],
                "state_sha256": state["state_sha256"], "unresolved_skips": [],
                "assurance_minted": False,
            },
            "result": {
                "ok": False, "status": "FAILED",
                "failures": [{"code": "SIMULATED"}], "unresolved_skips": [],
                "assurance_minted": False,
            },
        }

    broken = ProjectStateStore(ws, ontology_synchronizer=failed_sync)
    with pytest.raises(ProjectionCommitError):
        broken.compare_and_append(_event("closeout-2", 1, "task-2"), expected_revision=1, as_of=NOW)
    assert inspect_project_state(ws)["status"] in {"DIRTY", "INVALID"}
    with pytest.raises(CurrentStateProjectionError):
        CortexSearchIndex(ws).discover_documents()

    ProjectStateStore(ws).recover_if_dirty(as_of=NOW)
    assert inspect_project_state(ws)["status"] == "CLEAN"
    assert len(ProjectStateStore(ws).read_events()) == 2

    entity = next(
        item for item in ontology.load_entities(ws).values()
        if "docs/current.md" in item.source_paths
    )
    changed = ontology.upsert_entity(
        "doc", entity.name, status="deprecated", summary=entity.summary,
        aliases=list(entity.aliases), source_paths=list(entity.source_paths),
        attributes=dict(entity.attributes), author_model=entity.author_model, workspace=ws,
    )
    assert changed["ok"] is True
    drifted = inspect_project_state(ws)
    assert drifted["status"] in {"DIRTY", "INVALID"}
    assert any("ontology entity drift pending" in reason for reason in drifted["reasons"])
    with pytest.raises(CurrentStateProjectionError):
        CortexSearchIndex(ws).discover_documents()
