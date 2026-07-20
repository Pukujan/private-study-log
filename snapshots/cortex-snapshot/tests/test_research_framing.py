"""Framing robustness: research must work with ANY model (not just Anthropic), survive
reasoning-model output shapes, and degrade gracefully when no model is available. These
are the fixes from the 2026-07-06 lane (tested live against qwen35b/yolo-qwen; here as
deterministic unit tests with no network)."""

from __future__ import annotations

import cortex_core.research as R
from cortex_core.research import _extract_json_list, frame_question


# ---- _extract_json_list: robust to reasoning-model wrappers -----------------

def test_extract_json_list_reasoning_shapes() -> None:
    # POSITIVE: reasoning prose then array (the exact qwen35b/GLM failure shape)
    assert _extract_json_list(
        'Here is my thinking:\n1. decompose\nSub-questions:\n["What is X?", "What is Y?"]'
    ) == ["What is X?", "What is Y?"]
    # <think> tags
    assert _extract_json_list('<think>reasoning...</think>\n["a", "b", "c"]') == ["a", "b", "c"]
    # ```json fenced block
    assert _extract_json_list('Sure!\n```json\n["a?", "b?"]\n```\ndone') == ["a?", "b?"]
    # clean JSON (frontier models)
    assert _extract_json_list('["clean one", "clean two"]') == ["clean one", "clean two"]


def test_extract_json_list_garbage_returns_none() -> None:
    # NEGATIVE: no array anywhere -> None (caller falls back to the question, never crashes)
    assert _extract_json_list("I cannot help with that.") is None
    assert _extract_json_list("") is None


# ---- frame_question: graceful degrade + reasoning integration --------------

def test_frame_question_graceful_when_no_model(monkeypatch) -> None:
    # an unconfigured tier -> _llm_complete returns None -> use the question as-is, no crash
    monkeypatch.setattr(R, "_llm_complete", lambda *a, **k: None)
    assert frame_question("original question", model="doesnotexist") == ["original question"]


def test_frame_question_extracts_from_reasoning_model(monkeypatch) -> None:
    # a reasoning model wraps its JSON; frame_question must still recover the sub-questions
    reasoning_output = (
        "Let me think step by step about how to break this down...\n\n"
        'Final answer:\n["sub A about benchmarks", "sub B about adoption"]'
    )
    monkeypatch.setattr(R, "_llm_complete", lambda *a, **k: reasoning_output)
    subs = frame_question("benchmarks question", model="qwen35b")
    assert subs == ["sub A about benchmarks", "sub B about adoption"]


def test_frame_question_rejects_placeholder_echo_is_a_prompt_concern(monkeypatch) -> None:
    # If a weak model echoes literal placeholders they still PARSE (that's a prompt-quality
    # issue, fixed by the concrete example) -- frame_question's job is to not crash and to
    # return whatever parsed; document the boundary so this isn't mistaken for a parser bug.
    monkeypatch.setattr(R, "_llm_complete", lambda *a, **k: '["sub-question 1", "sub-question 2"]')
    assert frame_question("q", model="weak") == ["sub-question 1", "sub-question 2"]
