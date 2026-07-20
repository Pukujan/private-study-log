"""Frozen tests for the objective ABAC (attribute-based) policy-decision checker (Stage-2 lane).

LABEL AUTHORITY: a stdlib-only attribute-based rule engine (checker_abac.evaluate / check_record),
never a model/judge. These tests pin the engine on hand-picked cases (independent of the runner's
scenario list) covering each combining algorithm, each operator family, default fall-through, and
missing-attribute safety; then sweep every fixture asserting the checker agrees with its declared
expected_label; then assert the lane's structural invariants (balance, unique ids, taxonomy
coverage, all three combining algorithms present, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_abac_policy_decision.checker_abac import (  # noqa: E402
    check_record,
    evaluate,
)
from evals.objective_abac_policy_decision.run_abac import FIXTURES  # noqa: E402


def _req(subject=None, resource=None, action=None, environment=None):
    return {"subject": subject or {}, "resource": resource or {},
            "action": action or {}, "environment": environment or {}}


# --- hand-picked cases, independent of the runner's scenario list -----------------------------

def test_single_permit_rule_applies():
    p = {"combining_algorithm": "deny-overrides", "default_effect": "deny",
         "rules": [{"effect": "permit", "conditions": [["subject.role", "eq", "admin"]]}]}
    req = _req(subject={"role": "admin"})
    assert evaluate(p, req)[0] == "ALLOW"
    assert check_record(p, req, "ALLOW").objective_label == "CORRECT"
    assert check_record(p, req, "DENY").objective_label == "INCORRECT"


def test_deny_overrides_beats_permit():
    p = {"combining_algorithm": "deny-overrides", "default_effect": "deny", "rules": [
        {"effect": "permit", "conditions": [["subject.role", "eq", "user"]]},
        {"effect": "deny", "conditions": [["resource.locked", "eq", True]]}]}
    req = _req(subject={"role": "user"}, resource={"locked": True})
    assert evaluate(p, req)[0] == "DENY"


def test_permit_overrides_beats_deny():
    p = {"combining_algorithm": "permit-overrides", "default_effect": "deny", "rules": [
        {"effect": "permit", "conditions": [["subject.role", "eq", "user"]]},
        {"effect": "deny", "conditions": [["resource.locked", "eq", True]]}]}
    req = _req(subject={"role": "user"}, resource={"locked": True})
    assert evaluate(p, req)[0] == "ALLOW"


def test_first_applicable_takes_first_in_order():
    p = {"combining_algorithm": "first-applicable", "default_effect": "permit", "rules": [
        {"effect": "deny", "conditions": [["environment.mfa", "eq", False]]},
        {"effect": "permit", "conditions": [["subject.role", "eq", "user"]]}]}
    req = _req(subject={"role": "user"}, environment={"mfa": False})
    assert evaluate(p, req)[0] == "DENY"  # first applicable rule (deny) governs, not the later permit


def test_default_effect_when_nothing_applies_both_directions():
    p_deny = {"combining_algorithm": "deny-overrides", "default_effect": "deny",
              "rules": [{"effect": "permit", "conditions": [["action.type", "eq", "write"]]}]}
    req = _req(action={"type": "read"})
    assert evaluate(p_deny, req)[0] == "DENY"
    p_permit = {**p_deny, "default_effect": "permit"}
    assert evaluate(p_permit, req)[0] == "ALLOW"


def test_operator_ge_threshold():
    p = {"combining_algorithm": "deny-overrides", "default_effect": "permit",
         "rules": [{"effect": "deny", "conditions": [["environment.risk_score", "ge", 60]]}]}
    assert evaluate(p, _req(environment={"risk_score": 70}))[0] == "DENY"
    assert evaluate(p, _req(environment={"risk_score": 60}))[0] == "DENY"
    assert evaluate(p, _req(environment={"risk_score": 59}))[0] == "ALLOW"


def test_operator_in_membership():
    p = {"combining_algorithm": "permit-overrides", "default_effect": "deny",
         "rules": [{"effect": "permit", "conditions": [["subject.dept", "in", ["eng", "ops"]]]}]}
    assert evaluate(p, _req(subject={"dept": "eng"}))[0] == "ALLOW"
    assert evaluate(p, _req(subject={"dept": "sales"}))[0] == "DENY"


def test_operator_contains_over_list():
    p = {"combining_algorithm": "first-applicable", "default_effect": "permit",
         "rules": [{"effect": "deny", "conditions": [["resource.tags", "contains", "pii"]]}]}
    assert evaluate(p, _req(resource={"tags": ["public", "pii"]}))[0] == "DENY"
    assert evaluate(p, _req(resource={"tags": ["public"]}))[0] == "ALLOW"


def test_missing_attribute_makes_condition_false_not_crash():
    p = {"combining_algorithm": "permit-overrides", "default_effect": "deny",
         "rules": [{"effect": "permit", "conditions": [["subject.age", "ge", 18]]}]}
    req = _req(subject={"role": "guest"})  # no subject.age at all
    # must not raise, and must fall through to default deny
    assert evaluate(p, req)[0] == "DENY"


def test_incomparable_types_are_false_not_crash():
    p = {"combining_algorithm": "deny-overrides", "default_effect": "permit",
         "rules": [{"effect": "deny", "conditions": [["environment.risk_score", "ge", 60]]}]}
    # a string risk_score vs an int threshold would raise TypeError -> treated as false condition
    assert evaluate(p, _req(environment={"risk_score": "high"}))[0] == "ALLOW"


def test_computed_answer_is_the_decision_token():
    p = {"combining_algorithm": "deny-overrides", "default_effect": "deny",
         "rules": [{"effect": "permit", "conditions": [["subject.role", "eq", "admin"]]}]}
    r = check_record(p, _req(subject={"role": "admin"}), "ALLOW")
    assert r.computed_answer == "ALLOW"


# --- full fixture sweep -----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["policy"], fx["request"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == evaluate(fx["policy"], fx["request"])[0], fx["id"]


# --- structural invariants --------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "none", "wrong_combining", "condition_operator_error",
        "missing_attribute_default", "default_effect_missed", "deny_override_missed",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_combining_algorithms_present():
    required = {"deny-overrides", "permit-overrides", "first-applicable"}
    present = {fx["policy"]["combining_algorithm"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _key(fx):
    return (json.dumps(fx["policy"], sort_keys=True), json.dumps(fx["request"], sort_keys=True))


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(_key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[_key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != evaluate(fx["policy"], fx["request"])[0], fx["id"]
