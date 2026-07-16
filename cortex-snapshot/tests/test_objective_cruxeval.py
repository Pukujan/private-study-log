"""Frozen tests for the objective cruxeval checker (Stage-2 execution-oracle lane).

LABEL AUTHORITY: Python code EXECUTION (subprocess-isolated), never a model/judge. These tests pin
the checker on hand-picked execution cases (independent of the fixture file), sweep a bounded subset
of hard_gold.jsonl asserting the checker reproduces each declared objective_label, and assert the
structural / balance / self-validation-gate invariants the run script must honor.

Runtime is bounded on purpose: each check_record spawns a subprocess, so the full-file sweep is capped
to a deterministic subset (both labels covered) rather than executing all 60 fixtures.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_cruxeval.checker_cruxeval import (  # noqa: E402
    CruxExecutionError, check_record, execute_truth, values_equal,
)
from evals.objective_cruxeval.run_cruxeval import plausible_wrong_value  # noqa: E402

LANE_DIR = ROOT / "evals" / "objective_cruxeval"
HARD_GOLD = LANE_DIR / "hard_gold.jsonl"


def _load(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- hand-picked execution cases, independent of the fixture file -----------------------------

def test_true_output_of_execution_is_correct():
    code = "def f(nums):\n    return sorted(nums, reverse=True)\n"
    r = check_record(code, "[3, 1, 2]", "[3, 2, 1]")
    assert r.objective_label == "CORRECT" and r.computed_answer == "[3, 2, 1]"


def test_wrong_output_is_incorrect():
    code = "def f(nums):\n    return sorted(nums, reverse=True)\n"
    r = check_record(code, "[3, 1, 2]", "[1, 2, 3]")
    assert r.objective_label == "INCORRECT"


def test_multi_arg_input_is_spliced_like_cruxeval():
    code = "def f(a, b):\n    a.update(b)\n    return a\n"
    r = check_record(code, "{}, {'foo': 'bar'}", "{'foo': 'bar'}")
    assert r.objective_label == "CORRECT"


def test_string_output_quotes_and_repr_roundtrip():
    code = "def f(s):\n    return s + 'q'\n"
    r = check_record(code, "'bcksrut'", "'bcksrutq'")
    assert r.objective_label == "CORRECT"
    r2 = check_record(code, "'bcksrut'", "'wrong'")
    assert r2.objective_label == "INCORRECT"


def test_dict_key_order_is_ignored_but_tuple_vs_list_is_not():
    r = check_record("def f():\n    return {'a': 1, 'b': 2}\n", "", "{'b': 2, 'a': 1}")
    assert r.objective_label == "CORRECT"
    r2 = check_record("def f():\n    return (1, 2)\n", "", "[1, 2]")
    assert r2.objective_label == "INCORRECT"


def test_int_output_prediction():
    code = "def f(text):\n    return text.find(',')\n"
    r = check_record(code, '"There are, no"', "9")
    assert r.objective_label == "CORRECT"


def test_raising_snippet_raises_execution_error_never_a_silent_label():
    with pytest.raises(CruxExecutionError):
        check_record("def f(x):\n    return x + 1\n", "'oops'", "0")


def test_timeout_is_execution_error_not_a_hang():
    code = "def f():\n    while True:\n        pass\n"
    with pytest.raises(CruxExecutionError):
        execute_truth(code, "", timeout=2.0)


def test_values_equal_semantics():
    assert values_equal("[1, 2, 3]", "[1,2,3]")           # whitespace-insensitive
    assert values_equal("{'a': 1, 'b': 2}", "{'b': 2, 'a': 1}")  # dict order-insensitive
    assert not values_equal("(1, 2)", "[1, 2]")           # tuple != list
    assert not values_equal("2", "3")


# --- full-file sweep (bounded subset): checker reproduces each declared label ------------------

def _sweep_subset():
    rows = _load(HARD_GOLD)
    # deterministic subset covering both labels while bounding subprocess count
    subset = rows[::4]
    for lab in ("CORRECT", "INCORRECT"):
        if not any(r["objective_label"] == lab for r in subset):
            subset += [r for r in rows if r["objective_label"] == lab][:1]
    return subset


@pytest.mark.parametrize("rec", _sweep_subset(), ids=lambda r: r["task_id"])
def test_checker_reproduces_declared_label(rec):
    r = check_record(rec["code"], rec["input"], rec["candidate_output"])
    assert r.objective_label == rec["objective_label"], rec["task_id"]
    assert r.computed_answer == rec["computed_answer"], rec["task_id"]


# --- structural / balance / provenance invariants over the full file --------------------------

def test_hard_gold_exists_and_nonempty():
    rows = _load(HARD_GOLD)
    assert 20 <= len(rows) <= 80, len(rows)


def test_label_field_present_and_valued_on_every_record():
    for rec in _load(HARD_GOLD):
        assert rec["objective_label"] in ("CORRECT", "INCORRECT"), rec
        assert rec["label_authority"] == "code_execution"


def test_label_distribution_is_balanced():
    dist = Counter(r["objective_label"] for r in _load(HARD_GOLD))
    assert dist["CORRECT"] >= 8 and dist["INCORRECT"] >= 8, dist
    assert dist["CORRECT"] == dist["INCORRECT"], dist  # paired by construction


def test_every_source_has_both_a_correct_and_incorrect_fixture():
    by_src = {}
    for r in _load(HARD_GOLD):
        by_src.setdefault(r["source_task_id"], set()).add(r["objective_label"])
    for src, labels in by_src.items():
        assert labels == {"CORRECT", "INCORRECT"}, (src, labels)


def test_correct_fixture_candidate_equals_computed_truth():
    for r in _load(HARD_GOLD):
        if r["objective_label"] == "CORRECT":
            assert r["candidate_output"] == r["computed_answer"], r["task_id"]


def test_incorrect_fixture_candidate_differs_and_states_its_mutation():
    for r in _load(HARD_GOLD):
        if r["objective_label"] == "INCORRECT":
            assert r["candidate_output"] != r["computed_answer"], r["task_id"]
            assert r.get("mutation"), r["task_id"]


def test_task_ids_unique():
    ids = [r["task_id"] for r in _load(HARD_GOLD)]
    assert len(ids) == len(set(ids))


# --- self-validation gate is honored ----------------------------------------------------------

def test_plausible_wrong_value_always_differs_from_truth():
    for v in [0, 1, -5, 2.5, True, False, "", "abc", "aa", [], [1, 2], [1, 1],
              (1, 2), {}, {"a": 1}, {1, 2}, None]:
        wrong, mutation = plausible_wrong_value(v)
        assert wrong != v, (v, wrong)
        assert isinstance(mutation, str) and mutation


def test_incorrect_fixtures_are_oracle_confirmed_on_a_subset():
    """Re-run the checker on a subset of INCORRECT fixtures -- the wrong answer must still grade
    INCORRECT (the mutation is never assumed wrong, it is execution-confirmed)."""
    incorrect = [r for r in _load(HARD_GOLD) if r["objective_label"] == "INCORRECT"]
    for r in incorrect[::5]:
        assert check_record(r["code"], r["input"], r["candidate_output"]).objective_label == "INCORRECT"


def test_no_judge_or_network_import_anywhere_in_checker_module():
    src = (LANE_DIR / "checker_cruxeval.py").read_text(encoding="utf-8")
    for banned in ("cortex_core.judge", "codex_judge", "openai", "anthropic", "httpx", "requests"):
        assert banned not in src, f"checker_cruxeval.py must not reference {banned!r}"


def test_promotion_record_declares_execution_authority_and_no_judge():
    recs = _load(LANE_DIR / "PROMOTION.jsonl")
    assert len(recs) == 1, recs
    p = recs[0]
    assert p["lane"] == "cruxeval"
    assert p["label_authority"] == "code_execution"
    assert p["label_field"] == "objective_label"
    assert p["judge_in_verdict_path"] is False
    assert p["count"] == len(_load(HARD_GOLD))
