from __future__ import annotations

import concurrent.futures
import json
import shutil
from pathlib import Path

import pytest

from cortex_core.project_state import (
    RevisionConflictError,
    canonical_json,
    canonical_sha256,
    reduce_events,
    validate_event,
)
from cortex_core.project_state_projection import render_projection_bundle
from cortex_core.project_state_store import (
    CloseoutBundleError,
    CorruptEventLogError,
    ProjectStateStore,
    ProjectionCommitError,
    TimeRewindError,
    build_closeout_event_bundle,
)


AS_OF = "2026-07-15T12:00:00+00:00"


@pytest.fixture(autouse=True)
def _workspace_shape(tmp_path: Path) -> None:
    (tmp_path / "library/cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text(
        '{"name":"project-1","paths":{"workspace_fallback":""}}',
        encoding="utf-8",
    )


def _event(
    event_id: str = "event-1",
    *,
    revision: int = 0,
    task_id: str = "task-1",
    claims: tuple[str, ...] = ("closeout recorded",),
) -> dict:
    return build_closeout_event_bundle(
        event_id=event_id,
        project_id="project-1",
        run_id="run-1",
        task_id=task_id,
        subject_id=task_id,
        subject_type="OPERATIONAL",
        scope={"kind": "TASK", "id": task_id},
        authority={
            "actor_id": "agent-1",
            "authority_class": "AGENT",
            "authority_role": "implementation-driver",
        },
        expected_prior_revision=revision,
        observed_at=AS_OF,
        valid_from=AS_OF,
        appended_at=AS_OF,
        lifecycle_state="COMPLETED",
        source={"repository": "repo", "commit": "abc123", "config_version": "v1"},
        claims=claims,
        affected_document_ids=("docs/HANDOFF.md",),
    )


def test_real_callbacks_append_reduce_render_and_preserve_owner_handoff(tmp_path: Path) -> None:
    owner_handoff = tmp_path / "HANDOFF.md"
    owner_handoff.write_text("owner-authored handoff\n", encoding="utf-8")
    store = ProjectStateStore(tmp_path)

    result = store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)

    assert result.revision == 1
    assert result.appended is True
    assert store.projection_status()["clean"] is True
    assert store.read_current()["reducer_version"] == "cortex.project_state.reducer/1"
    assert owner_handoff.read_text(encoding="utf-8") == "owner-authored handoff\n"
    assert (tmp_path / "project-state/projections/HANDOFF.md").is_file()
    assert result.projection_files == (
        "HANDOFF.md",
        "agent-resume-pack.json",
        "capability-status.json",
        "documents.json",
        "projection-metadata.json",
    )


def test_closeout_adapter_emits_exact_valid_v1_event_and_is_bounded(tmp_path: Path) -> None:
    event = _event()

    valid, problems = validate_event(event)
    assert valid, problems
    assert set(event) == {
        "schema_version", "event_id", "project_id", "run_id", "task_id",
        "subject_id", "subject_type", "scope", "event_type",
        "expected_prior_revision", "authority", "observed_at", "valid_from",
        "expires_at", "appended_at", "lifecycle_state", "claims", "blockers",
        "next_actions", "affected_document_ids", "affected_capability_ids",
        "evidence_refs", "supersedes", "invalidates", "source",
    }
    assert event["event_type"] == "STATE_ASSERTED"
    assert not (tmp_path / "HANDOFF.md").exists()

    with pytest.raises(CloseoutBundleError, match="item bound"):
        _event(claims=tuple(f"claim-{index}" for index in range(129)))


def test_exact_retry_is_idempotent_and_does_not_duplicate_jsonl(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    event = _event()
    store.compare_and_append(event, expected_revision=0, as_of=AS_OF)

    retry = store.compare_and_append(event, expected_revision=0, as_of=AS_OF)

    assert retry.idempotent is True
    assert retry.appended is False
    assert len(store.read_events()) == 1
    assert store.paths.events.read_text(encoding="utf-8").count("\n") == 1


def test_exclusive_lock_and_revision_allow_only_one_concurrent_append(tmp_path: Path) -> None:
    events = [_event("event-a"), _event("event-b", task_id="task-2")]

    def append(event: dict):
        try:
            result = ProjectStateStore(tmp_path).compare_and_append(
                event, expected_revision=0, as_of=AS_OF,
            )
            return "ok", result
        except Exception as exc:
            return "error", exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, events))

    # One append succeeds; the second event carries a stale internal revision
    # and must fail closed rather than being silently rebased.
    successes = [outcome for status, outcome in outcomes if status == "ok"]
    errors = [outcome for status, outcome in outcomes if status == "error"]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RevisionConflictError)


def test_projection_failure_commits_event_marks_dirty_and_recovers(tmp_path: Path) -> None:
    def failing_renderer(current_state, *, include_history=False):
        raise RuntimeError("renderer unavailable")

    broken = ProjectStateStore(tmp_path, renderer=failing_renderer)
    with pytest.raises(ProjectionCommitError) as caught:
        broken.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)

    assert caught.value.committed_revision == 1
    assert len(broken.read_events()) == 1
    assert broken.paths.dirty.is_file()

    repaired = ProjectStateStore(tmp_path)
    result = repaired.recover_if_dirty(as_of=AS_OF)
    assert result.rebuilt is True
    assert repaired.projection_status()["clean"] is True
    assert not repaired.paths.dirty.exists()


