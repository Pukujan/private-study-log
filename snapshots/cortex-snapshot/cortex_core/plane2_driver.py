"""Plane-2 enforcement: drive an EXTERNAL model through the build-track state machine.

The two planes, and why they differ (terra's verdict, 2026-07-12):

  Plane-1 (Claude Code agents): the agent owns its own tool loop -- native tools + MCP tools.
  The server can only DISCLOSE the phase's tools (cortex_run_start/step, phase_legal_tools);
  it cannot COERCE, because the agent can ignore the chart, call native tools, and forge a
  closeout. terra proved this can't be forced, so Plane-1 stays disclosure-only
  (protocol/STATE-MACHINE.md).

  Plane-2 (external-model driver, e.g. a collaborator's Hermes on 9router: GLM/Qwen/...):
  the model has NO tool loop of its own. It is a text-in/text-out callable. THIS driver is
  the only thing that talks to the StateEngine, and it ALWAYS submits the engine's declared
  advance_tool for the CURRENT state -- the model only fills the phase's content slot. The
  model never names a state, never chooses the next transition; the DETERMINISTIC engine owns
  every transition (state_engine.StateEngine). "Skipping a phase" is therefore not expressible:
  the driver cannot submit a later phase's tool early (the engine refuses ILLEGAL_IN_STATE),
  and the ONLY path to DONE is CLOSEOUT's advance passing its gate. This is the coercion path
  terra endorsed as genuinely deterministic for Plane-2.

What this adds on top of the ordered walk: order alone does not stop a weak model from
advancing every phase with EMPTY/degenerate payloads to a hollow DONE. The driver runs the build
track under `build_grounding_gate` (state_engine), which requires MEANINGFUL (non-degenerate)
content at every phase and a closeout that COEXISTS with meaningful evidence the WALK produced
(the driver strips any caller-seeded evidence from the intent, so that provenance is real).
Reaching status "done" therefore requires: (1) every phase, in order (engine), AND (2) a closeout
coexisting with walk-produced evidence (gate). Neither is bypassable by the external model, which
supplies only slot content.

Anti-bloat: this is a driver module + `build_grounding_gate` (a gate function) -- NO new MCP
tool surface. Reversible: it constructs its OWN StateEngine with the grounding gate, so the
Plane-1 MCP engine (mcp._run_engine) is untouched.

Honest limits (stated plainly; hardened per the sol@xhigh red-team,
reviewed/plane2-enforcement-sol-xhigh-review-2026-07-14.md):
  - Enforcement is unbypassable for the MODEL (slot content only). It relies on the DRIVER being
    this shipped Cortex code with its DEFAULT own-engine + grounding gate. Passing your own
    `engine`/`gate`/`slot_filler` is a TRUSTED integration seam that can relax the default gate --
    outside the model's reach, but a caller footgun; the coercion guarantee is about the default
    path a weak external model is driven through.
  - The gates check content is PRESENT and NON-DEGENERATE and that a closeout COEXISTS with
    walk-produced evidence. They do NOT verify the evidence semantically SUPPORTS the closeout (no
    citation/derivation binding) or that any content is TRUE -- that needs the Phase-4.4 LLM-judge
    as the gate `base`. Structural coercion is real; semantic verification is a separate axis.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from cortex_core.state_engine import (
    StateEngine,
    build_grounding_gate,
    make_universal_gate,
)

# Belt-and-braces bound on the drive loop (the chart itself terminates via rework/esc caps ->
# ABANDONED). Matches hybrid_build._MAX_STEPS. A model that never grounds a phase exhausts this
# and returns an honest "incomplete" -- never a fake "done".
_MAX_STEPS = 60

SlotFiller = Callable[[str, str, str, dict[str, Any]], dict[str, Any]]
"""(state, advance_tool, model_output, envelope) -> payload dict for engine.step()."""


def default_build_gate() -> Callable[..., dict[str, Any]]:
    """The build-track gate pipeline the Plane-2 driver enforces by default: grounding floor
    (findings evidence + grounded closeout) composed with the existing REVIEW scope check."""
    return make_universal_gate(base=build_grounding_gate)


# Bound on the text we scan for embedded JSON (sol@xhigh finding #7: no size/depth guard). A
# model turn that is larger than this, or a JSON nesting deeper than this, is treated as "no
# parseable object" -> the phase's grounding gate refuses it (bounded retries), never a crash.
_MAX_SCAN = 200_000
_MAX_DEPTH = 200


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort: pull the first balanced JSON object out of a model's text response, STRING-
    AWARE so a brace inside a JSON string value does not misbalance the scan (sol@xhigh finding
    #7). Bounded in size and nesting depth; a RecursionError/ValueError from json.loads is caught.
    Returns the parsed dict, or None if none is found / it isn't an object."""
    if not isinstance(text, str) or not text:
        return None
    text = text[:_MAX_SCAN]
    # Fast path: the whole string is JSON.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (ValueError, RecursionError):
        pass
    # Scan for the first {...} that parses, tracking string/escape state so braces inside quoted
    # strings are ignored, and bailing on runaway nesting.
    for m in re.finditer(r"\{", text):
        start = m.start()
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
                if depth > _MAX_DEPTH:
                    break
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except (ValueError, RecursionError):
                        break
                    return obj if isinstance(obj, dict) else None
    return None


