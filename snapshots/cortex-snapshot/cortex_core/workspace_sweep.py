"""Mechanically enforce the file/folder governance contract, not just document it.

`workspace_scaffold.py` writes AGENTS.md/MANIFEST.md into a workspace so an
agent *can* learn the naming contract before it writes anything. That's
advisory only -- it depends on the writing agent's own harness choosing to
read it. Observed live, three times, same night: an agent (Hermes) wrote
files straight to a workspace root via its own raw file-write tool, which
never touches Cortex's MCP surface at all, so nothing in Cortex ever saw
those writes to redirect them -- AGENTS.md sat right next to them, unread.

This module is the backstop for exactly that case: it doesn't require the
writing agent's cooperation. Two tiers, deliberately asymmetric risk:

  AUTO-MOVE: only file types with essentially zero chance of being a
  load-bearing config/doc/script -- currently just images (screenshots,
  the single most common stray-file pattern observed). Relocated into
  work/audits/<date>-auto-swept/ without asking.

  FLAG ONLY: everything else uncatalogued (scripts, docs, data dumps).
  Reported, never moved. An earlier version of this module auto-moved
  ANYTHING uncatalogued and, same night, wrongly relocated a real
  governing doc (FILE-MANAGEMENT-PROPOSAL-2026-07-07.md) and a real
  load-bearing script (run_deep_research.py) purely because manifest.json
  didn't happen to list them by exact path -- manifest drift is real, so
  "not catalogued" is not sufficient evidence something is disposable.
  A false "moved your real file" is worse than a missed "didn't clean up
  your screenshot" -- this module is deliberately conservative about the
  one and aggressive about the other.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Never touched (moved OR flagged), regardless of manifest state.
_ALWAYS_SAFE = {
    "AGENTS.md", "MANIFEST.md", "manifest.json", "work",
    ".git", ".gitignore", ".env", ".env.example", "README.md",
    "cortex.json", "pyproject.toml", "package.json", "package-lock.json",
}

# High-confidence scratch: essentially never a load-bearing file at a
# workspace root. Extend this set only with extensions that share that
# property -- do NOT add .md/.py/.json/.txt here, those are exactly the
# extensions that broke this module once already.
_AUTO_MOVE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _catalogued_root_names(manifest: dict[str, Any]) -> set[str]:
    names = set()
    for entry in manifest.get("entries", []):
        path = entry.get("path", "")
        if not path:
            continue
        top = path.split("/", 1)[0].rstrip("/")
        if top:
            names.add(top)
    return names


def sweep_workspace_root(workspace: str | Path) -> dict[str, Any]:
    """Auto-move high-confidence scratch (images) sitting directly in
    `workspace`'s root into `work/audits/<today>-auto-swept/`, and FLAG
    (report, don't touch) anything else uncatalogued. Returns
    {"moved": [...], "flagged": [...], "skipped": [...]}.

    No-op if `workspace` has no manifest.json yet -- sweeping only makes
    sense once a workspace has opted into the contract (via
    `workspace_scaffold.ensure_workspace_scaffold`)."""
    ws = Path(workspace)
    manifest_path = ws / "manifest.json"
    if not ws.is_dir() or not manifest_path.exists():
        return {"moved": [], "flagged": [], "skipped": [], "skipped_reason": "no manifest.json -- not opted in"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"moved": [], "flagged": [], "skipped": [], "skipped_reason": "manifest.json unreadable, refusing to touch anything"}

    protected = _ALWAYS_SAFE | _catalogued_root_names(manifest)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sweep_dir = ws / "work" / "audits" / f"{today}-auto-swept"

    moved: list[str] = []
    flagged: list[str] = []
    skipped: list[str] = []
    for child in sorted(ws.iterdir()):
        if child.name in protected or child.name.startswith("."):
            continue
        if child.is_file() and child.suffix.lower() in _AUTO_MOVE_EXTENSIONS:
            sweep_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(child), str(sweep_dir / child.name))
                moved.append(child.name)
            except OSError:
                skipped.append(child.name)
        else:
            flagged.append(child.name)

    if moved:
        manifest.setdefault("entries", []).append({
            "path": f"work/audits/{today}-auto-swept/",
            "purpose": f"Auto-swept: {len(moved)} scratch file(s) written to workspace root outside the "
                       "naming contract, relocated by cortex_core.workspace_sweep (image files only).",
            "provenance": "session",
            "status": "archived",
            "auto_swept_items": moved,
        })
        manifest["updated"] = today
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return {"moved": moved, "flagged": flagged, "skipped": skipped,
            "dest": str(sweep_dir) if moved else None}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Sweep uncatalogued root-level scratch into work/, flag the rest")
    parser.add_argument("workspace")
    args = parser.parse_args()
    result = sweep_workspace_root(args.workspace)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
