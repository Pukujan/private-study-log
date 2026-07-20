"""BUILD-03: the add-search-filter build-skill (template-injection edit skill).

Enforces the same non-negotiables as the other build-skills: the seed skill loads + validates,
rendering is template-injection (model emits ONE JSON slot; harness writes every line), the
rendered app stays py_compile-clean and App-Contract §1.1 compliant, slot validation rejects a
non-identifier field, and a scaffold+search app returns ONLY the rows whose searched field
contains the query term for a seeded db (empty q -> full list).
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


def _search_slot():
    return {"field": "name"}


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
    assert "add-search-filter" in skills
    sk = skills["add-search-filter"]
    assert sk.track == "app_build"
    ok, errors = bs.validate_skill(sk, REPO)
    assert ok is True, errors
    for c in sk.done_checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))
    assert any(c["kind"] == "filtered_results" for c in sk.done_checks)
    assert any(c["kind"] == "data_persists" for c in sk.done_checks)  # mandatory behavioral state


def test_step_prompt_leaks_no_gate_internals():
    sk = _load_real("add-search-filter")
    prompt = bs.build_step_prompt(sk, "let me search clients by name")
    assert "@hidden:" not in prompt
    assert "filtered_results" not in prompt
    assert "query_param" not in prompt


# --------------------------------------------------------------------------- #
# slot validation                                                             #
# --------------------------------------------------------------------------- #
def test_slot_validation_rejects_bad_field():
    sk = _load_real("add-search-filter")
    assert bs.validate_slot(sk, _search_slot())[0] is True
    # non-identifier field
    assert bs.validate_slot(sk, {"field": "1name"})[0] is False
    # reserved word as field
    assert bs.validate_slot(sk, {"field": "select"})[0] is False
    # injection attempt fails the identifier pattern
    assert bs.validate_slot(sk, {"field": "name; DROP TABLE clients;--"})[0] is False
    # missing required field
    assert bs.validate_slot(sk, {})[0] is False
    # unknown extra property
    assert bs.validate_slot(sk, {"field": "name", "extra": "x"})[0] is False


def test_render_rejects_unvalidated_slot(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    sk = _load_real("add-search-filter")
    with pytest.raises(bs.SlotValidationError):
        bs.render_skill(sk, {"field": "1bad"}, app_dir, workspace=REPO)


# --------------------------------------------------------------------------- #
# deterministic anchored render                                               #
# --------------------------------------------------------------------------- #
def test_render_replaces_both_anchors_and_stays_compilable(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    before = (app_dir / "app.py").read_text(encoding="utf-8")
    assert 'search_box = ""' in before                     # scaffold default: empty search box
    assert "CORTEX-SLOT:search_filter BEGIN" in before
    sk = _load_real("add-search-filter")
    bs.render_skill(sk, _search_slot(), app_dir, workspace=REPO)
    after = (app_dir / "app.py").read_text(encoding="utf-8")

    assert after != before
    assert 'search_box = ""' not in after                  # the empty default was replaced
    assert 'method="get"' in after                         # the GET search form
    assert 'name="q"' in after
    assert "WHERE name LIKE ?" in after                    # filters on the chosen column
    _contract_compliant(after)
    py_compile.compile(str(app_dir / "app.py"), doraise=True)


def test_render_uses_bound_param_not_string_formatting(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    sk = _load_real("add-search-filter")
    bs.render_skill(sk, {"field": "name"}, app_dir, workspace=REPO)
    after = (app_dir / "app.py").read_text(encoding="utf-8")
    assert "WHERE name LIKE ?" in after                    # column baked in, term is a '?' param
    assert '"%" + q + "%"' in after                        # the term rides a bound param
    assert "LIKE '%" not in after                           # never interpolated into SQL text


def test_render_is_deterministic(tmp_path):
    a = _scaffold_into(tmp_path / "a")
    b = _scaffold_into(tmp_path / "b")
    sk = _load_real("add-search-filter")
    bs.render_skill(sk, _search_slot(), a, workspace=REPO)
    bs.render_skill(sk, _search_slot(), b, workspace=REPO)
    assert (a / "app.py").read_bytes() == (b / "app.py").read_bytes()


def test_scaffold_only_default_anchors_are_noop(tmp_path):
    # With both anchors at default, a scaffold-only app keeps the unfiltered query and an empty
    # search box (no-op default anchors) -> behaviorally identical to a pre-BUILD-03 scaffold.
    app_dir = _scaffold_into(tmp_path / "app")
    text = (app_dir / "app.py").read_text(encoding="utf-8")
    assert 'search_box = ""' in text                      # empty search box default
    assert "ORDER BY id" in text                           # unfiltered list query intact
    assert "WHERE" not in text.split("CORTEX-SLOT:search_filter END")[0].split(
        "CORTEX-SLOT:search_filter BEGIN")[1]              # default filter has no WHERE clause
    py_compile.compile(str(app_dir / "app.py"), doraise=True)


# --------------------------------------------------------------------------- #
# generated done-checks target the real scaffold entity                       #
# --------------------------------------------------------------------------- #
def test_search_filter_done_checks_target_slot_entity():
    checks = bs.search_filter_done_checks(
        {"entity": "invoice",
         "fields": [{"name": "title", "type": "text", "required": True},
                    {"name": "state", "type": "text", "required": True}]},
        {"field": "title"})
    by = {c["kind"]: c for c in checks}
    fr = by["filtered_results"]
    assert fr["create"]["path"] == "/invoices"
    assert fr["search"] == {"get_path": "/", "query_param": "q"}
    assert fr["match_form"]["title"].startswith("@hidden:")   # searched field carries the token
    assert fr["nomatch_form"]["title"].startswith("@hidden:")
    assert by["data_persists"]["resource"]["table"] == "invoices"
    for c in checks:
        assert validate_check_spec(c) == [], (c, validate_check_spec(c))


# --------------------------------------------------------------------------- #
# end-to-end: the rendered search returns ONLY matching rows                   #
# --------------------------------------------------------------------------- #
def test_rendered_search_returns_only_matching_rows(tmp_path):
    app_dir = _scaffold_into(tmp_path / "app")
    sk = _load_real("add-search-filter")
    bs.render_skill(sk, _search_slot(), app_dir, workspace=REPO)  # search on `name`

    ctx = GateContext(seed=7, start_timeout_s=10.0)
    port = _alloc_port(ctx)
    db = tmp_path / "app.db"
    with AppProcess(app_dir, port, db, ctx, {}) as proc:
        matches = ["alpha_needle", "needle_beta", "gamma_needle_x"]
        others = ["zulu_row", "yankee_row", "xray_row"]
        for nm in matches + others:
            r = proc.request("POST", "/clients",
                             form={"name": nm, "paid": "0", "status": "active"})
            assert r.status < 400
        # no query -> full list (every row present)
        full = proc.request("GET", "/").body
        for nm in matches + others:
            assert nm in full
        # query -> only rows whose name contains the term
        filtered = proc.request("GET", "/?q=needle").body
    for nm in matches:
        assert nm in filtered, f"matching row {nm!r} missing from filtered result"
    for nm in others:
        assert nm not in filtered, f"non-matching row {nm!r} leaked into filtered result"
