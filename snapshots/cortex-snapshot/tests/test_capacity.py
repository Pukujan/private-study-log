"""GAP-CORTEX-0021: the model-tier routing policy is loadable, recommends per-stage bands,
maps models to tiers, and flags overspend (the Fable-on-fetch guard)."""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from cortex_core.capacity import (  # noqa: E402
    capacity_violation,
    load_policy,
    model_tier,
    recommend,
    tier_rank,
)


def test_policy_loads_and_has_expected_shape():
    p = load_policy()
    assert p["tiers"][0] == "frontier" and p["tiers"][-1] == "micro"
    assert "research.fetch" in p["stages"] and "design" in p["stages"]


def test_tier_rank_orders_strongest_highest():
    assert tier_rank("frontier") > tier_rank("strong") > tier_rank("small") > tier_rank("micro")
    assert tier_rank("nonexistent") == 0


def test_recommend_returns_the_band():
    fetch = recommend("research.fetch")
    assert fetch["max"] == "small" and fetch["effort"] == "none"
    design = recommend("design")
    assert design["min"] == "frontier" and design["effort"] == "max"
    with pytest.raises(KeyError):
        recommend("no.such.stage")


def test_model_tier_maps_concrete_ids_longest_match_wins():
    assert model_tier("claude-fable-5") == "frontier"
    assert model_tier("qwen-4b") == "small"
    assert model_tier("glm-5.2") == "strong"
    assert model_tier("deepseek-v4-flash") == "mid"
    assert model_tier("some-unknown-model") is None


def test_capacity_violation_flags_overspend_only():
    # Fable on a fetch leg = overspend (frontier > small ceiling) -> the incident this guards.
    v = capacity_violation("research.fetch", "claude-fable-5")
    assert v is not None and "CAPACITY_VIOLATION" in v
    # A cheap model on a fetch leg = fine.
    assert capacity_violation("research.fetch", "qwen-4b") is None
    # A strong model at its own stage = fine.
    assert capacity_violation("review", "claude-opus") is None
    # Unknown model is not a violation (can't judge).
    assert capacity_violation("research.fetch", "mystery-model") is None
