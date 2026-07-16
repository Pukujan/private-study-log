"""BUILD-02: the add-summary-metric build-skill (template-injection edit skill).

Enforces the same non-negotiables as the other build-skills: the seed skill loads + validates,
rendering is template-injection (model emits ONE JSON slot; harness writes every line), the
rendered app stays py_compile-clean and App-Contract §1.1 compliant, slot validation rejects bad
field/op/label, and a scaffold+metric app renders a `data-cortex-metric` card with the CORRECT
filtered count for a seeded db.
"""
from __future__ import annotations

import py_compile
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import build_skills as bs  # noqa: E402
from cortex_core.app_contract import validate_check_spec  # noqa: E402
from cortex_core.app_gates import AppProcess, GateContext, _alloc_port  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ambient_workspace(monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)


def _load_real(skill_id: str) -> "bs.BuildSkill":
    return bs.load_skills(REPO)[skill_id]


def _client_slot():
    return {"entity": "client",
            "fields": [{"name": "name", "type": "text", "required": True},
                       {"name": "paid", "type": "bool", "required": True},
                       {"name": "status", "type": "text", "required": True}]}


def _metric_slot():
    return {"label": "Late clients", "field": "status", "op": "eq", "value": "late"}


def _scaffold_into(app_dir: Path) -> Path:
    sk = _load_real("scaffold-crud-sqlite")
    bs.render_skill(sk, _client_slot(), app_dir, workspace=REPO)
    return app_dir


def _contract_compliant(text: str):
    assert "--port" in text and "--db" in text
    assert "CORTEX_APP_READY" in text
    assert "sqlite3" in text
    assert ":memory:" not in text
    assert "{{" not in text  # no unresolved template markers


# --------------------------------------------------------------------------- #
# load / validate                                                             #
# --------------------------------------------------------------------------- #
def test_seed_skill_loads_and_validates():
    skills = bs.load_skills(REPO)
    assert "add-summary-metric" in skills
    sk = skills["add-summary-metric"]
    assert sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    # every declared done_check lints clean; the derived_value one is present
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
    assert any(c["kind"] == "derived_value" for c in sk.done_checks)
    assert any(c["kind"] == "data_persists" for c in sk.done_checks)  # mandatory behavioral state


def test_step_prompt_leaks_no_gate_internals():
    sk = _load_real("add-summary-metric")
    prompt = bs.build_step_prompt(sk, "how many clients are late")
    assert "@hidden:" not in prompt
    assert "data-cortex-metric" not in prompt
    assert "derived_value" not in prompt


# --------------------------------------------------------------------------- #
# slot validation                                                             #
# --------------------------------------------------------------------------- #
def test_slot_validation_rejects_bad_field_op_label():
    sk = _load_real("add-summary-metric")
    good = _metric_slot()
    assert bs.validate_slot(sk, good)[0] is True

    # non-identifier field
    assert bs.validate_slot(sk, {**good, "field": "1status"})[0] is False
    # reserved word as field
    assert bs.validate_slot(sk, {**good, "field": "select"})[0] is False
    # op outside the enum
    assert bs.validate_slot(sk, {**good, "op": "contains"})[0] is False
    # over-long label (>40)
    assert bs.validate_slot(sk, {**good, "label": "x" * 41})[0] is False
    # over-long value (>40)
    assert bs.validate_slot(sk, {**good, "value": "y" * 41})[0] is False
    # injection attempt in field fails the identifier pattern
    assert bs.validate_slot(sk, {**good, "field": "status; DROP TABLE clients;--"})[0] is False


