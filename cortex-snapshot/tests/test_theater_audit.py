"""RED-first tests for I4 theater-audit (docs/GAP-CLOSURE-PLAN.md §I4).

Contract: scan closeouts for governance-ritual / evidence-theater signals and
REPORT them (detection-over-coercion -- flag, never block). Reuses
``cortex_core.evaluator``'s existing anti-evidence-theater rubric rather than
re-implementing it; adds text-level ceremony signals on top.

A theatrical closeout is one that performs the *ceremony* of completion --
"tests passed", "fully closed", "0 HIGH" -- while citing nothing a reader could
check. A clean closeout points at real artifacts (evidence items, file paths,
test counts). The audit must flag the first and pass the second.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from cortex_core.theater_audit import audit_closeouts, audit_one, Signal


def _make_ws(tmp_path):
    """A minimal Cortex checkout so the evaluator can resolve file evidence."""
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _write_closeout(workspace, name, payload, timestamp=None):
    agent_dir = workspace / "audit" / "audit-log-1" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    payload.setdefault("status", "completed")
    payload.setdefault("timestamp", timestamp or datetime.now(timezone.utc).isoformat())
    (agent_dir / f"cortex-closeout__{name}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_flags_tests_passed_with_no_reference(tmp_path):
    """A closeout that asserts 'all tests passed' but cites no count, no test
    evidence item, and no runnable reference is pure ceremony -- flag it."""
    flags = audit_one(
        {
            "status": "completed",
            "task": "harden the widget",
            "task_type": "feature",
            "tests": "yes, all tests passed, everything green",
            "result": "Fully closed. Everything works. Fresh-reviewed, 0 HIGH.",
            "evidence": [],
            "scripts": "",
        }
    )
    kinds = {f.signal for f in flags}
    assert Signal.TESTS_CLAIMED_UNREFERENCED in kinds
    assert Signal.CEREMONY_WITHOUT_SUBSTANCE in kinds


def test_flags_evidence_theater_via_evaluator(tmp_path):
    """Reuse point: the evaluator already grades 'evidence present but none
    relevant' as UNSUPPORTED. A substantive claim whose only evidence is
    unrelated must surface as an UNSUPPORTED_CLAIM theater signal."""
    ws = _make_ws(tmp_path)
    (ws / "notes").mkdir(parents=True, exist_ok=True)
    (ws / "notes" / "todo.txt").write_text("misc\n", encoding="utf-8")
    flags = audit_one(
        {
            "status": "completed",
            "task": "fix the parser crash on empty input",
            "task_type": "bugfix",
            "tests": "3 passed",
            "result": "done",
            "evidence": [
                {"type": "test", "ref": "unrelated_config_thing", "detail": "ok"},
                {"type": "file", "ref": "notes/todo.txt", "detail": "misc"},
            ],
        },
        workspace=ws,
    )
    assert Signal.UNSUPPORTED_CLAIM in {f.signal for f in flags}


def test_clean_closeout_is_not_flagged(tmp_path):
    """A closeout that cites a real test count, real file paths relevant to the
    claim, and concrete references must NOT be flagged."""
    ws = _make_ws(tmp_path)
    (ws / "cortex_core").mkdir(parents=True, exist_ok=True)
    (ws / "cortex_core" / "parser.py").write_text("x=1\n", encoding="utf-8")
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    (ws / "tests" / "test_parser.py").write_text("x=1\n", encoding="utf-8")
    flags = audit_one(
        {
            "status": "completed",
            "task": "fix the parser crash on empty input",
            "task_type": "bugfix",
            "tests": "pytest 3 passed (1 new regression test)",
            "result": "Fixed parser.py to guard empty input; added test_parser.py.",
            "evidence": [
                {"type": "test", "ref": "tests/test_parser.py", "detail": "3 passed"},
                {"type": "file", "ref": "cortex_core/parser.py", "detail": "guard added"},
            ],
        },
        workspace=ws,
    )
    assert flags == [], f"clean closeout was flagged: {[f.signal for f in flags]}"


def test_lenient_task_type_not_flagged_for_missing_evidence(tmp_path):
    """A chore/explore closeout with no strong completion claim must not be
    flagged as ceremony -- lenient types are exempt (mirrors evaluator)."""
    flags = audit_one(
        {
            "status": "completed",
            "task": "poke around the config loader",
            "task_type": "explore",
            "tests": "",
            "result": "Looked at how config resolves; noted the env-first order.",
            "evidence": [],
        }
    )
    assert Signal.CEREMONY_WITHOUT_SUBSTANCE not in {f.signal for f in flags}


def test_audit_closeouts_scans_workspace_and_reports(tmp_path):
    """End-to-end: scan a workspace, return a report with per-closeout flags and
    a total. Detection-over-coercion: it reports, never raises."""
    _write_closeout(
        tmp_path,
        "theatrical",
        {
            "task": "ship the thing",
            "task_type": "feature",
            "tests": "all green",
            "result": "Fully closed, 0 HIGH, fresh-reviewed, shipped.",
            "evidence": [],
        },
    )
    _write_closeout(
        tmp_path,
        "honest",
        {
            "task": "poke at logs",
            "task_type": "explore",
            "tests": "",
            "result": "read some logs",
            "evidence": [],
        },
    )
    report = audit_closeouts([tmp_path])
    assert report["scanned"] == 2
    flagged_tasks = {r["task"] for r in report["flagged"]}
    assert "ship the thing" in flagged_tasks
    assert "poke at logs" not in flagged_tasks
