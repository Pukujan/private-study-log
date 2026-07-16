"""GAP A5 -- frozen tests for the findability metrics + grep baseline.

Tests the metric machinery and the grep ranker on a tiny synthetic corpus; the
live hybrid-vs-grep numbers are produced by running findability_eval.py against
the real index (reported separately), not asserted here.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent / "evals" / "findability"
sys.path.insert(0, str(MOD))

import findability_eval as fe  # noqa: E402


def test_metrics_recall_and_mrr():
    ranks = [1, 2, None, 5, 11]  # found at 1,2,5,11 ; one miss
    m = fe._metrics(ranks)
    assert m["n"] == 5 and m["found"] == 4
    assert m["recall@1"] == 0.2       # only rank-1
    assert m["recall@3"] == 0.4       # ranks 1,2
    assert m["recall@5"] == 0.6       # 1,2,5
    assert m["recall@10"] == 0.6      # 11 excluded
    assert abs(m["mrr"] - (1 + 0.5 + 0.2 + (1 / 11)) / 5) < 1e-3  # mrr rounded to 4dp


def test_grep_rank_frequency_ranked(tmp_path):
    c = fe.Corpus(
        workspace=tmp_path,
        rel_paths=["a.md", "b.md", "c.md"],
        text_lower={
            "a.md": "kanban board sprint kanban kanban",  # 3 hits of 'kanban'
            "b.md": "kanban once here",                    # 1 hit
            "c.md": "unrelated text",                      # 0 hits
        },
    )
    assert fe.grep_rank(c, "kanban", "a.md") == 1
    assert fe.grep_rank(c, "kanban", "b.md") == 2
    assert fe.grep_rank(c, "kanban", "c.md") is None  # never matches -> not found


def test_grep_miss_when_terms_absent(tmp_path):
    c = fe.Corpus(tmp_path, ["a.md"], {"a.md": "the quick brown fox"})
    # semantic paraphrase with no shared terms -> grep cannot find it
    assert fe.grep_rank(c, "rapid auburn canine", "a.md") is None


def test_load_golden_parses_pairs(tmp_path):
    y = tmp_path / "g.yaml"
    y.write_text(
        "queries:\n"
        '  - query: "alpha beta"\n'
        '    expected_doc: "docs/x.md"\n'
        "    category: and\n"
        '  - query: "gamma"\n'
        '    expected_doc: "docs/y.md"\n',
        encoding="utf-8",
    )
    g = fe.load_golden(y)
    assert len(g) == 2
    assert g[0]["query"] == "alpha beta" and g[0]["expected_doc"] == "docs/x.md"
