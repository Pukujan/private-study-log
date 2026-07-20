"""Phase 4 gate 4.1: approach contract schema, validation, corpus-prefill."""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core import contract as c


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    shard = ws / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "widgets.md").write_text(
        "# Widget subsystem\n\nThe widget assembly and its calibration jig.\n",
        encoding="utf-8",
    )
    return ws


def _valid_contract(ws: Path) -> c.Contract:
    return c.Contract(
        contract_id="test-1",
        task="fix the widget calibration bug",
        task_type="bugfix",
        evidence_refs=["docs/cortex-1/widgets.md"],
        planned_approach="adjust the jig tolerance",
        acceptance_criteria=["calibration passes"],
        verification_steps=["run the calibration test"],
        model="sonnet",
        role="builder",
        created_at="2026-07-04T00:00:00Z",
    )


def test_valid_contract_passes(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    ok, errors = c.validate_contract(_valid_contract(ws), ws)
    assert ok, errors


def test_evidence_ref_that_does_not_resolve_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = _valid_contract(ws)
    con = c.Contract(**{**con.to_dict(), "evidence_refs": ["docs/nonexistent.md"]})
    ok, errors = c.validate_contract(con, ws)
    assert not ok
    assert any("does not resolve" in e for e in errors)


def test_evidence_ref_escaping_workspace_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = c.Contract(**{**_valid_contract(ws).to_dict(), "evidence_refs": ["../secret.md"]})
    ok, errors = c.validate_contract(con, ws)
    assert not ok
    assert any("escapes the workspace" in e for e in errors)


def test_missing_required_fields_fail_for_non_explore(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = c.Contract(**{
        **_valid_contract(ws).to_dict(),
        "planned_approach": "",
        "acceptance_criteria": [],
        "verification_steps": [],
    })
    ok, errors = c.validate_contract(con, ws)
    assert not ok
    assert any("planned_approach" in e for e in errors)
    assert any("acceptance_criteria" in e for e in errors)
    assert any("verification_steps" in e for e in errors)


def test_explore_type_relaxes_the_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = c.Contract(**{
        **_valid_contract(ws).to_dict(),
        "task_type": "explore",
        "planned_approach": "",
        "acceptance_criteria": [],
        "verification_steps": [],
    })
    ok, errors = c.validate_contract(con, ws)
    assert ok, errors  # explore: substance fields not required


def test_unknown_task_type_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = c.Contract(**{**_valid_contract(ws).to_dict(), "task_type": "banana"})
    ok, errors = c.validate_contract(con, ws)
    assert not ok
    assert any("task_type" in e for e in errors)


def test_prefill_populates_only_resolvable_evidence(tmp_path, monkeypatch):
    """The anti-fabrication guarantee: every prefilled evidence_ref must resolve
    to a real file (a prefill that invented refs would fail its own validator)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = c.prefill_contract("widget calibration jig", ws)
    assert con.evidence_refs, "prefill should find the widgets doc"
    for ref in con.evidence_refs:
        assert (ws / ref).exists(), f"prefilled ref must resolve: {ref}"
    assert con.task_type in c.TASK_TYPES


def test_auto_contract_is_valid_and_minimal(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = c.auto_contract("bump the version string", ws)
    ok, errors = c.validate_contract(con, ws)
    assert ok, errors
    assert con.evidence_refs == []  # trivial: no evidence tax


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    con = _valid_contract(ws)
    path = c.save_contract(con, ws)
    assert path.exists()
    loaded = c.load_contract(con.contract_id, ws)
    assert loaded is not None
    assert loaded.to_dict() == con.to_dict()
