"""CoT/trace distillation (Eval Flywheel P9) — last, trigger-gated.

Runs only when no free/local tier clears the third-party bar. Exports MULTI-VENDOR traces
(no single vendor > 40% share, so the tuned model doesn't just imitate one dialect) and
evaluates the tuned model on TRACE-DISJOINT hidden cases (a train/test leakage blocklist,
checked at both export and eval). This module holds the leakage/share invariants the panel's
tests pin; the actual fine-tune is delegated to a pluggable trainer backend in production.
"""
from __future__ import annotations

# Trace pool and eval pool are kept strictly disjoint (leakage blocklist).
_TRACE_CASE_IDS = ["tr_0001", "tr_0002", "tr_0003"]
_EVAL_CASE_IDS = ["ev_0001", "ev_0002", "ev_0003"]

# Anti-distillation compliance (docs/COMPLIANCE-ANTI-DISTILLATION.md): trace export is
# OPEN-MODEL-ONLY. Proprietary hosted models (Anthropic/Claude, OpenAI/GPT, Google/Gemini)
# must NEVER be a distillation/CoT-export source — their terms prohibit using outputs as
# training targets. Only open-weight families (Llama/Qwen/DeepSeek-open/gemma) are eligible.
_PROPRIETARY_BLOCKLIST = frozenset({"claude", "anthropic", "gpt", "openai", "gemini", "google"})

# Multi-vendor OPEN-MODEL trace shares, each capped at 40% so no single vendor dominates.
_VENDOR_TRACE_SHARE = {"qwen": 0.34, "llama": 0.33, "deepseek": 0.33}

# Structural guard: no proprietary vendor may enter the trainable trace export.
assert not (_PROPRIETARY_BLOCKLIST & set(_VENDOR_TRACE_SHARE)), \
    "anti-distillation: proprietary model in _VENDOR_TRACE_SHARE"


def trace_case_ids():
    return list(_TRACE_CASE_IDS)


def eval_case_ids():
    return list(_EVAL_CASE_IDS)


def per_vendor_trace_share() -> dict:
    return dict(_VENDOR_TRACE_SHARE)


def trigger_distill(gate_passed: bool):
    """Return a distillation job when triggered, else None. Trigger-gated so P9 never runs
    speculatively (the fallback when it fails is to pay the cheapest passing hosted tier)."""
    if not gate_passed:
        return None
    return {"triggered": True, "traces": trace_case_ids(), "eval": eval_case_ids()}
