"""Guards the actual reason this module exists: two dispatched background agents went
untrackable tonight (no completion notification, "no task found" on retry) -- these tests pin
that the audit trail itself is a reliable substitute regardless of the notification channel."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from cortex_core.closeout_reconcile import find_recent_closeouts, summarize


def _write_closeout(workspace, name, task, status="completed", timestamp=None, version=None):
    agent_dir = workspace / "audit" / "audit-log-1" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": task,
        "status": status,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "cortex_version": version or {"commit": "abc1234", "dirty": True},
    }
    (agent_dir / f"cortex-closeout__{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_finds_closeouts_in_a_single_workspace(tmp_path):
    _write_closeout(tmp_path, "a", "build the thing")
    results = find_recent_closeouts([tmp_path])
    assert len(results) == 1
    assert results[0]["task"] == "build the thing"


def test_scans_multiple_workspaces_the_stale_server_misroute_case(tmp_path):
    """The exact scenario this tool exists for: a closeout that landed in the wrong (env-pinned)
    workspace due to a stale MCP server, plus one that landed correctly via the CLI fallback --
    both must be found when both workspace roots are given."""
    ws_a, ws_b = tmp_path / "correct_repo", tmp_path / "misrouted_workspace"
    _write_closeout(ws_a, "correct", "fix via CLI fallback")
    _write_closeout(ws_b, "misrouted", "fix that misrouted via stale MCP server")

    results = find_recent_closeouts([ws_a, ws_b])

    tasks = {r["task"] for r in results}
    assert tasks == {"fix via CLI fallback", "fix that misrouted via stale MCP server"}


def test_since_filter_excludes_older_closeouts(tmp_path):
    _write_closeout(tmp_path, "old", "old task", timestamp="2026-07-07T10:00:00+00:00")
    _write_closeout(tmp_path, "new", "new task", timestamp="2026-07-07T20:00:00+00:00")

    since = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    results = find_recent_closeouts([tmp_path], since=since)

    assert [r["task"] for r in results] == ["new task"]


def test_unparseable_timestamp_is_included_not_silently_dropped(tmp_path):
    _write_closeout(tmp_path, "weird", "weird timestamp task", timestamp="not-a-real-timestamp")
    since = datetime(2026, 7, 7, tzinfo=timezone.utc)

    results = find_recent_closeouts([tmp_path], since=since)

    assert len(results) == 1


def test_results_sorted_newest_first(tmp_path):
    _write_closeout(tmp_path, "a", "first", timestamp="2026-07-07T10:00:00+00:00")
    _write_closeout(tmp_path, "b", "second", timestamp="2026-07-07T20:00:00+00:00")

    results = find_recent_closeouts([tmp_path])

    assert [r["task"] for r in results] == ["second", "first"]


def test_missing_audit_dir_is_a_clean_empty_result(tmp_path):
    assert find_recent_closeouts([tmp_path / "does_not_exist"]) == []


def test_summarize_surfaces_version_and_dirty_flag():
    record = {
        "task": "some task", "status": "completed", "timestamp": "2026-07-07T20:00:00+00:00",
        "cortex_version": {"commit": "deadbee", "dirty": True},
    }
    line = summarize(record)
    assert "deadbee" in line
    assert "(dirty)" in line
    assert "some task" in line


def test_summarize_handles_missing_version_gracefully():
    record = {"task": "no version stamp", "status": "completed", "timestamp": "t"}
    line = summarize(record)
    assert "no-commit" in line
