"""Tests for the RESEARCH_TRACK chart on the existing StateEngine.

The RESEARCH_TRACK chart (cortex_core/state_engine.py) is pure chart-data added
alongside BUILD_TRACK; the interpreter is unchanged. These tests are the
acceptance conditions for the feature (contract 019f3db9):

- the chart loads (referential integrity via _validate_chart);
- a task on track="research" walks the full legal tool sequence to DONE;
- an illegal tool is refused (ILLEGAL_IN_STATE) without advancing, and the task
  recovers by submitting the correct advance tool (refusal-and-recovery);
- a coverage-gate failure at CITE_CHECK loops the task back to FETCH via
  rework_to, and after the gate passes the task resumes forward to DONE
  (gate-refusal-and-recovery, mirroring the build track's REVIEW->IMPLEMENT);
- the pre-existing BUILD_TRACK path is unaffected.
"""

from __future__ import annotations

import pytest

state_engine = pytest.importorskip("cortex_core.state_engine")
StateEngine = state_engine.StateEngine
RESEARCH_TRACK = state_engine.RESEARCH_TRACK


# The legal advance-tool sequence FRAME -> ... -> REPORT (-> DONE).
RESEARCH_WALK = [
    ("FRAME", "cortex_submit_framing", "SEED"),
    ("SEED", "cortex_submit_seeds", "FETCH"),
    ("FETCH", "cortex_submit_fetch_report", "EVIDENCE"),
    ("EVIDENCE", "cortex_submit_evidence", "CITE_CHECK"),
    ("CITE_CHECK", "cortex_submit_coverage", "SUMMARIZE"),
    ("SUMMARIZE", "cortex_submit_findings", "REPORT"),
    ("REPORT", "cortex_write_research_report", "DONE"),
]


def _eng(tmp_path, **kw):
    return StateEngine(str(tmp_path / "engine.sqlite"), **kw)


def test_research_chart_is_registered_and_valid(tmp_path):
    """Both first-party tracks are built-in; the research chart validated at load."""
    eng = _eng(tmp_path)
    assert set(("build", "research")).issubset(eng._charts.keys())
    chart = eng._charts["research"]
    assert chart["initial"] == "FRAME"
    # every phase's `next`/`rework_to` resolves to a defined state (else load raised)
    for name, spec in chart["states"].items():
        for ref_key in ("next", "rework_to"):
            ref = spec.get(ref_key)
            if ref is not None:
                assert ref in chart["states"], f"{name}.{ref_key} -> undefined {ref}"


def test_create_task_on_research_track_starts_in_frame(tmp_path):
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "how does X work"}, track="research")
    row = eng.get(tid)
    assert row["track"] == "research"
    assert row["state"] == "FRAME"
    assert row["seq"] == 0
    legal = row["legal_tools"]
    assert "cortex_submit_framing" in legal
    assert "cortex_search" in legal  # in-phase extra tool
    assert "cortex_submit_patch" not in legal  # a build-track tool must not leak in


def test_full_walk_to_done(tmp_path):
    """A research task walks the full legal tool sequence to DONE."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "survey of X"}, track="research")
    for state, tool, nxt in RESEARCH_WALK:
        row = eng.get(tid)
        assert row["state"] == state
        env = eng.step(tid, tool=tool, payload={"note": state}, seq=row["seq"])
        assert env["ok"] is True, env
        assert env["state"] == nxt, f"{state} via {tool} -> {env['state']}, want {nxt}"
    final = eng.get(tid)
    assert final["state"] == "DONE"
    assert final["closeout_written"] is True  # REPORT is the is_closeout phase


def test_illegal_tool_refused_then_recovers(tmp_path):
    """An illegal tool is refused without advancing; the correct tool then advances."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"}, track="research")
    row = eng.get(tid)  # FRAME, seq 0
    # a downstream tool is illegal in FRAME
    bad = eng.step(tid, tool="cortex_write_research_report", payload={}, seq=row["seq"])
    assert bad["ok"] is False
    assert bad["code"] == "ILLEGAL_IN_STATE"
    assert bad.get("do_instead", {}).get("tool") == "cortex_submit_framing"
    after = eng.get(tid)
    assert after["state"] == "FRAME" and after["seq"] == 0  # unchanged
    # recovery: submit the real advance tool
    good = eng.step(tid, tool="cortex_submit_framing", payload={"sub_questions": ["q1"]},
                    seq=after["seq"])
    assert good["ok"] is True
    assert good["state"] == "SEED"


