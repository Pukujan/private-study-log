from __future__ import annotations

import json
from pathlib import Path

from cortex_core.fetch import choose_doc_shard, fetch_document


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (workspace / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return workspace


def test_choose_doc_shard_creates_first_shard(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    shard = choose_doc_shard(workspace)
    assert shard == workspace / "docs" / "cortex-1"


def test_fetch_document_saves_markdown_and_updates_catalog(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)

    def opener(_: str) -> _FakeResponse:
        return _FakeResponse("hello world")

    path = fetch_document(
        "https://example.com/doc",
        "Example Doc",
        workspace=workspace,
        opener=opener,
    )

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Source: https://example.com/doc" in text
    catalog = workspace / "library" / "cortex-library" / "sources" / "collection.yaml"
    assert "source_url: https://example.com/doc" in catalog.read_text(encoding="utf-8")


def test_fetch_long_name_does_not_overflow_windows_path_limit(tmp_path: Path) -> None:
    """`--name` / the `cortex_fetch_doc` MCP `name` param is user-supplied and
    was slugified into the filename with no length cap -- a long name overflows
    Windows' ~260-char full-path limit and crashes the write after the network
    round-trip (same class as audit._slugify; found by the 2026-07-04 Windows
    sweep). The slug must be bounded so the fetch still succeeds."""
    workspace = _make_workspace(tmp_path)

    def opener(_: str) -> _FakeResponse:
        return _FakeResponse("hello world")

    long_name = "a very long document title that goes on and on " * 10
    assert len(long_name) > 200

    path = fetch_document(
        "https://example.com/doc", long_name, workspace=workspace, opener=opener
    )

    assert path.exists()
    assert len(path.name) < 100  # slug bounded, ".md" fits comfortably
