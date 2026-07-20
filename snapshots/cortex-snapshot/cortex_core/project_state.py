"""Deterministic append/reduce core for Cortex project operational state.

The event log is the authority; ``reduce_events`` materializes a replayable
current view.  This module deliberately does no filesystem I/O, signing, or
projection writes.  A storage layer can persist the copy returned by
``append_event`` and regenerate projections from the returned current state.

Time is an explicit reducer input.  No function reads the wall clock, so replay
with the same event log and ``as_of`` value is byte-equivalent.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable


EVENT_SCHEMA_VERSION = 1
CURRENT_SCHEMA_VERSION = 1
REDUCER_VERSION = "cortex.project_state.reducer/1"

EVENT_TYPES = {
    "STATE_ASSERTED", "STATE_SUPERSEDED", "STATE_INVALIDATED", "STATE_RESOLVED",
}
SUBJECT_TYPES = {"NORMATIVE", "OPERATIONAL", "DECISION", "RUNTIME", "CAPABILITY"}
SCOPE_KINDS = {"PROJECT", "RUN", "TASK", "COMPONENT"}
LIFECYCLE_STATES = {"ACTIVE", "COMPLETED", "BLOCKED", "UNRESOLVED"}
AUTHORITY_CLASSES = {
    "HUMAN_OWNER", "DOMAIN_EXPERT", "COMPONENT_OWNER", "RUNTIME",
    "EXTERNAL_EVALUATOR", "AGENT", "DETERMINISTIC_REDUCER",
}
_AUTHORITY_PRECEDENCE = {
    "AGENT": 10,
    "RUNTIME": 20,
    "EXTERNAL_EVALUATOR": 30,
    "COMPONENT_OWNER": 40,
    "DOMAIN_EXPERT": 50,
    "HUMAN_OWNER": 60,
}
EVIDENCE_AUTHORITY_CLASSES = {
    "SIGNED_RECEIPT", "DETERMINISTIC_CHECK", "EXTERNAL_OBSERVATION",
    "HUMAN_ATTESTATION", "DOCUMENTARY",
}
INDEPENDENCE_CLASSES = {"INDEPENDENT", "SAME_PROCESS", "SELF_REPORTED", "HUMAN"}
PROVENANCE_CLASSES = {"CONTENT_ADDRESSED", "SIGNED", "ATTESTED", "OBSERVED"}
_REJECTED_AUTHORITY_REASON = (
    "unverified authority event rejected; existing verified or safe current authority retained"
)

_EVENT_FIELDS = {
    "schema_version", "event_id", "project_id", "run_id", "task_id",
    "subject_id", "subject_type", "scope", "event_type",
    "expected_prior_revision", "authority", "observed_at", "valid_from",
    "expires_at", "appended_at", "lifecycle_state", "claims", "blockers",
    "next_actions", "affected_document_ids", "affected_capability_ids",
    "evidence_refs", "supersedes", "invalidates", "source",
}
_AUTHORITY_FIELDS = {"actor_id", "authority_class", "authority_role"}
_SCOPE_FIELDS = {"kind", "id"}
_SOURCE_FIELDS = {"repository", "commit", "config_version"}
_EVIDENCE_FIELDS = {
    "evidence_id", "uri", "sha256", "authority_class", "independence_class",
    "provenance_class", "observed_at", "expires_at",
}


class ProjectStateError(ValueError):
    """Base class for deterministic event-log boundary failures."""


class EventValidationError(ProjectStateError):
    """An event is not valid project-state-event/v1 data."""


class RevisionConflictError(ProjectStateError):
    """The caller's expected prior project revision is stale or inconsistent."""


