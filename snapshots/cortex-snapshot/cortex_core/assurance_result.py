"""Non-averaging final result format for a Cortex-assured run."""
from __future__ import annotations

import re
from typing import Any

ASSURANCE_RESULT_SCHEMA_VERSION = 1
VERDICTS = ("PASS", "FAIL", "ABSTAIN", "UNRESOLVED", "ENVIRONMENT_UNAVAILABLE")
AXES = (
    "procedure", "behavior", "evidence", "independence",
    "repeatability", "human_acceptance",
)
REQUIRED_FIELDS = (
    "schema_version", "run_id", "execution_contract_sha256",
    "success_contract_sha256", "artifact_hashes", "evidence_refs",
    "axis_verdicts", "unresolved",
)
OPTIONAL_FIELDS = ("overall_verdict",)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def overall_verdict(axis_verdicts: dict[str, str]) -> str:
    """Hard gates dominate; no mean can average away a failed axis."""
    values = set(axis_verdicts.values())
    for verdict in ("FAIL", "ENVIRONMENT_UNAVAILABLE", "UNRESOLVED", "ABSTAIN"):
        if verdict in values:
            return verdict
    return "PASS"


def validate_assurance_result(result: Any) -> tuple[bool, list[str]]:
    if not isinstance(result, dict):
        return False, [f"result must be an object, got {type(result).__name__}"]
    problems = [f"missing required field {f!r}" for f in REQUIRED_FIELDS if f not in result]
    problems.extend(
        f"unknown field {f!r}" for f in result
        if f not in REQUIRED_FIELDS and f not in OPTIONAL_FIELDS
    )
    if result.get("schema_version") != ASSURANCE_RESULT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {ASSURANCE_RESULT_SCHEMA_VERSION}")
    if not isinstance(result.get("run_id"), str) or not result.get("run_id", "").strip():
        problems.append("run_id must be a non-empty string")
    for field in ("execution_contract_sha256", "success_contract_sha256"):
        if not isinstance(result.get(field), str) or not _SHA256_RE.match(result.get(field, "")):
            problems.append(f"{field} must be a sha256 hex digest")

    hashes = result.get("artifact_hashes")
    if not isinstance(hashes, dict) or any(
        not isinstance(path, str) or not path.strip()
        or not isinstance(digest, str) or not _SHA256_RE.match(digest)
        for path, digest in (hashes.items() if isinstance(hashes, dict) else [])
    ):
        problems.append("artifact_hashes must be a path-to-sha256 object")

    refs = result.get("evidence_refs")
    if not isinstance(refs, list) or any(not isinstance(r, str) or not r.strip() for r in refs):
        problems.append("evidence_refs must be a list of non-empty strings")

    axes = result.get("axis_verdicts")
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        problems.append(f"axis_verdicts must contain exactly {list(AXES)}")
    elif any(verdict not in VERDICTS for verdict in axes.values()):
        problems.append(f"axis verdicts must be one of {VERDICTS}")
    elif "overall_verdict" in result and result["overall_verdict"] != overall_verdict(axes):
        problems.append("overall_verdict does not match the non-averaging axis result")

    unresolved = result.get("unresolved")
    if not isinstance(unresolved, list) or any(not isinstance(v, str) or not v.strip() for v in unresolved):
        problems.append("unresolved must be a list of non-empty strings")
    if isinstance(axes, dict) and any(v in ("ABSTAIN", "UNRESOLVED", "ENVIRONMENT_UNAVAILABLE")
                                      for v in axes.values()) and not unresolved:
        problems.append("non-pass uncertainty verdicts require at least one unresolved explanation")
    return not problems, problems


def finalize_assurance_result(result: dict[str, Any]) -> dict[str, Any]:
    ok, problems = validate_assurance_result(result)
    if not ok:
        raise ValueError(f"invalid assurance result: {problems}")
    return {**result, "overall_verdict": overall_verdict(result["axis_verdicts"])}
