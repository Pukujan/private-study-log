from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from cortex_core.project_state import reduce_events
from cortex_core.project_state_projection import (
    GENERATED_MARKER,
    build_agent_resume_pack,
    build_capability_status_projection,
    build_projection_metadata,
    project_state_input_sha256,
    render_human_handoff,
    render_projection_bundle,
    select_projection_documents,
)


def _state() -> dict:
    return {
        "schema_version": 1,
        "project_id": "cortex",
        "project_revision": 12,
        "reducer_revision": "project-state-reducer/1",
        "as_of": "2026-07-15T12:00:00Z",
        "locked_outcome": "Assured workflows must not self-certify.",
        "active_goal": "Publish current-state projections.",
        "active_runs": [{"run_id": "run_2", "status": "ACTIVE", "task": "render"}],
        "blockers": [{"id": "block_1", "status": "OPEN", "reason": "signer unavailable"}],
        "next_actions": [
            {"action_id": "a2", "status": "PENDING", "action": "second"},
            {"action_id": "a1", "status": "READY", "action": "run projection tests"},
        ],
        "claims": [
            {"claim_id": "claim_ok", "status": "ACTIVE", "claim": "reducer is deterministic"},
            {"claim_id": "claim_u", "status": "UNRESOLVED", "claim": "remote trace joined"},
            {"claim_id": "claim_c", "status": "ACTIVE", "claim": "one authority",
             "conflicts": ["event_7", "event_8"]},
            {"claim_id": "claim_e", "status": "ACTIVE", "claim": "probe is live",
             "expires_at": "2026-07-15T11:00:00Z"},
        ],
        "capabilities": [
            {"capability_id": "route", "status": "ACTIVE", "summary": "route planning"},
            {"capability_id": "runtime", "status": "ACTIVE", "summary": "runtime probe",
             "expires_at": "2026-07-15T11:59:59Z"},
        ],
        "documents": [
            {"document_id": "expected", "path": "docs/EXPECTED.md", "status": "ACTIVE",
             "scope": "normative"},
            {"document_id": "old", "path": "docs/OLD.md", "status": "SUPERSEDED",
             "invalidation_reason": "replaced by expected", "replaced_by": "expected"},
            {"document_id": "narrow", "path": "closeouts/task.md", "status": "ACTIVE",
             "scope": "task"},
        ],
        "evidence_hashes": {"event_12": "a" * 64},
    }


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def test_same_reduced_state_is_byte_deterministic_and_not_mutated() -> None:
    state = _state()
    before = deepcopy(state)
    reordered = {key: state[key] for key in reversed(list(state))}

    one = render_projection_bundle(state)
    two = render_projection_bundle(reordered)

    assert one == two
    assert _canonical(one) == _canonical(two)
    assert state == before


def test_generated_markers_and_metadata_are_explicitly_non_assuring() -> None:
    state = _state()
    handoff = render_human_handoff(state)
    metadata = build_projection_metadata(state)

    assert handoff.startswith(f"<!-- {GENERATED_MARKER} -->")
    assert "does not" in handoff and "mint assurance" in handoff
    assert metadata["generated_projection"] is True
    assert metadata["reducer_version"] == "project-state-reducer/1"
    assert metadata["reducer_revision"] == "project-state-reducer/1"
    assert metadata["input_sha256"] == project_state_input_sha256(state)
    assert metadata["assurance_minted"] is False
    assert metadata["ontology_certified"] is False


def test_default_document_selection_excludes_history_and_explicit_mode_labels_it() -> None:
    active = select_projection_documents(_state())
    assert active["selection_mode"] == "ACTIVE_ONLY"
    assert [d["document_id"] for d in active["documents"]] == ["expected", "narrow"]
    assert active["history_excluded_count"] == 1

    all_docs = select_projection_documents(_state(), include_history=True)
    by_id = {d["document_id"]: d for d in all_docs["documents"]}
    assert all_docs["selection_mode"] == "ACTIVE_AND_HISTORY"
    assert by_id["old"]["selection_status"] == "HISTORY"
    assert by_id["old"]["current"] is False
    assert by_id["old"]["invalidation_reason"] == "replaced by expected"
    assert by_id["old"]["replaced_by"] == "expected"


def test_uncertain_conflicting_and_expired_claims_are_visible() -> None:
    state = _state()
    pack = build_agent_resume_pack(state)
    statuses = {c["claim_id"]: c["effective_status"] for c in pack["attention_claims"]}
    assert statuses == {
        "claim_c": "CONFLICTING",
        "claim_e": "EXPIRED",
        "claim_u": "UNRESOLVED",
    }
    handoff = render_human_handoff(state)
    assert "`claim_c` [CONFLICTING]" in handoff
    assert "`claim_e` [EXPIRED]" in handoff
    assert "`claim_u` [UNRESOLVED]" in handoff


