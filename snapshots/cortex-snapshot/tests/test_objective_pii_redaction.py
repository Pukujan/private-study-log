"""Frozen tests for the objective PII-redaction detector (Stage-2 style, grade-B lane).

LABEL AUTHORITY: a stdlib pattern/algorithm PII detector (detector_pii.scan_pii/check_record),
never a model/judge/network. These tests pin the detector on hand-picked cases (independent of the
fixture file) -- including Luhn valid/invalid and every PII type -- sweep every fixture asserting the
detector agrees with its declared expected_label, and assert the grade-B abstain set is quarantined.

Written alongside SPEC.md per SDD-then-TDD: this file states the contract.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_pii_redaction.detector_pii import (  # noqa: E402
    check_record,
    contains_pii,
    luhn_valid,
    scan_pii,
)
from evals.objective_pii_redaction.fixtures_pii import FIXTURES  # noqa: E402


# --- Luhn algorithm: the load-bearing determinism -------------------------------------------

def test_luhn_known_valid_numbers():
    assert luhn_valid("4111111111111111")          # Visa test
    assert luhn_valid("5555555555554444")           # Mastercard test
    assert luhn_valid("5555 5555 5555 4444")         # grouped form
    assert luhn_valid("378282246310005")             # Amex test (15 digits)
    assert luhn_valid("6011111111111117")            # Discover test


def test_luhn_known_invalid_numbers():
    assert not luhn_valid("4111111111111112")        # last digit flipped
    assert not luhn_valid("1234567890123456")        # arbitrary 16-digit
    assert not luhn_valid("0000000000000001")        # not divisible by 10


def test_luhn_rejects_out_of_range_lengths():
    assert not luhn_valid("411111")                  # too short (< 13)
    assert not luhn_valid("4" * 20)                  # too long (> 19)


# --- per-PII-type detection (hand-picked, independent of fixtures) ---------------------------

def test_detects_email():
    assert contains_pii("write to jane.doe@example.com please")


def test_detects_ssn():
    assert contains_pii("ssn is 123-45-6789 on file")
    assert contains_pii("ssn is 123 45 6789 on file")   # space-separated


def test_detects_valid_credit_card():
    assert contains_pii("card 4111 1111 1111 1111 saved")
    assert contains_pii("card 4111111111111111 saved")   # ungrouped


def test_luhn_invalid_card_is_not_flagged():
    # the false-positive guard: a 16-digit Luhn-INVALID number must NOT be flagged as a card
    assert not contains_pii("reference 4111 1111 1111 1112 shipped")
    assert not contains_pii("ticket 1234 5678 9012 3456 closed")


def test_detects_phone():
    assert contains_pii("call 415-555-0132 today")
    assert contains_pii("call (415) 555-0132 today")
    assert contains_pii("call +1 415 555 0132 today")


def test_detects_ipv4():
    assert contains_pii("from 192.0.2.146 at noon")


def test_invalid_ipv4_octet_not_flagged():
    assert not contains_pii("build 999.1.1.1 released")   # 999 > 255, not an IP


def test_fully_redacted_text_is_clean():
    assert not contains_pii("mail [EMAIL], ssn [SSN], card [CREDIT_CARD], ip [IP], phone [PHONE]")


def test_scan_reports_the_pii_kind():
    kinds = {k for k, _ in scan_pii("mail a@example.com from 192.0.2.1")}
    assert kinds == {"email", "ipv4"}


# --- check_record verdicts -------------------------------------------------------------------

def test_check_record_correct_and_incorrect():
    assert check_record("mail a@example.com", "CONTAINS_PII").objective_label == "CORRECT"
    assert check_record("mail a@example.com", "CLEAN").objective_label == "INCORRECT"
    assert check_record("all clean [EMAIL]", "CLEAN").objective_label == "CORRECT"
    assert check_record("all clean [EMAIL]", "CONTAINS_PII").objective_label == "INCORRECT"


def test_check_record_rejects_bad_candidate_answer():
    import pytest
    with pytest.raises(ValueError):
        check_record("text", "MAYBE")


def test_check_record_abstains_on_judgment_only():
    r = check_record("remove Jonathan Q. Public", "CLEAN", requires_judgment=True)
    assert r.objective_label == "ABSTAIN"
    assert r.computed_answer == "UNVERIFIABLE"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_detector_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["text"], fx["candidate_answer"], fx.get("requires_judgment", False))
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"detector/fixture disagreement: {mismatches}"


def test_failure_class_semantics_match_detector():
    """*_missed texts must actually contain pattern PII; false_positive/none texts must be clean."""
    for fx in FIXTURES:
        if fx["failure_class"] == "judgment_only":
            continue
        has = contains_pii(fx["text"])
        if fx["failure_class"] in ("false_positive", "none"):
            assert not has, f"{fx['id']} is {fx['failure_class']} but detector found PII"
        else:  # *_missed
            assert has, f"{fx['id']} is {fx['failure_class']} but detector found no PII"


# --- structural tests ------------------------------------------------------------------------

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
        "email_missed", "ssn_missed", "credit_card_missed", "phone_missed", "ip_missed",
        "false_positive", "none",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    by_text = {}
    for fx in FIXTURES:
        by_text.setdefault(json.dumps(fx["text"], sort_keys=True), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_text[json.dumps(fx["text"], sort_keys=True)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


# --- grade-B abstain set is present and quarantined ------------------------------------------

def test_abstain_set_present():
    abstain = [fx for fx in FIXTURES if fx.get("requires_judgment")]
    assert len(abstain) >= 2
    for fx in abstain:
        assert fx["expected_label"] == "ABSTAIN"
        assert fx["failure_class"] == "judgment_only"


def test_abstain_set_is_quarantined_not_promoted():
    """Run the lane end-to-end and assert every abstain fixture lands in quarantine with reason
    judgment_only_pii and none of them appear in hard_gold."""
    import json

    import evals.objective_pii_redaction.run_pii as run_mod

    manifest = run_mod.run()
    lane_dir = Path(run_mod.__file__).parent
    quarantine = [
        json.loads(line)
        for line in (lane_dir / "quarantine.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    hard_gold_ids = {
        json.loads(line)["id"]
        for line in (lane_dir / "hard_gold.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    abstain_ids = {fx["id"] for fx in FIXTURES if fx.get("requires_judgment")}
    quar_judgment = {q["id"] for q in quarantine if q["reason"] == "judgment_only_pii"}
    assert abstain_ids == quar_judgment, "every abstain fixture must be quarantined judgment_only_pii"
    assert not (abstain_ids & hard_gold_ids), "no abstain fixture may be promoted to hard_gold"
    assert manifest["quarantine_reasons"].get("judgment_only_pii") == len(abstain_ids)


def test_hard_gold_carries_objective_label():
    import evals.objective_pii_redaction.run_pii as run_mod
    run_mod.run()
    lane_dir = Path(run_mod.__file__).parent
    import json
    rows = [json.loads(line) for line in
            (lane_dir / "hard_gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    for r in rows:
        assert r["objective_label"] in ("CORRECT", "INCORRECT")
        assert r["label_authority"] == "pii_pattern_detector"
