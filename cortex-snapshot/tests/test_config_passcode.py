"""Local config-change passcode (CORTEX_CONFIG_PASSCODE_SHA256): a second, independent secret that
gates local config changes (repointing workspace/brain, routing/mode) so a connected agent can't
silently reconfigure the local setup to bypass the harness. Fail-closed, backward-compatible when unset."""
from __future__ import annotations

from cortex_core.authz import (
    CONFIG_PASSCODE_HASH_ENV,
    authorize_config_change,
    config_change_requires_passcode,
    hash_token,
    verify_config_passcode,
)

PASS = "correct horse battery staple"
ENV = {CONFIG_PASSCODE_HASH_ENV: hash_token(PASS)}


def test_unset_is_backward_compatible_owner_mode():
    # no passcode configured -> not required, and any change is allowed
    assert config_change_requires_passcode({}) is False
    ok, reason = authorize_config_change(None, {})
    assert ok is True and "owner" in reason


def test_configured_requires_and_verifies():
    assert config_change_requires_passcode(ENV) is True
    assert verify_config_passcode(PASS, ENV) is True
    assert authorize_config_change(PASS, ENV)[0] is True


def test_configured_refuses_wrong_or_missing():
    assert verify_config_passcode("wrong", ENV) is False
    assert verify_config_passcode(None, ENV) is False
    ok, reason = authorize_config_change("wrong", ENV)
    assert ok is False and "refused" in reason
    assert authorize_config_change(None, ENV)[0] is False  # fail-closed: no passcode -> refused


def test_verify_fail_closed_when_no_hash_even_with_token():
    # a token presented but no server-side hash configured -> never authenticates
    assert verify_config_passcode(PASS, {}) is False


def test_config_passcode_is_independent_of_admin_token():
    # setting only the admin hash must NOT satisfy the config passcode gate
    from cortex_core.authz import ADMIN_HASH_ENV
    admin_only = {ADMIN_HASH_ENV: hash_token("admintok")}
    assert config_change_requires_passcode(admin_only) is False
    assert verify_config_passcode("admintok", admin_only) is False


def _register_fn():
    """cortex_register, unwrapped if FastMCP wrapped it into a Tool."""
    from cortex_core import mcp as m
    reg = m.cortex_register
    return getattr(reg, "fn", reg)


def test_register_refuses_workspace_repoint_without_passcode(monkeypatch):
    monkeypatch.setenv(CONFIG_PASSCODE_HASH_ENV, hash_token(PASS))
    out = _register_fn()("agent", "model", workspace="D:/some/other/workspace")
    assert out.get("error") == "config_change_refused"


def test_register_unaffected_when_no_passcode_configured(monkeypatch):
    # default: no passcode -> a workspace override registers normally (no refusal)
    monkeypatch.delenv(CONFIG_PASSCODE_HASH_ENV, raising=False)
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    out = _register_fn()("agent", "model")  # no workspace override, no passcode
    assert "error" not in out and out.get("session_id")
