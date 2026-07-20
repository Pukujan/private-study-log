"""A deep-research task whose worker/process dies mid-run must be reported as `died` (with a restart
hint), not as an eternal `running`. This is the fix for the "background thread died with the process"
failure: durable on-disk state alone couldn't distinguish a live run from a dead one."""
from __future__ import annotations

import json

import pytest

from cortex_core import deep_research as dr


@pytest.fixture(autouse=True)
def _isolated_ws(tmp_path, monkeypatch):
    """Make tmp_path resolve as its own workspace (an `audit/` dir marks it) and ignore any ambient
    CORTEX_WORKSPACE so the test can't read/write the real repo."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    (tmp_path / "audit").mkdir(exist_ok=True)


def _write_rec(ws, task_id, **rec):
    d = ws / "research" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"task_id": task_id, **rec}
    (d / f"{task_id}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def test_stale_running_task_reported_as_died(tmp_path):
    # a run left "running" with a heartbeat well past the stale window, and a dead pid
    _write_rec(tmp_path, "t1", state="running", created="2020-01-01T00:00:00Z",
               heartbeat="2020-01-01T00:00:00Z", pid=2_000_000_000)  # pid that cannot exist
    out = dr.research_status("t1", workspace=tmp_path)
    assert out["state"] == "died"
    assert out["last_live_state"] == "running"
    assert "restart" in out["hint"].lower()


def test_fresh_running_task_stays_running(tmp_path):
    # a heartbeat from just now -> still alive, must NOT be flipped to died
    _write_rec(tmp_path, "t2", state="running", created=dr._now(), heartbeat=dr._now())
    out = dr.research_status("t2", workspace=tmp_path)
    assert out["state"] == "running"


def test_terminal_task_never_flipped(tmp_path):
    # an old DONE task must stay done even though its timestamps are ancient
    _write_rec(tmp_path, "t3", state="done", created="2020-01-01T00:00:00Z",
               heartbeat="2020-01-01T00:00:00Z")
    assert dr.research_status("t3", workspace=tmp_path)["state"] == "done"


def test_synchronous_run_completes_and_heartbeats_stop(tmp_path, monkeypatch):
    # background=False runs inline to a terminal state; a completed run is not "died"
    monkeypatch.setattr(dr, "run_research", lambda q, workspace=None, **k: {
        "report_path": "research/none.md", "fetch": {"fetched": []}})
    monkeypatch.setattr(dr, "_grounding", lambda ws, result: {"skipped": "test"})
    out = dr.start_deep_research("does the liveness path work?", workspace=tmp_path, background=False)
    assert out["state"] == "done"


def test_worker_killed_mid_run_is_detected_as_died_via_real_thread(tmp_path, monkeypatch):
    """Drive the ACTUAL background thread + heartbeat loop (not synthetic pre-written state):
    start a real task whose research call hangs forever (simulating a worker that got killed
    mid-run and stopped heartbeating), shrink the stale window so the test doesn't wait 90s,
    and confirm a poll after the window reports state='died' with a restart hint -- the exact
    "background thread died with the process" failure this module exists to catch."""
    import threading
    import time as _time

    hung = threading.Event()

    def _hang_forever(q, workspace=None, **k):
        # simulate a worker whose heartbeat thread has stopped updating (as if killed) by
        # blocking the run itself; the heartbeat loop is a separate real thread started by
        # start_deep_research, so it WOULD keep beating -- to simulate a true process-death we
        # instead shrink the stale window below and just never let this call return quickly.
        hung.set()
        _time.sleep(5)
        return {"report_path": "research/none.md", "fetch": {"fetched": []}}

    monkeypatch.setattr(dr, "run_research", _hang_forever)
    monkeypatch.setattr(dr, "_HEARTBEAT_SECONDS", 100)  # heartbeat loop won't beat again in time
    monkeypatch.setattr(dr, "_STALE_SECONDS", 0.2)  # shrink so the test resolves fast

    handle = dr.start_deep_research("mid-run kill test", workspace=tmp_path, background=True)
    task_id = handle["task_id"]
    assert hung.wait(timeout=5), "worker thread never started"

    _time.sleep(0.4)  # let the shrunk stale window elapse with no second heartbeat
    out = dr.research_status(task_id, workspace=tmp_path)
    assert out["state"] == "died"
    assert "restart" in out["hint"].lower()
    assert "cortex_deep_research" in out["hint"]
