from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import load_cortex_json, resolve_workspace

_LOG = logging.getLogger(__name__)

_DEFAULT_USER_ID = "cortex-user"
_DEFAULT_AGENT_ID = "cortex"
_DEFAULT_COLLECTION = "cortex-memory"
_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_EMBEDDER_MODEL = "nomic-embed-text:latest"
_DEFAULT_LLM_MODEL = "qwen3.5:4b"
_DEFAULT_CUSTOM_INSTRUCTIONS = (
    "Store only durable facts, decisions, preferences, constraints, and open tasks. "
    "Do not store raw transcripts, repeated boilerplate, or noisy intermediate steps. "
    "Prefer stable, reviewable memories over temporary chat history."
)

_LOCK = threading.RLock()


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value and str(value).strip():
            return str(value).strip()
    return default


def _deep_get(data: dict[str, Any] | None, *path: str, default: Any = None) -> Any:
    current: Any = data or {}
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


@dataclass(frozen=True)
class MemorySettings:
    enabled: bool
    user_id: str
    agent_id: str
    collection_name: str
    history_db_path: Path
    vector_store_path: Path
    vector_store_host: str | None
    vector_store_port: int | None
    llm_model: str
    llm_base_url: str
    embedder_model: str
    embedder_base_url: str
    embedding_model_dims: int
    prefetch_top_k: int
    search_top_k: int
    custom_instructions: str
    rerank: bool


def _workspace_root(workspace: str | Path | None = None) -> Path:
    return resolve_workspace(workspace)


def load_settings(workspace: str | Path | None = None) -> MemorySettings:
    ws = _workspace_root(workspace)
    raw = load_cortex_json(ws)
    mem = raw.get("memory", {}) if isinstance(raw, dict) else {}
    oss = mem.get("oss", {}) if isinstance(mem, dict) else {}
    llm = _deep_get(oss, "llm", "config", default={}) if isinstance(oss, dict) else {}
    embedder = _deep_get(oss, "embedder", "config", default={}) if isinstance(oss, dict) else {}
    vector_store = _deep_get(oss, "vector_store", "config", default={}) if isinstance(oss, dict) else {}

    enabled = _truthy(_env_first("CORTEX_MEM0_ENABLED", default=str(mem.get("enabled", False))))
    user_id = _env_first("CORTEX_MEM0_USER_ID", default=str(mem.get("user_id", _DEFAULT_USER_ID)))
    agent_id = _env_first("CORTEX_MEM0_AGENT_ID", default=str(mem.get("agent_id", _DEFAULT_AGENT_ID)))
    collection_name = _env_first(
        "CORTEX_MEM0_COLLECTION", default=str(_deep_get(mem, "oss", "vector_store", "config", "collection_name",
                                                        default=_DEFAULT_COLLECTION))
    )

    history_db = Path(_env_first(
        "CORTEX_MEM0_HISTORY_DB",
        default=str(_deep_get(mem, "history_db_path", default=ws / "logs" / "mem0" / "history.db")),
    )).expanduser()
    if not history_db.is_absolute():
        history_db = (ws / history_db).resolve()

    vector_store_path = Path(_env_first(
        "CORTEX_MEM0_VECTOR_PATH",
        default=str(vector_store.get("path") or (ws / "logs" / "mem0_qdrant")),
    )).expanduser()
    if not vector_store_path.is_absolute():
        vector_store_path = (ws / vector_store_path).resolve()

    vector_store_host = _env_first("CORTEX_MEM0_VECTOR_HOST", default=str(vector_store.get("host") or "")).strip() or None
    vector_store_port_raw = _env_first("CORTEX_MEM0_VECTOR_PORT", default=str(vector_store.get("port") or "")).strip()
    vector_store_port = int(vector_store_port_raw) if vector_store_port_raw.isdigit() else None

    llm_model = _env_first(
        "CORTEX_MEM0_LLM_MODEL",
        default=str(llm.get("model") or _DEFAULT_LLM_MODEL),
    )
    llm_base_url = _env_first(
        "CORTEX_MEM0_OLLAMA_URL",
        "CORTEX_MEM0_LLM_URL",
        default=str(llm.get("ollama_base_url") or _DEFAULT_OLLAMA_URL),
    )
    embedder_model = _env_first(
        "CORTEX_MEM0_EMBEDDER_MODEL",
        default=str(embedder.get("model") or _DEFAULT_EMBEDDER_MODEL),
    )
    embedder_base_url = _env_first(
        "CORTEX_MEM0_EMBEDDER_URL",
        default=str(embedder.get("ollama_base_url") or llm_base_url),
    )
    embedding_model_dims_raw = _env_first(
        "CORTEX_MEM0_EMBED_DIMS",
        default=str(vector_store.get("embedding_model_dims") or 768),
    )
    try:
        embedding_model_dims = max(1, int(embedding_model_dims_raw))
    except ValueError:
        embedding_model_dims = 768
    prefetch_top_k_raw = _env_first("CORTEX_MEM0_PREFETCH_TOP_K", default=str(mem.get("prefetch_top_k", 4)))
    search_top_k_raw = _env_first("CORTEX_MEM0_SEARCH_TOP_K", default=str(mem.get("search_top_k", 8)))
    try:
        prefetch_top_k = max(1, int(prefetch_top_k_raw))
    except ValueError:
        prefetch_top_k = 4
    try:
        search_top_k = max(1, int(search_top_k_raw))
    except ValueError:
        search_top_k = 8

    custom_instructions = _env_first(
        "CORTEX_MEM0_CUSTOM_INSTRUCTIONS",
        default=str(mem.get("custom_instructions") or _DEFAULT_CUSTOM_INSTRUCTIONS),
    )
    rerank = _truthy(_env_first("CORTEX_MEM0_RERANK", default=str(mem.get("rerank", False))))

    # If Mem0 is not enabled, keep the rest of the config available for inspection,
    # but don't attempt to instantiate the client.
    return MemorySettings(
        enabled=enabled,
        user_id=user_id,
        agent_id=agent_id,
        collection_name=collection_name,
        history_db_path=history_db,
        vector_store_path=vector_store_path,
        vector_store_host=vector_store_host,
        vector_store_port=vector_store_port,
        llm_model=llm_model,
        llm_base_url=llm_base_url,
        embedder_model=embedder_model,
        embedder_base_url=embedder_base_url,
        embedding_model_dims=embedding_model_dims,
        prefetch_top_k=prefetch_top_k,
        search_top_k=search_top_k,
        custom_instructions=custom_instructions,
        rerank=rerank,
    )


