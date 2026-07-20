"""Pure projections of Cortex's reduced project state.

The reducer owns operational truth.  This module only renders that truth for
humans, cold agents, capability/status consumers, and retrieval selection.  It
does not read clocks, inspect signatures, write files, mutate ontology, or mint
assurance.  A storage/reconciliation lane can therefore stage these outputs and
publish them atomically after reduction.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


PROJECTION_SCHEMA_VERSION = 1
GENERATED_MARKER = "cortex:generated-project-state-projection"
_HISTORY_STATES = frozenset({
    "ARCHIVED", "CLOSED", "HISTORY", "INACTIVE", "INVALIDATED", "REPLACED",
    "RETIRED", "SUPERSEDED",
})
_ATTENTION_STATES = frozenset({
    "ABSTAIN", "CONFLICT", "CONFLICTING", "EXPIRED", "STALE", "UNRESOLVED",
})


def _canonical_bytes(value: Any) -> bytes:
    """Canonical JSON bytes; reject non-JSON and non-finite values."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_state(current_state: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(current_state, Mapping):
        raise TypeError("current_state must be a plain mapping")
    # Round-tripping also proves that projection input is a plain JSON mapping
    # and prevents a caller-owned nested object from being returned by reference.
    try:
        state = json.loads(_canonical_bytes(dict(current_state)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"current_state must contain canonical JSON values: {exc}") from exc
    revision = _reducer_revision(state)
    if revision is None:
        raise ValueError("current_state requires a non-empty reducer_version")
    return state


def _reducer_revision(state: Mapping[str, Any]) -> str | None:
    # project-state/v1 calls this ``reducer_version``.  Accept the earlier
    # projection-only spelling as an input alias while emitting the canonical
    # field in every new projection.
    value = state.get("reducer_version", state.get("reducer_revision"))
    if value is None and isinstance(state.get("metadata"), Mapping):
        value = state["metadata"].get(
            "reducer_version", state["metadata"].get("reducer_revision"),
        )
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value)
    return None


def project_state_input_sha256(current_state: Mapping[str, Any]) -> str:
    """Digest of the exact reduced state consumed by every projection."""
    return _sha256(_require_state(current_state))


