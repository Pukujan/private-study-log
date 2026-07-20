from copy import deepcopy
import json
from pathlib import Path

import pytest

from cortex_core.assurance_contracts import (
    EXECUTION_REQUIRED,
    SUCCESS_REQUIRED,
    contract_sha256,
    freeze_contract,
    validate_execution_contract,
    validate_success_contract,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


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


def success_contract():
    return {
        "schema_version": 1,
        "contract_id": "success-salesops-v1",
        "task_id": "salesops-001",
        "desired_user_outcomes": ["A sales operator can manage and understand pipeline health"],
        "observable_behaviors": ["Pipeline changes persist across restart"],
        "domain_invariants": ["Role permissions apply server-side"],
        "acceptance_criteria": ["Independent browser journeys pass"],
        "prohibited_behaviors": ["Generic gate success cannot certify product readiness"],
        "oracle_lanes": [{
            "oracle_id": "salesops-browser-v1",
            "authority": "external_observer",
            "scope": "approved user journeys",
            "fixture_sha256": "a" * 64,
            "hidden_from_builder": True,
        }],
        "evaluator_independence": {
            "builder_may_certify": False,
            "cross_driver_required": True,
            "external_replay_required": True,
        },
        "repeatability": {"minimum_distinct_drivers": 2, "clean_workspace_required": True},
        "human_review_boundaries": ["Product-quality acceptance remains owner-controlled"],
    }


def test_valid_contracts_pass_and_freeze():
    assert validate_execution_contract(execution_contract()) == (True, [])
    assert validate_success_contract(success_contract()) == (True, [])
    frozen = freeze_contract(success_contract(), kind="success")
    assert frozen["sha256"] == contract_sha256(success_contract())
    assert len(frozen["sha256"]) == 64


def test_contract_hash_is_order_independent_and_content_sensitive():
    original = success_contract()
    reordered = dict(reversed(list(original.items())))
    assert contract_sha256(original) == contract_sha256(reordered)
    changed = deepcopy(original)
    changed["desired_user_outcomes"] = ["Something else"]
    assert contract_sha256(original) != contract_sha256(changed)


def test_execution_rejects_missing_required_tool_and_unknown_field():
    contract = execution_contract()
    contract["enabled_tools"].remove("cortex_run_step")
    contract["surprise"] = True
    ok, problems = validate_execution_contract(contract)
    assert not ok
    assert any("required_tools are not enabled" in p for p in problems)
    assert any("unknown field" in p for p in problems)


def test_execution_rejects_silent_governance_telemetry_policy():
    contract = execution_contract()
    contract["telemetry_policy"]["missing_evidence_result"] = "PASS"
    ok, problems = validate_execution_contract(contract)
    assert not ok
    assert any("invalid verdict" in p for p in problems)


def test_success_rejects_builder_self_certification():
    contract = success_contract()
    contract["evaluator_independence"]["builder_may_certify"] = True
    ok, problems = validate_success_contract(contract)
    assert not ok
    assert "builder_may_certify must be false" in problems


def test_success_rejects_empty_oracle_and_bad_repeatability():
    contract = success_contract()
    contract["oracle_lanes"] = []
    contract["repeatability"]["minimum_distinct_drivers"] = 0
    ok, problems = validate_success_contract(contract)
    assert not ok
    assert "oracle_lanes must be a non-empty list" in problems
    assert any("minimum_distinct_drivers" in p for p in problems)


def test_freeze_rejects_invalid_contract_and_kind():
    bad = execution_contract()
    bad["runtime_mode"] = "pretend-governed"
    with pytest.raises(ValueError, match="invalid execution contract"):
        freeze_contract(bad, kind="execution")
    with pytest.raises(ValueError, match="kind must"):
        freeze_contract(success_contract(), kind="other")


def test_portable_json_schemas_do_not_drift_from_stdlib_validator():
    execution = json.loads((REPO_ROOT / "schemas/assurance/execution-contract.schema.json").read_text())
    success = json.loads((REPO_ROOT / "schemas/assurance/success-contract.schema.json").read_text())
    assert tuple(execution["required"]) == EXECUTION_REQUIRED
    assert tuple(success["required"]) == SUCCESS_REQUIRED
