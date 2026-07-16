from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cortex_core import research_sufficiency as rs
from cortex_core import state_engine as se

_SIGNERS: dict[str, dict[str, tuple[str, Ed25519PrivateKey]]] = {}


def _iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "report.md").write_text("# grounded report\n", encoding="utf-8")
    (tmp_path / "docs" / "source.md").write_text("primary source\n", encoding="utf-8")
    return tmp_path


def _configure_trust(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    signers = {}
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
    path = ws / "trust-root.json"
    path.write_text(json.dumps({"schema_version": 1, "keys": keys}), encoding="utf-8")
    monkeypatch.setenv("CORTEX_RESEARCH_TRUST_ROOT", str(path))
    _SIGNERS[str(ws)] = signers


def _trust(ws: Path, payload: dict, kind: str) -> dict:
    issuer = payload.get("issuer_id") if kind != "POLICY" else "judge-service"
    key_id, private = _SIGNERS[str(ws)][issuer]
    canonical = rs.canonical_json(payload).encode()
    return {"key_id": key_id, "payload_sha256": hashlib.sha256(canonical).hexdigest(),
            "signature": base64.b64encode(private.sign(canonical)).decode()}


def _store_policy(ws: Path, policy: dict) -> None:
    rs.store_policy(policy, _trust(ws, policy, "POLICY"), workspace=ws)


def _store_source(ws: Path) -> None:
    source = {
        "schema_version": 1, "source_id": "primary-example",
        "url_origin": "https://primary.example", "authority_class": "primary",
        "independence_group": "primary.example", "issuer_id": "judge-service",
        "valid_from": _iso(timedelta(days=-10)), "expires_at": _iso(timedelta(days=30)),
    }
    rs.store_source_authority(source, _trust(ws, source, "SOURCE_AUTHORITY"), workspace=ws)


def _store_attestation(ws: Path, attestation: dict) -> None:
    rs.store_attestation(attestation, _trust(ws, attestation, attestation["kind"]), workspace=ws)


def _policy(task_id: str, *, high: bool = False) -> dict:
    return {
        "schema_version": 1, "policy_id": f"policy-{task_id}", "task_id": task_id,
        "risk_tier": "high" if high else "low",
        "valid_from": _iso(timedelta(days=-1)), "expires_at": _iso(timedelta(days=20)),
        "lanes": [{
            "lane_id": "product", "required_question_ids": ["q1"],
            "required_authority_classes": ["primary"], "min_independence_groups": 1,
            "max_age_days": 30, "snapshot_required": True,
        }],
        "claim_rules": [],
        "conflict_policy": {"blocking_severities": ["high"]},
        "review_policy": {
            "required_from_risk": "medium", "trusted_issuer_ids": ["judge-service"],
            "required_scopes": ["research"],
        },
        "human_requirements": ([{
            "scope_id": "professional-signoff", "required_from_risk": "high",
            "accepted_roles": ["professional"], "trusted_issuer_ids": ["human-console"],
        }] if high else []),
        "invalidation_triggers": ["scope changes"],
    }


def _proposal(ws: Path, policy: dict, research_task_id: str, run_id: str) -> dict:
    return {
        "schema_version": 1, "proposal_id": f"proposal-{research_task_id}", "run_id": run_id,
        "task_id": policy["task_id"], "research_task_id": research_task_id,
        "decision": {
            "decision_id": "decision-1", "statement": "Use the researched pattern.",
            "scope": "implementation plan", "downstream_work": ["PLAN"],
            "risk_tier": policy["risk_tier"], "as_of": _iso(timedelta(hours=-1)),
            "expires_at": _iso(timedelta(days=10)),
        },
        "policy_id": policy["policy_id"], "policy_sha256": rs.digest_json(policy),
        "proposer": {"id": "builder", "family": "builder-family"},
        "report": {"path": "docs/report.md", "sha256": _sha(ws / "docs" / "report.md")},
        "questions": [{"question_id": "q1", "lane_id": "product", "status": "ANSWERED",
                       "claim_ids": ["c1"]}],
        "claims": [{"claim_id": "c1", "lane_id": "product", "claim_kind": "pattern",
                    "statement": "Pattern is supported.", "evidence_ids": ["e1"]}],
        "evidence": [{
            "evidence_id": "e1", "source_url": "https://primary.example/source",
            "local_path": "docs/source.md", "content_sha256": _sha(ws / "docs" / "source.md"),
            "captured_at": _iso(timedelta(hours=-2)), "source_version": "current",
            "authority_class": "primary", "independence_group": "primary.example",
        }],
        "conflicts": [], "remaining_uncertainties": [], "rejected_evidence": [],
        "invalidation_triggers": ["source changes"],
    }


def _review(proposal_sha: str, policy_sha: str) -> dict:
    return {
        "schema_version": 1, "attestation_id": "review-1", "kind": "SUBSTANTIVE_REVIEW",
        "proposal_sha256": proposal_sha, "policy_sha256": policy_sha,
        "decision_id": "decision-1",
        "authority": {
            "authority_id": "external-judge", "authority_family": "external-family",
            "authority_type": "external_evaluator", "role": "reviewer",
            "credential_ref": "trusted-channel",
        },
        "issuer_id": "judge-service", "scope_ids": ["research"], "verdict": "ADEQUATE",
        "reason": "independent evidence review", "evidence_refs": ["e1"],
        "issued_at": _iso(timedelta(minutes=-5)), "expires_at": _iso(timedelta(days=5)),
    }


def test_assured_build_requires_stored_bound_receipt_before_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _workspace(tmp_path)
    _configure_trust(ws, monkeypatch)
    policy = _policy("task-1")
    _store_policy(ws, policy)
    _store_source(ws)
    intent = {
        "seeking": "build a professional app", "assurance_task_id": "task-1",
        "research_decision_id": "decision-1", "research_policy_sha256": rs.digest_json(policy),
    }
    eng = se.StateEngine(str(ws / "engine.db"), workspace=str(ws))
    try:
        tid = eng.create_task(intent, track="assured_build")
        env = eng.step(tid, "cortex_report_findings", {"evidence": ["local"]}, seq=0)
        env = eng.step(tid, "cortex_report_findings", {"evidence": ["external"]}, seq=env["seq"])
        assert env["state"] == "RESEARCH_DECISION"

        refused = eng.step(tid, "cortex_submit_research_sufficiency",
                           {"receipt_id": "invented", "outcome": "SUFFICIENT_FOR_DECISION"},
                           seq=env["seq"])
        assert refused["gate"]["pass"] is False
        assert refused["state"] == "RESEARCH"

        proposal = _proposal(ws, policy, tid, eng.get(tid)["run_id"])
        frozen = rs.freeze_proposal(proposal, policy, workspace=ws)
        receipt = rs.finalize_sufficiency(frozen["proposal_sha256"], policy, [], workspace=ws,
                                          assessed_at=_iso())
        env = eng.step(tid, "cortex_report_findings", {"evidence": ["closed"]}, seq=refused["seq"])
        assert env["state"] == "RESEARCH_DECISION"
        advanced = eng.step(tid, "cortex_submit_research_sufficiency",
                            {"receipt_id": receipt["receipt_id"]}, seq=env["seq"])
        assert advanced["state"] == "PLAN"
        assert advanced["gate"]["outcome"] == "SUFFICIENT_FOR_DECISION"
    finally:
        eng.close()


def test_assured_research_abstain_is_terminal_and_unlocks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _workspace(tmp_path)
    _configure_trust(ws, monkeypatch)
    policy = _policy("task-high", high=True)
    _store_policy(ws, policy)
    _store_source(ws)
    intent = {
        "seeking": "high-risk domain decision", "assurance_task_id": "task-high",
        "research_decision_id": "decision-1", "research_policy_sha256": rs.digest_json(policy),
    }
    eng = se.StateEngine(str(ws / "engine.db"), workspace=str(ws))
    try:
        tid = eng.create_task(intent, track="assured_research")
        for tool in (
            "cortex_submit_framing", "cortex_submit_seeds", "cortex_submit_fetch_report",
            "cortex_submit_evidence", "cortex_submit_coverage", "cortex_submit_findings",
            "cortex_write_research_report",
        ):
            row = eng.get(tid)
            env = eng.step(tid, tool, {}, seq=row["seq"])
        assert env["state"] == "SUFFICIENCY"

        proposal = _proposal(ws, policy, tid, eng.get(tid)["run_id"])
        frozen = rs.freeze_proposal(proposal, policy, workspace=ws)
        review = _review(frozen["proposal_sha256"], frozen["policy_sha256"])
        _store_attestation(ws, review)
        receipt = rs.finalize_sufficiency(frozen["proposal_sha256"], policy, ["review-1"],
                                          workspace=ws, assessed_at=_iso())
        assert receipt["outcome"] == "ABSTAIN" and receipt["unlocked_work"] == []
        final = eng.step(tid, "cortex_submit_research_sufficiency",
                         {"receipt_id": receipt["receipt_id"]}, seq=env["seq"])
        assert final["state"] == "ABSTAINED"
        assert final["outcome"] == "ABSTAIN"
        assert eng.get(tid)["closeout_written"] is True
    finally:
        eng.close()


def test_assured_bound_gate_cannot_be_removed_by_track_registration() -> None:
    chart = __import__("json").loads(__import__("json").dumps(se.ASSURED_BUILD_TRACK))
    del chart["states"]["RESEARCH_DECISION"]["bound_gate"]
    with pytest.raises(ValueError, match="bound-gate safety spine"):
        se.register_track(chart)
