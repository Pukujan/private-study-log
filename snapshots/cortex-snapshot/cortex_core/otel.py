"""Optional OpenTelemetry export for the cost/latency/token plane (GAP I6).

WHY: the ship rule's cost clause (docs/GAP-CLOSURE-PLAN.md A3) is unevaluable while the
cost/latency/token plane is null -- there is no per-trial record of tokens, wall time, or cost
to gate on. This module is the emitter for that plane. It produces one span per unit of
model work carrying `gen_ai.usage.*` (tokens, cost) + a `session.id`/`prompt.id` join key -- the
exact correlation Claude Code's own native client-side OTel export uses (`code.claude.com/docs/
en/monitoring-usage.md`, verified 2026-07-05; recorded in docs/PHASE-GATES.md 3.5). Cortex's
server-plane spans join against that client plane by the same ids.

OPTIONAL dependency, mirroring the vector/browser pattern in pyproject.toml. Install `.[otel]`
to pull in opentelemetry-sdk + the OTLP-HTTP exporter. WITHOUT the extra, every function here
degrades to a TRUE no-op: zero cost, no network, no crash, core unaffected (same contract as
the `[vector]` leg degrading to BM25).

Export is OFF by default and, when on, ONLY ever targets a LOCAL, operator-configured OTLP
endpoint (or stdout, for tests). It NEVER points at a paid/hosted collector by default -- the
owner controls whether any hosted collector is ever enabled.

Env contract (values live in the shell / gitignored .env, never tracked):
  CORTEX_OTEL
      unset / "0" / "off"            -> no-op (DEFAULT). Nothing is configured, nothing exported.
      "1" / "stdout" / "console"     -> ConsoleSpanExporter: spans printed to stdout (local, for
                                        tests / eyeballing). No network.
      "otlp"                         -> OTLPSpanExporter (HTTP/protobuf) to OTEL_EXPORTER_OTLP_ENDPOINT.
                                        The endpoint MUST be set and is expected to be LOCAL
                                        (e.g. a self-hosted Phoenix/Langfuse at http://localhost:4318).
  OTEL_EXPORTER_OTLP_ENDPOINT         the local collector base URL (only read when CORTEX_OTEL=otlp).
  CORTEX_METRICS_LEDGER               optional path. When set, each finished span ALSO appends one
                                        JSONL row (session/prompt/model/tokens/cost/wall_ms/tool_calls)
                                        -- the disk-recountable A3 feed. This path is otel-INDEPENDENT:
                                        it works even when the SDK isn't installed, so the cost/latency
                                        plane is populated on disk regardless of collector state.

Design rules (shared with cortex_core/telemetry.py):
  * OPT-IN + no-op default -- absence of the extra AND absence of CORTEX_OTEL both mean silence.
  * FAIL-OPEN -- emitting telemetry must NEVER break the work it measures; every path swallows.
  * No hosted collector by default -- stdout or a local OTLP endpoint only.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

# ---- optional SDK detection (the `[otel]` extra) -------------------------------------------------
try:  # opentelemetry-sdk ships the TracerProvider + exporters; api alone cannot export.
    from opentelemetry.sdk.resources import Resource as _Resource
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor as _BatchSpanProcessor,
        ConsoleSpanExporter as _ConsoleSpanExporter,
        SimpleSpanProcessor as _SimpleSpanProcessor,
    )
    _HAVE_SDK = True
except Exception:  # noqa: BLE001 -- extra not installed -> pure no-op
    _HAVE_SDK = False

# GenAI semantic-convention attribute keys (opentelemetry-semantic-conventions gen_ai.*), pinned
# as literals so the module carries no import dependency on the (still-beta) conventions package.
ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
ATTR_COST_USD = "gen_ai.usage.cost_usd"        # no stdized cost key yet; Cortex-local, documented
ATTR_MODEL = "gen_ai.request.model"
ATTR_LATENCY_MS = "gen_ai.latency_ms"          # duration is also the span's own wall time
ATTR_TOOL_CALLS = "gen_ai.tool_calls"
ATTR_SESSION_ID = "session.id"                 # the client<->server join key (Claude Code native)
ATTR_PROMPT_ID = "prompt.id"                   # one prompt's full API+tool-call chain
ATTR_TRACK = "cortex.track"
ATTR_RUN_ID = "cortex.run_id"
ATTR_TASK_ID = "cortex.task_id"
ATTR_ROUTE_ID = "cortex.route_id"

_INSTRUMENTATION = "cortex.otel"
_provider: Any = None          # cached TracerProvider once configured
_tracer: Any = None
_configured = False


def _mode(env: Mapping[str, str] | None = None) -> str:
    """Resolve the export mode from CORTEX_OTEL. Returns 'off' | 'stdout' | 'otlp'."""
    e = os.environ if env is None else env
    v = (e.get("CORTEX_OTEL") or "").strip().lower()
    if v in ("", "0", "off", "false", "no"):
        return "off"
    if v in ("1", "stdout", "console"):
        return "stdout"
    if v == "otlp":
        return "otlp"
    return "off"  # unknown value -> safe default


def otel_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when the SDK is installed AND CORTEX_OTEL selects a live exporter."""
    return _HAVE_SDK and _mode(env) != "off"