def canonical_json(value: Any) -> str:
    """Return the one stable JSON representation used for every digest."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_time(value: Any, field: str, problems: list[str]) -> datetime | None:
    if not _nonempty(value):
        problems.append(f"{field} must be a non-empty timezone-aware date-time")
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        problems.append(f"{field} must be an ISO-8601 date-time")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        problems.append(f"{field} must include a timezone")
        return None
    return parsed.astimezone(timezone.utc)


def _required_object(
    value: Any, field: str, fields: set[str], problems: list[str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        problems.append(f"{field} must be an object")
        return None
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    problems.extend(f"{field} missing required field {name!r}" for name in missing)
    problems.extend(f"{field} has unknown field {name!r}" for name in unknown)
    return value


def _string_list(value: Any, field: str, problems: list[str]) -> None:
    if not isinstance(value, list) or any(not _nonempty(item) for item in value):
        problems.append(f"{field} must be a list of non-empty strings")
        return
    if len(value) != len(set(value)):
        problems.append(f"{field} must not contain duplicates")


def _validate_evidence(value: Any, index: int, problems: list[str]) -> None:
    prefix = f"evidence_refs[{index}]"
    evidence = _required_object(value, prefix, _EVIDENCE_FIELDS, problems)
    if evidence is None:
        return
    for field in ("evidence_id", "uri"):
        if not _nonempty(evidence.get(field)):
            problems.append(f"{prefix}.{field} must be a non-empty string")
    digest = evidence.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        c not in "0123456789abcdef" for c in digest
    ):
        problems.append(f"{prefix}.sha256 must be 64 lowercase hexadecimal characters")
    if evidence.get("authority_class") not in EVIDENCE_AUTHORITY_CLASSES:
        problems.append(f"{prefix}.authority_class is unknown")
    if evidence.get("independence_class") not in INDEPENDENCE_CLASSES:
        problems.append(f"{prefix}.independence_class is unknown")
    if evidence.get("provenance_class") not in PROVENANCE_CLASSES:
        problems.append(f"{prefix}.provenance_class is unknown")
    observed = _parse_time(evidence.get("observed_at"), f"{prefix}.observed_at", problems)
    expires = None
    if evidence.get("expires_at") is not None:
        expires = _parse_time(evidence.get("expires_at"), f"{prefix}.expires_at", problems)
    if observed is not None and expires is not None and observed >= expires:
        problems.append(f"{prefix}.expires_at must be later than observed_at")


def validate_event(event: Any) -> tuple[bool, list[str]]:
    """Validate the public v1 event shape and its deterministic invariants."""
    if not isinstance(event, dict):
        return False, [f"event must be an object, got {type(event).__name__}"]
    problems: list[str] = []
    missing = sorted(_EVENT_FIELDS - set(event))
    unknown = sorted(set(event) - _EVENT_FIELDS)
    problems.extend(f"missing required field {name!r}" for name in missing)
    problems.extend(f"unknown field {name!r}" for name in unknown)
    if event.get("schema_version") != EVENT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {EVENT_SCHEMA_VERSION}")
    for field in ("event_id", "project_id", "run_id", "task_id", "subject_id"):
        if not _nonempty(event.get(field)):
            problems.append(f"{field} must be a non-empty string")
    if event.get("subject_type") not in SUBJECT_TYPES:
        problems.append("subject_type is unknown")
    if event.get("event_type") not in EVENT_TYPES:
        problems.append("event_type is unknown")
    if event.get("lifecycle_state") not in LIFECYCLE_STATES:
        problems.append("lifecycle_state is unknown")
    revision = event.get("expected_prior_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        problems.append("expected_prior_revision must be an integer >= 0")

    scope = _required_object(event.get("scope"), "scope", _SCOPE_FIELDS, problems)
    if scope is not None:
        if scope.get("kind") not in SCOPE_KINDS:
            problems.append("scope.kind is unknown")
        if not _nonempty(scope.get("id")):
            problems.append("scope.id must be a non-empty string")
        expected_scope_id = {
            "PROJECT": event.get("project_id"),
            "RUN": event.get("run_id"),
            "TASK": event.get("task_id"),
        }.get(scope.get("kind"))
        if expected_scope_id is not None and scope.get("id") != expected_scope_id:
            problems.append(
                f"scope.id must equal {scope.get('kind', '').lower()} identifier "
                f"{expected_scope_id!r}"
            )

    authority = _required_object(
        event.get("authority"), "authority", _AUTHORITY_FIELDS, problems,
    )
    if authority is not None:
        for field in ("actor_id", "authority_role"):
            if not _nonempty(authority.get(field)):
                problems.append(f"authority.{field} must be a non-empty string")
        if authority.get("authority_class") not in AUTHORITY_CLASSES:
            problems.append("authority.authority_class is unknown")
        elif authority.get("authority_class") == "DETERMINISTIC_REDUCER":
            problems.append("input events cannot claim DETERMINISTIC_REDUCER authority")

    observed = _parse_time(event.get("observed_at"), "observed_at", problems)
    valid_from = _parse_time(event.get("valid_from"), "valid_from", problems)
    appended = _parse_time(event.get("appended_at"), "appended_at", problems)
    expires = None
    if event.get("expires_at") is not None:
        expires = _parse_time(event.get("expires_at"), "expires_at", problems)
    if observed is not None and appended is not None and observed > appended:
        problems.append("observed_at must not be later than appended_at")
    if valid_from is not None and expires is not None and valid_from >= expires:
        problems.append("expires_at must be later than valid_from")

    for field in (
        "claims", "blockers", "next_actions", "affected_document_ids",
        "affected_capability_ids", "supersedes", "invalidates",
    ):
        _string_list(event.get(field), field, problems)
    supersedes = event.get("supersedes") if isinstance(event.get("supersedes"), list) else []
    invalidates = event.get("invalidates") if isinstance(event.get("invalidates"), list) else []
    if set(supersedes) & set(invalidates):
        problems.append("an event target cannot appear in both supersedes and invalidates")
    if event.get("event_id") in set(supersedes) | set(invalidates):
        problems.append("an event cannot supersede or invalidate itself")
    if event.get("event_type") == "STATE_ASSERTED" and (supersedes or invalidates):
        problems.append("STATE_ASSERTED cannot carry supersedes or invalidates targets")
    if event.get("event_type") == "STATE_SUPERSEDED" and not supersedes:
        problems.append("STATE_SUPERSEDED requires at least one supersedes target")
    if event.get("event_type") == "STATE_INVALIDATED" and not invalidates:
        problems.append("STATE_INVALIDATED requires at least one invalidates target")
    if event.get("event_type") == "STATE_RESOLVED" and not (supersedes or invalidates):
        problems.append("STATE_RESOLVED requires an explicit supersedes or invalidates target")

    evidence = event.get("evidence_refs")
    if not isinstance(evidence, list):
        problems.append("evidence_refs must be a list")
        evidence = []
    for i, item in enumerate(evidence):
        _validate_evidence(item, i, problems)
    evidence_ids = [e.get("evidence_id") for e in evidence if isinstance(e, dict)]
    if len(evidence_ids) != len(set(evidence_ids)):
        problems.append("evidence_refs must not repeat an evidence_id")
    if event.get("subject_type") in {"RUNTIME", "CAPABILITY"} and not any(
        isinstance(item, dict)
        and item.get("authority_class") == "SIGNED_RECEIPT"
        and item.get("provenance_class") == "SIGNED"
        for item in evidence
    ):
        problems.append("RUNTIME and CAPABILITY subjects require signed-receipt evidence")

    source = _required_object(event.get("source"), "source", _SOURCE_FIELDS, problems)
    if source is not None:
        for field in _SOURCE_FIELDS:
            if not _nonempty(source.get(field)):
                problems.append(f"source.{field} must be a non-empty string")
    return not problems, problems


def _copy_json(value: Any) -> Any:
    return json.loads(canonical_json(value))


def append_event(events: Iterable[dict[str, Any]], event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a new event log after an optimistic-revision append.

    The input is never mutated.  Retrying the identical event id with identical
    canonical content is idempotent.  Reusing an id for different content or
    appending against a stale revision fails before producing a new log.
    """
    current = _copy_json(list(events))
    seen: dict[str, dict[str, Any]] = {}
    project_id: str | None = None
    for index, existing in enumerate(current):
        ok, problems = validate_event(existing)
        if not ok:
            raise EventValidationError(f"existing event at index {index} is invalid: {problems}")
        event_id = existing["event_id"]
        if event_id in seen:
            raise RevisionConflictError(f"existing event log repeats event_id {event_id!r}")
        if existing["expected_prior_revision"] != index:
            raise RevisionConflictError(
                f"existing event {event_id!r} expected revision "
                f"{existing['expected_prior_revision']}, log position requires {index}"
            )
        if project_id is None:
            project_id = existing["project_id"]
        elif existing["project_id"] != project_id:
            raise RevisionConflictError("an append-only project log cannot mix project identifiers")
        seen[event_id] = existing

    ok, problems = validate_event(event)
    if not ok:
        raise EventValidationError(f"invalid project-state event: {problems}")
    existing = seen.get(event["event_id"])
    if existing is not None:
        if canonical_json(existing) == canonical_json(event):
            return current
        raise RevisionConflictError(f"event_id {event['event_id']!r} already has different content")
    if project_id is not None and event["project_id"] != project_id:
        raise RevisionConflictError(
            f"event project {event['project_id']!r} does not match log project {project_id!r}"
        )
    if event["expected_prior_revision"] != len(current):
        raise RevisionConflictError(
            f"expected prior revision {event['expected_prior_revision']}, current revision is {len(current)}"
        )
    return current + [_copy_json(event)]


