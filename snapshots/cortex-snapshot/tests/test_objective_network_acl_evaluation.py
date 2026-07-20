"""Frozen tests for the objective network-ACL-evaluation checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only (`ipaddress`) ordered first-match ACL evaluation with default-deny
(checker_acl.evaluate / check_record), never a model/judge. These tests pin the checker on
hand-picked cases (independent of the runner's fixture list), sweep every fixture asserting the
checker agrees with its declared expected_label, and assert the lane's structural invariants
(balance, unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_network_acl_evaluation.checker_acl import (  # noqa: E402
    check_record,
    evaluate,
)
from evals.objective_network_acl_evaluation.run_acl import FIXTURES  # noqa: E402

_ANY = "0.0.0.0/0"


def _r(action, src, dst, proto, lo, hi):
    return {"action": action, "src_cidr": src, "dst_cidr": dst, "proto": proto, "port_range": [lo, hi]}


def _p(src, dst, proto, port):
    return {"src_ip": src, "dst_ip": dst, "proto": proto, "port": port}


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_first_match_wins_over_later_contradictory_rule():
    rules = [_r("allow", "10.1.2.0/24", _ANY, "any", 0, 65535),
             _r("deny", "10.1.0.0/16", _ANY, "any", 0, 65535)]
    pkt = _p("10.1.2.3", "203.0.113.1", "tcp", 443)
    assert evaluate(rules, pkt) == ("ALLOW", 0)
    assert check_record(rules, pkt, "ALLOW").objective_label == "CORRECT"
    assert check_record(rules, pkt, "DENY").objective_label == "INCORRECT"


def test_deny_then_allow_ordering_pair():
    rules = [_r("deny", "10.1.2.0/24", _ANY, "any", 0, 65535),
             _r("allow", "10.1.0.0/16", _ANY, "any", 0, 65535)]
    pkt = _p("10.1.2.3", "203.0.113.1", "tcp", 443)
    assert evaluate(rules, pkt) == ("DENY", 0)
    assert check_record(rules, pkt, "DENY").objective_label == "CORRECT"


def test_default_deny_when_nothing_matches():
    rules = [_r("allow", "10.0.0.0/16", _ANY, "tcp", 80, 80)]
    pkt = _p("192.168.1.1", "203.0.113.5", "tcp", 80)
    assert evaluate(rules, pkt) == ("DENY", -1)
    assert check_record(rules, pkt, "DENY").objective_label == "CORRECT"
    assert check_record(rules, pkt, "ALLOW").objective_label == "INCORRECT"


def test_port_lower_bound_inclusive():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "tcp", 1000, 2000)]
    assert evaluate(rules, _p("10.1.1.1", "203.0.113.1", "tcp", 1000))[0] == "ALLOW"
    assert evaluate(rules, _p("10.1.1.1", "203.0.113.1", "tcp", 999))[0] == "DENY"


def test_port_upper_bound_inclusive():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "tcp", 1000, 2000)]
    assert evaluate(rules, _p("10.1.1.1", "203.0.113.1", "tcp", 2000))[0] == "ALLOW"
    assert evaluate(rules, _p("10.1.1.1", "203.0.113.1", "tcp", 2001))[0] == "DENY"


def test_proto_any_matches_udp():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "any", 53, 53)]
    assert evaluate(rules, _p("10.1.1.1", "203.0.113.1", "udp", 53))[0] == "ALLOW"


def test_proto_specific_ignores_other_proto():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "tcp", 53, 53)]
    assert evaluate(rules, _p("10.1.1.1", "203.0.113.1", "udp", 53))[0] == "DENY"
    assert check_record(rules, _p("10.1.1.1", "203.0.113.1", "udp", 53), "ALLOW").objective_label == "INCORRECT"


def test_cidr_network_and_broadcast_addresses_are_members():
    rules = [_r("allow", "192.168.10.0/24", _ANY, "any", 0, 65535)]
    dst = "203.0.113.1"
    assert evaluate(rules, _p("192.168.10.0", dst, "tcp", 22))[0] == "ALLOW"     # network addr
    assert evaluate(rules, _p("192.168.10.255", dst, "tcp", 22))[0] == "ALLOW"   # broadcast addr


def test_cidr_just_outside_is_denied():
    rules = [_r("allow", "192.168.10.0/24", _ANY, "any", 0, 65535)]
    assert evaluate(rules, _p("192.168.11.0", "203.0.113.1", "tcp", 22))[0] == "DENY"


def test_dst_cidr_clause_enforced():
    rules = [_r("allow", "10.0.0.0/8", "172.16.0.0/12", "any", 0, 65535)]
    assert evaluate(rules, _p("10.1.1.1", "172.16.5.5", "tcp", 80))[0] == "ALLOW"
    assert evaluate(rules, _p("10.1.1.1", "192.168.1.1", "tcp", 80))[0] == "DENY"


def test_computed_answer_is_the_decision_token():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "tcp", 22, 22)]
    r = check_record(rules, _p("10.1.1.1", "203.0.113.1", "tcp", 22), "ALLOW")
    assert r.computed_answer == "ALLOW"
    assert r.matched_rule_index == 0


def test_matched_rule_index_is_minus_one_on_default_deny():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "tcp", 22, 22)]
    r = check_record(rules, _p("192.168.1.1", "203.0.113.1", "tcp", 22), "DENY")
    assert r.matched_rule_index == -1


def test_invalid_candidate_rejected():
    rules = [_r("allow", "10.0.0.0/8", _ANY, "tcp", 22, 22)]
    try:
        check_record(rules, _p("10.1.1.1", "203.0.113.1", "tcp", 22), "MAYBE")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a non ALLOW/DENY candidate")


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["rules"], fx["packet"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed_decision():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == evaluate(fx["rules"], fx["packet"])[0], fx["id"]


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
        "none", "wrong_first_match", "default_deny_missed",
        "port_boundary", "proto_mismatch", "cidr_boundary",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    def key(fx):
        return (json.dumps(fx["rules"], sort_keys=True), json.dumps(fx["packet"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed_decision():
    # a genuinely INCORRECT record must carry a candidate that is not the true decision
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != evaluate(fx["rules"], fx["packet"])[0], fx["id"]
