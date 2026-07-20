"""GAP G3 (docs/GAP-CLOSURE-PLAN.md row G3): doc-currency / freshness.

Before this, the only currency mechanism was index-rebuild-on-mtime
(``cortex_core/search.py``): the FTS index re-chunks a doc when its bytes
change, but nothing ever *notices* that a doc has quietly gone stale, and a
contradicted fact stayed live forever. This module adds the three pieces G3
asks for, extending the existing machinery rather than replacing it:

1. **Staleness-gap SLI** (``staleness_report``) — reads the search index's own
   ``documents`` table (path + mtime), computes each doc's age against a
   freshness horizon, and surfaces the count + worst offenders. It is a
   METRIC, never a blocker (detection-over-coercion, like the other SLIs in
   ``docs/ROADMAP.md`` §5 and ``cortex_core/retrieval_health.py``). The horizon
   default is the RECORDED decision, not a guess: ``refresh_policy: 180d``
   (``docs/DEEP-RESEARCH-DESIGN.md`` §98), which ties to the ROADMAP
   corpus-freshness SLI ("no ``accepted/`` doc past its per-source refresh
   policy", ROADMAP §5 / line 334).

2. **Supersede-don't-delete fact validity** (``assert_fact`` / ``live_facts`` /
   ``fact_as_of``) — a bi-temporal fact store (Zep/Graphiti move, ROADMAP
   Stage C). Asserting a *contradicting* value for the same key CLOSES the old
   fact's validity window (``valid_to`` stamped, ``status='superseded'``)
   instead of deleting it, so the superseded value drops out of live results
   but stays queryable as historical / as-of a past time. This is the
   automatic-on-contradiction complement to ``ontology.invalidate_relation``
   (which requires an explicit call): storage + lock discipline are shared with
   the ontology / task-ledger (one critical section, not a third copy).

3. **Incremental per-doc reindex** (``incremental_reindex``) — refresh ONE (or
   a few) document's chunks in the FTS index without walking + hashing the whole
   corpus the way ``CortexSearchIndex.rebuild()`` does. Reuses the index's own
   connection / schema / chunker so the two paths can never diverge.

No new MCP tool (anti-bloat): surfaced via the ``cortex-freshness`` CLI only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import make_stdio_encoding_safe, resolve_exact_workspace, resolve_workspace
from .search import (
    CortexSearchIndex,
    _EXCLUDED_DIR_NAMES,
    _extract_title,
    _strip_frontmatter,
)

# Reuse the one lock discipline already in the codebase (search rebuild lock ->
# task ledger -> ontology -> here), never a fourth copy of the critical section.
from .task_ledger import _acquire_lock, _lock_path, _release_lock

# RECORDED decision, not a guessed parameter: docs/DEEP-RESEARCH-DESIGN.md §98
# sets refresh_policy: 180d as the per-source re-fetch cadence, and ROADMAP §5
# (line 334) defines the corpus-freshness SLI as "no accepted/ doc past its
# per-source refresh policy". 180 days is that cadence used as the default
# freshness horizon; a per-run --horizon-days override exists for corpora with a
# different policy. (No numeric freshness threshold existed in the corpus/code
# before this; the 180d refresh_policy is the closest recorded decision, cited.)
DEFAULT_FRESHNESS_HORIZON_DAYS = 180

FACTS_REL = ("docs", "ontology", "facts.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# 1. Staleness-gap SLI
# ===========================================================================
def staleness_report(
    workspace: str | Path | None = None,
    horizon_days: int = DEFAULT_FRESHNESS_HORIZON_DAYS,
    top: int = 10,
) -> dict[str, Any]:
    """Per-doc age vs a freshness horizon, read from the search index's own
    ``documents`` table (mtime_ns). Returns the total doc count, the stale count
    (age > horizon), the stale fraction, and the worst offenders (oldest first).

    A METRIC, not a gate: it never raises on a stale corpus and never blocks a
    write -- detection over coercion. If the index is missing it is built first
    (the same lazy-build every other read path uses), so the SLI always has data
    to report rather than silently returning zeros."""
    ws = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
    index = CortexSearchIndex(ws)
    if index.needs_rebuild():
        index.rebuild()

    conn = index.connect()
    index.ensure_schema(conn)
    rows = conn.execute(
        "SELECT path, shard, mtime_ns, indexed_at FROM documents"
    ).fetchall()
    conn.close()

    now_ns = time.time_ns()
    horizon_ns = horizon_days * 86_400 * 1_000_000_000
    docs: list[dict[str, Any]] = []
    stale_count = 0
    for row in rows:
        path, shard, mtime_ns, indexed_at = row[0], row[1], int(row[2]), row[3]
        age_ns = max(0, now_ns - mtime_ns)
        age_days = age_ns / (86_400 * 1_000_000_000)
        is_stale = age_ns > horizon_ns
        if is_stale:
            stale_count += 1
        docs.append(
            {
                "path": path,
                "shard": shard,
                "age_days": round(age_days, 1),
                "stale": is_stale,
                "indexed_at": indexed_at,
            }
        )

    docs.sort(key=lambda d: d["age_days"], reverse=True)
    total = len(docs)
    return {
        "horizon_days": horizon_days,
        "total_docs": total,
        "stale_count": stale_count,
        "stale_fraction": (stale_count / total) if total else 0.0,
        "worst_offenders": docs[: max(0, top)],
        "generated_at": _now(),
    }


# ===========================================================================
# 2. Supersede-don't-delete fact validity (bi-temporal)
# ===========================================================================
@dataclass(frozen=True)
class Fact:
    fact_id: str
    key: str
    value: str
    valid_from: str
    valid_to: str | None = None  # None => live; set => window closed (superseded)
    status: str = "active"  # active | superseded
    source_path: str = ""
    author_model: str = ""
    superseded_by: str | None = None
    created_at: str = ""
    updated_at: str = ""
    event: str = "assert"
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fact":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]


def facts_path(workspace: str | Path | None = None) -> Path:
    ws = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
    return ws.joinpath(*FACTS_REL)


def _new_fact_id(key: str) -> str:
    # Time-sortable, collision-resistant, human-greppable by key.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha1(f"{key}:{time.time_ns()}".encode()).hexdigest()[:8]
    return f"fact-{stamp}-{digest}"


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


def load_facts(workspace: str | Path | None = None) -> dict[str, Fact]:
    """Current fact state: the last record written per fact_id wins (the same
    append-only reduction the task ledger and ontology use)."""
    state: dict[str, Fact] = {}
    for rec in _read_jsonl(facts_path(workspace)):
        fid = rec.get("fact_id")
        if fid:
            state[fid] = Fact.from_dict(rec)
    return state


def _fact_is_live(fact: Fact) -> bool:
    return fact.status == "active" and fact.valid_to is None


def live_facts(workspace: str | Path | None = None, *, key: str | None = None) -> list[Fact]:
    facts = [f for f in load_facts(workspace).values() if _fact_is_live(f)]
    if key is not None:
        facts = [f for f in facts if f.key == key]
    return sorted(facts, key=lambda f: (f.key, f.valid_from))


def historical_facts(workspace: str | Path | None = None) -> list[Fact]:
    """Facts whose validity window has been closed -- superseded, never deleted."""
    facts = [f for f in load_facts(workspace).values() if not _fact_is_live(f)]
    return sorted(facts, key=lambda f: (f.key, f.valid_from))


def facts_for_key(key: str, *, workspace: str | Path | None = None) -> list[Fact]:
    """Every version of a key -- live head plus the closed history -- oldest first."""
    facts = [f for f in load_facts(workspace).values() if f.key == key]
    return sorted(facts, key=lambda f: f.valid_from)


def fact_as_of(key: str, ts: str, *, workspace: str | Path | None = None) -> Fact | None:
    """The fact that was live for ``key`` at ISO timestamp ``ts``: valid_from <= ts
    and (valid_to is None or ts < valid_to). ISO-8601/UTC strings from ``_now``
    compare correctly lexically, so no parsing is needed for the ordering."""
    candidates = [
        f
        for f in facts_for_key(key, workspace=workspace)
        if f.valid_from <= ts and (f.valid_to is None or ts < f.valid_to)
    ]
    if not candidates:
        return None
    # Most recently opened window that still contained ts.
    return sorted(candidates, key=lambda f: f.valid_from)[-1]


def assert_fact(
    key: str,
    value: str,
    *,
    source_path: str = "",
    author_model: str = "",
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Assert that ``key`` currently has ``value``.

    Supersede-don't-delete (Zep/Graphiti bi-temporal): if a *different* value is
    currently live for this key, its validity window is CLOSED (``valid_to``
    stamped, ``status='superseded'``, ``superseded_by`` -> the new fact) rather
    than deleted, and the new value is appended as the live head. Re-asserting
    the SAME value is a no-op (no spurious supersession, no duplicate head).
    Locked read-check-append, exactly like the ledger/ontology writes."""
    ws = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
    path = facts_path(ws)
    lock = _acquire_lock(_lock_path(path))
    if lock is None:
        return {"ok": False, "errors": ["could not acquire freshness fact lock"], "key": key}
    try:
        current_live = [f for f in load_facts(ws).values() if _fact_is_live(f) and f.key == key]

        # Idempotent: the same value is already live -> nothing to supersede.
        same = [f for f in current_live if f.value == value]
        if same:
            return {"ok": True, "fact_id": same[0].fact_id, "event": "noop",
                    "superseded": [], "key": key}

        now = _now()
        new_id = _new_fact_id(key)

        # Close every contradicting live window for this key (usually exactly one).
        superseded: list[str] = []
        for old in current_live:
            record = old.to_dict()
            record.update(
                valid_to=now,
                status="superseded",
                superseded_by=new_id,
                updated_at=now,
                event="supersede",
            )
            _append_jsonl(path, record)
            superseded.append(old.fact_id)

        fact = Fact(
            fact_id=new_id,
            key=key,
            value=value,
            valid_from=now,
            valid_to=None,
            status="active",
            source_path=source_path,
            author_model=author_model,
            created_at=now,
            updated_at=now,
            event="assert",
        )
        _append_jsonl(path, fact.to_dict())
        return {"ok": True, "fact_id": new_id, "event": fact.event,
                "superseded": superseded, "key": key, "fact": fact.to_dict()}
    finally:
        _release_lock(lock)


