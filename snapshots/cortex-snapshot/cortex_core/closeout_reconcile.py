"""Reconcile dispatched background-agent work against the durable audit trail, independent of
whether a completion notification ever arrived.

The problem this fixes, observed live 2026-07-07: two background agents dispatched via the Task
tool went silently untrackable -- no completion notification ever fired, and querying their task
ID later returned "no task found." Whether they crashed, finished and got lost, or are still
genuinely running was unanswerable from the notification channel alone. That channel is outside
this repo (harness infrastructure, not code here), so it can't be fixed here.

What CAN be fixed here: every real closeout is already durably written to an audit log
(`audit.write_closeout`), stamped with `cortex_version()` since tonight. This module scans BOTH
plausible audit-log locations (a session's own workspace, and the `.mcp.json`-pinned cortex-local
workspace that closeouts land in when the live MCP server is stale -- the exact issue this same
session diagnosed and is separately fixing) and reports what actually landed, by real timestamp,
regardless of what the notification channel did or didn't say. Ground truth over self-report --
the standing discipline of this entire session, now applied to the orchestration layer itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iter_closeout_json(audit_root: Path):
    """Yield every closeout .json under `audit_root` (any shard, any `agent/` subdir)."""
    if not audit_root.is_dir():
        return
    for shard in sorted(audit_root.glob("audit-log-*")):
        agent_dir = shard / "agent"
        if not agent_dir.is_dir():
            continue
        for f in sorted(agent_dir.glob("cortex-closeout__*.json")):
            yield f


def _load_closeout(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    data["_file"] = str(path)
    return data


def find_recent_closeouts(
    workspaces: list[str | Path], since: datetime | None = None
) -> list[dict[str, Any]]:
    """Scan every `audit/audit-log-*/agent/*.json` under each given workspace root, return
    closeouts with `timestamp` >= `since` (or all, if `since` is None), sorted newest-first.
    Duplicate-safe: a closeout re-scanned from two workspace roots (shouldn't normally happen,
    but the whole point of this tool is not to assume normal) is deduped by its own filename."""
    seen_names: set[str] = set()
    results: list[dict[str, Any]] = []
    for ws in workspaces:
        audit_root = Path(ws) / "audit"
        for path in _iter_closeout_json(audit_root):
            if path.name in seen_names:
                continue
            record = _load_closeout(path)
            if record is None:
                continue
            ts_raw = record.get("timestamp")
            if since is not None and ts_raw:
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts < since:
                        continue
                except ValueError:
                    pass  # unparseable timestamp -- include rather than silently drop
            seen_names.add(path.name)
            results.append(record)
    results.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return results


def summarize(record: dict[str, Any]) -> str:
    ts = record.get("timestamp", "?")
    task = record.get("task", "?")
    status = record.get("status", "?")
    version = record.get("cortex_version") or {}
    commit = version.get("commit") or "no-commit"
    dirty = " (dirty)" if version.get("dirty") else ""
    return f"[{ts}] {status:10s} {commit}{dirty}  {task}"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="List real closeouts from the audit trail, ground truth over self-report."
    )
    parser.add_argument("workspaces", nargs="+", help="Workspace root(s) to scan")
    parser.add_argument("--since", help="ISO timestamp; only closeouts at/after this time")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    records = find_recent_closeouts(args.workspaces, since=since)
    if not records:
        print("No closeouts found.")
        return
    for record in records:
        print(summarize(record))
    print(f"\n{len(records)} closeout(s).")


if __name__ == "__main__":
    main()
