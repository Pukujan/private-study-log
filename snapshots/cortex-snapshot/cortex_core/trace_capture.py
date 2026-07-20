"""Frontier-trace capture — the self-improving-harness's memory of HOW work was done.

Every meaningful model run (a builder filling a slot, a strong model designing a spec/oracle, a
reviewer diagnosing a failure) is recorded as a `TraceRecord`: {task, model, role, cot, tool_calls,
output, gate_verdict, cost, latency}. Records land in a durable local JSONL (mandatory floor) and are
best-effort mirrored to R2 (reusing `telemetry.py`). The point (see docs/OPERATING-PLAN.md capture
section): because our GATE is deterministic, we can later distill from ONLY gate-verified traces
(`distillation_records()` yields `gate_verdict == "PASS"`) — a correctness-guaranteed corpus with no
LLM judge in the loop. That is the "capture strong traces -> calibrate weak model" vision, made
*verified*.

FAIL-OPEN: capture must NEVER break or delay real work; any sink failure is swallowed after the local
write. Writes go to the gitignored `ops-local/` (never the committed corpus, never .env-adjacent).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

_CAPTURE_FILE = "trace-capture.jsonl"


@dataclass
class TraceRecord:
    task: str                         # the request the model was working on
    model: str                        # the concrete model/tier that ran (e.g. "big-pickle", "opus")
    run_id: str = ""                 # server-generated Cortex run join key
    task_id: str = ""                # state-machine task within the run
    route_id: str = ""               # capability-router receipt used for this call
    prompt_id: str = ""              # one model-call/tool chain inside the run
    trace_id: str = ""               # stable Langfuse/local trace identity
    role: str = "builder"             # builder | designer | reviewer | executor
    cot: str = ""                     # captured reasoning / chain-of-thought (the teacher signal)
    tool_calls: list[Any] = field(default_factory=list)
    output: str = ""                  # the model's raw output (e.g. the slot JSON)
    gate_verdict: str = ""            # "PASS" | "FAIL" | "" (deterministic gate result; NOT a judge)
    failure_class: str | None = None  # coarse failure class when gate_verdict == "FAIL"
    cost: float = 0.0                 # output tokens or $ (0 for free models)
    latency_s: float = 0.0
    ts: float = 0.0                   # epoch seconds; stamped at capture if unset
    extra: dict[str, Any] = field(default_factory=dict)


def _capture_path(workspace: str | Path | None) -> Path:
    from cortex_core.config import resolve_workspace
    root = Path(resolve_workspace(workspace))
    out = root / "ops-local" / _CAPTURE_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def capture(record: TraceRecord, workspace: str | Path | None = None) -> bool:
    """Durably record one trace. Local JSONL is the mandatory floor (its success is the return value);
    R2 mirroring is best-effort. FAIL-OPEN -- never raises to the caller."""
    if not record.ts:
        record.ts = time.time()
    if not record.trace_id:
        import uuid
        record.trace_id = uuid.uuid4().hex
    line = json.dumps(asdict(record), ensure_ascii=False)
    try:
        path = _capture_path(workspace)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 -- capture must not break real work
        return False
    # best-effort durable mirror (reuses the existing R2 sink; no-op if unconfigured)
    try:
        from cortex_core import telemetry
        if telemetry.enabled():
            telemetry.mirror_file(str(path), env=None)
    except Exception:  # noqa: BLE001
        pass
    # Tier 3 (runbook Part 3): fan the single record into self-hosted Langfuse for analytics.
    # Fail-open + no-op when unconfigured (no LANGFUSE_* keys). Never delays/breaks the run.
    try:
        from cortex_core import langfuse_sink
        if langfuse_sink.enabled():
            langfuse_sink.push_trace(record)
    except Exception:  # noqa: BLE001
        pass
    return True


def capture_build(task: str, model: str, output: str, verdict: Any,
                  *, cot: str = "", latency_s: float = 0.0, cost: float = 0.0,
                  role: str = "builder", workspace: str | Path | None = None,
                  run_id: str = "", task_id: str = "", route_id: str = "",
                  prompt_id: str = "") -> bool:
    """Convenience: capture a build run from a GateVerdict (or a bool). `verdict` may be a
    GateVerdict (uses .passed/.failure_class) or a truthy/falsey pass flag."""
    passed = getattr(verdict, "passed", bool(verdict))
    fc = getattr(verdict, "failure_class", None)
    return capture(TraceRecord(
        task=task, model=model, run_id=run_id, task_id=task_id, route_id=route_id,
        prompt_id=prompt_id, role=role, cot=cot, output=output,
        gate_verdict="PASS" if passed else "FAIL", failure_class=fc,
        cost=cost, latency_s=latency_s), workspace=workspace)


def read_records(workspace: str | Path | None = None) -> Iterator[TraceRecord]:
    """Yield every captured record (newest sinks may lag; local JSONL is the source of truth)."""
    path = _capture_path(workspace)
    if not path.is_file():
        return
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            yield TraceRecord(**json.loads(ln))
        except Exception:  # noqa: BLE001 -- skip a corrupt line, never crash a reader
            continue


def distillation_records(workspace: str | Path | None = None) -> Iterator[TraceRecord]:
    """The judge-free distillation corpus: ONLY gate-verified (`gate_verdict == "PASS"`) traces.
    Because the gate is deterministic, every yielded trace is a correctness-*guaranteed* exemplar."""
    for r in read_records(workspace):
        if r.gate_verdict == "PASS":
            yield r