def build_mem0_config(settings: MemorySettings) -> dict[str, Any]:
    vector_config: dict[str, Any] = {
        "collection_name": settings.collection_name,
        "path": str(settings.vector_store_path),
        "embedding_model_dims": settings.embedding_model_dims,
    }
    if settings.vector_store_host:
        vector_config["host"] = settings.vector_store_host
    if settings.vector_store_port is not None:
        vector_config["port"] = settings.vector_store_port

    return {
        "vector_store": {
            "provider": "qdrant",
            "config": vector_config,
        },
        "llm": {
            "provider": "ollama",
            "config": {
                "model": settings.llm_model,
                "temperature": 0,
                "max_tokens": 512,
                "ollama_base_url": settings.llm_base_url,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": settings.embedder_model,
                "ollama_base_url": settings.embedder_base_url,
            },
        },
        "history_db_path": str(settings.history_db_path),
        "custom_instructions": settings.custom_instructions,
    }


def _memory_cache_key(settings: MemorySettings) -> tuple[Any, ...]:
    return (
        settings.collection_name,
        str(settings.history_db_path),
        str(settings.vector_store_path),
        settings.vector_store_host,
        settings.vector_store_port,
        settings.llm_model,
        settings.llm_base_url,
        settings.embedder_model,
        settings.embedder_base_url,
        settings.embedding_model_dims,
        settings.custom_instructions,
    )


@lru_cache(maxsize=8)
def _client_from_key(cache_key: tuple[Any, ...]) -> Any:
    from mem0 import Memory

    # Reconstruct from the key via the settings object cached in the module-level map.
    settings = _CLIENT_SETTINGS[cache_key]
    return Memory.from_config(build_mem0_config(settings))


_CLIENT_SETTINGS: dict[tuple[Any, ...], MemorySettings] = {}


