import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_ROOT = (
    REPO_ROOT
    / "evals"
    / "cross_driver_assurance"
    / "contracts"
    / "production-behavior-v2"
)


def test_production_behavior_v2_freeze_receipt_matches_artifacts():
    receipt = json.loads((CONTRACT_ROOT / "freeze-receipt.json").read_text(encoding="utf-8"))
    assert receipt["contract_id"] == "cortex-production-behavior-v2"
    assert receipt["scenario_status"] == "not_yet_instantiated"
    assert receipt["change_control"]["changes_require_new_contract_version"] is True

    for relative_path, expected in receipt["artifacts"].items():
        artifact = REPO_ROOT / relative_path
        assert artifact.is_file(), f"frozen artifact is missing: {relative_path}"
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert actual == expected, f"frozen artifact changed: {relative_path}"


def test_production_behavior_v2_baseline_is_non_self_certifying():
    baseline = json.loads((CONTRACT_ROOT / "success-conditions.json").read_text(encoding="utf-8"))
    assert baseline["verdict_policy"]["builder_may_certify"] is False
    assert baseline["verdict_policy"]["procedure_implies_correctness"] is False
    assert baseline["verdict_policy"]["source_count_implies_sufficiency"] is False
    assert baseline["verdict_policy"]["known_bad_false_pass_budget"] == 0
    assert baseline["change_control"]["post_result_reinterpretation_forbidden"] is True

