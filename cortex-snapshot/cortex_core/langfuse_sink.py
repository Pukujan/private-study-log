"""Fail-open Langfuse Tier-3 sink for `trace_capture` (runbook Part 3, tier 3).

Pushes one gate-verified TraceRecord to a self-hosted Langfuse as a trace + a `gate_verdict`
score, via a direct HTTP POST to Langfuse's public ingestion API (Basic auth). NO SDK dependency
and NO OTLP dep -- stdlib `urllib` only -- matching `telemetry.py`'s zero-dep, fail-open ethos.

FAIL-OPEN by contract: a down / unconfigured / unreachable Langfuse is a silent no-op. The local
JSONL write in `trace_capture.capture()` is the source of truth; this sink is analytics fan-in and
must NEVER break or delay a real run.

Config is read from the environment, filling gaps from the gitignored `.env` (same pattern as
`telemetry._cfg`). Required keys (create them in the Langfuse UI on the self-hosted instance):
  LANGFUSE_HOST         e.g. http://<tailnet-ip>:3000   (infra endpoint -- gitignored .env ONLY)
  LANGFUSE_PUBLIC_KEY   pk-lf-...
  LANGFUSE_SECRET_KEY   sk-lf-...
Absent any of the three, `enabled()` is False and `push_trace()` is a no-op. No endpoint/secret is
ever hard-coded here (public-repo OPS boundary).
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _cfg(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    e = dict(os.environ) if env is not None else dict(os.environ)
    if env is None:
        # os.environ wins; fill gaps from the repo's gitignored .env / ops-local/.env so a run
        # with a local .env works without exporting vars. Absent/unreadable .env must not break.
        try:
            root = Path(__file__).resolve().parents[1]
            for fn in (".env", "ops-local/.env"):
                p = root / fn
                if not p.is_file():
                    continue
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        e.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:  # noqa: BLE001 -- config discovery must never raise
            pass
    else:
        e = dict(env)
    host, pub, sec = e.get("LANGFUSE_HOST"), e.get("LANGFUSE_PUBLIC_KEY"), e.get("LANGFUSE_SECRET_KEY")
    if host and pub and sec:
        return {"host": host.rstrip("/"), "public": pub, "secret": sec}
    return None


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff LANGFUSE_HOST + LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are all configured."""
    return _cfg(env) is not None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def build_batch(record: Any) -> dict:
    """Build the Langfuse ingestion batch (a trace-create + an optional gate_verdict score-create)
    for one TraceRecord (dataclass or dict). Pure/deterministic given a trace id + timestamp is
    passed via the record; exposed for unit-testing the payload without a network call."""
    d = record if isinstance(record, dict) else getattr(record, "__dict__", {})
    ts = d.get("ts") or time.time()
    now = _iso(ts)
    tid = d.get("trace_id") or uuid.uuid4().hex
    cot = d.get("cot") or ""
    batch: list[dict] = [{
        "id": uuid.uuid4().hex, "type": "trace-create", "timestamp": now,
        "body": {
            "id": tid,
            "name": f"cortex:{d.get('role', 'builder')}",
            "sessionId": d.get("run_id") or None,
            "input": d.get("task", ""),
            "output": d.get("output", ""),
            "tags": ["cortex", "trace_capture", str(d.get("gate_verdict") or "unknown")],
            "metadata": {
                "model": d.get("model"), "role": d.get("role"),
                "run_id": d.get("run_id"), "task_id": d.get("task_id"),
                "route_id": d.get("route_id"), "prompt_id": d.get("prompt_id"),
                "gate_verdict": d.get("gate_verdict"), "failure_class": d.get("failure_class"),
                "cost": d.get("cost"), "latency_s": d.get("latency_s"),
                "tool_calls": d.get("tool_calls"), "cot": cot[:5000],
                **(d.get("extra") or {}),
            },
        },
    }]
    gv = d.get("gate_verdict")
    if gv:
        batch.append({
            "id": uuid.uuid4().hex, "type": "score-create", "timestamp": now,
            "body": {"traceId": tid, "name": "gate_verdict", "value": str(gv), "dataType": "CATEGORICAL"},
        })
    return {"batch": batch}


def push_trace(record: Any, env: Mapping[str, str] | None = None) -> bool:
    """Push one TraceRecord to Langfuse. Returns True on a 2xx, False otherwise. FAIL-OPEN --
    never raises (a down/unconfigured Langfuse just returns False)."""
    cfg = _cfg(env)
    if not cfg:
        return False
    try:
        payload = json.dumps(build_batch(record)).encode("utf-8")
        auth = base64.b64encode(f"{cfg['public']}:{cfg['secret']}".encode()).decode()
        req = urllib.request.Request(
            cfg["host"] + "/api/public/ingestion", data=payload,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 -- fixed internal host
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 -- fail-open by contract
        return False
