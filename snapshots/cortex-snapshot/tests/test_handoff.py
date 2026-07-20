from __future__ import annotations

import pytest

from cortex_core.handoff import build_handoff, validate_handoff


def test_build_handoff_contains_required_fields() -> None:
    payload = build_handoff(task="stabilize cortex", phase="phase-1", owner="builder")
    validate_handoff(payload)
    assert payload["task"] == "stabilize cortex"
    assert payload["phase"] == "phase-1"
    assert payload["owner"] == "builder"


def test_validate_handoff_rejects_missing_fields() -> None:
    with pytest.raises(ValueError):
        validate_handoff({"task": "x"})
