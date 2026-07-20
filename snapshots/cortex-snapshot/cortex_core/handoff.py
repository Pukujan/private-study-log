from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class Handoff:
    task: str
    phase: str
    owner: str
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    status: str = "pending"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


REQUIRED_KEYS = {"task", "phase", "owner"}


def build_handoff(task: str, phase: str, owner: str, acceptance_criteria: list[str] | None = None, evidence: list[str] | None = None, status: str = "pending") -> dict[str, Any]:
    return {
        "task": task,
        "phase": phase,
        "owner": owner,
        "acceptance_criteria": list(acceptance_criteria or []),
        "evidence": list(evidence or []),
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_handoff(payload: Mapping[str, Any]) -> None:
    missing = REQUIRED_KEYS.difference(payload.keys())
    if missing:
        raise ValueError(f"handoff payload missing required keys: {sorted(missing)}")
    if not str(payload["task"]).strip():
        raise ValueError("handoff task must not be empty")
    if not str(payload["phase"]).strip():
        raise ValueError("handoff phase must not be empty")
    if not str(payload["owner"]).strip():
        raise ValueError("handoff owner must not be empty")
