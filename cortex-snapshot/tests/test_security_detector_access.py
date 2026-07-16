"""Frozen tests for the access-control detector (`detector_access`) — the objective labeler that
fills the access-control gap (CWE-306/862/639) in the injection-centric base/ext detectors.

Every seed pair must label exactly: `detect_access` flags each vulnerable snippet with its stated
class and flags NO secure snippet (precision is the point — a false positive on a secure form would
poison any gold minted from it).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evals.objective_security.detector_access import detect_access, detect_access_findings  # noqa: E402
from evals.objective_security.fixtures_access import ACCESS_PAIRS  # noqa: E402


def test_every_seed_pair_labels_exactly():
    for pair in ACCESS_PAIRS:
        got = detect_access(pair["code"])
        assert got == pair["expect"], (pair["id"], sorted(got), sorted(pair["expect"]))


def test_no_secure_snippet_is_flagged():
    for pair in ACCESS_PAIRS:
        if pair["kind"] == "secure":
            assert detect_access(pair["code"]) == set(), pair["id"]


def test_every_vuln_snippet_is_flagged():
    for pair in ACCESS_PAIRS:
        if pair["kind"] == "vuln":
            assert detect_access(pair["code"]), pair["id"]


def test_missing_auth_on_mutation():
    code = ("def do_POST(self):\n"
            "    conn.execute('UPDATE accounts SET balance = 0 WHERE id = ?', (1,))\n")
    assert "missing_auth_check" in detect_access(code)


def test_auth_gate_via_abort_clears_finding():
    code = ("@app.post('/wipe')\n"
            "def wipe():\n"
            "    if not session.get('is_admin'):\n"
            "        abort(403)\n"
            "    db.execute('DELETE FROM accounts')\n")
    assert detect_access(code) == set()


def test_findings_carry_class_line_detail():
    code = ("def do_POST(self):\n"
            "    conn.execute('DELETE FROM t WHERE id = ?', (1,))\n")
    findings = detect_access_findings(code)
    assert findings and findings[0][0] == "missing_auth_check"
    assert isinstance(findings[0][1], int) and findings[0][2]


def test_syntax_error_is_safe():
    assert detect_access("def (((") == set()
