"""RED tests for Tier-0 item 6 — frontmatter out of indexed content.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§6).

``fetch_document()`` prepends a ``---``-delimited YAML frontmatter block
(``source_url``, ``fetched_at``) into the saved ``.md``, and
``chunk_text()`` has no frontmatter awareness, so chunk 0 of a fetched doc
is the raw frontmatter — indexed as ordinary FTS5 body content. This
pollutes BM25 ranking with URL/timestamp tokens (and, combined with the
SSRF finding, makes fetched secret paths full-text-searchable). Finding #7
of ``reviewed/opus-deep-review-2026-07-03.md``; PHASE-GATES 0.9.

Desired: the frontmatter block is stripped before chunking/indexing, so no
chunk contains ``source_url``/``fetched_at``; the metadata may still be
retained as structured fields (contract §6 leaves the retention mechanism
to the implementer).

No network or DNS: a fake single-arg ``opener`` supplies the body and a
fake ``resolver`` satisfies the SSRF host check with a public IP.
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core.fetch import fetch_document
from cortex_core.search import CortexSearchIndex


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def _public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _fetch_and_index(workspace: Path) -> CortexSearchIndex:
    def opener(_: str) -> _FakeResponse:
        return _FakeResponse("The body text discusses widgets and gears only.")

    fetch_document(
        "https://example.com/doc",
        "Widget Doc",
        workspace=workspace,
        opener=opener,
        resolver=_public_resolver,
    )
    index = CortexSearchIndex(workspace)
    index.rebuild()
    return index


def test_frontmatter_keys_absent_from_indexed_chunks(tmp_path: Path, monkeypatch) -> None:
    """RED: no indexed chunk of the fetched doc may contain the raw
    frontmatter keys. Today chunk 0 is the verbatim frontmatter block."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    index = _fetch_and_index(workspace)

    conn = index.connect()
    try:
        rows = conn.execute(
            "SELECT content FROM chunks WHERE filename = ?", ("widget-doc.md",)
        ).fetchall()
    finally:
        conn.close()

    assert rows, "fetched doc was not indexed at all"
    joined = "\n".join(row[0] for row in rows)
    assert "source_url" not in joined, "frontmatter key 'source_url' leaked into indexed content"
    assert "fetched_at" not in joined, "frontmatter key 'fetched_at' leaked into indexed content"


def test_frontmatter_token_not_searchable_as_body(tmp_path: Path, monkeypatch) -> None:
    """RED: a token that appears *only* in the frontmatter ('fetched', from
    ``fetched_at``) and never in the body must not make the doc a full-text
    hit. Today it does."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    index = _fetch_and_index(workspace)

    results = index.search("fetched")

    assert not any(result.filename == "widget-doc.md" for result in results), (
        "fetched doc matched a frontmatter-only token ('fetched'); frontmatter "
        "is being indexed as body content"
    )
