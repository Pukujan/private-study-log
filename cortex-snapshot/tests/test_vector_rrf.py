"""Phase 2 gates 2.2/2.3: vector leg + RRF fusion.

The pure RRF math and the graceful-degradation / feature-flag behavior are
tested WITHOUT the optional embedder (they must hold regardless of whether
`model2vec` is installed). The end-to-end embed+KNN test is gated behind
`importorskip("model2vec")` since it needs the model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cortex_core.vector as vector
from cortex_core.search import CortexSearchIndex


# --- pure RRF math (always runs, no deps) ---------------------------------


def test_reciprocal_rank_fusion_matches_the_formula():
    # Two ranked lists of chunk ids. RRF score = sum of 1/(k+rank), rank from 1.
    a = [10, 20, 30]
    b = [30, 40]
    scores = vector.reciprocal_rank_fusion([a, b], k=60)
    # 30 appears in both (rank 3 in a, rank 1 in b) -> highest fused score.
    assert scores[30] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[10] == pytest.approx(1 / 61)
    assert scores[40] == pytest.approx(1 / 62)
    ranked = sorted(scores, key=lambda i: scores[i], reverse=True)
    assert ranked[0] == 30  # the item both legs agree on wins


def test_rrf_rewards_agreement_over_a_single_strong_hit():
    # An item ranked #2 by BOTH legs should beat an item ranked #1 by only one.
    both = vector.reciprocal_rank_fusion([[99, 7], [99, 7]], k=60)
    one = vector.reciprocal_rank_fusion([[5], []], k=60)
    assert both[7] > one[5]


# --- feature flag / graceful degradation (no embedder needed) -------------


def _tiny_index(tmp_path: Path) -> CortexSearchIndex:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    shard = ws / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "alpha.md").write_text("# Alpha\n\nwidgets and gears.\n", encoding="utf-8")
    (shard / "beta.md").write_text("# Beta\n\nsprockets and cogs.\n", encoding="utf-8")
    idx = CortexSearchIndex(ws)
    idx.rebuild()
    return idx


def test_use_vector_false_is_the_untouched_bm25_path(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    idx = _tiny_index(tmp_path)
    results = idx.search("widgets", use_vector=False)
    assert any(r.filename == "alpha.md" for r in results)


def test_use_vector_degrades_to_bm25_when_embedder_unavailable(tmp_path, monkeypatch):
    """If the optional deps aren't importable, search(use_vector=True) must
    still return BM25 results, never crash."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setattr(vector, "vector_available", lambda: False)
    idx = _tiny_index(tmp_path)
    results = idx.search("widgets", use_vector=True)
    assert any(r.filename == "alpha.md" for r in results)


