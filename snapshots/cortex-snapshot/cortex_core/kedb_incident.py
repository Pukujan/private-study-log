"""Structured incident records for the pre-pattern stage of the Cortex KEDB.

``patterns.py`` intentionally requires repeated occurrences before promotion.
That must not erase the first real failure.  An incident records one observed
failure and its reproducer/provenance while it progresses toward a reusable
pattern or oracle.  Incident prose is evidence-indexed, never verdict authority.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

INCIDENT_SCHEMA_VERSION = 1

FAILURE_CLASSES = (
    "SM", "AUTH", "DRIVER", "MISSION", "REC", "ORACLE",
    "RAG", "AUDIT", "PRODUCT", "OPS",
)
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
STATUSES = (
    "observed", "expected_behavior_approved", "reproducer_ready",
    "fixed", "cross_driver_replayed", "oracle_candidate", "closed",
)

REQUIRED_FIELDS = (
    "schema_version", "id", "failure_class", "driver", "model",
    "runtime_version", "cortex_version", "task", "starting_state",
    "expected_behavior", "observed_behavior", "tool_trace",
    "artifact_hashes", "severity", "why_existing_tests_missed_it",
    "root_cause", "fix", "deterministic_reproducer", "cross_driver_replay",
    "oracle_candidate", "human_decision", "status",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_incident(record: Any) -> tuple[bool, list[str]]:
    if not isinstance(record, dict):
        return False, [f"incident must be an object, got {type(record).__name__}"]
    problems = [f"missing required field {f!r}" for f in REQUIRED_FIELDS if f not in record]
    problems.extend(f"unknown field {f!r}" for f in record if f not in REQUIRED_FIELDS)
    if record.get("schema_version") != INCIDENT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {INCIDENT_SCHEMA_VERSION}")
    if record.get("failure_class") not in FAILURE_CLASSES:
        problems.append(f"failure_class must be one of {FAILURE_CLASSES}")
    if record.get("severity") not in SEVERITIES:
        problems.append(f"severity must be one of {SEVERITIES}")
    if record.get("status") not in STATUSES:
        problems.append(f"status must be one of {STATUSES}")

    string_fields = (
        "id", "driver", "model", "runtime_version", "cortex_version", "task",
        "starting_state", "expected_behavior", "observed_behavior",
        "why_existing_tests_missed_it", "root_cause", "fix",
        "deterministic_reproducer", "cross_driver_replay", "human_decision",
    )
    for field in string_fields:
        if field in record and not _nonempty(record[field]):
            problems.append(f"{field} must be a non-empty string")

    trace = record.get("tool_trace")
    if not isinstance(trace, list) or not trace or any(not isinstance(item, dict) for item in trace):
        problems.append("tool_trace must be a non-empty list of evidence objects")
    else:
        for i, item in enumerate(trace):
            if set(item) != {"kind", "locator", "summary"}:
                problems.append(f"tool_trace[{i}] must contain exactly kind, locator, summary")
            elif any(not _nonempty(item.get(k)) for k in ("kind", "locator", "summary")):
                problems.append(f"tool_trace[{i}] fields must be non-empty strings")

    hashes = record.get("artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        problems.append("artifact_hashes must be a non-empty path-to-sha256 object")
    else:
        for path, digest in hashes.items():
            if not _nonempty(path) or not isinstance(digest, str) or not _SHA256_RE.match(digest):
                problems.append(f"artifact_hashes entry {path!r} must contain a sha256 hex digest")

    if "oracle_candidate" in record and not isinstance(record["oracle_candidate"], bool):
        problems.append("oracle_candidate must be boolean")
    if record.get("oracle_candidate") and record.get("status") not in ("oracle_candidate", "closed"):
        problems.append("oracle_candidate true requires oracle_candidate or closed status")
    return not problems, problems


def load_incident(path: str | Path) -> dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    ok, problems = validate_incident(record)
    if not ok:
        raise ValueError(f"invalid KEDB incident {path}: {problems}")
    return record
