"""GAP G2 — ontology → retrieval fusion (docs/ONTOLOGY-RETRIEVAL-SPEC.md).

Locks the shipped behaviour of the ontology-expansion RRF leg and the structural
doc->doc `references` derivation. The leg ships **default OFF** (PARKED — the
golden-set gate was not met, see evals/reports/ontology_retrieval_gate.md), so
the load-bearing guarantees these tests protect are:

  * OFF is a true no-op: use_ontology=False is byte-identical to the untouched
    BM25 path (no regression to the default retrieval path).
  * ON is deterministic and reuses the existing index + RRF primitive.
  * ON actually performs the multi-hop hop: it injects a graph-connected
    document that BM25 alone never retrieves (the mechanism, even though the
    aggregate gate did not clear).
  * `references` derivation is structural (a real citation) and idempotent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cortex_core import ontology as o
from cortex_core import ontology_seed as seed
from cortex_core.search import CortexSearchIndex, _ontology_normalize

REAL_SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "ontology" / "schema.yaml"


# --------------------------------------------------------------------------
# normalize helper
# --------------------------------------------------------------------------
def test_ontology_normalize_lowercases_and_strips_punct():
    assert _ontology_normalize("GLM-5.2, Phase-2!") == "glm 5 2 phase 2"
    assert _ontology_normalize("  multiple   spaces ") == "multiple spaces"


# --------------------------------------------------------------------------
# fixtures: a tiny workspace + graph where BM25 genuinely misses the neighbor
# --------------------------------------------------------------------------
def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "docs" / "ontology").mkdir(parents=True)
    (ws / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    shutil.copy(REAL_SCHEMA, ws / "docs" / "ontology" / "schema.yaml")

    # ALPHA names the query terms; BRAVO shares ZERO vocabulary with the query,
    # so a flat BM25 search over the query can only ever find ALPHA. ALPHA cites
    # BRAVO, so the ontology hop is the only path to BRAVO.
    (ws / "docs" / "ALPHA.md").write_text(
        "# Alpha\n\nThe alpha aardvark widget calibration procedure. See ALPHA links BRAVO.md for details.\n",
        encoding="utf-8",
    )
    (ws / "docs" / "BRAVO.md").write_text(
        "# Bravo\n\nQuxzzz florblenak glimberwock throcket — deliberately disjoint vocabulary.\n",
        encoding="utf-8",
    )
    return ws


def _seed_doc_graph(ws: Path) -> None:
    """Seed the two doc entities + derive the structural ALPHA->BRAVO reference."""
    schema = o.load_schema(ws)
    o.upsert_entity("doc", "ALPHA", source_paths=["docs/ALPHA.md"], workspace=ws, schema=schema)
    o.upsert_entity("doc", "BRAVO", source_paths=["docs/BRAVO.md"], workspace=ws, schema=schema)
    written, errors = seed.derive_references(ws)
    assert errors == [], errors
    assert written == 1  # ALPHA literally links BRAVO.md; BRAVO links nothing


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    w = _make_ws(tmp_path)
    _seed_doc_graph(w)
    return w


# --------------------------------------------------------------------------
# references derivation
# --------------------------------------------------------------------------
def test_references_are_structural_and_directed(ws: Path):
    nb = o.neighbors("doc:alpha", predicate="references", direction="out", workspace=ws)
    assert [n["neighbor"] for n in nb] == ["doc:bravo"]
    # BRAVO cites nothing -> no outgoing reference edge.
    assert o.neighbors("doc:bravo", predicate="references", direction="out", workspace=ws) == []


def test_references_derivation_is_idempotent(ws: Path):
    before = {
        rid for rid, r in o.load_relations(ws).items()
        if r.predicate == "references" and r.status == "active" and r.invalid_from is None
    }
    written, errors = seed.derive_references(ws)  # re-derive
    assert errors == []
    after = {
        rid for rid, r in o.load_relations(ws).items()
        if r.predicate == "references" and r.status == "active" and r.invalid_from is None
    }
    assert before == after  # deterministic ids -> upsert, no new live edge
    assert len(after) == 1


# --------------------------------------------------------------------------
# the fusion leg
# --------------------------------------------------------------------------
def _index(ws: Path) -> CortexSearchIndex:
    idx = CortexSearchIndex(ws)
    idx.rebuild()
    return idx


def test_ontology_off_is_a_true_noop(ws: Path):
    idx = _index(ws)
    q = "alpha aardvark widget"
    off = idx.search(q, limit=20, use_vector=False, use_ontology=False)
    # BM25-only default path (both flags off) must be byte-identical.
    default = idx.search(q, limit=20)
    assert [(r.path, r.chunk_index, r.rank) for r in off] == [
        (r.path, r.chunk_index, r.rank) for r in default
    ]
    # And BRAVO (reachable ONLY via the ontology hop) is absent when OFF.
    assert not any("BRAVO.md" in r.path for r in off)


def test_ontology_on_injects_graph_connected_doc_bm25_misses(ws: Path):
    idx = _index(ws)
    q = "alpha aardvark widget"
    on = idx.search(q, limit=20, use_vector=False, use_ontology=True)
    paths = [r.path for r in on]
    # The multi-hop mechanism: BRAVO shares no vocabulary with the query and is
    # unreachable by BM25, yet the ALPHA->BRAVO citation hop surfaces it.
    assert any("BRAVO.md" in p for p in paths)
    # ALPHA (the named seed doc) is still present via BM25.
    assert any("ALPHA.md" in p for p in paths)


def test_ontology_leg_is_deterministic(ws: Path):
    idx = _index(ws)
    q = "alpha aardvark widget"
    first = idx.search(q, limit=20, use_vector=False, use_ontology=True)
    second = idx.search(q, limit=20, use_vector=False, use_ontology=True)
    assert [(r.path, r.chunk_index) for r in first] == [(r.path, r.chunk_index) for r in second]


def test_ontology_leg_noop_when_query_names_no_entity(ws: Path):
    idx = _index(ws)
    # No entity surface form ("alpha"/"bravo") appears -> leg resolves nothing,
    # so ON == OFF for this query.
    q = "florblenak glimberwock"
    off = idx.search(q, limit=20, use_vector=False, use_ontology=False)
    on = idx.search(q, limit=20, use_vector=False, use_ontology=True)
    assert [(r.path, r.chunk_index) for r in off] == [(r.path, r.chunk_index) for r in on]
