"""Deterministic activation check for a driver claiming Cortex governance.

The caller supplies observations captured by an external harness or protocol
inspector.  This module does not perform the observations and cannot turn
self-reported booleans into evidence; it only applies the frozen execution
contract consistently.  A wrapper must display the returned status verbatim.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cortex_core.assurance_contracts import contract_sha256, validate_execution_contract
from cortex_core.assurance_evaluator import verify_identity_signature

PREFLIGHT_STATUSES = ("GOVERNED_ACTIVE", "ADVISORY_RUN", "UNGOVERNED_RUN", "BLOCKED")

OBSERVATION_FIELDS = {
    "observer", "observer_issuer_id", "observer_family", "observation_source",
    "execution_contract_sha256", "observed_at", "expires_at",
    "config_args_is_list", "mcp_connected",
    "mcp_session_id", "discovered_tools", "model_completion_verified",
    "model_probe_receipt", "execution_contract_frozen_pre_run",
    "execution_contract_receipt", "active_run_id", "active_track",
    "active_assurance_mode", "route_id", "route_receipt_ref", "model_route_bound",
    "external_evaluator_ready", "external_evaluator_id", "external_evaluator_trust_ref",
    "telemetry_ready", "telemetry_correlation_field", "evidence_refs",
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _time(value: Any, field: str) -> datetime:
    if not _nonempty(value):
        raise ValueError(f"{field} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utcnow() -> datetime:
    """Authoritative process clock; tests monkeypatch this private seam."""
    return datetime.now(timezone.utc)


def evaluate_driver_activation(
    execution_contract: dict[str, Any], observation: Any,
    signature_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the only governance status a wrapper is allowed to claim.

    Governed activation requires a signed external observation.  An unsigned
    wrapper self-report can still be classified according to the contract's
    fallback behavior, but it can never produce ``GOVERNED_ACTIVE``.
    """
    ok, contract_problems = validate_execution_contract(execution_contract)
    if not ok:
        return {
            "status": "BLOCKED",
            "can_claim_governed": False,
            "problems": [f"invalid execution contract: {p}" for p in contract_problems],
        }

    problems: list[str] = []
    if not isinstance(observation, dict):
        problems.append(f"observation must be an object, got {type(observation).__name__}")
        observation = {}
    else:
        unknown = sorted(set(observation) - OBSERVATION_FIELDS)
        missing = sorted(OBSERVATION_FIELDS - set(observation))
        problems.extend(f"unknown observation field {f!r}" for f in unknown)
        problems.extend(f"missing observation field {f!r}" for f in missing)

    if not _nonempty(observation.get("observer")):
        problems.append("external observer identity is missing")
    if not _nonempty(observation.get("observer_issuer_id")):
        problems.append("external observer issuer identity is missing")
    if not _nonempty(observation.get("observer_family")):
        problems.append("external observer family is missing")
    if observation.get("observation_source") not in ("external_harness", "mcp_inspector"):
        problems.append("observation_source must be external_harness or mcp_inspector")
    expected_contract_sha = contract_sha256(execution_contract)
    if observation.get("execution_contract_sha256") != expected_contract_sha:
        problems.append("preflight observation does not bind the frozen execution contract")
    instant = _utcnow()
    try:
        observed_at = _time(observation.get("observed_at"), "observed_at")
        expires_at = _time(observation.get("expires_at"), "expires_at")
        if observed_at >= expires_at:
            problems.append("preflight expires_at must be later than observed_at")
        if instant < observed_at:
            problems.append("preflight observation is future-dated")
        if instant >= expires_at:
            problems.append("preflight observation is expired")
    except ValueError as exc:
        problems.append(str(exc))
    signature = None
    try:
        signature = verify_identity_signature(
            observation, signature_envelope, kind="PREFLIGHT_OBSERVATION",
            issuer_field="observer_issuer_id", identity_field="observer",
            family_field="observer_family",
        )
    except ValueError as exc:
        problems.append(f"external preflight signature is invalid: {exc}")
    if observation.get("config_args_is_list") is not True:
        problems.append("MCP argument configuration is not a native list")
    if observation.get("mcp_connected") is not True:
        problems.append("Cortex MCP connection is not live")
    if not _nonempty(observation.get("mcp_session_id")):
        problems.append("server-issued MCP session identity is missing")

    discovered = observation.get("discovered_tools")
    if not isinstance(discovered, list) or any(not _nonempty(t) for t in discovered):
        problems.append("discovered_tools must be a list of non-empty tool names")
    else:
        missing_tools = sorted(set(execution_contract["required_tools"]) - set(discovered))
        if missing_tools:
            problems.append(f"required Cortex tools were not discovered: {missing_tools}")

    if observation.get("model_completion_verified") is not True:
        problems.append("real model completion probe did not pass")
    if not _nonempty(observation.get("model_probe_receipt")):
        problems.append("model completion probe receipt is missing")
    if observation.get("execution_contract_frozen_pre_run") is not True:
        problems.append("execution contract was not externally frozen before the run")
    if not _nonempty(observation.get("execution_contract_receipt")):
        problems.append("pre-run execution contract freeze receipt is missing")
    if not _nonempty(observation.get("active_run_id")):
        problems.append("active Cortex run ID is missing")
    if execution_contract["runtime_mode"] == "governed":
        if observation.get("active_track") not in ("assured_build", "assured_research"):
            problems.append("governed mode requires assured_build or assured_research")
        if observation.get("active_assurance_mode") != "ASSURED":
            problems.append("the active Cortex run is not marked ASSURED")
        if not _nonempty(observation.get("route_id")):
            problems.append("capability-qualified model route ID is missing")
        if not _nonempty(observation.get("route_receipt_ref")):
            problems.append("model route receipt reference is missing")
        if observation.get("model_route_bound") is not True:
            problems.append("the selected model route is not bound to the actual model call")
        if observation.get("external_evaluator_ready") is not True:
            problems.append("external evaluator is not ready before execution")
        if not _nonempty(observation.get("external_evaluator_id")):
            problems.append("external evaluator identity is missing")
        if not _nonempty(observation.get("external_evaluator_trust_ref")):
            problems.append("external evaluator trust reference is missing")

    telemetry = execution_contract["telemetry_policy"]
    if telemetry["required"]:
        if observation.get("telemetry_ready") is not True:
            problems.append("required telemetry correlation is unavailable")
        if observation.get("telemetry_correlation_field") != telemetry["correlation_field"]:
            problems.append("telemetry correlation field does not match the execution contract")

    refs = observation.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not _nonempty(r) for r in refs):
        problems.append("preflight requires external evidence references")

    requested_mode = execution_contract["runtime_mode"]
    fallback = execution_contract["fallback_behavior"]
    if problems:
        status = {
            "fail_closed": "BLOCKED",
            "mark_ungoverned": "UNGOVERNED_RUN",
            "allow_advisory": "ADVISORY_RUN",
        }[fallback]
    elif requested_mode == "governed":
        status = "GOVERNED_ACTIVE"
    elif requested_mode == "advisory":
        status = "ADVISORY_RUN"
    else:
        status = "UNGOVERNED_RUN"

    return {
        "status": status,
        "can_claim_governed": status == "GOVERNED_ACTIVE",
        "problems": problems,
        "run_id": observation.get("active_run_id") if status == "GOVERNED_ACTIVE" else None,
        "track": observation.get("active_track") if status == "GOVERNED_ACTIVE" else None,
        "route_id": observation.get("route_id") if status == "GOVERNED_ACTIVE" else None,
        "observation_sha256": signature.get("payload_sha256") if signature else None,
    }