def _client(settings: MemorySettings) -> Any | None:
    if not settings.enabled:
        return None
    cache_key = _memory_cache_key(settings)
    with _LOCK:
        _CLIENT_SETTINGS[cache_key] = settings
    try:
        return _client_from_key(cache_key)
    except Exception as exc:  # noqa: BLE001 - fail open; memory is additive
        _LOG.warning("Mem0 client unavailable: %s", exc)
        return None


def status(workspace: str | Path | None = None) -> dict[str, Any]:
    settings = load_settings(workspace)
    return {
        "enabled": settings.enabled,
        "user_id": settings.user_id,
        "agent_id": settings.agent_id,
        "collection_name": settings.collection_name,
        "history_db_path": str(settings.history_db_path),
        "vector_store_path": str(settings.vector_store_path),
        "llm_model": settings.llm_model,
        "embedder_model": settings.embedder_model,
        "prefetch_top_k": settings.prefetch_top_k,
        "search_top_k": settings.search_top_k,
    }


memory_status = status


def remember_closeout(
    *,
    workspace: str | Path | None,
    task: str,
    result: str,
    status_text: str = "completed",
    tests: str = "",
    scripts: str = "",
    contract_id: str = "",
    evidence: list[dict[str, Any]] | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> bool:
    # GAP G4 memory-write policy: the store is a durable memory, so apply the SAME
    # deterministic input boundary the closeout write path uses (reject prompt-injection /
    # oversized / empty subjects) BEFORE anything reaches mem0 -- validate inputs, not writes.
    # This is the "memory-poisoning exposure" guard on the one path that literally writes to a
    # memory. Judge-free and stdlib-only; no client is constructed for rejected input.
    from .write_policy import check_write_policy

    policy = check_write_policy(task, result, tests=tests, scripts=scripts)
    if not policy.allowed:
        _LOG.warning("Mem0 write refused by memory-write policy: %s", "; ".join(policy.violations))
        return False

    settings = load_settings(workspace)
    client = _client(settings)
    if client is None:
        return False

    pieces = [
        f"Task: {task}",
        f"Status: {status_text}",
        f"Result: {result}",
    ]
    if tests:
        pieces.append(f"Tests: {tests}")
    if scripts:
        pieces.append(f"Scripts: {scripts}")
    if contract_id:
        pieces.append(f"Contract: {contract_id}")
    if evidence:
        pieces.append("Evidence: " + json.dumps(evidence, ensure_ascii=False))
    text = "\n".join(pieces)
    metadata = {
        "source": "closeout",
        "workspace": str(_workspace_root(workspace)),
        "task": task,
        "status": status_text,
    }
    try:
        client.add(
            text,
            user_id=settings.user_id,
            agent_id=agent_id or settings.agent_id,
            run_id=run_id or task,
            metadata=metadata,
            infer=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - fail open, never block the turn
        _LOG.warning("Mem0 closeout sync failed: %s", exc)
        return False


def recall_relevant(
    query: str,
    *,
    workspace: str | Path | None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    settings = load_settings(workspace)
    client = _client(settings)
    if client is None or not query.strip():
        return []
    try:
        response = client.search(
            query,
            top_k=top_k or settings.prefetch_top_k,
            filters={"user_id": settings.user_id},
            rerank=settings.rerank,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Mem0 recall failed: %s", exc)
        return []
    results = response.get("results", []) if isinstance(response, dict) else []
    normalized: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            normalized.append(
                {
                    "id": item.get("id"),
                    "memory": item.get("memory") or item.get("text") or item.get("content") or "",
                    "score": item.get("score"),
                }
            )
    return normalized


def prefetch_summary(workspace: str | Path | None) -> dict[str, Any]:
    ws = _workspace_root(workspace)
    state_path = ws / "logs" / "hermes_state.json"
    if not state_path.exists():
        return {"query": "", "memories": []}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"query": "", "memories": []}
    query = " ".join(
        str(state.get(key, "")).strip()
        for key in ("task_id", "description", "status")
        if str(state.get(key, "")).strip()
    ).strip()
    if not query:
        return {"query": "", "memories": []}
    return {"query": query, "memories": recall_relevant(query, workspace=ws)}
