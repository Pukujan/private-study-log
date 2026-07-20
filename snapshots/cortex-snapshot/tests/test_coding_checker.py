"""Frozen tests for the Stage-2B objective coding checker (the pass/fail authority).

The whole coding hard-gold lane trusts this checker's verdicts, so: a correct solution must
PASS, a solution that fails visible tests must FAIL at the visible stage, one that passes
visible but fails a hidden edge case must FAIL at the hidden stage, and a syntax error must
FAIL at compile.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_coding.checker import check_solution  # noqa: E402
from evals.objective_coding.fixtures import FIXTURES  # noqa: E402


def test_correct_solution_passes():
    r = check_solution("def f(x):\n    return x*2\n", "assert f(2)==4\n", "assert f(0)==0\n")
    assert r.verdict == "pass", r.asdict()


def test_visible_failure_detected():
    r = check_solution("def f(x):\n    return x+1\n", "assert f(2)==4\n", "assert f(0)==0\n")
    assert r.verdict == "fail" and r.failed_stage == "visible_tests"


def test_hidden_edge_case_failure_detected():
    # passes visible (f(2)==4) but fails hidden (f(0) should be 0, returns 1)
    r = check_solution("def f(x):\n    return x*2 if x else 1\n", "assert f(2)==4\n", "assert f(0)==0\n")
    assert r.verdict == "fail" and r.failed_stage == "hidden_tests"


def test_syntax_error_detected_at_compile():
    r = check_solution("def f(x)\n    return x\n", "assert f(1)==1\n", "assert f(2)==2\n")
    assert r.verdict == "fail" and r.failed_stage == "compile"


def test_timeout_is_a_failure_not_a_hang():
    slow = "def f(x):\n    while True: pass\n"
    r = check_solution(slow, "assert f(1)==1\n", "assert f(2)==2\n", timeout=3.0)
    assert r.verdict == "fail"


def test_all_fixture_references_pass_and_buggy_fail():
    for fx in FIXTURES:
        ref = check_solution(fx["reference"], fx["visible_tests"], fx["hidden_tests"])
        assert ref.verdict == "pass", f"{fx['id']} reference should pass: {ref.asdict()}"
        for b in fx.get("buggy", []):
            r = check_solution(b["code"], fx["visible_tests"], fx["hidden_tests"])
            assert r.verdict == "fail", f"{fx['id']}/{b['label']} should fail but passed"
