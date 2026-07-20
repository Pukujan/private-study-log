"""Frozen tests for the objective URL-canonicalization checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic RFC 3986 syntax normalization via the stdlib `urllib.parse`
(checker_url.canonicalize / same_resource / check_record), never a model/judge. These tests pin
the checker on hand-picked cases (independent of the fixture file) plus a full sweep over every
fixture in fixtures_url.py, asserting the checker's objective_label always matches the fixture's
declared expected_label, plus structural invariants.

Written before checker_url.py was trusted: this file defines the contract.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from evals.objective_url_canonicalization.checker_url import (  # noqa: E402
    canonicalize,
    check_record,
    same_resource,
)
from evals.objective_url_canonicalization.fixtures_url import FIXTURES  # noqa: E402


# --- hand-picked oracle cases, independent of the fixture file -------------------------------

def test_scheme_lowercased():
    assert canonicalize("HTTP://example.com/") == "http://example.com/"


def test_host_lowercased():
    assert canonicalize("http://Example.COM/") == "http://example.com/"


def test_default_port_removed():
    assert canonicalize("http://example.com:80/x") == "http://example.com/x"
    assert canonicalize("https://example.com:443/") == "https://example.com/"


def test_nondefault_port_kept():
    assert canonicalize("http://example.com:8080/x") == "http://example.com:8080/x"


def test_https_default_port_not_removed_from_http():
    # :443 is NOT the default for http, so it must be kept
    assert canonicalize("http://example.com:443/") == "http://example.com:443/"


def test_empty_path_normalized_to_slash():
    assert canonicalize("http://example.com") == "http://example.com/"


def test_dot_segments_removed():
    assert canonicalize("http://example.com/a/./b/../c") == "http://example.com/a/c"
    assert canonicalize("http://example.com/../x") == "http://example.com/x"


def test_percent_hex_uppercased_reserved_kept():
    assert canonicalize("http://example.com/a%2fb") == "http://example.com/a%2Fb"


def test_percent_unreserved_decoded():
    assert canonicalize("http://example.com/%41") == "http://example.com/A"
    assert canonicalize("http://example.com/%7euser") == "http://example.com/~user"


def test_query_preserved_verbatim():
    assert canonicalize("https://example.com/a/b?q=1") == "https://example.com/a/b?q=1"


def test_userinfo_preserved():
    assert canonicalize("http://User@Example.com:80/") == "http://User@example.com/"


def test_same_resource_equivalences():
    assert same_resource("http://Example.com:80/p", "http://example.com/p") == "SAME"
    assert same_resource("http://example.com/a/../b", "http://example.com/b") == "SAME"


def test_same_resource_scheme_matters():
    assert same_resource("http://example.com/p", "https://example.com/p") == "DIFFERENT"


def test_check_record_correct_and_incorrect_labels():
    assert check_record("canonicalize", {"url": "HTTP://example.com/"},
                        "http://example.com/").objective_label == "CORRECT"
    assert check_record("canonicalize", {"url": "HTTP://example.com/"},
                        "HTTP://example.com/").objective_label == "INCORRECT"


def test_check_record_rejects_unknown_op():
    with pytest.raises(ValueError):
        check_record("nonsense_op", {}, "x")


def test_self_test_passes():
    from evals.objective_url_canonicalization import checker_url
    checker_url.self_test()


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["args"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 30


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {"scheme_case", "host_case", "default_port", "dot_segments",
                "percent_encoding", "empty_path", "same_resource", "none"}
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_both_ops_present():
    ops = {fx["op"] for fx in FIXTURES}
    assert ops == {"canonicalize", "same_resource"}


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} INCORRECT but no mutation"


def test_every_correct_fixture_has_empty_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == "", f"{fx['id']} CORRECT but carries a mutation"


def test_mutation_integrity_incorrect_shares_scenario_with_correct_sibling():
    def key(fx):
        return (fx["op"], json.dumps(fx["args"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]
        correct = next(s for s in siblings if s["expected_label"] == "CORRECT")
        assert correct["candidate_answer"] != fx["candidate_answer"], fx["id"]
