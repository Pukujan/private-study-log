import hashlib
import json
from pathlib import Path

from cortex_core.assurance_contracts import contract_sha256, validate_success_contract
from cortex_core.research_sufficiency import validate_sufficiency_policy


REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_ROOT = REPO_ROOT / "evals" / "cross_driver_assurance" / "scenarios" / "caseos-v1"
DERIVED_ROOT = REPO_ROOT / "evals" / "cross_driver_assurance" / "derived"


def _json(name: str):
    return json.loads((SCENARIO_ROOT / name).read_text(encoding="utf-8"))


def test_caseos_public_boundary_freeze_matches_files():
    receipt = _json("public-boundary-freeze-receipt.json")
    assert receipt["driver_exposure_status"] == "not_exposed"
    assert receipt["change_control"]["changes_require_new_scenario_version"] is True
    for relative_path, expected in receipt["artifacts"].items():
        actual = hashlib.sha256((REPO_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected, f"frozen CaseOS public boundary changed: {relative_path}"


def test_caseos_boundary_forbids_self_certification_and_unsafe_legal_defaults():
    boundary = _json("success-boundary.json")
    assert boundary["evaluator_independence"]["builder_may_certify"] is False
    assert boundary["evaluator_independence"]["hidden_fixture_required"] is True
    assert boundary["verdict_policy"]["missing_professional_authority"] == "ABSTAIN"
    assert boundary["verdict_policy"]["known_bad_false_pass_budget"] == 0
    assert any("does not invent court rules" in item for item in boundary["domain_invariants"])
    assert any("generic dashboard" in item.lower() for item in boundary["prohibited_behaviors"])


def test_caseos_execution_requirements_fail_closed_and_require_missing_spine():
    requirements = _json("execution-requirements.json")
    assert requirements["fallback_behavior"] == "fail_closed"
    required = set(requirements["required_cortex_capabilities"])
    assert {
        "composite_local_knowledge_search",
        "decision_specific_research_sufficiency_receipt",
        "capability_based_model_routing_and_escalation",
        "external_evaluator_dispatch",
        "joined_otel_and_langfuse_correlation",
    } <= required
    assert "raw_ungoverned_baseline" in requirements["raw_baseline_exception"]


def test_caseos_scenario_freeze_matches_every_public_artifact_and_baseline():
    receipt = _json("scenario-freeze-receipt.json")
    assert receipt["driver_exposure_status"] == "not_exposed"
    assert receipt["scenario_readiness"] == "CONTRACT_AND_EVALUATOR_READY_CORTEX_EXECUTION_SPINE_BLOCKED"
    baseline_receipt = (
        REPO_ROOT
        / "evals"
        / "cross_driver_assurance"
        / "contracts"
        / "production-behavior-v2"
        / "freeze-receipt.json"
    )
    assert hashlib.sha256(baseline_receipt.read_bytes()).hexdigest() == receipt["baseline_freeze_receipt_sha256"]
    for relative_path, expected in receipt["artifacts"].items():
        actual = hashlib.sha256((REPO_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected, f"frozen CaseOS scenario artifact changed: {relative_path}"


def test_caseos_success_contract_is_valid_and_bound_to_private_commitment():
    contract = _json("success-contract.json")
    ok, problems = validate_success_contract(contract)
    assert ok, problems
    receipt = _json("scenario-freeze-receipt.json")
    assert contract_sha256(contract) == receipt["canonical_success_contract_sha256"]

    evaluator = _json("evaluator-commitment.json")
    private_commitment = evaluator["private_package_sha256"]
    assert private_commitment == receipt["private_evaluator"]["commitment"]
    assert {lane["fixture_sha256"] for lane in contract["oracle_lanes"]} == {private_commitment}
    assert all(lane["hidden_from_builder"] for lane in contract["oracle_lanes"])
    assert evaluator["calibration"]["false_passes"] == 0
    assert evaluator["calibration"]["known_bad_rejected"] == evaluator["calibration"]["known_bad_total"]


def test_caseos_derived_machine_policy_is_valid_and_bound_without_mutating_freeze():
    readiness = json.loads(
        (DERIVED_ROOT / "caseos-v1-assured-route-readiness.json").read_text(encoding="utf-8"))
    machine_path = REPO_ROOT / readiness["derived_machine_policy"]["path"]
    assert hashlib.sha256(machine_path.read_bytes()).hexdigest() == readiness["derived_machine_policy"]["sha256"]
    assert readiness["source_frozen_policy"]["sha256"] == _json("scenario-freeze-receipt.json")["artifacts"][
        "evals/cross_driver_assurance/scenarios/caseos-v1/research-policy.json"]
    policy = json.loads(machine_path.read_text(encoding="utf-8"))
    ok, problems = validate_sufficiency_policy(policy)
    assert ok, problems
    assert readiness["frozen_scenario_files_modified"] is False
    assert readiness["derivation_status"] == "MACHINE_POLICY_VALID_DRIVER_EXPOSURE_BLOCKED"
