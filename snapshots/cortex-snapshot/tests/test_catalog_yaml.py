"""RED tests for Tier-0 item 7 — real YAML serializer for the catalog.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§7).

``_update_collection_catalog()`` (``cortex_core/fetch.py``) builds catalog
entries with ``f"  source_url: {source_url}"`` — no quoting/escaping — and
splices them into ``collection.yaml`` as raw text. A perfectly ordinary
documentation URL containing a ``": "`` (colon-space) sequence — a
``#fragment: x`` anchor or a ``?key=a: b`` query value — makes the *entire*
file unparseable by any real YAML consumer, not just the offending entry.
Finding #8 of ``reviewed/opus-deep-review-2026-07-03.md``; PHASE-GATES 0.5.

Desired: use a real serializer (PyYAML ``safe_dump`` — already available;
``ruamel`` is not installed) so URLs with query strings / colons / unicode
round-trip losslessly and ``collection.yaml`` stays ``safe_load``-parseable,
with the existing ``sources:`` list format and dedup preserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cortex_core.fetch import _update_collection_catalog


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _catalog_path(workspace: Path) -> Path:
    return workspace / "library" / "cortex-library" / "sources" / "collection.yaml"


@pytest.mark.parametrize(
    "url",
    [
        "https://modelcontextprotocol.io/spec#tools: schema",  # fragment, colon-space
        "https://example.com/search?q=type: doc&page=1",       # query value, colon-space
        "https://example.com/café/naïve?q=a: b",     # unicode + colon-space
    ],
)
def test_catalog_roundtrips_hard_url(tmp_path: Path, url: str, monkeypatch) -> None:
    """RED: after recording a realistic URL, ``collection.yaml`` must parse
    with a real YAML loader and the URL must round-trip byte-for-byte. Today
    the splice writes an unquoted ``source_url`` with a ``": "`` inside,
    so ``yaml.safe_load`` raises ``ScannerError`` on the whole file."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    _update_collection_catalog(
        workspace, "real-doc", url, workspace / "docs" / "cortex-1" / "real-doc.md"
    )

    data = yaml.safe_load(_catalog_path(workspace).read_text(encoding="utf-8"))
    recorded = [entry["source_url"] for entry in data["sources"]]
    assert url in recorded, f"url did not round-trip losslessly; got {recorded!r}"


def test_catalog_plain_url_parses_and_dedupes(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): a plain URL must still produce a
    ``safe_load``-parseable catalog with exactly one entry even when
    recorded twice (PHASE-GATES 0.5: one entry per fetched doc). Guards that
    the serializer swap preserves the format and dedup."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    url = "https://example.com/doc"
    local = workspace / "docs" / "cortex-1" / "plain-doc.md"

    _update_collection_catalog(workspace, "plain-doc", url, local)
    _update_collection_catalog(workspace, "plain-doc", url, local)

    data = yaml.safe_load(_catalog_path(workspace).read_text(encoding="utf-8"))
    entries = [e for e in data["sources"] if e.get("source_url") == url]
    assert len(entries) == 1, f"expected exactly one catalog entry, got {len(entries)}"
