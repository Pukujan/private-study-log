from __future__ import annotations

import json
from pathlib import Path

import cortex_core.memory as memory_mod


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "logs").mkdir(parents=True)
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return workspace


def test_build_mem0_config_uses_local_oss_settings(tmp_path: Path, monkeypatch) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    monkeypatch.setenv("CORTEX_MEM0_ENABLED", "1")
    monkeypatch.setenv("CORTEX_MEM0_USER_ID", "test-user")
    monkeypatch.setenv("CORTEX_MEM0_AGENT_ID", "test-agent")
    monkeypatch.setenv("CORTEX_MEM0_LLM_MODEL", "qwen-4b")
    monkeypatch.setenv("CORTEX_MEM0_EMBEDDER_MODEL", "nomic-embed-text:latest")
    monkeypatch.setenv("CORTEX_MEM0_VECTOR_PATH", str(workspace / "logs" / "mem0_qdrant"))
    monkeypatch.setenv("CORTEX_MEM0_EMBED_DIMS", "768")

    settings = memory_mod.load_settings(workspace)
    config = memory_mod.build_mem0_config(settings)

    assert settings.enabled is True
    assert settings.user_id == "test-user"
    assert settings.agent_id == "test-agent"
    assert config["llm"]["provider"] == "ollama"
    assert config["llm"]["config"]["model"] == "qwen-4b"
    assert config["embedder"]["config"]["model"] == "nomic-embed-text:latest"
    assert config["vector_store"]["provider"] == "qdrant"
    assert config["vector_store"]["config"]["path"].endswith("mem0_qdrant")


def test_remember_closeout_uses_the_mem0_client(tmp_path: Path, monkeypatch) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    monkeypatch.setenv("CORTEX_MEM0_ENABLED", "1")

    captured: dict[str, object] = {}

    class FakeClient:
        def add(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs

    monkeypatch.setattr(memory_mod, "_client", lambda settings: FakeClient())

    ok = memory_mod.remember_closeout(
        workspace=workspace,
        task="trim history",
        result="lowered the cap and wired Mem0",
        status_text="completed",
        tests="pytest tests/test_memory_layer.py -q",
        scripts="cortex-mcp",
        contract_id="contract-123",
        evidence=[{"type": "file", "ref": "cortex_core/memory.py"}],
        agent_id="hermes",
        run_id="run-123",
    )

    assert ok is True
    assert "Task: trim history" in str(captured["messages"])
    assert captured["kwargs"]["user_id"] == "cortex-user"
    assert captured["kwargs"]["agent_id"] == "hermes"
    assert captured["kwargs"]["run_id"] == "run-123"


def test_prefetch_summary_reads_the_current_task_state(tmp_path: Path, monkeypatch) -> None:
    workspace = _make_workspace(tmp_path)
    state_path = workspace / "logs" / "hermes_state.json"
    state_path.write_text(
        json.dumps({"task_id": "task-7", "description": "fix memory retention", "status": "in_progress"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(memory_mod, "recall_relevant", lambda query, workspace=None, top_k=None: [{"memory": query}])

    summary = memory_mod.prefetch_summary(workspace)

    assert summary["query"] == "task-7 fix memory retention in_progress"
    assert summary["memories"] == [{"memory": "task-7 fix memory retention in_progress"}]
