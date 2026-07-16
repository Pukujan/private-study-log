"""GAP G4 (P1): reconcile-on-write + an explicit memory-write policy.

The problem (docs/GAP-CLOSURE-PLAN.md row G4): the closeout write path is
append-only and its gates default OFF, so *anything* can be stored blindly --
a memory-poisoning exposure. The fix, per the Mem0 pattern, is two deterministic,
stdlib-only guards that make the store a **decision procedure with a stated
security boundary**, not a blind log:

1. **memory-write policy** (`check_write_policy`) -- a security check on the
   INPUT ("validate inputs, not writes", per the plan). It states what MAY be
   stored: an allow/deny boundary that rejects prompt-injection-shaped content,
   role/prompt delimiters, and oversized/empty subjects *before* they reach the
   permanent audit trail.

2. **reconcile-on-write** (`reconcile`) -- the Mem0 ADD / UPDATE / DELETE / NOOP
   decision. Instead of blindly appending, a write is compared against what is
   already stored for the same subject and gets a decision:
     - ADD    -- a genuinely new subject
     - NOOP   -- an exact duplicate already on record (no write)
     - UPDATE -- the same subject with a *different* result: supersede the prior
                 record (append-with-supersede -- the old file is retained for
                 audit integrity, its validity closed by the new one, matching the
                 "supersede don't delete" fact-validity pattern of G3)
     - DELETE -- an explicit retraction of a stored subject (tombstone-by-append)

Both guards are pure functions over already-loaded records; `evaluate_write`
composes them with a cheap slug-prefiltered candidate loader so the MCP write
path (cortex_write_log) can call one function. Integration is deliberately scoped
to the MCP write tool: the session-less CLI write path (audit.write_closeout /
`cortex write-log`) is left exactly as-is, matching the existing gate trust model.

Honest debt: duplicate/contradiction detection is *lexical* (normalized
whitespace/case-insensitive subject + result comparison), not semantic -- a
paraphrased duplicate is not yet caught as a NOOP, and a same-subject write with
a differently-worded but equivalent result is treated as an UPDATE. Deepening this
to embedding-level similarity is the obvious next step (the vector leg already
exists in cortex_core/search.py) but is out of scope for this stdlib-only,
deterministic first cut.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Mem0-style reconcile actions ------------------------------------------------------------
ADD = "ADD"
UPDATE = "UPDATE"
DELETE = "DELETE"
NOOP = "NOOP"


# --- memory-write policy: the input allow/deny boundary --------------------------------------

# Size ceilings. `task` is a short subject/title (the memory key); a multi-KB "title" is either
# a mistake or a stuffing attempt. `result` is prose and legitimately long, so its ceiling only
# guards against pathological blobs, not normal closeouts.
_MAX_TASK_CHARS = 2000
_MAX_RESULT_CHARS = 200_000

# High-signal prompt-injection markers. Kept deliberately tight (whole directives / literal
# delimiters) so a genuine closeout is very unlikely to trip them: the cost of a false negative
# here is a poisoned permanent memory, but the cost of a false positive is only that one closeout
# must paraphrase. See the honest-debt note in the module docstring.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:your\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
               r"(?:instructions|prompts?|messages?|context)", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+|any\s+)?(?:your\s+|the\s+)?(?:previous|prior|above|earlier|system)\s+"
               r"(?:instructions|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"forget\s+(?:everything|all|your)\s+(?:you|above|previous|prior|instructions)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\b|an\b|in\b|no\s+longer)", re.IGNORECASE),
    re.compile(r"(?:new|updated)\s+system\s+prompt\s*[:=]", re.IGNORECASE),
    re.compile(r"override\s+(?:your|the|all)\s+(?:safety|instructions|guardrails|rules|policy|policies)",
               re.IGNORECASE),
    # Literal chat/role delimiters that only appear when someone is trying to forge turns.
    re.compile(r"<\|\s*(?:im_start|im_end|system|endoftext)\s*\|>", re.IGNORECASE),
    re.compile(r"</?\s*(?:system|assistant)\s*>", re.IGNORECASE),
    re.compile(r"\[/?INST\]", re.IGNORECASE),
]


@dataclass
class PolicyResult:
    allowed: bool
    violations: list[str] = field(default_factory=list)


def check_write_policy(
    task: Any,
    result: Any,
    *,
    tests: str = "",
    scripts: str = "",
    max_task_chars: int = _MAX_TASK_CHARS,
    max_result_chars: int = _MAX_RESULT_CHARS,
) -> PolicyResult:
    """The memory-write policy: decide whether this input MAY be stored.

    A security check on the *input* (not the write) -- the memory-poisoning boundary. Returns a
    `PolicyResult`; `allowed=False` means the caller must refuse the write. Deterministic and
    stdlib-only. Shape (task/result must be strings) is validated upstream by
    `_validate_closeout_shape`; this focuses on content/size, tolerating non-strings defensively.
    """
    violations: list[str] = []
    task_s = task if isinstance(task, str) else str(task or "")
    result_s = result if isinstance(result, str) else str(result or "")

    if not task_s.strip():
        violations.append("empty subject: a stored memory must have a non-empty task/subject")
    if len(task_s) > max_task_chars:
        violations.append(
            f"subject too long ({len(task_s)} chars > {max_task_chars}): the task is a short "
            "subject/title, not the payload -- suspiciously large (size/stuffing guard)"
        )
    if len(result_s) > max_result_chars:
        violations.append(
            f"result too long ({len(result_s)} chars > {max_result_chars}): refusing to store "
            "a pathologically large blob (size guard)"
        )

    haystack = f"{task_s}\n{result_s}\n{tests or ''}\n{scripts or ''}"
    for pat in _INJECTION_PATTERNS:
        m = pat.search(haystack)
        if m:
            violations.append(
                f"prompt-injection marker in the input ({m.group(0).strip()!r}): a stored memory "
                "must not carry instructions that try to override a future reader/agent"
            )
            break  # one is enough to reject; don't enumerate every marker

    return PolicyResult(allowed=not violations, violations=violations)


# --- reconcile-on-write: ADD / UPDATE / DELETE / NOOP ----------------------------------------

# Explicit retraction markers on the SUBJECT: a deliberate "unsay this" signal, not normal work.
_RETRACT_RE = re.compile(r"^\s*(?:retract|delete|revoke|unsay)\b[:\-\s]", re.IGNORECASE)


def _norm(text: Any) -> str:
    """Whitespace-collapsed, case-folded normalization for lexical equality of a subject/result."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _retract_subject(task: str) -> str:
    """The real subject a retraction targets, with the RETRACT:/DELETE: prefix stripped."""
    return _RETRACT_RE.sub("", str(task or ""), count=1)


