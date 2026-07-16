"""Phase 2 vector leg (gates 2.2 / 2.3): an OPTIONAL, isolated dense-retrieval
companion to the FTS5/BM25 index.

Design constraints this module holds itself to:

- **Isolated & additive.** Nothing here touches the FTS `chunks` table or the
  existing `search()` path. The vector index lives in its own `chunk_vec`
  virtual table inside the same index db and is rebuilt *from* the FTS chunks
  table (the source of truth for chunk rowid -> content), so BM25 keeps
  working byte-for-byte whether or not the vector leg exists.
- **Off by default, graceful degradation.** `model2vec` is an optional
  `[vector]` extra. If it (or `sqlite-vec`) isn't importable, `vector_available()`
  returns False and callers fall back to pure BM25 -- never a crash.
- **Model pinned + invalidation-safe (gate 2.2 pitfall).** The model id and
  dim are recorded in the index `meta`; a model change is detected and forces
  a vector rebuild rather than silently querying mismatched embeddings.

Embedder chosen in `docs/ADR-0001-VECTOR-LEG.md` follow-up and verified live
on Windows 2026-07-04: model2vec `potion-base-8M`, 256-dim static embeddings,
~2ms/query on CPU, zero torch dependency.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

# Pinned embedding model (gate 2.2). A change to either value invalidates any
# previously-built vector index (see ensure_built).
MODEL_ID = "minishlab/potion-base-8M"
MODEL_DIM = 256

# Relevance floor (vec0 default L2 distance). Dense retrieval always returns
# nearest neighbours, so for a genuinely-unanswerable query it surfaces junk --
# the `negatives_correct 2/2 -> 0/2` finding in docs/EVAL-DESIGN-PHASE2.md. A
# vector hit past this distance is dropped, so a no-real-match query returns
# nothing from the vector leg. Calibrated on the approved graded set (n=15):
# every *answer* chunk sat at <= ~1.05, while the nearest chunk for a real
# negative was >= ~1.13, so 1.10 keeps positives and drops negatives. Small-n
# calibration -- re-tune if the answer-chunk max climbs toward it. None disables.
MAX_VECTOR_DISTANCE: float | None = 1.10

_MODEL = None  # lazy singleton -- loading/downloading the model is deferred
# until the vector leg is actually used, so importing this module is free.


def vector_available() -> bool:
    """True only if BOTH optional pieces are importable: the embedder
    (`model2vec`, the `[vector]` extra) and the vector store (`sqlite_vec`).
    Callers use this to decide whether to attempt the vector leg at all."""
    try:
        import model2vec  # noqa: F401
        import sqlite_vec  # noqa: F401
    except Exception:
        return False
    return True


def _load_model():
    global _MODEL
    if _MODEL is None:
        from model2vec import StaticModel

        _MODEL = StaticModel.from_pretrained(MODEL_ID)
    return _MODEL


def embed_texts(texts: list[str]) -> "np.ndarray":
    """Embed a batch of texts into an (n, MODEL_DIM) float32 array. Raises if
    the optional deps are missing -- callers should gate on vector_available()
    first."""
    model = _load_model()
    return model.encode(list(texts))


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    import sqlite_vec

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def ensure_vector_schema(conn: sqlite3.Connection) -> None:
    _load_sqlite_vec(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(embedding float[{MODEL_DIM}])"
    )


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _vector_meta(conn: sqlite3.Connection) -> tuple[str | None, int]:
    """Return (stored_model_id, vec_row_count). Missing meta -> (None, 0)."""
    stored_model = _read_meta(conn, "vector_model_id")
    try:
        count = conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
    return stored_model, count


def needs_vector_rebuild(conn: sqlite3.Connection) -> bool:
    """Stale if the pinned model changed (gate 2.2 invalidation) OR the FTS
    index has been rebuilt since the vectors were last built.

    Freshness is tied to the FTS index *generation* (`indexed_at_ns`), NOT to a
    chunk-count match. FTS5 `DELETE`+`INSERT` on `rebuild()` does not preserve
    chunk rowids, so a count-preserving content edit would otherwise leave
    stale embeddings mapped onto reused rowids and serve WRONG results -- the
    HIGH bug caught in `reviewed/vector-leg-review-2026-07-04.md`. Every
    `rebuild()` bumps `indexed_at_ns`, so any corpus change the FTS index picked
    up forces a full vector re-embed here, keeping the two indexes in lockstep."""
    stored_model, vec_count = _vector_meta(conn)
    if stored_model != MODEL_ID:
        return True
    stored_gen = _read_meta(conn, "vector_index_generation")
    current_gen = _read_meta(conn, "indexed_at_ns")
    if stored_gen is None or current_gen is None or stored_gen != current_gen:
        return True
    # Belt-and-suspenders: a count mismatch (vectors never built, or a
    # partial/interrupted build) also forces a rebuild.
    chunk_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    return vec_count != chunk_count


def rebuild_vectors(conn: sqlite3.Connection) -> int:
    """(Re)build the vector index in full from the FTS chunks table. Keyed on
    the FTS chunk rowid so vector hits map straight back to the same chunk BM25
    returns. Full rebuild (not incremental) is correct and simple at this
    corpus scale; it only ever runs when the vector leg is enabled. Returns the
    number of chunks embedded."""
    ensure_vector_schema(conn)
    rows = conn.execute("SELECT rowid, content FROM chunks ORDER BY rowid").fetchall()
    conn.execute("DELETE FROM chunk_vec")
    if rows:
        import sqlite_vec

        rowids = [r[0] for r in rows]
        vectors = embed_texts([r[1] for r in rows])
        for rowid, vec in zip(rowids, vectors, strict=True):
            conn.execute(
                "INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, sqlite_vec.serialize_float32([float(x) for x in vec])),
            )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('vector_model_id', ?)",
        (MODEL_ID,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('vector_dim', ?)",
        (str(MODEL_DIM),),
    )
    # Stamp the FTS index generation these vectors were built against, so
    # needs_vector_rebuild() can tell they've gone stale after any rebuild().
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('vector_index_generation', ?)",
        (_read_meta(conn, "indexed_at_ns") or "",),
    )
    conn.commit()
    return len(rows)


def ensure_built(conn: sqlite3.Connection) -> None:
    """Ensure the vector index exists and is fresh vs the FTS chunks table.

    NOTE (review LOW): when the FTS index has changed, this re-embeds the whole
    corpus synchronously and can take tens of seconds on the first vector search
    after a rebuild. The stderr notice makes that latency cliff visible rather
    than a silent hang; an out-of-band prebuild is future work."""
    ensure_vector_schema(conn)
    if needs_vector_rebuild(conn):
        import sys

        print(
            "(building vector index -- first search after a corpus change may take a moment)",
            file=sys.stderr,
        )
        rebuild_vectors(conn)


def vector_search(conn: sqlite3.Connection, query: str, limit: int) -> list[tuple[int, float]]:
    """KNN over the vector index. Returns [(chunk_rowid, distance), ...] sorted
    nearest-first, dropping hits past MAX_VECTOR_DISTANCE so an unanswerable
    query returns nothing from the vector leg (negatives fix). Assumes
    ensure_built() has run on this connection."""
    import sqlite_vec

    ensure_vector_schema(conn)
    (qvec,) = embed_texts([query])
    rows = conn.execute(
        "SELECT rowid, distance FROM chunk_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (sqlite_vec.serialize_float32([float(x) for x in qvec]), limit),
    ).fetchall()
    hits = [(int(r[0]), float(r[1])) for r in rows]
    if MAX_VECTOR_DISTANCE is not None:
        hits = [(rowid, dist) for rowid, dist in hits if dist <= MAX_VECTOR_DISTANCE]
    return hits


def reciprocal_rank_fusion(
    ranked_lists: list[list[int]], k: int = 60
) -> dict[int, float]:
    """Standard RRF (Cormack et al., SIGIR'09): fuse several ranked lists of
    ids by summing 1/(k + rank) across lists, rank starting at 1. Fusing ranks
    (not raw scores) is the whole point -- it sidesteps BM25-vs-cosine score
    normalization entirely. Returns {id: fused_score}, higher is better."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores
