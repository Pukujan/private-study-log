"""Trusted-runner attestation — the authenticated provenance boundary.

The single keystone three sol@xhigh red-teams converged on: `evidence` was
caller-populated, so *any* trust tier was forgeable by a caller willing to
fabricate a dict. This module makes evidence AUTHENTICITY provable.

The reconciliation with the owner's never-wait policy (never block on a human):

  * Unattested caller-dict evidence stays USABLE NOW, but capped at the
    non-trainable `non_human_verified` tier (see promotion.derive_tier).
  * A SERVER-SIGNED attestation is what UNLOCKS the trainable / authoritative
    tiers. The server issues it deterministically at machine speed, so speed
    is preserved AND trust is real.

An attestation binds {what deterministic check ran, its result, the captured
request bytes, a signed role credential, an issuer identity, a clock + TTL,
a single-use nonce} under an HMAC-SHA256 signature keyed by a secret the
SERVER holds and the caller does not. On the actual deployment (single host,
owner-privileged, server both signs and verifies) that symmetric secret is
exactly sufficient, and it is pure stdlib.

Phased upgrades (designed; seams isolated to `_sign`/`_verify_sig` and the
`request_sha256` / `external_timestamp` fields): out-of-band gateway
byte-capture, an external signed clock (RFC-3161 / OpenTimestamps), a
separate-OS signing identity, and asymmetric Ed25519 for multi-host verify.
See docs/design/trusted-runner-attestation-2026-07-14.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Roles that confer privilege and therefore MUST be backed by a signed
# credential — a bare self-claim of one of these is rejected. Everything else
# is a harmless descriptive self-claim (never-wait: still usable).
PRIVILEGED_ROLES = frozenset({"admin", "gold_author", "trainer"})
DEFAULT_UNPRIVILEGED_ROLE = "agent"

# A signature only proves the server SIGNED the record — it does NOT prove the check PASSED.
# The trainable tiers require a passing verdict, so verification cross-checks the signed `result`
# against this allow-list (sol@xhigh P0 #2: a genuine result="fail" attestation must not authorize
# a trainable tier). "true"/True cover boolean checkers; the security lane uses vulnerable/secure.
PASSING_RESULTS = frozenset({"pass", "passed", "true", "vulnerable", "secure", "ok"})


def is_passing_result(result) -> bool:
    if result is True:
        return True
    return str(result).strip().lower() in PASSING_RESULTS


# Clock-skew tolerance for the future-issuance guard (an attestation issued "in the future"
# relative to the verifier is a tampered/replayed clock — reject beyond this slack).
CLOCK_SKEW_SECONDS = 300
# Minimum entropy for an env-supplied signing secret (chars). An operator who sets a short secret
# invites offline guessing; fail loudly rather than sign with a weak key.
MIN_ENV_SECRET_LEN = 32

# Attestation / credential validity window (seconds). Short by default: an
# attestation is minted at the moment a check runs and consumed immediately.
DEFAULT_TTL_SECONDS = 3600
ROLE_CRED_TTL_SECONDS = 30 * 24 * 3600  # role credentials are longer-lived

_SECRET_ENV = "CORTEX_ATTEST_SECRET"
_ISSUER_ENV = "CORTEX_ATTEST_ISSUER"
_DEFAULT_ISSUER = "cortex-server"


# --------------------------------------------------------------------------- time helpers
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- secret / issuer
def _secret_store_path(store_path: str | Path | None) -> Path:
    if store_path is not None:
        return Path(store_path)
    from cortex_core.config import resolve_workspace
    return resolve_workspace() / "logs" / "attest_secret.json"


def _load_or_create_secret(store_path: str | Path | None, env=None) -> bytes:
    """Server signing secret. Precedence: explicit env override, else a gitignored
    per-workspace file (auto-created once). The raw secret never leaves the host."""
    import os
    env = os.environ if env is None else env
    env_secret = (env.get(_SECRET_ENV) or "").strip()
    if env_secret:
        if len(env_secret) < MIN_ENV_SECRET_LEN:
            raise ValueError(
                f"{_SECRET_ENV} is too short ({len(env_secret)} < {MIN_ENV_SECRET_LEN} chars) — "
                "a weak signing secret is offline-guessable; use a long random value.")
        return env_secret.encode("utf-8")
    p = _secret_store_path(store_path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("secret"):
                return data["secret"].encode("utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    # mint once
    raw = secrets.token_urlsafe(48)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"secret": raw, "created": _iso(_now())}), encoding="utf-8")
    try:  # best-effort tighten perms (no-op on Windows ACLs)
        p.chmod(0o600)
    except OSError:
        pass
    return raw.encode("utf-8")


def issuer_id(env=None) -> str:
    import os
    env = os.environ if env is None else env
    return (env.get(_ISSUER_ENV) or _DEFAULT_ISSUER).strip() or _DEFAULT_ISSUER


# --------------------------------------------------------------------------- signing seam (HMAC today; Ed25519 later)
def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sign(payload: dict, secret: bytes) -> str:
    return hmac.new(secret, _canonical(payload), hashlib.sha256).hexdigest()


def _verify_sig(payload: dict, signature: str, secret: bytes) -> bool:
    expected = _sign(payload, secret)
    # constant-time; compare_digest tolerates unequal lengths safely
    return hmac.compare_digest(expected, str(signature or ""))


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- nonce replay store
class NonceStore:
    """Single-use nonce ledger for replay defense. In-memory by default (tests);
    file-backed when a path is given (append-on-consume, idempotent-safe)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self._seen: set[str] = set()
        if self.path and self.path.exists():
            try:
                self._seen = set(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                self._seen = set()

    def seen(self, nonce: str) -> bool:
        return nonce in self._seen

    def consume(self, nonce: str) -> None:
        self._seen.add(nonce)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(sorted(self._seen)), encoding="utf-8")


