"""Frozen tests for the STRUCTURAL no-judge guarantee (terra red-team Finding 2, Critical,
`reviewed/oracle-machinery-redteam-terra-2026-07-14.md`).

Before this fix, `evals.oracle_adapter.is_verdict_safe` trusted one optional, caller-supplied
diagnostics flag (`diagnostics["llm_judge_in_verdict"]`). An adapter could dispatch an LLM
judge, simply omit that key, and the guardrail returned True — advisory, not structural, and
trivially forgeable.

This proves the fix is real: `is_verdict_safe(result, verdict_modules=[...])` now AST-scans the
adapter's actual verdict-path source file(s) for judge/LLM/network imports and CANNOT be
defeated by omitting a diagnostics key. A lane whose verdict module gains a judge import must
fail this check — that is the load-bearing regression test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.oracle_adapter import (  # noqa: E402
    OracleResult,
    FORBIDDEN_VERDICT_IMPORT_PREFIXES,
    is_verdict_safe,
    module_forbidden_imports,
    verdict_path_is_judge_free,
)


def _clean_result() -> OracleResult:
    return OracleResult(passed=True, score=1.0, checks={"x": True})


def _forged_result() -> OracleResult:
    # The exact forgery Finding 2 describes: an adapter that used a judge but simply never
    # set the diagnostics flag.
    return OracleResult(passed=True, score=1.0, checks={"x": True}, diagnostics={})


# ---- 1. the diagnostics flag alone is still checked (unchanged, backward compatible) --------

def test_diagnostics_flag_still_fails_when_explicitly_set():
    result = OracleResult(passed=True, score=1.0, diagnostics={"llm_judge_in_verdict": True})
    safe, reason = is_verdict_safe(result)
    assert not safe
    assert "judge" in reason.lower()


def test_omitted_diagnostics_flag_with_no_verdict_modules_is_advisory_only():
    # The pre-fix forgery: no verdict_modules supplied -> the old, weaker, forgeable path.
    # It still returns True, but the message must be honest that nothing was structurally
    # verified (never silently claim a stronger guarantee than was actually checked).
    safe, reason = is_verdict_safe(_forged_result())
    assert safe
    assert "advisory" in reason.lower() or "not verified" in reason.lower()


# ---- 2. THE STRUCTURAL CHECK: an adapter that forged the flag is still caught ----------------

def test_structural_check_catches_forged_result_via_real_judge_import(tmp_path):
    bad_module = tmp_path / "sneaky_checker.py"
    bad_module.write_text(
        "from cortex_core import judge\n\n"
        "def check_trace(scenario, trace):\n"
        "    return judge.dispatch(scenario, trace)\n",
        encoding="utf-8",
    )
    # Omitted diagnostics flag (the forgery) — must STILL fail once real source is scanned.
    safe, reason = is_verdict_safe(_forged_result(), verdict_modules=[bad_module])
    assert not safe
    assert "judge" in reason.lower()


@pytest.mark.parametrize("forbidden_import", [
    "from cortex_core import judge",
    "import cortex_core.judge",
    "from cortex_core import evaluator",
    "import judge",
    "from judge import score",
    "import evaluator",
    "import anthropic",
    "import openai",
    "from openai import OpenAI",
    "import httpx",
    "import requests",
    "import urllib.request",
    "from urllib.request import urlopen",
])
def test_structural_check_catches_every_forbidden_import(tmp_path, forbidden_import):
    mod = tmp_path / "checker.py"
    mod.write_text(f"{forbidden_import}\n\ndef check_trace(s, t):\n    return True\n",
                    encoding="utf-8")
    clean, problems = verdict_path_is_judge_free([mod])
    assert not clean, f"{forbidden_import!r} should have been caught"
    assert problems


def test_structural_check_passes_a_genuinely_clean_module(tmp_path):
    mod = tmp_path / "checker.py"
    mod.write_text(
        "import json\nimport re\n\n"
        "def check_trace(scenario, trace):\n"
        "    return scenario['expected'] == trace\n",
        encoding="utf-8",
    )
    clean, problems = verdict_path_is_judge_free([mod])
    assert clean
    assert problems == []


def test_structural_check_does_not_false_positive_on_prose_mentioning_judge(tmp_path):
    # A docstring/comment merely discussing "judge" or "LLM" in prose (explaining their
    # ABSENCE, as every objective-lane checker's module docstring does) must not trip the
    # AST-based scan — only real import statements count.
    mod = tmp_path / "checker.py"
    mod.write_text(
        '"""No LLM judge anywhere in this verdict path. We never call a judge."""\n'
        "import json\n\n"
        "def check_trace(s, t):\n"
        "    return True  # not a judge call\n",
        encoding="utf-8",
    )
    clean, problems = verdict_path_is_judge_free([mod])
    assert clean, problems


def test_syntax_error_module_is_never_trusted(tmp_path):
    mod = tmp_path / "checker.py"
    mod.write_text("def broken(:\n", encoding="utf-8")
    clean, problems = verdict_path_is_judge_free([mod])
    assert not clean
    assert any("syntax" in p.lower() for p in problems)


# ---- 3. real repo lanes: the structural check must pass over EVERY discovered lane's ----------
#         actual verdict-path modules — this is the regression gate that fails the moment any
#         lane's checker gains a judge import, wired into the shared lane-integrity CI gate.

def test_every_discovered_lane_verdict_module_is_structurally_judge_free():
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from lanes import discover_lanes  # noqa: E402

    lanes = discover_lanes()
    assert lanes, "no evals/objective_* lanes discovered -- discovery is broken"
    failures = []
    for lane in lanes:
        for mod in lane.verdict_modules:
            clean, problems = verdict_path_is_judge_free([mod])
            if not clean:
                failures.append((lane.name, str(mod), problems))
    assert not failures, f"lane verdict modules with judge/LLM/network imports: {failures}"


def test_prompt_injection_trace_checker_is_structurally_judge_free():
    # The specific lane the red-team report scrutinized.
    checker_path = ROOT / "evals" / "objective_prompt_injection_trace" / "checker.py"
    result = OracleResult(passed=True, score=1.0)
    safe, reason = is_verdict_safe(result, verdict_modules=[checker_path])
    assert safe, reason


# ---- 4. the two forbidden-import lists (this module's and the CI gate's) must not drift -------

def test_forbidden_import_list_matches_ci_lane_gate():
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    import lanes as L  # noqa: E402

    assert set(FORBIDDEN_VERDICT_IMPORT_PREFIXES) == set(L.FORBIDDEN_IMPORT_PREFIXES), (
        "evals/oracle_adapter.py's FORBIDDEN_VERDICT_IMPORT_PREFIXES has drifted from "
        "scripts/ci/lanes.py's FORBIDDEN_IMPORT_PREFIXES -- these are deliberately mirrored "
        "(not imported, to keep oracle_adapter.py dependency-free) and must be kept in sync "
        "by hand whenever either list changes."
    )


def test_module_forbidden_imports_is_pure_stdlib_ast_based():
    # No third-party dependency: importing this module must not require anything beyond the
    # stdlib `ast` module (the whole point is it can run in the bare CI install).
    src = (ROOT / "evals" / "oracle_adapter.py").read_text(encoding="utf-8")
    assert "import ast" in src
