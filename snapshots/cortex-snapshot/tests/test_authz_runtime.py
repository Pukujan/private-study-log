"""Frozen tests for the runtime authorization labeler (`authz_runtime`) — the objective verdict for
INCORRECT authorization (CWE-863), which static AST cannot reach.

The labeler EXECUTES a handler under an adversary (authenticated non-admin, attacker-controlled
request) and a legitimate admin, and decides by whether the adversary reaches the state-changing
sink. Ground truth, no judge. These assertions pin the four canonical CWE-863 defects + controls.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evals.objective_security.authz_runtime import label_snippet, label_pair  # noqa: E402


# --- the four canonical incorrect-authz defects (vulnerable members) --------------------------
def test_denylist_role_check_is_insecure():
    code = ("@app.post('/config/update')\n"
            "def update_config():\n"
            "    if session.get('role') == 'guest':\n"
            "        abort(403)\n"
            "    conn.execute(\"UPDATE config SET value = ? WHERE key = ?\", (1, 2))\n")
    assert label_snippet(code) == "insecure"  # a non-guest 'user' slips through a deny-list


def test_or_instead_of_and_is_insecure():
    code = ("@app.post('/x')\n"
            "def delete_project():\n"
            "    if not (session.get('authenticated') or session.get('role') == 'admin'):\n"
            "        abort(403)\n"
            "    conn.execute(\"DELETE FROM projects WHERE id = ?\", (1,))\n")
    assert label_snippet(code) == "insecure"  # merely-authenticated satisfies the 'or'


def test_request_supplied_role_is_insecure():
    code = ("@app.post('/grants')\n"
            "def grant():\n"
            "    if request.json.get('role') != 'admin':\n"
            "        abort(403)\n"
            "    conn.execute(\"INSERT INTO grants (user_id) VALUES (?)\", (1,))\n")
    assert label_snippet(code) == "insecure"  # attacker claims role=admin in the body


def test_header_vs_header_compare_is_insecure():
    code = ("def do_POST(self):\n"
            "    if self.headers.get('Authorization') != self.headers.get('X-Expected-Token'):\n"
            "        self._send(403, 'x')\n"
            "        return\n"
            "    conn.execute(\"UPDATE feature_flags SET enabled = 1 WHERE name = ?\", ('f',))\n")
    assert label_snippet(code) == "insecure"  # two equal attacker headers pass the compare


# --- the secure fixes are SECURE (adversary blocked, admin allowed) ---------------------------
def test_allowlist_admin_is_secure():
    code = ("@app.post('/config/update')\n"
            "def update_config():\n"
            "    if session.get('role') != 'admin':\n"
            "        abort(403)\n"
            "    conn.execute(\"UPDATE config SET value = ? WHERE key = ?\", (1, 2))\n")
    assert label_snippet(code) == "secure"


def test_session_role_not_request_is_secure():
    code = ("@app.post('/grants')\n"
            "def grant():\n"
            "    if session.get('role') != 'admin':\n"
            "        abort(403)\n"
            "    conn.execute(\"INSERT INTO grants (user_id) VALUES (?)\", (1,))\n")
    assert label_snippet(code) == "secure"


def test_server_token_compare_is_secure():
    code = ("def do_POST(self):\n"
            "    if self.headers.get('Authorization') != ADMIN_TOKEN:\n"
            "        self._send(403, 'x')\n"
            "        return\n"
            "    conn.execute(\"UPDATE feature_flags SET enabled = 1 WHERE name = ?\", ('f',))\n")
    assert label_snippet(code) == "secure"


# --- pair verdict + controls ------------------------------------------------------------------
def test_pair_confirms_vuln_vs_secure():
    vuln = ("@app.post('/x')\ndef f():\n    if session.get('role') == 'guest':\n        abort(403)\n"
            "    conn.execute('DELETE FROM t WHERE id = ?', (1,))\n")
    secure = ("@app.post('/x')\ndef f():\n    if session.get('role') != 'admin':\n        abort(403)\n"
              "    conn.execute('DELETE FROM t WHERE id = ?', (1,))\n")
    r = label_pair(vuln, secure)
    assert r["confirmed"] and r["vuln_runtime"] == "insecure" and r["secure_runtime"] == "secure"


def test_unrunnable_snippet_is_error_not_a_false_verdict():
    assert label_snippet("def (((") == "error"


def test_deny_everyone_is_broken_not_secure():
    # aborts unconditionally -> admin can't get through either -> 'broken', never mislabeled secure
    code = ("@app.post('/x')\ndef f():\n    abort(403)\n    conn.execute('DELETE FROM t', ())\n")
    assert label_snippet(code) == "broken"
