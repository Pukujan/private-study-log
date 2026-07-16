"""RED tests for Tier-0 item 1 — SQLite WAL mode on every connection.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§1).

``CortexSearchIndex.connect()`` must open every connection (read *and*
write path — it is the single connection helper everything funnels
through) in **WAL journal mode** with an explicit **busy_timeout**, so
that ``--hybrid`` reads can proceed during a concurrent ``--index`` write
and concurrent writers wait-and-retry instead of crashing with
``sqlite3.OperationalError: database is locked`` (finding #2 of
``reviewed/opus-deep-review-2026-07-03.md``; PHASE-GATES 0.6).

Notes on what is / isn't a red anchor here:
  * ``journal_mode`` is the genuine red: the current code opens with the
    default rollback journal (``delete``), never WAL.
  * ``busy_timeout`` is already 5000ms *incidentally*, because Python's
    ``sqlite3.connect`` defaults ``timeout=5.0`` -> ``busy_timeout=5000``.
    The busy_timeout test is therefore a **green control**: it documents
    the contract requirement (a multi-second busy_timeout on the one
    connection helper) and guards against a regression that drops it, but
    it is not expected to be red against the current code.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def test_connect_enables_wal_journal_mode(tmp_path: Path, monkeypatch) -> None:
    """RED: every connection from ``connect()`` must be in WAL mode so a
    reader (``--hybrid``) and a writer (``--index``) can coexist. The
    current code leaves the default ``delete`` journal."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    index = CortexSearchIndex(_make_workspace(tmp_path))

    conn = index.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert mode.lower() == "wal", (
        f"connect() opened journal_mode={mode!r}; contract requires 'wal' "
        "so reads proceed during a concurrent rebuild"
    )


def test_connect_sets_busy_timeout(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): a multi-second busy_timeout must be in
    effect on the single connection helper, per BUILD-PLAN 0.6 addendum
    (~5000ms). This guards against a refactor that drops it below the
    production floor; it is not a red anchor because Python's
    ``sqlite3.connect(timeout=5.0)`` default already yields 5000ms."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    index = CortexSearchIndex(_make_workspace(tmp_path))

    conn = index.connect()
    try:
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert int(busy) >= 5000, (
        f"busy_timeout={busy}ms is below the ~5000ms floor the contract "
        "requires on every connection"
    )


