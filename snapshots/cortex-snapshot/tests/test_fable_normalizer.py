"""Tests for evals/fable_capture/normalize.py — schema unification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.fable_capture.normalize import normalize_record, NORMALIZED_REQUIRED_FIELDS

def _record(**kwargs):
    """Build a raw record with sensible defaults."""
    defaults = {
        "case_id": "c1",
        "category": "agentic_task",
        "prompt": "do the thing",
        "expected_behavior": "thing done",
        "expected_tool_call": {"tool": "foo"},
        "checker_assertions": ["a"],
        "failure_modes": ["b"],
        "difficulty": "medium",
    }
    defaults.update(kwargs)
    return defaults


def test_already_unified_record_passes_through():
    r = _record()
    out = normalize_record(r)
    for f in NORMALIZED_REQUIRED_FIELDS:
        assert f in out, f"missing required field {f}"
    assert out["case_id"] == "c1"
    assert out["prompt"] == "do the thing"
    assert out["expected_behavior"] == "thing done"
    assert out["expected_tool_call"] == {"tool": "foo"}


def test_task_mapped_to_prompt():
    r = _record(task="do the thing")
    del r["prompt"]
    out = normalize_record(r)
    assert out["prompt"] == "do the thing"


def test_correct_next_action_mapped_to_expected_behavior():
    r = _record(correct_next_action="thing done")
    del r["expected_behavior"]
    out = normalize_record(r)
    assert out["expected_behavior"] == "thing done"


def test_tool_call_expected_mapped():
    r = _record(tool_call_expected={"tool": "bar"})
    del r["expected_tool_call"]
    out = normalize_record(r)
    assert out["expected_tool_call"] == {"tool": "bar"}


def test_id_mapped_to_case_id():
    r = _record(id="x9")
    del r["case_id"]
    out = normalize_record(r)
    assert out["case_id"] == "x9"


def test_provenance_corrected():
    r = _record(
        author_model="fable",
        authority="ground_truth",
        promotion_tier="fable_ground_truth",
        ground_truth_for_now=True,
        human_reviewed=False,
    )
    out = normalize_record(r)
    assert out["authority"] == "weak_candidate_exemplar"
    assert out["promotion_tier"] == "weak_candidate_exemplar"
    assert out["ground_truth_for_now"] is False
    assert out["human_reviewed"] is False
    assert out["author_model"] == "fable"


def test_typo_fields_renamed():
    r = _record(why_inform="bad info", why_incurrent="also bad")
    out = normalize_record(r)
    assert "why_inform" not in out
    assert "why_incurrent" not in out
    assert out["why_incorrect"] == ["bad info", "also bad"]


def test_why_incorrect_preserved():
    r = _record(why_incorrect="original")
    out = normalize_record(r)
    assert out["why_incorrect"] == "original"


def test_optional_domain_fields_kept():
    r = _record(expected_state_change="x", must_include=["y"], fan_out_or_fan_in="fan_out")
    out = normalize_record(r)
    assert out["expected_state_change"] == "x"
    assert out["must_include"] == ["y"]
    assert out["fan_out_or_fan_in"] == "fan_out"


def test_missing_required_fields_filled_with_none():
    r = {"case_id": "c1", "category": "agentic"}
    out = normalize_record(r)
    assert out["prompt"] is None
    assert out["expected_behavior"] is None
    assert out["expected_tool_call"] is None
    assert out["checker_assertions"] is None
    assert out["failure_modes"] is None
    assert out["difficulty"] is None


def test_coding_record_normalized():
    """Objective checker domain: coding uses id + prompt, no expected_tool_call."""
    r = {
        "id": "coding_01",
        "prompt": "sort a list",
        "reference": "def sort(x): return sorted(x)",
        "buggy": [],
        "visible_tests": "assert sort([3,1]) == [1,3]",
        "hidden_tests": "assert sort([]) == []",
        "category": "coding",
        "author_model": "fable",
        "authority": "candidate",
        "promotion_tier": "edge_case_candidate",
        "ground_truth_for_now": False,
        "human_reviewed": False,
    }
    out = normalize_record(r)
    assert out["case_id"] == "coding_01"
    assert out["prompt"] == "sort a list"
    assert out["expected_behavior"] is None
    assert out["expected_tool_call"] is None
    assert out["authority"] == "weak_candidate_exemplar"
    assert out["promotion_tier"] == "weak_candidate_exemplar"


def test_ui_ux_record_normalized():
    r = {
        "case_id": "ui_01",
        "category": "ui_ux",
        "task": "build a form",
        "correct_next_action": "return html",
        "tool_call_expected": None,
        "checker_assertions": ["wcag"],
        "failure_modes": ["missing_label"],
        "difficulty": "hard",
        "visual_requirements": ["contrast"],
    }
    out = normalize_record(r)
    assert out["prompt"] == "build a form"
    assert out["expected_behavior"] == "return html"
    assert out["visual_requirements"] == ["contrast"]


def test_normalization_is_pure():
    r = _record()
    original_keys = set(r.keys())
    normalize_record(r)
    assert set(r.keys()) == original_keys


def test_all_raw_files_normalize():
    """Every record in every *_raw.jsonl file normalizes without error and contains
    all required fields."""
    here = Path.cwd() / "evals" / "fable_capture"
    files = sorted(here.glob("*_raw.jsonl"))
    total = 0
    for path in files:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for row in rows:
            out = normalize_record(row)
            for f in NORMALIZED_REQUIRED_FIELDS:
                assert f in out, f"{path.name}: missing {f}"
            assert out["authority"] == "weak_candidate_exemplar"
            assert out["promotion_tier"] == "weak_candidate_exemplar"
            assert out["ground_truth_for_now"] is False
            total += 1
    print(f"validated {total} rows across {len(files)} files")
    assert total >= 7000
