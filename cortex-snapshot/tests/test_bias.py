"""RED tests (GLM-5.2, panel 2026-07-09) for P6 bias-detection."""
from cortex_core.bias import spearman_rho, flag_oracle_skew


def test_bias_anthropic_dialect_rho_over_threshold_flags_skew():
    rho = spearman_rho([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
    assert rho < -0.8 and flag_oracle_skew(rho, threshold=-0.6).oracle_skew is True
