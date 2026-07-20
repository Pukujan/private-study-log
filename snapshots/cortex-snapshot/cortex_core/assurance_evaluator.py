"""Verify and persist assurance results signed by an external evaluator.

The builder-facing MCP cannot mint these records.  An evaluator signs the complete
evaluation payload with an Ed25519 key whose public identity is pinned in an
operator-owned trust root.  Cortex verifies the signature, contract bindings,
cross-driver independence, and expiry before writing an append-only receipt.

This is a cryptographic process boundary, not an OS sandbox: the trust-root file and
evaluator private key still need to live outside the builder workspace/process and be
protected by the operator or a separate service identity.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex_core.assurance_contracts import (
    contract_sha256,
    validate_execution_contract,
    validate_success_contract,
)
from cortex_core.assurance_result import validate_assurance_result


SCHEMA_VERSION = 1
TRUST_ROOT_ENV = "CORTEX_ASSURANCE_TRUST_ROOT"
ASSURANCE_DB_ENV = "CORTEX_ASSURANCE_DB_PATH"
PAYLOAD_FIELDS = {
    "schema_version", "evaluation_id", "task_id", "run_id", "issuer_id",
    "evaluator_id", "evaluator_family", "execution_contract_sha256",
    "success_contract_sha256", "result", "evidence_manifest_sha256",
    "evidence_refs", "replay_id", "evaluated_at", "expires_at",
}
ENVELOPE_FIELDS = {"key_id", "payload_sha256", "signature"}
KEY_FIELDS = {
    "public_key", "issuer_id", "evaluator_id", "evaluator_family", "allowed_kinds",
    "not_before", "not_after", "revoked",
}
RECEIPT_FIELDS = {
    "schema_version", "receipt_id", "evaluation_id", "task_id", "run_id",
    "issuer_id", "evaluator_id", "evaluator_family", "execution_contract_sha256",
    "success_contract_sha256", "result_sha256", "evidence_manifest_sha256",
    "overall_verdict", "replay_id", "evaluated_at", "expires_at", "key_id",
    "signed_payload_sha256",
}
_SHA256 = set("0123456789abcdef")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256


def _dt(value: Any, field: str) -> datetime:
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


def validate_external_evaluation(payload: Any) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, [f"evaluation must be an object, got {type(payload).__name__}"]
    problems = [f"missing required field {name!r}" for name in PAYLOAD_FIELDS if name not in payload]
    problems.extend(f"unknown field {name!r}" for name in payload if name not in PAYLOAD_FIELDS)
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in (
        "evaluation_id", "task_id", "run_id", "issuer_id", "evaluator_id",
        "evaluator_family", "evaluated_at", "expires_at",
    ):
        if not _nonempty(payload.get(field)):
            problems.append(f"{field} must be a non-empty string")
    replay_id = payload.get("replay_id")
    if replay_id is not None and not _nonempty(replay_id):
        problems.append("replay_id must be null or a non-empty string")
    for field in (
        "execution_contract_sha256", "success_contract_sha256",
        "evidence_manifest_sha256",
    ):
        if not _is_sha(payload.get(field)):
            problems.append(f"{field} must be a sha256 hex digest")
    refs = payload.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not _nonempty(ref) for ref in refs):
        problems.append("evidence_refs must be a non-empty list of non-empty strings")
    result_ok, result_problems = validate_assurance_result(payload.get("result"))
    if not result_ok:
        problems.extend(f"invalid result: {p}" for p in result_problems)
    elif "overall_verdict" not in payload["result"]:
        problems.append("result must be finalized with overall_verdict before signing")
    try:
        if _dt(payload.get("evaluated_at"), "evaluated_at") >= _dt(payload.get("expires_at"), "expires_at"):
            problems.append("expires_at must be later than evaluated_at")
    except ValueError as exc:
        problems.append(str(exc))
    return not problems, problems


def _trust_root() -> dict[str, Any]:
    raw = (os.environ.get(TRUST_ROOT_ENV) or "").strip()
    if not raw:
        raise ValueError(f"{TRUST_ROOT_ENV} is not configured")
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("assurance trust root is not a file")
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"assurance trust root is invalid: {exc}") from exc
    if not isinstance(root, dict) or set(root) != {"schema_version", "keys"}:
        raise ValueError("assurance trust root must contain exactly schema_version and keys")
    if root.get("schema_version") != SCHEMA_VERSION or not isinstance(root.get("keys"), dict):
        raise ValueError("assurance trust root schema is invalid")
    return root


def verify_identity_signature(payload: dict[str, Any], envelope: Any, *, kind: str,
                              issuer_field: str, identity_field: str,
                              family_field: str) -> dict[str, str]:
    """Verify any external assurance-bound payload against the same public trust root.

    Callers validate their payload schema first.  Identity values are then taken from
    the trusted key record, so an observer cannot gain independence merely by changing
    self-declared names in a signed or unsigned JSON object.
    """
    if kind not in {"ASSURANCE_RESULT", "PREFLIGHT_OBSERVATION"}:
        raise ValueError(f"unsupported assurance signature kind: {kind}")
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
        raise ValueError("signature envelope has invalid fields")
    root = _trust_root()
    key_id = envelope.get("key_id")
    key = root["keys"].get(key_id)
    if not isinstance(key, dict) or set(key) != KEY_FIELDS:
        raise ValueError("signature key_id is unknown or malformed")
    allowed = key.get("allowed_kinds")
    if (not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed)
            or any(item not in {"ASSURANCE_RESULT", "PREFLIGHT_OBSERVATION"}
                   for item in allowed)):
        raise ValueError("trusted key allowed_kinds is malformed")
    if any(not _nonempty(key.get(field)) for field in (
        "public_key", "issuer_id", "evaluator_id", "evaluator_family",
        "not_before", "not_after",
    )):
        raise ValueError("trusted key identity or validity fields are malformed")
    if kind not in key["allowed_kinds"]:
        raise ValueError(f"trusted key is not authorized for {kind}")
    if key.get("revoked") is not False:
        raise ValueError("trusted key is revoked or has invalid revocation state")
    instant = _utcnow()
    not_before = _dt(key.get("not_before"), "trusted key not_before")
    not_after = _dt(key.get("not_after"), "trusted key not_after")
    if not_before >= not_after:
        raise ValueError("trusted key validity window is invalid")
    if instant < not_before:
        raise ValueError("trusted key is not active yet")
    if instant >= not_after:
        raise ValueError("trusted key is expired")
    bindings = {
        issuer_field: "issuer_id",
        identity_field: "evaluator_id",
        family_field: "evaluator_family",
    }
    for payload_field, key_field in bindings.items():
        if payload.get(payload_field) != key[key_field]:
            raise ValueError(f"signed {payload_field} does not match the trusted key identity")
    canonical = canonical_json(payload).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if envelope.get("payload_sha256") != digest:
        raise ValueError("signature payload digest mismatch")
    try:
        public_bytes = base64.b64decode(key["public_key"], validate=True)
        signature = base64.b64decode(envelope.get("signature", ""), validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, canonical)
    except ImportError as exc:
        raise ValueError("cryptography is required to verify assurance signatures") from exc
    except Exception as exc:
        raise ValueError("external assurance signature is invalid") from exc
    return {"key_id": key_id, "payload_sha256": digest,
            "issuer_id": key["issuer_id"], "evaluator_id": key["evaluator_id"],
            "evaluator_family": key["evaluator_family"]}


def verify_external_signature(payload: dict[str, Any], envelope: Any) -> dict[str, str]:
    ok, problems = validate_external_evaluation(payload)
    if not ok:
        raise ValueError(f"invalid external evaluation: {problems}")
    return verify_identity_signature(
        payload, envelope, kind="ASSURANCE_RESULT", issuer_field="issuer_id",
        identity_field="evaluator_id", family_field="evaluator_family",
    )


def _db_path(workspace: str | Path) -> Path:
    # Deliberately not derived from the builder workspace.  The external verifier
    # service/operator chooses this path and protects it with a separate OS identity.
    # The builder-facing MCP has no action that sets or writes it.
    raw = (os.environ.get(ASSURANCE_DB_ENV) or "").strip()
    if not raw:
        raise ValueError(f"{ASSURANCE_DB_ENV} is not configured")
    path = Path(raw).expanduser().resolve()
    if path.exists() and not path.is_file():
        raise ValueError("assurance database path is not a file")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(workspace: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(_db_path(workspace))
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_evaluation(
          evaluation_id TEXT PRIMARY KEY,
          payload_sha256 TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          envelope_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assurance_receipt(
          receipt_id TEXT PRIMARY KEY,
          receipt_sha256 TEXT NOT NULL,
          canonical_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        """
    )
    return db


