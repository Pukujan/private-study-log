"""BUILD-10: the add-dashboard build-skill + its load-bearing `dashboard_metrics` gate check.

A dashboard is only trustworthy if a WRONG number fails the gate. We drive the REAL gate over a
scaffold+dashboard app and assert:
  - the HONEST app PASSES;
  - three mutants -- a hardcoded total, a count-everything card, and a hardcoded card -- each FAIL
    with DASHBOARD_FAIL.
Plus: the skill loads/validates, the slot is parameterless, the render is deterministic, and it adds
a /dashboard route the scaffold-only app lacks, with one card per boolean field.
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
                       {"name": "paid", "type": "bool", "required": True},
                       {"name": "active", "type": "bool", "required": True}]}


def _build(mutate=None) -> Path:
    app_dir = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), app_dir, workspace=REPO)
    bs.render_skill(bs.load_skills(REPO)["add-dashboard"], {}, app_dir, workspace=REPO)
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
    return bs.dashboard_done_checks(_slot())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


def test_skill_loads_and_validates():
    sk = bs.load_skills(REPO).get("add-dashboard")
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "dashboard_metrics" for c in sk.done_checks)


def test_one_card_per_bool_field():
    checks = _checks()
    cards = checks[0]["cards"]
    assert {c["marker_attr"] for c in cards} == {"data-cortex-dash-paid", "data-cortex-dash-active"}


def test_slot_parameterless_and_deterministic():
    sk = bs.load_skills(REPO)["add-dashboard"]
    assert bs.validate_slot(sk, {})[0] is True
    assert (_build() / "app.py").read_bytes() == (_build() / "app.py").read_bytes()


def test_dashboard_route_added():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    assert "/dashboard" not in (d / "app.py").read_text(encoding="utf-8")
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert "/dashboard" in both and "data-cortex-dash-total" in both and "{{" not in both


def test_honest_dashboard_passes():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_hardcoded_total_fails():
    def m(t):
        return t.replace('_dtotal = _dconn.execute("SELECT COUNT(*) FROM " + TABLE).fetchone()[0]',
                         "_dtotal = 42")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DASHBOARD_FAIL"


def test_count_everything_card_fails():
    def m(t):
        return t.replace('" WHERE " + _bn + " = 1"', '" WHERE 1=1"')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DASHBOARD_FAIL"


def test_hardcoded_card_fails():
    def m(t):
        return t.replace('+ _bn + " = 1"\n                        ).fetchone()[0]',
                         '+ _bn + " = 1"\n                        ).fetchone()[0]; _bc = 7')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DASHBOARD_FAIL"


def test_scaffold_with_no_bool_fields_still_valid():
    slot = {"entity": "note", "fields": [{"name": "title", "type": "text", "required": True}]}
    checks = bs.dashboard_done_checks(slot)
    assert checks[0]["cards"] == []           # no bool fields -> just the total card
    assert validate_check_spec(checks[0]) == []
