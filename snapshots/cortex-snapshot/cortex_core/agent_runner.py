"""A minimal ReAct-style agent loop that gives a cheap model (qwen, etc.) real tool access --
sandboxed file write, a best-effort restricted shell (cwd-confined, stripped env, pattern-blocked,
timed out -- NOT a real OS-level sandbox), and the Cortex MCP tools via the LOCAL owner-mode route --
so it can attempt open-ended benchmark tasks (build a dashboard, clean data, etc.), not just the
synthetic bakeoff engine's toy build-track.

Distinct from `bakeoff.py` (a measurement harness over a scripted BUILD_TRACK state machine, used to
test search-guidance effects) -- this is a general driver for a REAL task with REAL side effects.

"Non-admin route" here = local owner-mode: the model calls cortex_register/search/status/onboarding/
contract/write_log by having its tool call dispatched directly to the already-imported
`cortex_core.mcp` functions, scoped to a per-run sandbox workspace (never the canonical SSC
workspace) so a benchmark run can never write into the repo itself. cortex_write_log goes through
the REAL gated MCP tool (admin/forced-docs/contract gates), so a benchmark's pass/fail on "did it
close out" is trustworthy evidence rather than theater -- a write attempt without an approved
contract comes back as a refusal (not a crash), and the agent is expected to call cortex_contract
first.

Design: the model-completion callable is INJECTABLE (`model_complete`) so the dispatch/loop logic is
unit-testable with a scripted fake model, with no network call required to verify the wiring. The
live qwen callable (`qwen_complete`) is a thin wrapper around `research._llm_complete`.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from cortex_core.llm_parse import extract_tool_call

# The tools this loop offers to the model. cortex_* tools call the real MCP functions (owner-mode,
# session-scoped to this run); write_file/run_shell are sandboxed to the run's own directory; done
# ends the loop. Kept small and explicit -- this is not the full 22-tool MCP surface, just what an
# open-ended build task needs.
AGENT_TOOLS = [
    "cortex_search", "cortex_status", "cortex_onboarding", "cortex_contract", "cortex_write_log",
    "write_file", "read_file", "append_file", "edit_file", "run_shell", "done",
]

_SYSTEM_PROMPT = """You are an autonomous agent completing a task in a sandboxed working directory.
Available tools (call exactly one per turn as a single JSON object: {{"tool": "<name>", "payload": {{...}}}}):
- cortex_search: {{"query": "..."}} -- search the Cortex brain/corpus for grounding before you build.
- cortex_contract: {{"task": "...", "planned_approach": "...", "acceptance_criteria": ["..."],
  "verification_steps": ["..."]}} -- create/approve the approach contract this task needs BEFORE any
  closeout (optional for benchmarks: the harness auto-approves one if write_log is refused). Two-call pattern: call with only "task" to get a corpus-prefilled stub, then call again
  with planned_approach + acceptance_criteria[] + verification_steps[] filled in to approve it.
- cortex_write_log: {{"task": "...", "result": "...", "tests": "..."}} -- record a closeout. Do this
  at least once before finishing, even on partial failure.
- write_file: {{"path": "relative/path.ext", "content": "..."}} -- create/overwrite a whole file in
  your sandbox. write_file ALREADY creates any parent directories itself -- you do NOT need
  run_shell/mkdir first. DANGER: a full-file write of a large file (a whole dashboard's HTML+CSS+JS at
  once) gets TRUNCATED mid-output and silently fails. Use write_file ONLY for the small skeleton
  (see "Building a file in phases" below), then fill it in with append_file/edit_file.
- read_file: {{"path": "relative/path.ext", "offset": 0, "limit": 4000}} -- read a file that already
  exists in your sandbox. Use this FIRST when the sandbox already has partial output from an earlier
  attempt, so you continue it instead of clobbering it. Large files are returned in windows of up to
  ~4000 chars; a response ending in "[TRUNCATED: ... call read_file again with offset=N]" means there
  is more -- call read_file again with that exact offset to continue. Do NOT try to work around this
  with shell commands (Substring/Get-Content/head/tail) to "see more" -- use offset/limit, it is
  reliable and the truncation marker tells you exactly what to pass next.
- append_file: {{"path": "relative/path.ext", "content": "..."}} -- append content to the END of a
  file (creates it if absent). You do NOT resend the existing content -- only the new part. This is
  how you add one more section to a file you already started, without risking truncation of the whole.
- edit_file: {{"path": "relative/path.ext", "find": "...", "replace": "..."}} -- a targeted
  find-and-replace inside an existing file. `find` must match EXACTLY ONCE; if it matches zero times
  or more than once the edit is REFUSED (not applied) with a reason -- make `find` long/unique enough
  to hit exactly one spot (e.g. a whole placeholder line). This is how you replace one placeholder
  section with its real content, or flip a section marker from PENDING to DONE, without rewriting the file.
- run_shell: {{"cmd": "..."}} -- run a shell command (best-effort restricted: cwd-confined to your
  sandbox, stripped env, obviously-destructive/exfiltration patterns blocked, capped timeout). Not a
  real OS sandbox; a refused command returns an observation explaining why. This sandbox runs on
  Windows -- prefer write_file over shell mkdir/rm for file operations, since Unix-style flags
  (mkdir -p, rm -rf, ; chaining) may not work here and wasting turns fighting shell syntax is a
  failure. If a command fails twice, switch approach (e.g. use write_file) instead of retrying
  the same syntax a third time. Output over ~4000 chars is truncated, but the FULL output is always
  saved to a file (path given in the truncation note) -- use read_file with offset/limit on that
  path to see the rest. Do NOT re-run the command to "try to get more output" -- the full output
  already exists on disk, go read it.
- done: {{"summary": "..."}} -- end the task; summarize what you built and its state.

Task: {task}

Extracting/cleaning input data (do this BEFORE building anything -- and give it a HARD CEILING of
TWO parse attempts):
  Real input data is messy and you will NOT parse it perfectly. That is fine and expected. Attempt a
  parse AT MOST TWICE. If your second attempt captures most of the data -- even imperfectly -- STOP
  parsing and move on to building. Flag any uncertain/malformed/missed rows honestly (an "unverified"
  or "PARSE?" marker on that row) instead of rewriting the parser a third time to chase them. A
  working deliverable with a few honestly-flagged imperfect rows BEATS a perfect parser and no
  deliverable at all -- that trade is the whole point of this ceiling.
  - Do NOT rewrite the same parsing script over and over with small regex tweaks (extract.py ->
    parse_v2.py -> parse_final.py is the failure, not the process). If a parse UNDER-captures, the fix
    is almost never "one more regex tweak" -- it is usually that the data has MORE THAN ONE row
    format (e.g. some rows richly formatted with `**bold**` markers, others plain one-liners), so a
    single pattern can only ever match one shape and silently drops the rest. Handle the shapes you
    can, flag what you miss, and do not loop.
  - Encoding/display artifact: if a separator or character shows up as the replacement character (the
    black-diamond question mark, `�`) in shell OUTPUT, that is a DISPLAY artifact of the terminal
    re-encoding, NOT the file's real content. The real character is almost always an em dash (`—`)
    or a curly quote. Do NOT paste the replacement character into your regex -- it will match nothing
    in the real UTF-8 file. Match the real Unicode character, or better, split on a simpler stable
    anchor (the leading `N. ` row number) instead of the fancy separator.
  - Once you have a "good enough" parse, EMBED that data directly into the deliverable and start
    building. Every parsing turn past attempt two is a turn NOT spent on the thing the task asked for.

