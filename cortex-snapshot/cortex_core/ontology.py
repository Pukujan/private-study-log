"""Phase 7 / ROADMAP Phase-4: the **living ontology** -- a self-maintaining
structured knowledge graph that IS the current state of the project, replacing
accreting markdown.

The concrete question this answers is "which doc / rubric / gap is CURRENT?" --
the one that handoff-prose proliferation kept getting wrong. Instead of reading
five overlapping markdown files and guessing which supersedes which, an agent
queries the graph: entities carry a status, and a `supersedes` edge makes the
replacement explicit and auditable.

Storage mirrors the task ledger's substrate (``cortex_core/task_ledger.py``):
plain **append-only JSONL**, no service.

    docs/ontology/entities.jsonl   -- one entity state-snapshot per line
    docs/ontology/relations.jsonl  -- one relation state-snapshot per line

The CURRENT graph is the reduction of those logs -- the last record written for
each id wins (records are appended under a lock, so the final line is the live
truth), exactly the collapse the ledger does. A relation is *live* only while
``status == "active"`` and ``invalid_from is None``: a superseded edge is
**invalidated, not deleted** (the Graphiti bi-temporal move, ROADMAP Stage C),
so the history stays auditable rather than lost.

The schema (``docs/ontology/schema.yaml``) declares the allowed entity types,
the allowed relation predicates, and each predicate's subject/object type
constraints. ``validate_entity`` / ``validate_relation`` enforce it, and every
entity must cite a real ``source_path`` -- provenance, not trust, the same
discipline patterns and contracts already carry.

Concurrency reuses the ledger's exclusive-create lock helpers rather than a
third copy of that critical section, so there is ONE lock discipline in the
codebase, not three.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uuid6
import yaml

from .config import make_stdio_encoding_safe, resolve_exact_workspace, resolve_workspace

# Reuse the ledger's exclusive-create lock (O_CREAT|O_EXCL, stale-steal on a
# dead PID) instead of duplicating the critical section a third time -- one lock
# discipline for the whole corpus (search rebuild lock -> ledger -> here).
from .task_ledger import _acquire_lock, _lock_path, _release_lock

ONTOLOGY_DIRNAME = "docs/ontology"
_WILDCARD = "*"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def ontology_dir(workspace: str | Path | None = None) -> Path:
    # Env-first for an OMITTED workspace (backward-compatible), but an EXPLICIT path must win
    # over the ambient CORTEX_WORKSPACE pin -- otherwise the dual-plane brain path that
    # `mcp._read_ws` already resolved is silently re-resolved back to the env pin here, and an
    # explicit `cortex_ontology_query(workspace=...)` reads the wrong corpus (same re-resolution
    # trap CortexSearchIndex already fixed with resolve_exact_workspace).
    root = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
    return root / "docs" / "ontology"


def schema_path(workspace: str | Path | None = None) -> Path:
    return ontology_dir(workspace) / "schema.yaml"


def entities_path(workspace: str | Path | None = None) -> Path:
    return ontology_dir(workspace) / "entities.jsonl"


def relations_path(workspace: str | Path | None = None) -> Path:
    return ontology_dir(workspace) / "relations.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "x"


def make_entity_id(entity_type: str, name: str) -> str:
    """Deterministic id ``<type>:<slug(name)>`` -- so re-seeding the same source
    upserts the same node rather than minting a duplicate (idempotent sync)."""
    return f"{entity_type}:{_slug(name)}"


def new_relation_id() -> str:
    # Edges have no natural key (many predicates between the same pair are
    # legitimate), so mint a time-sortable, collision-free id like the ledger.
    return f"rel-{uuid6.uuid7()}"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Schema:
    schema_version: int
    status_values: tuple[str, ...]
    entity_types: dict[str, dict[str, Any]]
    relation_types: dict[str, dict[str, Any]]

    def subject_types(self, predicate: str) -> list[str]:
        return list(self.relation_types.get(predicate, {}).get("subject_types", []))

    def object_types(self, predicate: str) -> list[str]:
        return list(self.relation_types.get(predicate, {}).get("object_types", []))


def load_schema(workspace: str | Path | None = None) -> Schema:
    raw = yaml.safe_load(schema_path(workspace).read_text(encoding="utf-8"))
    return Schema(
        schema_version=int(raw.get("schema_version", 1)),
        status_values=tuple(raw.get("status_values", ["active"])),
        entity_types=dict(raw.get("entity_types", {})),
        relation_types=dict(raw.get("relation_types", {})),
    )


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Entity:
    entity_id: str
    type: str
    name: str
    status: str = "active"
    summary: str = ""
    aliases: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    author_model: str = ""
    created_at: str = ""
    updated_at: str = ""
    event: str = "upsert"
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Relation:
    relation_id: str
    subject: str  # entity_id
    predicate: str
    object: str  # entity_id
    status: str = "active"
    valid_from: str = ""
    invalid_from: str | None = None  # bi-temporal: set => edge no longer live
    summary: str = ""
    source_paths: list[str] = field(default_factory=list)
    author_model: str = ""
    created_at: str = ""
    updated_at: str = ""
    event: str = "assert"
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Relation":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Append-only log read/reduce (mirrors task_ledger._read_records/_current_state)
# ---------------------------------------------------------------------------
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line (crash mid-append) must not poison reads.
                continue
    return out


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_entities(workspace: str | Path | None = None) -> dict[str, Entity]:
    """Current entity state: the last record written per entity_id wins."""
    state: dict[str, Entity] = {}
    for rec in _read_jsonl(entities_path(workspace)):
        eid = rec.get("entity_id")
        if eid:
            state[eid] = Entity.from_dict(rec)
    return state


def load_relations(workspace: str | Path | None = None) -> dict[str, Relation]:
    """Current relation state: the last record written per relation_id wins."""
    state: dict[str, Relation] = {}
    for rec in _read_jsonl(relations_path(workspace)):
        rid = rec.get("relation_id")
        if rid:
            state[rid] = Relation.from_dict(rec)
    return state


def _relation_is_live(rel: Relation) -> bool:
    return rel.status == "active" and rel.invalid_from is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_entity(
    entity: Entity, schema: Schema, workspace: str | Path | None = None
) -> tuple[bool, list[str]]:
    """An entity is well-formed only with a known type, a known status, a
    non-empty name, and at least one source_path that resolves to a real file
    inside the workspace (provenance, not trust -- the same rule patterns and
    contracts carry)."""
    ws = resolve_workspace(workspace)
    errors: list[str] = []
    if entity.type not in schema.entity_types:
        errors.append(f"unknown entity type {entity.type!r} (schema: {sorted(schema.entity_types)})")
    if entity.status not in schema.status_values:
        errors.append(f"unknown status {entity.status!r} (schema: {list(schema.status_values)})")
    if not entity.name.strip():
        errors.append("name is empty")
    expected = make_entity_id(entity.type, entity.name)
    if entity.entity_id != expected:
        errors.append(f"entity_id {entity.entity_id!r} != canonical {expected!r} for this type+name")
    if not entity.source_paths:
        errors.append("source_paths is empty (an entity must cite where it comes from)")
    for ref in entity.source_paths:
        target = (ws / ref).resolve()
        if not target.is_relative_to(ws.resolve()):
            errors.append(f"source_path escapes the workspace: {ref}")
        elif not target.exists():
            errors.append(f"source_path does not resolve to a real file/dir: {ref}")
    return (not errors, errors)


def _type_allowed(entity_type: str, allowed: list[str]) -> bool:
    return _WILDCARD in allowed or entity_type in allowed


def validate_relation(
    relation: Relation,
    schema: Schema,
    entities: dict[str, Entity],
    workspace: str | Path | None = None,
) -> tuple[bool, list[str]]:
    """A relation is well-formed only with a known predicate, both endpoints
    present in the current entity state, endpoint types allowed by the
    predicate's schema constraints, no self-loop, and a known status. Referential
    wholeness is enforced -- an edge to an unknown entity is rejected."""
    errors: list[str] = []
    if relation.predicate not in schema.relation_types:
        errors.append(f"unknown predicate {relation.predicate!r} (schema: {sorted(schema.relation_types)})")
    if relation.status not in schema.status_values:
        errors.append(f"unknown status {relation.status!r}")
    if relation.subject == relation.object:
        errors.append("self-loop: subject and object are the same entity")
    subj = entities.get(relation.subject)
    obj = entities.get(relation.object)
    if subj is None:
        errors.append(f"subject {relation.subject!r} is not a known entity")
    if obj is None:
        errors.append(f"object {relation.object!r} is not a known entity")
    if relation.predicate in schema.relation_types and subj is not None and obj is not None:
        allowed_s = schema.subject_types(relation.predicate)
        allowed_o = schema.object_types(relation.predicate)
        if not _type_allowed(subj.type, allowed_s):
            errors.append(f"predicate {relation.predicate!r} forbids subject type {subj.type!r} (allowed: {allowed_s})")
        if not _type_allowed(obj.type, allowed_o):
            errors.append(f"predicate {relation.predicate!r} forbids object type {obj.type!r} (allowed: {allowed_o})")
    return (not errors, errors)


# ---------------------------------------------------------------------------
# Writes (locked read-check-append, like the ledger)
# ---------------------------------------------------------------------------
def upsert_entity(
    entity_type: str,
    name: str,
    *,
    summary: str = "",
    status: str = "active",
    aliases: list[str] | None = None,
    source_paths: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    author_model: str = "",
    workspace: str | Path | None = None,
    schema: Schema | None = None,
) -> dict[str, Any]:
    """Add or update an entity. The id is derived from type+name, so calling
    this again for the same source updates rather than duplicates. Validates
    against the schema before appending; a stamped ``created_at`` is preserved
    across updates."""
    ws = resolve_workspace(workspace)
    sch = schema or load_schema(ws)
    eid = make_entity_id(entity_type, name)
    path = entities_path(ws)
    lock = _acquire_lock(_lock_path(path))
    if lock is None:
        return {"ok": False, "errors": ["could not acquire ontology lock"], "entity_id": eid}
    try:
        existing = load_entities(ws).get(eid)
        now = _now()
        entity = Entity(
            entity_id=eid,
            type=entity_type,
            name=name,
            status=status,
            summary=summary,
            aliases=list(aliases or []),
            source_paths=list(source_paths or []),
            attributes=dict(attributes or {}),
            author_model=author_model or (existing.author_model if existing else ""),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            event="update" if existing else "create",
        )
        ok, errors = validate_entity(entity, sch, ws)
        if not ok:
            return {"ok": False, "errors": errors, "entity_id": eid}
        _append_jsonl(path, entity.to_dict())
        return {"ok": True, "entity_id": eid, "event": entity.event, "entity": entity.to_dict()}
    finally:
        _release_lock(lock)


def assert_relation(
    subject: str,
    predicate: str,
    object: str,  # noqa: A002 - mirrors (subject, predicate, object) triple naming
    *,
    summary: str = "",
    status: str = "active",
    source_paths: list[str] | None = None,
    author_model: str = "",
    relation_id: str | None = None,
    workspace: str | Path | None = None,
    schema: Schema | None = None,
) -> dict[str, Any]:
    """Assert a (subject, predicate, object) edge. Validates that both endpoints
    exist and the predicate permits their types before appending. Passing an
    existing ``relation_id`` updates that edge in place (same reduce-by-last-line
    rule)."""
    ws = resolve_workspace(workspace)
    sch = schema or load_schema(ws)
    path = relations_path(ws)
    lock = _acquire_lock(_lock_path(path))
    if lock is None:
        return {"ok": False, "errors": ["could not acquire ontology lock"], "relation_id": relation_id}
    try:
        entities = load_entities(ws)
        existing = load_relations(ws).get(relation_id or "") if relation_id else None
        now = _now()
        relation = Relation(
            relation_id=relation_id or new_relation_id(),
            subject=subject,
            predicate=predicate,
            object=object,
            status=status,
            valid_from=existing.valid_from if existing else now,
            invalid_from=existing.invalid_from if existing else None,
            summary=summary,
            source_paths=list(source_paths or []),
            author_model=author_model or (existing.author_model if existing else ""),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            event="update" if existing else "assert",
        )
        ok, errors = validate_relation(relation, sch, entities, ws)
        if not ok:
            return {"ok": False, "errors": errors, "relation_id": relation.relation_id}
        _append_jsonl(path, relation.to_dict())
        return {"ok": True, "relation_id": relation.relation_id, "event": relation.event,
                "relation": relation.to_dict()}
    finally:
        _release_lock(lock)


def invalidate_relation(
    relation_id: str, reason: str, *, workspace: str | Path | None = None
) -> dict[str, Any]:
    """Bi-temporal invalidation (Stage C): stamp ``invalid_from`` and set the
    status to ``superseded`` so the edge stops being live, WITHOUT deleting it --
    the history stays auditable. The append-only log keeps every prior state."""
    ws = resolve_workspace(workspace)
    path = relations_path(ws)
    lock = _acquire_lock(_lock_path(path))
    if lock is None:
        return {"ok": False, "reason": "could not acquire ontology lock", "relation_id": relation_id}
    try:
        current = load_relations(ws).get(relation_id)
        if current is None:
            return {"ok": False, "reason": "no such relation", "relation_id": relation_id}
        now = _now()
        record = current.to_dict()
        record.update(
            status="superseded",
            invalid_from=now,
            updated_at=now,
            event="invalidate",
            summary=(current.summary + f" [invalidated: {reason}]").strip(),
        )
        _append_jsonl(path, record)
        return {"ok": True, "relation_id": relation_id, "invalid_from": now}
    finally:
        _release_lock(lock)


def supersede_entity(
    old_entity_id: str,
    new_entity_id: str,
    *,
    reason: str = "",
    author_model: str = "",
    source_paths: list[str] | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Mark ``old`` superseded by ``new`` -- the canonical "this is no longer
    current" move. Flips the old entity's status to ``superseded`` AND records a
    ``supersedes`` edge new -> old, so both the node status and the graph agree
    on what replaced what. This is what makes "which is current" answerable."""
    ws = resolve_workspace(workspace)
    entities = load_entities(ws)
    old = entities.get(old_entity_id)
    new = entities.get(new_entity_id)
    if old is None:
        return {"ok": False, "errors": [f"unknown old entity {old_entity_id!r}"]}
    if new is None:
        return {"ok": False, "errors": [f"unknown new entity {new_entity_id!r}"]}
    # Reuse the old entity's provenance if the caller gives none -- superseding
    # doesn't invent a new source.
    up = upsert_entity(
        old.type, old.name, summary=old.summary, status="superseded",
        aliases=old.aliases, source_paths=old.source_paths, attributes=old.attributes,
        author_model=old.author_model, workspace=ws,
    )
    if not up.get("ok"):
        return up
    rel = assert_relation(
        new_entity_id, "supersedes", old_entity_id,
        summary=reason, source_paths=source_paths or new.source_paths,
        author_model=author_model, workspace=ws,
    )
    return {"ok": rel.get("ok", False), "entity": up, "relation": rel}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def resolve_entity(
    ref: str, workspace: str | Path | None = None, entities: dict[str, Entity] | None = None
) -> Entity | None:
    """Resolve a reference to a single entity by exact id, exact name, or alias
    (Stage B canonicalization). Id wins; then a unique name/alias match."""
    ents = entities if entities is not None else load_entities(workspace)
    if ref in ents:
        return ents[ref]
    lowered = ref.lower()
    matches = [
        e for e in ents.values()
        if e.name.lower() == lowered or lowered in [a.lower() for a in e.aliases]
    ]
    return matches[0] if len(matches) == 1 else None


