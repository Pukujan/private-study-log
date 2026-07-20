"""RED tests for Phase-0 KE item 3 — KE-04 + KE-05: O(corpus) staleness check.

Contract: ``reviewed/phase0-ke-fixes-contract-2026-07-04.md`` (§3);
PHASE-GATES 0.16; BUILD-PLAN Phase 0 addendum KE-04/KE-05.

``CortexSearchIndex.needs_rebuild()`` (``cortex_core/search.py``) calls
``discover_documents()``, which SHA-256-hashes **every** file in the corpus,
then compares those hashes to the stored ones. Every CLI ``--hybrid`` search
runs this first, so staleness costs O(corpus bytes), not O(index) — repro'd
at ~4× the search cost on a 1MB/66-file corpus, scaling linearly (the failure
mode that bites hardest at the multi-GB shard scale the docs already
advertise). The gate-0.7 search telemetry measures only ``search()``, so this
cost is invisible in the very numbers meant to catch regressions like it.

Desired (gate 0.16), mirroring git/make/ninja/rsync staleness discipline:
  * a stat (``mtime_ns`` + size) fast-path runs *before* any hashing; a file
    whose stat matches its stored fingerprint is trusted without re-hashing;
  * git's "racy" guard is honored — a file whose mtime falls inside the
    index's own write window is still re-hashed, not trusted on mtime alone
    (that window is deliberately avoided here by back-dating the corpus);
  * telemetry gains a ``rescan_ms`` field, distinct from search ``ms``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import cortex_core.search as search_mod
from cortex_core.search import CortexSearchIndex

# Back-date the corpus well before the index write so the stat fast-path is in
# its steady state (outside git's same-second "racy" re-hash window).
_SETTLE_NS = 30_000_000_000  # 30 s


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _seed_docs(workspace: Path) -> None:
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "widgets.md").write_text(
        "# Widgets\n\nThis document discusses widgets and gears.\n", encoding="utf-8"
    )
    (shard / "sprockets.md").write_text(
        "# Sprockets\n\nSprockets and cogs and other machinery.\n", encoding="utf-8"
    )


def _settle_mtimes(workspace: Path) -> None:
    old_ns = time.time_ns() - _SETTLE_NS
    for path in (workspace / "docs").rglob("*.md"):
        os.utime(path, ns=(old_ns, old_ns))


def _read_telemetry(workspace: Path) -> list[dict]:
    path = workspace / "logs" / "search-telemetry.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256_spy(monkeypatch) -> dict:
    counter = {"n": 0}
    real_sha256 = search_mod.hashlib.sha256

    def counting_sha256(*args, **kwargs):
        counter["n"] += 1
        return real_sha256(*args, **kwargs)

    monkeypatch.setattr(search_mod.hashlib, "sha256", counting_sha256)
    return counter


def test_unchanged_corpus_triggers_zero_content_hashing(tmp_path: Path, monkeypatch) -> None:
    """RED: on an unchanged, settled corpus ``needs_rebuild()`` must report
    not-stale while doing ZERO content hashing (the stat fast-path). Today it
    re-hashes every file via ``discover_documents()`` — one SHA-256 per file,
    independent of the index."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    _settle_mtimes(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    counter = _sha256_spy(monkeypatch)
    stale = index.needs_rebuild()

    assert stale is False, "an unchanged corpus must report not-stale"
    assert counter["n"] == 0, (
        f"needs_rebuild() hashed {counter['n']} file(s) on an UNCHANGED corpus; a stat "
        "(mtime_ns + size) fast-path must avoid all content hashing when nothing changed"
    )


def test_changed_file_still_detected_stale(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): the stat fast-path must not lose real-change
    detection — a modified file (new size + mtime) must still make the corpus
    stale. Green today (via hashing) and after the fix (via the stat
    fingerprint); guards that the optimization keeps its correctness."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    _settle_mtimes(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    # Modify one file: content, size and mtime all advance to ~now.
    changed = workspace / "docs" / "cortex-1" / "widgets.md"
    changed.write_text(
        "# Widgets\n\nThis document now discusses widgets, gears AND flywheels.\n",
        encoding="utf-8",
    )

    assert index.needs_rebuild() is True, (
        "a changed file must still be detected as stale by needs_rebuild()"
    )


def test_telemetry_records_rescan_ms_distinct_from_search_ms(tmp_path: Path, monkeypatch) -> None:
    """RED: a staleness-check-then-search (the real ``--hybrid`` CLI path) must
    record a ``rescan_ms`` field distinct from the search ``ms`` field, so the
    staleness cost is visible in telemetry rather than silently omitted. Today
    the telemetry entry has only ``{ts, query, rung, hits, top_path, ms}`` —
    no ``rescan_ms``."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    _settle_mtimes(workspace)
    CortexSearchIndex(workspace).rebuild()

    rc = search_mod.main(["--hybrid", "widgets", "--workspace", str(workspace)])
    assert rc == 0

    entries = _read_telemetry(workspace)
    assert entries, "the hybrid search path wrote no telemetry entry"
    entry = entries[-1]
    assert "rescan_ms" in entry, (
        f"telemetry entry lacks a 'rescan_ms' field distinct from search 'ms': {entry!r}"
    )
    assert isinstance(entry["rescan_ms"], (int, float)) and entry["rescan_ms"] >= 0, (
        f"'rescan_ms' must be a non-negative number, got {entry.get('rescan_ms')!r}"
    )
    assert "ms" in entry, "search 'ms' must remain a distinct field alongside 'rescan_ms'"
