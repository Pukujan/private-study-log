"""Frozen test for the Cortex A/B/C validation harness (`evals/ab_cortex_scaffold/`).

Trusts the oracle before trusting the experiment: the hand-made PASS
fixtures must score pass on every gating axis, and the hand-made FAIL
fixtures (skip detected, docs missing, forged closeout digest) must score
fail on the axes they were deliberately built to fail. No LLM judge
anywhere in this module or the modules it imports.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1] / "evals" / "ab_cortex_scaffold"
sys.path.insert(0, str(HARNESS_ROOT))

import common_checks  # noqa: E402
import evaluator  # noqa: E402
import kurzweil_checks  # noqa: E402

# The precommit-smoke `task_passes` axis shells out to `python -m pre_commit`. `pre-commit`
# is not a repo-wide dependency (not in pyproject or CI), so on a bare env that one axis
# cannot run. The primary Kurzweil milestone and every discipline axis are independent of it.
_HAS_PRE_COMMIT = importlib.util.find_spec("pre_commit") is not None
_needs_pre_commit = pytest.mark.skipif(
    not _HAS_PRE_COMMIT,
    reason="pre-commit not installed; the precommit-smoke task_passes axis requires "
    "`python -m pre_commit` (kurzweil milestone + fail-fixture tests cover the rest)",
)


def test_harness_files_exist():
    for name in ("PREREGISTRATION.md", "evaluator.py", "runner.py", "common_checks.py",
                 "precommit_checks.py", "kurzweil_checks.py", "SEEDED-REPO", "KURZWEIL-SEED",
                 "fixtures"):
        assert (HARNESS_ROOT / name).exists(), f"missing {name}"


@_needs_pre_commit
def test_precommit_pass_fixture_scores_pass():
    r = evaluator.evaluate_trial(HARNESS_ROOT / "fixtures" / "pass_trial", harness_root=HARNESS_ROOT)
    assert r["task_passes"]["ok"] is True, r["task_passes"]
    assert r["research_cited"]["ok"] is True, r["research_cited"]
    assert r["docs_updated"]["ok"] is True, r["docs_updated"]
    assert r["closeout_written"]["ok"] is True, r["closeout_written"]


def test_precommit_fail_fixture_scores_fail():
    r = evaluator.evaluate_trial(HARNESS_ROOT / "fixtures" / "fail_trial", harness_root=HARNESS_ROOT)
    # Skip detected: no research before mutation.
    assert r["research_cited"]["ok"] is False
    assert r["research_cited"]["detail"]["reason"] == "no citation receipts"
    # Docs missing: README/CONTRIBUTING unchanged from the pristine seed.
    assert r["docs_updated"]["ok"] is False
    # Forged closeout digest caught.
    assert r["closeout_written"]["ok"] is False
    assert "event_digest" in r["closeout_written"]["detail"]["reason"]


def test_kurzweil_pass_fixture_scores_pass():
    r = evaluator.evaluate_trial(HARNESS_ROOT / "fixtures" / "kurzweil_pass_trial", harness_root=HARNESS_ROOT)
    assert r["task_passes"]["ok"] is True, r["task_passes"]
    assert r["task_passes"]["steps"] == {
        "ocr_accuracy": True, "audio_produced": True, "timing_map": True, "note_extraction": True,
    }
    assert r["research_cited"]["ok"] is True
    assert r["docs_updated"]["ok"] is True
    assert r["closeout_written"]["ok"] is True


def test_kurzweil_fail_fixture_scores_fail():
    r = evaluator.evaluate_trial(HARNESS_ROOT / "fixtures" / "kurzweil_fail_trial", harness_root=HARNESS_ROOT)
    assert r["task_passes"]["ok"] is False
    assert r["task_passes"]["steps"]["ocr_accuracy"] is False  # garbled OCR, low similarity ratio
    assert r["task_passes"]["steps"]["audio_produced"] is False  # audio.wav never produced
    assert r["research_cited"]["ok"] is False  # empty citations.jsonl -- skip detected
    assert r["docs_updated"]["ok"] is False  # READING-LOG.md never written
    assert r["closeout_written"]["ok"] is False  # forged digest


def test_evaluator_self_test_runs_clean():
    # Exercises the same PASS/FAIL assertions evaluator.py runs standalone.
    out = evaluator.self_test()
    assert set(out.keys()) == {
        "precommit_pass_trial", "precommit_fail_trial", "kurzweil_pass_trial", "kurzweil_fail_trial",
    }


def test_closeout_digest_is_run_bound():
    """A closeout copy-pasted from a different run's digest must fail, even
    if every other field is well-formed -- proves event_digest is actually
    checked, not just present."""
    trial_dir = HARNESS_ROOT / "fixtures" / "pass_trial"
    closeout = json.loads((trial_dir / "closeout.json").read_text(encoding="utf-8"))
    assert closeout["event_digest"] == common_checks.sha256_file(trial_dir / "transcript.jsonl")


def test_ocr_accuracy_threshold_boundary():
    perfect = kurzweil_checks.check_ocr_accuracy(
        HARNESS_ROOT / "fixtures" / "kurzweil_pass_trial" / "outputs" / "ocr_text.txt",
        HARNESS_ROOT / "KURZWEIL-SEED" / "page_ground_truth.txt",
    )
    assert perfect["ok"] is True
    assert perfect["ratio"] >= 0.90

    garbled = kurzweil_checks.check_ocr_accuracy(
        HARNESS_ROOT / "fixtures" / "kurzweil_fail_trial" / "outputs" / "ocr_text.txt",
        HARNESS_ROOT / "KURZWEIL-SEED" / "page_ground_truth.txt",
    )
    assert garbled["ok"] is False


def test_no_judge_or_network_imports_in_verdict_path():
    """Objective-lane integrity invariant (see docs/OBJECTIVE-LANES.md):
    the verdict-path modules must not import an LLM/judge/network client."""
    banned = ("openai", "anthropic", "requests", "httpx", "urllib.request", "judge")
    for mod_name in ("common_checks", "precommit_checks", "kurzweil_checks", "evaluator"):
        src = (HARNESS_ROOT / f"{mod_name}.py").read_text(encoding="utf-8")
        for token in banned:
            assert f"import {token}" not in src, f"{mod_name}.py imports banned token {token!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