def test_cite_check_gate_failure_reworks_to_fetch_then_recovers(tmp_path):
    """Coverage gate fail at CITE_CHECK loops back to FETCH (rework_to), then recovers to DONE.

    This is the research-track analogue of the build track's REVIEW->IMPLEMENT
    rework: the same gate-refusal-and-recovery invariant the engine already
    proves for BUILD_TRACK, exercised on the new chart.
    """
    calls = {"cite_check": 0}

    def gate(phase, task, payload):
        # Fail the FIRST CITE_CHECK submission (forces one rework to FETCH),
        # then pass everything (so the task can recover forward to DONE).
        if phase == "CITE_CHECK":
            calls["cite_check"] += 1
            return {"pass": calls["cite_check"] > 1,
                    "reason": "sub-question q2 unanswered; fetch more sources"}
        return {"pass": True}

    eng = _eng(tmp_path, gate=gate)
    tid = eng.create_task(intent={"seeking": "x"}, track="research")

    def advance(tool):
        row = eng.get(tid)
        return eng.step(tid, tool=tool, payload={}, seq=row["seq"])

    # FRAME -> SEED -> FETCH -> EVIDENCE -> CITE_CHECK
    advance("cortex_submit_framing")
    advance("cortex_submit_seeds")
    advance("cortex_submit_fetch_report")
    advance("cortex_submit_evidence")
    assert eng.get(tid)["state"] == "CITE_CHECK"

    # First coverage submission FAILS the gate -> rework back to FETCH.
    reworked = advance("cortex_submit_coverage")
    assert reworked["ok"] is True
    assert reworked["state"] == "FETCH", reworked
    assert eng.get(tid)["rework_count"] == 1

    # Recovery: re-fetch, re-gather, resubmit coverage -- gate now passes.
    advance("cortex_submit_fetch_report")   # FETCH -> EVIDENCE
    advance("cortex_submit_evidence")        # EVIDENCE -> CITE_CHECK
    assert eng.get(tid)["state"] == "CITE_CHECK"
    passed = advance("cortex_submit_coverage")  # gate passes this time
    assert passed["ok"] is True
    assert passed["state"] == "SUMMARIZE"

    # Drive the rest to DONE.
    advance("cortex_submit_findings")        # SUMMARIZE -> REPORT
    advance("cortex_write_research_report")  # REPORT -> DONE
    assert eng.get(tid)["state"] == "DONE"


def test_build_track_still_reaches_done_unchanged(tmp_path):
    """Adding the research track must not disturb the pre-existing build path."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})  # default track="build"
    assert eng.get(tid)["state"] == "SEARCH_BRAIN"
    walk = [
        "cortex_report_findings",   # SEARCH_BRAIN -> RESEARCH
        "cortex_report_findings",   # RESEARCH -> PLAN
        "cortex_submit_plan",       # PLAN -> SPEC
        "cortex_submit_spec",       # SPEC -> IMPLEMENT
        "cortex_submit_patch",      # IMPLEMENT -> REVIEW
        "cortex_submit_review",     # REVIEW -> CLOSEOUT
        "cortex_write_closeout",    # CLOSEOUT -> DONE
    ]
    for tool in walk:
        row = eng.get(tid)
        env = eng.step(tid, tool=tool, payload={"evidence": []}, seq=row["seq"])
        assert env["ok"] is True, env
    assert eng.get(tid)["state"] == "DONE"


def test_unknown_track_raises(tmp_path):
    """create_task on an unregistered track fails loudly (KeyError), not silently."""
    eng = _eng(tmp_path)
    with pytest.raises(KeyError):
        eng.create_task(intent={"seeking": "x"}, track="nope")