@dataclass
class ReconcileDecision:
    action: str  # ADD | UPDATE | DELETE | NOOP
    reason: str
    supersedes: list[str] = field(default_factory=list)  # _file paths being superseded/deleted
    target: dict[str, Any] | None = None  # the matched existing record (UPDATE/DELETE/NOOP)


def _newest(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The most-recent record by timestamp (falls back to file order for missing timestamps)."""
    return max(records, key=lambda r: str(r.get("timestamp") or ""))


def reconcile(
    task: str,
    result: str,
    existing: list[dict[str, Any]],
    *,
    status: str = "completed",
) -> ReconcileDecision:
    """Mem0-style reconcile: decide ADD / UPDATE / DELETE / NOOP for this write against the
    records already stored for the same subject. `existing` is a list of loaded closeout payloads
    (each ideally carrying its `_file`). Pure function -- no I/O."""
    is_retraction = bool(_RETRACT_RE.match(str(task or "")))
    subject_key = _norm(_retract_subject(task) if is_retraction else task)

    matches = [r for r in existing if _norm(r.get("task", "")) == subject_key]

    if is_retraction:
        if not matches:
            return ReconcileDecision(
                NOOP, reason="retraction of a subject that is not on record -- nothing to delete")
        return ReconcileDecision(
            DELETE,
            reason=f"explicit retraction of stored subject; tombstoning {len(matches)} record(s)",
            supersedes=[str(r.get("_file")) for r in matches if r.get("_file")],
            target=_newest(matches),
        )

    if not matches:
        return ReconcileDecision(ADD, reason="new subject -- not previously on record")

    result_key = _norm(result)
    for r in matches:
        if _norm(r.get("result", "")) == result_key and _norm(r.get("status", "")) == _norm(status):
            return ReconcileDecision(
                NOOP,
                reason="exact duplicate of an already-stored record (same subject, result, status)",
                target=r,
            )

    newest = _newest(matches)
    return ReconcileDecision(
        UPDATE,
        reason="same subject with a different result -- supersede the prior record, don't blindly append",
        supersedes=[str(newest.get("_file"))] if newest.get("_file") else [],
        target=newest,
    )


# --- candidate loading (cheap slug-prefiltered) ----------------------------------------------

def load_candidate_records(workspace: str | Path, task: str) -> list[dict[str, Any]]:
    """Load only the stored closeouts whose filename slug matches this task's subject, so
    reconcile is O(matching files) rather than reading every closeout on every write.

    Filenames are ``cortex-closeout__{stamp}-{slug}__{uuid7}.json`` (audit.write_closeout), and
    the slug is a deterministic function of the task -- so we can glob for `-{slug}__` and only
    parse those. `_slugify` emits alnum+`-` only, so the slug is glob-safe."""
    from .audit import _slugify

    slug = _slugify(task)
    root = Path(workspace) / "audit"
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for shard in sorted(root.glob("audit-log-*")):
        agent = shard / "agent"
        if not agent.is_dir():
            continue
        for f in agent.glob(f"cortex-closeout__*-{slug}__*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                data["_file"] = str(f)
                out.append(data)
    return out


def evaluate_write(
    workspace: str | Path,
    task: str,
    result: str,
    status: str = "completed",
    tests: str = "",
    scripts: str = "",
) -> tuple[PolicyResult, ReconcileDecision]:
    """Compose the two guards for the write path: run the memory-write policy on the input, then
    reconcile against the on-disk records for the same subject. Returns (policy, decision). The
    caller refuses on ``not policy.allowed`` and acts on ``decision.action``."""
    policy = check_write_policy(task, result, tests=tests, scripts=scripts)
    if not policy.allowed:
        # Don't touch the store when the input is rejected; hand back a NOOP so the shape is stable.
        return policy, ReconcileDecision(NOOP, reason="input rejected by write policy")
    existing = load_candidate_records(workspace, task)
    decision = reconcile(task, result, existing, status=status)
    return policy, decision


def write_policy_enabled() -> bool:
    """Default ON (this IS the G4 fix -- the blind-log default is the vulnerability). Set
    CORTEX_WRITE_POLICY=0 to restore the old append-anything behavior (reversible escape hatch)."""
    import os

    return (os.environ.get("CORTEX_WRITE_POLICY", "1").strip().lower()
            not in ("0", "false", "no", "off", ""))
