"""Content tests for the live coding lane (P2 production). Deterministic — no model, no
network: the mbpp reference solution must PASS its own checker, a non-answer must parse_fail.
"""
import json
import pathlib

from cortex_core.lanes import grade_coding_record, build_coding_prompt

_MBPP = "evals/hf_datasets/mbpp/hard_gold.jsonl"


def _first_record():
    with pathlib.Path(_MBPP).open(encoding="utf-8") as fh:
        return json.loads(fh.readline())


def test_reference_candidate_passes_its_own_checker():
    """Guards the adapter wiring: the dataset's reference solution must pass check_solution."""
    rec = _first_record()
    fenced = "```python\n" + rec["candidate_code"] + "\n```"
    verdict, detail = grade_coding_record(rec, fenced)
    assert verdict == "pass", (verdict, detail)


def test_non_code_output_is_parse_fail_not_fail():
    """Guards parse_fail != fail: a model that ignores the code contract is a parse failure."""
    rec = _first_record()
    verdict, _ = grade_coding_record(rec, "I cannot help with that.")
    assert verdict == "parse_fail"


def test_prompt_carries_task_and_signature_hint():
    """Guards that the model is told the task and one example test (the signature)."""
    rec = _first_record()
    p = build_coding_prompt(rec)
    assert rec["problem"]["prompt"][:20] in p and "python" in p.lower()