def test_expiry_uses_reduced_as_of_not_wall_clock() -> None:
    state = _state()
    before = build_capability_status_projection(state)
    state["as_of"] = "2026-07-15T11:00:00Z"
    after = build_capability_status_projection(state)

    assert {c["capability_id"]: c["effective_status"] for c in before["capabilities"]}["runtime"] == "EXPIRED"
    assert {c["capability_id"]: c["effective_status"] for c in after["capabilities"]}["runtime"] == "ACTIVE"


def test_resume_pack_is_minimal_and_keeps_task_doc_out_of_current_doc_set() -> None:
    state = _state()
    state["event_log"] = [{"large": "must not leak into resume pack"}]
    state["transcript"] = "must not leak"
    pack = build_agent_resume_pack(state)

    assert "event_log" not in pack and "transcript" not in pack
    assert [d["document_id"] for d in pack["current_documents"]] == ["expected"]
    assert pack["next_safe_action"]["action_id"] == "a1"
    assert pack["locked_outcome"] == "Assured workflows must not self-certify."


def test_capability_projection_expires_claim_but_never_qualifies_or_routes() -> None:
    projection = build_capability_status_projection(_state())
    statuses = {c["capability_id"]: c["effective_status"] for c in projection["capabilities"]}
    assert statuses == {"route": "ACTIVE", "runtime": "EXPIRED"}
    assert projection["assurance_minted"] is False
    assert "route_id" not in projection
    assert "dispatch_authorized" not in projection
    assert "cannot qualify, route, sign, or certify" in projection["notice"]


def test_bundle_metadata_hashes_every_non_metadata_output() -> None:
    bundle = render_projection_bundle(_state())
    metadata = bundle["projection-metadata.json"]
    assert set(metadata["output_sha256"]) == set(bundle) - {"projection-metadata.json"}
    for name, expected in metadata["output_sha256"].items():
        value = bundle[name]
        payload = value.encode("utf-8") if isinstance(value, str) else _canonical(value)
        assert hashlib.sha256(payload).hexdigest() == expected


def test_missing_reducer_revision_and_non_json_values_fail_closed() -> None:
    state = _state()
    state.pop("reducer_revision")
    with pytest.raises(ValueError, match="reducer_version"):
        render_projection_bundle(state)

    state = _state()
    state["bad"] = float("nan")
    with pytest.raises(ValueError, match="canonical JSON"):
        render_projection_bundle(state)


def test_recency_does_not_reactivate_invalidated_history() -> None:
    state = _state()
    state["documents"][1]["updated_at"] = "2999-01-01T00:00:00Z"
    selected = select_projection_documents(state)
    assert "old" not in {doc["document_id"] for doc in selected["documents"]}


def test_explicit_inventory_history_wins_over_subject_derived_context() -> None:
    state = _state()
    state["documents"] = [{
        "document_id": "shared",
        "path": "docs/history.md",
        "status": "SUPERSEDED",
        "current": False,
        "replacement_event_ids": ["replacement"],
    }]
    state["capabilities"] = [{
        "capability_id": "shared-capability",
        "status": "INVALIDATED",
        "current": False,
    }]
    state["subjects"] = [
        {
            "subject_id": "normative-source",
            "subject_type": "NORMATIVE",
            "lifecycle_state": "ACTIVE",
            "affected_document_ids": ["shared"],
        },
        {
            "subject_id": "capability-source",
            "subject_type": "CAPABILITY",
            "lifecycle_state": "ACTIVE",
            "affected_capability_ids": ["shared-capability"],
        },
    ]

    selected = select_projection_documents(state, include_history=True)
    assert len(selected["documents"]) == 1
    assert selected["documents"][0]["selection_status"] == "HISTORY"
    assert selected["documents"][0]["source_subject_id"] == "normative-source"

    capabilities = build_capability_status_projection(state)["capabilities"]
    assert len(capabilities) == 1
    assert capabilities[0]["effective_status"] == "INVALIDATED"
    assert capabilities[0]["source_subject_id"] == "capability-source"


