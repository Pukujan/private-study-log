"""Tests for GAP G3 (docs/GAP-CLOSURE-PLAN.md) — doc-currency / freshness.

Three contracts, one per build item, TDD-first:

  1. **Staleness-gap SLI** counts a doc whose age exceeds the freshness horizon
     and surfaces it as the worst offender. It is a METRIC, never a blocker
     (detection-over-coercion) — a stale doc changes the count, it never fails
     a call.
  2. **Supersede-don't-delete validity** (Zep/Graphiti bi-temporal): asserting a
     contradicting value for the same fact key CLOSES the old fact's validity
     window (valid_to stamped) instead of deleting it — the old value drops out
     of live results but stays retrievable as historical / as-of a past time.
  3. **Incremental per-doc reindex** updates ONE document in the FTS index
     without a full-corpus rebuild (needs_rebuild stays satisfied, other docs'
     rows are untouched).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cortex_core import freshness as f
from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library" / "search").mkdir(parents=True)
    (ws / "docs" / "ontology").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return ws


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    return _make_workspace(tmp_path)


def _write_doc(ws: Path, rel: str, text: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _age_file(path: Path, days: float) -> None:
    old = time.time() - days * 86400.0
    os.utime(path, (old, old))


# --- 1. staleness-gap SLI -------------------------------------------------
def test_staleness_sli_counts_a_stale_doc_as_worst_offender(ws: Path) -> None:
    fresh = _write_doc(ws, "docs/cortex-1/fresh.md", "# Fresh\n\nrecent content.")
    stale = _write_doc(ws, "docs/cortex-1/stale.md", "# Stale\n\nold content.")
    _age_file(fresh, days=1)
    _age_file(stale, days=400)  # well past the 180d horizon

    index = CortexSearchIndex(ws)
    index.rebuild()

    report = f.staleness_report(ws, horizon_days=180)

    assert report["total_docs"] == 2
    assert report["stale_count"] == 1
    assert 0.0 < report["stale_fraction"] < 1.0
    # worst offender is the oldest doc, surfaced by path + age
    worst = report["worst_offenders"][0]
    assert worst["path"].endswith("stale.md")
    assert worst["age_days"] >= 180
    assert worst["stale"] is True


def test_staleness_sli_is_a_metric_not_a_blocker(ws: Path) -> None:
    """Even an all-stale corpus produces a report, never an exception."""
    d = _write_doc(ws, "docs/cortex-1/old.md", "# Old\n\ncontent.")
    _age_file(d, days=999)
    CortexSearchIndex(ws).rebuild()

    report = f.staleness_report(ws, horizon_days=180)
    assert report["stale_count"] == 1
    assert report["stale_fraction"] == 1.0  # 100% stale, still just a number


def test_default_horizon_matches_recorded_180d_decision() -> None:
    # Recorded decision: DEEP-RESEARCH-DESIGN.md §98 refresh_policy: 180d,
    # ties to the ROADMAP corpus-freshness SLI. Not a guessed number.
    assert f.DEFAULT_FRESHNESS_HORIZON_DAYS == 180


# --- 2. supersede-don't-delete validity ----------------------------------
def test_contradicting_fact_closes_the_old_validity_window_not_deleted(ws: Path) -> None:
    src = _write_doc(ws, "docs/note.md", "# note")

    r1 = f.assert_fact("phase2.status", "in-progress", source_path="docs/note.md", workspace=ws)
    assert r1["ok"]
    old_id = r1["fact_id"]

    # contradiction: same key, different value -> supersede
    r2 = f.assert_fact("phase2.status", "closed", source_path="docs/note.md", workspace=ws)
    assert r2["ok"]
    assert r2["superseded"] == [old_id]

    live = f.live_facts(ws)
    live_vals = {(x.key, x.value) for x in live}
    assert ("phase2.status", "closed") in live_vals
    assert ("phase2.status", "in-progress") not in live_vals  # excluded from live

    # old value NOT deleted: its window is closed and it stays retrievable
    history = f.facts_for_key("phase2.status", workspace=ws)
    assert len(history) == 2
    closed = [x for x in history if x.value == "in-progress"][0]
    assert closed.valid_to is not None          # window closed
    assert closed.status == "superseded"
    assert closed.superseded_by == r2["fact_id"]

    # live head has an OPEN window
    head = [x for x in history if x.value == "closed"][0]
    assert head.valid_to is None


def test_historical_fact_is_retrievable_as_of_a_past_time(ws: Path) -> None:
    _write_doc(ws, "docs/note.md", "# note")
    r1 = f.assert_fact("owner", "alice", source_path="docs/note.md", workspace=ws)
    t_between = f._now()
    time.sleep(0.01)
    f.assert_fact("owner", "bob", source_path="docs/note.md", workspace=ws)

    # as-of the moment only alice was live
    as_of_old = f.fact_as_of("owner", t_between, workspace=ws)
    assert as_of_old is not None and as_of_old.value == "alice"

    # as-of now, bob is live
    as_of_now = f.fact_as_of("owner", f._now(), workspace=ws)
    assert as_of_now is not None and as_of_now.value == "bob"


def test_reasserting_same_value_does_not_spuriously_supersede(ws: Path) -> None:
    _write_doc(ws, "docs/note.md", "# note")
    f.assert_fact("k", "v", source_path="docs/note.md", workspace=ws)
    r2 = f.assert_fact("k", "v", source_path="docs/note.md", workspace=ws)
    assert r2["superseded"] == []  # no contradiction -> no window closed
    assert len(f.live_facts(ws)) == 1


# --- 3. incremental per-doc reindex --------------------------------------
def test_incremental_reindex_updates_one_doc_without_full_rebuild(ws: Path) -> None:
    a = _write_doc(ws, "docs/cortex-1/a.md", "# A\n\nalpha aardvark content.")
    b = _write_doc(ws, "docs/cortex-1/b.md", "# B\n\nbravo baseline content.")
    index = CortexSearchIndex(ws)
    index.rebuild()

    # capture b's indexed_at so we can prove it was NOT rewritten
    conn = index.connect()
    b_indexed_before = conn.execute(
        "SELECT indexed_at FROM documents WHERE path = ?", (b.as_posix(),)
    ).fetchone()[0]
    conn.close()

    # change ONE doc's content
    a.write_text("# A\n\nzelkova zeppelin replacement content.", encoding="utf-8")

    result = f.incremental_reindex(ws, [a])
    assert result["reindexed"] == [a.as_posix()]
    assert result["full_rebuild"] is False

    # new content is searchable, old content is gone
    assert any(r.path == a.as_posix() for r in index.search("zeppelin replacement"))
    assert not index.search("aardvark")

    # the untouched doc b was NOT rewritten by the incremental path
    conn = index.connect()
    b_indexed_after = conn.execute(
        "SELECT indexed_at FROM documents WHERE path = ?", (b.as_posix(),)
    ).fetchone()[0]
    conn.close()
    assert b_indexed_after == b_indexed_before

    # and the index no longer considers itself stale for the changed doc
    assert index.needs_rebuild() is False


def test_incremental_reindex_of_new_doc_adds_it(ws: Path) -> None:
    _write_doc(ws, "docs/cortex-1/a.md", "# A\n\nalpha content.")
    index = CortexSearchIndex(ws)
    index.rebuild()

    new = _write_doc(ws, "docs/cortex-1/c.md", "# C\n\ncharlie fresh addition.")
    result = f.incremental_reindex(ws, [new])
    assert new.as_posix() in result["reindexed"]
    assert any(r.path == new.as_posix() for r in index.search("charlie fresh"))