def _build_exporter(mode: str, env: Mapping[str, str]) -> Any:
    if mode == "stdout":
        return _ConsoleSpanExporter()
    if mode == "otlp":
        endpoint = (env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
        if not endpoint:
            # otlp requested but no local endpoint -> refuse to guess a hosted default; stay silent.
            return None
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")
    return None


def configure(*, exporter: Any = None, force: bool = False,
              env: Mapping[str, str] | None = None) -> bool:
    """Idempotently stand up a TracerProvider + span processor. Returns True if a live tracer is
    active after the call. No-op (returns False) when the SDK is absent or CORTEX_OTEL is off,
    unless an `exporter` is injected (the test seam -- e.g. InMemorySpanExporter)."""
    global _provider, _tracer, _configured
    if not _HAVE_SDK:
        return False
    if _configured and not force:
        return _tracer is not None
    e = dict(os.environ if env is None else env)
    mode = _mode(e)
    exp = exporter if exporter is not None else _build_exporter(mode, e)
    if exp is None:
        _configured = True
        _tracer = None
        return False
    resource = _Resource.create({"service.name": "cortex"})
    provider = _TracerProvider(resource=resource)
    # Injected/console exporters flush synchronously (deterministic for tests); OTLP batches.
    proc = (_SimpleSpanProcessor(exp) if (exporter is not None or mode == "stdout")
            else _BatchSpanProcessor(exp))
    provider.add_span_processor(proc)
    _provider = provider
    _tracer = provider.get_tracer(_INSTRUMENTATION)
    _configured = True
    return True


def reset_for_test() -> None:
    """Tear down the cached provider so a test can reconfigure with a fresh exporter."""
    global _provider, _tracer, _configured
    try:
        if _provider is not None:
            _provider.shutdown()
    except Exception:  # noqa: BLE001
        pass
    _provider = None
    _tracer = None
    _configured = False


class TrialSpan:
    """Handle yielded by `gen_ai_span`. Accepts usage/attributes whether or not a real OTel span
    exists behind it (no-op safe). On close it stamps the wall-clock latency, mirrors everything
    onto the real span (if any), and appends the disk ledger row (if CORTEX_METRICS_LEDGER is set)."""

    __slots__ = ("_span", "attrs", "_t0", "_tool_calls", "name", "session_id", "prompt_id")

    def __init__(self, span: Any, name: str, session_id: str | None, prompt_id: str | None):
        self._span = span
        self.name = name
        self.session_id = session_id
        self.prompt_id = prompt_id
        self.attrs: dict[str, Any] = {}
        self._tool_calls = 0
        self._t0 = time.perf_counter()

    def set_usage(self, input_tokens: int | None = None, output_tokens: int | None = None,
                  cost_usd: float | None = None) -> "TrialSpan":
        if input_tokens is not None:
            self.set_attribute(ATTR_INPUT_TOKENS, int(input_tokens))
        if output_tokens is not None:
            self.set_attribute(ATTR_OUTPUT_TOKENS, int(output_tokens))
        if input_tokens is not None and output_tokens is not None:
            self.set_attribute(ATTR_TOTAL_TOKENS, int(input_tokens) + int(output_tokens))
        if cost_usd is not None:
            self.set_attribute(ATTR_COST_USD, float(cost_usd))
        return self

    def add_tool_call(self, n: int = 1) -> "TrialSpan":
        self._tool_calls += int(n)
        return self

    def set_attribute(self, key: str, value: Any) -> "TrialSpan":
        self.attrs[key] = value
        if self._span is not None:
            try:
                self._span.set_attribute(key, value)
            except Exception:  # noqa: BLE001 -- telemetry must not raise
                pass
        return self

    def _finalize(self) -> dict[str, Any]:
        wall_ms = round((time.perf_counter() - self._t0) * 1000.0, 3)
        self.set_attribute(ATTR_LATENCY_MS, wall_ms)
        if self._tool_calls:
            self.set_attribute(ATTR_TOOL_CALLS, self._tool_calls)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "name": self.name,
            "session_id": self.session_id,
            "prompt_id": self.prompt_id,
            "run_id": self.attrs.get(ATTR_RUN_ID),
            "task_id": self.attrs.get(ATTR_TASK_ID),
            "route_id": self.attrs.get(ATTR_ROUTE_ID),
            "wall_ms": wall_ms,
            "tool_calls": self._tool_calls,
            "input_tokens": self.attrs.get(ATTR_INPUT_TOKENS),
            "output_tokens": self.attrs.get(ATTR_OUTPUT_TOKENS),
            "total_tokens": self.attrs.get(ATTR_TOTAL_TOKENS),
            "cost_usd": self.attrs.get(ATTR_COST_USD),
            "model": self.attrs.get(ATTR_MODEL),
        }
        return row


