from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cortex_core import assurance_evaluator as ae
from cortex_core.assurance_contracts import contract_sha256
from cortex_core.assurance_evaluator import (
    PAYLOAD_FIELDS,
    RECEIPT_FIELDS,
    canonical_json,
    load_assurance_receipt,
    store_external_evaluation,
    validate_assurance_receipt,
    validate_external_evaluation,
)
from cortex_core.assurance_result import finalize_assurance_result


NOW = "2026-07-15T12:00:00+00:00"


def execution_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_id": "exec-caseos-v1",
        "task_id": "caseos-001",
        "profile": "product_build",
        "runtime_mode": "governed",
        "driver": {
            "name": "hermes",
            "runtime_version": "1.0",
            "model_id": "qualified-driver",
            "wrapper_version": "2.0",
        },
        "required_phases": ["INTAKE", "RESEARCH", "SPEC", "IMPLEMENT", "VERIFY"],
        "required_tools": ["cortex_run_start", "cortex_run_step"],
        "enabled_tools": ["cortex_run_start", "cortex_run_step", "cortex_run_state"],
        "research_policy": {"required": True, "evidence_types": ["source"]},
        "mission_policy": {
            "required": True, "claims_disjoint": True, "merge_receipt_required": True,
        },
        "fallback_behavior": "fail_closed",
        "evidence_requirements": ["state_receipts", "artifact_hashes", "external_replay"],
        "telemetry_policy": {
            "required": True,
            "sinks": ["otel", "langfuse"],
            "correlation_field": "run_id",
            "missing_evidence_result": "UNRESOLVED",
        },
    }


def success_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_id": "success-caseos-v1",
        "task_id": "caseos-001",
        "desired_user_outcomes": ["usable legal workflow prototype"],
        "observable_behaviors": ["matter intake persists"],
        "domain_invariants": ["no binding legal advice claim"],
        "acceptance_criteria": ["hidden evaluator passes"],
        "prohibited_behaviors": ["fabricated jurisdiction support"],
        "oracle_lanes": [{
            "oracle_id": "caseos-hidden-v1", "authority": "external_observer",
            "scope": "end-to-end behavior", "fixture_sha256": "a" * 64,
            "hidden_from_builder": True,
        }],
        "evaluator_independence": {
            "builder_may_certify": False,
            "cross_driver_required": True,
            "external_replay_required": True,
        },
        "repeatability": {"minimum_distinct_drivers": 2, "clean_workspace_required": True},
        "human_review_boundaries": ["attorney verifies jurisdiction-specific claims"],
    }


@pytest.fixture()
def signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )
    root = {
        "schema_version": 1,
        "keys": {
            "external-key": {
                "public_key": base64.b64encode(public).decode(),
                "issuer_id": "private-evaluator-service",
                "evaluator_id": "caseos-hidden-evaluator",
                "evaluator_family": "independent-python-harness",
                "allowed_kinds": ["ASSURANCE_RESULT"],
                "not_before": "2026-01-01T00:00:00+00:00",
                "not_after": "2027-01-01T00:00:00+00:00",
                "revoked": False,
            }
        },
    }
    path = tmp_path / "assurance-trust-root.json"
    path.write_text(json.dumps(root), encoding="utf-8")
    monkeypatch.setenv("CORTEX_ASSURANCE_TRUST_ROOT", str(path))
    monkeypatch.setenv("CORTEX_ASSURANCE_DB_PATH", str(tmp_path / "operator" / "assurance.db"))
    monkeypatch.setattr(ae, "_utcnow", lambda: datetime.fromisoformat(NOW))
    return private


def result(execution: dict, success: dict) -> dict:
    return finalize_assurance_result({
        "schema_version": 1,
        "run_id": "run-caseos-1",
        "execution_contract_sha256": contract_sha256(execution),
        "success_contract_sha256": contract_sha256(success),
        "artifact_hashes": {"app.tar": "b" * 64},
        "evidence_refs": ["replay:caseos-replay-1", "browser:flow-1"],
        "axis_verdicts": {
            "procedure": "PASS", "behavior": "PASS", "evidence": "PASS",
            "independence": "PASS", "repeatability": "PASS", "human_acceptance": "PASS",
        },
        "unresolved": [],
    })