def test_missing_projection_is_detected_and_rebuilt(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)
    missing = store.paths.projections / "documents.json"
    missing.unlink()

    status = store.projection_status()
    assert status["clean"] is False
    assert "projection missing: documents.json" in status["reasons"]

    store.recover_if_dirty(as_of=AS_OF)
    assert missing.is_file()
    assert store.projection_status()["clean"] is True


def test_replay_rebuild_is_byte_stable_for_same_as_of(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)
    expected_current = store.paths.current.read_bytes()
    expected_outputs = {
        path.name: path.read_bytes()
        for path in store.paths.projections.iterdir()
        if path.is_file()
    }

    store.paths.current.unlink()
    shutil.rmtree(store.paths.projections)
    result = store.rebuild_projections(as_of=AS_OF)

    assert result.rebuilt is True
    assert store.paths.current.read_bytes() == expected_current
    assert {
        path.name: path.read_bytes()
        for path in store.paths.projections.iterdir()
        if path.is_file()
    } == expected_outputs


def test_corrupt_or_torn_event_log_fails_closed(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)
    store.paths.events.write_text(
        store.paths.events.read_text(encoding="utf-8").rstrip("\n"),
        encoding="utf-8",
    )

    with pytest.raises(CorruptEventLogError, match="unterminated"):
        store.replay(as_of=AS_OF)


def test_reused_event_id_with_different_content_fails_closed(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)

    with pytest.raises(RevisionConflictError, match="different content"):
        store.compare_and_append(
            _event(claims=("different",)), expected_revision=1, as_of=AS_OF,
        )


def test_status_replays_same_length_valid_log_replacement(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)
    replacement = _event("replacement-event", claims=("history was replaced",))
    store.paths.events.write_text(canonical_json(replacement) + "\n", encoding="utf-8")

    status = store.projection_status()

    assert status["clean"] is False
    assert "current event_log_sha256 differs from immutable event log" in status["reasons"]
    assert "current snapshot differs from immutable-history replay" in status["reasons"]


def test_status_rejects_coordinated_current_metadata_and_projection_rewrite(
    tmp_path: Path,
) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event(), expected_revision=0, as_of=AS_OF)
    tampered = store.read_current()
    tampered["subjects"][0]["claims"] = ["coordinated forged current state"]
    unsigned = dict(tampered)
    unsigned.pop("state_sha256", None)
    tampered["state_sha256"] = canonical_sha256(unsigned)
    store.paths.current.write_text(canonical_json(tampered), encoding="utf-8")
    bundle = render_projection_bundle(tampered)
    for name, value in bundle.items():
        data = value if isinstance(value, str) else canonical_json(value)
        (store.paths.projections / name).write_text(data, encoding="utf-8")

    status = store.projection_status()

    assert status["clean"] is False
    assert "current state_sha256 differs from immutable-history replay" in status["reasons"]
    assert "current snapshot differs from immutable-history replay" in status["reasons"]


def test_materialization_time_cannot_rewind_and_reactivate_expired_state(
    tmp_path: Path,
) -> None:
    store = ProjectStateStore(tmp_path)
    event = build_closeout_event_bundle(
        event_id="expiring-event",
        project_id="project-1",
        run_id="run-1",
        task_id="task-1",
        subject_id="task-1",
        subject_type="OPERATIONAL",
        scope={"kind": "TASK", "id": "task-1"},
        authority={
            "actor_id": "agent-1",
            "authority_class": "AGENT",
            "authority_role": "implementation-driver",
        },
        expected_prior_revision=0,
        observed_at="2026-07-15T10:00:00+00:00",
        valid_from="2026-07-15T10:00:00+00:00",
        expires_at="2026-07-15T11:00:00+00:00",
        appended_at="2026-07-15T10:00:00+00:00",
        lifecycle_state="ACTIVE",
        source={"repository": "repo", "commit": "abc123", "config_version": "v1"},
        claims=["temporary active claim"],
    )
    materialized_at = "2026-07-15T12:00:00+00:00"
    earlier = "2026-07-15T10:30:00+00:00"
    store.compare_and_append(event, expected_revision=0, as_of=materialized_at)
    current_before = store.paths.current.read_bytes()
    assert store.read_current()["subjects"][0]["lifecycle_state"] == "UNRESOLVED"

    with pytest.raises(TimeRewindError):
        store.compare_and_append(event, expected_revision=0, as_of=earlier)
    with pytest.raises(TimeRewindError):
        store.rebuild_projections(as_of=earlier)
    (store.paths.projections / "documents.json").unlink()
    with pytest.raises(TimeRewindError):
        store.recover_if_dirty(as_of=earlier)

    assert store.paths.current.read_bytes() == current_before
    assert store.read_current()["subjects"][0]["lifecycle_state"] == "UNRESOLVED"


