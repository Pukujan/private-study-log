"""Frozen tests for the objective exact-key deduplication checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only keep-first dedup on normalized key tuples (checker_dedup.dedup /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of the
runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy coverage,
mode coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_deduplication_exact_key.checker_dedup import (  # noqa: E402
    check_record,
    dedup,
    normalize_key,
)
from evals.objective_deduplication_exact_key.run_dedup import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_no_duplicates_all_survive():
    recs = [{"id": "a", "e": "1"}, {"id": "b", "e": "2"}, {"id": "c", "e": "3"}]
    r = dedup(recs, ["e"], "exact")
    assert r.surviving_ids == ["a", "b", "c"]
    assert r.removed_ids == []


def test_exact_duplicate_keep_first():
    recs = [{"id": "a", "e": "x"}, {"id": "b", "e": "x"}, {"id": "c", "e": "y"}]
    r = dedup(recs, ["e"], "exact")
    assert r.surviving_ids == ["a", "c"]
    assert r.removed_ids == ["b"]
    good = {"surviving_ids": ["a", "c"], "removed_ids": ["b"]}
    assert check_record(recs, ["e"], "exact", good).objective_label == "CORRECT"
    keptlast = {"surviving_ids": ["b", "c"], "removed_ids": ["a"]}
    assert check_record(recs, ["e"], "exact", keptlast).objective_label == "INCORRECT"


def test_keep_first_not_last():
    recs = [{"id": "a", "e": "x"}, {"id": "b", "e": "x"}]
    r = dedup(recs, ["e"], "exact")
    assert r.surviving_ids == ["a"]
    assert r.removed_ids == ["b"]


def test_composite_key_second_field_matters():
    recs = [{"id": "a", "f": "J", "l": "S"}, {"id": "b", "f": "J", "l": "D"},
            {"id": "c", "f": "J", "l": "S"}]
    r = dedup(recs, ["f", "l"], "exact")
    assert r.surviving_ids == ["a", "b"]   # b differs on last, not a dup
    assert r.removed_ids == ["c"]          # c dups a on (J, S)
    # deduping on 'first' alone would wrongly drop b -> INCORRECT under the composite key
    wrong = {"surviving_ids": ["a"], "removed_ids": ["b", "c"]}
    assert check_record(recs, ["f", "l"], "exact", wrong).objective_label == "INCORRECT"


def test_casefold_mode_merges_case_variants():
    recs = [{"id": "a", "k": "A"}, {"id": "b", "k": "a"}]
    assert dedup(recs, ["k"], "casefold").removed_ids == ["b"]


def test_exact_mode_keeps_case_variants_separate():
    recs = [{"id": "a", "k": "A"}, {"id": "b", "k": "a"}]
    r = dedup(recs, ["k"], "exact")
    assert r.surviving_ids == ["a", "b"]
    assert r.removed_ids == []


def test_trim_mode_merges_whitespace_variants():
    recs = [{"id": "a", "t": "  hot  "}, {"id": "b", "t": "hot"}, {"id": "c", "t": "cold"}]
    r = dedup(recs, ["t"], "trim")
    assert r.surviving_ids == ["a", "c"]
    assert r.removed_ids == ["b"]
    # exact mode would keep the whitespace variants separate
    assert dedup(recs, ["t"], "exact").removed_ids == []


def test_non_key_field_difference_is_still_a_duplicate():
    recs = [{"id": "a", "k": "1", "note": "hello"}, {"id": "b", "k": "1", "note": "world"}]
    r = dedup(recs, ["k"], "exact")
    assert r.surviving_ids == ["a"]
    assert r.removed_ids == ["b"]   # differs only on the non-key 'note' field -> still a dup


def test_normalize_key_tuples():
    assert normalize_key({"k": "AB"}, ["k"], "exact") == ("AB",)
    assert normalize_key({"k": "AB"}, ["k"], "casefold") == ("ab",)
    assert normalize_key({"k": "  z  "}, ["k"], "trim") == ("z",)
    assert normalize_key({"f": "J", "l": "S"}, ["f", "l"], "exact") == ("J", "S")


def test_missed_duplicate_candidate_is_incorrect():
    recs = [{"id": "m1", "e": "a"}, {"id": "m2", "e": "a"}, {"id": "m3", "e": "b"}]
    bad = {"surviving_ids": ["m1", "m2", "m3"], "removed_ids": []}
    assert check_record(recs, ["e"], "exact", bad).objective_label == "INCORRECT"


def test_computed_answer_is_the_partition():
    recs = [{"id": "a", "e": "x"}, {"id": "b", "e": "x"}, {"id": "c", "e": "y"}]
    r = check_record(recs, ["e"], "exact", {"surviving_ids": ["a", "c"], "removed_ids": ["b"]})
    assert r.computed_answer == {"surviving_ids": ["a", "c"], "removed_ids": ["b"]}


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["records"], fx["key_fields"], fx["mode"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == dedup(fx["records"], fx["key_fields"], fx["mode"]).asdict(), fx["id"]


# --- structural invariants -------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "none", "missed_duplicate", "over_dedup", "wrong_key_fields",
        "kept_wrong_record", "normalization_error",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_modes_covered():
    required = {"exact", "casefold", "trim"}
    present = {fx["mode"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return (json.dumps(fx["records"], sort_keys=True),
                json.dumps(fx["key_fields"], sort_keys=True),
                fx["mode"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != dedup(fx["records"], fx["key_fields"], fx["mode"]).asdict(), fx["id"]
