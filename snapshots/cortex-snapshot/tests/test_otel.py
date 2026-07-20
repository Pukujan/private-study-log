"""GAP I6 -- OpenTelemetry export for the cost/latency/token plane.

Proves:
  * spans emit to an injected (stub) exporter carrying cost/latency/token + session/prompt attrs;
  * a stdout/console exporter path configures without a network collector;
  * absence of the otel SDK degrades to a NO-OP (no crash, handle still usable);
  * the disk metrics ledger (the A3 feed) is written independently of the SDK.
"""
from __future__ import annotations

import json

import pytest

from cortex_core import otel


@pytest.fixture(autouse=True)
def _reset():
    otel.reset_for_test()
    yield
    otel.reset_for_test()


def _memory_exporter():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    return InMemorySpanExporter()


@pytest.mark.skipif(not otel._HAVE_SDK, reason="otel SDK ([otel] extra) not installed")
def test_span_emits_cost_latency_tokens_and_correlation_ids():
    exp = _memory_exporter()
    assert otel.configure(exporter=exp, force=True) is True

    with otel.gen_ai_span("judge.call", session_id="sess-1", prompt_id="prm-1",
                          model="glm-4.6", track="build") as span:
        span.set_usage(input_tokens=1200, output_tokens=300, cost_usd=0.0042)
        span.add_tool_call()
        span.add_tool_call()

    spans = exp.get_finished_spans()
    assert len(spans) == 1
    a = dict(spans[0].attributes)
    # tokens
    assert a[otel.ATTR_INPUT_TOKENS] == 1200
    assert a[otel.ATTR_OUTPUT_TOKENS] == 300
    assert a[otel.ATTR_TOTAL_TOKENS] == 1500
    # cost
    assert a[otel.ATTR_COST_USD] == pytest.approx(0.0042)
    # latency (wall time stamped on close)
    assert a[otel.ATTR_LATENCY_MS] >= 0.0
    # tool-call count
    assert a[otel.ATTR_TOOL_CALLS] == 2
    # session/prompt correlation (the client<->server join key, PHASE-GATES 3.5)
    assert a[otel.ATTR_SESSION_ID] == "sess-1"
    assert a[otel.ATTR_PROMPT_ID] == "prm-1"
    assert a[otel.ATTR_MODEL] == "glm-4.6"
    assert a[otel.ATTR_TRACK] == "build"


@pytest.mark.skipif(not otel._HAVE_SDK, reason="otel SDK ([otel] extra) not installed")
def test_stdout_exporter_configures_without_network():
    # CORTEX_OTEL=stdout -> ConsoleSpanExporter (local, no collector, no network).
    assert otel.configure(force=True, env={"CORTEX_OTEL": "stdout"}) is True
    assert otel.otel_enabled({"CORTEX_OTEL": "stdout"}) is True
    # The wired exporter is a Console (stdout) exporter -- never an OTLP/network exporter
    # by default. This is the guarantee: no paid/hosted collector unless CORTEX_OTEL=otlp
    # AND an explicit local OTEL_EXPORTER_OTLP_ENDPOINT is set.
    exporter = otel._provider._active_span_processor._span_processors[0].span_exporter
    assert type(exporter).__name__ == "ConsoleSpanExporter"
    # And a span runs through it without error.
    with otel.gen_ai_span("x", session_id="s", env={"CORTEX_OTEL": "stdout"}) as span:
        span.set_usage(input_tokens=10, output_tokens=5, cost_usd=0.001)


def test_otlp_mode_requires_explicit_local_endpoint():
    # CORTEX_OTEL=otlp with NO endpoint -> refuse to guess a hosted default; stay silent (no export).
    assert otel.configure(force=True, env={"CORTEX_OTEL": "otlp"}) is False
    assert otel._tracer is None


def test_default_is_off_and_noop(monkeypatch):
    monkeypatch.delenv("CORTEX_OTEL", raising=False)
    monkeypatch.delenv("CORTEX_METRICS_LEDGER", raising=False)
    assert otel.otel_enabled() is False
    # A no-op span must still yield a working handle and never raise.
    with otel.gen_ai_span("noop", session_id="s", prompt_id="p") as span:
        span.set_usage(input_tokens=1, output_tokens=1, cost_usd=0.0)
        span.add_tool_call()
    # nothing configured -> no tracer
    assert otel._tracer is None


