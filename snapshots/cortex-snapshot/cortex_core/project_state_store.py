"""Durable storage and reconciliation for project-state events.

The event log is the authority.  ``current.json`` and every file under
``project-state/projections`` are rebuildable materialized views.  This module
therefore commits one validated event before it attempts reduction or
projection, and records a dirty marker until every projection is published.

The reducer and renderer are callbacks on purpose: the storage transaction is
independent of the project's policy vocabulary and of its human/search
projections.  The default callbacks are loaded lazily from ``project_state``
and ``project_state_projection`` once those lanes are installed.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import uuid6

from .task_ledger import _acquire_lock, _release_lock


STORE_SCHEMA_VERSION = 1
ONTOLOGY_RECEIPT_SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = "project-state"
MAX_PROJECTION_FILES = 32
MAX_PROJECTION_FILE_BYTES = 4 * 1024 * 1024
MAX_PROJECTION_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_CLOSEOUT_BUNDLE_BYTES = 256 * 1024
MAX_CLOSEOUT_LIST_ITEMS = 128
REQUIRED_PROJECTION_FILES = frozenset({
    "HANDOFF.md",
    "agent-resume-pack.json",
    "capability-status.json",
    "documents.json",
    "projection-metadata.json",
})


class ProjectStateStoreError(RuntimeError):
    """Base class for durable project-state failures."""


class StoreRevisionConflictError(ProjectStateStoreError):
    """The caller's expected revision is not the committed revision."""


class TimeRewindError(ProjectStateStoreError):
    """A materialization attempted to move current state to an earlier time."""


class CorruptEventLogError(ProjectStateStoreError):
    """The immutable event log is malformed or has an invalid causal chain."""


class LockUnavailableError(ProjectStateStoreError):
    """The shared exclusive-create lock could not be acquired safely."""


class ProjectionCommitError(ProjectStateStoreError):
    """An event is durable but its current/projection views remain dirty."""

    def __init__(self, message: str, *, committed_revision: int) -> None:
        super().__init__(message)
        self.committed_revision = committed_revision


class UnsafeProjectionPathError(ProjectStateStoreError):
    """A renderer attempted to publish outside the projection directory."""


class CloseoutBundleError(ValueError):
    """A closeout event bundle is unbounded or structurally unsafe."""


class EventAppender(Protocol):
    def __call__(
        self, events: Sequence[Mapping[str, Any]], event: Mapping[str, Any]
    ) -> list[dict[str, Any]]: ...


class StateReducer(Protocol):
    def __call__(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        as_of: str,
        verified_authority_event_ids: Sequence[str] = (),
    ) -> dict[str, Any]: ...


class ProjectionRenderer(Protocol):
    def __call__(
        self, current_state: Mapping[str, Any], *, include_history: bool = False
    ) -> Mapping[str, Any]: ...


class AuthorityVerifier(Protocol):
    def __call__(self, event: Mapping[str, Any]) -> bool: ...


class OntologySynchronizer(Protocol):
    def __call__(
        self, current_state: Mapping[str, Any], *, workspace: str | Path,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class StorePaths:
    root: Path
    events: Path
    current: Path
    projections: Path
    dirty: Path
    ontology_sync: Path
    staging: Path
    lock: Path


@dataclass(frozen=True)
class ReplayResult:
    revision: int
    events: tuple[dict[str, Any], ...]
    current_state: dict[str, Any]
    event_log_sha256: str
    current_sha256: str


@dataclass(frozen=True)
class ReconcileResult:
    revision: int
    appended: bool
    idempotent: bool
    rebuilt: bool
    event_log_sha256: str
    current_sha256: str
    projection_files: tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fallback_canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _fallback_canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_fallback_canonical_json(value).encode("utf-8")).hexdigest()


def _no_external_authority_verifier(_event: Mapping[str, Any]) -> bool:
    """Production default: no external authority service is configured."""
    return False


def _default_ontology_synchronizer(
    current_state: Mapping[str, Any], *, workspace: str | Path,
) -> Mapping[str, Any]:
    from .project_state_ontology import sync_project_state_ontology

    return sync_project_state_ontology(current_state, workspace=workspace)