def close_fact(
    key: str, *, reason: str = "", workspace: str | Path | None = None
) -> dict[str, Any]:
    """Explicitly retire a key's live value without a replacement (e.g. a fact
    that is simply no longer true). Closes the window; the value stays historical."""
    ws = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
    path = facts_path(ws)
    lock = _acquire_lock(_lock_path(path))
    if lock is None:
        return {"ok": False, "errors": ["could not acquire freshness fact lock"], "key": key}
    try:
        current_live = [f for f in load_facts(ws).values() if _fact_is_live(f) and f.key == key]
        if not current_live:
            return {"ok": False, "errors": [f"no live fact for key {key!r}"], "key": key}
        now = _now()
        closed: list[str] = []
        for old in current_live:
            record = old.to_dict()
            record.update(
                valid_to=now,
                status="superseded",
                updated_at=now,
                event="close",
                value=old.value + (f" [closed: {reason}]" if reason else ""),
            )
            _append_jsonl(path, record)
            closed.append(old.fact_id)
        return {"ok": True, "closed": closed, "key": key, "valid_to": now}
    finally:
        _release_lock(lock)


# ===========================================================================
# 3. Incremental per-doc reindex
# ===========================================================================
def _shard_for_path(ws: Path, path: Path) -> str:
    """Cheap shard label from the path alone (no corpus walk), mirroring the root
    labels ``CortexSearchIndex._iter_document_paths`` assigns."""
    try:
        rel = path.resolve().relative_to(ws.resolve())
    except ValueError:
        return "docs"
    parts = rel.parts
    if parts and parts[0] == "docs":
        if len(parts) >= 2 and parts[1].startswith("cortex-"):
            return parts[1]
        if len(parts) >= 2 and parts[1] == "research":
            return "research"
        if len(parts) >= 2 and parts[1] == "ontology":
            return "docs"
        return "docs"
    if parts and parts[0] in ("reviewed", "accepted", "inbox", "patterns"):
        return parts[0]
    if parts[:2] == ("library", "cortex-library"):
        return "library-docs"
    if parts and parts[0] == "audit":
        return "audit-log-1" if len(parts) < 2 else parts[1]
    return parts[0] if parts else "docs"


