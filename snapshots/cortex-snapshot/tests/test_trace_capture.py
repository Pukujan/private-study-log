"""Tests for frontier-trace capture: durable local record + the judge-free distillation view."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import trace_capture as tc  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tmp_path))
    return tmp_path


def test_capture_writes_durable_local_record(ws):
    ok = tc.capture(tc.TraceRecord(task="track clients", model="big-pickle",
                                   cot="pick entity=client", output='{"entity":"client"}',
                                   gate_verdict="PASS"), workspace=ws)
    assert ok is True
    f = ws / "ops-local" / "trace-capture.jsonl"
    assert f.is_file() and "big-pickle" in f.read_text(encoding="utf-8")
    recs = list(tc.read_records(ws))
    assert len(recs) == 1 and recs[0].model == "big-pickle" and recs[0].ts > 0


def test_capture_build_from_verdict(ws):
    class V:
        passed = False
        failure_class = "RELATION_FAIL"
    tc.capture_build("track orders", "laguna-m.1", '{"entity":"order"}', V(),
                     cot="reasoned", latency_s=1.2, workspace=ws)
    r = list(tc.read_records(ws))[0]
    assert r.gate_verdict == "FAIL" and r.failure_class == "RELATION_FAIL" and r.latency_s == 1.2


def test_distillation_view_keeps_only_verified(ws):
    tc.capture(tc.TraceRecord(task="a", model="m1", gate_verdict="PASS"), workspace=ws)
    tc.capture(tc.TraceRecord(task="b", model="m2", gate_verdict="FAIL"), workspace=ws)
    tc.capture(tc.TraceRecord(task="c", model="m3", gate_verdict="PASS"), workspace=ws)
    distilled = list(tc.distillation_records(ws))
    assert {r.task for r in distilled} == {"a", "c"}          # only the gate-verified ones
    assert all(r.gate_verdict == "PASS" for r in distilled)


def test_capture_is_fail_open_on_bad_workspace(ws, monkeypatch):
    # a broken sink returns False, never raises -- capture must never break real work
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(tc, "_capture_path", boom)
    assert tc.capture(tc.TraceRecord(task="x", model="m"), workspace=ws) is False


def test_reader_skips_corrupt_lines(ws):
    tc.capture(tc.TraceRecord(task="good", model="m", gate_verdict="PASS"), workspace=ws)
    f = ws / "ops-local" / "trace-capture.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    recs = list(tc.read_records(ws))
    assert len(recs) == 1 and recs[0].task == "good"      # corrupt line skipped, not crashed