def test_concurrent_connect_never_raises_database_is_locked(tmp_path: Path, monkeypatch) -> None:
    """RED (regression, Opus Stage F review of the Tier-0 diff, finding F1):
    ``connect()`` set ``PRAGMA journal_mode=WAL`` *before* ``PRAGMA
    busy_timeout=5000``. On a brand-new database file, switching journal
    mode itself needs exclusive access; when two connections race to be
    the first to make that switch, the loser can raise
    ``sqlite3.OperationalError: database is locked`` -- instantly, before
    busy_timeout is even in effect on that connection -- rather than
    waiting and retrying. An ordinary write lock (e.g. ``BEGIN EXCLUSIVE``
    held by another connection) does *not* reproduce this; it specifically
    requires two connections both attempting the journal-mode switch on
    the same fresh file at once, which is exactly what two concurrent
    ``cortex --index`` processes do. Live-reproduced two ways: real
    concurrent ``--index`` subprocesses against a fresh workspace hit this
    ~1/15 runs (traceback pointing at the ``journal_mode=WAL`` line
    exactly), and a raw two-thread race hit it 11/200 runs.

    Because the race is probabilistic, this test repeats it enough times
    (60 parallel-connect rounds against a shared fresh workspace) to make
    a pre-fix failure highly likely (~96% per a 5.5% single-shot rate) while
    the post-fix behavior is unconditional (busy_timeout active before the
    exclusive-lock-requiring pragma removes this specific race entirely)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    index = CortexSearchIndex(workspace)

    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _race_connect(barrier: threading.Barrier) -> None:
        barrier.wait()  # line both threads up to hit connect() at the same instant
        try:
            conn = index.connect()
            conn.close()
        except sqlite3.OperationalError as exc:
            with errors_lock:
                errors.append(exc)

    for round_num in range(60):
        # Fresh db file each round -- the race only exists on first-ever
        # journal-mode initialization, not on an already-WAL database.
        for suffix in ("", "-wal", "-shm"):
            candidate = index.index_db.with_name(index.index_db.name + suffix)
            candidate.unlink(missing_ok=True)

        barrier = threading.Barrier(2)
        threads = [threading.Thread(target=_race_connect, args=(barrier,)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            break

    assert not errors, (
        f"connect() raised {errors[0]!r} when two connections raced to "
        "initialize journal_mode=WAL on the same fresh database file -- "
        "busy_timeout must be set BEFORE the journal_mode=WAL pragma so "
        "SQLite's busy handler can wait out the race instead of failing "
        "instantly."
    )


# --- F1(a): rebuild lock (gate 0.6 "rebuild lock" + stale-lock handling) ----


def _seed_one_doc(workspace: Path) -> None:
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "a.md").write_text("# A\n\nalpha lockmarker content.\n", encoding="utf-8")


def test_rebuild_releases_its_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_one_doc(ws)
    idx = CortexSearchIndex(ws)
    idx.rebuild()
    assert not idx._rebuild_lock_path().exists(), "rebuild must release its lock"


def test_stale_lock_with_dead_pid_is_stolen(tmp_path: Path, monkeypatch) -> None:
    """A lock left by a crashed rebuild (its PID no longer alive) must be
    stolen so future rebuilds never wedge."""
    import subprocess
    import sys

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_one_doc(ws)
    idx = CortexSearchIndex(ws)

    # A definitely-dead-but-valid PID: spawn a trivial process and let it exit.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead_pid = proc.pid

    lock = idx._rebuild_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{dead_pid}\n{time.time()}", encoding="utf-8")  # fresh ts, dead pid

    meta = idx.rebuild()  # must steal the dead-pid lock and complete
    assert int(meta["document_count"]) >= 1
    assert not lock.exists()


def test_stale_lock_by_age_is_stolen(tmp_path: Path, monkeypatch) -> None:
    """Even a live PID's lock is stolen once it's older than stale_after, so a
    hung/reused-PID holder can't block forever."""
    import os as _os

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_one_doc(ws)
    idx = CortexSearchIndex(ws)
    lock = idx._rebuild_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    # This process's own PID (alive), but an ancient timestamp -> stale by age.
    lock.write_text(f"{_os.getpid()}\n{time.time() - 10_000}", encoding="utf-8")

    assert idx._rebuild_lock_is_stale(lock, stale_after=60.0) is True
    idx.rebuild()
    assert not lock.exists()


def test_two_concurrent_rebuilds_both_complete_and_index_intact(tmp_path: Path, monkeypatch) -> None:
    """gate 0.6: two rebuilds racing must both complete and leave the index
    intact. The lock serializes them (same-process threads share a PID, so the
    holder is seen as alive and the loser waits, then proceeds after release)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_one_doc(ws)
    idx = CortexSearchIndex(ws)

    errors: list[BaseException] = []
    errors_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def _race_rebuild() -> None:
        barrier.wait()
        try:
            idx.rebuild()
        except BaseException as exc:  # noqa: BLE001 - record any failure
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_race_rebuild) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent rebuild raised {errors[0]!r}"
    assert not idx._rebuild_lock_path().exists(), "lock must be released after both"
    results = idx.search("lockmarker")
    assert any(r.filename == "a.md" for r in results), "index must be intact after concurrent rebuilds"


def test_two_parallel_index_subprocesses_both_complete(tmp_path: Path, monkeypatch) -> None:
    """gate 0.6, its literal wording ('two parallel --index runs both complete,
    index intact') exercised with REAL processes, not threads."""
    import subprocess
    import sys

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_one_doc(ws)

    code = "from cortex_core.search import main; import sys; sys.exit(main(['--index', '--workspace', sys.argv[1]]))"
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(ws)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    for p in procs:
        p.wait(timeout=60)
    assert all(p.returncode == 0 for p in procs), "both --index processes must exit 0"

    idx = CortexSearchIndex(ws)
    assert not idx._rebuild_lock_path().exists(), "no stale lock left behind"
    assert any(r.filename == "a.md" for r in idx.search("lockmarker")), "index intact"
