"""GAP G1 — the self-learning oracle-mining loop, keyed to Cortex's OWN tasks.

Replay past FAILED closeouts (`audit/audit-log-*/agent/*.json`) against a
DETERMINISTIC check — never a judge — to mint local gold / anti-patterns. This is
the main-repo port of the wrapper's stdlib `oracle_miner.py`
(`d:/claude/cortex-agent-wrapper/.cortex/scripts/oracle_miner.py`) and honors the
SAME fixed rule (`LOCAL-DATA-SETUP.md` step 3):

  * a task that deterministically FAILED whose LATER attempt PASSES its recorded
    tests  -> `positive`   (the fix worked; that passing closeout is the local gold).
  * a task that FAILED and NEVER reaches a passing attempt
           -> `anti_pattern`.
  * a task with NO deterministic outcome to decide on
           -> `UNVERIFIABLE` (quarantined; NEVER guessed — the anti-oracle rule).

The one adaptation from the wrapper: the main-repo closeout schema
(`cortex_core/audit.py`) has NO `tests_passed` bool. `tests` is a free-text string
and (schema v2+) there is a structured `evidence[]` array. `test_outcome()` derives
the deterministic pass/fail from those with an explicit precedence and records WHICH
signal decided it (provenance), so a human can audit every verdict. Undecidable ->
`None` -> quarantined, never a guessed bool.

HARD invariants (frozen by tests/test_self_learning.py):
  * No LLM / judge / network in the verdict path — pure deterministic parsing.
    (This is the same trust order as the `evals/` objective lanes: a deterministic
    checker is ground truth; a judge is never in an objective verdict path.)
  * Nothing is auto-promoted. Output is a QUARANTINED JSONL of candidates, each
    stamped `promoted: false` / `promotion_status: "quarantined"`. Promotion to
    trainable gold is a separate, human-gated step (`cortex_core/promotion.py` —
    `hard_gold` needs an objective checker, not this miner's say-so). This module
    deliberately does NOT import or call promotion.

Stdlib only. Offline. CLI-only (no MCP tool — anti-bloat).

CLI:
    cortex-self-learning                       # mine this workspace's closeouts
    cortex-self-learning --print               # summary to stdout, write nothing
    cortex-self-learning --closeouts-dir DIR --out FILE
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

POSITIVE = "positive"
ANTI_PATTERN = "anti_pattern"
UNVERIFIABLE = "UNVERIFIABLE"

# Default quarantine sink (relative to the workspace). Deliberately NOT under any
# trainable-gold location and NOT indexed as corpus — these are unverified candidates.
DEFAULT_OUT_REL = "audit/self-learning/oracle_candidates.jsonl"

# --------------------------------------------------------------------------- deterministic signals
# A closeout status that is itself a decisive TEST-FAILURE verdict. Deliberately
# NARROW: "blocked"/"aborted"/"in-progress" mean the task never reached a test
# verdict for an EXTERNAL reason (a down endpoint, a dependency) — that is
# absence-of-signal (-> UNVERIFIABLE), not a bad approach. Blaming a blocked task as
# an anti_pattern would be a wrong label; the anti-oracle rule says quarantine it.
_FAIL_STATUS = frozenset({"failed", "failure", "fail", "error", "errored"})

# Explicit failure phrasings in the free-text `tests` field. Deliberately
# CONSERVATIVE: a failure token must be UNAMBIGUOUS (a nonzero failed/error COUNT, a
# "tests failed/failing" phrase, an explicit non-pass verdict, or a nonzero exit
# code). A bare "fail"/"failure" substring is NOT a signal — real closeouts say
# things like "1 pre-existing unrelated ... failure" while REPORTING A PASS, and a
# FALSE anti_pattern is a wrong label (worse than an honest UNVERIFIABLE). When in
# doubt we do NOT declare failure; the task simply isn't mined.
_FAIL_RE = re.compile(
    r"([1-9]\d*\s+(?:tests?\s+)?fail(?:ed|ing)?"   # "3 failed", "3 tests failing"
    r"|[1-9]\d*\s+errors?"                          # "2 errors" (nonzero only; not "0 errors")
    r"|tests?\s+fail(?:ed|ing)?"                    # "test failed", "tests failing"
    r"|did\s+not\s+pass"
    r"|exit\s*(?:code)?\s*[1-9]\d*)",              # nonzero exit code
    re.IGNORECASE,
)

# Explicit PASS phrasings. Broad on purpose (a "passed"/"passing"/"green"/"exit 0"
# token) — but FAILURE is checked first and wins, so a mixed "3 failed, 14 passed"
# is still a failure. `\b` keeps "pass" out of "bypass".
_PASS_RE = re.compile(
    r"(\bpass(?:ed|ing|es)?\b"
    r"|\bgreen\b"
    r"|exit\s*(?:code)?\s*0)",
    re.IGNORECASE,
)

# A test ratio is only trusted when it's UNAMBIGUOUS: either adjacent to a test
# keyword (e.g. "6/6 tests", "tests: 2/17"), or a bare all-pass shorthand "N/N"
# (equal, nonzero). A bare UNEQUAL ratio (e.g. a length list "0/1/10/100", a date,
# a path) is NOT read as a failure — it returns None (never guessed).
_RATIO_KW = re.compile(
    r"(?:(\d+)\s*/\s*(\d+)\s*(?:tests?|passed|passing|green|checks?|cases?|ok)"
    r"|(?:tests?|passed|passing|green|checks?)\s*[:=]?\s*(\d+)\s*/\s*(\d+))",
    re.IGNORECASE,
)
_RATIO_BARE = re.compile(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])")
_EXIT_RE = re.compile(r"(?:exit|return)[\s:_-]*(?:code[\s:_-]*)?(\d+)", re.IGNORECASE)


def _ratio_kw_signal(tests: str) -> Optional[bool]:
    """A keyword-adjacent ratio (`6/6 tests`, `tests: 2/17`) is a PRECISE verdict:
    num>=den -> pass, else fail. Absent/`N/0` -> None."""
    m = _RATIO_KW.search(tests)
    if not m:
        return None
    nums = [int(x) for x in m.groups() if x is not None]
    num, den = nums[0], nums[1]
    return None if den == 0 else num >= den


def _ratio_bare_pass(tests: str) -> bool:
    """A bare `N/N` (equal, nonzero) is an all-pass shorthand ("TDD 6/6", "9/9").
    A bare UNEQUAL ratio (a length list `0/1/10/100`, a date, a path) is NOT a
    signal — never guessed as a failure."""
    m = _RATIO_BARE.search(tests)
    if not m:
        return False
    num, den = int(m.group(1)), int(m.group(2))
    return den > 0 and num == den


def _exit_code_from_evidence(item: dict[str, Any]) -> Optional[int]:
    """Pull an EXPLICIT numeric exit/return code out of a v2 `evidence` item
    (type=='test') — `exit 0`, `exit code 1`, `returncode 2`. Also honors a
    structured boolean `passed`/`ok` flag if present. Deliberately does NOT fall
    back to a bare "pass"/"fail" WORD in the free-text detail: a detail legitimately
    reads "12 passed; 1 pre-existing unrelated failure" while the task PASSED, so a
    substring match would mint a fake failure. No explicit code -> None (fall through
    to the tests-string layer / undecidable)."""
    for key in ("passed", "ok", "success"):
        v = item.get(key)
        if isinstance(v, bool):
            return 0 if v else 1
    for v in (item.get("ref"), item.get("detail"), item.get("exit_code"), item.get("returncode")):
        if v is None:
            continue
        if isinstance(v, int):
            return v
        m = _EXIT_RE.search(str(v))
        if m:
            return int(m.group(1))
    return None


def test_outcome(rec: dict[str, Any]) -> tuple[Optional[bool], str]:
    """Derive a DETERMINISTIC pass/fail for one closeout, with provenance.

    Returns ``(outcome, signal)`` where outcome is ``True``/``False``/``None`` and
    signal names which deterministic source decided it. Precedence:
      1. ``test_evidence_exit`` — a v2 structured `evidence` test exit code (strongest,
         closest to the wrapper's recorded test exit codes).
      2. ``status_fail`` — an authoritative failure `status`.
      3. ``ratio`` — a precise keyword-adjacent `N/M tests` ratio.
      4. ``fail_signal`` / ``pass_signal`` — a one-sided token in the `tests` prose.
      5. ``ambiguous_mixed`` — BOTH a pass and a fail token in the prose (e.g.
         "771 passed, 1 failed (pre-existing)") — a regex can't tell a real partial
         failure from a pass with an unrelated caveat, so this is UNDECIDABLE and
         returns None. NEVER guessed.
      6. ``none`` — no signal at all; UNVERIFIABLE.
    """
    # 1. Structured test evidence (exit code) — the machine signal.
    for item in rec.get("evidence") or []:
        if isinstance(item, dict) and str(item.get("type", "")).lower() == "test":
            code = _exit_code_from_evidence(item)
            if code is not None:
                return (code == 0, "test_evidence_exit")

    # 2. An authoritative failure status.
    status = str(rec.get("status") or "").strip().lower()
    if status in _FAIL_STATUS:
        return (False, "status_fail")

    tests = str(rec.get("tests") or "")

    # 3. A precise keyword-adjacent ratio beats loose prose tokens.
    kw = _ratio_kw_signal(tests)
    if kw is not None:
        return (kw, "ratio")

    # 4/5. One-sided prose is decisive; a MIXED pass+fail line is undecidable.
    tests_fail = bool(_FAIL_RE.search(tests))
    tests_pass = bool(_PASS_RE.search(tests)) or _ratio_bare_pass(tests)
    if tests_fail and tests_pass:
        return (None, "ambiguous_mixed")   # both signals -> can't decide, never guessed
    if tests_fail:
        return (False, "fail_signal")
    if tests_pass:
        return (True, "pass_signal")

    return (None, "none")


# --------------------------------------------------------------------------- grouping / loading
def task_key(task: str) -> str:
    """Normalize a task title so repeated attempts at the same task group together
    (same normalization as the wrapper's miner)."""
    return re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-") or "untitled"


def _sort_key(rec: dict[str, Any]) -> str:
    # timestamp is ISO-8601, so a string sort is chronological. Empty sorts first.
    return str(rec.get("timestamp") or "")


def load_closeouts(closeouts_dir: str | Path) -> list[dict[str, Any]]:
    """Load every closeout `.json` under a dir (recursively). Skips unreadable files
    and any JSON without a `task` (not a closeout). Tags each with `_source`."""
    root = Path(closeouts_dir)
    records: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or "task" not in data:
            continue
        data.setdefault("_source", str(p))
        records.append(data)
    return records


def _group(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        groups.setdefault(task_key(str(rec.get("task", ""))), []).append(rec)
    return groups


def _candidate(key: str, recs: list[dict[str, Any]], label: str, reason: str,
               outcomes: list[Optional[bool]], signals: list[str],
               fix_source: Optional[str]) -> dict[str, Any]:
    return {
        "task_key": key,
        "task": recs[-1].get("task"),
        "label": label,
        "reason": reason,
        "attempts": len(recs),
        "outcomes": outcomes,
        "outcome_signals": signals,
        "fix_source": fix_source,          # positive only: the passing closeout (local gold)
        "sources": [r.get("_source") for r in recs],
        "timestamps": [r.get("timestamp") for r in recs],
        "contract_ids": [r.get("contract_id") for r in recs],
        # Explicit, machine-checkable: this is a QUARANTINED suggestion. Promotion to
        # trainable gold is a separate, human-gated step (cortex_core/promotion.py).
        "promoted": False,
        "promotion_status": "quarantined",
        "mined_by": "cortex-self-learning",
    }


# --------------------------------------------------------------------------- classification
def classify(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by task, then label every task that has at least one deterministic
    FAILURE. A task that only ever passed (or was never decisively failed) is not a
    failure->fix oracle and is skipped here (quarantine of undecidable-only tasks is
    added by `mine`)."""
    candidates: list[dict[str, Any]] = []
    for key, recs in sorted(_group(records).items()):
        recs = sorted(recs, key=_sort_key)
        pairs = [test_outcome(r) for r in recs]
        outcomes = [o for o, _ in pairs]
        signals = [s for _, s in pairs]

        if not any(o is False for o in outcomes):
            continue  # never deterministically failed -> not a failure/fix oracle.

        if any(o is True for o in outcomes):
            # the FIRST passing attempt after a failure is the fix whose CoT is gold.
            fix_idx = next(i for i, o in enumerate(outcomes) if o is True)
            candidates.append(_candidate(
                key, recs, POSITIVE,
                "failed then a later attempt passed its recorded tests (fix verified)",
                outcomes, signals, recs[fix_idx].get("_source")))
        else:
            # real failing outcomes, no passing one -> anti-pattern (undecidable
            # attempts are NEVER guessed toward a pass).
            candidates.append(_candidate(
                key, recs, ANTI_PATTERN,
                "failed with recorded tests and never reached a passing attempt",
                outcomes, signals, None))
    return candidates


def mine(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`classify` PLUS an explicit UNVERIFIABLE quarantine for tasks whose attempts
    carry NO deterministic signal at all — so undecidable work is surfaced, never
    silently dropped and never guessed."""
    out = list(classify(records))
    seen = {c["task_key"] for c in out}

    for key, recs in sorted(_group(records).items()):
        if key in seen:
            continue
        recs = sorted(recs, key=_sort_key)
        pairs = [test_outcome(r) for r in recs]
        outcomes = [o for o, _ in pairs]
        signals = [s for _, s in pairs]
        if any(o is True or o is False for o in outcomes):
            continue  # a decisive (pass-only) task; not a quarantine, just not an oracle.
        out.append(_candidate(
            key, recs, UNVERIFIABLE,
            "no deterministic test outcome recorded — cannot decide (never guessed)",
            outcomes, signals, None))
    return out


# --------------------------------------------------------------------------- auto-wiring (GAP G1)
# The flywheel: a closeout WRITE (or a deterministic gate failure) auto-mints a QUARANTINED
# pattern candidate -- no CLI, no manual mine() call. The mint is gated by the deterministic
# regression oracle above (`test_outcome`/`classify`): only a decisively positive/anti_pattern
# task produces a candidate; UNVERIFIABLE mints nothing (never guessed). A candidate is a
# suggestion in quarantine, never an active pattern (authoring a detection recipe stays a
# human step) -- so this is never a free-form self-edit.
GATE_FAILURES_REL = ("audit", "self-learning", "gate_failures.jsonl")


def _task_group_records(workspace: str | Path, task: str) -> list[dict[str, Any]]:
    """Load only the on-disk closeouts for ONE task's subject (slug-scoped glob), so the
    hook is O(matching files) rather than reading every closeout on every write. Mirrors
    write_policy.load_candidate_records' filename-slug trick."""
    from .audit import _slugify

    slug = _slugify(task)
    root = Path(workspace) / "audit"
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for shard in sorted(root.glob("audit-log-*")):
        agent = shard / "agent"
        if not agent.is_dir():
            continue
        for f in agent.glob(f"cortex-closeout__*-{slug}__*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and "task" in data:
                data.setdefault("_source", str(f))
                records.append(data)
    return records


def on_closeout(record: dict[str, Any], workspace: str | Path) -> list[dict[str, Any]]:
    """Auto-hook fired by the closeout WRITE path (`audit.write_closeout`). Deterministically
    re-mines the just-written closeout's task group and UPSERTs a quarantined pattern candidate
    for THIS task if -- and only if -- the deterministic oracle reaches a positive/anti_pattern
    verdict. Returns the list of minted candidates (empty when the task is UNVERIFIABLE, so the
    flywheel never guesses). Pure of any judge/network. Callers wire this fail-open."""
    from . import patterns

    task = str(record.get("task") or "")
    key = task_key(task)
    group = _task_group_records(workspace, task)
    # The freshly-written record is already on disk, but include the in-memory copy defensively
    # (e.g. if the write hasn't flushed) so the verdict never misses the triggering closeout.
    if not any(r.get("_source") == record.get("_source") for r in group):
        group = group + [record]
    group = [r for r in group if task_key(str(r.get("task", ""))) == key]

    minted: list[dict[str, Any]] = []
    for cand in classify(group):
        if cand["task_key"] == key:
            minted.append(patterns.mint_pattern_candidate(cand, workspace))
    return minted


def record_gate_failure(
    workspace: str | Path,
    *,
    gate: str,
    tool: str = "",
    detail: str = "",
    session_id: str = "",
    mint: bool = True,
) -> dict[str, Any]:
    """Record a DETERMINISTIC gate failure (a gate refusing IS an objective regression signal,
    not a judgement) to ``audit/self-learning/gate_failures.jsonl`` -- the ledger G1 flagged as
    perpetually unpopulated -- and, by default, mint a quarantined anti_pattern candidate from
    it. Returns ``{"recorded": <row>, "candidate": <candidate|None>}``. Stdlib-only, judge-free,
    fail-open-friendly (the caller wires it so a ledger hiccup never blocks the gate itself)."""
    from . import patterns

    row = {
        "kind": "gate_failure",
        "gate": gate,
        "tool": tool,
        "detail": detail,
        "session_id": session_id,
        "timestamp": _iso_now(),
    }
    path = Path(workspace).joinpath(*GATE_FAILURES_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    candidate = None
    if mint:
        gate_task = f"gate:{gate}" + (f"/{tool}" if tool else "")
        candidate = patterns.mint_pattern_candidate(
            {
                "task_key": task_key(gate_task),
                "task": gate_task,
                "label": ANTI_PATTERN,
                "reason": f"deterministic gate failure ({gate})"
                          + (f" on {tool}" if tool else "") + (f": {detail}" if detail else ""),
                "attempts": 1,
                "outcomes": [False],
                "outcome_signals": ["gate_failure"],
                "fix_source": None,
                "sources": [str(path)],
                "mined_by": "cortex-self-learning:gate",
            },
            workspace,
        )
    return {"recorded": row, "candidate": candidate}


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def write_candidates(candidates: list[dict[str, Any]], out_path: str | Path) -> int:
    """Write candidates as a quarantined JSONL. Returns the count written."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for c in candidates:
            fh.write(json.dumps(c) + "\n")
    return len(candidates)


def _counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {POSITIVE: 0, ANTI_PATTERN: 0, UNVERIFIABLE: 0}
    for c in candidates:
        counts[c["label"]] = counts.get(c["label"], 0) + 1
    return counts


# --------------------------------------------------------------------------- CLI
def _default_closeouts_dir(workspace: Path) -> Path:
    return workspace / "audit"


def main(argv: list[str] | None = None) -> int:
    try:
        from .config import make_stdio_encoding_safe, resolve_workspace
        make_stdio_encoding_safe()
    except Exception:  # pragma: no cover - config is always importable in-repo
        resolve_workspace = None  # type: ignore[assignment]

    ap = argparse.ArgumentParser(
        prog="cortex-self-learning",
        description="Mine FAILED Cortex closeouts into quarantined positive/anti_pattern/"
                    "UNVERIFIABLE oracle candidates (deterministic; never auto-promoted).")
    ap.add_argument("--closeouts-dir", default=None,
                    help="dir of closeout .json files (searched recursively; "
                         "default: <workspace>/audit)")
    ap.add_argument("--workspace", default=None,
                    help="workspace root (default: resolved via CORTEX_WORKSPACE)")
    ap.add_argument("--out", default=None,
                    help=f"quarantine JSONL to write (default: <workspace>/{DEFAULT_OUT_REL})")
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="print a summary to stdout and write nothing")
    args = ap.parse_args(argv)

    if args.workspace:
        workspace = Path(args.workspace)
    elif resolve_workspace is not None:
        workspace = Path(resolve_workspace())
    else:  # pragma: no cover
        workspace = Path.cwd()

    closeouts_dir = Path(args.closeouts_dir) if args.closeouts_dir else _default_closeouts_dir(workspace)
    records = load_closeouts(closeouts_dir)
    candidates = mine(records)
    counts = _counts(candidates)

    if args.print_only:
        for c in candidates:
            print(f"{c['label']:12} {c['task_key']}  ({c['reason']})")
        print(f"# {len(records)} closeouts -> {len(candidates)} candidates: "
              f"{counts[POSITIVE]} positive, {counts[ANTI_PATTERN]} anti_pattern, "
              f"{counts[UNVERIFIABLE]} UNVERIFIABLE", file=sys.stderr)
        return 0

    out_path = Path(args.out) if args.out else (workspace / DEFAULT_OUT_REL)
    n = write_candidates(candidates, out_path)
    print(f"wrote {n} quarantined candidates to {out_path} "
          f"({counts[POSITIVE]} positive, {counts[ANTI_PATTERN]} anti_pattern, "
          f"{counts[UNVERIFIABLE]} UNVERIFIABLE) — NOT promoted; promotion is human-gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
