"""GAP-CORTEX-0013: file fingerprint for stale-context detection.

Surfaced live 2026-07-05: an orchestrator (Hermes) acted on a STALE read of a file another
process had edited -- the exact failure this whole project exists to prevent, but for a file
on disk instead of a doc in the corpus. A cheap fingerprint (size, mtime, sha256) lets a
caller verify the file it read is still the file on disk BEFORE it acts on that read: capture
`fingerprint(path)` when you read, compare before you write.

Pure and read-only: hashes bytes, touches nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 65536


def fingerprint(path: str | Path) -> dict[str, Any]:
    """`{path, exists, is_file, size, mtime, sha256}` for a file. For a missing path,
    `{path, exists: False}`. For a directory, `sha256` is None (size/mtime still reported)."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    out: dict[str, Any] = {
        "path": str(p),
        "exists": True,
        "is_file": p.is_file(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "sha256": None,
    }
    if p.is_file():
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                h.update(chunk)
        out["sha256"] = h.hexdigest()
    return out


def changed_since(path: str | Path, prior: dict[str, Any]) -> bool:
    """True if `path` differs from a `prior` fingerprint (content changed, or it appeared/
    vanished). sha256 is authoritative when both are files; existence flips also count."""
    now = fingerprint(path)
    if now.get("exists") != prior.get("exists"):
        return True
    if now.get("is_file") and prior.get("sha256") is not None:
        return now.get("sha256") != prior.get("sha256")
    # dir or no prior hash: fall back to size+mtime
    return (now.get("size"), now.get("mtime")) != (prior.get("size"), prior.get("mtime"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fingerprint a file (size, mtime, sha256) for "
                                             "stale-context detection.")
    ap.add_argument("path")
    args = ap.parse_args(argv)
    print(json.dumps(fingerprint(args.path), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
