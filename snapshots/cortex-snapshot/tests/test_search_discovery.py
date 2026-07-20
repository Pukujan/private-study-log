"""RED tests for Tier-0 item 3 — docs/ root + inbox/ search visibility.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§3).

``CortexSearchIndex.discover_documents()`` walks an explicit include-list
(``docs/cortex-*``, ``docs/research``, ``reviewed``, ``accepted``,
``audit/audit-log-*/agent``, ``library/cortex-library/docs``). It does
**not** walk the ``docs/`` root itself (so ``docs/BUILD-PLAN.md`` /
``docs/PHASE-GATES.md`` are invisible) nor ``inbox/`` at all. The corpus
is therefore blind to its own most current planning docs — finding #4 of
``reviewed/opus-deep-review-2026-07-03.md`` and PHASE-GATES 0.11.

Desired: a known string in a ``docs/`` root file or an ``inbox/`` file is
findable via ``search()`` after ``rebuild()``, *without* double-indexing
content already covered by shard scanning.
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


def test_docs_root_file_is_discovered_and_searchable(tmp_path: Path, monkeypatch) -> None:
    """RED: a markdown file living directly under ``docs/`` (not in a
    ``cortex-*`` shard) must be indexed and findable. Mirrors
    ``docs/BUILD-PLAN.md`` being invisible today."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "BUILD-PLAN.md").write_text(
        "# Build Plan\n\nThe corpus must index its own uniquerootmarker planning docs.\n",
        encoding="utf-8",
    )

    index = CortexSearchIndex(workspace)
    index.rebuild()

    discovered = {doc.path.name for doc in index.discover_documents()}
    assert "BUILD-PLAN.md" in discovered, (
        "docs/ root file not discovered by discover_documents()"
    )

    results = index.search("uniquerootmarker")
    assert any(result.filename == "BUILD-PLAN.md" for result in results), (
        "docs/ root file not findable via search()"
    )


def test_inbox_file_is_discovered_and_searchable(tmp_path: Path, monkeypatch) -> None:
    """RED: a markdown file under ``inbox/`` must be indexed and findable.
    ``inbox/`` is not scanned at all today."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    (workspace / "inbox").mkdir(parents=True)
    (workspace / "inbox" / "external-research.md").write_text(
        "# Inbox Research\n\nContains the uniqueinboxmarker token for indexing.\n",
        encoding="utf-8",
    )

    index = CortexSearchIndex(workspace)
    index.rebuild()

    discovered = {doc.path.name for doc in index.discover_documents()}
    assert "external-research.md" in discovered, (
        "inbox/ file not discovered by discover_documents()"
    )

    results = index.search("uniqueinboxmarker")
    assert any(result.filename == "external-research.md" for result in results), (
        "inbox/ file not findable via search()"
    )


def test_existing_shard_doc_not_double_indexed(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): once discovery also walks the ``docs/``
    root, a doc already covered by ``docs/cortex-*`` shard scanning must
    still be indexed exactly once (PHASE-GATES 0.11 dedup pitfall). Green
    today; must stay green after the fix."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    (workspace / "docs" / "cortex-1").mkdir(parents=True)
    (workspace / "docs" / "cortex-1" / "shard-doc.md").write_text(
        "# Shard Doc\n\nShardonlymarker content inside a shard.\n", encoding="utf-8"
    )
    (workspace / "docs" / "BUILD-PLAN.md").write_text(
        "# Plan\n\nRootonlymarker content at the docs root.\n", encoding="utf-8"
    )

    index = CortexSearchIndex(workspace)
    docs = index.discover_documents()

    shard_hits = [doc for doc in docs if doc.path.name == "shard-doc.md"]
    assert len(shard_hits) == 1, (
        f"shard doc indexed {len(shard_hits)} times; docs/ root walking must "
        "dedupe against shard scanning"
    )


def test_scratch_and_hidden_dirs_are_excluded_from_discovery(tmp_path: Path, monkeypatch) -> None:
    """F2 (tracked LOW): .md files under scratch/build/tooling or hidden dirs
    must NOT be indexed, while a real sibling doc still is -- so the index
    can't be polluted by junk that happens to be markdown."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    docs = workspace / "docs"
    docs.mkdir(parents=True)
    # A genuine doc: must be found.
    (docs / "real.md").write_text("# Real\n\nkeepmemarker content.\n", encoding="utf-8")
    # Junk under excluded dirs: must be skipped.
    for junk_dir in ("scratch", "node_modules", "__pycache__", ".git", "build"):
        d = docs / junk_dir
        d.mkdir(parents=True)
        (d / "junk.md").write_text("# Junk\n\ndropmemarker should not be indexed.\n", encoding="utf-8")

    index = CortexSearchIndex(workspace)
    index.rebuild()

    discovered = {p.name for p, _shard in index._iter_document_paths()}
    assert "real.md" in discovered
    assert "junk.md" not in discovered

    # And the junk marker is not searchable, while the real one is.
    assert index.search("keepmemarker"), "real doc must be findable"
    assert not index.search("dropmemarker"), "junk under excluded dirs must not be indexed"
