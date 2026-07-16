"""Frozen tests for the objective config-format-parse checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only parse (checker_config.parse_config / check_record) with
`tomllib`/`configparser`/`json`, never a model/judge. These tests pin the checker on hand-picked
cases (independent of the runner's fixture list), sweep every fixture asserting the checker agrees
with its declared expected_label, and assert the lane's structural invariants (balance, unique ids,
taxonomy + format coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_config_format_parse.checker_config import (  # noqa: E402
    PARSE_ERROR,
    check_record,
    parse_config,
)
from evals.objective_config_format_parse.run_config import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

# toml
def test_toml_valid_typed_values():
    assert parse_config('title = "x"\nport = 8080\ndebug = true\n', "toml") == {
        "title": "x", "port": 8080, "debug": True}


def test_toml_int_vs_string_distinction():
    assert parse_config('port = 8080\n', "toml") == {"port": 8080}
    assert parse_config('port = "8080"\n', "toml") == {"port": "8080"}
    assert check_record('port = 8080\n', "toml", {"port": "8080"}).objective_label == "INCORRECT"
    assert check_record('port = 8080\n', "toml", {"port": 8080}).objective_label == "CORRECT"


def test_toml_syntax_error_is_parse_error():
    assert parse_config("port = \n", "toml") == PARSE_ERROR
    assert check_record("port = \n", "toml", {"port": ""}).objective_label == "INCORRECT"
    assert check_record("port = \n", "toml", PARSE_ERROR).objective_label == "CORRECT"


# ini
def test_ini_valid_values_are_strings():
    assert parse_config("[server]\nhost = localhost\nport = 8080\n", "ini") == {
        "server": {"host": "localhost", "port": "8080"}}


def test_ini_value_not_coerced_to_int():
    # configparser never coerces: the true value is the string "5432", not the int 5432.
    assert parse_config("[db]\nport = 5432\n", "ini") == {"db": {"port": "5432"}}
    assert check_record("[db]\nport = 5432\n", "ini", {"db": {"port": 5432}}).objective_label == "INCORRECT"
    assert check_record("[db]\nport = 5432\n", "ini", {"db": {"port": "5432"}}).objective_label == "CORRECT"


def test_ini_missing_section_header_is_parse_error():
    assert parse_config("host = localhost\n", "ini") == PARSE_ERROR


def test_ini_duplicate_option_is_parse_error():
    assert parse_config("[a]\nx = 1\nx = 2\n", "ini") == PARSE_ERROR


# json
def test_json_valid_nested():
    assert parse_config('{"a": {"b": [1, 2, 3]}, "c": true}', "json") == {
        "a": {"b": [1, 2, 3]}, "c": True}


def test_json_trailing_comma_is_parse_error():
    assert parse_config('{"a": 1,}', "json") == PARSE_ERROR
    assert check_record('{"a": 1,}', "json", {"a": 1}).objective_label == "INCORRECT"
    assert check_record('{"a": 1,}', "json", PARSE_ERROR).objective_label == "CORRECT"


def test_false_syntax_error_on_valid_doc_is_incorrect():
    assert check_record('name = "ok"\n', "toml", PARSE_ERROR).objective_label == "INCORRECT"


def test_computed_answer_is_the_true_parse():
    r = check_record("[db]\nport = 5432\n", "ini", {"db": {"port": 5432}})
    assert r.computed_answer == {"db": {"port": "5432"}}
    r2 = check_record("port = \n", "toml", PARSE_ERROR)
    assert r2.computed_answer == PARSE_ERROR


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["text"], fx["fmt"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert fx["candidate"] == parse_config(fx["text"], fx["fmt"]), fx["id"]


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
        "none", "wrong_value", "missing_key", "wrong_type_coercion",
        "missed_syntax_error", "false_syntax_error",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_all_formats_present():
    present = {fx["fmt"] for fx in FIXTURES}
    assert {"toml", "ini", "json"}.issubset(present), {"toml", "ini", "json"} - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    def key(fx):
        return (fx["text"], fx["fmt"])

    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(key(fx), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[key(fx)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    # a genuinely INCORRECT record must carry a candidate that is not the true parse
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert fx["candidate"] != parse_config(fx["text"], fx["fmt"]), fx["id"]
