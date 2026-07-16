"""Asymmetric trust-root verification for research policy and authority records.

Cortex stores public Ed25519 keys only. Policy authors, source curators, external evaluators, and
human consoles keep private keys outside the builder workspace/process. The trust-root path is an
operator setting, never a builder-MCP argument.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any


TRUST_ROOT_ENV = "CORTEX_RESEARCH_TRUST_ROOT"
KINDS = {"POLICY", "SOURCE_AUTHORITY", "SUBSTANTIVE_REVIEW", "HUMAN_APPROVAL"}
ENVELOPE_FIELDS = {"key_id", "payload_sha256", "signature"}


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _root_path() -> Path:
    value = (os.environ.get(TRUST_ROOT_ENV) or "").strip()
    if not value:
        raise ValueError(f"{TRUST_ROOT_ENV} is not configured")
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("research trust root is not a file")
    return path


def _load_root() -> dict[str, Any]:
    try:
        root = json.loads(_root_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"research trust root is invalid: {exc}") from exc
    if not isinstance(root, dict) or set(root) != {"schema_version", "keys"}:
        raise ValueError("research trust root must contain exactly schema_version and keys")
    if root.get("schema_version") != 1 or not isinstance(root.get("keys"), dict):
        raise ValueError("research trust root schema is invalid")
    return root


def verify_envelope(payload: dict[str, Any], envelope: dict[str, Any], *, kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown research trust kind: {kind}")
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
        raise ValueError("trust envelope has invalid fields")
    root = _load_root()
    key_id = envelope.get("key_id")
    key = root["keys"].get(key_id)
    key_fields = {"public_key", "issuer_id", "allowed_kinds"}
    if not isinstance(key, dict) or set(key) != key_fields:
        raise ValueError("trust envelope key_id is unknown or malformed")
    if kind not in key["allowed_kinds"]:
        raise ValueError(f"trusted key is not authorized for {kind}")
    if kind != "POLICY" and payload.get("issuer_id") != key["issuer_id"]:
        raise ValueError("payload issuer_id does not match trusted key issuer")
    canonical = _canonical(payload)
    digest = hashlib.sha256(canonical).hexdigest()
    if envelope.get("payload_sha256") != digest:
        raise ValueError("trust envelope payload digest mismatch")
    try:
        public_bytes = base64.b64decode(key["public_key"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, canonical)
    except ImportError as exc:
        raise ValueError("cryptography is required to verify research trust envelopes") from exc
    except Exception as exc:  # invalid key/base64/signature all fail closed
        raise ValueError("research trust envelope signature is invalid") from exc
    return {"key_id": key_id, "issuer_id": key["issuer_id"], "kind": kind,
            "payload_sha256": digest}

