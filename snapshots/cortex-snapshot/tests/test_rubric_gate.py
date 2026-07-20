"""Tests for cortex_core/rubric_gate.py -- the rubric-driven verification gate.

Proves the four contract acceptance criteria without a browser or a model in CI (both are
injectable seams): (a) a genuinely broken UI is REFUSED, (b) a genuinely good UI passes,
(c) the gate degrades gracefully with no vision judge, (d) a fail routes back to IMPLEMENT
through the engine's existing rework loop carrying the concrete bug description. Plus a
real-Playwright interaction-capture test (skipped if Playwright is unavailable).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import rubric_gate as rg
from cortex_core.state_engine import BUILD_TRACK, StateEngine

FIX = Path(__file__).resolve().parent / "fixtures" / "rubric_gate"

# A minimal rubric stand-in so tests don't depend on the YAML on disk (also the injectable
# `rubric=` seam of make_verification_gate).
_RUBRIC = {"layer_2_vlm_judge": [{"id": "vj1", "question": "Is the layout free of clipping/overflow?"}]}


def _shots(*labels):
    return [rg.Screenshot(l, b"") for l in labels]


def _boom_render(*a, **k):
    raise AssertionError("renderer must NOT be called after a Layer-1 BLOCK")


def _boom_judge(*a, **k):
    raise AssertionError("judge must NOT be called after a Layer-1 BLOCK")


# --- Layer 1 deterministic (no renderer, no judge, no cost) ------------------------------


def test_layer1_flags_purple_gradient_and_inter_only():
    html, css = rg._read_artifact_text(FIX / "broken_slop")
    checks = rg.layer1_ui_ux(html, css)
    names = {c.name: c.status for c in checks}
    assert names["gradient_purple_band"] == "fail"
    assert names["font_allowlist"] == "fail"
    assert rg._overall(checks) == "fail"


def test_layer1_block_refuses_before_any_judge_call():
    # Ordering invariant: a Layer-1 BLOCK is final and the judge is never reached.
    result = rg.verify_ui_artifact(FIX / "broken_slop", _RUBRIC,
                                   render_fn=_boom_render, vision_judge_fn=_boom_judge)
    assert result.passed is False
    assert result.layer1_overall == "fail"
    assert "Layer-1 BLOCK" in result.reason
    assert result.judge is None  # never called


def test_good_ui_clears_layer1():
    html, css = rg._read_artifact_text(FIX / "good")
    assert rg._overall(rg.layer1_ui_ux(html, css)) != "fail"


# --- Layer 2 vision judge wiring (injected, deterministic) -------------------------------


def test_good_ui_passes(monkeypatch):
    result = rg.verify_ui_artifact(
        FIX / "good", _RUBRIC,
        render_fn=lambda *a, **k: _shots("initial"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(met=True, reason="clean, readable, no clipping"))
    assert result.passed is True
    assert result.judge_skipped is False
    assert "clean" in result.reason


def test_broken_ui_refused_by_vision_judge():
    # The real empty-detail bug class: passes Layer 1, the VISION judge catches it.
    calls = {}

    def render(artifact, interactions, **k):
        calls["interactions"] = interactions
        return _shots("initial", "detail-open")

    def judge(rubric, shots, **k):
        assert [s.label for s in shots] == ["initial", "detail-open"]  # got the post-click state
        return rg.VisionVerdict(met=False, reason="detail pane is blank after clicking a task; text clipped to 0 height")

    result = rg.verify_ui_artifact(
        FIX / "empty_detail", _RUBRIC,
        interactions=[{"action": "click", "selector": ".task-item", "label": "detail-open"}],
        render_fn=render, vision_judge_fn=judge)
    assert result.passed is False
    assert "blank after clicking" in result.reason
    assert calls["interactions"][0]["selector"] == ".task-item"


# --- Graceful degradation (criterion c) --------------------------------------------------


def test_degrades_when_no_vision_judge_configured():
    def judge(*a, **k):
        raise rg.JudgeUnavailable("no ANTHROPIC creds in this environment")

    result = rg.verify_ui_artifact(FIX / "good", _RUBRIC,
                                   render_fn=lambda *a, **k: _shots("initial"), vision_judge_fn=judge)
    assert result.passed is True            # Layer-1 alone decided
    assert result.judge_skipped is True
    assert "SKIPPED" in result.reason


def test_degrades_when_renderer_unavailable():
    def render(*a, **k):
        raise rg.RendererUnavailable("playwright not installed")

    result = rg.verify_ui_artifact(FIX / "good", _RUBRIC, render_fn=render, vision_judge_fn=_boom_judge)
    assert result.passed is True
    assert result.judge_skipped is True


# --- The engine gate (parallel to make_coding_gate) --------------------------------------


def test_gate_defers_outside_review():
    gate = rg.make_verification_gate(rubric=_RUBRIC)
    assert gate("IMPLEMENT", {}, {"patch": "x"})["pass"] is True  # default_gate


def test_gate_fails_closed_on_missing_artifact():
    gate = rg.make_verification_gate(rubric=_RUBRIC)
    out = gate("REVIEW", {}, {"verdict": "pass"})  # no artifact key
    assert out["pass"] is False
    assert "no artifact" in out["reason"]


def test_gate_fails_closed_on_unexpected_exception():
    def render(*a, **k):
        raise ValueError("kaboom")  # NOT a RendererUnavailable -> must fail closed, not crash

    gate = rg.make_verification_gate(rubric=_RUBRIC, render_fn=render, vision_judge_fn=_boom_judge)
    out = gate("REVIEW", {}, {"artifact": str(FIX / "good")})
    assert out["pass"] is False
    assert "raised" in out["reason"]


def test_gate_calls_on_fail_hook():
    seen = {}
    gate = rg.make_verification_gate(
        rubric=_RUBRIC,
        render_fn=lambda *a, **k: _shots("initial"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(met=False, reason="clipped"),
        on_fail=lambda phase, result: seen.update(phase=phase, reason=result.reason))
    out = gate("REVIEW", {}, {"artifact": str(FIX / "empty_detail")})
    assert out["pass"] is False
    assert seen["phase"] == "REVIEW" and "clipped" in seen["reason"]


# --- The integration test: fail at REVIEW -> rework to IMPLEMENT with the concrete reason -


def _advance(eng, tid, tool, payload):
    v = eng.get(tid)
    return eng.step(tid, tool=tool, payload=payload, seq=v["seq"])


def _drive_to_review(eng, tid):
    _advance(eng, tid, "cortex_report_findings", {"evidence": [{"claim": "x", "source": "y"}]})
    _advance(eng, tid, "cortex_report_findings", {"evidence": [{"claim": "x", "source": "y"}]})
    _advance(eng, tid, "cortex_submit_plan", {"plan": "build ui"})
    _advance(eng, tid, "cortex_submit_spec", {"spec": "ok"})
    _advance(eng, tid, "cortex_submit_patch", {"patch": "..."})


def test_review_fail_routes_back_to_implement_with_bug_description():
    gate = rg.make_verification_gate(
        rubric=_RUBRIC,
        render_fn=lambda *a, **k: _shots("initial", "detail-open"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(
            met=False, reason="empty detail pane after click; retry-history text clipped"))
    eng = StateEngine(":memory:", chart=BUILD_TRACK, gate=gate)
    tid = eng.create_task(intent={"goal": "build a task console UI"})
    _drive_to_review(eng, tid)
    assert eng.get(tid)["state"] == "REVIEW"

    env = _advance(eng, tid, "cortex_submit_review",
                   {"artifact": str(FIX / "empty_detail"), "verdict": "pass"})

    # The gate refused, so the existing rework_to loop sent the task BACK to IMPLEMENT...
    assert env["state"] == "IMPLEMENT"
    assert env["gate"]["pass"] is False
    # ...carrying the CONCRETE bug description, not just a boolean.
    assert "empty detail pane after click" in env["instruction"]
    assert "empty detail pane after click" in env["gate"]["reason"]


def test_review_pass_reaches_closeout():
    gate = rg.make_verification_gate(
        rubric=_RUBRIC,
        render_fn=lambda *a, **k: _shots("initial"),
        vision_judge_fn=lambda *a, **k: rg.VisionVerdict(met=True, reason="renders correctly"))
    eng = StateEngine(":memory:", chart=BUILD_TRACK, gate=gate)
    tid = eng.create_task(intent={"goal": "build ui"})
    _drive_to_review(eng, tid)
    env = _advance(eng, tid, "cortex_submit_review", {"artifact": str(FIX / "good"), "verdict": "pass"})
    assert env["state"] == "CLOSEOUT"


# --- Real Playwright: interaction capture (criterion e), skipped if unavailable ----------


def _playwright_ok():
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _playwright_ok(), reason="playwright not installed")
def test_playwright_captures_post_interaction_state():
    try:
        shots = rg.playwright_render(
            FIX / "empty_detail",
            [{"action": "click", "selector": ".task-item", "label": "detail-open"}])
    except rg.RendererUnavailable as exc:
        pytest.skip(f"no browser: {exc}")
    labels = [s.label for s in shots]
    assert labels[0] == "initial"
    assert "detail-open" in labels          # captured the state AFTER the click, not just load
    assert all(len(s.png) > 0 for s in shots)  # real PNG bytes


# --- Second rubric wired via a clean deterministic hook (handoff / actionable-item) ------


def test_handoff_gate_blocks_non_verifiable_wish():
    gate = rg.make_text_rubric_gate(rg.handoff_check)
    out = gate("REVIEW", {}, {"handoff": "make agents smarter until they stop making mistakes."})
    assert out["pass"] is False
    assert "Handoff rubric BLOCK" in out["reason"]


def test_handoff_gate_passes_well_formed_item():
    gate = rg.make_text_rubric_gate(rg.handoff_check)
    text = ("Acceptance: retrieves the intended exemplar in top-5 for at least 8 of 10 queries, "
            "verified by an automated enumeration test. Verification: benchmark script plus two "
            "integration tests. Rollback: revert the migration. Scope: one module.")
    out = gate("REVIEW", {}, {"handoff": text})
    assert out["pass"] is True


def test_handoff_gate_defers_outside_review():
    gate = rg.make_text_rubric_gate(rg.handoff_check)
    assert gate("IMPLEMENT", {}, {"handoff": "anything"})["pass"] is True


@pytest.mark.skipif(not _playwright_ok(), reason="playwright not installed")
def test_playwright_static_page_single_screenshot():
    try:
        shots = rg.playwright_render(FIX / "good", None)  # no interactions -> one load screenshot
    except rg.RendererUnavailable as exc:
        pytest.skip(f"no browser: {exc}")
    assert [s.label for s in shots] == ["initial"]


# --- Layer 0: deterministic STRUCTURAL floor (2026-07-07 ledger-mining pass) --------------
# Each test reproduces a real benchmark defect class the pre-Layer-0 gate waved through when
# the vision judge was unavailable (the common CI case). All are caught with NO renderer and
# NO judge -- the injected _boom_* callables prove the deterministic layer decides alone.


def _names(checks):
    return {c.name: c.status for c in checks}


def test_layer0_flags_truncated_markup(tmp_path):
    # task04/task05/task08 class: max_tokens truncation cut the write_file payload off
    # mid-<style>; no </style>, no </body>, no </html>. Renders blank.
    d = tmp_path / "truncated"
    d.mkdir()
    (d / "index.html").write_text(
        "<!doctype html><html><head><style>\n"
        "  body { margin: 0 }\n  .detail-grid dd { color: #111; padding-top: 4px",  # cut off mid-CSS
        encoding="utf-8")
    checks = rg.layer0_structural(d)
    assert _names(checks).get("unclosed_markup") == "fail"
    assert rg._overall(checks) == "fail"


def test_layer0_flags_broken_local_script_refs(tmp_path):
    # task03 class: index.html loads data.js + app.js; neither exists on disk. The one JS
    # file that WAS written (script.js) is never referenced. dir-listing "verification"
    # missed it; the reference check makes acting on it non-optional.
    d = tmp_path / "wiring"
    d.mkdir()
    (d / "index.html").write_text(
        "<!doctype html><html><head></head><body><table id='t'></table>"
        "<script src='data.js'></script><script src='app.js'></script></body></html>",
        encoding="utf-8")
    (d / "script.js").write_text("class DataTable {}\n", encoding="utf-8")  # written, unreferenced
    checks = rg.layer0_structural(d)
    assert _names(checks).get("broken_reference") == "fail"
    details = " ".join(c.detail for c in checks if c.status == "fail")
    assert "data.js" in details and "app.js" in details


def test_layer0_flags_elision_placeholder_content(tmp_path):
    # task13 (3-byte "...") and task15 (18-byte "<!DOCTYPE html>...") class: a well-formed
    # write_file tool call whose CONTENT is a lazy elision stub. Passes JSON-parse; hollow.
    d13 = tmp_path / "t13"
    d13.mkdir()
    (d13 / "index.html").write_text("...", encoding="utf-8")
    assert _names(rg.layer0_structural(d13)).get("elision_placeholder") == "fail"

    d15 = tmp_path / "t15"
    d15.mkdir()
    (d15 / "index.html").write_text("<!DOCTYPE html>...", encoding="utf-8")
    assert _names(rg.layer0_structural(d15)).get("elision_placeholder") == "fail"

    # And a "[rest of file omitted]" marker inside otherwise-plausible markup.
    d_marker = tmp_path / "marker"
    d_marker.mkdir()
    (d_marker / "index.html").write_text(
        "<!doctype html><html><body><h1>Hi</h1>\n<!-- ... rest of the file omitted ... -->\n"
        "</body></html>", encoding="utf-8")
    assert _names(rg.layer0_structural(d_marker)).get("elision_placeholder") == "fail"


def test_layer0_passes_a_complete_wired_artifact(tmp_path):
    # No false positive on a genuinely complete deliverable: balanced markup, an external
    # CDN script (skipped), a resolvable local script, and prose that merely contains "...".
    d = tmp_path / "ok"
    d.mkdir()
    (d / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
    (d / "index.html").write_text(
        "<!doctype html><html><head><style>body{margin:0}</style></head><body>"
        "<p>Loading more results...</p>"
        "<script src='https://cdn.example.com/lib.js'></script>"
        "<script src='app.js'></script></body></html>",
        encoding="utf-8")
    checks = rg.layer0_structural(d)
    assert rg._overall(checks) == "pass"
    assert _names(checks).get("structural") == "pass"


def test_verify_layer0_block_is_final_no_render_no_judge(tmp_path):
    # The ordering invariant extended: a Layer-0 structural BLOCK is reached with ZERO
    # renderer and ZERO judge calls -- the deterministic floor still bites when vision degrades.
    d = tmp_path / "truncated"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><html><head><style>body{", encoding="utf-8")
    result = rg.verify_ui_artifact(d, _RUBRIC, render_fn=_boom_render, vision_judge_fn=_boom_judge)
    assert result.passed is False
    assert result.layer0_overall == "fail"
    assert "Layer-0 STRUCTURAL BLOCK" in result.reason
    assert result.judge is None


def test_verification_gate_blocks_truncated_ui_at_review(tmp_path):
    # End-to-end: a truncated build cannot reach CLOSEOUT even with NO vision judge available
    # (the exact CI/degraded condition that let task04/05 through before). Fail-closed floor.
    d = tmp_path / "truncated"
    d.mkdir()
    (d / "index.html").write_text(
        "<!doctype html><html><head><style>body{margin:0}\n.card{border-radius:8px",
        encoding="utf-8")
    gate = rg.make_verification_gate(rubric=_RUBRIC, render_fn=_boom_render, vision_judge_fn=_boom_judge)
    eng = StateEngine(":memory:", chart=BUILD_TRACK, gate=gate)
    tid = eng.create_task(intent={"goal": "build ui"})
    _drive_to_review(eng, tid)
    env = _advance(eng, tid, "cortex_submit_review", {"artifact": str(d), "verdict": "pass"})
    assert env["state"] == "IMPLEMENT"          # routed back, not closed out
    assert env["gate"]["pass"] is False
    assert "truncated" in env["instruction"].lower() or "STRUCTURAL" in env["instruction"]
