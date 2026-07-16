from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Phase 1, gate 1.4 (docs/SLI-SCORECARD-SCHEMA.md): the model_scorecards
# schema + hierarchical-backoff rollup mechanism. Real data collection is
# Phase 6 scope (OTel plane, gateway records) -- this proves the mechanism
# against synthetic data, which is exactly what the gate asks for.

MIN_N = 10

# Placeholder task_type -> task_family grouping (docs/SLI-SCORECARD-SCHEMA.md
# §3). Phase 5.3 owns the frozen, evidence-grown taxonomy; this exists only
# to prove backoff works across two real levels.
TASK_FAMILIES: dict[str, str] = {
    "refactor": "code_change",
    "bugfix": "code_change",
    "feature": "code_change",
    "test": "code_change",
    "docs": "knowledge_work",
    "review": "knowledge_work",
    "research": "knowledge_work",
    "misc": "misc",
}


@dataclass(frozen=True)
class ScorecardRow:
    """One model_scorecards row (docs/SLI-SCORECARD-SCHEMA.md §2)."""

    model: str
    provider: str
    role: str
    task_type: str
    n_tasks: int
    verified_success_rate: float
    self_report_vs_verified_gap: float
    avg_input_tokens: float
    avg_output_tokens: float
    avg_cost_usd: float
    p50_latency_s: float
    retry_rate: float
    escalation_rate: float
    window: str
    updated_at: str


_WEIGHTED_FIELDS = (
    "verified_success_rate",
    "self_report_vs_verified_gap",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_cost_usd",
    "p50_latency_s",
    "retry_rate",
    "escalation_rate",
)


def _aggregate(rows: list[ScorecardRow]) -> dict[str, Any]:
    """n-weighted mean of every rate/average field across rows, plus a
    summed n_tasks -- the correct way to combine per-cell aggregates
    without re-deriving from raw per-task data (which this rollup
    deliberately doesn't require)."""
    total_n = sum(r.n_tasks for r in rows)
    combined: dict[str, Any] = {"n_tasks": total_n}
    for field_name in _WEIGHTED_FIELDS:
        if total_n == 0:
            combined[field_name] = 0.0
        else:
            combined[field_name] = (
                sum(getattr(r, field_name) * r.n_tasks for r in rows) / total_n
            )
    return combined


def rollup(
    rows: list[ScorecardRow], model: str, task_type: str, min_n: int = MIN_N
) -> dict[str, Any] | None:
    """Hierarchical backoff (docs/SLI-SCORECARD-SCHEMA.md §4):
    task_type -> task_family -> global, never returning a cell below
    min_n at any level. Returns None if even the global aggregate for
    this model is too sparse -- a caller must never fabricate a stat
    below the floor."""
    model_rows = [r for r in rows if r.model == model]

    exact = [r for r in model_rows if r.task_type == task_type]
    exact_agg = _aggregate(exact)
    if exact_agg["n_tasks"] >= min_n:
        return {"level": "task_type", "task_type": task_type, **exact_agg}

    family = TASK_FAMILIES.get(task_type)
    if family is not None:
        family_rows = [r for r in model_rows if TASK_FAMILIES.get(r.task_type) == family]
        family_agg = _aggregate(family_rows)
        if family_agg["n_tasks"] >= min_n:
            return {"level": "task_family", "task_family": family, **family_agg}

    global_agg = _aggregate(model_rows)
    if global_agg["n_tasks"] >= min_n:
        return {"level": "global", **global_agg}

    return None
