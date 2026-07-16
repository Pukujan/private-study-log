from __future__ import annotations

import json
import os
from pathlib import Path

from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return workspace


def test_index_discovers_docs_audit_and_dynamic_shards(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "docs" / "cortex-1").mkdir(parents=True)
    (workspace / "docs" / "cortex-3").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "reviewed").mkdir()
    (workspace / "docs" / "cortex-1" / "alpha.md").write_text("# Alpha\n\nSharded search target.", encoding="utf-8")
    (workspace / "docs" / "cortex-3" / "beta.md").write_text("# Beta\n\nDynamic shard content.", encoding="utf-8")
    (workspace / "audit" / "audit-log-1" / "agent" / "closeout.md").write_text("# Closeout\n\nAudit evidence for the search index.", encoding="utf-8")
    (workspace / "reviewed" / "review.md").write_text("# Review\n\nReviewed content with a unique phrase.", encoding="utf-8")

    index = CortexSearchIndex(workspace)
    meta = index.rebuild()

    assert meta["document_count"] == 4
    results = index.search("unique phrase")
    assert any(result.path.endswith("review.md") for result in results)
    assert any(result.path.endswith("closeout.md") for result in index.search("audit evidence"))
    assert any(result.path.endswith("beta.md") for result in index.search("dynamic shard"))


def test_search_negative_limit_is_clamped_not_unbounded(tmp_path: Path) -> None:
    """Stability audit finding #3 (2026-07-07): SQLite treats a negative LIMIT as
    "no upper bound", and that fell straight through to `search()`'s `LIMIT ?` with no
    clamp -- a real repro on the production corpus returned 1106 hits for `limit=-1` vs. 5
    for `limit=5`. Build a corpus with more matching chunks than MAX_SEARCH_LIMIT and confirm
    a negative (and an absurdly large) limit both get capped, never returning the whole
    corpus unbounded."""
    from cortex_core.search import MAX_SEARCH_LIMIT

    workspace = _make_workspace(tmp_path)
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    n_docs = MAX_SEARCH_LIMIT + 20
    for i in range(n_docs):
        (shard / f"doc{i}.md").write_text(f"# Doc {i}\n\nwidget content number {i}.", encoding="utf-8")

    index = CortexSearchIndex(workspace)
    index.rebuild()

    negative = index.search("widget", limit=-1)
    huge = index.search("widget", limit=10**9)
    normal = index.search("widget", limit=5)

    assert len(negative) <= MAX_SEARCH_LIMIT
    assert len(huge) <= MAX_SEARCH_LIMIT
    assert len(normal) == 5


def test_index_rebuilds_when_content_changes_even_if_mtime_is_restored(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    doc = workspace / "docs" / "cortex-1"
    doc.mkdir(parents=True)
    path = doc / "change.md"
    path.write_text("# Title\n\nfirst version", encoding="utf-8")

    index = CortexSearchIndex(workspace)
    index.rebuild()

    original_mtime = path.stat().st_mtime_ns
    path.write_text("# Title\n\nsecond version with different content", encoding="utf-8")
    os.utime(path, ns=(original_mtime, original_mtime))

    assert index.needs_rebuild() is True
