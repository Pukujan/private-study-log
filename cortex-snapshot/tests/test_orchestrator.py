"""GAP I3 orchestrator: driver -> tiered-worker parallel fan-out.

Proves the four required properties with ZERO network (injected decompose + worker):
  1. a task fans out to N workers;
  2. concurrency is bounded (both the global max_workers pool cap AND the per-tier
     account cap in judge.MAX_CONCURRENT_BY_TIER, e.g. qwen35b=2);
  3. results reconcile (every subtask accounted for, succeeded/failed partitioned,
     outputs mapped back to ids);
  4. a worker failure is HANDLED (recorded as a failed result + retried), not lost.
"""
from __future__ import annotations

import threading
import time

import pytest

from cortex_core import orchestrator as orch
from cortex_core.orchestrator import Subtask


@pytest.fixture(autouse=True)
def _isolate_locks(tmp_path, monkeypatch):
    """Every test gets a fresh cross-process lock dir, so gated-tier (qwen35b) slots can
    never collide with a real background run's .locks/ or with a sibling test."""
    monkeypatch.setattr(orch._J, "_LOCK_DIR", tmp_path / ".locks")


def _fixed_decompose(n: int, role: str = "impl"):
    return lambda task: [{"prompt": f"{task} :: part {i}", "role": role} for i in range(n)]


# --- 1. fan-out to N workers ---------------------------------------------------------------

def test_fans_out_to_n_workers():
    seen: list[str] = []
    lock = threading.Lock()

    def worker(st: Subtask) -> str:
        with lock:
            seen.append(st.subtask_id)
        return f"done:{st.prompt}"

    rec = orch.orchestrate("build X", decompose=_fixed_decompose(5),
                           worker=worker, max_workers=5)

    assert len(rec.results) == 5
    assert len(set(r.subtask_id for r in rec.results)) == 5  # unique subtasks
    assert set(seen) == set(r.subtask_id for r in rec.results)  # every one actually ran
    assert rec.all_ok


# --- 2a. bounded by the global max_workers pool --------------------------------------------

def test_concurrency_bounded_by_max_workers():
    live = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def worker(st: Subtask) -> str:
        with lock:
            live["n"] += 1
            live["peak"] = max(live["peak"], live["n"])
        time.sleep(0.05)  # hold the slot so overlap is observable
        with lock:
            live["n"] -= 1
        return "ok"

    # 8 subtasks on a strong tier (glm5.2 -> NOT in MAX_CONCURRENT_BY_TIER, so only the
    # pool cap constrains), max_workers=3.
    rec = orch.orchestrate("t", decompose=_fixed_decompose(8, role="research"),
                           worker=worker, max_workers=3)

    assert len(rec.results) == 8
    assert live["peak"] <= 3, f"pool cap breached: peak={live['peak']}"
    assert rec.peak_concurrency <= 3


# --- 2b. bounded by the per-tier account cap (qwen35b = 2), even with a wide pool ----------

def test_concurrency_bounded_by_per_tier_cap(tmp_path, monkeypatch):
    # Isolate the file-lock dir so this test can't collide with a real run's .locks/.
    monkeypatch.setattr(orch._J, "_LOCK_DIR", tmp_path / ".locks")
    # Confirm the account cap we rely on is really 2 (grounds the assertion below).
    assert orch._J.MAX_CONCURRENT_BY_TIER.get("qwen35b") == 2

    live = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def worker(st: Subtask) -> str:
        with lock:
            live["n"] += 1
            live["peak"] = max(live["peak"], live["n"])
        time.sleep(0.05)
        with lock:
            live["n"] -= 1
        return "ok"

    # 6 qwen35b subtasks, pool WIDER (max_workers=6) than the tier cap -> the per-tier
    # concurrency_slot must clamp real in-body overlap to 2.
    rec = orch.orchestrate("t", decompose=_fixed_decompose(6, role="impl"),
                           worker=worker, max_workers=6,
                           weak_tier="qwen35b")

    assert all(r.tier == "qwen35b" for r in rec.results)
    assert len(rec.results) == 6
    assert live["peak"] <= 2, f"qwen 2-concurrent account cap breached: peak={live['peak']}"
    assert rec.peak_concurrency <= 2


# --- 3. results reconcile ------------------------------------------------------------------

