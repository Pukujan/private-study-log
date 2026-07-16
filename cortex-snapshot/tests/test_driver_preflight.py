from __future__ import annotations

import base64
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cortex_core import driver_preflight as dp
from cortex_core.assurance_contracts import contract_sha256
from cortex_core.assurance_evaluator import canonical_json
from cortex_core.driver_preflight import evaluate_driver_activation
from cortex_core.mcp import cortex_contract


NOW = "2026-07-15T12:00:00+00:00"


def execution_contract():
    return {
        "schema_version": 1,
        "contract_id": "exec-salesops-v1",
        "task_id": "salesops-001",
        "profile": "product_build",
        "runtime_mode": "governed",
        "driver": {
            "name": "hermes",
            "runtime_version": "observed-version",
            "model_id": "umans/umans-glm-5.2",
            "wrapper_version": "observed-wrapper",
        },
        "required_phases": ["INTAKE", "RESEARCH", "SPEC", "IMPLEMENT", "VERIFY"],
        "required_tools": ["cortex_run_start", "cortex_run_step"],
        "enabled_tools": ["cortex_run_start", "cortex_run_step", "cortex_run_state"],
        "research_policy": {"required": True, "evidence_types": ["source", "product_brief"]},
        "mission_policy": {
            "required": True,
            "claims_disjoint": True,
            "merge_receipt_required": True,
        },
        "fallback_behavior": "mark_ungoverned",
        "evidence_requirements": ["state_receipts", "artifact_hashes", "external_replay"],
        "telemetry_policy": {
            "required": True,
            "sinks": ["otel", "langfuse"],
            "correlation_field": "run_id",
            "missing_evidence_result": "UNRESOLVED",
        },
    }


def observation(contract: dict | None = None):
    contract = contract or execution_contract()
    return {
        "observer": "cross-driver-harness",
        "observer_issuer_id": "assurance-service",
        "observer_family": "independent-preflight",
        "observation_source": "external_harness",
        "execution_contract_sha256": contract_sha256(contract),
        "observed_at": "2026-07-15T11:55:00+00:00",
        "expires_at": "2026-07-15T12:30:00+00:00",
        "config_args_is_list": True,
        "mcp_connected": True,
        "mcp_session_id": "session-123",
        "discovered_tools": ["cortex_run_start", "cortex_run_step", "cortex_run_state"],
        "model_completion_verified": True,
        "model_probe_receipt": "probe:sha256:abc",
        "execution_contract_frozen_pre_run": True,
        "execution_contract_receipt": "contract:sha256:def",
        "active_run_id": "run-123",
        "active_track": "assured_build",
        "active_assurance_mode": "ASSURED",
        "route_id": "route-123",
        "route_receipt_ref": "routing-db:route-123",
        "model_route_bound": True,
        "external_evaluator_ready": True,
        "external_evaluator_id": "hidden-evaluator",
        "external_evaluator_trust_ref": "trust-root:external-key",
        "telemetry_ready": True,
        "telemetry_correlation_field": "run_id",
        "evidence_refs": ["event:1", "event:2"],
    }


@pytest.fixture()
def signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )
    root = {
        "schema_version": 1,
        "keys": {
            "preflight-key": {
                "public_key": base64.b64encode(public).decode(),
                "issuer_id": "assurance-service",
                "evaluator_id": "cross-driver-harness",
                "evaluator_family": "independent-preflight",
                "allowed_kinds": ["PREFLIGHT_OBSERVATION"],
                "not_before": "2026-01-01T00:00:00+00:00",
                "not_after": "2027-01-01T00:00:00+00:00",
                "revoked": False,
            }
        },
    }
    path = tmp_path / "trust-root.json"
    path.write_text(json.dumps(root), encoding="utf-8")
    monkeypatch.setenv("CORTEX_ASSURANCE_TRUST_ROOT", str(path))
    monkeypatch.setattr(dp, "_utcnow", lambda: datetime.fromisoformat(NOW))
    monkeypatch.setattr("cortex_core.assurance_evaluator._utcnow",
                        lambda: datetime.fromisoformat(NOW))
    return private


def envelope(private: Ed25519PrivateKey, observed: dict) -> dict:
    canonical = canonical_json(observed).encode("utf-8")
    return {
        "key_id": "preflight-key",
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature": base64.b64encode(private.sign(canonical)).decode(),
    }


def evaluate(contract: dict, observed: dict, signing: Ed25519PrivateKey) -> dict:
    return evaluate_driver_activation(contract, observed, envelope(signing, observed))


def test_complete_external_preflight_is_governed_active(signing):
    contract = execution_contract()
    observed = observation(contract)
    result = evaluate(contract, observed, signing)
    assert result == {
        "status": "GOVERNED_ACTIVE",
        "can_claim_governed": True,
        "problems": [],
        "run_id": "run-123",
        "track": "assured_build",
        "route_id": "route-123",
        "observation_sha256": hashlib.sha256(canonical_json(observed).encode("utf-8")).hexdigest(),
    }


