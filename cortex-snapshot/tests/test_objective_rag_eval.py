"""Frozen tests for the objective RAG-eval checker (second Class-B oracle, judge-free).

LABEL AUTHORITY: a stdlib-only deterministic grounding match (checker_rag.score_rag / check_record)
against the scenario's planted ground truth -- never a model/judge/threshold. These tests pin the
checker on hand-built cases (independent of the runner's fixture list, exercising each of the four
criteria), sweep every fixture asserting the checker agrees with its declared expected_label, and
assert the lane's structural invariants (count, balance, unique ids, failure-class coverage,
distractor presence, >=2 required elements, mutation-integrity, and ground-truth self-consistency).
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_rag_eval.checker_rag import (  # noqa: E402
    _normalize,
    canonical_answer,
    check_record,
    score_rag,
)
from evals.objective_rag_eval.run_rag import FIXTURES, _SCENARIOS  # noqa: E402


def _scn():
    """A small hand-built scenario, independent of the runner's list."""
    return {
        "question": "What does the SLA guarantee?",
        "corpus": [
            {"doc_id": "g1", "text": "The service level agreement guarantees 99.9 percent uptime per month."},
            {"doc_id": "g2", "text": "Support responds to critical tickets within 2 hours."},
            {"doc_id": "x1", "text": "The marketing site promises a delightful browsing experience."},
            {"doc_id": "x2", "text": "Refunds are processed within 5 business days of a request."},
        ],
        "ground_truth": {
            "gold_doc_ids": ["g1", "g2"],
            "distractor_doc_ids": ["x1", "x2"],
            "required_answer_elements": ["99.9 percent uptime", "within 2 hours"],
            "element_to_doc": {"99.9 percent uptime": "g1", "within 2 hours": "g2"},
        },
    }


# --- hand-picked criterion cases, independent of the runner's fixtures -------------------------

def test_exact_correct_answer_passes():
    scn = _scn()
    r = score_rag(scn, canonical_answer(scn))
    assert r.objective_label == "CORRECT"
    assert r.failed_criteria == []


def test_missed_gold_doc_fails_criterion_1():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn), "retrieved_doc_ids": ["g1"]})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [1]


def test_retrieved_distractor_fails_criterion_1():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn), "retrieved_doc_ids": ["g1", "g2", "x1"]})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [1]


def test_missing_required_element_fails_criterion_2():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn), "answer_elements": ["99.9 percent uptime"],
                        "citations": {"99.9 percent uptime": "g1"}})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [2]


def test_hallucinated_element_fails_criterion_3():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn),
                        "answer_elements": ["99.9 percent uptime", "within 2 hours", "100 percent uptime"]})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [3]


def test_citation_to_distractor_fails_criterion_4():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn),
                        "citations": {"99.9 percent uptime": "x1", "within 2 hours": "g2"}})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [4]


def test_citation_to_wrong_gold_doc_fails_criterion_4():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn),
                        "citations": {"99.9 percent uptime": "g2", "within 2 hours": "g2"}})
    assert r.objective_label == "INCORRECT" and r.failed_criteria == [4]


def test_element_order_irrelevant():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn),
                        "answer_elements": ["within 2 hours", "99.9 percent uptime"]})
    assert r.objective_label == "CORRECT"


def test_normalization_tolerates_case_and_whitespace():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn),
                        "answer_elements": ["  99.9   PERCENT  Uptime ", "within 2 hours"]})
    assert r.objective_label == "CORRECT"


def test_absent_citations_never_fail_criterion_4():
    scn = _scn()
    r = score_rag(scn, {**canonical_answer(scn), "citations": {}})
    assert r.objective_label == "CORRECT" and 4 not in r.failed_criteria


def test_multi_error_accumulates_all_criteria():
    scn = _scn()
    r = score_rag(scn, {"retrieved_doc_ids": ["g1", "x1"],
                        "answer_elements": ["99.9 percent uptime", "fake fact"],
                        "citations": {"99.9 percent uptime": "x2"}})
    assert set(r.failed_criteria) == {1, 2, 3, 4}


def test_computed_answer_is_canonical():
    scn = _scn()
    r = score_rag(scn, canonical_answer(scn))
    assert r.computed_answer == canonical_answer(scn)


def test_check_record_is_same_authority():
    scn = _scn()
    assert check_record(scn, canonical_answer(scn)).objective_label == "CORRECT"
    assert check_record(scn, {**canonical_answer(scn), "retrieved_doc_ids": ["g1"]}).objective_label == "INCORRECT"


# --- full fixture sweep -----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["scenario"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_canonical():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == canonical_answer(fx["scenario"]), fx["id"]


def test_every_incorrect_candidate_actually_fails_a_criterion():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        r = check_record(fx["scenario"], fx["candidate"])
        assert r.failed_criteria, f"{fx['id']} is INCORRECT but no criterion failed"


# --- structural invariants --------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 28, len(FIXTURES)


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8, dist
    assert dist["INCORRECT"] >= 8, dist


def test_all_failure_classes_present():
    required = {
        "none", "missed_gold_doc", "retrieved_distractor",
        "missing_answer_element", "hallucinated_element", "invalid_citation",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_scenario_has_a_distractor_and_two_required_elements():
    for scn in _SCENARIOS:
        gt = scn["ground_truth"]
        assert gt["distractor_doc_ids"], f"{scn['name']} has no distractor"
        assert len(gt["required_answer_elements"]) >= 2, f"{scn['name']} has <2 required elements"


def test_every_incorrect_states_a_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_scn = {}
    for fx in FIXTURES:
        by_scn.setdefault(fx["scenario_name"], []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_scn[fx["scenario_name"]]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_ground_truth_is_self_consistent():
    """Every required element is a normalized substring of its element_to_doc gold doc; gold and
    distractor id sets are disjoint; every element_to_doc target is a gold doc."""
    for scn in _SCENARIOS:
        gt = scn["ground_truth"]
        docs = {d["doc_id"]: d["text"] for d in scn["corpus"]}
        gold = set(gt["gold_doc_ids"])
        distractors = set(gt["distractor_doc_ids"])
        assert gold.isdisjoint(distractors), f"{scn['name']} gold/distractor overlap"
        for el in gt["required_answer_elements"]:
            doc_id = gt["element_to_doc"][el]
            assert doc_id in gold, f"{scn['name']}: element {el!r} maps to non-gold doc {doc_id}"
            assert _normalize(el) in _normalize(docs[doc_id]), \
                f"{scn['name']}: element {el!r} not verbatim in gold doc {doc_id}"
