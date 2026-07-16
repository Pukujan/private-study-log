"""Frozen tests for the objective mbpp checker (Stage-2 test-execution-oracle lane).

LABEL AUTHORITY: Python code EXECUTION (subprocess-isolated), never a model/judge. These tests pin the
checker on hand-picked execution cases (independent of the fixture file), sweep a BOUNDED subset of
hard_gold.jsonl asserting the checker reproduces each declared objective_label, and assert the
structural / balance / self-validation-gate invariants the run script must honor.

Runtime is bounded on purpose: each check_record spawns a subprocess, so the full-file sweep is capped
to a deterministic subset (both labels covered) rather than executing all 460 fixtures.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_mbpp.checker_mbpp import check_record  # noqa: E402
from evals.objective_mbpp.run_mbpp import iter_mutants, STRATEGIES  # noqa: E402

LANE_DIR = ROOT / "evals" / "objective_mbpp"
HARD_GOLD = LANE_DIR / "hard_gold.jsonl"


def _load(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- hand-picked execution cases, independent of the fixture file -----------------------------

def test_correct_solution_passes_all_asserts():
    code = "def add(a, b):\n    return a + b\n"
    r = check_record(code, ["assert add(2, 3) == 5", "assert add(-1, 1) == 0"])
    assert r.objective_label == "PASS", r.asdict()


def test_broken_solution_fails():
    code = "def add(a, b):\n    return a - b\n"
    r = check_record(code, ["assert add(2, 3) == 5"])
    assert r.objective_label == "FAIL", r.asdict()


def test_exception_is_fail_not_a_crash():
    code = "def add(a, b):\n    raise ValueError('boom')\n"
    r = check_record(code, ["assert add(1, 1) == 2"])
    assert r.objective_label == "FAIL", r.asdict()


def test_syntax_error_is_fail():
    code = "def add(a, b)\n    return a + b\n"  # missing colon
    r = check_record(code, ["assert add(1, 1) == 2"])
    assert r.objective_label == "FAIL", r.asdict()


def test_timeout_is_fail_not_a_hang():
    code = "def loop():\n    while True:\n        pass\n"
    r = check_record(code, ["assert loop() is None"], timeout=2.0)
    assert r.objective_label == "FAIL" and r.detail == "TIMEOUT", r.asdict()


def test_test_setup_code_is_prepended():
    code = "def f(x):\n    return x + OFFSET\n"
    r = check_record(code, ["assert f(1) == 11"], test_setup_code="OFFSET = 10")
    assert r.objective_label == "PASS", r.asdict()


def test_empty_test_list_is_refused_as_fail():
    r = check_record("def f():\n    return 1\n", [])
    assert r.objective_label == "FAIL", r.asdict()


# --- full-file sweep (bounded subset): checker reproduces each declared label ------------------

def _sweep_subset():
    rows = _load(HARD_GOLD)
    # deterministic subset covering both labels while bounding subprocess count (~24 execs)
    subset = rows[::20]
    for lab in ("PASS", "FAIL"):
        if not any(r["objective_label"] == lab for r in subset):
            subset += [r for r in rows if r["objective_label"] == lab][:1]
    return subset


@pytest.mark.parametrize("rec", _sweep_subset(), ids=lambda r: r["task_id"])
def test_checker_reproduces_declared_label(rec):
    r = check_record(rec["candidate_code"], rec["test_list"], rec.get("test_setup_code", ""))
    assert r.objective_label == rec["objective_label"], (rec["task_id"], r.asdict())


# --- structural / balance / provenance invariants over the full file --------------------------

def test_hard_gold_exists_and_nonempty():
    rows = _load(HARD_GOLD)
    assert 300 <= len(rows) <= 500, len(rows)


def test_label_field_present_and_valued_on_every_record():
    for rec in _load(HARD_GOLD):
        assert rec["objective_label"] in ("PASS", "FAIL"), rec
        assert rec["label_authority"] == "subprocess_test_execution"


def test_label_distribution_is_balanced():
    dist = Counter(r["objective_label"] for r in _load(HARD_GOLD))
    assert dist["PASS"] >= 100 and dist["FAIL"] >= 100, dist
    assert dist["PASS"] == dist["FAIL"], dist  # paired by construction


def test_every_source_has_both_a_pass_and_fail_record():
    by_src = {}
    for r in _load(HARD_GOLD):
        by_src.setdefault(r["source_task_id"], set()).add(r["objective_label"])
    for src, labels in by_src.items():
        assert labels == {"PASS", "FAIL"}, (src, labels)


def test_pass_records_are_dataset_reference_and_fail_records_are_mutants():
    for r in _load(HARD_GOLD):
        if r["objective_label"] == "PASS":
            assert r["candidate_origin"] == "dataset_reference", r["task_id"]
        else:
            assert r["candidate_origin"] == "deterministic_mutant", r["task_id"]
            assert r.get("mutation"), r["task_id"]


def test_fail_mutant_differs_from_its_pass_reference():
    by_src = {}
    for r in _load(HARD_GOLD):
        by_src.setdefault(r["source_task_id"], {})[r["objective_label"]] = r["candidate_code"]
    for src, pair in by_src.items():
        assert pair["PASS"] != pair["FAIL"], src


def test_task_ids_unique():
    ids = [r["task_id"] for r in _load(HARD_GOLD)]
    assert len(ids) == len(set(ids))


def test_declared_mutations_are_known_strategies():
    known = {name for name, _ in STRATEGIES}
    for r in _load(HARD_GOLD):
        if r["objective_label"] == "FAIL":
            assert r["mutation"] in known, r["mutation"]


# --- self-validation gate is honored (bounded re-execution) -----------------------------------

def test_pass_references_truly_pass_on_a_subset():
    passes = [r for r in _load(HARD_GOLD) if r["objective_label"] == "PASS"]
    for r in passes[::25]:
        got = check_record(r["candidate_code"], r["test_list"], r.get("test_setup_code", ""))
        assert got.objective_label == "PASS", (r["task_id"], got.asdict())


def test_fail_mutants_truly_fail_on_a_subset():
    fails = [r for r in _load(HARD_GOLD) if r["objective_label"] == "FAIL"]
    for r in fails[::25]:
        got = check_record(r["candidate_code"], r["test_list"], r.get("test_setup_code", ""))
        assert got.objective_label == "FAIL", (r["task_id"], got.asdict())


def test_mutation_engine_is_deterministic():
    code = "def f(x):\n    if x > 0:\n        return x + 1\n    return 0\n"
    first = list(iter_mutants(code))
    second = list(iter_mutants(code))
    assert first == second and first, "mutation must be deterministic and non-empty"


# --- promotion-record / no-judge invariants ---------------------------------------------------

def test_no_judge_or_network_import_anywhere_in_checker_module():
    src = (LANE_DIR / "checker_mbpp.py").read_text(encoding="utf-8")
    for banned in ("cortex_core.judge", "codex_judge", "openai", "anthropic", "httpx", "requests"):
        assert banned not in src, f"checker_mbpp.py must not reference {banned!r}"


def test_promotion_record_declares_execution_authority_and_no_judge():
    recs = _load(LANE_DIR / "PROMOTION.jsonl")
    assert len(recs) == 1, recs
    p = recs[0]
    assert p["lane"] == "mbpp"
    assert p["label_authority"] == "subprocess_test_execution"
    assert p["label_field"] == "objective_label"
    assert p["judge_in_verdict_path"] is False
    assert p["count"] == len(_load(HARD_GOLD))
