"""Scoreboard metrics (Eval Flywheel P4).

Report N, a Wilson confidence interval, cost, latency percentiles, and — critically —
`parse_failure_rate` kept SEPARATE from `abstain_rate`. Conflating them (a model that
emits unparseable output looks like it "abstained", shrinking the denominator and
inflating accuracy) is the gaming the panel flagged. Never collapse the two.
"""
from __future__ import annotations

import math


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion (stable at small n)."""
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (center - half, center + half)


def cost(tokens_in: int, tokens_out: int, rate_in: float, rate_out: float) -> float:
    """Dollar cost of a call given per-token input/output rates."""
    return tokens_in * rate_in + tokens_out * rate_out


def latency_p(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in [0,1]) of a latency sample."""
    s = sorted(values)
    if not s:
        return 0.0
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def _rate(outs, label: str) -> float:
    return sum(1 for lbl, _ in outs if lbl == label) / len(outs) if outs else 0.0


def abstain_rate(outs) -> float:
    """Fraction of (label, correct) rows where the model semantically abstained."""
    return _rate(outs, "abstain")


def parse_failure_rate(outs) -> float:
    """Fraction of (label, correct) rows the extractor could not parse — NOT abstains."""
    return _rate(outs, "parse_fail")
