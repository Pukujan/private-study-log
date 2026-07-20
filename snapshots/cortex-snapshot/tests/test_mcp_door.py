"""RED tests (GLM-5.2, panel 2026-07-09) for P8 cortex_grade MCP door."""
from cortex_core.mcp_door import _REGISTERED_MCP_TOOLS, call_tool


def test_mcp_door_cortex_grade_registered_and_callable():
    assert "cortex_grade" in _REGISTERED_MCP_TOOLS and callable(_REGISTERED_MCP_TOOLS["cortex_grade"])


def test_mcp_door_1000_case_call_truncated_to_64():
    result = call_tool("cortex_grade", cases=[{"q": f"q{i}", "a": f"a{i}"} for i in range(1000)])
    assert result["n"] == 64 and result["truncated"] is True
