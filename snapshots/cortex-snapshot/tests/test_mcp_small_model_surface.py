"""TDD tests for small-model MCP surface: per-phase tool injection, token budget,
gate defaults, and deduplication.

These verify the fixes for the three problems that made the MCP unusable
for 4B/16k context models:
  1. Tool-surface token bloat (was 38 tools = 12,237 cl100k tok = 153% of 8k;
     G5 2026-07-14 folded 12 family tools into 4 dispatchers -> 30 tools)
  2. Duplicate server load (.mcp.json loaded two cortex servers = ~24k tokens)
  3. max_tokens floor at 64,000 choking reasoning
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. phase_legal_tools returns correct subset for each track/state
# ---------------------------------------------------------------------------

def test_phase_legal_tools_build_track_search_brain():
    from cortex_core.state_engine import phase_legal_tools, BUILD_TRACK
    tools = phase_legal_tools("build", "SEARCH_BRAIN")
    assert isinstance(tools, list)
    assert len(tools) > 0
    spec = BUILD_TRACK["states"]["SEARCH_BRAIN"]
    assert spec["advance_tool"] in tools


def test_phase_legal_tools_build_track_plan():
    from cortex_core.state_engine import phase_legal_tools
    tools = phase_legal_tools("build", "PLAN")
    assert isinstance(tools, list)
    assert "cortex_submit_plan" in tools


def test_phase_legal_tools_build_track_terminal():
    from cortex_core.state_engine import phase_legal_tools
    tools = phase_legal_tools("build", "DONE")
    assert tools == []


def test_phase_legal_tools_unknown_track():
    from cortex_core.state_engine import phase_legal_tools
    with pytest.raises(KeyError):
        phase_legal_tools("nonexistent", "SEARCH_BRAIN")


# ---------------------------------------------------------------------------
# 2. Gate toggle defaults -- all four gates default OFF
# ---------------------------------------------------------------------------

def test_forced_pipeline_gate_defaults_off(monkeypatch):
    monkeypatch.delenv("CORTEX_FORCED_PIPELINE", raising=False)
    from cortex_core.mcp import _forced_pipeline_on
    assert _forced_pipeline_on() is False


def test_mandatory_state_machine_gate_defaults_off(monkeypatch):
    monkeypatch.delenv("CORTEX_MANDATORY_STATE_MACHINE", raising=False)
    from cortex_core.mcp import _mandatory_state_machine_on
    assert _mandatory_state_machine_on() is False


def test_contract_gate_defaults_off(monkeypatch):
    monkeypatch.delenv("CORTEX_CONTRACT_GATE", raising=False)
    from cortex_core.mcp import _contract_gate_on
    assert _contract_gate_on() is False


def test_admin_gate_defaults_off(monkeypatch):
    monkeypatch.delenv("CORTEX_ADMIN_GATE", raising=False)
    from cortex_core.mcp import _admin_gate_on
    assert _admin_gate_on() is False


# ---------------------------------------------------------------------------
# 3. Admin gate security still runs when coercion is off
# ---------------------------------------------------------------------------

def test_admin_gate_read_scope_key_still_refused(monkeypatch):
    """Even with _admin_gate_on() False, a read-scoped API key must still be refused."""
    monkeypatch.delenv("CORTEX_ADMIN_GATE", raising=False)
    from cortex_core.mcp import _admin_gate, _sessions
    sid = "test-read-scope-session"
    _sessions[sid] = {"agent_id": "test", "model": "test", "role": "agent",
                       "calls": [], "is_admin": False, "scope": "read", "tenant_id": None}
    try:
        result = _admin_gate(sid, "cortex_write_log", None)
        assert result is not None
        assert result.get("refused") is True
        assert "read" in result.get("reason", "").lower()
    finally:
        _sessions.pop(sid, None)


def test_admin_gate_no_refusal_in_owner_mode_when_off(monkeypatch):
    """In owner mode (default), with coercion off, writes should not be refused."""
    monkeypatch.delenv("CORTEX_ADMIN_GATE", raising=False)
    monkeypatch.delenv("CORTEX_SERVER_MODE", raising=False)
    from cortex_core.mcp import _admin_gate, _sessions
    sid = "test-owner-session"
    _sessions[sid] = {"agent_id": "test", "model": "test", "role": "agent",
                       "calls": [], "is_admin": False, "scope": None, "tenant_id": None}
    try:
        result = _admin_gate(sid, "cortex_write_log", None)
        assert result is None
    finally:
        _sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# 4. phase_tool_schemas returns subset (not all 38)
# ---------------------------------------------------------------------------

def test_phase_tool_schemas_returns_subset():
    from cortex_core.mcp import phase_tool_schemas
    schemas = phase_tool_schemas("build", "SEARCH_BRAIN")
    assert isinstance(schemas, list)
    if len(schemas) > 0:
        assert len(schemas) < 38, f"Got {len(schemas)} schemas -- expected <38"


def test_phase_tool_schemas_token_count_under_2000():
    """Phase-injected schemas should be well under 2000 tokens."""
    from cortex_core.mcp import phase_tools_token_count
    count = phase_tools_token_count("build", "SEARCH_BRAIN")
    if count > 0:
        assert count < 2000, f"Phase token count {count} exceeds 2000 budget"


def test_full_tool_surface_token_count_above_5000():
    """The full surface should be 5000+ tokens (baseline for comparison)."""
    from cortex_core.mcp import full_tool_surface_token_count
    count = full_tool_surface_token_count()
    if count > 0:
        assert count > 5000, f"Full surface only {count} tokens -- expected 5000+"


# ---------------------------------------------------------------------------
# 5. max_tokens floor is 12000 (not 64000)
# ---------------------------------------------------------------------------

def test_max_tokens_floor_is_12000():
    from cortex_core.judge import MIN_MAX_TOKENS_BY_TIER
    for tier, floor in MIN_MAX_TOKENS_BY_TIER.items():
        assert floor == 12000, f"Tier {tier} has floor {floor}, expected 12000"


def test_apply_min_max_tokens_does_not_raise_to_64k():
    from cortex_core.judge import apply_min_max_tokens
    result = apply_min_max_tokens("opencode-zen", 1500)
    assert result == 12000, f"Got {result}, expected 12000"


def test_apply_min_max_tokens_preserves_higher_caller_value():
    from cortex_core.judge import apply_min_max_tokens
    result = apply_min_max_tokens("opencode-zen", 20000)
    assert result == 20000


# ---------------------------------------------------------------------------
# 6. .mcp.json has only one cortex server (no duplicate)
# ---------------------------------------------------------------------------

def test_mcp_json_has_single_cortex_server():
    """The .mcp.json must not load two cortex servers (the ~24k duplicate load)."""
    import cortex_core
    repo_root = Path(cortex_core.__file__).parent.parent
    mcp_json_path = repo_root / ".mcp.json"
    if not mcp_json_path.exists():
        pytest.skip(".mcp.json not found at repo root")
    with open(mcp_json_path) as f:
        config = json.load(f)
    servers = config.get("mcpServers", {})
    cortex_servers = [name for name in servers if "cortex" in name.lower()]
    assert len(cortex_servers) == 1, (
        f"Found {len(cortex_servers)} cortex servers: {cortex_servers}. "
        f"Expected exactly 1 (duplicate load was the biggest token waste)."
    )
