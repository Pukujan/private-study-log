from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from .config import make_stdio_encoding_safe, resolve_workspace

# GAP-CORTEX-0014: a non-trivial pile of untracked closeouts means the audit
# trail isn't reaching git at all, not just "a few in flight" -- flag loudly.
_AUDIT_UNTRACKED_FLAG_THRESHOLD = 5
_SAMPLE_CAP = 10


def git_hygiene(ws: Path) -> dict[str, Any]:
    """Read-only git-tree hygiene report (GAP-CORTEX-0014). Never mutates git
    state -- detect-and-report only, per the gap's explicit scope decision.
    `git status --porcelain` already excludes anything `.gitignore` matches,
    so every path this reports is real signal, not scratch noise re-derived
    here -- no separate pattern list to keep in sync with `.gitignore`."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(ws), capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"is_git_repo": False}
    if result.returncode != 0:
        return {"is_git_repo": False}

    untracked: list[str] = []
    modified: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        code, path = line[:2], line[3:]
        (untracked if code.strip() == "??" else modified).append(path)

    # Default porcelain collapses a wholly-untracked directory into one line
    # ("?? audit/"), which would undercount how many closeouts are actually
    # off-git. Re-query with --untracked-files=all scoped ONLY to audit/ --
    # cheap because it's pathspec-bounded, not a repo-wide -uall walk (which
    # is the thing to avoid on a large tree).
    audit_untracked_count = 0
    if any(p == "audit/" or p.startswith("audit/") for p in untracked):
        try:
            audit_result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all", "--", "audit"],
                cwd=str(ws), capture_output=True, text=True, timeout=10, check=False,
            )
            audit_untracked_count = sum(
                1 for line in audit_result.stdout.splitlines()
                if line[:2].strip() == "??"
            )
        except (OSError, subprocess.SubprocessError):
            audit_untracked_count = sum(1 for p in untracked if p.startswith("audit/"))
    return {
        "is_git_repo": True,
        "untracked_count": len(untracked),
        "modified_count": len(modified),
        "untracked_sample": untracked[:_SAMPLE_CAP],
        "modified_sample": modified[:_SAMPLE_CAP],
        "audit_untracked_count": audit_untracked_count,
        "audit_untracked_flag": audit_untracked_count >= _AUDIT_UNTRACKED_FLAG_THRESHOLD,
        "clean": not untracked and not modified,
    }


def retrieval_health() -> dict[str, Any]:
    """Content-agnostic guard for the silent retrieval-regression class: assert the
    query builder produces FTS5-safe MATCH strings for version-token queries (the
    2026-07-05 `GLM-5.2` -> `fts5: syntax error near "."` -> 0-hits bug). Runs in any
    workspace because it tests the query normalizer, not corpus content."""
    from .retrieval_health import CHECKER_VERSION, fts5_safe
    probes = ["GLM-5.2 provenance", "gpt-5.2 codex tool", "sqlite-vec vs txtai", "model v1.0 test"]
    unsafe = [p for p in probes if not fts5_safe(p)]
    return {"checker": CHECKER_VERSION, "probes": len(probes),
            "unsafe": unsafe, "ok": not unsafe}


def doctor(
    workspace: str | Path | None = None,
    json_output: bool = False,
    *,
    include_git_hygiene: bool = True,
) -> dict[str, Any]:
    """Return workspace diagnostics.

    ``include_git_hygiene=False`` is the latency-bounded path used by the MCP
    status tool.  Git is an external process and, on Windows stdio servers, a
    timed-out ``git status`` can leave an inherited pipe open after the child
    is killed.  Python then waits indefinitely for its pipe reader threads.
    The full CLI doctor keeps the check enabled; callers that need a health
    response without subprocess risk get an explicit skipped receipt instead.
    """
    ws = resolve_workspace(workspace)
    index_db = ws / "library" / "cortex-library" / "search" / "cortex-index.sqlite"
    plugin_file = ws / "plugin.yaml"
    report = {
        "workspace": str(ws),
        "exists": ws.exists(),
        "index": index_db.exists(),
        "plugin_manifest": plugin_file.exists(),
        "docs": {
            "cortex_1": (ws / "docs" / "cortex-1").exists(),
            "cortex_2": (ws / "docs" / "cortex-2").exists(),
            "research": (ws / "docs" / "research").exists(),
        },
        "git_hygiene": (
            git_hygiene(ws)
            if include_git_hygiene
            else {
                "skipped": True,
                "reason": "excluded from latency-bounded MCP status",
                "how_to_run": "cortex-doctor --json",
            }
        ),
        "retrieval_health": retrieval_health(),
    }
    # Project state is an optional capability. Preserve the historic doctor
    # shape for workspaces that have never initialized it; once artifacts
    # exist, however, only a replay-anchored clean state may report PASS.
    if (ws / "project-state").exists():
        from .project_state_cli import project_state_diagnostic

        report["project_state"] = project_state_diagnostic(ws)
    if json_output:
        return report

    print(f"workspace: {report['workspace']}")
    print(f"index: {'ok' if report['index'] else 'missing'}")
    rh = report["retrieval_health"]
    print(f"retrieval: {'ok (version-token queries FTS5-safe)' if rh['ok'] else 'BROKEN — unsafe queries: ' + str(rh['unsafe'])}")
    print(f"plugin.yaml: {'ok' if report['plugin_manifest'] else 'missing'}")
    print(f"docs/cortex-1: {'ok' if report['docs']['cortex_1'] else 'missing'}")
    print(f"docs/cortex-2: {'ok' if report['docs']['cortex_2'] else 'missing'}")
    print(f"docs/research: {'ok' if report['docs']['research'] else 'missing'}")
    if "project_state" in report:
        ps = report["project_state"]
        print(
            f"project-state: {ps['status']} [{ps['level']}] -- {ps['reason']} "
            "(replay-anchored; does not mint assurance)"
        )
        for reason in ps.get("reasons") or []:
            print(f"  - {reason}")
        for notice in ps.get("notices") or []:
            print(f"  NOTICE: {notice}")
    gh = report["git_hygiene"]
    if not gh.get("is_git_repo"):
        print("git: not a repo (or git unavailable)")
    elif gh["clean"]:
        print("git: clean (nothing untracked or modified)")
    else:
        print(f"git: {gh['untracked_count']} untracked, {gh['modified_count']} modified"
              " -- detected, not modified; review and stage/ignore intentionally")
        if gh["audit_untracked_flag"]:
            print(f"  WARNING: {gh['audit_untracked_count']} untracked files under audit/ --"
                  " the closeout trail may not be reaching git")
        for p in gh["untracked_sample"]:
            print(f"  ?? {p}")
        if gh["untracked_count"] > len(gh["untracked_sample"]):
            print(f"  ... and {gh['untracked_count'] - len(gh['untracked_sample'])} more untracked")
    return report


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex workspace diagnostics")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--models", action="store_true",
                        help="also probe which configured model providers are reachable "
                             "(free-only; delegates to cortex-models / model_probe)")
    args = parser.parse_args(argv)
    result = doctor(json_output=args.json)

    if args.models:
        # Delegate to the portable probe. Free-only by construction (see model_probe).
        from . import model_probe
        probe_results = model_probe.probe_fleet()
        result["model_availability"] = model_probe._availability_doc(probe_results)
        if not args.json:
            print()
            model_probe._print_table(probe_results)

    if args.json:
        print(json.dumps(result, indent=2))
    project_state_ok = result.get("project_state", {}).get("ok", True)
    return 0 if result["exists"] and project_state_ok else 1