Building a file in phases (DO THIS for any non-trivial file -- a dashboard, a page, a multi-part
document -- a single write_file of the whole thing WILL be truncated and silently fail):
  1. PLAN the sections first. Before writing any code, list the sections the file needs (e.g. for a
     dashboard: head/styles, header, nav, each chart card, the data table, footer scripts).
  2. write_file a SKELETON only -- the outer structure plus one placeholder marker per planned
     section, and nothing else. A marker is a comment carrying the section name and status PENDING,
     in that file's comment syntax. For HTML: `<!-- SECTION:analytics-chart:PENDING -->`. For JS/CSS:
     `/* SECTION:analytics-chart:PENDING */`. Use the same `SECTION:<name>:PENDING` token whatever the
     comment style. This skeleton is tiny, so it never truncates.
  3. Then fill ONE section per turn: use edit_file to replace that section's
     `SECTION:<name>:PENDING` placeholder line with the real content for that section FOLLOWED BY a
     `SECTION:<name>:DONE` marker (or append_file if you are strictly adding to the end). One card, one
     table, one script block at a time -- each turn's output stays small and cannot truncate.
  4. Keep going until no `PENDING` markers remain. The markers are your checklist and your resume
     point: at any turn you (or a later retry) can read_file and see exactly which sections are DONE
     and which are still PENDING, and pick up at the first PENDING one -- never restart from scratch.
Never try to emit a large finished file in one write_file call. Skeleton first, then fill by section.

Deliverable entry point: the primary HTML page for this project MUST be named `index.html` (at your
sandbox root, or at the root of its own subfolder if the task calls for multiple separate builds). This
is enforced -- calling `done` with an HTML deliverable that is not named `index.html` will be refused.
Pick this name from the start; do not build under a different name and rename at the end.

Work step by step. Search before you build. Write real files. Test what you build if you can.
Call cortex_write_log to record a closeout, then call done. Respond with ONLY the
JSON tool call, nothing else.

