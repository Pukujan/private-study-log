"""Frozen tests for the objective financial-statement-reconciliation checker (Stage 2 lane).

These lock the checker's verdicts on the gold fixtures: every RECONCILED fixture must be
RECONCILED, every DISCREPANCY fixture must be DISCREPANCY and must isolate exactly the one
failure class it targets. Plus targeted unit checks per failure class (so a regression names the
class it broke), a mutation-integrity check (each DISCREPANCY breaks exactly one identity), and
the Decimal vs integer-cents cross-check. Pure arithmetic — no judge anywhere in the verdict path.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_financial_statement_reconciliation.checker_financial import (  # noqa: E402
    check_financial, check_financial_intcents, FAILURE_CLASSES)
from evals.objective_financial_statement_reconciliation.fixtures_financial import (  # noqa: E402
    fixtures)


def test_all_fixtures_labeled_and_isolated():
    for fx in fixtures():
        res = check_financial(fx)
        assert res.label == fx["label"], f"{fx['id']}: expected {fx['label']}, got {res.label}"
        if fx["label"] == "DISCREPANCY":
            assert res.types == {fx["failure_class"]}, \
                f"{fx['id']}: expected only {fx['failure_class']}, got {sorted(res.types)}"
        else:
            assert not res.violations, f"{fx['id']}: RECONCILED but got {res.violations}"


def test_each_discrepancy_breaks_exactly_one_identity():
    # mutation-integrity: a DISCREPANCY fixture must trip exactly one identity check, never cascade.
    for fx in fixtures():
        if fx["label"] == "DISCREPANCY":
            res = check_financial(fx)
            assert len(res.violations) == 1, \
                f"{fx['id']}: expected exactly 1 violation, got {res.violations}"


def test_decimal_and_intcents_agree_on_every_fixture():
    for fx in fixtures():
        assert check_financial(fx).label == check_financial_intcents(fx), \
            f"{fx['id']}: Decimal and integer-cents paths disagree"


def test_label_distribution_is_stable():
    labels = [check_financial(fx).label for fx in fixtures()]
    assert labels.count("RECONCILED") == 9
    assert labels.count("DISCREPANCY") == 11


def test_every_failure_class_is_represented():
    seen = set()
    for fx in fixtures():
        seen |= check_financial(fx).types
    assert seen == set(FAILURE_CLASSES)


def test_each_failure_class_has_at_least_two_fixtures():
    from collections import Counter
    dist = Counter(fx["failure_class"] for fx in fixtures() if fx["label"] == "DISCREPANCY")
    for cls in FAILURE_CLASSES:
        assert dist[cls] >= 2, f"{cls}: only {dist[cls]} fixtures"


# ---- targeted per-failure-class units (minimal statements) ----

def _line(name, amount):
    return {"name": name, "amount": amount}


def _balanced_bs():
    # total_assets 1000 == total_liabilities 400 + total_equity 600
    return {
        "current_assets": [_line("Cash", "600.00")],
        "current_assets_subtotal": "600.00",
        "non_current_assets": [_line("PP&E", "400.00")],
        "non_current_assets_subtotal": "400.00",
        "total_assets": "1000.00",
        "current_liabilities": [_line("Accounts Payable", "100.00")],
        "current_liabilities_subtotal": "100.00",
        "non_current_liabilities": [_line("Long-term Debt", "300.00")],
        "non_current_liabilities_subtotal": "300.00",
        "total_liabilities": "400.00",
        "equity": [_line("Common Stock", "600.00")],
        "total_equity": "600.00",
    }


def _good_income():
    # gross_profit 400 == 1000 - 600 ; net_income 150 == 1000 - 600 - 200 - 50
    return {"revenue": "1000.00", "cogs": "600.00", "gross_profit": "400.00",
            "opex": "200.00", "tax": "50.00", "net_income": "150.00"}


def _rec(bs, inc, re=None):
    r = {"balance_sheet": bs, "income_statement": inc}
    if re is not None:
        r["retained_earnings"] = re
    return r


def test_reconciled_minimal():
    assert check_financial(_rec(_balanced_bs(), _good_income())).label == "RECONCILED"


def test_balance_sheet_imbalance_isolated():
    bs = _balanced_bs()
    # bump an asset leaf + its subtotal + total together: asset side stays internally summed,
    # only the fundamental equation breaks.
    bs["current_assets"] = [_line("Cash", "700.00")]
    bs["current_assets_subtotal"] = "700.00"
    bs["total_assets"] = "1100.00"   # 1100 != 400 + 600
    assert check_financial(_rec(bs, _good_income())).types == {"balance_sheet_imbalance"}


def test_subtotal_mismatch_leaf_isolated():
    bs = _balanced_bs()
    # mutate a leaf only; keep the subtotal + total, so nothing above cascades.
    bs["current_assets"] = [_line("Cash", "650.00")]  # sum 650 != stated subtotal 600
    assert check_financial(_rec(bs, _good_income())).types == {"subtotal_mismatch"}


def test_subtotal_mismatch_total_vs_subtotals_isolated():
    bs = _balanced_bs()
    # total_assets != cur+noncur subtotals, offset equity so the fundamental equation still holds.
    bs["total_assets"] = "1050.00"           # 1050 != 600 + 400 = 1000
    bs["equity"] = [_line("Common Stock", "650.00")]
    bs["total_equity"] = "650.00"            # 1050 == 400 + 650  (equation still balances)
    assert check_financial(_rec(bs, _good_income())).types == {"subtotal_mismatch"}


def test_gross_profit_error_isolated():
    inc = _good_income()
    inc["gross_profit"] = "450.00"   # should be 1000 - 600 = 400
    assert check_financial(_rec(_balanced_bs(), inc)).types == {"gross_profit_error"}


def test_income_calc_error_isolated():
    inc = _good_income()
    inc["net_income"] = "120.00"     # should be 1000 - 600 - 200 - 50 = 150
    assert check_financial(_rec(_balanced_bs(), inc)).types == {"income_calc_error"}


def test_retained_earnings_tie_error_isolated():
    re = {"prior": "500.00", "dividends": "0.00", "ending": "600.00"}
    # ending 600 != prior 500 + net_income 150 - dividends 0 = 650
    assert check_financial(_rec(_balanced_bs(), _good_income(), re)).types == \
        {"retained_earnings_tie_error"}


def test_retained_earnings_tie_reconciled_with_net_loss():
    inc = _good_income()
    inc["revenue"] = "600.00"        # net_income = 600 - 600 - 200 - 50 = -250, gross_profit = 0
    inc["gross_profit"] = "0.00"
    inc["net_income"] = "-250.00"
    re = {"prior": "300.00", "dividends": "0.00", "ending": "50.00"}  # 300 + (-250) - 0 = 50
    assert check_financial(_rec(_balanced_bs(), inc, re)).label == "RECONCILED"


def test_optional_retained_earnings_section_absent_is_fine():
    res = check_financial(_rec(_balanced_bs(), _good_income()))   # no retained_earnings key
    assert res.label == "RECONCILED" and res.violations == []


def test_float_money_rejected_balance_sheet():
    import pytest
    bs = _balanced_bs()
    bs["current_assets"] = [_line("Cash", 600.0)]   # float amount
    with pytest.raises(TypeError):
        check_financial(_rec(bs, _good_income()))


def test_float_money_rejected_income_statement():
    import pytest
    inc = _good_income()
    inc["net_income"] = 150.0   # float
    with pytest.raises(TypeError):
        check_financial(_rec(_balanced_bs(), inc))