def _subject_tuple(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        event["project_id"], event["scope"]["kind"], event["scope"]["id"],
        event["subject_id"], event["authority"]["authority_role"],
    )


def _subject_key(key: tuple[str, str, str, str, str]) -> str:
    return canonical_sha256(list(key))


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _subject_tuple(left) != _subject_tuple(right) or left["subject_type"] != right["subject_type"]:
        return False
    left_class = str(left["authority"]["authority_class"])
    right_class = str(right["authority"]["authority_class"])
    left_rank = _AUTHORITY_PRECEDENCE.get(left_class, -1)
    right_rank = _AUTHORITY_PRECEDENCE.get(right_class, -1)
    same_actor = left["authority"]["actor_id"] == right["authority"]["actor_id"]
    return left_rank > right_rank or (same_actor and left_rank >= right_rank)


def _safe_self_report(event: dict[str, Any]) -> bool:
    return (
        event["authority"]["authority_class"] == "AGENT"
        and event["subject_type"] in {"OPERATIONAL", "DECISION"}
        and event["scope"]["kind"] in {"TASK", "RUN"}
    )


def _authority_is_trusted(event: dict[str, Any], verified_ids: set[str]) -> bool:
    return event["event_id"] in verified_ids or _safe_self_report(event)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _normal_time(value: str) -> str:
    return _utc(value).isoformat()


