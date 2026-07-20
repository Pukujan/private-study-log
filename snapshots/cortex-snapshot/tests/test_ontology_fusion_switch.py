"""GAP G2-local — the corpus-characteristic ontology-fusion switch + configurable
hops (docs/ONTOLOGY-RETRIEVAL-SPEC.md; evals/reports/ontology_retrieval_gate.md).

The global default stays OFF (the gate PARKED ontology fusion for dense corpora,
where it was a net wash). A *scattered* corpus opts in WITHOUT any global default
change, via a per-workspace config file `docs/ontology/retrieval.yaml`:

    ontology_fusion:
      enabled: true
      max_hops: 2

These tests lock the switch's load-bearing guarantees:
  * No config file  -> (disabled, default hops): byte-identical to the parked
    default path (dense repo unchanged, reversible).
  * enabled:true    -> ontology fusion turns on for THIS workspace with NO
    use_ontology argument (the corpus-characteristic auto-on).
  * max_hops:2      -> the leg traverses 2 ontology edges, reaching a doc two
    hops away that a 1-hop expansion (the recorded default) misses.
  * An explicit call arg overrides the config; hop count is bounded.
  * A malformed/partial config degrades safely to disabled.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cortex_core import ontology as o
from cortex_core import ontology_seed as seed
from cortex_core.search import CortexSearchIndex, ONTOLOGY_MAX_HOPS

REAL_SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "ontology" / "schema.yaml"


def _make_chain_ws(tmp_path: Path) -> Path:
    """A 3-doc citation CHAIN A -> B -> C where each doc's vocabulary is disjoint
    from the others. A query phrased in A's vocabulary can only reach B via one
    ontology hop and C via two. This is the antithesis of the dense repo where
    cross-referenced docs share vocabulary (the reason the gate parked)."""
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "docs" / "ontology").mkdir(parents=True)
    (ws / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    shutil.copy(REAL_SCHEMA, ws / "docs" / "ontology" / "schema.yaml")

    # ALPHA names the query terms and literally links BRAVO. BRAVO links CHARLIE.
    # Vocabularies are pairwise disjoint, so only the graph connects them.
    (ws / "docs" / "ALPHA.md").write_text(
        "# Alpha\n\nThe alpha aardvark widget calibration. See BRAVO.md.\n",
        encoding="utf-8",
    )
    (ws / "docs" / "BRAVO.md").write_text(
        "# Bravo\n\nQuxzzz florblenak intermediate node. See CHARLIE.md.\n",
        encoding="utf-8",
    )
    (ws / "docs" / "CHARLIE.md").write_text(
        "# Charlie\n\nZibblewock throcketmancy — the two-hop answer, wholly disjoint terms.\n",
        encoding="utf-8",
    )

    schema = o.load_schema(ws)
    o.upsert_entity("doc", "ALPHA", source_paths=["docs/ALPHA.md"], workspace=ws, schema=schema)
    o.upsert_entity("doc", "BRAVO", source_paths=["docs/BRAVO.md"], workspace=ws, schema=schema)
    o.upsert_entity("doc", "CHARLIE", source_paths=["docs/CHARLIE.md"], workspace=ws, schema=schema)
    written, errors = seed.derive_references(ws)
    assert errors == [], errors
    assert written == 2  # ALPHA->BRAVO, BRAVO->CHARLIE
    return ws


@pytest.fixture()
def chain_ws(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    return _make_chain_ws(tmp_path)


def _write_config(ws: Path, body: str) -> None:
    (ws / "docs" / "ontology" / "retrieval.yaml").write_text(body, encoding="utf-8")


def _index(ws: Path) -> CortexSearchIndex:
    idx = CortexSearchIndex(ws)
    idx.rebuild()
    return idx


# --------------------------------------------------------------------------
# config resolution
# --------------------------------------------------------------------------
def test_no_config_is_disabled_default_hops(chain_ws: Path):
    idx = _index(chain_ws)
    assert idx._ontology_fusion_config() == (False, ONTOLOGY_MAX_HOPS)


def test_enabled_config_is_read(chain_ws: Path):
    _write_config(chain_ws, "ontology_fusion:\n  enabled: true\n  max_hops: 2\n")
    idx = _index(chain_ws)
    assert idx._ontology_fusion_config() == (True, 2)


def test_malformed_config_degrades_to_disabled(chain_ws: Path):
    _write_config(chain_ws, "this: [is not, the schema\n")  # invalid yaml
    idx = _index(chain_ws)
    assert idx._ontology_fusion_config() == (False, ONTOLOGY_MAX_HOPS)


def test_hops_are_bounded(chain_ws: Path):
    _write_config(chain_ws, "ontology_fusion:\n  enabled: true\n  max_hops: 99\n")
    idx = _index(chain_ws)
    enabled, hops = idx._ontology_fusion_config()
    assert enabled is True
    assert 1 <= hops <= 3  # fan-out bounded; never the raw 99


# --------------------------------------------------------------------------
# the switch drives retrieval end-to-end
# --------------------------------------------------------------------------
def test_disabled_config_leaves_default_path_untouched(chain_ws: Path):
    # No config file: the parked default. use_ontology defaults False -> BRAVO
    # (reachable only via the graph) must be absent.
    idx = _index(chain_ws)
    q = "alpha aardvark widget"
    off = idx.search(q, limit=20)
    assert not any("BRAVO" in r.path for r in off)


def test_enabled_config_auto_turns_on_without_arg(chain_ws: Path):
    # enabled:true makes ontology fusion fire with NO use_ontology argument --
    # the corpus-characteristic auto-on. At 1 hop, BRAVO (one edge away) is
    # surfaced even though it shares no vocabulary with the query.
    _write_config(chain_ws, "ontology_fusion:\n  enabled: true\n  max_hops: 1\n")
    idx = _index(chain_ws)
    q = "alpha aardvark widget"
    on = idx.search(q, limit=20)  # no use_ontology arg
    assert any("BRAVO" in r.path for r in on)


def test_one_hop_misses_two_hop_doc(chain_ws: Path):
    # At the recorded default of 1 hop, CHARLIE (two edges from ALPHA) is NOT
    # reached -- confirming the hop bound actually bounds.
    _write_config(chain_ws, "ontology_fusion:\n  enabled: true\n  max_hops: 1\n")
    idx = _index(chain_ws)
    on = idx.search("alpha aardvark widget", limit=20)
    assert not any("CHARLIE" in r.path for r in on)


def test_two_hops_reach_two_hop_doc(chain_ws: Path):
    # max_hops:2 traverses BOTH edges: CHARLIE, two hops from the named ALPHA
    # and vocabulary-disjoint from the query, is surfaced. This is the regime the
    # gate parked pending -- a scattered corpus needing 2+ ontology edges.
    _write_config(chain_ws, "ontology_fusion:\n  enabled: true\n  max_hops: 2\n")
    idx = _index(chain_ws)
    on = idx.search("alpha aardvark widget", limit=20)
    assert any("CHARLIE" in r.path for r in on)


def test_explicit_hops_arg_overrides_config(chain_ws: Path):
    # Config says 1 hop; an explicit ontology_max_hops=2 wins (needed so the eval
    # harness can measure each hop arm without rewriting the workspace config).
    _write_config(chain_ws, "ontology_fusion:\n  enabled: true\n  max_hops: 1\n")
    idx = _index(chain_ws)
    on = idx.search("alpha aardvark widget", limit=20, ontology_max_hops=2)
    assert any("CHARLIE" in r.path for r in on)


def test_explicit_use_ontology_still_honored_without_config(chain_ws: Path):
    # The pre-existing explicit flag path is unchanged: use_ontology=True fuses
    # even with no config file present.
    idx = _index(chain_ws)
    on = idx.search("alpha aardvark widget", limit=20, use_ontology=True)
    assert any("BRAVO" in r.path for r in on)
