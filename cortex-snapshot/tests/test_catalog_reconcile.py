"""RED-first tests for I5 catalog-reconcile (docs/GAP-CLOSURE-PLAN.md §I5).

Contract: detect drift between the declared corpus catalog
(``library/cortex-library/sources/collection.yaml``) and what's actually on
disk under ``docs/cortex-*/``. Report three drift kinds:

  missing    -- a catalog entry whose ``local_path`` file is not on disk.
  orphaned   -- a doc file on disk that no catalog entry references.
  path_drift -- an entry whose absolute ``local_path`` is rooted at a different
                machine/checkout than the current workspace (cosmetic, real).

``--fix`` performs SAFE ADDITIONS ONLY: it appends catalog entries for orphaned
on-disk docs. It never removes ``missing`` entries (that is destructive and a
missing file may just be un-synced), and it round-trips the YAML with the same
``safe_dump`` serializer ``fetch.py`` uses.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from cortex_core.catalog_reconcile import (
    reconcile,
    apply_safe_additions,
    reconcile_objective_catalog,
    regenerate_objective_catalog,
    render_objective_catalog,
)


def _catalog_path(ws: Path) -> Path:
    return ws / "library" / "cortex-library" / "sources" / "collection.yaml"


def _make_ws(tmp_path: Path, sources: list[dict], docs: list[str]) -> Path:
    ws = tmp_path / "ws"
    (ws / "library" / "cortex-library" / "sources").mkdir(parents=True, exist_ok=True)
    _catalog_path(ws).write_text(
        yaml.safe_dump({"sources": sources}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    for rel in docs:
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# doc\n", encoding="utf-8")
    return ws


def _entry(name: str, ws: Path, rel: str) -> dict:
    return {
        "name": name,
        "source_url": f"https://example.com/{name}",
        "local_path": (ws / rel).as_posix(),
        "checked_at": "2026-07-13T00:00:00+00:00",
    }


def test_synced_catalog_is_clean(tmp_path):
    """A catalog whose entries exactly match on-disk docs has zero drift."""
    ws = tmp_path / "ws"
    (ws / "library" / "cortex-library" / "sources").mkdir(parents=True, exist_ok=True)
    docs = ["docs/cortex-1/a.md", "docs/cortex-1/b.md"]
    for rel in docs:
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# doc\n", encoding="utf-8")
    sources = [_entry("a", ws, "docs/cortex-1/a.md"), _entry("b", ws, "docs/cortex-1/b.md")]
    _catalog_path(ws).write_text(
        yaml.safe_dump({"sources": sources}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    report = reconcile(ws)
    assert report["missing"] == []
    assert report["orphaned"] == []
    assert report["path_drift"] == []
    assert report["clean"] is True


def test_detects_missing_and_orphaned(tmp_path):
    """Inject drift both ways: a catalog entry with no file (missing) and an
    on-disk doc no entry references (orphaned)."""
    ws = tmp_path / "ws"
    (ws / "library" / "cortex-library" / "sources").mkdir(parents=True, exist_ok=True)
    # On disk: a.md (catalogued) + orphan.md (NOT catalogued).
    for rel in ["docs/cortex-1/a.md", "docs/cortex-1/orphan.md"]:
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# doc\n", encoding="utf-8")
    # Catalog: a.md (present) + ghost.md (missing on disk).
    sources = [
        _entry("a", ws, "docs/cortex-1/a.md"),
        _entry("ghost", ws, "docs/cortex-1/ghost.md"),
    ]
    _catalog_path(ws).write_text(
        yaml.safe_dump({"sources": sources}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    report = reconcile(ws)
    assert [m["name"] for m in report["missing"]] == ["ghost"]
    assert report["orphaned"] == ["docs/cortex-1/orphan.md"]
    assert report["clean"] is False


def test_detects_path_drift_foreign_root(tmp_path):
    """An entry whose absolute local_path is rooted at a different checkout is
    path_drift -- the file may exist here under the same relative path but the
    recorded root points elsewhere."""
    ws = tmp_path / "ws"
    (ws / "library" / "cortex-library" / "sources").mkdir(parents=True, exist_ok=True)
    p = ws / "docs/cortex-1/a.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# doc\n", encoding="utf-8")
    sources = [
        {
            "name": "a",
            "source_url": "https://example.com/a",
            "local_path": "/home/otheruser/stupidly-simple-cortex/docs/cortex-1/a.md",
            "checked_at": "2026-07-13T00:00:00+00:00",
        }
    ]
    _catalog_path(ws).write_text(
        yaml.safe_dump({"sources": sources}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    report = reconcile(ws)
    # File resolves (same repo-relative path), so not missing -- but the root drifted.
    assert report["missing"] == []
    assert [d["name"] for d in report["path_drift"]] == ["a"]


def test_fix_adds_orphans_but_not_removes_missing(tmp_path):
    """--fix appends catalog entries for orphaned docs, and leaves the missing
    (destructive-to-remove) entry untouched."""
    ws = tmp_path / "ws"
    (ws / "library" / "cortex-library" / "sources").mkdir(parents=True, exist_ok=True)
    for rel in ["docs/cortex-1/a.md", "docs/cortex-1/orphan.md"]:
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# doc\n", encoding="utf-8")
    sources = [
        _entry("a", ws, "docs/cortex-1/a.md"),
        _entry("ghost", ws, "docs/cortex-1/ghost.md"),
    ]
    _catalog_path(ws).write_text(
        yaml.safe_dump({"sources": sources}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    added = apply_safe_additions(ws)
    assert added == ["docs/cortex-1/orphan.md"]

    data = yaml.safe_load(_catalog_path(ws).read_text(encoding="utf-8"))
    names = {e["name"] for e in data["sources"]}
    local_paths = {e["local_path"] for e in data["sources"]}
    # orphan added, ghost (missing) still present -- never destroyed.
    assert any("orphan.md" in lp for lp in local_paths)
    assert "ghost" in names
    # Re-running is idempotent: the orphan is now catalogued.
    assert apply_safe_additions(ws) == []
    report = reconcile(ws)
    assert report["orphaned"] == []


# --------------------------------------------------------------------------
# I5 part 2: the OBJECTIVE gold catalog (lanes/counts/promotion coverage).
# --------------------------------------------------------------------------
import json


def _make_objective_lane(ws: Path, name: str, gold_rows: int, *,
                         promotion: dict | None = None, label_field: str = "objective_verdict"):
    d = ws / "evals" / f"objective_{name}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "hard_gold.jsonl").write_text(
        "\n".join(json.dumps({label_field: "pass", "i": i}) for i in range(gold_rows)) + "\n",
        encoding="utf-8",
    )
    if promotion is not None:
        (d / "PROMOTION.jsonl").write_text(json.dumps(promotion) + "\n", encoding="utf-8")


def test_objective_reconcile_counts_from_disk(tmp_path):
    """Per-lane gold count == committed hard_gold rows; totals sum from disk."""
    ws = tmp_path / "ws"
    _make_objective_lane(ws, "datetime_correctness", 20, promotion={
        "source": "evals/objective_datetime_correctness/hard_gold.jsonl",
        "count": 20, "label_field": "objective_verdict",
        "label_authority": "stdlib_datetime_computation", "judge_in_verdict_path": False})
    _make_objective_lane(ws, "tool_calling", 131, promotion={
        "source": "evals/objective_tool_calling/hard_gold.jsonl",
        "count": 3279, "label_field": "objective_verdict",
        "label_authority": "bfcl_ast_checker (3rd-party)", "judge_in_verdict_path": False})

    report = reconcile_objective_catalog(ws)
    assert report["n_lanes"] == 2
    assert report["disk_gold_total"] == 151          # 20 + 131, re-countable from disk
    assert report["n_lanes_with_promotion"] == 2
    assert report["lanes_missing_promotion"] == []
    assert report["judge_free"] is True
    by = {l["name"]: l for l in report["lanes"]}
    assert by["datetime_correctness"]["disk_rows"] == 20
    assert by["datetime_correctness"]["data_class"] == "synthetic"
    assert by["tool_calling"]["disk_rows"] == 131
    assert by["tool_calling"]["data_class"] == "real"       # bfcl marker
    # tool_calling declares 3,279 promoted but only 131 committed -> surfaced, not conflated.
    assert {"name": "tool_calling", "disk_rows": 131, "ledger_declared": 3279} in report["generated_beyond_disk"]


def test_objective_reconcile_flags_missing_promotion(tmp_path):
    """A lane with hard_gold but no promotion record is flagged, not silently counted clean."""
    ws = tmp_path / "ws"
    _make_objective_lane(ws, "orphan_lane", 5, promotion=None)
    report = reconcile_objective_catalog(ws)
    assert report["lanes_missing_promotion"] == ["orphan_lane"]
    assert report["clean"] is False


def test_objective_regenerate_makes_catalog_match_disk(tmp_path):
    """--objective-fix writes a catalog whose header lane-count + total == disk, and
    a second reconcile against the freshly-written catalog is clean."""
    ws = tmp_path / "ws"
    for i, n in enumerate(["aggregation_correctness", "regex_correctness", "sql_correctness"]):
        _make_objective_lane(ws, n, 10 + i, promotion={
            "source": f"evals/objective_{n}/hard_gold.jsonl",
            "count": 10 + i, "label_field": "objective_verdict",
            "label_authority": "det", "judge_in_verdict_path": False})

    report = regenerate_objective_catalog(ws)
    catalog = (ws / "docs" / "OBJECTIVE-GOLD-CATALOG.md").read_text(encoding="utf-8")
    assert "3 lanes" in catalog
    assert f"{report['disk_gold_total']:,} committed gold records" in catalog
    # every lane appears
    for n in ["aggregation_correctness", "regex_correctness", "sql_correctness"]:
        assert f"`{n}`" in catalog
    # re-reconcile now sees the declared header matching disk lane-count -> clean
    report2 = reconcile_objective_catalog(ws)
    assert report2["declared_catalog"]["lanes"] == 3
    assert report2["clean"] is True
