"""RED tests (GLM-5.2, panel 2026-07-09) for P1 vendor-agnostic output normalization.

Guards that a model's raw output (filler, CoT, code fences) is normalized identically to a
clean output BEFORE any deterministic checker sees it — otherwise the harness measures
format compliance, not capability, and non-Claude models fail at parsing before the checker
even runs. See docs/EVAL-FLYWHEEL-PLAN.md P1.
"""
from cortex_core.extract import normalize_output


def test_code_only_contract_strips_filler_and_prose():
    """Guards against filler text leaking into code lane (inflates parse_failure)."""
    raw = "Sure! Here is the code:\n```python\nprint(1)\n```\nHope this helps!"
    out = normalize_output(raw, "code_only")
    assert out.code.strip() == "print(1)"
    assert out.filler_stripped is True
    assert out.json is None and out.answer is None


def test_json_only_contract_extracts_first_json_object():
    """Guards against JSON lane picking up prose or nested arrays."""
    raw = 'Reasoning: ...\n```json\n{"answer": 42}\n```\nDone.'
    out = normalize_output(raw, "json_only")
    assert out.json == {"answer": 42}


def test_raw_contract_preserves_everything():
    """Guards against raw lane silently mutating model output."""
    raw = "Anything goes\n```python\nx=1\n```"
    out = normalize_output(raw, "raw")
    assert out.code == raw and out.filler_stripped is False


def test_tool_calls_contract_extracts_calls_not_prose():
    """Guards against prose being parsed as a tool call."""
    raw = 'I will call search.\n[{"name":"search","args":{"q":"x"}}]'
    out = normalize_output(raw, "tool_calls")
    assert out.tool_calls == [{"name": "search", "args": {"q": "x"}}]
    assert out.answer is None