def test_results_reconcile():
    def worker(st: Subtask) -> str:
        return f"OUT[{st.subtask_id}]"

    rec = orch.orchestrate("t", decompose=_fixed_decompose(4), worker=worker, max_workers=4)

    assert len(rec.succeeded) == 4
    assert rec.failed == []
    omap = rec.output_map()
    assert set(omap) == set(r.subtask_id for r in rec.results)
    for sid, out in omap.items():
        assert out == f"OUT[{sid}]"  # each output maps back to its own subtask
    # deterministic ordering regardless of completion race
    assert [r.subtask_id for r in rec.results] == sorted(r.subtask_id for r in rec.results)


# --- 4. worker failure is handled, not lost ------------------------------------------------

def test_worker_failure_is_recorded_not_lost():
    def worker(st: Subtask) -> str:
        if st.prompt.endswith("part 2"):
            raise ValueError("boom on part 2")
        return "ok"

    rec = orch.orchestrate("t", decompose=_fixed_decompose(4), worker=worker,
                           max_workers=4, retries=1)

    assert len(rec.results) == 4  # nothing dropped
    assert len(rec.failed) == 1
    (bad,) = rec.failed
    assert bad.ok is False
    assert "boom on part 2" in bad.error
    assert bad.output is None
    assert not rec.all_ok
    assert len(rec.succeeded) == 3


def test_worker_failure_retried_then_succeeds():
    attempts: dict[str, int] = {}
    lock = threading.Lock()

    def flaky(st: Subtask) -> str:
        with lock:
            attempts[st.subtask_id] = attempts.get(st.subtask_id, 0) + 1
            n = attempts[st.subtask_id]
        if n < 2:  # fail the first attempt for every subtask, succeed on retry
            raise RuntimeError("transient")
        return "recovered"

    rec = orch.orchestrate("t", decompose=_fixed_decompose(3), worker=flaky,
                           max_workers=3, retries=2)

    assert rec.all_ok
    assert all(r.attempts == 2 for r in rec.results)  # retried exactly once each
    assert all(r.output == "recovered" for r in rec.results)


# --- role -> tier policy -------------------------------------------------------------------

def test_role_tier_assignment():
    assert orch.assign_tier("research") == orch.DEFAULT_STRONG_TIER
    assert orch.assign_tier("spec") == orch.DEFAULT_STRONG_TIER
    assert orch.assign_tier("impl") == orch.DEFAULT_WEAK_TIER
    assert orch.assign_tier("mechanical") == orch.DEFAULT_WEAK_TIER
    assert orch.assign_tier("research", strong_tier="opus") == "opus"

    # a mixed decomposition routes each role to the right tier
    def mixed(task):
        return [{"prompt": "design the schema", "role": "spec"},
                {"prompt": "write the CRUD boilerplate", "role": "impl"}]

    rec = orch.orchestrate("app", decompose=mixed, worker=lambda st: st.tier, max_workers=2)
    by_role = {r.role: r.tier for r in rec.results}
    assert by_role["spec"] == orch.DEFAULT_STRONG_TIER
    assert by_role["impl"] == orch.DEFAULT_WEAK_TIER


def test_empty_decomposition_is_safe():
    rec = orch.orchestrate("t", decompose=lambda t: [], worker=lambda st: "x")
    assert rec.results == []
    assert rec.peak_concurrency == 0
    assert not rec.all_ok


def test_concurrency_slot_survives_permissionerror_on_open(tmp_path, monkeypatch):
    """Regression (GAP I3): on Windows a concurrent unlink/O_EXCL-open on the same slot
    raises PermissionError (a subclass of OSError), NOT FileExistsError. judge.concurrency_slot
    must treat that as 'slot busy, retry' and never propagate it as a spurious failure --
    otherwise a fast slot-cycling fan-out sees fake task failures."""
    import os as _os
    monkeypatch.setattr(orch._J, "_LOCK_DIR", tmp_path / ".locks")
    monkeypatch.setitem(orch._J.MAX_CONCURRENT_BY_TIER, "qwen35b", 1)

    real_open = _os.open
    state = {"n": 0}

    def flaky_open(path, flags, *a, **k):
        # Fail the first O_EXCL create attempt with PermissionError, then behave normally.
        if flags & _os.O_EXCL and state["n"] == 0:
            state["n"] += 1
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(orch._J.os, "open", flaky_open)

    with orch._J.concurrency_slot("qwen35b", timeout_s=5, poll_s=0.01):
        pass  # must acquire despite the first PermissionError, not raise
    assert state["n"] == 1  # the PermissionError path was actually exercised


def test_explicit_tier_override_in_subtask():
    def decompose(task):
        return [{"prompt": "p", "role": "impl", "tier": "big-pickle"}]

    rec = orch.orchestrate("t", decompose=decompose, worker=lambda st: st.tier, max_workers=1)
    assert rec.results[0].tier == "big-pickle"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
