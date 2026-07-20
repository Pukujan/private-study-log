"""BUILD-07: the add-edit-record build-skill + its load-bearing `edits_row` gate check.

An edit feature is only trustworthy if a BROKEN edit fails the gate. We drive the REAL gate over a
scaffold+edit app and assert:
  - the HONEST app PASSES the edits_row check;
  - three mutants -- a no-op edit, an UPDATE-without-WHERE (clobbers the bystander), and a
    wrong-row edit -- each FAIL with EDIT_FAIL.
Plus: the skill loads/validates, the slot is parameterless, the render is deterministic + stays
py_compile-clean, and applying it to a scaffold adds an edit endpoint the scaffold-only app lacks.
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
    bs.render_skill(bs.load_skills(REPO)["add-edit-record"], {}, app_dir, workspace=REPO)
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
    return bs.edit_done_checks(_slot())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


# --------------------------------------------------------------------------- #
# load / validate / render                                                    #
# --------------------------------------------------------------------------- #
def test_skill_loads_and_validates():
    sk = bs.load_skills(REPO).get("add-edit-record")
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "edits_row" for c in sk.done_checks)
    assert any(c["kind"] == "data_persists" for c in sk.done_checks)


def test_slot_is_parameterless_and_render_deterministic():
    sk = bs.load_skills(REPO)["add-edit-record"]
    assert bs.validate_slot(sk, {})[0] is True
    assert (_build() / "app.py").read_bytes() == (_build() / "app.py").read_bytes()


def test_edit_changes_a_scaffold_only_app():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    only = (d / "app.py").read_text(encoding="utf-8")
    assert "/edit" not in only
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert "/edit" in both and "UPDATE" in both
    assert "{{" not in both


# --------------------------------------------------------------------------- #
# mutant-integrity sweep over the REAL gate                                    #
# --------------------------------------------------------------------------- #
def test_honest_edit_passes_the_gate():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_noop_edit_fails():
    def m(t):
        return t.replace(
            'conn.execute("UPDATE {} SET {} WHERE id = ?".format(TABLE, _esets),\n'
            '                                 _evalues + [_edit_id])', "pass")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "EDIT_FAIL"


def test_update_all_rows_fails():
    # dropping the id filter clobbers the bystander row -> caught
    def m(t):
        return t.replace("UPDATE {} SET {} WHERE id = ?", "UPDATE {} SET {} WHERE id = id OR id = ?")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "EDIT_FAIL"


def test_wrong_row_edit_fails():
    def m(t):
        return t.replace("WHERE id = ?", "WHERE id = -999 AND id = ?")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "EDIT_FAIL"


# --------------------------------------------------------------------------- #
# generated done-checks target the real scaffold entity                        #
# --------------------------------------------------------------------------- #
def test_edit_done_checks_target_slot_entity():
    checks = bs.edit_done_checks(
        {"entity": "invoice",
         "fields": [{"name": "title", "type": "text", "required": True},
                    {"name": "amount", "type": "int", "required": True}]})
    by = {c["kind"]: c for c in checks}
    er = by["edits_row"]
    assert er["create"]["path"] == "/invoices"
    assert er["edit"]["path"] == "/invoices/edit"
    assert er["table"] == "invoices" and er["column"] == "title"
    # the three tokens (old / bystander / new) must be distinct
    assert len({er["create"]["form"]["title"], er["create_b"]["form"]["title"],
                er["edit"]["form"]["title"]}) == 3
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
