"""cortex_onboarding: the server self-describes how to operate it, generated from live sources of
truth (so it can't rot). The load-bearing test is anti-drift: EVERY registered MCP tool must be
documented in the guide -- add a tool without documenting it and this fails."""
from __future__ import annotations

import asyncio

from cortex_core import onboarding
from cortex_core.mcp import mcp, _FORCED_PIPELINE_STEPS


def _real_tool_names():
    return [t.name for t in asyncio.run(mcp.list_tools())]


def test_build_onboarding_has_the_expected_shape():
    guide = onboarding.build_onboarding(["cortex_search", "cortex_register"], ["step 1"])
    for key in ("summary", "start_here", "pipeline", "rag_flow", "tools",
                "reasoning_tiers", "task_types", "disciplines", "undocumented_tools"):
        assert key in guide
    assert guide["pipeline"] == ["step 1"]
    assert guide["tools"]["cortex_search"] and "search" in guide["tools"]["cortex_search"].lower()


def test_anti_drift_every_registered_tool_is_documented():
    # the whole point: no registered tool may be missing from the guide
    gap = onboarding.coverage_gap(_real_tool_names())
    assert gap == [], f"undocumented MCP tools (add them to onboarding.TOOL_GUIDANCE): {gap}"


def test_onboarding_covers_the_real_tools_and_the_real_pipeline():
    names = _real_tool_names()
    guide = onboarding.build_onboarding(names, _FORCED_PIPELINE_STEPS)
    assert set(guide["tools"]) == set(names)          # every real tool present
    assert guide["undocumented_tools"] == []           # and none undocumented
    assert guide["pipeline"] == list(_FORCED_PIPELINE_STEPS)
    assert "cortex_onboarding" in guide["tools"]        # the guide documents itself


def test_task_types_and_disciplines_present():
    guide = onboarding.build_onboarding([], [])
    assert guide["task_types"] and "bugfix" in guide["task_types"]
    assert any("closeout" in d.lower() for d in guide["disciplines"])


def test_cortex_onboarding_tool_returns_the_guide():
    fn = getattr(mcp_onboarding := _onboarding_fn(), "fn", mcp_onboarding)
    guide = asyncio.run(fn())
    assert guide["undocumented_tools"] == [] and "cortex_search" in guide["tools"]


def _onboarding_fn():
    from cortex_core import mcp as m
    return m.cortex_onboarding


def test_onboarding_resource_registered_and_valid_json():
    import json
    from cortex_core import mcp as m
    resources = asyncio.run(m.mcp.list_resources())
    assert any(str(r.uri) == "cortex://onboarding" for r in resources), \
        "cortex://onboarding resource not registered"
    fn = getattr(m.onboarding_resource, "fn", m.onboarding_resource)
    payload = json.loads(asyncio.run(fn()))
    assert payload["undocumented_tools"] == [] and "cortex_search" in payload["tools"]
