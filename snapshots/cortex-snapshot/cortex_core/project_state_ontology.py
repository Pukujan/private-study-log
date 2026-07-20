"""Deterministic living-ontology synchronization from reduced project state.

The reduced state remains authoritative.  This module projects canonical
document lifecycle and only unambiguous, event-explicit replacement lineage
into ontology ``doc`` entities.  It never guesses semantic relations, chooses
replacements, certifies runtime truth, or writes ontology JSONL directly.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .config import resolve_exact_workspace
from .ontology import (
    Entity, Relation, assert_relation, load_entities, load_relations, make_entity_id,
    upsert_entity,
)
from .project_state import canonical_sha256


ONTOLOGY_SYNC_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATUS_MAP = {
    "ACTIVE": "active",
    "SUPERSEDED": "superseded",
    "INVALIDATED": "deprecated",
    "EXPIRED": "expired",
    "UNRESOLVED": "unavailable",
    "CONFLICT": "unavailable",
    "CONFLICTING": "unavailable",
}


def _json_copy(value: Any) -> Any:
    # canonical_sha256 performs the same strict JSON serialization used by the
    # reducer and rejects non-JSON/non-finite input.
    canonical_sha256(value)
    return copy.deepcopy(value)


def _validate_anchor(current_state: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(current_state, Mapping):
        raise TypeError("current_state must be a mapping")
    state = _json_copy(dict(current_state))
    reducer_version = state.get("reducer_version")
    event_hash = state.get("event_log_sha256")
    state_hash = state.get("state_sha256")
    if not isinstance(reducer_version, str) or not reducer_version.strip():
        raise ValueError("current_state requires reducer_version")
    if not isinstance(event_hash, str) or not _SHA256.fullmatch(event_hash):
        raise ValueError("current_state requires a canonical event_log_sha256")
    if not isinstance(state_hash, str) or not _SHA256.fullmatch(state_hash):
        raise ValueError("current_state requires a canonical state_sha256")
    payload = {key: value for key, value in state.items() if key != "state_sha256"}
    if canonical_sha256(payload) != state_hash:
        raise ValueError("current_state state_sha256 does not match its replay payload")
    if not isinstance(state.get("documents"), list):
        raise ValueError("current_state requires canonical documents[]")
    return state


def _safe_relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text != value or "\\" in text or any(ord(char) < 32 for char in text):
        return None
    if any(char in text for char in '<>:"|?*#'):
        return None
    parts = text.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in parts):
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or path.as_posix() != text:
        return None
    if "/" not in text and not path.suffix:
        return None
    return text


def _skip(document_id: Any, path: Any, code: str, detail: str) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "path": path,
        "code": code,
        "detail": detail,
        "status": "UNRESOLVED_SKIP",
    }


def _lineage_skip(
    replacement_event_id: Any, code: str, detail: str, *,
    prior_document_ids: Sequence[str] = (), replacement_document_ids: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "document_id": None,
        "path": None,
        "replacement_event_id": replacement_event_id,
        "prior_document_ids": sorted(set(prior_document_ids)),
        "replacement_document_ids": sorted(set(replacement_document_ids)),
        "code": code,
        "detail": detail,
        "status": "UNRESOLVED_SKIP",
    }


def _provenance(state: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "projection_only": True,
        "assurance_minted": False,
        "runtime_certified": False,
        "project_id": state.get("project_id"),
        "revision": state.get("revision"),
        "reducer_version": state.get("reducer_version"),
        "event_log_sha256": state.get("event_log_sha256"),
        "state_sha256": state.get("state_sha256"),
        "document_id": document.get("document_id"),
        "last_event_id": document.get("last_event_id"),
        "source_event_ids": sorted(str(item) for item in document.get("source_event_ids", [])),
        "replacement_event_ids": sorted(
            str(item) for item in document.get("replacement_event_ids", [])
        ),
        "invalidation_reasons": sorted(
            str(item) for item in document.get("invalidation_reasons", [])
        ),
    }


def _desired_matches(entity: Entity, operation: Mapping[str, Any]) -> bool:
    attributes = copy.deepcopy(entity.attributes)
    attributes["project_state_sync"] = copy.deepcopy(operation["provenance"])
    return (
        entity.status == operation["status"]
        and entity.name == operation["name"]
        and operation["source_path"] in entity.source_paths
        and entity.attributes == attributes
    )


def _relation_matches(relation: Relation, operation: Mapping[str, Any]) -> bool:
    return (
        relation.subject == operation["subject"]
        and relation.predicate == "supersedes"
        and relation.object == operation["object"]
        and relation.status == "active"
        and relation.invalid_from is None
        and relation.summary == operation["summary"]
        and sorted(relation.source_paths) == sorted(operation["source_paths"])
    )


def build_ontology_sync_plan(
    current_state: Mapping[str, Any], *, workspace: str | Path,
) -> dict[str, Any]:
    """Build a read-only, byte-stable plan from canonical ``documents[]``.

    Existing ontology documents are matched only by an exact ``source_paths``
    entry.  No timestamps, names, or summaries infer identity or relations;
    replacement event ids create lineage only through the exact documented
    one-prior/one-replacement rule.
    """
    state = _validate_anchor(current_state)
    ws = resolve_exact_workspace(workspace)
    entities = load_entities(ws)
    docs_by_path: dict[str, list[Entity]] = {}
    for entity in entities.values():
        if entity.type != "doc":
            continue
        for source_path in entity.source_paths:
            docs_by_path.setdefault(source_path, []).append(entity)

    operations: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    documents: Sequence[Any] = state["documents"]
    id_counts: dict[str, int] = {}
    path_counts: dict[str, int] = {}
    for item in documents:
        if not isinstance(item, Mapping):
            continue
        if isinstance(item.get("document_id"), str):
            key = str(item["document_id"])
            id_counts[key] = id_counts.get(key, 0) + 1
        if isinstance(item.get("path"), str):
            key = str(item["path"])
            path_counts[key] = path_counts.get(key, 0) + 1
    for raw in sorted(documents, key=lambda value: str(
        value.get("document_id", "") if isinstance(value, Mapping) else value
    )):
        if not isinstance(raw, Mapping):
            skips.append(_skip(raw, None, "INVALID_DOCUMENT", "document entry is not an object"))
            continue
        document = dict(raw)
        document_id = document.get("document_id")
        path = document.get("path")
        safe_id = _safe_relative_path(document_id)
        if safe_id is None:
            skips.append(_skip(document_id, path, "OPAQUE_DOCUMENT_ID",
                               "document_id is not a safe relative path"))
            continue
        if path in (None, ""):
            skips.append(_skip(document_id, path, "MISSING_PATH",
                               "canonical document has no materialized path"))
            continue
        safe_path = _safe_relative_path(path)
        if safe_path is None:
            skips.append(_skip(document_id, path, "UNSAFE_PATH",
                               "path is not a safe relative document path"))
            continue
        if safe_path != safe_id:
            skips.append(_skip(document_id, path, "IDENTITY_PATH_CONFLICT",
                               "document_id and path differ; identity will not be inferred"))
            continue
        if id_counts.get(safe_id, 0) > 1 or path_counts.get(safe_path, 0) > 1:
            skips.append(_skip(document_id, path, "DUPLICATE_DOCUMENT",
                               "duplicate document identity/path in current state"))
            continue
        raw_status = str(document.get("status", "")).upper()
        status = _STATUS_MAP.get(raw_status)
        if status is None:
            skips.append(_skip(document_id, path, "UNSUPPORTED_STATUS",
                               f"document lifecycle {raw_status!r} has no ontology mapping"))
            continue
        expected_current = raw_status == "ACTIVE"
        if document.get("current") is not expected_current:
            skips.append(_skip(document_id, path, "LIFECYCLE_CONFLICT",
                               "current flag conflicts with explicit lifecycle status"))
            continue
        target = (ws / PurePosixPath(safe_path)).resolve()
        try:
            target.relative_to(ws.resolve())
        except ValueError:
            skips.append(_skip(document_id, path, "PATH_ESCAPE", "path escapes workspace"))
            continue
        if not target.is_file():
            skips.append(_skip(document_id, path, "SOURCE_NOT_FOUND",
                               "path does not resolve to a real file"))
            continue
        matches = sorted(docs_by_path.get(safe_path, []), key=lambda item: item.entity_id)
        if len(matches) > 1:
            skips.append(_skip(document_id, path, "ONTOLOGY_SOURCE_CONFLICT",
                               "multiple ontology doc entities have the exact source_path"))
            continue
        existing = matches[0] if matches else None
        name = existing.name if existing else safe_path
        entity_id = existing.entity_id if existing else make_entity_id("doc", name)
        collision = entities.get(entity_id)
        if existing is None and collision is not None:
            skips.append(_skip(document_id, path, "ONTOLOGY_ID_CONFLICT",
                               "deterministic entity id exists with a different source_path"))
            continue
        operation = {
            "action": "create" if existing is None else "update",
            "document_id": safe_id,
            "entity_id": entity_id,
            "entity_type": "doc",
            "name": name,
            "status": status,
            "source_path": safe_path,
            "provenance": _provenance(state, document),
        }
        if existing is not None and _desired_matches(existing, operation):
            operation["action"] = "noop"
        operations.append(operation)

    operation_by_document = {item["document_id"]: item for item in operations}
    prior_by_event: dict[str, set[str]] = {}
    replacement_by_event: dict[str, set[str]] = {}
    for raw in documents:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("document_id"), str):
            continue
        document_id = str(raw["document_id"])
        for event_id in raw.get("replacement_event_ids", []):
            if isinstance(event_id, str) and event_id:
                prior_by_event.setdefault(event_id, set()).add(document_id)
        for event_id in raw.get("source_event_ids", []):
            if isinstance(event_id, str) and event_id:
                replacement_by_event.setdefault(event_id, set()).add(document_id)

    relation_operations: list[dict[str, Any]] = []
    current_relations = load_relations(ws)
    for event_id in sorted(prior_by_event):
        prior_ids = sorted(prior_by_event[event_id])
        replacement_ids = sorted(replacement_by_event.get(event_id, set()))
        if len(prior_ids) != 1 or len(replacement_ids) != 1:
            skips.append(_lineage_skip(
                event_id,
                "AMBIGUOUS_REPLACEMENT_LINEAGE",
                "replacement event must map to exactly one prior and one replacement document",
                prior_document_ids=prior_ids,
                replacement_document_ids=replacement_ids,
            ))
            continue
        old_document_id = prior_ids[0]
        new_document_id = replacement_ids[0]
        if old_document_id == new_document_id:
            skips.append(_lineage_skip(
                event_id, "SELF_REPLACEMENT_LINEAGE",
                "replacement event maps a document to itself",
                prior_document_ids=prior_ids,
                replacement_document_ids=replacement_ids,
            ))
            continue
        old_operation = operation_by_document.get(old_document_id)
        new_operation = operation_by_document.get(new_document_id)
        if old_operation is None or new_operation is None:
            skips.append(_lineage_skip(
                event_id, "LINEAGE_DOCUMENT_UNAVAILABLE",
                "one or both explicitly mapped documents were skipped from ontology sync",
                prior_document_ids=prior_ids,
                replacement_document_ids=replacement_ids,
            ))
            continue
        subject = new_operation["entity_id"]
        object_id = old_operation["entity_id"]
        exact = sorted(
            (
                relation for relation in current_relations.values()
                if relation.predicate == "supersedes"
                and relation.subject == subject and relation.object == object_id
            ),
            key=lambda relation: relation.relation_id,
        )
        conflicting_live = sorted(
            relation.relation_id for relation in current_relations.values()
            if relation.predicate == "supersedes"
            and relation.object == object_id
            and relation.status == "active" and relation.invalid_from is None
            and relation.subject != subject
        )
        if len(exact) > 1 or any(
            relation.status != "active" or relation.invalid_from is not None for relation in exact
        ) or conflicting_live:
            skips.append(_lineage_skip(
                event_id, "ONTOLOGY_LINEAGE_CONFLICT",
                "existing ontology lineage is duplicate, invalid, or points to another replacement",
                prior_document_ids=prior_ids,
                replacement_document_ids=replacement_ids,
            ))
            continue
        source_paths = sorted({old_operation["source_path"], new_operation["source_path"]})
        summary = (
            f"Explicit project-state replacement event {event_id}: "
            f"{new_document_id} supersedes {old_document_id}."
        )
        relation_id = exact[0].relation_id if exact else (
            "rel-project-state-" + canonical_sha256({
                "event_id": event_id,
                "subject": subject,
                "predicate": "supersedes",
                "object": object_id,
            })[:32]
        )
        collision = current_relations.get(relation_id)
        if collision is not None and not exact:
            skips.append(_lineage_skip(
                event_id, "ONTOLOGY_RELATION_ID_CONFLICT",
                "deterministic relation id is already occupied",
                prior_document_ids=prior_ids,
                replacement_document_ids=replacement_ids,
            ))
            continue
        relation_operation = {
            "action": "assert" if not exact else "update",
            "relation_id": relation_id,
            "replacement_event_id": event_id,
            "subject": subject,
            "predicate": "supersedes",
            "object": object_id,
            "source_paths": source_paths,
            "summary": summary,
        }
        if exact and _relation_matches(exact[0], relation_operation):
            relation_operation["action"] = "noop"
        relation_operations.append(relation_operation)

    return {
        "schema_version": ONTOLOGY_SYNC_SCHEMA_VERSION,
        "generated_sync_plan": True,
        "project_id": state.get("project_id"),
        "revision": state.get("revision"),
        "reducer_version": state.get("reducer_version"),
        "event_log_sha256": state.get("event_log_sha256"),
        "state_sha256": state.get("state_sha256"),
        "operations": sorted(operations, key=lambda item: (item["source_path"], item["entity_id"])),
        "unresolved_skips": sorted(
            skips, key=lambda item: (str(item.get("document_id")), item["code"], str(item.get("path")))
        ),
        "relations": sorted(
            relation_operations,
            key=lambda item: (item["replacement_event_id"], item["subject"], item["object"]),
        ),
        "assurance_minted": False,
        "runtime_certified": False,
    }


def apply_ontology_sync_plan(
    plan: Mapping[str, Any], *, workspace: str | Path,
) -> dict[str, Any]:
    """Apply a generated plan through ontology public APIs, never direct JSONL."""
    if not isinstance(plan, Mapping) or plan.get("generated_sync_plan") is not True:
        raise ValueError("plan must be produced by build_ontology_sync_plan")
    if not isinstance(plan.get("relations", []), list):
        raise ValueError("plan relations must be a list")
    ws = resolve_exact_workspace(workspace)
    applied: list[dict[str, Any]] = []
    noops: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for operation in plan.get("operations", []):
        if not isinstance(operation, Mapping) or operation.get("entity_type") != "doc":
            raise ValueError("plan contains a non-document operation")
        source_path = operation.get("source_path")
        if _safe_relative_path(source_path) is None:
            raise ValueError("plan contains an unsafe source_path")
        entities = load_entities(ws)
        source_matches = sorted(
            (
                entity for entity in entities.values()
                if entity.type == "doc" and source_path in entity.source_paths
            ),
            key=lambda entity: entity.entity_id,
        )
        if len(source_matches) > 1 or (
            source_matches and source_matches[0].entity_id != operation.get("entity_id")
        ):
            failures.append({
                "entity_id": operation.get("entity_id"),
                "code": "CONCURRENT_SOURCE_CONFLICT",
                "detail": "source_path identity changed after the plan was built",
            })
            continue
        current = entities.get(str(operation.get("entity_id")))
        if current is not None and _desired_matches(current, operation):
            noops.append({"entity_id": current.entity_id, "action": "noop"})
            continue
        if current is not None and source_path not in current.source_paths:
            failures.append({
                "entity_id": operation.get("entity_id"),
                "code": "CONCURRENT_ID_CONFLICT",
                "detail": "entity id now points at a different source_path",
            })
            continue
        attributes = copy.deepcopy(current.attributes) if current else {}
        attributes["project_state_sync"] = copy.deepcopy(operation["provenance"])
        result = upsert_entity(
            "doc",
            str(operation["name"]),
            summary=current.summary if current else "",
            status=str(operation["status"]),
            aliases=list(current.aliases) if current else [],
            source_paths=list(current.source_paths) if current else [str(source_path)],
            attributes=attributes,
            author_model=current.author_model if current else "",
            workspace=ws,
        )
        if result.get("ok"):
            applied.append({"entity_id": result["entity_id"], "action": result["event"]})
        else:
            failures.append({
                "entity_id": operation.get("entity_id"),
                "code": "ONTOLOGY_WRITE_FAILED",
                "detail": "; ".join(str(item) for item in result.get("errors", [])),
            })
    relation_results: list[dict[str, Any]] = []
    relations_asserted = 0
    for operation in plan.get("relations", []):
        if not isinstance(operation, Mapping) or operation.get("predicate") != "supersedes":
            raise ValueError("plan contains a non-supersedes relation")
        if not isinstance(operation.get("source_paths"), list) or any(
            _safe_relative_path(path) is None for path in operation["source_paths"]
        ):
            raise ValueError("plan relation contains unsafe source_paths")
        entities = load_entities(ws)
        if operation.get("subject") not in entities or operation.get("object") not in entities:
            failures.append({
                "relation_id": operation.get("relation_id"),
                "code": "RELATION_ENDPOINT_UNAVAILABLE",
                "detail": "one or both planned ontology document entities are unavailable",
            })
            continue
        relations = load_relations(ws)
        exact = sorted(
            (
                relation for relation in relations.values()
                if relation.predicate == "supersedes"
                and relation.subject == operation.get("subject")
                and relation.object == operation.get("object")
            ),
            key=lambda relation: relation.relation_id,
        )
        conflicting_live = [
            relation for relation in relations.values()
            if relation.predicate == "supersedes"
            and relation.object == operation.get("object")
            and relation.subject != operation.get("subject")
            and relation.status == "active" and relation.invalid_from is None
        ]
        if len(exact) > 1 or any(
            relation.status != "active" or relation.invalid_from is not None for relation in exact
        ) or conflicting_live:
            failures.append({
                "relation_id": operation.get("relation_id"),
                "code": "CONCURRENT_LINEAGE_CONFLICT",
                "detail": "ontology lineage changed after the plan was built",
            })
            continue
        if exact and _relation_matches(exact[0], operation):
            relation_results.append({
                "relation_id": exact[0].relation_id,
                "action": "noop",
                "subject": operation["subject"],
                "predicate": "supersedes",
                "object": operation["object"],
            })
            continue
        relation_id = exact[0].relation_id if exact else str(operation["relation_id"])
        result = assert_relation(
            str(operation["subject"]),
            "supersedes",
            str(operation["object"]),
            summary=str(operation["summary"]),
            source_paths=list(operation["source_paths"]),
            author_model="",
            relation_id=relation_id,
            workspace=ws,
        )
        if result.get("ok"):
            relations_asserted += 1
            relation_results.append({
                "relation_id": result["relation_id"],
                "action": result["event"],
                "subject": operation["subject"],
                "predicate": "supersedes",
                "object": operation["object"],
            })
        else:
            failures.append({
                "relation_id": relation_id,
                "code": "ONTOLOGY_RELATION_WRITE_FAILED",
                "detail": "; ".join(str(item) for item in result.get("errors", [])),
            })
    unresolved = copy.deepcopy(list(plan.get("unresolved_skips", [])))
    return {
        "ok": not failures,
        "status": "APPLIED_WITH_UNRESOLVED" if unresolved and not failures
                  else "FAILED" if failures else "APPLIED",
        "applied": applied,
        "noops": noops,
        "unresolved_skips": unresolved,
        "failures": failures,
        "relations": relation_results,
        "relations_asserted": relations_asserted,
        "assurance_minted": False,
        "runtime_certified": False,
    }


def sync_project_state_ontology(
    current_state: Mapping[str, Any], *, workspace: str | Path,
) -> dict[str, Any]:
    """Store-callback entry point: plan then apply one published reduced state."""
    plan = build_ontology_sync_plan(current_state, workspace=workspace)
    result = apply_ontology_sync_plan(plan, workspace=workspace)
    return {"plan": plan, "result": result}


def verify_project_state_ontology(
    current_state: Mapping[str, Any], *, workspace: str | Path,
) -> dict[str, Any]:
    """Read-only post-publication drift check suitable for store status.

    ``clean`` requires every required entity and explicit-lineage relation to
    already be a no-op.  Unresolved inputs/conflicts are returned as warnings
    and keep the verification non-clean; this function never repairs them.
    """
    plan = build_ontology_sync_plan(current_state, workspace=workspace)
    pending_entities = [
        item["entity_id"] for item in plan["operations"] if item["action"] != "noop"
    ]
    pending_relations = [
        item["relation_id"] for item in plan["relations"] if item["action"] != "noop"
    ]
    warnings = copy.deepcopy(plan["unresolved_skips"])
    reasons = [f"entity operation pending: {entity_id}" for entity_id in pending_entities]
    reasons.extend(f"relation operation pending: {relation_id}" for relation_id in pending_relations)
    reasons.extend(
        f"unresolved ontology sync input: {item['code']}"
        + (f" ({item['replacement_event_id']})" if item.get("replacement_event_id") else "")
        for item in warnings
    )
    return {
        "clean": not pending_entities and not pending_relations and not warnings,
        "plan": plan,
        "reasons": reasons,
        "warnings": warnings,
        "pending_entity_ids": pending_entities,
        "pending_relation_ids": pending_relations,
        "assurance_minted": False,
        "runtime_certified": False,
    }


__all__ = [
    "ONTOLOGY_SYNC_SCHEMA_VERSION",
    "apply_ontology_sync_plan",
    "build_ontology_sync_plan",
    "sync_project_state_ontology",
    "verify_project_state_ontology",
]
