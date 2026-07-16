from __future__ import annotations

import argparse
import os
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uuid6

from .config import (
    make_stdio_encoding_safe,
    resolve_brain_workspace,
    resolve_workspace,
    resolve_workspace_override,
)

MAX_AUDIT_FILES_PER_SHARD = 500

# A task closeout is an event, not project-wide authority. The generated marker lets
# later closeouts refresh a convenience view without replacing a deliberately authored
# project continuation document.
_GENERATED_HANDOFF_MARKER = "<!-- cortex:generated-closeout-handoff -->"
_LEGACY_GENERATED_HANDOFF_TEXT = "Auto-generated from the latest closeout:"
_LATEST_CLOSEOUT_NAME = "LATEST-CLOSEOUT.md"


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _uuid7() -> str:
    """RFC 9562 UUIDv7: time-sortable (48-bit ms timestamp in the high bits)
    and collision-resistant, generated locally with no directory read -- so
    appending it to a filename is unique-by-construction, never a
    read-before-write detect-and-suffix race (gate 0.15). Uses the ``uuid6``
    package (pure Python, zero transitive dependencies) rather than hand-rolled
    bit manipulation for a spec'd binary format -- ``uuid.uuid7()`` isn't
    available before Python 3.14, and this is exactly the kind of well-specified
    algorithm a small, correct library beats hand-rolling on."""
    return str(uuid6.uuid7())


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` via a same-directory temp file + ``os.replace``,
    so a crash mid-write can never leave a torn/partial file (gate 0.15)."""
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{os.urandom(4).hex()}")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


_SLUG_MAX_LEN = 80


def _slugify(text: str) -> str:
    cleaned = [ch.lower() if ch.isalnum() else "-" for ch in text]
    slug = "".join(cleaned)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-") or "closeout"
    # Windows' ~260-char path limit is real (repro'd 2026-07-04: an
    # unbounded task-string slug alone blew past it) -- Linux's much looser
    # per-component limit let this go unnoticed until tested on Windows.
    return slug[:_SLUG_MAX_LEN].rstrip("-") or "closeout"


def _audit_shard_number(shard_dir: Path) -> int:
    match = re.search(r"audit-log-(\d+)$", shard_dir.name)
    return int(match.group(1)) if match else 0


def choose_audit_dir(workspace: Path, *, max_files: int = MAX_AUDIT_FILES_PER_SHARD) -> Path:
    audit_root = workspace / "audit"
    candidates = sorted(
        (p for p in audit_root.glob("audit-log-*/agent") if p.is_dir()),
        key=lambda p: _audit_shard_number(p.parent),
    )
    if not candidates:
        fallback = audit_root / "audit-log-1" / "agent"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    for candidate in candidates:
        if sum(1 for _ in candidate.glob("*.md")) < max_files:
            return candidate
    next_number = _audit_shard_number(candidates[-1].parent) + 1
    next_shard = audit_root / f"audit-log-{next_number}" / "agent"
    next_shard.mkdir(parents=True, exist_ok=True)
    return next_shard


# Closeout schema version. v2 (gate 4.3) adds `contract_id` (the approach
# contract this work was done under) and structured, machine-oriented
# `evidence`. v3 (2026-07-07, user standing rule) adds `handoff` -- every
# closeout must state WHERE the artifacts are (`locations`) and HOW to carry the
# work forward (`continuation`). v4 (2026-07-14, gap J4) requires every
# *checkable* evidence item to link to a MECHANICALLY RECORDED artifact -- an
# exit code, a file sha256, a git diff ref, an oracle output id, or a trace span
# id -- so the closeout is an INDEX of the trace, never narration that outruns
# it; the evaluator abstains (`UNVERIFIABLE`) on a v4 checkable claim whose only
# backing is prose (`evaluator.unmechanized_evidence` /
# `evaluator.EVIDENCE_REF_SCHEMA_VERSION`). v1-v3 entries (missing the newer
# keys / mechanical refs) are read at their OWN version and stay both searchable
# and valid -- the requirement applies forward, never retroactively.
CLOSEOUT_SCHEMA_VERSION = 4


def _file_ref_resolves(root: Path, file_part: str) -> bool:
    root = root.resolve()
    target = (root / file_part).resolve()
    return target.is_relative_to(root) and target.is_file()


def validate_evidence(evidence: list[dict[str, Any]], workspace: str | Path | None = None) -> list[str]:
    """Gate 4.3: closeout evidence should resolve to real spans/exit codes.
    Returns the refs that DON'T resolve (a `file` item whose `path` -- optionally
    `path:line` -- isn't a real file under the workspace). Non-file evidence
    (`test`/`command`/`eval` exit codes/ids) is taken at face value here; the
    Phase-4.4 evaluator is what grades claim-vs-evidence relevance.

    Dual-plane (GAP-CORTEX-0015 H2a) fix, 2026-07-07: a closeout can legitimately
    cite a doc that a READ tool (`cortex_search`/`cortex_fetch_doc`) resolved on the
    BRAIN plane (`CORTEX_BRAIN_WORKSPACE`), even though the closeout itself is being
    WRITTEN to the tenant's `workspace` plane -- "read my brain, write their folder."
    Checking only the write-plane root produced a false `evidence_warning` for every
    such (real) reference. So a ref is only flagged as unresolvable if it resolves
    under NEITHER the write-plane root NOR the brain-plane root -- a ref that's
    genuinely missing from both still warns, this only fixes the false-negative
    where the file is real but lives on the other plane."""
    ws = resolve_workspace_override(workspace)
    try:
        brain_ws = resolve_brain_workspace()
    except FileNotFoundError:
        brain_ws = None
    bad: list[str] = []
    for item in evidence or []:
        if item.get("type") == "file":
            ref = str(item.get("ref", ""))
            file_part = ref.split(":", 1)[0]  # allow path:line spans
            resolves = _file_ref_resolves(ws, file_part)
            if not resolves and brain_ws is not None and brain_ws.resolve() != ws.resolve():
                resolves = _file_ref_resolves(brain_ws, file_part)
            if not resolves:
                bad.append(ref)
    return bad


def test_evidence(
    exit_code: int,
    ref: str,
    detail: str = "",
) -> dict[str, Any]:
    """Build a machine-readable `test` evidence item recording a REAL test-run
    outcome — the exit code plus a `ref` naming what was run (a command, a test id,
    a CI job). This is deliverable 3 of the never-wait trust model: closeouts used to
    store test results only as PROSE in the `tests` string, so `self_learning.py`'s
    deterministic miner had nothing to decide on (163 UNVERIFIABLE / 0 gold). A
    structured `{type:"test", exit_code, passed, ref}` item is exactly what
    `self_learning.test_outcome()` reads first (its strongest signal,
    `test_evidence_exit`) — so a failed→fixed pair of closeouts becomes a
    deterministically mineable positive, and a never-fixed failure an anti_pattern.

    `passed` is set from the exit code (0 == pass) so the reader needs no re-parsing.
    A closeout carrying this is stamped `non_human_verified` by the writer: it is an
    honest, self-reported deterministic outcome — usable and mineable NOW, upgradeable
    to hard_gold only when an independent objective checker (not the agent's own run)
    confirms it."""
    return {
        "type": "test",
        "ref": ref,
        "exit_code": int(exit_code),
        "passed": int(exit_code) == 0,
        "detail": detail,
    }


def validate_handoff_field(handoff: Any) -> list[str]:
    """v3 gate (2026-07-07 user standing rule): every closeout must state WHERE
    the artifacts are and HOW the work carries forward. Returns a list of
    problems; empty means the handoff is well-formed. This mirrors
    `validate_evidence`: it is a *warn* surface, not a hard reject -- write_closeout
    still writes so the self-learning loop never loses its fuel, but the omission
    is surfaced loudly to whoever wrote the closeout.

    A well-formed handoff is ``{"locations": [<concrete path>, ...],
    "continuation": "<what happens next>"}`` -- `locations` must hold at least one
    non-empty path (the real artifacts, not a vague description), and
    `continuation` must be a real, specific statement (not empty/placeholder)."""
    if not isinstance(handoff, dict) or not handoff:
        return [
            "missing handoff: a closeout must state `locations` (concrete paths to "
            "the artifacts) and `continuation` (what happens next)"
        ]
    problems: list[str] = []
    locations = handoff.get("locations")
    if not isinstance(locations, list) or not any(str(x).strip() for x in locations):
        problems.append(
            "handoff.locations is empty: give concrete paths to the real artifacts "
            "this closeout is about, not a vague description"
        )
    continuation = handoff.get("continuation")
    if not isinstance(continuation, str) or not continuation.strip():
        problems.append(
            "handoff.continuation is empty: state the next step specifically "
            "(e.g. 'done, no follow-up' / 'feeds into X' / 'blocked on Y')"
        )
    return problems


# A prose test claim: "9/9", "69 passed", "all tests pass", "6/6 green", "tests_pass",
# "verified working", "TDD 6/6". These are the exact phrasings the 2026-07-07 benchmark
# spot-check found asserted in prose while `evidence` stayed []. Kept deliberately broad --
# a false-positive costs one advisory line; a false-negative is the whole failure mode.
_TEST_CLAIM_RE = re.compile(
    r"(\b\d+\s*/\s*\d+\b"                       # 9/9, 6 / 6
    r"|\b\d+\s+(?:tests?\s+)?(?:passed|passing|pass|green)\b"  # 69 passed, 12 tests passing
    r"|\ball\s+(?:tests?|checks?|\d+)\s+(?:pass|passed|passing|green)\b"
    r"|\btests?\s+pass(?:ed|ing)?\b"
    r"|\bverified\s+working\b"
    r"|\btdd\b)",
    re.IGNORECASE,
)


def evidence_theater_warning(
    status: str, result: str, tests: str, evidence: list[dict[str, Any]] | None
) -> str | None:
    """Anti-evidence-theater WARN (2026-07-07 ledger-mining pass). The single
    highest-frequency pattern in the real-build benchmark: a `completed` closeout
    asserts a test/verification result in PROSE ("69 passed", "verified working",
    "TDD 6/6") while the machine-checkable `evidence[]` array is EMPTY -- across
    *every* qwen/deepseek lane and all 8 spot-checked Hermes closeouts. This makes the
    claim unverifiable from the record itself, defeating the whole point of the
    evidence field.

    Deliberately a WARN, not a hard reject -- matching the considered `validate_handoff_field`
    decision (audit.py): write_closeout still writes so the self-learning loop never
    loses its fuel (an incomplete closeout is still valuable failure data), but the gap is
    surfaced loudly to whoever wrote it. Returns the warning string, or None when there is
    no prose test claim OR the claim is already backed by >=1 evidence item."""
    if str(status).lower() not in ("completed", "success", "done"):
        return None
    if evidence:  # any structured evidence at all -> the claim is not prose-only
        return None
    haystack = f"{tests or ''}\n{result or ''}"
    m = _TEST_CLAIM_RE.search(haystack)
    if not m:
        return None
    return (
        f"prose asserts a test/verification result ({m.group(0).strip()!r}) but evidence[] is "
        "empty -- the claim is unverifiable from this closeout. Add >=1 evidence item "
        "(a `test` exit code, a `command` you ran with its output, or a `file` ref you read) "
        "instead of asserting it in prose only."
    )


def write_closeout(
    workspace: str | Path,
    task: str,
    result: str,
    status: str = "completed",
    tests: str = "",
    scripts: str = "",
    promoted_solution: bool = False,
    max_files: int = MAX_AUDIT_FILES_PER_SHARD,
    contract_id: str = "",
    evidence: list[dict[str, Any]] | None = None,
    handoff: dict[str, Any] | None = None,
    supersedes: list[str] | None = None,
) -> Path:
    # Arg-first: honor the workspace the caller already resolved (MCP passes the tenant-pin-safe
    # path from _write_ws); an omitted/None workspace still falls back env-first.
    ws = resolve_workspace_override(workspace)
    audit_dir = choose_audit_dir(ws, max_files=max_files)
    stamp = _timestamp_slug()
    # KE-03 (gate 0.15): stamp+slug alone collide within the same wall-clock
    # second; append a time-sortable, collision-free UUIDv7 suffix so two
    # same-second closeouts land under distinct filenames.
    path = audit_dir / f"cortex-closeout__{stamp}-{_slugify(task)}__{_uuid7()}.md"
    from .version import cortex_version
    payload = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "status": status,
        "task": task,
        "result": result,
        "tests": tests,
        "scripts": scripts,
        "contract_id": contract_id,
        "evidence": evidence or [],
        "handoff": handoff or {},
        # GAP G4 reconcile-on-write: when this closeout supersedes/retracts prior record(s) for
        # the same subject, name them here so the store is a decision procedure, not a blind log.
        # Empty for a plain ADD -- append-with-supersede keeps the old file (audit integrity),
        # its validity closed by this pointer (the "supersede don't delete" fact-validity pattern).
        "supersedes": supersedes or [],
        "promoted_solution": promoted_solution,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # So a before/after self-improvement comparison can never silently mix runs from two
        # different Cortex code states -- every closeout is tagged with exactly which commit
        # produced it (2026-07-07, direct user requirement).
        "cortex_version": cortex_version(),
        # Purely additive, same precedent as `cortex_version` above (2026-07-07): a routing
        # mystery -- why a closeout landed in the wrong workspace -- took 28 tool calls of
        # event-log correlation to diagnose because the closeout itself never recorded which
        # `workspace=` it was actually written to. Recording the RESOLVED path here (post
        # `resolve_workspace_override`, not the raw possibly-None argument) makes that a
        # one-file read instead of a log-correlation exercise.
        "workspace": str(ws),
    }
    # Never-wait trust model (2026-07-14): every record carries a provenance tier so it
    # is USABLE immediately, labelled by how it was validated. A closeout is DELIBERATELY
    # stamped `non_human_verified` regardless of its `evidence[]` list: it is the agent's
    # OWN self-run work record, not an independent checker, so a self-reported exit code
    # must never auto-mint gold (sol@xhigh #6/#8). `stamp` is called with no evidence dict
    # on purpose — an independent objective-checker attestation, not this closeout, is what
    # would later earn `hard_gold` for the underlying result via the promotion path.
    from . import provenance_tiers
    provenance_tiers.stamp(payload)
    body = ["---"]
    for key, value in payload.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (list, dict)):
            rendered = json.dumps(value)
        elif isinstance(value, str):
            rendered = json.dumps(value)
        else:
            rendered = str(value)
        body.append(f"{key}: {rendered}")
    body.append("---")
    body.append(f"# {task}")
    body.append("")
    body.append(result)
    body.append("")
    if evidence:
        # Rendered into the body too so evidence is searchable, not just in the
        # JSON sidecar.
        body.append("## Evidence")
        for item in evidence:
            body.append(
                f"- {item.get('type', '?')}: {item.get('ref', '')}"
                + (f" — {item['detail']}" if item.get("detail") else "")
            )
        body.append("")
    if handoff:
        # Rendered into the searchable body too, not just the JSON sidecar.
        body.append("## Handoff")
        locs = handoff.get("locations") or []
        if locs:
            body.append("**Where to look:**")
            for loc in locs:
                body.append(f"- {loc}")
        cont = handoff.get("continuation")
        if cont:
            body.append(f"**How to carry forward:** {cont}")
        body.append("")
    _atomic_write_text(path, "\n".join(body))
    _atomic_write_text(path.with_suffix(".json"), json.dumps(payload, indent=2))
    # Also expose the latest task handoff at the workspace root, not just inside the closeout.
    # A closeout is a task event, however, and must not overwrite a deliberately authored
    # project HANDOFF.md. LATEST-CLOSEOUT.md is always refreshed; the legacy HANDOFF.md path is
    # refreshed only when absent or already generated by this writer. This preserves cold-start
    # discoverability without letting the newest narrow task become project-wide authority.
    if handoff and (handoff.get("locations") or handoff.get("continuation")):
        try:
            # NOTE: the word "handoff" must appear in lowercase somewhere in the body, not just the
            # ALL-CAPS header -- confirmed live (2026-07-07) that a case-sensitive grep for
            # "handoff|hand_off|hand-off" (the default for most search tools) finds ZERO matches
            # against a file that only ever spells it "HANDOFF". Don't repeat that.
            handoff_lines = [_GENERATED_HANDOFF_MARKER,
                             "# Latest task closeout — read with the project handoff", "",
                             "This is the handoff for this deliverable.", "",
                             f"_Auto-generated from the latest closeout: `{path.name}`_", ""]
            locs = handoff.get("locations") or []
            if locs:
                handoff_lines.append("**Where to look:**")
                for loc in locs:
                    handoff_lines.append(f"- {loc}")
                handoff_lines.append("")
            cont = handoff.get("continuation")
            if cont:
                handoff_lines.append(f"**How to carry forward:** {cont}")
                handoff_lines.append("")
            handoff_lines.append(f"Full closeout (evidence, tests, contract): `{path}`")
            rendered_handoff = "\n".join(handoff_lines)
            _atomic_write_text(Path(ws) / _LATEST_CLOSEOUT_NAME, rendered_handoff)

            canonical_path = Path(ws) / "HANDOFF.md"
            existing = canonical_path.read_text(encoding="utf-8") if canonical_path.exists() else ""
            if (
                not existing
                or _GENERATED_HANDOFF_MARKER in existing
                or _LEGACY_GENERATED_HANDOFF_TEXT in existing
            ):
                _atomic_write_text(canonical_path, rendered_handoff)
        except Exception:  # noqa: BLE001 -- fail-open, the closeout itself already succeeded
            pass
    # GAP G1 self-learning flywheel: a closeout WRITE auto-mints a quarantined pattern
    # candidate (no CLI, no manual mine()), gated by the deterministic regression oracle in
    # self_learning (UNVERIFIABLE -> nothing; never a free-form self-edit). Fail-open by
    # contract -- the closeout itself already succeeded and must never be blocked by the
    # flywheel. Candidates land in audit/self-learning/ (quarantine), not the indexed corpus.
    try:
        from . import self_learning
        self_learning.on_closeout(payload, ws)
    except Exception:  # noqa: BLE001 -- fail-open; the flywheel is additive, never load-bearing
        pass
    # Durable-telemetry mirror (opt-in, fail-open): on an ephemeral served plane (Railway) this
    # closeout is the self-learning loop's only fuel and dies on redeploy; mirror it to R2/S3 when
    # configured. A sink failure must NEVER break the closeout itself.
    try:
        from cortex_core import telemetry
        if telemetry.enabled():
            telemetry.mirror_file(path)
            telemetry.mirror_file(path.with_suffix(".json"))
    except Exception:  # noqa: BLE001 -- fail-open by contract
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Write a Cortex closeout log")
    parser.add_argument("--task", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--status", default="completed")
    parser.add_argument("--tests", default="")
    parser.add_argument("--scripts", default="")
    parser.add_argument("--promoted-solution", action="store_true")
    parser.add_argument(
        "--handoff-location", action="append", default=[], dest="handoff_locations",
        help="concrete path to an artifact this closeout is about (repeatable)",
    )
    parser.add_argument(
        "--handoff-continuation", default="",
        help="what happens next: 'done, no follow-up' / 'feeds into X' / 'blocked on Y'",
    )
    parser.add_argument(
        "--test-exit-code", type=int, default=None,
        help="exit code of the test run this closeout reports (0=pass). Records a "
             "machine-readable `test` evidence item so the self-learning miner can "
             "deterministically decide pass/fail instead of guessing from prose.",
    )
    parser.add_argument(
        "--test-ref", default="",
        help="what was run for --test-exit-code (a command / test id / CI job); "
             "required when --test-exit-code is given.",
    )
    parser.add_argument(
        "--test-detail", default="",
        help="optional extra detail for the recorded test evidence (e.g. '12 passed').",
    )
    args = parser.parse_args(argv)
    handoff = None
    if args.handoff_locations or args.handoff_continuation:
        handoff = {"locations": args.handoff_locations, "continuation": args.handoff_continuation}
    evidence: list[dict[str, Any]] | None = None
    if args.test_exit_code is not None:
        ref = args.test_ref.strip() or "unspecified-test-run"
        if not args.test_ref.strip():
            print("warning: --test-exit-code given without --test-ref; recording "
                  "ref='unspecified-test-run' (name the command/test id for a mineable record)",
                  file=sys.stderr)
        evidence = [test_evidence(args.test_exit_code, ref, detail=args.test_detail)]
    for problem in validate_handoff_field(handoff):
        print(f"warning: {problem}", file=sys.stderr)
    path = write_closeout(
        workspace=resolve_workspace(),
        task=args.task,
        result=args.result,
        status=args.status,
        tests=args.tests,
        scripts=args.scripts,
        promoted_solution=args.promoted_solution,
        evidence=evidence,
        handoff=handoff,
    )
    print(path)
    return 0


def _read_closeouts(workspace: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(workspace.glob("audit/audit-log-*/agent/*.json")):
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return entries


def read_main(argv: list[str] | None = None) -> int:
    """Read/query path (`cortex_audit`) — lists existing closeouts, writes nothing."""
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Read Cortex closeout logs")
    parser.add_argument("--task", default=None, help="substring filter on task")
    parser.add_argument("--status", default=None, help="exact filter on status")
    args = parser.parse_args(argv)
    entries = _read_closeouts(resolve_workspace())
    if args.task:
        entries = [e for e in entries if args.task.lower() in str(e.get("task", "")).lower()]
    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]
    for entry in entries:
        print(json.dumps(entry))
    return 0