def _insert_immutable(db: sqlite3.Connection, table: str, identity_field: str,
                      identity: str, values: dict[str, Any]) -> None:
    columns = [identity_field, *values]
    params = [identity, *values.values()]
    try:
        db.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            params,
        )
    except sqlite3.IntegrityError as exc:
        row = db.execute(f"SELECT * FROM {table} WHERE {identity_field}=?", (identity,)).fetchone()
        if row is None or any(row[name] != value for name, value in values.items()):
            raise ValueError(f"immutable {table} identity already exists with different bytes") from exc


def _validate_bindings(payload: dict[str, Any], execution_contract: dict[str, Any],
                       success_contract: dict[str, Any], now: datetime) -> None:
    exec_ok, exec_problems = validate_execution_contract(execution_contract)
    success_ok, success_problems = validate_success_contract(success_contract)
    if not exec_ok:
        raise ValueError(f"invalid execution contract: {exec_problems}")
    if not success_ok:
        raise ValueError(f"invalid success contract: {success_problems}")
    if execution_contract["task_id"] != success_contract["task_id"]:
        raise ValueError("execution and success contracts bind different tasks")
    if payload["task_id"] != execution_contract["task_id"]:
        raise ValueError("evaluation task_id does not match the frozen contracts")
    if payload["execution_contract_sha256"] != contract_sha256(execution_contract):
        raise ValueError("evaluation execution-contract digest mismatch")
    if payload["success_contract_sha256"] != contract_sha256(success_contract):
        raise ValueError("evaluation success-contract digest mismatch")
    result = payload["result"]
    if result["run_id"] != payload["run_id"]:
        raise ValueError("evaluation and result bind different run IDs")
    if result["execution_contract_sha256"] != payload["execution_contract_sha256"]:
        raise ValueError("result execution-contract digest mismatch")
    if result["success_contract_sha256"] != payload["success_contract_sha256"]:
        raise ValueError("result success-contract digest mismatch")
    if payload["evaluator_id"] == execution_contract["driver"]["name"]:
        raise ValueError("the build driver cannot certify its own result")
    independence = success_contract["evaluator_independence"]
    if independence["cross_driver_required"] and payload["evaluator_family"] == execution_contract["driver"]["name"]:
        raise ValueError("cross-driver evaluation requires a different evaluator family")
    if independence["external_replay_required"]:
        if not _nonempty(payload.get("replay_id")):
            raise ValueError("the success contract requires an external replay_id")
        if payload["replay_id"] == payload["run_id"]:
            raise ValueError("external replay_id must differ from the builder run_id")
    if now < _dt(payload["evaluated_at"], "evaluated_at"):
        raise ValueError("evaluation is future-dated")
    if now >= _dt(payload["expires_at"], "expires_at"):
        raise ValueError("evaluation is expired")


