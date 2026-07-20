"""Frozen tests for the objective faithfulness (HaluEval) checker (Stage-2 style lane).

LABEL AUTHORITY: HaluEval's HUMAN annotation, cross-validated by the DETERMINISTIC hardened
faithfulness backend (checker_faithfulness.grounding_verdict / check_record) -- never a model or
judge. These tests pin the checker on hand-picked cases (independent of the generated hard_gold),
sweep every hard_gold record asserting the checker reproduces its objective_label, and assert the
agreement-gate / balance / structural invariants of the built lane.

Written to define the contract: the checker's verdict must be exactly reproducible offline.
"""

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_faithfulness_halueval.checker_faithfulness import (  # noqa: E402
    GROUNDED,
    HALLUCINATED,
    check_record,
    grounding_verdict,
    self_test,
)

LANE_DIR = ROOT / "evals" / "objective_faithfulness_halueval"
HARD_GOLD = LANE_DIR / "hard_gold.jsonl"
QUARANTINE = LANE_DIR / "quarantine.jsonl"
MANIFEST = LANE_DIR / "run_manifest.json"
PROMOTION = LANE_DIR / "PROMOTION.jsonl"
CHECKER = LANE_DIR / "checker_faithfulness.py"


def _load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- hand-picked backend cases, independent of the generated fixtures ------------------------

def test_self_test_passes():
    self_test()


def test_grounded_answer_is_grounded():
    know = "The Oberoi Group is a hotel company with its head office in Delhi."
    assert grounding_verdict("Delhi", know) == GROUNDED
    assert check_record(know, "Delhi", "GROUNDED").objective_label == "CORRECT"


def test_grounded_answer_claimed_hallucinated_is_incorrect():
    know = "The Oberoi Group is a hotel company with its head office in Delhi."
    assert check_record(know, "Delhi", "HALLUCINATED").objective_label == "INCORRECT"


def test_hallucinated_entity_absent_is_hallucinated():
    know = "The Oberoi Group is a hotel company with its head office in Delhi."
    answer = "The Oberoi family's hotel company is based in Mumbai."
    assert grounding_verdict(answer, know) == HALLUCINATED
    assert check_record(know, answer, "HALLUCINATED").objective_label == "CORRECT"


def test_hallucinated_answer_claimed_grounded_is_incorrect():
    know = "The Oberoi Group is a hotel company with its head office in Delhi."
    answer = "The Oberoi family's hotel company is based in Mumbai."
    assert check_record(know, answer, "GROUNDED").objective_label == "INCORRECT"


def test_negation_contradiction_is_hallucinated():
    know = "The build lock is atomic and prevents concurrent rebuilds."
    # a polarity flip on shared topic -> the hardened backend rejects it.
    assert grounding_verdict("The build lock is not atomic.", know) == HALLUCINATED


def test_bad_candidate_token_rejected():
    import pytest
    with pytest.raises(ValueError):
        check_record("some knowledge", "some answer", "MAYBE")


def test_computed_answer_is_canonical_token():
    know = "Paris is the capital of France."
    r = check_record(know, "Paris", "GROUNDED")
    assert r.computed_answer in (GROUNDED, HALLUCINATED)
    assert r.backend == "hardened"


# --- hard_gold sweep: checker must reproduce every objective_label offline --------------------

def test_hard_gold_exists_and_parses():
    assert HARD_GOLD.exists(), "hard_gold.jsonl missing -- run run_faithfulness.py"
    rows = _load_jsonl(HARD_GOLD)
    assert rows, "hard_gold.jsonl is empty"


def test_checker_reproduces_every_hard_gold_label():
    rows = _load_jsonl(HARD_GOLD)
    mismatches = []
    for r in rows:
        got = check_record(r["knowledge"], r["answer"], r["candidate_answer"])
        if got.objective_label != r["objective_label"]:
            mismatches.append((r["id"], r["objective_label"], got.objective_label))
        if got.computed_answer != r["computed_answer"]:
            mismatches.append((r["id"], "computed", r["computed_answer"], got.computed_answer))
    assert mismatches == [], f"checker/hard_gold disagreement: {mismatches[:10]}"


