from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_core.config import find_repo_root, resolve_workspace


def _make_repo(tmp_path: Path, fallback: str = "") -> Path:
    repo = tmp_path / "repo"
    (repo / "library" / "cortex-library").mkdir(parents=True)
    config = {
        "paths": {
            "workspace_fallback": fallback,
        }
    }
    (repo / "cortex.json").write_text(json.dumps(config), encoding="utf-8")
    return repo


def test_env_override_wins(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolved = resolve_workspace(start_path=repo, env={"CORTEX_WORKSPACE": str(workspace)})
    assert resolved == workspace.resolve()


def test_workspace_fallback_is_used_when_env_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _make_repo(tmp_path, fallback=str(workspace))
    resolved = resolve_workspace(start_path=repo, env={})
    assert resolved == workspace.resolve()


def test_find_repo_root_detects_checkout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    nested = repo / "subdir" / "deeper"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == repo.resolve()


def test_missing_workspace_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_workspace(start_path=tmp_path / "outside", env={})
