"""CI regex check for PHASE-GATES 0.13 (MCP/CLI naming convergence).

Claude's tool-use ``name`` field must match ``^[a-zA-Z0-9_-]{1,64}$`` — dots
are not allowed, even though MCP's own SEP-986 permits them (the client-side
constraint is stricter and binding; see ``docs/BUILD-PLAN.md``'s Phase 0
addendum). This scans every doc that currently declares a Cortex MCP tool
name and fails if any of them regress to a dot-namespaced or otherwise
non-compliant name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TOOL_NAME_SOURCES = [
    REPO_ROOT / "docs" / "BUILD-PLAN.md",
    REPO_ROOT / "templates" / "workspace-control-plane" / "contracts" / "MCP_CONTRACT.md",
    REPO_ROOT / "templates" / "workspace-control-plane" / "modules" / "mcp-evidence-controller.md",
]

TOOL_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
BACKTICKED_CORTEX_TOKEN = re.compile(r"`(cortex[._][a-zA-Z0-9_.]*)`")

# Backticked references that look like a tool name but aren't one (module
# paths, filenames) — excluded from extraction rather than asserted on.
_NON_TOOL_TOKENS = {"cortex.json"}


def _extract_declared_tool_names(text: str) -> set[str]:
    names: set[str] = set()
    for match in BACKTICKED_CORTEX_TOKEN.finditer(text):
        token = match.group(1)
        if token in _NON_TOOL_TOKENS or token.startswith("cortex_core"):
            continue
        names.add(token)
    return names


@pytest.mark.parametrize("path", TOOL_NAME_SOURCES, ids=lambda p: p.name)
def test_declared_mcp_tool_names_match_claude_tool_use_regex(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    names = _extract_declared_tool_names(text)

    assert names, (
        f"no cortex_*/cortex.* backticked tool-name tokens found in {path.name} "
        "-- if this doc's format changed, update this test's extraction rather "
        "than letting the naming gate go unchecked"
    )
    violations = sorted(n for n in names if not TOOL_NAME_REGEX.match(n))
    assert not violations, (
        f"{path.name} declares tool name(s) violating Claude's tool-use name "
        f"regex ^[a-zA-Z0-9_-]{{1,64}}$ (dots are not allowed): {violations}"
    )
