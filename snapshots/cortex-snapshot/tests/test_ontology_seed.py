"""Tests for the self-maintaining ontology seed.

Contract: scanning the corpus's structured sources materializes one entity per
real artifact with the source file as provenance, derives only the structural
relations it can get right (phase depends_on chain, rubric covers domain,
rubric authored_by fable), and is idempotent (re-run upserts, never duplicates).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cortex_core import ontology as o
from cortex_core import ontology_seed as seed

REAL_SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "ontology" / "schema.yaml"


def _make_corpus(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)  # checkout marker
    (ws / "docs" / "ontology").mkdir(parents=True)
    (ws / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    shutil.copy(REAL_SCHEMA, ws / "docs" / "ontology" / "schema.yaml")

    (ws / "docs" / "MODEL-ROLES.md").write_text("# models\n", encoding="utf-8")
    (ws / "docs" / "PHASE-GATES.md").write_text(
        "# Gates\n\n## Phase 0 — Floor\n\n## Phase 1 — Measure first\n\n## Phase 2 — Retrieval\n",
        encoding="utf-8",
    )
    gaps = ws / "templates" / "workspace-control-plane" / "gaps"
    gaps.mkdir(parents=True)
    (gaps / "registry.md").write_text(
        "| id | desc |\n| `GAP-CORTEX-0001` | Gap ledger | x |\n"
        "| `GAP-CORTEX-0016` | Task ledger | y |\n",
        encoding="utf-8",
    )
    rub = ws / "calibration" / "rubrics"
    rub.mkdir(parents=True)
    (rub / "test_quality.v1.yaml").write_text("name: test_quality\n", encoding="utf-8")
    (rub / "code_quality_rubrics.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (ws / "cortex_core").mkdir()
    (ws / "cortex_core" / "ontology.py").write_text("# mod\n", encoding="utf-8")
    (ws / "cortex_core" / "__init__.py").write_text("", encoding="utf-8")  # dunder skipped
    return ws


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    return _make_corpus(tmp_path)


def test_seed_materializes_entities_from_sources(ws: Path) -> None:
    result = seed.seed(ws)
    assert result["errors"] == [], result["errors"]
    ents = o.load_entities(ws)
    # phases scanned from the headings
    assert {"phase:phase-0", "phase:phase-1", "phase:phase-2"} <= set(ents)
    # gaps from the registry table
    assert "gap:gap-cortex-0001" in ents and "gap:gap-cortex-0016" in ents
    # rubrics + their domains
    assert o.make_entity_id("rubric", "test_quality.v1") in ents
    assert o.make_entity_id("rubric_domain", "test_quality") in ents
    assert o.make_entity_id("rubric_domain", "code_quality") in ents  # _rubrics stripped
    # module, but not the dunder one
    assert "module:ontology" in ents
    assert "module:--init--" not in ents and not any(e.startswith("module:_") for e in ents)


def test_seed_derives_structural_relations(ws: Path) -> None:
    seed.seed(ws)
    # phase 2 depends_on phase 1 depends_on phase 0
    p2 = o.neighbors("phase:phase-2", predicate="depends_on", direction="out", workspace=ws)
    assert p2 and p2[0]["neighbor"] == "phase:phase-1"
    # rubric covers its domain AND is authored_by fable
    preds = {n["predicate"] for n in o.neighbors(
        o.make_entity_id("rubric", "test_quality.v1"), direction="out", workspace=ws)}
    assert "covers" in preds and "authored_by" in preds
    authored = o.neighbors(o.make_entity_id("rubric", "test_quality.v1"),
                           predicate="authored_by", direction="out", workspace=ws)
    assert authored[0]["neighbor"] == "model:fable-max"


def test_seed_is_idempotent(ws: Path) -> None:
    first = seed.seed(ws)
    n_after_first = len(o.load_entities(ws))
    second = seed.seed(ws)
    n_after_second = len(o.load_entities(ws))
    assert n_after_first == n_after_second  # upsert, not duplicate
    assert first["total_entities"] == second["total_entities"]
    # the graph still validates after a re-seed
    assert o.validate_all(ws)["ok"] is True


def test_seeded_graph_validates(ws: Path) -> None:
    seed.seed(ws)
    assert o.validate_all(ws)["ok"] is True
