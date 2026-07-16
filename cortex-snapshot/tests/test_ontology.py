"""Tests for the living ontology (Phase 7 / ROADMAP Phase-4).

The core contract: schema-validated entities/relations, append-only reduction
(last line per id wins), referential wholeness (no edges to unknown entities),
bi-temporal invalidation (superseded, never deleted), and the headline query --
"which entity is current?" following supersedes edges.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cortex_core import ontology as o

REAL_SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "ontology" / "schema.yaml"


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (ws / "docs" / "ontology").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    # Ship the real schema into the test workspace so tests bind to what actually
    # ships, not a stand-in that could drift.
    shutil.copy(REAL_SCHEMA, ws / "docs" / "ontology" / "schema.yaml")
    # A couple of real files entities can cite for provenance.
    (ws / "docs" / "MODEL-ROLES.md").write_text("# models", encoding="utf-8")
    (ws / "docs" / "PHASE-GATES.md").write_text("# phases", encoding="utf-8")
    return ws


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    return _make_workspace(tmp_path)


def _model(ws: Path, name: str) -> str:
    res = o.upsert_entity("model", name, source_paths=["docs/MODEL-ROLES.md"],
                          author_model="claude-opus", workspace=ws)
    assert res["ok"], res
    return res["entity_id"]


# --- schema -------------------------------------------------------------
def test_schema_loads_and_declares_types(ws: Path) -> None:
    sch = o.load_schema(ws)
    assert "model" in sch.entity_types
    assert "supersedes" in sch.relation_types
    assert "active" in sch.status_values
    assert sch.object_types("authored_by") == ["model"]


# --- entities -----------------------------------------------------------
def test_upsert_entity_lands_in_jsonl(ws: Path) -> None:
    res = o.upsert_entity("model", "Fable Max", source_paths=["docs/MODEL-ROLES.md"], workspace=ws)
    assert res["ok"] is True
    assert res["entity_id"] == "model:fable-max"  # deterministic slug id
    path = ws / "docs" / "ontology" / "entities.jsonl"
    assert path.is_file()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["event"] == "create"


def test_reupsert_is_update_not_duplicate(ws: Path) -> None:
    o.upsert_entity("model", "Opus", source_paths=["docs/MODEL-ROLES.md"], workspace=ws)
    o.upsert_entity("model", "Opus", summary="frontier author", source_paths=["docs/MODEL-ROLES.md"], workspace=ws)
    ents = o.load_entities(ws)
    assert len(ents) == 1  # same id -> collapsed to one current entity
    assert ents["model:opus"].summary == "frontier author"
    # created_at is preserved across the update; the log still has both lines.
    lines = (ws / "docs" / "ontology" / "entities.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([x for x in lines if x.strip()]) == 2


def test_entity_requires_source_path(ws: Path) -> None:
    res = o.upsert_entity("model", "ghost", source_paths=[], workspace=ws)
    assert res["ok"] is False
    assert any("source_paths is empty" in e for e in res["errors"])


def test_entity_rejects_nonexistent_source(ws: Path) -> None:
    res = o.upsert_entity("model", "ghost", source_paths=["docs/DOES-NOT-EXIST.md"], workspace=ws)
    assert res["ok"] is False
    assert any("does not resolve" in e for e in res["errors"])


def test_entity_rejects_unknown_type(ws: Path) -> None:
    res = o.upsert_entity("wombat", "x", source_paths=["docs/MODEL-ROLES.md"], workspace=ws)
    assert res["ok"] is False
    assert any("unknown entity type" in e for e in res["errors"])


# --- relations ----------------------------------------------------------
def test_assert_relation_between_known_entities(ws: Path) -> None:
    o.upsert_entity("rubric", "test_quality", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    _model(ws, "fable-max")
    # underscore in the name slugs to a hyphen in the id: test_quality -> test-quality
    res = o.assert_relation("rubric:test-quality", "authored_by", "model:fable-max",
                            source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    assert res["ok"] is True
    assert res["relation_id"].startswith("rel-")


def test_relation_to_unknown_entity_is_rejected(ws: Path) -> None:
    _model(ws, "fable-max")
    res = o.assert_relation("rubric:nope", "authored_by", "model:fable-max", workspace=ws)
    assert res["ok"] is False
    assert any("not a known entity" in e for e in res["errors"])


def test_relation_type_constraint_enforced(ws: Path) -> None:
    # authored_by requires the object to be a model; point it at a rubric instead.
    o.upsert_entity("rubric", "a", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.upsert_entity("rubric", "b", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    res = o.assert_relation("rubric:a", "authored_by", "rubric:b", workspace=ws)
    assert res["ok"] is False
    assert any("forbids object type" in e for e in res["errors"])


def test_self_loop_rejected(ws: Path) -> None:
    _model(ws, "m")
    res = o.assert_relation("model:m", "supersedes", "model:m", workspace=ws)
    assert res["ok"] is False
    assert any("self-loop" in e for e in res["errors"])


# --- bi-temporal invalidation ------------------------------------------
def test_invalidate_relation_keeps_history(ws: Path) -> None:
    o.upsert_entity("doc", "old", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.upsert_entity("doc", "new", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    r = o.assert_relation("doc:new", "supersedes", "doc:old", workspace=ws)
    rid = r["relation_id"]
    assert o.load_relations(ws)[rid].invalid_from is None

    inv = o.invalidate_relation(rid, "was wrong", workspace=ws)
    assert inv["ok"] is True
    rel = o.load_relations(ws)[rid]
    assert rel.status == "superseded"
    assert rel.invalid_from is not None  # invalidated, not deleted
    # The edge is no longer live, but still present in the log (auditable).
    assert o.neighbors("doc:new", workspace=ws) == []
    assert len(o.neighbors("doc:new", include_invalid=True, workspace=ws)) == 1


# --- queries ------------------------------------------------------------
def test_resolve_by_alias(ws: Path) -> None:
    o.upsert_entity("benchmark", "SWE-bench Verified", aliases=["swebench", "swe-bench"],
                    source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    e = o.resolve_entity("swebench", ws)
    assert e is not None and e.name == "SWE-bench Verified"


def test_neighbors_direction(ws: Path) -> None:
    o.upsert_entity("rubric", "r", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    _model(ws, "fable-max")
    o.assert_relation("rubric:r", "authored_by", "model:fable-max", workspace=ws)
    out_edges = o.neighbors("rubric:r", direction="out", workspace=ws)
    assert len(out_edges) == 1 and out_edges[0]["neighbor"] == "model:fable-max"
    in_edges = o.neighbors("model:fable-max", direction="in", workspace=ws)
    assert len(in_edges) == 1 and in_edges[0]["neighbor"] == "rubric:r"


def test_current_version_follows_supersedes(ws: Path) -> None:
    for n in ("v1", "v2", "v3"):
        o.upsert_entity("doc", n, source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.supersede_entity("doc:v1", "doc:v2", reason="v2 replaces v1", workspace=ws)
    o.supersede_entity("doc:v2", "doc:v3", reason="v3 replaces v2", workspace=ws)

    cur = o.current_version("v1", ws)
    assert cur["found"] is True
    assert cur["current"]["entity_id"] == "doc:v3"
    assert cur["is_current"] is False
    assert cur["supersession_chain"] == ["doc:v1", "doc:v2", "doc:v3"]
    # v1 is now marked superseded, v3 stays active.
    assert o.load_entities(ws)["doc:v1"].status == "superseded"
    assert o.load_entities(ws)["doc:v3"].status == "active"


def test_current_version_of_head_is_itself(ws: Path) -> None:
    o.upsert_entity("doc", "solo", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    cur = o.current_version("solo", ws)
    assert cur["is_current"] is True
    assert cur["current"]["entity_id"] == "doc:solo"


def test_graph_stats(ws: Path) -> None:
    _model(ws, "fable-max")
    o.upsert_entity("rubric", "r", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.assert_relation("rubric:r", "authored_by", "model:fable-max", workspace=ws)
    stats = o.graph_stats(ws)
    assert stats["entities"] == 2
    assert stats["entities_by_type"] == {"model": 1, "rubric": 1}
    assert stats["relations_live"] == 1
    assert stats["relations_by_predicate"] == {"authored_by": 1}


def test_validate_all_clean(ws: Path) -> None:
    _model(ws, "fable-max")
    o.upsert_entity("rubric", "r", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.assert_relation("rubric:r", "authored_by", "model:fable-max", workspace=ws)
    report = o.validate_all(ws)
    assert report["ok"] is True


def test_torn_final_line_does_not_break_reads(ws: Path) -> None:
    _model(ws, "fable-max")
    path = ws / "docs" / "ontology" / "entities.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"entity_id": "model:torn", "type": "mod')  # truncated
    ents = o.load_entities(ws)
    assert list(ents) == ["model:fable-max"]


# --- CLI ----------------------------------------------------------------
def test_cli_stats_and_current(ws: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CORTEX_WORKSPACE", str(ws))
    o.upsert_entity("doc", "v1", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.upsert_entity("doc", "v2", source_paths=["docs/PHASE-GATES.md"], workspace=ws)
    o.supersede_entity("doc:v1", "doc:v2", reason="r", workspace=ws)
    o.main(["current", "v1"])
    out = json.loads(capsys.readouterr().out)
    assert out["current"]["entity_id"] == "doc:v2"
