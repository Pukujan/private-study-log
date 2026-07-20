"""Frozen tests for the objective semver-constraint checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic semantic-version parsing + npm-style range resolution
(checker_semver.satisfies / max_satisfying / check_record), never a model/judge. These tests pin
the checker's behavior on hand-picked cases (independent of the fixture file) plus a full sweep
over every fixture in fixtures_semver.py, asserting the checker's objective_label always matches
the fixture's declared expected_label (the same self-validation gate every other Stage-2 lane
uses). Where `packaging` is importable, the release-only comparison core is cross-checked against
it.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_semver_constraint_resolution.checker_semver import (  # noqa: E402
    check_record,
    compare,
    crosscheck_compare,
    max_satisfying,
    parse_version,
    satisfies,
)
from evals.objective_semver_constraint_resolution.fixtures_semver import FIXTURES  # noqa: E402


# --- version precedence (hand-picked, independent of the fixture file) ------------------------

def test_prerelease_ranks_below_release():
    assert compare(parse_version("1.0.0-alpha"), parse_version("1.0.0")) < 0


def test_prerelease_longer_set_wins_when_prefix_equal():
    assert compare(parse_version("1.0.0-alpha"), parse_version("1.0.0-alpha.1")) < 0


def test_prerelease_numeric_below_alphanumeric():
    assert compare(parse_version("1.0.0-alpha.1"), parse_version("1.0.0-alpha.beta")) < 0


def test_prerelease_full_ordering_chain():
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
             "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    for lo, hi in zip(chain, chain[1:]):
        assert compare(parse_version(lo), parse_version(hi)) < 0, f"{lo} !< {hi}"


def test_build_metadata_ignored_for_precedence():
    assert compare(parse_version("1.2.3+build.9"), parse_version("1.2.3")) == 0
    assert satisfies("1.2.3+build.9", "1.2.3") is True


# --- caret boundary --------------------------------------------------------------------------

def test_caret_upper_bound_excluded():
    assert satisfies("2.0.0", "^1.2.3") is False


def test_caret_within_range():
    assert satisfies("1.9.9", "^1.2.3") is True
    assert satisfies("1.2.3", "^1.2.3") is True


def test_caret_zero_major_locks_minor():
    assert satisfies("0.2.9", "^0.2.3") is True
    assert satisfies("0.3.0", "^0.2.3") is False


def test_caret_zero_zero_locks_patch():
    assert satisfies("0.0.3", "^0.0.3") is True
    assert satisfies("0.0.4", "^0.0.3") is False


# --- tilde boundary --------------------------------------------------------------------------

def test_tilde_allows_patch_not_minor():
    assert satisfies("1.2.9", "~1.2.3") is True
    assert satisfies("1.3.0", "~1.2.3") is False


def test_tilde_major_only_allows_minor():
    assert satisfies("1.9.0", "~1") is True
    assert satisfies("2.0.0", "~1") is False


# --- x-ranges --------------------------------------------------------------------------------

def test_xrange_patch_wildcard():
    assert satisfies("1.2.9", "1.2.x") is True
    assert satisfies("1.3.0", "1.2.x") is False


def test_xrange_minor_wildcard():
    assert satisfies("1.9.9", "1.x") is True
    assert satisfies("2.0.0", "1.x") is False


def test_star_matches_any_release():
    assert satisfies("99.99.99", "*") is True


# --- comparators, unions, hyphen -------------------------------------------------------------

def test_and_comparator_set():
    assert satisfies("1.5.0", ">=1.0.0 <2.0.0") is True
    assert satisfies("2.0.0", ">=1.0.0 <2.0.0") is False


def test_or_union():
    assert satisfies("2.5.0", "^1.0.0 || ^2.0.0") is True
    assert satisfies("3.0.0", "^1.0.0 || ^2.0.0") is False


def test_hyphen_range_inclusive_bounds():
    assert satisfies("1.0.0", "1.0.0 - 2.4.9") is True
    assert satisfies("2.4.9", "1.0.0 - 2.4.9") is True
    assert satisfies("2.5.0", "1.0.0 - 2.4.9") is False


def test_partial_comparator_gt():
    # >1.2 means >=1.3.0 (after all of 1.2.x)
    assert satisfies("1.3.0", ">1.2") is True
    assert satisfies("1.2.9", ">1.2") is False


# --- prerelease exclusion + ordering in ranges -----------------------------------------------

def test_prerelease_excluded_by_default():
    assert satisfies("1.2.3-beta", ">=1.0.0") is False


def test_prerelease_allowed_only_with_matching_tuple_comparator():
    assert satisfies("1.2.3-beta.2", ">=1.2.3-beta.1") is True
    assert satisfies("1.2.4-beta.2", ">=1.2.3-beta.1") is False   # different tuple -> excluded


def test_prerelease_ordering_in_range():
    assert satisfies("1.0.0-alpha", ">1.0.0-alpha.1") is False
    assert satisfies("1.0.0-alpha.2", ">1.0.0-alpha.1") is True


def test_release_satisfies_prerelease_lower_bound():
    assert satisfies("1.0.0", "^1.0.0-alpha") is True


# --- max_satisfying --------------------------------------------------------------------------

def test_max_satisfying_picks_highest():
    assert max_satisfying(["1.1.0", "1.2.0", "1.2.5", "2.0.0"], "^1.2.0") == "1.2.5"


def test_max_satisfying_skips_prerelease():
    assert max_satisfying(["1.0.0", "1.4.2", "1.5.0-beta", "2.0.0"], "^1.0.0") == "1.4.2"


def test_max_satisfying_none_when_no_match():
    assert max_satisfying(["0.1.0", "0.2.0"], "^1.0.0") is None


def test_max_satisfying_returns_original_string():
    # returns the exact string from the input list
    assert max_satisfying(["1.2.0", "1.2.3+build"], "^1.2.0") == "1.2.3+build"


# --- record-level verdict --------------------------------------------------------------------

def test_check_record_satisfies_correct_and_incorrect():
    assert check_record("satisfies", "2.0.0", "^1.2.3", None, "UNSATISFIED").objective_label == "CORRECT"
    assert check_record("satisfies", "2.0.0", "^1.2.3", None, "SATISFIES").objective_label == "INCORRECT"


def test_check_record_max_satisfying_correct_and_incorrect():
    vs = ["1.1.0", "1.2.0", "1.2.5", "2.0.0"]
    assert check_record("max_satisfying", None, "^1.2.0", vs, "1.2.5").objective_label == "CORRECT"
    assert check_record("max_satisfying", None, "^1.2.0", vs, "2.0.0").objective_label == "INCORRECT"


def test_check_record_rejects_bad_op():
    import pytest
    with pytest.raises(ValueError):
        check_record("bogus", "1.0.0", "*", None, "SATISFIES")


def test_check_record_rejects_bad_satisfies_token():
    import pytest
    with pytest.raises(ValueError):
        check_record("satisfies", "1.0.0", "*", None, "YES")


# --- optional cross-check against packaging (release-only) -----------------------------------

def test_crosscheck_agrees_on_release_versions_when_available():
    pairs = [("1.2.3", "1.2.4"), ("1.2.3", "1.2.3"), ("2.0.0", "1.9.9"), ("1.0.0", "1.0.1")]
    for a, b in pairs:
        cc = crosscheck_compare(a, b)
        if cc is None:
            continue  # packaging absent -> skip, it is defense-in-depth only
        assert cc == compare(parse_version(a), parse_version(b)), (a, b)


def test_crosscheck_abstains_on_prerelease():
    # prerelease/build versions are outside the narrow cross-check window -> always None
    assert crosscheck_compare("1.0.0-alpha", "1.0.0") is None
    assert crosscheck_compare("1.2.3+build", "1.2.3") is None


# --- full fixture sweep: checker must agree with every fixture's declared expected_label ------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx.get("version"), fx.get("range"), fx.get("versions"),
                         fx["candidate_answer"])
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
    required = {"caret_boundary", "tilde_boundary", "prerelease_ordering",
                "x_range", "max_satisfying_wrong_pick", "none"}
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-op/same-version/same-versions/same-range CORRECT
    sibling (identical question, only candidate_answer differs) -- proof the checker is graded on
    the DECISION, not the setup."""
    import json

    def key(fx):
        return (fx["op"], json.dumps(fx.get("version"), sort_keys=True),
                json.dumps(fx.get("versions"), sort_keys=True),
                json.dumps(fx.get("range"), sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
