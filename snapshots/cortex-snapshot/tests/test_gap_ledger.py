"""TDD contract for gap_ledger v0 (GAP-CORTEX-0001, the Gap/Friction Ledger).

Written FIRST, before cortex_core/gap_ledger.py. Encodes the reconciled
Fable+Codex design: one append-only JSONL ground-truth store, last-wins per
gap_id, task_ledger lock discipline reused, evidence-gated closure with a
verifying/closed split (anti-evidence-theater), derived blocked/phase status
(no write amplification), and a deterministic render projection with a
--check drift gate.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from cortex_core import gap_ledger as gl


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_path(tmp_path, monkeypatch):
    """A tmp dir that resolves as a Cortex workspace (matches test_task_ledger's
    convention) so gap_ledger's workspace resolution finds it."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    (tmp_path / "library" / "cortex-library").mkdir(parents=True)
    (tmp_path / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    return tmp_path


def _mk(ws: Path, gap_id: str, **kw) -> dict:
    kw.setdefault("title", f"title for {gap_id}")
    kw.setdefault("phase", "phase-7")
    kw.setdefault("source", "test")
    kw.setdefault("author_agent", "tester")
    return gl.create_gap(gap_id, workspace=ws, **kw)


def _evidence_file(ws: Path, name: str = "evidence.txt", lines: int = 5) -> None:
    (ws / name).write_text("\n".join(f"line {i}" for i in range(1, lines + 1)), encoding="utf-8")


# --------------------------------------------------------------------------- #
# create / read / reduce
# --------------------------------------------------------------------------- #
def test_create_and_show(tmp_path):
    res = _mk(tmp_path, "GAP-CORTEX-9001", title="Durable gap tracking", priority="P0")
    assert res["created"] is True
    assert res["gap_id"] == "GAP-CORTEX-9001"
    assert res["status"] == "open"
    assert res["verified"] is False
    got = gl.get_gap("GAP-CORTEX-9001", workspace=tmp_path)
    assert got["title"] == "Durable gap tracking"
    assert got["priority"] == "P0"


def test_create_duplicate_refused(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    dup = _mk(tmp_path, "GAP-CORTEX-9001")
    assert dup["created"] is False


def test_ledger_is_append_only_last_wins(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.update_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="t", priority="P2")
    path = gl.ledger_path(tmp_path)
    # both events retained on disk (append-only), current state is the last
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert gl.get_gap("GAP-CORTEX-9001", workspace=tmp_path)["priority"] == "P2"


def test_list_filters(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001", phase="phase-7")
    _mk(tmp_path, "GAP-CORTEX-9002", phase="phase-8")
    all_gaps = gl.list_gaps(workspace=tmp_path)
    assert {g["gap_id"] for g in all_gaps} == {"GAP-CORTEX-9001", "GAP-CORTEX-9002"}
    p7 = gl.list_gaps(workspace=tmp_path, phase="phase-7")
    assert [g["gap_id"] for g in p7] == ["GAP-CORTEX-9001"]


# --------------------------------------------------------------------------- #
# schema validation: strict on WRITE, forgiving on READ
# --------------------------------------------------------------------------- #
def test_reject_bad_status(tmp_path):
    with pytest.raises(ValueError):
        _mk(tmp_path, "GAP-CORTEX-9001", status="in_progress")


def test_reject_bad_priority(tmp_path):
    with pytest.raises(ValueError):
        _mk(tmp_path, "GAP-CORTEX-9001", priority="P9")


def test_reject_bad_gap_id(tmp_path):
    with pytest.raises(ValueError):
        _mk(tmp_path, "not-a-gap-id")


def test_write_validator_rejects_unknown_field(tmp_path):
    # a typoed field must never silently become state (Codex rule)
    rec = {
        "schema_version": 1,
        "event_id": "gap-event-x",
        "event": "create",
        "gap_id": "GAP-CORTEX-9001",
        "title": "t",
        "status": "open",
        "phase": "phase-7",
        "priority": "P1",
        "typoed_feild": True,
    }
    with pytest.raises(ValueError):
        gl._validate_write_record(rec)


def test_read_is_forgiving_of_future_fields(tmp_path):
    # a line from a newer schema (extra field) must still reduce, not crash (Fable rule)
    _mk(tmp_path, "GAP-CORTEX-9001")
    path = gl.ledger_path(tmp_path)
    future = {
        "schema_version": 2,
        "event_id": "gap-event-future",
        "event": "update",
        "gap_id": "GAP-CORTEX-9001",
        "title": "from the future",
        "status": "open",
        "phase": "phase-9",
        "priority": "P1",
        "brand_new_field": [1, 2, 3],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(future) + "\n")
    got = gl.get_gap("GAP-CORTEX-9001", workspace=tmp_path)
    assert got["title"] == "from the future"
    assert got["brand_new_field"] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# torn line tolerance (crash mid-append must not poison reads)
# --------------------------------------------------------------------------- #
def test_torn_final_line_tolerated(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    path = gl.ledger_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"gap_id": "GAP-CORTEX-9002", "title": "torn"')  # no close, no newline
    gaps = gl.list_gaps(workspace=tmp_path)
    assert [g["gap_id"] for g in gaps] == ["GAP-CORTEX-9001"]


# --------------------------------------------------------------------------- #
# concurrency: atomic claim, exactly one winner
# --------------------------------------------------------------------------- #
def test_claim_race_single_winner(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        barrier.wait()
        results[name] = gl.claim_gap("GAP-CORTEX-9001", owner=name, workspace=tmp_path)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [n for n, r in results.items() if r.get("claimed")]
    assert len(winners) == 1
    owner = gl.get_gap("GAP-CORTEX-9001", workspace=tmp_path)["owner_agent"]
    assert owner == winners[0]


def test_release_returns_ownership(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.claim_gap("GAP-CORTEX-9001", owner="a", workspace=tmp_path)
    gl.release_gap("GAP-CORTEX-9001", owner="a", workspace=tmp_path)
    g = gl.get_gap("GAP-CORTEX-9001", workspace=tmp_path)
    assert g["owner_agent"] is None
    assert g["status"] == "open"


# --------------------------------------------------------------------------- #
# evidence-gated closure: verifying vs closed (anti-evidence-theater)
# --------------------------------------------------------------------------- #
def test_close_requires_evidence(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    with pytest.raises(ValueError):
        gl.close_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="a", evidence=[])


def test_close_moves_to_verifying_not_closed(tmp_path):
    _evidence_file(tmp_path)
    _mk(tmp_path, "GAP-CORTEX-9001")
    res = gl.close_gap(
        "GAP-CORTEX-9001",
        workspace=tmp_path,
        author_agent="a",
        evidence=[{"path": "evidence.txt", "line": 2, "kind": "closeout"}],
    )
    assert res["status"] == "verifying"
    assert res["verified"] is False


def test_verify_promotes_when_evidence_resolves(tmp_path):
    _evidence_file(tmp_path)
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.close_gap(
        "GAP-CORTEX-9001",
        workspace=tmp_path,
        author_agent="a",
        evidence=[{"path": "evidence.txt", "line": 2, "kind": "test"}],
    )
    res = gl.verify_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="checker")
    assert res["verified"] is True
    assert res["status"] == "closed"
    assert res["closed_at"] is not None


def test_verify_refuses_when_evidence_does_not_resolve(tmp_path):
    _evidence_file(tmp_path, lines=3)
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.close_gap(
        "GAP-CORTEX-9001",
        workspace=tmp_path,
        author_agent="a",
        evidence=[{"path": "evidence.txt", "line": 999, "kind": "test"}],  # out of range
    )
    res = gl.verify_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="checker")
    assert res["verified"] is False
    assert res["status"] == "verifying"
    assert res["unresolved"]


def test_verify_missing_file_refused(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.close_gap(
        "GAP-CORTEX-9001",
        workspace=tmp_path,
        author_agent="a",
        evidence=[{"path": "does_not_exist.txt", "line": 1, "kind": "test"}],
    )
    res = gl.verify_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="checker")
    assert res["verified"] is False


def test_human_verify_bypasses_resolution(tmp_path):
    # non-machine-checkable metrics close on an explicit human event
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.close_gap(
        "GAP-CORTEX-9001",
        workspace=tmp_path,
        author_agent="a",
        evidence=[{"path": "external://design-review", "line": None, "kind": "human"}],
    )
    res = gl.verify_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="pujan", human=True)
    assert res["verified"] is True
    assert res["status"] == "closed"


def test_reopen_clears_verified(tmp_path):
    _evidence_file(tmp_path)
    _mk(tmp_path, "GAP-CORTEX-9001")
    gl.close_gap(
        "GAP-CORTEX-9001", workspace=tmp_path, author_agent="a",
        evidence=[{"path": "evidence.txt", "line": 1, "kind": "test"}],
    )
    gl.verify_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="c")
    res = gl.reopen_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="a", reason="regressed")
    assert res["status"] == "open"
    assert res["verified"] is False


# --------------------------------------------------------------------------- #
# dependency edges: self-edge + cycle rejection; derived blocked status
# --------------------------------------------------------------------------- #
def test_self_edge_rejected(tmp_path):
    with pytest.raises(ValueError):
        _mk(tmp_path, "GAP-CORTEX-9001", blocked_by=["GAP-CORTEX-9001"])


def test_cycle_rejected(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    _mk(tmp_path, "GAP-CORTEX-9002", blocked_by=["GAP-CORTEX-9001"])
    with pytest.raises(ValueError):
        gl.update_gap(
            "GAP-CORTEX-9001", workspace=tmp_path, author_agent="a",
            blocked_by=["GAP-CORTEX-9002"],
        )


def test_derived_blocked_status(tmp_path):
    _evidence_file(tmp_path)
    _mk(tmp_path, "GAP-CORTEX-9001")  # blocker
    _mk(tmp_path, "GAP-CORTEX-9002", blocked_by=["GAP-CORTEX-9001"])
    b = gl.get_gap("GAP-CORTEX-9002", workspace=tmp_path)
    assert b["effective_status"] == "blocked"
    # close+verify the blocker -> dependent unblocks
    gl.close_gap(
        "GAP-CORTEX-9001", workspace=tmp_path, author_agent="a",
        evidence=[{"path": "evidence.txt", "line": 1, "kind": "test"}],
    )
    gl.verify_gap("GAP-CORTEX-9001", workspace=tmp_path, author_agent="c")
    b2 = gl.get_gap("GAP-CORTEX-9002", workspace=tmp_path)
    assert b2["effective_status"] == "open"


def test_blocked_by_unknown_target_rejected(tmp_path):
    with pytest.raises(ValueError):
        _mk(tmp_path, "GAP-CORTEX-9002", blocked_by=["GAP-CORTEX-DOESNOTEXIST"])


# --------------------------------------------------------------------------- #
# supersession
# --------------------------------------------------------------------------- #
def test_supersede(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    _mk(tmp_path, "GAP-CORTEX-9002")
    res = gl.supersede_gap(
        "GAP-CORTEX-9001", by="GAP-CORTEX-9002", workspace=tmp_path, author_agent="a"
    )
    assert res["status"] == "superseded"
    assert res["superseded_by"] == "GAP-CORTEX-9002"


def test_supersede_unknown_target_rejected(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    with pytest.raises(ValueError):
        gl.supersede_gap(
            "GAP-CORTEX-9001", by="GAP-CORTEX-NOPE", workspace=tmp_path, author_agent="a"
        )


# --------------------------------------------------------------------------- #
# validate: whole-log integrity
# --------------------------------------------------------------------------- #
def test_validate_clean_ledger(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    report = gl.validate_ledger(workspace=tmp_path)
    assert report["ok"] is True
    assert report["errors"] == []


def test_validate_detects_dangling_edge(tmp_path):
    # inject a dangling blocked_by directly on disk (bypassing the write validator)
    _mk(tmp_path, "GAP-CORTEX-9001")
    path = gl.ledger_path(tmp_path)
    rec = gl.get_gap("GAP-CORTEX-9001", workspace=tmp_path)
    rec = {k: v for k, v in rec.items() if not k.startswith("effective")}
    rec["blocked_by"] = ["GAP-CORTEX-GHOST"]
    rec["event"] = "update"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    report = gl.validate_ledger(workspace=tmp_path)
    assert report["ok"] is False


# --------------------------------------------------------------------------- #
# render projection + --check drift gate + phase rollup
# --------------------------------------------------------------------------- #
def test_render_deterministic(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9002")
    _mk(tmp_path, "GAP-CORTEX-9001", priority="P0")
    a = gl.render(workspace=tmp_path)
    b = gl.render(workspace=tmp_path)
    assert a == b
    # gaps appear sorted by id
    assert a.index("GAP-CORTEX-9001") < a.index("GAP-CORTEX-9002")


def test_render_check_detects_drift(tmp_path):
    _mk(tmp_path, "GAP-CORTEX-9001")
    out = tmp_path / "GAPS.md"
    out.write_text(gl.render(workspace=tmp_path), encoding="utf-8")
    assert gl.render_check(out, workspace=tmp_path) is True
    out.write_text("hand-edited drift\n", encoding="utf-8")
    assert gl.render_check(out, workspace=tmp_path) is False


def test_phase_rollup(tmp_path):
    _evidence_file(tmp_path)
    _mk(tmp_path, "GAP-CORTEX-9001", phase="phase-7")
    _mk(tmp_path, "GAP-CORTEX-9002", phase="phase-7")
    roll = gl.phase_rollup(workspace=tmp_path)
    assert roll["phase-7"] == "open"
    gl.claim_gap("GAP-CORTEX-9001", owner="a", workspace=tmp_path)
    assert gl.phase_rollup(workspace=tmp_path)["phase-7"] == "active"
    # close+verify both -> phase complete
    for gid in ("GAP-CORTEX-9001", "GAP-CORTEX-9002"):
        gl.close_gap(
            gid, workspace=tmp_path, author_agent="a",
            evidence=[{"path": "evidence.txt", "line": 1, "kind": "test"}],
        )
        gl.verify_gap(gid, workspace=tmp_path, author_agent="c")
    assert gl.phase_rollup(workspace=tmp_path)["phase-7"] == "complete"


# --------------------------------------------------------------------------- #
# CLI smoke
# --------------------------------------------------------------------------- #
def test_cli_create_and_list(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tmp_path))
    rc = gl.main([
        "create", "--gap-id", "GAP-CORTEX-9001", "--title", "cli gap",
        "--phase", "phase-7", "--source", "cli", "--author-agent", "tester",
    ])
    assert rc == 0
    capsys.readouterr()
    rc = gl.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GAP-CORTEX-9001" in out