def find_entities(
    *, type: str | None = None, status: str | None = None,  # noqa: A002
    name_contains: str | None = None, workspace: str | Path | None = None,
) -> list[Entity]:
    """Filter the current entity set by type / status / name substring. Sorted by
    id for a stable order."""
    ents = load_entities(workspace).values()
    out = []
    for e in ents:
        if type is not None and e.type != type:
            continue
        if status is not None and e.status != status:
            continue
        if name_contains is not None and name_contains.lower() not in e.name.lower():
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.entity_id)


def neighbors(
    entity_id: str, *, predicate: str | None = None, direction: str = "both",
    include_invalid: bool = False, workspace: str | Path | None = None,
) -> list[dict[str, Any]]:
    """One-hop graph traversal from an entity. ``direction`` is out (edges where
    it is subject), in (object), or both. Live edges only unless
    ``include_invalid``. Each result names the edge, the neighbor, and which way
    it points."""
    rels = load_relations(workspace).values()
    ents = load_entities(workspace)
    out: list[dict[str, Any]] = []
    for r in rels:
        if not include_invalid and not _relation_is_live(r):
            continue
        if predicate is not None and r.predicate != predicate:
            continue
        if direction in ("out", "both") and r.subject == entity_id:
            other = ents.get(r.object)
            out.append({"direction": "out", "predicate": r.predicate, "neighbor": r.object,
                        "neighbor_name": other.name if other else None, "relation_id": r.relation_id,
                        "status": r.status})
        if direction in ("in", "both") and r.object == entity_id:
            other = ents.get(r.subject)
            out.append({"direction": "in", "predicate": r.predicate, "neighbor": r.subject,
                        "neighbor_name": other.name if other else None, "relation_id": r.relation_id,
                        "status": r.status})
    return out


