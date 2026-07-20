"""Stamp Cortex's own git state onto anything that needs to be diff-able
before/after a self-improvement fix lands.

Direct requirement (2026-07-07): before/after comparisons of the same
benchmark task run under two different Cortex code states are meaningless
unless each run is tagged with exactly which Cortex commit produced it --
otherwise a "did the fix help" comparison silently mixes pre-fix and
post-fix output with no way to tell them apart after the fact. This module
is the single source of that tag: current commit hash (short), whether the
tree was dirty (uncommitted changes -- a "before/after" claim across a
dirty tree is not trustworthy), and the commit's timestamp.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def cortex_version() -> dict[str, str | bool | None]:
    """Best-effort git identity of the running Cortex codebase. Never raises --
    a version tag that fails closed to 'unknown' is far better than a closeout
    that crashes because git isn't on PATH in some environment."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        commit_hash = commit.stdout.strip() if commit.returncode == 0 else None

        dirty_check = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        dirty = bool(dirty_check.stdout.strip()) if dirty_check.returncode == 0 else None

        commit_ts = subprocess.run(
            ["git", "show", "-s", "--format=%cI", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        commit_timestamp = commit_ts.stdout.strip() if commit_ts.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None, "commit_timestamp": None, "note": "git unavailable"}

    return {"commit": commit_hash, "dirty": dirty, "commit_timestamp": commit_timestamp}
