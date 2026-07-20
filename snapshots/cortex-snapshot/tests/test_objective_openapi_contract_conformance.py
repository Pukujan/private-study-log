"""Frozen tests for the objective OpenAPI-response-conformance checker (Stage-2 style lane).

LABEL AUTHORITY: a deterministic, stdlib-only OpenAPI response validator
(checker_openapi.check_record) -- status-code presence plus the REUSED JSON-Schema subset body
validator -- never a model/judge. These tests pin the checker on hand-picked cases (independent of
the fixture file), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the structural invariants of the fixture set.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_openapi_contract_conformance.checker_openapi import (  # noqa: E402
    check_record,
    computed_answer,
    extract_body_schema,
    resolve_response_spec,
)
from evals.objective_openapi_contract_conformance.fixtures_openapi import FIXTURES  # noqa: E402


def _op(responses):
    return {"path": "/x", "method": "get", "responses": responses}


def _json_resp(status, schema):
    return {"description": str(status), "content": {"application/json": {"schema": schema}}}


_OBJ_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "n": {"type": "integer", "minimum": 0, "maximum": 10}},
    "required": ["id", "n"],
    "additionalProperties": False,
}
_OP = _op({
    "200": _json_resp("200", _OBJ_SCHEMA),
    "404": _json_resp("404", {"type": "object", "properties": {"error": {"type": "string"}},
                             "required": ["error"]}),
    "204": {"description": "no content"},
})


# --- hand-picked checker cases, independent of the fixture file -------------------------------

def test_conforming_200_is_correct():
    r = check_record(_OP, {"status": 200, "body": {"id": "a", "n": 3}}, "CONFORMS")
    assert r.objective_label == "CORRECT"
    assert r.computed_answer == "CONFORMS"
    assert r.matched_status == "200"


def test_conforming_200_claimed_violates_is_incorrect():
    assert check_record(_OP, {"status": 200, "body": {"id": "a", "n": 3}}, "VIOLATES").objective_label == "INCORRECT"


def test_undocumented_status_violates():
    r = check_record(_OP, {"status": 500, "body": {"id": "a", "n": 3}}, "VIOLATES")
    assert r.objective_label == "CORRECT"
    assert r.computed_answer == "VIOLATES"
    assert r.matched_status is None
    assert r.errors  # names the undocumented status


def test_undocumented_status_claimed_conforms_is_incorrect():
    assert check_record(_OP, {"status": 500, "body": {}}, "CONFORMS").objective_label == "INCORRECT"


def test_body_missing_required_field_violates():
    assert check_record(_OP, {"status": 200, "body": {"id": "a"}}, "VIOLATES").objective_label == "CORRECT"


def test_body_type_mismatch_violates():
    assert check_record(_OP, {"status": 200, "body": {"id": "a", "n": "3"}}, "VIOLATES").objective_label == "CORRECT"


def test_body_additional_property_violates():
    assert check_record(_OP, {"status": 200, "body": {"id": "a", "n": 3, "extra": 1}}, "VIOLATES").objective_label == "CORRECT"


def test_enum_and_range_come_from_reused_validator():
    # inclusive maximum boundary conforms (reused JSON-Schema validator semantics)
    assert computed_answer(_OP, {"status": 200, "body": {"id": "a", "n": 10}}) == "CONFORMS"
    # above maximum violates
    assert computed_answer(_OP, {"status": 200, "body": {"id": "a", "n": 11}}) == "VIOLATES"


def test_documented_404_conforming_body():
    assert check_record(_OP, {"status": 404, "body": {"error": "nope"}}, "CONFORMS").objective_label == "CORRECT"


def test_204_no_body_schema_conforms_on_status_alone():
    r = check_record(_OP, {"status": 204, "body": None}, "CONFORMS")
    assert r.objective_label == "CORRECT"
    assert r.matched_status == "204"


def test_default_fallback_used_when_status_absent():
    op = _op({
        "200": _json_resp("200", {"type": "object"}),
        "default": _json_resp("default", {"type": "object", "properties": {"code": {"type": "integer"}},
                                          "required": ["code"]}),
    })
    key, spec = resolve_response_spec(op["responses"], 503)
    assert key == "default"
    assert check_record(op, {"status": 503, "body": {"code": 503}}, "CONFORMS").objective_label == "CORRECT"
    assert check_record(op, {"status": 503, "body": {}}, "VIOLATES").objective_label == "CORRECT"


def test_exact_status_wins_over_default():
    op = _op({"200": _json_resp("200", {"type": "object"}), "default": _json_resp("default", {"type": "string"})})
    key, _ = resolve_response_spec(op["responses"], 200)
    assert key == "200"


def test_extract_body_schema_none_when_no_content():
    assert extract_body_schema({"description": "no content"}) is None
    assert extract_body_schema({"content": {"application/xml": {"schema": {"type": "object"}}}}) is None


def test_invalid_candidate_answer_raises():
    try:
        check_record(_OP, {"status": 200, "body": {"id": "a", "n": 3}}, "MAYBE")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non CONFORMS/VIOLATES candidate_answer")


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["operation"], fx["response"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "undocumented_status", "body_missing_required_field", "body_type_mismatch",
        "body_additional_property", "enum_violation_in_body",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_clean_baseline_present():
    assert any(fx["failure_class"] == "none" for fx in FIXTURES)


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_correct_fixtures_have_no_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == ""


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return (json.dumps(fx["operation"], sort_keys=True),
                json.dumps(fx["response"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_checker_self_test_runs():
    from evals.objective_openapi_contract_conformance.checker_openapi import self_test
    self_test()