def payload() -> dict:
    execution, success = execution_contract(), success_contract()
    return {
        "schema_version": 1,
        "evaluation_id": "evaluation-caseos-1",
        "task_id": "caseos-001",
        "run_id": "run-caseos-1",
        "issuer_id": "private-evaluator-service",
        "evaluator_id": "caseos-hidden-evaluator",
        "evaluator_family": "independent-python-harness",
        "execution_contract_sha256": contract_sha256(execution),
        "success_contract_sha256": contract_sha256(success),
        "result": result(execution, success),
        "evidence_manifest_sha256": "c" * 64,
        "evidence_refs": ["private:manifest-1", "replay:caseos-replay-1"],
        "replay_id": "caseos-replay-1",
        "evaluated_at": "2026-07-15T11:00:00+00:00",
        "expires_at": "2026-07-20T00:00:00+00:00",
    }


def sign(private: Ed25519PrivateKey, value: dict) -> dict:
    canonical = canonical_json(value).encode("utf-8")
    return {
        "key_id": "external-key",
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature": base64.b64encode(private.sign(canonical)).decode(),
    }


def store(tmp_path: Path, private: Ed25519PrivateKey, value: dict | None = None) -> dict:
    value = value or payload()
    return store_external_evaluation(
        value, sign(private, value), execution_contract=execution_contract(),
        success_contract=success_contract(), workspace=tmp_path,
    )


def test_signed_external_result_mints_bound_append_only_receipt(tmp_path: Path, signing) -> None:
    receipt = store(tmp_path, signing)
    assert receipt["receipt_id"].startswith("ar_")
    assert receipt["overall_verdict"] == "PASS"
    assert receipt["run_id"] == "run-caseos-1"
    loaded = validate_assurance_receipt(
        receipt["receipt_id"], expected_task_id="caseos-001",
        expected_run_id="run-caseos-1",
        expected_execution_contract_sha256=contract_sha256(execution_contract()),
        expected_success_contract_sha256=contract_sha256(success_contract()),
        workspace=tmp_path,
    )
    assert loaded == receipt


def test_result_must_be_finalized_and_strict() -> None:
    value = payload()
    del value["result"]["overall_verdict"]
    ok, problems = validate_external_evaluation(value)
    assert not ok
    assert any("finalized" in problem for problem in problems)
    value = payload()
    value["surprise"] = True
    assert validate_external_evaluation(value)[0] is False


def test_published_schemas_match_strict_top_level_contracts() -> None:
    root = Path(__file__).resolve().parents[1] / "schemas" / "assurance"
    evaluation = json.loads((root / "external-evaluation.schema.json").read_text(encoding="utf-8"))
    receipt = json.loads((root / "assurance-receipt.schema.json").read_text(encoding="utf-8"))
    trust = json.loads((root / "evaluator-trust-root.schema.json").read_text(encoding="utf-8"))
    assert set(evaluation["required"]) == PAYLOAD_FIELDS
    assert set(receipt["required"]) == RECEIPT_FIELDS
    assert evaluation["additionalProperties"] is False
    assert receipt["additionalProperties"] is False
    assert trust["additionalProperties"] is False


def test_wrong_signature_and_self_declared_identity_fail(tmp_path: Path, signing) -> None:
    value = payload()
    value["result"]["artifact_hashes"]["app.tar"] = "d" * 64
    with pytest.raises(ValueError, match="signature"):
        store_external_evaluation(
            value, sign(signing, payload()), execution_contract=execution_contract(),
            success_contract=success_contract(), workspace=tmp_path,
        )
    value = payload()
    value["evaluator_id"] = "some-other-evaluator"
    with pytest.raises(ValueError, match="trusted key identity"):
        store_external_evaluation(
            value, sign(signing, value), execution_contract=execution_contract(),
            success_contract=success_contract(), workspace=tmp_path,
        )