def default_slot_filler(state: str, advance_tool: str, model_output: str,
                        envelope: dict[str, Any]) -> dict[str, Any]:
    """Map a model's free-text/JSON output into the advance tool's payload.

    The model is responsible for producing correct SHAPE; the driver does not fabricate
    content. If the model emits a JSON object, it is used verbatim (so a well-behaved model
    controls its own evidence/plan/closeout). If it emits only prose, we preserve it under a
    phase-appropriate key -- which for findings/closeout will (correctly) FAIL the grounding
    gate and force a retry, because prose alone is not a grounded phase pass. That refusal IS
    the enforcement working, not a driver bug.
    """
    obj = _extract_json_object(model_output)
    if obj is not None:
        return obj
    text = model_output if isinstance(model_output, str) else str(model_output)
    # No JSON: park the prose under a per-phase key. Grounding-gated phases will refuse this
    # (no evidence / no task+result), which is the intended coercion.
    if advance_tool == "cortex_report_findings":
        return {"summary": text, "evidence": []}
    if advance_tool == "cortex_write_closeout":
        return {"note": text}
    return {"note": text}


def _phase_prompt(intent: dict[str, Any], envelope: dict[str, Any]) -> str:
    """The prompt the external model sees for ONE phase. It is told the phase instruction and
    the ONE tool it is filling the slot for -- never a menu of states to choose from (the model
    cannot select a transition; the engine does)."""
    seeking = (intent or {}).get("seeking", "")
    state = envelope.get("state")
    instruction = envelope.get("instruction", "")
    legal = envelope.get("legal_tools", [])
    advance_tool = legal[0] if legal else "(none)"
    evidence = (envelope.get("intent") or intent or {}).get("evidence", [])
    return (
        f"TASK: {seeking}\n"
        f"CURRENT PHASE: {state}\n"
        f"INSTRUCTION: {instruction}\n"
        f"Produce ONLY the content for this phase, as a JSON object, to submit via "
        f"'{advance_tool}'.\n"
        f"For SEARCH_BRAIN/RESEARCH: {{\"evidence\": [{{\"claim\": ..., \"source\": ...}}], "
        f"\"summary\": ...}} (a non-empty evidence list; 'no corpus coverage' is a legal entry).\n"
        f"For PLAN: {{\"plan\": [step, ...]}}. For SPEC: {{\"spec\": ...}}. "
        f"For IMPLEMENT: {{\"patch\": ...}}.\n"
        f"For REVIEW: {{\"review\": ..., \"scope_check\": {{\"delivered\": ..., "
        f"\"matches_request\": true}}}}.\n"
        f"For CLOSEOUT: {{\"task\": ..., \"result\": ..., \"test_status\": ...}} "
        f"(task and result must be non-empty).\n"
        f"Evidence gathered so far: {json.dumps(evidence)[:1200]}\n"
    )


