"""Frozen tests for the objective Kubernetes-manifest-schema checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only encoded-schema validation (checker_k8s.validate_manifest /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy + kind
coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_k8s_manifest_schema.checker_k8s import (  # noqa: E402
    check_record,
    validate_manifest,
)
from evals.objective_k8s_manifest_schema.run_k8s import FIXTURES  # noqa: E402

_POD = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "nginx"},
        "spec": {"containers": [{"name": "nginx", "image": "nginx:1.25"}]}}
_DEP = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web"},
        "spec": {"replicas": 3, "selector": {"matchLabels": {"app": "web"}},
                 "template": {"spec": {}}}}
_SVC = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "web-svc"},
        "spec": {"ports": [{"port": 80}]}}
_CM = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"},
       "data": {"KEY": "value", "MODE": "prod"}}


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_valid_pod():
    assert validate_manifest(_POD)[0] is True
    assert check_record(_POD, "VALID").objective_label == "CORRECT"
    assert check_record(_POD, "INVALID").objective_label == "INCORRECT"


def test_valid_deployment():
    assert validate_manifest(_DEP)[0] is True
    assert check_record(_DEP, "VALID").objective_label == "CORRECT"


def test_valid_service():
    assert validate_manifest(_SVC)[0] is True
    assert check_record(_SVC, "VALID").objective_label == "CORRECT"


def test_valid_configmap():
    assert validate_manifest(_CM)[0] is True
    assert check_record(_CM, "VALID").objective_label == "CORRECT"


def test_missing_apiversion_invalid():
    m = {k: v for k, v in _POD.items() if k != "apiVersion"}
    assert validate_manifest(m)[0] is False
    assert check_record(m, "INVALID").objective_label == "CORRECT"
    assert check_record(m, "VALID").objective_label == "INCORRECT"


def test_wrong_apiversion_for_kind_invalid():
    # Deployment declared v1 (must be apps/v1)
    assert validate_manifest({**_DEP, "apiVersion": "v1"})[0] is False
    # Pod declared apps/v1 (must be v1)
    assert validate_manifest({**_POD, "apiVersion": "apps/v1"})[0] is False


def test_missing_metadata_name_invalid():
    m = {**_SVC, "metadata": {"labels": {"a": "b"}}}
    assert validate_manifest(m)[0] is False
    assert check_record(m, "INVALID").objective_label == "CORRECT"


def test_wrong_field_type_replicas_invalid():
    m = {**_DEP, "spec": {**_DEP["spec"], "replicas": "3"}}
    assert validate_manifest(m)[0] is False


def test_bool_is_not_a_valid_int_replicas():
    m = {**_DEP, "spec": {**_DEP["spec"], "replicas": True}}
    assert validate_manifest(m)[0] is False


def test_empty_containers_invalid():
    m = {**_POD, "spec": {"containers": []}}
    assert validate_manifest(m)[0] is False


def test_container_missing_image_invalid():
    m = {**_POD, "spec": {"containers": [{"name": "c"}]}}
    assert validate_manifest(m)[0] is False


def test_missing_required_spec_field_invalid():
    # Deployment missing selector
    assert validate_manifest({**_DEP, "spec": {"replicas": 1, "template": {}}})[0] is False
    # Service missing ports
    assert validate_manifest({**_SVC, "spec": {"type": "ClusterIP"}})[0] is False


def test_configmap_non_string_value_invalid():
    m = {**_CM, "data": {"KEY": "ok", "PORT": 8080}}
    assert validate_manifest(m)[0] is False


def test_unknown_kind_invalid():
    assert validate_manifest({"apiVersion": "v1", "kind": "Ingress",
                              "metadata": {"name": "x"}})[0] is False


def test_computed_answer_records_problems():
    m = {k: v for k, v in _POD.items() if k != "apiVersion"}
    r = check_record(m, "INVALID")
    assert r.computed_answer.startswith("INVALID:")
    assert check_record(_POD, "VALID").computed_answer == "VALID"


def test_check_record_rejects_non_decision_candidate():
    import pytest
    with pytest.raises(ValueError):
        check_record(_POD, "MAYBE")


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["manifest"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        valid, _ = validate_manifest(fx["manifest"])
        decision = "VALID" if valid else "INVALID"
        assert fx["candidate"] == decision, fx["id"]


# --- structural invariants -------------------------------------------------------------------

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
        "none", "missing_apiVersion", "wrong_apiVersion_for_kind",
        "missing_metadata_name", "wrong_field_type", "missing_required_spec_field",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_four_kinds_present():
    kinds = {fx["manifest"].get("kind") for fx in FIXTURES}
    assert {"Pod", "Deployment", "Service", "ConfigMap"}.issubset(kinds), kinds


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return json.dumps(fx["manifest"], sort_keys=True)

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        valid, _ = validate_manifest(fx["manifest"])
        decision = "VALID" if valid else "INVALID"
        assert fx["candidate"] != decision, fx["id"]
