"""Hermes hook boundary for Cortex-assured work.

This plugin owns no verdict and mints no receipt.  It observes the main Cortex
MCP results already returned to Hermes and prevents a session that has entered an
assured run from falling back to raw, uncorrelated delegation.

Ordinary sessions remain advisory and can delegate normally.  Once a session
starts ``assured_build``/``assured_research``, delegation is blocked until the
main Cortex MCP has verified an externally signed preflight as
``GOVERNED_ACTIVE`` for the same server run.  Even then, every delegated task
must carry the exact joined identifiers in its context.
"""
from __future__ import annotations

import json
import re
from threading import RLock
from typing import Any


_LOCK = RLock()
_SESSIONS: dict[str, dict[str, Any]] = {}
_ASSURED_TRACKS = {"assured_build", "assured_research"}
_LOCAL_DOCKER_DIAGNOSTIC = re.compile(
    r"^\s*docker(?:\.exe)?\s+(?:--version|version|ps)\s*$", re.IGNORECASE,
)


def _is_tool(tool_name: str, raw_name: str) -> bool:
    return tool_name == raw_name or tool_name.endswith("_" + raw_name)


def _json_result(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return None
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _contexts(args: Any) -> list[str]:
    if not isinstance(args, dict):
        return []
    tasks = args.get("tasks")
    if isinstance(tasks, list) and tasks:
        return [str(item.get("context") or "") for item in tasks if isinstance(item, dict)]
    return [str(args.get("context") or "")]


_NONTRIVIAL_PATH_MARKERS = ("plugins/", "skills/", "cortex/")
_BLOCK_THRESHOLD = 3  # Safety valve: after this many blocks, assume cortex_search MCP is unavailable


def _is_nontrivial_write(path: str) -> bool:
    if not path:
        return False
    if path.endswith(".py"):
        return True
    return any(marker in path for marker in _NONTRIVIAL_PATH_MARKERS)


def _required_markers(state: dict[str, str]) -> tuple[str, ...]:
    return (
        f"CORTEX_RUN_ID={state['run_id']}",
        f"CORTEX_TASK_ID={state['task_id']}",
        f"CORTEX_ROUTE_ID={state['route_id']}",
    )


def _on_pre_tool_call(
    tool_name: str = "", args: Any = None, session_id: str = "", **_: Any,
) -> dict[str, str] | None:
    args = args if isinstance(args, dict) else {}

    if _is_tool(tool_name, "cortex_run_start"):
        track = args.get("track", "build")
        if track not in _ASSURED_TRACKS:
            return _block(
                "cortex-assured-driver refused a legacy Cortex run. Use track=assured_build "
                "or track=assured_research; LEGACY_UNASSURED cannot support a governed claim."
            )

    with _LOCK:
        state = dict(_SESSIONS.get(session_id, {}))
    if not state.get("status"):
        if _is_tool(tool_name, "delegate_task") and not state.get("searched"):
            block_count = state.get("block_count", 0)
            if block_count >= _BLOCK_THRESHOLD:
                # Safety valve: cortex_search MCP may be down. Log and allow through.
                with _LOCK:
                    s = _SESSIONS.get(session_id)
                    if s is not None:
                        s["searched"] = True
                        s["safety_valve"] = True
                    else:
                        _SESSIONS[session_id] = {"searched": True, "safety_valve": True}
                return None
            with _LOCK:
                s = _SESSIONS.get(session_id)
                if s is not None:
                    s["block_count"] = block_count + 1
                else:
                    _SESSIONS[session_id] = {"block_count": 1}
            return _block(
                "Call cortex_search before delegating. Retrieve context first; "
                "do not skip the research step. "
                f"(block {block_count + 1}/{_BLOCK_THRESHOLD} before safety valve)"
            )
        if _is_tool(tool_name, "write_file") or _is_tool(tool_name, "patch"):
            if _is_nontrivial_write(str(args.get("path") or "")) and not state.get("searched"):
                block_count = state.get("block_count", 0)
                if block_count >= _BLOCK_THRESHOLD:
                    with _LOCK:
                        s = _SESSIONS.get(session_id)
                        if s is not None:
                            s["searched"] = True
                            s["safety_valve"] = True
                        else:
                            _SESSIONS[session_id] = {"searched": True, "safety_valve": True}
                    return None
                with _LOCK:
                    s = _SESSIONS.get(session_id)
                    if s is not None:
                        s["block_count"] = block_count + 1
                    else:
                        _SESSIONS[session_id] = {"block_count": 1}
                return _block(
                    "Call cortex_search before making code or plugin changes. "
                    "Retrieve context first; do not skip the research step. "
                    f"(block {block_count + 1}/{_BLOCK_THRESHOLD} before safety valve)"
                )
        return None

    if _is_tool(tool_name, "delegate_task"):
        if state.get("status") != "GOVERNED_ACTIVE":
            return _block(
                "Cortex assured run is awaiting a signed external preflight. Raw delegation is "
                "blocked; call cortex_contract(action=driver_preflight). Missing route binding, "
                "signer, evaluator, or telemetry must remain UNRESOLVED."
            )
        contexts = _contexts(args)
        markers = _required_markers(state)
        if not contexts or any(any(marker not in context for marker in markers)
                               for context in contexts):
            return _block(
                "Every delegated task in this assured run must carry exact context markers: "
                + ", ".join(markers)
            )

    if _is_tool(tool_name, "terminal"):
        command = str(args.get("command") or "")
        forbidden = ("cortex_core.govern", "StateEngine(", "mint_approval(",
                     "run_and_record_smoke_verdict(")
        if any(token in command for token in forbidden):
            return _block(
                "Direct local Cortex engine/receipt execution is blocked during an assured MCP "
                "run. Follow the server's legal tools and submit opaque receipt IDs only."
            )
        lowered = command.lower()
        if "docker" in lowered and "ssh gravebuster" not in lowered:
            if not _LOCAL_DOCKER_DIAGNOSTIC.fullmatch(command):
                return _block(
                    "Docker execution for a Cortex-assured Hades run belongs on gravebuster. "
                    "Use ssh gravebuster with the same run/task/route markers; local Windows is "
                    "limited to bounded docker --version/version/ps diagnostics."
                )
    return None


def _on_post_tool_call(
    tool_name: str = "", result: Any = None, session_id: str = "", **_: Any,
) -> None:
    if session_id and _is_tool(tool_name, "cortex_search"):
        data = _json_result(result)
        # Only mark as searched if the result is non-empty and not an error
        if data and not data.get("error"):
            with _LOCK:
                current = _SESSIONS.get(session_id)
                if current is None:
                    _SESSIONS[session_id] = {"searched": True}
                else:
                    current["searched"] = True
                    current.pop("block_count", None)  # reset counter on success

    data = _json_result(result)
    if not data or not session_id:
        return None

    # cortex_run_start satisfies the search requirement: the state machine's
    # SEARCH_BRAIN phase (phase 1) handles search. Starting the pipeline
    # means search will happen as part of the structured flow.
    if _is_tool(tool_name, "cortex_run_start") and data.get("ok") is not False:
        with _LOCK:
            current = _SESSIONS.get(session_id)
            if current is None or not current.get("searched"):
                if current is None:
                    _SESSIONS[session_id] = {"searched": True, "sm_active": True}
                else:
                    current["searched"] = True
                    current["sm_active"] = True
                    current.pop("block_count", None)

    if _is_tool(tool_name, "cortex_run_start"):
        if data.get("assurance_mode") != "ASSURED" or data.get("track") not in _ASSURED_TRACKS:
            return None
        run_id, task_id = data.get("run_id"), data.get("task_id")
        if not isinstance(run_id, str) or not run_id or not isinstance(task_id, str) or not task_id:
            return None
        with _LOCK:
            _SESSIONS[session_id] = {
                "status": "AWAITING_SIGNED_PREFLIGHT",
                "run_id": run_id,
                "task_id": task_id,
                "track": data["track"],
                "route_id": "",
            }
        return None

    if _is_tool(tool_name, "cortex_contract") and isinstance(data.get("preflight"), dict):
        preflight = data.get("preflight")
        if not data.get("ok") or not isinstance(preflight, dict):
            return None
        if preflight.get("status") != "GOVERNED_ACTIVE":
            return None
        with _LOCK:
            current = _SESSIONS.get(session_id)
            if not current:
                return None
            if (preflight.get("run_id") != current.get("run_id")
                    or preflight.get("track") != current.get("track")):
                current["status"] = "PREFLIGHT_IDENTITY_MISMATCH"
                return None
            route_id = preflight.get("route_id")
            if not isinstance(route_id, str) or not route_id:
                current["status"] = "PREFLIGHT_ROUTE_MISSING"
                return None
            current["route_id"] = route_id
            current["status"] = "GOVERNED_ACTIVE"
    return None


def _on_session_end(session_id: str = "", **_: Any) -> None:
    if session_id:
        with _LOCK:
            _SESSIONS.pop(session_id, None)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