def test_builder_cannot_sign_its_own_result(tmp_path: Path, signing) -> None:
    value = payload()
    value["evaluator_id"] = "hermes"
    root_path = Path(os.environ["CORTEX_ASSURANCE_TRUST_ROOT"])
    root = json.loads(root_path.read_text(encoding="utf-8"))
    root["keys"]["external-key"]["evaluator_id"] = "hermes"
    root_path.write_text(json.dumps(root), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot certify its own"):
        store_external_evaluation(
            value, sign(signing, value), execution_contract=execution_contract(),
            success_contract=success_contract(), workspace=tmp_path,
        )


def test_missing_or_same_replay_is_rejected(tmp_path: Path, signing) -> None:
    for replay in (None, "run-caseos-1"):
        value = payload()
        value["replay_id"] = replay
        with pytest.raises(ValueError, match="replay"):
            store_external_evaluation(
                value, sign(signing, value), execution_contract=execution_contract(),
                success_contract=success_contract(), workspace=tmp_path,
            )


def test_contract_run_and_expiry_bindings_fail_closed(tmp_path: Path, signing) -> None:
    value = payload()
    value["task_id"] = "other-task"
    with pytest.raises(ValueError, match="task_id"):
        store_external_evaluation(
            value, sign(signing, value), execution_contract=execution_contract(),
            success_contract=success_contract(), workspace=tmp_path,
        )
    value = payload()
    value["expires_at"] = "2026-07-15T11:30:00+00:00"
    with pytest.raises(ValueError, match="expired"):
        store_external_evaluation(
            value, sign(signing, value), execution_contract=execution_contract(),
            success_contract=success_contract(), workspace=tmp_path,
        )


def test_database_tampering_is_detected_on_every_read(tmp_path: Path, signing) -> None:
    receipt = store(tmp_path, signing)
    db_path = Path(os.environ["CORTEX_ASSURANCE_DB_PATH"])
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT canonical_json FROM assurance_receipt WHERE receipt_id=?",
            (receipt["receipt_id"],),
        ).fetchone()
        changed = json.loads(row[0])
        changed["overall_verdict"] = "FAIL"
        db.execute(
            "UPDATE assurance_receipt SET canonical_json=? WHERE receipt_id=?",
            (canonical_json(changed), receipt["receipt_id"]),
        )
    with pytest.raises(ValueError, match="digest mismatch"):
        load_assurance_receipt(receipt["receipt_id"], workspace=tmp_path)


def test_duplicate_evaluation_identity_with_changed_bytes_is_rejected(tmp_path: Path, signing) -> None:
    store(tmp_path, signing)
    changed = deepcopy(payload())
    changed["evidence_refs"].append("private:extra")
    with pytest.raises(ValueError, match="immutable external_evaluation"):
        store(tmp_path, signing, changed)


def test_identical_retry_is_idempotent_at_a_later_time(
    tmp_path: Path, signing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = store(tmp_path, signing)
    monkeypatch.setattr(ae, "_utcnow", lambda: datetime.fromisoformat(
        "2026-07-16T12:00:00+00:00"))
    second = store_external_evaluation(
        payload(), sign(signing, payload()), execution_contract=execution_contract(),
        success_contract=success_contract(), workspace=tmp_path,
    )
    assert second == first


def test_expired_receipt_cannot_be_loaded_as_valid(
    tmp_path: Path, signing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = store(tmp_path, signing)
    monkeypatch.setattr(ae, "_utcnow", lambda: datetime.fromisoformat(
        "2026-07-21T00:00:00+00:00"))
    with pytest.raises(ValueError, match="expired"):
        load_assurance_receipt(receipt["receipt_id"], workspace=tmp_path)


def test_revoked_or_expired_trust_key_is_rejected(tmp_path: Path, signing) -> None:
    root_path = Path(os.environ["CORTEX_ASSURANCE_TRUST_ROOT"])
    for field, value, message in (
        ("revoked", True, "revoked"),
        ("not_after", "2026-07-15T12:00:00+00:00", "expired"),
    ):
        root = json.loads(root_path.read_text(encoding="utf-8"))
        root["keys"]["external-key"][field] = value
        root_path.write_text(json.dumps(root), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            store(tmp_path, signing)
        if field == "revoked":
            root["keys"]["external-key"][field] = False
            root_path.write_text(json.dumps(root), encoding="utf-8")


def test_operator_database_path_is_required(
    tmp_path: Path, signing, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORTEX_ASSURANCE_DB_PATH")
    with pytest.raises(ValueError, match="CORTEX_ASSURANCE_DB_PATH"):
        store(tmp_path, signing)
    assert not (tmp_path / "ops-local" / "assurance-results.db").exists()
