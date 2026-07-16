"""Tests for GAP-CORTEX-0016 -- the shared task-coordination ledger.

The core contract (GAP-0016 "Next Gate"): concurrent-claim conflict (two
agents, one task -> exactly one wins), read-visibility (a claimed task shows as
owned to a second agent), and status lifecycle (pending -> active -> done).
Plus append-only durability (nothing is rewritten; the log grows) and the
JSONL/lockfile layout the design fixes.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cortex_core import task_ledger as tl


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    return _make_workspace(tmp_path)


def test_create_lands_in_jsonl_at_expected_path(workspace: Path) -> None:
    rec = tl.create_task("build the ledger", "fable-max", workspace=workspace)
    assert rec["created"] is True
    assert rec["status"] == "pending"
    assert rec["owner"] is None
    assert rec["author_model"] == "fable-max"

    led = workspace / "logs" / "task_ledger.jsonl"
    assert led.is_file(), "ledger must live at logs/task_ledger.jsonl"
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["task_id"] == rec["task_id"]
    assert lines[0]["event"] == "create"


def test_duplicate_task_id_is_refused(workspace: Path) -> None:
    tl.create_task("first", "m1", workspace=workspace, task_id="T-1")
    dup = tl.create_task("second", "m2", workspace=workspace, task_id="T-1")
    assert dup["created"] is False
    assert "exists" in dup["reason"]
    # Only the original survives in current state.
    tasks = tl.list_tasks(workspace=workspace)
    assert [t["task_id"] for t in tasks] == ["T-1"]
    assert tasks[0]["description"] == "first"


def test_claim_moves_pending_to_active_and_records_owner(workspace: Path) -> None:
    tl.create_task("do a thing", "m1", workspace=workspace, task_id="T-1")
    res = tl.claim_task("T-1", "hermes", workspace=workspace)
    assert res["claimed"] is True
    assert res["status"] == "active"
    assert res["owner"] == "hermes"
    assert res["claimed_at"] is not None


def test_claimed_task_is_visible_as_owned_to_a_second_agent(workspace: Path) -> None:
    """Read-visibility: once one agent claims, a peer listing the ledger sees it
    owned and would skip it."""
    tl.create_task("shared work", "m1", workspace=workspace, task_id="T-1")
    tl.claim_task("T-1", "hermes", workspace=workspace)

    seen_by_peer = tl.list_tasks(workspace=workspace, status="active")
    assert len(seen_by_peer) == 1
    assert seen_by_peer[0]["owner"] == "hermes"
    # No pending tasks remain for the peer to grab.
    assert tl.list_tasks(workspace=workspace, status="pending") == []


def test_second_claim_of_owned_task_is_refused(workspace: Path) -> None:
    tl.create_task("one task", "m1", workspace=workspace, task_id="T-1")
    first = tl.claim_task("T-1", "hermes", workspace=workspace)
    second = tl.claim_task("T-1", "claude", workspace=workspace)
    assert first["claimed"] is True
    assert second["claimed"] is False
    assert second["owner"] == "hermes", "loser must be told the real owner"


def test_claim_of_unknown_task_is_refused(workspace: Path) -> None:
    res = tl.claim_task("nope", "hermes", workspace=workspace)
    assert res["claimed"] is False
    assert res["reason"] == "no such task"


def test_concurrent_claim_exactly_one_wins(workspace: Path) -> None:
    """The core red check: many agents race to claim the same single task;
    exactly one may succeed. Last-writer-wins would let several win -- the
    exclusive lock is what makes this exactly-one."""
    tl.create_task("hot task", "m1", workspace=workspace, task_id="T-HOT")

    n = 12

    def claim(i: int) -> dict:
        return tl.claim_task("T-HOT", f"agent-{i}", workspace=workspace)

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(claim, range(n)))

    winners = [r for r in results if r.get("claimed")]
    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"

    # The ledger's current state agrees: one active owner, and it's the winner.
    tasks = tl.list_tasks(workspace=workspace)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "active"
    assert tasks[0]["owner"] == winners[0]["owner"]


def test_status_lifecycle_pending_active_done(workspace: Path) -> None:
    tl.create_task("lifecycle", "m1", workspace=workspace, task_id="T-1")
    assert tl.list_tasks(workspace=workspace)[0]["status"] == "pending"
    tl.claim_task("T-1", "hermes", workspace=workspace)
    assert tl.list_tasks(workspace=workspace)[0]["status"] == "active"
    done = tl.update_task("T-1", workspace=workspace, status="done", result="shipped")
    assert done["updated"] is True
    final = tl.list_tasks(workspace=workspace)[0]
    assert final["status"] == "done"
    assert final["result"] == "shipped"
    assert final["owner"] == "hermes", "update must preserve owner unless changed"


def test_update_can_mark_failed(workspace: Path) -> None:
    tl.create_task("might fail", "m1", workspace=workspace, task_id="T-1")
    tl.claim_task("T-1", "hermes", workspace=workspace)
    res = tl.update_task("T-1", workspace=workspace, status="failed")
    assert res["updated"] is True
    assert tl.list_tasks(workspace=workspace)[0]["status"] == "failed"


def test_update_unknown_task_is_refused(workspace: Path) -> None:
    res = tl.update_task("ghost", workspace=workspace, status="done")
    assert res["updated"] is False
    assert res["reason"] == "no such task"


def test_invalid_status_rejected(workspace: Path) -> None:
    with pytest.raises(ValueError):
        tl.create_task("x", "m1", workspace=workspace, status="bogus")
    tl.create_task("y", "m1", workspace=workspace, task_id="T-1")
    with pytest.raises(ValueError):
        tl.update_task("T-1", workspace=workspace, status="bogus")


def test_log_is_append_only(workspace: Path) -> None:
    """Create + claim + update = three appended lines for one task; the log
    grows and never rewrites in place (it is the audit trail)."""
    tl.create_task("audited", "m1", workspace=workspace, task_id="T-1")
    tl.claim_task("T-1", "hermes", workspace=workspace)
    tl.update_task("T-1", workspace=workspace, status="done")

    led = workspace / "logs" / "task_ledger.jsonl"
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [ln["event"] for ln in lines] == ["create", "claim", "update"]
    # Current state still collapses to a single done task.
    assert len(tl.list_tasks(workspace=workspace)) == 1


def test_torn_final_line_does_not_break_reads(workspace: Path) -> None:
    """A crash mid-append can leave a torn final line; reads must skip it rather
    than raise, keeping the rest of the ledger usable."""
    tl.create_task("good", "m1", workspace=workspace, task_id="T-1")
    led = workspace / "logs" / "task_ledger.jsonl"
    with led.open("a", encoding="utf-8") as fh:
        fh.write('{"task_id": "T-2", "status": "pend')  # truncated, no newline
    tasks = tl.list_tasks(workspace=workspace)
    assert [t["task_id"] for t in tasks] == ["T-1"]


def test_cli_round_trip(workspace: Path, monkeypatch, capsys) -> None:
    """The CLI wrapper drives the same core code; create -> claim -> list."""
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    tl.main(["create", "--description", "cli task", "--author-model", "opus", "--task-id", "C-1"])
    tl.main(["claim", "--task-id", "C-1", "--owner", "cli-agent"])
    capsys.readouterr()  # drain
    tl.main(["list"])
    out = capsys.readouterr().out
    listed = json.loads(out)
    assert len(listed) == 1
    assert listed[0]["task_id"] == "C-1"
    assert listed[0]["owner"] == "cli-agent"
    assert listed[0]["status"] == "active"
