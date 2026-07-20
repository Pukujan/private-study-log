"""Tests for the Phase 1 gate 1.4 scorecard rollup (cortex_core/scorecard.py).

Synthetic data only -- real data collection is Phase 6 scope. What's being
proven here is the hierarchical-backoff mechanism itself: task_type ->
task_family -> global, never fabricating a stat below MIN_N at any level.
"""

from __future__ import annotations

from cortex_core.scorecard import MIN_N, ScorecardRow, rollup


def _row(task_type: str, n_tasks: int, verified_success_rate: float, model: str = "claude-sonnet-5") -> ScorecardRow:
    return ScorecardRow(
        model=model,
        provider="anthropic",
        role="builder",
        task_type=task_type,
        n_tasks=n_tasks,
        verified_success_rate=verified_success_rate,
        self_report_vs_verified_gap=0.05,
        avg_input_tokens=1000.0,
        avg_output_tokens=500.0,
        avg_cost_usd=0.10,
        p50_latency_s=2.0,
        retry_rate=0.02,
        escalation_rate=0.01,
        window="7d",
        updated_at="2026-07-04T00:00:00Z",
    )


def test_exact_task_type_used_directly_when_n_meets_floor() -> None:
    rows = [_row("refactor", n_tasks=MIN_N + 5, verified_success_rate=0.9)]

    result = rollup(rows, model="claude-sonnet-5", task_type="refactor")

    assert result is not None
    assert result["level"] == "task_type"
    assert result["n_tasks"] == MIN_N + 5
    assert result["verified_success_rate"] == 0.9


def test_backs_off_to_task_family_when_exact_cell_is_sparse() -> None:
    """'refactor' alone has only 2 samples (below MIN_N), but 'refactor' +
    'bugfix' + 'feature' + 'test' all share the code_change family
    (docs/SLI-SCORECARD-SCHEMA.md §3) and together clear the floor."""
    rows = [
        _row("refactor", n_tasks=2, verified_success_rate=1.0),
        _row("bugfix", n_tasks=6, verified_success_rate=0.5),
        _row("feature", n_tasks=4, verified_success_rate=0.75),
    ]

    result = rollup(rows, model="claude-sonnet-5", task_type="refactor")

    assert result is not None
    assert result["level"] == "task_family"
    assert result["task_family"] == "code_change"
    assert result["n_tasks"] == 12  # 2 + 6 + 4


def test_family_rollup_is_n_weighted_not_a_plain_mean() -> None:
    """Hand-computed: refactor (n=2, rate=1.0) + bugfix (n=8, rate=0.5) ->
    weighted mean = (2*1.0 + 8*0.5) / 10 = 6/10 = 0.6, NOT the plain mean
    of 1.0 and 0.5 (0.75) -- a plain-mean bug would silently over-weight
    the smaller cell."""
    rows = [
        _row("refactor", n_tasks=2, verified_success_rate=1.0),
        _row("bugfix", n_tasks=8, verified_success_rate=0.5),
    ]

    result = rollup(rows, model="claude-sonnet-5", task_type="refactor")

    assert result is not None
    assert result["level"] == "task_family"
    assert result["n_tasks"] == 10
    assert result["verified_success_rate"] == 0.6


def test_backs_off_to_global_when_family_is_still_sparse() -> None:
    """'refactor' (code_change family) is sparse, and the rest of
    code_change is also too sparse to clear the floor alone -- but adding
    the unrelated knowledge_work-family 'docs' row (misc has no natural
    relation to code_change) pushes the GLOBAL total over MIN_N."""
    rows = [
        _row("refactor", n_tasks=2, verified_success_rate=0.8),
        _row("bugfix", n_tasks=3, verified_success_rate=0.6),
        _row("docs", n_tasks=6, verified_success_rate=0.95),
    ]

    result = rollup(rows, model="claude-sonnet-5", task_type="refactor")

    assert result is not None
    assert result["level"] == "global"
    assert result["n_tasks"] == 11  # 2 + 3 + 6, every row for this model


def test_returns_none_when_even_global_is_below_the_floor() -> None:
    """No fabricated stat below MIN_N at any level -- the floor is a hard
    stop, not a soft suggestion."""
    rows = [
        _row("refactor", n_tasks=1, verified_success_rate=1.0),
        _row("bugfix", n_tasks=2, verified_success_rate=0.0),
    ]

    result = rollup(rows, model="claude-sonnet-5", task_type="refactor")

    assert result is None


def test_rollup_never_leaks_rows_from_a_different_model() -> None:
    """A different model's abundant data must never backfill this model's
    sparse cell -- scorecards are per-model by definition."""
    rows = [
        _row("refactor", n_tasks=2, verified_success_rate=0.9, model="claude-sonnet-5"),
        _row("refactor", n_tasks=50, verified_success_rate=0.5, model="claude-opus-4-8"),
    ]

    result = rollup(rows, model="claude-sonnet-5", task_type="refactor")

    assert result is None  # sonnet-5's own refactor+family+global total is only 2


def test_task_type_with_no_family_mapping_skips_straight_to_global() -> None:
    """An unmapped task_type (not in TASK_FAMILIES) must not crash the
    family-lookup step -- it should skip straight to the global level."""
    rows = [
        _row("some-unmapped-type", n_tasks=3, verified_success_rate=0.7),
        _row("refactor", n_tasks=8, verified_success_rate=0.9),
    ]

    result = rollup(rows, model="claude-sonnet-5", task_type="some-unmapped-type")

    assert result is not None
    assert result["level"] == "global"
    assert result["n_tasks"] == 11
