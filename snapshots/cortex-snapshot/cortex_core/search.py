from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import make_stdio_encoding_safe, resolve_exact_workspace, resolve_workspace

# Stability audit finding #3 (2026-07-07): a hard, always-positive cap on `search(limit=...)` --
# SQLite treats a negative LIMIT as "unlimited", which let a caller-supplied negative or absurd
# limit turn a budget-capped search into a full-corpus dump. 100 is generously above the default
# (20) and every real caller's needs, while still bounding the worst case.
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100

# GAP G2 ontology-retrieval leg (docs/ONTOLOGY-RETRIEVAL-SPEC.md). Both are
# deliberately conservative: one hop keeps graph fan-out bounded (a hub doc's
# references, not references-of-references), and a 4-char minimum surface form
# keeps entity resolution from firing on short/common tokens. Neither is a
# guessed retrieval parameter -- the fusion primitive itself reuses the recorded
# RRF k=60 (cortex_core/vector.reciprocal_rank_fusion), unchanged.
ONTOLOGY_MAX_HOPS = 1
ONTOLOGY_MIN_MATCH_LEN = 4
# GAP G2-local: the ontology leg is a NET WASH on this repo's dense corpus and
# ships PARKED / default-OFF globally (evals/reports/ontology_retrieval_gate.md).
# A SCATTERED corpus -- many small, loosely-linked, poorly-named docs whose
# connected documents do NOT share vocabulary -- is the regime the park note
# flags for revisit. Such a corpus opts in WITHOUT any global default change via
# a per-workspace config file, docs/ontology/retrieval.yaml:
#     ontology_fusion:
#       enabled: true
#       max_hops: 2
# Absent => (disabled, ONTOLOGY_MAX_HOPS): the dense-repo path is unchanged,
# byte-for-byte, and the switch is reversible (delete the file). Hops are hard-
# bounded to keep graph fan-out sane even if the config over-asks.
ONTOLOGY_MAX_CONFIGURABLE_HOPS = 3
_ONTOLOGY_FUSION_CONFIG_REL = ("docs", "ontology", "retrieval.yaml")


@dataclass(frozen=True)
class Document:
    path: Path
    shard: str
    kind: str
    title: str
    content_hash: str
    mtime_ns: int
    size: int


# KE-04 (gate 0.16): a file whose mtime falls within this window of the
# index's own last write is not trusted on stat alone -- mirrors git's
# same-second "racy" guard, since 1s mtime resolution can hide an edit made
# in the same second the index was written.
_RACY_WINDOW_NS = 1_000_000_000

# Gate 0.7 pitfall (previously tracked as "not yet built"): telemetry JSONL
# must not grow unbounded. When a log passes this size it rolls to a single
# `.1` backup and starts fresh, bounding total footprint to ~2x this.
_TELEMETRY_MAX_BYTES = 5 * 1024 * 1024

# F2 (tracked LOW): doc discovery must not index scratch/build/tooling junk
# that happens to contain .md files. Any path with one of these directory
# names -- or any hidden (dot-prefixed) directory -- between a scan root and
# the file is skipped.
_EXCLUDED_DIR_NAMES = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        "scratch",
        "tmp",
        "temp",
        "build",
        "dist",
        ".git",
        ".docker",
        ".hf-cache",
        ".pytest_cache",
    }
)


def _rotate_log_if_large(log_path: Path, max_bytes: int | None = None) -> None:
    """Size-based rotation for a JSONL telemetry log (gate 0.7). Best-effort:
    callers wrap telemetry in a fire-and-forget guard, so a rotation failure
    must never surface -- it just means the log keeps growing until the next
    successful roll. max_bytes is resolved from the module constant at CALL
    time (not bound as a default) so a runtime override actually takes effect."""
    if max_bytes is None:
        max_bytes = _TELEMETRY_MAX_BYTES
    try:
        if log_path.exists() and log_path.stat().st_size >= max_bytes:
            backup = log_path.parent / (log_path.name + ".1")
            os.replace(log_path, backup)  # atomic; replaces any prior .1
    except OSError:
        pass


@dataclass(frozen=True)
class SearchResult:
    path: str
    shard: str
    filename: str
    title: str
    snippet: str
    rank: float
    chunk_index: int
    kind: str


class CurrentStateProjectionError(RuntimeError):
    """Current-only retrieval was requested while project state is untrusted."""


