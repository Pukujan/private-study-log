"""Frozen tests for the objective dependency-CVE-matching checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only semver range-membership check (checker_cve.satisfies /
find_vulnerabilities / check_record), never a model/judge. These tests pin the checker on
hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants
(balance, unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_dependency_cve_matching.checker_cve import (  # noqa: E402
    check_record,
    find_vulnerabilities,
    parse_semver,
    satisfies,
)
from evals.objective_dependency_cve_matching.run_cve import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_parse_semver_pads_and_strips():
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("1.2") == (1, 2, 0)
    assert parse_semver("4") == (4, 0, 0)
    assert parse_semver("1.2.0-beta.1") == (1, 2, 0)
    assert parse_semver("1.2.0+build") == (1, 2, 0)


def test_version_in_range_flagged_and_outside_not():
    assert satisfies("1.2.0", "<1.4.2") is True
    assert satisfies("1.4.2", "<1.4.2") is False
    assert satisfies("1.5.0", "<1.4.2") is False


def test_boundary_inclusive_lower():
    assert satisfies("1.0.0", ">=1.0.0") is True
    assert satisfies("0.9.9", ">=1.0.0") is False


def test_boundary_exclusive_upper():
    assert satisfies("2.0.0", "<2.0.0") is False
    assert satisfies("1.9.9", "<2.0.0") is True


def test_boundary_inclusive_upper():
    assert satisfies("2.2.2", "<=2.2.2") is True
    assert satisfies("2.2.3", "<=2.2.2") is False


def test_multi_comparator_all_must_hold():
    assert satisfies("1.2.0", ">=1.0.0 <1.4.0") is True
    assert satisfies("1.5.0", ">=1.0.0 <1.4.0") is False    # fails the upper bound only
    assert satisfies("0.9.0", ">=1.0.0 <1.4.0") is False    # fails the lower bound only


def test_any_of_affected_ranges():
    lock = [{"package": "pkga", "version": "1.5.0"}]
    db = [{"cve_id": "CVE-A", "package": "pkga", "affected_ranges": ["<1.0.0", "<2.0.0"]}]
    assert find_vulnerabilities(lock, db) == {("pkga", "1.5.0", "CVE-A")}


def test_multiple_cves_matched():
    lock = [{"package": "libx", "version": "1.0.0"}, {"package": "liby", "version": "2.3.1"}]
    db = [
        {"cve_id": "CVE-1", "package": "libx", "affected_ranges": ["<2.0.0"]},
        {"cve_id": "CVE-2", "package": "liby", "affected_ranges": [">=2.0.0 <3.0.0"]},
    ]
    assert find_vulnerabilities(lock, db) == {("libx", "1.0.0", "CVE-1"), ("liby", "2.3.1", "CVE-2")}


def test_wrong_package_not_matched():
    lock = [{"package": "alpha", "version": "1.2.0"}, {"package": "beta", "version": "1.2.0"}]
    db = [{"cve_id": "CVE-9", "package": "alpha", "affected_ranges": ["<2.0.0"]}]
    # only alpha matches; beta at the same version must not be flagged
    assert find_vulnerabilities(lock, db) == {("alpha", "1.2.0", "CVE-9")}


def test_clean_lockfile_no_vulnerabilities():
    lock = [{"package": "safe", "version": "3.0.0"}]
    db = [{"cve_id": "CVE-B", "package": "safe", "affected_ranges": ["<2.0.0"]}]
    assert find_vulnerabilities(lock, db) == set()
    assert check_record(lock, db, []).objective_label == "CORRECT"
    assert check_record(lock, db, []).computed_answer == []


def test_check_record_correct_and_wrong():
    lock = [{"package": "libx", "version": "1.0.0"}, {"package": "liby", "version": "2.3.1"}]
    db = [
        {"cve_id": "CVE-1", "package": "libx", "affected_ranges": ["<2.0.0"]},
        {"cve_id": "CVE-2", "package": "liby", "affected_ranges": [">=2.0.0 <3.0.0"]},
    ]
    correct = [{"package": "libx", "version": "1.0.0", "cve_id": "CVE-1"},
               {"package": "liby", "version": "2.3.1", "cve_id": "CVE-2"}]
    assert check_record(lock, db, correct).objective_label == "CORRECT"
    # candidate order does not matter
    assert check_record(lock, db, list(reversed(correct))).objective_label == "CORRECT"
    # drop a real match (missed vuln)
    assert check_record(lock, db, correct[:1]).objective_label == "INCORRECT"
    # add a false positive
    fp = correct + [{"package": "liby", "version": "2.3.1", "cve_id": "CVE-1"}]
    assert check_record(lock, db, fp).objective_label == "INCORRECT"


def test_computed_answer_is_sorted_list_of_dicts():
    lock = [{"package": "liby", "version": "2.3.1"}, {"package": "libx", "version": "1.0.0"}]
    db = [
        {"cve_id": "CVE-2", "package": "liby", "affected_ranges": [">=2.0.0 <3.0.0"]},
        {"cve_id": "CVE-1", "package": "libx", "affected_ranges": ["<2.0.0"]},
    ]
    ans = check_record(lock, db, []).computed_answer
    assert ans == [
        {"package": "libx", "version": "1.0.0", "cve_id": "CVE-1"},
        {"package": "liby", "version": "2.3.1", "cve_id": "CVE-2"},
    ]


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["lockfile"], fx["cve_db"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        computed = check_record(fx["lockfile"], fx["cve_db"], []).computed_answer
        # the CORRECT candidate is exactly the computed sorted vulnerable list
        assert fx["candidate"] == computed, fx["id"]


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
        "none", "missed_vuln", "false_positive", "boundary_version",
        "wrong_package_match", "multi_comparator_error",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def _scenario_key(fx):
    return (json.dumps(fx["lockfile"], sort_keys=True), json.dumps(fx["cve_db"], sort_keys=True))


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(_scenario_key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[_scenario_key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        computed = check_record(fx["lockfile"], fx["cve_db"], []).computed_answer
        assert fx["candidate"] != computed, fx["id"]
