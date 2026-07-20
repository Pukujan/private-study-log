"""Frozen tests for Deep Research Mode async task-handoff (cortex_core/deep_research.py)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import deep_research as DR  # noqa: E402


Q = "What is the rebuild lock and how does it prevent index corruption?"


def test_synchronous_run_reaches_done():
    rec = DR.start_deep_research(Q, background=False, do_fetch=False)
    assert rec["state"] in ("done", "failed")
    if rec["state"] == "done":
        assert rec["result"]["report_path"] and "grounding" in rec
        assert "coverage" in rec["result"]


def test_status_lookup_returns_same_task():
    rec = DR.start_deep_research(Q, background=False, do_fetch=False)
    again = DR.research_status(rec["task_id"])
    assert again["task_id"] == rec["task_id"] and again["state"] == rec["state"]


def test_unknown_task_is_reported_not_crashing():
    r = DR.research_status("does-not-exist-xyz")
    assert r["state"] == "unknown"


def test_background_handoff_returns_immediately_with_task_id():
    # background start must return a running handle without blocking on the whole run
    t0 = time.monotonic()
    handle = DR.start_deep_research(Q, background=True, do_fetch=False)
    elapsed = time.monotonic() - t0
    assert handle["state"] == "running" and handle["task_id"] and handle["poll_with"] == "cortex_research_status"
    # give the daemon thread a moment; then status must be a known state
    for _ in range(50):
        st = DR.research_status(handle["task_id"])
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert DR.research_status(handle["task_id"])["state"] in ("running", "done", "failed")


def test_grounding_uses_empty_context_guard_when_no_fetch():
    # corpus-only run has no fetched web sources -> grounding must flag empty_context,
    # never a false near-1.0 (the documented artifact)
    rec = DR.start_deep_research(Q, background=False, do_fetch=False)
    if rec["state"] == "done":
        g = rec["grounding"]
        assert g.get("empty_context") is True and g.get("score") == 0.0


def test_mcp_tools_registered():
    from cortex_core import mcp as M
    # the two async task-handoff tools must be importable/registered on the FastMCP server
    assert hasattr(M, "cortex_deep_research") and hasattr(M, "cortex_research_status")
