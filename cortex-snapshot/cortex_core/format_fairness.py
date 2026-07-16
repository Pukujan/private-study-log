"""Format-fairness (Eval Flywheel P5).

A model graded under only one output contract can be penalized for formatting, not
capability. Grade each model under >=2 contracts; `adapter_bias` = max-min accuracy across
them. A large swing means the ADAPTER is biased, not the model — that model is not promotable
until the swing is explained. The gate uses the MIN across contracts (robust-to-all), never
the max (which would let a model cherry-pick its best format).
"""
from __future__ import annotations

from dataclasses import dataclass

_ADAPTER_BIAS_THRESHOLD = 0.15


def adapter_bias(scores: dict) -> float:
    v = list(scores.values())
    return (max(v) - min(v)) if v else 0.0


@dataclass
class ContractGrade:
    bias: float
    adapter_bias_suspect: bool
    promotable: bool


def grade_contracts(scores: dict, threshold: float = _ADAPTER_BIAS_THRESHOLD) -> ContractGrade:
    b = adapter_bias(scores)
    suspect = b > threshold
    return ContractGrade(bias=b, adapter_bias_suspect=suspect, promotable=not suspect)


@dataclass
class MinGate:
    pass_: bool


def gate_min(scores: dict, bar: float) -> MinGate:
    """Gate on the WORST contract score, so a passing model is robust to all of them."""
    return MinGate(pass_=(min(scores.values()) >= bar) if scores else False)
