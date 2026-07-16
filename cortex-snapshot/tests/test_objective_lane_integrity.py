"""Frozen, parametrized integrity test for EVERY objective lane (evals/objective_*/).

The deep, lane-specific frozen tests (test_objective_datetime_correctness.py, ...) pin a checker's
verdicts case by case. This test is the *shared floor* underneath all of them: it runs the same
mechanical invariant the CI objective-integrity gate enforces, once per lane, so a lane that has no
deep test of its own is still covered inside the pytest suite -- and so the load-bearing rule
("no judge in any verdict path, deterministic ground truth, evidence-backed promotion") can never
regress silently on a push.

The check logic lives in scripts/ci/check_objective_integrity.py; this test just exercises it, so the
standalone CI gate and the test suite can never disagree about what "clean" means.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from check_objective_integrity import check_lane  # noqa: E402
from lanes import discover_lanes  # noqa: E402

LANES = discover_lanes()


def test_lanes_are_discovered():
    assert LANES, "no evals/objective_* lanes discovered -- discovery is broken"


@pytest.mark.parametrize("lane", LANES, ids=[lane.name for lane in LANES])
def test_lane_integrity(lane):
    problems = check_lane(lane)
    assert problems == [], f"objective-integrity violations in lane '{lane.name}':\n  " + \
        "\n  ".join(problems)


@pytest.mark.parametrize("lane", LANES, ids=[lane.name for lane in LANES])
def test_lane_verdict_path_is_judge_free(lane):
    # Redundant with test_lane_integrity, but stated explicitly so the invariant is legible in
    # the test report: no promotion record may admit a judge into the verdict path.
    assert lane.judge_in_verdict_path is False, \
        f"lane '{lane.name}' has a promotion record with judge_in_verdict_path == true"
