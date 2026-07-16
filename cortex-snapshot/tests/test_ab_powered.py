"""GAP A1 -- frozen tests for the powered A-vs-B machinery (power + analysis).

These test the STATISTICS, not any model behaviour: textbook power values, and
recovery of an INJECTED effect on synthetic paired data. No claim about real
scaffolding is made here -- that is the separate live delivery gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCAFFOLD = HERE.parent / "evals" / "ab_cortex_scaffold"
sys.path.insert(0, str(SCAFFOLD))

import ab_analysis  # noqa: E402
import power  # noqa: E402


# --------------------------------------------------------------------------
# power.py
# --------------------------------------------------------------------------
def test_norm_ppf_matches_textbook_quantiles():
    assert power.norm_ppf(0.975) == pytest.approx(1.95996, abs=1e-3)
    assert power.norm_ppf(0.80) == pytest.approx(0.84162, abs=1e-3)
    assert power.norm_ppf(0.90) == pytest.approx(1.28155, abs=1e-3)
    assert power.norm_ppf(0.5) == pytest.approx(0.0, abs=1e-6)


def test_two_proportion_matches_textbook_n():
    # p1=0.10, p2=0.40, alpha=0.05 two-sided, power=0.80 -> ~29-30 per arm.
    r = power.n_two_proportion(0.10, 0.30, alpha=0.05, power=0.80)
    assert r.n_pairs in range(28, 32), r.asdict()


def test_mcnemar_efficient_under_low_discordance():
    # Paired design is efficient ONLY when discordance is low (high arm
    # correlation): with worsen_rate->0, McNemar N < independent two-proportion N.
    # Compare exact (pre-ceil) N to avoid integer-rounding noise; effect is
    # clearest at moderate MDE where the paired correlation buys the most.
    for mde in (0.2, 0.3):
        paired = power.n_mcnemar(0.10, mde, power=0.80, worsen_rate=0.0)
        indep = power.n_two_proportion(0.10, mde, power=0.80)
        assert paired.detail["n_exact"] < indep.detail["n_exact"], (mde, paired.detail, indep.detail)


def test_registered_n_is_conservative_max():
    reg = power.preregister(0.10, [0.3], powers=(0.80,))
    g = reg["grid"][0]
    assert g["n_registered"] == max(g["n_pairs_paired_mcnemar"], g["n_per_arm_two_proportion"])


def test_power_monotonic_in_mde_and_power():
    # Bigger effect -> fewer pairs; more power -> more pairs.
    assert power.n_mcnemar(0.10, 0.2).n_pairs > power.n_mcnemar(0.10, 0.4).n_pairs
    assert power.n_mcnemar(0.10, 0.3, power=0.90).n_pairs > \
           power.n_mcnemar(0.10, 0.3, power=0.80).n_pairs


def test_preregister_grid_shape():
    reg = power.preregister(0.10, [0.2, 0.3, 0.4], powers=(0.80, 0.90))
    assert len(reg["grid"]) == 6
    assert all("n_pairs_paired_mcnemar" in g for g in reg["grid"])


# --------------------------------------------------------------------------
# ab_analysis.py -- injected-effect recovery
# --------------------------------------------------------------------------
def _row(arm, idx, **axes):
    d = {"arm": arm, "trial_idx": idx, "metrics": {"refusal_count": 0, "loop_count": 0}}
    for k, v in axes.items():
        d[k] = {"ok": v}
    return d


def _write_jsonl(tmp_path, rows):
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def test_full_effect_recovered(tmp_path):
    # A always fails discipline, B always passes it -> delta=+1.0, CI excludes 0.
    rows = []
    for i in range(8):
        rows.append(_row("A", i, research_cited=False, task_passes=True))
        rows.append(_row("B", i, research_cited=True, task_passes=True))
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), n_boot=2000)
    assert res["n_pairs"] == 8
    ax = res["axes"]["research_cited"]
    assert ax["delta"] == 1.0
    assert ax["ci95"][0] > 0.0  # lower bound above zero -> significant lift
    assert ax["mcnemar_b_lift"] == 8 and ax["mcnemar_c_regression"] == 0
    assert ax["mcnemar_p"] < 0.05


def test_null_effect_ci_contains_zero(tmp_path):
    rows = []
    for i in range(10):
        v = bool(i % 2)
        rows.append(_row("A", i, research_cited=v))
        rows.append(_row("B", i, research_cited=v))  # identical -> delta 0
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), n_boot=2000)
    ax = res["axes"]["research_cited"]
    assert ax["delta"] == 0.0
    assert ax["ci95"][0] <= 0.0 <= ax["ci95"][1]
    assert ax["mcnemar_p"] == 1.0


def test_non_inferiority_pass_when_success_equal(tmp_path):
    rows = []
    for i in range(6):
        rows.append(_row("A", i, task_passes=True))
        rows.append(_row("B", i, task_passes=True))
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), ni_margin=0.10, n_boot=1000)
    ni = res["non_inferiority_task_success"]
    assert ni["non_inferior"] is True


def test_non_inferiority_fails_when_b_much_worse(tmp_path):
    rows = []
    for i in range(10):
        rows.append(_row("A", i, task_passes=True))
        rows.append(_row("B", i, task_passes=False))  # B tanks success
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), ni_margin=0.10, n_boot=1000)
    ni = res["non_inferiority_task_success"]
    assert ni["non_inferior"] is False


def test_safety_gate_flags_refusal_in_b(tmp_path):
    rows = [_row("A", 0, task_passes=True), _row("B", 0, task_passes=True)]
    rows[1]["metrics"]["refusal_count"] = 1
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), n_boot=200)
    assert res["safety_gate"]["zero_refusal_loop"] is False


def test_only_complete_pairs_analysed(tmp_path):
    rows = [_row("A", 0, task_passes=True), _row("B", 0, task_passes=True),
            _row("A", 1, task_passes=True)]  # idx 1 has no B -> dropped
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), n_boot=200)
    assert res["n_pairs"] == 1


def test_render_markdown_smoke(tmp_path):
    rows = [_row("A", 0, task_passes=True, research_cited=False),
            _row("B", 0, task_passes=True, research_cited=True)]
    res = ab_analysis.analyze(_write_jsonl(tmp_path, rows), n_boot=200)
    md = ab_analysis.render_markdown(res)
    assert "powered A-vs-B analysis" in md and "Safety gate" in md
