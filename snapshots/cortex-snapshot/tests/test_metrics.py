"""RED tests (GLM-5.2, panel 2026-07-09) for P4 metrics."""
import pytest

from cortex_core.metrics import wilson_ci, cost, latency_p, abstain_rate, parse_failure_rate


def test_metrics_wilson_ci_width_under_0_1_for_n200():
    lo, hi = wilson_ci(180, 200)
    assert (hi - lo) == pytest.approx(0.083, abs=0.02) and (hi - lo) <= 0.1


def test_metrics_parse_failure_separate_from_abstain():
    outs = [("abstain", False), ("parse_fail", False), ("correct", True)]
    assert parse_failure_rate(outs) == 1 / 3 and abstain_rate(outs) == 1 / 3


def test_metrics_cost_latency_p50_p95():
    assert cost(tokens_in=100, tokens_out=50, rate_in=1e-6, rate_out=2e-6) == pytest.approx(2e-4)
    assert latency_p([10, 20, 30, 40, 100], 0.5) == 30 and latency_p([10, 20, 30, 40, 100], 0.95) == 100