def test_render_rejects_unvalidated_slot(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    sk = _load_real("add-summary-metric")
    with pytest.raises(bs.SlotValidationError):
        bs.render_skill(sk, {**_metric_slot(), "op": "contains"}, app_dir, workspace=REPO)


# --------------------------------------------------------------------------- #
# deterministic anchored render                                               #
# --------------------------------------------------------------------------- #
def test_render_replaces_metrics_anchor_and_stays_compilable(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    before = (app_dir / "app.py").read_text(encoding="utf-8")
    assert 'metrics_html = ""' in before  # scaffold default is the empty metric
    sk = _load_real("add-summary-metric")
    bs.render_skill(sk, _metric_slot(), app_dir, workspace=REPO)
    after = (app_dir / "app.py").read_text(encoding="utf-8")

    assert after != before
    assert 'metrics_html = ""' not in after       # the empty default was replaced
    assert "data-cortex-metric" in after          # the machine-readable metric attribute
    assert "SELECT COUNT(*)" in after
    assert "status" in after                       # the predicate field
    _contract_compliant(after)
    py_compile.compile(str(app_dir / "app.py"), doraise=True)


def test_render_uses_bound_param_not_string_formatting(tmp_path):
    # the value must ride a bound param `?`, never be formatted into the SQL text
    app_dir = _scaffold_into(tmp_path / "app")
    sk = _load_real("add-summary-metric")
    bs.render_skill(sk, {"label": "L", "field": "status", "op": "eq", "value": "late"},
                    app_dir, workspace=REPO)
    after = (app_dir / "app.py").read_text(encoding="utf-8")
    assert "WHERE status = ?" in after            # op from enum map, value is a '?' param
    assert "WHERE status = 'late'" not in after   # value NOT interpolated into SQL


def test_render_is_deterministic(tmp_path):
    a = _scaffold_into(tmp_path / "a")
    b = _scaffold_into(tmp_path / "b")
    sk = _load_real("add-summary-metric")
    bs.render_skill(sk, _metric_slot(), a, workspace=REPO)
    bs.render_skill(sk, _metric_slot(), b, workspace=REPO)
    assert (a / "app.py").read_bytes() == (b / "app.py").read_bytes()


# --------------------------------------------------------------------------- #
# generated done-checks target the real scaffold entity                       #
# --------------------------------------------------------------------------- #
def test_summary_metric_done_checks_target_slot_entity():
    checks = bs.summary_metric_done_checks(
        {"entity": "invoice",
         "fields": [{"name": "title", "type": "text", "required": True},
                    {"name": "state", "type": "text", "required": True}]},
        {"label": "Overdue", "field": "state", "op": "eq", "value": "overdue"})
    by = {c["kind"]: c for c in checks}
    dv = by["derived_value"]
    assert dv["create"]["path"] == "/invoices"
    assert dv["match_form"]["state"] == "overdue"          # satisfies predicate
    assert dv["nomatch_form"]["state"] != "overdue"        # does not
    assert dv["marker_attr"] == "data-cortex-metric"
    assert by["data_persists"]["resource"]["table"] == "invoices"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))


# --------------------------------------------------------------------------- #
# end-to-end: the rendered metric card shows the CORRECT filtered count        #
# --------------------------------------------------------------------------- #
def _metric_int(body: str) -> int | None:
    m = re.search(r'data-cortex-metric="(-?\d+)"', body)
    return int(m.group(1)) if m else None


def test_metric_on_only_text_field_passes_gate(tmp_path):
    """Regression (live-battery find, 2026-07-10): when the metric predicate is on the ONLY text
    field (e.g. expenses = amount:int + category:text, count where category='food'), the derived_value
    check's per-row token collides with the predicate field. The seeder must preserve the predicate
    and uniquify the spare INT field numerically -- NOT mint a hex token into `amount` (int('cx..')
    -> 400 -> ENV_FAIL). Drives the REAL gate over the real skill-generated checks; must PASS."""
    from cortex_core.app_gates import run_done_checks

    s_slot = {"entity": "expense",
              "fields": [{"name": "amount", "type": "int", "required": True},
                         {"name": "category", "type": "text", "required": True}]}
    m_slot = {"label": "Food expenses", "field": "category", "op": "eq", "value": "food"}
    app_dir = tmp_path / "app"
    bs.render_skill(_load_real("scaffold-crud-sqlite"), s_slot, app_dir, workspace=REPO)
    bs.render_skill(_load_real("add-summary-metric"), m_slot, app_dir, workspace=REPO)

    checks = bs.summary_metric_done_checks(s_slot, m_slot)
    dv = next(c for c in checks if c["kind"] == "derived_value")
    assert dv["predicate_field"] == "category"          # the seeder must know what to preserve

    v = run_done_checks(app_dir, checks, ctx=GateContext(seed=11, start_timeout_s=10.0))
    assert v.passed, (v.failure_class, [r.detail for r in v.results if not r.passed])


def test_rendered_metric_card_shows_correct_filtered_count(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    sk = _load_real("add-summary-metric")
    bs.render_skill(sk, _metric_slot(), app_dir, workspace=REPO)  # count where status == 'late'

    ctx = GateContext(seed=7, start_timeout_s=10.0)
    port = _alloc_port(ctx)
    db = tmp_path / "app.db"
    with AppProcess(app_dir, port, db, ctx, {}) as proc:
        empty = _metric_int(proc.request("GET", "/").body)
        assert empty == 0                                   # nothing matches yet
        for i in range(3):
            r = proc.request("POST", "/clients",
                             form={"name": f"late{i}", "paid": "0", "status": "late"})
            assert r.status < 400
        for i in range(2):
            r = proc.request("POST", "/clients",
                             form={"name": f"paid{i}", "paid": "1", "status": "paid"})
            assert r.status < 400
        body = proc.request("GET", "/").body
    assert _metric_int(body) == 3                            # exactly the 3 'late' rows
    assert "Late clients: 3" in body                         # human-readable label + count
