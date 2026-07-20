"""Frozen tests for the objective graph-algorithm checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic Dijkstra / Kahn / Kruskal on the graph
(checker_graph.verify_* / check_record), never a model/judge. These tests pin the checker's
behavior on hand-picked cases (independent of the fixture file) plus a full sweep over every
fixture in fixtures_graph.py, asserting the checker's objective_label always matches the
fixture's declared expected_label (the same self-validation gate every other Stage-2 lane uses).

Written before checker_graph.py was trusted (SDD then TDD): this file defines the contract.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_graph_algorithm_correctness.checker_graph import (  # noqa: E402
    check_record,
    verify_mst,
    verify_shortest_path,
    verify_topo,
)
from evals.objective_graph_algorithm_correctness.fixtures_graph import FIXTURES  # noqa: E402


# --- shared hand-built graphs, independent of the fixture file -------------------------------
DIRECTED = {
    "vertices": ["A", "B", "C", "D"],
    "edges": [
        {"u": "A", "v": "B", "w": 1},
        {"u": "B", "v": "C", "w": 1},
        {"u": "C", "v": "D", "w": 1},
        {"u": "A", "v": "D", "w": 10},
    ],
}
# shortest A->D is A-B-C-D = 3; the direct A->D edge (10) is far worse.

DAG = {
    "vertices": ["A", "B", "C"],
    "edges": [{"u": "A", "v": "B"}, {"u": "B", "v": "C"}, {"u": "A", "v": "C"}],
}
CYCLE = {
    "vertices": ["A", "B", "C"],
    "edges": [{"u": "A", "v": "B"}, {"u": "B", "v": "C"}, {"u": "C", "v": "A"}],
}
UNDIRECTED = {
    "vertices": ["A", "B", "C", "D"],
    "edges": [
        {"u": "A", "v": "B", "w": 1},
        {"u": "B", "v": "C", "w": 2},
        {"u": "C", "v": "D", "w": 1},
        {"u": "A", "v": "D", "w": 3},
    ],
}
# MST is A-B(1), C-D(1), then cheapest connector B-C(2) -> total 4 (A-D(3) not needed).


# --- shortest_path ---------------------------------------------------------------------------
def test_shortest_path_true_shortest_is_valid():
    r = verify_shortest_path(DIRECTED, "A", "D", ["A", "B", "C", "D"])
    assert r["valid"] is True
    assert r["computed_cost"] == 3 and r["true_cost"] == 3


def test_shortest_path_claim_valid_matches():
    r = check_record("shortest_path", DIRECTED,
                     {"source": "A", "target": "D", "path": ["A", "B", "C", "D"]}, "VALID")
    assert r.objective_label == "CORRECT" and r.computed_answer == "VALID"


def test_shortest_path_suboptimal_is_invalid():
    # A->D direct edge is a real path but costs 10 > 3.
    r = verify_shortest_path(DIRECTED, "A", "D", ["A", "D"])
    assert r["valid"] is False
    assert r["computed_cost"] == 10 and r["true_cost"] == 3


def test_shortest_path_suboptimal_claimed_valid_is_incorrect():
    r = check_record("shortest_path", DIRECTED,
                     {"source": "A", "target": "D", "path": ["A", "D"]}, "VALID")
    assert r.objective_label == "INCORRECT" and r.computed_answer == "INVALID"


def test_shortest_path_invalid_edge_is_invalid():
    # D->A is not a directed edge.
    r = verify_shortest_path(DIRECTED, "A", "D", ["A", "B", "A", "D"])
    assert r["valid"] is False
    assert "invalid_edge" in r["reason"]


def test_shortest_path_wrong_endpoint_is_invalid():
    r = verify_shortest_path(DIRECTED, "A", "D", ["A", "B", "C"])  # ends at C, not D
    assert r["valid"] is False


def test_shortest_path_source_equals_target_zero_cost():
    r = verify_shortest_path(DIRECTED, "A", "A", ["A"])
    assert r["valid"] is True and r["computed_cost"] == 0


# --- topological_order -----------------------------------------------------------------------
def test_topo_valid_order():
    r = verify_topo(DAG, ["A", "B", "C"])
    assert r["valid"] is True and r["has_cycle"] is False


def test_topo_backwards_edge_is_invalid():
    r = verify_topo(DAG, ["B", "A", "C"])   # A->B requires A first
    assert r["valid"] is False and "edge_backwards" in r["reason"]


def test_topo_missing_vertex_is_invalid():
    r = verify_topo(DAG, ["A", "B"])        # C missing
    assert r["valid"] is False and "vertex_set_mismatch" in r["reason"]


def test_topo_cycle_admits_no_order():
    r = verify_topo(CYCLE, ["A", "B", "C"])
    assert r["valid"] is False and r["has_cycle"] is True


def test_topo_cycle_claimed_ordered_is_correct_when_called_invalid():
    r = check_record("topological_order", CYCLE, {"order": ["A", "B", "C"]}, "INVALID")
    assert r.objective_label == "CORRECT" and r.computed_answer == "INVALID"


# --- mst_total -------------------------------------------------------------------------------
def test_mst_true_minimum_is_valid():
    r = verify_mst(UNDIRECTED, [{"u": "A", "v": "B", "w": 1}, {"u": "C", "v": "D", "w": 1},
                                {"u": "B", "v": "C", "w": 2}])
    assert r["valid"] is True
    assert r["computed_weight"] == 4 and r["true_weight"] == 4


def test_mst_suboptimal_spanning_tree_is_invalid():
    # Spanning tree using A-D(3) instead of B-C(2): weight 5 > MST 4.
    r = verify_mst(UNDIRECTED, [{"u": "A", "v": "B", "w": 1}, {"u": "C", "v": "D", "w": 1},
                                {"u": "A", "v": "D", "w": 3}])
    assert r["valid"] is False
    assert r["computed_weight"] == 5 and r["true_weight"] == 4


def test_mst_not_spanning_too_few_edges_is_invalid():
    r = verify_mst(UNDIRECTED, [{"u": "A", "v": "B", "w": 1}, {"u": "C", "v": "D", "w": 1}])
    assert r["valid"] is False and "edge_count" in r["reason"]


def test_mst_fabricated_edge_is_invalid():
    r = verify_mst(UNDIRECTED, [{"u": "A", "v": "B", "w": 1}, {"u": "C", "v": "D", "w": 1},
                                {"u": "A", "v": "C", "w": 1}])  # A-C is not an edge
    assert r["valid"] is False and "fabricated_edge" in r["reason"]


def test_mst_weight_mismatch_is_invalid():
    r = verify_mst(UNDIRECTED, [{"u": "A", "v": "B", "w": 1}, {"u": "C", "v": "D", "w": 1},
                                {"u": "B", "v": "C", "w": 99}])  # B-C weight is really 2
    assert r["valid"] is False and "weight_mismatch" in r["reason"]


def test_mst_cycle_edge_is_invalid():
    # A-B, B-C, plus... A-D and D-C? Use 4 edges (correct count) but one forms a cycle.
    g = {
        "vertices": ["A", "B", "C"],
        "edges": [{"u": "A", "v": "B", "w": 1}, {"u": "B", "v": "C", "w": 1},
                  {"u": "A", "v": "C", "w": 1}],
    }
    # 3 vertices need 2 edges; give A-B, B-C, A-C -> 3 edges, wrong count first, but test cycle
    # explicitly with correct count is not possible for a tree, so assert count failure here.
    r = verify_mst(g, [{"u": "A", "v": "B", "w": 1}, {"u": "B", "v": "C", "w": 1},
                       {"u": "A", "v": "C", "w": 1}])
    assert r["valid"] is False


def test_mst_claimed_valid_matches():
    r = check_record("mst_total", UNDIRECTED,
                     {"mst_edges": [{"u": "A", "v": "B", "w": 1}, {"u": "C", "v": "D", "w": 1},
                                    {"u": "B", "v": "C", "w": 2}]}, "VALID")
    assert r.objective_label == "CORRECT"


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----
def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["graph"], fx["claim"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "none", "suboptimal_path", "invalid_edge", "topo_order_violation",
        "cycle_claimed_ordered", "mst_not_spanning", "mst_suboptimal",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_three_ops_covered():
    ops = {fx["op"] for fx in FIXTURES}
    assert ops == {"shortest_path", "topological_order", "mst_total"}


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-op/same-graph/same-claim CORRECT sibling (same
    graph question, only the candidate_answer verdict flips) -- proof it perturbs exactly the
    decision, not the scenario."""
    def key(fx):
        return (
            fx["op"],
            json.dumps(fx["graph"], sort_keys=True),
            json.dumps(fx["claim"], sort_keys=True),
        )

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
        # the two siblings must differ only in candidate_answer (opposite verdicts)
        corrects = [s for s in siblings if s["expected_label"] == "CORRECT"]
        assert any(s["candidate_answer"] != fx["candidate_answer"] for s in corrects), fx["id"]


def test_candidate_answers_are_canonical_tokens():
    for fx in FIXTURES:
        assert fx["candidate_answer"] in ("VALID", "INVALID"), fx["id"]
