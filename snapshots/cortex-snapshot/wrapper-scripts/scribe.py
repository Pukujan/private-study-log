#!/usr/bin/env python3
"""Scribe — transcript -> atomic per-project closeout. Replaces the ceremony.

The agent never hand-writes a closeout. After the work, this script reads the
transcript + receipts + git + the scorer's SLI and writes ONE atomic closeout
pair (`<stamp>-<slug>__<run_id>.md` + `.json`) into the project's own audit dir,
`projects/<slug>/audit/closeouts/`. Records are PROJECT-SCOPED — never a global
flat dump (the 848-file flat audit dir is the anti-pattern this fixes).

"Atomic" = written to a temp file in the same dir then os.replace'd, so a reader
never sees a half-written record (the state/filesystem-consistency fix from the
redesign, kept as the mechanism).

No LLM. The `result` prose is assembled deterministically from what the
transcript already contains (mutations, test outcomes, an optional summary
event). The frontmatter schema mirrors the repo's audit/audit-log-*/agent
closeouts so a generated record is indistinguishable in shape from a hand-written
one — only richer, because it has the whole transcript.

Stdlib only. Offline. No install.

CLI:
    python scribe.py --transcript run.jsonl --project ../projects/my-proj \
        [--repo .] [--base <ref>] [--status completed]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import scorer as _scorer  # noqa: E402  (sibling stdlib module)


def _slug(text: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "task").lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "task"


def _project_slug(project_root: Path) -> str:
    pyaml = project_root / "PROJECT.yaml"
    if pyaml.exists():
        try:
            data = _scorer.parse_simple_yaml(pyaml.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("slug"):
                return str(data["slug"])
        except Exception:
            pass
    return project_root.name


def build_result(events: list[dict[str, Any]], sli: dict[str, Any]) -> str:
    """Deterministic result prose from transcript events + the SLI. No model."""
    muts = [ev for ev in events if ev.get("type") == "mutation"]
    tests = [ev for ev in events if ev.get("type") == "test_run"]
    summary = next((ev.get("text") for ev in reversed(events)
                    if ev.get("type") == "summary" and ev.get("text")), None)

    parts: list[str] = []
    if summary:
        parts.append(str(summary))
    if muts:
        paths = sorted({str(ev.get("path")) for ev in muts if ev.get("path")})
        parts.append(f"Changed {len(paths)} file(s): " + ", ".join(paths) + ".")
    else:
        parts.append("No file mutations recorded.")
    if tests:
        outcomes = ", ".join(f"{ev.get('cmd')} -> exit {ev.get('exit')}" for ev in tests)
        parts.append(f"Verify: {outcomes}.")
    rf = sli.get("research_first", {})
    parts.append("Research-first honored." if rf.get("ok")
                 else "Research-first NOT honored (" + str(rf.get("detail")) + ").")
    if sli.get("skip_detected"):
        parts.append("Skip(s) detected: " + "; ".join(sli.get("reasons", [])) + ".")
    return " ".join(parts)


def build_tests_passed(events: list[dict[str, Any]]) -> bool | None:
    """Bool from real test events: all exit 0 -> True, any nonzero -> False, none -> None.
    Derived from the transcript, never self-reported (anti-evidence-theater)."""
    tests = [ev for ev in events if ev.get("type") == "test_run"]
    if not tests:
        return None
    return all(int(ev.get("exit", 1)) == 0 for ev in tests)


def _sha256_file(path: str | Path) -> str:
    """sha256 of the transcript bytes -- binds the closeout to THIS run (anti-forgery)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_tests_field(events: list[dict[str, Any]]) -> str:
    tests = [ev for ev in events if ev.get("type") == "test_run"]
    if not tests:
        return ""
    return "; ".join(f"{ev.get('cmd')} -> exit {ev.get('exit')}" for ev in tests)


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return '""'
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def render_closeout(record: dict[str, Any]) -> str:
    """The .md: YAML frontmatter (repo schema) + a human body."""
    fm_keys = ["schema_version", "status", "task", "result", "tests", "tests_passed", "run_id", "event_digest",
               "project", "task_type", "skip_detected", "timestamp"]
    lines = ["---"]
    for k in fm_keys:
        lines.append(f"{k}: {_yaml_scalar(record.get(k))}")
    lines.append("sli: " + json.dumps(record.get("sli", {}), sort_keys=True))
    lines.append("---")
    lines.append(f"# {record.get('task')}")
    lines.append("")
    lines.append(str(record.get("result")))
    lines.append("")
    lines.append("## Discipline SLI (post-hoc, deterministic — no judge)")
    sli = record.get("sli", {})
    lines.append(f"- research_first: {sli.get('research_first', {}).get('ok')}")
    lines.append(f"- docs_current: {sli.get('docs_current', {}).get('ok')}"
                 f" (missing: {sli.get('docs_current', {}).get('missing')})")
    lines.append(f"- verify_evidence: {sli.get('verify_evidence', {}).get('ok')}")
    lines.append(f"- coercion signature: {sli.get('coercion', {})}")
    lines.append(f"- skip_detected: {record.get('skip_detected')}")
    if record.get("skip_detected"):
        lines.append("")
        lines.append("### Reasons")
        for r in sli.get("reasons", []):
            lines.append(f"- {r}")
    lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    """Write to a temp file in the same dir, fsync, then os.replace (atomic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_closeout(
    transcript: str | Path,
    project: str | Path,
    *,
    repo: str | None = None,
    base: str | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Score the run, assemble the closeout, and atomically write the .md + .json
    into projects/<slug>/audit/closeouts/. Returns the record + written paths."""
    project_root = Path(project)
    events = _scorer.load_jsonl(transcript)
    task_ev = next((ev for ev in events if ev.get("type") == "task"), {})
    run_id = task_ev.get("run_id") or ("run-" + str(int(time.time())))
    task = task_ev.get("task") or "Untitled task"
    task_type = task_ev.get("task_type", "implementation")

    sli = _scorer.score_run(transcript, project=project_root, repo=repo, base=base)

    now = datetime.now(timezone.utc)
    record: dict[str, Any] = {
        "schema_version": 3,
        "status": status,
        "task": task,
        "result": build_result(events, sli),
        "tests": build_tests_field(events),
        "tests_passed": build_tests_passed(events),
        "run_id": run_id,
        "project": _project_slug(project_root),
        "task_type": task_type,
        "skip_detected": sli.get("skip_detected", False),
        "timestamp": now.isoformat(),
        # Binds this closeout to the exact transcript bytes -- run-bound, non-forgeable,
        # and satisfies any consumer (e.g. the ab_cortex evaluator) that checks event_digest.
        "event_digest": _sha256_file(transcript),
        "sli": sli,
        "scribe_version": "1",
    }

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    base_name = f"cortex-closeout__{stamp}-{_slug(task)}__{run_id}"
    out_dir = project_root / "audit" / "closeouts"
    md_path = out_dir / (base_name + ".md")
    json_path = out_dir / (base_name + ".json")

    _atomic_write(json_path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    _atomic_write(md_path, render_closeout(record))

    return {"record": record, "md_path": str(md_path), "json_path": str(json_path)}


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Transcript -> atomic per-project closeout.")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--project", required=True, help="projects/<slug>/ directory")
    ap.add_argument("--repo")
    ap.add_argument("--base")
    ap.add_argument("--status", default="completed", choices=["completed", "abandoned"])
    args = ap.parse_args(argv)

    result = write_closeout(
        args.transcript, args.project, repo=args.repo, base=args.base, status=args.status,
    )
    print(f"wrote {result['md_path']}")
    print(f"wrote {result['json_path']}")
    # Exit 0 always — the scribe records, it never blocks.
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