def test_every_hard_gold_carries_objective_label_field():
    for r in _load_jsonl(HARD_GOLD):
        assert r.get("objective_label") in ("CORRECT", "INCORRECT"), r.get("id")
        assert r.get("label_authority") == "halueval_human_annotation"


# --- structural invariants of the built lane --------------------------------------------------

def test_label_distribution_balanced():
    dist = Counter(r["objective_label"] for r in _load_jsonl(HARD_GOLD))
    # exactly balanced by construction (each promoted scenario -> one CORRECT + one INCORRECT)
    assert dist["CORRECT"] == dist["INCORRECT"], dist
    assert dist["CORRECT"] >= 50


def test_human_label_distribution_balanced():
    dist = Counter(r["human_label"] for r in _load_jsonl(HARD_GOLD))
    assert dist[GROUNDED] == dist[HALLUCINATED], dist


def test_ids_unique():
    ids = [r["id"] for r in _load_jsonl(HARD_GOLD)]
    assert len(ids) == len(set(ids))


def test_agreement_gate_promoted_only_where_backend_matches_human():
    """Every promoted record's computed_answer (backend verdict) must equal its human_label --
    the core agreement-gate invariant. A scenario where they differ must be quarantined, not here."""
    for r in _load_jsonl(HARD_GOLD):
        assert r["computed_answer"] == r["human_label"], (
            f"{r['id']}: promoted despite backend {r['computed_answer']} != human {r['human_label']}"
        )


def test_correct_and_incorrect_are_mutation_siblings():
    """Every INCORRECT record shares knowledge+answer+human_label with a CORRECT sibling; only the
    candidate_answer differs (proof it perturbs the decision, not the scenario)."""
    rows = _load_jsonl(HARD_GOLD)
    by_scenario = {}
    for r in rows:
        by_scenario.setdefault((r["source_id"], r["answer"], r["human_label"]), []).append(r)
    for r in rows:
        if r["objective_label"] != "INCORRECT":
            continue
        sibs = by_scenario[(r["source_id"], r["answer"], r["human_label"])]
        assert any(s["objective_label"] == "CORRECT" for s in sibs), r["id"]
        assert r.get("mutation"), f"{r['id']} INCORRECT but no mutation stated"


def test_quarantine_reason_is_backend_human_disagreement():
    rows = _load_jsonl(QUARANTINE)
    if rows:  # quarantine is expected non-empty, but tolerate a re-run producing none
        assert all(q["reason"] == "backend_human_disagreement" for q in rows)
        for q in rows:
            # honest: quarantined exactly because backend verdict != human label
            assert q["computed_answer"] != q["human_label"], q["id"]


def test_manifest_records_agreement_and_counts():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["judge_in_verdict_path"] is False
    assert m["hard_gold"] == len(_load_jsonl(HARD_GOLD))
    assert m["quarantine"] == len(_load_jsonl(QUARANTINE))
    agr = m["backend_human_agreement"]
    assert 0.0 <= agr["agreement_rate"] <= 1.0
    assert agr["overall_total"] == m["scenarios_evaluated"]


def test_promotion_record_shape():
    rows = _load_jsonl(PROMOTION)
    assert len(rows) == 1
    p = rows[0]
    assert p["lane"] == "faithfulness_halueval"
    assert p["label_authority"] == "halueval_human_annotation"
    assert p["label_field"] == "objective_label"
    assert p["judge_in_verdict_path"] is False
    assert p["trainable"] is True
    assert p["count"] == len(_load_jsonl(HARD_GOLD))


def test_checker_imports_no_judge_or_network():
    """AST-level guard: the verdict module must not import a judge / LLM dispatch / network client."""
    forbidden = ("cortex_core.judge", "cortex_core.codex_judge", "anthropic", "openai",
                 "httpx", "requests")
    tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    for mod in imported:
        for bad in forbidden:
            assert not (mod == bad or mod.startswith(bad + ".")), f"forbidden import {mod}"
