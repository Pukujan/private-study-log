"""Frozen tests for the Stage-2F objective regex_correctness checker.

Written BEFORE checker_regex.py exists (TDD): these assert the checker's verdicts against known
fixtures. The oracle is EXECUTION (Python `re` against a labeled must_match/must_not_match
string set) -- never a model judge. See evals/objective_regex_correctness/SPEC.md.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_regex_correctness.checker_regex import grade_regex  # noqa: E402
from evals.objective_regex_correctness.fixtures_regex import fixtures  # noqa: E402


def _rec(id_):
    for r in fixtures():
        if r["id"] == id_:
            return r
    raise KeyError(id_)


# --- direct oracle checks (bypass fixtures, exercise grade_regex's contract directly) --------

def test_correct_regex_matching_and_rejecting_as_specified_is_correct():
    result = grade_regex(
        candidate_regex=r"^\d{5}$",
        match_mode="fullmatch",
        must_match=["12345"],
        must_not_match=["1234"],
    )
    assert result["verdict"] == "correct"
    assert result["mismatches"] == []


def test_regex_that_rejects_a_must_match_string_is_incorrect():
    result = grade_regex(
        candidate_regex=r"^\d{4}$",
        match_mode="fullmatch",
        must_match=["12345"],
        must_not_match=["1234"],
    )
    assert result["verdict"] == "incorrect"
    assert any(m["expected"] == "match" for m in result["mismatches"])


def test_regex_that_accepts_a_must_not_match_string_is_incorrect():
    result = grade_regex(
        candidate_regex=r"^\d{3,}$",
        match_mode="fullmatch",
        must_match=["12345"],
        must_not_match=["1234567890123"],
    )
    assert result["verdict"] == "incorrect"
    assert any(m["expected"] == "no_match" for m in result["mismatches"])


def test_search_mode_is_respected():
    # unanchored pattern used with search: substring hits count as a match.
    result = grade_regex(
        candidate_regex=r"\d{3}",
        match_mode="search",
        must_match=["abc123"],
        must_not_match=["abcxyz"],
    )
    assert result["verdict"] == "correct"


def test_compile_error_is_incorrect_not_a_crash():
    result = grade_regex(
        candidate_regex=r"^(unclosed",
        match_mode="fullmatch",
        must_match=["x"],
        must_not_match=["y"],
    )
    assert result["verdict"] == "incorrect"
    assert result["failure_reason"] == "compile_error"


def test_catastrophic_backtracking_fails_safe_within_guard():
    # (a+)+ against a long near-miss string is classically exponential; the checker must return
    # promptly (bounded by the guard) rather than hang, and must grade it incorrect.
    result = grade_regex(
        candidate_regex=r"^([a-z]+)+$",
        match_mode="fullmatch",
        must_match=["hello"],
        must_not_match=["a" * 25 + "!"],
        timeout_s=0.5,
    )
    assert result["verdict"] == "incorrect"
    assert "catastrophic_backtracking" in result["failure_reason"]


def test_safe_regex_on_long_input_does_not_time_out():
    result = grade_regex(
        candidate_regex=r"^[a-z]+$",
        match_mode="fullmatch",
        must_match=["a" * 200],
        must_not_match=["a" * 25 + "!"],
        timeout_s=0.5,
    )
    assert result["verdict"] == "correct"


# --- fixture-level agreement: every fixture's checker verdict must match its authored label ---

def test_all_correct_fixtures_grade_correct():
    for rec in fixtures():
        if rec["objective_label"] != "correct":
            continue
        result = grade_regex(rec["candidate_regex"], rec["match_mode"],
                             rec["must_match"], rec["must_not_match"])
        assert result["verdict"] == "correct", (rec["id"], result)


def test_all_incorrect_fixtures_grade_incorrect():
    for rec in fixtures():
        if rec["objective_label"] != "incorrect":
            continue
        result = grade_regex(rec["candidate_regex"], rec["match_mode"],
                             rec["must_match"], rec["must_not_match"])
        assert result["verdict"] == "incorrect", (rec["id"], result)


def test_every_failure_class_is_exercised_by_at_least_one_fixture():
    taxonomy = {
        "too_permissive", "too_restrictive", "missing_anchor", "wrong_charclass",
        "unescaped_metachar", "off_by_one_quantifier", "catastrophic_backtracking",
    }
    seen = {r["failure_class"] for r in fixtures() if r["failure_class"]}
    assert taxonomy <= seen, taxonomy - seen


def test_every_incorrect_fixture_has_a_source_correct_id_and_it_exists():
    ids = {r["id"] for r in fixtures()}
    for rec in fixtures():
        if rec["objective_label"] == "incorrect":
            assert rec["source_correct_id"] in ids, rec["id"]
        else:
            assert rec["source_correct_id"] is None, rec["id"]


def test_mutation_integrity_correct_and_incorrect_share_the_same_string_sets():
    """Every incorrect record must reuse EXACTLY its source's must_match/must_not_match sets --
    only the candidate_regex differs. This is what makes the mutation a controlled experiment."""
    for rec in fixtures():
        if rec["objective_label"] != "incorrect":
            continue
        src = _rec(rec["source_correct_id"])
        assert rec["must_match"] == src["must_match"], rec["id"]
        assert rec["must_not_match"] == src["must_not_match"], rec["id"]
        assert rec["candidate_regex"] != src["candidate_regex"], rec["id"]


def test_fixture_count_in_expected_range():
    n = len(fixtures())
    assert 18 <= n <= 20, n
