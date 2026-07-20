from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PHASE_SECONDS = 480
DEFAULT_HEARTBEAT_SECONDS = 60
MAX_EMPTY_OUTPUTS_BEFORE_ESCALATION = 3

_SECTION_MARKER_ONLY_RE = re.compile(
    r"^(?:\s|<!--\s*SECTION:[^>]+-->|SECTION:[A-Za-z0-9_.:-]+(?::PENDING)?)*$"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> int:
    return int(time.time())


def _state_path(workspace: str | Path) -> Path:
    root = Path(workspace)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "phase_runtime.json"


def _empty_state() -> dict[str, Any]:
    return {"version": 1, "records": {}, "resume_index": {}}


def _load(workspace: str | Path) -> dict[str, Any]:
    path = _state_path(workspace)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("version", 1)
    data.setdefault("records", {})
    data.setdefault("resume_index", {})
    return data


def _save(workspace: str | Path, data: dict[str, Any]) -> None:
    path = _state_path(workspace)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _resume_key(workspace: str | Path, task_id: str) -> str:
    material = f"{Path(workspace).resolve()}::{task_id}".encode("utf-8", errors="ignore")
    return hashlib.sha256(material).hexdigest()[:20]


def _coerce_positive_int(value: Any, default: int, *, floor: int, ceiling: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(ceiling, max(floor, parsed))


def _normalise_phases(phases: list[dict[str, Any]] | None,
                      phase_seconds: int) -> list[dict[str, Any]]:
    if not phases:
        phases = [
            {
                "phase_id": "plan",
                "name": "Ground, scope, and plan",
                "expected_outputs": ["bounded plan", "acceptance criteria", "resume notes"],
            },
            {
                "phase_id": "execute",
                "name": "Execute the scoped work",
                "expected_outputs": ["changed files or generated artifact", "partial outputs"],
            },
            {
                "phase_id": "verify",
                "name": "Verify against the acceptance criteria",
                "expected_outputs": ["test results", "review notes", "remaining risks"],
            },
            {
                "phase_id": "finalize",
                "name": "Close out and hand off",
                "expected_outputs": ["closeout id", "resume-safe summary"],
            },
        ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(phases):
        if not isinstance(raw, dict):
            raise ValueError("each phase must be a dict")
        phase_id = str(raw.get("phase_id") or raw.get("id") or f"phase_{i + 1}").strip()
        if not phase_id:
            raise ValueError("phase_id cannot be empty")
        if phase_id in seen:
            raise ValueError(f"duplicate phase_id: {phase_id}")
        seen.add(phase_id)
        out.append({
            **raw,
            "phase_id": phase_id,
            "name": str(raw.get("name") or phase_id),
            "max_seconds": _coerce_positive_int(
                raw.get("max_seconds"), phase_seconds, floor=60, ceiling=3600
            ),
        })
    return out


def _resolve_record(data: dict[str, Any], *, task_id: str | None = None,
                    resume_key: str | None = None) -> tuple[str, dict[str, Any]]:
    records = data.get("records", {})
    if task_id and task_id in records:
        return task_id, records[task_id]
    if resume_key:
        mapped = data.get("resume_index", {}).get(resume_key)
        if mapped and mapped in records:
            return mapped, records[mapped]
    raise KeyError("phase runtime record not found")


def _current_phase(record: dict[str, Any]) -> dict[str, Any]:
    phases = record.get("phases") or []
    idx = min(max(int(record.get("phase_index", 0)), 0), max(len(phases) - 1, 0))
    if not phases:
        return {}
    return phases[idx]


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(record, default=str))


def create_phase_plan(workspace: str | Path, task_id: str, intent: dict[str, Any],
                      track: str = "build", session_id: str | None = None,
                      phases: list[dict[str, Any]] | None = None,
                      phase_seconds: int = DEFAULT_PHASE_SECONDS,
                      heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS) -> dict[str, Any]:
    """Create or return the durable phase plan for a state-machine task."""
    phase_seconds = _coerce_positive_int(
        phase_seconds, DEFAULT_PHASE_SECONDS, floor=60, ceiling=3600
    )
    heartbeat_seconds = _coerce_positive_int(
        heartbeat_seconds, DEFAULT_HEARTBEAT_SECONDS, floor=15, ceiling=600
    )
    norm_phases = _normalise_phases(phases, phase_seconds)
    data = _load(workspace)
    if task_id in data["records"]:
        return _public(data["records"][task_id])
    now = _now_iso()
    now_epoch = _now_epoch()
    resume_key = _resume_key(workspace, task_id)
    active = norm_phases[0]
    record = {
        "job_id": task_id,
        "task_id": task_id,
        "session_id": session_id or "",
        "track": track,
        "intent": intent,
        "status": "planned",
        "phase_index": 0,
        "phase_id": active["phase_id"],
        "phase_status": "pending",
        "phase_seconds": phase_seconds,
        "heartbeat_seconds": heartbeat_seconds,
        "heartbeat_at": now,
        "lease_until_epoch": now_epoch + phase_seconds,
        "checkpoint_state": {},
        "partial_outputs": [],
        "lane_budget": {
            "max_phase_seconds": phase_seconds,
            "heartbeat_seconds": heartbeat_seconds,
            "max_turns": 0,
        },
        "resume_key": resume_key,
        "retry_count": 0,
        "empty_output_count": 0,
        "stuck_loop_flags": {},
        "created_at": now,
        "updated_at": now,
        "phases": norm_phases,
    }
    data["records"][task_id] = record
    data["resume_index"][resume_key] = task_id
    _save(workspace, data)
    return _public(record)


def get_phase_state(workspace: str | Path, task_id: str | None = None,
                    resume_key: str | None = None) -> dict[str, Any]:
    data = _load(workspace)
    _, record = _resolve_record(data, task_id=task_id, resume_key=resume_key)
    if record.get("status") not in ("done", "escalated"):
        if int(record.get("lease_until_epoch", 0)) < _now_epoch():
            record["status"] = "heartbeat_lost"
            record["phase_status"] = "stale"
            record["updated_at"] = _now_iso()
            _save(workspace, data)
    return _public(record)


def heartbeat_phase(workspace: str | Path, task_id: str | None = None,
                    resume_key: str | None = None, phase_id: str | None = None,
                    partial_outputs: list[dict[str, Any]] | None = None,
                    checkpoint_state: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _load(workspace)
    key, record = _resolve_record(data, task_id=task_id, resume_key=resume_key)
    if phase_id and phase_id != record.get("phase_id"):
        raise ValueError(f"phase_id {phase_id!r} is not active")
    now = _now_iso()
    now_epoch = _now_epoch()
    if record.get("status") not in ("done", "escalated"):
        record["status"] = "running"
        record["phase_status"] = "running"
    record["heartbeat_at"] = now
    record["lease_until_epoch"] = now_epoch + int(record.get("phase_seconds", DEFAULT_PHASE_SECONDS))
    record["updated_at"] = now
    if checkpoint_state is not None:
        record["checkpoint_state"] = checkpoint_state
    if partial_outputs:
        record.setdefault("partial_outputs", []).extend(partial_outputs)
    data["records"][key] = record
    _save(workspace, data)
    return _public(record)


def checkpoint_phase(workspace: str | Path, task_id: str | None = None,
                     resume_key: str | None = None, phase_id: str | None = None,
                     checkpoint_state: dict[str, Any] | None = None,
                     partial_outputs: list[dict[str, Any]] | None = None,
                     advance: bool = False) -> dict[str, Any]:
    data = _load(workspace)
    key, record = _resolve_record(data, task_id=task_id, resume_key=resume_key)
    if phase_id and phase_id != record.get("phase_id"):
        raise ValueError(f"phase_id {phase_id!r} is not active")
    if checkpoint_state is not None:
        record["checkpoint_state"] = checkpoint_state
    if partial_outputs:
        record.setdefault("partial_outputs", []).extend(partial_outputs)
    if record.get("status") not in ("done", "escalated"):
        record["status"] = "checkpointed"
        record["phase_status"] = "checkpointed"
    if advance:
        phases = record.get("phases") or []
        next_idx = int(record.get("phase_index", 0)) + 1
        if next_idx >= len(phases):
            record["status"] = "done"
            record["phase_status"] = "done"
        else:
            record["phase_index"] = next_idx
            record["phase_id"] = phases[next_idx]["phase_id"]
            record["phase_status"] = "pending"
            record["status"] = "planned"
    now = _now_iso()
    record["heartbeat_at"] = now
    record["lease_until_epoch"] = _now_epoch() + int(record.get("phase_seconds", DEFAULT_PHASE_SECONDS))
    record["updated_at"] = now
    data["records"][key] = record
    _save(workspace, data)
    return _public(record)


def resume_phase(workspace: str | Path, task_id: str | None = None,
                 resume_key: str | None = None) -> dict[str, Any]:
    record = get_phase_state(workspace, task_id=task_id, resume_key=resume_key)
    phase = _current_phase(record)
    action = "continue_phase"
    if record.get("status") == "heartbeat_lost":
        action = "resume_from_checkpoint"
    elif record.get("status") == "escalated":
        action = "handoff_to_stronger_lane"
    return {
        "ok": True,
        "task_id": record["task_id"],
        "resume_key": record["resume_key"],
        "next_action": action,
        "active_phase": phase,
        "phase_state": record,
    }


def looks_empty_output(raw_output: Any) -> bool:
    if raw_output is None:
        return True
    text = str(raw_output).strip()
    if not text:
        return True
    if text in ("{}", "[]", "null"):
        return True
    return bool(_SECTION_MARKER_ONLY_RE.fullmatch(text))


def report_empty_output(workspace: str | Path, task_id: str | None = None,
                        resume_key: str | None = None, model_id: str = "",
                        prompt_hash: str = "", raw_output: Any = "") -> dict[str, Any]:
    data = _load(workspace)
    key, record = _resolve_record(data, task_id=task_id, resume_key=resume_key)
    empty = looks_empty_output(raw_output)
    if not empty:
        return {
            "ok": True,
            "empty_output_detected": False,
            "action": "continue_phase",
            "phase_state": _public(record),
        }
    count = int(record.get("empty_output_count", 0)) + 1
    record["empty_output_count"] = count
    record["retry_count"] = int(record.get("retry_count", 0)) + 1
    record["last_empty_output"] = {
        "model_id": model_id,
        "prompt_hash": prompt_hash,
        "timestamp_utc": _now_iso(),
    }
    if count == 1:
        action = "retry_tightened_prompt"
        record["status"] = "retrying"
        record["phase_status"] = "retrying_empty_output"
    elif count == 2:
        action = "switch_backend_or_lane"
        record["status"] = "retrying"
        record["phase_status"] = "retrying_empty_output"
        record.setdefault("stuck_loop_flags", {})["empty_output_repeat"] = True
    else:
        action = "escalate"
        record["status"] = "escalated"
        record["phase_status"] = "failed_empty_output"
        record.setdefault("stuck_loop_flags", {})["empty_output_escalated"] = True
    now = _now_iso()
    record["heartbeat_at"] = now
    record["lease_until_epoch"] = _now_epoch() + int(record.get("phase_seconds", DEFAULT_PHASE_SECONDS))
    record["updated_at"] = now
    data["records"][key] = record
    _save(workspace, data)
    return {
        "ok": True,
        "empty_output_detected": True,
        "empty_output_count": count,
        "action": action,
        "never_mark_complete": True,
        "phase_state": _public(record),
    }
