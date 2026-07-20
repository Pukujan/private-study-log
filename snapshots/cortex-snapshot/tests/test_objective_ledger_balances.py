"""Frozen tests for the objective double-entry ledger checker (Stage 2 ledger_balances lane).

These lock the checker's verdicts on the gold fixtures: every BALANCED fixture must be BALANCED,
every BROKEN fixture must be BROKEN and must isolate exactly the one invariant it targets. Plus
targeted unit checks per invariant (so a regression names the invariant it broke) and the Decimal
vs integer-cents cross-check. Pure arithmetic — no judge anywhere in the verdict path.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_ledger_balances.checker_ledger import (  # noqa: E402
    check_ledger, check_ledger_intcents, INVARIANTS)
from evals.objective_ledger_balances.fixtures_ledger import fixtures  # noqa: E402


def test_all_fixtures_labeled_and_isolated():
    for fx in fixtures():
        res = check_ledger(fx)
        assert res.label == fx["label"], f"{fx['id']}: expected {fx['label']}, got {res.label}"
        if fx["label"] == "BROKEN":
            assert res.types == {fx["invariant"]}, \
                f"{fx['id']}: expected only {fx['invariant']}, got {sorted(res.types)}"
        else:
            assert not res.violations, f"{fx['id']}: BALANCED but got {res.violations}"


def test_decimal_and_intcents_agree_on_every_fixture():
    for fx in fixtures():
        assert check_ledger(fx).label == check_ledger_intcents(fx), \
            f"{fx['id']}: Decimal and integer-cents paths disagree"


def test_label_distribution_is_stable():
    labels = [check_ledger(fx).label for fx in fixtures()]
    assert labels.count("BALANCED") == 9
    assert labels.count("BROKEN") == 10


def test_every_invariant_class_is_represented():
    seen = set()
    for fx in fixtures():
        res = check_ledger(fx)
        seen |= res.types
    assert seen == set(INVARIANTS)


# ---- targeted per-invariant units (minimal ledgers) ----

def _acct(**kw):
    return kw


def test_unbalanced_txn_flagged_isolated():
    # two offsetting mis-entries: each txn unbalanced, ledger nets to zero -> only unbalanced_txn
    rec = {"accounts": {"cash": _acct(type="asset"), "rev": _acct(type="revenue")},
           "transactions": [
               {"id": "a", "postings": [{"account": "cash", "debit": "100.00", "credit": "0"},
                                        {"account": "rev", "debit": "0", "credit": "90.00"}]},
               {"id": "b", "postings": [{"account": "cash", "debit": "90.00", "credit": "0"},
                                        {"account": "rev", "debit": "0", "credit": "100.00"}]}]}
    assert check_ledger(rec).types == {"unbalanced_txn"}


def test_one_cent_error_caught_not_masked():
    rec = {"accounts": {"a": _acct(type="asset"), "b": _acct(type="liability")},
           "transactions": [{"id": "t", "postings": [{"account": "a", "debit": "0.10", "credit": "0"},
                                                     {"account": "b", "debit": "0", "credit": "0.11"}]}]}
    assert "unbalanced_txn" in check_ledger(rec).types


def test_trial_mismatch_from_openings():
    rec = {"accounts": {"cash": _acct(type="asset", opening="100.00"),
                        "eq": _acct(type="equity", opening="-90.00")},
           "transactions": []}
    assert check_ledger(rec).types == {"trial_mismatch"}


def test_account_unreconciled_isolated():
    rec = {"accounts": {"cash": _acct(type="asset", reported="999.00"),
                        "rev": _acct(type="revenue", reported="-100.00")},
           "transactions": [{"id": "t", "postings": [{"account": "cash", "debit": "100.00", "credit": "0"},
                                                     {"account": "rev", "debit": "0", "credit": "100.00"}]}]}
    # cash computes to 100.00 but reports 999.00 -> only reconciliation fails (trial nets 0)
    assert check_ledger(rec).types == {"account_unreconciled"}


def test_illegal_negative_isolated():
    rec = {"accounts": {"cash": _acct(type="asset", no_overdraft=True, opening="100.00"),
                        "eq": _acct(type="equity", opening="-100.00"),
                        "exp": _acct(type="expense")},
           "transactions": [{"id": "t", "postings": [{"account": "exp", "debit": "500.00", "credit": "0"},
                                                     {"account": "cash", "debit": "0", "credit": "500.00"}]}]}
    assert check_ledger(rec).types == {"illegal_negative"}


def test_no_overdraft_at_exactly_zero_is_allowed():
    rec = {"accounts": {"cash": _acct(type="asset", no_overdraft=True, opening="500.00"),
                        "eq": _acct(type="equity", opening="-500.00"),
                        "exp": _acct(type="expense")},
           "transactions": [{"id": "t", "postings": [{"account": "exp", "debit": "500.00", "credit": "0"},
                                                     {"account": "cash", "debit": "0", "credit": "500.00"}]}]}
    assert check_ledger(rec).label == "BALANCED"


def test_float_amounts_are_rejected():
    import pytest
    rec = {"accounts": {"a": _acct(type="asset"), "b": _acct(type="liability")},
           "transactions": [{"id": "t", "postings": [{"account": "a", "debit": 1.10, "credit": "0"},
                                                     {"account": "b", "debit": "0", "credit": "1.10"}]}]}
    with pytest.raises(TypeError):
        check_ledger(rec)
