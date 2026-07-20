"""Unit tests for cortex_core.app_contract — the pure-data shared leaf.

Covers the §2.3 test-list items 1-6 (contract vocabulary + coach_view redaction +
validate_check_spec lint). Zero process launches, zero I/O.
"""
from __future__ import annotations

import json

from cortex_core.app_contract import (
    CHECK_KINDS,
    FAILURE_CLASSES,
    KIND_TO_CLASS,
    CheckResult,
    GateVerdict,
    coach_view,
    validate_check_spec,
)


# 1
def test_check_kinds_match_synthesis_table():
    assert CHECK_KINDS == (
        "app_starts",
        "buttons_work",
        "logic_works",
        "data_persists",
        "schema_real",
        "input_handling",
        "security_controls",
        "regression",
        "derived_value",
        "filtered_results",
        "deletes_row",
        "edits_row",
        "auth_required",
        "audit_trail",
        "dashboard_metrics",
        "detail_view",
        "relation_integrity",
        "status_lifecycle",
        "soft_delete",
        "assignment",
        "review_approval",
    )
    # KIND_TO_CLASS covers every kind, every value a real failure class.
    assert set(KIND_TO_CLASS) == set(CHECK_KINDS)
    for value in KIND_TO_CLASS.values():
        assert value in FAILURE_CLASSES
    # ENV_FAIL is a failure class but not a check kind (gate-side only).
    assert "ENV_FAIL" in FAILURE_CLASSES
    assert "ENV_FAIL" not in KIND_TO_CLASS.values()


# 2
def test_coach_view_exposes_only_pass_and_class():
    sentinel = "SENTINEL_HIDDEN_VALUE"
    results = (
        CheckResult("app_starts", True, False, f"pid=1 {sentinel}"),
        CheckResult("data_persists", False, True, f"token={sentinel}", "PERSISTENCE_FAIL"),
    )
    v = GateVerdict(
        passed=False,
        results=results,
        failure_class="PERSISTENCE_FAIL",
        hidden_coverage=True,
        env_retries=0,
        seed=123,
    )
    view = coach_view(v)
    assert set(view) == {"pass", "failure_class"}
    assert view["pass"] is False
    assert view["failure_class"] == "PERSISTENCE_FAIL"
    # Serializing the coach view must leak no hidden detail/payload/assertion text.
    assert "SENTINEL" not in json.dumps(view)


# 3
def test_validate_check_spec_rejects_unknown_kind():
    errs = validate_check_spec({"kind": "vibes"})
    assert errs
    assert any("vibes" in e for e in errs)


# 4
def test_validate_check_spec_buttons_require_state_change():
    spec = {
        "kind": "buttons_work",
        "actions": [
            {
                "name": "add",
                "request": {"method": "POST", "path": "/clients", "form": {"name": "x"}},
                "expect": {"status_lt": 400},
                # NO state_change -> DOM/status-only, must be rejected
            }
        ],
    }
    errs = validate_check_spec(spec)
    assert errs
    assert any("state_change" in e for e in errs)


# 5
def test_validate_check_spec_logic_requires_negative_case():
    positive_only = {
        "kind": "logic_works",
        "cases": [{"get_path": "/", "row_containing": "a", "has_class": "late"}],
    }
    assert validate_check_spec(positive_only)  # missing negative -> error

    with_negative = {
        "kind": "logic_works",
        "cases": [
            {"get_path": "/", "row_containing": "a", "has_class": "late"},
            {"get_path": "/", "row_containing": "b", "not_has_class": "late"},
        ],
    }
    assert validate_check_spec(with_negative) == []


# 6
def test_validate_check_spec_security_never_empty():
    empty = {"kind": "security_controls", "tests": []}
    assert validate_check_spec(empty)

    with_protected = {
        "kind": "security_controls",
        "tests": [],
        "protected": [{"method": "POST", "path": "/admin/reset"}],
    }
    assert validate_check_spec(with_protected) == []

    with_tests = {"kind": "security_controls", "tests": ["reflected_escape"]}
    assert validate_check_spec(with_tests) == []


def test_data_persists_resource_keys_required():
    incomplete = {"kind": "data_persists", "resource": {"create": {}, "read_path": "/x"}}
    errs = validate_check_spec(incomplete)
    assert any("table" in e for e in errs)
    assert any("column" in e for e in errs)
