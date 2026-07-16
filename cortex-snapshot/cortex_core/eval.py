from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from .config import make_stdio_encoding_safe, resolve_workspace
from .search import CortexSearchIndex

# Phase 1, gates 1.1/1.2 (docs/PHASE-GATES.md): golden query set + eval CLI.
# Metrics are TREC-standard (recall@5, MRR) computed directly against the
# formal definitions below, not hand-rolled by guesswork -- cross-validated
# in tests/test_eval.py against ranx's reference implementation for exact
# numeric agreement. ranx itself is an optional [eval] extra (heavy:
# numba/pandas/scipy), not a runtime dependency of cortex-eval; the native
# implementation here is what actually ships and runs by default.

DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden_queries.yaml"
DEFAULT_SEARCH_LIMIT = 20
RECALL_K = 5


def load_golden_queries(golden_path: Path | None = None) -> list[dict[str, Any]]:
    path = golden_path or DEFAULT_GOLDEN_PATH
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return data.get("queries") or []


def compute_metrics(
    results_by_query: dict[str, list[str]], golden: list[dict[str, Any]]
) -> dict[str, Any]:
    """Recall@5 and MRR (TREC-standard, single-relevant-doc-per-query form:
    recall@k is 1 if the expected doc is within the top-k results else 0,
    reciprocal rank is 1/rank of the expected doc or 0 if absent), plus a
    zero-result rate over the same positive queries -- a stricter failure
    signal than "not in top-5" (the ladder found literally nothing).
    Negative queries (expected_doc is None) are checked separately: correct
    behavior is exactly zero results."""
    positive = [g for g in golden if g.get("expected_doc")]
    negative = [g for g in golden if not g.get("expected_doc")]

    reciprocal_ranks: list[float] = []
    recall_hits = 0
    zero_result_count = 0
    misses: list[dict[str, Any]] = []
    for item in positive:
        results = results_by_query.get(item["query"], [])
        if not results:
            zero_result_count += 1
        rank = next(
            (i for i, path in enumerate(results, start=1) if path == item["expected_doc"]),
            None,
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        if rank is not None and rank <= RECALL_K:
            recall_hits += 1
        else:
            misses.append({"query": item["query"], "expected_doc": item["expected_doc"], "rank": rank})

    negative_correct = sum(
        1 for item in negative if not results_by_query.get(item["query"], [])
    )
    false_positives = [
        item["query"] for item in negative if results_by_query.get(item["query"], [])
    ]

    return {
        "recall_at_5": recall_hits / len(positive) if positive else 0.0,
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "zero_result_rate": zero_result_count / len(positive) if positive else 0.0,
        "n_positive": len(positive),
        "n_negative": len(negative),
        "negatives_correct": negative_correct,
        "false_positives": false_positives,
        "misses": misses,
    }


def run_eval(
    workspace: str | Path | None = None,
    golden_path: Path | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    use_vector: bool = False,
) -> dict[str, Any]:
    ws = resolve_workspace(workspace)
    golden_raw = load_golden_queries(golden_path)
    index = CortexSearchIndex(ws)
    if index.needs_rebuild():
        index.rebuild()

    golden: list[dict[str, Any]] = []
    results_by_query: dict[str, list[str]] = {}
    for item in golden_raw:
        query = item["query"]
        expected = item.get("expected_doc")
        expected_abs = (ws / expected).resolve().as_posix() if expected else None
        golden.append({"query": query, "expected_doc": expected_abs, "category": item.get("category", "unknown")})
        results = index.search(query, limit=limit, tag="eval", use_vector=use_vector)
        results_by_query[query] = [r.path for r in results]

    metrics = compute_metrics(results_by_query, golden)
    metrics["n_queries"] = len(golden)
    return metrics


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex retrieval eval (Phase 1, gate 1.2)")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--golden", default=None, help="path to a golden_queries.yaml (default: tests/golden_queries.yaml)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--vector",
        action="store_true",
        help="fuse the optional dense vector leg with BM25 (RRF); requires the [vector] extra",
    )
    args = parser.parse_args(argv)

    if args.vector:
        # Review LOW: a silently-unavailable vector leg would just print
        # BM25-identical numbers, masquerading as "vectors ran." Warn loudly.
        from . import vector as _vector

        if not _vector.vector_available():
            print(
                "warning: --vector requested but the [vector] extra (model2vec) isn't "
                "importable; these numbers are BM25-only.",
                file=sys.stderr,
            )

    t0 = time.time()
    golden_path = Path(args.golden) if args.golden else None
    metrics = run_eval(workspace=args.workspace, golden_path=golden_path, use_vector=args.vector)
    elapsed = time.time() - t0

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(f"recall@5:         {metrics['recall_at_5']:.3f}")
        print(f"mrr:              {metrics['mrr']:.3f}")
        print(f"zero_result_rate: {metrics['zero_result_rate']:.3f}")
        print(f"queries:          {metrics['n_queries']} ({metrics['n_positive']} positive, {metrics['n_negative']} negative)")
        print(f"negatives_correct: {metrics['negatives_correct']}/{metrics['n_negative']}")
        if metrics["misses"]:
            print(f"misses ({len(metrics['misses'])}):")
            for miss in metrics["misses"]:
                print(f"  - {miss['query']!r} (rank: {miss['rank']})")
        if metrics["false_positives"]:
            print(f"false positives (negative query got hits): {metrics['false_positives']}")
        print(f"eval ran in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
