"""GAP-CORTEX-0013: fingerprint detects a changed file (the stale-read guard)."""

from __future__ import annotations

from pathlib import Path

from cortex_core.fingerprint import changed_since, fingerprint


def test_fingerprint_of_a_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    fp = fingerprint(f)
    assert fp["exists"] and fp["is_file"]
    assert fp["size"] == 5
    assert len(fp["sha256"]) == 64


def test_missing_path(tmp_path: Path):
    fp = fingerprint(tmp_path / "nope.txt")
    assert fp["exists"] is False


def test_changed_since_detects_edit(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("v1", encoding="utf-8")
    prior = fingerprint(f)              # what the agent read
    assert changed_since(f, prior) is False
    f.write_text("v2 edited by another process", encoding="utf-8")
    assert changed_since(f, prior) is True   # the stale-read guard fires


def test_changed_since_detects_deletion(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    prior = fingerprint(f)
    f.unlink()
    assert changed_since(f, prior) is True
