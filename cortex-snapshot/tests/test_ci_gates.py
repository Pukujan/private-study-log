"""Tests for the CI quality gates in scripts/ci/.

These run the gates inside the pytest suite (so a normal `pytest` run exercises them) in addition
to their dedicated CI steps. Covers:
  * the merge-safety gate's self-test (silent-commit-failure detection -- the 2026-07-11 incident),
  * the objective-integrity gate (no judge in any verdict path, every lane evidence-backed),
  * the lane-manifest sync gate (a new lane must be registered).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import check_objective_integrity  # noqa: E402
import generate_lane_manifest  # noqa: E402
import verify_merge  # noqa: E402
from verify_merge import is_dubious_ownership  # noqa: E402


def test_merge_safety_selftest_passes():
    # Reproduces the silent-commit-failure signature in a throwaway repo and proves the gate
    # blocks worktree removal for it, while passing a genuinely-landed merge.
    assert verify_merge.selftest() == 0


def test_dubious_ownership_detector():
    assert is_dubious_ownership("fatal: detected dubious ownership in repository at 'D:/x'")
    assert not is_dubious_ownership("")
    assert not is_dubious_ownership("some unrelated git message")


def test_objective_integrity_is_clean():
    results = check_objective_integrity.check_all()
    offenders = {lane: probs for lane, probs in results.items() if probs}
    assert not offenders, f"objective-integrity violations: {offenders}"


def test_every_objective_lane_verdict_path_is_judge_free():
    from lanes import discover_lanes  # noqa: E402
    lanes = discover_lanes()
    assert lanes, "no objective lanes discovered"
    for lane in lanes:
        assert lane.judge_in_verdict_path is False, \
            f"lane {lane.name} admits a judge into the verdict path"


def test_lane_manifest_in_sync():
    # If this fails, a lane was added/changed without regenerating the manifest:
    #   python scripts/ci/generate_lane_manifest.py
    assert generate_lane_manifest.check() == 0
