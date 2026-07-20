"""Frozen tests for the objective contract-clause-presence detector (Stage-2 style lane).

LABEL AUTHORITY: an anchored, stdlib-only clause detector (detector_clause.detect/check_record),
never a model/judge. These tests pin the detector on hand-picked cases (independent of the fixture
file) -- including the decoy_mention trap and every clause type -- then sweep every fixture asserting
the detector agrees with its declared expected_label (and abstains on the paraphrase-only set), plus
structural invariants (count, uniqueness, balance, taxonomy coverage, mutation integrity) and that
the abstain set is quarantined by the runner.

Written before the detector per SDD-then-TDD: this file states the contract.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_contract_clause_presence.detector_clause import (  # noqa: E402
    CLAUSE_CATALOG,
    check_record,
    detect,
    heading_hit,
    phrase_hit,
)
from evals.objective_contract_clause_presence.fixtures_clause import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the fixture file --------------------------------------

def test_heading_anchor_present():
    t = "12. Governing Law\nThe parties will act in good faith.\n"
    assert detect(t, "governing_law")[0] == "PRESENT"
    assert check_record(t, "governing_law", "PRESENT").objective_label == "CORRECT"
    assert check_record(t, "governing_law", "ABSENT").objective_label == "INCORRECT"


def test_phrase_anchor_without_heading():
    t = ("Miscellaneous\nIn no event shall either party be liable for indirect damages.\n")
    assert heading_hit(t, "limitation_of_liability") is None
    assert phrase_hit(t, "limitation_of_liability") is not None
    assert detect(t, "limitation_of_liability")[0] == "PRESENT"


def test_decoy_mention_word_only_is_absent():
    # The clause NAME appears incidentally but there is no anchor -> must be ABSENT, not PRESENT.
    decoy = "We kept a confidential, friendly tone with every confidential client.\n"
    assert detect(decoy, "confidentiality")[0] == "ABSENT"
    assert check_record(decoy, "confidentiality", "PRESENT").objective_label == "INCORRECT"
    assert check_record(decoy, "confidentiality", "ABSENT").objective_label == "CORRECT"


def test_decoy_indemnification_noun_is_absent():
    decoy = "The seminar covered indemnification as an insurance concept for context only.\n"
    assert detect(decoy, "indemnification")[0] == "ABSENT"


def test_genuinely_absent_is_absent():
    t = "Fees\nInvoices are due in 30 days.\n"
    assert detect(t, "termination")[0] == "ABSENT"
    assert detect(t, "indemnification")[0] == "ABSENT"


def test_every_clause_type_detected_via_heading():
    headings = {
        "governing_law": "Governing Law",
        "limitation_of_liability": "Limitation of Liability",
        "confidentiality": "Confidentiality",
        "termination": "Termination",
        "indemnification": "Indemnification",
    }
    for clause_type, heading in headings.items():
        assert clause_type in CLAUSE_CATALOG
        text = f"3. {heading}\nBody text for the {clause_type} clause goes here.\n"
        assert detect(text, clause_type)[0] == "PRESENT", clause_type


def test_heading_numbering_normalization_variants():
    assert heading_hit("ARTICLE IV - INDEMNIFICATION\n", "indemnification") is not None
    assert heading_hit("Section 3.2 Confidentiality:\n", "confidentiality") is not None
    assert heading_hit("(b) Termination\n", "termination") is not None
    assert heading_hit("7) Governing Law\n", "governing_law") is not None
    # a bare heading word must survive numbering strip intact
    assert heading_hit("Termination\n", "termination") is not None


def test_abstain_on_paraphrase_only():
    para = ("Each side promises never to reveal to outside parties any of the private business "
            "information it may learn about the other.\n")
    assert detect(para, "confidentiality")[0] == "ABSTAIN"
    r = check_record(para, "confidentiality", "PRESENT")
    assert r.abstain is True
    assert r.objective_label == "ABSTAIN"


def test_unknown_clause_type_raises():
    import pytest
    with pytest.raises(ValueError):
        detect("anything", "force_majeure")


def test_bad_candidate_answer_raises():
    import pytest
    with pytest.raises(ValueError):
        check_record("1. Termination\nx\n", "termination", "MAYBE")


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_detector_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["contract_text"], fx["clause_type"], fx["candidate_answer"])
        if fx["expected_label"] == "ABSTAIN":
            if not r.abstain:
                mismatches.append((fx["id"], "ABSTAIN", r.objective_label))
        elif r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"detector/fixture disagreement: {mismatches}"


# --- structural invariants -------------------------------------------------------------------

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
    required = {"heading_present", "phrase_anchor_present", "absent", "decoy_mention"}
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present
    assert "none" in present            # clean baselines
    assert "paraphrase_only" in present  # documented abstain set


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return (fx["clause_type"], fx["contract_text"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        # an INCORRECT record must share its identical (clause_type, text) with a CORRECT sibling,
        # differing ONLY in candidate_answer.
        corrects = [s for s in siblings if s["expected_label"] == "CORRECT"]
        assert corrects, fx["id"]
        for c in corrects:
            assert c["candidate_answer"] != fx["candidate_answer"], fx["id"]


# --- abstain set is quarantined by the runner ------------------------------------------------

def test_abstain_set_is_quarantined_not_promoted():
    from evals.objective_contract_clause_presence import run_clause

    manifest = run_clause.run()
    here = Path(run_clause.__file__).parent

    # the paraphrase-only fixtures must be quarantined with reason "paraphrase_only"
    assert manifest["quarantine_reasons"].get("paraphrase_only") == 2
    assert "ABSTAIN" not in manifest["label_dist"]  # abstain never becomes a trainable label

    quarantined = [json.loads(l) for l in
                   (here / "quarantine.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    abstain_ids = {q["id"] for q in quarantined if q["reason"] == "paraphrase_only"}
    fixture_abstain_ids = {fx["id"] for fx in FIXTURES if fx["expected_label"] == "ABSTAIN"}
    assert abstain_ids == fixture_abstain_ids

    # none of the abstain fixtures leaked into hard_gold
    hard_gold = [json.loads(l) for l in
                 (here / "hard_gold.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    hg_ids = {r["id"] for r in hard_gold}
    assert not (hg_ids & fixture_abstain_ids)
    assert all(r["objective_label"] in ("CORRECT", "INCORRECT") for r in hard_gold)


def test_hard_gold_records_carry_objective_label_and_authority():
    from evals.objective_contract_clause_presence import run_clause

    run_clause.run()
    here = Path(run_clause.__file__).parent
    hard_gold = [json.loads(l) for l in
                 (here / "hard_gold.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert hard_gold
    for r in hard_gold:
        assert r["objective_label"] in ("CORRECT", "INCORRECT")
        assert r["label_authority"] == "clause_anchor_detector"
