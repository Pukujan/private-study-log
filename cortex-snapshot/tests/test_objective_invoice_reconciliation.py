"""Frozen tests for the objective invoice-reconciliation checker (Stage 2 lane,
invoice_reconciliation — item #1 in the NEXT build queue,
docs/research/oracle-domains-top50-prioritized-2026-07-11.md).

These lock the checker's verdicts on the gold fixtures: every RECONCILED fixture must be
RECONCILED, every DISCREPANCY fixture must be DISCREPANCY and must isolate exactly the one
failure class it targets. Plus targeted unit checks per failure class (so a regression names the
class it broke) and the Decimal vs integer-cents cross-check. Pure arithmetic — no judge anywhere
in the verdict path.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_invoice_reconciliation.checker_invoice import (  # noqa: E402
    check_invoice, check_invoice_intcents, FAILURE_CLASSES)
from evals.objective_invoice_reconciliation.fixtures_invoice import fixtures  # noqa: E402


def test_all_fixtures_labeled_and_isolated():
    for fx in fixtures():
        res = check_invoice(fx)
        assert res.label == fx["label"], f"{fx['id']}: expected {fx['label']}, got {res.label}"
        if fx["label"] == "DISCREPANCY":
            assert res.types == {fx["failure_class"]}, \
                f"{fx['id']}: expected only {fx['failure_class']}, got {sorted(res.types)}"
        else:
            assert not res.violations, f"{fx['id']}: RECONCILED but got {res.violations}"


def test_decimal_and_intcents_agree_on_every_fixture():
    for fx in fixtures():
        assert check_invoice(fx).label == check_invoice_intcents(fx), \
            f"{fx['id']}: Decimal and integer-cents paths disagree"


def test_label_distribution_is_stable():
    labels = [check_invoice(fx).label for fx in fixtures()]
    assert labels.count("RECONCILED") == 9
    assert labels.count("DISCREPANCY") == 10


def test_every_failure_class_is_represented():
    seen = set()
    for fx in fixtures():
        res = check_invoice(fx)
        seen |= res.types
    assert seen == set(FAILURE_CLASSES)


def test_each_failure_class_has_at_least_two_fixtures():
    from collections import Counter
    dist = Counter(fx["failure_class"] for fx in fixtures() if fx["label"] == "DISCREPANCY")
    for cls in FAILURE_CLASSES:
        assert dist[cls] >= 2, f"{cls}: only {dist[cls]} fixtures"


# ---- targeted per-failure-class units (minimal invoices) ----

def _line(desc, qty, unit_price, line_total):
    return {"description": desc, "qty": qty, "unit_price": unit_price, "line_total": line_total}


def _inv(lines, subtotal, discount, tax_rate, tax, total):
    return {"line_items": lines, "subtotal": subtotal, "discount": discount,
            "tax_rate": tax_rate, "tax": tax, "total": total}


def test_line_math_error_isolated():
    rec = _inv(
        [_line("widget", 3, "10.00", "35.00")],   # 3*10.00 = 30.00, stated 35.00 (wrong)
        subtotal="35.00",                          # consistent with the (wrong) stated line total
        discount={"type": "fixed", "value": "0.00", "amount": "0.00"},
        tax_rate="0.00", tax="0.00", total="35.00")
    assert check_invoice(rec).types == {"line_math_error"}


def test_subtotal_mismatch_isolated():
    rec = _inv(
        [_line("a", 2, "10.00", "20.00"), _line("b", 3, "5.00", "15.00")],  # lines correct, sum 35.00
        subtotal="36.00",                          # wrong: doesn't match sum of stated line totals
        discount={"type": "fixed", "value": "0.00", "amount": "0.00"},
        tax_rate="0.00", tax="0.00", total="36.00")
    assert check_invoice(rec).types == {"subtotal_mismatch"}


def test_discount_error_isolated():
    rec = _inv(
        [_line("a", 4, "25.00", "100.00")],
        subtotal="100.00",
        discount={"type": "percentage", "value": "10.00", "amount": "12.00"},  # should be 10.00
        tax_rate="0.00", tax="0.00", total="88.00")
    assert check_invoice(rec).types == {"discount_error"}


def test_tax_rounding_error_isolated():
    rec = _inv(
        [_line("a", 1, "50.00", "50.00")],
        subtotal="50.00",
        discount={"type": "fixed", "value": "0.00", "amount": "0.00"},
        tax_rate="0.0625", tax="3.12",  # 50.00*0.0625 = 3.125 -> half-up canonical is 3.13
        total="53.12")
    assert check_invoice(rec).types == {"tax_rounding_error"}


def test_grand_total_mismatch_isolated():
    rec = _inv(
        [_line("a", 6, "12.00", "72.00")],
        subtotal="72.00",
        discount={"type": "fixed", "value": "2.00", "amount": "2.00"},
        tax_rate="0.05", tax="3.50",   # 72.00 - 2.00 + 3.50 = 73.50, stated total below is wrong
        total="73.00")
    assert check_invoice(rec).types == {"grand_total_mismatch"}


def test_half_up_boundary_reconciled():
    # exact X.XX5 boundary rounds up under the fixed ROUND_HALF_UP convention
    rec = _inv(
        [_line("a", 1, "50.00", "50.00")],
        subtotal="50.00",
        discount={"type": "fixed", "value": "0.00", "amount": "0.00"},
        tax_rate="0.0625", tax="3.13", total="53.13")
    res = check_invoice(rec)
    assert res.label == "RECONCILED", res.violations


def test_float_money_rejected():
    import pytest
    rec = _inv(
        [_line("a", 1, 50.0, "50.00")],   # float unit_price
        subtotal="50.00",
        discount={"type": "fixed", "value": "0.00", "amount": "0.00"},
        tax_rate="0.00", tax="0.00", total="50.00")
    with pytest.raises(TypeError):
        check_invoice(rec)


def test_float_qty_rejected():
    import pytest
    rec = _inv(
        [_line("a", 1.0, "50.00", "50.00")],   # float qty
        subtotal="50.00",
        discount={"type": "fixed", "value": "0.00", "amount": "0.00"},
        tax_rate="0.00", tax="0.00", total="50.00")
    with pytest.raises(TypeError):
        check_invoice(rec)