def current_version(
    ref: str, workspace: str | Path | None = None
) -> dict[str, Any]:
    """Answer "which is current?" for a ref. Resolves it, then follows
    ``supersedes`` edges FORWARD (something that supersedes it) until it reaches
    the node nothing supersedes -- the live head. Returns the head plus the
    superseded chain, so the caller sees both the current truth and the history."""
    ws = resolve_workspace(workspace)
    ents = load_entities(ws)
    start = resolve_entity(ref, ws, ents)
    if start is None:
        return {"found": False, "ref": ref}
    rels = [r for r in load_relations(ws).values() if _relation_is_live(r) and r.predicate == "supersedes"]
    superseded_by = {r.object: r.subject for r in rels}  # old -> new
    chain = [start.entity_id]
    head = start.entity_id
    seen = {head}
    while head in superseded_by:
        head = superseded_by[head]
        if head in seen:  # defensive: never loop on a malformed cycle
            break
        seen.add(head)
        chain.append(head)
    head_entity = ents.get(head)
    return {
        "found": True,
        "queried": start.entity_id,
        "current": head_entity.to_dict() if head_entity else None,
        "is_current": head == start.entity_id,
        "supersession_chain": chain,
    }


def graph_stats(workspace: str | Path | None = None) -> dict[str, Any]:
    """A topology summary for cortex-status: entity counts by type, live vs
    superseded, edge counts by predicate. Cheap, deterministic, no LLM."""
    ents = load_entities(workspace)
    rels = load_relations(workspace)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for e in ents.values():
        by_type[e.type] = by_type.get(e.type, 0) + 1
        by_status[e.status] = by_status.get(e.status, 0) + 1
    live = [r for r in rels.values() if _relation_is_live(r)]
    by_pred: dict[str, int] = {}
    for r in live:
        by_pred[r.predicate] = by_pred.get(r.predicate, 0) + 1
    return {
        "entities": len(ents),
        "entities_by_type": dict(sorted(by_type.items())),
        "entities_by_status": dict(sorted(by_status.items())),
        "relations_total": len(rels),
        "relations_live": len(live),
        "relations_by_predicate": dict(sorted(by_pred.items())),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex living ontology (Phase 7)")
    parser.add_argument("--workspace", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="topology summary of the graph")

    p_find = sub.add_parser("find", help="filter current entities")
    p_find.add_argument("--type", default=None)
    p_find.add_argument("--status", default=None)
    p_find.add_argument("--name-contains", default=None)

    p_get = sub.add_parser("get", help="resolve one entity by id/name/alias")
    p_get.add_argument("ref")

    p_nb = sub.add_parser("neighbors", help="one-hop edges from an entity")
    p_nb.add_argument("entity_id")
    p_nb.add_argument("--predicate", default=None)
    p_nb.add_argument("--direction", default="both", choices=["in", "out", "both"])

    p_cur = sub.add_parser("current", help="which entity is current for a ref (follows supersedes)")
    p_cur.add_argument("ref")

    sub.add_parser("validate", help="validate every entity and relation against the schema")

    args = parser.parse_args(argv)
    ws = args.workspace

    if args.command == "stats":
        _print(graph_stats(ws))
    elif args.command == "find":
        _print([e.to_dict() for e in find_entities(
            type=args.type, status=args.status, name_contains=args.name_contains, workspace=ws)])
    elif args.command == "get":
        e = resolve_entity(args.ref, ws)
        _print(e.to_dict() if e else {"found": False, "ref": args.ref})
    elif args.command == "neighbors":
        _print(neighbors(args.entity_id, predicate=args.predicate, direction=args.direction, workspace=ws))
    elif args.command == "current":
        _print(current_version(args.ref, ws))
    elif args.command == "validate":
        _print(validate_all(ws))
    return 0


def validate_all(workspace: str | Path | None = None) -> dict[str, Any]:
    """Whole-graph integrity check: every entity and every relation re-validated
    against the current schema. The self-maintaining loop's guardrail -- run it
    in CI so a bad append can't rot the graph silently."""
    ws = resolve_workspace(workspace)
    sch = load_schema(ws)
    ents = load_entities(ws)
    entity_errors: dict[str, list[str]] = {}
    for eid, e in ents.items():
        ok, errs = validate_entity(e, sch, ws)
        if not ok:
            entity_errors[eid] = errs
    relation_errors: dict[str, list[str]] = {}
    for rid, r in load_relations(ws).items():
        ok, errs = validate_relation(r, sch, ents, ws)
        if not ok:
            relation_errors[rid] = errs
    return {
        "ok": not entity_errors and not relation_errors,
        "entity_errors": entity_errors,
        "relation_errors": relation_errors,
    }


if __name__ == "__main__":
    raise SystemExit(main())
