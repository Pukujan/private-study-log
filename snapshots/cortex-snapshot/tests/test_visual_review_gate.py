"""Tests for the 2026-07-07 REVIEW-gate additions:

1. The scoped visual-review gate (cortex_core/rubric_gate.make_scoped_review_gate +
   is_visual_deliverable) -- composes review_scope_gate with the rubric verification gate,
   but ONLY runs the Playwright/vision-judge layer on tasks that actually produce a UI
   deliverable (cost discipline, explicit user requirement). Wired into cortex_run_step's
   engine via cortex_core.mcp._run_engine.

2. The optional `rationale` trace field threaded through StateEngine.step -> _append_event,
   and its read path (StateEngine.event_history / cortex_run_state's "events" list).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import rubric_gate as rg
from cortex_core.state_engine import BUILD_TRACK, StateEngine, review_scope_gate

FIX = Path(__file__).resolve().parent / "fixtures" / "rubric_gate"
_RUBRIC = {"layer_2_vlm_judge": [{"id": "vj1", "question": "Is the layout free of clipping/overflow?"}]}


def _shots(*labels):
    return [rg.Screenshot(l, b"") for l in labels]


def _boom_render(*a, **k):
    raise AssertionError("renderer must NOT be called for a non-visual task")


def _boom_judge(*a, **k):
    raise AssertionError("vision judge must NOT be called for a non-visual task")


# --- is_visual_deliverable: the exact trigger condition ------------------------------------


def test_visual_detector_triggers_on_html_artifact_path():
    payload = {"artifact": str(FIX / "good")}
    assert rg.is_visual_deliverable({}, payload) is True


def test_visual_detector_skips_when_no_signal_at_all():
    payload = {"result": "done", "scope_check": {"delivered": "wrote a CLI parser"}}
    assert rg.is_visual_deliverable({}, payload) is False


def test_visual_detector_skips_non_html_artifact():
    payload = {"artifact": __file__}  # a .py file -- no html entry point
    assert rg.is_visual_deliverable({}, payload) is False


def test_visual_detector_explicit_flag_forces_on_even_without_files():
    task = {"produces_ui": True}
    assert rg.is_visual_deliverable(task, {"result": "done"}) is True


def test_visual_detector_explicit_flag_forces_off_even_with_html_artifact():
    task = {"produces_ui": False}
    payload = {"artifact": str(FIX / "good")}
    assert rg.is_visual_deliverable(task, payload) is False


def test_visual_detector_task_type_on_intent():
    task = {"intent": {"task_type": "ui_ux"}}
    assert rg.is_visual_deliverable(task, {"result": "done"}) is True


def test_visual_detector_delivered_files_list_with_html_entry():
    payload = {"files": ["style.css", "app.jsx", "index.html"]}
    assert rg.is_visual_deliverable({}, payload) is True


def test_visual_detector_delivered_files_list_without_html_entry():
    payload = {"files": ["style.css", "app.jsx"]}
    assert rg.is_visual_deliverable({}, payload) is False


# --- make_scoped_review_gate: composition + cost discipline --------------------------------


def test_scoped_gate_skips_visual_layer_for_non_visual_task():
    gate = rg.make_scoped_review_gate(rubric=_RUBRIC, render_fn=_boom_render, vision_judge_fn=_boom_judge)
    task = {"intent": {"seeking": "write a CLI parser"}}
    out = gate("REVIEW", task, {"result": "done", "scope_check": {"delivered": "wrote a CLI parser"}})
    assert out["pass"] is True
    assert "visual_gate" not in out
    assert "visual_gate_warning" not in out


def test_scoped_gate_runs_visual_layer_for_visual_task_and_can_fail_it():
    gate = rg.make_scoped_review_gate(
        rubric=_RUBRIC,
        render_fn=lambda *a, **k: _shots("initial"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(met=False, reason="text clipped in the header"))
    task = {"intent": {"seeking": "build a UI"}}
    out = gate("REVIEW", task, {"artifact": str(FIX / "good"), "scope_check": {
        "matches_request": True, "delivered": "built a UI"}})
    assert out["pass"] is False
    assert "text clipped" in out["reason"]


def test_scoped_gate_passes_visual_task_with_good_ui():
    gate = rg.make_scoped_review_gate(
        rubric=_RUBRIC,
        render_fn=lambda *a, **k: _shots("initial"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(met=True, reason="renders cleanly"))
    task = {"intent": {"seeking": "build a UI"}}
    out = gate("REVIEW", task, {"artifact": str(FIX / "good"), "scope_check": {
        "matches_request": True, "delivered": "built a UI"}})
    assert out["pass"] is True
    assert "renders cleanly" in out["visual_gate"]


def test_scoped_gate_scope_failure_wins_before_visual_layer_ever_runs():
    """A scope-vs-intent mismatch is caught by review_scope_gate BEFORE the (expensive) visual
    layer is even reached -- composition preserves review_scope_gate's own fail-fast contract."""
    gate = rg.make_scoped_review_gate(rubric=_RUBRIC, render_fn=_boom_render, vision_judge_fn=_boom_judge)
    task = {"intent": {"seeking": "scrape competitor forum tools"}}
    out = gate("REVIEW", task, {"artifact": str(FIX / "good"),
                                "scope_check": {"matches_request": False, "delivered": "built a UI"}})
    assert out["pass"] is False
    assert "does not match" in out["reason"]


