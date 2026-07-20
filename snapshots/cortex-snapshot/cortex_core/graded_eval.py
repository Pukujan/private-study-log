"""Phase 2 Layer-A eval (docs/EVAL-DESIGN-PHASE2.md): graded, multi-relevant,
chunk-sensitive retrieval metrics.

Why this exists: `cortex_core/eval.py` scores document-level recall@5 with a
single `expected_doc` per query (binary). That is saturated and blind to
chunk-level change, and it can't express that several docs may be legitimately
relevant to different degrees (the query-#5 Hermes problem). This module scores:

- **graded nDCG@k** (doc-level, rank-aware, uses per-doc grades 1-3) -- resolves
  the "multiple docs are relevant" ambiguity: a canonical doc is grade 3, an
  also-relevant doc grade 1, and surfacing the latter earns partial credit, not
  zero.
- **chunk recall@k / chunk MRR** -- for queries that name a distinctive
  `chunk` substring, whether the *right passage* (not just the right file) is
  retrieved. This is the metric that is actually sensitive to gates 2.4/2.6.
- **context precision@k** -- of the top-k retrieved docs, how many are relevant.

Labels are graded/multi-relevant. Under the never-wait trust model
(`cortex_core/provenance_tiers.py`, 2026-07-14) they are `non_human_verified`:
**used NOW** to gate retrieval changes, not blocked pending a human. A human review is
an OPTIONAL later UPGRADE to `human_verified` (raising confidence), never a precondition
for running the eval. The metric math here is deterministic and unit-tested against
hand-computed values, so the gate itself never depends on judgment.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import yaml

from .config import make_stdio_encoding_safe, resolve_workspace
from .search import CortexSearchIndex

DEFAULT_GRADED_PATH = Path(__file__).resolve().parents[1] / "tests" / "graded_queries.yaml"
DEFAULT_K = 5
DEFAULT_LIMIT = 20


def load_graded_queries(path: Path | None = None) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path or DEFAULT_GRADED_PATH).read_text(encoding="utf-8"))
    return data.get("queries") or []


# --- pure metric math (deterministic, unit-tested) ------------------------


def _dcg(grades: list[int]) -> float:
    """Discounted cumulative gain with the standard exponential gain
    (2**g - 1) and log2(rank+1) discount, rank starting at 1."""
    return sum((2 ** g - 1) / math.log2(i + 1) for i, g in enumerate(grades, start=1))


def ndcg_at_k(retrieved_grades: list[int], all_relevant_grades: list[int], k: int) -> float:
    """Graded nDCG@k. `retrieved_grades` are the grades of the top-k retrieved
    docs in rank order (0 for a retrieved-but-irrelevant doc). The ideal is the
    best k grades from the FULL relevant set (so failing to retrieve a relevant
    doc is penalized, not hidden)."""
    dcg = _dcg(retrieved_grades[:k])
    idcg = _dcg(sorted(all_relevant_grades, reverse=True)[:k])
    return dcg / idcg if idcg > 0 else 0.0


def context_precision_at_k(retrieved_grades: list[int], k: int) -> float:
    """Fraction of the top-k retrieved docs that are relevant (grade >= 1)."""
    top = retrieved_grades[:k]
    return sum(1 for g in top if g >= 1) / k if k else 0.0


# --- running the eval -----------------------------------------------------


def _resolve_relevant(ws: Path, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": (ws / entry["doc"]).resolve().as_posix(),
        "grade": int(entry.get("grade", 1)),
        "chunk": entry.get("chunk"),
    }


def _chunk_content(index: CortexSearchIndex, path: str, chunk_index: int) -> str:
    conn = index.connect()
    try:
        row = conn.execute(
            "SELECT content FROM chunks WHERE path = ? AND chunk_index = ?",
            (path, chunk_index),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def evaluate_query(index: CortexSearchIndex, ws: Path, item: dict[str, Any], k: int, limit: int, use_vector: bool, use_ontology: bool = False, ontology_max_hops: int | None = None) -> dict[str, Any]:
    relevant = [_resolve_relevant(ws, e) for e in (item.get("relevant") or [])]
    by_path = {r["path"]: r for r in relevant}
    results = index.search(item["query"], limit=limit, tag="eval", use_vector=use_vector, use_ontology=use_ontology, ontology_max_hops=ontology_max_hops)

    # Doc-level ranking: first (best) occurrence of each doc, in rank order.
    seen: set[str] = set()
    ranked_docs: list[str] = []
    first_chunk_index: dict[str, int] = {}
    for r in results:
        if r.path not in seen:
            seen.add(r.path)
            ranked_docs.append(r.path)
            first_chunk_index[r.path] = r.chunk_index
    retrieved_grades = [by_path[p]["grade"] if p in by_path else 0 for p in ranked_docs]
    all_relevant_grades = [r["grade"] for r in relevant]

    # Chunk-level: for relevant entries that name a `chunk` marker, is a chunk
    # containing that marker present in the top-k results, and at what rank?
    chunk_targets = [r for r in relevant if r["chunk"]]
    chunk_hit_rank: int | None = None
    if chunk_targets:
        for rank, r in enumerate(results[:limit], start=1):
            tgt = by_path.get(r.path)
            if tgt is None or not tgt["chunk"]:
                continue
            content = _chunk_content(index, r.path, r.chunk_index)
            if tgt["chunk"].lower() in content.lower():
                chunk_hit_rank = rank
                break

    return {
        "query": item["query"],
        "category": item.get("category", "unknown"),
        "n_relevant": len(relevant),
        "ndcg_at_k": ndcg_at_k(retrieved_grades, all_relevant_grades, k) if relevant else None,
        "context_precision_at_k": context_precision_at_k(retrieved_grades, k) if relevant else None,
        "has_results": bool(results),
        "chunk_targeted": bool(chunk_targets),
        "chunk_hit_rank": chunk_hit_rank,
        "chunk_recall_at_k": (chunk_hit_rank is not None and chunk_hit_rank <= k) if chunk_targets else None,
        "chunk_rr": (1.0 / chunk_hit_rank if chunk_hit_rank else 0.0) if chunk_targets else None,
    }


def run_graded_eval(workspace=None, graded_path=None, k=DEFAULT_K, limit=DEFAULT_LIMIT, use_vector=False, use_ontology=False, ontology_max_hops=None) -> dict[str, Any]:
    ws = resolve_workspace(workspace)
    queries = load_graded_queries(graded_path)
    index = CortexSearchIndex(ws)
    if index.needs_rebuild():
        index.rebuild()

    per_query = [evaluate_query(index, ws, q, k, limit, use_vector, use_ontology, ontology_max_hops) for q in queries]

    positives = [r for r in per_query if r["n_relevant"] > 0]
    negatives = [r for r in per_query if r["n_relevant"] == 0]
    chunk_q = [r for r in positives if r["chunk_targeted"]]

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "k": k,
        "n_queries": len(per_query),
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "mean_ndcg_at_k": _mean([r["ndcg_at_k"] for r in positives]),
        "mean_context_precision_at_k": _mean([r["context_precision_at_k"] for r in positives]),
        "chunk_recall_at_k": _mean([1.0 if r["chunk_recall_at_k"] else 0.0 for r in chunk_q]),
        "chunk_mrr": _mean([r["chunk_rr"] for r in chunk_q]),
        "n_chunk_targeted": len(chunk_q),
        "negatives_correct": sum(1 for r in negatives if not r["has_results"]),
        "per_query": per_query,
    }


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex graded/chunk-level retrieval eval (Phase 2 Layer A)")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--graded", default=None, help="path to graded_queries.yaml")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--vector", action="store_true", help="fuse the dense vector leg (RRF)")
    parser.add_argument("--ontology", action="store_true", help="fuse the ontology-expansion leg (GAP G2)")
    parser.add_argument("--ontology-hops", type=int, default=None,
                        help="max ontology hops to traverse (GAP G2-local; default: the "
                        "workspace config's max_hops, else 1). Explicit value overrides config.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.vector:
        from . import vector as _vector

        if not _vector.vector_available():
            print("warning: --vector requested but the [vector] extra isn't importable; BM25-only.", file=sys.stderr)

    graded_path = Path(args.graded) if args.graded else None
    m = run_graded_eval(workspace=args.workspace, graded_path=graded_path, k=args.k, use_vector=args.vector, use_ontology=args.ontology, ontology_max_hops=args.ontology_hops)

    if args.json:
        print(json.dumps(m, indent=2))
    else:
        print(f"nDCG@{m['k']}:            {m['mean_ndcg_at_k']:.3f}")
        print(f"context_precision@{m['k']}: {m['mean_context_precision_at_k']:.3f}")
        print(f"chunk_recall@{m['k']}:     {m['chunk_recall_at_k']:.3f}  (over {m['n_chunk_targeted']} chunk-targeted queries)")
        print(f"chunk_mrr:            {m['chunk_mrr']:.3f}")
        print(f"queries:             {m['n_queries']} ({m['n_positive']} positive, {m['n_negative']} negative)")
        print(f"negatives_correct:   {m['negatives_correct']}/{m['n_negative']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
