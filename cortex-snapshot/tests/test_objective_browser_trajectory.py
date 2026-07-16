"""Frozen tests for the objective browser-trajectory-correctness lane (Stage-2 style).

LABEL AUTHORITY: a stdlib-only trajectory-comparison checker (checker_browser_trajectory.
check_trajectory / check_record) decides pass/fail via six independent, individually-named checks
-- legal_actions, valid_elements, on_task_domain, no_redundant_loop, step_efficiency,
sequence_match -- never a model/judge. These tests:
  1. pin the checker on hand-picked cases (independent of the fixture file) -- a correct reference
     trajectory PASSES, and each of the eight named mutation classes (wrong selector, hallucinated
     element, illegal action, wrong value, redundant loop, off-task domain, missing step, step
     inflation) FAILS via its specific check;
  2. sweep every fixture in fixtures_browser_trajectory.py asserting the checker's objective_label
     matches the fixture's declared expected_label (the self-validation gate every Stage-2 lane
     uses) and that CORRECT/INCORRECT fixtures share their `gold` task per source_task_id
     (mutation-integrity);
  3. exercise the oracle-adapter conformance shim (runtime_browser_trajectory.BrowserTrajectoryOracle)
     end to end on both a correct and a broken run.

Written before checker_browser_trajectory.py was trusted (SDD then TDD): this file pins the
contract stated in SPEC.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_browser_trajectory.checker_browser_trajectory import (  # noqa: E402
    check_record,
    check_trajectory,
)
from evals.objective_browser_trajectory.fixtures_browser_trajectory import FIXTURES  # noqa: E402
from evals.objective_browser_trajectory.runtime_browser_trajectory import (  # noqa: E402
    BrowserTrajectoryOracle,
)


# --- hand-picked reference trajectory, independent of the fixture file -------------------------

GOLD = {
    "website": "exploretock", "domain": "Travel", "task": "book a pickup reservation",
    "actions": [
        {"op": "SELECT", "target_id": "136", "value": "Pickup"},
        {"op": "CLICK", "target_id": "6068", "value": None},
        {"op": "TYPE", "target_id": "6068", "value": "Boston"},
    ],
    "valid_elements_by_step": [
        ["136", "589", "590"],
        ["6068", "6077", "6530"],
        ["6068", "6077", "6530"],
    ],
}


def test_correct_reference_trajectory_passes():
    candidate = [dict(a) for a in GOLD["actions"]]
    r = check_trajectory(GOLD, candidate)
    assert r.objective_label == "CORRECT"
    assert all(r.checks.values())


def test_wrong_selector_fails_sequence_match_only():
    candidate = [dict(a) for a in GOLD["actions"]]
    candidate[1] = {"op": "CLICK", "target_id": "6077", "value": None}  # real, on-page decoy
    r = check_trajectory(GOLD, candidate)
    assert r.objective_label == "INCORRECT"
    assert r.checks["valid_elements"] is True   # a real element -- just the WRONG one
    assert r.checks["sequence_match"] is False


def test_hallucinated_element_fails_valid_elements():
    candidate = [dict(a) for a in GOLD["actions"]]
    candidate[0] = {"op": "SELECT", "target_id": "no-such-node", "value": "Pickup"}
    r = check_trajectory(GOLD, candidate)
    assert r.checks["valid_elements"] is False
    assert r.objective_label == "INCORRECT"


def test_illegal_action_fails_legal_actions():
    candidate = [dict(a) for a in GOLD["actions"]]
    candidate[1] = {"op": "DRAG", "target_id": "6068", "value": None}
    r = check_trajectory(GOLD, candidate)
    assert r.checks["legal_actions"] is False
    assert r.objective_label == "INCORRECT"


def test_redundant_loop_fails_no_redundant_loop():
    candidate = [GOLD["actions"][0]] * 3 + [dict(a) for a in GOLD["actions"][1:]]
    r = check_trajectory(GOLD, candidate)
    assert r.checks["no_redundant_loop"] is False
    assert r.objective_label == "INCORRECT"


def test_off_task_domain_fails_on_task_domain():
    candidate = [dict(a, domain="Shopping") for a in GOLD["actions"]]
    r = check_trajectory(GOLD, candidate)
    assert r.checks["on_task_domain"] is False
    assert r.objective_label == "INCORRECT"


def test_matching_domain_passes_on_task_domain():
    candidate = [dict(a, domain="Travel") for a in GOLD["actions"]]
    r = check_trajectory(GOLD, candidate)
    assert r.checks["on_task_domain"] is True
    assert r.objective_label == "CORRECT"


def test_missing_step_fails_sequence_match():
    candidate = [dict(GOLD["actions"][0]), dict(GOLD["actions"][2])]  # dropped the middle step
    r = check_trajectory(GOLD, candidate)
    assert r.checks["sequence_match"] is False
    assert r.objective_label == "INCORRECT"


def test_step_inflation_fails_step_efficiency():
    candidate = [dict(a) for a in GOLD["actions"]] + [
        {"op": "CLICK", "target_id": "589", "value": None},
        {"op": "CLICK", "target_id": "590", "value": None},
        {"op": "CLICK", "target_id": "6530", "value": None},
    ]
    r = check_trajectory(GOLD, candidate)
    assert r.checks["step_efficiency"] is False
    assert r.objective_label == "INCORRECT"


def test_wrong_typed_value_fails_sequence_match():
    candidate = [dict(a) for a in GOLD["actions"]]
    candidate[2] = {"op": "TYPE", "target_id": "6068", "value": "Chicago"}
    r = check_trajectory(GOLD, candidate)
    assert r.checks["sequence_match"] is False
    assert r.checks["valid_elements"] is True  # still a real, valid element
    assert r.objective_label == "INCORRECT"


def test_extra_step_beyond_gold_length_fails_sequence_match_even_if_all_valid():
    # a candidate that reaches gold's exact prefix then adds one more otherwise-legal step is
    # still INCORRECT: length must match gold exactly for sequence_match (see SPEC honest limits).
    candidate = [dict(a) for a in GOLD["actions"]] + [
        {"op": "CLICK", "target_id": "589", "value": None},
    ]
    r = check_trajectory(GOLD, candidate)
    assert r.checks["sequence_match"] is False
    assert r.objective_label == "INCORRECT"


# --- sweep every real-Mind2Web-derived fixture ---------------------------------------------------

@pytest.mark.parametrize("fx", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_fixture_matches_declared_label(fx):
    result = check_record(fx)
    assert result.objective_label == fx["expected_label"], (
        f"{fx['id']} ({fx['failure_class']}): checker said {result.objective_label}, "
        f"fixture declared {fx['expected_label']} -- {result.detail}"
    )


def test_fixtures_are_balanced_correct_incorrect():
    labels = [f["expected_label"] for f in FIXTURES]
    assert labels.count("CORRECT") == labels.count("INCORRECT")
    assert labels.count("CORRECT") == 8


def test_every_mutation_class_covered_exactly_once():
    classes = [f["failure_class"] for f in FIXTURES if f["failure_class"] != "none"]
    expected = {
        "wrong_selector", "hallucinated_element", "illegal_action", "wrong_value",
        "redundant_loop", "off_task_domain", "missing_step", "step_inflation",
    }
    assert set(classes) == expected
    assert len(classes) == len(expected)  # each class appears exactly once


def test_mutation_integrity_correct_and_incorrect_share_gold_task():
    by_source: dict[str, list[dict]] = {}
    for fx in FIXTURES:
        by_source.setdefault(fx["source_task_id"], []).append(fx)
    for source_id, pair in by_source.items():
        assert len(pair) == 2, f"{source_id} should have exactly one CORRECT + one INCORRECT fixture"
        golds = [f["gold"] for f in pair]
        assert golds[0] == golds[1], f"{source_id}: CORRECT/INCORRECT siblings must share the SAME gold task"


def test_correct_fixture_candidate_equals_gold_actions_verbatim():
    # the CORRECT sibling must never be hand-typed independently of gold -- it agrees with the
    # oracle by construction, same discipline as the SAT lane's find_satisfying_assignment.
    for fx in FIXTURES:
        if fx["failure_class"] == "none":
            assert fx["candidate"] == fx["gold"]["actions"]


# --- oracle-adapter conformance -----------------------------------------------------------------

class _FakeRun:
    def __init__(self, tool_calls, gold):
        self.case_id = "test-case"
        self.final_answer = None
        self.tool_calls = tool_calls
        self.trajectory_path = None
        self.artifact_path = None
        self.environment_id = None
        self.model = "test-model"
        self.metadata = {"gold": gold}


def test_oracle_adapter_passes_correct_run():
    oracle = BrowserTrajectoryOracle()
    run = _FakeRun([dict(a) for a in GOLD["actions"]], GOLD)
    result = oracle.evaluate(run)
    assert result.passed is True
    assert result.score == 1.0
    assert result.invalid_tool_calls == 0


def test_oracle_adapter_fails_broken_run_with_invalid_tool_call_count():
    oracle = BrowserTrajectoryOracle()
    candidate = [dict(a) for a in GOLD["actions"]]
    candidate[0] = {"op": "SELECT", "target_id": "hallucinated-node", "value": "Pickup"}
    run = _FakeRun(candidate, GOLD)
    result = oracle.evaluate(run)
    assert result.passed is False
    assert result.invalid_tool_calls == 1
    assert result.checks["valid_elements"] is False


def test_oracle_adapter_quarantines_missing_gold():
    oracle = BrowserTrajectoryOracle()
    run = _FakeRun([{"op": "CLICK", "target_id": "1", "value": None}], {})
    result = oracle.evaluate(run)
    assert result.passed is False
    assert result.quarantine_reason == "missing_gold"


# --- verdict-path judge/network freedom, mirroring test_oracle_adapter_verdict_safety.py --------

def test_checker_module_has_no_forbidden_imports():
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from lanes import module_forbidden_imports  # noqa: E402
    lane_dir = ROOT / "evals" / "objective_browser_trajectory"
    for mod_name in ("checker_browser_trajectory.py", "runtime_browser_trajectory.py"):
        bad = module_forbidden_imports(lane_dir / mod_name)
        assert bad == [], f"{mod_name} imports forbidden modules: {bad}"
