"""Frozen checker tests for the Stage-2F tenant-isolation lane.

These lock the behavior of BOTH deterministic signals — the RUNTIME labeler (the authority) and
the STATIC AST detector (the cross-check). Zero judges are involved in any verdict here.

  1. The runtime oracle must reproduce every authored ISOLATED/LEAKY label (0 quarantine).
  2. The static detector must match the runtime verdict on every fixture EXCEPT the two
     deliberately-authored disagreement cases (a false-positive on an app-layer filter, a
     false-negative on a tautology filter). Those two are asserted explicitly so a regression
     that "fixes" or worsens them is caught.
  3. Targeted per-rule units so a regression names the rule it broke.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_tenant_isolation import runtime_tenant as RT  # noqa: E402
from evals.objective_tenant_isolation.detector_tenant import (  # noqa: E402
    detect_reasons,
    static_verdict,
)
from evals.objective_tenant_isolation.fixtures_tenant import fixtures  # noqa: E402

# The only fixtures where the static detector is expected to disagree with runtime/authored.
_STATIC_DISAGREE = {"post_query_python_filter__isolated", "tautology_or_1eq1__leaky"}


# ---- 1. Runtime oracle reproduces every authored label ---------------------------------------

def test_runtime_labels_match_authored():
    for fx in fixtures():
        rt = RT.label(fx["code"], fx["op"])
        assert rt.verdict == fx["label"], (
            f"{fx['id']}: runtime said {rt.verdict}, authored {fx['label']}")


def test_no_broken_handlers():
    for fx in fixtures():
        rt = RT.label(fx["code"], fx["op"])
        assert rt.functional, f"{fx['id']}: positive control failed (handler broken)"


# ---- 2. Static detector agrees with runtime except on the two documented cases --------------

def test_static_matches_runtime_except_documented():
    for fx in fixtures():
        rt = RT.label(fx["code"], fx["op"])
        static = static_verdict(fx["code"])
        if fx["id"] in _STATIC_DISAGREE:
            assert static != rt.verdict, f"{fx['id']}: expected a static disagreement"
        else:
            assert static == rt.verdict, (
                f"{fx['id']}: static {static} != runtime {rt.verdict}")


def test_static_agreement_rate_is_16_of_18():
    total = agree = 0
    for fx in fixtures():
        rt = RT.label(fx["code"], fx["op"])
        total += 1
        agree += int(static_verdict(fx["code"]) == rt.verdict)
    assert total == 18
    assert agree == 16


def test_app_layer_filter_is_a_static_false_positive():
    # runtime confirms ISOLATED; static over-flags a missing SQL predicate
    fx = next(f for f in fixtures() if f["id"] == "post_query_python_filter__isolated")
    assert RT.label(fx["code"], fx["op"]).verdict == "ISOLATED"
    assert "missing_tenant_predicate" in detect_reasons(fx["code"])


def test_tautology_is_a_static_false_negative():
    # runtime confirms LEAKY; static is fooled by the session-sourced tenant predicate
    fx = next(f for f in fixtures() if f["id"] == "tautology_or_1eq1__leaky")
    assert RT.label(fx["code"], fx["op"]).verdict == "LEAKY"
    assert static_verdict(fx["code"]) == "ISOLATED"


# ---- 3. Per-rule static units ----------------------------------------------------------------

def test_missing_tenant_predicate_flagged():
    code = ("def handler(conn, session, params):\n"
            "    return conn.execute('SELECT id, tenant_id FROM documents WHERE id = ?',\n"
            "                        (params['id'],)).fetchall()\n")
    assert "missing_tenant_predicate" in detect_reasons(code)


def test_session_scoped_query_is_clean():
    code = ("def handler(conn, session, params):\n"
            "    return conn.execute('SELECT id FROM documents WHERE id = ? AND tenant_id = ?',\n"
            "                        (params['id'], session['tenant_id'])).fetchall()\n")
    assert static_verdict(code) == "ISOLATED"


def test_tenant_from_client_flagged():
    code = ("def handler(conn, session, params):\n"
            "    return conn.execute('SELECT id FROM documents WHERE id = ? AND tenant_id = ?',\n"
            "                        (params['id'], params['tenant_id'])).fetchall()\n")
    assert "tenant_from_client" in detect_reasons(code)


def test_raw_string_sql_flagged():
    code = ("def handler(conn, session, params):\n"
            "    q = f\"SELECT id FROM documents WHERE id = {params['id']}\"\n"
            "    return conn.execute(q).fetchall()\n")
    reasons = detect_reasons(code)
    assert "raw_string_sql" in reasons
    assert "missing_tenant_predicate" in reasons


def test_admin_list_no_where_flagged():
    code = ("def handler(conn, session, params):\n"
            "    return conn.execute('SELECT id, tenant_id, body FROM documents').fetchall()\n")
    assert "missing_tenant_predicate" in detect_reasons(code)


def test_non_tenant_table_ignored():
    # a query on a table outside TENANT_TABLES is out of scope -> clean
    code = ("def handler(conn, session, params):\n"
            "    return conn.execute('SELECT * FROM audit_log WHERE id = ?',\n"
            "                        (params['id'],)).fetchall()\n")
    assert static_verdict(code) == "ISOLATED"