def test_authority_verifier_is_recorded_and_revocation_invalidates_replay(
    tmp_path: Path,
) -> None:
    trusted = _event("verified-project-owner")
    trusted["subject_id"] = "project-policy"
    trusted["subject_type"] = "NORMATIVE"
    trusted["scope"] = {"kind": "PROJECT", "id": trusted["project_id"]}
    trusted["authority"] = {
        "actor_id": "human:owner",
        "authority_class": "HUMAN_OWNER",
        "authority_role": "project-owner",
    }
    trusted["lifecycle_state"] = "ACTIVE"

    accepting = ProjectStateStore(
        tmp_path, authority_verifier=lambda event: event["event_id"] == "verified-project-owner",
    )
    accepting.compare_and_append(trusted, expected_revision=0, as_of=AS_OF)
    current = accepting.read_current()
    assert current["project_status"] == "RESOLVED"
    assert current["verified_authority_event_ids"] == ["verified-project-owner"]

    revoked = ProjectStateStore(tmp_path)
    status = revoked.projection_status()
    assert status["clean"] is False
    assert "current snapshot differs from immutable-history replay" in status["reasons"]


def test_legacy_reducer_callback_without_authority_keyword_remains_compatible(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def legacy_reducer(events, *, as_of):
        calls.append(as_of)
        return reduce_events(events, as_of=as_of)

    store = ProjectStateStore(tmp_path, reducer=legacy_reducer)
    store.compare_and_append(_event("legacy-reducer"), expected_revision=0, as_of=AS_OF)

    assert calls == [AS_OF]
    assert store.projection_status()["clean"] is True


def test_ontology_receipt_is_replay_anchored_and_exposes_unresolved_skips(
    tmp_path: Path,
) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event("ontology-unresolved"), expected_revision=0, as_of=AS_OF)

    status = store.projection_status()

    assert status["clean"] is True
    assert store.paths.ontology_sync.is_file()
    receipt = status["ontology_sync_receipt"]
    assert receipt["project_id"] == store.read_current()["project_id"]
    assert receipt["revision"] == 1
    assert receipt["state_sha256"] == store.read_current()["state_sha256"]
    assert receipt["result"]["status"] == "APPLIED_WITH_UNRESOLVED"
    assert status["ontology_unresolved_skips"]
    assert status["notices"]
    assert "SOURCE_NOT_FOUND" in status["notices"][0]


def test_ontology_failure_keeps_dirty_and_recovery_finishes_idempotently(
    tmp_path: Path,
) -> None:
    def failing_sync(state, *, workspace):
        plan = {
            "project_id": state["project_id"],
            "revision": state["revision"],
            "reducer_version": state["reducer_version"],
            "event_log_sha256": state["event_log_sha256"],
            "state_sha256": state["state_sha256"],
            "unresolved_skips": [],
            "assurance_minted": False,
        }
        return {
            "plan": plan,
            "result": {
                "ok": False,
                "status": "FAILED",
                "failures": [{"code": "SIMULATED", "detail": "ontology unavailable"}],
                "unresolved_skips": [],
                "assurance_minted": False,
            },
        }

    broken = ProjectStateStore(tmp_path, ontology_synchronizer=failing_sync)
    with pytest.raises(ProjectionCommitError):
        broken.compare_and_append(_event("ontology-failure"), expected_revision=0, as_of=AS_OF)

    assert len(broken.read_events()) == 1
    assert broken.paths.dirty.is_file()
    assert not broken.paths.ontology_sync.exists()

    repaired = ProjectStateStore(tmp_path)
    result = repaired.recover_if_dirty(as_of=AS_OF)
    assert result.rebuilt is True
    assert repaired.projection_status()["clean"] is True
    receipt_before = repaired.paths.ontology_sync.read_bytes()
    repaired.rebuild_projections(as_of=AS_OF)
    assert repaired.projection_status()["clean"] is True
    assert repaired.paths.ontology_sync.read_bytes() != b""
    assert len(repaired.read_events()) == 1
    assert receipt_before != b""


def test_missing_or_tampered_ontology_receipt_fails_status_closed(tmp_path: Path) -> None:
    store = ProjectStateStore(tmp_path)
    store.compare_and_append(_event("ontology-receipt"), expected_revision=0, as_of=AS_OF)
    receipt = json.loads(store.paths.ontology_sync.read_text(encoding="utf-8"))

    store.paths.ontology_sync.unlink()
    missing = store.projection_status()
    assert missing["clean"] is False
    assert "ontology sync receipt missing" in missing["reasons"]

    store.recover_if_dirty(as_of=AS_OF)
    receipt = json.loads(store.paths.ontology_sync.read_text(encoding="utf-8"))
    receipt["result"]["unresolved_skips"][0]["detail"] = "tampered skip reason"
    store.paths.ontology_sync.write_text(canonical_json(receipt), encoding="utf-8")
    tampered = store.projection_status()
    assert tampered["clean"] is False
    assert any("ontology sync receipt invalid" in reason for reason in tampered["reasons"])
