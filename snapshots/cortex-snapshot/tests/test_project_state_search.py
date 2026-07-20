from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cortex_core.project_state_projection import render_projection_bundle
from cortex_core.project_state_store import ProjectStateStore, build_closeout_event_bundle
from cortex_core.search import CortexSearchIndex, CurrentStateProjectionError


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _publish_projection(workspace: Path) -> tuple[dict, dict]:
    store = ProjectStateStore(workspace)
    store.compare_and_append(
        _store_event("event-old", 0, "docs/history.md"),
        expected_revision=0,
        as_of="2026-07-15T12:00:00Z",
    )
    store.compare_and_append(
        _store_event("event-current", 1, "docs/active.md", supersedes=("event-old",)),
        expected_revision=1,
        as_of="2026-07-15T12:00:00Z",
    )
    opaque = build_closeout_event_bundle(
        event_id="event-opaque",
        project_id="project-search",
        run_id="run-search",
        task_id="task-search",
        subject_id="opaque-doc-record",
        subject_type="OPERATIONAL",
        scope={"kind": "TASK", "id": "task-search"},
        authority={
            "actor_id": "agent:driver",
            "authority_class": "AGENT",
            "authority_role": "opaque-document-reporter",
        },
        expected_prior_revision=2,
        observed_at="2026-07-15T10:02:00Z",
        valid_from="2026-07-15T10:02:00Z",
        appended_at="2026-07-15T10:03:00Z",
        lifecycle_state="ACTIVE",
        source={"repository": "repo", "commit": "opaque1", "config_version": "1"},
        affected_document_ids=("doc:opaque",),
    )
    store.compare_and_append(
        opaque,
        expected_revision=2,
        as_of="2026-07-15T12:00:00Z",
    )
    documents = json.loads(
        (store.paths.projections / "documents.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (store.paths.projections / "projection-metadata.json").read_text(encoding="utf-8")
    )
    return documents, metadata


def _corpus(workspace: Path) -> None:
    docs = workspace / "docs"
    docs.mkdir(parents=True)
    (docs / "ontology").mkdir()
    shutil.copy(
        Path(__file__).resolve().parent.parent / "docs/ontology/schema.yaml",
        docs / "ontology/schema.yaml",
    )
    (docs / "active.md").write_text("# Active\nactive-current-token", encoding="utf-8")
    (docs / "history.md").write_text("# Old\nsupersededonlyzxq", encoding="utf-8")
    (docs / "unmanaged.md").write_text("# Extra\nunmanaged-corpus-token", encoding="utf-8")


def test_trusted_projection_excludes_only_managed_history(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    index = CortexSearchIndex(tmp_path)

    discovered = {doc.path.name for doc in index.discover_documents()}

    assert discovered == {"active.md", "unmanaged.md"}
    status = index.status()["current_state_filter"]
    assert status["status"] == "ACTIVE_ONLY"
    assert status["trusted"] is True
    assert status["historical_document_count"] == 1


def test_include_history_is_explicit_at_discovery_rebuild_search_and_status(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    index = CortexSearchIndex(tmp_path)

    assert {doc.path.name for doc in index.discover_documents(include_history=True)} == {
        "active.md", "history.md", "unmanaged.md",
    }
    meta = index.rebuild(include_history=True)
    assert meta["current_state_filter"]["status"] == "HISTORY_INCLUDED"
    assert index.search("supersededonlyzxq", include_history=True)
    assert index.status(include_history=True)["current_state_filter"]["active_only"] is False


def test_projection_absence_preserves_legacy_discovery_and_reports_unavailable(tmp_path: Path) -> None:
    _corpus(tmp_path)
    index = CortexSearchIndex(tmp_path)

    assert {doc.path.name for doc in index.discover_documents()} == {
        "active.md", "history.md", "unmanaged.md",
    }
    status = index.status()["current_state_filter"]
    assert status["status"] == "UNAVAILABLE"
    assert status["active_only"] is False
    assert "legacy corpus discovery" in status["reason"]


def test_existing_but_incomplete_project_state_directory_fails_closed(tmp_path: Path) -> None:
    _corpus(tmp_path)
    (tmp_path / "project-state").mkdir()
    index = CortexSearchIndex(tmp_path)

    with pytest.raises(CurrentStateProjectionError, match="projection is incomplete"):
        index.discover_documents()
    assert {doc.path.name for doc in index.discover_documents(include_history=True)} == {
        "active.md", "history.md", "unmanaged.md",
    }
    status = index.status()
    assert status["retrieval_blocked"] is True
    assert status["current_state_filter"]["status"] == "INVALID"


def test_dirty_projection_is_not_consumed_and_failure_is_visible(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    _write_json(tmp_path / "project-state" / "projections-dirty.json", {"stage": "publish"})
    index = CortexSearchIndex(tmp_path)

    with pytest.raises(CurrentStateProjectionError, match="current-only retrieval is blocked"):
        index.discover_documents()
    assert "history.md" in {
        doc.path.name for doc in index.discover_documents(include_history=True)
    }
    with pytest.raises(CurrentStateProjectionError, match="current-only retrieval is blocked"):
        index.search("supersededonlyzxq")
    status = index.status()["current_state_filter"]
    assert status["status"] == "DIRTY"
    assert status["trusted"] is False
    assert status["active_only"] is False
    assert index.status()["retrieval_blocked"] is True


@pytest.mark.parametrize("corruption", ["input", "state", "revision", "reducer", "output"])
def test_hash_and_revision_mismatches_fail_visibly_without_claiming_active_only(
    tmp_path: Path, corruption: str,
) -> None:
    _corpus(tmp_path)
    documents, metadata = _publish_projection(tmp_path)
    root = tmp_path / "project-state"
    current = json.loads((root / "current.json").read_text(encoding="utf-8"))
    if corruption == "input":
        metadata["input_sha256"] = "0" * 64
        _write_json(root / "projections" / "projection-metadata.json", metadata)
    elif corruption == "state":
        current["state_sha256"] = "0" * 64
        _write_json(root / "current.json", current)
        metadata["input_sha256"] = hashlib.sha256(_canonical(current)).hexdigest()
        _write_json(root / "projections" / "projection-metadata.json", metadata)
    elif corruption == "revision":
        metadata["project_revision"] = 99
        _write_json(root / "projections" / "projection-metadata.json", metadata)
    elif corruption == "reducer":
        metadata["reducer_version"] = "other"
        metadata["reducer_revision"] = "other"
        _write_json(root / "projections" / "projection-metadata.json", metadata)
    else:
        documents["history_excluded_count"] = 99
        _write_json(root / "projections" / "documents.json", documents)

    index = CortexSearchIndex(tmp_path)
    status = index.status()["current_state_filter"]
    assert status["status"] == "INVALID"
    assert status["active_only"] is False
    with pytest.raises(CurrentStateProjectionError, match="current-only retrieval is blocked"):
        index.discover_documents()
    assert "history.md" in {
        doc.path.name for doc in index.discover_documents(include_history=True)
    }


def test_projection_requires_every_selected_entry_to_be_explicitly_active(tmp_path: Path) -> None:
    _corpus(tmp_path)
    documents, metadata = _publish_projection(tmp_path)
    root = tmp_path / "project-state" / "projections"
    documents["documents"][0]["selection_status"] = "HISTORY"
    documents["documents"][0]["current"] = False
    documents_raw = _canonical(documents)
    metadata["output_sha256"]["documents.json"] = hashlib.sha256(documents_raw).hexdigest()
    (root / "documents.json").write_bytes(documents_raw)
    _write_json(root / "projection-metadata.json", metadata)

    status = CortexSearchIndex(tmp_path).status()["current_state_filter"]
    assert status["status"] == "INVALID"
    assert "not explicitly ACTIVE/current" in status["reason"]


def test_query_time_barrier_prevents_optional_legs_reactivating_history(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    index = CortexSearchIndex(tmp_path)
    index.rebuild(include_history=True)

    assert index.search("supersededonlyzxq", include_history=True)
    assert not index.search(
        "supersededonlyzxq", include_history=False, use_ontology=True, use_vector=False,
    )
    assert index.search("active-current-token", include_history=False)


def test_history_mode_change_marks_the_index_stale(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    index = CortexSearchIndex(tmp_path)
    index.rebuild(include_history=True)

    assert index.needs_rebuild(include_history=True) is False
    assert index.needs_rebuild(include_history=False) is True


def _store_event(
    event_id: str,
    revision: int,
    document: str,
    *,
    supersedes: tuple[str, ...] = (),
    project_authority: bool = False,
) -> dict:
    return build_closeout_event_bundle(
        event_id=event_id,
        project_id="project-search",
        run_id="run-search",
        task_id="task-search",
        subject_id="normative-search-docs" if project_authority else "task-search-docs",
        subject_type="NORMATIVE" if project_authority else "OPERATIONAL",
        scope=(
            {"kind": "PROJECT", "id": "project-search"}
            if project_authority else {"kind": "TASK", "id": "task-search"}
        ),
        authority={
            "actor_id": "human:owner" if project_authority else "agent:driver",
            "authority_class": "HUMAN_OWNER" if project_authority else "AGENT",
            "authority_role": "project-owner" if project_authority else "task-driver",
        },
        expected_prior_revision=revision,
        observed_at="2026-07-15T10:00:00Z",
        valid_from="2026-07-15T10:00:00Z",
        appended_at="2026-07-15T10:01:00Z",
        lifecycle_state="ACTIVE",
        source={"repository": "repo", "commit": "abc123", "config_version": "1"},
        event_type="STATE_SUPERSEDED" if supersedes else "STATE_ASSERTED",
        affected_document_ids=(document,),
        supersedes=supersedes,
    )


def test_real_reducer_store_projection_and_search_exclude_superseded_document(
    tmp_path: Path,
) -> None:
    _corpus(tmp_path)
    verifier = lambda _event: True
    store = ProjectStateStore(tmp_path, authority_verifier=verifier)
    store.compare_and_append(
        _store_event("event-old", 0, "docs/history.md", project_authority=True),
        expected_revision=0,
        as_of="2026-07-15T12:00:00Z",
    )
    store.compare_and_append(
        _store_event(
            "event-current", 1, "docs/active.md",
            supersedes=("event-old",), project_authority=True,
        ),
        expected_revision=1,
        as_of="2026-07-15T12:00:00Z",
    )
    task_reference = build_closeout_event_bundle(
        event_id="event-task-closeout",
        project_id="project-search",
        run_id="run-search",
        task_id="task-search",
        subject_id="task-search",
        subject_type="OPERATIONAL",
        scope={"kind": "TASK", "id": "task-search"},
        authority={
            "actor_id": "agent:driver",
            "authority_class": "AGENT",
            "authority_role": "task-driver",
        },
        expected_prior_revision=2,
        observed_at="2026-07-15T10:02:00Z",
        valid_from="2026-07-15T10:02:00Z",
        appended_at="2026-07-15T10:03:00Z",
        lifecycle_state="COMPLETED",
        source={"repository": "repo", "commit": "def456", "config_version": "1"},
        affected_document_ids=("docs/history.md",),
    )
    store.compare_and_append(
        task_reference,
        expected_revision=2,
        as_of="2026-07-15T12:00:00Z",
    )

    current = store.read_current()
    by_id = {item["document_id"]: item for item in current["documents"]}
    assert by_id["docs/history.md"]["status"] == "SUPERSEDED"
    assert by_id["docs/active.md"]["status"] == "ACTIVE"
    projected = json.loads(
        (store.paths.projections / "documents.json").read_text(encoding="utf-8")
    )
    assert [item["document_id"] for item in projected["documents"]] == ["docs/active.md"]

    index = CortexSearchIndex(tmp_path, authority_verifier=verifier)
    assert {doc.path.name for doc in index.discover_documents()} == {
        "active.md", "unmanaged.md",
    }
    index.rebuild()
    assert index.search("active-current-token")
    assert index.search("unmanaged-corpus-token")
    assert not index.search("supersededonlyzxq")

    revoked = CortexSearchIndex(tmp_path)
    revoked_status = revoked.status()
    assert revoked_status["retrieval_blocked"] is True
    assert revoked_status["current_state_filter"]["status"] == "INVALID"


def test_search_refuses_same_revision_event_log_replacement(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    store = ProjectStateStore(tmp_path)
    events = store.read_events()
    events[0]["claims"] = ["coordinated replacement at the same event count"]
    store.paths.events.write_text(
        "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in events),
        encoding="utf-8",
    )

    index = CortexSearchIndex(tmp_path)
    status = index.status()
    assert status["retrieval_blocked"] is True
    assert status["current_state_filter"]["status"] == "INVALID"
    assert "immutable event history" in status["current_state_filter"]["reason"]
    with pytest.raises(CurrentStateProjectionError, match="current-only retrieval is blocked"):
        index.discover_documents()


def test_search_refuses_coordinated_current_metadata_and_projection_rewrite(
    tmp_path: Path,
) -> None:
    _corpus(tmp_path)
    _publish_projection(tmp_path)
    store = ProjectStateStore(tmp_path)
    current = store.read_current()
    current["subjects"][0]["claims"] = ["forged but internally self-consistent"]
    unsigned = dict(current)
    unsigned.pop("state_sha256", None)
    current["state_sha256"] = hashlib.sha256(_canonical(unsigned)).hexdigest()
    _write_json(store.paths.current, current)
    bundle = render_projection_bundle(current)
    for name, value in bundle.items():
        target = store.paths.projections / name
        if isinstance(value, str):
            target.write_text(value, encoding="utf-8")
        else:
            _write_json(target, value)

    index = CortexSearchIndex(tmp_path)
    status = index.status()
    assert status["retrieval_blocked"] is True
    assert "immutable event history" in status["current_state_filter"]["reason"]
    with pytest.raises(CurrentStateProjectionError, match="current-only retrieval is blocked"):
        index.search("active-current-token")
