from __future__ import annotations

from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cortex_core import research_sufficiency as rs


NOW = "2026-07-15T12:00:00+00:00"
_SIGNERS: dict[str, dict[str, tuple[str, Ed25519PrivateKey]]] = {}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "report.md").write_text("# Report\nGrounded finding.\n", encoding="utf-8")
    (tmp_path / "docs" / "source.md").write_text("Primary authority snapshot.\n", encoding="utf-8")
    signers: dict[str, tuple[str, Ed25519PrivateKey]] = {}
    keys = {}
    for issuer, key_id, kinds in (
        ("judge-service", "judge-key", ["POLICY", "SOURCE_AUTHORITY", "SUBSTANTIVE_REVIEW"]),
        ("human-console", "human-key", ["HUMAN_APPROVAL"]),
    ):
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        signers[issuer] = (key_id, private)
        keys[key_id] = {"public_key": base64.b64encode(public).decode(),
                        "issuer_id": issuer, "allowed_kinds": kinds}
    trust_root = tmp_path / "trust-root.json"
    trust_root.write_text(json.dumps({"schema_version": 1, "keys": keys}), encoding="utf-8")
    monkeypatch.setenv("CORTEX_RESEARCH_TRUST_ROOT", str(trust_root))
    _SIGNERS[str(tmp_path)] = signers
    return tmp_path


def _trust(ws: Path, payload: dict, kind: str) -> dict:
    issuer = payload.get("issuer_id") if kind != "POLICY" else "judge-service"
    key_id, private = _SIGNERS[str(ws)][issuer]
    canonical = rs.canonical_json(payload).encode("utf-8")
    return {"key_id": key_id, "payload_sha256": hashlib.sha256(canonical).hexdigest(),
            "signature": base64.b64encode(private.sign(canonical)).decode()}


def _store_policy(ws: Path, policy: dict) -> dict:
    return rs.store_policy(policy, _trust(ws, policy, "POLICY"), workspace=ws)


def _store_source(ws: Path) -> dict:
    source = {
        "schema_version": 1, "source_id": "authority-example",
        "url_origin": "https://authority.example", "authority_class": "primary",
        "independence_group": "authority.example", "issuer_id": "judge-service",
        "valid_from": "2026-01-01T00:00:00+00:00",
        "expires_at": "2027-01-01T00:00:00+00:00",
    }
    return rs.store_source_authority(source, _trust(ws, source, "SOURCE_AUTHORITY"),
                                     workspace=ws)


def _store_attestation(ws: Path, attestation: dict) -> dict:
    return rs.store_attestation(attestation, _trust(ws, attestation, attestation["kind"]),
                                workspace=ws)


def _policy() -> dict:
    policy = {
        "schema_version": 1,
        "policy_id": "policy-1",
        "task_id": "task-1",
        "risk_tier": "high",
        "valid_from": "2026-07-01T00:00:00+00:00",
        "expires_at": "2026-08-01T00:00:00+00:00",
        "lanes": [{
            "lane_id": "law",
            "required_question_ids": ["q1"],
            "required_authority_classes": ["primary"],
            "min_independence_groups": 1,
            "max_age_days": 30,
            "snapshot_required": True,
        }],
        "claim_rules": [{
            "claim_kind": "legal_rule",
            "required_authority_classes": ["primary"],
            "min_independence_groups": 1,
            "max_age_days": 30,
        }],
        "conflict_policy": {"blocking_severities": ["high"]},
        "review_policy": {
            "required_from_risk": "medium",
            "trusted_issuer_ids": ["judge-service"],
            "required_scopes": ["research"],
        },
        "human_requirements": [{
            "scope_id": "legal-signoff",
            "required_from_risk": "high",
            "accepted_roles": ["attorney"],
            "trusted_issuer_ids": ["human-console"],
        }],
        "invalidation_triggers": ["law changes"],
    }
    return policy


