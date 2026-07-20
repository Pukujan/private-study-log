"""Fable oracle STYLE-report (Eval Flywheel P7) — explicitly OFF the gate path.

The ~10k Fable-authored oracle cases are single-vendor, so they can never promote a model.
This module emits a labeled style-alignment report only. `gating.py` imports nothing from
here (import-lint enforced). A keyword-parrot or a hedging non-answer must score low, so the
report can act as a canary for gate weakness — never as a promotion signal.
"""
from __future__ import annotations

_HEDGES = ("maybe", "perhaps", "might", "possibly", "i think", "not sure")


def style_score(case, text: str = "") -> float:
    """Low score for parroting (few unique tokens) or hedging non-answers; else lexical
    diversity as a crude style-alignment proxy. Bounded [0,1]."""
    toks = text.split()
    if not toks:
        return 0.0
    lowered = text.lower()
    if any(h in lowered for h in _HEDGES):
        return 0.2
    unique_ratio = len(set(t.lower() for t in toks)) / len(toks)
    if unique_ratio < 0.5:  # heavy repetition == parroting
        return 0.2
    return min(1.0, unique_ratio)
