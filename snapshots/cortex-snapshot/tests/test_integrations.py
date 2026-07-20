"""Frozen tests for the deferred integration seams (cortex_core/integrations.py).

The whole point: unwired seams must FAIL LOUD, never silently no-op (a passthrough PII
"redactor" is a security footgun). These tests pin that contract so a future edit can't quietly
turn a seam into a dangerous no-op.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import integrations as I  # noqa: E402


def test_all_seams_report_unavailable_today():
    assert I.AVAILABLE == {"litellm_gateway": False, "presidio_pii": False, "otel_trace": False}


def test_redact_pii_raises_not_silently_passes_text_through():
    with pytest.raises(I.IntegrationNotWired):
        I.redact_pii("email me at alice@example.com")


def test_record_llm_call_raises():
    with pytest.raises(I.IntegrationNotWired):
        I.record_llm_call(model="x", cost=0.01)


def test_trace_span_raises_on_use():
    with pytest.raises(I.IntegrationNotWired):
        with I.trace_span("op"):
            pass


def test_integration_error_is_a_notimplemented():
    assert issubclass(I.IntegrationNotWired, NotImplementedError)
