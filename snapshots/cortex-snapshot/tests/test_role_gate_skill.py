"""BUILD-08: the add-role-gate build-skill + its load-bearing `auth_required` gate check.

An auth boundary is only trustworthy if a BROKEN guard fails the gate. We drive the REAL gate over a
scaffold+role-gate app and assert:
  - the HONEST app PASSES the auth_required check;
  - three mutants -- no auth check (leaks unauthenticated), always-401 (never serves), and
    accept-any-nonempty-token (doesn't check the value) -- each FAIL with AUTH_FAIL.
Plus: the skill loads/validates, the slot is parameterless, the render is deterministic + stays
py_compile-clean, and applying it adds a protected /admin/export route the scaffold-only app lacks.
"""
from __future__ import annotations

import py_compile
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import build_skills as bs  # noqa: E402
from cortex_core.app_contract import validate_check_spec  # noqa: E402
from cortex_core.app_gates import GateContext, run_done_checks  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ambient_workspace(monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)


def _slot():
    return {"entity": "client",
            "fields": [{"name": "name", "type": "text", "required": True},
                       {"name": "paid", "type": "bool", "required": True}]}


def _build(mutate=None) -> Path:
    app_dir = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), app_dir, workspace=REPO)
    bs.render_skill(bs.load_skills(REPO)["add-role-gate"], {}, app_dir, workspace=REPO)
    py_compile.compile(str(app_dir / "app.py"), doraise=True)
    if mutate is not None:
        p = app_dir / "app.py"
        t = p.read_text(encoding="utf-8")
        t2 = mutate(t)
        assert t2 != t, "mutation did not match the rendered source"
        p.write_text(t2, encoding="utf-8")
        py_compile.compile(str(p), doraise=True)
    return app_dir


def _checks():
    return bs.role_gate_done_checks(_slot())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


def test_skill_loads_and_validates():
    sk = bs.load_skills(REPO).get("add-role-gate")
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "auth_required" for c in sk.done_checks)
    assert any(c["kind"] == "data_persists" for c in sk.done_checks)


def test_slot_is_parameterless_and_render_deterministic():
    sk = bs.load_skills(REPO)["add-role-gate"]
    assert bs.validate_slot(sk, {})[0] is True
    assert (_build() / "app.py").read_bytes() == (_build() / "app.py").read_bytes()


def test_role_gate_adds_a_protected_route():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    only = (d / "app.py").read_text(encoding="utf-8")
    assert "/admin/export" not in only
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert "/admin/export" in both and "X-Admin-Token" in both
    assert "{{" not in both


def test_honest_role_gate_passes_the_gate():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_no_auth_check_leaks_and_fails():
    def m(t):
        return t.replace('if self.headers.get("X-Admin-Token") != ADMIN_TOKEN:', "if False:")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "AUTH_FAIL"


def test_always_deny_fails():
    def m(t):
        return t.replace('if self.headers.get("X-Admin-Token") != ADMIN_TOKEN:', "if True:")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "AUTH_FAIL"


def test_accepts_any_nonempty_token_fails():
    # weak guard that checks presence but not the value -> the wrong-token leg catches it
    def m(t):
        return t.replace('if self.headers.get("X-Admin-Token") != ADMIN_TOKEN:',
                         'if not self.headers.get("X-Admin-Token"):')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "AUTH_FAIL"


def test_no_hardcoded_admin_token_in_output_cwe_798():
    """Regression (security-detector-as-eval find, 2026-07-11): the rendered app must NOT bake a
    hardcoded admin-token literal (CWE-798). It reads APP_ADMIN_TOKEN from the environment (matching
    cortex_core/authz.py) and generates a random one if unset. Our own static detector must not flag
    `hardcoded_secret` on the harness output."""
    from evals.objective_security.detector import detect_classes
    app_dir = _build()
    src = (app_dir / "app.py").read_text(encoding="utf-8")
    assert 'ADMIN_TOKEN = "cortex-admin-token"' not in src
    assert "os.environ.get(\"APP_ADMIN_TOKEN\")" in src
    assert "hardcoded_secret" not in detect_classes(src)


def test_role_gate_done_checks_target_slot_entity():
    checks = bs.role_gate_done_checks(
        {"entity": "invoice",
         "fields": [{"name": "title", "type": "text", "required": True},
                    {"name": "amount", "type": "int", "required": True}]})
    by = {c["kind"]: c for c in checks}
    ar = by["auth_required"]
    assert ar["protected_path"] == "/admin/export"
    assert ar["auth_header"] == "X-Admin-Token" and ar["auth_value"]
    assert ar["create"]["path"] == "/invoices"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
