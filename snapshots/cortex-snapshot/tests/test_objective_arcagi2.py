"""Frozen tests for the objective ARC-AGI-2 grid lane.

Covers: (1) hand-picked grid-equality cases against the checker, (2) the checker's own self_test,
(3) that every hard_gold record's objective_label is reproduced by the checker from
candidate_output vs expected_output, and (4) structural invariants (counts, balance, every INCORRECT
candidate truly differs from expected and names its mutation, no judge in the verdict path).
Fast: pure stdlib list comparisons, no model, no network, no execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.objective_arcagi2.checker_arcagi2 import check_record, grids_equal, self_test

ROOT = Path(__file__).resolve().parents[1]
LANE = ROOT / "evals" / "objective_arcagi2"
HARD_GOLD = LANE / "hard_gold.jsonl"


# ---- 1. hand-picked grid-equality cases -----------------------------------------------------

def test_equal_grids_are_correct():
    g = [[1, 2, 3], [4, 5, 6]]
    r = check_record([[1, 2, 3], [4, 5, 6]], g)
    assert r.objective_label == "CORRECT"
    assert r.computed_answer == "MATCH"


def test_single_cell_diff_is_incorrect():
    g = [[1, 2], [3, 4]]
    r = check_record([[1, 2], [3, 9]], g)
    assert r.objective_label == "INCORRECT"
    assert r.computed_answer == "MISMATCH"


def test_shape_diff_row_count_is_incorrect():
    g = [[1, 2], [3, 4]]
    assert check_record([[1, 2]], g).objective_label == "INCORRECT"


def test_shape_diff_row_width_is_incorrect():
    assert grids_equal([[1, 2, 3]], [[1, 2]]) is False


def test_transpose_of_nonsquare_differs():
    rect = [[1, 2, 3], [4, 5, 6]]
    transposed = [[1, 4], [2, 5], [3, 6]]
    assert grids_equal(transposed, rect) is False
    assert check_record(transposed, rect).objective_label == "INCORRECT"


def test_list_tuple_normalization_equal():
    assert grids_equal([[1, 2], [3, 4]], ((1, 2), (3, 4))) is True


def test_non_grid_never_equal():
    g = [[1, 2], [3, 4]]
    assert grids_equal(None, g) is False
    assert grids_equal(g, 5) is False
    assert grids_equal(g, {"a": 1}) is False
    assert grids_equal([[True, False]], [[1, 0]]) is False  # bool cells rejected


def test_checker_self_test_passes(capsys):
    self_test()
    assert "self_test ok" in capsys.readouterr().out


# ---- hard_gold loading ----------------------------------------------------------------------

def _load_hard_gold():
    assert HARD_GOLD.exists(), "hard_gold.jsonl missing -- run run_arcagi2.py first"
    rows = []
    for line in HARD_GOLD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


HARD_GOLD_ROWS = _load_hard_gold() if HARD_GOLD.exists() else []


# ---- 2. checker reproduces every stored objective_label -------------------------------------

@pytest.mark.parametrize("rec", HARD_GOLD_ROWS, ids=lambda r: r["id"])
def test_checker_reproduces_stored_label(rec):
    r = check_record(rec["candidate_output"], rec["expected_output"])
    assert r.objective_label == rec["objective_label"], (
        f"{rec['id']}: checker={r.objective_label} stored={rec['objective_label']}"
    )
    assert r.computed_answer == rec["computed_answer"]


# ---- 3. structural invariants ---------------------------------------------------------------

def test_hard_gold_nonempty_and_balanced():
    rows = _load_hard_gold()
    assert len(rows) >= 16, "expected at least 8 CORRECT + 8 INCORRECT"
    correct = [r for r in rows if r["objective_label"] == "CORRECT"]
    incorrect = [r for r in rows if r["objective_label"] == "INCORRECT"]
    assert len(correct) >= 8
    assert len(incorrect) >= 8
    # exact balance: one CORRECT + one INCORRECT per consumed test item
    assert len(correct) == len(incorrect)


def test_every_incorrect_candidate_truly_differs_and_names_mutation():
    known_mutations = {
        "single_cell_flip", "row_dropped", "row_duplicated", "transposed", "dim_changed",
    }
    for r in _load_hard_gold():
        if r["objective_label"] == "INCORRECT":
            assert not grids_equal(r["candidate_output"], r["expected_output"]), (
                f"{r['id']}: INCORRECT candidate equals expected -- mislabeled"
            )
            assert r["mutation"] in known_mutations, f"{r['id']}: unnamed mutation {r['mutation']!r}"
            assert r["failure_class"] == r["mutation"]


def test_every_correct_candidate_equals_expected():
    for r in _load_hard_gold():
        if r["objective_label"] == "CORRECT":
            assert grids_equal(r["candidate_output"], r["expected_output"]), (
                f"{r['id']}: CORRECT candidate does not equal expected"
            )
            assert r["mutation"] == ""
            assert r["failure_class"] == "none"


def test_all_records_carry_required_fields():
    for r in _load_hard_gold():
        for field in ("task_id", "train", "test_input", "candidate_output", "expected_output",
                      "objective_label", "failure_class", "label_authority"):
            assert field in r, f"{r['id']}: missing field {field}"
        assert r["label_authority"] == "grid_exact_match"
        assert r["objective_label"] in ("CORRECT", "INCORRECT")


def test_promotion_record_declares_no_judge():
    promo = LANE / "PROMOTION.jsonl"
    assert promo.exists()
    lines = [l for l in promo.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["lane"] == "arcagi2"
    assert rec["label_authority"] == "grid_exact_match"
    assert rec["label_field"] == "objective_label"
    assert rec["judge_in_verdict_path"] is False
    assert rec["count"] == len(_load_hard_gold())


def test_checker_module_has_no_judge_or_network_import():
    """AST-based (like scripts/ci/lanes.py): only real import statements count, so a docstring
    that merely names a forbidden module is fine -- what matters is the verdict path never
    imports a judge / LLM dispatch / network client / subprocess-execution module."""
    import ast

    tree = ast.parse((LANE / "checker_arcagi2.py").read_text(encoding="utf-8"))
    forbidden = ("cortex_core.judge", "cortex_core.codex_judge", "anthropic", "openai",
                 "httpx", "requests", "subprocess")
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imported.add(node.module)
    for name in imported:
        for bad in forbidden:
            assert not (name == bad or name.startswith(bad + ".")), \
                f"checker imports forbidden {name!r}"
