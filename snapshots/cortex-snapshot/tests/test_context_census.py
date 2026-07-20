"""GAP A4 -- frozen tests for the provider-specific context census."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MOD = HERE.parent / "evals" / "context_census"
sys.path.insert(0, str(MOD))

import context_census as cc  # noqa: E402


def test_percentiles_nearest_rank():
    p = cc.percentiles(list(range(1, 101)))  # 1..100
    assert p.p50 == 50 and p.p95 == 95 and p.p99 == 99 and p.maximum == 100
    assert p.n == 100


def test_percentiles_empty():
    p = cc.percentiles([])
    assert p.n == 0 and p.p95 == 0


def test_char_over_4_heuristic():
    assert cc.count_tokens("a" * 40, "char_over_4") == 10


def test_provider_counts_differ_or_present():
    txt = '{"name":"x","input_schema":{"type":"object"}}'
    a = cc.count_tokens(txt, "cl100k_base")
    b = cc.count_tokens(txt, "o200k_base")
    assert a > 0 and b > 0


def test_tool_surface_is_measured_not_50k():
    """The core A4 correction: the real surface is ~12k, an order below the
    fabled 50k. Assert the measured total sits in a sane band under 30k."""
    s = cc.measure_tool_surface(providers=("cl100k_base",))
    pp = s["per_provider"]["cl100k_base"]
    assert pp["n_tools"] >= 30
    assert 5000 < pp["surface_total_tokens"] < 30000, pp
    assert 0 < pp["core_tier_tokens"] < pp["surface_total_tokens"]


def test_guardrail_relative_to_A():
    s = cc.measure_tool_surface(providers=("cl100k_base",))
    g = cc.guardrail_relative_to_A(s)
    assert g["arm_A_resting_tokens"] == 0
    assert g["arm_B_core_tier_resting_tokens"] > 0
    # core tier must leave headroom in an 8k window (else the design is DOA).
    assert g["windows"]["8000"]["headroom_after_core_tokens"] > 0


def test_census_corpus_smoke(tmp_path):
    (tmp_path / "a.md").write_text("hello world " * 100, encoding="utf-8")
    (tmp_path / "b.md").write_text("x", encoding="utf-8")
    r = cc.census_corpus([tmp_path / "a.md", tmp_path / "b.md"])
    assert r["n_docs"] == 2 and r["per_doc_tokens"]["max"] > r["per_doc_tokens"]["p50"] - 1
