"""Validate the Stage-2A objective tool-calling checker (the pass/fail authority).

Two guarantees the whole hard-gold lane rests on:
  1. Every case's OWN canonical answer must PASS (checker is not over-strict).
  2. Targeted mutations must FAIL with the correct taxonomy code (checker is not lenient).

If either breaks, no label the checker emits can be trusted, so these are frozen tests.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_tool_calling.checker import (  # noqa: E402
    check_candidate, parse_candidate, value_equal, value_in_acceptable,
    E_WRONG_TOOL, E_FABRICATED_TOOL, E_MISSING_CALL, E_EXTRA_CALL,
    E_MISSING_REQUIRED_ARG, E_INVALID_ARG_NAME, E_WRONG_ARG_VALUE,
    E_INVALID_ENUM, E_SHOULD_NOT_CALL, E_MALFORMED,
)

CASES = [json.loads(l) for l in
         (ROOT / "evals/objective_tool_calling/cases.jsonl").read_text(encoding="utf-8").splitlines()
         if l.strip()]


def _canonical(case):
    """Build ONE concrete correct candidate by taking the first acceptable value per arg."""
    calls = []
    for ec in case["expected"]:
        args = {}
        for k, acc in ec["arguments"].items():
            first = acc[0] if isinstance(acc, list) else acc
            if first == "":            # optional-empty encoding -> omit the arg
                continue
            args[k] = first
        calls.append({"name": ec["name"], "arguments": args})
    return calls


# ---- guarantee 1: canonical answers pass ----
def test_every_canonical_answer_passes():
    failures = []
    for case in CASES:
        cand = _canonical(case)          # [] for irrelevance -> correct refusal
        res = check_candidate(case, cand)
        if res.verdict != "pass":
            failures.append((case["id"], res.errors))
    assert not failures, f"canonical answers failed: {failures}"


def test_irrelevance_cases_pass_on_no_call():
    irr = [c for c in CASES if c["category"] == "irrelevance"]
    assert irr, "expected irrelevance cases present"
    for c in irr:
        assert check_candidate(c, []).verdict == "pass"
        assert check_candidate(c, "").verdict == "pass"


# ---- guarantee 2: mutations fail with the right code ----
def _first_non_irrelevance():
    return next(c for c in CASES if c["expected"])


def test_wrong_tool_fails():
    case = next(c for c in CASES if c["id"] == "simple_weather_1")
    res = check_candidate(case, [{"name": "get_weather", "arguments": {"city": "Paris"}}])
    assert res.verdict == "pass"
    # swap to a tool that exists elsewhere but isn't provided here -> fabricated
    res2 = check_candidate(case, [{"name": "calculate", "arguments": {"expression": "1+1"}}])
    assert res2.verdict == "fail"
    assert any(e["code"] == E_FABRICATED_TOOL for e in res2.errors)


def test_wrong_tool_among_provided_fails():
    case = next(c for c in CASES if c["id"] == "multiple_3")  # expects get_weather
    res = check_candidate(case, [{"name": "calculate", "arguments": {"expression": "1"}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_WRONG_TOOL for e in res.errors)


def test_fabricated_tool_fails():
    case = _first_non_irrelevance()
    res = check_candidate(case, [{"name": "totally_made_up_tool", "arguments": {}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_FABRICATED_TOOL for e in res.errors)


def test_missing_required_arg_fails():
    case = next(c for c in CASES if c["id"] == "simple_convert_1")
    res = check_candidate(case, [{"name": "convert_currency",
                                  "arguments": {"amount": 100, "from_currency": "USD"}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_MISSING_REQUIRED_ARG for e in res.errors)


def test_wrong_arg_value_fails():
    case = next(c for c in CASES if c["id"] == "simple_weather_1")
    res = check_candidate(case, [{"name": "get_weather", "arguments": {"city": "Berlin"}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_WRONG_ARG_VALUE for e in res.errors)


def test_invalid_enum_fails():
    case = next(c for c in CASES if c["id"] == "simple_weather_2")  # unit fahrenheit
    res = check_candidate(case, [{"name": "get_weather",
                                  "arguments": {"city": "Tokyo", "unit": "kelvin"}}])
    assert res.verdict == "fail"
    assert any(e["code"] in (E_INVALID_ENUM, E_WRONG_ARG_VALUE) for e in res.errors)


def test_invalid_arg_name_fails():
    case = next(c for c in CASES if c["id"] == "simple_weather_1")
    res = check_candidate(case, [{"name": "get_weather",
                                  "arguments": {"city": "Paris", "made_up": 1}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_INVALID_ARG_NAME for e in res.errors)


def test_extra_call_fails():
    case = next(c for c in CASES if c["id"] == "simple_weather_1")
    res = check_candidate(case, [{"name": "get_weather", "arguments": {"city": "Paris"}},
                                 {"name": "get_weather", "arguments": {"city": "Paris"}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_EXTRA_CALL for e in res.errors)


def test_missing_call_fails():
    case = next(c for c in CASES if c["id"] == "parallel_1")  # expects 2 calls
    res = check_candidate(case, [{"name": "get_weather", "arguments": {"city": "London"}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_MISSING_CALL for e in res.errors)


def test_irrelevance_should_not_call_fails():
    case = next(c for c in CASES if c["id"] == "irrelevance_3")  # weather, no city
    res = check_candidate(case, [{"name": "get_weather", "arguments": {"city": "somewhere"}}])
    assert res.verdict == "fail"
    assert any(e["code"] == E_SHOULD_NOT_CALL for e in res.errors)


def test_malformed_candidate_fails():
    case = _first_non_irrelevance()
    res = check_candidate(case, "this is not json and not a call {oops")
    assert res.verdict == "fail"
    assert any(e["code"] == E_MALFORMED for e in res.errors)


def test_parallel_order_insensitive():
    case = next(c for c in CASES if c["id"] == "parallel_1")  # London + Rome
    res = check_candidate(case, [{"name": "get_weather", "arguments": {"city": "Rome"}},
                                 {"name": "get_weather", "arguments": {"city": "London"}}])
    assert res.verdict == "pass"  # order must not matter


# ---- value-equality unit checks (semantic equivalence) ----
def test_value_equal_numeric_and_string():
    assert value_equal(3, "3")
    assert value_equal(100.0, 100)
    assert value_equal("New York", "new york")
    assert value_equal("  Paris ", "paris")
    assert not value_equal("Paris", "London")


def test_value_in_acceptable_list():
    assert value_in_acceptable("USD", ["USD", "US dollars", "dollars"])
    assert value_in_acceptable("dollars", ["USD", "US dollars", "dollars"])
    assert not value_in_acceptable("EUR", ["USD", "dollars"])


# ---- expression-formatting whitespace insensitivity (tier-probe regression) ----
def test_expression_whitespace_insensitive():
    """'2 ** 10' (spaces around the operator) is the SAME expression as '2**10'.
    `_norm_str` only squeezes runs of whitespace, so it can't collapse the space
    *around* the operator; the AST-equality fallback must. Regression for the
    tier-probe finding that artificially capped capable models at 0.982 on parallel_7.
    """
    acc = ["2**10", "2^10", "1024"]
    assert value_in_acceptable("2 ** 10", acc)     # was FALSE before the fix
    assert value_in_acceptable("2**10", acc)       # already passed
    assert value_equal("3 * 4", "3*4")
    assert value_equal("a + b", "a+b")


def test_wrong_expression_still_fails():
    """The fix must NOT loosen into false positives: a genuinely different
    expression (different operand or operator) must still fail, and AST equality
    never *evaluates* ('1+1' is not '2')."""
    acc = ["2**10", "2^10", "1024"]
    assert not value_in_acceptable("2 ** 11", acc)   # wrong operand
    assert not value_equal("1+1", "2")               # no arithmetic evaluation
    assert not value_equal("2 ** 10", "2 * 10")      # wrong operator
    assert not value_equal("a b", "ab")              # not a valid single expr -> no collapse


def test_parallel_7_capable_model_passes():
    """End-to-end: the exact case the tier-probe flagged. A model that answers with
    spaced operators must PASS, matching the un-spaced gold encoding."""
    case = next(c for c in CASES if c["id"] == "parallel_7")
    res = check_candidate(case, [
        {"name": "calculate", "arguments": {"expression": "2 ** 10"}},
        {"name": "calculate", "arguments": {"expression": "3 ** 5"}},
    ])
    assert res.verdict == "pass", res.errors
    # a wrong exponent must still fail
    bad = check_candidate(case, [
        {"name": "calculate", "arguments": {"expression": "2 ** 11"}},
        {"name": "calculate", "arguments": {"expression": "3 ** 5"}},
    ])
    assert bad.verdict == "fail"


def test_parse_openai_tool_call_shape():
    calls, ok = parse_candidate([{"type": "function",
                                  "function": {"name": "get_weather",
                                               "arguments": '{"city": "Paris"}'}}])
    assert ok and calls == [{"name": "get_weather", "arguments": {"city": "Paris"}}]


def test_parse_json_string_and_fences():
    calls, ok = parse_candidate('```json\n[{"name":"calculate","arguments":{"expression":"1+1"}}]\n```')
    assert ok and calls[0]["name"] == "calculate"
