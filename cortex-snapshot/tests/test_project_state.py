"""Focused contract tests for the deterministic project-state event reducer."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from cortex_core.project_state import (
    EventValidationError,
    RevisionConflictError,
    append_event,
    canonical_json,
    canonical_sha256,
    reduce_events as reduce_events_core,
    validate_event,
)


NOW = "2026-07-15T12:00:00Z"
LATER = "2026-07-16T12:00:00Z"
HASH = "a" * 64


def evidence(
    evidence_id: str = "evidence-1", *, authority_class: str = "DETERMINISTIC_CHECK",
    expires_at: str | None = "2026-08-01T00:00:00Z",
) -> dict:
    return {
        "evidence_id": evidence_id,
        "uri": f"evidence://{evidence_id}",
        "sha256": HASH,
        "authority_class": authority_class,
        "independence_class": "INDEPENDENT",
        "provenance_class": "SIGNED" if authority_class == "SIGNED_RECEIPT" else "CONTENT_ADDRESSED",
        "observed_at": "2026-07-15T11:00:00Z",
        "expires_at": expires_at,
    }


def event(
    event_id: str,
    revision: int,
    *,
    subject_id: str = "project-goal",
    subject_type: str = "OPERATIONAL",
    scope_kind: str = "PROJECT",
    scope_id: str | None = None,
    authority_role: str = "project-owner",
    actor_id: str = "human:owner",
    authority_class: str = "HUMAN_OWNER",
    event_type: str = "STATE_ASSERTED",
    supersedes: list[str] | None = None,
    invalidates: list[str] | None = None,
    lifecycle_state: str = "ACTIVE",
    valid_from: str = "2026-07-15T10:00:00Z",
    expires_at: str | None = "2026-08-01T00:00:00Z",
    evidence_refs: list[dict] | None = None,
    affected_document_ids: list[str] | None = None,
    affected_capability_ids: list[str] | None = None,
) -> dict:
    project_id = "project-cortex"
    run_id = "run-1"
    task_id = "task-1"
    default_scope = {
        "PROJECT": project_id,
        "RUN": run_id,
        "TASK": task_id,
        "COMPONENT": "component:wrapper",
    }[scope_kind]
    return {
        "schema_version": 1,
        "event_id": event_id,
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "subject_id": subject_id,
        "subject_type": subject_type,
        "scope": {"kind": scope_kind, "id": scope_id or default_scope},
        "event_type": event_type,
        "expected_prior_revision": revision,
        "authority": {
            "actor_id": actor_id,
            "authority_class": authority_class,
            "authority_role": authority_role,
        },
        "observed_at": "2026-07-15T11:00:00Z",
        "valid_from": valid_from,
        "expires_at": expires_at,
        "appended_at": "2026-07-15T11:30:00Z",
        "lifecycle_state": lifecycle_state,
        "claims": [f"claim from {event_id}"],
        "blockers": [],
        "next_actions": [f"next action from {event_id}"],
        "affected_document_ids": (
            ["doc:handoff"] if affected_document_ids is None else affected_document_ids
        ),
        "affected_capability_ids": affected_capability_ids or [],
        "evidence_refs": evidence_refs if evidence_refs is not None else [evidence()],
        "supersedes": supersedes or [],
        "invalidates": invalidates or [],
        "source": {
            "repository": "stupidly-simple-cortex",
            "commit": "commit-abc",
            "config_version": "config-v1",
        },
    }


def subject(state: dict, subject_id: str = "project-goal", role: str = "project-owner") -> dict:
    return next(
        item for item in state["subjects"]
        if item["subject_id"] == subject_id and item["authority_role"] == role
    )


def reduce_events(events: list[dict], *, as_of: str) -> dict:
    """Existing reducer fixtures model authority already verified by the caller."""
    verified = [item["event_id"] for item in events if validate_event(item)[0]]
    return reduce_events_core(
        events, as_of=as_of, verified_authority_event_ids=verified,
    )


def test_public_schemas_exist_and_are_valid_json() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("project-state-event-v1.json", "project-state-current-v1.json"):
        schema = json.loads((root / "schemas" / name).read_text(encoding="utf-8"))
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["additionalProperties"] is False

    current_schema = json.loads(
        (root / "schemas" / "project-state-current-v1.json").read_text(encoding="utf-8")
    )
    reduced = reduce_events([
        event("event-schema", 0, affected_capability_ids=["capability:schema"]),
    ], as_of=NOW)
    assert set(reduced) == set(current_schema["properties"])
    assert set(current_schema["required"]) <= set(reduced)
    for definition, items in (
        (current_schema["$defs"]["document"], reduced["documents"]),
        (current_schema["$defs"]["capability"], reduced["capabilities"]),
    ):
        assert items
        for item in items:
            assert set(definition["required"]) <= set(item)
            assert set(item) <= set(definition["properties"])


def test_append_is_copy_on_write_revision_checked_and_retry_idempotent() -> None:
    original: list[dict] = []
    first = event("event-1", 0)
    log = append_event(original, first)
    assert original == [] and log == [first]
    assert append_event(log, deepcopy(first)) == log

    stale = event("event-2", 0, event_type="STATE_SUPERSEDED", supersedes=["event-1"])
    with pytest.raises(RevisionConflictError, match="current revision is 1"):
        append_event(log, stale)

    reused = deepcopy(first)
    reused["claims"] = ["different content"]
    with pytest.raises(RevisionConflictError, match="different content"):
        append_event(log, reused)


def test_event_validation_requires_stable_metadata_and_explicit_runtime_receipt() -> None:
    value = event(
        "runtime-1", 0, subject_type="RUNTIME", subject_id="wrapper-runtime",
        authority_role="runtime-observer", authority_class="RUNTIME",
        affected_capability_ids=["capability:wrapper-runtime"],
    )
    ok, problems = validate_event(value)
    assert not ok
    assert "RUNTIME and CAPABILITY subjects require signed-receipt evidence" in problems

    value["evidence_refs"] = [evidence(authority_class="SIGNED_RECEIPT")]
    assert validate_event(value) == (True, [])
    state = reduce_events([value], as_of=NOW)
    runtime = subject(state, subject_id="wrapper-runtime", role="runtime-observer")
    assert runtime["lifecycle_state"] == "UNRESOLVED"
    assert any(
        "cryptographic receipt verification is not integrated" in reason
        for reason in runtime["unresolved_reasons"]
    )
    assert runtime["evidence_refs"][0]["authority_class"] == "SIGNED_RECEIPT"
    assert state["capabilities"][0]["status"] == "UNRESOLVED"
    assert any(
        "cryptographically verified receipt" in reason
        for reason in state["capabilities"][0]["invalidation_reasons"]
    )

    value["scope"] = {"kind": "TASK", "id": value["project_id"]}
    ok, problems = validate_event(value)
    assert not ok and any("scope.id must equal task identifier" in problem for problem in problems)


def test_two_active_authorities_for_same_exact_key_reduce_unresolved_not_newest() -> None:
    first = event("event-1", 0)
    second = event("event-2", 1, actor_id="agent:newer", authority_class="AGENT")
    state = reduce_events([first, second], as_of=NOW)
    current = subject(state)
    assert state["project_status"] == "UNRESOLVED"
    assert current["lifecycle_state"] == "UNRESOLVED"
    assert current["authority_owner"]["actor_id"] == "cortex:project-state-reducer"
    assert current["candidate_event_ids"] == ["event-1", "event-2"]
    assert {row["status"] for row in state["history"]} == {"CONFLICT"}


def test_explicit_compatible_supersession_selects_one_current_authority() -> None:
    first = event("event-1", 0)
    second = event(
        "event-2", 1, event_type="STATE_SUPERSEDED", supersedes=["event-1"],
    )
    state = reduce_events([first, second], as_of=NOW)
    current = subject(state)
    assert state["project_status"] == "RESOLVED"
    assert current["last_accepted_event_id"] == "event-2"
    assert current["authority_owner"]["actor_id"] == "human:owner"
    assert current["claims"] == ["claim from event-2"]
    history = {row["event_id"]: row for row in state["history"]}
    assert history["event-1"]["status"] == "SUPERSEDED"
    assert history["event-1"]["replacement_event_id"] == "event-2"
    assert history["event-2"]["status"] == "ACTIVE"


def test_explicit_invalidation_is_auditable_and_excluded_from_current() -> None:
    first = event("event-1", 0)
    replacement = event(
        "event-2", 1, event_type="STATE_INVALIDATED", invalidates=["event-1"],
        lifecycle_state="BLOCKED",
    )
    state = reduce_events([first, replacement], as_of=NOW)
    current = subject(state)
    assert current["lifecycle_state"] == "BLOCKED"
    old = next(row for row in state["history"] if row["event_id"] == "event-1")
    assert old["status"] == "INVALIDATED" and old["replacement_event_id"] == "event-2"


def test_reducer_materializes_document_and_capability_lifecycle_without_guessing_paths() -> None:
    old = event(
        "event-old", 0,
        affected_document_ids=["docs/old.md", "doc:opaque"],
        affected_capability_ids=["capability:route"],
    )
    replacement = event(
        "event-new", 1, event_type="STATE_SUPERSEDED", supersedes=["event-old"],
        affected_document_ids=["docs/current.md"],
        affected_capability_ids=["capability:route-v2"],
    )

    state = reduce_events([old, replacement], as_of=NOW)
    documents = {item["document_id"]: item for item in state["documents"]}
    capabilities = {item["capability_id"]: item for item in state["capabilities"]}

    assert documents["docs/old.md"]["status"] == "SUPERSEDED"
    assert documents["docs/old.md"]["current"] is False
    assert documents["docs/old.md"]["path"] == "docs/old.md"
    assert documents["docs/current.md"]["status"] == "ACTIVE"
    assert documents["docs/current.md"]["current"] is True
    assert "path" not in documents["doc:opaque"]
    assert capabilities["capability:route"]["status"] == "SUPERSEDED"
    assert capabilities["capability:route-v2"]["status"] == "UNRESOLVED"


def test_explicit_expiry_makes_inventory_non_current_and_wall_clock_recency_is_ignored() -> None:
    expired = event(
        "event-expired", 0,
        expires_at="2026-07-15T11:45:00Z",
        affected_document_ids=["docs/expired.md"],
        affected_capability_ids=["capability:expired"],
    )
    state = reduce_events([expired], as_of=NOW)

    assert state["documents"][0]["status"] == "EXPIRED"
    assert state["documents"][0]["current"] is False
    assert state["capabilities"][0]["status"] == "EXPIRED"
    # Replaying at an earlier explicit validity instant, not file/event recency,
    # is the only thing that changes this lifecycle result.
    earlier = reduce_events([expired], as_of="2026-07-15T11:30:00Z")
    assert earlier["documents"][0]["status"] == "ACTIVE"


def test_narrower_scope_cannot_reactivate_project_superseded_document() -> None:
    old = event(
        "project-old", 0, affected_document_ids=["docs/old.md"],
    )
    replacement = event(
        "project-new", 1, event_type="STATE_SUPERSEDED", supersedes=["project-old"],
        affected_document_ids=["docs/new.md"],
    )
    task_closeout = event(
        "task-closeout", 2, subject_id="task-closeout", scope_kind="TASK",
        authority_role="task-driver", authority_class="AGENT", actor_id="agent:driver",
        affected_document_ids=["docs/old.md"],
    )

    state = reduce_events([old, replacement, task_closeout], as_of=NOW)
    documents = {item["document_id"]: item for item in state["documents"]}
    assert documents["docs/old.md"]["status"] == "SUPERSEDED"
    assert documents["docs/old.md"]["current"] is False
    assert documents["docs/new.md"]["status"] == "ACTIVE"


def test_document_inventory_retains_unsafe_ids_without_inventing_paths() -> None:
    asserted = event(
        "unsafe-document-ids", 0,
        affected_document_ids=[
            "../outside.md", "C:/outside.md", "docs\\windows.md", "opaque-id", "README.md",
        ],
    )
    state = reduce_events([asserted], as_of=NOW)
    documents = {item["document_id"]: item for item in state["documents"]}

    assert documents["README.md"]["path"] == "README.md"
    for document_id in ("../outside.md", "C:/outside.md", "docs\\windows.md", "opaque-id"):
        assert document_id in documents
        assert "path" not in documents[document_id]


def test_task_scope_cannot_supersede_project_scope() -> None:
    project_event = event("project-event", 0)
    task_event = event(
        "task-event", 1, scope_kind="TASK", event_type="STATE_SUPERSEDED",
        supersedes=["project-event"],
    )
    state = reduce_events([project_event, task_event], as_of=NOW)
    project_current = subject(state)
    task_current = next(item for item in state["subjects"] if item["scope"]["kind"] == "TASK")
    assert project_current["authority_owner"]["actor_id"] == "human:owner"
    assert project_current["lifecycle_state"] == "ACTIVE"
    assert task_current["lifecycle_state"] == "UNRESOLVED"
    assert any("incompatible type or scope" in reason for reason in task_current["unresolved_reasons"])


def test_lower_or_same_rank_different_actor_cannot_replace_human_owner() -> None:
    owner = event("owner", 0)
    lower = event(
        "agent", 1, event_type="STATE_SUPERSEDED", supersedes=["owner"],
        actor_id="agent:driver", authority_class="AGENT",
    )
    lower_state = reduce_events([owner, lower], as_of=NOW)
    assert subject(lower_state)["authority_owner"]["authority_class"] == "DETERMINISTIC_REDUCER"
    assert lower_state["project_status"] == "UNRESOLVED"
    lower_history = {item["event_id"]: item["status"] for item in lower_state["history"]}
    assert lower_history == {"owner": "CONFLICT", "agent": "CONFLICT"}

    peer = event(
        "peer", 1, event_type="STATE_SUPERSEDED", supersedes=["owner"],
        actor_id="human:other", authority_class="HUMAN_OWNER",
    )
    peer_state = reduce_events([owner, peer], as_of=NOW)
    assert subject(peer_state)["authority_owner"]["authority_class"] == "DETERMINISTIC_REDUCER"
    assert peer_state["project_status"] == "UNRESOLVED"

    agent_owned = event(
        "agent-owned", 0, actor_id="agent:driver", authority_class="AGENT",
    )
    elevated = event(
        "owner-elevated", 1, event_type="STATE_SUPERSEDED", supersedes=["agent-owned"],
    )
    elevated_state = reduce_events([agent_owned, elevated], as_of=NOW)
    assert subject(elevated_state)["authority_owner"]["actor_id"] == "human:owner"
    assert elevated_state["project_status"] == "RESOLVED"


def test_input_event_cannot_impersonate_deterministic_reducer() -> None:
    forged = event(
        "forged", 0, actor_id="cortex:project-state-reducer",
        authority_class="DETERMINISTIC_REDUCER",
    )
    valid, problems = validate_event(forged)
    assert valid is False
    assert "input events cannot claim DETERMINISTIC_REDUCER authority" in problems


def test_unverified_initial_owner_and_project_agent_claims_are_unresolved() -> None:
    forged_owner = event("forged-owner", 0)
    owner_state = reduce_events_core([forged_owner], as_of=NOW)
    assert owner_state["project_status"] == "UNRESOLVED"
    assert subject(owner_state)["lifecycle_state"] == "UNRESOLVED"
    assert owner_state["verified_authority_event_ids"] == []

    forged_project_agent = event(
        "forged-project-agent", 0,
        actor_id="agent:driver", authority_class="AGENT",
    )
    agent_state = reduce_events_core([forged_project_agent], as_of=NOW)
    assert agent_state["project_status"] == "UNRESOLVED"
    assert any(
        "only AGENT OPERATIONAL/DECISION self-reports at TASK or RUN scope" in reason
        for reason in subject(agent_state)["unresolved_reasons"]
    )


def test_default_task_agent_self_report_resolves_without_owner_authority() -> None:
    closeout = event(
        "task-closeout-default", 0,
        subject_id="task-closeout", scope_kind="TASK", authority_role="task-driver",
        actor_id="agent:driver", authority_class="AGENT",
    )
    state = reduce_events_core([closeout], as_of=NOW)
    assert state["project_status"] == "RESOLVED"
    assert subject(state, "task-closeout", "task-driver")["lifecycle_state"] == "ACTIVE"
    assert state["verified_authority_event_ids"] == []


def test_explicit_verified_human_project_event_resolves_and_is_hashed() -> None:
    owner = event("verified-owner", 0)
    state = reduce_events_core(
        [owner], as_of=NOW, verified_authority_event_ids=["verified-owner"],
    )
    assert state["project_status"] == "RESOLVED"
    assert state["verified_authority_event_ids"] == ["verified-owner"]
    assert state["verified_authority_event_ids_sha256"] == canonical_sha256([
        "verified-owner",
    ])


def test_unverified_same_actor_supersession_cannot_remove_verified_current() -> None:
    owner = event(
        "owner-current", 0, affected_document_ids=["docs/owner.md"],
    )
    unverified = event(
        "owner-unverified-successor", 1,
        event_type="STATE_SUPERSEDED", supersedes=["owner-current"],
        affected_document_ids=["docs/unverified.md"],
    )
    state = reduce_events_core(
        [owner, unverified], as_of=NOW,
        verified_authority_event_ids=["owner-current"],
    )

    assert state["project_status"] == "RESOLVED"
    assert subject(state)["last_accepted_event_id"] == "owner-current"
    history = {item["event_id"]: item for item in state["history"]}
    assert history["owner-current"]["status"] == "ACTIVE"
    assert history["owner-unverified-successor"]["status"] == "UNRESOLVED"
    assert "existing verified or safe current authority retained" in history[
        "owner-unverified-successor"
    ]["reason"]
    assert [item["document_id"] for item in state["documents"]] == ["docs/owner.md"]


def test_unverified_invalidation_cannot_remove_verified_current() -> None:
    owner = event("owner-current", 0)
    unverified = event(
        "owner-unverified-invalidation", 1,
        event_type="STATE_INVALIDATED", invalidates=["owner-current"],
        lifecycle_state="BLOCKED",
    )
    state = reduce_events_core(
        [owner, unverified], as_of=NOW,
        verified_authority_event_ids=["owner-current"],
    )

    assert state["project_status"] == "RESOLVED"
    assert subject(state)["last_accepted_event_id"] == "owner-current"
    history = {item["event_id"]: item["status"] for item in state["history"]}
    assert history == {
        "owner-current": "ACTIVE",
        "owner-unverified-invalidation": "UNRESOLVED",
    }


def test_unverified_parallel_candidate_cannot_conflict_verified_current() -> None:
    owner = event("owner-current", 0)
    parallel = event("owner-unverified-parallel", 1)
    state = reduce_events_core(
        [owner, parallel], as_of=NOW,
        verified_authority_event_ids=["owner-current"],
    )

    assert state["project_status"] == "RESOLVED"
    current = subject(state)
    assert current["lifecycle_state"] == "ACTIVE"
    assert current["candidate_event_ids"] == ["owner-current"]
    rejected = next(item for item in state["history"] if item["event_id"] == parallel["event_id"])
    assert rejected["status"] == "UNRESOLVED"

    safe = event(
        "safe-task-current", 0, subject_id="task-state", scope_kind="TASK",
        authority_role="task-driver", actor_id="agent:driver", authority_class="AGENT",
    )
    forged_owner = event(
        "forged-task-owner", 1, subject_id="task-state", scope_kind="TASK",
        authority_role="task-driver", actor_id="human:forged", authority_class="HUMAN_OWNER",
    )
    safe_state = reduce_events_core([safe, forged_owner], as_of=NOW)
    safe_current = subject(safe_state, "task-state", "task-driver")
    assert safe_state["project_status"] == "RESOLVED"
    assert safe_current["last_accepted_event_id"] == "safe-task-current"


def test_verified_same_actor_successor_can_replace_verified_current() -> None:
    owner = event("owner-current", 0)
    successor = event(
        "owner-verified-successor", 1,
        event_type="STATE_SUPERSEDED", supersedes=["owner-current"],
    )
    state = reduce_events_core(
        [owner, successor], as_of=NOW,
        verified_authority_event_ids=["owner-current", "owner-verified-successor"],
    )

    assert state["project_status"] == "RESOLVED"
    assert subject(state)["last_accepted_event_id"] == "owner-verified-successor"
    history = {item["event_id"]: item["status"] for item in state["history"]}
    assert history == {
        "owner-current": "SUPERSEDED",
        "owner-verified-successor": "ACTIVE",
    }


def test_different_authority_roles_are_distinct_exact_keys() -> None:
    owner = event("owner", 0, authority_role="project-owner")
    verifier = event(
        "verifier", 1, authority_role="independent-verifier",
        actor_id="evaluator:1", authority_class="EXTERNAL_EVALUATOR",
    )
    state = reduce_events([owner, verifier], as_of=NOW)
    assert state["project_status"] == "RESOLVED"
    assert len(state["subjects"]) == 2
    assert {item["authority_role"] for item in state["subjects"]} == {
        "project-owner", "independent-verifier",
    }


def test_expired_event_or_evidence_becomes_unresolved() -> None:
    expiring = event(
        "event-1", 0, expires_at="2026-07-15T11:45:00Z",
        evidence_refs=[evidence(expires_at="2026-07-15T11:50:00Z")],
    )
    state = reduce_events([expiring], as_of=NOW)
    current = subject(state)
    assert current["lifecycle_state"] == "UNRESOLVED"
    assert current["freshness_deadline"] == "2026-07-15T11:45:00+00:00"
    assert state["history"][0]["status"] == "EXPIRED"
    assert any("expired" in reason for reason in current["unresolved_reasons"])


def test_not_yet_valid_event_becomes_unresolved() -> None:
    future = event("event-1", 0, valid_from=LATER, expires_at="2026-08-01T00:00:00Z")
    state = reduce_events([future], as_of=NOW)
    assert subject(state)["lifecycle_state"] == "UNRESOLVED"
    assert state["history"][0]["status"] == "UNRESOLVED"


def test_invalid_unknown_event_is_retained_as_unresolved_history() -> None:
    unknown = event("event-1", 0)
    unknown["lifecycle_state"] = "MAYBE"
    state = reduce_events([unknown], as_of=NOW)
    assert state["project_status"] == "UNRESOLVED"
    assert state["revision"] == 0
    assert state["history"][0]["status"] == "UNRESOLVED"
    assert state["unresolved"][0]["code"] == "INVALID_EVENT"
    with pytest.raises(EventValidationError):
        append_event([], unknown)


def test_reducer_detects_corrupt_revision_chain_without_accepting_newest() -> None:
    first = event("event-1", 0)
    corrupt = event(
        "event-2", 7, event_type="STATE_SUPERSEDED", supersedes=["event-1"],
    )
    state = reduce_events([first, corrupt], as_of=NOW)
    assert state["revision"] == 1
    assert subject(state)["last_accepted_event_id"] == "event-1"
    assert any(item["code"] == "REVISION_CONFLICT" for item in state["unresolved"])


def test_replay_is_byte_equivalent_and_hashes_are_canonical() -> None:
    first = event("event-1", 0)
    second = event(
        "event-2", 1, event_type="STATE_RESOLVED", supersedes=["event-1"],
        lifecycle_state="COMPLETED",
    )
    log = append_event(append_event([], first), second)
    one = reduce_events(log, as_of=NOW)
    two = reduce_events(deepcopy(log), as_of="2026-07-15T12:00:00+00:00")
    assert canonical_json(one) == canonical_json(two)
    assert one["event_log_sha256"] == canonical_sha256(log)
    state_hash = one.pop("state_sha256")
    assert state_hash == canonical_sha256(one)


def test_empty_log_is_explicitly_unresolved() -> None:
    state = reduce_events([], as_of=NOW)
    assert state["project_id"] == "UNRESOLVED"
    assert state["project_status"] == "UNRESOLVED"
    assert state["unresolved"][0]["code"] == "EMPTY_EVENT_LOG"
