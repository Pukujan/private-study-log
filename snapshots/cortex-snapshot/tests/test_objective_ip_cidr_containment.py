"""Frozen tests for the objective IP/CIDR-containment checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic IP/subnet math via the stdlib `ipaddress` module
(checker_cidr.check_record / address_in_cidr / subnet_overlap / acl_first_match), never a
model/judge. These tests pin the checker's behavior on hand-picked cases (independent of the
fixture file) plus a full sweep over every fixture in fixtures_cidr.py, asserting the checker's
objective_label always matches the fixture's declared expected_label (the same self-validation gate
every other Stage-2 lane uses), plus structural invariants (counts, balance, taxonomy coverage,
mutation-integrity).

Written before checker_cidr.py was trusted (SDD then TDD): this file defines the contract.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_ip_cidr_containment.checker_cidr import (  # noqa: E402
    acl_decision,
    acl_first_match,
    address_in_cidr,
    check_record,
    subnet_overlap,
)
from evals.objective_ip_cidr_containment.fixtures_cidr import FIXTURES  # noqa: E402


# --- hand-picked oracle cases, independent of the fixture file -------------------------------

def test_address_clearly_inside_is_in():
    assert address_in_cidr("10.1.2.3", "10.1.0.0/16") == "IN"


def test_broadcast_address_is_a_member():
    assert address_in_cidr("10.1.255.255", "10.1.0.0/16") == "IN"


def test_network_address_is_a_member():
    assert address_in_cidr("10.1.0.0", "10.1.0.0/16") == "IN"


def test_one_past_top_of_block_is_out():
    assert address_in_cidr("10.2.0.0", "10.1.0.0/16") == "OUT"


def test_wrong_prefix_len_24_excludes():
    assert address_in_cidr("10.1.2.3", "10.1.0.0/24") == "OUT"
    assert address_in_cidr("10.1.2.3", "10.1.0.0/16") == "IN"


def test_prefix_25_second_half_includes():
    assert address_in_cidr("192.168.1.200", "192.168.1.128/25") == "IN"
    # a /26 from .128 would only reach .191, so .200 would be OUT under the wrong mask
    assert address_in_cidr("192.168.1.200", "192.168.1.128/26") == "OUT"


def test_adjacent_blocks_do_not_overlap():
    assert subnet_overlap("10.0.0.0/24", "10.0.1.0/24") == "DISJOINT"


def test_containment_counts_as_overlap():
    assert subnet_overlap("10.0.0.0/16", "10.0.1.0/24") == "OVERLAP"
    assert subnet_overlap("10.0.1.0/24", "10.0.0.0/16") == "OVERLAP"  # symmetric


def test_identical_blocks_overlap():
    assert subnet_overlap("10.0.0.0/24", "10.0.0.0/24") == "OVERLAP"


def test_acl_first_match_allow_beats_later_deny():
    rules = [{"action": "allow", "cidr": "10.1.2.0/24"},
             {"action": "deny", "cidr": "10.1.0.0/16"}]
    assert acl_first_match(rules, "10.1.2.3") == "ALLOW"
    assert acl_decision(rules, "10.1.2.3") == ("ALLOW", 0)


def test_acl_first_match_deny_beats_later_allow():
    rules = [{"action": "deny", "cidr": "10.1.2.0/24"},
             {"action": "allow", "cidr": "10.1.0.0/16"}]
    assert acl_first_match(rules, "10.1.2.3") == "DENY"
    assert acl_decision(rules, "10.1.2.3") == ("DENY", 0)


def test_acl_default_deny_when_no_rule_matches():
    rules = [{"action": "allow", "cidr": "10.0.0.0/16"}]
    assert acl_first_match(rules, "192.168.1.1") == "DENY"
    assert acl_decision(rules, "192.168.1.1") == ("DENY", -1)


def test_acl_later_rule_matches_when_earlier_does_not():
    rules = [{"action": "deny", "cidr": "10.1.0.0/16"},
             {"action": "allow", "cidr": "192.168.0.0/16"}]
    assert acl_decision(rules, "192.168.5.5") == ("ALLOW", 1)


def test_ipv6_containment():
    assert address_in_cidr("2001:db8::1", "2001:db8::/32") == "IN"
    assert address_in_cidr("2001:db9::1", "2001:db8::/32") == "OUT"


def test_ipv6_adjacent_disjoint():
    assert subnet_overlap("2001:db8:0::/48", "2001:db8:1::/48") == "DISJOINT"


def test_ipv6_containment_overlap():
    assert subnet_overlap("2001:db8::/32", "2001:db8:abcd::/48") == "OVERLAP"


def test_mixed_family_never_contained_or_overlapping():
    assert address_in_cidr("2001:db8::1", "10.0.0.0/8") == "OUT"
    assert subnet_overlap("10.0.0.0/8", "2001:db8::/32") == "DISJOINT"


def test_check_record_correct_and_incorrect_labels():
    assert check_record("address_in_cidr",
                        {"address": "10.1.2.3", "cidr": "10.1.0.0/16"}, "IN").objective_label == "CORRECT"
    assert check_record("address_in_cidr",
                        {"address": "10.1.2.3", "cidr": "10.1.0.0/16"}, "OUT").objective_label == "INCORRECT"


def test_check_record_rejects_bad_answer_token():
    import pytest
    with pytest.raises(ValueError):
        check_record("address_in_cidr", {"address": "10.1.2.3", "cidr": "10.1.0.0/16"}, "OVERLAP")


def test_check_record_rejects_unknown_op():
    import pytest
    with pytest.raises(ValueError):
        check_record("nonsense_op", {}, "IN")


def test_self_test_passes():
    from evals.objective_ip_cidr_containment import checker_cidr
    checker_cidr.self_test()  # raises on any internal inconsistency


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -----

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["op"], fx["args"], fx["candidate_answer"])
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
        "off_by_one_boundary", "wrong_prefix_len", "adjacent_not_overlapping",
        "acl_first_match_error", "ipv6", "none",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_three_ops_present():
    ops = {fx["op"] for fx in FIXTURES}
    assert ops == {"address_in_cidr", "subnet_overlap", "acl_first_match"}


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_every_correct_fixture_has_empty_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx.get("mutation", "") == "", f"{fx['id']} is CORRECT but carries a mutation"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-op/same-args CORRECT sibling (same question, only
    the candidate_answer differs) -- proof it perturbs exactly the decision, not the scenario."""
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
        # the CORRECT sibling's answer must be the opposite token
        correct = next(s for s in siblings if s["expected_label"] == "CORRECT")
        assert correct["candidate_answer"] != fx["candidate_answer"], fx["id"]


def test_candidate_answer_tokens_valid_per_op():
    allowed = {
        "address_in_cidr": {"IN", "OUT"},
        "subnet_overlap": {"OVERLAP", "DISJOINT"},
        "acl_first_match": {"ALLOW", "DENY"},
    }
    for fx in FIXTURES:
        assert fx["candidate_answer"] in allowed[fx["op"]], fx["id"]
