"""Deferred external-integration seams (product consolidation, honest stubs).

The critic's list named LiteLLM (gateway/spend), Presidio (PII redaction), and OTel trace
integration. Per `docs/BUILD-PLAN.md` these are **Phase 6** (fleet/gateway/OTel plane) and
**Phase 8** (PII/guardrails, trigger-gated) — deliberately deferred behind cost/service/trigger
gates. This module gives each a single, discoverable seam so future wiring has one obvious home.

IMPORTANT — these seams **raise**, they do not silently no-op. A no-op `redact_pii` that returns
text unchanged would be *worse* than nothing: a caller could assume redaction happened. So every
unwired seam fails loudly with the phase it belongs to. `AVAILABLE` reports wiring status
honestly (all False today); check it before calling, or catch `IntegrationNotWired`.
"""

from __future__ import annotations

from contextlib import contextmanager

AVAILABLE = {
    "litellm_gateway": False,   # Phase 6 — per-key budgets, actual served-model + cost logging
    "presidio_pii": False,      # Phase 8 — PII detection/redaction at the boundary (trigger-gated)
    "otel_trace": False,        # Phase 6 — GenAI-semconv spans across client + server planes
}

_PHASE = {"litellm_gateway": "Phase 6 (gateway/budgets)",
          "presidio_pii": "Phase 8 (guardrails, trigger-gated)",
          "otel_trace": "Phase 6 (OTel plane)"}


class IntegrationNotWired(NotImplementedError):
    """Raised when a deferred integration seam is called before it is wired."""


def _unwired(key: str):
    raise IntegrationNotWired(
        f"{key} is a deferred integration seam ({_PHASE[key]}); not wired. "
        f"See docs/BUILD-PLAN.md. Check integrations.AVAILABLE['{key}'] before calling.")


def record_llm_call(*_args, **_kwargs):
    """LiteLLM gateway seam: record the ACTUAL served model + cost per call (Phase 6)."""
    _unwired("litellm_gateway")


def redact_pii(_text: str) -> str:
    """Presidio seam: detect + redact PII before content crosses a boundary (Phase 8).

    Raises rather than returning text unchanged — a silent passthrough would masquerade as
    redaction. Do not swap this for a no-op; wire Presidio or keep failing loud.
    """
    _unwired("presidio_pii")


@contextmanager
def trace_span(_name: str, **_attrs):
    """OTel trace seam: a GenAI-semconv span across the client + server planes (Phase 6)."""
    _unwired("otel_trace")
    yield  # unreachable; keeps the contextmanager shape for the future wiring
