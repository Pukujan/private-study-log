"""Owner-only local key dashboard: it refuses to run unauthenticated, refuses a non-localhost bind,
401s any /api request without the admin bearer, lists metadata only (no raw key / no sha256 leak),
and does a full issue -> list -> revoke round-trip. No secret is ever returned except the one-time
raw key at issuance."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from cortex_core import keys_dashboard as kd
from cortex_core.authz import hash_token

BEARER = "owner-secret-bearer"
SHA = hash_token(BEARER)
AUTH = {"Authorization": f"Bearer {BEARER}"}


def _client(store):
    return TestClient(kd.build_app(SHA, store_path=store))


def test_refuses_to_start_without_admin_bearer():
    with pytest.raises(ValueError):
        kd.build_app("")            # unset bearer -> never runs unauthenticated
    with pytest.raises(ValueError):
        kd.serve(host="127.0.0.1", bearer_sha256="")


def test_refuses_non_localhost_bind():
    for bad in ("0.0.0.0", "192.168.1.5", "::", "example.com"):
        with pytest.raises(ValueError):
            kd._require_localhost(bad)
    # serve() must reject a public host before ever binding, even with a valid bearer
    with pytest.raises(ValueError):
        kd.serve(host="0.0.0.0", bearer_sha256=SHA)
    assert kd._require_localhost("localhost") == "127.0.0.1"
    assert kd._require_localhost("127.0.0.1") == "127.0.0.1"


def test_api_without_bearer_is_401(tmp_path):
    c = _client(tmp_path / "k.json")
    assert c.get("/api/keys").status_code == 401
    assert c.post("/api/keys/issue", json={"label": "x"}).status_code == 401
    assert c.post("/api/keys/revoke", json={"key_id": "ck_x"}).status_code == 401
    # wrong bearer also 401
    assert c.get("/api/keys", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_list_is_metadata_only_no_secret_leak(tmp_path):
    from cortex_core import keys
    store = tmp_path / "k.json"
    kid, raw = keys.issue_key("secret-label", scope="read", store_path=store)
    c = _client(store)
    r = c.get("/api/keys", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    rec = body["keys"][0]
    assert rec["key_id"] == kid and rec["status"] == "active"
    assert "sha256" not in rec and "hash" not in rec
    # neither the raw key nor its stored hash appear anywhere in the response
    assert raw not in r.text and hash_token(raw) not in r.text


def test_issue_list_revoke_round_trip(tmp_path):
    store = tmp_path / "k.json"
    c = _client(store)
    # issue with a TTL -> raw shown once
    r = c.post("/api/keys/issue", json={"label": "phantomic", "scope": "read", "ttl": "30d"}, headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    kid, raw = d["key_id"], d["raw"]
    assert raw.startswith("cortex_")
    # the issued key actually verifies against the store
    from cortex_core import keys
    assert keys.verify_key(raw, store_path=store) is not None
    # list shows it active with expiry
    listed = c.get("/api/keys", headers=AUTH).json()["keys"]
    rec = next(k for k in listed if k["key_id"] == kid)
    assert rec["status"] == "active" and rec["expires_at"]
    # revoke -> gone
    assert c.post("/api/keys/revoke", json={"key_id": kid}, headers=AUTH).json()["revoked"] is True
    assert keys.verify_key(raw, store_path=store) is None
    rec2 = next(k for k in c.get("/api/keys", headers=AUTH).json()["keys"] if k["key_id"] == kid)
    assert rec2["status"] == "revoked"


def test_rotate_set_expiry_and_no_log_via_api(tmp_path):
    from cortex_core import keys
    store = tmp_path / "k.json"
    c = _client(store)
    kid = c.post("/api/keys/issue", json={"label": "a"}, headers=AUTH).json()["key_id"]
    # rotate -> new raw once, old dead
    rot = c.post("/api/keys/rotate", json={"key_id": kid}, headers=AUTH).json()
    assert rot["raw"].startswith("cortex_") and rot["key_id"] != kid
    new_id = rot["key_id"]
    # set-expiry
    assert c.post("/api/keys/set-expiry", json={"key_id": new_id, "ttl": "1d"}, headers=AUTH).json()["ok"] is True
    assert next(k for k in c.get("/api/keys", headers=AUTH).json()["keys"] if k["key_id"] == new_id)["expires_at"]
    # toggle no-log
    assert c.post("/api/keys/no-log", json={"key_id": new_id, "no_log": True}, headers=AUTH).json()["ok"] is True
    assert next(k for k in c.get("/api/keys", headers=AUTH).json()["keys"] if k["key_id"] == new_id)["no_log"] is True
    # unknown key -> 404
    assert c.post("/api/keys/set-expiry", json={"key_id": "nope", "ttl": "1d"}, headers=AUTH).status_code == 404


def test_index_page_is_self_contained_no_external_refs(tmp_path):
    c = _client(tmp_path / "k.json")
    html = c.get("/").text
    assert "<title>" in html
    # no external CDN / remote assets (same CSP discipline as the other web surfaces)
    for bad in ("http://", "https://", "//cdn", "src=\"http"):
        assert bad not in html
