"""BUILD-12: the add-second-entity-relation build-skill + its `relation_integrity` gate check.

A relation is only trustworthy if a BROKEN one fails the gate. We drive the REAL gate over a
scaffold(parent)+relation(child) app and assert:
  - the HONEST app PASSES;
  - three mutants -- no FK check (accepts a bogus parent), dropped join (parent not shown), and a
    broken child insert (child not created) -- each FAIL with RELATION_FAIL.
Plus: the skill loads/validates, the child slot requires a text field, the render is deterministic +
py_compile-clean, and applying it adds the child table + FK + join view the scaffold-only app lacks.
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


def _parent():
    return {"entity": "client",
            "fields": [{"name": "name", "type": "text", "required": True},
                       {"name": "paid", "type": "bool", "required": True}]}


def _child():
    return {"entity": "order",
            "fields": [{"name": "item", "type": "text", "required": True},
                       {"name": "qty", "type": "int", "required": True}]}


def _build(mutate=None) -> Path:
    app_dir = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _parent(), app_dir, workspace=REPO)
    bs.render_skill(bs.load_skills(REPO)["add-second-entity-relation"], _child(), app_dir, workspace=REPO)
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
    return bs.relation_done_checks(_parent(), _child())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


def test_skill_loads_and_validates():
    sk = bs.load_skills(REPO).get("add-second-entity-relation")
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "relation_integrity" for c in sk.done_checks)


def test_child_slot_requires_a_text_field():
    sk = bs.load_skills(REPO)["add-second-entity-relation"]
    no_text = {"entity": "order", "fields": [{"name": "qty", "type": "int", "required": True}]}
    assert bs.validate_slot(sk, no_text)[0] is False
    assert bs.validate_slot(sk, _child())[0] is True


def test_render_deterministic_and_adds_child():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _parent(), d, workspace=REPO)
    only = (d / "app.py").read_text(encoding="utf-8")
    assert "CHILD_TABLE" in only and 'CHILD_TABLE = ""' in only  # scaffold default: empty
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert '"orders"' in both and "FOREIGN KEY" in both and "{{" not in both
    assert (_build() / "app.py").read_bytes() == (_build() / "app.py").read_bytes()


def test_honest_relation_passes():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_no_fk_check_accepts_bogus_parent_fails():
    def m(t):
        return t.replace('if not conn.execute("SELECT 1 FROM " + TABLE + " WHERE id = ?", (_fk,)).fetchone():',
                         "if False:")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "RELATION_FAIL"


def test_dropped_join_hides_parent_fails():
    def m(t):
        return t.replace('"SELECT c.*, p." + _ptf + " AS _parent FROM " + CHILD_TABLE + " c "',
                         '"SELECT c.*, \'\' AS _parent FROM " + CHILD_TABLE + " c "') \
                .replace('+ "LEFT JOIN " + TABLE + " p ON c." + FK_COL + " = p.id ORDER BY c.id"',
                         '+ "ORDER BY c.id"')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "RELATION_FAIL"


def test_broken_child_insert_fails():
    def m(t):
        return t.replace('conn.execute("INSERT INTO " + CHILD_TABLE + " (" + _cnames + ", " + FK_COL\n'
                         '                             + ") VALUES (" + _cmarks + ", ?)", _cvals + [_fk])',
                         "pass")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "RELATION_FAIL"


def test_done_checks_target_entities():
    checks = bs.relation_done_checks(
        {"entity": "invoice", "fields": [{"name": "title", "type": "text", "required": True}]},
        {"entity": "lineitem", "fields": [{"name": "sku", "type": "text", "required": True}]})
    ri = checks[0]
    assert ri["parent_table"] == "invoices" and ri["child_create"]["path"] == "/lineitems"
    assert ri["child_fk_param"] == "invoice_id" and ri["child_column"] == "sku"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
