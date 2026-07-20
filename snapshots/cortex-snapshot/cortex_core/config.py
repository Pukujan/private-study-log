from __future__ import annotations

from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
from typing import Mapping

WORKSPACE_ENV = "CORTEX_WORKSPACE"
# Dual-plane (GAP-CORTEX-0015 H2a): the canonical READ/knowledge plane (the admin's
# curated brain). When set, READ tools (search/scope_pack/ontology) resolve HERE while
# WRITE tools (write_log/fetch_doc/research) resolve to CORTEX_WORKSPACE (the tenant's own
# folder) -- "read my brain, write their folder." Unset => single-plane (owner mode).
BRAIN_WORKSPACE_ENV = "CORTEX_BRAIN_WORKSPACE"


def make_stdio_encoding_safe() -> None:
    """Windows consoles default to a legacy codepage (cp1252) that can't
    encode most real-world corpus text (smart quotes, em-dashes, emoji).
    Any CLI entry point that prints document content, queries, or paths must
    call this first so it degrades to a replacement char instead of crashing
    with UnicodeEncodeError. Shared here (not inlined per-command) so the
    fix can't be applied to one entry point and silently missed on the rest
    -- exactly the drift a 2026-07-04 Windows sweep flagged."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


@dataclass(frozen=True)
class CortexConfig:
    repo_root: Path
    workspace_root: Path
    raw: dict


def find_repo_root(start_path: str | Path | None = None) -> Path:
    start = Path(start_path or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "cortex.json").is_file() and (candidate / "library" / "cortex-library").exists():
            return candidate
    raise FileNotFoundError(
        "Unable to locate a Cortex checkout. Run from the repo root or set CORTEX_WORKSPACE."
    )


def load_cortex_json(repo_root: Path) -> dict:
    config_path = repo_root / "cortex.json"
    if not config_path.is_file():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def _looks_like_workspace(p: Path) -> bool:
    """A directory that is itself a Cortex workspace (has cortex.json or the shard dirs) --
    as opposed to a repo-root *hint* passed to resolve_workspace for auto-detection."""
    return p.is_dir() and (
        (p / "cortex.json").exists() or (p / "docs").exists()
        or (p / "library").exists() or (p / "audit").exists()
    )


def resolve_exact_workspace(path: str | Path) -> Path:
    """Resolve a workspace path DIRECTLY, ignoring the ambient CORTEX_WORKSPACE env. Used for
    dual-plane reads (GAP-0015): the brain path must win even when CORTEX_WORKSPACE points at
    the tenant. Falls back to normal resolution only if `path` isn't itself a workspace dir."""
    p = Path(path).expanduser()
    return p.resolve() if _looks_like_workspace(p) else resolve_workspace(path)


