from __future__ import annotations

import pytest

from cortex_core import authz
from cortex_core.mcp import _admin_gate, _sessions, cortex_register


# ---- authz primitive -------------------------------------------------------

def test_hash_token_is_deterministic_sha256() -> None:
    # known SHA-256 of "secret"
    assert authz.hash_token("secret") == (
        "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b"
    )


def test_resolve_server_mode_defaults_to_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    assert authz.resolve_server_mode() == authz.MODE_OWNER
    # anything unrecognized also falls back to owner -- never silently "served"
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "garbage")
    assert authz.resolve_server_mode() == authz.MODE_OWNER


def test_resolve_server_mode_served(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    assert authz.resolve_server_mode() == authz.MODE_SERVED
    assert authz.mutation_requires_admin() is True


def test_verify_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("correct-horse"))
    assert authz.verify_admin_token("correct-horse") is True
    assert authz.verify_admin_token("wrong") is False
    assert authz.verify_admin_token("") is False
    assert authz.verify_admin_token(None) is False


def test_verify_admin_token_fails_closed_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    # no admin hash configured -> no token can ever be valid (never fail-open)
    monkeypatch.delenv(authz.ADMIN_HASH_ENV, raising=False)
    assert authz.verify_admin_token("anything") is False


# ---- MCP register + gate ---------------------------------------------------

def test_owner_mode_allows_writes_without_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    # default (owner) mode: the local owner is implicitly admin, gate is a no-op
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    reg = cortex_register(agent_id="a", model="m")
    sid = reg["session_id"]
    assert reg["server_mode"] == "owner"
    assert _admin_gate(sid, "cortex_write_log", None) is None


def test_served_mode_blocks_non_admin_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("s3cret"))
    monkeypatch.setenv("CORTEX_ADMIN_GATE", "1")  # opt in to coercion behavior
    reg = cortex_register(agent_id="a", model="m")  # no admin token
    sid = reg["session_id"]
    assert reg["is_admin"] is False
    refusal = _admin_gate(sid, "cortex_write_log", None)
    assert refusal is not None
    assert refusal["refused"] is True
    assert "admin authentication" in refusal["reason"]


def test_served_mode_allows_admin_authenticated_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("s3cret"))
    reg = cortex_register(agent_id="a", model="m", admin_token="s3cret")
    sid = reg["session_id"]
    assert reg["is_admin"] is True
    assert _admin_gate(sid, "cortex_write_log", None) is None


def test_served_mode_wrong_token_is_not_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("s3cret"))
    monkeypatch.setenv("CORTEX_ADMIN_GATE", "1")  # opt in to coercion behavior
    reg = cortex_register(agent_id="a", model="m", admin_token="guess")
    assert reg["is_admin"] is False
    refusal = _admin_gate(reg["session_id"], "cortex_write_log", None)
    assert refusal is not None
    assert refusal["refused"] is True


def teardown_function() -> None:
    # keep the process-local session registry from leaking between tests
    _sessions.clear()
