"""Tests for the shared reasoning-model-robust JSON parser (cortex_core/llm_parse.py) —
the ONE place that recovers JSON from qwen35b/GLM chain-of-thought, used by the judge
verdict parser, research framing, and exemplar grading. Found by the yolo-qwen sweep:
frontier models emit clean JSON; reasoning models don't, and a greedy {.*} silently
dropped their answers (verdict -> UNVERIFIABLE, framing -> 1 sub-question)."""

from __future__ import annotations

from cortex_core.llm_parse import extract_json_list, extract_json_object


# ---- objects (judge verdicts, exemplar levels) -----------------------------

def test_object_clean_and_fenced() -> None:
    assert extract_json_object('{"verdict":"supported","confidence":0.9}') == {"verdict": "supported", "confidence": 0.9}
    assert extract_json_object('```json\n{"level":"good"}\n```') == {"level": "good"}


def test_object_reasoning_and_braces_and_think() -> None:
    # reasoning prose then object
    assert extract_json_object('Let me reason.\n{"verdict":"refuted"}') == {"verdict": "refuted"}
    # braces IN the reasoning before the real object -> greedy {.*} used to break here
    assert extract_json_object('The format is {a, b}. Final: {"verdict":"supported","confidence":0.7}') \
        == {"verdict": "supported", "confidence": 0.7}
    # <think> tags with a stray brace inside
    assert extract_json_object('<think>maybe {note} supported</think>\n{"verdict":"supported"}') \
        == {"verdict": "supported"}
    # multiple objects -> the LAST one (the answer after reasoning) wins
    assert extract_json_object('draft {"verdict":"unsupported"} ... corrected {"verdict":"supported"}') \
        == {"verdict": "supported"}


def test_object_garbage_returns_none() -> None:
    assert extract_json_object("I refuse to answer.") is None
    assert extract_json_object("") is None
    assert extract_json_object(None) is None


# ---- lists (research framing) ----------------------------------------------

def test_list_reasoning_shapes() -> None:
    assert extract_json_list('["a","b"]') == ["a", "b"]
    assert extract_json_list('thinking...\n["x?", "y?"]') == ["x?", "y?"]
    assert extract_json_list('<think>reason</think>\n["one","two","three"]') == ["one", "two", "three"]
    assert extract_json_list('```json\n["p","q"]\n```') == ["p", "q"]


def test_list_garbage_returns_none() -> None:
    assert extract_json_list("no array here") is None
    assert extract_json_list(None) is None
