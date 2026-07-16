"""Model-tier + reasoning-effort routing (GAP-CORTEX-0021).

The keyless-server rule: the server STORES the policy (`config/capacity_policy.yaml`) and
RECOMMENDS a tier per work-stage; the host resolves that against its own models and RUNS it.
This module is the read/reason side: load the policy, recommend a stage's band, map a concrete
model id to its tier, and flag overspend (a model above a stage's ceiling -- the never-again
guard for the Fable-on-fetch incident).

Nothing here runs a model or holds a key -- it is pure policy lookup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "capacity_policy.yaml"
_cache: dict[str, Any] | None = None


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache the routing policy. `path=None` uses config/capacity_policy.yaml."""
    global _cache
    if path is None and _cache is not None:
        return _cache
    p = Path(path) if path is not None else _DEFAULT_POLICY
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "tiers" not in data or "stages" not in data:
        raise ValueError(f"malformed capacity policy at {p}")
    if path is None:
        _cache = data
    return data


def tier_rank(tier: str, policy: dict[str, Any] | None = None) -> int:
    """Rank of a tier, strongest highest (frontier=len..micro=1). 0 if unknown."""
    tiers = (policy or load_policy())["tiers"]
    return (len(tiers) - tiers.index(tier)) if tier in tiers else 0


def recommend(stage: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """The recommended band for a stage: {min, max, effort, why}. Raises on unknown stage
    (a caller asking to route an unmodeled stage is a bug, not a silent default)."""
    stages = (policy or load_policy())["stages"]
    if stage not in stages:
        raise KeyError(f"unknown stage {stage!r}; known: {sorted(stages)}")
    return dict(stages[stage])


def model_tier(model_id: str, policy: dict[str, Any] | None = None) -> str | None:
    """Map a concrete model id to its tier via `tier_examples` (case-insensitive substring
    match, longest-example-first so 'opus-high' beats 'opus'). None if unmatched."""
    pol = policy or load_policy()
    mid = (model_id or "").lower()
    best: tuple[int, str] | None = None
    for tier, examples in (pol.get("tier_examples") or {}).items():
        for ex in examples:
            e = str(ex).lower()
            if e in mid and (best is None or len(e) > best[0]):
                best = (len(e), tier)
    return best[1] if best else None


def capacity_violation(stage: str, model_id: str,
                       policy: dict[str, Any] | None = None) -> str | None:
    """A warning string if `model_id`'s tier exceeds the stage's MAX class (overspend), else
    None. Warn-not-block: availability fallback upward must stay possible, but never silent.
    Returns None when the model can't be mapped (unknown model != a violation)."""
    pol = policy or load_policy()
    tier = model_tier(model_id, pol)
    if tier is None:
        return None
    band = recommend(stage, pol)
    if tier_rank(tier, pol) > tier_rank(band["max"], pol):
        return (f"CAPACITY_VIOLATION: {model_id} ({tier}) exceeds the {band['max']} ceiling for "
                f"stage {stage!r} -- overspend ({band['why']})")
    return None
