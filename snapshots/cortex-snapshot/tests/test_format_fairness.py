"""RED tests (GLM-5.2, panel 2026-07-09) for P5 format-fairness."""
from cortex_core.format_fairness import grade_contracts, adapter_bias, gate_min


def test_format_fairness_swing_over_15pp_flags_adapter_bias_suspect():
    scores = {"json": 0.90, "xml": 0.70, "yaml": 0.85}
    assert adapter_bias(scores) > 0.15 and grade_contracts(scores).adapter_bias_suspect is True


def test_format_fairness_suspect_not_promotable():
    scores = {"json": 0.95, "xml": 0.60}
    g = grade_contracts(scores)
    assert g.promotable is False


def test_format_fairness_gate_uses_min_not_max():
    scores = {"json": 0.95, "xml": 0.60, "yaml": 0.80}
    assert gate_min(scores, bar=0.7).pass_ is False and gate_min(scores, bar=0.5).pass_ is True
