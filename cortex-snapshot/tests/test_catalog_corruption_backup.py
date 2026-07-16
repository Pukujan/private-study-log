"""RED tests for Phase-0 KE item 1 — F3/KE-06: catalog corruption data loss.

Contract: ``reviewed/phase0-ke-fixes-contract-2026-07-04.md`` (§1).

``_update_collection_catalog()`` (``cortex_core/fetch.py`` — NOT ``search.py``;
the task brief mislabels the module) opens the catalog and, on a
``yaml.YAMLError``, sets ``data = {}`` and then writes back only the new
entry::

    try:
        data = yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        data = {}          # <-- every prior source silently dropped

A single stray tab (the exact corruption reproduced in PHASE-GATES 0.5 /
BUILD-PLAN KE-06, "dropping 2 prior sources to 1 from one corrupting stray
tab") therefore turns the whole append-only source catalog into a one-entry
file — an unrecoverable, silent data-loss path. This is the highest-severity
open item (BUILD-PLAN Phase 0 backlog #1).

Desired (gate 0.5): on YAML corruption the corrupt file is **backed up**
(``.corrupt-<timestamp>`` sibling) and the reset is **not silent** (an error
is logged); the prior source list stays recoverable from the backup, and the
new entry is still recorded sanely in a fresh, parseable catalog.
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


def test_corrupt_catalog_is_backed_up_not_silently_reset(tmp_path: Path, monkeypatch) -> None:
    """RED: after a stray tab corrupts a catalog that held two real sources,
    the update path must preserve those prior sources in a ``*.corrupt*``
    backup (never silently drop them) and still record the new entry in a
    parseable catalog. Today no backup is written and the two prior sources
    vanish (``data = {}``)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    catalog = _catalog_path(workspace)

    # Seed two real prior sources through the normal (valid) path.
    _update_collection_catalog(
        workspace, "doc-a", "https://example.com/a", workspace / "docs" / "cortex-1" / "a.md"
    )
    _update_collection_catalog(
        workspace, "doc-b", "https://example.com/b", workspace / "docs" / "cortex-1" / "b.md"
    )
    prior = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert len(prior["sources"]) == 2, "test premise: two prior sources must be present"

    # Corrupt the catalog exactly as gate 0.5 reproduces it: a stray tab-indented
    # line appended to the otherwise-valid file. The prior source URLs stay
    # textually present (so a faithful backup is demonstrably recoverable), but
    # ``yaml.safe_load`` now rejects the whole file.
    corrupt_bytes = catalog.read_text(encoding="utf-8") + "\tbroken: tabbed\n"
    catalog.write_text(corrupt_bytes, encoding="utf-8")
    with pytest.raises(yaml.YAMLError):  # guard the test's own premise
        yaml.safe_load(catalog.read_text(encoding="utf-8"))

    # Run the update path against the now-corrupt catalog.
    _update_collection_catalog(
        workspace, "doc-c", "https://example.com/c", workspace / "docs" / "cortex-1" / "c.md"
    )

    # (1) The corrupt content must survive in a backup, not be silently dropped.
    backups = [
        p
        for p in catalog.parent.iterdir()
        if p.is_file() and p.name != catalog.name and "corrupt" in p.name
    ]
    assert backups, (
        "no '*.corrupt*' backup of the poisoned catalog was written; the two "
        "prior sources were silently dropped. sources dir now contains: "
        f"{sorted(p.name for p in catalog.parent.iterdir())}"
    )
    assert any(
        b.read_text(encoding="utf-8") == corrupt_bytes for b in backups
    ), "a backup exists but does not preserve the pre-corruption catalog bytes (prior sources lost)"

    # (2) The new entry is still recorded sanely: catalog parses, contains doc-c.
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    recorded = [entry.get("source_url") for entry in data.get("sources", [])]
    assert "https://example.com/c" in recorded, (
        f"new source was not recorded after the corruption recovery; got {recorded!r}"
    )


def test_valid_catalog_update_makes_no_backup(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): a well-formed catalog must be updated in place
    with NO spurious ``.corrupt`` backup — the backup path fires only on real
    corruption, never on the happy path. Green today; must stay green after the
    fix so the recovery logic does not over-trigger."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    catalog = _catalog_path(workspace)

    _update_collection_catalog(
        workspace, "doc-a", "https://example.com/a", workspace / "docs" / "cortex-1" / "a.md"
    )
    _update_collection_catalog(
        workspace, "doc-b", "https://example.com/b", workspace / "docs" / "cortex-1" / "b.md"
    )

    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert {e["source_url"] for e in data["sources"]} == {
        "https://example.com/a",
        "https://example.com/b",
    }
    siblings = [p.name for p in catalog.parent.iterdir() if p.name != catalog.name]
    assert siblings == [], f"no backup should exist for a clean catalog, found {siblings!r}"