# --------------------------------------------------------------------------- role credentials
def issue_role_credential(key_id: str, tenant_id: str, role: str, *,
                          store_path: str | Path | None = None, env=None,
                          ttl_seconds: int = ROLE_CRED_TTL_SECONDS,
                          now: datetime | None = None) -> dict:
    """Mint a SERVER-SIGNED credential binding {key_id, tenant_id, role}. This is what a
    caller presents to prove a privileged role, instead of merely self-claiming it."""
    secret = _load_or_create_secret(store_path, env)
    n = now or _now()
    payload = {
        "kind": "role_credential",
        "key_id": key_id, "tenant_id": tenant_id, "role": role,
        "issuer": issuer_id(env),
        "issued_at": _iso(n),
        "expires_at": _iso(n + timedelta(seconds=ttl_seconds)),
    }
    return {"payload": payload, "signature": _sign(payload, secret)}


def verify_role_credential(cred: dict | None, *, key_id: str | None = None,
                           tenant_id: str | None = None, store_path: str | Path | None = None,
                           env=None, now: datetime | None = None) -> tuple[bool, str]:
    """Verify a role credential's signature, issuer, expiry, and (optionally) that it is
    bound to the presenting key/tenant. Returns (ok, reason)."""
    if not isinstance(cred, dict):
        return False, "no role credential presented"
    payload, sig = cred.get("payload"), cred.get("signature")
    if not isinstance(payload, dict) or not sig:
        return False, "malformed role credential"
    if payload.get("kind") != "role_credential":
        return False, "not a role credential"
    if payload.get("issuer") != issuer_id(env):
        return False, f"wrong issuer {payload.get('issuer')!r}"
    secret = _load_or_create_secret(store_path, env)
    if not _verify_sig(payload, sig, secret):
        return False, "bad signature (forged or tampered)"
    n = now or _now()
    try:
        if n > _parse_iso(payload["expires_at"]):
            return False, "expired role credential"
    except (KeyError, ValueError):
        return False, "missing/invalid expiry"
    if key_id is not None and payload.get("key_id") != key_id:
        return False, "credential not bound to the presenting key"
    if tenant_id is not None and payload.get("tenant_id") != tenant_id:
        return False, "credential not bound to the presenting tenant"
    return True, "verified"


def authenticate_role(claimed_role: str | None, credential: dict | None = None, *,
                      key_info: dict | None = None, store_path: str | Path | None = None,
                      env=None, now: datetime | None = None) -> tuple[str, bool, str]:
    """Resolve the AUTHORITATIVE role for a session, closing the "trusts arbitrary role" hole.

    - A privileged claim (admin/gold_author/trainer) requires a valid signed credential bound
      to the presenting key; without it the claim is REJECTED and the role falls back to the
      unprivileged default (never-wait: the session still works, just unprivileged).
    - An unprivileged claim is a harmless self-claim, accepted as-is (unauthenticated).

    Returns (authoritative_role, authenticated, reason)."""
    claim = (claimed_role or DEFAULT_UNPRIVILEGED_ROLE).strip() or DEFAULT_UNPRIVILEGED_ROLE
    if claim not in PRIVILEGED_ROLES:
        return claim, False, "unprivileged self-claim (no credential required)"
    # Fail-closed on the binding (sol@xhigh P0 #6): a privileged role requires a COMPLETE
    # server-derived principal. Without a presented key_id+tenant_id the credential's binding
    # can't be checked, so a stolen credential would verify unbound — refuse instead.
    key_id = (key_info or {}).get("key_id")
    tenant_id = (key_info or {}).get("tenant_id")
    if not key_id or not tenant_id:
        return DEFAULT_UNPRIVILEGED_ROLE, False, (
            f"privileged role {claim!r} refused: no bound key principal presented "
            f"(need an authenticated key_id+tenant_id); downgraded to {DEFAULT_UNPRIVILEGED_ROLE!r}")
    ok, reason = verify_role_credential(credential, key_id=key_id, tenant_id=tenant_id,
                                        store_path=store_path, env=env, now=now)
    if not ok:
        return DEFAULT_UNPRIVILEGED_ROLE, False, (
            f"privileged role {claim!r} refused: {reason}; downgraded to "
            f"{DEFAULT_UNPRIVILEGED_ROLE!r}")
    if credential["payload"].get("role") != claim:
        return DEFAULT_UNPRIVILEGED_ROLE, False, (
            f"credential grants {credential['payload'].get('role')!r}, not claimed {claim!r}")
    return claim, True, "role credential verified"