def incremental_reindex(
    workspace: str | Path | None,
    paths: list[str | Path],
) -> dict[str, Any]:
    """Reindex ONLY the given documents in the FTS index -- no full-corpus walk +
    hash. For each path: re-chunk its (frontmatter-stripped) content, replace its
    chunk + document rows, and refresh the stat fingerprint so
    ``needs_rebuild()``'s fast path treats it as current. Missing paths are
    removed from the index (a deletion is a currency event too). Meta counts are
    refreshed so ``--status`` and the staleness SLI stay accurate.

    Returns ``{"reindexed": [...], "removed": [...], "full_rebuild": False}``.
    ``full_rebuild`` is always False here by construction -- that field exists so
    a caller can assert the cheap path actually ran."""
    ws = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
    index = CortexSearchIndex(ws)
    conn = index.connect()
    index.ensure_schema(conn)

    now = _now()
    reindexed: list[str] = []
    removed: list[str] = []
    for raw in paths:
        p = Path(raw)
        key = p.as_posix()
        # Guard: refuse to index junk dirs, matching the discovery filter.
        try:
            rel_parts = p.resolve().relative_to(ws.resolve()).parts[:-1]
        except ValueError:
            rel_parts = ()
        if any(part in _EXCLUDED_DIR_NAMES or part.startswith(".") for part in rel_parts):
            continue

        conn.execute("DELETE FROM chunks WHERE path = ?", (key,))
        conn.execute("DELETE FROM documents WHERE path = ?", (key,))
        if not p.is_file():
            removed.append(key)
            continue

        text = p.read_text(encoding="utf-8", errors="replace")
        st = p.stat()
        chunks = index.chunk_text(_strip_frontmatter(text))
        shard = _shard_for_path(ws, p)
        kind = index._kind_for_path(p)
        title = _extract_title(text)
        content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks(content, path, shard, filename, title, kind, chunk_index) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chunk, key, shard, p.name, title, kind, i),
            )
        conn.execute(
            "INSERT INTO documents(path, shard, kind, title, content_hash, mtime_ns, size, "
            "indexed_at, chunk_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, shard, kind, title, content_hash, st.st_mtime_ns, st.st_size, now, len(chunks)),
        )
        reindexed.append(key)

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    indexed_at_ns = time.time_ns()
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('indexed_at', ?)", (now,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('indexed_at_ns', ?)", (str(indexed_at_ns),))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('document_count', ?)", (str(doc_count),))
    conn.commit()
    conn.close()

    # Keep meta.json (what needs_rebuild reads for the racy-window baseline) in
    # sync, so a subsequent search does not wrongly decide the index is stale.
    meta = {}
    if index.meta_path.exists():
        try:
            meta = json.loads(index.meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    meta.update(
        {
            "indexed_at": now,
            "indexed_at_ns": indexed_at_ns,
            "document_count": doc_count,
            "db_bytes": index.index_db.stat().st_size if index.index_db.exists() else 0,
        }
    )
    index.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return {"reindexed": reindexed, "removed": removed, "full_rebuild": False}


# ===========================================================================
# CLI
# ===========================================================================
def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(
        description="Cortex doc-currency / freshness (GAP G3): staleness SLI, "
        "supersede-don't-delete fact validity, incremental per-doc reindex."
    )
    parser.add_argument("--workspace", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_stale = sub.add_parser("staleness", help="staleness-gap SLI: per-doc age vs a freshness horizon")
    p_stale.add_argument("--horizon-days", type=int, default=DEFAULT_FRESHNESS_HORIZON_DAYS)
    p_stale.add_argument("--top", type=int, default=10, help="how many worst offenders to list")
    p_stale.add_argument("--json", action="store_true")

    p_assert = sub.add_parser("assert-fact", help="assert key=value; a contradiction closes the old window (supersede-don't-delete)")
    p_assert.add_argument("key")
    p_assert.add_argument("value")
    p_assert.add_argument("--source-path", default="")
    p_assert.add_argument("--author-model", default="")

    p_close = sub.add_parser("close-fact", help="retire a key's live value (window closed, kept as historical)")
    p_close.add_argument("key")
    p_close.add_argument("--reason", default="")

    p_facts = sub.add_parser("facts", help="list facts")
    p_facts.add_argument("--key", default=None, help="restrict to one key (shows full history)")
    p_facts.add_argument("--all", action="store_true", help="include closed/historical facts")

    p_re = sub.add_parser("reindex", help="incremental per-doc reindex (no full rebuild)")
    p_re.add_argument("paths", nargs="+")

    args = parser.parse_args(argv)
    ws = args.workspace

    if args.command == "staleness":
        report = staleness_report(ws, horizon_days=args.horizon_days, top=args.top)
        if args.json:
            _print(report)
        else:
            print(f"freshness horizon: {report['horizon_days']} days")
            print(f"documents:         {report['total_docs']}")
            print(f"stale:             {report['stale_count']} ({report['stale_fraction']:.1%})")
            if report["worst_offenders"]:
                print("worst offenders (oldest first):")
                for d in report["worst_offenders"]:
                    flag = "STALE" if d["stale"] else "ok"
                    print(f"  [{flag:>5}] {d['age_days']:>7.1f}d  {d['path']}")
    elif args.command == "assert-fact":
        _print(assert_fact(args.key, args.value, source_path=args.source_path,
                           author_model=args.author_model, workspace=ws))
    elif args.command == "close-fact":
        _print(close_fact(args.key, reason=args.reason, workspace=ws))
    elif args.command == "facts":
        if args.key is not None:
            facts = facts_for_key(args.key, workspace=ws)
        elif args.all:
            facts = sorted(load_facts(ws).values(), key=lambda f: (f.key, f.valid_from))
        else:
            facts = live_facts(ws)
        _print([f.to_dict() for f in facts])
    elif args.command == "reindex":
        _print(incremental_reindex(ws, list(args.paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
