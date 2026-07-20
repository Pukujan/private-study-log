from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cortex_core import langfuse_sink, otel
from cortex_core.mcp import cortex_run_start, cortex_run_state
from cortex_core.state_engine import StateEngine
from cortex_core.trace_capture import TraceRecord, capture


def test_server_generates_root_run_id_and_children_inherit_it(tmp_path: Path) -> None:
    eng = StateEngine(str(tmp_path / "state.db"), workspace=str(tmp_path))
    try:
        root = eng.create_task({"seeking": "root", "run_id": "caller-forgery"}, track="mission")
        run_id = eng.get(root)["run_id"]
        assert run_id.startswith("run_") and run_id != "caller-forgery"
        child = eng.create_task({"seeking": "child", "run_id": "different"}, parent_id=root)
        assert eng.get(child)["run_id"] == run_id
        dispatched = eng.dispatch_workers(root, [{
            "intent": {"seeking": "unit", "run_id": "worker-forgery"},
            "claims": [{"kind": "path", "key": "unit/**"}],
        }])
        assert eng.get(dispatched["worker_ids"][0])["run_id"] == run_id
    finally:
        eng.close()


def test_mcp_start_surfaces_run_id_and_legacy_assurance_warning(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    env = asyncio.run(cortex_run_start({"seeking": "x", "run_id": "caller"},
                                       workspace=str(tmp_path)))
    assert env["run_id"].startswith("run_")
    assert env["intent"]["run_id"] == env["run_id"]
    assert env["assurance_mode"] == "LEGACY_UNASSURED"
    resumed = asyncio.run(cortex_run_state(env["task_id"], workspace=str(tmp_path)))
    assert resumed["run_id"] == env["run_id"]
    assert resumed["track"] == "build"
    assert resumed["assurance_mode"] == "LEGACY_UNASSURED"


def test_assured_mode_survives_state_resume(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    env = asyncio.run(cortex_run_start(
        {"seeking": "research before build"}, track="assured_build", workspace=str(tmp_path),
    ))
    assert env["assurance_mode"] == "ASSURED"
    resumed = asyncio.run(cortex_run_state(env["task_id"], workspace=str(tmp_path)))
    assert resumed["run_id"] == env["run_id"]
    assert resumed["track"] == "assured_build"
    assert resumed["assurance_mode"] == "ASSURED"


def test_same_run_identity_joins_local_langfuse_and_otel_records(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    run_id, task_id, route_id, prompt_id = "run_1", "task_1", "mr_1", "prompt_1"
    record = TraceRecord(
        task="build", model="big-pickle", run_id=run_id, task_id=task_id,
        route_id=route_id, prompt_id=prompt_id, trace_id="a" * 32, gate_verdict="PASS",
    )
    assert capture(record, workspace=tmp_path)
    stored = json.loads((tmp_path / "ops-local" / "trace-capture.jsonl").read_text(encoding="utf-8"))
    assert stored["run_id"] == run_id and stored["route_id"] == route_id

    batch = langfuse_sink.build_batch(record)
    body = batch["batch"][0]["body"]
    assert body["id"] == "a" * 32 and body["sessionId"] == run_id
    assert body["metadata"]["task_id"] == task_id

    ledger = tmp_path / "metrics.jsonl"
    env = {"CORTEX_OTEL": "off", "CORTEX_METRICS_LEDGER": str(ledger)}
    with otel.gen_ai_span("unit", run_id=run_id, task_id=task_id, route_id=route_id,
                          prompt_id=prompt_id, env=env):
        pass
    metric = json.loads(ledger.read_text(encoding="utf-8"))
    assert (metric["run_id"], metric["task_id"], metric["route_id"], metric["prompt_id"]) == (
        run_id, task_id, route_id, prompt_id,
    )
