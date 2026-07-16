"""RED tests for Tier-0 item 2 — FTS5 multi-word query normalizer.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§2).

``_normalize_query()`` in ``cortex_core/search.py`` turns any bareword
multi-word query into ``t1 AND t2 AND ... AND tn``. Because the FTS5
``MATCH`` is evaluated per chunk, a query whose terms are spread across
several docs/chunks matches **nothing** under AND, even though the corpus
covers every term. This is finding #3 of
``reviewed/opus-deep-review-2026-07-03.md`` and PHASE-GATES 0.3 (the
"6-term kanban query returns the kanban doc" gate).

Desired: a multi-word query whose terms are spread across the corpus
returns non-empty results (the normalizer must fall through to an
OR-ranked rung, not dead-end on AND). These tests assert *behavior*
(non-empty results / the expected doc surfaces), not the exact normalized
string, so the implementer is free to choose the ladder shape
(phrase -> AND -> OR -> prefix) as long as multi-word queries stop
returning nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _seed_spread_corpus(workspace: Path) -> None:
    """Write a corpus where the query terms exist but no single chunk
    contains all of them, so AND-join yields 0 and OR-join yields hits."""
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "kanban-reporting.md").write_text(
        "# Kanban Reporting Agent Tasks\n\nThe kanban board tracks flow for agent tasks.\n",
        encoding="utf-8",
    )
    (shard / "metrics.md").write_text(
        "# Metrics\n\nWe compute evaluation metrics and render a reporting dashboard.\n",
        encoding="utf-8",
    )
    (shard / "regression.md").write_text(
        "# Regression\n\nA regression guard protects the pipeline.\n",
        encoding="utf-8",
    )


def test_six_term_spread_query_returns_kanban_doc(tmp_path: Path, monkeypatch) -> None:
    """RED: the cited 6-term probe. Terms are spread across three docs, so
    the AND-join built by ``_normalize_query`` matches nothing; a working
    ladder returns hits, including the kanban doc (PHASE-GATES 0.3)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_spread_corpus(workspace)

    index = CortexSearchIndex(workspace)
    index.rebuild()

    results = index.search("kanban flow metrics evaluation dashboard regression")

    assert results, "6-term multi-word query returned no results (AND-join dead-ends)"
    assert any("kanban" in result.filename for result in results), (
        "kanban doc not surfaced for the 6-term query"
    )


def test_version_token_query_does_not_dead_end(tmp_path: Path, monkeypatch) -> None:
    """REGRESSION (2026-07-05): a query containing a dotted version token like
    ``GLM-5.2`` used to normalize to ``... AND 5.2 AND ...``; the ``5.2`` threw
    ``fts5: syntax error near "."`` which dropped the whole query to the LIKE
    fallback (whole-phrase substring) and returned ZERO hits, even though the
    corpus contained the answer. The normalizer must strip ``.`` (and other
    punctuation), so version-number queries retrieve. This corpus is *about*
    models/tools, so version tokens are everywhere -- a real, high-impact miss."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "model-roles.md").write_text(
        "# Model Roles\n\nProvenance rule: GLM-5.2 ONLY via Umans or opencode-go.\n",
        encoding="utf-8",
    )
    index = CortexSearchIndex(workspace)
    index.rebuild()

    for query in ("GLM-5.2 provenance Umans", "GLM-5.2 provenance rule", "sqlite-vec txtai"):
        results = index.search(query)
        # the version-token query must not self-sabotage to zero on a corpus that has the terms
        if "GLM-5.2" in query:
            assert results, f"version-token query {query!r} returned no results (the '.' dead-end)"
            assert any("model-roles" in r.filename for r in results), (
                f"the GLM-5.2 provenance doc was not surfaced for {query!r}"
            )


def test_two_word_query_across_docs_returns_results(tmp_path: Path, monkeypatch) -> None:
    """RED: even a 2-word query fails when the words live in different
    docs. 'kanban' is only in one doc, 'dashboard' only in another; AND ->
    0, OR -> both."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_spread_corpus(workspace)

    index = CortexSearchIndex(workspace)
    index.rebuild()

    results = index.search("kanban dashboard")

    assert results, "2-word cross-doc query returned no results (AND-join dead-ends)"
