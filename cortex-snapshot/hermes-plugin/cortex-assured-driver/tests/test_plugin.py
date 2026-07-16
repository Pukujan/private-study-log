from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "__init__.py"
SPEC = importlib.util.spec_from_file_location("cortex_assured_driver_test", PLUGIN)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def setup_function() -> None:
    module._SESSIONS.clear()


def start(session: str = "s1") -> None:
    module._on_post_tool_call(
        tool_name="mcp_cortex_local_write_cortex_run_start",
        session_id=session,
        result=json.dumps({
            "assurance_mode": "ASSURED", "track": "assured_build",
            "run_id": "run_1", "task_id": "task_1",
        }),
    )


def activate(session: str = "s1", *, run_id: str = "run_1") -> None:
    module._on_post_tool_call(
        tool_name="mcp_cortex_local_write_cortex_contract",
        session_id=session,
        result=json.dumps({
            "ok": True,
            "preflight": {
                "status": "GOVERNED_ACTIVE", "run_id": run_id,
                "track": "assured_build", "route_id": "route_1",
            },
        }),
    )


def search(session: str = "s1") -> None:
    module._on_post_tool_call(
        tool_name="mcp_cortex_local_read_cortex_search",
        session_id=session,
        result=json.dumps({"ok": True}),
    )


def test_legacy_run_start_is_blocked() -> None:
    out = module._on_pre_tool_call(
        tool_name="mcp_x_cortex_run_start", args={"track": "build"}, session_id="s1",
    )
    assert out["action"] == "block"
    assert "LEGACY_UNASSURED" in out["message"]


def test_ordinary_advisory_session_can_delegate() -> None:
    search(session="ordinary")
    assert module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "ordinary"}, session_id="ordinary",
    ) is None


def test_assured_session_blocks_delegate_before_signed_preflight() -> None:
    start()
    out = module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "build"}, session_id="s1",
    )
    assert out["action"] == "block"
    assert "signed external preflight" in out["message"]


def test_mismatched_preflight_does_not_activate() -> None:
    start()
    activate(run_id="run_other")
    assert module._SESSIONS["s1"]["status"] == "PREFLIGHT_IDENTITY_MISMATCH"
    assert module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "build"}, session_id="s1",
    )["action"] == "block"


def test_active_delegate_requires_exact_join_markers_for_every_child() -> None:
    start()
    activate()
    missing = module._on_pre_tool_call(
        tool_name="delegate_task",
        args={"tasks": [{"goal": "a", "context": "CORTEX_RUN_ID=run_1"}]},
        session_id="s1",
    )
    assert missing["action"] == "block"
    context = "\n".join((
        "CORTEX_RUN_ID=run_1", "CORTEX_TASK_ID=task_1", "CORTEX_ROUTE_ID=route_1",
    ))
    assert module._on_pre_tool_call(
        tool_name="delegate_task",
        args={"tasks": [{"goal": "a", "context": context},
                        {"goal": "b", "context": context}]},
        session_id="s1",
    ) is None


def test_direct_local_engine_is_blocked_during_assured_run() -> None:
    start()
    out = module._on_pre_tool_call(
        tool_name="terminal", args={"command": "python -m cortex_core.govern --selftest"},
        session_id="s1",
    )
    assert out["action"] == "block"


def test_local_docker_execution_is_blocked_but_gravebuster_and_probe_are_allowed() -> None:
    start()
    out = module._on_pre_tool_call(
        tool_name="terminal", args={"command": "docker compose up --build"}, session_id="s1",
    )
    assert out["action"] == "block"
    assert "gravebuster" in out["message"]
    assert module._on_pre_tool_call(
        tool_name="terminal", args={"command": "docker ps"}, session_id="s1",
    ) is None
    assert module._on_pre_tool_call(
        tool_name="terminal",
        args={"command": "ssh gravebuster \"docker compose up --build\""},
        session_id="s1",
    ) is None


def test_session_end_forgets_authority() -> None:
    start()
    activate()
    module._on_session_end(session_id="s1")
    assert "s1" not in module._SESSIONS


def test_register_uses_current_hook_api() -> None:
    calls = []

    class Context:
        def register_hook(self, name, callback):
            calls.append((name, callback))

    module.register(Context())
    assert [name for name, _ in calls] == ["pre_tool_call", "post_tool_call", "on_session_end"]


def test_search_required_before_delegate() -> None:
    out = module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "do stuff"}, session_id="s1",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "cortex_search" in out["message"]


def test_search_unblocks_delegate() -> None:
    search()
    assert module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "do stuff"}, session_id="s1",
    ) is None


