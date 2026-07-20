"""Frozen tests for trace / closeout credential redaction (gap J7).

Guarantee under test: a seeded credential in a trace is redacted BEFORE it would be persisted
(the durable store never contains the secret); ordinary prose passes through unchanged. No
model, no network, no judge anywhere in the path.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cortex_core.trace_redaction import (  # noqa: E402
    REDACTED,
    find_secrets,
    looks_high_entropy_secret,
    redact_text,
    redact_then_persist,
    redact_trace,
)

# Synthetic secrets assembled at runtime so this test file carries no literal key
# (keeps ops/secret_audit.py clean on the test file itself).
_FAKE_OPENAI = "sk-" + "A1b2C3d4E5f6G7h8J9k0"
_FAKE_GH = "ghp_" + "abcdEFGH1234ijklMNOP5678"
_FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "SflKxwRJSMeKKF2QT4"


# --- ordinary prose passes through -----------------------------------------------------------
def test_plain_prose_unchanged():
    prose = "The retrieval baseline improved recall@5 from 0.61 to 0.70 on the same corpus."
    assert redact_text(prose) == prose


def test_novel_dict_prose_unchanged():
    obj = {"task": "measure retrieval", "result": "recall improved", "count": 42}
    assert redact_trace(obj) == obj


# --- credential shapes get masked ------------------------------------------------------------
def test_openai_style_key_masked():
    out = redact_text(f"export KEY={_FAKE_OPENAI}")
    assert _FAKE_OPENAI not in out and "sk-" in out  # prefix hint kept, secret masked


def test_github_and_aws_keys_masked():
    assert _FAKE_GH not in redact_text(f"token: {_FAKE_GH}")
    assert _FAKE_AWS not in redact_text(f"aws_access_key_id = {_FAKE_AWS}")


def test_jwt_masked():
    assert _FAKE_JWT not in redact_text(f"Cookie: session={_FAKE_JWT}")


def test_pem_private_key_masked():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEncodedstuffHERE\n-----END RSA PRIVATE KEY-----"
    out = redact_text(f"key material:\n{pem}")
    assert "PRIVATE KEY-----\nMIIE" not in out
    assert "REDACTED PRIVATE KEY" in out


def test_auth_header_masked():
    out = redact_text(f"Authorization: Bearer {_FAKE_JWT}")
    assert _FAKE_JWT not in out


def test_db_connection_string_password_masked():
    out = redact_text("postgres://appuser:sup3rSecretPw@db.internal:5432/prod")
    assert "sup3rSecretPw" not in out and "***" in out


def test_ssn_and_card_masked():
    assert "123-45-6789" not in redact_text("SSN 123-45-6789 on file")
    out = redact_text("card 4111 1111 1111 1111 charged")
    assert "4111 1111 1111 1111" not in out  # full PAN gone
    assert out.endswith("1111charged") or out.endswith("1111 charged")  # last-4 retained


def test_high_entropy_token_masked():
    tok = "Zx9Kq2Lm7Pw4Rt6Yv8Bn3Cd5Fg1Hj0"  # 31 chars, high entropy, no known prefix
    assert looks_high_entropy_secret(tok)
    assert tok not in redact_text(f"opaque token {tok} here")


# --- structured / field-level redaction ------------------------------------------------------
def test_secret_named_field_fully_redacted():
    obj = {"model": "qwen", "api_key": "whatever-value-here", "prompt": "hello"}
    out = redact_trace(obj)
    assert out["api_key"] == REDACTED
    assert out["model"] == "qwen" and out["prompt"] == "hello"


def test_nested_trace_structure_redacted():
    trace = {
        "steps": [
            {"tool": "http", "headers": {"Authorization": f"Bearer {_FAKE_JWT}"}},
            {"note": "benign step", "password": "hunter2xxxxxx"},
        ],
        "meta": {"session": "abc"},
    }
    out = redact_trace(trace)
    assert out["steps"][1]["password"] == REDACTED
    assert _FAKE_JWT not in json.dumps(out)


# --- THE core guarantee: redaction runs BEFORE persistence -----------------------------------
def test_seeded_credential_is_redacted_before_persistence(tmp_path):
    store = tmp_path / "traces.jsonl"
    record = {"task": "call the API",
              "trace": f"curl -H 'Authorization: Bearer {_FAKE_OPENAI}' https://api",
              "api_key": _FAKE_GH}
    scrubbed = redact_then_persist(record, store)
    # The returned record is scrubbed...
    assert scrubbed["api_key"] == REDACTED
    # ...and CRUCIALLY the durable file never contains the secret.
    on_disk = store.read_text(encoding="utf-8")
    assert _FAKE_OPENAI not in on_disk
    assert _FAKE_GH not in on_disk
    assert find_secrets(json.loads(on_disk)) == []


def test_find_secrets_detects_unredacted_and_clears_after_redaction():
    dirty = {"trace": f"key {_FAKE_AWS} and jwt {_FAKE_JWT}"}
    assert find_secrets(dirty)  # non-empty: residual secrets present
    assert find_secrets(redact_trace(dirty)) == []  # clean after redaction
