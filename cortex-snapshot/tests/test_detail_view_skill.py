"""BUILD-11: the add-detail-view build-skill + its load-bearing `detail_view` gate check.

A detail page is only trustworthy if a broken one fails the gate. We drive the REAL gate over a
scaffold+detail app and assert:
  - the HONEST app PASSES;
  - three mutants -- leak the whole list, no-404 on a missing id, and a wrong id marker -- each FAIL
    with DETAIL_FAIL.
Plus: the skill loads/validates, the slot is parameterless, the render is deterministic, and it adds
a GET /<entity>/<id> route the scaffold-only app lacks.
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
    bs.render_skill(bs.load_skills(REPO)["add-detail-view"], {}, app_dir, workspace=REPO)
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
    return bs.detail_view_done_checks(_slot())


def _ctx():
    return GateContext(seed=9, start_timeout_s=10.0)


def test_skill_loads_and_validates():
    sk = bs.load_skills(REPO).get("add-detail-view")
    assert sk is not None and sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))
    assert any(c["kind"] == "detail_view" for c in sk.done_checks)


def test_slot_parameterless_and_deterministic():
    sk = bs.load_skills(REPO)["add-detail-view"]
    assert bs.validate_slot(sk, {})[0] is True
    assert (_build() / "app.py").read_bytes() == (_build() / "app.py").read_bytes()


def test_detail_route_added():
    d = Path(tempfile.mkdtemp()) / "app"
    bs.render_skill(bs.load_skills(REPO)["scaffold-crud-sqlite"], _slot(), d, workspace=REPO)
    assert "data-cortex-detail-id" not in (d / "app.py").read_text(encoding="utf-8")
    both = (_build() / "app.py").read_text(encoding="utf-8")
    assert "data-cortex-detail-id" in both and "{{" not in both


def test_honest_detail_passes():
    v = run_done_checks(_build(), _checks(), ctx=_ctx())
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_leak_whole_list_fails():
    def m(t):
        return t.replace(
            '_dvcells = "".join(',
            '_dvcells = "".join("<div>"+html.escape(str(_lr["name"]))+"</div>" for _lr in '
            '_connect().execute("SELECT * FROM "+TABLE).fetchall()) + "".join(')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DETAIL_FAIL"


def test_no_404_on_missing_fails():
    def m(t):
        return t.replace(
            'if _dvrow is None:\n                self._send(404, "<h1>not found</h1>")\n                return',
            'if _dvrow is None:\n                self._send(200, "<h1>record</h1>'
            '<div data-cortex-detail-id=\\"0\\"></div>")\n                return')
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DETAIL_FAIL"


def test_wrong_id_marker_fails():
    def m(t):
        return t.replace("html.escape(str(_dvid)) + '\">' + _dvcells",
                         "html.escape('0') + '\">' + _dvcells")
    v = run_done_checks(_build(m), _checks(), ctx=_ctx())
    assert not v.passed and v.failure_class == "DETAIL_FAIL"


def test_done_checks_target_slot_entity():
    checks = bs.detail_view_done_checks(
        {"entity": "invoice", "fields": [{"name": "title", "type": "text", "required": True}]})
    dv = checks[0]
    assert dv["detail_path_prefix"] == "/invoices" and dv["table"] == "invoices" and dv["column"] == "title"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
