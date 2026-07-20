"""STAGE 1.5 -- the bake-off runs ON the state machine.

Per docs/research/CONSOLIDATION-bakeoff-on-state-machine-2026-07-06.md: each v4 CONDITION is a
config of the ONE engine (a gate + a guidance flag), and running a subject through the engine
under that config IS a run -- the engine's terminal state and event log ARE the objective
result (no separate rig, no judge). This is the FIRST CUT: baseline / l1_suggest / l1_force,
proving the FORCE lever *measurably changes a weak subject's behavior through the engine*.

Follow-ups (not this cut): L2 (learn-from-failures via the miner reading the event log),
sham-force, active-control, and the real small-model subject (the dogfood / acceptance run) --
that last one plugs into `run_episode` unchanged; only the `subject` callable differs.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from .llm_parse import extract_tool_call
from .state_engine import BUILD_TRACK, StateEngine, default_gate

# The guidance lever, expressed as the SEARCH_BRAIN instruction the engine emits (so it reaches
# the model through the envelope, not a subject-side hack). This is what makes l1_suggest
# genuinely differ from baseline.
_SEARCH_GUIDANCE = {
    "none":    "Call cortex_report_findings with your findings for this phase.",
    "suggest": "You may want to cortex_search the corpus first, then call cortex_report_findings.",
    "force":   "Search the corpus with cortex_search, then call cortex_report_findings with the evidence.",
}


def _chart_for(guidance: str) -> dict[str, Any]:
    """A build-track chart variant whose SEARCH_BRAIN instruction carries the condition's
    guidance. Deep-copied so variants never alias BUILD_TRACK."""
    chart = copy.deepcopy(BUILD_TRACK)
    chart["states"]["SEARCH_BRAIN"]["instruction"] = _SEARCH_GUIDANCE.get(guidance, _SEARCH_GUIDANCE["none"])
    return chart

# subject(view) -> (tool, payload). `view` is the engine's public task dict (state, seq,
# legal_tools, intent) -- the same shape a real model reads from an envelope.
Subject = Callable[[dict[str, Any]], "tuple[str, Any]"]


def _force_search_gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
    """L1-FORCE lever: you cannot leave SEARCH_BRAIN without reporting evidence, and evidence
    requires an actual search. Downstream phases fall back to the permissive default gate."""
    if phase == "SEARCH_BRAIN":
        ev = payload.get("evidence") if isinstance(payload, dict) else None
        if not ev:
            return {"pass": False,
                    "reason": "forced pipeline: search the brain and report evidence before advancing"}
    return default_gate(phase, task, payload)


# Each v4 condition is a config of the one engine. baseline/suggest share the permissive gate
# and differ only in the guidance the envelope carries (a follow-up, once real subjects read
# guidance); l1_force differs by its gate. L2/sham/active-control land here later.
CONDITIONS: dict[str, dict[str, Any]] = {
    "baseline":   {"gate": default_gate,       "guidance": "none",    "chart": _chart_for("none")},
    "l1_suggest": {"gate": default_gate,       "guidance": "suggest", "chart": _chart_for("suggest")},
    "l1_force":   {"gate": _force_search_gate, "guidance": "force",   "chart": _chart_for("force")},
}


def make_coding_gate(check: Callable[[str], "tuple[bool, str]"]) -> Callable:
    """Turn a DETERMINISTIC code checker into an engine gate (GAP-CORTEX-0022 mechanism).

    `check(patch) -> (passed, detail)` runs the submitted implementation. The gate applies it
    at the coding phases (IMPLEMENT/REVIEW) and defers to the permissive default elsewhere.
    THIS is what makes the bake-off measure real CORRECTNESS -- a deterministic checker decides
    pass/fail, never a judge (the anti-circularity core). Fail-closed: a checker that raises is
    a fail, so a broken checker can't wave wrong code through. Compose with run_episode(...,
    gate=make_coding_gate(...)).
    """
    def gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
        if phase in ("IMPLEMENT", "REVIEW"):
            patch = payload.get("patch", "") if isinstance(payload, dict) else ""
            try:
                passed, detail = check(patch)
            except Exception as exc:  # noqa: BLE001 -- arbitrary checker
                return {"pass": False, "reason": f"checker raised: {exc!r}"}
            return {"pass": bool(passed), "reason": str(detail)}
        return default_gate(phase, task, payload)
    return gate


# --- the model-backed subject: the harness that makes small models work --------------------
# Validated overnight (2026-07-06) on qwen-4b (ollama) / qwen35b / mimo (openrouter): all
# three drive the engine to a passing closeout, almost entirely with genuine tool calls. The
# levers that got there, in order of impact: (1) minimal per-state legal tools (the engine's
# job); (2) ONE imperative instruction per envelope; (3) anti-search-loop guidance (a 4B model
# will otherwise call search forever and never advance); (4) robust nested-JSON tool-call parse
# (extract_tool_call -- reasoning models nest {"tool",...,"payload":{...}}); (5) one honest
# format-retry; (6) an HONEST fallback -- coerce to the advance tool with an EMPTY payload,
# NEVER fabricate evidence, so the force gate genuinely blocks a model that won't search.


def _subject_prompt(view: dict[str, Any], already_searched: bool) -> tuple[str, str]:
    state = view["state"]
    spec = BUILD_TRACK["states"].get(state, {})
    advance = spec.get("advance_tool", (view["legal_tools"] or [""])[0])
    # Instruction from the ENGINE's envelope (varies by condition/chart), not the global chart.
    instruction = view.get("instruction") or spec.get("instruction", "")
    anti_loop = f"You have ALREADY searched. Do NOT search again -- call {advance} now.\n" if already_searched else ""
    prompt = (
        "You are doing a software task ONE STEP AT A TIME. A server tells you the phase and the "
        "single action to take now.\n"
        f"PHASE: {state}\n"
        f"DO THIS NOW: {instruction}\n"
        f"{anti_loop}"
        f"ALLOWED TOOLS (pick exactly ONE): {view['legal_tools']}\n"
        "Do the action for THIS phase, then STOP; never repeat a previous action.\n"
        'Reply with a single JSON object as the LAST line of your reply:\n'
        '{"tool":"<one allowed tool>","payload":{...}}\n'
        'search payload: {"query":"..."}; '
        'report/submit/closeout payload: {"evidence":[{"claim":"...","source":"..."}],"result":"done"}'
    )
    return prompt, advance


def make_model_subject(complete: Callable[[str], str], *, log: list | None = None) -> Subject:
    """Wrap a raw completer ``complete(prompt) -> text`` into a run_episode subject. `log`
    (if given) receives ("RETRY_OK"|"COERCE", state) tuples so a caller can measure how often
    the model needed help vs drove cleanly. Injectable completer keeps this unit-testable
    without real model calls."""
    searched_phase = {"name": None}

    def subject(view: dict[str, Any]) -> "tuple[str, Any]":
        legal = view["legal_tools"]
        prompt, advance = _subject_prompt(view, searched_phase["name"] == view["state"])
        obj = extract_tool_call(complete(prompt) or "", legal)
        tool = obj.get("tool")
        if tool not in legal:  # one honest format-retry (same decision, cleaner formatting)
            obj2 = extract_tool_call(
                complete(prompt + "\n\nOutput ONLY the JSON object. No reasoning, no prose.") or "", legal)
            if obj2.get("tool") in legal:
                obj, tool = obj2, obj2.get("tool")
                if log is not None:
                    log.append(("RETRY_OK", view["state"]))
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if tool == "cortex_search":
            searched_phase["name"] = view["state"]
        if tool not in legal:  # honest fallback: advance tool, EMPTY payload (force still blocks)
            if log is not None:
                log.append(("COERCE", view["state"]))
            tool, payload = advance, {}
        return tool, payload

    return subject


def tier_subject(tier: str, *, max_tokens: int = 2000, log: list | None = None) -> Subject:
    """Convenience: a model-backed subject driven by a judge-tier model (qwen35b/ollama/
    openrouter/...). Imports the dispatcher lazily so run_episode stays dependency-light."""
    from .research import _llm_complete
    return make_model_subject(lambda p: _llm_complete(p, tier, max_tokens) or "", log=log)


# --- the CODING subject: drives the pipeline AND actually solves a real task -----------------
# Validated end-to-end 2026-07-06: qwen-4b / mimo / qwen35b all SOLVED a real blinded-authored
# task (code passed the deterministic subprocess checker incl. the hidden holdout, no judge).

import re as _re


def _extract_solution(text: str) -> str:
    """Pull the function code out of a model reply: prefer a ```code``` fence, else keep from
    the first `def ` so leading prose is dropped."""
    m = _re.search(r"```(?:python)?\s*(.+?)```", text or "", _re.DOTALL)
    code = m.group(1) if m else (text or "")
    i = code.find("def ")
    return code[i:] if i >= 0 else code


def make_coding_subject(complete: Callable[[str], str], task: dict[str, Any]) -> Subject:
    """A subject that drives the pipeline AND actually SOLVES `task` at IMPLEMENT -- it writes
    code for ``task['entry']`` via ``complete()`` so a ``make_coding_gate(build_checker(task))``
    measures REAL correctness. Searches once (anti-loop), advances, solves, carries the patch to
    REVIEW. `complete(prompt) -> text` is injectable (unit-testable without model calls)."""
    solved = {"code": ""}
    searched = {"done": False}

    def subject(view: dict[str, Any]) -> "tuple[str, Any]":
        state, legal = view["state"], view["legal_tools"]
        advance = legal[0]
        if state == "SEARCH_BRAIN" and not searched["done"] and "cortex_search" in legal:
            searched["done"] = True
            return "cortex_search", {"query": task.get("title", "")}
        if state == "IMPLEMENT":
            prompt = (f"Solve this Python task. Output ONLY the function code, no prose, no tests.\n\n"
                      f"{task.get('prompt', '')}\n\nWrite def {task.get('entry', 'solve')}(...).")
            solved["code"] = _extract_solution(complete(prompt) or "")
            return advance, {"patch": solved["code"], "result": "done"}
        if state == "REVIEW":
            return advance, {"patch": solved["code"], "verdict": "pass"}
        return advance, {"evidence": [{"claim": "reviewed", "source": "task"}], "result": "done"}

    return subject


def tier_coding_subject(tier: str, task: dict[str, Any], *, max_tokens: int = 900) -> Subject:
    """A coding subject driven by a judge-tier model."""
    from .research import _llm_complete
    return make_coding_subject(lambda p: _llm_complete(p, tier, max_tokens) or "", task)


def run_episode(condition: str, task_intent: dict[str, Any], subject: Subject, *,
                db_path: str = ":memory:", max_steps: int = 60,
                gate: Callable | None = None) -> dict[str, Any]:
    """Drive `subject` through a fresh engine under `condition` until terminal or max_steps.

    Returns the objective result the bake-off measures -- all read from the engine, no judge:
    outcome (terminal state), reached_done, steps (the budget proxy), whether it searched,
    the refusal/gate-fail count, and whether a closeout was written.

    `gate` overrides the condition's gate -- pass ``make_coding_gate(check)`` to measure real
    task CORRECTNESS (a deterministic checker) instead of mere pipeline completion.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; known: {sorted(CONDITIONS)}")
    cfg = CONDITIONS[condition]
    eng = StateEngine(db_path, chart=cfg.get("chart"), gate=gate or cfg["gate"])
    tid = eng.create_task(intent=dict(task_intent))
    searched = False
    refused = 0
    steps = 0
    for _ in range(max_steps):
        view = eng.get(tid)
        if view["state"] in ("DONE", "ABANDONED"):
            break
        tool, payload = subject(view)
        if tool == "cortex_search":
            searched = True
        env = eng.step(tid, tool=tool, payload=payload, seq=view["seq"])
        steps += 1
        if not env.get("ok"):
            refused += 1
    final = eng.get(tid)
    return {
        "condition": condition,
        "outcome": final["state"],
        "reached_done": final["state"] == "DONE",
        "steps": steps,
        "searched": searched,
        "refused": refused,
        "closeout_written": bool(final.get("closeout_written")),
    }