def test_scoped_gate_noop_outside_review_phase():
    gate = rg.make_scoped_review_gate(rubric=_RUBRIC, render_fn=_boom_render, vision_judge_fn=_boom_judge)
    out = gate("IMPLEMENT", {}, {"artifact": str(FIX / "good")})
    assert out["pass"] is True


# --- Graceful degradation (mandatory: verification tooling unavailable never hard-blocks) --


def test_scoped_gate_degrades_when_playwright_unavailable():
    def render(*a, **k):
        raise rg.RendererUnavailable("playwright not installed")

    gate = rg.make_scoped_review_gate(rubric=_RUBRIC, render_fn=render, vision_judge_fn=_boom_judge)
    task = {"intent": {"seeking": "build a UI"}}
    out = gate("REVIEW", task, {"artifact": str(FIX / "good"), "scope_check": {
        "matches_request": True, "delivered": "built a UI"}})
    assert out["pass"] is True   # WARN, not block
    assert "SKIPPED" in out["visual_gate"]


def test_scoped_gate_degrades_when_no_vision_judge_configured():
    def judge(*a, **k):
        raise rg.JudgeUnavailable("no vision-capable judge configured")

    gate = rg.make_scoped_review_gate(
        rubric=_RUBRIC, render_fn=lambda *a, **k: _shots("initial"), vision_judge_fn=judge)
    task = {"intent": {"seeking": "build a UI"}}
    out = gate("REVIEW", task, {"artifact": str(FIX / "good"), "scope_check": {
        "matches_request": True, "delivered": "built a UI"}})
    assert out["pass"] is True
    assert "SKIPPED" in out["visual_gate"]


def test_scoped_gate_degrades_when_rubric_material_is_missing(tmp_path):
    """No calibration/rubrics/ under this workspace (e.g. a bare tenant folder) must WARN, not
    crash gate construction or block a visual task -- graceful degradation all the way down."""
    gate = rg.make_scoped_review_gate(rubric_id="ui_ux", workspace=tmp_path,
                                      render_fn=_boom_render, vision_judge_fn=_boom_judge)
    task = {"intent": {"seeking": "build a UI"}}
    out = gate("REVIEW", task, {"artifact": str(FIX / "good"), "scope_check": {
        "matches_request": True, "delivered": "built a UI"}})
    assert out["pass"] is True
    assert "visual_gate_warning" in out
    assert "could not be constructed" in out["visual_gate_warning"]


# --- Engine integration: fail routes to rework, pass reaches CLOSEOUT ----------------------


def _advance(eng, tid, tool, payload):
    v = eng.get(tid)
    return eng.step(tid, tool=tool, payload=payload, seq=v["seq"])