def _items(value: Any, *, id_field: str = "id") -> list[Any]:
    """Normalize either a list or an id-keyed mapping to a stable list."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        out: list[Any] = []
        for key in sorted(value, key=str):
            item = copy.deepcopy(value[key])
            if isinstance(item, dict) and not any(
                field in item for field in ("id", id_field, "subject_id", "document_id",
                                             "capability_id", "claim_id")
            ):
                item[id_field] = str(key)
            out.append(item)
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return copy.deepcopy(list(value))
    return [copy.deepcopy(value)]


def _stable_key(value: Any) -> tuple[str, str]:
    if isinstance(value, Mapping):
        for field in ("id", "subject_id", "claim_id", "capability_id", "document_id",
                      "run_id", "action_id", "path", "name"):
            if value.get(field) is not None:
                return str(value[field]), _canonical_bytes(value).decode("utf-8")
    return "", _canonical_bytes(value).decode("utf-8")


def _status(value: Mapping[str, Any]) -> str:
    raw = value.get("lifecycle_state", value.get("status", value.get("state", "UNKNOWN")))
    return str(raw).strip().upper() or "UNKNOWN"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_of(state: Mapping[str, Any]) -> datetime | None:
    for field in ("as_of", "reduced_at", "materialized_at"):
        parsed = _parse_time(state.get(field))
        if parsed is not None:
            return parsed
    return None


def _effective_status(item: Mapping[str, Any], state: Mapping[str, Any]) -> str:
    status = _status(item)
    if item.get("expired") is True or item.get("is_expired") is True:
        return "EXPIRED"
    expiry = _parse_time(item.get("expires_at")) or _parse_time(item.get("freshness_deadline"))
    reference = _as_of(state)
    if expiry is not None and reference is not None and expiry <= reference:
        return "EXPIRED"
    if item.get("conflicts") or item.get("conflict_ids"):
        return "CONFLICTING"
    reasons = item.get("unresolved_reasons") or []
    if isinstance(reasons, list) and any(
        "conflict" in str(reason).lower() or "multiple active" in str(reason).lower()
        for reason in reasons
    ):
        return "CONFLICTING"
    return status


def _identifier(item: Any) -> str:
    if isinstance(item, Mapping):
        for field in ("subject_id", "claim_id", "capability_id", "document_id", "run_id",
                      "action_id", "id", "name", "path"):
            if item.get(field) not in (None, ""):
                return str(item[field])
    return str(item)


def _compact_item(item: Any, state: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        return {"id": _identifier(item), "kind": kind, "value": copy.deepcopy(item),
                "effective_status": "UNKNOWN"}
    allowed = (
        "subject_id", "claim_id", "capability_id", "document_id", "run_id", "action_id",
        "subject_key", "subject_type", "source_subject_id", "source_subject_key",
        "id", "name", "path", "scope", "authority_owner", "authority_class", "owner",
        "claim", "claims", "value", "summary", "goal", "task", "reason", "next_action", "action",
        "last_event_id", "last_accepted_event_id", "last_verified_at", "last_verification_at",
        "freshness_deadline", "expires_at",
        "evidence_refs", "evidence_hashes", "conflicts", "conflict_ids", "blocked_by",
        "replacement", "replaced_by", "invalidation_reason", "unresolved_reasons",
        "source_event_ids", "last_event_id", "replacement_event_ids", "invalidation_reasons",
    )
    out = {field: copy.deepcopy(item[field]) for field in allowed if field in item}
    out.setdefault("id", _identifier(item))
    out["kind"] = kind
    out["effective_status"] = _effective_status(item, state)
    return out


def _claims(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    combined = _items(state.get("claims"), id_field="claim_id")
    combined.extend(_items(state.get("subjects"), id_field="subject_id"))
    compact = [_compact_item(item, state, kind="claim") for item in combined]
    # Structural reducer failures may have no current subject.  They still need
    # to be visible to a human and a cold agent instead of disappearing from the
    # friendlier projections.
    for index, unresolved in enumerate(_items(state.get("unresolved"), id_field="id")):
        if isinstance(unresolved, Mapping):
            item = {
                "id": unresolved.get("subject_key") or unresolved.get("code")
                      or f"project-unresolved-{index}",
                "claim": unresolved.get("detail") or unresolved.get("code"),
                "status": "UNRESOLVED",
                "subject_key": unresolved.get("subject_key"),
                "evidence_refs": unresolved.get("event_ids", []),
            }
        else:
            item = {"id": f"project-unresolved-{index}", "claim": unresolved,
                    "status": "UNRESOLVED"}
        compact.append(_compact_item(item, state, kind="claim"))
    return sorted(compact, key=_stable_key)


def _attention_claims(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [claim for claim in _claims(state)
            if claim.get("effective_status") in _ATTENTION_STATES]


def _active_work(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    work = _items(state.get("active_work"), id_field="subject_id")
    work.extend(_items(state.get("active_runs"), id_field="run_id"))
    work.extend(
        subject for subject in _items(state.get("subjects"), id_field="subject_id")
        if isinstance(subject, Mapping)
        and str(subject.get("subject_type", "")).upper() == "OPERATIONAL"
        and _status(subject) != "COMPLETED"
    )
    compact = [_compact_item(item, state, kind="active_work") for item in work]
    return sorted(compact, key=_stable_key)


def _blockers(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = [_compact_item(item, state, kind="blocker")
             for item in _items(state.get("blockers"), id_field="id")]
    for subject in _items(state.get("subjects"), id_field="subject_id"):
        if not isinstance(subject, Mapping):
            continue
        for index, blocker in enumerate(_items(subject.get("blockers"))):
            item = blocker if isinstance(blocker, Mapping) else {"reason": blocker}
            item = {
                **item,
                "id": item.get("id") or f"{_identifier(subject)}:blocker:{index}",
                "source_subject_id": subject.get("subject_id"),
                "source_subject_key": subject.get("subject_key"),
                "subject_type": subject.get("subject_type"),
                "status": item.get("status", subject.get("lifecycle_state", "BLOCKED")),
            }
            items.append(_compact_item(item, state, kind="blocker"))
    return sorted(items, key=_stable_key)


def _next_actions(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = [_compact_item(item, state, kind="next_action")
             for item in _items(state.get("next_actions"), id_field="action_id")]
    for subject in _items(state.get("subjects"), id_field="subject_id"):
        if not isinstance(subject, Mapping):
            continue
        for index, action in enumerate(_items(subject.get("next_actions"))):
            item = action if isinstance(action, Mapping) else {"action": action}
            item = {
                **item,
                "action_id": item.get("action_id") or f"{_identifier(subject)}:action:{index}",
                "source_subject_id": subject.get("subject_id"),
                "source_subject_key": subject.get("subject_key"),
                "subject_type": subject.get("subject_type"),
                "status": item.get("status", "PENDING"),
            }
            items.append(_compact_item(item, state, kind="next_action"))
    return sorted(items, key=_stable_key)


def _derived_documents(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for subject in _items(state.get("subjects"), id_field="subject_id"):
        if not isinstance(subject, Mapping) or str(subject.get("subject_type", "")).upper() != "NORMATIVE":
            continue
        for document_id in _items(subject.get("affected_document_ids")):
            docs.append({
                "document_id": str(document_id),
                "scope": copy.deepcopy(subject.get("scope")),
                "status": subject.get("lifecycle_state", "UNKNOWN"),
                "authority_owner": copy.deepcopy(subject.get("authority_owner")),
                "source_subject_id": subject.get("subject_id"),
                "source_subject_key": subject.get("subject_key"),
                "last_accepted_event_id": subject.get("last_accepted_event_id"),
                "last_verification_at": subject.get("last_verification_at"),
                "freshness_deadline": subject.get("freshness_deadline"),
                "evidence_refs": copy.deepcopy(subject.get("evidence_refs", [])),
                "unresolved_reasons": copy.deepcopy(subject.get("unresolved_reasons", [])),
            })
    return docs


def _derived_capabilities(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for subject in _items(state.get("subjects"), id_field="subject_id"):
        if not isinstance(subject, Mapping) or str(subject.get("subject_type", "")).upper() not in {
            "CAPABILITY", "RUNTIME",
        }:
            continue
        ids = _items(subject.get("affected_capability_ids")) or [subject.get("subject_id")]
        for capability_id in ids:
            capabilities.append({
                "capability_id": str(capability_id),
                "status": subject.get("lifecycle_state", "UNKNOWN"),
                "summary": "; ".join(str(value) for value in subject.get("claims", [])),
                "scope": copy.deepcopy(subject.get("scope")),
                "authority_owner": copy.deepcopy(subject.get("authority_owner")),
                "source_subject_id": subject.get("subject_id"),
                "source_subject_key": subject.get("subject_key"),
                "last_accepted_event_id": subject.get("last_accepted_event_id"),
                "last_verification_at": subject.get("last_verification_at"),
                "freshness_deadline": subject.get("freshness_deadline"),
                "evidence_refs": copy.deepcopy(subject.get("evidence_refs", [])),
                "unresolved_reasons": copy.deepcopy(subject.get("unresolved_reasons", [])),
                "blocked_by": copy.deepcopy(subject.get("blockers", [])),
            })
    return capabilities


def _merge_inventory(
    explicit: Sequence[Any], derived: Sequence[Any], *, id_field: str,
) -> list[Any]:
    """Merge subject context without overriding explicit lifecycle history.

    Canonical top-level inventories own status/current/history. Subject-derived
    rows can enrich missing context, or provide the whole row for older states,
    but cannot duplicate or reactivate an explicitly historical item.
    """
    merged: dict[str, Any] = {}
    anonymous: dict[str, Any] = {}

    def add(item: Any) -> None:
        copied = copy.deepcopy(item)
        if not isinstance(copied, Mapping) or copied.get(id_field) in (None, ""):
            anonymous.setdefault(_canonical_bytes(copied).decode("utf-8"), copied)
            return
        key = str(copied[id_field])
        if key not in merged:
            merged[key] = dict(copied)
            return
        current = merged[key]
        for field, value in copied.items():
            if field not in current or current[field] in (None, "", [], {}):
                current[field] = copy.deepcopy(value)

    for item in explicit:
        add(item)
    for item in derived:
        add(item)
    return [merged[key] for key in sorted(merged)] + [anonymous[key] for key in sorted(anonymous)]


def _locked_outcome(state: Mapping[str, Any]) -> Any:
    if state.get("locked_outcome") is not None:
        return copy.deepcopy(state.get("locked_outcome"))
    claims = [
        {"subject_id": subject.get("subject_id"), "claims": copy.deepcopy(subject.get("claims", []))}
        for subject in _items(state.get("subjects"), id_field="subject_id")
        if isinstance(subject, Mapping)
        and str(subject.get("subject_type", "")).upper() == "NORMATIVE"
        and _scope_kind(subject.get("scope")) == "project"
        and _status(subject) == "ACTIVE"
    ]
    return claims or None


def _active_goal(state: Mapping[str, Any]) -> Any:
    if state.get("active_goal", state.get("goal")) is not None:
        return copy.deepcopy(state.get("active_goal", state.get("goal")))
    goals = [
        {"subject_id": subject.get("subject_id"), "claims": copy.deepcopy(subject.get("claims", []))}
        for subject in _items(state.get("subjects"), id_field="subject_id")
        if isinstance(subject, Mapping)
        and str(subject.get("subject_type", "")).upper() == "OPERATIONAL"
        and _scope_kind(subject.get("scope")) == "project"
        and _status(subject) != "COMPLETED"
    ]
    return goals or None


def _scope_kind(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("kind", "")).lower()
    return str(value or "").lower()


def _evidence_hashes(state: Mapping[str, Any]) -> dict[str, str]:
    hashes = {
        str(key): str(value)
        for key, value in (state.get("evidence_hashes") or {}).items()
    } if isinstance(state.get("evidence_hashes"), Mapping) else {}
    for subject in _items(state.get("subjects"), id_field="subject_id"):
        if not isinstance(subject, Mapping):
            continue
        for evidence in _items(subject.get("evidence_refs"), id_field="evidence_id"):
            if isinstance(evidence, Mapping) and evidence.get("sha256"):
                evidence_id = evidence.get("evidence_id") or evidence.get("uri")
                if evidence_id:
                    hashes[str(evidence_id)] = str(evidence["sha256"])
    return dict(sorted(hashes.items()))


def _doc_is_history(doc: Mapping[str, Any]) -> bool:
    if doc.get("active") is False or doc.get("current") is False:
        return True
    if _status(doc) in _HISTORY_STATES:
        return True
    return any(doc.get(field) not in (None, "", [], {}) for field in (
        "invalidated_at", "invalidated_by", "invalidation_reason", "replaced_by",
        "superseded_by",
    ))


def _document_projection(doc: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_item(doc, state, kind="document")
    history = isinstance(doc, Mapping) and _doc_is_history(doc)
    compact["selection_status"] = "HISTORY" if history else "ACTIVE"
    compact["current"] = not history
    if history:
        compact.setdefault("invalidation_reason", "marked non-current by reduced state")
    return compact


def select_projection_documents(
    current_state: Mapping[str, Any], *, include_history: bool = False,
) -> dict[str, Any]:
    """Select current documents; history is opt-in and explicitly labeled.

    Recency is intentionally ignored.  Only reduced lifecycle/invalidation fields
    decide whether a document is active.
    """
    state = _require_state(current_state)
    docs = _merge_inventory(
        _items(state.get("documents"), id_field="document_id")
        + _items(state.get("normative_documents"), id_field="document_id")
        + _items(state.get("status_documents"), id_field="document_id"),
        _derived_documents(state),
        id_field="document_id",
    )
    projected = sorted((_document_projection(doc, state) for doc in docs), key=_stable_key)
    active = [doc for doc in projected if doc["selection_status"] == "ACTIVE"]
    history = [doc for doc in projected if doc["selection_status"] == "HISTORY"]
    selected = active + history if include_history else active
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "generated_projection": True,
        "selection_mode": "ACTIVE_AND_HISTORY" if include_history else "ACTIVE_ONLY",
        "documents": selected,
        "active_count": len(active),
        "history_included_count": len(history) if include_history else 0,
        "history_excluded_count": 0 if include_history else len(history),
        "assurance_minted": False,
    }


def build_agent_resume_pack(current_state: Mapping[str, Any]) -> dict[str, Any]:
    """Minimal cold-start context; never returns the complete corpus or event log."""
    state = _require_state(current_state)
    docs = select_projection_documents(state)["documents"]
    active_docs = [doc for doc in docs if _scope_kind(doc.get("scope")) in {
        "", "project", "normative", "status", "operational",
    }]
    actions = _next_actions(state)
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "generated_projection": True,
        "project_id": state.get("project_id"),
        "project_revision": state.get("project_revision", state.get("revision")),
        "reducer_version": _reducer_revision(state),
        "reducer_revision": _reducer_revision(state),
        "input_sha256": _sha256(state),
        "locked_outcome": _locked_outcome(state),
        "active_goal": _active_goal(state),
        "active_work": _active_work(state),
        "blockers": _blockers(state),
        "attention_claims": _attention_claims(state),
        "next_safe_action": actions[0] if actions else None,
        "current_documents": active_docs,
        "evidence_hashes": _evidence_hashes(state),
        "assurance_minted": False,
        "notice": "Projection only; authority and assurance remain with reduced state and receipts.",
    }


def build_capability_status_projection(current_state: Mapping[str, Any]) -> dict[str, Any]:
    """Project reduced capability claims without qualifying or routing a model."""
    state = _require_state(current_state)
    capability_items = _merge_inventory(
        _items(state.get("capabilities"), id_field="capability_id"),
        _derived_capabilities(state),
        id_field="capability_id",
    )
    capabilities = sorted(
        [_compact_item(item, state, kind="capability")
         for item in capability_items],
        key=_stable_key,
    )
    counts: dict[str, int] = {}
    for capability in capabilities:
        status = str(capability["effective_status"])
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "generated_projection": True,
        "project_id": state.get("project_id"),
        "reducer_version": _reducer_revision(state),
        "reducer_revision": _reducer_revision(state),
        "input_sha256": _sha256(state),
        "capabilities": capabilities,
        "status_counts": dict(sorted(counts.items())),
        "blockers": _blockers(state),
        "attention_claims": _attention_claims(state),
        "assurance_minted": False,
        "notice": "Status projection only; it cannot qualify, route, sign, or certify capability.",
    }


def _md_value(value: Any) -> str:
    if value is None or value == "":
        return "Not recorded"
    if isinstance(value, str):
        return value.strip() or "Not recorded"
    return _canonical_bytes(value).decode("utf-8")


def _md_items(items: Sequence[Mapping[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    lines: list[str] = []
    for item in items:
        label = _identifier(item)
        status = item.get("effective_status", "UNKNOWN")
        detail = item.get("summary", item.get("reason", item.get(
            "claim", item.get("claims", item.get("action")),
        )))
        suffix = f" — {_md_value(detail)}" if detail not in (None, "") else ""
        lines.append(f"- `{label}` [{status}]{suffix}")
    return lines


def render_human_handoff(current_state: Mapping[str, Any]) -> str:
    """Human-readable current handoff, byte-stable for the same input mapping."""
    state = _require_state(current_state)
    actions = _next_actions(state)
    documents = select_projection_documents(state)["documents"]
    lines = [
        f"<!-- {GENERATED_MARKER} -->",
        "# Current project handoff",
        "",
        "> Generated from reduced operational state. Do not hand-edit. This projection does not ",
        "> mint assurance or replace the event log, evidence, signatures, or normative contracts.",
        "",
        f"- Project: `{_md_value(state.get('project_id'))}`",
        f"- Project revision: `{_md_value(state.get('project_revision', state.get('revision')))}`",
        f"- Reducer revision: `{_reducer_revision(state)}`",
        f"- Projection input SHA-256: `{_sha256(state)}`",
        "",
        "## Locked outcome",
        "",
        _md_value(_locked_outcome(state)),
        "",
        "## Active goal",
        "",
        _md_value(_active_goal(state)),
        "",
        "## Active work",
        "",
        *_md_items(_active_work(state), empty="No active work recorded."),
        "",
        "## Blockers",
        "",
        *_md_items(_blockers(state), empty="No blockers recorded."),
        "",
        "## Unresolved, conflicting, stale, or expired claims",
        "",
        *_md_items(_attention_claims(state), empty="No attention claims recorded."),
        "",
        "## Next safe action",
        "",
        *(_md_items(actions[:1], empty="No next action recorded.")),
        "",
        "## Current documents",
        "",
        *_md_items(documents, empty="No active documents recorded."),
        "",
    ]
    return "\n".join(lines)


def build_projection_metadata(
    current_state: Mapping[str, Any], *, output_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    state = _require_state(current_state)
    hashes = dict(sorted((str(k), str(v)) for k, v in (output_hashes or {}).items()))
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "generated_projection": True,
        "generated_marker": GENERATED_MARKER,
        "project_id": state.get("project_id"),
        "project_revision": state.get("project_revision", state.get("revision")),
        "reducer_version": _reducer_revision(state),
        "reducer_revision": _reducer_revision(state),
        "input_sha256": _sha256(state),
        "output_sha256": hashes,
        "history_default": "EXCLUDED",
        "assurance_minted": False,
        "ontology_certified": False,
    }


def render_projection_bundle(
    current_state: Mapping[str, Any], *, include_history: bool = False,
) -> dict[str, Any]:
    """Build all projection content without filesystem or external side effects."""
    state = _require_state(current_state)
    outputs: dict[str, Any] = {
        "HANDOFF.md": render_human_handoff(state),
        "agent-resume-pack.json": build_agent_resume_pack(state),
        "capability-status.json": build_capability_status_projection(state),
        "documents.json": select_projection_documents(state, include_history=include_history),
    }
    hashes = {
        name: hashlib.sha256(
            value.encode("utf-8") if isinstance(value, str) else _canonical_bytes(value)
        ).hexdigest()
        for name, value in outputs.items()
    }
    outputs["projection-metadata.json"] = build_projection_metadata(state, output_hashes=hashes)
    return outputs


__all__ = [
    "GENERATED_MARKER",
    "PROJECTION_SCHEMA_VERSION",
    "build_agent_resume_pack",
    "build_capability_status_projection",
    "build_projection_metadata",
    "project_state_input_sha256",
    "render_human_handoff",
    "render_projection_bundle",
    "select_projection_documents",
]
