"""BUILD-06: the add-delete-with-confirm build-skill + its load-bearing `deletes_row` gate check.

A delete feature is only trustworthy if a BROKEN delete fails the gate. We drive the REAL gate
(`cortex_core.app_gates.run_done_checks`) over a scaffold+delete app and assert:
  - the HONEST app PASSES the deletes_row check;
  - three mutants -- a delete that ignores the confirmation guard, a no-op delete, and a
    wrong-key delete -- each FAIL with DELETE_FAIL.
Plus: the skill loads/validates, the slot is parameterless, the render is deterministic and stays
py_compile-clean + App-Contract compliant, and applying it to a scaffold changes the app (the
scaffold-only render never carries a delete endpoint).
"""
from __future__ import annotations

import py_compile
import sys
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
    """Render scaffold + delete into a temp dir; optionally mutate the app.py. Always py_compile."""
    import tempfile
    app_dir = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), app_dir, workspace=REPO)
    bs.render_skill(bs.load_skills(REPO)["add-delete-with-confirm"], {}, app_dir, workspace=REPO)
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
    return bs.delete_done_checks(_slot())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


# --------------------------------------------------------------------------- #
# load / validate / render                                                    #
# --------------------------------------------------------------------------- #
def test_skill_loads_and_validates():
    skills = bs.load_skills(REPO)
    assert "add-delete-with-confirm" in skills
    sk = skills["add-delete-with-confirm"]
    assert sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "deletes_row" for c in sk.done_checks)
    assert any(c["kind"] == "data_persists" for c in sk.done_checks)  # mandatory behavioral state


def test_slot_is_parameterless_and_render_deterministic(tmp_path):
    sk = bs.load_skills(REPO)["add-delete-with-confirm"]
    # tolerant slot: an empty object validates
    assert bs.validate_slot(sk, {})[0] is True
    a = _build()
    b = _build()
    assert (a / "app.py").read_bytes() == (b / "app.py").read_bytes()


def test_delete_changes_a_scaffold_only_app():
    # render scaffold alone vs scaffold+delete; the delete endpoint must be absent from scaffold-only
    import tempfile
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    only = (d / "app.py").read_text(encoding="utf-8")
    assert "/delete" not in only and 'name="confirm"' not in only
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert "/delete" in both and 'name="confirm"' in both
    assert "{{" not in both  # no unresolved template markers


# --------------------------------------------------------------------------- #
# mutant-integrity sweep over the REAL gate                                    #
# --------------------------------------------------------------------------- #
def test_honest_delete_passes_the_gate():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_delete_without_confirm_guard_fails():
    # guard removed -> an unconfirmed delete now succeeds -> the guard leg must catch it
    def m(t):
        return t.replace('if (dform.get("confirm") or [""])[0] != "yes":', "if False:")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DELETE_FAIL"


def test_noop_delete_fails():
    def m(t):
        return t.replace(
            'conn.execute("DELETE FROM {} WHERE id = ?".format(TABLE), (_del_id,))', "pass")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DELETE_FAIL"


def test_wrong_key_delete_fails():
    def m(t):
        return t.replace("WHERE id = ?", "WHERE id = -999 AND id = ?")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DELETE_FAIL"


# --------------------------------------------------------------------------- #
# generated done-checks target the real scaffold entity                        #
# --------------------------------------------------------------------------- #
def test_delete_done_checks_target_slot_entity():
    checks = bs.delete_done_checks(
        {"entity": "invoice",
         "fields": [{"name": "title", "type": "text", "required": True},
                    {"name": "amount", "type": "int", "required": True}]})
    by = {c["kind"]: c for c in checks}
    dr = by["deletes_row"]
    assert dr["create"]["path"] == "/invoices"
    assert dr["delete"]["path"] == "/invoices/delete"
    assert dr["table"] == "invoices" and dr["column"] == "title"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
