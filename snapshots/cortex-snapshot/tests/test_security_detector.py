"""Frozen tests for the Stage-2C defensive AST vulnerability detector.

Every vulnerable fixture must be flagged; every secure fixture must be clean. Plus targeted
unit checks per class so a regression names the class it broke.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_security.detector import detect_classes  # noqa: E402
from evals.objective_security.fixtures import fixtures  # noqa: E402


def test_all_fixtures_labeled_correctly():
    for fx in fixtures():
        cls = detect_classes(fx["code"])
        if fx["label"] == "vulnerable":
            assert cls, f"{fx['id']} should be flagged, got clean"
        else:
            assert not cls, f"{fx['id']} should be clean, got {cls}"


def test_sql_injection_via_variable():
    code = "def q(c, uid):\n    s = f'SELECT * FROM t WHERE id={uid}'\n    return c.execute(s)\n"
    assert "sql_injection" in detect_classes(code)


def test_parameterized_sql_is_clean():
    code = "def q(c, uid):\n    return c.execute('SELECT * FROM t WHERE id=?', (uid,))\n"
    assert "sql_injection" not in detect_classes(code)


def test_shell_true_dynamic_flagged():
    code = "import subprocess\ndef r(h):\n    return subprocess.call('ping '+h, shell=True)\n"
    assert "command_injection" in detect_classes(code)


def test_pickle_loads_flagged():
    assert "insecure_deserialization" in detect_classes("import pickle\ndef f(b):\n    return pickle.loads(b)\n")


def test_eval_dynamic_flagged_literal_clean():
    assert "dangerous_eval" in detect_classes("def f(e):\n    return eval(e)\n")
    assert "dangerous_eval" not in detect_classes("def f():\n    return eval('1+1')\n")


def test_hardcoded_secret_flagged():
    assert "hardcoded_secret" in detect_classes("password = 'abc123prod'\n")
    assert "hardcoded_secret" not in detect_classes("import os\npassword = os.environ['P']\n")


def test_weak_hash_flagged():
    assert "weak_hash" in detect_classes("import hashlib\nh=hashlib.md5(b'x')\n")


def test_ssrf_guarded_is_clean():
    vuln = "import requests\ndef f(u):\n    return requests.get(u)\n"
    safe = ("import requests\nfrom urllib.parse import urlparse\nALLOWED_HOSTS={'a'}\n"
            "def f(u):\n    if urlparse(u).hostname not in ALLOWED_HOSTS:\n        raise ValueError()\n"
            "    return requests.get(u)\n")
    assert "ssrf" in detect_classes(vuln)
    assert "ssrf" not in detect_classes(safe)
