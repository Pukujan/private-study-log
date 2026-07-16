"""Bootstrap the file/folder governance contract into any Cortex workspace.

Every workspace this session touches should have an AGENTS.md any agent
reads before writing new output, plus a MANIFEST.md/manifest.json pair
that tracks what's pinned and what's safe to move. Without this, agents
(seen live: Hermes running qwen/deepseek) default to dumping scratch
output straight into the workspace root, because nothing tells them not
to. This was previously fixed by hand in one workspace (cortex-local);
this module makes that fix apply to every workspace cortex_register sees,
not just the one someone happened to notice was messy.

Idempotent: only writes files that don't already exist. Never overwrites
a workspace's own governance docs -- if AGENTS.md/MANIFEST.md are already
there, leave them alone, since they may have been hand-tuned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_AGENTS_MD_TEMPLATE = """# Before you write anything to this workspace

Read `MANIFEST.md` first. It tells you what's already here and where new
output belongs.

**The one rule that matters: never write new files to this root directory.**
Screenshots, scrape dumps, reports, scratch scripts -- none of it goes in
the workspace root directly. It goes under `work/`, in a dated subfolder:

```
work/
  benchmarks/<YYYY-MM-DD>-<slug>/     # one session = one dated dir
      runs/  consolidated/  dashboards/<arm>/
  research/<YYYY-MM-DD>-<topic-slug>/
  audits/<YYYY-MM-DD>-<slug>/
  status/<component>/
```

Lowercase-hyphen slugs, dated prefixes. If you're not sure which bucket
fits, `audits/` is the safe default for one-off scrapes/reports.

Everything else -- what's pinned and why, what's live and can't be moved,
what's already been cleaned up -- is in `MANIFEST.md` / `manifest.json`.
"""

_MANIFEST_MD_TEMPLATE = """# {workspace_name} workspace manifest

Machine source of truth: `manifest.json` (this file is the human-readable
view of it -- if they disagree, `manifest.json` wins).

Any agent working in this workspace should read `AGENTS.md` first -- it's
the short, always-read-this pointer to the naming contract below.

## Naming contract for anything new

All new output goes under `work/`, never the root:

```
work/
  benchmarks/<YYYY-MM-DD>-<slug>/
  research/<YYYY-MM-DD>-<topic-slug>/
  audits/<YYYY-MM-DD>-<slug>/
  status/<component>/
```

Lowercase-hyphen slugs, dated prefixes.

## Root

Nothing has been catalogued here yet -- this manifest was auto-generated
as a starting skeleton. Update `manifest.json` as workspace-runtime dirs
(`audit/`, `contracts/`, `logs/`, etc.) and session output accumulate.
"""

_MANIFEST_JSON_SKELETON: dict[str, Any] = {
    "schema_version": 1,
    "entries": [],
    "deferred_moves": [],
    "note": "Auto-generated skeleton -- populate as the workspace accumulates output.",
}


def ensure_workspace_scaffold(workspace: str | Path) -> dict[str, Any]:
    """Write AGENTS.md / MANIFEST.md / manifest.json into `workspace` if any
    are missing. Returns which files were created (empty list if the
    workspace already had its own governance docs -- never overwritten)."""
    ws = Path(workspace)
    if not ws.is_dir():
        return {"created": [], "skipped_reason": "workspace path does not exist"}

    created: list[str] = []

    agents_md = ws / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text(_AGENTS_MD_TEMPLATE, encoding="utf-8")
        created.append("AGENTS.md")

    manifest_md = ws / "MANIFEST.md"
    if not manifest_md.exists():
        manifest_md.write_text(
            _MANIFEST_MD_TEMPLATE.format(workspace_name=ws.name), encoding="utf-8"
        )
        created.append("MANIFEST.md")

    manifest_json = ws / "manifest.json"
    if not manifest_json.exists():
        manifest_json.write_text(
            json.dumps(
                {"workspace": str(ws), **_MANIFEST_JSON_SKELETON}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        created.append("manifest.json")

    return {"created": created}
