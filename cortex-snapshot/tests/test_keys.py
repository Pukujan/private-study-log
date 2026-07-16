"""Per-tenant API key issuance (H2b / browser-extension auth): the owner mints scoped keys clients
present as the bearer; only the SHA-256 + metadata is stored, the raw key is returned ONCE. Keys are
independently rotatable/revocable so a leaked browser key is killed without touching anyone else."""
from __future__ import annotations

from cortex_core import keys


def test_issue_then_verify_and_raw_returned_once(tmp_path):
    store = tmp_path / "api_keys.json"
    key_id, raw = keys.issue_key("hermes-browser", scope="read", tenant_id="t1", store_path=store)
    assert key_id and raw and raw != key_id
    info = keys.verify_key(raw, store_path=store)
    assert info and info["key_id"] == key_id and info["tenant_id"] == "t1" and info["scope"] == "read"
    # the store holds only the hash, never the raw key
    assert raw not in store.read_text(encoding="utf-8")


def test_wrong_key_does_not_verify(tmp_path):
    store = tmp_path / "api_keys.json"
    keys.issue_key("x", store_path=store)
    assert keys.verify_key("cortex_not_a_real_key", store_path=store) is None
    assert keys.verify_key("", store_path=store) is None
    assert keys.verify_key(None, store_path=store) is None


def test_revoke_kills_the_key(tmp_path):
    store = tmp_path / "api_keys.json"
    key_id, raw = keys.issue_key("x", store_path=store)
    assert keys.verify_key(raw, store_path=store) is not None
    assert keys.revoke_key(key_id, store_path=store) is True
    assert keys.verify_key(raw, store_path=store) is None            # revoked -> fails closed
    assert keys.revoke_key("nope", store_path=store) is False


def test_rotate_invalidates_old_and_issues_new_same_scope(tmp_path):
    store = tmp_path / "api_keys.json"
    key_id, raw = keys.issue_key("x", scope="tenant_write", tenant_id="t9", store_path=store)
    new_id, new_raw = keys.rotate_key(key_id, store_path=store)
    assert new_id != key_id and new_raw != raw
    assert keys.verify_key(raw, store_path=store) is None            # old dead
    info = keys.verify_key(new_raw, store_path=store)
    assert info and info["scope"] == "tenant_write" and info["tenant_id"] == "t9"  # scope/tenant carried


def test_http_bearer_gate_accepts_shared_bearer_and_issued_keys(monkeypatch):
    # multi-tenant transport auth: the shared bearer (owner) OR any valid issued key gets in
    from cortex_core.authz import hash_token
    from cortex_core.http_server import _BearerAuthMiddleware
    mw = _BearerAuthMiddleware(app=None, expected_sha256=hash_token("shared-owner-bearer"))
    assert mw._authorized("shared-owner-bearer") is True     # shared bearer
    assert mw._authorized("wrong") is False
    assert mw._authorized("") is False
    monkeypatch.setattr("cortex_core.keys.verify_key",
                        lambda t, **k: {"tenant_id": "t1", "scope": "read"} if t == "issued-key-abc" else None)
    assert mw._authorized("issued-key-abc") is True          # a valid tenant key authenticates
    assert mw._authorized("revoked-or-fake") is False


def _reg():
    from cortex_core import mcp as m
    return getattr(m.cortex_register, "fn", m.cortex_register)


def test_read_scoped_key_is_blocked_from_writes(monkeypatch):
    from cortex_core import mcp as m
    monkeypatch.setattr("cortex_core.keys.verify_key",
                        lambda t, **k: {"scope": "read", "tenant_id": "t1", "key_id": "ck_r"} if t == "readkey" else None)
    out = _reg()("agent", "model", api_key="readkey")
    sid = out["session_id"]
    assert m._sessions[sid]["scope"] == "read"
    gate = m._admin_gate(sid, "cortex_write_log", None)   # a write tool consults this gate
    assert gate is not None and gate["refused"] is True    # read key -> writes refused everywhere


def test_tenant_write_key_may_write_in_owner_mode(monkeypatch):
    from cortex_core import mcp as m
    monkeypatch.setattr("cortex_core.keys.verify_key",
                        lambda t, **k: {"scope": "tenant_write", "tenant_id": "t2", "key_id": "ck_w"} if t == "writekey" else None)
    out = _reg()("agent", "model", api_key="writekey")
    sid = out["session_id"]
    assert m._sessions[sid]["scope"] == "tenant_write"
    assert m._admin_gate(sid, "cortex_write_log", None) is None   # not read-scoped -> gate passes (owner mode)


def test_list_keys_is_metadata_only_never_secret(tmp_path):
    store = tmp_path / "api_keys.json"
    kid, raw = keys.issue_key("label-a", scope="read", store_path=store)
    listed = keys.list_keys(store_path=store)
    assert len(listed) == 1
    rec = listed[0]
    assert rec["key_id"] == kid and rec["label"] == "label-a" and rec["status"] == "active"
    # no raw key and no hash leak in the listing
    assert "hash" not in rec and "sha256" not in rec
    assert all(raw not in str(v) for v in rec.values())