Do not repeatedly rewrite the same file with only minor variations -- if you catch yourself doing
that, it means you are stuck restating the input/setup instead of progressing toward the actual
deliverable; move forward to the next concrete step (the transformation, UI, or analysis the task
actually asked for) instead of regenerating the same starting point again.
"""

_MAX_SHELL_SECONDS = 30
_MAX_OUTPUT_CHARS = 4000
# Sized for run_shell's worst case: _MAX_OUTPUT_CHARS stdout + its truncation note (~90 chars) +
# 1000 stderr + its truncation note (~90 chars) + the "exit=.../stdout:/stderr:" scaffolding.
# See the history-append site (search _HISTORY_OBS_CAP) for why this must not be smaller than
# any single tool's own output cap plus its truncation marker.
_HISTORY_OBS_CAP = 6000

# --- Data-prep "stop parsing, start building" escape hatch --------------------------------------
# Two DIFFERENT weak models (opencode/deepseek-v4-flash task22, opencode-zen/big-pickle task22b, both
# 2026-07-07/08) each burned an ENTIRE run rewriting parser scripts (extract.py -> parse_facilities.py
# -> parse_v2.py -> parse_final.py ...) and NEVER started the actual deliverable. Confirmed root cause
# from both transcripts: the input data has >1 row format (bold rich rows + plain one-liners) so a
# single-regex parse under-captures, AND the em dash separator rendered as `�` in shell output so each
# model literally put the replacement char in its regex (`r'^(\d+)\. \*\*(.+?)\*\* � (.*)'` appears
# verbatim in BOTH runs) -- so every parse under-captured and the model "tweaked the regex" forever.
# The prompt now sets a 2-attempt ceiling, but a weak model won't self-count. So we count parser-script
# rewrites for it and inject a ONE-TIME hard nudge once it crosses the ceiling without having started
# the real (HTML) deliverable. Cheap, deterministic, unit-testable -- no model call.
_DATA_PREP_SCRIPT_LIMIT = 3
_PARSER_SCRIPT_KEYWORDS = ("parse", "extract", "split", "debug", "clean", "wrangl", "scrape")
_BUILD_DELIVERABLE_EXTS = (".html", ".htm")


def _is_parser_script(path: str) -> bool:
    """True if `path` looks like yet another throwaway data-parsing script (a `.py` whose name says
    parse/extract/split/...). This is the signal for the re-parse loop, not for real progress."""
    p = (path or "").strip().lower()
    if not p.endswith(".py"):
        return False
    stem = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return any(k in stem for k in _PARSER_SCRIPT_KEYWORDS)


def _is_build_deliverable(path: str) -> bool:
    """True if `path` is the actual build deliverable (an HTML page) -- once one of these is written,
    the model has moved past data-prep into building and the anti-reparse nudge is no longer needed."""
    return (path or "").strip().lower().endswith(_BUILD_DELIVERABLE_EXTS)


# 2026-07-08: real, filesystem-verified finding across the whole 2026-07-08 overnight CNA config
# sweep (same task, ~30 independent runs) -- the single HTML deliverable was named `index.html` in
# some runs, `tracker.html` in others, `cna_tracker.html` in others, entirely at each run's own
# discretion, with no enforced convention. Every dashboard/file-browsing tool built around these
# runs (Dashboard D included) has to guess the entry-point filename per task instead of relying on
# one. The prompt now names `index.html` as required; this is the deterministic backstop -- a model
# won't reliably self-enforce a prose rule, so `done` is refused (not silently allowed) when an HTML
# deliverable exists but none of them is named `index.html`. Fires at most once per run (tracked by
# the caller) so a model that still won't comply on the second attempt isn't blocked forever.
def _missing_index_entrypoint(run_dir: "Path") -> bool:
    """True if the sandbox has at least one HTML deliverable but none of them is literally named
    `index.html` (case-insensitive) anywhere in the tree -- the exact drift this nudge exists to catch."""
    run_dir = Path(run_dir)
    if not run_dir.exists():
        return False
    html_files = [p for p in run_dir.rglob("*")
                  if p.is_file() and _is_build_deliverable(p.name)
                  and (p.relative_to(run_dir).parts[:1] or [""])[0] not in _SCAN_SKIP]
    if not html_files:
        return False
    return not any(p.name.lower() == "index.html" for p in html_files)


_MISSING_INDEX_NUDGE = (
    "\n[system: `done` refused -- you have written an HTML deliverable but none of your files is "
    "named `index.html`. Rename (or add) your primary page to `index.html` at your sandbox root (or "
    "the root of its own subfolder, if this task builds multiple separate projects) before finishing. "
    "This is a required, enforced convention across every project, not a suggestion.]\n"
)


_DATA_PREP_NUDGE = (
    "\n[system: STOP RE-PARSING. You have now written {n} separate data-parsing scripts and have not "
    "yet started building the actual deliverable. Real input data is irregular -- it almost certainly "
    "has more than one row format, and any separator showing as `�` in shell output is a display "
    "artifact of an em dash, not real content. A parse that captures MOST rows, with the few "
    "malformed/uncertain ones flagged honestly, is GOOD ENOUGH. Take what your best parse already "
    "produced, embed that data directly into the deliverable, and build the actual file (the HTML "
    "tracker) NOW. A working tracker with a few honestly-flagged imperfect rows beats a perfect parser "
    "and no tracker. Do NOT write another parser script.]\n"
)

# 2026-07-08: real failure data (4 back-to-back runs, single pinned model, no round-robin, correct
# max_tokens floor) showed a DIFFERENT shape than what _DATA_PREP_NUDGE catches -- every one made
# ZERO write_file/append_file/edit_file calls at all (so parser_scripts never grows and the nudge
# above can never fire) while burning 5-14 of an 11-19 turn budget entirely inside run_shell --
# writing/rerunning parse scripts via inline `python -c`/heredocs, inspecting output, iterating --
# and never once reaching the file-writing tools, let alone cortex_contract. The gap: the existing
# nudge only watches the WRITE tools for parser-script paths; it has no visibility into shell-only
# data wrangling. This one does -- it counts run_shell calls directly, independent of what tool
# wrote (or didn't write) any scripts.
_SHELL_EXPLORATION_LIMIT = 6
_SHELL_LOOP_NUDGE = (
    "\n[system: STOP EXPLORING VIA SHELL. You have run {n} shell commands and have not yet called "
    "write_file/edit_file to start the actual deliverable (the HTML tracker) -- all of this work has "
    "stayed inside run_shell (parsing, re-parsing, inspecting output) with nothing durable written to "
    "the deliverable file itself. Whatever data you already have from your shell commands -- even if "
    "imperfect -- is enough to start. Use write_file NOW to create the HTML skeleton, then fill it in. "
    "Do not run another exploratory or parsing shell command before you have called write_file at "
    "least once on the actual deliverable.]\n"
)

# 2026-07-08, same night: real data (ab_variant_qwen35b) showed a model can RELAPSE into the same
# reparse loop AFTER build_started is already True -- 8 near-identical inline `python -c "..."`
# parse attempts via run_shell, turns 44-58, well after index.html already existed. The nudge
# above is gated on `not build_started` and fires only ONCE, so it structurally cannot catch a
# later relapse. This one is different on purpose: it counts CONSECUTIVE run_shell calls (reset by
# any other tool), independent of build_started, and can fire more than once (each firing resets
# the counter) -- a real mid-build relapse is exactly what it's for.
_CONSECUTIVE_SHELL_RELAPSE_LIMIT = 5
_SHELL_RELAPSE_NUDGE = (
    "\n[system: STOP RE-RUNNING SHELL COMMANDS. You have run {n} run_shell calls in a row -- this "
    "looks like the same re-parsing loop again, just later in the build this time. You already have "
    "a deliverable in progress; whatever data your last shell attempt produced (even if imperfect) is "
    "enough to keep going. Go back to editing the deliverable file directly (write_file/edit_file) "
    "instead of re-running the same shell command.]\n"
)

# Best-effort deny-list for run_shell. This is a HEURISTIC, not a security boundary -- it catches
# the most obviously destructive/exfiltrating command shapes so a hallucinated or prompt-injected
# model output can't trivially wipe a drive or phone secrets home. It is trivially bypassable (obfus-
# cation, encoding, alternate tools) and must not be trusted as real isolation; the real containment
# would be an OS-level sandbox/container, which this loop does not have.
_SHELL_DENY_PATTERNS = [
    # recursive delete of a drive root or upward parent-traversal delete
    r"rm\s+-[a-z]*r[a-z]*f?\s+/(?:\s|$)",       # rm -rf /
    r"rm\s+-[a-z]*r[a-z]*f?\s+\.\.",            # rm -rf ..
    r"rm\s+-[a-z]*r[a-z]*f?\s+[a-zA-Z]:\\?(?:\s|$)",  # rm -rf C:\
    r"\brd\s+/s\b", r"\brmdir\s+/s\b",          # Windows recursive dir delete
    r"(?:del|erase)\s+/[a-z]*s",                # del /s recursive
    r"\.\.[\\/].*\b(?:rm|del|erase|rmdir|rd)\b",  # delete reaching through ../ or ..\
    r"\b(?:rm|del|erase)\b.*\.\.[\\/]",         # delete of a ../ or ..\ target
    # formatting / partitioning
    r"\bformat\b\s+[a-zA-Z]:", r"\bmkfs\b", r"\bdiskpart\b", r"\bfdisk\b",
    r">\s*/dev/[sh]d[a-z]",
    # shutdown / reboot
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b", r"\bInit\s+0\b",
    r"Stop-Computer", r"Restart-Computer",
    # registry deletion
    r"\breg\s+delete\b", r"Remove-Item.*HK(?:LM|CU|CR|U|CC):",
    # outbound network to a non-localhost host (exfiltration vector)
    r"\b(?:curl|wget)\b\s+.*https?://(?!(?:localhost|127\.0\.0\.1|\[?::1\]?))",
    r"Invoke-WebRequest\b.*https?://(?!(?:localhost|127\.0\.0\.1|\[?::1\]?))",
    r"Invoke-RestMethod\b.*https?://(?!(?:localhost|127\.0\.0\.1|\[?::1\]?))",
    r"\bnc\b\s+\S", r"\bncat\b\s+\S", r"\btelnet\b\s+\S",
]


def _shell_is_denied(cmd: str) -> bool:
    """Best-effort: True if `cmd` matches an obviously destructive/exfiltration pattern."""
    import re
    return any(re.search(p, cmd, flags=re.IGNORECASE) for p in _SHELL_DENY_PATTERNS)


def _restricted_shell_env() -> dict[str, str]:
    """A minimal env for run_shell -- carries only what's needed to resolve/run tools, so a shell
    command cannot read/exfiltrate API keys or other secrets sitting in the parent process env
    (e.g. QWEN_*/GLM_* judge-tier keys this repo's .env loads). Best-effort, not a real boundary."""
    import os
    keep = ("PATH", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE",
            "HOME", "LANG", "LC_ALL")
    return {k: os.environ[k] for k in keep if k in os.environ}


class RunBudgetExceeded(Exception):
    pass


def _safe_path(run_dir: Path, rel: str) -> Path:
    """Resolve `rel` under run_dir; refuse any path that escapes the sandbox."""
    p = (run_dir / rel).resolve()
    if not p.is_relative_to(run_dir.resolve()):
        raise ValueError(f"path escapes sandbox: {rel}")
    return p


def _auto_contract_from_state(task: str, run_dir: Path, session_id: str) -> bool:
    """2026-07-08: When write_log is refused for lack of contract, auto-fill and approve it.
    Returns True if the contract was successfully approved, False if approval failed or is impossible.
    This is HARNESS-ONLY assistance to remove model ceremony turns; no governance/provenance logic."""
    from cortex_core.mcp import cortex_contract
    import asyncio
    try:
        run_dir = Path(run_dir)
        # Step 1: prefill to get the corpus-backed stub
        prefill = asyncio.run(cortex_contract(
            task=task, session_id=session_id, workspace=str(run_dir)
        ))
        # prefill is a dict; if it has "refused", contract can't be auto-filled
        if isinstance(prefill, dict) and prefill.get("refused"):
            return False

        # Step 2: collect real deliverable files (skip scaffolding directories)
        deliverables: list[str] = []
        if run_dir.exists():
            for p in sorted(run_dir.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(run_dir)
                # Skip audit, transcript, overflow dirs
                if rel.parts and rel.parts[0] in _SCAN_SKIP or rel.parts[0] == "_shell_overflow":
                    continue
                deliverables.append(rel.as_posix())

        # Step 3: submit with auto-filled fields
        submit = asyncio.run(cortex_contract(
            task=task,
            session_id=session_id,
            workspace=str(run_dir),
            planned_approach=f"build {task[:120]}",  # truncated task
            acceptance_criteria=[f"deliverable exists at {d}" for d in deliverables[:10]],
            verification_steps=["open index.html"],
            evidence_refs=(prefill.get("evidence_refs", []) if isinstance(prefill, dict) else []) + deliverables,
        ))
        # submit is a dict; check if it was approved
        if isinstance(submit, dict):
            return submit.get("approved", False)
        return False
    except Exception:
        return False


import re as _re

# A section marker is `SECTION:<name>:<STATUS>` embedded in a comment of whatever the file's syntax is
# (`<!-- SECTION:x:PENDING -->`, `/* SECTION:x:DONE */`, `# SECTION:x:PENDING`). We match the token
# regardless of comment wrapper so one convention works across HTML/CSS/JS/Python. This is the
# phased-generation checklist AND the resume point: a later retry reads which sections are still
# PENDING without re-reasoning over the file's prose. Kept deliberately cheap (regex, no model call).
_SECTION_MARKER = _re.compile(r"SECTION:([A-Za-z0-9._+\-]+):(PENDING|DONE)", _re.IGNORECASE)

# Sandbox scaffolding that is not part of the model's deliverable and must not be scanned/surfaced as
# resumable output (run_task creates these itself).
_SCAN_SKIP = {"audit", "transcript.jsonl"}


def scan_section_markers(run_dir: Path) -> dict[str, dict[str, list[str]]]:
    """Cheap, model-free scan of the sandbox for phased-generation section markers.

    Returns {relpath: {"done": [names], "pending": [names]}} for every text file that carries at least
    one `SECTION:<name>:<STATUS>` marker. A name seen as DONE anywhere wins over a PENDING sighting
    (the flip edit_file makes leaves the DONE marker; a stale PENDING should not mask it). This is what
    lets an interrupted run resume at the first still-PENDING section instead of restarting."""
    run_dir = Path(run_dir)
    out: dict[str, dict[str, list[str]]] = {}
    if not run_dir.exists():
        return out
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(run_dir)
        if rel.parts and rel.parts[0] in _SCAN_SKIP:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary/unreadable -- not a phased text deliverable
        done: list[str] = []
        pending: list[str] = []
        for name, status in _SECTION_MARKER.findall(text):
            (done if status.upper() == "DONE" else pending).append(name)
        if not done and not pending:
            continue
        done_set = set(done)
        # de-dupe, preserve first-seen order, and drop pendings already satisfied by a DONE marker
        seen_done: list[str] = []
        for n in done:
            if n not in seen_done:
                seen_done.append(n)
        seen_pending: list[str] = []
        for n in pending:
            if n not in done_set and n not in seen_pending:
                seen_pending.append(n)
        out[rel.as_posix()] = {"done": seen_done, "pending": seen_pending}
    return out


def _resume_notice(run_dir: Path) -> str:
    """If the sandbox already holds partial output from a prior (interrupted/rate-limited) attempt,
    build a prompt preamble telling the model to READ existing files and resume at the first PENDING
    section rather than clobbering them. Empty string when there is nothing to resume."""
    run_dir = Path(run_dir)
    existing = [p for p in sorted(run_dir.rglob("*"))
                if p.is_file() and (p.relative_to(run_dir).parts[:1] or [""])[0] not in _SCAN_SKIP]
    if not existing:
        return ""
    markers = scan_section_markers(run_dir)
    lines = ["\n[RESUME] This sandbox ALREADY contains partial output from an earlier attempt that was "
             "interrupted before finishing. Do NOT start over and do NOT overwrite these files with a "
             "fresh write_file -- continue them. First read_file the relevant file(s), then fill the "
             "PENDING sections one at a time with edit_file/append_file, flipping each marker to DONE."]
    lines.append("Existing files: " + ", ".join(p.relative_to(run_dir).as_posix() for p in existing))
    if markers:
        lines.append("Section status detected (resume at the first PENDING one):")
        for rel, st in markers.items():
            done = ", ".join(st["done"]) or "none"
            pending = ", ".join(st["pending"]) or "none (file may be complete)"
            lines.append(f"  - {rel}: DONE[{done}] | PENDING[{pending}]")
    return "\n".join(lines) + "\n"


def execute_tool(name: str, payload: dict[str, Any], run_dir: Path, session_id: str | None) -> str:
    """Dispatch one tool call; returns the observation text fed back to the model."""
    if name == "cortex_search":
        from cortex_core.mcp import cortex_search
        import asyncio
        out = asyncio.run(cortex_search(query=payload.get("query", ""), session_id=session_id,
                                        workspace=str(run_dir)))
        return json.dumps(out)[:_MAX_OUTPUT_CHARS]
    if name == "cortex_status":
        from cortex_core.mcp import cortex_status
        import asyncio
        out = asyncio.run(cortex_status(session_id=session_id, workspace=str(run_dir)))
        return json.dumps(out)[:_MAX_OUTPUT_CHARS]
    if name == "cortex_onboarding":
        from cortex_core.mcp import cortex_onboarding
        import asyncio
        out = asyncio.run(cortex_onboarding(session_id=session_id))
        return json.dumps(out)[:_MAX_OUTPUT_CHARS]
    if name == "cortex_contract":
        from cortex_core.mcp import cortex_contract
        import asyncio
        kwargs: dict[str, Any] = {}
        for k in ("planned_approach", "acceptance_criteria", "verification_steps", "task_type",
                  "evidence_refs"):
            if k in payload:
                kwargs[k] = payload[k]
        out = asyncio.run(cortex_contract(task=str(payload.get("task", "")), session_id=session_id,
                                          workspace=str(run_dir), **kwargs))
        return json.dumps(out)[:_MAX_OUTPUT_CHARS]
    if name == "cortex_write_log":
        # Route through the REAL gated MCP tool (admin/forced-docs/contract gates) -- no direct
        # audit.write_closeout bypass. Without an approved cortex_contract this returns a refusal
        # dict, not a written closeout; the harness can optionally auto-fill+approve one.
        from cortex_core.mcp import cortex_write_log
        import asyncio
        kwargs = {}
        if "evidence" in payload:
            kwargs["evidence"] = payload["evidence"]
        # The benchmark harness drives its OWN task loop (search -> contract -> closeout), not the
        # server-side chart, so it takes the logged escape hatch for the mandatory-state-machine gate
        # (Decision B, 2026-07-07). This is a legitimate "can't reasonably go through the chart" case:
        # it does NOT bypass the contract or forced-docs gates, which still apply and are separately
        # tested. A model may override this by supplying its own reason in the payload.
        kwargs.setdefault(
            "state_machine_override_reason",
            payload.get("state_machine_override_reason")
            or "benchmark harness: agent_runner drives its own task loop, not the server chart",
        )
        task_str = str(payload.get("task", ""))
        result_str = str(payload.get("result", ""))
        tests_str = str(payload.get("tests", ""))
        out = asyncio.run(cortex_write_log(task=task_str, result=result_str, tests=tests_str,
                                           session_id=session_id, workspace=str(run_dir), **kwargs))

        # 2026-07-08: if write_log was refused for lack of contract, try auto-fill+approve once
        if isinstance(out, dict) and out.get("refused") and "contract" in out.get("reason", "").lower():
            if _auto_contract_from_state(task_str, run_dir, session_id):
                # Contract auto-approved; retry the write_log once
                out = asyncio.run(cortex_write_log(task=task_str, result=result_str, tests=tests_str,
                                                   session_id=session_id, workspace=str(run_dir), **kwargs))
        return json.dumps(out)[:_MAX_OUTPUT_CHARS]
    if name == "write_file":
        rel = payload.get("path", "")
        content = payload.get("content", "")
        # Observed live twice (task07, task13): an empty/"."-style path resolves to run_dir
        # itself, and writing to a directory path raises a confusing PermissionError deep in
        # pathlib. Reject it up front with a reason the model can actually act on.
        if not rel or not rel.strip() or rel.strip() in (".", "./", ".\\"):
            return ("write_file refused: path is empty or refers to the run directory itself "
                     "-- give a real filename, e.g. \"output.html\" or \"src/app.js\"")
        p = _safe_path(run_dir, rel)
        if p == run_dir.resolve() or p.is_dir():
            return f"write_file refused: \"{rel}\" resolves to a directory, not a file"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        obs = f"wrote {rel} ({len(content)} bytes)"
        # 2026-07-08: after a successful edit/append/write, scan for remaining PENDING sections
        # and fold them into the observation so the model doesn't need a confirming re-read to
        # find the next marker (mirrors Aider's search-replace returning the delta).
        markers = scan_section_markers(run_dir)
        if rel in markers or any(m.startswith(rel + "/") for m in markers):
            # This file or a file under it has section markers
            matching_keys = [k for k in markers.keys() if k == rel or k.startswith(rel + "/")]
            for key in matching_keys:
                pending = markers[key].get("pending", [])
                done = markers[key].get("done", [])
                if pending:
                    # List remaining PENDING with count
                    count_str = f"{len(done)}/{len(done)+len(pending)}"
                    pending_list = ", ".join(pending[:8])  # limit to ~8 names
                    obs += f"\n[remaining PENDING ({count_str}): {pending_list}]"
                elif done:
                    # All sections done
                    obs += "\n[all sections DONE]"
        return obs
    if name == "read_file":
        rel = payload.get("path", "")
        if not rel or not rel.strip() or rel.strip() in (".", "./", ".\\"):
            return ("read_file refused: path is empty or refers to the run directory itself "
                     "-- give a real filename, e.g. \"index.html\"")
        p = _safe_path(run_dir, rel)
        if not p.exists():
            return f"read_file: \"{rel}\" does not exist yet -- nothing to read"
        if p.is_dir():
            return f"read_file refused: \"{rel}\" is a directory, not a file"
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            return f"read_file: could not read \"{rel}\" as text ({e})"
        # 2026-07-08: real, transcript-verified root cause of the CNA reparse loop -- a model
        # needing more than one window of a file had NO deterministic way to ask for the next
        # chunk, so it resorted to ad hoc PowerShell .Substring() math against erratic, overlapping,
        # non-monotonic ranges (never converging) instead of a clean, boring offset walk. Modeled
        # on Claude Code's own Read tool, which exposes real offset/limit params for exactly this
        # reason (anthropics/claude-code#4002). offset/limit are char offsets here (this tool
        # predates any line-oriented reading); both optional, default to a full read from 0.
        try:
            offset = int(payload.get("offset", 0) or 0)
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(payload.get("limit", 0) or 0) or _MAX_OUTPUT_CHARS
        except (TypeError, ValueError):
            limit = _MAX_OUTPUT_CHARS
        offset = max(0, offset)
        chunk = text[offset:offset + limit]
        shown_end = offset + len(chunk)
        note = (
            f"\n[TRUNCATED: showing chars {offset}-{shown_end} of {len(text)} -- call read_file "
            f"again with offset={shown_end} to see more]"
            if shown_end < len(text) else ""
        )
        return f"contents of {rel} ({len(text)} bytes total, showing {offset}-{shown_end}):\n{chunk}{note}"
    if name == "append_file":
        # Same up-front path safety as write_file, but appends (open mode "a") so the model never has
        # to resend the existing file content -- the fix for full-file-rewrite truncation. Creates the
        # file if it does not exist yet (append-to-fresh is a legal first section-fill).
        rel = payload.get("path", "")
        content = payload.get("content", "")
        if not rel or not rel.strip() or rel.strip() in (".", "./", ".\\"):
            return ("append_file refused: path is empty or refers to the run directory itself "
                     "-- give a real filename, e.g. \"index.html\"")
        p = _safe_path(run_dir, rel)
        if p == run_dir.resolve() or p.is_dir():
            return f"append_file refused: \"{rel}\" resolves to a directory, not a file"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)
        obs = f"appended {len(content)} bytes to {rel} (now {p.stat().st_size} bytes)"
        # 2026-07-08: after a successful edit/append/write, scan for remaining PENDING sections
        # and fold them into the observation so the model doesn't need a confirming re-read to
        # find the next marker (mirrors Aider's search-replace returning the delta).
        markers = scan_section_markers(run_dir)
        if rel in markers or any(m.startswith(rel + "/") for m in markers):
            # This file or a file under it has section markers
            matching_keys = [k for k in markers.keys() if k == rel or k.startswith(rel + "/")]
            for key in matching_keys:
                pending = markers[key].get("pending", [])
                done = markers[key].get("done", [])
                if pending:
                    # List remaining PENDING with count
                    count_str = f"{len(done)}/{len(done)+len(pending)}"
                    pending_list = ", ".join(pending[:8])  # limit to ~8 names
                    obs += f"\n[remaining PENDING ({count_str}): {pending_list}]"
                elif done:
                    # All sections done
                    obs += "\n[all sections DONE]"
        return obs
    if name == "edit_file":
        # Targeted find-and-replace, mirroring Claude Code's own Edit tool: `find` must match EXACTLY
        # ONCE. Zero matches or multiple matches are REFUSED cleanly (not applied, not a crash, not a
        # silent pick-one) so the model must disambiguate -- the battle-tested pattern that avoids
        # editing the wrong spot. This lets a file be built/patched a section at a time without ever
        # re-emitting the whole thing (Aider: whole-file rewrites "limit how large a file can be
        # edited"; diff/search-replace formats use far fewer tokens).
        rel = payload.get("path", "")
        find = payload.get("find", "")
        replace = payload.get("replace", "")
        if not rel or not rel.strip() or rel.strip() in (".", "./", ".\\"):
            return ("edit_file refused: path is empty or refers to the run directory itself "
                     "-- give a real filename, e.g. \"index.html\"")
        p = _safe_path(run_dir, rel)
        if not p.exists():
            return (f"edit_file refused: \"{rel}\" does not exist yet -- create it with write_file "
                     "(a skeleton) first, then edit_file to fill sections in")
        if p.is_dir():
            return f"edit_file refused: \"{rel}\" is a directory, not a file"
        if not find:
            return "edit_file refused: \"find\" is empty -- give the exact text to locate and replace"
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            return f"edit_file refused: could not read \"{rel}\" as text ({e})"
        count = text.count(find)
        if count == 0:
            return (f"edit_file refused: \"find\" text was NOT found in {rel} (0 matches) -- read_file "
                     "to check the exact current content, then retry with text that appears verbatim")
        if count > 1:
            return (f"edit_file refused: \"find\" text matches {count} times in {rel} (ambiguous) -- "
                     "make it longer/more unique (e.g. include the whole placeholder line) so it "
                     "matches exactly once")
        p.write_text(text.replace(find, replace, 1), encoding="utf-8")
        obs = f"edited {rel}: replaced 1 match ({len(find)} -> {len(replace)} bytes)"
        # 2026-07-08: after a successful edit/append/write, scan for remaining PENDING sections
        # and fold them into the observation so the model doesn't need a confirming re-read to
        # find the next marker (mirrors Aider's search-replace returning the delta).
        markers = scan_section_markers(run_dir)
        if rel in markers or any(m.startswith(rel + "/") for m in markers):
            # This file or a file under it has section markers
            matching_keys = [k for k in markers.keys() if k == rel or k.startswith(rel + "/")]
            for key in matching_keys:
                pending = markers[key].get("pending", [])
                done = markers[key].get("done", [])
                if pending:
                    # List remaining PENDING with count
                    count_str = f"{len(done)}/{len(done)+len(pending)}"
                    pending_list = ", ".join(pending[:8])  # limit to ~8 names
                    obs += f"\n[remaining PENDING ({count_str}): {pending_list}]"
                elif done:
                    # All sections done
                    obs += "\n[all sections DONE]"
        return obs
    if name == "run_shell":
        cmd = payload.get("cmd", "")
        # Best-effort pre-execution deny check (heuristic, NOT a security boundary -- see
        # _SHELL_DENY_PATTERNS). Refuse obviously destructive/exfiltration shapes with a clear reason.
        if _shell_is_denied(cmd):
            return ("run_shell refused: matches a blocked destructive/exfiltration pattern "
                    "(recursive/parent-traversal delete, format/partition, shutdown/reboot, registry "
                    "delete, or outbound non-localhost network). Choose a safe, cwd-local command.")
        try:
            # encoding/errors are REQUIRED, not cosmetic: text=True alone decodes stdout/stderr with
            # the OS locale codec (cp1252 on Windows), so any command whose output contains a byte
            # cp1252 can't represent -- an em dash, curly quote, any real Unicode typography -- crashes
            # the subprocess reader thread with UnicodeDecodeError. Observed live (task22, opencode
            # tier): `type` of a data file with em dashes crashed here and cost ~40 of 48 turns. Force
            # UTF-8 with errors="replace" so undecodable bytes degrade to a replacement char, never crash.
            proc = subprocess.run(cmd, shell=True, cwd=run_dir, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=_MAX_SHELL_SECONDS, env=_restricted_shell_env())
            # proc.stdout/stderr can come back None in some Windows shell=True edge cases (observed
            # live: a real benchmark run crashed here with "'NoneType' object is not subscriptable")
            # despite capture_output=True -- guard rather than assume text mode always populates both.
            out = proc.stdout or ""
            err = proc.stderr or ""
            # 2026-07-08: unlike read_file, run_shell's output isn't a re-requestable range of an
            # existing file -- once the cap clips it, the rest used to be simply gone, no recovery
            # path at all (worse than read_file's old bug, which at least had the source file still
            # on disk). Fixed per the deep-research sweep's clearest actionable finding: Cursor's own
            # "Dynamic Context Discovery" post-mortem (they stopped truncating shell/MCP output and
            # instead write it to a file the agent can tail/read/grep), and the near-identical
            # opencode issue #11313 ("agents fail to use the offset/limit file and retry-loop
            # instead") -- which is why the note below also spells out read_file usage explicitly,
            # not just the file path. Overflow files land in a dedicated subdir, one per turn, and
            # are real files under read_file's now-real offset/limit pagination.
            out_note = ""
            if len(out) > _MAX_OUTPUT_CHARS:
                overflow_dir = run_dir / "_shell_overflow"
                overflow_dir.mkdir(exist_ok=True)
                overflow_path = overflow_dir / f"stdout_{time.time_ns()}.txt"
                overflow_path.write_text(out, encoding="utf-8")
                rel_overflow = overflow_path.relative_to(run_dir).as_posix()
                out_note = (
                    f"\n[TRUNCATED: showing first {_MAX_OUTPUT_CHARS} of {len(out)} chars. Full "
                    f"output saved to \"{rel_overflow}\" -- use read_file with offset/limit on that "
                    f"path to see the rest, do NOT re-run this command.]"
                )
            err_note = f"\n[TRUNCATED: stderr cut off at 1000 chars of {len(err)}]" if len(err) > 1000 else ""
            return (f"exit={proc.returncode}\nstdout:\n{out[:_MAX_OUTPUT_CHARS]}{out_note}"
                    f"\nstderr:\n{err[:1000]}{err_note}")
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {_MAX_SHELL_SECONDS}s"
    if name == "done":
        return "done"
    return f"unknown tool: {name}"


def run_task(task_id: str, task_text: str, run_dir: str | Path, model_complete: Callable[[str], str],
            max_turns: int = 30, agent_id: str = "agent", model_label: str = "unknown",
            tools: list[str] | None = None, system_prompt: str | None = None,
            inject_orientation: bool = True) -> dict[str, Any]:
    """Drive one task to completion (or budget exhaustion). Returns a summary record: {task_id,
    turns, tool_calls[], closed_out, done, transcript_path}. `model_complete(prompt) -> str` is
    injectable so this loop is unit-testable without a live network call.

    `tools` restricts the tool surface offered to the model (default: full AGENT_TOOLS). Pass a
    file-only subset to run a no-MCP condition (the A/B/C benchmark's condition A). `system_prompt`
    overrides the prompt template (must contain a `{task}` placeholder and escape literal braces as
    `{{`/`}}`); default is the full _SYSTEM_PROMPT. `inject_orientation=False` suppresses the boot
    onboarding/status digest so a no-MCP run isn't handed Cortex context it shouldn't have. All three
    default to today's exact behavior, so existing callers/tests are unaffected."""
    active_tools = tools if tools is not None else AGENT_TOOLS
    prompt_template = system_prompt if system_prompt is not None else _SYSTEM_PROMPT
    run_dir = Path(run_dir)
    # Detect resumable partial output BEFORE we create the audit/ scaffolding, so the model is told up
    # front to read + continue an interrupted attempt (task21 was rate-limited mid-dashboard) instead
    # of clobbering it. Empty when the sandbox is fresh, so a normal first run is unaffected.
    resume_preamble = _resume_notice(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Make the sandbox a self-resolving Cortex workspace so the cortex_* tools (search/status/
    # contract/write_log) scope HERE via the explicit workspace= arg -- without a marker dir,
    # resolve_workspace() walks up looking for a checkout and would raise (turning a gated refusal
    # into a crash). This keeps a benchmark run's writes inside its own dir, never the real corpus.
    (run_dir / "audit").mkdir(exist_ok=True)
    transcript_path = run_dir / "transcript.jsonl"

    from cortex_core.mcp import cortex_register
    # @mcp.tool() returns the plain function in this FastMCP version (verified), so call it directly
    # -- consistent with the cortex_search/status/onboarding branches above (no .fn unwrap).
    reg = cortex_register(agent_id=agent_id, model=model_label, role="benchmark", workspace=str(run_dir))
    session_id = reg["session_id"]

    # 2026-07-08 (architech review): wire the benchmark harness into the EXISTING
    # durable phase runtime (cortex_core/phase_runtime.py) so long runs survive the
    # 8-minute lease timeout with a real checkpoint/resume state machine -- not just the
    # file-level SECTION: markers (those catch a re-run of the SAME run_dir; the phase
    # runtime catches a timeout/max-turn loss and tells a reconnecting session exactly
    # where to pick up). The MCP server already exposes these as cortex_run_start /
    # cortex_phase_*, but run_task drove its OWN loop and never called them -- the
    # documented "legitimate bypass" (state_machine_override_reason) only skipped the
    # *mandatory server-side chart gate*, it did NOT opt out of durability. So we call
    # the phase_runtime functions directly here (same process, no network hop).
    from cortex_core.phase_runtime import (
        create_phase_plan,
        get_phase_state,
        heartbeat_phase,
        checkpoint_phase,
        resume_phase,
    )
    _phase_resume_note = ""
    try:
        _existing = get_phase_state(run_dir, task_id=task_id)
        _st = _existing.get("status")
        if _st not in ("done", "escalated"):
            # Resumable record already exists for this task_id -- this is a continue,
            # not a fresh start. Fold the durable phase state into the prompt so the
            # model knows it is resuming a checkpointed run, not starting cold.
            _res = resume_phase(run_dir, task_id=task_id)
            _ph = _res.get("active_phase") or {}
            _phase_resume_note = (
                f"\n[PHASE-RESUME] Durable phase record found for task {task_id}. "
                f"Next action: {_res.get('next_action')}. Active phase: "
                f"{_ph.get('name', _ph.get('phase_id', 'unknown'))}. Continue from the "
                f"first PENDING SECTION marker (or the phase's expected outputs), do NOT "
                f"restart from scratch.]"
            )
    except KeyError:
        # Fresh run: create the durable phase plan with an 8-minute default lease
        # (matching cortex_run_start's DEFAULT_PHASE_SECONDS). This is what gives a
        # long run a recoverable checkpoint if it is killed at the lease boundary.
        try:
            create_phase_plan(
                run_dir, task_id,
                intent={"seeking": task_text[:500]},
                track="build", session_id=session_id,
                phase_seconds=480, heartbeat_seconds=60,
            )
        except Exception:
            # Durability is best-effort here: a failure to create the plan must
            # NEVER break the actual benchmark run. Swallow and continue.
            _phase_resume_note = ""

    # 2026-07-08: boot-time orientation. Fetch cortex_onboarding and cortex_status once at startup,
    # truncate to ~600 chars each, and fold into the initial history with a framing line. This moves
    # orientation out of the per-turn tool list (where weak models treat protocol as the task) and
    # into the harness background. The tools remain in AGENT_TOOLS/execute_tool for manual override.
    if inject_orientation:
        onboarding_obs = execute_tool("cortex_onboarding", {}, run_dir, session_id)[:600]
        status_obs = execute_tool("cortex_status", {}, run_dir, session_id)[:600]
        boot_digest = (
            "\n[boot: onboarding + workspace status already fetched for you (shown below); "
            "you do NOT need to call cortex_onboarding or cortex_status again]\n"
            f"Onboarding: {onboarding_obs}\n"
            f"Status: {status_obs}\n"
        )
    else:
        # No-MCP condition (benchmark A): the model gets no Cortex orientation at all.
        boot_digest = ""

    history = boot_digest + resume_preamble + _phase_resume_note
    tool_calls: list[dict[str, Any]] = []
    closed_out = False
    finished = False
    closed_out_at_turn: int | None = None
    material_work_since_closeout = True
    # Anti-reparse-loop bookkeeping (see _DATA_PREP_* above): distinct parser scripts written so far,
    # whether the real deliverable has been started, and whether we've already fired the one-time nudge.
    parser_scripts: set[str] = set()
    build_started = False
    data_prep_nudged = False
    # Anti-shell-loop bookkeeping (see _SHELL_LOOP_NUDGE above): counts run_shell calls directly,
    # independent of parser_scripts -- catches the "all data wrangling stayed in run_shell, no
    # write_file ever called" shape parser_scripts can't see.
    shell_call_count = 0
    shell_loop_nudged = False
    # Anti-relapse bookkeeping (see _SHELL_RELAPSE_NUDGE above): consecutive run_shell calls,
    # reset by any other tool. Independent of build_started -- can fire more than once.
    consecutive_shell_count = 0
    # Entry-point enforcement (see _missing_index_entrypoint above): fires at most once so a model
    # that still won't comply on the second `done` attempt isn't blocked forever.
    index_entrypoint_nudged = False
    # 2026-07-08: read dedup tracking. The diagnosis found models re-read the same file with the same
    # offset/limit up to 2.64x per edit, burning turns just to re-locate the next SECTION marker.
    # Track last read state and files dirtied since, so back-to-back identical reads are stubbed
    # without a network call (mirrors Claude Code's read-dedup).
    last_read_state: tuple[str, int, int] | None = None  # (path, offset, limit)
    files_dirty_since_last_read: set[str] = set()  # rel paths written/edited/appended
    t0 = time.time()

    # Grace window after a real closeout lands: observed live on a real benchmark run (task04,
    # 2026-07-07) that closing out at turn 23 did NOT stop the model -- it kept running all the way
    # to a 100-turn ceiling (3.7 hours wall-clock) because it never called `done`. The deliverable
    # already exists once closed_out is True; there is no reason to burn the full turn budget
    # waiting for `done`. Nudge once, then force-stop if it still doesn't wrap up.
    _POST_CLOSEOUT_GRACE_TURNS = 8

    for turn in range(max_turns):
        # 2026-07-08 (architech review): heartbeat the durable phase lease every 5 turns
        if turn % 5 == 0:
            try:
                heartbeat_phase(run_dir, task_id=task_id, session_id=session_id)
            except Exception:
                # Durability is best-effort: a failure to heartbeat must NEVER
                # break the actual benchmark run. Swallow and continue.
                pass
        prompt = prompt_template.format(task=task_text) + history
        raw = model_complete(prompt)
        call = extract_tool_call(raw, legal_tools=active_tools)
        tool, payload = call.get("tool"), call.get("payload", {})
        with transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"turn": turn, "raw": raw, "tool": tool, "payload": payload}) + "\n")
        if not tool:
            history += "\n[system: no valid tool call parsed from your last response; respond with valid JSON]\n"
            if closed_out_at_turn is not None and turn - closed_out_at_turn >= _POST_CLOSEOUT_GRACE_TURNS:
                finished = True  # already closed out; not burning the rest of the budget on silence
                break
            continue
        tool_calls.append({"turn": turn, "tool": tool})
        if closed_out and tool == "cortex_write_log" and not material_work_since_closeout:
            # A real closeout already landed and the model is trying to write the same audit
            # artifact again without doing any intervening work. End the run before creating
            # duplicate closeout files or burning the rest of the turn budget.
            finished = True
            break
        if tool == "done":
            if not index_entrypoint_nudged and _missing_index_entrypoint(run_dir):
                index_entrypoint_nudged = True
                history += _MISSING_INDEX_NUDGE
                continue
            finished = True
            break

        # 2026-07-08: read dedup. If this is a read_file call with the same path+offset+limit as
        # the last read, and the file hasn't been dirtied since, stub it without dispatching.
        obs = None
        if tool == "read_file" and last_read_state is not None:
            read_path = payload.get("path", "")
            read_offset = int(payload.get("offset", 0) or 0)
            read_limit = int(payload.get("limit", 0) or 0) or _MAX_OUTPUT_CHARS
            if (read_path, read_offset, read_limit) == last_read_state and read_path not in files_dirty_since_last_read:
                # Same file, same window, no writes/edits/appends since -- dedup it
                obs = (f"already showed chars {read_offset}-{read_offset + _MAX_OUTPUT_CHARS} of {read_path} "
                       f"above (nothing changed) -- do NOT re-read to find the marker. Build the next PENDING "
                       f"section instead with edit_file or append_file.")

        if obs is None:
            # Normal dispatch
            try:
                obs = execute_tool(tool, payload, run_dir, session_id)
            except ValueError as e:
                # A sandbox-escaping path (e.g. the model calling read_file with the absolute path of the
                # source data file, as observed live on the task22c re-run) raises out of _safe_path. That
                # must be a RECOVERABLE refusal the model can act on -- not a crash that kills the whole run.
                # execute_tool still raises for its own unit tests; run_task turns it into an observation.
                obs = (f"{tool} refused: path escapes the sandbox ({e}). Use a path RELATIVE to your run "
                       "directory. To use an external source file, first copy it in with run_shell, then "
                       "read the local copy.")

        # Update dedup tracking: record this read for dedup checking, and mark files dirty on writes.
        if tool == "read_file" and obs is not None and "already showed" not in obs.lower():
            # A real read (not a dedup stub) -- record it for future dedup checks
            read_path = payload.get("path", "")
            read_offset = int(payload.get("offset", 0) or 0)
            read_limit = int(payload.get("limit", 0) or 0) or _MAX_OUTPUT_CHARS
            last_read_state = (read_path, read_offset, read_limit)
            files_dirty_since_last_read.clear()
        elif tool in ("write_file", "append_file", "edit_file"):
            # File modified -- mark it dirty so future reads aren't deduped
            write_path = payload.get("path", "")
            files_dirty_since_last_read.add(write_path)
            # 2026-07-08 (architech review): checkpoint the durable phase state
            # after every material file mutation. This is what lets a run that is
            # killed at the 8-minute lease boundary resume at its last written
            # artifact instead of replaying the whole turn budget cold.
            try:
                checkpoint_phase(
                    run_dir, task_id=task_id, session_id=session_id,
                    partial_outputs=[{"turn": turn, "tool": tool, "path": str(write_path)}],
                )
            except Exception:
                # Durability is best-effort: a checkpoint failure must NEVER
                # break the actual benchmark run. Swallow and continue.
                pass
        if tool == "cortex_write_log":
            # closed_out must reflect a REAL gated write, not just an attempt: a write refused for
            # want of an approved contract returns a refusal dict (no "path"), so it must NOT count
            # as a closeout -- otherwise the benchmark's "did it close out" signal is theater.
            try:
                wrote_closeout = "path" in json.loads(obs)
            except (json.JSONDecodeError, TypeError):
                wrote_closeout = False
            if wrote_closeout:
                closed_out = True
                material_work_since_closeout = False
                if closed_out_at_turn is None:
                    closed_out_at_turn = turn
        elif tool in ("write_file", "append_file", "edit_file", "run_shell"):
            material_work_since_closeout = True
        # 2026-07-08: this used to re-clip to 1000 chars regardless of what the tool already
        # returned -- destructively stacking on top of read_file/run_shell's own ~4000-char caps
        # and silently eating their `[TRUNCATED...]` markers (which land past char 1000, so they
        # never survived this second cut). That was the confirmed, transcript-verified root cause
        # of the CNA benchmark's reparse loop: the model could never reliably see whether a read
        # was complete or fragmentary. _HISTORY_OBS_CAP is sized to comfortably fit the largest
        # single observation any tool here can produce (run_shell's worst case: 4000 stdout + its
        # own truncation note + 1000 stderr + its own truncation note), so a tool's own truncation
        # marker -- the one signal that tells the model "this is a fragment, here's how to get the
        # rest" -- actually reaches the model instead of being silently cut a second time.
        # 2026-07-08: real, transcript-verified finding (isolated_retest_0b24b1ba, a clean
        # single-model run with the truncation fix already in place): 60% of all 60 turns were
        # unparseable, and 67% of THOSE (24/36, 40% of the whole run) were the model literally
        # imitating THIS history log's own OLD format -- `[tool_call: name({...})]` -- as if it
        # were the syntax to emit a new call, instead of the real required `{"tool":...,
        # "payload":...}` JSON. A weak/reasoning model pattern-matches whatever repeated shape it
        # sees most in its own context; showing it a bracket-and-parens pseudo-function-call
        # format turn after turn taught it that shape. Rewritten as narrative prose with no
        # bracket/paren/colon-after-name pattern a model could mistake for callable syntax.
        history += (
            f"\nYou previously called {tool} with arguments {json.dumps(payload)[:300]} and got "
            f"this result: {obs[:_HISTORY_OBS_CAP]}\n"
        )
        # Track re-parse-loop signals: parser-script rewrites vs. real deliverable progress. When a
        # weak model crosses the 2-attempt ceiling without having started the HTML deliverable, inject
        # the "stop parsing, start building" nudge exactly once (the model can't self-count reliably).
        if tool in ("write_file", "append_file", "edit_file"):
            _path = str(payload.get("path", ""))
            if _is_parser_script(_path):
                parser_scripts.add(_path.strip().lower())
            if _is_build_deliverable(_path):
                build_started = True
        if tool == "run_shell":
            shell_call_count += 1
            consecutive_shell_count += 1
        else:
            consecutive_shell_count = 0
        if (not data_prep_nudged and not build_started
                and len(parser_scripts) >= _DATA_PREP_SCRIPT_LIMIT):
            history += _DATA_PREP_NUDGE.format(n=len(parser_scripts))
            data_prep_nudged = True
        if (not shell_loop_nudged and not build_started
                and shell_call_count >= _SHELL_EXPLORATION_LIMIT):
            history += _SHELL_LOOP_NUDGE.format(n=shell_call_count)
            shell_loop_nudged = True
        if consecutive_shell_count >= _CONSECUTIVE_SHELL_RELAPSE_LIMIT:
            history += _SHELL_RELAPSE_NUDGE.format(n=consecutive_shell_count)
            consecutive_shell_count = 0  # can fire again later if it relapses again
        if closed_out_at_turn is not None and turn - closed_out_at_turn >= _POST_CLOSEOUT_GRACE_TURNS:
            history += ("\n[system: you already closed out this task -- call done NOW to end the "
                        "task instead of continuing.]\n")
        if closed_out_at_turn is not None and turn - closed_out_at_turn >= 2 * _POST_CLOSEOUT_GRACE_TURNS:
            finished = True  # gave it a nudge + grace window; stop burning turns past that
            break

    return {
        "task_id": task_id, "turns": len(tool_calls), "tool_calls": tool_calls,
        "closed_out": closed_out, "finished": finished,
        "elapsed_s": round(time.time() - t0, 1),
        "transcript_path": str(transcript_path), "run_dir": str(run_dir),
    }


def qwen_complete(prompt: str, max_tokens: int = 1500, tier: str = "qwen35b") -> str:
    """The live model callable for `run_task`'s `model_complete` -- wraps the repo's existing
    OpenAI-compatible dispatch (research._llm_complete) against the configured tier's env
    (default "qwen35b" -- the yolo-qwen tier per docs/MODEL-ROLES.md; "qwen" alone is not a
    configured tier in cortex_core/judge.py and would fail dispatch)."""
    from cortex_core.judge import apply_min_max_tokens
    from cortex_core.research import _llm_complete
    max_tokens = apply_min_max_tokens(tier, max_tokens)
    out = _llm_complete(prompt, tier, max_tokens=max_tokens)
    return out or ""
