from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cortex_core import ontology
from cortex_core.project_state import canonical_sha256
from cortex_core.project_state_ontology import (
    apply_ontology_sync_plan,
    build_ontology_sync_plan,
    sync_project_state_ontology,
    verify_project_state_ontology,
)


REAL_SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "ontology" / "schema.yaml"


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = tmp_path / "workspace"
    (ws / "docs" / "ontology").mkdir(parents=True)
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "docs" / "current.md").write_text("# Current", encoding="utf-8")
    (ws / "docs" / "old.md").write_text("# Old", encoding="utf-8")
    (ws / "docs" / "newer.md").write_text("# Newer", encoding="utf-8")
    (ws / "cortex.json").write_text('{"paths":{"workspace_fallback":""}}', encoding="utf-8")
    shutil.copy(REAL_SCHEMA, ws / "docs" / "ontology" / "schema.yaml")
    return ws


def _state(documents: list[dict]) -> dict:
    payload = {
        "schema_version": 1,
        "reducer_version": "project-state/v1",
        "project_id": "cortex",
        "revision": 2,
        "as_of": "2026-07-15T12:00:00+00:00",
        "project_status": "RESOLVED",
        "event_log_sha256": "a" * 64,
        "documents": documents,
        "capabilities": [],
        "subjects": [],
        "history": [],
        "unresolved": [],
    }
    return {**payload, "state_sha256": canonical_sha256(payload)}


def _doc(path: str, status: str, event: str) -> dict:
    return {
        "document_id": path,
        "path": path,
        "status": status,
        "current": status == "ACTIVE",
        "source_event_ids": [event],
        "last_event_id": event,
        "replacement_event_ids": ["event-current"] if status == "SUPERSEDED" else [],
        "invalidation_reasons": ["replaced"] if status == "SUPERSEDED" else [],
    }


def _entity_log_count(workspace: Path) -> int:
    path = workspace / "docs" / "ontology" / "entities.jsonl"
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line])


def test_explicit_event_lineage_maps_new_document_supersedes_old(workspace: Path) -> None:
    state = _state([
        _doc("docs/current.md", "ACTIVE", "event-current"),
        _doc("docs/old.md", "SUPERSEDED", "event-old"),
    ])

    outcome = sync_project_state_ontology(state, workspace=workspace)

    assert outcome["result"]["ok"] is True
    entities = ontology.load_entities(workspace)
    by_path = {entity.source_paths[0]: entity for entity in entities.values()}
    assert by_path["docs/current.md"].status == "active"
    assert by_path["docs/old.md"].status == "superseded"
    relations = list(ontology.load_relations(workspace).values())
    assert len(relations) == 1
    assert relations[0].subject == by_path["docs/current.md"].entity_id
    assert relations[0].object == by_path["docs/old.md"].entity_id
    assert relations[0].predicate == "supersedes"
    assert outcome["plan"]["relations"][0]["replacement_event_id"] == "event-current"
    assert outcome["result"]["relations_asserted"] == 1


def test_missing_path_missing_file_and_opaque_id_are_unresolved_skips(workspace: Path) -> None:
    state = _state([
        {**_doc("docs/current.md", "ACTIVE", "one"), "path": None},
        _doc("docs/not-there.md", "ACTIVE", "two"),
        {**_doc("docs/current.md", "ACTIVE", "three"), "document_id": "doc:opaque"},
    ])

    plan = build_ontology_sync_plan(state, workspace=workspace)

    assert plan["operations"] == []
    assert {item["code"] for item in plan["unresolved_skips"]} == {
        "MISSING_PATH", "OPAQUE_DOCUMENT_ID", "SOURCE_NOT_FOUND",
    }
    result = apply_ontology_sync_plan(plan, workspace=workspace)
    assert result["status"] == "APPLIED_WITH_UNRESOLVED"
    assert ontology.load_entities(workspace) == {}


def test_existing_document_is_matched_only_by_exact_source_path(workspace: Path) -> None:
    created = ontology.upsert_entity(
        "doc", "Human title", summary="curated summary",
        source_paths=["docs/current.md"], workspace=workspace,
    )
    assert created["ok"]
    state = _state([_doc("docs/current.md", "EXPIRED", "expiry-event")])

    plan = build_ontology_sync_plan(state, workspace=workspace)

    assert len(plan["operations"]) == 1
    operation = plan["operations"][0]
    assert operation["entity_id"] == created["entity_id"]
    assert operation["name"] == "Human title"
    apply_ontology_sync_plan(plan, workspace=workspace)
    current = ontology.load_entities(workspace)[created["entity_id"]]
    assert current.status == "expired"
    assert current.summary == "curated summary"