def test_builder_mcp_can_verify_but_not_mint_external_assurance(signing, tmp_path: Path):
    contract = execution_contract()
    observed = observation(contract)
    checked = asyncio.run(cortex_contract(
        action="driver_preflight",
        assurance={
            "execution_contract": contract,
            "observation": observed,
            "signature_envelope": envelope(signing, observed),
        },
        workspace=str(tmp_path),
    ))
    assert checked["ok"] is True
    assert checked["preflight"]["status"] == "GOVERNED_ACTIVE"
    refused = asyncio.run(cortex_contract(
        action="store_result", workspace=str(tmp_path),
    ))
    assert refused["ok"] is False
    assert refused["code"] == "UNKNOWN_CONTRACT_ACTION"


def test_unsigned_or_tampered_preflight_cannot_claim_governance(signing):
    contract = execution_contract()
    observed = observation(contract)
    unsigned = evaluate_driver_activation(contract, observed)
    assert unsigned["status"] == "UNGOVERNED_RUN"
    assert any("signature" in p for p in unsigned["problems"])
    signed = envelope(signing, observed)
    observed["telemetry_ready"] = False
    tampered = evaluate_driver_activation(contract, observed, signed)
    assert tampered["status"] == "UNGOVERNED_RUN"
    assert any("signature" in p for p in tampered["problems"])


def test_legacy_track_and_unbound_route_cannot_claim_governance(signing):
    contract = execution_contract()
    observed = observation(contract)
    observed["active_track"] = "build"
    observed["active_assurance_mode"] = "LEGACY_UNASSURED"
    observed["model_route_bound"] = False
    result = evaluate(contract, observed, signing)
    assert result["status"] == "UNGOVERNED_RUN"
    assert any("assured_build" in p for p in result["problems"])
    assert any("not marked ASSURED" in p for p in result["problems"])
    assert any("not bound" in p for p in result["problems"])


def test_hermes_malformed_args_becomes_ungoverned(signing):
    contract, observed = execution_contract(), observation()
    observed["config_args_is_list"] = False
    result = evaluate(contract, observed, signing)
    assert result["status"] == "UNGOVERNED_RUN"
    assert any("native list" in p for p in result["problems"])


def test_missing_state_tool_cannot_claim_governance(signing):
    contract, observed = execution_contract(), observation()
    observed["discovered_tools"].remove("cortex_run_step")
    result = evaluate(contract, observed, signing)
    assert result["status"] == "UNGOVERNED_RUN"
    assert any("cortex_run_step" in p for p in result["problems"])


def test_model_list_is_not_a_completion_probe(signing):
    contract, observed = execution_contract(), observation()
    observed["model_completion_verified"] = False
    observed["model_probe_receipt"] = ""
    result = evaluate(contract, observed, signing)
    assert result["status"] == "UNGOVERNED_RUN"
    assert any("real model completion" in p for p in result["problems"])


def test_missing_run_telemetry_or_evaluator_cannot_claim_governance(signing):
    contract, observed = execution_contract(), observation()
    observed["active_run_id"] = None
    observed["telemetry_ready"] = False
    observed["external_evaluator_ready"] = False
    result = evaluate(contract, observed, signing)
    assert result["status"] == "UNGOVERNED_RUN"
    assert result["run_id"] is None
    assert any("run ID" in p for p in result["problems"])
    assert any("telemetry" in p for p in result["problems"])
    assert any("evaluator is not ready" in p for p in result["problems"])


def test_retrospective_contract_cannot_claim_governance(signing):
    contract, observed = execution_contract(), observation()
    observed["execution_contract_frozen_pre_run"] = False
    observed["execution_contract_receipt"] = ""
    result = evaluate(contract, observed, signing)
    assert result["status"] == "UNGOVERNED_RUN"
    assert any("before the run" in p for p in result["problems"])
    assert any("freeze receipt" in p for p in result["problems"])


def test_expired_or_wrong_contract_observation_fails(signing):
    contract, observed = execution_contract(), observation()
    observed["expires_at"] = "2026-07-15T11:59:00+00:00"
    result = evaluate(contract, observed, signing)
    assert any("expired" in p for p in result["problems"])
    observed = observation()
    observed["execution_contract_sha256"] = "f" * 64
    result = evaluate(contract, observed, signing)
    assert any("frozen execution contract" in p for p in result["problems"])


def test_fail_closed_policy_blocks_instead_of_falling_back(signing):
    contract = execution_contract()
    contract["fallback_behavior"] = "fail_closed"
    observed = observation(contract)
    observed["mcp_connected"] = False
    result = evaluate(contract, observed, signing)
    assert result["status"] == "BLOCKED"


def test_allow_advisory_is_visible_and_never_governed(signing):
    contract = execution_contract()
    contract["fallback_behavior"] = "allow_advisory"
    observed = observation(contract)
    observed["mcp_connected"] = False
    result = evaluate(contract, observed, signing)
    assert result["status"] == "ADVISORY_RUN"
    assert result["can_claim_governed"] is False


def test_invalid_contract_blocks_before_observation(signing):
    contract = deepcopy(execution_contract())
    contract["runtime_mode"] = "magic"
    result = evaluate_driver_activation(contract, observation())
    assert result["status"] == "BLOCKED"
    assert result["can_claim_governed"] is False
    assert any("invalid execution contract" in p for p in result["problems"])
