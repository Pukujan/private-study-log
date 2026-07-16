"""RED tests (GLM-5.2, panel 2026-07-09) for P9 distillation (trigger-gated, leakage-safe)."""
from cortex_core.distill import (
    trace_case_ids, eval_case_ids, per_vendor_trace_share, trigger_distill,
)


def test_distill_zero_overlap_trace_vs_eval():
    assert set(trace_case_ids()) & set(eval_case_ids()) == set()


def test_distill_per_vendor_trace_cap_under_40pct():
    shares = per_vendor_trace_share()
    assert all(s <= 0.40 for s in shares.values())


def test_distill_trigger_gated_only_when_gate_passes():
    assert trigger_distill(gate_passed=False) is None and trigger_distill(gate_passed=True) is not None