def test_sdk_absent_degrades_to_noop(monkeypatch):
    # Simulate the [otel] extra not being installed.
    monkeypatch.setattr(otel, "_HAVE_SDK", False)
    otel.reset_for_test()
    assert otel.configure(force=True, env={"CORTEX_OTEL": "stdout"}) is False
    assert otel.otel_enabled({"CORTEX_OTEL": "stdout"}) is False
    # Still yields a usable handle -- callers never branch on availability.
    with otel.gen_ai_span("x", session_id="s") as span:
        span.set_usage(input_tokens=99, output_tokens=1, cost_usd=0.5)


def test_metrics_ledger_written_independently_of_sdk(tmp_path, monkeypatch):
    # The disk A3 feed works even with the SDK "absent".
    monkeypatch.setattr(otel, "_HAVE_SDK", False)
    otel.reset_for_test()
    ledger = tmp_path / "results.jsonl"
    env = {"CORTEX_METRICS_LEDGER": str(ledger)}
    with otel.gen_ai_span("trial", session_id="sess-9", prompt_id="prm-9",
                          model="qwen3-4b", env=env) as span:
        span.set_usage(input_tokens=500, output_tokens=120, cost_usd=0.0009)
        span.add_tool_call(3)

    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["session_id"] == "sess-9"
    assert r["prompt_id"] == "prm-9"
    assert r["input_tokens"] == 500
    assert r["output_tokens"] == 120
    assert r["total_tokens"] == 620
    assert r["cost_usd"] == pytest.approx(0.0009)
    assert r["tool_calls"] == 3
    assert r["model"] == "qwen3-4b"
    assert r["wall_ms"] >= 0.0


def test_judge_call_site_emits_tokens_to_ledger(tmp_path):
    # The wiring is LIVE, not phantomic: a real llm_judge call (mocked transport) writes a
    # metrics row carrying the model's reported token usage + the claim-id correlation key.
    from cortex_core import judge as J
    from cortex_core.evaluator import AtomicClaim

    class _Resp:
        headers = {"content-type": "application/json"}
        def raise_for_status(self):  # noqa: D401
            return None
        def json(self):
            return {
                "choices": [{"message": {"content":
                    '{"verdict": "SUPPORTED", "confidence": 0.9, "reasoning": "ok", "gaps": []}'}}],
                "usage": {"prompt_tokens": 812, "completion_tokens": 144},
            }

    def _post(url, headers=None, json=None):
        return _Resp()

    ledger = tmp_path / "results.jsonl"
    claim = AtomicClaim(claim_id="claim-42", task_type="bugfix", description="Fix parser crash")
    J.llm_judge(
        claim, [], tier="glm5.2",
        env={"GLM_API_URL": "http://localhost/v1", "GLM_API_KEY": "k", "GLM_MODEL": "glm-x",
             "CORTEX_METRICS_LEDGER": str(ledger)},
        http_post=_post, session_id="sess-live",
    )
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["input_tokens"] == 812
    assert r["output_tokens"] == 144
    assert r["total_tokens"] == 956
    assert r["session_id"] == "sess-live"
    assert r["prompt_id"] == "claim-42"   # defaults to claim id for client-plane join
    assert r["model"] == "glm-x"
    assert r["name"] == "judge.llm_judge"


@pytest.mark.skipif(not otel._HAVE_SDK, reason="otel SDK ([otel] extra) not installed")
def test_ledger_and_span_together(tmp_path):
    exp = _memory_exporter()
    ledger = tmp_path / "m.jsonl"
    otel.configure(exporter=exp, force=True)
    with otel.gen_ai_span("both", session_id="s", prompt_id="p",
                          env={"CORTEX_METRICS_LEDGER": str(ledger)}) as span:
        span.set_usage(input_tokens=7, output_tokens=3, cost_usd=0.01)
    assert len(exp.get_finished_spans()) == 1
    assert ledger.exists() and ledger.read_text(encoding="utf-8").strip()