def _load_default_callbacks() -> tuple[
    EventAppender,
    StateReducer,
    ProjectionRenderer,
    Callable[[Any], str],
    Callable[[Any], str],
]:
    from .project_state import append_event, canonical_json, canonical_sha256, reduce_events
    from .project_state_projection import render_projection_bundle

    return append_event, reduce_events, render_projection_bundle, canonical_json, canonical_sha256


def _json_clone(value: Any, canonical_json: Callable[[Any], str]) -> Any:
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectStateStoreError(f"value is not canonical JSON: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability; Windows cannot open directories."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{os.urandom(4).hex()}")
    try:
        _write_file_durable(tmp, data)
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _append_line_durable(path: Path, line: bytes) -> None:
    """Append one already-canonical line and force it to stable storage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        view = memoryview(line)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("event append made no forward progress")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


class ProjectStateStore:
    """Append, replay, and materialize one workspace's project state."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        append_event_fn: EventAppender | None = None,
        reducer: StateReducer | None = None,
        renderer: ProjectionRenderer | None = None,
        canonical_json_fn: Callable[[Any], str] | None = None,
        canonical_sha256_fn: Callable[[Any], str] | None = None,
        authority_verifier: AuthorityVerifier | None = None,
        ontology_synchronizer: OntologySynchronizer | None = None,
    ) -> None:
        workspace_path = Path(workspace).resolve()
        root = workspace_path / DEFAULT_STATE_DIR
        self.workspace = workspace_path
        self.paths = StorePaths(
            root=root,
            events=root / "events.jsonl",
            current=root / "current.json",
            projections=root / "projections",
            dirty=root / "projections-dirty.json",
            ontology_sync=root / "ontology-sync.json",
            staging=root / ".staging",
            lock=root / "events.jsonl.lock",
        )

        if any(
            callback is None
            for callback in (
                append_event_fn,
                reducer,
                renderer,
                canonical_json_fn,
                canonical_sha256_fn,
            )
        ):
            defaults = _load_default_callbacks()
            append_event_fn = append_event_fn or defaults[0]
            reducer = reducer or defaults[1]
            renderer = renderer or defaults[2]
            canonical_json_fn = canonical_json_fn or defaults[3]
            canonical_sha256_fn = canonical_sha256_fn or defaults[4]

        self._append_event = append_event_fn
        self._reduce = reducer
        self._render = renderer
        self._canonical_json = canonical_json_fn
        self._canonical_sha256 = canonical_sha256_fn
        self._authority_verifier = (
            authority_verifier
            if authority_verifier is not None
            else _no_external_authority_verifier
        )
        self._ontology_synchronizer = ontology_synchronizer or _default_ontology_synchronizer
        try:
            reducer_parameters = inspect.signature(self._reduce).parameters.values()
            self._reducer_accepts_authority = any(
                parameter.name == "verified_authority_event_ids"
                or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in reducer_parameters
            )
        except (TypeError, ValueError):
            self._reducer_accepts_authority = True

    def _verified_authority_event_ids(
        self, events: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        verified: list[str] = []
        for event in events:
            try:
                accepted = bool(self._authority_verifier(copy.deepcopy(event)))
            except Exception:
                accepted = False
            if accepted:
                verified.append(str(event["event_id"]))
        return sorted(set(verified))

    def _reduce_current(
        self, events: Sequence[Mapping[str, Any]], *, as_of: str,
    ) -> dict[str, Any]:
        if not self._reducer_accepts_authority:
            return self._reduce(events, as_of=as_of)
        return self._reduce(
            events,
            as_of=as_of,
            verified_authority_event_ids=self._verified_authority_event_ids(events),
        )

    def _lock(self) -> Path:
        lock = _acquire_lock(self.paths.lock)
        if lock is None:
            raise LockUnavailableError(f"could not acquire project-state lock: {self.paths.lock}")
        return lock

    def _read_json_file(self, path: Path) -> Any:
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptEventLogError(f"corrupt JSON file {path}: {exc}") from exc
        try:
            canonical = self._canonical_json(value)
        except (TypeError, ValueError) as exc:
            raise CorruptEventLogError(f"non-canonical JSON value in {path}: {exc}") from exc
        if raw != canonical:
            raise CorruptEventLogError(f"non-canonical or externally modified JSON file: {path}")
        return value

    def read_events(self) -> list[dict[str, Any]]:
        """Strictly read history.  A torn or altered line poisons the read."""
        if not self.paths.events.exists():
            return []
        records: list[dict[str, Any]] = []
        event_ids: set[str] = set()
        try:
            raw = self.paths.events.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CorruptEventLogError(f"cannot read event log: {exc}") from exc
        if raw and not raw.endswith("\n"):
            raise CorruptEventLogError("event log ends with a torn or unterminated line")
        lines = raw.splitlines()
        for number, line in enumerate(lines, start=1):
            if not line:
                raise CorruptEventLogError(f"blank event-log line {number}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorruptEventLogError(
                    f"corrupt event-log line {number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise CorruptEventLogError(f"event-log line {number} is not an object")
            try:
                if self._canonical_json(record) != line:
                    raise CorruptEventLogError(
                        f"event-log line {number} is not canonical or was externally modified"
                    )
            except (TypeError, ValueError) as exc:
                raise CorruptEventLogError(
                    f"event-log line {number} is not canonical JSON: {exc}"
                ) from exc
            event_id = record.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise CorruptEventLogError(f"event-log line {number} has no event_id")
            if event_id in event_ids:
                raise CorruptEventLogError(f"duplicate committed event_id {event_id!r}")
            event_ids.add(event_id)
            records.append(record)
        return records

    def _validate_chain(self, events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        validated: list[dict[str, Any]] = []
        for number, event in enumerate(events, start=1):
            try:
                candidate = self._append_event(validated, event)
            except Exception as exc:
                raise CorruptEventLogError(
                    f"invalid event causal chain at line {number}: {exc}"
                ) from exc
            if len(candidate) != len(validated) + 1:
                raise CorruptEventLogError(
                    f"event-log line {number} did not append exactly one event"
                )
            self._assert_preserved_prefix(validated, candidate)
            validated = [_json_clone(item, self._canonical_json) for item in candidate]
        return validated

    def _assert_preserved_prefix(
        self,
        before: Sequence[Mapping[str, Any]],
        after: Sequence[Mapping[str, Any]],
    ) -> None:
        if len(after) < len(before):
            raise ProjectStateStoreError("event appender truncated immutable history")
        for index, existing in enumerate(before):
            if self._canonical_json(existing) != self._canonical_json(after[index]):
                raise ProjectStateStoreError(
                    f"event appender mutated immutable history at revision {index + 1}"
                )

    def replay(self, *, as_of: str) -> ReplayResult:
        events = self._validate_chain(self.read_events())
        try:
            state = self._reduce_current(events, as_of=as_of)
        except Exception as exc:
            raise CorruptEventLogError(f"event replay failed: {exc}") from exc
        state = _json_clone(state, self._canonical_json)
        return ReplayResult(
            revision=len(events),
            events=tuple(copy.deepcopy(events)),
            current_state=state,
            event_log_sha256=self._canonical_sha256(events),
            current_sha256=self._canonical_sha256(state),
        )

    def read_current(self) -> dict[str, Any] | None:
        if not self.paths.current.exists():
            return None
        value = self._read_json_file(self.paths.current)
        if not isinstance(value, dict):
            raise CorruptEventLogError("project-state/current.json is not an object")
        return value

    @staticmethod
    def _parse_materialization_time(value: Any, *, field: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty timezone-aware date-time")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 date-time") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{field} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def _ensure_not_rewind_locked(self, as_of: str) -> None:
        requested = self._parse_materialization_time(as_of, field="as_of")
        current = self.read_current()
        if current is None:
            return
        try:
            materialized = self._parse_materialization_time(
                current.get("as_of"), field="project-state/current.json as_of",
            )
        except ValueError as exc:
            raise CorruptEventLogError(str(exc)) from exc
        if requested < materialized:
            raise TimeRewindError(
                f"materialization as_of {as_of!r} precedes committed current.as_of "
                f"{current.get('as_of')!r}"
            )

    def _dirty_marker(self) -> dict[str, Any] | None:
        if not self.paths.dirty.exists():
            return None
        value = self._read_json_file(self.paths.dirty)
        if not isinstance(value, dict):
            raise CorruptEventLogError("projection dirty marker is not an object")
        return value

    def _build_ontology_receipt(
        self,
        state: Mapping[str, Any],
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(outcome, Mapping):
            raise ProjectStateStoreError("ontology synchronizer must return a mapping")
        plan = _json_clone(outcome.get("plan"), self._canonical_json)
        result = _json_clone(outcome.get("result"), self._canonical_json)
        if not isinstance(plan, dict) or not isinstance(result, dict):
            raise ProjectStateStoreError("ontology synchronizer requires plan and result objects")
        anchors = {
            "project_id": state.get("project_id"),
            "revision": state.get("revision"),
            "reducer_version": state.get("reducer_version"),
            "event_log_sha256": state.get("event_log_sha256"),
            "state_sha256": state.get("state_sha256"),
        }
        for field, expected in anchors.items():
            if plan.get(field) != expected:
                raise ProjectStateStoreError(
                    f"ontology sync plan {field} does not match reduced current state"
                )
        if plan.get("assurance_minted") is not False or result.get("assurance_minted") is not False:
            raise ProjectStateStoreError("ontology synchronization must never mint assurance")
        failures = result.get("failures")
        if not isinstance(failures, list):
            raise ProjectStateStoreError("ontology sync result failures must be a list")
        if result.get("ok") is not True or failures:
            raise ProjectStateStoreError(
                f"ontology synchronization failed: {failures or result.get('status')!r}"
            )
        plan_skips = plan.get("unresolved_skips")
        result_skips = result.get("unresolved_skips")
        if not isinstance(plan_skips, list) or not isinstance(result_skips, list):
            raise ProjectStateStoreError("ontology unresolved_skips must be explicit lists")
        if self._canonical_json(plan_skips) != self._canonical_json(result_skips):
            raise ProjectStateStoreError("ontology plan/result unresolved_skips differ")
        expected_status = "APPLIED_WITH_UNRESOLVED" if result_skips else "APPLIED"
        if result.get("status") != expected_status:
            raise ProjectStateStoreError(
                f"ontology result status must be {expected_status} for its unresolved skips"
            )
        payload = {
            "schema_version": ONTOLOGY_RECEIPT_SCHEMA_VERSION,
            **anchors,
            "plan": plan,
            "result": result,
            "assurance_minted": False,
        }
        return {**payload, "receipt_sha256": self._canonical_sha256(payload)}

    def _validate_ontology_receipt(
        self, state: Mapping[str, Any], receipt: Any,
    ) -> dict[str, Any]:
        if not isinstance(receipt, dict):
            raise ProjectStateStoreError("ontology sync receipt is not an object")
        expected = self._build_ontology_receipt(
            state,
            {"plan": receipt.get("plan"), "result": receipt.get("result")},
        )
        if self._canonical_json(receipt) != self._canonical_json(expected):
            raise ProjectStateStoreError(
                "ontology sync receipt hash, anchors, or canonical outcome do not match"
            )
        return expected

    def _mark_dirty(
        self,
        *,
        revision: int,
        phase: str,
        as_of: str,
        error: str | None = None,
        state_sha256: str | None = None,
        projection_files: Sequence[str] = (),
        stage: str | None = None,
    ) -> None:
        marker = {
            "schema_version": STORE_SCHEMA_VERSION,
            "dirty": True,
            "revision": revision,
            "phase": phase,
            "as_of": as_of,
            "marked_at": _utc_now(),
            "error": error,
            "state_sha256": state_sha256,
            "projection_files": sorted(str(item) for item in projection_files),
            "stage": stage,
        }
        _atomic_write(self.paths.dirty, self._canonical_json(marker).encode("utf-8"))

    def _projection_bytes(self, bundle: Mapping[str, Any]) -> dict[str, bytes]:
        if not isinstance(bundle, Mapping):
            raise ProjectStateStoreError("projection renderer must return a mapping")
        if len(bundle) > MAX_PROJECTION_FILES:
            raise ProjectStateStoreError("projection bundle exceeds file-count bound")
        encoded: dict[str, bytes] = {}
        total = 0
        projection_root = self.paths.projections.resolve()
        for raw_name, value in bundle.items():
            name = str(raw_name)
            relative = Path(name)
            if not name or relative.is_absolute() or ".." in relative.parts:
                raise UnsafeProjectionPathError(f"unsafe projection path: {name!r}")
            target = (self.paths.projections / relative).resolve()
            try:
                target.relative_to(projection_root)
            except ValueError as exc:
                raise UnsafeProjectionPathError(
                    f"projection path escapes projection root: {name!r}"
                ) from exc
            if isinstance(value, str):
                data = value.encode("utf-8")
            else:
                data = self._canonical_json(value).encode("utf-8")
            if len(data) > MAX_PROJECTION_FILE_BYTES:
                raise ProjectStateStoreError(f"projection file too large: {name!r}")
            total += len(data)
            if total > MAX_PROJECTION_BUNDLE_BYTES:
                raise ProjectStateStoreError("projection bundle exceeds byte bound")
            encoded[relative.as_posix()] = data
        return encoded

    def _publish_locked(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        as_of: str,
        appended: bool,
        idempotent: bool,
        rebuilt: bool,
    ) -> ReconcileResult:
        revision = len(events)
        self._mark_dirty(revision=revision, phase="reduce", as_of=as_of)
        try:
            state = _json_clone(self._reduce_current(events, as_of=as_of), self._canonical_json)
            state_sha = self._canonical_sha256(state)
            self._mark_dirty(
                revision=revision,
                phase="render",
                as_of=as_of,
                state_sha256=state_sha,
            )
            bundle = self._projection_bytes(self._render(state, include_history=False))

            stage_name = f"revision-{revision}-{uuid6.uuid7()}"
            stage = self.paths.staging / stage_name
            stage_current = stage / "current.json"
            _write_file_durable(stage_current, self._canonical_json(state).encode("utf-8"))
            for name, data in bundle.items():
                _write_file_durable(stage / "projections" / Path(name), data)
            _fsync_directory(stage)

            self._mark_dirty(
                revision=revision,
                phase="publish",
                as_of=as_of,
                state_sha256=state_sha,
                projection_files=tuple(bundle),
                stage=stage_name,
            )

            self.paths.current.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage_current, self.paths.current)
            _fsync_directory(self.paths.current.parent)

            # Metadata is the bundle commit record and is published last.
            names = sorted(name for name in bundle if name != "projection-metadata.json")
            if "projection-metadata.json" in bundle:
                names.append("projection-metadata.json")
            for name in names:
                source = stage / "projections" / Path(name)
                target = self.paths.projections / Path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                _fsync_directory(target.parent)

            # Ontology is a derived projection too, but its public API owns the
            # append-only graph writes. Keep the global dirty marker until that
            # idempotent callback succeeds and its replay-anchored receipt is
            # durably published. Search therefore cannot trust the new metadata
            # in the partial window between file projection and graph sync.
            self._mark_dirty(
                revision=revision,
                phase="ontology-sync",
                as_of=as_of,
                state_sha256=state_sha,
                projection_files=tuple(bundle),
                stage=stage_name,
            )
            ontology_outcome = self._ontology_synchronizer(
                copy.deepcopy(state), workspace=self.workspace,
            )
            ontology_receipt = self._build_ontology_receipt(state, ontology_outcome)
            _atomic_write(
                self.paths.ontology_sync,
                self._canonical_json(ontology_receipt).encode("utf-8"),
            )

            self.paths.dirty.unlink(missing_ok=True)
            _fsync_directory(self.paths.root)
            shutil.rmtree(stage, ignore_errors=True)
            return ReconcileResult(
                revision=revision,
                appended=appended,
                idempotent=idempotent,
                rebuilt=rebuilt,
                event_log_sha256=self._canonical_sha256(events),
                current_sha256=state_sha,
                projection_files=tuple(sorted(bundle)),
            )
        except Exception as exc:
            try:
                self._mark_dirty(
                    revision=revision,
                    phase="failed",
                    as_of=as_of,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise ProjectionCommitError(
                f"revision {revision} is committed but projections are dirty: {exc}",
                committed_revision=revision,
            ) from exc

    def compare_and_append(
        self,
        event: Mapping[str, Any],
        *,
        expected_revision: int,
        as_of: str,
    ) -> ReconcileResult:
        """Compare under the shared lock, append once, then reconcile views."""
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        lock = self._lock()
        try:
            self._ensure_not_rewind_locked(as_of)
            events = self._validate_chain(self.read_events())
            if self.paths.dirty.exists():
                self._publish_locked(
                    events,
                    as_of=as_of,
                    appended=False,
                    idempotent=False,
                    rebuilt=True,
                )

            try:
                candidate = self._append_event(events, event)
            except Exception:
                # Preserve the reducer lane's typed validation/conflict errors.
                raise
            self._assert_preserved_prefix(events, candidate)

            if len(candidate) == len(events):
                # Exact event retry: appender idempotence wins over a now-stale
                # expected revision, and no duplicate JSONL line is written.
                return self._publish_locked(
                    events,
                    as_of=as_of,
                    appended=False,
                    idempotent=True,
                    rebuilt=False,
                )
            if len(candidate) != len(events) + 1:
                raise ProjectStateStoreError("event appender must add exactly one event")
            if expected_revision != len(events):
                raise StoreRevisionConflictError(
                    f"expected revision {expected_revision}, committed revision is {len(events)}"
                )

            normalized = _json_clone(candidate[-1], self._canonical_json)
            # Publish intent before the append.  A crash before the append leaves
            # a harmless marker that rebuilds the prior revision; a crash after
            # it can no longer expose stale projections without a dirty marker.
            self._mark_dirty(
                revision=len(candidate),
                phase="append",
                as_of=as_of,
            )
            _append_line_durable(
                self.paths.events,
                self._canonical_json(normalized).encode("utf-8") + b"\n",
            )
            committed = [*events, normalized]
            return self._publish_locked(
                committed,
                as_of=as_of,
                appended=True,
                idempotent=False,
                rebuilt=False,
            )
        finally:
            _release_lock(lock)

    def rebuild_projections(self, *, as_of: str) -> ReconcileResult:
        lock = self._lock()
        try:
            self._ensure_not_rewind_locked(as_of)
            events = self._validate_chain(self.read_events())
            return self._publish_locked(
                events,
                as_of=as_of,
                appended=False,
                idempotent=False,
                rebuilt=True,
            )
        finally:
            _release_lock(lock)

    def _projection_status_locked(self) -> dict[str, Any]:
        events = self._validate_chain(self.read_events())
        marker = self._dirty_marker()
        current = self.read_current()
        revision = len(events)
        reasons: list[str] = []
        notices: list[str] = []
        ontology_unresolved_skips: list[dict[str, Any]] = []
        ontology_receipt: dict[str, Any] | None = None
        ontology_verification: dict[str, Any] | None = None
        if marker is not None:
            reasons.append("dirty marker present")
        if current is None:
            if events:
                reasons.append("current snapshot missing")
        else:
            current_revision = current.get("project_revision", current.get("revision"))
            if current_revision != revision:
                reasons.append(
                    f"current revision {current_revision!r} != event revision {revision}"
                )
            current_as_of = current.get("as_of")
            try:
                self._parse_materialization_time(current_as_of, field="current.as_of")
                replayed = _json_clone(
                    self._reduce_current(events, as_of=current_as_of), self._canonical_json,
                )
            except Exception as exc:
                reasons.append(f"current snapshot cannot be replay-anchored: {exc}")
            else:
                actual_event_hash = self._canonical_sha256(events)
                if current.get("event_log_sha256") != actual_event_hash:
                    reasons.append("current event_log_sha256 differs from immutable event log")
                if replayed.get("event_log_sha256") != actual_event_hash:
                    reasons.append("reducer event_log_sha256 differs from immutable event log")
                embedded_state_hash = current.get("state_sha256")
                unsigned_current = dict(current)
                unsigned_current.pop("state_sha256", None)
                if embedded_state_hash != self._canonical_sha256(unsigned_current):
                    reasons.append("current embedded state_sha256 is invalid")
                if current.get("state_sha256") != replayed.get("state_sha256"):
                    reasons.append("current state_sha256 differs from immutable-history replay")
                if self._canonical_json(current) != self._canonical_json(replayed):
                    reasons.append("current snapshot differs from immutable-history replay")
        metadata_path = self.paths.projections / "projection-metadata.json"
        if current is not None and not metadata_path.exists():
            reasons.append("projection metadata missing")
        elif metadata_path.exists():
            metadata = self._read_json_file(metadata_path)
            if not isinstance(metadata, dict):
                reasons.append("projection metadata is not an object")
            elif current is not None:
                expected_state_hash = self._canonical_sha256(current)
                if metadata.get("input_sha256") != expected_state_hash:
                    reasons.append("projection input hash differs from current snapshot")
                meta_revision = metadata.get("project_revision")
                if meta_revision != revision:
                    reasons.append(
                        f"projection revision {meta_revision!r} != event revision {revision}"
                    )
                hashes = metadata.get("output_sha256", {})
                if not isinstance(hashes, dict):
                    reasons.append("projection output hash map is invalid")
                else:
                    expected_outputs = REQUIRED_PROJECTION_FILES - {"projection-metadata.json"}
                    for name in sorted(expected_outputs - set(hashes)):
                        reasons.append(f"projection metadata omits generated view: {name}")
                    for name, expected_hash in sorted(hashes.items()):
                        path = self.paths.projections / str(name)
                        if not path.is_file():
                            reasons.append(f"projection missing: {name}")
                            continue
                        actual = hashlib.sha256(path.read_bytes()).hexdigest()
                        if actual != expected_hash:
                            reasons.append(f"projection hash mismatch: {name}")
        if current is not None:
            if not self.paths.ontology_sync.exists():
                reasons.append("ontology sync receipt missing")
            else:
                raw_receipt = self._read_json_file(self.paths.ontology_sync)
                try:
                    ontology_receipt = self._validate_ontology_receipt(current, raw_receipt)
                except ProjectStateStoreError as exc:
                    reasons.append(f"ontology sync receipt invalid: {exc}")
                else:
                    ontology_unresolved_skips = copy.deepcopy(
                        ontology_receipt["result"]["unresolved_skips"]
                    )
                    for skip in ontology_unresolved_skips:
                        notices.append(
                            "ontology unresolved skip "
                            f"{skip.get('code', 'UNKNOWN')}: {skip.get('detail', '')}"
                        )
                    try:
                        from .project_state_ontology import verify_project_state_ontology

                        ontology_verification = verify_project_state_ontology(
                            current, workspace=self.workspace,
                        )
                    except Exception as exc:
                        reasons.append(f"ontology verification failed: {exc}")
                    else:
                        for pending in ontology_verification.get("pending_entity_ids", []):
                            reasons.append(f"ontology entity drift pending: {pending}")
                        for pending in ontology_verification.get("pending_relation_ids", []):
                            reasons.append(f"ontology relation drift pending: {pending}")
                        warnings = ontology_verification.get("warnings", [])
                        if self._canonical_json(warnings) != self._canonical_json(
                            ontology_unresolved_skips
                        ):
                            reasons.append(
                                "ontology verification warnings differ from sync receipt"
                            )
        return {
            "clean": not reasons,
            "revision": revision,
            "dirty_marker": marker,
            "reasons": reasons,
            "notices": notices,
            "documents_path": str(self.paths.projections / "documents.json"),
            "ontology_sync_path": str(self.paths.ontology_sync),
            "ontology_sync_receipt": ontology_receipt,
            "ontology_unresolved_skips": ontology_unresolved_skips,
            "ontology_verification": ontology_verification,
        }

    def projection_status(self) -> dict[str, Any]:
        """Replay-anchor every projection before a reader may trust it."""
        lock = self._lock()
        try:
            return self._projection_status_locked()
        finally:
            _release_lock(lock)

    def recover_if_dirty(self, *, as_of: str) -> ReconcileResult:
        lock = self._lock()
        try:
            self._ensure_not_rewind_locked(as_of)
            status = self._projection_status_locked()
            events = self._validate_chain(self.read_events())
            if not status["clean"]:
                return self._publish_locked(
                    events,
                    as_of=as_of,
                    appended=False,
                    idempotent=False,
                    rebuilt=True,
                )
            state = _json_clone(self._reduce_current(events, as_of=as_of), self._canonical_json)
            return ReconcileResult(
                revision=len(events),
                appended=False,
                idempotent=False,
                rebuilt=False,
                event_log_sha256=self._canonical_sha256(events),
                current_sha256=self._canonical_sha256(state),
                projection_files=tuple(
                    sorted(
                        path.relative_to(self.paths.projections).as_posix()
                        for path in self.paths.projections.rglob("*")
                        if path.is_file()
                    )
                ),
            )
        finally:
            _release_lock(lock)


def build_closeout_event_bundle(
    *,
    event_id: str,
    project_id: str,
    run_id: str,
    task_id: str,
    subject_id: str,
    subject_type: str,
    scope: Mapping[str, Any],
    authority: Mapping[str, Any],
    expected_prior_revision: int,
    observed_at: str,
    valid_from: str,
    appended_at: str,
    lifecycle_state: str,
    source: Mapping[str, Any],
    event_type: str = "STATE_ASSERTED",
    claims: Sequence[str] = (),
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    blockers: Sequence[str] = (),
    next_actions: Sequence[str] = (),
    affected_document_ids: Sequence[str] = (),
    affected_capability_ids: Sequence[str] = (),
    supersedes: Sequence[str] = (),
    invalidates: Sequence[str] = (),
    expires_at: str | None = None,
    canonical_json_fn: Callable[[Any], str] = _fallback_canonical_json,
) -> dict[str, Any]:
    """Build one bounded closeout event without performing filesystem I/O.

    In particular, this adapter never writes ``HANDOFF.md``.  Its result must
    still pass the project-state reducer's event validator before the store
    will commit it.
    """
    scalar_fields = {
        "event_id": event_id,
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "subject_id": subject_id,
        "subject_type": subject_type,
        "event_type": event_type,
        "observed_at": observed_at,
        "valid_from": valid_from,
        "appended_at": appended_at,
        "lifecycle_state": lifecycle_state,
    }
    for name, value in scalar_fields.items():
        if not isinstance(value, str) or not value.strip():
            raise CloseoutBundleError(f"{name} must be a non-empty string")
        if len(value) > 4096:
            raise CloseoutBundleError(f"{name} exceeds the closeout bound")
    if (
        isinstance(expected_prior_revision, bool)
        or not isinstance(expected_prior_revision, int)
        or expected_prior_revision < 0
    ):
        raise CloseoutBundleError("expected_prior_revision must be a non-negative integer")
    sequences = {
        "claims": claims,
        "evidence_refs": evidence_refs,
        "blockers": blockers,
        "next_actions": next_actions,
        "affected_document_ids": affected_document_ids,
        "affected_capability_ids": affected_capability_ids,
        "supersedes": supersedes,
        "invalidates": invalidates,
    }
    for name, values in sequences.items():
        if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
            raise CloseoutBundleError(f"{name} must be a sequence")
        if len(values) > MAX_CLOSEOUT_LIST_ITEMS:
            raise CloseoutBundleError(f"{name} exceeds the closeout item bound")

    event = {
        "schema_version": 1,
        "event_id": event_id,
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "subject_id": subject_id,
        "subject_type": subject_type,
        "scope": copy.deepcopy(dict(scope)),
        "event_type": event_type,
        "expected_prior_revision": expected_prior_revision,
        "authority": copy.deepcopy(dict(authority)),
        "observed_at": observed_at,
        "valid_from": valid_from,
        "expires_at": expires_at,
        "appended_at": appended_at,
        "lifecycle_state": lifecycle_state,
        "claims": copy.deepcopy(list(claims)),
        "blockers": copy.deepcopy(list(blockers)),
        "next_actions": copy.deepcopy(list(next_actions)),
        "affected_document_ids": list(affected_document_ids),
        "affected_capability_ids": list(affected_capability_ids),
        "evidence_refs": copy.deepcopy(list(evidence_refs)),
        "supersedes": list(supersedes),
        "invalidates": list(invalidates),
        "source": copy.deepcopy(dict(source)),
    }
    try:
        cloned = json.loads(canonical_json_fn(event))
        size = len(canonical_json_fn(cloned).encode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CloseoutBundleError(f"closeout is not canonical JSON: {exc}") from exc
    if size > MAX_CLOSEOUT_BUNDLE_BYTES:
        raise CloseoutBundleError("closeout event exceeds the byte bound")
    from .project_state import validate_event

    valid, problems = validate_event(cloned)
    if not valid:
        raise CloseoutBundleError(f"closeout event is invalid: {problems}")
    return cloned


__all__ = [
    "AuthorityVerifier",
    "CloseoutBundleError",
    "CorruptEventLogError",
    "EventAppender",
    "LockUnavailableError",
    "OntologySynchronizer",
    "ONTOLOGY_RECEIPT_SCHEMA_VERSION",
    "ProjectStateStore",
    "ProjectStateStoreError",
    "ProjectionCommitError",
    "ProjectionRenderer",
    "ReconcileResult",
    "REQUIRED_PROJECTION_FILES",
    "ReplayResult",
    "StateReducer",
    "StorePaths",
    "StoreRevisionConflictError",
    "TimeRewindError",
    "UnsafeProjectionPathError",
    "build_closeout_event_bundle",
]
