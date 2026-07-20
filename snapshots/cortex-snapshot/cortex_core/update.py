"""`cortex update` — version + staleness reporter for the installed brain (H1).

The scaffold (`.cortex/scripts/update.py`) closes H1 for the zero-install drop-in
folder. This is the sibling for the pip-installed brain: when Cortex is run from a
git checkout (`pip install -e .`), the same "am I stale?" question applies. This
reports the running commit (via `cortex_core.version`), whether the tree is dirty,
and — best-effort, no hard dependency — how many commits behind its tracked
upstream it is, plus the `git pull` path. Nothing here mutates the tree.

Stdlib only. Offline-safe (git/network bits degrade to "commit only").

CLI:  cortex update            # human line
      cortex update --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .version import cortex_version

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def report(check: bool = False) -> dict[str, Any]:
    """Installed-brain provenance. With check=True, fetches upstream to count how
    far behind HEAD is (network); without it, purely local."""
    ver = cortex_version()
    info: dict[str, Any] = {
        "commit": ver.get("commit"),
        "dirty": ver.get("dirty"),
        "commit_timestamp": ver.get("commit_timestamp"),
    }
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        info["upstream"] = upstream
    if check and upstream:
        _git("fetch", "--quiet")
        behind = _git("rev-list", "--count", "HEAD..@{u}")
        if behind is not None and behind.isdigit():
            info["behind"] = int(behind)
    return info


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    ap = argparse.ArgumentParser(
        prog="cortex update",
        description="Report the installed Cortex version/commit and whether it is "
                    "behind upstream (never auto-mutates; prints the git pull path).",
    )
    ap.add_argument("--check", action="store_true",
                    help="fetch upstream and report how many commits behind HEAD is")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    info = report(check=args.check)
    if args.json:
        print(json.dumps(info))
        return 0

    commit = info.get("commit") or "unknown"
    line = f"cortex (brain) commit {commit}"
    if info.get("dirty"):
        line += " (dirty tree)"
    print(line)
    behind = info.get("behind")
    if behind:
        print(f"  {behind} commit(s) behind {info.get('upstream')} — "
              f"review then update with: git pull", file=sys.stderr)
    elif args.check and "upstream" in info:
        print("  up to date with upstream.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