def _freshness_deadline(events: list[dict[str, Any]]) -> str | None:
    values: list[datetime] = []
    for event in events:
        if event.get("expires_at") is not None:
            values.append(_utc(event["expires_at"]))
        for evidence in event.get("evidence_refs", []):
            if evidence.get("expires_at") is not None:
                values.append(_utc(evidence["expires_at"]))
    return min(values).isoformat() if values else None


def _merged_evidence(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for event in events:
        for evidence in event.get("evidence_refs", []):
            eid = evidence["evidence_id"]
            prior = by_id.get(eid)
            if prior is not None and canonical_json(prior) != canonical_json(evidence):
                problems.append(f"evidence_id {eid!r} has conflicting metadata")
            else:
                by_id[eid] = deepcopy(evidence)
    return [by_id[eid] for eid in sorted(by_id)], sorted(set(problems))


def _union(events: list[dict[str, Any]], field: str) -> list[str]:
    return sorted({item for event in events for item in event.get(field, [])})


def _unresolved(
    code: str, detail: str, event_ids: Iterable[str] = (), subject_key: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "subject_key": subject_key,
        "event_ids": sorted(set(event_ids)),
        "detail": detail,
    }


def _safe_document_path(document_id: str) -> str | None:
    """Return a deterministic relative path only when an id is path-like.

    Document ids are allowed to be opaque (for example ``doc:handoff``).  The
    reducer preserves those ids but must never guess that they name a file.
    Path recognition is platform-neutral so replay is byte-identical on Windows
    and POSIX, and excludes traversal, absolute paths, URI-like identifiers,
    Windows-reserved syntax, fragments, and control characters.
    """
    value = document_id.strip()
    if not value or "\\" in value or any(ord(char) < 32 for char in value):
        return None
    if any(char in value for char in '<>:"|?*#'):
        return None
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in raw_parts):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        return None
    # A slash or a filename suffix is the minimum evidence that this is a path
    # rather than an opaque identifier.  No extension allow-list is guessed.
    if "/" not in value and not path.suffix:
        return None
    return value