def _receipt_body(payload: dict[str, Any], verified: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": payload["evaluation_id"],
        "task_id": payload["task_id"],
        "run_id": payload["run_id"],
        "issuer_id": payload["issuer_id"],
        "evaluator_id": payload["evaluator_id"],
        "evaluator_family": payload["evaluator_family"],
        "execution_contract_sha256": payload["execution_contract_sha256"],
        "success_contract_sha256": payload["success_contract_sha256"],
        "result_sha256": digest_json(payload["result"]),
        "evidence_manifest_sha256": payload["evidence_manifest_sha256"],
        "overall_verdict": payload["result"]["overall_verdict"],
        "replay_id": payload["replay_id"],
        "evaluated_at": payload["evaluated_at"],
        "expires_at": payload["expires_at"],
        "key_id": verified["key_id"],
        "signed_payload_sha256": verified["payload_sha256"],
    }


def store_external_evaluation(payload: dict[str, Any], envelope: dict[str, Any], *,
                              execution_contract: dict[str, Any],
                              success_contract: dict[str, Any],
                              workspace: str | Path) -> dict[str, Any]:
    verified = verify_external_signature(payload, envelope)
    instant = _utcnow()
    _validate_bindings(payload, execution_contract, success_contract, instant)
    body = _receipt_body(payload, verified)
    receipt = {**body, "receipt_id": "ar_" + digest_json(body)[:32]}
    payload_json = canonical_json(payload)
    envelope_json = canonical_json(envelope)
    receipt_json = canonical_json(receipt)
    # Stable creation bytes make a retry idempotent even when it occurs later.  The
    # signed evaluator timestamp is authoritative; server wall-clock time is used
    # only to reject future/expired payloads.
    created_at = payload["evaluated_at"]
    with _connect(workspace) as db:
        _insert_immutable(db, "external_evaluation", "evaluation_id", payload["evaluation_id"], {
            "payload_sha256": verified["payload_sha256"], "payload_json": payload_json,
            "envelope_json": envelope_json, "created_at": created_at,
        })
        _insert_immutable(db, "assurance_receipt", "receipt_id", receipt["receipt_id"], {
            "receipt_sha256": hashlib.sha256(receipt_json.encode("utf-8")).hexdigest(),
            "canonical_json": receipt_json, "created_at": created_at,
        })
    return receipt


