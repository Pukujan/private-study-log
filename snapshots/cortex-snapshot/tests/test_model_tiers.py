"""Tests for the benchmark/measured-backed dispatch-tier classifier
(cortex_core/model_tiers.py).

This replaces the wrapper's keyword-substring `classify()` (the old
`ninerouter_tiers.py` `_RULES`), whose two headline bugs these tests pin as
RED-first regressions:

  1. Any model whose name contains "flash" was auto-classified **weak** — but
     our own BFCL tool-calling probe measured gemini-3.5-flash at **1.000** and
     gemini-3-flash at **1.000** (evals/reports/tier-probe-objective-lanes-2026-07-14.md).
     A "flash" model must NOT be auto-weak.
  2. An unrecognized model silently defaulted to **medium** — a guess. The
     anti-guessing rule (CLAUDE.md research-first) says an un-probed model is
     **UNKNOWN** (probe-first), never a name-guessed tier.

Plus the measured placements: big-pickle lands **upper-mid** (0.964 measured on
our BFCL lane, promoted from owner-usage-assessed), and separator-insensitive
version matching (served ids use "4-6", the table uses "4.6").

Sources cited inline: docs/research/model-tier-list-benchmarked-2026-07-14.md
(public benchmarks + our κ) and evals/reports/tier-probe-objective-lanes-2026-07-14.md
(the MEASURED tool-calling pass-rates).
"""

from __future__ import annotations

import pytest

from cortex_core import model_tiers as MT


# --- Headline bug #1: a "flash" model is NOT auto-weak ---

@pytest.mark.parametrize("served_id", [
    "gemini-3.5-flash",
    "ag/gemini-3.5-flash-low",   # the real 9router served id (test_9router_tiers.py)
    "gemini-3-flash-agent",
    "deepseek-v4-flash",         # also carries "flash"; measured 1.000 executor
])
def test_flash_models_are_not_auto_weak(served_id: str):
    """RED against the old `_RULES` rule ("flash" -> weak). Measured >=0.96 each."""
    assert MT.classify(served_id) != "weak"


def test_gemini_35_flash_is_strong():
    """gemini-3.5-flash measured 1.000 on our BFCL lane; doc tiers it strong."""
    assert MT.classify("ag/gemini-3.5-flash-low") == "strong"


# --- Headline bug #2: an unknown model -> UNKNOWN, never a guessed tier ---

def test_default_tier_is_unknown_not_medium():
    assert MT.DEFAULT_TIER == "UNKNOWN"


@pytest.mark.parametrize("unknown_id", [
    "totally-made-up-model-x9",
    "some-vendor/mystery-7t-turbo",
    "north-mini-code-free",       # stealth; old rules guessed "medium"
])
def test_unknown_model_returns_unknown(unknown_id: str):
    assert MT.classify(unknown_id) == "UNKNOWN"


# --- big-pickle lands upper-mid (measured 0.964, promoted from owner-assessed) ---

def test_big_pickle_is_upper_mid():
    assert MT.classify("big-pickle") == "upper-mid"


def test_big_pickle_aux_alias_is_upper_mid():
    """`aux` resolves server-side to big-pickle (judge.py, verified live 2026-07-08)."""
    assert MT.classify("aux") == "upper-mid"


def test_big_pickle_provenance_is_measured():
    tier, prov = MT.tier_and_provenance("big-pickle")
    assert tier == "upper-mid"
    assert prov == "measured"


# --- separator-insensitive version matching (served "4-6" vs table "4.6") ---

def test_sonnet_46_gateway_id_matches_strong():
    assert MT.classify("ag/claude-sonnet-4-6") == "strong"


def test_sonnet_45_is_upper_mid_not_conflated_with_46():
    """Prior-gen Sonnet 4.5 must stay distinct from 4.6 despite sharing 'claude-sonnet'."""
    assert MT.classify("kr/claude-sonnet-4.5") == "upper-mid"


# --- longest-match wins: preview beats the gemini-3-flash substring ---

def test_gemini_preview_stays_unknown_not_flash_medium():
    """'gemini-3-flash-preview' contains 'gemini-3-flash' but is a rate-limited,
    unmeasured alias -> must resolve UNKNOWN (probe-first), not the flash tier."""
    assert MT.classify("gemini/gemini-3-flash-preview") == "UNKNOWN"


# --- measured executor placements ---

def test_qwen35b_served_model_is_medium_dispatch():
    """qwen35b tier serves qwen3.6-35b-a3b; measured 0.982 executor -> medium dispatch."""
    assert MT.classify("qwen3.6-35b-a3b") == "medium"


def test_ollama_qwen3_4b_is_weak():
    """The one real downward separation on the lane: 0.909."""
    assert MT.classify("qwen3:4b-16k") == "weak"


def test_deepseek_v4_flash_is_upper_mid_executor():
    assert MT.classify("deepseek-v4-flash") == "upper-mid"


# --- utility detection (non-chat helpers) ---

@pytest.mark.parametrize("util_id", [
    "text-embedding-3-large",
    "bge-reranker-v2",
    "whisper-large-v3",
    "prometheus-eval-7b",
])
def test_utility_models_classify_utility(util_id: str):
    assert MT.classify(util_id) == "utility"


# --- table hygiene: only known tier values, provenance present ---

def test_all_table_entries_use_known_tiers_and_have_provenance():
    known = set(MT.TIER_ORDER) | {"UNKNOWN"}
    for key, (tier, prov) in MT.MODEL_TIER_TABLE.items():
        assert tier in known, f"{key!r} -> unknown tier {tier!r}"
        assert prov, f"{key!r} missing provenance"


def test_classify_return_is_always_a_known_tier():
    for probe in ["big-pickle", "aux", "gemini-3.5-flash", "qwen3:4b-16k",
                  "text-embedding-3-large", "never-heard-of-it"]:
        assert MT.classify(probe) in (set(MT.TIER_ORDER) | {"UNKNOWN"})
