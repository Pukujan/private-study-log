"""Oracle-skew bias detection (Eval Flywheel P6).

For a lane, correlate a model's per-case pass-rate with its perplexity on a fixed
vendor-neutral oracle text set. High |rho| means the lane rewards models that match the
oracle's dialect (a skew), not task capability — such a lane is flagged and demoted to
advisory. Heuristic, not proof (see honest debt); flags are advisory unless |rho| is extreme.
"""
from __future__ import annotations

from dataclasses import dataclass


def _ranks(a):
    n = len(a)
    order = sorted(range(n), key=lambda i: a[i])
    r = [0] * n
    for pos, i in enumerate(order):
        r[i] = pos + 1
    return r


def spearman_rho(x, y) -> float:
    """Spearman rank correlation of two equal-length samples."""
    n = len(x)
    if n == 0 or n != len(y):
        return 0.0
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    sy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (sx * sy) if sx * sy else 0.0


@dataclass
class SkewFlag:
    oracle_skew: bool
    rho: float


def flag_oracle_skew(rho: float, threshold: float = -0.6) -> SkewFlag:
    """Flag a lane as oracle-skewed when |rho| >= |threshold|."""
    return SkewFlag(oracle_skew=abs(rho) >= abs(threshold), rho=rho)