def resolve_workspace(start_path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    env_map = dict(os.environ if env is None else env)
    env_value = env_map.get(WORKSPACE_ENV)
    if env_value:
        workspace = Path(env_value).expanduser().resolve()
        if workspace.exists():
            return workspace
        raise FileNotFoundError(f"{WORKSPACE_ENV} points to a missing path: {workspace}")

    start = Path(start_path or Path.cwd()).resolve()
    if start.is_dir() and not (start / "cortex.json").exists():
        if (start / "docs").exists() or (start / "audit").exists() or (start / "library").exists():
            return start

    repo_root = find_repo_root(start)
    config = load_cortex_json(repo_root)
    fallback = config.get("paths", {}).get("workspace_fallback") if isinstance(config, dict) else None
    if fallback:
        fallback_path = Path(fallback)
        if not fallback_path.is_absolute():
            fallback_path = (repo_root / fallback_path).resolve()
        if fallback_path.exists():
            return fallback_path
    return repo_root


def resolve_workspace_override(
    path: str | Path | None, env: Mapping[str, str] | None = None
) -> Path:
    """Arg-first workspace resolution -- the INVERSE precedence of ``resolve_workspace``.

    ``resolve_workspace`` is ENV-FIRST: ``CORTEX_WORKSPACE`` always wins, so a served-mode
    tenant session stays pinned to its own folder even when calling code passes a different
    path (GAP-CORTEX-0015 -- deliberately NOT changed). This is the counterpart for the case
    where a caller EXPLICITLY passes a workspace and means it: the given path wins over the
    ambient ``CORTEX_WORKSPACE`` pin. An OMITTED path (``None``) still falls back to normal
    env-first resolution, so the pin holds whenever no override is supplied.

    SECURITY: this function alone does NOT decide whether an override is *allowed* -- honoring
    an explicit override for a served-mode tenant would let it escape its pin. That policy
    decision belongs to the caller (see ``cortex_core.mcp._write_ws``), which only routes here
    when the session is not tenant-pinned. Here we merely resolve the explicit path directly,
    ignoring ``CORTEX_WORKSPACE`` for that path (by passing an empty env)."""
    if path is None:
        return resolve_workspace(start_path=None, env=env)
    return resolve_workspace(start_path=path, env={})


def resolve_brain_workspace(start_path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """The READ/knowledge plane (GAP-CORTEX-0015 H2a dual-plane). When
    ``CORTEX_BRAIN_WORKSPACE`` is set, READ tools resolve to the admin's canonical brain
    HERE (while writes stay on the tenant's ``CORTEX_WORKSPACE``) -- "read my brain, write
    their folder." Unset => falls back to the normal workspace (single-plane owner mode),
    so this is fully backward-compatible. (Deployment target: the local harness points
    this at your REMOTE brain over HTTP -- transport is H1; the routing is here.)"""
    env_map = dict(os.environ if env is None else env)
    brain = env_map.get(BRAIN_WORKSPACE_ENV)
    if brain:
        workspace = Path(brain).expanduser().resolve()
        if workspace.exists():
            return workspace
        raise FileNotFoundError(f"{BRAIN_WORKSPACE_ENV} points to a missing path: {workspace}")
    return resolve_workspace(start_path=start_path, env=env)


def resolve_brain_workspace_override(
    path: str | Path | None, env: Mapping[str, str] | None = None
) -> Path:
    """READ/brain-plane counterpart to ``resolve_workspace_override``.

    ``resolve_brain_workspace`` is ENV-FIRST: ``CORTEX_BRAIN_WORKSPACE`` (else the env-first
    ``CORTEX_WORKSPACE``) always wins, so an explicit ``workspace=`` passed to a READ tool was
    silently overridden back to the pinned env value -- the read-plane twin of the write-plane
    bug ``resolve_workspace_override`` fixed. This is the counterpart for the case where a caller
    EXPLICITLY passes a workspace and means it: the given path wins over BOTH the brain env and
    the ``CORTEX_WORKSPACE`` pin (via ``resolve_exact_workspace``, which resolves a real workspace
    dir directly and ignores the ambient env). An OMITTED path (``None``) still falls back to
    normal env-first brain resolution, so the dual-plane brain routing holds whenever no override
    is supplied.

    SECURITY: this function alone does NOT decide whether an override is *allowed* -- honoring an
    explicit override for a tenant-pinned served session would let it escape the brain pin and read
    a foreign workspace (GAP-CORTEX-0015). That policy decision belongs to the caller (see
    ``cortex_core.mcp._read_ws``), which only routes here when the session is NOT tenant-pinned."""
    if path is None:
        return resolve_brain_workspace(start_path=None, env=env)
    return resolve_exact_workspace(path)


def load_workspace_config(start_path: str | Path | None = None, env: Mapping[str, str] | None = None) -> CortexConfig:
    try:
        repo_root = find_repo_root(start_path)
    except FileNotFoundError:
        repo_root = Path(start_path or Path.cwd()).resolve()
    workspace_root = resolve_workspace(start_path=start_path, env=env)
    return CortexConfig(repo_root=repo_root, workspace_root=workspace_root, raw=load_cortex_json(repo_root))
