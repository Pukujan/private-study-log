"""Frozen tests for the trusted-runner attestation layer (the authenticated provenance boundary).

Pins the five anti-laundering invariants:
  (a) unattested evidence  -> non_human_verified + USABLE (never blocked)
  (b) valid signed attestation -> hard_gold / trainable
  (c) forged / replayed / expired / wrong-issuer attestation -> quarantine
  (d) a privileged self-claimed role with no signed credential -> rejected
  (e) the training-export chokepoint refuses unattested trainable data
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import attestation as A  # noqa: E402
from cortex_core.attestation import (  # noqa: E402
    NonceStore, issue_attestation, verify_attestation, issue_role_credential,
    authenticate_role, _now, sha256_hex,
)
from cortex_core.promotion import derive_tier, State, TRAINABLE  # noqa: E402
from cortex_core import registry as R  # noqa: E402


@pytest.fixture()
def secret(tmp_path):
    """A per-test signing secret file so tests are isolated and hermetic."""
    return str(tmp_path / "attest_secret.json")


def _checker_evidence():
    """A well-formed objective-checker claim (would reach hard_gold if authentic)."""
    return {"label_authority": "bfcl_ast_checker", "objective_verdict": "pass",
            "checker_decided": True}


# ----------------------------------------------------------------- (a) unattested is usable, not trainable
def test_unattested_evidence_is_usable_but_capped_at_non_human_verified():
    ev = _checker_evidence()  # a bare caller dict, no attestation
    d = derive_tier("x", ev)
    assert d.state == State.PROMOTED                 # USABLE NOW (never-wait) -- not blocked
    assert d.tier == "non_human_verified"            # but capped below trainable
    assert d.tier not in TRAINABLE
    assert not d.asdict()["trainable"]


# ----------------------------------------------------------------- (b) valid attestation -> trainable
def test_valid_signed_attestation_reaches_hard_gold(secret, monkeypatch):
    ev = _checker_evidence()
    # bind the attestation to THIS evidence's subject sha
    from cortex_core.promotion import _evidence_subject_sha
    subject = _evidence_subject_sha(ev)
    att = issue_attestation(check="bfcl_ast_checker", result="pass",
                            request_bytes=b"tool_call_bytes", subject_sha=subject,
                            store_path=secret)
    ev["attestation"] = att
    store = NonceStore()
    d = derive_tier("x", ev, nonce_store=store,
                    verifier_kwargs={"store_path": secret})
    assert d.state == State.PROMOTED and d.tier == "hard_gold"
    assert d.asdict()["trainable"]


# ----------------------------------------------------------------- (c) forged / replayed / expired / wrong-issuer
def test_forged_signature_is_quarantined(secret):
    ev = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    att = issue_attestation(check="bfcl_ast_checker", result="pass",
                            subject_sha=_evidence_subject_sha(ev), store_path=secret)
    att["signature"] = "deadbeef" * 8               # tamper
    ev["attestation"] = att
    d = derive_tier("x", ev, verifier_kwargs={"store_path": secret})
    assert d.state == State.QUARANTINED
    assert "not verified" in " ".join(d.reasons)


def test_wrong_issuer_is_quarantined(secret, monkeypatch):
    ev = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    monkeypatch.setenv("CORTEX_ATTEST_ISSUER", "attacker-server")
    att = issue_attestation(check="c", result="pass",
                            subject_sha=_evidence_subject_sha(ev), store_path=secret)
    monkeypatch.setenv("CORTEX_ATTEST_ISSUER", "cortex-server")   # verifier expects the real issuer
    ev["attestation"] = att
    d = derive_tier("x", ev, verifier_kwargs={"store_path": secret})
    assert d.state == State.QUARANTINED


def test_expired_attestation_is_quarantined(secret):
    ev = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    att = issue_attestation(check="c", result="pass", ttl_seconds=1,
                            subject_sha=_evidence_subject_sha(ev), store_path=secret)
    later = _now() + timedelta(hours=2)
    ev["attestation"] = att
    d = derive_tier("x", ev, now=later, verifier_kwargs={"store_path": secret})
    assert d.state == State.QUARANTINED
    assert "expired" in " ".join(d.reasons)


def test_replayed_nonce_is_quarantined(secret):
    ev = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    att = issue_attestation(check="bfcl_ast_checker", result="pass",
                            subject_sha=_evidence_subject_sha(ev), store_path=secret)
    ev["attestation"] = att
    store = NonceStore()
    first = derive_tier("x", ev, nonce_store=store, verifier_kwargs={"store_path": secret})
    assert first.tier == "hard_gold"                 # first use OK
    second = derive_tier("x", ev, nonce_store=store, verifier_kwargs={"store_path": secret})
    assert second.state == State.QUARANTINED         # replay refused
    assert "replay" in " ".join(second.reasons)


def test_attestation_bound_to_other_evidence_is_quarantined(secret):
    """A valid attestation for evidence A cannot launder a swapped evidence B."""
    ev_a = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    att = issue_attestation(check="c", result="pass",
                            subject_sha=_evidence_subject_sha(ev_a), store_path=secret)
    ev_b = {"label_authority": "bfcl_ast_checker", "objective_verdict": "pass",
            "checker_decided": True, "extra": "swapped"}   # different subject sha
    ev_b["attestation"] = att
    d = derive_tier("x", ev_b, verifier_kwargs={"store_path": secret})
    assert d.state == State.QUARANTINED
    assert "subject" in " ".join(d.reasons)


# ----------------------------------------------------------------- folded sol@xhigh must-fixes
def test_valid_but_failing_verdict_cannot_reach_trainable(secret):
    """sol P0 #2: a genuine attestation with result='fail' must NOT authorize a trainable tier."""
    ev = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    att = issue_attestation(check="bfcl_ast_checker", result="fail",
                            subject_sha=_evidence_subject_sha(ev), store_path=secret)
    ev["attestation"] = att
    d = derive_tier("x", ev, verifier_kwargs={"store_path": secret})
    assert d.state == State.QUARANTINED and "non-passing" in " ".join(d.reasons)


