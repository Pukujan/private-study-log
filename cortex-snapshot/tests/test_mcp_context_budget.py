"""FROZEN regression gate against MCP tool-surface + context bloat ("Disease A").

The whole progressive-disclosure / skill-tree design exists to stop all 30
cortex_* MCP tools (~10k tokens) from eager-loading into every agent's context.
This test FREEZES the current surface as MAX budgets. A change that adds an
always-loaded tool or bloats a schema pushes a metric over its ceiling and
FAILS here.

Raising a budget is a deliberate, reviewed act: edit the constant below AND
justify it (see docs/MCP-CONTEXT-BUDGET.md). Never a silent bump -- that would
be the exact regression this gate exists to prevent.

Numbers are the deterministic chars/4 proxy from cortex_core.tool_surface
(dependency-free, byte-identical across machines/CI). See that module and
docs/MCP-CONTEXT-BUDGET.md for the estimate rationale, and
docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md for the
~12k/server finding that motivates the whole thing.
"""
from __future__ import annotations

from cortex_core import tool_surface

# ---------------------------------------------------------------------------
# FROZEN BUDGETS -- re-frozen 2026-07-14 after G5 consolidation (chars/4 proxy),
# + documented headroom. Do NOT raise without an explicit justification + review
# (docs/MCP-CONTEXT-BUDGET.md). Lowering these is the GOOD direction (G5 did).
# ---------------------------------------------------------------------------

# G5 (2026-07-14): four action-arg dispatchers folded 12 always-loaded family
# tools -> 4, dropping the registered surface 38 -> 30. Re-frozen at 30 with
# +2 headroom for a genuinely new always-loaded core tool (which should be rare
# -- prefer an action-arg dispatcher or phase-gating; see the doc's rule).
# (Was 40, when the surface was 38.)
MAX_TOOL_COUNT = 32

# G5 measured 10,033 tok; assurance/catalog hardening now measures 10,743 after
# folding verification back into an existing dispatcher. The 11,000 ceiling was
# not raised. This is the cost a client that does NOT
# do per-phase injection (Claude-Code-over-MCP) pays on connect; it grows with
# every registered tool, phase-gated or not, so it is the ceiling on total
# registered surface. (Was 12,500.)
MAX_ALL_SCHEMAS_TOKENS = 11_000

# Measured worst phase = research/FETCH at 1,742 tok. Headroom is 58 tokens.
# This is the number that MUST stay tiny -- it is what a phased client actually
# rests on. New phase-gated capability must not blow this.
MAX_PHASE_EXPOSED_TOKENS = 1_800


def test_tool_count_within_budget():
    count = tool_surface.total_tool_count()
    assert count <= MAX_TOOL_COUNT, (
        f"Registered MCP tool count {count} exceeds frozen budget "
        f"{MAX_TOOL_COUNT}. A new always-loaded @mcp.tool() is the regression "
        f"this gate blocks -- fold it into an action-arg dispatcher or "
        f"phase-gate it (docs/MCP-CONTEXT-BUDGET.md), or justify + raise the "
        f"budget in the open."
    )


def test_all_schemas_tokens_within_budget():
    toks = tool_surface.all_schemas_tokens()
    assert toks <= MAX_ALL_SCHEMAS_TOKENS, (
        f"All-schemas token footprint {toks} exceeds frozen budget "
        f"{MAX_ALL_SCHEMAS_TOKENS} (chars/4). Either a new tool was added or a "
        f"schema was bloated. Shrink it, move capability behind disclosure, or "
        f"justify + raise the budget (docs/MCP-CONTEXT-BUDGET.md)."
    )


def test_worst_phase_exposed_tokens_within_budget():
    track, state, toks = tool_surface.max_phase_exposed_tokens()
    assert toks <= MAX_PHASE_EXPOSED_TOKENS, (
        f"Worst per-phase exposed surface is {toks} tok at {track}/{state}, "
        f"over frozen budget {MAX_PHASE_EXPOSED_TOKENS}. Progressive disclosure "
        f"must keep every phase tiny -- a phase is exposing too many/too-large "
        f"tools (docs/MCP-CONTEXT-BUDGET.md)."
    )


def test_every_phase_under_budget():
    """No single track/state may exceed the per-phase ceiling."""
    snap = tool_surface.measure()
    over = {p: t for p, t in snap["per_phase_tokens"].items()
            if t > MAX_PHASE_EXPOSED_TOKENS}
    assert not over, f"Phases over the per-phase budget: {over}"


def test_progressive_disclosure_measurably_shrinks_surface():
    """The skill-tree must be doing its job: the WORST-case per-phase surface
    must be well under half the all-tools surface. (Measured: ~16.2%, ~6.2x cut.)"""
    all_toks = tool_surface.all_schemas_tokens()
    _, _, worst_phase_toks = tool_surface.max_phase_exposed_tokens()
    assert worst_phase_toks < all_toks / 2, (
        f"Progressive disclosure is NOT shrinking the surface: worst phase "
        f"{worst_phase_toks} tok vs all-tools {all_toks} tok. Disclosure must "
        f"cut the per-phase surface to well under half of the full surface."
    )


def test_disclosure_ratio_reported():
    """Sanity: the disclosure ratio is a real fraction in (0, 1)."""
    snap = tool_surface.measure()
    ratio = snap["disclosure_ratio"]
    assert 0.0 < ratio < 1.0, f"disclosure_ratio {ratio} not a proper fraction"


def test_estimate_is_deterministic_chars_over_4():
    """The estimate must stay the documented, dependency-free chars/4 -- so the
    frozen budget means the same thing on every machine and in CI."""
    assert tool_surface.estimate_tokens("a" * 40) == 10
    assert tool_surface.estimate_tokens("") == 0
    assert tool_surface.estimate_tokens("abc") == 0  # 3 // 4
