"""Tests for `cortex update` (H1) — installed-brain version/staleness reporter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import update  # noqa: E402
from cortex_core.__main__ import main as cli_main  # noqa: E402


def test_report_returns_commit_provenance():
    info = update.report(check=False)
    # keys always present; values may be None if git is unavailable (fail-open).
    assert set(info) >= {"commit", "dirty", "commit_timestamp"}
    assert "behind" not in info  # no network probe without check


def test_report_is_offline_safe(monkeypatch):
    # Force every git call to fail -> must not raise, must degrade to None.
    monkeypatch.setattr(update, "_git", lambda *a: None)
    monkeypatch.setattr(update, "cortex_version",
                        lambda: {"commit": None, "dirty": None, "commit_timestamp": None})
    info = update.report(check=True)
    assert info["commit"] is None and "upstream" not in info


def test_json_cli_emits_valid_json(capsys):
    rc = update.main(["--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "commit" in out


def test_cortex_update_subcommand_is_wired(capsys):
    rc = cli_main(["update", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "commit" in out