def test_use_vector_degrades_to_bm25_when_vector_leg_raises(tmp_path, monkeypatch):
    """Even if the deps are 'available', a failure inside the vector path
    (model download, extension load, corrupt vec table) must fall back to
    BM25 rather than propagate."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    monkeypatch.setattr(vector, "vector_available", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("simulated vector-leg failure")

    monkeypatch.setattr(vector, "ensure_built", _boom)
    idx = _tiny_index(tmp_path)
    results = idx.search("widgets", use_vector=True)
    assert any(r.filename == "alpha.md" for r in results)


# --- HIGH-bug regression: staleness must track the FTS index generation ----


def test_count_preserving_edit_invalidates_the_vector_index(tmp_path):
    """Regression for the HIGH bug (reviewed/vector-leg-review-2026-07-04.md):
    an edit that rewrites content WITHOUT changing the chunk count must still
    mark the vector index stale. FTS5 DELETE+INSERT on rebuild() does not
    preserve chunk rowids, so a count-based staleness check would serve stale
    embeddings mapped onto reused rowids. Freshness must track the FTS index
    generation (`indexed_at_ns`). No embedder needed -- this isolates the
    staleness logic that is the actual fix."""
    import sqlite3

    import sqlite_vec

    db = tmp_path / "idx.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(content)")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO chunks(content) VALUES ('a'), ('b'), ('c')")
    conn.execute("INSERT INTO meta(key, value) VALUES ('indexed_at_ns', '1000')")

    # Simulate a completed vector build at FTS generation 1000: 3 vecs, model
    # stamped, generation stamped.
    vector.ensure_vector_schema(conn)
    for rowid in (1, 2, 3):
        conn.execute(
            "INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32([0.0] * vector.MODEL_DIM)),
        )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('vector_model_id', ?)", (vector.MODEL_ID,)
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('vector_index_generation', '1000')"
    )
    conn.commit()

    assert vector.needs_vector_rebuild(conn) is False  # fresh: gen + count match

    # A rebuild() bumps indexed_at_ns; chunk COUNT stays 3 (the bug's trigger).
    conn.execute("UPDATE meta SET value = '2000' WHERE key = 'indexed_at_ns'")
    conn.commit()

    # The old count-only check returned False here (3 == 3) -> stale served.
    # The fix keys on generation, so this must now be True.
    assert vector.needs_vector_rebuild(conn) is True


# --- distance threshold (negatives fix) -----------------------------------


def test_vector_search_drops_hits_past_the_distance_threshold(tmp_path, monkeypatch):
    """MAX_VECTOR_DISTANCE drops nearest-neighbours that are too far, so an
    unanswerable query returns nothing from the vector leg (the negatives fix,
    docs/EVAL-DESIGN-PHASE2.md). No embedder needed -- embed_texts is stubbed."""
    import sqlite3

    np = pytest.importorskip("numpy")  # optional (vector extra); skip when absent, don't fail
    sqlite_vec = pytest.importorskip("sqlite_vec")

    db = tmp_path / "idx.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(content)")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO chunks(content) VALUES ('near'), ('far')")
    vector.ensure_vector_schema(conn)
    near = [1.0] + [0.0] * (vector.MODEL_DIM - 1)
    far = [0.0] * (vector.MODEL_DIM - 1) + [5.0]  # L2 ~5.1 from `near`
    conn.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (1, ?)", (sqlite_vec.serialize_float32(near),))
    conn.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (2, ?)", (sqlite_vec.serialize_float32(far),))
    conn.commit()

    monkeypatch.setattr(vector, "embed_texts", lambda texts: [np.array(near, dtype="float32")])

    monkeypatch.setattr(vector, "MAX_VECTOR_DISTANCE", 1.0)
    ids = [rowid for rowid, _dist in vector.vector_search(conn, "q", 10)]
    assert 1 in ids, "a near (in-threshold) hit must survive"
    assert 2 not in ids, "a far (past-threshold) hit must be dropped"

    monkeypatch.setattr(vector, "MAX_VECTOR_DISTANCE", None)
    ids_off = [rowid for rowid, _dist in vector.vector_search(conn, "q", 10)]
    assert 2 in ids_off, "with the threshold disabled, the far hit returns"


# --- end-to-end embed + KNN (needs the optional embedder) -----------------


def test_vector_leg_finds_a_semantic_match_end_to_end(tmp_path, monkeypatch):
    pytest.importorskip("model2vec")
    if not vector.vector_available():
        pytest.skip("vector deps not available")
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)

    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    shard = ws / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    # Target doc uses technical vocabulary; the query below uses none of it.
    (shard / "network.md").write_text(
        "# Network guard\n\nBlock server-side request forgery by pinning the "
        "resolved IP at connect time so a rebinding DNS answer cannot redirect "
        "the fetch to a private address.\n",
        encoding="utf-8",
    )
    (shard / "unrelated.md").write_text(
        "# Recipes\n\nHow to bake sourdough bread with a long overnight rise.\n",
        encoding="utf-8",
    )
    idx = CortexSearchIndex(ws)
    idx.rebuild()

    # Pure-semantic query: zero lexical overlap with network.md's terms.
    results = idx.search(
        "keep a malicious link from making the program contact internal machines",
        limit=2,
        use_vector=True,
    )
    assert results, "vector leg returned nothing"
    assert results[0].filename == "network.md"