def test_trivial_write_not_blocked() -> None:
    assert module._on_pre_tool_call(
        tool_name="write_file",
        args={"path": "D:/hermes/profiles/hades/HANDOFF.md", "content": "test"},
        session_id="s1",
    ) is None


def test_code_write_blocked_without_search() -> None:
    out = module._on_pre_tool_call(
        tool_name="write_file",
        args={"path": "D:/hermes/profiles/hades/plugins/foo/bar.py", "content": "x = 1"},
        session_id="s1",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "cortex_search" in out["message"]


def test_assured_session_not_affected_by_search_gate() -> None:
    start()
    activate()
    context = "\n".join((
        "CORTEX_RUN_ID=run_1", "CORTEX_TASK_ID=task_1", "CORTEX_ROUTE_ID=route_1",
    ))
    assert module._on_pre_tool_call(
        tool_name="delegate_task",
        args={"tasks": [{"goal": "a", "context": context}]},
        session_id="s1",
    ) is None
    assert module._on_pre_tool_call(
        tool_name="write_file",
        args={"path": "D:/hermes/plugins/foo/bar.py", "content": "x = 1"},
        session_id="s1",
    ) is None


def test_safety_valve_after_threshold_blocks() -> None:
    """After _BLOCK_THRESHOLD blocks, the gate disables itself (MCP may be down)."""
    # First N-1 attempts should be blocked
    for i in range(module._BLOCK_THRESHOLD):
        out = module._on_pre_tool_call(
            tool_name="delegate_task", args={"goal": "x"}, session_id="s1",
        )
        assert out is not None, f"block {i+1} should still be blocked"
        assert out["action"] == "block"
    # After threshold, should pass through
    out = module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "x"}, session_id="s1",
    )
    assert out is None
    assert module._SESSIONS["s1"].get("safety_valve") is True


def test_safety_valve_write_file() -> None:
    """Same safety valve for non-trivial writes."""
    for i in range(module._BLOCK_THRESHOLD):
        out = module._on_pre_tool_call(
            tool_name="write_file",
            args={"path": "plugins/test.py", "content": "x"},
            session_id="s2",
        )
        assert out is not None
    out = module._on_pre_tool_call(
        tool_name="write_file",
        args={"path": "plugins/test.py", "content": "x"},
        session_id="s2",
    )
    assert out is None


def test_failed_search_does_not_set_searched() -> None:
    """If cortex_search returns an error, don't mark as searched."""
    module._on_post_tool_call(
        tool_name="mcp_cortex_local_read_cortex_search",
        session_id="s3",
        result=json.dumps({"error": "connection failed"}),
    )
    assert module._SESSIONS.get("s3", {}).get("searched") is not True
    out = module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "x"}, session_id="s3",
    )
    assert out is not None
    assert out["action"] == "block"


def test_successful_search_resets_block_count() -> None:
    """A successful search should reset the block counter."""
    # Trigger 2 blocks
    for _ in range(2):
        module._on_pre_tool_call(
            tool_name="delegate_task", args={"goal": "x"}, session_id="s4",
        )
    assert module._SESSIONS["s4"]["block_count"] == 2
    # Successful search
    module._on_post_tool_call(
        tool_name="mcp_cortex_local_read_cortex_search",
        session_id="s4",
        result=json.dumps({"ok": True, "results": []}),
    )
    assert module._SESSIONS["s4"].get("searched") is True
    assert "block_count" not in module._SESSIONS["s4"]


def test_cortex_run_start_satisfies_search_gate() -> None:
    """Starting the state machine (cortex_run_start) should satisfy the search
    requirement, since SEARCH_BRAIN is phase 1 of the pipeline."""
    module._on_post_tool_call(
        tool_name="mcp_cortex_local_write_cortex_run_start",
        session_id="s5",
        result=json.dumps({
            "ok": True, "task_id": "t_1", "run_id": "run_1",
            "track": "build", "state": "SEARCH_BRAIN",
        }),
    )
    assert module._SESSIONS["s5"].get("searched") is True
    assert module._SESSIONS["s5"].get("sm_active") is True
    # delegate_task should be allowed without a separate cortex_search call
    out = module._on_pre_tool_call(
        tool_name="delegate_task", args={"goal": "x"}, session_id="s5",
    )
    assert out is None
    # write_file on .py should also be allowed
    out = module._on_pre_tool_call(
        tool_name="write_file", args={"path": "plugins/test.py", "content": "x"},
        session_id="s5",
    )
    assert out is None
