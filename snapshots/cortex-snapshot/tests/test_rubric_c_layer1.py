"""Tests for Rubric C deterministic Layer-1 (evals/rubrics/rubric_c_layer1.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.rubrics.rubric_c_layer1 import rubric_c_layer1


def test_non_verifiable_wish_hard_fails():
    r = rubric_c_layer1("Item: make agents smarter until they stop making mistakes.")
    assert r.overall == "fail"
    assert any(c.name == "smell:non_verifiable" and c.status == "fail" for c in r.checks)


def test_missing_acceptance_and_verification_fail():
    r = rubric_c_layer1("Item: refactor the module a bit.")
    names = {c.name: c.status for c in r.checks}
    assert names["field:acceptance"] == "fail"
    assert names["field:verification_method"] == "fail"
    assert r.overall == "fail"


def test_well_formed_item_does_not_hard_fail():
    text = (
        "Item: implement cortex_golden_search. Acceptance: on the seed corpus a held query "
        "set retrieves the intended exemplar in top-5 for at least 8 of 10 queries, verified "
        "by an automated enumeration test. Verification: benchmark script plus two integration "
        "tests. Rollback: revert the migration; module unloads via config. Phase: after the "
        "schema migration, before promotion-gate work. Scope: one module."
    )
    r = rubric_c_layer1(text)
    assert r.overall != "fail"  # a well-formed item screens clean (pass or needs_review)


def test_vague_terms_route_to_needs_review_not_fail():
    text = ("Item: add acceptance test. Acceptance: verified by a unit test. It should be "
            "robust and user-friendly and handle several cases.")
    r = rubric_c_layer1(text)
    assert any(c.name == "smell:vague_terms" and c.status == "needs_review" for c in r.checks)
    # vague terms alone don't hard-fail when required fields are present
    assert r.overall in ("needs_review", "pass")


def test_overall_precedence_fail_beats_needs_review():
    r = rubric_c_layer1("Acceptance: verified by test. Some vague robust thing that must stop making mistakes.")
    assert r.overall == "fail"  # a fail anywhere dominates needs_review