def _materialize_inventory(
    accepted: dict[str, dict[str, Any]],
    accepted_revision: dict[str, int],
    history_by_id: dict[str, dict[str, Any]],
    *,
    source_field: str,
    id_field: str,
    include_path: bool,
) -> list[dict[str, Any]]:
    """Reduce affected ids using explicit accepted-event lifecycle history."""
    occurrences: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for event_id in sorted(accepted, key=lambda item: accepted_revision[item]):
        event = accepted[event_id]
        row = history_by_id[event_id]
        if row.get("reason") == _REJECTED_AUTHORITY_REASON:
            continue
        for item_id in event[source_field]:
            occurrences[item_id].append((event, row))

    inventory: list[dict[str, Any]] = []
    for item_id in sorted(occurrences):
        event_rows = occurrences[item_id]
        rows = [row for _event, row in event_rows]
        scope_rank = {"PROJECT": 4, "COMPONENT": 3, "RUN": 2, "TASK": 1}
        highest_scope = max(scope_rank[event["scope"]["kind"]] for event, _row in event_rows)
        controlling = [
            (event, row) for event, row in event_rows
            if scope_rank[event["scope"]["kind"]] == highest_scope
        ]
        by_scope: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for event, row in controlling:
            by_scope[(event["scope"]["kind"], event["scope"]["id"])].append(row)
        scope_statuses: list[str] = []
        for scope_rows in by_scope.values():
            statuses = [str(row["status"]) for row in scope_rows]
            if any(value in {"CONFLICT", "UNRESOLVED"} for value in statuses):
                scope_statuses.append("UNRESOLVED")
            elif "ACTIVE" in statuses:
                scope_statuses.append("ACTIVE")
            else:
                scope_latest = max(scope_rows, key=lambda row: int(row["revision"] or 0))
                value = str(scope_latest["status"])
                scope_statuses.append(
                    value if value in {"SUPERSEDED", "INVALIDATED", "EXPIRED"}
                    else "UNRESOLVED"
                )

        if "UNRESOLVED" in scope_statuses or (
            "ACTIVE" in scope_statuses and any(value != "ACTIVE" for value in scope_statuses)
        ):
            status = "UNRESOLVED"
        elif scope_statuses and all(value == "ACTIVE" for value in scope_statuses):
            status = "ACTIVE"
        else:
            controlling_rows = [row for _event, row in controlling]
            latest = max(controlling_rows, key=lambda row: int(row["revision"] or 0))
            status = str(latest["status"])
            if status not in {"SUPERSEDED", "INVALIDATED", "EXPIRED"}:
                status = "UNRESOLVED"
        latest = max(rows, key=lambda row: int(row["revision"] or 0))
        replacements = sorted({
            str(row["replacement_event_id"])
            for row in rows if row.get("replacement_event_id") not in (None, "")
        })
        reasons = sorted({str(row["reason"]) for row in rows if row.get("reason")})
        if source_field == "affected_capability_ids":
            if status == "ACTIVE":
                status = "UNRESOLVED"
            reasons.append(
                "capability activation requires cryptographically verified receipt; "
                "verification is not integrated"
            )
        item: dict[str, Any] = {
            id_field: item_id,
            "status": status,
            "current": status == "ACTIVE",
            "source_event_ids": [str(row["event_id"]) for row in rows],
            "last_event_id": str(latest["event_id"]),
            "replacement_event_ids": replacements,
            "invalidation_reasons": reasons,
        }
        if include_path:
            path = _safe_document_path(item_id)
            if path is not None:
                item["path"] = path
        inventory.append(item)
    return inventory


