"""RED tests for PHASE-GATES gate 0.7 — search telemetry.

Every query must append a JSONL line (``{ts, query, rung, hits, top_path,
ms}``) to a log, fire-and-forget (a telemetry write failure must never
break search), with zero-result queries listable via one call. None of
this exists yet — ``search()`` currently has no telemetry side effect at
all.
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


def _seed_docs(workspace: Path) -> None:
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "widgets.md").write_text(
        "# Widgets\n\nThis document discusses widgets and gears.\n", encoding="utf-8"
    )


def _telemetry_path(workspace: Path) -> Path:
    return workspace / "logs" / "search-telemetry.jsonl"


def _read_entries(workspace: Path) -> list[dict]:
    path = _telemetry_path(workspace)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_search_records_telemetry_entry_per_query(tmp_path: Path, monkeypatch) -> None:
    """RED: a search must append one JSONL entry with the required schema."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    index.search("widgets")

    entries = _read_entries(workspace)
    assert len(entries) == 1, "expected exactly one telemetry entry after one query"
    entry = entries[0]
    for field in ("ts", "query", "rung", "hits", "top_path", "ms"):
        assert field in entry, f"telemetry entry missing required field {field!r}: {entry!r}"
    assert entry["query"] == "widgets"
    assert entry["hits"] == 1
    assert entry["top_path"] and entry["top_path"].endswith("widgets.md")
    assert isinstance(entry["ms"], (int, float)) and entry["ms"] >= 0


def test_zero_result_query_recorded_with_zero_hits(tmp_path: Path, monkeypatch) -> None:
    """RED: a query matching nothing must still be logged, with hits=0 and
    no top_path, not silently dropped."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    index.search("nonexistentzzzterm")

    entries = _read_entries(workspace)
    assert len(entries) == 1
    assert entries[0]["hits"] == 0
    assert entries[0]["top_path"] is None


def test_zero_result_queries_listable_with_one_call(tmp_path: Path, monkeypatch) -> None:
    """RED: zero-result queries must be listable with one call (the gate's
    'listable with one command' requirement), without hand-parsing the
    JSONL and without mixing in queries that did find hits."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    index.search("widgets")
    index.search("nonexistentzzzterm")
    index.search("alsomissingzzz")

    zero_result = index.zero_result_queries()

    assert len(zero_result) == 2
    assert {e["query"] for e in zero_result} == {"nonexistentzzzterm", "alsomissingzzz"}
    assert all(e["hits"] == 0 for e in zero_result)


def test_telemetry_write_failure_does_not_break_search(tmp_path: Path, monkeypatch) -> None:
    """RED: telemetry must be fire-and-forget. If the log write fails for
    any reason, search() must still return its results, not raise."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    def _boom(*_args, **_kwargs):
        raise OSError("simulated telemetry write failure")

    monkeypatch.setattr(Path, "open", _boom)

    results = index.search("widgets")

    assert results, "search() must still return results even if telemetry logging fails"


def test_tagged_search_is_distinguishable_in_telemetry(tmp_path: Path, monkeypatch) -> None:
    """gate 1.2 pitfall: eval traffic must be distinguishable from real
    usage in telemetry, not silently mixed in indistinguishably."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    index.search("widgets")
    index.search("widgets", tag="eval")

    entries = _read_entries(workspace)
    assert len(entries) == 2
    assert "tag" not in entries[0], "an untagged search must not gain a spurious tag field"
    assert entries[1]["tag"] == "eval"


def test_telemetry_rotates_when_it_exceeds_the_size_cap(tmp_path: Path, monkeypatch) -> None:
    """gate 0.7 (previously tracked as unbuilt): the telemetry JSONL must not
    grow unbounded. When it passes the size cap it rolls to a single `.1`
    backup and the live log starts fresh, bounding total footprint."""
    import cortex_core.search as search_mod

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    # Tiny cap so a couple of entries trip rotation deterministically.
    monkeypatch.setattr(search_mod, "_TELEMETRY_MAX_BYTES", 200)
    workspace = _make_workspace(tmp_path)
    _seed_docs(workspace)
    index = CortexSearchIndex(workspace)
    index.rebuild()

    live = _telemetry_path(workspace)
    backup = live.parent / (live.name + ".1")

    # First write: no rotation yet (file doesn't exist / is small).
    index.search("widgets")
    assert live.exists()
    assert not backup.exists()

    # Keep writing until the live log exceeds the cap, then one more search
    # triggers the roll on its next write.
    for _ in range(20):
        index.search("widgets and gears and more terms to grow the line")

    assert backup.exists(), "oversized telemetry must roll to a .1 backup"
    # The live log was reset by rotation, so it holds far fewer than all 21
    # entries -- proving growth is bounded, not unbounded accumulation.
    live_entries = [ln for ln in live.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(live_entries) < 21, "live log must have been reset by rotation"
    # Both files are still valid JSONL (no corruption from the roll).
    for path in (live, backup):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)
