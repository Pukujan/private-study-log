"""TDD (RED-first) for CoT/reasoning capture in the live cross-vendor generator.

Success conditions (see docs/research/cot-trace-capture-2026-07-11.md):
  * every objectively-labeled live candidate is wired into trace_capture,
  * a candidate whose model emitted reasoning gets a NON-EMPTY `cot` (no silent drop),
  * `gate_verdict` is the ast_checker verdict (PASS/FAIL) -> distillation stays judge-free.

The HTTP layer is mocked so this is offline + deterministic. The real ast_checker
(the deterministic label authority) is NOT mocked.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

pytest.importorskip("bfcl_eval")

from cortex_core import trace_capture as tc  # noqa: E402
from evals.objective_tool_calling import generate_live as gl  # noqa: E402


class _Cfg:
    url = "https://fake.local/v1"
    model = "fake-reasoner-4b"
    key = "sk-test"


# One minimal, real BFCL "simple" case + its ground truth.
_REC = {
    "id": "simple_python_test",
    "question": [[{"role": "user", "content": "Add 2 and 3."}]],
    "function": [{
        "name": "add", "description": "add two integers",
        "parameters": {"type": "dict", "properties": {
            "a": {"type": "integer", "description": "first"},
            "b": {"type": "integer", "description": "second"}},
            "required": ["a", "b"]}}],
}
_GT = [{"add": {"a": [2], "b": [3]}}]


def _fake_post(reasoning: str, content: str):
    """Return a stand-in for httpx.post yielding one OpenAI-compatible message."""
    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        msg = {"role": "assistant", "content": content}
        if reasoning is not None:
            msg["reasoning_content"] = reasoning
        return types.SimpleNamespace(
            status_code=200, raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": msg}]})
    return _post


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tmp_path))
    return tmp_path


def test_reasoning_candidate_captured_with_cot(ws, monkeypatch):
    monkeypatch.setattr(gl.httpx, "post", _fake_post(
        reasoning="I must add 2 and 3, so I call add(a=2, b=3).",
        content='[{"name": "add", "arguments": {"a": 2, "b": 3}}]'))

    raw, hard = gl._process_candidate(_Cfg(), "glm5.2", _REC, _GT, "simple",
                                      "BFCL_v4_simple_python.json", ws)

    recs = list(tc.read_records(ws))
    assert len(recs) == 1, "each labeled candidate must be wired into trace_capture"
    r = recs[0]
    assert r.cot.strip(), "reasoning-emitting vendor must NOT have its CoT silently dropped"
    assert "add 2 and 3" in r.cot
    assert r.role == "executor" and r.model == "fake-reasoner-4b"
    # gate_verdict is the deterministic ast_checker verdict, mirrored from the hard-gold label
    assert r.gate_verdict in ("PASS", "FAIL")
    assert r.gate_verdict == ("PASS" if hard["objective_verdict"] == "pass" else "FAIL")
    assert r.extra["label_authority"] == "bfcl_ast_checker"
    assert raw["reasoning_chars"] > 0
    # this correct call should PASS and therefore appear in the judge-free distillation view
    if r.gate_verdict == "PASS":
        assert list(tc.distillation_records(ws))[0].cot == r.cot


def test_visible_pre_answer_cot_is_captured(ws, monkeypatch):
    # no separate reasoning field, but visible chain-of-thought before the JSON answer
    monkeypatch.setattr(gl.httpx, "post", _fake_post(
        reasoning=None,
        content='Let me think: the user wants a sum. I will call add.\n'
                '[{"name": "add", "arguments": {"a": 2, "b": 3}}]'))

    gl._process_candidate(_Cfg(), "openrouter", _REC, _GT, "simple",
                          "BFCL_v4_simple_python.json", ws)
    r = list(tc.read_records(ws))[0]
    assert "Let me think" in r.cot, "visible pre-answer CoT must be captured too"


def test_no_reasoning_candidate_still_captured(ws, monkeypatch):
    # a terse model that emits only the JSON: still captured (cot may be empty), never dropped
    monkeypatch.setattr(gl.httpx, "post", _fake_post(
        reasoning=None, content='[{"name": "add", "arguments": {"a": 2, "b": 3}}]'))

    raw, hard = gl._process_candidate(_Cfg(), "ollama", _REC, _GT, "simple",
                                      "BFCL_v4_simple_python.json", ws)
    recs = list(tc.read_records(ws))
    assert len(recs) == 1 and recs[0].gate_verdict in ("PASS", "FAIL")
    assert raw["reasoning_chars"] == 0  # honestly records absence, not a fake trace
