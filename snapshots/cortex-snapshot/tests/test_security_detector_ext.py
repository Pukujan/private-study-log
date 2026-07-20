"""Frozen tests for the Fable-authored, promoted security detector extension.

The extension grew the core detector 8 -> 19 vuln classes. It was objectively cross-validated
(88% on Fable's own pairs) and confirmed to have ZERO false positives on the original Stage-2C
fixtures before promotion. These tests pin that: the new classes are detected, the original
fixtures are unaffected, and detect_classes() now surfaces the extension classes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_security.detector import detect_classes  # noqa: E402
from evals.objective_security.detector_ext import detect_ext  # noqa: E402
from evals.objective_security.fixtures import fixtures  # noqa: E402


def test_ext_has_zero_false_positives_on_original_fixtures():
    # promotion safety invariant: the extension must not fire on the original SECURE fixtures
    for fx in fixtures():
        if fx["label"] == "secure":
            assert detect_ext(fx["code"]) == set(), f"{fx['id']} false-positived: {detect_ext(fx['code'])}"


def test_new_classes_detected():
    assert "xss" in detect_ext(
        "def view(request):\n    name = request.args.get('n')\n    return '<div>' + name + '</div>'\n")
    assert "tls_verify_disabled" in detect_ext("import requests\ndef f(u):\n    return requests.get(u, verify=False)\n")
    assert "flask_debug_true" in detect_ext("from flask import Flask\napp=Flask(__name__)\napp.run(debug=True)\n")
    assert "assert_for_auth" in detect_ext("def h(user):\n    assert user.is_admin\n    return 1\n")


def test_secure_forms_stay_clean():
    assert "tls_verify_disabled" not in detect_ext("import requests\ndef f(u):\n    return requests.get(u, verify=True)\n")
    assert "flask_debug_true" not in detect_ext("from flask import Flask\napp=Flask(__name__)\napp.run(debug=False)\n")


def test_core_detect_classes_now_includes_extension():
    # detect_classes (the public API) unions base + extension
    cls = detect_classes("import requests\ndef f(u):\n    return requests.get(u, verify=False)\n")
    assert "tls_verify_disabled" in cls
