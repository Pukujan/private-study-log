"""Frozen tests for the objective RBAC/ABAC policy-decision checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic evaluation of an explicit policy by the small rule engine in
checker_rbac.py -- never a model/judge. These tests pin the engine's behavior on hand-picked
policy/request cases (independent of the fixture file) plus a full sweep over every fixture in
fixtures_rbac.py, asserting the checker's objective_label always matches the fixture's declared
expected_label (the same self-validation gate every other Stage-2 lane uses).

Written before checker_rbac.py existed (confirmed RED via ModuleNotFoundError), per strict TDD.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_rbac_policy_decision.checker_rbac import (  # noqa: E402
    check_record, decide, effective_roles,
)
from evals.objective_rbac_policy_decision.fixtures_rbac import FIXTURES  # noqa: E402


# --- hand-picked engine cases, independent of the fixture file -------------------------------

def test_effective_roles_transitive_closure_two_hops():
    hierarchy = {"admin": ["editor"], "editor": ["viewer"]}
    assert effective_roles(["admin"], hierarchy) == {"admin", "editor", "viewer"}


def test_effective_roles_transitive_closure_three_hops():
    hierarchy = {"superadmin": ["admin"], "admin": ["editor"], "editor": ["viewer"]}
    assert effective_roles(["superadmin"], hierarchy) == {"superadmin", "admin", "editor", "viewer"}


def test_effective_roles_no_edges_is_just_subject_roles():
    assert effective_roles(["viewer"], {}) == {"viewer"}


def test_effective_roles_cycle_safe_terminates():
    hierarchy = {"a": ["b"], "b": ["a"]}
    assert effective_roles(["a"], hierarchy) == {"a", "b"}


def test_effective_roles_inheritance_is_one_directional():
    hierarchy = {"admin": ["editor"]}
    # editor does NOT get admin's roles; only admin inherits editor's.
    assert effective_roles(["editor"], hierarchy) == {"editor"}
    assert effective_roles(["admin"], hierarchy) == {"admin", "editor"}


def test_decide_plain_single_rule_allow():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "read", "resource": "document", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "document", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "ALLOW"
    assert len(matched) == 1


def test_decide_default_deny_no_rules_at_all():
    policy = {"roles_hierarchy": {}, "rules": []}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "document", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_decide_default_deny_role_mismatch():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "editor", "action": "edit", "resource": "report", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "edit",
               "resource": "report", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_decide_deny_overrides_allow_same_request():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "editor", "action": "delete", "resource": "report", "condition": None},
        {"effect": "DENY", "role": "contractor", "action": "delete", "resource": "report", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["editor", "contractor"], "action": "delete",
               "resource": "report", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert len(matched) == 2


def test_decide_wildcard_role_matches_everyone():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "DENY", "role": "*", "action": "delete", "resource": "archived_report", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["intern"], "action": "delete",
               "resource": "archived_report", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert len(matched) == 1


def test_decide_wildcard_action_matches_any_action_same_resource():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "editor", "action": "*", "resource": "draft", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["editor"], "action": "delete",
               "resource": "draft", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "ALLOW"


def test_decide_wildcard_resource_matches_any_resource_same_action():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "read", "resource": "*", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "invoice", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "ALLOW"


def test_decide_action_wildcard_does_not_loosen_resource_field():
    """A rule wildcarding `action` says nothing about `resource` -- it must still be an exact
    resource match. This is the engine-level proof of the wildcard_over_broad trap."""
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "editor", "action": "*", "resource": "draft", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["editor"], "action": "delete",
               "resource": "published_report", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_decide_resource_wildcard_does_not_loosen_action_field():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "read", "resource": "*", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "write",
               "resource": "document", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_condition_ownership_true_grants():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "edit", "resource": "note",
         "condition": {"type": "ownership", "field": "owner_id"}},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "edit",
               "resource": "note", "context": {"owner_id": "u1"}}
    decision, matched = decide(policy, request)
    assert decision == "ALLOW"


def test_condition_ownership_false_falls_to_default_deny():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "edit", "resource": "note",
         "condition": {"type": "ownership", "field": "owner_id"}},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "edit",
               "resource": "note", "context": {"owner_id": "u2"}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_condition_attr_eq_true_grants():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "support_agent", "action": "reset_password", "resource": "account",
         "condition": {"type": "attr_eq", "field": "account_tier", "value": "standard"}},
    ]}
    request = {"subject_id": "agent7", "subject_roles": ["support_agent"], "action": "reset_password",
               "resource": "account", "context": {"account_tier": "standard"}}
    decision, matched = decide(policy, request)
    assert decision == "ALLOW"


def test_condition_attr_eq_false_falls_to_default_deny():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "support_agent", "action": "reset_password", "resource": "account",
         "condition": {"type": "attr_eq", "field": "account_tier", "value": "standard"}},
    ]}
    request = {"subject_id": "agent7", "subject_roles": ["support_agent"], "action": "reset_password",
               "resource": "account", "context": {"account_tier": "enterprise"}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_condition_unknown_type_raises():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "read", "resource": "doc",
         "condition": {"type": "not_a_real_condition"}},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "doc", "context": {}}
    with pytest.raises(ValueError):
        decide(policy, request)


def test_decide_non_overlapping_allow_and_deny_only_allow_matches():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "read", "resource": "document", "condition": None},
        {"effect": "DENY", "role": "editor", "action": "write", "resource": "invoice", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "document", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "ALLOW"
    assert len(matched) == 1


def test_decide_privilege_escalation_hierarchy_direction_not_inverted():
    hierarchy = {"admin": ["editor"]}
    policy = {"roles_hierarchy": hierarchy, "rules": [
        {"effect": "ALLOW", "role": "admin", "action": "grant_role", "resource": "account", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["editor"], "action": "grant_role",
               "resource": "account", "context": {}}
    decision, matched = decide(policy, request)
    assert decision == "DENY"
    assert matched == []


def test_check_record_correct_when_candidate_matches_computed():
    policy = {"roles_hierarchy": {}, "rules": [
        {"effect": "ALLOW", "role": "viewer", "action": "read", "resource": "document", "condition": None},
    ]}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "document", "context": {}}
    r = check_record(policy, request, "ALLOW")
    assert r.objective_label == "CORRECT"
    assert r.computed_decision == "ALLOW"


def test_check_record_incorrect_when_candidate_mismatches_computed():
    policy = {"roles_hierarchy": {}, "rules": []}
    request = {"subject_id": "u1", "subject_roles": ["viewer"], "action": "read",
               "resource": "document", "context": {}}
    r = check_record(policy, request, "ALLOW")
    assert r.objective_label == "INCORRECT"
    assert r.computed_decision == "DENY"


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["policy"], fx["request"], fx["candidate_decision"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 20


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_seven_failure_classes_covered():
    required = {
        "missing_grant_allowed", "deny_override_ignored", "condition_ignored",
        "wildcard_over_broad", "default_deny_violated", "role_inheritance_error",
        "privilege_escalation",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_every_correct_fixture_has_empty_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == "", f"{fx['id']} is CORRECT but declares a mutation"


def test_mutation_integrity_incorrect_shares_pair_id_and_policy_and_request_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-pair_id CORRECT sibling with the identical policy
    and request (same question) -- proof it perturbs exactly the candidate_decision, not the
    question itself."""
    by_pair = {}
    for fx in FIXTURES:
        by_pair.setdefault(fx["pair_id"], []).append(fx)

    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_pair[fx["pair_id"]]
        correct_siblings = [s for s in siblings if s["expected_label"] == "CORRECT"]
        assert correct_siblings, fx["id"]
        sib = correct_siblings[0]
        assert sib["policy"] == fx["policy"], f"{fx['id']}: policy diverged from its CORRECT sibling"
        assert sib["request"] == fx["request"], f"{fx['id']}: request diverged from its CORRECT sibling"
        assert sib["candidate_decision"] != fx["candidate_decision"], fx["id"]


def test_each_pair_has_exactly_one_correct_and_one_incorrect():
    by_pair = {}
    for fx in FIXTURES:
        by_pair.setdefault(fx["pair_id"], []).append(fx)
    for pair_id, members in by_pair.items():
        labels = Counter(m["expected_label"] for m in members)
        assert labels["CORRECT"] == 1, pair_id
        assert labels["INCORRECT"] == 1, pair_id
        assert len(members) == 2, pair_id


def test_depth_classes_have_two_pairs():
    """deny_override_ignored, role_inheritance_error, and privilege_escalation each get 2 pairs
    (4 records) for depth on the trickiest/most security-critical traps; the other 4 classes get 1
    pair (2 records) each."""
    pair_ids_by_class = {}
    for fx in FIXTURES:
        pair_ids_by_class.setdefault(fx["failure_class"], set()).add(fx["pair_id"])
    for cls in ("deny_override_ignored", "role_inheritance_error", "privilege_escalation"):
        assert len(pair_ids_by_class[cls]) == 2, cls
    for cls in ("missing_grant_allowed", "condition_ignored", "wildcard_over_broad",
                "default_deny_violated"):
        assert len(pair_ids_by_class[cls]) == 1, cls
