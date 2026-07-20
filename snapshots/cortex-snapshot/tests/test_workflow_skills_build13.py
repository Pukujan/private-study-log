"""BUILD-13..16: four workflow follow-on skills + their load-bearing deterministic gate checks.

Each gate is only trustworthy if a BROKEN fill FAILS it. For every skill we drive the REAL gate over
a scaffold + skill app and assert the HONEST app PASSES, while targeted mutants (each defeating one
distinct leg of the check) FAIL with the skill's failure class. Plus: every skill loads/validates, is
parameterless, renders deterministically, and stays py_compile-clean.

  - add-status-lifecycle -> status_lifecycle / LIFECYCLE_FAIL
  - add-soft-delete       -> soft_delete / SOFTDELETE_FAIL
  - add-ownership-assignment -> assignment / ASSIGN_FAIL
  - add-review-approval   -> review_approval / REVIEW_FAIL
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


def _ctx():
    return GateContext(seed=11, start_timeout_s=15.0)


def _build(skill_id, mutate=None) -> Path:
    app_dir = Path(tempfile.mkdtemp()) / "app"
    skills = bs.load_skills(REPO)
    bs.render_skill(skills["scaffold-crud-sqlite"], _slot(), app_dir, workspace=REPO)
    bs.render_skill(skills[skill_id], {}, app_dir, workspace=REPO)
    py_compile.compile(str(app_dir / "app.py"), doraise=True)
    if mutate is not None:
        p = app_dir / "app.py"
        t = p.read_text(encoding="utf-8")
        t2 = mutate(t)
        assert t2 != t, "mutation did not match the rendered source"
        p.write_text(t2, encoding="utf-8")
        py_compile.compile(str(p), doraise=True)
    return app_dir


# --------------------------------------------------------------------------- #
# generic skill hygiene (all four)
# --------------------------------------------------------------------------- #
_ALL = [
    ("add-status-lifecycle", "status_lifecycle", bs.status_lifecycle_done_checks),
    ("add-soft-delete", "soft_delete", bs.soft_delete_done_checks),
    ("add-ownership-assignment", "assignment", bs.assignment_done_checks),
    ("add-review-approval", "review_approval", bs.review_approval_done_checks),
]


@pytest.mark.parametrize("skill_id,kind,gen", _ALL)
def test_skill_loads_validates_and_is_parameterless(skill_id, kind, gen):
    sk = bs.load_skills(REPO).get(skill_id)
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == kind for c in sk.done_checks)
    assert any(c["kind"] == "data_persists" for c in sk.done_checks)
    assert bs.validate_slot(sk, {})[0] is True  # parameterless


@pytest.mark.parametrize("skill_id,kind,gen", _ALL)
def test_render_is_deterministic_and_marker_free(skill_id, kind, gen):
    a = (_build(skill_id) / "app.py").read_bytes()
    b = (_build(skill_id) / "app.py").read_bytes()
    assert a == b
    assert b"{{" not in a


@pytest.mark.parametrize("skill_id,kind,gen", _ALL)
def test_honest_app_passes_the_gate(skill_id, kind, gen):
    v = run_done_checks(_build(skill_id), gen(_slot()), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


@pytest.mark.parametrize("skill_id,kind,gen", _ALL)
def test_done_checks_target_slot_entity(skill_id, kind, gen):
    checks = gen({"entity": "invoice",
                  "fields": [{"name": "title", "type": "text", "required": True}]})
    feat = next(c for c in checks if c["kind"] == kind)
    assert feat["create"]["path"] == "/invoices"
    assert feat["table"] == "invoices" and feat["column"] == "title"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))


# --------------------------------------------------------------------------- #
# add-status-lifecycle mutants
# --------------------------------------------------------------------------- #
def _checks(gen):
    return gen(_slot())


def test_status_scaffold_alone_has_no_status_route():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    assert "/status" not in (d / "app.py").read_text(encoding="utf-8")


def test_status_accept_any_transition_fails():
    def m(t):
        return t.replace('if _to not in _ALLOWED.get(_cur["status"], []):', "if False:")
    v = run_done_checks(_build("add-status-lifecycle", m), _checks(bs.status_lifecycle_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "LIFECYCLE_FAIL"


def test_status_noop_transition_fails():
    def m(t):
        return t.replace('conn.execute("UPDATE " + TABLE + " SET status = ? WHERE id = ?", (_to, _sid))',
                         "_to  # no-op: never applies the transition")
    v = run_done_checks(_build("add-status-lifecycle", m), _checks(bs.status_lifecycle_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "LIFECYCLE_FAIL"


# --------------------------------------------------------------------------- #
# add-soft-delete mutants
# --------------------------------------------------------------------------- #
def test_soft_delete_hard_delete_mutant_fails():
    # archive that actually DELETEs the row -> "row still in sqlite" leg catches it
    def m(t):
        return t.replace(
            'conn.execute("UPDATE " + TABLE + " SET archived = ? WHERE id = ?", (_arch, _adid))',
            'conn.execute(("DELETE FROM " + TABLE + " WHERE id = ?") if _arch else '
            '("UPDATE " + TABLE + " SET archived = 0 WHERE id = ?"), (_adid,))')
    v = run_done_checks(_build("add-soft-delete", m), _checks(bs.soft_delete_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "SOFTDELETE_FAIL"


def test_soft_delete_noop_archive_fails():
    def m(t):
        return t.replace('_arch = 1 if path.endswith("/archive") else 0', "_arch = 0")
    v = run_done_checks(_build("add-soft-delete", m), _checks(bs.soft_delete_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "SOFTDELETE_FAIL"


def test_soft_delete_active_view_ignores_flag_fails():
    def m(t):
        return t.replace('WHERE archived = 0 ORDER BY id', 'ORDER BY id')
    v = run_done_checks(_build("add-soft-delete", m), _checks(bs.soft_delete_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "SOFTDELETE_FAIL"


# --------------------------------------------------------------------------- #
# add-ownership-assignment mutants
# --------------------------------------------------------------------------- #
def test_assignment_noop_assign_fails():
    def m(t):
        return t.replace('conn.execute("UPDATE " + TABLE + " SET assignee = ? WHERE id = ?", (_who, _asid))',
                         "_who  # no-op: never records the owner")
    v = run_done_checks(_build("add-ownership-assignment", m), _checks(bs.assignment_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "ASSIGN_FAIL"


def test_assignment_scoped_view_ignores_filter_fails():
    def m(t):
        return t.replace('WHERE assignee = ? ORDER BY id', 'WHERE assignee = ? OR 1=1 ORDER BY id')
    v = run_done_checks(_build("add-ownership-assignment", m), _checks(bs.assignment_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "ASSIGN_FAIL"


# --------------------------------------------------------------------------- #
# add-review-approval mutants
# --------------------------------------------------------------------------- #
def test_review_does_not_record_approver_fails():
    def m(t):
        return t.replace('(_NEXT[_decision], _approver, _rvid))', '(_NEXT[_decision], "", _rvid))')
    v = run_done_checks(_build("add-review-approval", m), _checks(bs.review_approval_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "REVIEW_FAIL"


def test_review_not_terminal_allows_second_decision_fails():
    def m(t):
        return t.replace('if _row["review_status"] != "pending":', "if False:")
    v = run_done_checks(_build("add-review-approval", m), _checks(bs.review_approval_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "REVIEW_FAIL"


def test_review_noop_status_fails():
    def m(t):
        return t.replace('_NEXT = {"approve": "approved", "reject": "rejected"}',
                         '_NEXT = {"approve": "pending", "reject": "pending"}')
    v = run_done_checks(_build("add-review-approval", m), _checks(bs.review_approval_done_checks), ctx=_ctx())
    assert not v.passed and v.failure_class == "REVIEW_FAIL"
