"""Phase 2 Layer-A graded eval: metric math + end-to-end sensitivity.

The pure metric functions are checked against hand-computed values (a plain-mean
or off-by-one bug would silently pass a self-consistent check, so the numbers
here are worked out by hand). The end-to-end test builds a tiny corpus and
confirms the graded/chunk metrics actually respond to retrieval quality.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from cortex_core import graded_eval as ge
from cortex_core.search import CortexSearchIndex


# --- pure metric math (hand-computed) -------------------------------------


def test_dcg_matches_hand_computed():
    # grades [3, 0, 2] at ranks 1,2,3:
    #  (2^3-1)/log2(2) + (2^0-1)/log2(3) + (2^2-1)/log2(4)
    #  = 7/1 + 0 + 3/2 = 8.5
    assert ge._dcg([3, 0, 2]) == pytest.approx(7 + 0 + 1.5)


def test_ndcg_at_k_penalizes_missing_relevant_docs():
    # Retrieved grades [3,0,2]; full relevant set {3,2,1}.
    # DCG@3 = 8.5 ; IDCG@3 = ideal [3,2,1] = 7/1 + 3/log2(3) + 1/log2(4)
    retrieved = [3, 0, 2]
    all_relevant = [3, 2, 1]
    idcg = 7 + 3 / math.log2(3) + 1 / math.log2(4)
    assert ge.ndcg_at_k(retrieved, all_relevant, 3) == pytest.approx(8.5 / idcg)


def test_ndcg_is_1_for_perfect_ranking():
    assert ge.ndcg_at_k([3, 2, 1], [3, 2, 1], 3) == pytest.approx(1.0)


def test_ndcg_is_0_when_nothing_relevant_retrieved():
    assert ge.ndcg_at_k([0, 0, 0], [3, 2], 3) == 0.0


def test_context_precision_counts_relevant_in_top_k():
    # top-5 grades [3,0,1,0,0] -> 2 of 5 relevant
    assert ge.context_precision_at_k([3, 0, 1, 0, 0], 5) == pytest.approx(2 / 5)


# --- end-to-end sensitivity ------------------------------------------------


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    shard = ws / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "canonical.md").write_text(
        "# Widget Guide\n\nThe widget assembly uses a torque wrench and a "
        "calibration jig for the gearbox.\n",
        encoding="utf-8",
    )
    (shard / "tangential.md").write_text(
        "# Notes\n\nWidgets are mentioned here in passing among many topics.\n",
        encoding="utf-8",
    )
    return ws


def test_graded_eval_runs_and_scores_a_tiny_corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    graded = tmp_path / "graded.yaml"
    graded.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "widget torque wrench calibration jig gearbox",
                        "category": "and",
                        "relevant": [
                            {"doc": "docs/cortex-1/canonical.md", "grade": 3, "chunk": "torque wrench"},
                            {"doc": "docs/cortex-1/tangential.md", "grade": 1},
                        ],
                    },
                    {"query": "zzzn, unrelatedzzz nonexistent", "category": "negative", "relevant": []},
                ]
            }
        ),
        encoding="utf-8",
    )

    m = ge.run_graded_eval(workspace=str(ws), graded_path=graded, k=5)

    assert m["n_positive"] == 1
    assert m["n_negative"] == 1
    # The canonical (grade-3) doc should rank at/near the top -> high nDCG.
    assert m["mean_ndcg_at_k"] > 0.5
    # The chunk marker "torque wrench" is in canonical.md's chunk -> chunk hit.
    assert m["chunk_recall_at_k"] == pytest.approx(1.0)
    assert m["chunk_mrr"] > 0.0
    # The negative query returns nothing -> counted correct.
    assert m["negatives_correct"] == 1


def test_cli_prints_graded_metrics(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    graded = tmp_path / "graded.yaml"
    graded.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "widget torque wrench",
                        "category": "and",
                        "relevant": [{"doc": "docs/cortex-1/canonical.md", "grade": 3, "chunk": "torque wrench"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rc = ge.main(["--workspace", str(ws), "--graded", str(graded)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nDCG@5" in out
    assert "chunk_recall@5" in out