def test_registry_refuses_failing_verdict_on_trainable_tier(tmp_path, secret):
    content = "def check(): return True"
    att = issue_attestation(check="pytest", result="fail", subject_sha=sha256_hex(content),
                            store_path=secret)
    with pytest.raises(PermissionError, match="not verified"):
        R.register("m", "checker", content, author_model="local", trust_tier="hard_gold",
                   metadata={"attestation": att}, workspace=tmp_path,
                   attestation_verifier=lambda a, **k: verify_attestation(a, store_path=secret, **k))


def test_privileged_role_without_key_principal_is_refused(secret):
    """sol P0 #6: a stolen credential with no presented key binding must fail closed."""
    cred = issue_role_credential("ck_1", "tenant_1", "admin", store_path=secret)
    role, authed, reason = authenticate_role("admin", cred, key_info=None, store_path=secret)
    assert role == "agent" and authed is False and "bound key principal" in reason


def test_future_issued_attestation_is_rejected(secret):
    ev = _checker_evidence()
    from cortex_core.promotion import _evidence_subject_sha
    from datetime import timedelta as _td
    att = issue_attestation(check="c", result="pass", subject_sha=_evidence_subject_sha(ev),
                            store_path=secret)
    ev["attestation"] = att
    earlier = _now() - _td(hours=1)   # verifier's clock is BEFORE issuance -> future issuance
    d = derive_tier("x", ev, now=earlier, verifier_kwargs={"store_path": secret})
    assert d.state == State.QUARANTINED and "future" in " ".join(d.reasons)


def test_weak_env_secret_is_rejected(monkeypatch):
    from cortex_core.attestation import _load_or_create_secret
    with pytest.raises(ValueError, match="too short"):
        _load_or_create_secret(None, env={"CORTEX_ATTEST_SECRET": "short"})


def test_non_json_native_evidence_is_rejected():
    from cortex_core.promotion import _evidence_subject_sha

    class Sneaky:
        def __str__(self):
            return "pass"
    with pytest.raises(TypeError):
        _evidence_subject_sha({"objective_verdict": Sneaky()})


# ----------------------------------------------------------------- (d) role credential
def test_privileged_self_claim_without_credential_is_rejected():
    role, authed, reason = authenticate_role("admin", credential=None)
    assert role == "agent" and authed is False and "refused" in reason


def test_unprivileged_self_claim_is_accepted():
    role, authed, _ = authenticate_role("builder")
    assert role == "builder" and authed is False   # accepted but unauthenticated (harmless)


def test_valid_role_credential_grants_privileged_role(secret):
    cred = issue_role_credential("ck_1", "tenant_1", "gold_author", store_path=secret)
    role, authed, _ = authenticate_role(
        "gold_author", cred, key_info={"key_id": "ck_1", "tenant_id": "tenant_1"},
        store_path=secret)
    assert role == "gold_author" and authed is True


def test_role_credential_bound_to_other_key_is_rejected(secret):
    cred = issue_role_credential("ck_1", "tenant_1", "trainer", store_path=secret)
    role, authed, reason = authenticate_role(
        "trainer", cred, key_info={"key_id": "ck_OTHER", "tenant_id": "tenant_1"},
        store_path=secret)
    assert role == "agent" and authed is False and "bound" in reason


def test_forged_role_credential_is_rejected(secret):
    cred = issue_role_credential("ck_1", "tenant_1", "admin", store_path=secret)
    cred["signature"] = "00" * 32
    role, authed, _ = authenticate_role(
        "admin", cred, key_info={"key_id": "ck_1", "tenant_id": "tenant_1"}, store_path=secret)
    assert role == "agent" and authed is False


# ----------------------------------------------------------------- (e) training-export chokepoint
def test_registry_refuses_unattested_trainable_tier(tmp_path):
    with pytest.raises(PermissionError, match="requires a server-signed attestation"):
        R.register("m", "checker", "def check(): return True", author_model="local",
                   trust_tier="hard_gold", workspace=tmp_path)


def test_registry_allows_unattested_non_trainable_tier(tmp_path):
    art = R.register("m", "rubric", "content", author_model="fable",
                     trust_tier="non_human_verified", workspace=tmp_path)
    assert art.trust_tier == "non_human_verified"    # unattested is still WRITABLE, just not trainable


def test_registry_accepts_attested_trainable_tier(tmp_path, secret):
    content = "def check(): return True"
    att = issue_attestation(check="pytest", result="pass",
                            subject_sha=sha256_hex(content), store_path=secret)
    art = R.register("m", "checker", content, author_model="local", trust_tier="hard_gold",
                     metadata={"attestation": att}, workspace=tmp_path,
                     attestation_verifier=lambda a, **k: verify_attestation(a, store_path=secret, **k))
    assert art.trust_tier == "hard_gold"


def test_registry_refuses_forged_attestation_on_trainable_tier(tmp_path, secret):
    content = "payload"
    att = issue_attestation(check="pytest", result="pass",
                            subject_sha=sha256_hex(content), store_path=secret)
    att["payload"]["result"] = "TAMPERED"            # break the signature binding
    with pytest.raises(PermissionError, match="not verified"):
        R.register("m", "checker", content, author_model="local", trust_tier="hard_gold",
                   metadata={"attestation": att}, workspace=tmp_path,
                   attestation_verifier=lambda a, **k: verify_attestation(a, store_path=secret, **k))
