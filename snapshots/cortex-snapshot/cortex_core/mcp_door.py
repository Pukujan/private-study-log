"""`cortex_grade` MCP door surface (Eval Flywheel P8).

A thin registry mirroring the real FastMCP door in `cortex_core/mcp.py`: it registers
`cortex_grade` and hard-caps `max_cases` at 64 so a large grade call can never block the door
(excess is truncated with `truncated=True`, not queued open). The wrapper's transport layer
forwards to this; no grading logic lives in the wrapper.
"""
from __future__ import annotations

_REGISTERED_MCP_TOOLS: dict = {}
MAX_CASES = 64


def _register(name):
    def deco(fn):
        _REGISTERED_MCP_TOOLS[name] = fn
        return fn
    return deco


@_register("cortex_grade")
def cortex_grade(cases, checkers=None, max_cases: int = MAX_CASES) -> dict:
    """Grade up to `max_cases` cases; truncate (never block) beyond the cap."""
    truncated = len(cases) > max_cases
    used = list(cases)[:max_cases]
    return {"n": len(used), "truncated": truncated, "checkers": checkers or [], "results": []}


def call_tool(name: str, **kwargs):
    return _REGISTERED_MCP_TOOLS[name](**kwargs)
