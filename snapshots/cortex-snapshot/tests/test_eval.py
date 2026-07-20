"""Tests for the Phase 1 eval harness (cortex_core/eval.py), gates 1.1-1.3.

compute_metrics() is unit-tested against hand-computed expected values and
cross-validated against ranx's reference computation (an optional [eval]
extra, skipped if not installed -- never a hard dependency of the test
suite itself). run_eval()/main() are tested against the real repo's
tests/golden_queries.yaml for the actual gate 1.2 deliverable (prints
recall@5/MRR/zero-result-rate, deterministic, runs fast), plus a CI
regression check (gate 1.3): an intentionally broken ladder must turn the
eval red.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

import cortex_core.search as search_mod
from cortex_core.eval import DEFAULT_GOLDEN_PATH, compute_metrics, load_golden_queries, main, run_eval
from cortex_core.search import CortexSearchIndex


def test_compute_metrics_matches_hand_computed_values() -> None:
    """The exact example independently hand-computed and cross-checked
    against ranx before writing this module: q1 hit@rank1, q2 hit@rank3,
    q3 miss -> recall@5 = 2/3, mrr = (1 + 1/3 + 0)/3 = 4/9."""
    golden = [
        {"query": "q1", "expected_doc": "docA", "category": "and"},
        {"query": "q2", "expected_doc": "docB", "category": "and"},
        {"query": "q3", "expected_doc": "docC", "category": "and"},
    ]
    results_by_query = {
        "q1": ["docA", "docX"],
        "q2": ["docX", "docY", "docB"],
        "q3": [],
    }
    metrics = compute_metrics(results_by_query, golden)
    assert metrics["recall_at_5"] == pytest.approx(2 / 3)
    assert metrics["mrr"] == pytest.approx((1.0 + 1 / 3 + 0.0) / 3)
    assert metrics["zero_result_rate"] == pytest.approx(1 / 3)
    assert metrics["n_positive"] == 3
    assert metrics["misses"] == [{"query": "q3", "expected_doc": "docC", "rank": None}]


def test_compute_metrics_cross_validated_against_ranx() -> None:
    """gate 1.2's 'never hand-rolled IR math' -- proven by exact numeric
    agreement with ranx's reference implementation, not just self-consistent
    hand-computed values. Skipped if the optional [eval] extra isn't
    installed; ranx is never a hard dependency of this suite."""
    ranx = pytest.importorskip("ranx")

    golden = [
        {"query": "q1", "expected_doc": "docA", "category": "and"},
        {"query": "q2", "expected_doc": "docB", "category": "and"},
        {"query": "q3", "expected_doc": "docC", "category": "and"},
        {"query": "q4", "expected_doc": "docD", "category": "and"},
    ]
    results_by_query = {
        "q1": ["docA", "docX", "docY"],
        "q2": ["docX", "docY", "docB", "docZ"],
        "q3": [],
        "q4": ["docW", "docD", "docA", "docB", "docC", "docQ"],  # rank 2, within top-5
    }

    ours = compute_metrics(results_by_query, golden)

    qrels = ranx.Qrels({item["query"]: {item["expected_doc"]: 1} for item in golden})
    run_dict = {
        item["query"]: {
            path: float(len(results_by_query[item["query"]]) - i)
            for i, path in enumerate(results_by_query[item["query"]])
        }
        for item in golden
    }
    run = ranx.Run(run_dict)
    ranx_result = ranx.evaluate(qrels, run, ["recall@5", "mrr"])

    assert ours["recall_at_5"] == pytest.approx(float(ranx_result["recall@5"]))
    assert ours["mrr"] == pytest.approx(float(ranx_result["mrr"]))


def test_golden_query_set_size_and_expected_docs_all_exist() -> None:
    """gate 1.1: 20-40 pairs, mixing categories, every expected doc must
    actually exist in the real corpus -- not asserted, verified on disk."""
    golden = load_golden_queries()
    assert 20 <= len(golden) <= 40, f"expected 20-40 golden pairs, got {len(golden)}"

    categories = {item.get("category") for item in golden}
    assert "and" in categories
    assert "natural_language" in categories
    assert "negative" in categories

    workspace = DEFAULT_GOLDEN_PATH.resolve().parents[1]
    positive = [item for item in golden if item.get("expected_doc")]
    negative = [item for item in golden if not item.get("expected_doc")]
    assert len(positive) >= 15
    assert len(negative) >= 3
    for item in positive:
        doc_path = workspace / item["expected_doc"]
        assert doc_path.is_file(), f"expected_doc does not exist: {item['expected_doc']!r}"


def test_run_eval_against_the_real_repo_is_fast_and_scores_well() -> None:
    """gate 1.2: cortex-eval against the real corpus, deterministic, < 5s,
    and the golden set (built by verifying each query live) should score
    well against the corpus it was built from."""
    t0 = time.time()
    metrics = run_eval()
    elapsed = time.time() - t0

    assert elapsed < 5.0, f"eval took {elapsed:.2f}s, gate 1.2 requires < 5s"
    assert metrics["recall_at_5"] >= 0.8, (
        f"recall@5 = {metrics['recall_at_5']:.3f} on the golden set's own corpus; "
        f"misses: {metrics['misses']}"
    )
    assert metrics["negatives_correct"] == metrics["n_negative"], (
        f"expected all {metrics['n_negative']} negative queries to correctly return "
        f"zero hits; false positives: {metrics['false_positives']}"
    )

    metrics_again = run_eval()
    assert metrics_again["recall_at_5"] == metrics["recall_at_5"]
    assert metrics_again["mrr"] == metrics["mrr"]


def test_cli_main_prints_the_required_metrics(capsys) -> None:
    exit_code = main([])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "recall@5:" in out
    assert "mrr:" in out
    assert "zero_result_rate:" in out


def test_cli_json_output_is_valid_json(capsys) -> None:
    exit_code = main(["--json"])
    assert exit_code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "recall_at_5" in data
    assert "mrr" in data


def test_ci_regression_broken_normalization_turns_eval_red(tmp_path: Path, monkeypatch) -> None:
    """gate 1.3: an intentionally broken query-ladder must show up as a
    measurable regression, not silently pass. Break _normalize_query to
    never join multi-term queries (simulating the historical AND-only bug
    reappearing) and confirm recall@5 measurably drops against a small,
    controlled golden set built for this test -- not a vague assertion,
    a real before/after comparison on the real search path."""
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)

    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    # Padded past chunk_text's max_chars (1500) so "widgets" and
    # "gears"/"sprockets" land in genuinely separate FTS5 chunks -- verified
    # directly (not assumed) before relying on it: an AND query needs all
    # terms in ONE row, so this doc is only findable via the OR rung.
    para1 = "This document mentions widgets. " + ("Filler sentence padding this paragraph out. " * 40)
    para2 = "A much later paragraph mentions gears and sprockets separately. " + ("More filler content here too. " * 5)
    (shard / "multiterm.md").write_text(f"# Multiterm\n\n{para1}\n\n{para2}\n", encoding="utf-8")

    golden_path = tmp_path / "golden.yaml"
    golden_path.write_text(
        yaml.safe_dump(
            {
                "queries": [
                    {
                        "query": "widgets gears sprockets",
                        "expected_doc": "docs/cortex-1/multiterm.md",
                        "category": "and",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    baseline = run_eval(workspace=str(workspace), golden_path=golden_path)
    assert baseline["recall_at_5"] == 1.0, "control: the unmodified ladder must find this via its OR rung"

    real_normalize = search_mod._normalize_query

    def and_only_normalize(query: str, joiner: str = " AND ") -> str:
        # Simulate the historical bug: always force AND, never fall through.
        return real_normalize(query, joiner=" AND ")

    monkeypatch.setattr(search_mod, "_normalize_query", and_only_normalize)
    CortexSearchIndex(workspace).rebuild()  # fresh connection picks up the patched module function
    regressed = run_eval(workspace=str(workspace), golden_path=golden_path)

    assert regressed["recall_at_5"] < baseline["recall_at_5"], (
        "expected the AND-only regression to measurably drop recall@5, "
        f"but baseline={baseline['recall_at_5']} regressed={regressed['recall_at_5']}"
    )
