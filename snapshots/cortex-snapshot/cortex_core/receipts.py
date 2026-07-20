"""Server-owned receipts: deterministic gate verdicts + human-approval capabilities.

Built for the two terra HIGHs that shared one root cause -- "a caller-supplied value was
trusted as if the server had produced it" (reviewed/hybrid-state-machine-codex-gpt56-terra-
review-2026-07-11.md, findings #1 and #2):

  #1  SMOKE verdicts. `state_engine.smoke_verdict_gate` used to accept a caller payload
      `{"verdict": {"passed": bool}}` and only type-check it -- a model could forge a pass.
      Now the ONLY thing a caller may submit at SMOKE is an opaque `verdict_id` minted by
      `record_smoke_verdict()` at the moment the deterministic gate actually ran. The gate
      LOOKS UP the receipt and re-validates it: task binding, artifact content digest,
      checks digest, gate version. Missing / unknown / mismatched => fail-CLOSED.

  #2  Human approvals. `reaction.confirm(pid, True, by="model")` used to be recorded as a
      human decision -- `by` was an unauthenticated label and `bool("yes")` coerced. Now
      every training-feeding mutation (acceptance, relabel, pass_count decrement) requires
      a live single-use approval receipt minted by `mint_approval()` -- the server-side
      human console primitive. `director.load_trainable()` trusts ONLY acceptance records
      whose receipt verifies against this store.

Store: one SQLite db in gitignored `ops-local/receipts.db` (telemetry/training plumbing,
never committed corpus -- the routing-log precedent). Stdlib only; fully offline-testable.

HONEST LIMIT (stated, not hidden): any code running in-process with filesystem access can
call `mint_approval()` or insert rows directly -- Python cannot make an in-process
capability cryptographically unforgeable. What this store makes STRUCTURAL is that no
declared model-reachable surface (MCP tools, `run_chunk` parameters, the documented
reaction/director APIs) can produce a trusted verdict or approval without the server-side
mint actually executing: the trust decision moved from "did the payload look right" to
"did the deterministic gate / human console run". That is the ground-truth invariant the
project is built on (CLAUDE.md: deterministic gates decide; a model only proposes).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

# Version of the smoke-verdict receipt semantics; stored per-receipt so a future change to
# what "the checks ran" means can invalidate old receipts instead of silently honoring them.
# "2" (terra RE-REVIEW-2 #1): receipts now also carry the GATE IDENTITY that produced the
# verdict; v1 receipts (no gate_id) are stale and fail closed.
GATE_VERSION = "2"

# terra RE-REVIEW-2 #1 (the callback mint seam): the ONLY gate identity a production receipt
# may carry. `run_and_record_smoke_verdict` runs `app_gates.run_done_checks` itself unless the
# TEST-ONLY seam below is explicitly opened; validation rejects any receipt whose gate_id is
# not the real gate (or, under the open seam, an explicitly-marked injected test gate).
REAL_GATE_ID = "app_gates.run_done_checks"
_INJECTED_GATE_PREFIX = "injected:"

# TEST-ONLY seam, default CLOSED. While closed (production posture): minting with any
# `run_checks` other than the real `app_gates.run_done_checks` raises PermissionError, and
# validation refuses every receipt not minted by the real gate (GATE_NOT_AUTHENTIC). Offline
# tests open it via `allow_injected_gate_for_tests()` / monkeypatch; nothing in production
# code paths ever flips it.
_ALLOW_INJECTED_GATE = False


def allow_injected_gate_for_tests(enabled: bool = True) -> None:
    """Open/close the TEST-ONLY injected-gate seam (offline suites with fake gates). This is
    deliberately loud and greppable: no production module calls it. While the seam is closed,
    a caller-supplied `run_checks` callback can neither MINT a receipt nor VALIDATE one --
    the deterministic `app_gates.run_done_checks` is the only pass source (terra RE-REVIEW-2 #1)."""
    global _ALLOW_INJECTED_GATE
    _ALLOW_INJECTED_GATE = bool(enabled)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS smoke_verdict(
  verdict_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  app_dir TEXT NOT NULL,
  artifact_digest TEXT NOT NULL,
  checks_digest TEXT NOT NULL,
  gate_version TEXT NOT NULL,
  passed INTEGER NOT NULL,
  failure_class TEXT,
  ts REAL,
  gate_id TEXT
);
CREATE TABLE IF NOT EXISTS approval(
  receipt_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  decision INTEGER NOT NULL,
  channel TEXT NOT NULL,
  consumed INTEGER NOT NULL DEFAULT 0,
  ts REAL
);
CREATE TABLE IF NOT EXISTS proposal_resolution(
  proposal_id TEXT PRIMARY KEY,
  receipt_id TEXT NOT NULL,
  accepted INTEGER NOT NULL,
  ts REAL
);
"""


def _db_path(workspace: str | Path | None) -> Path:
    """Same resolution discipline as director._routing_log_path: an explicit directory is
    used directly (tests, bound engines); otherwise the resolved workspace root."""
    if workspace is not None and Path(str(workspace)).is_dir():
        root = Path(str(workspace))
    else:
        from cortex_core.config import resolve_workspace
        root = Path(resolve_workspace(None))
    out = root / "ops-local" / "receipts.db"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _connect(workspace: str | Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(workspace)), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)
    try:  # pre-gate_id stores: add the column; those rows stay NULL -> fail closed at validation
        conn.execute("ALTER TABLE smoke_verdict ADD COLUMN gate_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists (fresh schema or already migrated)
    return conn


# --------------------------------------------------------------------------- #
# digests                                                                      #
# --------------------------------------------------------------------------- #

def digest_dir(app_dir: str | Path) -> str | None:
    """Content digest of a built artifact: sha256 over sorted (relative-posix-path, bytes)
    of every file under app_dir. None when the directory is absent/unreadable -- the caller
    fails CLOSED on None, never open."""
    root = Path(app_dir)
    if not root.is_dir():
        return None
    h = hashlib.sha256()
    try:
        files = sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(b"\x00")
            h.update(p.read_bytes())
            h.update(b"\x00")
    except OSError:
        return None
    return h.hexdigest()


def digest_checks(checks: Any) -> str:
    """Canonical digest of the done-checks list the gate actually ran."""
    return hashlib.sha256(
        json.dumps(checks, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# smoke-verdict receipts (finding #1)                                          #
# --------------------------------------------------------------------------- #

def run_and_record_smoke_verdict(*, task_id: str, app_dir: str | Path, checks: Any,
                                 run_checks: Callable[[Any, Any], Any] | None = None,
                                 workspace: str | Path | None = None) -> tuple[str, Any]:
    """RUN the deterministic gate and persist ITS verdict as a receipt (terra RE-REVIEW #1).

    There is deliberately NO `passed` parameter. The passing/failing bit is taken from the
    `GateVerdict` the gate returns -- and the gate is executed HERE, inside the mint.

    terra RE-REVIEW-2 #1 (the callback seam, now SEALED): `run_checks` is no longer a trusted
    parameter. With `run_checks=None` (the production path) the mint runs THE real
    deterministic gate, `app_gates.run_done_checks`, itself -- and records that identity
    (`gate_id=REAL_GATE_ID`) on the receipt, which `validate_smoke_receipt` re-checks. Any
    OTHER callable is refused (PermissionError) unless the TEST-ONLY seam is explicitly open
    (`allow_injected_gate_for_tests()`); a seam-minted receipt is marked `injected:<qualname>`
    and is rejected by validation whenever the seam is closed -- so a production process can
    neither mint nor honor a fake-gate receipt.

    The receipt is digest-bound to the ARTIFACT the gate graded and the CHECKS it ran, so the
    state engine can later require the receipt match the task's own SCAFFOLD artifact + required
    checks. Returns (verdict_id, verdict) so the caller reuses the single gate run."""
    gate_id: str | None = None
    if run_checks is None:
        from cortex_core import app_gates
        run_checks = app_gates.run_done_checks
        gate_id = REAL_GATE_ID
    else:
        try:
            from cortex_core import app_gates
            if run_checks is app_gates.run_done_checks:
                gate_id = REAL_GATE_ID
        except Exception:  # noqa: BLE001 -- identity probe only; injected path decides below
            pass
        if gate_id is None:
            if not _ALLOW_INJECTED_GATE:
                raise PermissionError(
                    "run_and_record_smoke_verdict: a caller-supplied run_checks callback "
                    "cannot mint verdict receipts -- the deterministic gate "
                    f"({REAL_GATE_ID}) is the only pass source. Offline tests must open the "
                    "test seam explicitly via allow_injected_gate_for_tests() "
                    "(terra RE-REVIEW-2 #1)")
            gate_id = _INJECTED_GATE_PREFIX + getattr(
                run_checks, "__qualname__", repr(run_checks))
    art = digest_dir(app_dir)
    if art is None:
        raise ValueError(f"smoke verdict: app_dir {app_dir!r} is not a readable directory -- "
                         "a verdict must be minted over a real artifact")
    verdict = run_checks(app_dir, checks)          # THE gate execution -- the only PASS source
    passed = bool(verdict.passed)
    failure_class = getattr(verdict, "failure_class", None)
    # Re-digest AFTER the run: bind to the exact bytes graded (a check that mutates the dir
    # would change this, and the state-engine task-binding would then reject the receipt).
    art_after = digest_dir(app_dir)
    vid = "sv_" + uuid.uuid4().hex
    conn = _connect(workspace)
    try:
        conn.execute(
            "INSERT INTO smoke_verdict(verdict_id, task_id, app_dir, artifact_digest,"
            " checks_digest, gate_version, passed, failure_class, ts, gate_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (vid, str(task_id), str(app_dir), art_after or art, digest_checks(checks),
             GATE_VERSION, 1 if passed else 0, failure_class, time.time(), gate_id))
        conn.commit()
    finally:
        conn.close()
    return vid, verdict


def lookup_smoke_verdict(verdict_id: str,
                         workspace: str | Path | None = None) -> dict[str, Any] | None:
    conn = _connect(workspace)
    try:
        row = conn.execute(
            "SELECT verdict_id, task_id, app_dir, artifact_digest, checks_digest,"
            " gate_version, passed, failure_class, ts, gate_id FROM smoke_verdict"
            " WHERE verdict_id=?",
            (str(verdict_id),)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"verdict_id": row[0], "task_id": row[1], "app_dir": row[2],
            "artifact_digest": row[3], "checks_digest": row[4], "gate_version": row[5],
            "passed": bool(row[6]), "failure_class": row[7], "ts": row[8],
            "gate_id": row[9]}


def validate_smoke_receipt(verdict_id: Any, *, task_id: str | None,
                           expected_artifact_digest: str | None = None,
                           expected_checks_digest: str | None = None,
                           workspace: str | Path | None = None) -> dict[str, Any]:
    """Re-validate a submitted verdict reference. FAIL-CLOSED on every branch: unknown id,
    wrong task, stale gate version, an inauthentic gate identity, vanished artifact, an
    artifact whose current content digest no longer matches the one the gate graded, OR
    (terra RE-REVIEW #1) a receipt whose artifact/checks digest does not match the TASK's own
    SCAFFOLD artifact + required checks. Those digests are the real binding: a genuine passing
    receipt minted over artifact A / checks X cannot pass a SMOKE whose task artifact is B /
    required checks are Y.

    terra RE-REVIEW-2 #1: a None/absent expected digest FAILS the comparison instead of
    skipping it. A task whose SCAFFOLD never bound a real artifact digest (no app_dir, or an
    unreadable one) therefore can never advance SMOKE on ANY receipt -- NO_ARTIFACT, fail
    CLOSED, never open. Likewise a receipt whose gate_id is not the real deterministic gate
    (or an injected test gate while the test seam is closed) is GATE_NOT_AUTHENTIC."""
    if not isinstance(verdict_id, str) or not verdict_id:
        return {"ok": False, "code": "NO_VERDICT_RECEIPT",
                "reason": "SMOKE requires the opaque verdict_id minted by the server-side "
                          "deterministic gate run; caller-supplied booleans are never trusted"}
    rec = lookup_smoke_verdict(verdict_id, workspace)
    if rec is None:
        return {"ok": False, "code": "UNKNOWN_VERDICT",
                "reason": f"verdict_id {verdict_id!r} is not in the server verdict store"}
    if task_id is not None and rec["task_id"] != str(task_id):
        return {"ok": False, "code": "VERDICT_TASK_MISMATCH",
                "reason": "this verdict was minted for a different task"}
    gid = rec.get("gate_id")
    if gid != REAL_GATE_ID and not (_ALLOW_INJECTED_GATE and isinstance(gid, str)
                                    and gid.startswith(_INJECTED_GATE_PREFIX)):
        return {"ok": False, "code": "GATE_NOT_AUTHENTIC",
                "reason": f"receipt gate identity {gid!r} is not the real deterministic gate "
                          f"({REAL_GATE_ID}); a callback-minted verdict is never trusted in "
                          "production (terra RE-REVIEW-2 #1)"}
    if not expected_artifact_digest:
        # terra RE-REVIEW-2 #1: the task never bound a scaffold artifact digest -- there is
        # NOTHING to compare the receipt against, so no receipt (however genuine) may pass.
        return {"ok": False, "code": "NO_ARTIFACT",
                "reason": "this task's SCAFFOLD never bound a real artifact digest (missing/"
                          "unreadable app_dir); SMOKE fails CLOSED -- rework SCAFFOLD with a "
                          "real artifact directory (terra RE-REVIEW-2 #1)"}
    if rec["artifact_digest"] != expected_artifact_digest:
        return {"ok": False, "code": "ARTIFACT_TASK_MISMATCH",
                "reason": "the receipt was minted over a different artifact than the one this "
                          "task submitted at SCAFFOLD (receipt-for-A cannot pass a SMOKE-of-B)"}
    if not expected_checks_digest:
        return {"ok": False, "code": "NO_CHECKS_BINDING",
                "reason": "this task never bound its required-checks digest at SCAFFOLD; "
                          "SMOKE fails CLOSED (terra RE-REVIEW-2 #1)"}
    if rec["checks_digest"] != expected_checks_digest:
        return {"ok": False, "code": "CHECKS_MISMATCH",
                "reason": "the receipt was minted over a different checks set than the task's "
                          "required checks"}
    if rec["gate_version"] != GATE_VERSION:
        return {"ok": False, "code": "VERDICT_GATE_VERSION_STALE",
                "reason": f"verdict gate_version {rec['gate_version']!r} != {GATE_VERSION!r}"}
    current = digest_dir(rec["app_dir"])
    if current is None:
        return {"ok": False, "code": "ARTIFACT_MISSING",
                "reason": f"artifact dir {rec['app_dir']!r} is gone; cannot re-verify the "
                          "verdict against the artifact at SMOKE"}
    if current != rec["artifact_digest"]:
        return {"ok": False, "code": "ARTIFACT_TAMPERED",
                "reason": "artifact content changed since the deterministic gate graded it"}
    return {"ok": True, "passed": rec["passed"], "failure_class": rec["failure_class"],
            "verdict_id": verdict_id}


# --------------------------------------------------------------------------- #
# human-approval receipts (finding #2)                                         #
# --------------------------------------------------------------------------- #

def mint_approval(subject_id: str, decision: bool, *, channel: str = "human_cli",
                  workspace: str | Path | None = None) -> str:
    """Mint ONE single-use human-approval capability for `subject_id` (a proposal id).
    This is the server-side human-console primitive: the ONLY legitimate caller is the
    surface where a human actually answered the binary (CLI prompt / console). It is
    deliberately NOT exported through any MCP tool."""
    if not isinstance(decision, bool):
        raise TypeError("mint_approval: decision must be a real bool (the human binary)")
    rid = "ap_" + uuid.uuid4().hex
    conn = _connect(workspace)
    try:
        conn.execute(
            "INSERT INTO approval(receipt_id, subject_id, decision, channel, consumed, ts)"
            " VALUES(?,?,?,?,0,?)",
            (rid, str(subject_id), 1 if decision else 0, str(channel), time.time()))
        conn.commit()
    finally:
        conn.close()
    return rid


def check_approval(receipt_id: Any, *, subject_id: str | None = None,
                   decision: bool | None = None, require_unconsumed: bool = True,
                   workspace: str | Path | None = None) -> dict[str, Any] | None:
    """Verify an approval receipt. Returns the record when EVERY requested binding holds
    (subject match, decision match, unconsumed if required); None otherwise -- callers
    treat None as a refusal, never a soft warning."""
    if not isinstance(receipt_id, str) or not receipt_id:
        return None
    conn = _connect(workspace)
    try:
        row = conn.execute(
            "SELECT receipt_id, subject_id, decision, channel, consumed, ts FROM approval"
            " WHERE receipt_id=?", (receipt_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    rec = {"receipt_id": row[0], "subject_id": row[1], "decision": bool(row[2]),
           "channel": row[3], "consumed": bool(row[4]), "ts": row[5]}
    if subject_id is not None and rec["subject_id"] != str(subject_id):
        return None
    if decision is not None and rec["decision"] is not bool(decision):
        return None
    if require_unconsumed and rec["consumed"]:
        return None
    return rec


def consume_approval(receipt_id: str, workspace: str | Path | None = None) -> bool:
    """Mark a receipt consumed (single-use). Returns True iff it was live and is now spent."""
    conn = _connect(workspace)
    try:
        cur = conn.execute(
            "UPDATE approval SET consumed=1 WHERE receipt_id=? AND consumed=0",
            (str(receipt_id),))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def claim_proposal_resolution(proposal_id: str, receipt_id: str, accepted: bool,
                              workspace: str | Path | None = None) -> bool:
    """Atomically claim the ONE-TIME resolution/application slot for a proposal (terra
    RE-REVIEW-2 #2): `proposal_id` is the PRIMARY KEY, so exactly one caller ever gets True.
    This is what makes application one-time at the PROPOSAL level, not merely per-receipt --
    two concurrent confirms holding two DISTINCT valid receipts for the same proposal cannot
    both apply, because only one wins this INSERT. Returns True iff THIS call claimed the
    slot; False when the proposal was already resolved/applied."""
    conn = _connect(workspace)
    try:
        try:
            conn.execute(
                "INSERT INTO proposal_resolution(proposal_id, receipt_id, accepted, ts)"
                " VALUES(?,?,?,?)",
                (str(proposal_id), str(receipt_id), 1 if accepted else 0, time.time()))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    finally:
        conn.close()