def _proposal(ws: Path, policy: dict) -> dict:
    return {
        "schema_version": 1,
        "proposal_id": "proposal-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "research_task_id": "research-1",
        "decision": {
            "decision_id": "decision-1",
            "statement": "Use the jurisdiction-neutral workflow pattern.",
            "scope": "prototype architecture",
            "downstream_work": ["app-build"],
            "risk_tier": "high",
            "as_of": "2026-07-15T00:00:00+00:00",
            "expires_at": "2026-07-30T00:00:00+00:00",
        },
        "policy_id": policy["policy_id"],
        "policy_sha256": rs.digest_json(policy),
        "proposer": {"id": "builder-1", "family": "builder-family"},
        "report": {"path": "docs/report.md", "sha256": _sha(ws / "docs" / "report.md")},
        "questions": [{
            "question_id": "q1", "lane_id": "law", "status": "ANSWERED",
            "claim_ids": ["c1"],
        }],
        "claims": [{
            "claim_id": "c1", "lane_id": "law", "claim_kind": "legal_rule",
            "statement": "The prototype must not claim jurisdiction-specific correctness.",
            "evidence_ids": ["e1"],
        }],
        "evidence": [{
            "evidence_id": "e1",
            "source_url": "https://authority.example/rule",
            "local_path": "docs/source.md",
            "content_sha256": _sha(ws / "docs" / "source.md"),
            "captured_at": "2026-07-10T00:00:00+00:00",
            "source_version": "2026-07-10",
            "authority_class": "primary",
            "independence_group": "authority.example",
        }],
        "conflicts": [],
        "remaining_uncertainties": [],
        "rejected_evidence": [],
        "invalidation_triggers": ["jurisdiction selected"],
    }


def _attestation(proposal_sha: str, policy_sha: str, *, kind: str, attestation_id: str,
                 verdict: str, family: str, issuer: str, role: str, scopes: list[str],
                 issued_at: str = "2026-07-15T01:00:00+00:00") -> dict:
    authority_type = "external_evaluator" if kind == "SUBSTANTIVE_REVIEW" else "human"
    return {
        "schema_version": 1,
        "attestation_id": attestation_id,
        "kind": kind,
        "proposal_sha256": proposal_sha,
        "policy_sha256": policy_sha,
        "decision_id": "decision-1",
        "authority": {
            "authority_id": f"authority-{attestation_id}",
            "authority_family": family,
            "authority_type": authority_type,
            "role": role,
            "credential_ref": "trusted-local-channel",
        },
        "issuer_id": issuer,
        "scope_ids": scopes,
        "verdict": verdict,
        "reason": "independently checked",
        "evidence_refs": ["e1"],
        "issued_at": issued_at,
        "expires_at": "2026-07-25T00:00:00+00:00",
    }


def _freeze(ws: Path) -> tuple[dict, dict, dict]:
    policy = _policy()
    proposal = _proposal(ws, policy)
    _store_policy(ws, policy)
    _store_source(ws)
    frozen = rs.freeze_proposal(proposal, policy, workspace=ws)
    return policy, proposal, frozen


