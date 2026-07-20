"""Frozen tests for the objective sql-correctness checker (Stage-2 style lane).

LABEL AUTHORITY: sqlite3 EXECUTION, never a model/judge. These tests pin the checker's behavior on
hand-picked cases (independent of the fixture file, covering NULLs/duplicates/column-set/errors/
order-sensitivity explicitly) plus a full sweep over every fixture in fixtures_sql.py, asserting the
checker's objective_label always matches the fixture's declared expected_label (the same
self-validation gate every other Stage-2 lane uses).
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_sql_correctness.checker_sql import check_record  # noqa: E402
from evals.objective_sql_correctness.fixtures_sql import FIXTURES, SCHEMA, SEED  # noqa: E402


# --- hand-picked cases, independent of the fixture file ---------------------------------------

def test_identical_query_is_correct():
    r = check_record(SCHEMA, SEED, "SELECT id FROM orders", "SELECT id FROM orders")
    assert r.objective_label == "CORRECT"


def test_wrong_join_key_is_incorrect():
    reference = "SELECT c.name AS name, o.amount AS amount FROM customers c JOIN orders o ON o.customer_id = c.id"
    candidate = "SELECT c.name AS name, o.amount AS amount FROM customers c JOIN orders o ON o.id = c.id"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_missing_where_is_incorrect():
    reference = "SELECT name FROM customers WHERE country = 'USA'"
    candidate = "SELECT name FROM customers"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_candidate_sql_error_is_incorrect_never_crashes():
    reference = "SELECT id FROM orders"
    candidate = "SELECT id FROM not_a_real_table"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"
    assert r.error


def test_candidate_sql_syntax_error_is_incorrect_never_crashes():
    reference = "SELECT id FROM orders"
    candidate = "SELEKT id FRM orders"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"
    assert r.error


def test_order_insensitive_by_default_reordered_rows_still_correct():
    reference = "SELECT id FROM orders ORDER BY id ASC"
    candidate = "SELECT id FROM orders ORDER BY id DESC"
    r = check_record(SCHEMA, SEED, reference, candidate, order_sensitive=False)
    assert r.objective_label == "CORRECT"


def test_order_sensitive_reordered_rows_are_incorrect():
    reference = "SELECT id FROM orders ORDER BY id ASC"
    candidate = "SELECT id FROM orders ORDER BY id DESC"
    r = check_record(SCHEMA, SEED, reference, candidate, order_sensitive=True)
    assert r.objective_label == "INCORRECT"


def test_null_equals_null_comparison_is_incorrect():
    # classic trap: `= NULL` is always unknown/false in SQL, never matches -- IS NULL is required.
    reference = "SELECT id FROM orders WHERE amount IS NULL"
    candidate = "SELECT id FROM orders WHERE amount = NULL"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_count_star_vs_count_column_null_handling_is_incorrect():
    reference = "SELECT COUNT(amount) AS n FROM orders"
    candidate = "SELECT COUNT(*) AS n FROM orders"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_missing_column_is_incorrect():
    reference = "SELECT id, amount FROM orders"
    candidate = "SELECT id FROM orders"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_extra_column_is_incorrect():
    reference = "SELECT id, amount FROM orders"
    candidate = "SELECT id, amount, status FROM orders"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_duplicate_rows_are_not_conflated_with_single_row():
    tiny_schema = ["CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER NOT NULL)"]
    tiny_seed = ["INSERT INTO t (id, v) VALUES (1, 5)", "INSERT INTO t (id, v) VALUES (2, 6)"]
    reference = "SELECT v FROM t WHERE id = 1"
    # fan-out bug: an unrestricted cartesian join against t2 (2 rows) doubles the single row.
    candidate = "SELECT t1.v FROM t t1, t t2 WHERE t1.id = 1"
    r = check_record(tiny_schema, tiny_seed, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_off_by_one_limit_is_incorrect():
    reference = "SELECT id FROM orders WHERE amount IS NOT NULL ORDER BY amount DESC LIMIT 3"
    candidate = "SELECT id FROM orders WHERE amount IS NOT NULL ORDER BY amount DESC LIMIT 2"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_wrong_group_by_is_incorrect():
    reference = "SELECT country, COUNT(*) AS n FROM customers GROUP BY country"
    candidate = "SELECT country, COUNT(*) AS n FROM customers GROUP BY name"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_wrong_aggregate_sum_vs_count_is_incorrect():
    reference = "SELECT SUM(amount) AS total FROM orders"
    candidate = "SELECT COUNT(amount) AS total FROM orders"
    r = check_record(SCHEMA, SEED, reference, candidate)
    assert r.objective_label == "INCORRECT"


def test_reference_sql_error_raises_for_run_script_to_quarantine():
    with pytest.raises(Exception):
        check_record(SCHEMA, SEED, "SELECT * FROM nonexistent_table", "SELECT 1")


# --- full fixture sweep: checker must agree with every fixture's declared expected_label -------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["schema"], fx["seed"], fx["reference_sql"], fx["candidate_sql"],
                          order_sensitive=fx["order_sensitive"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 22


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_eight_failure_classes_covered():
    required = {
        "wrong_join", "missing_where", "wrong_aggregate", "wrong_group_by",
        "off_by_one_limit", "wrong_order", "extra_missing_column", "null_mishandling",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_every_correct_fixture_candidate_matches_reference_verbatim():
    """CORRECT fixtures are guaranteed correct by construction: candidate_sql == reference_sql."""
    for fx in FIXTURES:
        if fx["expected_label"] == "CORRECT":
            assert fx["candidate_sql"] == fx["reference_sql"], fx["id"]


def test_mutation_integrity_incorrect_shares_reference_sql_with_a_correct_sibling():
    """Every INCORRECT fixture must have a same-reference_sql CORRECT sibling (same question,
    only candidate_sql differs) -- proof it perturbs exactly one trap, not the question."""
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(fx["reference_sql"], []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[fx["reference_sql"]]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_no_judge_import_anywhere_in_checker_module():
    """Static guard: the checker module must never import a judge/LLM dispatch path."""
    src = (ROOT / "evals" / "objective_sql_correctness" / "checker_sql.py").read_text(encoding="utf-8")
    for banned in ("cortex_core.judge", "codex_judge", "openai", "anthropic"):
        assert banned not in src, f"checker_sql.py must not reference {banned!r}"
