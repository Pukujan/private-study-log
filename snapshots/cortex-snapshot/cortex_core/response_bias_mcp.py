"""`claude-bias` MCP server -- deterministic Claude/Hermes response-bias signal scanner.

STATUS (GAP I6, honest label, 2026-07-13): this is a LIVE, tested, deterministic surface
(`cortex_core.response_bias` + `ops/claude_bias_prometheus_exporter.py` + `tests/test_response_bias.py`)
but it is ADVISORY-ONLY and NOT wired into Cortex's self-learning loop: nothing in the corpus,
patterns, evaluator, calibration, or promotion path consumes its output automatically. It exists
for two consumers only -- a human calling `claude_bias_scan` for review, and a Prometheus scrape
of `claude_bias_prometheus`. It is a SEPARATE stdio MCP server (`.mcp.json` -> `claude-bias`),
not part of the main `cortex-brain` tool surface, so it adds zero tools to that surface. Kept
(not removed) because it works and has tests; recorded here as an accounted-for orphan rather than
a dead pipe -- if it is ever to feed the flywheel, wire a consumer, do not silently trust it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from cortex_core.response_bias import DEFAULT_CLAUDE_ROOT, render_prometheus, scan_roots, scan_to_dict

mcp = FastMCP("response-bias")


@mcp.tool()
def claude_bias_scan(root: str = "", limit: int = 25) -> dict[str, Any]:
    """Scan Claude/Hermes transcript JSONL for deterministic bias-signal patterns.

    `root` may be a transcript file or folder. Default scans ~/.claude/projects.
    Returns counts plus the first `limit` snippets for human review.
    """
    roots = [Path(root).expanduser()] if root else [DEFAULT_CLAUDE_ROOT]
    limit = max(0, min(int(limit or 25), 200))
    return scan_to_dict(scan_roots(roots), limit=limit)


@mcp.tool()
def claude_bias_prometheus(root: str = "") -> str:
    """Return Prometheus text exposition for the same deterministic bias-signal scan."""
    roots = [Path(root).expanduser()] if root else [DEFAULT_CLAUDE_ROOT]
    return render_prometheus(scan_roots(roots))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
