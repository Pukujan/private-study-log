"""Separate pre-run execution and success contracts for Cortex assurance.

Procedure and outcome are deliberately independent.  An execution contract
describes how a driver must run; a success contract describes what a user must
be able to observe in the final result.  Neither contract contains a verdict.
Both are frozen before execution with a canonical SHA-256 digest.

The JSON Schema documents in ``schemas/assurance`` are the portable public
form.  This module supplies a small strict stdlib validator so Cortex does not
need a runtime JSON-Schema dependency merely to enforce its own boundary.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

CONTRACT_SCHEMA_VERSION = 1

EXECUTION_REQUIRED = (
    "schema_version", "contract_id", "task_id", "profile", "runtime_mode",
    "driver", "required_phases", "required_tools", "enabled_tools",
    "research_policy", "mission_policy", "fallback_behavior",
    "evidence_requirements", "telemetry_policy",
)

SUCCESS_REQUIRED = (
    "schema_version", "contract_id", "task_id", "desired_user_outcomes",
    "observable_behaviors", "domain_invariants", "acceptance_criteria",
    "prohibited_behaviors", "oracle_lanes", "evaluator_independence",
    "repeatability", "human_review_boundaries",
)

RUNTIME_MODES = ("governed", "advisory", "ungoverned")
FALLBACK_BEHAVIORS = ("fail_closed", "mark_ungoverned", "allow_advisory")
ORACLE_AUTHORITIES = ("deterministic", "external_observer", "human")


def canonical_contract_json(contract: dict[str, Any]) -> str:
    """Stable representation used to freeze a contract before a run."""
    return json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contract_sha256(contract: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_json(contract).encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, field: str, problems: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or any(not _nonempty(item) for item in value):
        problems.append(f"{field} must be a list of non-empty strings")
    elif not allow_empty and not value:
        problems.append(f"{field} must not be empty")


def _strict_fields(contract: Any, required: tuple[str, ...]) -> tuple[list[str], dict[str, Any] | None]:
    if not isinstance(contract, dict):
        return [f"contract must be an object, got {type(contract).__name__}"], None
    problems = [f"missing required field {name!r}" for name in required if name not in contract]
    problems.extend(f"unknown field {name!r}" for name in contract if name not in required)
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {CONTRACT_SCHEMA_VERSION}")
    for field in ("contract_id", "task_id"):
        if field in contract and not _nonempty(contract[field]):
            problems.append(f"{field} must be a non-empty string")
    return problems, contract


def validate_execution_contract(contract: Any) -> tuple[bool, list[str]]:
    problems, obj = _strict_fields(contract, EXECUTION_REQUIRED)
    if obj is None:
        return False, problems

    if obj.get("runtime_mode") not in RUNTIME_MODES:
        problems.append(f"runtime_mode must be one of {RUNTIME_MODES}")
    if not _nonempty(obj.get("profile")):
        problems.append("profile must be a non-empty string")
    if obj.get("fallback_behavior") not in FALLBACK_BEHAVIORS:
        problems.append(f"fallback_behavior must be one of {FALLBACK_BEHAVIORS}")

    driver = obj.get("driver")
    if not isinstance(driver, dict):
        problems.append("driver must be an object")
    else:
        expected = {"name", "runtime_version", "model_id", "wrapper_version"}
        if set(driver) != expected or any(not _nonempty(driver.get(k)) for k in expected):
            problems.append(f"driver must contain exactly non-empty {sorted(expected)}")

    for field in ("required_phases", "required_tools", "enabled_tools", "evidence_requirements"):
        if field in obj:
            _string_list(obj[field], field, problems)

    if isinstance(obj.get("required_tools"), list) and isinstance(obj.get("enabled_tools"), list):
        missing = sorted(set(obj["required_tools"]) - set(obj["enabled_tools"]))
        if missing:
            problems.append(f"required_tools are not enabled: {missing}")

    research = obj.get("research_policy")
    if not isinstance(research, dict) or set(research) != {"required", "evidence_types"}:
        problems.append("research_policy must contain exactly required and evidence_types")
    else:
        if not isinstance(research["required"], bool):
            problems.append("research_policy.required must be boolean")
        _string_list(research["evidence_types"], "research_policy.evidence_types", problems,
                     allow_empty=not bool(research.get("required")))

    mission = obj.get("mission_policy")
    mission_fields = {"required", "claims_disjoint", "merge_receipt_required"}
    if not isinstance(mission, dict) or set(mission) != mission_fields:
        problems.append(f"mission_policy must contain exactly {sorted(mission_fields)}")
    elif any(not isinstance(mission[k], bool) for k in mission_fields):
        problems.append("mission_policy values must be boolean")

    telemetry = obj.get("telemetry_policy")
    telemetry_fields = {"required", "sinks", "correlation_field", "missing_evidence_result"}
    if not isinstance(telemetry, dict) or set(telemetry) != telemetry_fields:
        problems.append(f"telemetry_policy must contain exactly {sorted(telemetry_fields)}")
    else:
        if not isinstance(telemetry["required"], bool):
            problems.append("telemetry_policy.required must be boolean")
        _string_list(telemetry["sinks"], "telemetry_policy.sinks", problems,
                     allow_empty=not bool(telemetry.get("required")))
        if not _nonempty(telemetry["correlation_field"]):
            problems.append("telemetry_policy.correlation_field must be non-empty")
        if telemetry["missing_evidence_result"] not in ("FAIL", "UNRESOLVED", "ENVIRONMENT_UNAVAILABLE"):
            problems.append("telemetry_policy.missing_evidence_result has invalid verdict")

    return not problems, problems


def validate_success_contract(contract: Any) -> tuple[bool, list[str]]:
    problems, obj = _strict_fields(contract, SUCCESS_REQUIRED)
    if obj is None:
        return False, problems

    for field in (
        "desired_user_outcomes", "observable_behaviors", "domain_invariants",
        "acceptance_criteria", "prohibited_behaviors", "human_review_boundaries",
    ):
        if field in obj:
            _string_list(obj[field], field, problems)

    lanes = obj.get("oracle_lanes")
    if not isinstance(lanes, list) or not lanes:
        problems.append("oracle_lanes must be a non-empty list")
    else:
        expected = {"oracle_id", "authority", "scope", "fixture_sha256", "hidden_from_builder"}
        for i, lane in enumerate(lanes):
            if not isinstance(lane, dict) or set(lane) != expected:
                problems.append(f"oracle_lanes[{i}] must contain exactly {sorted(expected)}")
                continue
            if lane["authority"] not in ORACLE_AUTHORITIES:
                problems.append(f"oracle_lanes[{i}].authority must be one of {ORACLE_AUTHORITIES}")
            for field in ("oracle_id", "scope", "fixture_sha256"):
                if not _nonempty(lane[field]):
                    problems.append(f"oracle_lanes[{i}].{field} must be non-empty")
            if not isinstance(lane["hidden_from_builder"], bool):
                problems.append(f"oracle_lanes[{i}].hidden_from_builder must be boolean")

    independence = obj.get("evaluator_independence")
    independence_fields = {"builder_may_certify", "cross_driver_required", "external_replay_required"}
    if not isinstance(independence, dict) or set(independence) != independence_fields:
        problems.append(f"evaluator_independence must contain exactly {sorted(independence_fields)}")
    elif any(not isinstance(independence[k], bool) for k in independence_fields):
        problems.append("evaluator_independence values must be boolean")
    elif independence["builder_may_certify"]:
        problems.append("builder_may_certify must be false")

    repeatability = obj.get("repeatability")
    repeatability_fields = {"minimum_distinct_drivers", "clean_workspace_required"}
    if not isinstance(repeatability, dict) or set(repeatability) != repeatability_fields:
        problems.append(f"repeatability must contain exactly {sorted(repeatability_fields)}")
    else:
        n = repeatability["minimum_distinct_drivers"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            problems.append("repeatability.minimum_distinct_drivers must be an integer >= 1")
        if not isinstance(repeatability["clean_workspace_required"], bool):
            problems.append("repeatability.clean_workspace_required must be boolean")

    return not problems, problems


def freeze_contract(contract: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Validate and return a frozen envelope containing the canonical digest."""
    validator = {
        "execution": validate_execution_contract,
        "success": validate_success_contract,
    }.get(kind)
    if validator is None:
        raise ValueError("kind must be 'execution' or 'success'")
    ok, problems = validator(contract)
    if not ok:
        raise ValueError(f"invalid {kind} contract: {problems}")
    return {"kind": kind, "sha256": contract_sha256(contract), "contract": contract}
