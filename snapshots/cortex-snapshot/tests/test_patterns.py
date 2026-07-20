"""Phase 5 gate 5.1: pattern (KEDB) schema, validation, promotion detector."""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core import patterns as p


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    (ws / "reviewed").mkdir(parents=True)
    (ws / "reviewed" / "some-review.md").write_text("# Review\n", encoding="utf-8")
    return ws


def _pattern(ws: Path, **over) -> p.Pattern:
    base = dict(
        pattern_id="pat-1",
        title="A real recurring class",
        symptom="the symptom",
        root_cause="the cause",
        detection_recipe="run X, observe Y",
        fix="do Z",
        evidence_links=["reviewed/some-review.md"],
        task_type="bugfix",
        occurrence_count=3,
        first_seen="2026-07-04T00:00:00Z",
        last_seen="2026-07-04T00:00:00Z",
        last_verified="2026-07-04T00:00:00Z",
    )
    base.update(over)
    return p.Pattern(**base)


def test_valid_pattern_passes(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    ok, errors = p.validate_pattern(_pattern(ws), ws)
    assert ok, errors


def test_pattern_without_detection_recipe_is_a_horoscope(tmp_path, monkeypatch):
    """Gate 5.1: symptom-only entries (no detection recipe) are rejected."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    ok, errors = p.validate_pattern(_pattern(ws, detection_recipe="  "), ws)
    assert not ok
    assert any("detection_recipe" in e for e in errors)


def test_pattern_below_occurrence_floor_is_rejected(tmp_path, monkeypatch):
    """Gate 5.1: don't generalize from n=1."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    ok, errors = p.validate_pattern(_pattern(ws, occurrence_count=1), ws)
    assert not ok
    assert any("occurrence_count" in e for e in errors)


def test_pattern_with_unresolvable_evidence_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    ok, errors = p.validate_pattern(_pattern(ws, evidence_links=["reviewed/ghost.md"]), ws)
    assert not ok
    assert any("does not resolve" in e for e in errors)


def test_pattern_with_no_evidence_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    ok, errors = p.validate_pattern(_pattern(ws, evidence_links=[]), ws)
    assert not ok
    assert any("evidence_links is empty" in e for e in errors)


def test_save_and_load_roundtrip_and_searchable_body(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    path = p.save_pattern(_pattern(ws), ws)
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    # The recipe/symptom/fix are in the searchable markdown body, not just JSON.
    assert "## Detection recipe" in body and "run X, observe Y" in body
    loaded = p.load_patterns(ws)
    assert len(loaded) == 1
    assert loaded[0].title == "A real recurring class"


def test_promote_candidates_surfaces_repeat_classes(tmp_path, monkeypatch):
    """The librarian's detector flags terms shared by >= min_occurrence closeouts."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    agent = ws / "audit" / "audit-log-1" / "agent"
    agent.mkdir(parents=True)
    for i in range(3):
        (agent / f"c{i}.json").write_text(
            json.dumps({"task": f"fix widgetflurb calibration {i}", "result": "done"}),
            encoding="utf-8",
        )
    (agent / "other.json").write_text(
        json.dumps({"task": "unrelated documentation task", "result": "done"}),
        encoding="utf-8",
    )
    cands = p.promote_candidates(ws, min_occurrence=3)
    terms = {c["term"] for c in cands}
    assert "widgetflurb" in terms  # the distinctive repeated term is surfaced
    assert "unrelated" not in terms  # a one-off is not