def test_canonical_digest_is_order_independent_and_rejects_nan() -> None:
    assert rs.digest_json({"b": 2, "a": 1}) == rs.digest_json({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        rs.digest_json({"bad": float("nan")})


def test_published_schemas_match_strict_top_level_contracts() -> None:
    root = Path(__file__).resolve().parents[1] / "schemas" / "research"
    policy_schema = json.loads((root / "sufficiency-policy.schema.json").read_text(encoding="utf-8"))
    proposal_schema = json.loads((root / "sufficiency-proposal.schema.json").read_text(encoding="utf-8"))
    attestation_schema = json.loads((root / "sufficiency-attestation.schema.json").read_text(encoding="utf-8"))
    receipt_schema = json.loads((root / "sufficiency-receipt.schema.json").read_text(encoding="utf-8"))
    source_schema = json.loads((root / "source-authority.schema.json").read_text(encoding="utf-8"))
    assert set(policy_schema["required"]) == rs.POLICY_FIELDS
    assert set(proposal_schema["required"]) == rs.PROPOSAL_FIELDS
    assert set(attestation_schema["required"]) == rs.ATTESTATION_FIELDS
    assert set(source_schema["required"]) == rs.SOURCE_AUTHORITY_FIELDS
    assert "research_task_id" in receipt_schema["required"]
    assert "run_id" in receipt_schema["required"]
    assert all(schema["additionalProperties"] is False for schema in (
        policy_schema, proposal_schema, attestation_schema, receipt_schema,
    ))


def test_strict_validation_rejects_unknown_and_dangling_references(ws: Path) -> None:
    policy = _policy()
    proposal = _proposal(ws, policy)
    proposal["surprise"] = True
    ok, problems = rs.validate_sufficiency_proposal(proposal)
    assert not ok and "proposal unknown field surprise" in problems
    proposal.pop("surprise")
    proposal["claims"][0]["evidence_ids"] = ["missing"]
    ok, problems = rs.validate_sufficiency_proposal(proposal)
    assert not ok and any("unknown evidence missing" in item for item in problems)


def test_freeze_refuses_hash_mismatch_and_workspace_escape(ws: Path, tmp_path: Path) -> None:
    policy = _policy()
    _store_policy(ws, policy)
    _store_source(ws)
    proposal = _proposal(ws, policy)
    proposal["report"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="report snapshot hash mismatch"):
        rs.freeze_proposal(proposal, policy, workspace=ws)

    outside = tmp_path.parent / "outside-sufficiency.txt"
    outside.write_text("outside", encoding="utf-8")
    proposal = _proposal(ws, policy)
    proposal["report"] = {"path": str(outside), "sha256": _sha(outside)}
    with pytest.raises(ValueError):
        rs.freeze_proposal(proposal, policy, workspace=ws)


def test_builder_cannot_supply_its_own_unregistered_policy(ws: Path) -> None:
    policy = _policy()
    proposal = _proposal(ws, policy)
    with pytest.raises(ValueError, match="not registered on the trusted policy surface"):
        rs.freeze_proposal(proposal, policy, workspace=ws)


def test_driver_cannot_self_declare_source_authority_or_independence(ws: Path) -> None:
    policy = _policy()
    _store_policy(ws, policy)
    _store_source(ws)
    proposal = _proposal(ws, policy)
    proposal["evidence"][0]["independence_group"] = "invented-independent-owner"
    with pytest.raises(ValueError, match="not backed by a current trusted source record"):
        rs.freeze_proposal(proposal, policy, workspace=ws)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p["questions"][0].update(status="UNRESOLVED"), "question q1 is unresolved"),
        (lambda p: p["evidence"][0].update(authority_class="blog"), "lacks authority"),
        (lambda p: p["evidence"][0].update(captured_at="2025-01-01T00:00:00+00:00"), "freshness"),
        (lambda p: p["conflicts"].append({
            "conflict_id": "x", "severity": "high", "status": "UNRESOLVED",
            "evidence_ids": ["e1"], "resolution": "pending", "resolution_evidence_ids": [],
        }), "blocking conflict unresolved"),
        (lambda p: p["remaining_uncertainties"].append({
            "uncertainty_id": "u1", "severity": "high", "disposition": "BLOCK",
            "required_approval_scope": "legal-signoff",
        }), "blocking uncertainty"),
    ],
)
def test_mechanical_assessment_fails_closed(ws: Path, mutate, expected: str) -> None:
    policy = _policy()
    proposal = _proposal(ws, policy)
    mutate(proposal)
    frozen = {"proposal_sha256": rs.digest_json(proposal),
              "policy_sha256": rs.digest_json(policy), "proposal": proposal}
    result = rs.assess_mechanical(frozen, policy, assessed_at=NOW)
    assert result["outcome"] == "UNRESOLVED"
    assert any(expected in gap for gap in result["gaps"])


def test_expired_policy_and_future_decision_are_unresolved(ws: Path) -> None:
    policy = _policy()
    proposal = _proposal(ws, policy)
    policy["expires_at"] = "2026-07-14T00:00:00+00:00"
    proposal["policy_sha256"] = rs.digest_json(policy)
    proposal["decision"]["as_of"] = "2026-07-16T00:00:00+00:00"
    frozen = {"proposal_sha256": rs.digest_json(proposal),
              "policy_sha256": rs.digest_json(policy), "proposal": proposal}
    result = rs.assess_mechanical(frozen, policy, assessed_at=NOW)
    assert "policy is not currently valid" in result["gaps"]
    assert "decision as_of is in the future" in result["gaps"]


