"""BUILD-09: the add-audit-log build-skill + its load-bearing `audit_trail` gate check.

An audit trail is only trustworthy if a BROKEN log fails the gate. We drive the REAL gate over a
scaffold+audit app and assert:
  - the HONEST app PASSES the audit_trail check;
  - three mutants -- no logging, overwrite-not-append, and log-without-detail -- each FAIL with
    AUDIT_FAIL.
Plus: the skill loads/validates, the slot is parameterless, the render is deterministic + stays
py_compile-clean, and applying it adds an audit_log table + a /audit view the scaffold-only app lacks.
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
    bs.render_skill(bs.load_skills(REPO)["add-audit-log"], {}, app_dir, workspace=REPO)
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
    return bs.audit_log_done_checks(_slot())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


def test_skill_loads_and_validates():
    sk = bs.load_skills(REPO).get("add-audit-log")
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "audit_trail" for c in sk.done_checks)


def test_slot_parameterless_and_deterministic():
    sk = bs.load_skills(REPO)["add-audit-log"]
    assert bs.validate_slot(sk, {})[0] is True
    assert (_build() / "app.py").read_bytes() == (_build() / "app.py").read_bytes()


def test_audit_adds_table_and_view():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    only = (d / "app.py").read_text(encoding="utf-8")
    assert "audit_log" not in only and "/audit" not in only
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert "audit_log" in both and "/audit" in both
    assert "{{" not in both


def test_honest_audit_passes():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_no_logging_fails():
    def m(t):
        return t.replace(
            '_au.execute(\n                    "INSERT INTO audit_log (action, entity, detail) '
            'VALUES (?, ?, ?)",\n                    ("create", ENTITY, form.get(_au_f["name"], '
            '[""])[0]))', "pass")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "AUDIT_FAIL"


def test_overwrite_not_append_fails():
    def m(t):
        return t.replace('with _connect() as _au:\n                _au.execute(',
                         'with _connect() as _au:\n                _au.execute("DELETE FROM audit_log")\n'
                         '                _au.execute(')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "AUDIT_FAIL"


def test_log_without_detail_fails():
    def m(t):
        return t.replace('form.get(_au_f["name"], [""])[0]))', '"redacted"))')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "AUDIT_FAIL"


def test_audit_done_checks_target_slot_entity():
    checks = bs.audit_log_done_checks(
        {"entity": "invoice",
         "fields": [{"name": "title", "type": "text", "required": True}]})
    by = {c["kind"]: c for c in checks}
    at = by["audit_trail"]
    assert at["create"]["path"] == "/invoices"
    assert at["audit_table"] == "audit_log" and at["detail_column"] == "detail" and at["column"] == "title"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