def reduce_events(
    events: Iterable[dict[str, Any]],
    *,
    as_of: str,
    verified_authority_event_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Purely reduce one project's append-only events into current state.

    Schema/revision corruption is retained in ``history`` and produces a top-level
    ``UNRESOLVED`` project status.  Current-subject authority conflicts, incompatible
    targets, unknown state, not-yet-valid claims, and expiry likewise materialize as
    explicit unresolved subjects; timestamp recency never selects a winner.
    """
    time_problems: list[str] = []
    instant = _parse_time(as_of, "as_of", time_problems)
    if instant is None:
        raise EventValidationError(f"invalid reducer as_of: {time_problems}")
    normalized_as_of = instant.isoformat()
    log = _copy_json(list(events))
    if isinstance(verified_authority_event_ids, (str, bytes, bytearray)):
        raise EventValidationError("verified_authority_event_ids must be an iterable of event ids")
    raw_verified_ids = list(verified_authority_event_ids)
    if any(not _nonempty(event_id) for event_id in raw_verified_ids):
        raise EventValidationError("verified_authority_event_ids must contain non-empty strings")
    verified_ids = sorted(set(raw_verified_ids))
    verified_id_set = set(verified_ids)

    project_id: str | None = None
    revision = 0
    accepted: dict[str, dict[str, Any]] = {}
    accepted_revision: dict[str, int] = {}
    events_by_key: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    active: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    pending_errors: dict[
        tuple[str, str, str, str, str], dict[str, list[str]]
    ] = defaultdict(dict)
    history: list[dict[str, Any]] = []
    history_by_id: dict[str, dict[str, Any]] = {}
    structural_unresolved: list[dict[str, Any]] = []

    for position, event in enumerate(log):
        event_id = event.get("event_id") if isinstance(event, dict) else None
        display_id = event_id if _nonempty(event_id) else f"invalid-event-at-{position}"
        digest = canonical_sha256(event)
        ok, problems = validate_event(event)
        if not ok:
            history.append({
                "event_id": display_id, "event_sha256": digest, "revision": None,
                "status": "UNRESOLVED", "replacement_event_id": None,
                "reason": "; ".join(problems),
            })
            structural_unresolved.append(_unresolved(
                "INVALID_EVENT", f"event {display_id!r} failed schema validation: {problems}",
                [display_id],
            ))
            continue
        if event_id in accepted:
            history.append({
                "event_id": event_id, "event_sha256": digest, "revision": None,
                "status": "CONFLICT", "replacement_event_id": None,
                "reason": "duplicate event_id in append-only log",
            })
            structural_unresolved.append(_unresolved(
                "DUPLICATE_EVENT_ID", f"event_id {event_id!r} occurs more than once", [event_id],
            ))
            continue
        if project_id is None:
            project_id = event["project_id"]
        if event["project_id"] != project_id:
            history.append({
                "event_id": event_id, "event_sha256": digest, "revision": None,
                "status": "CONFLICT", "replacement_event_id": None,
                "reason": "mixed project identifier in one event log",
            })
            structural_unresolved.append(_unresolved(
                "MIXED_PROJECTS",
                f"event project {event['project_id']!r} differs from {project_id!r}", [event_id],
            ))
            continue
        if event["expected_prior_revision"] != revision:
            history.append({
                "event_id": event_id, "event_sha256": digest, "revision": None,
                "status": "CONFLICT", "replacement_event_id": None,
                "reason": (
                    f"expected prior revision {event['expected_prior_revision']}, "
                    f"accepted revision is {revision}"
                ),
            })
            structural_unresolved.append(_unresolved(
                "REVISION_CONFLICT",
                f"event {event_id!r} was not accepted at revision {revision}", [event_id],
            ))
            continue

        revision += 1
        accepted[event_id] = event
        accepted_revision[event_id] = revision
        key = _subject_tuple(event)
        skey = _subject_key(key)
        events_by_key[key].append(event)
        row = {
            "event_id": event_id, "event_sha256": digest, "revision": revision,
            "status": "ACTIVE", "replacement_event_id": None, "reason": None,
        }
        history.append(row)
        history_by_id[event_id] = row

        trusted_current_exists = any(
            _authority_is_trusted(candidate, verified_id_set)
            for candidate in active[key].values()
        )
        if not _authority_is_trusted(event, verified_id_set) and trusted_current_exists:
            row.update({"status": "UNRESOLVED", "reason": _REJECTED_AUTHORITY_REASON})
            continue

        target_ids = list(event["supersedes"]) + list(event["invalidates"])
        target_problems: list[str] = []
        for target_id in target_ids:
            target = accepted.get(target_id)
            if target is None:
                target_problems.append(f"target event {target_id!r} is unknown or not prior")
            elif not _compatible(event, target):
                target_problems.append(
                    f"target event {target_id!r} has incompatible type or scope, "
                    "or lower authority precedence"
                )
        if target_problems:
            row["status"] = "CONFLICT"
            row["reason"] = "; ".join(target_problems)
            pending_errors[key][event_id] = target_problems
            continue

        for target_id in event["supersedes"]:
            target = accepted[target_id]
            target_key = _subject_tuple(target)
            active[target_key].pop(target_id, None)
            pending_errors[target_key].pop(target_id, None)
            target_row = history_by_id[target_id]
            target_row.update({
                "status": "SUPERSEDED", "replacement_event_id": event_id,
                "reason": f"explicitly superseded by {event_id}",
            })
        for target_id in event["invalidates"]:
            target = accepted[target_id]
            target_key = _subject_tuple(target)
            active[target_key].pop(target_id, None)
            pending_errors[target_key].pop(target_id, None)
            target_row = history_by_id[target_id]
            target_row.update({
                "status": "INVALIDATED", "replacement_event_id": event_id,
                "reason": f"explicitly invalidated by {event_id}",
            })
        active[key][event_id] = event

    unknown_verified_ids = sorted(set(verified_ids) - set(accepted))
    for event_id in unknown_verified_ids:
        structural_unresolved.append(_unresolved(
            "UNKNOWN_VERIFIED_AUTHORITY_EVENT",
            f"authority verification references event {event_id!r}, which is not accepted history",
            [event_id],
        ))
    accepted_verified_ids = sorted(set(verified_ids) & set(accepted))

    subjects: list[dict[str, Any]] = []
    current_unresolved = list(structural_unresolved)
    all_keys = set(events_by_key) | set(active) | set(pending_errors)
    for key in sorted(all_keys):
        skey = _subject_key(key)
        candidates = [active[key][eid] for eid in sorted(active[key])]
        pending = pending_errors[key]
        contributing = candidates + [
            event for event in events_by_key[key]
            if event["event_id"] in pending and event["event_id"] not in active[key]
        ]
        if not contributing:
            continue
        base = max(contributing, key=lambda event: accepted_revision[event["event_id"]])
        reasons = [reason for event_reasons in pending.values() for reason in event_reasons]
        if len(candidates) > 1:
            reasons.append(
                "multiple active authority events exist without explicit compatible supersession: "
                + ", ".join(sorted(event["event_id"] for event in candidates))
            )

        for candidate in candidates:
            if (
                candidate["event_id"] not in accepted_verified_ids
                and not _safe_self_report(candidate)
            ):
                reasons.append(
                    f"event {candidate['event_id']!r} has no verified authority; only AGENT "
                    "OPERATIONAL/DECISION self-reports at TASK or RUN scope are allowed by default"
                )
            if instant < _utc(candidate["valid_from"]):
                reasons.append(f"event {candidate['event_id']!r} is not yet valid")
                history_by_id[candidate["event_id"]].update({
                    "status": "UNRESOLVED", "reason": "valid_from is later than reducer as_of",
                })
            expired = candidate.get("expires_at") is not None and instant >= _utc(candidate["expires_at"])
            expired_evidence = [
                evidence["evidence_id"] for evidence in candidate["evidence_refs"]
                if evidence.get("expires_at") is not None and instant >= _utc(evidence["expires_at"])
            ]
            if expired:
                reasons.append(f"event {candidate['event_id']!r} expired")
            if expired_evidence:
                reasons.append(
                    f"event {candidate['event_id']!r} has expired evidence: "
                    + ", ".join(sorted(expired_evidence))
                )
            if expired or expired_evidence:
                history_by_id[candidate["event_id"]].update({
                    "status": "EXPIRED", "reason": "event or required evidence expired",
                })
            if candidate["subject_type"] in {"RUNTIME", "CAPABILITY"}:
                reasons.append(
                    f"event {candidate['event_id']!r} has signed-receipt metadata but "
                    "cryptographic receipt verification is not integrated"
                )
        evidence, evidence_problems = _merged_evidence(contributing)
        reasons.extend(evidence_problems)
        if len(candidates) == 1 and candidates[0]["lifecycle_state"] == "UNRESOLVED":
            reasons.append(f"event {candidates[0]['event_id']!r} declares unresolved state")
        reasons = sorted(set(reasons))
        is_unresolved = bool(reasons) or len(candidates) != 1
        current = candidates[0] if len(candidates) == 1 else base
        authority = (
            {
                "actor_id": "cortex:project-state-reducer",
                "authority_class": "DETERMINISTIC_REDUCER",
                "authority_role": key[4],
            }
            if is_unresolved else deepcopy(current["authority"])
        )
        if is_unresolved:
            event_ids = [event["event_id"] for event in contributing]
            current_unresolved.append(_unresolved(
                "SUBJECT_UNRESOLVED", "; ".join(reasons) or "no active authority event",
                event_ids, skey,
            ))
            for candidate in candidates:
                if history_by_id[candidate["event_id"]]["status"] == "ACTIVE":
                    history_by_id[candidate["event_id"]].update({
                        "status": "CONFLICT", "reason": "current authority conflict",
                    })
        subjects.append({
            "subject_key": skey,
            "project_id": key[0],
            "subject_id": key[3],
            "subject_type": current["subject_type"],
            "scope": deepcopy(current["scope"]),
            "authority_role": key[4],
            "lifecycle_state": "UNRESOLVED" if is_unresolved else current["lifecycle_state"],
            "authority_owner": authority,
            "last_accepted_event_id": base["event_id"],
            "last_verification_at": max(
                _normal_time(event["observed_at"]) for event in contributing
            ),
            "evidence_refs": evidence,
            "freshness_deadline": _freshness_deadline(contributing),
            "claims": _union(contributing, "claims") if is_unresolved else sorted(current["claims"]),
            "blockers": sorted(set(_union(contributing, "blockers") + reasons)),
            "next_actions": _union(contributing, "next_actions") if is_unresolved else sorted(current["next_actions"]),
            "affected_document_ids": _union(contributing, "affected_document_ids"),
            "affected_capability_ids": _union(contributing, "affected_capability_ids"),
            "candidate_event_ids": sorted(event["event_id"] for event in contributing),
            "unresolved_reasons": reasons,
        })

    history.sort(key=lambda row: (
        row["revision"] is None,
        row["revision"] if row["revision"] is not None else 10**18,
        row["event_id"], row["event_sha256"],
    ))
    current_unresolved.sort(key=lambda item: (
        item["code"], item["subject_key"] or "", item["event_ids"], item["detail"],
    ))
    if project_id is None:
        project_id = "UNRESOLVED"
        current_unresolved.append(_unresolved(
            "EMPTY_EVENT_LOG", "no stable project identifier exists because the event log is empty",
        ))
    documents = _materialize_inventory(
        accepted, accepted_revision, history_by_id,
        source_field="affected_document_ids", id_field="document_id", include_path=True,
    )
    capabilities = _materialize_inventory(
        accepted, accepted_revision, history_by_id,
        source_field="affected_capability_ids", id_field="capability_id", include_path=False,
    )
    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "reducer_version": REDUCER_VERSION,
        "project_id": project_id,
        "revision": revision,
        "as_of": normalized_as_of,
        "project_status": "UNRESOLVED" if current_unresolved else "RESOLVED",
        "event_log_sha256": canonical_sha256(log),
        "verified_authority_event_ids": accepted_verified_ids,
        "verified_authority_event_ids_sha256": canonical_sha256(accepted_verified_ids),
        "documents": documents,
        "capabilities": capabilities,
        "subjects": subjects,
        "history": history,
        "unresolved": current_unresolved,
    }
    return {**payload, "state_sha256": canonical_sha256(payload)}
