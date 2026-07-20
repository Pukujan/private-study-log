"""Tests for the fail-open Langfuse Tier-3 sink. No network: verifies config gating, payload
shape, and the fail-open contract (unconfigured / bad host never raises, never breaks capture)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cortex_core import langfuse_sink as LS  # noqa: E402
from cortex_core.trace_capture import TraceRecord, capture  # noqa: E402

_KEYS = {"LANGFUSE_HOST": "http://example.invalid:3000",
         "LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"}


def test_disabled_when_unconfigured():
    assert LS.enabled(env={}) is False
    assert LS.enabled(env={"LANGFUSE_HOST": "x"}) is False  # partial config -> disabled


def test_enabled_with_full_config():
    assert LS.enabled(env=_KEYS) is True


def test_push_noop_when_unconfigured():
    # fail-open: no keys -> returns False, never raises
    assert LS.push_trace(TraceRecord(task="t", model="m"), env={}) is False


def test_build_batch_shape():
    rec = TraceRecord(task="build a tracker", model="big-pickle", role="builder",
                      cot="reasoning...", output='{"tool":"x"}', gate_verdict="PASS",
                      cost=0.0, latency_s=1.2, ts=1.0)
    batch = LS.build_batch(rec)["batch"]
    types = [e["type"] for e in batch]
    assert types[0] == "trace-create"
    assert "score-create" in types  # gate_verdict present -> a score is emitted
    trace = batch[0]["body"]
    assert trace["input"] == "build a tracker"
    assert trace["metadata"]["model"] == "big-pickle"
    assert trace["metadata"]["gate_verdict"] == "PASS"
    score = next(e for e in batch if e["type"] == "score-create")["body"]
    assert score["name"] == "gate_verdict" and score["value"] == "PASS"
    assert score["traceId"] == trace["id"]  # score links to the trace


def test_build_batch_no_score_without_verdict():
    rec = TraceRecord(task="t", model="m", gate_verdict="", ts=1.0)
    types = [e["type"] for e in LS.build_batch(rec)["batch"]]
    assert types == ["trace-create"]  # no verdict -> no score event


def test_push_bad_host_fails_open(tmp_path):
    # a configured-but-unreachable host must NOT raise
    assert LS.push_trace(TraceRecord(task="t", model="m", gate_verdict="PASS"), env=_KEYS) is False


def test_capture_still_works_when_langfuse_unconfigured():
    # capture() must succeed (local JSONL floor) and NOT raise regardless of Langfuse config.
    # My Tier-3 addition is try/except-wrapped and only runs when enabled() (no keys here -> no-op),
    # so it provably cannot break capture(); this exercises the real path end to end.
    ok = capture(TraceRecord(task="langfuse-sink-test", model="m", gate_verdict="PASS"))
    assert ok is True
