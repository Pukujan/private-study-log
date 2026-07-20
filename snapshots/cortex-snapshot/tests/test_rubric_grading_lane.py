"""Frozen tests for evals/objective_rubric_grading/ -- the lane that wires the five shared
checker-cores (evals/checker_cores/) into deterministic graders for six Fable-calibrated
soft-anchor domains and measures agreement with her recorded verdicts.

Stage-2 contract, applied here: for each domain grader, a correct/honest constructed artifact
must grade PASS, a theater/violation constructed artifact must grade FAIL, and an artifact
missing the required extractable shape must ABSTAIN (None) rather than guess. Fixtures here are
small and constructed (NOT read from calibration/anchors/*.jsonl) so this suite stays stable
regardless of anchor-file edits -- it tests the WIRING has teeth, not this run's measured kappa
(that is reported separately in evals/objective_rubric_grading/REPORT.md and is expected to
drift as anchors/lexicons evolve).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_rubric_grading import extract  # noqa: E402
from evals.objective_rubric_grading.gold import binarize_criterion  # noqa: E402
from evals.objective_rubric_grading.graders import (  # noqa: E402
    grade_actionable_item,
    grade_code_arch,
    grade_deep_audit,
    grade_deep_research,
    grade_success_metric,
    grade_test_quality,
)
from evals.oracle_adapter import verdict_path_is_judge_free  # noqa: E402


# =========================================================================== structural: no judge

def test_grading_lane_verdict_path_is_judge_free():
    modules = [
        ROOT / "evals" / "objective_rubric_grading" / "graders.py",
        ROOT / "evals" / "objective_rubric_grading" / "extract.py",
        ROOT / "evals" / "objective_rubric_grading" / "gold.py",
        ROOT / "evals" / "objective_rubric_grading" / "run.py",
    ]
    clean, problems = verdict_path_is_judge_free(modules)
    assert clean, problems


# =========================================================================== deep_research (DR1+DR3)

def test_deep_research_faithful_single_source_passes():
    text = (
        "CLAIM: 'Roughly a sixth of tokens were saved. Single-source on this point.'\n"
        "CITED CHUNK 1 (verbatim): 'We cut retrieved tokens by 16.7% on the golden set.'"
    )
    pred, checks = grade_deep_research(text)
    assert pred == "PASS", checks


def test_deep_research_fabricated_numeral_fails():
    text = (
        "CLAIM: 'The change cut tokens by 34%.'\n"
        "CITED CHUNK 1 (verbatim): 'We observed a modest reduction, magnitude unspecified.'"
    )
    pred, checks = grade_deep_research(text)
    assert pred == "FAIL", checks


def test_deep_research_consensus_register_without_corroboration_fails():
    text = (
        "CLAIM: 'It is well established that this approach works.'\n"
        "Corroboration stats (run record): claim backed by 1 fetched source; 0 corroborating sources.\n"
        "CITED CHUNK 1 (verbatim): 'In our internal evaluation this improved results.'"
    )
    pred, checks = grade_deep_research(text)
    assert pred == "FAIL", checks


def test_deep_research_no_chunk_marker_abstains():
    text = "RESEARCH QUESTION: 'Should we adopt X?' SQ1: 'Is X worth it?' Coverage: 1/1 answered."
    pred, checks = grade_deep_research(text)
    assert pred is None
    assert "reason" in checks


# =========================================================================== deep_audit (DA1+DA2)

def test_deep_audit_fresh_behavioral_evidence_passes():
    text = (
        "AUDIT: closeout claims a fix. Audit re-ran `pytest tests/test_x.py -k y` at HEAD: "
        "3 passed, exit 0. VERIFIED."
    )
    pred, checks = grade_deep_audit(text)
    assert pred == "PASS", checks


def test_deep_audit_empty_selection_theater_fails():
    text = (
        "AUDIT: re-ran the named tests fresh: `pytest tests/test_x.py -k y -q` -> "
        "'no tests ran in 0.02s' (exit 5). Also ran without the filter: 41 passed. VERIFIED."
    )
    pred, checks = grade_deep_audit(text)
    assert pred == "FAIL", checks


def test_deep_audit_prose_only_confirmation_fails():
    text = "AUDIT: the closeout's tests field confirms 152 passed. VERIFIED. No discrepancies."
    pred, checks = grade_deep_audit(text)
    assert pred == "FAIL", checks


# =========================================================================== success_metric (SP-lite + SB4)

def test_success_metric_full_disciplined_proposal_passes():
    text = (
        "METRIC PROPOSAL: recall@5 gates retrieval changes. Goal: retrieval quality. "
        "Reported with nDCG in the same eval output. Cost: seconds, $0. "
        "Review: any set edit re-baselines. Level: per-phase. "
        "Gaming analysis: regressional risk caught by the named regression-test suite."
    )
    pred, checks = grade_success_metric(text)
    assert pred == "PASS", checks


def test_success_metric_ceremony_catcher_fails():
    text = (
        "METRIC PROPOSAL: adoption rate. Goal: context discipline. Cost: free. Review: quarterly. "
        "Paired with a secondary metric reported alongside it. "
        "GAMING-VECTOR ANALYSIS: we will monitor task-mix effects on an ongoing basis."
    )
    pred, checks = grade_success_metric(text)
    assert pred == "FAIL", checks


def test_success_metric_missing_required_fields_fails():
    text = "METRIC PROPOSAL: corpus size, trended upward. Goal: knowledge accumulation."
    pred, checks = grade_success_metric(text)
    assert pred == "FAIL", checks


# =========================================================================== actionable_item (AB7)

def test_actionable_item_named_test_verification_passes():
    text = "ITEM: fix the bug. Verification: new regression test in tests/test_bug.py asserting the fix."
    pred, checks = grade_actionable_item(text)
    assert pred == "PASS", checks


def test_actionable_item_walkthrough_ceremony_fails():
    text = "PLAN: migrate the schema. Oracle strategy: structured walkthrough where the engineer demonstrates the migrated sample in a recorded session."
    pred, checks = grade_actionable_item(text)
    assert pred == "FAIL", checks


def test_actionable_item_no_verification_clause_abstains():
    text = "ITEM: make the fetch path more robust against malformed responses."
    pred, checks = grade_actionable_item(text)
    assert pred is None


# =========================================================================== code_arch (CB9)

def test_code_arch_mirrored_magic_number_flags():
    text = (
        "--- core/packs.py\n"
        "+RELEVANCE_FLOOR = 0.6180339\n"
        "+    if pack_score < RELEVANCE_FLOOR:\n"
        "+        raise LowRelevancePack(pack_score)\n"
        "--- tests/test_packs.py\n"
        "+def test_floor_value():\n"
        "+    assert packs.RELEVANCE_FLOOR == 0.6180339\n"
    )
    pred, checks = grade_code_arch(text)
    assert pred == "FAIL", checks
    assert checks["flagged_without_derivation"] == 1


def test_code_arch_no_shared_literal_passes():
    text = (
        "--- core/search.py\n"
        "+def hybrid_search(q, k=10):\n"
        "+    return rrf_fuse(q, k)\n"
        "--- tests/test_search.py\n"
        "+def test_hybrid_returns_results():\n"
        "+    assert hybrid_search('q', k=10)\n"
    )
    pred, checks = grade_code_arch(text)
    assert pred == "PASS", checks


def test_code_arch_no_diff_markers_abstains():
    pred, checks = grade_code_arch("A prose description of a change with no diff markers at all.")
    assert pred is None


# =========================================================================== test_quality (B5-P2 proxy)

def test_test_quality_captured_decimal_flags():
    text = (
        "def test_rrf_score_values():\n"
        "    assert rrf_score(rank=3) == 0.015873015873015872\n"
    )
    pred, checks = grade_test_quality(text)
    assert pred == "FAIL", checks
    assert checks["long_decimal_equalities_found"]


def test_test_quality_symbolic_derivation_abstains():
    text = (
        "def test_rrf_score_matches_definition():\n"
        "    K = 60  # RRF constant per the design doc\n"
        "    assert rrf_score(rank=3) == 1 / (K + 3)\n"
    )
    pred, checks = grade_test_quality(text)
    assert pred is None  # absence of the smell is not positive evidence -- honest abstain


# =========================================================================== gold.py binarizer

def test_binarize_criterion_veto_and_marginal():
    assert binarize_criterion(0) == "FAIL"
    assert binarize_criterion(3) == "PASS"
    assert binarize_criterion(2) == "PASS"
    assert binarize_criterion(1) is None
    assert binarize_criterion("PASS -- clean") == "PASS"
    assert binarize_criterion("FAIL -- no evidence") == "FAIL"
    assert binarize_criterion("MARGINAL-PASS -- borderline") == "PASS"
    assert binarize_criterion("something else") is None


def test_extract_split_variants_and_binarize_verdict():
    assert extract.binarize_verdict("pass (6/6, vc6 marginal)") == "PASS"
    assert extract.binarize_verdict("fail (dq1=0 veto)") == "FAIL"
    assert extract.binarize_verdict("BLOCKED (Layer 1, provenance_freshness)") is None
    a, b = extract.binarize_pair_verdict("A=pass(dq1), B=fail(dq1=0)")
    assert a == "PASS" and b == "FAIL"


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", str(Path(__file__)), "-q"]))
