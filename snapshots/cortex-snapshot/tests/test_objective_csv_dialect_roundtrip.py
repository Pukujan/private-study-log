"""Frozen tests for the objective CSV-dialect-roundtrip checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only CSV parse + canonical re-serialization (checker_csv.canonicalize /
check_record), never a model/judge. These tests pin the checker on hand-picked cases (independent of
the runner's fixture list), sweep every fixture asserting the checker agrees with its declared
expected_label, and assert the lane's structural invariants (balance, unique ids, taxonomy coverage,
mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_csv_dialect_roundtrip.checker_csv import (  # noqa: E402
    canonicalize,
    check_record,
)
from evals.objective_csv_dialect_roundtrip.run_csv import FIXTURES  # noqa: E402

_C = {"delimiter": ",", "quotechar": '"', "quoting": "QUOTE_MINIMAL", "lineterminator": "\n"}
_SEMI = {**_C, "delimiter": ";"}
_TAB = {**_C, "delimiter": "\t"}


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_clean_grid_roundtrips_unchanged():
    assert canonicalize("a,b,c\nd,e,f\n", _C) == "a,b,c\nd,e,f\n"


def test_comma_field_must_be_quoted():
    assert canonicalize('x,"a,b",y\n', _C) == 'x,"a,b",y\n'
    # a candidate that leaves the comma field unquoted is INCORRECT
    assert check_record('x,"a,b",y\n', _C, "x,a,b,y\n").objective_label == "INCORRECT"
    assert check_record('x,"a,b",y\n', _C, 'x,"a,b",y\n').objective_label == "CORRECT"


def test_embedded_quote_is_doubled():
    assert canonicalize('bob,"she said ""hi"""\n', _C) == 'bob,"she said ""hi"""\n'
    assert check_record('bob,"she said ""hi"""\n', _C, 'bob,she said "hi"\n').objective_label == "INCORRECT"


def test_delimiter_confusion_semicolon():
    assert canonicalize("a;b;c\n", _SEMI) == "a,b,c\n"
    # keeping the semicolon delimiter is wrong
    assert check_record("a;b;c\n", _SEMI, "a;b;c\n").objective_label == "INCORRECT"
    assert check_record("a;b;c\n", _SEMI, "a,b,c\n").objective_label == "CORRECT"


def test_delimiter_confusion_tab():
    assert canonicalize("a\tb\tc\n", _TAB) == "a,b,c\n"
    assert check_record("a\tb\tc\n", _TAB, "a\tb\tc\n").objective_label == "INCORRECT"


def test_embedded_newline_stays_quoted():
    assert canonicalize('a,"l1\nl2",c\n', _C) == 'a,"l1\nl2",c\n'
    # unquoting the embedded newline corrupts the record
    assert check_record('a,"l1\nl2",c\n', _C, "a,l1\nl2,c\n").objective_label == "INCORRECT"


def test_trailing_whitespace_is_significant():
    # skipinitialspace is off: leading/trailing whitespace is preserved verbatim.
    assert canonicalize("a , b , c\n", _C) == "a , b , c\n"
    assert check_record("a , b , c\n", _C, "a,b,c\n").objective_label == "INCORRECT"


def test_empty_field_preserved():
    assert canonicalize("a,,c\n", _C) == "a,,c\n"
    assert check_record("a,,c\n", _C, "a,c\n").objective_label == "INCORRECT"


def test_over_quoting_is_incorrect():
    assert check_record("a,b,c\n", _C, '"a","b","c"\n').objective_label == "INCORRECT"


def test_trailing_newline_required():
    assert canonicalize("name,age\nAlice,30\n", _C) == "name,age\nAlice,30\n"
    assert check_record("name,age\nAlice,30\n", _C, "name,age\nAlice,30").objective_label == "INCORRECT"


def test_newline_convention_insensitive():
    # a CRLF-terminated candidate that is otherwise identical is still CORRECT
    assert check_record("a,b,c\n", _C, "a,b,c\r\n").objective_label == "CORRECT"
    assert check_record("a,b\nc,d\n", _C, "a,b\r\nc,d\r\n").objective_label == "CORRECT"


def test_canonicalize_is_idempotent():
    for raw, d in [("a,b,c\nd,e,f\n", _C), ('x,"a,b",y\n', _C), ("a;b;c\n", _SEMI),
                   ('a,"l1\nl2",c\n', _C)]:
        once = canonicalize(raw, d)
        assert canonicalize(once, _C) == once


def test_computed_answer_is_the_canonical_string():
    r = check_record("a;b;c\n", _SEMI, "a,b,c\n")
    assert r.computed_answer == "a,b,c\n"


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["raw"], fx["dialect"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_canonical():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == canonicalize(fx["raw"], fx["dialect"]), fx["id"]


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
    required = {
        "quoting_error", "delimiter_confusion", "embedded_newline",
        "trailing_whitespace", "empty_field", "header_roundtrip", "none",
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
        return (fx["raw"], json.dumps(fx["dialect"], sort_keys=True))

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_canonical():
    # a genuinely INCORRECT record must carry a candidate that is not the canonical answer
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != canonicalize(fx["raw"], fx["dialect"]), fx["id"]
