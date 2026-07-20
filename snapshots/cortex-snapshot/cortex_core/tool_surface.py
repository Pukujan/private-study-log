"""Deterministic MCP tool-surface measurement — the instrument behind the
context-budget regression gate.

This computes, with **zero optional dependencies and byte-identical results on
every machine**, three numbers that guard "Disease A" (eager tool-surface
bloat, see docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md
and docs/MCP-CONTEXT-BUDGET.md):

  1. total_tool_count       -- how many @mcp.tool() are registered
  2. all_schemas_tokens     -- token footprint if ALL tool schemas were loaded
                               (the eager-load cost every connecting agent pays)
  3. per-phase exposed tokens -- footprint of only the tools progressive
                               disclosure exposes at a given chart phase, and
                               the worst-case (max) across every track/state.

Token estimate: `len(json.dumps({name, description, parameters})) // 4` -- the
~4-chars-per-token rule of thumb. Deliberately NOT a real tokenizer: a frozen
budget gate needs a deterministic, dependency-free number (tiktoken is
present-or-absent and cannot anchor a frozen ceiling). Absolutes are estimates;
the gate cares about *deltas* (surface growth), which chars/4 tracks faithfully.
See docs/MCP-CONTEXT-BUDGET.md for the full rationale and the frozen budgets.
"""
from __future__ import annotations

import json
from typing import Any

# Chars-per-token proxy. Documented, deliberate, dependency-free. Do not swap
# for a real tokenizer here -- determinism across machines/CI is the whole point.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: characters // 4. See module docstring."""
    return len(text) // _CHARS_PER_TOKEN


def _wire(name: str, description: str | None, parameters: Any) -> str:
    """The JSON wire form of a single tool schema (what a client eager-loads)."""
    return json.dumps(
        {"name": name, "description": description or "", "parameters": parameters},
        ensure_ascii=False,
        sort_keys=True,
    )


def schema_tokens(name: str, description: str | None, parameters: Any) -> int:
    """Token footprint of one tool's schema (name + description + params)."""
    return estimate_tokens(_wire(name, description, parameters))


def _registry() -> dict[str, Any]:
    """Map tool-name -> live tool object from the FastMCP registry. Importing
    cortex_core.mcp registers every @mcp.tool()."""
    from .mcp import mcp
    return {t.name: t for t in mcp._tool_manager.list_tools()}


def total_tool_count() -> int:
    """Total number of registered @mcp.tool() tools."""
    return len(_registry())


def all_schemas_tokens() -> int:
    """Token footprint of ALL registered tool schemas loaded at once -- the
    eager-load cost that Disease A imposes on every connecting agent."""
    return sum(
        schema_tokens(t.name, t.description, t.parameters)
        for t in _registry().values()
    )


def phase_exposed_tokens(track: str, state: str) -> int:
    """Token footprint of only the tools progressive disclosure exposes at
    `track`/`state` (via _phase_tool_names -> phase_legal_tools + baseline)."""
    from .mcp import _phase_tool_names
    reg = _registry()
    names = set(_phase_tool_names(track, state))
    return sum(
        schema_tokens(t.name, t.description, t.parameters)
        for n in names
        if (t := reg.get(n)) is not None
    )


def _all_track_states() -> list[tuple[str, str]]:
    from .state_engine import _TRACKS
    return [
        (track, state)
        for track, chart in _TRACKS.items()
        for state in chart["states"]
    ]


def max_phase_exposed_tokens() -> tuple[str, str, int]:
    """Worst-case per-phase exposed footprint across every track/state.
    Returns (track, state, tokens)."""
    worst = ("", "", 0)
    for track, state in _all_track_states():
        toks = phase_exposed_tokens(track, state)
        if toks > worst[2]:
            worst = (track, state, toks)
    return worst


def measure() -> dict[str, Any]:
    """One deterministic snapshot of the whole tool surface -- what the frozen
    budget gate and `cortex-tool-surface --json` both consume."""
    reg = _registry()
    per_phase = {
        f"{track}/{state}": phase_exposed_tokens(track, state)
        for track, state in _all_track_states()
    }
    worst_track, worst_state, worst_toks = max_phase_exposed_tokens()
    all_toks = all_schemas_tokens()
    return {
        "chars_per_token": _CHARS_PER_TOKEN,
        "total_tool_count": len(reg),
        "all_schemas_tokens": all_toks,
        "per_phase_tokens": per_phase,
        "max_phase_exposed": {
            "track": worst_track,
            "state": worst_state,
            "tokens": worst_toks,
        },
        # ratio of worst per-phase surface to the full surface -- the
        # "is disclosure doing its job" number (lower is better).
        "disclosure_ratio": (worst_toks / all_toks) if all_toks else 0.0,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="cortex-tool-surface",
        description="Deterministic MCP tool-surface measurement (chars/4 proxy). "
                    "See docs/MCP-CONTEXT-BUDGET.md.",
    )
    parser.add_argument("--json", action="store_true",
                        help="emit the machine-readable snapshot the budget gate freezes")
    args = parser.parse_args()

    snap = measure()
    if args.json:
        print(json.dumps(snap, indent=2, sort_keys=True))
        return

    print("MCP tool-surface (deterministic chars/4 proxy)")
    print(f"  registered @mcp.tool() count : {snap['total_tool_count']}")
    print(f"  all-schemas tokens (eager)   : {snap['all_schemas_tokens']}")
    mp = snap["max_phase_exposed"]
    print(f"  worst per-phase exposed      : {mp['tokens']} "
          f"({mp['track']}/{mp['state']})")
    print(f"  disclosure ratio (worst/all) : {snap['disclosure_ratio']:.1%} "
          f"(lower = disclosure working)")
    print()
    print("  per-phase exposed tokens:")
    for phase, toks in sorted(snap["per_phase_tokens"].items(),
                              key=lambda kv: -kv[1]):
        print(f"    {toks:6}  {phase}")


if __name__ == "__main__":
    main()
