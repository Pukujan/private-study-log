"""Operator and CI visibility for Cortex's replayable project state.

This surface never mints assurance.  Read commands label materialized views as
trusted only after ``ProjectStateStore.projection_status`` replays immutable
history and verifies every generated projection.  Mutation is limited to the
explicit ``rebuild`` and ``recover`` commands, both with caller-supplied time.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import make_stdio_encoding_safe, resolve_workspace
from .project_state_store import (
    CorruptEventLogError,
    LockUnavailableError,
    ProjectStateStore,
    ProjectStateStoreError,
    TimeRewindError,
)


_ARTIFACT_NAMES = (
    "events.jsonl",
    "current.json",
    "projections-dirty.json",
    "projections",
)
_INVALID_REASON_MARKERS = (
    "immutable event log",
    "immutable-history replay",
    "cannot be replay-anchored",
    "embedded state_sha256",
    "reducer event_log_sha256",
    "current.as_of",
    "ontology sync receipt invalid",
)


def _workspace_path(workspace: str | Path | None) -> Path:
    if workspace is not None:
        return Path(workspace).resolve()
    return resolve_workspace(None).resolve()


def _has_project_state(root: Path) -> bool:
    return root.is_dir() and any((root / name).exists() for name in _ARTIFACT_NAMES)


def _base_status(status: str, *, workspace: Path, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "ok": status == "CLEAN",
        "available": status != "UNAVAILABLE",
        "workspace": str(workspace),
        "project_state_root": str(workspace / "project-state"),
        "reason": reason,
        "replay_anchored": status == "CLEAN",
        "assurance_minted": False,
    }


def inspect_project_state(workspace: str | Path | None = None) -> dict[str, Any]:
    """Return a non-assuring, replay-anchored operator status."""
    ws = _workspace_path(workspace)
    root = ws / "project-state"
    if not _has_project_state(root):
        return _base_status(
            "UNAVAILABLE",
            workspace=ws,
            reason="no project-state event/current/projection artifacts exist",
        )

    store = ProjectStateStore(ws)
    try:
        checked = store.projection_status()
    except (CorruptEventLogError, LockUnavailableError, ProjectStateStoreError, ValueError) as exc:
        report = _base_status(
            "INVALID",
            workspace=ws,
            reason=f"project state could not be replay-validated: {type(exc).__name__}: {exc}",
        )
        report.update({"reasons": [str(exc)], "severity": "FAIL"})
        return report

    reasons = list(checked.get("reasons") or [])
    invalid = any(
        marker in reason
        for reason in reasons
        for marker in _INVALID_REASON_MARKERS
    )
    if checked.get("clean"):
        status = "CLEAN"
        severity = "PASS"
        reason = "immutable history, current state, and generated views agree"
    elif invalid:
        status = "INVALID"
        severity = "FAIL"
        reason = "current state is not anchored to immutable replay"
    else:
        status = "DIRTY"
        severity = "WARN"
        reason = "project state is recoverable but one or more generated views are dirty or missing"
    report = _base_status(status, workspace=ws, reason=reason)
    report.update({
        "severity": severity,
        "revision": checked.get("revision"),
        "reasons": reasons,
        "dirty_marker": checked.get("dirty_marker"),
        "documents_path": checked.get("documents_path"),
        "notices": list(checked.get("notices") or []),
        "ontology_sync_path": checked.get("ontology_sync_path"),
        "ontology_unresolved_skips": list(checked.get("ontology_unresolved_skips") or []),
        "replay_anchored": not invalid,
    })
    return report


def project_state_diagnostic(workspace: str | Path | None = None) -> dict[str, Any]:
    """Doctor-shaped project-state diagnostic; PASS only when replay-clean."""
    report = inspect_project_state(workspace)
    return {
        **report,
        "level": report.get("severity", "WARN"),
        "ok": report["status"] == "CLEAN",
    }


def _read_command(command: str, ws: Path) -> tuple[dict[str, Any], int]:
    status = inspect_project_state(ws)
    if command == "status":
        return status, 0 if status["status"] == "CLEAN" else (2 if status["status"] == "UNAVAILABLE" else 1)

    store = ProjectStateStore(ws)
    report = {**status, "command": command, "trusted": status["status"] == "CLEAN"}
    if status["status"] == "UNAVAILABLE":
        return report, 2
    try:
        if command == "current":
            report["current"] = store.read_current()
        elif command == "resume-pack":
            if status["status"] != "CLEAN":
                report["resume_pack"] = None
                report["reason"] = "resume pack refused because generated views are not replay-clean"
            else:
                path = store.paths.projections / "agent-resume-pack.json"
                report["resume_pack"] = json.loads(path.read_text(encoding="utf-8"))
        elif command == "history":
            report["events"] = store.read_events()
        else:
            raise ValueError(f"unknown read command {command!r}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ProjectStateStoreError) as exc:
        report.update({
            "status": "INVALID",
            "ok": False,
            "trusted": False,
            "reason": f"{command} could not be read safely: {type(exc).__name__}: {exc}",
        })
        return report, 1
    return report, 0 if status["status"] == "CLEAN" else 1


def _write_command(command: str, ws: Path, as_of: str) -> tuple[dict[str, Any], int]:
    root = ws / "project-state"
    if not root.is_dir():
        return _base_status(
            "UNAVAILABLE", workspace=ws, reason="project-state directory does not exist",
        ), 2
    store = ProjectStateStore(ws)
    try:
        if command == "rebuild":
            result = store.rebuild_projections(as_of=as_of)
        else:
            result = store.recover_if_dirty(as_of=as_of)
    except TimeRewindError as exc:
        report = _base_status("INVALID", workspace=ws, reason=str(exc))
        report.update({"operation": command, "as_of": as_of, "severity": "FAIL"})
        return report, 1
    except (ProjectStateStoreError, ValueError) as exc:
        report = _base_status(
            "INVALID",
            workspace=ws,
            reason=f"{command} failed: {type(exc).__name__}: {exc}",
        )
        report.update({"operation": command, "as_of": as_of, "severity": "FAIL"})
        return report, 1
    report = inspect_project_state(ws)
    report.update({"operation": command, "as_of": as_of, "result": asdict(result)})
    return report, 0 if report["status"] == "CLEAN" else 1


def _print_human(report: dict[str, Any]) -> None:
    status = report.get("status", "INVALID")
    print(f"project-state: {status}")
    print(f"workspace: {report.get('workspace', '?')}")
    if report.get("revision") is not None:
        print(f"revision: {report['revision']}")
    print(f"replay anchor: {'verified' if report.get('replay_anchored') else 'not verified'}")
    print("assurance: not minted by this command")
    if report.get("reason"):
        print(f"reason: {report['reason']}")
    for reason in report.get("reasons") or []:
        print(f"  - {reason}")
    for notice in report.get("notices") or []:
        print(f"  NOTICE: {notice}")
    if "current" in report:
        print("current:")
        print(json.dumps(report["current"], indent=2, ensure_ascii=False, sort_keys=True))
    if "resume_pack" in report and report["resume_pack"] is not None:
        print("resume-pack:")
        print(json.dumps(report["resume_pack"], indent=2, ensure_ascii=False, sort_keys=True))
    if "events" in report:
        events = report["events"]
        print(f"history: {len(events)} event(s)")
        for index, event in enumerate(events, start=1):
            print(
                f"  {index}. {event.get('event_id', '?')} "
                f"{event.get('event_type', '?')} {event.get('scope', {})}"
            )
    if "operation" in report:
        print(f"operation: {report['operation']} at {report.get('as_of')}")


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(
        prog="cortex-project-state",
        description="Inspect or explicitly recover Cortex project state; never mints assurance.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=("status", "current", "resume-pack", "history", "rebuild", "recover"),
    )
    parser.add_argument("--workspace", help="workspace root (defaults to Cortex resolution)")
    parser.add_argument("--as-of", help="timezone-aware ISO-8601 materialization time")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    if args.command in {"rebuild", "recover"} and not args.as_of:
        parser.error(f"{args.command} requires --as-of")

    try:
        ws = _workspace_path(args.workspace)
    except (OSError, FileNotFoundError, ValueError) as exc:
        report = {
            "status": "UNAVAILABLE",
            "ok": False,
            "available": False,
            "reason": str(exc),
            "assurance_minted": False,
        }
        if args.json_output:
            print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        else:
            _print_human(report)
        return 2

    if args.command in {"rebuild", "recover"}:
        report, exit_code = _write_command(args.command, ws, args.as_of)
    else:
        report, exit_code = _read_command(args.command, ws)
    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["inspect_project_state", "main", "project_state_diagnostic"]
