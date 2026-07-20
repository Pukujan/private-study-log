"""Frozen tests for the objective base64/base32/base16 codec checker (Stage-2 style lane).

LABEL AUTHORITY: deterministic RFC 4648 codecs via the stdlib `base64` module
(checker_base64.encode_bytes / decode_answer / is_valid / check_record), never a model/judge.
These tests pin the checker on hand-picked cases (independent of the fixture file) plus a full
sweep over every fixture in fixtures_base64.py, asserting the checker's objective_label always
matches the fixture's declared expected_label, plus structural invariants (counts, balance,
taxonomy coverage, mutation-integrity).

Written before checker_base64.py was trusted: this file defines the contract.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from evals.objective_base64_codec_roundtrip.checker_base64 import (  # noqa: E402
    check_record,
    decode_answer,
    encode_bytes,
    is_valid,
)
from evals.objective_base64_codec_roundtrip.fixtures_base64 import FIXTURES  # noqa: E402


# --- hand-picked oracle cases, independent of the fixture file -------------------------------

def test_standard_encode():
    assert encode_bytes(b"Hello", "standard") == "SGVsbG8="


def test_urlsafe_vs_standard_alphabet():
    assert encode_bytes(b"\xfb\xff", "standard") == "+/8="
    assert encode_bytes(b"\xfb\xff", "urlsafe") == "-_8="


def test_base32_encode_padding():
    assert encode_bytes(b"foobar", "base32") == "MZXW6YTBOI======"


def test_base16_is_uppercase():
    assert encode_bytes(b"\xde\xad\xbe\xef", "base16") == "DEADBEEF"


def test_decode_roundtrip_hex():
    assert decode_answer("SGVsbG8=", "standard") == b"Hello".hex()
    assert decode_answer("-_8=", "urlsafe") == "fbff"


def test_wrong_alphabet_is_invalid():
    assert decode_answer("-_8=", "standard") == "INVALID"   # urlsafe chars, standard codec
    assert decode_answer("+/8=", "urlsafe") == "INVALID"    # standard chars, urlsafe codec


def test_base16_lowercase_rejected():
    assert decode_answer("deadbeef", "base16") == "INVALID"
    assert is_valid("deadbeef", "base16") == "INVALID"
    assert is_valid("DEADBEEF", "base16") == "VALID"


def test_missing_padding_invalid():
    assert is_valid("SGVsbG8", "standard") == "INVALID"
    assert is_valid("SGVsbG8=", "standard") == "VALID"


def test_noncanonical_trailing_bits_invalid():
    # 'Zm9vYg==' is canonical for 'foob'; 'Zm9vYh==' decodes but has non-zero unused bits.
    assert is_valid("Zm9vYg==", "standard") == "VALID"
    assert is_valid("Zm9vYh==", "standard") == "INVALID"


def test_base32_lowercase_invalid():
    assert is_valid("mzxw6ytboi======", "base32") == "INVALID"
    assert is_valid("MZXW6YTBOI======", "base32") == "VALID"


def test_check_record_correct_and_incorrect_labels():
    assert check_record("encode", {"data_hex": "48656c6c6f", "codec": "standard"},
                        "SGVsbG8=").objective_label == "CORRECT"
    assert check_record("encode", {"data_hex": "48656c6c6f", "codec": "standard"},
                        "SGVsbG9=").objective_label == "INCORRECT"


def test_check_record_rejects_unknown_op():
    with pytest.raises(ValueError):
        check_record("nonsense_op", {}, "x")


def test_self_test_passes():
    from evals.objective_base64_codec_roundtrip import checker_base64
    checker_base64.self_test()


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
    required = {"alphabet_confusion", "padding", "wrong_case", "non_canonical", "none"}
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_three_ops_present():
    ops = {fx["op"] for fx in FIXTURES}
    assert ops == {"encode", "decode", "is_valid"}


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