class CortexSearchIndex:
    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        authority_verifier: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        # An explicitly-passed workspace dir is used DIRECTLY (bypassing the ambient
        # CORTEX_WORKSPACE env) so a dual-plane read (GAP-0015) against the brain path
        # resolves to the brain, not the tenant env. None -> normal env/auto resolution.
        self.workspace = resolve_workspace(workspace) if workspace is None else resolve_exact_workspace(workspace)
        self.index_dir = self.workspace / "library" / "cortex-library" / "search"
        self.index_db = self.index_dir / "cortex-index.sqlite"
        self.meta_path = self.index_db.with_suffix(".meta.json")
        self.authority_verifier = authority_verifier

    def connect(self) -> sqlite3.Connection:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.index_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")  # 30s: survive a concurrent rebuild under many-session load (was 5s -> "database is locked")
        # PRAGMA journal_mode=WAL needs its own retry loop: switching a fresh
        # database's journal mode for the first time requires exclusive
        # access, and when two connections race to make that switch, SQLite's
        # busy handler does not reliably cover this specific transition -- the
        # loser can still get an immediate "database is locked" even with
        # busy_timeout already active (confirmed: reordering busy_timeout
        # before this pragma alone did not stop the race). Retrying the
        # pragma itself closes it (Opus Stage F review, finding F1).
        deadline = time.monotonic() + 5.0
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        return conn

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "  content, path UNINDEXED, shard UNINDEXED, filename UNINDEXED, "
            "  title UNINDEXED, kind UNINDEXED, chunk_index UNINDEXED, "
            "  tokenize='porter unicode61', prefix='2,3'"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS documents("
            "  path TEXT PRIMARY KEY,"
            "  shard TEXT NOT NULL,"
            "  kind TEXT NOT NULL,"
            "  title TEXT NOT NULL,"
            "  content_hash TEXT NOT NULL,"
            "  mtime_ns INTEGER NOT NULL,"
            "  size INTEGER NOT NULL DEFAULT 0,"
            "  indexed_at TEXT NOT NULL,"
            "  chunk_count INTEGER NOT NULL"
            ")"
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "size" not in existing_columns:
            # Pre-existing index predates the stat fast-path (KE-04): add the
            # column so old databases don't need a manual reset. Backfilled
            # rows read as size=0 until their next rebuild touches them --
            # they simply fall back to hashing (the old behavior) until then.
            conn.execute("ALTER TABLE documents ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL"
            ")"
        )

    @staticmethod
    def _canonical_json_bytes(value: object) -> bytes:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")

    def _managed_project_path(self, value: object) -> str | None:
        """Return a stable corpus key for a project-state path.

        Project state may only govern files inside this workspace.  A malformed
        or escaping path is rejected by the projection trust check rather than
        being allowed to hide an arbitrary file.
        """
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.workspace.resolve())
        except (OSError, ValueError):
            return None
        return resolved.as_posix()

    @staticmethod
    def _state_document_items(current: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        for field in ("documents", "normative_documents", "status_documents"):
            value = current.get(field)
            candidates = value.values() if isinstance(value, Mapping) else value
            if not isinstance(candidates, (list, tuple)) and not isinstance(value, Mapping):
                continue
            for item in candidates:
                if isinstance(item, Mapping):
                    items.append(item)
        return items

    def _current_state_filter(self, *, include_history: bool = False) -> dict[str, object]:
        """Validate and load the project-state ACTIVE-document projection.

        The dirty marker is the store's publication barrier.  Metadata must bind
        the exact current snapshot, reducer revision, and documents bytes before
        any lifecycle filtering is trusted.  Invalid/partial state falls back to
        legacy discovery but remains explicitly visible through ``status()``.
        """
        state_root = self.workspace / "project-state"
        current_path = state_root / "current.json"
        dirty_path = state_root / "projections-dirty.json"
        projections = state_root / "projections"
        metadata_path = projections / "projection-metadata.json"
        documents_path = projections / "documents.json"
        base: dict[str, object] = {
            "status": "UNAVAILABLE",
            "available": False,
            "trusted": False,
            "active_only": False,
            "include_history": include_history,
            "reason": "project-state current-document projection is unavailable; legacy corpus discovery is active",
            "managed_document_count": 0,
            "active_document_count": 0,
            "historical_document_count": 0,
            "ignored_non_path_document_count": 0,
            "_managed_paths": frozenset(),
            "_active_paths": frozenset(),
        }
        if dirty_path.exists():
            base.update(
                status="DIRTY",
                reason="project-state projection publication is dirty; active-only filtering is not trusted",
            )
            return base
        artifacts = (current_path, metadata_path, documents_path)
        if not state_root.exists():
            return base
        missing = [path.relative_to(self.workspace).as_posix() for path in artifacts if not path.is_file()]
        if missing:
            base.update(
                status="INVALID",
                reason="project-state projection is incomplete: missing " + ", ".join(missing),
            )
            return base
        try:
            current_raw = current_path.read_bytes()
            metadata_raw = metadata_path.read_bytes()
            documents_raw = documents_path.read_bytes()
            current = json.loads(current_raw)
            metadata = json.loads(metadata_raw)
            documents = json.loads(documents_raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            base.update(status="INVALID", reason=f"project-state projection JSON is unreadable: {exc}")
            return base
        reasons: list[str] = []
        if not isinstance(current, dict):
            reasons.append("current.json is not an object")
        if not isinstance(metadata, dict):
            reasons.append("projection-metadata.json is not an object")
        if not isinstance(documents, dict):
            reasons.append("documents.json is not an object")
        if reasons:
            base.update(status="INVALID", reason="; ".join(reasons))
            return base

        # Hash agreement among current/metadata/projections is not sufficient:
        # those files can be rewritten together.  The durable event log is the
        # authority, so require the store's lock-protected replay anchor before
        # trusting ACTIVE_ONLY selection.
        try:
            from .project_state_store import ProjectStateStore

            store_status = ProjectStateStore(
                self.workspace, authority_verifier=self.authority_verifier,
            ).projection_status()
        except Exception as exc:
            base.update(
                status="INVALID",
                reason=f"project-state projection cannot be replay-anchored: {exc}",
            )
            return base
        if store_status.get("clean") is not True:
            store_reasons = store_status.get("reasons")
            detail = "; ".join(str(item) for item in store_reasons) \
                if isinstance(store_reasons, list) else "store status is not clean"
            base.update(
                status="INVALID",
                reason="project-state projection is not anchored to immutable event history: "
                + detail,
            )
            return base

        try:
            expected_input_hash = hashlib.sha256(self._canonical_json_bytes(current)).hexdigest()
        except (TypeError, ValueError) as exc:
            base.update(status="INVALID", reason=f"current.json is not canonical JSON: {exc}")
            return base
        if metadata.get("input_sha256") != expected_input_hash:
            reasons.append("metadata input hash does not match current.json")
        state_hash = current.get("state_sha256")
        state_payload = dict(current)
        state_payload.pop("state_sha256", None)
        expected_state_hash = hashlib.sha256(self._canonical_json_bytes(state_payload)).hexdigest()
        if not isinstance(state_hash, str) or state_hash != expected_state_hash:
            reasons.append("current state_sha256 is missing or invalid")
        current_revision = current.get("project_revision", current.get("revision"))
        if current_revision is None or metadata.get("project_revision") != current_revision:
            reasons.append("metadata project revision does not match current.json")
        current_reducer = current.get("reducer_version", current.get("reducer_revision"))
        metadata_reducer = metadata.get("reducer_version", metadata.get("reducer_revision"))
        if current_reducer in (None, "") or str(metadata_reducer) != str(current_reducer):
            reasons.append("metadata reducer revision does not match current.json")
        for alias in ("reducer_version", "reducer_revision"):
            if alias in metadata and str(metadata[alias]) != str(current_reducer):
                reasons.append(f"metadata {alias} does not match current.json")
        if metadata.get("project_id") != current.get("project_id"):
            reasons.append("metadata project_id does not match current.json")
        output_hashes = metadata.get("output_sha256")
        if not isinstance(output_hashes, dict):
            reasons.append("metadata output hash map is invalid")
        elif output_hashes.get("documents.json") != hashlib.sha256(documents_raw).hexdigest():
            reasons.append("metadata documents.json hash does not match the published file")
        if documents.get("generated_projection") is not True:
            reasons.append("documents.json is not marked as a generated projection")
        if documents.get("selection_mode") != "ACTIVE_ONLY":
            reasons.append("documents.json is not an ACTIVE_ONLY selection")
        selected = documents.get("documents")
        if not isinstance(selected, list):
            reasons.append("documents.json documents must be a list")
            selected = []

        managed_paths: set[str] = set()
        ignored_non_paths = 0
        for item in self._state_document_items(current):
            raw_path = item.get("path")
            if raw_path in (None, ""):
                ignored_non_paths += 1
                continue
            normalized = self._managed_project_path(raw_path)
            if normalized is None:
                ignored_non_paths += 1
            else:
                managed_paths.add(normalized)
        active_paths: set[str] = set()
        for index, item in enumerate(selected):
            if not isinstance(item, dict):
                reasons.append(f"documents.json entry {index} is not an object")
                continue
            if item.get("selection_status") != "ACTIVE" or item.get("current") is not True:
                reasons.append(f"documents.json entry {index} is not explicitly ACTIVE/current")
            raw_path = item.get("path")
            if raw_path in (None, ""):
                continue
            normalized = self._managed_project_path(raw_path)
            if normalized is None:
                continue
            active_paths.add(normalized)
            managed_paths.add(normalized)
        active_count = documents.get("active_count")
        if isinstance(active_count, bool) or not isinstance(active_count, int) or active_count != len(selected):
            reasons.append("documents.json active_count does not match selected entries")
        if reasons:
            base.update(status="INVALID", reason="; ".join(reasons))
            return base

        historical_paths = managed_paths - active_paths
        base.update(
            status="HISTORY_INCLUDED" if include_history else "ACTIVE_ONLY",
            available=True,
            trusted=True,
            active_only=not include_history,
            reason=(
                "trusted project-state projection loaded; managed history is explicitly included"
                if include_history
                else "trusted project-state projection loaded; managed history is excluded"
            ),
            managed_document_count=len(managed_paths),
            active_document_count=len(active_paths),
            historical_document_count=len(historical_paths),
            ignored_non_path_document_count=ignored_non_paths,
            project_revision=current_revision,
            reducer_version=str(current_reducer),
            input_sha256=expected_input_hash,
            documents_sha256=hashlib.sha256(documents_raw).hexdigest(),
            _managed_paths=frozenset(managed_paths),
            _active_paths=frozenset(active_paths),
        )
        return base

    @staticmethod
    def _public_current_state_filter(filter_state: Mapping[str, object]) -> dict[str, object]:
        return {key: value for key, value in filter_state.items() if not key.startswith("_")}

    @staticmethod
    def _require_trusted_current_filter(
        filter_state: Mapping[str, object], *, include_history: bool,
    ) -> None:
        if not include_history and filter_state.get("status") in {"DIRTY", "INVALID"}:
            raise CurrentStateProjectionError(
                f"current-only retrieval is blocked: {filter_state.get('reason')}; "
                "pass include_history=True only for an explicit historical bypass"
            )

    def _iter_document_paths(self, *, include_history: bool = False) -> list[tuple[Path, str]]:
        """Cheap path discovery: which files exist and under which shard label,
        with no content read/hash. Shared by ``discover_documents()`` (which
        hashes each one) and ``needs_rebuild()``'s stat fast-path (KE-04),
        which must not pay that cost when nothing changed."""
        roots: list[tuple[Path, str]] = []
        for shard_dir in sorted((self.workspace / "docs").glob("cortex-*")):
            if shard_dir.is_dir():
                roots.append((shard_dir, shard_dir.name))
        for name in ("reviewed", "accepted"):
            root = self.workspace / name
            if root.is_dir():
                roots.append((root, name))
        research = self.workspace / "docs" / "research"
        if research.is_dir():
            roots.append((research, "research"))
        for audit_dir in sorted((self.workspace / "audit").glob("audit-log-*/agent")):
            if audit_dir.is_dir():
                roots.append((audit_dir, audit_dir.parent.name))
        library_docs = self.workspace / "library" / "cortex-library" / "docs"
        if library_docs.is_dir():
            roots.append((library_docs, "library-docs"))
        # docs/ root and inbox/ are added last so anything already covered by
        # the more specific roots above (cortex-* shards, research) keeps its
        # existing shard label; the `seen` dedupe below skips the re-walk.
        docs_root = self.workspace / "docs"
        if docs_root.is_dir():
            roots.append((docs_root, "docs"))
        inbox_root = self.workspace / "inbox"
        if inbox_root.is_dir():
            roots.append((inbox_root, "inbox"))
        # Phase 5: the pattern library (KEDB) is first-class corpus content, so
        # patterns are searchable and served back as guidance.
        patterns_root = self.workspace / "patterns"
        if patterns_root.is_dir():
            roots.append((patterns_root, "patterns"))

        filter_state = self._current_state_filter(include_history=include_history)
        self._require_trusted_current_filter(filter_state, include_history=include_history)
        managed_paths = filter_state["_managed_paths"]
        active_paths = filter_state["_active_paths"]
        apply_active_filter = filter_state["trusted"] is True and not include_history
        seen: set[Path] = set()
        result: list[tuple[Path, str]] = []
        for root, shard in roots:
            for path in sorted(root.rglob("*.md")):
                if path in seen:
                    continue
                # F2: skip scratch/build/tooling junk and hidden dirs anywhere
                # between the scan root and the file.
                between = path.relative_to(root).parts[:-1]
                if any(p in _EXCLUDED_DIR_NAMES or p.startswith(".") for p in between):
                    continue
                normalized = path.resolve().as_posix()
                if apply_active_filter and normalized in managed_paths and normalized not in active_paths:
                    continue
                seen.add(path)
                result.append((path, shard))
        return result

    def discover_documents(self, *, include_history: bool = False) -> list[Document]:
        docs: list[Document] = []
        for path, shard in self._iter_document_paths(include_history=include_history):
            text = path.read_text(encoding="utf-8", errors="replace")
            st = path.stat()
            docs.append(
                Document(
                    path=path,
                    shard=shard,
                    kind=self._kind_for_path(path),
                    title=_extract_title(text),
                    content_hash=hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                    mtime_ns=st.st_mtime_ns,
                    size=st.st_size,
                )
            )
        return docs

    def _kind_for_path(self, path: Path) -> str:
        parts = set(path.parts)
        if "audit" in parts:
            return "audit"
        if "accepted" in parts:
            return "accepted"
        if "reviewed" in parts:
            return "reviewed"
        if "research" in parts:
            return "research"
        return "doc"

    def chunk_text(self, text: str, max_chars: int = 1500) -> list[str]:
        paragraphs = re.split(r"\n\s*\n", text.strip()) if text.strip() else [""]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            piece = paragraph.strip()
            if not piece:
                continue
            if len(piece) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                lines = piece.splitlines()
                buffer = ""
                for line in lines:
                    candidate = f"{buffer}\n{line}".strip() if buffer else line
                    if len(candidate) > max_chars and buffer:
                        chunks.append(buffer.strip())
                        buffer = line
                    else:
                        buffer = candidate
                if buffer:
                    chunks.append(buffer.strip())
                continue
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if len(candidate) > max_chars and current:
                chunks.append(current.strip())
                current = piece
            else:
                current = candidate
        if current:
            chunks.append(current.strip())
        return chunks or [text.strip() or ""]

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Is this process still running? Read-only probe, cross-platform.

        Windows: do NOT use os.kill(pid, 0) -- on Windows os.kill calls
        TerminateProcess, so probing a *live* PID would kill it. Use a
        read-only OpenProcess + GetExitCodeProcess (STILL_ACTIVE) instead.
        POSIX: os.kill(pid, 0) is a genuine no-op probe."""
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False  # can't open -> treat as gone (safe for lock-stealing)
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _rebuild_lock_path(self) -> Path:
        return self.index_dir / "rebuild.lock"

    def _rebuild_lock_is_stale(self, lock_path: Path, stale_after: float) -> bool:
        """A lock is stale if we can't read it, it's older than stale_after, or
        the PID that wrote it is no longer alive -- so a crash mid-rebuild can
        never wedge future rebuilds."""
        try:
            raw = lock_path.read_text(encoding="utf-8").strip().split("\n")
            pid = int(raw[0])
            ts = float(raw[1])
        except (OSError, ValueError, IndexError):
            return True
        if time.time() - ts > stale_after:
            return True
        return not self._pid_alive(pid)

    def _acquire_rebuild_lock(
        self, stale_after: float = 60.0, wait_timeout: float = 30.0
    ) -> Path | None:
        """Advisory rebuild lock (gate 0.6 / F1(a)). Atomic-create a lockfile so
        two concurrent rebuilds serialize instead of redundantly racing (and
        risking a busy-timeout on a slow write). Steals a stale/crashed lock.
        If a *live* rebuild holds it past wait_timeout, returns None and the
        caller proceeds anyway -- WAL still guarantees correctness, the lock is
        only an optimization, so it must never hard-block a rebuild."""
        lock_path = self._rebuild_lock_path()
        self.index_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + wait_timeout
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, f"{os.getpid()}\n{time.time()}".encode())
                finally:
                    os.close(fd)
                return lock_path
            except FileExistsError:
                if self._rebuild_lock_is_stale(lock_path, stale_after):
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    return None  # live holder; proceed lockless (WAL protects us)
                time.sleep(0.1)

    def _release_rebuild_lock(self, lock_path: Path | None) -> None:
        if lock_path is None:
            return
        try:
            os.remove(lock_path)
        except OSError:
            pass

    def rebuild(self, *, include_history: bool = False) -> dict[str, object]:
        lock = self._acquire_rebuild_lock()
        try:
            return self._rebuild_unlocked(include_history=include_history)
        finally:
            self._release_rebuild_lock(lock)

    def _rebuild_unlocked(self, *, include_history: bool = False) -> dict[str, object]:
        filter_state = self._current_state_filter(include_history=include_history)
        docs = self.discover_documents(include_history=include_history)
        now = datetime.now(timezone.utc).isoformat()
        conn = self.connect()
        self.ensure_schema(conn)
        prev_rows = {
            row[0]: row[1]
            for row in conn.execute("SELECT path, content_hash FROM documents").fetchall()
        }
        current_paths = {doc.path.as_posix() for doc in docs}
        removed = set(prev_rows) - current_paths
        updated = 0
        removed_count = 0
        for path in removed:
            conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
            conn.execute("DELETE FROM documents WHERE path = ?", (path,))
            removed_count += 1
        for doc in docs:
            previous_hash = prev_rows.get(doc.path.as_posix())
            if previous_hash == doc.content_hash:
                # Content unchanged: no chunk rewrite needed, but keep the
                # stat fingerprint (mtime_ns, size) fresh so needs_rebuild()'s
                # fast path (KE-04) has an accurate baseline to compare
                # against -- also self-heals any row a schema migration
                # backfilled with size=0.
                conn.execute(
                    "UPDATE documents SET mtime_ns = ?, size = ? WHERE path = ?",
                    (doc.mtime_ns, doc.size, doc.path.as_posix()),
                )
                continue
            text = doc.path.read_text(encoding="utf-8", errors="replace")
            chunks = self.chunk_text(_strip_frontmatter(text))
            conn.execute("DELETE FROM chunks WHERE path = ?", (doc.path.as_posix(),))
            conn.execute("DELETE FROM documents WHERE path = ?", (doc.path.as_posix(),))
            for index, chunk in enumerate(chunks):
                conn.execute(
                    "INSERT INTO chunks(content, path, shard, filename, title, kind, chunk_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk,
                        doc.path.as_posix(),
                        doc.shard,
                        doc.path.name,
                        doc.title,
                        doc.kind,
                        index,
                    ),
                )
            conn.execute(
                "INSERT INTO documents(path, shard, kind, title, content_hash, mtime_ns, size, indexed_at, chunk_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.path.as_posix(),
                    doc.shard,
                    doc.kind,
                    doc.title,
                    doc.content_hash,
                    doc.mtime_ns,
                    doc.size,
                    now,
                    len(chunks),
                ),
            )
            updated += 1
        indexed_at_ns = time.time_ns()
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('indexed_at', ?)", (now,))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('indexed_at_ns', ?)", (str(indexed_at_ns),))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('document_count', ?)", (str(len(docs)),))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('db_bytes', ?)", (str(self.index_db.stat().st_size if self.index_db.exists() else 0),))
        conn.commit()
        conn.close()
        meta = {
            "indexed_at": now,
            "indexed_at_ns": indexed_at_ns,
            "document_count": len(docs),
            "updated": updated,
            "removed": removed_count,
            "db_bytes": self.index_db.stat().st_size if self.index_db.exists() else 0,
            "current_state_filter": self._public_current_state_filter(filter_state),
        }
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def needs_rebuild(self, *, include_history: bool = False) -> bool:
        # KE-04 (gate 0.16): staleness must not cost O(corpus bytes) -- a stat
        # (mtime_ns + size) fast-path below skips content hashing entirely for
        # any file whose fingerprint still matches what was indexed, hashing
        # only files whose stat changed or that fall inside the racy window.
        if not self.index_db.exists() or not self.meta_path.exists():
            return True
        try:
            prev_meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:
            return True
        try:
            indexed_at_ns = int(prev_meta.get("indexed_at_ns", 0))
        except (TypeError, ValueError):
            indexed_at_ns = 0

        conn = self.connect()
        self.ensure_schema(conn)
        stored = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT path, content_hash, mtime_ns, size FROM documents"
            ).fetchall()
        }
        conn.close()

        current = self._iter_document_paths(include_history=include_history)
        current_keys = {path.as_posix() for path, _shard in current}
        if current_keys != set(stored):
            return True  # a file was added or removed -- stale, no hashing needed to know it

        for path, _shard in current:
            prev_hash, prev_mtime_ns, prev_size = stored[path.as_posix()]
            st = path.stat()
            racy = st.st_mtime_ns >= indexed_at_ns - _RACY_WINDOW_NS
            if not racy and st.st_mtime_ns == prev_mtime_ns and st.st_size == prev_size:
                continue  # stat fast-path: trusted unchanged, no hash needed
            text = path.read_text(encoding="utf-8", errors="replace")
            content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
            if content_hash != prev_hash:
                return True
        return False

    def _ontology_fusion_config(self) -> tuple[bool, int]:
        """Per-workspace ontology-fusion switch (GAP G2-local). Reads
        docs/ontology/retrieval.yaml and returns (enabled, max_hops). Absent,
        unreadable, or malformed => (False, ONTOLOGY_MAX_HOPS): the parked
        default, so a corpus with no such file (this dense repo) is unchanged.
        max_hops is clamped to [1, ONTOLOGY_MAX_CONFIGURABLE_HOPS] so a config
        can never blow up graph fan-out. This is the corpus-characteristic
        opt-in, NOT a global default flip (the gate parks fusion for dense
        corpora -- evals/reports/ontology_retrieval_gate.md)."""
        cfg_path = self.workspace.joinpath(*_ONTOLOGY_FUSION_CONFIG_REL)
        if not cfg_path.is_file():
            return (False, ONTOLOGY_MAX_HOPS)
        try:
            import yaml

            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            block = raw.get("ontology_fusion") or {}
            enabled = bool(block.get("enabled", False))
            hops = int(block.get("max_hops", ONTOLOGY_MAX_HOPS))
        except Exception:
            return (False, ONTOLOGY_MAX_HOPS)
        hops = max(1, min(hops, ONTOLOGY_MAX_CONFIGURABLE_HOPS))
        return (enabled, hops)

    def search(
        self,
        query: str,
        limit: int = 20,
        rescan_ms: float = 0.0,
        tag: str | None = None,
        use_vector: bool = False,
        use_ontology: bool = False,
        log_telemetry: bool = True,
        ontology_max_hops: int | None = None,
        include_history: bool = False,
    ) -> list[SearchResult]:
        # GAP G6 (per-tenant no-log): when the caller identifies a no-log tenant it passes
        # log_telemetry=False so the raw query never lands in search-telemetry.jsonl. Default True
        # keeps every existing call site (owner/CLI/eval) logging exactly as before.
        filter_state = self._current_state_filter(include_history=include_history)
        self._require_trusted_current_filter(filter_state, include_history=include_history)
        if not self.index_db.exists():
            raise RuntimeError("No Cortex index exists. Run `--index` first.")
        # Stability audit finding #3 (2026-07-07): SQLite's own "negative LIMIT means no
        # upper bound" semantics were passing straight through to `LIMIT ?` -- a caller-supplied
        # `limit=-1` returned the entire matching corpus (1106 hits vs. 5 for `limit=5`, real
        # repro), and an absurd `limit=999999` degraded the same way. Clamp to a sane, always-
        # positive, hard-capped range so a malformed/adversarial limit can never turn a capped
        # search into an unbounded response.
        limit = max(MIN_SEARCH_LIMIT, min(int(limit), MAX_SEARCH_LIMIT))
        t0 = time.time()
        conn = self.connect()
        self.ensure_schema(conn)

        # Phase 2 gate 2.3 (vector) + GAP G2 (ontology): optional RRF fusion of
        # BM25 with the dense vector leg and/or the ontology-expansion leg. Each
        # extra leg is OFF by default; the fused path is only taken when at least
        # one is explicitly requested (and, for vector, importable) -- otherwise
        # this whole branch is skipped and the untouched BM25 ladder below runs,
        # so neither extra leg can regress or crash the default search path.
        # Resolve the corpus-characteristic ontology-fusion switch: an explicit
        # use_ontology=True (or the CLI flag / eval arg) always wins; otherwise a
        # scattered corpus can auto-enable via docs/ontology/retrieval.yaml. Hop
        # count follows the same precedence: an explicit ontology_max_hops arg
        # overrides the config, which overrides the recorded default.
        cfg_enabled, cfg_hops = self._ontology_fusion_config()
        effective_ontology = use_ontology or cfg_enabled
        max_hops = ontology_max_hops if ontology_max_hops is not None else cfg_hops
        max_hops = max(1, min(int(max_hops), ONTOLOGY_MAX_CONFIGURABLE_HOPS))

        vector_mod = None
        if use_vector:
            from . import vector as _vector

            if _vector.vector_available():
                vector_mod = _vector
        if vector_mod is not None or effective_ontology:
            try:
                results, rung = self._search_fused(conn, query, limit, vector_mod, effective_ontology, max_hops)
                results = self._filter_current_results(results, include_history=include_history)
                conn.close()
                if log_telemetry:
                    self._log_search_telemetry(
                        query, rung, results, (time.time() - t0) * 1000, rescan_ms, tag
                    )
                return results
            except Exception:
                # Any fused-leg failure (model download, extension load, corrupt
                # vec table, ontology read) degrades to pure BM25 rather than
                # failing the search. Fall through to the ladder below.
                pass

        def run_match(match_query: str) -> list[sqlite3.Row]:
            return conn.execute(
                "SELECT path, shard, filename, title, kind, chunk_index, snippet(chunks, 0, '<<', '>>', '...', 32) AS snippet, bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (match_query, limit),
            ).fetchall()

        rung = "and"
        rows: list[sqlite3.Row] = []
        try:
            rows = run_match(_normalize_query(query))
            if not rows:
                # AND rung matched nothing (terms spread across chunks/docs) —
                # fall through to an OR rung, still BM25-ranked, so the query
                # doesn't dead-end just because no single chunk has every term.
                or_query = _normalize_query(query, joiner=" OR ")
                if or_query != _normalize_query(query):
                    rung = "or"
                    rows = run_match(or_query)
        except sqlite3.OperationalError as exc:
            if "syntax error" in str(exc).lower():
                rung = "like"
                # PER-TERM OR-LIKE, not a whole-phrase LIKE. The old `content LIKE
                # '%<entire query>%'` required the full multi-word phrase verbatim, so
                # ANY multi-word query that reached this fallback silently returned 0
                # (the second half of the 2026-07-05 retrieval-miss bug). Fall back to
                # "contains ANY term" -- same spirit as the OR rung -- so the fallback
                # can never silently dead-end a query whose terms are in the corpus.
                terms = [t for t in re.sub(r"[^\w\s]", " ", query).split() if t] or [query]
                like_clause = " OR ".join("content LIKE ?" for _ in terms)
                rows = conn.execute(
                    "SELECT path, shard, filename, title, kind, chunk_index, "
                    "substr(content, 1, 240) AS snippet, 0.0 AS rank FROM chunks "
                    f"WHERE {like_clause} LIMIT ?",
                    (*[f"%{t}%" for t in terms], limit),
                ).fetchall()
            else:
                conn.close()
                raise
        results = [
            SearchResult(
                path=row[0],
                shard=row[1],
                filename=row[2],
                title=row[3],
                kind=row[4],
                chunk_index=int(row[5]),
                snippet=row[6],
                rank=float(row[7]),
            )
            for row in rows
        ]
        results = self._filter_current_results(results, include_history=include_history)
        conn.close()
        if log_telemetry:
            self._log_search_telemetry(query, rung, results, (time.time() - t0) * 1000, rescan_ms, tag)
        return results

    def _filter_current_results(
        self, results: list[SearchResult], *, include_history: bool = False,
    ) -> list[SearchResult]:
        """Final retrieval barrier, including for stale vector/ontology indexes.

        Discovery normally removes managed history during rebuild.  This second
        barrier prevents an old index or an optional retrieval leg from
        reactivating it after project state changes.
        """
        if include_history:
            return results
        filter_state = self._current_state_filter(include_history=False)
        self._require_trusted_current_filter(filter_state, include_history=False)
        if filter_state["trusted"] is not True:
            return results
        managed_paths = filter_state["_managed_paths"]
        active_paths = filter_state["_active_paths"]
        return [
            result for result in results
            if result.path not in managed_paths or result.path in active_paths
        ]

    def _search_fused(self, conn, query, limit, vector_mod, use_ontology, max_hops=ONTOLOGY_MAX_HOPS):
        """Gate 2.3 (vector) + GAP G2 (ontology): fuse the BM25 leg with the dense
        vector leg and/or the ontology-expansion leg via reciprocal rank fusion.
        Every leg contributes a ranked list of chunk rowids; RRF combines their
        ranks (not raw scores), so no cross-leg score normalization is needed.
        BM25 is always present; vector and ontology are each opt-in."""
        # RRF is pure-python and dep-free, so it is safe to import even when the
        # optional vector extra is absent (ontology-only fusion).
        from . import vector as _vector_rrf

        if vector_mod is not None:
            vector_mod.ensure_built(conn)

        # BM25 leg: same AND->OR ladder as the default path, but selecting
        # rowid so hits can be fused with the other legs. Fetch a deeper pool
        # than `limit` so fusion has material from every leg to work with.
        pool = max(limit * 4, 20)

        def run_match(match_query: str):
            return conn.execute(
                "SELECT rowid, path, shard, filename, title, kind, chunk_index, "
                "snippet(chunks, 0, '<<', '>>', '...', 32) AS snippet, bm25(chunks) AS rank "
                "FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (match_query, pool),
            ).fetchall()

        bm25_rows: list[sqlite3.Row] = []
        try:
            bm25_rows = run_match(_normalize_query(query))
            if not bm25_rows:
                or_query = _normalize_query(query, joiner=" OR ")
                if or_query != _normalize_query(query):
                    bm25_rows = run_match(or_query)
        except sqlite3.OperationalError as exc:
            if "syntax error" not in str(exc).lower():
                raise
            bm25_rows = []

        bm25_ranked = [int(r[0]) for r in bm25_rows]
        row_by_id = {int(r[0]): r for r in bm25_rows}

        ranked_lists: list[list[int]] = [bm25_ranked]
        legs = ["bm25"]

        # Vector leg (opt-in, only when the extra is importable).
        if vector_mod is not None:
            vec_hits = vector_mod.vector_search(conn, query, pool)
            ranked_lists.append([rowid for rowid, _dist in vec_hits])
            legs.append("vec")

        # Ontology leg (opt-in): resolve query -> entities -> graph neighbors ->
        # their indexed source docs -> those docs' chunk rowids, ranked. This is
        # the leg that can surface a graph-connected document BM25/vector never
        # retrieved (the multi-hop win). Deterministic, no LLM.
        if use_ontology:
            onto_ranked = self._ontology_leg(conn, query, pool, max_hops)
            ranked_lists.append(onto_ranked)
            legs.append("onto")

        fused = _vector_rrf.reciprocal_rank_fusion(ranked_lists)
        ordered_ids = sorted(fused, key=lambda i: (fused[i], -i), reverse=True)[:limit]

        # Backfill columns for hits BM25 never returned (vector- or ontology-only).
        missing = [i for i in ordered_ids if i not in row_by_id]
        if missing:
            placeholders = ",".join("?" for _ in missing)
            for r in conn.execute(
                "SELECT rowid, path, shard, filename, title, kind, chunk_index, "
                "substr(content, 1, 240) AS snippet, 0.0 AS rank "
                f"FROM chunks WHERE rowid IN ({placeholders})",
                missing,
            ).fetchall():
                row_by_id[int(r[0])] = r

        results: list[SearchResult] = []
        for rowid in ordered_ids:
            row = row_by_id.get(rowid)
            if row is None:
                continue
            results.append(
                SearchResult(
                    path=row[1],
                    shard=row[2],
                    filename=row[3],
                    title=row[4],
                    kind=row[5],
                    chunk_index=int(row[6]),
                    snippet=row[7],
                    rank=fused[rowid],
                )
            )
        # Rung label names the legs that actually fused (e.g. "rrf:bm25+vec+onto").
        return results, "rrf:" + "+".join(legs)

    def _ontology_leg(self, conn, query, pool, max_hops=ONTOLOGY_MAX_HOPS):
        """GAP G2 ontology-expansion leg (docs/ONTOLOGY-RETRIEVAL-SPEC.md).

        Deterministic, lazy, dep-free: load the living ontology on demand, resolve
        the entities NAMED in the query, walk up to `max_hops` live edges to
        their neighbors, collect the neighbors' indexed (.md) source documents in
        hop order, and emit those documents' chunk rowids ranked by query-term
        overlap (chunk_index as the deterministic tiebreak). Returns a ranked list
        of chunk rowids to fuse; empty when the query names no known entity or the
        reached docs are not indexed -- in which case the leg is a harmless no-op."""
        from . import ontology as _ontology

        ws = self.workspace
        try:
            entities = _ontology.load_entities(ws)
            relations = _ontology.load_relations(ws)
        except Exception:
            return []
        if not entities:
            return []

        # 1. Resolve seed entities NAMED in the query (literal, reproducible).
        q_norm = _ontology_normalize(query)
        q_tokens = set(q_norm.split())
        seeds: set[str] = set()
        for eid, ent in entities.items():
            for surface in [ent.name, *ent.aliases]:
                s = _ontology_normalize(surface)
                if len(s) < ONTOLOGY_MIN_MATCH_LEN:
                    continue
                # whole-token phrase match: every token of the surface form must
                # appear, and the phrase must occur contiguously in the query.
                if s in q_norm and set(s.split()) <= q_tokens:
                    seeds.add(eid)
                    break
        if not seeds:
            return []

        # 2. Breadth-first expand up to ONTOLOGY_MAX_HOPS over LIVE edges.
        live = [r for r in relations.values() if r.status == "active" and r.invalid_from is None]
        adj: dict[str, set[str]] = {}
        for r in live:
            adj.setdefault(r.subject, set()).add(r.object)
            adj.setdefault(r.object, set()).add(r.subject)
        hop_of: dict[str, int] = {eid: 0 for eid in seeds}
        frontier = set(seeds)
        for hop in range(1, max_hops + 1):
            nxt: set[str] = set()
            for node in frontier:
                for nb in adj.get(node, ()):
                    if nb not in hop_of:
                        hop_of[nb] = hop
                        nxt.add(nb)
            frontier = nxt
            if not frontier:
                break

        # 3. Collect the indexed (.md) source docs reached, with their hop distance.
        #    EXPANSION semantics: emit only NEIGHBOR docs (hop >= 1). The seed
        #    entities' own docs (hop 0) are what the query already names and BM25
        #    already finds -- re-injecting them would just re-boost the named doc
        #    and can DEMOTE the graph-connected answer (observed on a doc-named
        #    query). The leg's job is to reach the connected doc you'd otherwise
        #    miss, so seed docs are excluded, not prioritized.
        seed_paths: set[str] = set()
        for eid in seeds:
            ent = entities.get(eid)
            if ent is None:
                continue
            for src in ent.source_paths:
                if src.endswith(".md"):
                    seed_paths.add((ws / src).resolve().as_posix())
        path_hop: dict[str, int] = {}
        for eid in sorted(hop_of, key=lambda e: (hop_of[e], e)):
            if hop_of[eid] == 0:
                continue  # seed doc -- already named/found, not an expansion target
            ent = entities.get(eid)
            if ent is None:
                continue
            for src in ent.source_paths:
                if not src.endswith(".md"):
                    continue  # non-indexed source (.py/.yaml/.jsonl) -- skip
                p = (ws / src).resolve().as_posix()
                if p not in path_hop and p not in seed_paths:
                    path_hop[p] = hop_of[eid]
        if not path_hop:
            return []

        # 4. Rank each reached doc's chunks by query-term overlap (chunk_index as
        #    the deterministic tiebreak). Then order the DOCS by (hop asc, best
        #    chunk overlap desc, path) so the most query-relevant connected doc
        #    leads the leg -- a hub document (many references) can't bury the one
        #    that actually answers. Bounded by `pool` so it can't flood the fused
        #    candidate set. Deterministic throughout (no model, stable tiebreaks).
        per_doc: list[tuple[int, int, str, list[int]]] = []
        for path, hop in path_hop.items():
            rows = conn.execute(
                "SELECT rowid, content, chunk_index FROM chunks WHERE path = ? ORDER BY chunk_index",
                (path,),
            ).fetchall()
            if not rows:
                continue
            scored = []
            best = 0
            for row in rows:
                content_tokens = set(_ontology_normalize(row[1]).split())
                overlap = len(q_tokens & content_tokens)
                best = max(best, overlap)
                scored.append((-overlap, int(row[2]), int(row[0])))
            scored.sort()
            per_doc.append((hop, -best, path, [rowid for _ov, _ci, rowid in scored]))
        per_doc.sort(key=lambda d: (d[0], d[1], d[2]))

        ranked: list[int] = []
        for _hop, _negbest, _path, rowids in per_doc:
            ranked.extend(rowids)
            if len(ranked) >= pool:
                break
        return ranked[:pool]

    def _log_search_telemetry(
        self,
        query: str,
        rung: str,
        results: list[SearchResult],
        elapsed_ms: float,
        rescan_ms: float = 0.0,
        tag: str | None = None,
    ) -> None:
        # Fire-and-forget (PHASE-GATES 0.7): a telemetry write failure must
        # never break search.
        try:
            log_path = self.workspace / "logs" / "search-telemetry.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log_if_large(log_path)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "rung": rung,
                "hits": len(results),
                "top_path": results[0].path if results else None,
                "ms": round(elapsed_ms, 1),
                # KE-05 (gate 0.16): the staleness check used to be invisible
                # in telemetry, hiding exactly the cost KE-04 fixed.
                "rescan_ms": round(rescan_ms, 1),
                # gate 1.2 pitfall: eval traffic must be distinguishable from
                # real usage in telemetry, not silently mixed in. Only present
                # when the caller tags it -- normal search entries are
                # unchanged.
                **({"tag": tag} if tag else {}),
            }
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def zero_result_queries(self) -> list[dict[str, object]]:
        log_path = self.workspace / "logs" / "search-telemetry.jsonl"
        if not log_path.exists():
            return []
        entries: list[dict[str, object]] = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("hits") == 0:
                entries.append(entry)
        return entries

    def status(self, *, include_history: bool = False) -> dict[str, object]:
        filter_state = self._current_state_filter(include_history=include_history)
        current_filter = self._public_current_state_filter(filter_state)
        retrieval_blocked = (
            not include_history and filter_state.get("status") in {"DIRTY", "INVALID"}
        )
        if not self.index_db.exists():
            return {
                "index_exists": False,
                "workspace": str(self.workspace),
                "current_state_filter": current_filter,
                "retrieval_blocked": retrieval_blocked,
            }
        conn = self.connect()
        self.ensure_schema(conn)
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        meta = {}
        if self.meta_path.exists():
            try:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        return {
            "index_exists": True,
            "workspace": str(self.workspace),
            "documents": doc_count,
            "chunks": chunk_count,
            "meta": meta,
            "stale": True if retrieval_blocked else self.needs_rebuild(include_history=include_history),
            "current_state_filter": current_filter,
            "retrieval_blocked": retrieval_blocked,
            "retrieval_legs": self._retrieval_legs(),
        }

    def _retrieval_legs(self) -> dict[str, object]:
        """Phase 2 exit requirement: report which retrieval legs are available.
        BM25 (FTS5) is always on; the dense vector leg is opt-in (search
        `use_vector=True`) and only usable when the `[vector]` extra is
        importable -- surface both facts so `--status` tells the truth about
        what a search can actually use."""
        from . import vector as _vector

        return {
            "bm25": {"active": True, "always_on": True},
            "vector": {
                "available": _vector.vector_available(),
                "default_on": True,
                "active": _vector.vector_available(),
                "model": _vector.MODEL_ID,
                "max_distance": _vector.MAX_VECTOR_DISTANCE,
                "note": "ON by default for --hybrid / cortex_search (RRF-fused); "
                "degrades to BM25 when the [vector] extra is absent; --no-vector to disable",
            },
        }


def _strip_frontmatter(text: str) -> str:
    stripped = text.lstrip()
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1:]).lstrip("\n")
    return text  # unterminated frontmatter — defensive: don't swallow the doc


def _extract_title(text: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "(untitled)"


def _ontology_normalize(text: str) -> str:
    """Lowercase, punctuation->space, whitespace-collapsed form used by the
    ontology leg for both entity-surface matching and chunk term-overlap. Kept
    separate from _normalize_query (which builds FTS5 operator strings)."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _normalize_query(query: str, joiner: str = " AND ") -> str:
    # If the caller uses explicit FTS5 operators, trust them and pass through verbatim.
    if any(char in query for char in '"*()+'):
        return " ".join(query.split())
    # Otherwise strip EVERY punctuation char FTS5 would misparse -- not just '-' (which
    # FTS5 reads as NOT), but also a bare '.' (a token like "5.2" throws `fts5: syntax
    # error near "."`, which used to drop the whole query to the LIKE fallback and return
    # 0 hits -- the "GLM-5.2" retrieval miss). Keep word chars + whitespace only, so a
    # natural-language query can never self-sabotage on punctuation.
    cleaned = re.sub(r"[^\w\s]", " ", query)
    cleaned = " ".join(cleaned.split())
    terms = cleaned.split()
    if len(terms) > 1:
        return joiner.join(terms)
    return cleaned


def _print_results(results: Sequence[SearchResult], query: str) -> None:
    if not results:
        print(f"no results for: {query}")
        return
    for result in results:
        print(f"{result.path}  [{result.shard}/{result.filename}]")
        if result.title:
            print(f"  - {result.title}")
        print(f"  {result.snippet}")
        print()
    print(f"-- {len(results)} result(s) for '{query}' --")


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex FTS5 search")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--hybrid", nargs="?", const=None, default=None)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--zero-results", action="store_true")
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="include managed superseded/invalidated project documents; default retrieval uses only the trusted ACTIVE selection",
    )
    parser.add_argument(
        "--no-vector",
        action="store_true",
        help="disable the dense vector leg (BM25 only). The vector leg is ON by "
        "default for --hybrid when the [vector] extra is installed; it degrades "
        "to BM25 automatically when it isn't.",
    )
    parser.add_argument(
        "--ontology",
        action="store_true",
        help="fuse the ontology-expansion leg (GAP G2): resolve query entities, walk "
        "the living-ontology graph, and inject connected documents' chunks into the "
        "RRF pool -- for multi-hop queries. Default-OFF for dense corpora (PARKED, a "
        "net wash); SHIP-ON for SCATTERED corpora where it is a large win, opted into "
        "per-workspace via docs/ontology/retrieval.yaml (ontology_fusion). See "
        "docs/ONTOLOGY-RETRIEVAL-SPEC.md + evals/reports/ontology_retrieval_scattered_gate.md.",
    )
    parser.add_argument(
        "--ontology-hops",
        type=int,
        default=None,
        help="max ontology hops to traverse (GAP G2-local). Default: the workspace "
        "config's max_hops, else 1. Scattered corpora want 2 (reaches 2-edge answers).",
    )
    args = parser.parse_args(argv)

    index = CortexSearchIndex(args.workspace)
    if args.zero_results:
        entries = index.zero_result_queries()
        for entry in entries:
            print(json.dumps(entry))
        noun = "query" if len(entries) == 1 else "queries"
        print(f"-- {len(entries)} zero-result {noun} --")
        return 0
    if args.index:
        t0 = time.time()
        meta = index.rebuild(include_history=args.include_history)
        elapsed = time.time() - t0
        print(f"indexed {meta['document_count']} documents in {elapsed:.2f}s")
        return 0
    if args.hybrid is not None:
        rescan_t0 = time.time()
        if index.needs_rebuild(include_history=args.include_history):
            print("(index stale or missing -- rebuilding)", file=sys.stderr)
            index.rebuild(include_history=args.include_history)
        rescan_ms = (time.time() - rescan_t0) * 1000
        t0 = time.time()
        # Vector leg ON by default (gate 2.3 shipped default-on 2026-07-04 after
        # human-reviewed graded eval); degrades to BM25 when the extra is absent.
        results = index.search(args.hybrid, rescan_ms=rescan_ms, use_vector=not args.no_vector,
                               use_ontology=args.ontology, ontology_max_hops=args.ontology_hops,
                               include_history=args.include_history)
        _print_results(results, args.hybrid)
        print(f"(search took {(time.time() - t0) * 1000:.1f}ms)")
        return 0
    if args.status:
        print(json.dumps(index.status(include_history=args.include_history), indent=2))
        return 0
    parser.print_help()
    return 1
