"""Plane-2 enforcement: an EXTERNAL-model driver cannot reach "done" without walking every
build phase IN ORDER and producing a GROUNDED closeout.

The threat model (what the collaborator's weak model on 9router will try): skip a phase, jump
to DONE, advance with empty payloads, forge a closeout, or livelock on rework. Each is blocked
here -- structurally by the StateEngine (order, server-owned transitions) and by
build_grounding_gate (evidence + grounded closeout). All offline: the external model is an
injected scripted callable; no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import plane2_driver as p2  # noqa: E402
from cortex_core import state_engine as se  # noqa: E402

SEEKING = "build the widget parser module"

# A well-behaved external model: emits correct JSON slot content per phase. The driver tells it
# the CURRENT PHASE in the prompt; this scripted model branches on that.
_GOOD = {
    "SEARCH_BRAIN": {"evidence": [{"claim": "no prior widget parser in corpus",
                                   "source": "corpus:search"}], "summary": "searched"},
    "RESEARCH": {"evidence": [{"claim": "recursive descent fits", "source": "docs/parse"}],
                 "summary": "open questions closed"},
    "PLAN": {"plan": ["tokenize", "parse", "test"]},
    "SPEC": {"spec": "parser handles nested widgets; errors are line-numbered"},
    "IMPLEMENT": {"patch": "diff --git a/parser.py b/parser.py\n+ real change"},
    "REVIEW": {"review": "matches spec", "scope_check": {"delivered": "widget parser module",
                                                         "matches_request": True}},
    "CLOSEOUT": {"task": SEEKING, "result": "implemented and reviewed", "test_status": "pass"},
}


def _phase_of(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("CURRENT PHASE:"):
            return line.split(":", 1)[1].strip()
    return ""


def _scripted(mapping: dict[str, dict]):
    """Build an llm(prompt)->str that returns mapping[phase] as JSON."""
    def llm(prompt: str) -> str:
        phase = _phase_of(prompt)
        return json.dumps(mapping.get(phase, {}))
    return llm


def _intent() -> dict:
    return {"seeking": SEEKING}


# --------------------------------------------------------------------------------------------
# 1. Happy path: a grounded walk reaches DONE, and it passed EVERY phase IN ORDER.
# --------------------------------------------------------------------------------------------
def test_grounded_walk_reaches_done_in_order(tmp_path: Path) -> None:
    res = p2.run_build(_intent(), _scripted(_GOOD), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] == "done", res
    assert res["state"] == "DONE"
    submitted = [t["state"] for t in res["trail"] if t["ok"]]
    # The ordered spine of the build track -- proved walked, in order, none skipped.
    assert submitted == ["SEARCH_BRAIN", "RESEARCH", "PLAN", "SPEC",
                         "IMPLEMENT", "REVIEW", "CLOSEOUT"], submitted


# --------------------------------------------------------------------------------------------
# 2. Order is server-owned: the engine refuses a later phase's tool while in an earlier phase.
#    (The driver never even tries this; this asserts the wall it relies on exists.)
# --------------------------------------------------------------------------------------------
def test_engine_refuses_out_of_order_tool(tmp_path: Path) -> None:
    eng = se.StateEngine(str(tmp_path / "e.sqlite"), gate=p2.default_build_gate())
    tid = eng.create_task(_intent(), track="build")
    env = eng.get(tid)
    assert env["state"] == "SEARCH_BRAIN"
    # Try to jump straight to the closeout tool -- refused, nothing changes.
    r = eng.step(tid, "cortex_write_closeout",
                 {"task": SEEKING, "result": "faked done"}, seq=env["seq"])
    assert r["ok"] is False
    assert r["code"] == "ILLEGAL_IN_STATE"
    assert eng.get(tid)["state"] == "SEARCH_BRAIN"  # unmoved
    eng.close()


# --------------------------------------------------------------------------------------------
# 3. There is NO tool that sets DONE except CLOSEOUT's advance, and DONE is unreachable early.
# --------------------------------------------------------------------------------------------
def test_no_direct_jump_to_done(tmp_path: Path) -> None:
    eng = se.StateEngine(str(tmp_path / "e.sqlite"), gate=p2.default_build_gate())
    tid = eng.create_task(_intent(), track="build")
    env = eng.get(tid)
    # Even submitting the *right* first tool with a payload that names DONE changes only to the
    # engine's declared next state -- the caller's "state" is ignored (state is server-owned).
    env = eng.step(tid, "cortex_report_findings",
                   {"evidence": [{"claim": "x", "source": "y"}], "state": "DONE",
                    "skip_to": "DONE"}, seq=env["seq"])
    assert env["ok"] is True
    assert env["state"] == "RESEARCH"  # engine's next, NOT the caller-asserted DONE
    eng.close()


# --------------------------------------------------------------------------------------------
# 4. A hollow closeout (empty payload) cannot reach DONE -- the grounding gate refuses it, so
#    the driver returns "incomplete", stuck at CLOSEOUT.
# --------------------------------------------------------------------------------------------
def test_empty_closeout_cannot_reach_done(tmp_path: Path) -> None:
    bad = dict(_GOOD)
    bad["CLOSEOUT"] = {}  # forge: reach the closeout, submit nothing
    res = p2.run_build(_intent(), _scripted(bad), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "CLOSEOUT"  # walked everything, blocked at the grounded-closeout wall
    # and the closeout submissions all failed the gate
    closeout_tries = [t for t in res["trail"] if t["state"] == "CLOSEOUT"]
    assert closeout_tries and all(t["gate"] and t["gate"].get("pass") is False
                                  for t in closeout_tries)


# --------------------------------------------------------------------------------------------
# 5. Ungrounded findings (empty evidence) do not advance -- the model is held at SEARCH_BRAIN,
#    so it never reaches DONE (research-first is coerced, not merely suggested).
# --------------------------------------------------------------------------------------------
def test_ungrounded_findings_do_not_advance(tmp_path: Path) -> None:
    bad = dict(_GOOD)
    bad["SEARCH_BRAIN"] = {"summary": "I did not search", "evidence": []}
    res = p2.run_build(_intent(), _scripted(bad), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "SEARCH_BRAIN"  # never got past the first grounded phase
    assert all(t["state"] == "SEARCH_BRAIN" for t in res["trail"])


# --------------------------------------------------------------------------------------------
# 6. The external model's OUTPUT cannot forge state: even a model that emits {"state":"DONE"}
#    text every turn is walked one gated transition at a time and cannot self-promote.
# --------------------------------------------------------------------------------------------
def test_model_output_cannot_forge_state(tmp_path: Path) -> None:
    def liar(prompt: str) -> str:
        # The model tries to skip: it just claims it's done, with no evidence.
        return json.dumps({"state": "DONE", "done": True, "result": "trust me"})
    res = p2.run_build(_intent(), liar, db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "SEARCH_BRAIN"  # stuck at phase 1: no evidence, no advance


# --------------------------------------------------------------------------------------------
# 7. Rework exhaustion abandons via a server-written closeout -- ABANDONED is NOT "done".
#    A model whose REVIEW always self-declares a scope mismatch cannot launder to done.
# --------------------------------------------------------------------------------------------
def test_rework_exhaustion_abandons_not_done(tmp_path: Path) -> None:
    bad = dict(_GOOD)
    # REVIEW self-declares the deliverable does not match the request -> scope gate fails ->
    # rework to IMPLEMENT, bounded by rework_cap/esc_cap -> ABANDONED.
    bad["REVIEW"] = {"review": "wrong thing built",
                     "scope_check": {"delivered": "something else entirely",
                                     "matches_request": False}}
    res = p2.run_build(_intent(), _scripted(bad), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] == "abandoned", res
    assert res["state"] == "ABANDONED"
    assert res["status"] != "done"


# --------------------------------------------------------------------------------------------
# 8. Direct gate unit tests: the grounded-closeout definition is deterministic.
# --------------------------------------------------------------------------------------------
def test_grounding_gate_closeout_requires_evidence_and_fields() -> None:
    g = se.build_grounding_gate
    # No evidence accumulated -> ungrounded even with task+result.
    task_no_ev = {"track": "build", "intent": {"seeking": SEEKING, "evidence": []}}
    r = g("CLOSEOUT", task_no_ev, {"task": SEEKING, "result": "done"})
    assert r["pass"] is False and r["code"] == "UNGROUNDED_CLOSEOUT"
    # Evidence present but missing result -> refused.
    task_ev = {"track": "build", "intent": {"seeking": SEEKING, "evidence": [{"c": 1}]}}
    r2 = g("CLOSEOUT", task_ev, {"task": SEEKING, "result": "  "})
    assert r2["pass"] is False and r2["code"] == "UNGROUNDED_CLOSEOUT"
    # Evidence present AND task+result -> grounded.
    r3 = g("CLOSEOUT", task_ev, {"task": SEEKING, "result": "implemented"})
    assert r3["pass"] is True and r3.get("grounding") == "ok"


def test_grounding_gate_findings_requires_nonempty_evidence() -> None:
    g = se.build_grounding_gate
    task = {"track": "build", "intent": {"seeking": SEEKING}}
    assert g("SEARCH_BRAIN", task, {"evidence": []})["pass"] is False
    assert g("SEARCH_BRAIN", task, {"evidence": [{"claim": "x"}]})["pass"] is True
    # non-build tracks pass straight through (scoped gate)
    other = {"track": "research", "intent": {}}
    assert g("SEARCH_BRAIN", other, {"evidence": []})["pass"] is True


def test_grounding_gate_does_not_touch_other_tracks() -> None:
    g = se.build_grounding_gate
    # A research-track CLOSEOUT-named phase is not gated by the build grounding rule.
    r = g("CLOSEOUT", {"track": "research", "intent": {}}, {})
    assert r["pass"] is True


# ============================================================================================
# sol@xhigh red-team regression tests (reviewed/plane2-enforcement-sol-xhigh-review-2026-07-14.md)
# ============================================================================================

def test_sol_finding1_degenerate_evidence_map_cannot_reach_done(tmp_path: Path) -> None:
    """The exact model-response map sol proved reached DONE before the fix: degenerate evidence
    ([{}], whitespace) + hollow intermediate phases + an unrelated two-string closeout. Now it
    must NOT reach done -- it is held at the first phase whose evidence is degenerate."""
    hostile = {
        "SEARCH_BRAIN": {"evidence": [{}]},
        "RESEARCH": {"evidence": ["   "]},
        "PLAN": {}, "SPEC": {}, "IMPLEMENT": {}, "REVIEW": {},
        "CLOSEOUT": {"task": "x", "result": "y"},
    }
    res = p2.run_build(_intent(), _scripted(hostile), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "SEARCH_BRAIN"  # [{}] is not meaningful evidence


@pytest.mark.parametrize("degen", [[{}], ["   "], [None], [False], [[]], []])
def test_degenerate_evidence_variants_refused(degen) -> None:
    task = {"track": "build", "intent": {"seeking": SEEKING}}
    assert se.build_grounding_gate("SEARCH_BRAIN", task, {"evidence": degen})["pass"] is False


def test_hollow_intermediate_phase_does_not_advance(tmp_path: Path) -> None:
    """PLAN={} used to pass; now an empty intermediate phase is refused, so the walk halts."""
    bad = dict(_GOOD)
    bad["PLAN"] = {}
    res = p2.run_build(_intent(), _scripted(bad), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "PLAN"


def test_caller_preseeded_evidence_does_not_grease_the_path(tmp_path: Path) -> None:
    """sol finding #3: caller/model-seeded intent.evidence must NOT substitute for the research
    phases. The driver strips it, so an empty findings phase is still refused."""
    poisoned = dict(_GOOD)
    poisoned["SEARCH_BRAIN"] = {"evidence": []}  # model reports nothing this phase
    intent = {"seeking": SEEKING, "evidence": [{"claim": "forged-preseed"}]}
    res = p2.run_build(intent, _scripted(poisoned), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "SEARCH_BRAIN"  # preseeded evidence was stripped, gate still refuses


def test_string_evidence_in_intent_does_not_crash(tmp_path: Path) -> None:
    """sol finding #6: a non-list intent.evidence (e.g. "seed") used to crash the engine's
    .extend(); the driver strips it, so a good walk still completes without an exception."""
    intent = {"seeking": SEEKING, "evidence": "seed"}
    res = p2.run_build(intent, _scripted(_GOOD), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] == "done"


def test_review_scope_mismatch_falsy_is_rejected(tmp_path: Path) -> None:
    """sol finding #4b: matches_request=0 (falsy, not literal False) declares a mismatch and
    must fail -> rework -> abandon, never done."""
    bad = dict(_GOOD)
    bad["REVIEW"] = {"review": "done-ish", "scope_check": {"matches_request": 0,
                                                           "delivered": "   "}}
    res = p2.run_build(_intent(), _scripted(bad), db_path=str(tmp_path / "e.sqlite"))
    assert res["status"] != "done"
    assert res["state"] == "ABANDONED"


def test_extract_json_object_is_string_aware(tmp_path: Path) -> None:
    """sol finding #7: a brace inside a JSON string value must not misbalance the scanner."""
    prose = 'here you go: {"evidence": [{"claim": "a } b", "source": "s"}], "ok": true} -- done'
    obj = p2._extract_json_object(prose)
    assert obj is not None and obj["evidence"][0]["claim"] == "a } b"


def test_extract_json_object_bounded_no_crash() -> None:
    """Deep nesting / runaway input returns None gracefully rather than raising."""
    assert p2._extract_json_object("{" * 5000) is None
    assert p2._extract_json_object("no json here") is None


def test_intermediate_phase_field_gate_unit() -> None:
    g = se.build_grounding_gate
    t = {"track": "build", "intent": {"seeking": SEEKING}}
    assert g("PLAN", t, {})["pass"] is False
    assert g("PLAN", t, {"plan": []})["pass"] is False
    assert g("PLAN", t, {"plan": ["step"]})["pass"] is True
    assert g("SPEC", t, {"spec": "   "})["pass"] is False
    assert g("IMPLEMENT", t, {"patch": "real diff"})["pass"] is True
