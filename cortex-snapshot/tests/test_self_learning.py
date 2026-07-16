"""Frozen tests for the self-learning oracle miner (cortex_core/self_learning.py).

GAP G1: replay past FAILED Cortex closeouts against a DETERMINISTIC check to mint
local positive / anti_pattern / UNVERIFIABLE candidates. The rule is FIXED and these
tests pin it:

  * a task that deterministically FAILED whose later attempt PASSES -> positive
  * a task that FAILED and never passes                            -> anti_pattern
  * a task with no deterministic outcome to decide                 -> UNVERIFIABLE
    (quarantined, NEVER guessed)

Plus the two hard invariants: no LLM in the verdict path, and nothing is ever
auto-promoted to trainable gold (promotion is a separate, human-gated step).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import self_learning as sl  # noqa: E402


def _rec(task, *, tests="", status="completed", ts="2026-07-01T00:00:00+00:00",
         evidence=None, source="x.json", contract_id=""):
    return {
        "task": task, "tests": tests, "status": status, "timestamp": ts,
        "evidence": evidence or [], "contract_id": contract_id, "_source": source,
    }


# --------------------------------------------------------------------- the three-way rule
def test_failed_then_passed_is_positive():
    recs = [
        _rec("Fix the parser", tests="1 failed", status="failed",
             ts="2026-07-01T00:00:00+00:00", source="a.json"),
        _rec("Fix the parser", tests="9 passed", status="completed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    cands = sl.mine(recs)
    assert len(cands) == 1
    c = cands[0]
    assert c["label"] == sl.POSITIVE
    # the fix's CoT/gold lives in the PASSING attempt's closeout
    assert c["fix_source"] == "b.json"
    assert c["outcomes"] == [False, True]


def test_failed_never_passed_is_anti_pattern():
    recs = [
        _rec("Wire the gate", tests="2 failed", status="failed", source="a.json"),
        _rec("Wire the gate", tests="3 failed", status="failed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    cands = sl.mine(recs)
    assert len(cands) == 1
    assert cands[0]["label"] == sl.ANTI_PATTERN
    assert cands[0]["fix_source"] is None


def test_no_deterministic_signal_is_unverifiable_and_never_guessed():
    # empty tests + a status that is neither a fail nor a pass token -> undecidable.
    recs = [
        _rec("Investigate flakiness", tests="", status="in-progress", source="a.json"),
        _rec("Investigate flakiness", tests="", status="unknown",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    cands = sl.mine(recs)
    assert len(cands) == 1
    c = cands[0]
    assert c["label"] == sl.UNVERIFIABLE
    # NEVER guessed toward positive/anti — outcomes stay None.
    assert c["outcomes"] == [None, None]
    assert c["fix_source"] is None


def test_pass_only_task_is_not_a_failure_fix_oracle():
    # a task that only ever passed is not failure->fix gold — it must not be mined.
    recs = [
        _rec("Add docs", tests="5 passed", status="completed", source="a.json"),
        _rec("Add docs", tests="6 passed", status="completed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    assert sl.mine(recs) == []


def test_failure_plus_undecidable_stays_anti_pattern_not_guessed_positive():
    # one real failure, one undecidable attempt, NO pass anywhere -> anti_pattern
    # (a decisive failure exists; the undecidable attempt is never guessed as a pass).
    recs = [
        _rec("Handle timeout", tests="1 failed", status="failed", source="a.json"),
        _rec("Handle timeout", tests="", status="in-progress",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    cands = sl.mine(recs)
    assert len(cands) == 1
    assert cands[0]["label"] == sl.ANTI_PATTERN


# --------------------------------------------------------------------- deterministic signals
def test_structured_test_evidence_exit_code_decides():
    # v2 structured evidence (a test exit code) is the strongest signal.
    fail = _rec("Build X", tests="", status="completed", source="a.json",
                evidence=[{"type": "test", "ref": "pytest", "detail": "exit 1"}])
    ok = _rec("Build X", tests="", status="completed",
              ts="2026-07-02T00:00:00+00:00", source="b.json",
              evidence=[{"type": "test", "ref": "pytest", "detail": "exit 0"}])
    assert sl.test_outcome(fail)[0] is False
    assert sl.test_outcome(ok)[0] is True
    cands = sl.mine([fail, ok])
    assert cands and cands[0]["label"] == sl.POSITIVE


def test_ratio_pass_and_fail_parsing():
    assert sl.test_outcome(_rec("t", tests="17/17"))[0] is True          # bare N/N all-pass
    assert sl.test_outcome(_rec("t", tests="6/6 tests"))[0] is True      # keyword-adjacent
    assert sl.test_outcome(_rec("t", tests="2/17 passed"))[0] is False   # keyword-adjacent partial
    assert sl.test_outcome(_rec("t", tests="pytest 17 passed"))[0] is True
    assert sl.test_outcome(_rec("t", tests="2 failed"))[0] is False       # one-sided fail
    # genuinely empty/ambiguous -> None (undecidable), never a guessed bool
    assert sl.test_outcome(_rec("t", tests="", status="draft"))[0] is None


def test_mixed_pass_and_fail_line_is_ambiguous_not_guessed():
    # a regex cannot distinguish a real partial failure from a pass reported with an
    # unrelated pre-existing failure; both signals present -> UNDECIDABLE (None).
    assert sl.test_outcome(_rec("t", tests="359 passed/skipped, 1 failed"))[0] is None
    assert sl.test_outcome(
        _rec("t", tests="63 passed, 10 failed (unrelated pre-existing)"))[0] is None
    # an authoritative failure STATUS still decides, regardless of prose.
    assert sl.test_outcome(_rec("t", tests="5 passed", status="failed"))[0] is False


def test_no_tests_run_is_undecidable_not_a_failure():
    # "no tests run" is absence-of-signal, not a failure -> UNVERIFIABLE, never anti_pattern.
    assert sl.test_outcome(
        _rec("t", tests="No code changes made (pure investigation); no tests run."))[0] is None


def test_bare_unequal_ratio_is_not_guessed_as_failure():
    # a length list / date / path like "0/1/10/100" must NOT be read as a test
    # ratio failure — that was a real over-match; ambiguous -> None (never guessed).
    assert sl.test_outcome(_rec("t", tests="inline checks for lengths 0/1/10/100"))[0] is None
    # a pass reported alongside an unrelated pre-existing failure is still a pass.
    assert sl.test_outcome(
        _rec("t", tests="62 tests passed; 1 pre-existing unrelated failure"))[0] is True


def test_grouping_normalizes_task_key():
    recs = [
        _rec("Fix The Parser!", tests="1 failed", status="failed", source="a.json"),
        _rec("fix-the-parser", tests="9 passed", status="completed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    cands = sl.mine(recs)
    assert len(cands) == 1  # both attempts grouped
    assert cands[0]["label"] == sl.POSITIVE


# --------------------------------------------------------------------- hard invariants
def test_nothing_is_auto_promoted():
    recs = [
        _rec("Fix the parser", tests="1 failed", status="failed", source="a.json"),
        _rec("Fix the parser", tests="9 passed", status="completed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    for c in sl.mine(recs):
        assert c["promoted"] is False
        assert c["promotion_status"] == "quarantined"


def test_no_llm_or_judge_in_the_verdict_path():
    # the verdict path must be pure deterministic parsing — never a model, never
    # the network. Inspect actual IMPORTS (not prose), so the docstring can still
    # say "never a judge" without tripping the guard.
    import ast

    tree = ast.parse(Path(sl.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    banned = ("judge", "codex_judge", "openai", "anthropic", "requests", "httpx",
              "urllib.request", "urllib", "socket", "http.client")
    hits = [m for m in imported for b in banned if b in m]
    assert not hits, f"self_learning must not import LLM/network modules: {hits}"
    # and it must NOT reach into the promotion state machine (promotion is human-gated).
    assert not any("promotion" in m for m in imported)


def test_mining_is_deterministic():
    recs = [
        _rec("Fix the parser", tests="1 failed", status="failed", source="a.json"),
        _rec("Fix the parser", tests="9 passed", status="completed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    assert sl.mine(recs) == sl.mine(list(reversed(recs)))  # order-independent labels


# --------------------------------------------------------------------- IO / CLI plumbing
def test_load_closeouts_reads_json_recursively(tmp_path):
    d = tmp_path / "audit" / "audit-log-1" / "agent"
    d.mkdir(parents=True)
    (d / "one.json").write_text(json.dumps(_rec("A", tests="1 failed", status="failed")))
    (d / "two.json").write_text(json.dumps(_rec("A", tests="2 passed")))
    (d / "notacloseout.json").write_text(json.dumps({"foo": "bar"}))
    (d / "broken.json").write_text("{ not json")
    recs = sl.load_closeouts(d)
    assert len(recs) == 2  # only real closeouts with a `task`; junk skipped
    assert all("_source" in r for r in recs)


def test_write_candidates_jsonl_roundtrip_and_quarantine_marker(tmp_path):
    recs = [
        _rec("Fix the parser", tests="1 failed", status="failed", source="a.json"),
        _rec("Fix the parser", tests="9 passed", status="completed",
             ts="2026-07-02T00:00:00+00:00", source="b.json"),
    ]
    out = tmp_path / "oracle_candidates.jsonl"
    n = sl.write_candidates(sl.mine(recs), out)
    assert n == 1 and out.exists()
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert rows[0]["label"] == sl.POSITIVE
    assert rows[0]["promoted"] is False
