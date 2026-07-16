"""Frozen tests for the objective incident-investigation (RCA) checker (Class-B oracle, judge-free).

LABEL AUTHORITY: a stdlib-only deterministic RCA match (checker_incident.score_rca / check_record)
against the scenario's planted ground truth -- never a model/judge/threshold. These tests pin the
checker on hand-built cases (independent of the runner's fixture list, exercising each of the five
criteria), sweep every fixture asserting the checker agrees with its declared expected_label, and
assert the lane's structural invariants (count, balance, unique ids, failure-class coverage,
red-herring presence, mutation-integrity).
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_incident_investigation.checker_incident import (  # noqa: E402
    canonical_rca,
    check_record,
    score_rca,
)
from evals.objective_incident_investigation.run_incident import FIXTURES, _SCENARIOS  # noqa: E402


def _scn():
    """A small hand-built scenario, independent of the runner's list."""
    return {
        "services": ["a", "b", "c"],
        "events": [
            {"id": "e1", "t": 10, "kind": "deploy", "service": "a", "detail": "bad deploy"},
            {"id": "e2", "t": 11, "kind": "config_change", "service": "a", "detail": "flag on"},
            {"id": "e3", "t": 6, "kind": "traffic_spike", "service": "b", "detail": "spike herring"},
            {"id": "e4", "t": 1, "kind": "deploy", "service": "c", "detail": "clean earlier deploy"},
        ],
        "signals": [{"id": "s1", "line": "a blocking 3s"}, {"id": "s2", "line": "b RPS +40% herring"}],
        "ground_truth": {
            "root_cause_event_id": "e1",
            "contributing_factor_ids": ["e2"],
            "red_herring_ids": ["e3", "s2"],
            "correct_timeline_order": ["e4", "e3", "e1", "e2"],
            "affected_services": ["a", "b"],
            "remediation_class": "rollback_deploy",
        },
    }


# --- hand-picked criterion cases, independent of the runner's fixtures -------------------------

def test_exact_correct_rca_passes():
    scn = _scn()
    r = score_rca(scn, canonical_rca(scn))
    assert r.objective_label == "CORRECT"
    assert r.failed_criteria == []


def test_wrong_root_cause_fails_criterion_1():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "root_cause_event_id": "e4"})
    assert r.objective_label == "INCORRECT" and 1 in r.failed_criteria


def test_missing_contributing_factor_fails_criterion_2():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "contributing_factor_ids": []})
    assert r.objective_label == "INCORRECT" and 2 in r.failed_criteria


def test_extra_non_herring_factor_fails_only_criterion_2():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "contributing_factor_ids": ["e2", "e4"]})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [2]


def test_blaming_red_herring_root_fails_criteria_1_and_3():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "root_cause_event_id": "e3"})
    assert r.objective_label == "INCORRECT"
    assert 1 in r.failed_criteria and 3 in r.failed_criteria


def test_red_herring_as_contributing_fails_criteria_2_and_3():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "contributing_factor_ids": ["e2", "e3"]})
    assert 2 in r.failed_criteria and 3 in r.failed_criteria


def test_wrong_affected_services_fails_criterion_4():
    scn = _scn()
    assert 4 in score_rca(scn, {**canonical_rca(scn), "affected_services": ["a"]}).failed_criteria
    assert 4 in score_rca(scn, {**canonical_rca(scn), "affected_services": ["a", "b", "c"]}).failed_criteria


def test_wrong_remediation_fails_criterion_5():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "remediation_class": "scale_up"})
    assert r.objective_label == "INCORRECT" and 5 in r.failed_criteria


def test_timeline_optional_absent_never_fails():
    scn = _scn()
    cand = {k: v for k, v in canonical_rca(scn).items() if k != "timeline_order"}
    r = score_rca(scn, cand)
    assert r.objective_label == "CORRECT" and 6 not in r.failed_criteria


def test_timeline_present_and_wrong_fails_criterion_6():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "timeline_order": ["e3", "e4", "e1", "e2"]})
    assert r.objective_label == "INCORRECT" and 6 in r.failed_criteria


def test_contributing_factor_order_is_irrelevant():
    scn = _scn()
    r = score_rca(scn, {**canonical_rca(scn), "contributing_factor_ids": ["e2"]})
    assert r.objective_label == "CORRECT"


def test_computed_answer_is_canonical_rca():
    scn = _scn()
    r = score_rca(scn, canonical_rca(scn))
    assert r.computed_answer == canonical_rca(scn)


# --- full fixture sweep -----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["scenario"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_canonical():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == canonical_rca(fx["scenario"]), fx["id"]


def test_every_incorrect_candidate_actually_fails_a_criterion():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        r = check_record(fx["scenario"], fx["candidate"])
        assert r.failed_criteria, f"{fx['id']} is INCORRECT but no criterion failed"


# --- structural invariants --------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 28, len(FIXTURES)


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8, dist
    assert dist["INCORRECT"] >= 8, dist


def test_all_failure_classes_present():
    required = {
        "none", "blamed_red_herring", "missed_contributing_factor",
        "extra_wrong_factor", "wrong_affected_services", "wrong_remediation",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_scenario_has_a_red_herring():
    for scn in _SCENARIOS:
        rh = scn["ground_truth"]["red_herring_ids"]
        assert rh, f"{scn['name']} has no planted red herring"


def test_every_incorrect_states_a_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_scn = {}
    for fx in FIXTURES:
        by_scn.setdefault(fx["scenario_name"], []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_scn[fx["scenario_name"]]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_ground_truth_root_cause_is_a_real_event():
    for scn in _SCENARIOS:
        event_ids = {e["id"] for e in scn["events"]}
        assert scn["ground_truth"]["root_cause_event_id"] in event_ids, scn["name"]
