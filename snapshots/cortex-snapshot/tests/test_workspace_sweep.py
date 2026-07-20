"""Guards the false-positive class hit live 2026-07-07: an early version of
this sweep moved a real governing doc and a real load-bearing script purely
because manifest.json didn't list them by exact path. The fix is structural
(allowlist-of-scratch-extensions, not denylist-of-catalogued) -- these tests
pin that behavior so it can't regress back to the unsafe version."""

from __future__ import annotations

import json

from cortex_core.workspace_sweep import sweep_workspace_root


def _init_workspace(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    return tmp_path


def test_images_are_auto_moved(tmp_path):
    ws = _init_workspace(tmp_path)
    (ws / "scroll_001.png").write_bytes(b"fake")
    (ws / "current_state.jpg").write_bytes(b"fake")

    result = sweep_workspace_root(ws)

    assert set(result["moved"]) == {"scroll_001.png", "current_state.jpg"}
    assert not (ws / "scroll_001.png").exists()
    assert result["dest"] is not None


def test_uncatalogued_markdown_is_flagged_not_moved(tmp_path):
    """The exact regression: a real .md governance doc must never be auto-moved."""
    ws = _init_workspace(tmp_path)
    (ws / "FILE-MANAGEMENT-PROPOSAL-2026-07-07.md").write_text("real doc", encoding="utf-8")

    result = sweep_workspace_root(ws)

    assert "FILE-MANAGEMENT-PROPOSAL-2026-07-07.md" in result["flagged"]
    assert result["moved"] == []
    assert (ws / "FILE-MANAGEMENT-PROPOSAL-2026-07-07.md").exists()
    assert (ws / "FILE-MANAGEMENT-PROPOSAL-2026-07-07.md").read_text(encoding="utf-8") == "real doc"


def test_uncatalogued_python_script_is_flagged_not_moved(tmp_path):
    """The other exact regression: a real load-bearing .py script must never be auto-moved."""
    ws = _init_workspace(tmp_path)
    (ws / "run_deep_research.py").write_text("print('real script')", encoding="utf-8")

    result = sweep_workspace_root(ws)

    assert "run_deep_research.py" in result["flagged"]
    assert result["moved"] == []
    assert (ws / "run_deep_research.py").exists()


def test_catalogued_entries_are_never_touched(tmp_path):
    ws = tmp_path
    (ws / "manifest.json").write_text(
        json.dumps({"entries": [{"path": "research/"}]}), encoding="utf-8"
    )
    (ws / "research").mkdir()
    (ws / "research" / "sources.yaml").write_text("x: 1", encoding="utf-8")

    result = sweep_workspace_root(ws)

    assert result["moved"] == []
    assert result["flagged"] == []
    assert (ws / "research" / "sources.yaml").exists()


def test_always_safe_names_are_never_touched(tmp_path):
    ws = _init_workspace(tmp_path)
    (ws / "AGENTS.md").write_text("x", encoding="utf-8")
    (ws / "README.md").write_text("x", encoding="utf-8")
    (ws / ".env").write_text("SECRET=x", encoding="utf-8")

    result = sweep_workspace_root(ws)

    assert result["moved"] == []
    assert result["flagged"] == []


def test_no_manifest_is_a_clean_noop(tmp_path):
    result = sweep_workspace_root(tmp_path)
    assert result["moved"] == []
    assert result["flagged"] == []
    assert "skipped_reason" in result


def test_idempotent_on_a_clean_workspace(tmp_path):
    ws = _init_workspace(tmp_path)
    (ws / "shot.png").write_bytes(b"fake")

    first = sweep_workspace_root(ws)
    second = sweep_workspace_root(ws)

    assert first["moved"] == ["shot.png"]
    assert second["moved"] == []
    assert second["flagged"] == []