def _event(
    event_id: str, revision: int, *, subject_id: str, subject_type: str,
    authority_role: str, claims: list[str], blockers: list[str] | None = None,
    next_actions: list[str] | None = None, documents: list[str] | None = None,
    capabilities: list[str] | None = None,
    scope_kind: str = "PROJECT",
) -> dict:
    evidence_authority = "SIGNED_RECEIPT" if subject_type in {"RUNTIME", "CAPABILITY"} else "DOCUMENTARY"
    evidence_provenance = "SIGNED" if subject_type in {"RUNTIME", "CAPABILITY"} else "CONTENT_ADDRESSED"
    return {
        "schema_version": 1,
        "event_id": event_id,
        "project_id": "cortex",
        "run_id": "run_projection",
        "task_id": "task_projection",
        "subject_id": subject_id,
        "subject_type": subject_type,
        "scope": {
            "kind": scope_kind,
            "id": {
                "PROJECT": "cortex",
                "RUN": "run_projection",
                "TASK": "task_projection",
                "COMPONENT": "component:projection",
            }[scope_kind],
        },
        "event_type": "STATE_ASSERTED",
        "expected_prior_revision": revision,
        "authority": {
            "actor_id": "owner" if subject_type == "NORMATIVE" else "component",
            "authority_class": "HUMAN_OWNER" if subject_type == "NORMATIVE" else "COMPONENT_OWNER",
            "authority_role": authority_role,
        },
        "observed_at": "2026-07-15T10:00:00Z",
        "valid_from": "2026-07-15T10:00:00Z",
        "expires_at": None,
        "appended_at": "2026-07-15T10:01:00Z",
        "lifecycle_state": "ACTIVE",
        "claims": claims,
        "blockers": blockers or [],
        "next_actions": next_actions or [],
        "affected_document_ids": documents or [],
        "affected_capability_ids": capabilities or [],
        "evidence_refs": [{
            "evidence_id": f"evidence:{event_id}",
            "uri": f"file:///evidence/{event_id}",
            "sha256": (str(revision + 1) * 64)[:64],
            "authority_class": evidence_authority,
            "independence_class": "INDEPENDENT",
            "provenance_class": evidence_provenance,
            "observed_at": "2026-07-15T10:00:00Z",
            "expires_at": None,
        }],
        "supersedes": [],
        "invalidates": [],
        "source": {"repository": "cortex", "commit": "abc123", "config_version": "1"},
    }


def test_real_reducer_state_projects_subject_categories_end_to_end() -> None:
    events = [
        _event(
            "event_normative", 0, subject_id="locked-outcome", subject_type="NORMATIVE",
            authority_role="project-policy", claims=["Do not self-certify"],
            documents=["doc:expected-behavior"],
        ),
        _event(
            "event_operational", 1, subject_id="projection-work", subject_type="OPERATIONAL",
            authority_role="project-operator", claims=["Publish the current projection"],
            blockers=["Storage transaction pending"], next_actions=["Stage the bundle atomically"],
        ),
        _event(
            "event_capability", 2, subject_id="route-runtime", subject_type="CAPABILITY",
            authority_role="capability-owner", claims=["Route planning exists"],
            capabilities=["capability:model-route"],
        ),
        _event(
            "event_task_normative", 3, subject_id="task-policy", subject_type="NORMATIVE",
            authority_role="task-policy", claims=["Task-local wording"], scope_kind="TASK",
        ),
        _event(
            "event_task_work", 4, subject_id="task-work", subject_type="OPERATIONAL",
            authority_role="task-driver", claims=["Task-local work"], scope_kind="TASK",
        ),
    ]
    state = reduce_events(
        events,
        as_of="2026-07-15T12:00:00Z",
        verified_authority_event_ids=[item["event_id"] for item in events],
    )
    assert "reducer_version" in state and "reducer_revision" not in state

    bundle = render_projection_bundle(state)
    pack = bundle["agent-resume-pack.json"]
    capabilities = bundle["capability-status.json"]
    documents = bundle["documents.json"]

    assert pack["reducer_version"] == state["reducer_version"]
    assert pack["locked_outcome"] == [
        {"subject_id": "locked-outcome", "claims": ["Do not self-certify"]},
    ]
    assert [item["subject_id"] for item in pack["active_work"]] == [
        "projection-work", "task-work",
    ]
    assert pack["active_goal"] == [
        {"subject_id": "projection-work", "claims": ["Publish the current projection"]},
    ]
    assert pack["blockers"][0]["source_subject_id"] == "projection-work"
    assert pack["next_safe_action"]["source_subject_id"] == "projection-work"
    assert pack["evidence_hashes"]["evidence:event_normative"] == "1" * 64
    assert [doc["document_id"] for doc in documents["documents"]] == ["doc:expected-behavior"]
    assert [doc["document_id"] for doc in pack["current_documents"]] == ["doc:expected-behavior"]
    assert [item["capability_id"] for item in capabilities["capabilities"]] == [
        "capability:model-route",
    ]
    assert capabilities["capabilities"][0]["source_subject_id"] == "route-runtime"
    assert capabilities["capabilities"][0]["effective_status"] == "UNRESOLVED"
    assert "route_id" not in capabilities["capabilities"][0]
    assert "verified" not in capabilities["capabilities"][0]
    assert "Do not self-certify" in bundle["HANDOFF.md"]