def _append_ledger(row: Mapping[str, Any], env: Mapping[str, str] | None = None) -> bool:
    """Append one metrics row to CORTEX_METRICS_LEDGER (the A3 feed). Fail-open. Otel-independent."""
    e = os.environ if env is None else env
    path = (e.get("CORTEX_METRICS_LEDGER") or "").strip()
    if not path:
        return False
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except Exception:  # noqa: BLE001 -- fail-open
        return False


@contextmanager
def gen_ai_span(name: str, *, session_id: str | None = None, prompt_id: str | None = None,
                model: str | None = None, track: str | None = None,
                run_id: str | None = None, task_id: str | None = None,
                route_id: str | None = None,
                env: Mapping[str, str] | None = None) -> Iterator[TrialSpan]:
    """Measure one unit of model work: cost/latency/tokens with a session/prompt join key.

    Yields a `TrialSpan`; call `.set_usage(input_tokens=..., output_tokens=..., cost_usd=...)`
    and `.add_tool_call()` inside the block. On exit the wall-clock latency is stamped, a real
    OTel span is emitted IFF a tracer is configured (`.[otel]` + CORTEX_OTEL), and a JSONL row is
    appended to CORTEX_METRICS_LEDGER if set. When neither is configured this is a pure no-op
    context manager that still yields a working handle (so callers never branch on availability).
    """
    if not _configured:
        configure(env=env)
    real = None
    if _tracer is not None:
        try:
            real = _tracer.start_span(name)
        except Exception:  # noqa: BLE001
            real = None
    handle = TrialSpan(real, name, session_id, prompt_id)
    if session_id is not None:
        handle.set_attribute(ATTR_SESSION_ID, session_id)
    if prompt_id is not None:
        handle.set_attribute(ATTR_PROMPT_ID, prompt_id)
    if model is not None:
        handle.set_attribute(ATTR_MODEL, model)
    if track is not None:
        handle.set_attribute(ATTR_TRACK, track)
    if run_id is not None:
        handle.set_attribute(ATTR_RUN_ID, run_id)
    if task_id is not None:
        handle.set_attribute(ATTR_TASK_ID, task_id)
    if route_id is not None:
        handle.set_attribute(ATTR_ROUTE_ID, route_id)
    try:
        yield handle
    finally:
        row = handle._finalize()
        _append_ledger(row, env=env)
        if real is not None:
            try:
                real.end()
            except Exception:  # noqa: BLE001
                pass