def load_assurance_receipt(receipt_id: str, *, workspace: str | Path) -> dict[str, Any] | None:
    with _connect(workspace) as db:
        row = db.execute("SELECT * FROM assurance_receipt WHERE receipt_id=?", (receipt_id,)).fetchone()
        if row is None:
            return None
        try:
            receipt = json.loads(row["canonical_json"])
        except json.JSONDecodeError as exc:
            raise ValueError("stored assurance receipt JSON is corrupt") from exc
        if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
            raise ValueError("stored assurance receipt has invalid fields")
        canonical = canonical_json(receipt)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["receipt_sha256"]:
            raise ValueError("stored assurance receipt digest mismatch")
        body = {k: v for k, v in receipt.items() if k != "receipt_id"}
        if receipt["receipt_id"] != "ar_" + digest_json(body)[:32]:
            raise ValueError("stored assurance receipt identity mismatch")
        evaluation = db.execute(
            "SELECT * FROM external_evaluation WHERE evaluation_id=?", (receipt["evaluation_id"],)
        ).fetchone()
        if evaluation is None:
            raise ValueError("stored assurance receipt has no signed evaluation")
        if hashlib.sha256(evaluation["payload_json"].encode("utf-8")).hexdigest() != evaluation["payload_sha256"]:
            raise ValueError("stored external evaluation digest mismatch")
        payload = json.loads(evaluation["payload_json"])
        envelope = json.loads(evaluation["envelope_json"])
        verified = verify_external_signature(payload, envelope)
        if _receipt_body(payload, verified) != body:
            raise ValueError("stored assurance receipt no longer matches the signed evaluation")
        if _utcnow() >= _dt(receipt["expires_at"], "expires_at"):
            raise ValueError("assurance receipt is expired")
        return receipt


def validate_assurance_receipt(receipt_id: str, *, expected_task_id: str,
                               expected_run_id: str, expected_execution_contract_sha256: str,
                               expected_success_contract_sha256: str,
                               workspace: str | Path) -> dict[str, Any]:
    receipt = load_assurance_receipt(receipt_id, workspace=workspace)
    if receipt is None:
        raise ValueError("unknown assurance receipt")
    expected = {
        "task_id": expected_task_id,
        "run_id": expected_run_id,
        "execution_contract_sha256": expected_execution_contract_sha256,
        "success_contract_sha256": expected_success_contract_sha256,
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise ValueError(f"assurance receipt {field} mismatch")
    return receipt
