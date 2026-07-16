"""Third-party-only promotion gate (Eval Flywheel P3).

The gate reads ONLY the `third_party` lane — a model that aces Fable-authored cases but
fails third-party ones is NOT promoted. The bar comes from `bars.json` (computed once from a
multi-vendor open-source distribution, never from the vendor being graded, never from Claude).
Pass = accuracy >= bar AND abstain <= cap AND parse_failure <= cap.

This module MUST NOT import the Fable style-report module (P7): the single-vendor style
report can never reach the gate path. An import-lint test enforces that (it greps this file),
so the forbidden token must not appear here at all.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class GateDecision:
    pass_: bool
    reason: str = ""


def decide(scoreboard, bar=None, caps=None, bars_path=None) -> GateDecision:
    """Decide promotion from a `{lane: {accuracy, abstain, parse_failure}}` scoreboard."""
    caps = caps or {}
    tp = scoreboard.get("third_party")
    if tp is None:
        return GateDecision(pass_=False, reason="no third_party lane")

    if bar is None and bars_path:
        bar = json.loads(open(bars_path, encoding="utf-8").read()).get("default")
    if bar is None:
        bar = 0.8

    acc = tp.get("accuracy", 0.0)
    abstain = tp.get("abstain", 0.0)
    parse_failure = tp.get("parse_failure", 0.0)

    if acc < bar:
        return GateDecision(False, f"accuracy {acc:.3f} < bar {bar:.3f}")
    if abstain > caps.get("abstain", 1.0):
        return GateDecision(False, "abstain over cap")
    if parse_failure > caps.get("parse_failure", 1.0):
        return GateDecision(False, "parse_failure over cap")
    return GateDecision(True, "pass")
