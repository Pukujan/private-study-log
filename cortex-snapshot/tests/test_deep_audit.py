"""Frozen tests for Deep Audit Mode (cortex_core/deep_audit.py) — GAP-CORTEX-0009 close criteria."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import deep_audit as DA  # noqa: E402


def _leaves(n=12):
    return [DA.Closeout(id=f"c{i:02d}", date=f"2026-07-{(i % 28)+1:02d}T0{i % 9}",
                        task=f"task {i}", result=f"result {i}: recall@5 was 0.8{i % 10}, tests passed",
                        text=(f"Task {i}: did the work for module {i}. "
                              f"Result {i}: recall@5 was 0.8{i % 10} and all tests passed. "
                              f"Fixed a bug in the handler."))
            for i in range(n)]


def test_build_tree_is_multilevel():
    tree = DA.build_tree(_leaves(12), batch_size=4)
    sizes = [len(l) for l in tree["levels"]]
    assert sizes[0] == 12 and len(sizes) >= 3 and sizes[-1] == 1  # converges to a root


def test_provenance_root_traces_to_all_leaves():
    leaves = _leaves(10)
    tree = DA.build_tree(leaves, batch_size=3)
    root = tree["levels"][-1][0]
    assert set(root.leaf_ids) == {c.id for c in leaves}       # never loses a source
    assert root.date_range[0] and root.date_range[1]


def test_every_digest_node_carries_source_ids():
    tree = DA.build_tree(_leaves(10), batch_size=3)
    for level in tree["levels"][1:]:
        for node in level:
            assert node.source_ids and node.leaf_ids           # provenance-never-replacement


def test_faithfulness_gate_recorded_per_node():
    tree = DA.build_tree(_leaves(12), batch_size=4)
    for level in tree["levels"][1:]:
        for node in level:
            assert "score" in node.faithfulness and "passed" in node.faithfulness


def test_incremental_fold_in_is_partial_not_full_rebuild():
    leaves = _leaves(12)
    tree = DA.build_tree(leaves, batch_size=4)
    l1_before = len(tree["levels"][1])
    tree2 = DA.fold_in(tree, [DA.Closeout("new", "2026-07-29T09", "t", "r", "New task. Result: shipped it. Tests passed.")])
    assert tree2["n_leaves"] == 13
    reused = [n for n in tree2["levels"][1] if not n.id.startswith("L1-fold")]
    fresh = [n for n in tree2["levels"][1] if n.id.startswith("L1-fold")]
    # most old level-1 nodes are preserved; only the tail neighborhood is recomputed
    assert len(reused) >= l1_before - 2 and len(fresh) >= 1


def test_checkpoint_persists_digest_levels(tmp_path):
    tree = DA.build_tree(_leaves(10), batch_size=3)
    m = DA.checkpoint_tree(tree, tmp_path)
    assert (tmp_path / "digest_manifest.json").exists()
    assert any((tmp_path / f"level-{i}.jsonl").exists() for i in range(1, 6))
    assert m["levels"][0]["level"] == 0


def test_faithfulness_distribution_for_calibration():
    tree = DA.build_tree(_leaves(16), batch_size=4)
    dist = DA.faithfulness_distribution(tree)
    assert dist["n"] > 0 and 0.0 <= dist["p10"] <= dist["max"] <= 1.0