def test_missing_required_human_yields_abstain(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    review = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                          kind="SUBSTANTIVE_REVIEW", attestation_id="review-1",
                          verdict="ADEQUATE", family="independent-family",
                          issuer="judge-service", role="reviewer", scopes=["research"])
    _store_attestation(ws, review)
    receipt = rs.finalize_sufficiency(frozen["proposal_sha256"], policy, ["review-1"],
                                      workspace=ws, assessed_at=NOW)
    assert receipt["outcome"] == "ABSTAIN"
    assert receipt["unlocked_work"] == []


def test_independent_review_and_qualified_human_unlock_decision(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    review = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                          kind="SUBSTANTIVE_REVIEW", attestation_id="review-1",
                          verdict="ADEQUATE", family="independent-family",
                          issuer="judge-service", role="reviewer", scopes=["research"])
    human = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                         kind="HUMAN_APPROVAL", attestation_id="human-1",
                         verdict="ACCEPT", family="human-family", issuer="human-console",
                         role="attorney", scopes=["legal-signoff"])
    _store_attestation(ws, review)
    _store_attestation(ws, human)
    receipt = rs.finalize_sufficiency(
        frozen["proposal_sha256"], policy, ["review-1", "human-1"],
        workspace=ws, assessed_at=NOW,
    )
    assert receipt["outcome"] == "SUFFICIENT_FOR_DECISION"
    assert receipt["unlocked_work"] == ["app-build"]
    valid = rs.validate_sufficiency_receipt(
        receipt["receipt_id"], expected_task_id="task-1", expected_decision_id="decision-1",
        expected_policy_sha256=frozen["policy_sha256"], workspace=ws, now=NOW,
    )
    assert valid["valid"] is True and valid["outcome"] == "SUFFICIENT_FOR_DECISION"


def test_same_family_future_or_inadequate_reviewer_cannot_be_masked(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    good = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                        kind="SUBSTANTIVE_REVIEW", attestation_id="good", verdict="ADEQUATE",
                        family="independent", issuer="judge-service", role="reviewer",
                        scopes=["research"])
    bad = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                       kind="SUBSTANTIVE_REVIEW", attestation_id="bad", verdict="INADEQUATE",
                       family="independent-2", issuer="judge-service", role="reviewer",
                       scopes=["research"])
    human = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                         kind="HUMAN_APPROVAL", attestation_id="human", verdict="ACCEPT",
                         family="human", issuer="human-console", role="attorney",
                         scopes=["legal-signoff"])
    for item in (good, bad, human):
        _store_attestation(ws, item)
    result = rs.finalize_sufficiency(frozen["proposal_sha256"], policy,
                                     ["good", "bad", "human"], workspace=ws,
                                     assessed_at=NOW)
    assert result["outcome"] == "UNRESOLVED"

    same_family = deepcopy(good)
    same_family["attestation_id"] = "same-family"
    same_family["authority"]["authority_family"] = "builder-family"
    _store_attestation(ws, same_family)
    result = rs.finalize_sufficiency(frozen["proposal_sha256"], policy,
                                     ["same-family", "human"], workspace=ws,
                                     assessed_at=NOW)
    assert result["outcome"] == "UNRESOLVED"

    future = deepcopy(good)
    future["attestation_id"] = "future"
    future["issued_at"] = "2026-07-16T00:00:00+00:00"
    _store_attestation(ws, future)
    result = rs.finalize_sufficiency(frozen["proposal_sha256"], policy,
                                     ["future", "human"], workspace=ws,
                                     assessed_at=NOW)
    assert result["outcome"] == "UNRESOLVED"


def test_attestation_identity_is_immutable(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    item = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                        kind="SUBSTANTIVE_REVIEW", attestation_id="review-1",
                        verdict="ADEQUATE", family="independent", issuer="judge-service",
                        role="reviewer", scopes=["research"])
    _store_attestation(ws, item)
    changed = deepcopy(item)
    changed["reason"] = "changed after issuance"
    with pytest.raises(ValueError, match="immutable attestation identity collision"):
        _store_attestation(ws, changed)