def _drive_to_review(eng, tid):
    _advance(eng, tid, "cortex_report_findings", {"evidence": [{"claim": "x", "source": "y"}]})
    _advance(eng, tid, "cortex_report_findings", {"evidence": [{"claim": "x", "source": "y"}]})
    _advance(eng, tid, "cortex_submit_plan", {"plan": "build ui"})
    _advance(eng, tid, "cortex_submit_spec", {"spec": "ok"})
    _advance(eng, tid, "cortex_submit_patch", {"patch": "..."})


def test_engine_visual_fail_routes_back_to_implement():
    gate = rg.make_scoped_review_gate(
        rubric=_RUBRIC,
        render_fn=lambda *a, **k: _shots("initial"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(met=False, reason="empty panel after click"))
    eng = StateEngine(":memory:", chart=BUILD_TRACK, gate=gate)
    tid = eng.create_task(intent={"seeking": "build a task console UI"})
    _drive_to_review(eng, tid)
    env = _advance(eng, tid, "cortex_submit_review",
                   {"artifact": str(FIX / "good"),
                    "scope_check": {"matches_request": True, "delivered": "built a task console UI"}})
    assert env["state"] == "IMPLEMENT"
    assert "empty panel after click" in env["gate"]["reason"]


def test_engine_non_visual_task_never_touches_visual_layer():
    gate = rg.make_scoped_review_gate(rubric=_RUBRIC, render_fn=_boom_render, vision_judge_fn=_boom_judge)
    eng = StateEngine(":memory:", chart=BUILD_TRACK, gate=gate)
    tid = eng.create_task(intent={"seeking": "write a CLI parser"})
    _drive_to_review(eng, tid)
    env = _advance(eng, tid, "cortex_submit_review",
                   {"result": "done",
                    "scope_check": {"matches_request": True, "delivered": "wrote a CLI parser"}})
    assert env["state"] == "CLOSEOUT"


# --- rationale: write + read-path round-trip ------------------------------------------------


def test_rationale_round_trips_through_event_log():
    eng = StateEngine(":memory:", chart=BUILD_TRACK)
    tid = eng.create_task(intent={"seeking": "x"})
    v = eng.get(tid)
    env = eng.step(tid, tool=v["legal_tools"][0], seq=v["seq"],
                   payload={"evidence": [{"claim": "c", "source": "s"}]},
                   rationale="advancing because the corpus search turned up enough evidence")
    assert env["rationale"] == "advancing because the corpus search turned up enough evidence"
    hist = eng.event_history(tid)
    matches = [e for e in hist if e["rationale"] is not None]
    assert len(matches) == 1
    assert matches[0]["rationale"] == "advancing because the corpus search turned up enough evidence"
    assert matches[0]["seq"] == env["seq"]


def test_rationale_omitted_behaves_exactly_as_before():
    eng = StateEngine(":memory:", chart=BUILD_TRACK)
    tid = eng.create_task(intent={"seeking": "x"})
    v = eng.get(tid)
    env = eng.step(tid, tool=v["legal_tools"][0], seq=v["seq"],
                   payload={"evidence": [{"claim": "c", "source": "s"}]})
    assert "rationale" not in env
    hist = eng.event_history(tid)
    assert all(e["rationale"] is None for e in hist)


def test_rationale_on_rework_and_note_events_too():
    """rationale threads through _apply_note and the rework branch of _apply_advance, not just
    the plain-advance path."""
    def fail_once_gate(phase, task, payload):
        if phase == "REVIEW":
            return {"pass": False, "reason": "needs more polish"}
        return {"pass": True}

    eng = StateEngine(":memory:", chart=BUILD_TRACK, gate=fail_once_gate)
    tid = eng.create_task(intent={"seeking": "x"})
    v = eng.get(tid)
    # an in-phase note tool (cortex_search) with a rationale
    note_env = eng.step(tid, tool="cortex_search", seq=v["seq"], payload={"q": "x"},
                        rationale="searching before reporting findings")
    assert note_env["rationale"] == "searching before reporting findings"
    _drive_to_review(eng, tid)
    v = eng.get(tid)
    rework_env = eng.step(tid, tool="cortex_submit_review", seq=v["seq"], payload={"result": "done"},
                          rationale="submitting for review despite known rough edges")
    assert rework_env["state"] == "IMPLEMENT"
    assert rework_env["rationale"] == "submitting for review despite known rough edges"


# --- mcp.py wiring: cortex_run_step(rationale=...) -> cortex_run_state()["events"] ---------


def _make_workspace(root: Path) -> Path:
    ws = root / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (ws / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return ws


def test_mcp_run_step_rationale_is_retrievable_via_run_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_VISUAL_GATE", "0")  # isolate: rationale wiring, not the visual gate
    from cortex_core.mcp import cortex_run_start, cortex_run_state, cortex_run_step

    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "x"}, session_id="s1", workspace=str(ws)))
    tid = env["task_id"]
    asyncio.run(cortex_run_step(
        tid, env["legal_tools"][0], env["seq"],
        payload={"evidence": [{"claim": "c", "source": "s"}]},
        session_id="s1", workspace=str(ws),
        rationale="advancing past SEARCH_BRAIN with corpus evidence in hand"))
    state = asyncio.run(cortex_run_state(tid, session_id="s1", workspace=str(ws)))
    assert "events" in state
    rationales = [e["rationale"] for e in state["events"] if e["rationale"]]
    assert rationales == ["advancing past SEARCH_BRAIN with corpus evidence in hand"]


