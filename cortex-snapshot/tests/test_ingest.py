"""Bulk-ingest (gap I1) tests. SDD→TDD: written before cortex_core/ingest.py.

Contract under test (docs/INGEST-SPEC.md):
  - walk a scattered directory tree, extract text from common types
  - skip binaries + junk dirs (node_modules/.git/...)
  - dedup byte-identical extracted content by hash
  - materialize into the workspace corpus so the EXISTING index finds it
  - idempotent: re-ingest writes zero new docs
"""
from __future__ import annotations

import json
from pathlib import Path

from cortex_core.ingest import ingest_dir
from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _make_scattered_tree(root: Path) -> None:
    """A messy pile: mixed text/code types, an HTML file, a binary, a content
    dup, and a junk dir that must be pruned."""
    root.mkdir(parents=True)
    (root / "notes.md").write_text(
        "# Meeting Notes\n\nThe quarterly kingfisher migration report is due.",
        encoding="utf-8",
    )
    (root / "readme.txt").write_text(
        "Plain text about the aardvark deployment pipeline.", encoding="utf-8"
    )
    (root / "script.py").write_text(
        "def platypus():\n    return 'a rare code token'\n", encoding="utf-8"
    )
    (root / "config.yaml").write_text("service: narwhal\nport: 8080\n", encoding="utf-8")
    (root / "data.json").write_text('{"animal": "wombat", "count": 3}', encoding="utf-8")
    (root / "page.html").write_text(
        "<html><head><title>Doc</title><style>body{color:red}</style></head>"
        "<body><p>The elephant seal colony expanded this year.</p>"
        "<script>var x=1;</script></body></html>",
        encoding="utf-8",
    )
    # A nested dir with more content.
    sub = root / "projectA" / "src"
    sub.mkdir(parents=True)
    (sub / "buried.md").write_text(
        "# Buried Deep\n\nThe reticulated giraffe algorithm lives here.",
        encoding="utf-8",
    )
    # A content dup of notes.md (byte-identical text) elsewhere in the tree.
    (root / "projectA" / "copy_of_notes.md").write_text(
        "# Meeting Notes\n\nThe quarterly kingfisher migration report is due.",
        encoding="utf-8",
    )
    # A binary file (has NUL bytes + a non-text extension) -> must be skipped.
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binarygarbage\x00")
    # junk dir that must be pruned entirely.
    junk = root / "node_modules" / "leftpad"
    junk.mkdir(parents=True)
    (junk / "index.md").write_text("# should NOT be ingested\n\nnode junk", encoding="utf-8")
    gitdir = root / ".git"
    gitdir.mkdir()
    (gitdir / "COMMIT_EDITMSG.md").write_text("# git internal\n\nnope", encoding="utf-8")


def test_ingest_counts_and_skips(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    src = tmp_path / "scattered"
    _make_scattered_tree(src)

    result = ingest_dir(src, workspace=workspace, reindex=False)

    # 8 real candidate files walked (notes, readme, script, config, data, page,
    # buried, copy_of_notes) + the binary logo.png = 9. The two junk-dir files
    # are pruned and never seen.
    assert result["files_seen"] == 9
    # Unique extracted content ingested: notes, readme, script, config, data,
    # page, buried = 7. copy_of_notes dedupes against notes.
    assert result["ingested"] == 7
    assert result["deduped"] == 1
    # logo.png skipped as binary/unsupported.
    assert result["skipped"] >= 1
    # junk dirs pruned, not merely skipped-as-files.
    assert result["files_seen"] == result["ingested"] + result["deduped"] + result["skipped"]


def test_ingested_content_is_searchable(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    src = tmp_path / "scattered"
    _make_scattered_tree(src)

    ingest_dir(src, workspace=workspace, reindex=True)

    index = CortexSearchIndex(workspace)
    # A doc buried deep in the tree is findable in one search.
    hits = index.search("reticulated giraffe", use_vector=False)
    assert any("buried" in h.filename.lower() or "buried" in h.title.lower() for h in hits), (
        f"buried doc not found; got {[h.filename for h in hits]}"
    )
    # HTML was extracted to prose (script/style stripped), so its text is findable
    # and the raw <script> token is not.
    html_hits = index.search("elephant seal colony", use_vector=False)
    assert html_hits, "html-extracted prose not searchable"


def test_binary_and_junk_never_reach_corpus(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    src = tmp_path / "scattered"
    _make_scattered_tree(src)

    ingest_dir(src, workspace=workspace, reindex=True)

    index = CortexSearchIndex(workspace)
    # The pruned node_modules doc must be absent from the corpus entirely.
    assert not index.search("node junk", use_vector=False)
    assert not index.search("git internal", use_vector=False)
    # No output file should carry the binary's bytes.
    out_dir = workspace / "docs" / "cortex-ingest"
    for md in out_dir.rglob("*.md"):
        assert "binarygarbage" not in md.read_text(encoding="utf-8", errors="replace")


def test_reingest_is_idempotent(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    src = tmp_path / "scattered"
    _make_scattered_tree(src)

    first = ingest_dir(src, workspace=workspace, reindex=False)
    out_dir = workspace / "docs" / "cortex-ingest"
    files_after_first = sorted(p.name for p in out_dir.rglob("*.md"))

    second = ingest_dir(src, workspace=workspace, reindex=False)
    files_after_second = sorted(p.name for p in out_dir.rglob("*.md"))

    # Re-ingest writes ZERO new docs; every source is now a known-content dedupe.
    assert second["ingested"] == 0
    assert second["deduped"] == first["ingested"] + first["deduped"]
    # No duplicate output files appeared.
    assert files_after_first == files_after_second


def test_changed_source_is_updated_in_place(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    src = tmp_path / "scattered"
    _make_scattered_tree(src)
    ingest_dir(src, workspace=workspace, reindex=False)

    out_dir = workspace / "docs" / "cortex-ingest"
    before = sorted(p.name for p in out_dir.rglob("*.md"))

    # Edit one source file's content.
    (src / "readme.txt").write_text(
        "Plain text now about the capybara release train.", encoding="utf-8"
    )
    result = ingest_dir(src, workspace=workspace, reindex=True)
    after = sorted(p.name for p in out_dir.rglob("*.md"))

    # Exactly one doc re-ingested (the changed source), no orphan duplicate added.
    assert result["ingested"] == 1
    assert len(after) == len(before)

    index = CortexSearchIndex(workspace)
    assert index.search("capybara release train", use_vector=False)
    # Old content for that source is gone from the corpus.
    assert not index.search("aardvark deployment pipeline", use_vector=False)


def test_missing_dir_returns_nonzero(tmp_path: Path) -> None:
    from cortex_core.ingest import main

    rc = main([str(tmp_path / "does-not-exist"), "--workspace", str(tmp_path)])
    assert rc == 2