# --------------------------------------------------------------------------- attestations
def issue_attestation(*, check: str, result, request_bytes: bytes | str = b"",
                      subject_sha: str | None = None, role_credential: dict | None = None,
                      store_path: str | Path | None = None, env=None,
                      ttl_seconds: int = DEFAULT_TTL_SECONDS,
                      external_timestamp: str | None = None,
                      now: datetime | None = None) -> dict:
    """Issue a server-signed attestation binding a deterministic check's result to the
    captured request bytes, an issuer, a clock+TTL, a single-use nonce, and (optionally) the
    subject it attests to and a signed role credential.

    `request_bytes`: the captured request / tool-call bytes. In the full deployment a gateway
    captures these out-of-band (phased); here the issuer hashes what it is given.
    `external_timestamp`: reserved for an RFC-3161 / OpenTimestamps token (phased); the built
    path uses `issued_at` (server clock) + expiry.
    """
    secret = _load_or_create_secret(store_path, env)
    n = now or _now()
    payload = {
        "kind": "attestation",
        "check": check,
        "result": result,
        "request_sha256": sha256_hex(request_bytes),
        "subject_sha": subject_sha,
        "role_credential": role_credential,
        "external_timestamp": external_timestamp,
        "issuer": issuer_id(env),
        "issued_at": _iso(n),
        "expires_at": _iso(n + timedelta(seconds=ttl_seconds)),
        "nonce": uuid.uuid4().hex,
    }
    return {"payload": payload, "signature": _sign(payload, secret)}


def verify_attestation(att: dict | None, *, expected_subject_sha: str | None = None,
                       require_passing: bool = True,
                       store_path: str | Path | None = None, env=None,
                       nonce_store: NonceStore | None = None,
                       now: datetime | None = None, consume: bool = True) -> tuple[bool, str]:
    """Verify a server-signed attestation. Rejection order (fail-closed at every step):
    malformed -> wrong issuer -> bad signature -> future issuance -> expired -> non-passing verdict
    -> replayed nonce -> subject mismatch -> bad embedded credential. Any failure means the
    attestation is NOT trusted and the caller's tier claim must be quarantined (never silently
    downgraded to usable). `require_passing` (default True) rejects a genuine but FAILING verdict —
    a signature proves the server signed it, not that the check passed. Returns (ok, reason)."""
    if not isinstance(att, dict):
        return False, "no attestation presented"
    payload, sig = att.get("payload"), att.get("signature")
    if not isinstance(payload, dict) or not sig:
        return False, "malformed attestation"
    if payload.get("kind") != "attestation":
        return False, "not an attestation"
    if payload.get("issuer") != issuer_id(env):
        return False, f"wrong issuer {payload.get('issuer')!r} (untrusted signer)"
    secret = _load_or_create_secret(store_path, env)
    if not _verify_sig(payload, sig, secret):
        return False, "bad signature (forged or tampered)"
    n = now or _now()
    try:
        if _parse_iso(payload["issued_at"]) > n + timedelta(seconds=CLOCK_SKEW_SECONDS):
            return False, "attestation issued in the future (tampered clock)"
    except (KeyError, ValueError):
        return False, "missing/invalid issued_at"
    try:
        if n > _parse_iso(payload["expires_at"]):
            return False, "expired attestation"
    except (KeyError, ValueError):
        return False, "missing/invalid expiry"
    if require_passing and not is_passing_result(payload.get("result")):
        return False, (f"non-passing verdict {payload.get('result')!r} — a signed FAILING check "
                       "cannot authorize a trainable tier")
    nonce = payload.get("nonce")
    if not nonce:
        return False, "missing nonce"
    if nonce_store is not None and nonce_store.seen(nonce):
        return False, "replayed nonce (attestation already consumed)"
    if expected_subject_sha is not None and payload.get("subject_sha") != expected_subject_sha:
        return False, "subject mismatch (attestation is for different content)"
    # If a role credential rides along, it must itself verify (bound authenticity).
    if payload.get("role_credential") is not None:
        ok, reason = verify_role_credential(payload["role_credential"], store_path=store_path,
                                            env=env, now=now)
        if not ok:
            return False, f"embedded role credential invalid: {reason}"
    if nonce_store is not None and consume:
        nonce_store.consume(nonce)
    return True, "verified"