def test_forged_or_wrong_issuer_attestation_is_rejected_before_storage(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    item = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                        kind="SUBSTANTIVE_REVIEW", attestation_id="review-forged",
                        verdict="ADEQUATE", family="independent", issuer="judge-service",
                        role="reviewer", scopes=["research"])
    envelope = _trust(ws, item, item["kind"])
    envelope["signature"] = base64.b64encode(b"0" * 64).decode()
    with pytest.raises(ValueError, match="signature is invalid"):
        rs.store_attestation(item, envelope, workspace=ws)

    wrong = deepcopy(item)
    wrong["issuer_id"] = "human-console"
    key_id, private = _SIGNERS[str(ws)]["judge-service"]
    canonical = rs.canonical_json(wrong).encode()
    wrong_envelope = {"key_id": key_id, "payload_sha256": hashlib.sha256(canonical).hexdigest(),
                      "signature": base64.b64encode(private.sign(canonical)).decode()}
    with pytest.raises(ValueError, match="issuer_id does not match"):
        rs.store_attestation(wrong, wrong_envelope, workspace=ws)


def test_receipt_binding_and_expiry_are_enforced(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    review = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                          kind="SUBSTANTIVE_REVIEW", attestation_id="review-1",
                          verdict="ADEQUATE", family="independent", issuer="judge-service",
                          role="reviewer", scopes=["research"])
    _store_attestation(ws, review)
    receipt = rs.finalize_sufficiency(frozen["proposal_sha256"], policy, ["review-1"],
                                      workspace=ws, assessed_at=NOW)
    wrong = rs.validate_sufficiency_receipt(
        receipt["receipt_id"], expected_task_id="different", expected_decision_id="decision-1",
        expected_policy_sha256=frozen["policy_sha256"], workspace=ws, now=NOW,
    )
    assert wrong == {"valid": False, "reason": "task mismatch"}
    expired = rs.validate_sufficiency_receipt(
        receipt["receipt_id"], expected_task_id="task-1", expected_decision_id="decision-1",
        expected_policy_sha256=frozen["policy_sha256"], workspace=ws,
        now="2026-08-02T00:00:00+00:00",
    )
    assert expired["valid"] is False


def test_stored_receipt_tampering_is_detected_on_read(ws: Path) -> None:
    policy, _, frozen = _freeze(ws)
    review = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                          kind="SUBSTANTIVE_REVIEW", attestation_id="review-1",
                          verdict="ADEQUATE", family="independent", issuer="judge-service",
                          role="reviewer", scopes=["research"])
    _store_attestation(ws, review)
    receipt = rs.finalize_sufficiency(frozen["proposal_sha256"], policy, ["review-1"],
                                      workspace=ws, assessed_at=NOW)
    db = ws / "ops-local" / "research-sufficiency.db"
    with sqlite3.connect(db) as conn:
        forged = dict(receipt)
        forged["outcome"] = "SUFFICIENT_FOR_DECISION"
        forged["unlocked_work"] = ["forged-work"]
        conn.execute("UPDATE receipt SET canonical_json=? WHERE receipt_id=?",
                     (rs.canonical_json(forged), receipt["receipt_id"]))
    with pytest.raises(ValueError, match="stored receipt digest mismatch"):
        rs.lookup_sufficiency_receipt(receipt["receipt_id"], ws)


def test_explicit_human_rejection_dominates_missing_scope_regardless_of_order(ws: Path) -> None:
    policy = _policy()
    policy["human_requirements"].insert(0, {
        "scope_id": "first-missing", "required_from_risk": "high",
        "accepted_roles": ["attorney"], "trusted_issuer_ids": ["human-console"],
    })
    proposal = _proposal(ws, policy)
    _store_policy(ws, policy)
    _store_source(ws)
    frozen = rs.freeze_proposal(proposal, policy, workspace=ws)
    review = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                          kind="SUBSTANTIVE_REVIEW", attestation_id="review-1",
                          verdict="ADEQUATE", family="independent", issuer="judge-service",
                          role="reviewer", scopes=["research"])
    rejection = _attestation(frozen["proposal_sha256"], frozen["policy_sha256"],
                             kind="HUMAN_APPROVAL", attestation_id="reject-1",
                             verdict="REJECT", family="human", issuer="human-console",
                             role="attorney", scopes=["legal-signoff"])
    _store_attestation(ws, review)
    _store_attestation(ws, rejection)
    receipt = rs.finalize_sufficiency(frozen["proposal_sha256"], policy,
                                      ["review-1", "reject-1"], workspace=ws,
                                      assessed_at=NOW)
    assert receipt["outcome"] == "UNRESOLVED"
    assert "human scope rejected: legal-signoff" in receipt["remaining_uncertainty"]
