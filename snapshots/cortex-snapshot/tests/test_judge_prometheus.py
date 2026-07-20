"""Tests for Prometheus native [RESULT] N judge path."""

from __future__ import annotations

import pytest

from cortex_core.evaluator import AtomicClaim
from cortex_core.judge import _build_prometheus_prompt, _parse_prometheus_response


def _claim():
    return AtomicClaim(claim_id="c1", task_type="bugfix", description="Fix the SSRF bug")


def _evidence():
    return [{"type": "test", "ref": "test_fetch_ssrf_pinning", "detail": "PASSED"}]


def test_prometheus_prompt_contains_delimiters():
    prompt = _build_prometheus_prompt(_claim(), _evidence(), "reference answer")
    assert "|Instruction|" in prompt
    assert "|Response|" in prompt
    assert "|Reference Answer|" in prompt
    assert "|Score Rubric|" in prompt


def test_prometheus_prompt_includes_claim_and_evidence():
    prompt = _build_prometheus_prompt(_claim(), _evidence(), "ref answer text")
    assert "Fix the SSRF bug" in prompt
    assert "test_fetch_ssrf_pinning" in prompt
    assert "ref answer text" in prompt


def test_prometheus_prompt_omits_reference_when_none():
    prompt = _build_prometheus_prompt(_claim(), _evidence(), None)
    assert "|Reference Answer|" in prompt
    assert "(no reference answer provided)" in prompt


def test_parse_result_5():
    grade = _parse_prometheus_response("[RESULT] 5", "c1", 1, "prometheus")
    assert grade.verdict.value == "strongly_supported"
    assert grade.confidence == 1.0


def test_parse_result_4():
    grade = _parse_prometheus_response("some reasoning\n[RESULT] 4", "c1", 1, "prometheus")
    assert grade.verdict.value == "supported"


def test_parse_result_3():
    grade = _parse_prometheus_response("[RESULT]3", "c1", 1, "prometheus")
    assert grade.verdict.value == "verifiable_but_flawed"


def test_parse_result_2():
    grade = _parse_prometheus_response("I think [RESULT] 2", "c1", 1, "prometheus")
    assert grade.verdict.value == "partially_supported"


def test_parse_result_1():
    grade = _parse_prometheus_response("[RESULT] 1", "c1", 1, "prometheus")
    assert grade.verdict.value == "unsupported"


def test_parse_missing_result_is_unverifiable():
    grade = _parse_prometheus_response("just some text", "c1", 1, "prometheus")
    assert grade.verdict.value == "unverifiable"
    assert grade.confidence == 0.0


def test_parse_out_of_range_result_is_unverifiable():
    grade = _parse_prometheus_response("[RESULT] 0", "c1", 1, "prometheus")
    assert grade.verdict.value == "unverifiable"


def test_parse_non_integer_result_is_unverifiable():
    grade = _parse_prometheus_response("[RESULT] abc", "c1", 1, "prometheus")
    assert grade.verdict.value == "unverifiable"
