"""GAP-CORTEX-0016: shared, file-backed task-coordination ledger.

Independent orchestrators (Hermes/DeepSeek, Claude/Fable, Codex, Aider) work
against the same repo but Cortex had no concept of task *ownership* -- no shared
list where a peer can see what tasks exist, claim one, check whether another
agent already owns it, or mark it done. This is that list. It is deliberately
the *same substrate* as the audit log: plain files in the workspace, no network
service (v1). See ``templates/workspace-control-plane/gaps/GAP-CORTEX-0016.md``.

Storage is an **append-only JSONL** event log at ``logs/task_ledger.jsonl``:
every create/claim/update appends one full state-snapshot line, so the file is
a durable audit trail (nothing is ever rewritten in place) and the *current*
state of a task is the last line that mentions its ``task_id``.

Claim atomicity is the whole point -- last-writer-wins is NOT enough, since two
agents can read "pending" and both append a claim. The read-check-append
critical section is therefore serialized by an **exclusive-create lockfile**
(``os.O_CREAT | os.O_EXCL``), the same discipline as the Phase-0 rebuild lock
(``cortex_core/search.py``): a crashed holder's lock is stolen once it goes
stale (dead PID or age), so a crash mid-claim can never wedge the ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uuid6

from .config import make_stdio_encoding_safe, resolve_workspace_override

# The four lifecycle states a task can be in. pending -> active on claim;
# active -> done/failed on update. Kept small on purpose (v1 is claim-and-check,
# not a scheduler -- GAP-0016 "Out of scope").
VALID_STATUSES = ("pending", "active", "done", "failed")

_LOCK_STALE_AFTER = 30.0  # seconds; a lock older than this with a dead PID is stolen
_LOCK_WAIT_TIMEOUT = 10.0  # seconds to wait for a live holder before giving up


def ledger_path(workspace: str | Path | None = None) -> Path:
    # Arg-first: an explicit workspace wins over CORTEX_WORKSPACE (the MCP layer already made the
    # tenant-pin decision and passes a concrete path); an omitted workspace falls back env-first.
    ws = resolve_workspace_override(workspace)
    return ws / "logs" / "task_ledger.jsonl"


def _lock_path(led_path: Path) -> Path:
    return led_path.with_name(led_path.name + ".lock")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_task_id() -> str:
    # UUIDv7: time-sortable + collision-free by construction, so a generated id
    # never needs a read-before-write uniqueness check (same rationale as the
    # audit closeout suffix in cortex_core/audit.py).
    return f"task-{uuid6.uuid7()}"


def _pid_alive(pid: int) -> bool:
    """Read-only cross-platform liveness probe (mirrors search.py). On Windows
    os.kill(pid, 0) *terminates* the target, so use a read-only OpenProcess
    handle instead; on POSIX os.kill(pid, 0) is a genuine no-op signal."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
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


def _lock_is_stale(lock_path: Path) -> bool:
    """A lock is stale if it's unreadable, older than _LOCK_STALE_AFTER, or the
    PID that wrote it is gone -- so a crash mid-claim can't wedge the ledger."""
    try:
        raw = lock_path.read_text(encoding="utf-8").strip().split("\n")
        pid = int(raw[0])
        ts = float(raw[1])
    except (OSError, ValueError, IndexError):
        return True
    if time.time() - ts > _LOCK_STALE_AFTER:
        return True
    return not _pid_alive(pid)


def _acquire_lock(lock_path: Path) -> Path | None:
    """Serialize the claim/update critical section via an exclusive-create
    lockfile (O_CREAT | O_EXCL -- the atomic mutual-exclusion primitive;
    os.replace can't do this because it overwrites unconditionally). Steals a
    stale/crashed lock. Returns the lock path on success, or None if a *live*
    holder outlasts the wait -- the caller must treat None as "did not acquire"
    and refuse to mutate, so concurrency safety is never silently dropped."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}\n{time.time()}".encode())
            finally:
                os.close(fd)
            return lock_path
        except (FileExistsError, PermissionError):
            # FileExistsError: a live or stale holder owns the lock. PermissionError:
            # on Windows, opening a *delete-pending* lockfile (another claimant just
            # released it and the handle is mid-close) surfaces as ERROR_ACCESS_DENIED
            # rather than FileExistsError -- also transient contention, not a real fault.
            # Both are retried under the same deadline so a concurrent release can't crash
            # a competing claimant (the flake seen on windows/3.12 CI). The deadline check
            # lives outside the stale branch so a genuinely un-writable dir can't hot-loop.
            if _lock_is_stale(lock_path):
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


def _release_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        os.remove(lock_path)
    except OSError:
        pass


def _append(led_path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line. Callers hold the lock across read+append so the
    file stays a consistent event log; a single write of one line is the
    smallest durable unit here."""
    led_path.parent.mkdir(parents=True, exist_ok=True)
    with led_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _read_records(led_path: Path) -> list[dict[str, Any]]:
    if not led_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with led_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line (crash mid-append) must not poison reads;
                # skip it and keep the rest of the log usable.
                continue
    return records


def _current_state(led_path: Path) -> dict[str, dict[str, Any]]:
    """Reduce the append-only log to current per-task state: the last record
    written for each task_id wins (records are appended in causal order under
    the lock, so the final line is the live truth)."""
    state: dict[str, dict[str, Any]] = {}
    for rec in _read_records(led_path):
        tid = rec.get("task_id")
        if tid:
            state[tid] = rec
    return state


