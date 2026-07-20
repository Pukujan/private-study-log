from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cortex_core.doctor import doctor, git_hygiene


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "plugin.yaml").write_text("name: cortex\n", encoding="utf-8")
    (workspace / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return workspace


def test_doctor_reports_workspace_health(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    report = doctor(workspace=workspace, json_output=True)

    assert report["workspace"] == str(workspace.resolve())
    assert report["exists"] is True
    assert report["plugin_manifest"] is True
    assert report["index"] is False


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), check=True)


def test_git_hygiene_not_a_repo(tmp_path: Path) -> None:
    # GAP-CORTEX-0014: a plain directory (no .git) must report is_git_repo=False,
    # not raise or misreport a false-clean state.
    report = git_hygiene(tmp_path)
    assert report == {"is_git_repo": False}


def test_git_hygiene_clean_repo_is_clean(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "committed.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

    report = git_hygiene(tmp_path)
    assert report["is_git_repo"] is True
    assert report["clean"] is True
    assert report["untracked_count"] == 0
    assert report["modified_count"] == 0


def test_git_hygiene_flags_untracked_and_modified(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "committed.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

    (tmp_path / "committed.txt").write_text("changed\n", encoding="utf-8")
    (tmp_path / "new_untracked.txt").write_text("scratch\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored_*.txt\n", encoding="utf-8")
    (tmp_path / "ignored_scratch.txt").write_text("skip me\n", encoding="utf-8")

    report = git_hygiene(tmp_path)
    assert report["clean"] is False
    assert report["modified_count"] == 1
    assert "new_untracked.txt" in report["untracked_sample"]
    # a gitignored file must never appear -- git status --porcelain already
    # excludes it, so this proves the report doesn't re-derive its own
    # (potentially out-of-sync) scratch-pattern list
    assert "ignored_scratch.txt" not in report["untracked_sample"]
    assert not report["audit_untracked_flag"]


def test_git_hygiene_flags_audit_untracked_pileup(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "committed.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

    audit_dir = tmp_path / "audit" / "audit-log-1" / "agent"
    audit_dir.mkdir(parents=True)
    for i in range(6):
        (audit_dir / f"closeout-{i}.md").write_text("x\n", encoding="utf-8")

    report = git_hygiene(tmp_path)
    assert report["audit_untracked_count"] == 6
    assert report["audit_untracked_flag"] is True
