from __future__ import annotations

from pathlib import Path
from typing import Any
import threading

from .audit import write_closeout
from .config import resolve_workspace
from .memory import memory_status, prefetch_summary, remember_closeout

HOOK_NAMES = ("pre_llm_call", "post_llm_call")
SKILLS = (
    "cortex-skill",
    "cortex-build-pipeline",
    "cortex-write-log",
    "cortex-fetch",
)


def _register_mapping(context: Any, attr_name: str, items: dict[str, Any]) -> None:
    registrar = getattr(context, f"register_{attr_name}", None)
    if callable(registrar):
        for name, value in items.items():
            registrar(name, value)
        return

    container = getattr(context, attr_name, None)
    if isinstance(container, dict):
        container.update(items)
        return

    setattr(context, attr_name, items.copy())


def pre_llm_call(context: Any, **_: Any) -> dict[str, Any]:
    workspace = resolve_workspace(Path.cwd())
    payload = {
        "workspace": str(workspace),
        "note": "Search audit logs and local docs before answering.",
    }
    try:
        mem = prefetch_summary(workspace)
        if mem.get("memories"):
            payload["memory"] = mem
    except Exception:
        pass
    return payload


def post_llm_call(context: Any, **payload: Any) -> dict[str, Any]:
    workspace = resolve_workspace(Path.cwd())
    if payload.get("write_closeout", True):
        path = write_closeout(
            workspace=workspace,
            task=str(payload.get("task", "llm-turn")),
            result=str(payload.get("result", "completed")),
            status=str(payload.get("status", "completed")),
            handoff=payload.get("handoff"),
        )
        if payload.get("memory_sync", True):
            threading.Thread(
                target=remember_closeout,
                kwargs={
                    "workspace": workspace,
                    "task": str(payload.get("task", "llm-turn")),
                    "result": str(payload.get("result", "completed")),
                    "status_text": str(payload.get("status", "completed")),
                    "tests": str(payload.get("tests", "")),
                    "scripts": str(payload.get("scripts", "")),
                    "contract_id": str(payload.get("contract_id", "")),
                    "evidence": payload.get("evidence"),
                    "agent_id": str(payload.get("agent_id", "")) or None,
                    "run_id": str(payload.get("run_id", "")) or None,
                },
                daemon=True,
            ).start()
        return {
            "workspace": str(workspace),
            "logged": True,
            "closeout_path": str(path),
            "memory": memory_status(workspace),
        }
    return {"workspace": str(workspace), "logged": False, "memory": memory_status(workspace)}


def register(context: Any) -> dict[str, Any]:
    hooks = {
        "pre_llm_call": pre_llm_call,
        "post_llm_call": post_llm_call,
    }
    skills = {name: f"skills/{name}/SKILL.md" for name in SKILLS}

    _register_mapping(context, "hook", hooks)
    _register_mapping(context, "skill", skills)

    if hasattr(context, "metadata") and isinstance(context.metadata, dict):
        context.metadata["cortex_workspace"] = str(resolve_workspace(Path.cwd()))

    return {"hooks": list(hooks), "skills": list(skills)}
