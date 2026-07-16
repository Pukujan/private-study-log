"""H2: key expiry / TTL. A key past its TTL window verifies as None (expired != active);
a not-yet-expired key verifies OK; a never-expiry key is unchanged (back-compat); the status
view distinguishes active / expired / revoked. `now` is injected so tests are deterministic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cortex_core import keys

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_never_expiry_is_default_and_unchanged(tmp_path):
    store = tmp_path / "api_keys.json"
    kid, raw = keys.issue_key("no-ttl", store_path=store)
    # far in the future, still valid -- no expiry means never
    info = keys.verify_key(raw, store_path=store, now=T0 + timedelta(days=3650))
    assert info and info["key_id"] == kid
    rec = keys.list_keys(store_path=store)[0]
    assert rec["expires_at"] is None
    assert rec["status"] == "active"


def test_not_yet_expired_verifies_ok(tmp_path):
    store = tmp_path / "api_keys.json"
    kid, raw = keys.issue_key("ttl30", ttl="30d", store_path=store, now=T0)
    # 29 days later: still inside the window
    info = keys.verify_key(raw, store_path=store, now=T0 + timedelta(days=29))
    assert info and info["key_id"] == kid


def test_expired_key_verifies_as_none(tmp_path):
    store = tmp_path / "api_keys.json"
    kid, raw = keys.issue_key("ttl30", ttl="30d", store_path=store, now=T0)
    # 31 days later: past the window -> fails closed (expired != active)
    assert keys.verify_key(raw, store_path=store, now=T0 + timedelta(days=31)) is None


def test_ttl_hours_and_absolute_date(tmp_path):
    store = tmp_path / "api_keys.json"
    _, raw_h = keys.issue_key("ttl12h", ttl="12h", store_path=store, now=T0)
    assert keys.verify_key(raw_h, store_path=store, now=T0 + timedelta(hours=11)) is not None
    assert keys.verify_key(raw_h, store_path=store, now=T0 + timedelta(hours=13)) is None
    # absolute ISO date
    _, raw_abs = keys.issue_key("abs", ttl="2026-06-01", store_path=store, now=T0)
    assert keys.verify_key(raw_abs, store_path=store, now=datetime(2026, 5, 1, tzinfo=timezone.utc)) is not None
    assert keys.verify_key(raw_abs, store_path=store, now=datetime(2026, 7, 1, tzinfo=timezone.utc)) is None


def test_status_view_active_expired_revoked(tmp_path):
    store = tmp_path / "api_keys.json"
    kid_a, _ = keys.issue_key("active", store_path=store, now=T0)
    kid_e, _ = keys.issue_key("exp", ttl="1d", store_path=store, now=T0)
    kid_r, _ = keys.issue_key("rev", store_path=store, now=T0)
    keys.revoke_key(kid_r, store_path=store)
    now = T0 + timedelta(days=2)  # kid_e is now past its window
    by_id = {r["key_id"]: r for r in keys.list_keys(store_path=store, now=now)}
    assert by_id[kid_a]["status"] == "active"
    assert by_id[kid_e]["status"] == "expired"
    assert by_id[kid_r]["status"] == "revoked"


def test_set_expiry_can_add_and_extend(tmp_path):
    store = tmp_path / "api_keys.json"
    kid, raw = keys.issue_key("late-ttl", store_path=store, now=T0)
    # add a TTL after issuance
    assert keys.set_expiry(kid, ttl="7d", store_path=store, now=T0) is True
    assert keys.verify_key(raw, store_path=store, now=T0 + timedelta(days=8)) is None
    # extend it
    assert keys.set_expiry(kid, ttl="30d", store_path=store, now=T0) is True
    assert keys.verify_key(raw, store_path=store, now=T0 + timedelta(days=8)) is not None
    # clear expiry (back to never)
    assert keys.set_expiry(kid, ttl=None, store_path=store, now=T0) is True
    assert keys.verify_key(raw, store_path=store, now=T0 + timedelta(days=3650)) is not None
    assert keys.set_expiry("nope", ttl="1d", store_path=store) is False


def test_rotate_preserves_a_fresh_window(tmp_path):
    store = tmp_path / "api_keys.json"
    kid, raw = keys.issue_key("rot", ttl="30d", store_path=store, now=T0)
    new_id, new_raw = keys.rotate_key(kid, store_path=store, now=T0 + timedelta(days=10))
    # the rotated key gets a fresh 30d window from the rotation moment, not the original
    assert keys.verify_key(new_raw, store_path=store, now=T0 + timedelta(days=39)) is not None