def create_task(
    description: str,
    author_model: str,
    workspace: str | Path | None = None,
    task_id: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """Seed a task into the ledger (the CLI/handoff-seeding path -- handoffs are
    task *sources*, not the ledger itself). Returns the created record, or
    ``{"created": False, ...}`` if that task_id already exists."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; must be one of {VALID_STATUSES}")
    led_path = ledger_path(workspace)
    tid = task_id or _new_task_id()
    lock = _acquire_lock(_lock_path(led_path))
    if lock is None:
        return {"created": False, "reason": "could not acquire ledger lock", "task_id": tid}
    try:
        state = _current_state(led_path)
        if tid in state:
            return {"created": False, "reason": "task_id already exists", "task_id": tid}
        now = _now()
        record = {
            "task_id": tid,
            "status": status,
            "owner": None,
            "description": description,
            "author_model": author_model,
            "created_at": now,
            "claimed_at": None,
            "updated_at": now,
            "event": "create",
        }
        _append(led_path, record)
        return {"created": True, **record}
    finally:
        _release_lock(lock)


def list_tasks(
    workspace: str | Path | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    """Current state of every task (reads are open to any peer -- no lock, a
    stale read at worst misses an in-flight claim, which the atomic claim below
    then rejects). Optionally filter by status. Sorted by task_id for a stable
    order."""
    led_path = ledger_path(workspace)
    tasks = list(_current_state(led_path).values())
    if status is not None:
        tasks = [t for t in tasks if t.get("status") == status]
    return sorted(tasks, key=lambda t: t.get("task_id", ""))


def claim_task(
    task_id: str, owner: str, workspace: str | Path | None = None
) -> dict[str, Any]:
    """Atomically claim a pending task for ``owner``. Exactly one of two racing
    claimants wins: the read-check-append runs under the exclusive lock, so the
    loser sees the winner's claim and is refused. Returns
    ``{"claimed": True, ...}`` on success, else ``{"claimed": False, "reason": ...}``."""
    led_path = ledger_path(workspace)
    lock = _acquire_lock(_lock_path(led_path))
    if lock is None:
        return {"claimed": False, "reason": "could not acquire ledger lock", "task_id": task_id}
    try:
        state = _current_state(led_path)
        current = state.get(task_id)
        if current is None:
            return {"claimed": False, "reason": "no such task", "task_id": task_id}
        if current.get("status") != "pending" or current.get("owner"):
            return {
                "claimed": False,
                "reason": f"already owned by {current.get('owner')!r} (status={current.get('status')})",
                "task_id": task_id,
                "owner": current.get("owner"),
                "status": current.get("status"),
            }
        now = _now()
        record = {
            **current,
            "status": "active",
            "owner": owner,
            "claimed_at": now,
            "updated_at": now,
            "event": "claim",
        }
        _append(led_path, record)
        return {"claimed": True, **record}
    finally:
        _release_lock(lock)


def update_task(
    task_id: str,
    workspace: str | Path | None = None,
    status: str | None = None,
    owner: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """Update a task's status/owner/result (e.g. active -> done|failed). Runs
    under the lock so it reads-modifies-appends on top of the live record, never
    a stale one. Returns ``{"updated": True, ...}`` or a refusal."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; must be one of {VALID_STATUSES}")
    led_path = ledger_path(workspace)
    lock = _acquire_lock(_lock_path(led_path))
    if lock is None:
        return {"updated": False, "reason": "could not acquire ledger lock", "task_id": task_id}
    try:
        state = _current_state(led_path)
        current = state.get(task_id)
        if current is None:
            return {"updated": False, "reason": "no such task", "task_id": task_id}
        record = {**current, "updated_at": _now(), "event": "update"}
        if status is not None:
            record["status"] = status
        if owner is not None:
            record["owner"] = owner
        if result is not None:
            record["result"] = result
        _append(led_path, record)
        return {"updated": True, **record}
    finally:
        _release_lock(lock)


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """CLI wrapper for manual ledger operations (seeding from handoffs,
    inspecting/claiming/closing tasks by hand). Same code the MCP tools call."""
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex task-coordination ledger (GAP-0016)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="seed a task into the ledger")
    p_create.add_argument("--description", required=True)
    p_create.add_argument("--author-model", required=True)
    p_create.add_argument("--task-id", default=None)
    p_create.add_argument("--status", default="pending", choices=VALID_STATUSES)

    p_list = sub.add_parser("list", help="list current task state")
    p_list.add_argument("--status", default=None, choices=VALID_STATUSES)

    p_claim = sub.add_parser("claim", help="atomically claim a pending task")
    p_claim.add_argument("--task-id", required=True)
    p_claim.add_argument("--owner", required=True)

    p_update = sub.add_parser("update", help="update a task's status/owner/result")
    p_update.add_argument("--task-id", required=True)
    p_update.add_argument("--status", default=None, choices=VALID_STATUSES)
    p_update.add_argument("--owner", default=None)
    p_update.add_argument("--result", default=None)

    args = parser.parse_args(argv)
    if args.command == "create":
        _print(create_task(args.description, args.author_model, task_id=args.task_id, status=args.status))
    elif args.command == "list":
        _print(list_tasks(status=args.status))
    elif args.command == "claim":
        _print(claim_task(args.task_id, args.owner))
    elif args.command == "update":
        _print(update_task(args.task_id, status=args.status, owner=args.owner, result=args.result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