def run_build(intent: dict[str, Any], llm: Callable[[str], str], *,
              db_path: str = ":memory:", workspace: str | None = None,
              engine: StateEngine | None = None,
              slot_filler: SlotFiller | None = None,
              prompt_fn: Callable[[dict[str, Any], dict[str, Any]], str] | None = None,
              actor: str = "plane2-external", max_steps: int = _MAX_STEPS,
              lease_s: int = 600) -> dict[str, Any]:
    """Drive an external `llm` through the full build track under deterministic coercion.

    `intent` = {"seeking": "<what this task is>"}. `llm(prompt) -> str` is the external model
    (GLM/Qwen/...); it only ever fills the current phase's slot. Every transition is owned by
    the StateEngine; the loop always submits the engine's declared advance tool for the current
    state, so the model cannot skip, reorder, or jump to DONE.

    Returns {"status": "done" | "abandoned" | "incomplete", "task_id", "state", "seq",
    "steps", "trail"} where "done" is granted ONLY when the engine reaches terminal DONE via a
    grounded closeout. `trail` is the ordered list of (state, advance_tool, ok) submissions --
    the audit of the coerced walk.
    """
    if not isinstance(intent, dict):
        raise ValueError("intent must be a dict, e.g. {'seeking': '<task>'}")
    fill = slot_filler or default_slot_filler
    prompt = prompt_fn or _phase_prompt

    # sol@xhigh finding #3/#6: strip any caller-supplied `evidence`/`phase` from the seed intent.
    # The grounded-closeout check reads task.intent.evidence as PROOF THE WALK PRODUCED EVIDENCE;
    # if a caller (or the external model, via the intent) could pre-seed it, that provenance claim
    # would be false, and a non-list seed (`"evidence": "seed"`) would crash the engine's
    # `.extend()` later. Only the research phases may populate it.
    seed = {k: v for k, v in intent.items() if k not in ("evidence", "phase")}

    own_engine = engine is None
    if own_engine:
        engine = StateEngine(db_path, gate=default_build_gate(),
                             workspace=workspace)
    try:
        tid = engine.create_task(seed, track="build", lease_s=lease_s, actor=actor)
        env = engine.get(tid)
        trail: list[dict[str, Any]] = []
        for _ in range(max_steps):
            state, seq = env["state"], env["seq"]
            if state in ("DONE", "ABANDONED"):
                break
            legal = env.get("legal_tools") or []
            if not legal:  # terminal / nothing to submit
                break
            advance_tool = legal[0]  # phase_legal_tools contract: advance tool is first
            # The engine hands the driver the ONE tool + instruction for this phase; the model
            # fills the slot. The model's text can SAY "skip to done" -- it changes nothing,
            # because the driver still submits THIS phase's advance tool and the engine still
            # walks exactly one declared transition.
            model_out = llm(prompt(intent, env))
            payload = fill(state, advance_tool, model_out, env)
            env = engine.step(tid, advance_tool, payload, seq=seq, actor=actor)
            trail.append({"state": state, "tool": advance_tool,
                          "ok": bool(env.get("ok")),
                          "to_state": env.get("state"),
                          "gate": env.get("gate")})
            if not env.get("ok"):
                # A refusal (e.g. a stale seq, illegal tool -- shouldn't happen since we submit
                # the declared tool) writes NOTHING; resync from the engine and retry.
                env = engine.get(tid)
        final = engine.get(tid)
        status = {"DONE": "done", "ABANDONED": "abandoned"}.get(final["state"], "incomplete")
        return {"status": status, "task_id": tid, "state": final["state"],
                "seq": final["seq"], "steps": len(trail), "trail": trail}
    finally:
        if own_engine:
            engine.close()