def test_mcp_run_step_without_rationale_is_unaffected(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_VISUAL_GATE", "0")
    from cortex_core.mcp import cortex_run_start, cortex_run_state, cortex_run_step

    ws = _make_workspace(tmp_path)
    env = asyncio.run(cortex_run_start({"seeking": "x"}, session_id="s2", workspace=str(ws)))
    tid = env["task_id"]
    step_env = asyncio.run(cortex_run_step(
        tid, env["legal_tools"][0], env["seq"],
        payload={"evidence": [{"claim": "c", "source": "s"}]},
        session_id="s2", workspace=str(ws)))
    assert "rationale" not in step_env
    state = asyncio.run(cortex_run_state(tid, session_id="s2", workspace=str(ws)))
    assert all(e["rationale"] is None for e in state["events"])


# --- _run_engine wiring: the visual gate composes with review_scope_gate by default --------


def test_run_engine_wires_scoped_visual_gate_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_SCOPE_GATE", "1")
    monkeypatch.setenv("CORTEX_VISUAL_GATE", "1")
    import cortex_core.mcp as mcp_mod

    mcp_mod._run_engines.clear()
    ws = _make_workspace(tmp_path)
    eng = mcp_mod._run_engine(str(ws), None)
    # A non-visual REVIEW payload passes through the composed gate exactly like review_scope_gate
    # alone would (no calibration/rubrics/ under this bare tmp workspace -- graceful degradation
    # never blocks a task that isn't even visual, since the visual layer isn't reached).
    verdict = eng._gate("REVIEW", {"intent": {"seeking": "write a CLI parser"}},
                        {"result": "done", "scope_check": {"matches_request": True,
                         "delivered": "wrote a CLI parser"}})
    assert verdict["pass"] is True


def test_run_engine_visual_gate_can_be_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_SCOPE_GATE", "1")
    monkeypatch.setenv("CORTEX_VISUAL_GATE", "0")
    import cortex_core.mcp as mcp_mod

    mcp_mod._run_engines.clear()
    ws = _make_workspace(tmp_path)
    eng = mcp_mod._run_engine(str(ws), None)
    # eng._gate is always the universal gate now (mission tracks need it for PARTITION),
    # but with the visual gate disabled its REVIEW behavior must be identical to plain
    # review_scope_gate -- no visual_gate/visual_gate_warning key added.
    payload = {"result": "done", "scope_check": {"matches_request": True,
               "delivered": "wrote a CLI parser"}}
    task = {"intent": {"seeking": "write a CLI parser"}}
    assert eng._gate("REVIEW", task, payload) == review_scope_gate("REVIEW", task, payload)