def test_repeat_sync_is_a_true_append_free_noop(workspace: Path) -> None:
    state = _state([
        _doc("docs/current.md", "ACTIVE", "event-current"),
        _doc("docs/old.md", "SUPERSEDED", "event-old"),
    ])
    first = sync_project_state_ontology(state, workspace=workspace)
    lines_after_first = _entity_log_count(workspace)
    relation_lines_after_first = len(
        (workspace / "docs" / "ontology" / "relations.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    )

    second = sync_project_state_ontology(state, workspace=workspace)

    assert first["result"]["applied"]
    assert {item["action"] for item in second["plan"]["operations"]} == {"noop"}
    assert second["plan"]["relations"][0]["action"] == "noop"
    assert second["result"]["applied"] == []
    assert second["result"]["noops"]
    assert _entity_log_count(workspace) == lines_after_first
    assert len((workspace / "docs" / "ontology" / "relations.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()) == relation_lines_after_first


def test_sync_preserves_existing_provenance_and_never_widens_authority(workspace: Path) -> None:
    created = ontology.upsert_entity(
        "doc", "Owner document", summary="owner-authored", aliases=["owner-alias"],
        source_paths=["docs/current.md"], attributes={"authority": "human-owner"},
        author_model="external-curator", workspace=workspace,
    )
    state = _state([_doc("docs/current.md", "UNRESOLVED", "unresolved-event")])

    outcome = sync_project_state_ontology(state, workspace=workspace)

    entity = ontology.load_entities(workspace)[created["entity_id"]]
    assert entity.status == "unavailable"
    assert entity.summary == "owner-authored"
    assert entity.aliases == ["owner-alias"]
    assert entity.author_model == "external-curator"
    assert entity.attributes["authority"] == "human-owner"
    sync_meta = entity.attributes["project_state_sync"]
    assert sync_meta["assurance_minted"] is False
    assert sync_meta["runtime_certified"] is False
    assert outcome["result"]["assurance_minted"] is False
    assert ontology.load_relations(workspace) == {}


def test_multiple_exact_source_matches_are_reported_as_conflict(workspace: Path) -> None:
    for name in ("First", "Second"):
        result = ontology.upsert_entity(
            "doc", name, source_paths=["docs/current.md"], workspace=workspace,
        )
        assert result["ok"]
    before = _entity_log_count(workspace)

    plan = build_ontology_sync_plan(
        _state([_doc("docs/current.md", "ACTIVE", "event")]), workspace=workspace,
    )

    assert plan["operations"] == []
    assert plan["unresolved_skips"][0]["code"] == "ONTOLOGY_SOURCE_CONFLICT"
    apply_ontology_sync_plan(plan, workspace=workspace)
    assert _entity_log_count(workspace) == before


def test_duplicate_or_inconsistent_lifecycle_is_skipped_without_partial_apply(
    workspace: Path,
) -> None:
    duplicate = _doc("docs/current.md", "ACTIVE", "event")
    inconsistent = {**_doc("docs/old.md", "SUPERSEDED", "old"), "current": True}

    plan = build_ontology_sync_plan(
        _state([duplicate, dict(duplicate), inconsistent]), workspace=workspace,
    )

    assert plan["operations"] == []
    assert {item["code"] for item in plan["unresolved_skips"]} == {
        "DUPLICATE_DOCUMENT", "LIFECYCLE_CONFLICT", "AMBIGUOUS_REPLACEMENT_LINEAGE",
    }
    apply_ontology_sync_plan(plan, workspace=workspace)
    assert ontology.load_entities(workspace) == {}


def test_ambiguous_replacement_event_never_asserts_relation(workspace: Path) -> None:
    old = _doc("docs/old.md", "SUPERSEDED", "event-old")
    current = _doc("docs/current.md", "ACTIVE", "event-current")
    newer = _doc("docs/newer.md", "ACTIVE", "event-current")

    outcome = sync_project_state_ontology(_state([old, current, newer]), workspace=workspace)

    assert outcome["plan"]["relations"] == []
    assert any(
        item["code"] == "AMBIGUOUS_REPLACEMENT_LINEAGE"
        for item in outcome["plan"]["unresolved_skips"]
    )
    assert outcome["result"]["relations_asserted"] == 0
    assert ontology.load_relations(workspace) == {}


def test_read_only_verifier_detects_drift_then_reports_clean(workspace: Path) -> None:
    state = _state([
        _doc("docs/current.md", "ACTIVE", "event-current"),
        _doc("docs/old.md", "SUPERSEDED", "event-old"),
    ])

    before = verify_project_state_ontology(state, workspace=workspace)
    assert before["clean"] is False
    assert before["pending_entity_ids"] and before["pending_relation_ids"]
    assert ontology.load_entities(workspace) == {}

    sync_project_state_ontology(state, workspace=workspace)
    after = verify_project_state_ontology(state, workspace=workspace)
    assert after["clean"] is True
    assert after["reasons"] == []


def test_replay_anchor_is_required(workspace: Path) -> None:
    state = _state([_doc("docs/current.md", "ACTIVE", "event")])
    state["revision"] = 99
    with pytest.raises(ValueError, match="state_sha256"):
        build_ontology_sync_plan(state, workspace=workspace)
