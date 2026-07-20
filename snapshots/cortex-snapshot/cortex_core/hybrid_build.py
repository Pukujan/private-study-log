"""The hybrid state machine's spine: Director route -> APP_BUILD chart -> executor -> gate -> reaction.

This module composes the pieces WITHOUT owning any intelligence of its own:

    utterance
      -> director.direct()            the cascade picks track + fresh-build skill (logged)
      -> StateEngine, track=app_build the DETERMINISTIC engine owns every transition; this driver
                                      only ever submits the DECLARED advance tool for the current
                                      state (routing-as-data: the model/driver selects among
                                      declared transitions, the engine executes them; anything
                                      else is refused ILLEGAL_IN_STATE and changes nothing)
      -> vague_build.drive()          the template-injection executor (the model fills ONE JSON
                                      slot; the harness renders every line of code)
      -> smoke_verdict_gate           SERVER-OWNED (terra HIGH #1): the deterministic gate run
                                      mints a receipt (cortex_core.receipts.record_smoke_verdict)
                                      keyed to task + artifact digest + checks digest; SMOKE
                                      accepts ONLY that verdict_id and re-validates it. This
                                      driver never forwards a boolean.
      -> reaction.classify + PROPOSE  the human's verbatim reaction is classified; every learning
                                      signal is queued as a PROPOSAL requiring a human binary
                                      backed by an approval receipt (review fixes #2/#3 + terra #2)

Project rework budget (review fix #5, hardened per terra HIGH #3): the old JSONL
read-count-then-append ledger was a TOCTOU race (two concurrent workers could both read 7,
both pass, both append -> 9), leaked an attempt on a crash between drive() and the append,
and let a child task launder budget by inventing a fresh project_id. It is now a SQLite
store (`ops-local/project-budget.db`) where an attempt is RESERVED **before** execution in
a single BEGIN IMMEDIATE transaction against a project ownership row: reservations are
counted (not completions), the (project_id, seq) PRIMARY KEY makes a double-spend at the
same slot an IntegrityError even if the count check were bypassed, and a crash after
reservation *wastes* one unit (fail-safe) instead of minting a free attempt. Child chunks
must bind to their parent's project_id (`parent_task_id`) -- a mismatched project_id is an
honest refusal, not a fresh budget.

Anti-circularity invariant, restated for this file: the ONLY verdict-maker is the deterministic
gate (app_gates via the receipt-backed smoke_verdict_gate); the ONLY state-transition owner is
the StateEngine; the ONLY mutation path for skills/training data is reaction.confirm() (human
binary + approval receipt). LLMs here fill slots and propose — nothing else.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from cortex_core import build_skills as bs
from cortex_core import director
from cortex_core import fanout
from cortex_core import reaction as rx
from cortex_core import receipts
from cortex_core import vague_build as vb
from cortex_core.state_engine import StateEngine, make_universal_gate

# Fix #5: total executor build attempts allowed per PROJECT (across every chunk/task). The chart's
# own rework_cap/esc_cap still bound each chunk; this bounds the conversation. Tunable data.
PROJECT_REWORK_CAP = 8

# Hard bound on the chart walk (belt-and-braces; the chart itself terminates via caps).
_MAX_STEPS = 60

_BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS project(
  project_id TEXT PRIMARY KEY,
  root_task_id TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS attempt(
  project_id TEXT NOT NULL REFERENCES project(project_id),
  seq INTEGER NOT NULL,
  task_id TEXT,
  ts REAL,
  PRIMARY KEY(project_id, seq)
);
"""


def _ops_dir(workspace: str | Path | None) -> Path:
    if workspace is not None and Path(str(workspace)).is_dir():
        root = Path(str(workspace))
    else:
        from cortex_core.config import resolve_workspace
        root = Path(resolve_workspace(None))
    out = root / "ops-local"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _budget_conn(workspace: str | Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_ops_dir(workspace) / "project-budget.db"), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_BUDGET_SCHEMA)
    return conn


def _valid_project_id(project_id: Any) -> str:
    """terra RE-REVIEW-2 #3: a project identity must be a non-blank string. An EMPTY
    project_id used to be a real budget owner whose falsiness then skipped the continuation
    lineage guard (`if parent_project and ...`) -- a root run with project_id="" could exhaust
    its cap and a declared child could launder a fresh budget under any name. Normalizing +
    refusing here (and rejecting at run_chunk intake) kills both arms."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id must be a non-empty string -- a blank/falsy project "
                         "identity cannot own or launder a rework budget (terra RE-REVIEW-2 #3)")
    return project_id.strip()


def project_attempts(project_id: str, workspace: str | Path | None = None) -> int:
    """Executor build attempts RESERVED by this project (reservations, not completions --
    terra HIGH #3: a crash between reservation and execution burns the unit rather than
    minting a free retry)."""
    conn = _budget_conn(workspace)
    try:
        row = conn.execute("SELECT COUNT(*) FROM attempt WHERE project_id=?",
                           (str(project_id),)).fetchone()
        return int(row[0])
    finally:
        conn.close()


def reserve_attempt(project_id: str, task_id: str,
                    workspace: str | Path | None = None) -> dict[str, Any]:
    """Reserve ONE executor build attempt BEFORE execution, transactionally (terra HIGH #3).

    One BEGIN IMMEDIATE transaction: ensure the project ownership row exists (first
    reservation binds root_task_id), count existing reservations, refuse at the cap,
    else insert the next (project_id, seq) row. Two concurrent callers serialize on the
    write lock; the PRIMARY KEY is the belt-and-braces against a double-spend at one slot.
    Returns {"ok": True, "attempt_seq": n} or {"ok": False, "spent": n, "cap": cap}.

    terra RE-REVIEW #3: the cap is ALWAYS the server-side module PROJECT_REWORK_CAP. There
    is no caller-supplied cap parameter -- a caller could otherwise widen its own budget.

    terra RE-REVIEW-2 #3: a blank/falsy project_id is REFUSED at the server layer (it used to
    own a budget row of its own, and its falsiness skipped the lineage guard upstream)."""
    project_id = _valid_project_id(project_id)
    cap = PROJECT_REWORK_CAP
    conn = _budget_conn(workspace)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT OR IGNORE INTO project(project_id, root_task_id, created_at)"
                         " VALUES(?,?,?)", (str(project_id), str(task_id), time.time()))
            n = int(conn.execute("SELECT COUNT(*) FROM attempt WHERE project_id=?",
                                 (str(project_id),)).fetchone()[0])
            if n >= cap:
                conn.execute("ROLLBACK")
                return {"ok": False, "spent": n, "cap": cap}
            conn.execute("INSERT INTO attempt(project_id, seq, task_id, ts) VALUES(?,?,?,?)",
                         (str(project_id), n + 1, str(task_id), time.time()))
            conn.execute("COMMIT")
            return {"ok": True, "attempt_seq": n + 1}
        except sqlite3.IntegrityError:
            # A concurrent writer took this exact seq despite the lock (should be
            # impossible under BEGIN IMMEDIATE) -- refuse rather than over-spend.
            conn.execute("ROLLBACK")
            return {"ok": False, "spent": cap, "cap": cap, "code": "reservation_conflict"}
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _winner_build(fr: "fanout.FanoutResult", holder: dict[str, Any]) -> dict[str, Any]:
    """Fan-in reconciliation: fold a FanoutResult into the single-executor `build` dict shape +
    populate `holder` with the WINNER's server verdict (verdict_id/app_dir/checks) so the
    downstream cortex_submit_artifact + cortex_submit_smoke steps run byte-identically to the
    single path. The winner is the deterministic gate's pick (rank_passers, JUDGE-FREE). When
    no executor passed, a representative gate-caught FAILURE (its own failing receipt) is carried
    so SMOKE fails CLOSED honestly (SMOKE_FAIL/rework), never a waved-through pass; if every
    executor bad_slotted (no artifact, no receipt), holder stays empty -> SMOKE NO_VERDICT."""
    rep = (fr.winner
           or next((a for a in fr.attempts if a.verdict_id and a.app_dir), None)
           or (fr.attempts[0] if fr.attempts else None))
    fanout_summary = {
        "winner": fr.winner.executor if fr.winner else None,
        "ranking": [a.executor for a in fr.ranking],
        "attempts": {a.executor: {"passed": a.passed, "status": a.status,
                                  "failure_class": a.failure_class} for a in fr.attempts},
        "seed": fr.seed,
    }
    if rep is None:
        return {"status": "bad_slot", "skills": [], "app_dir": None, "passed": False,
                "failure_class": "SLOT_FAIL", "fanout": fanout_summary}
    # Carry ONLY the winner's (representative's) server receipt into the shared holder.
    holder["verdict_id"] = rep.verdict_id
    holder["app_dir"] = rep.app_dir
    holder["checks"] = rep.check_specs
    return {"status": rep.status, "skills": list(rep.skills), "app_dir": rep.app_dir,
            "slot": rep.slot, "passed": bool(rep.passed),
            "failure_class": rep.failure_class, "verdict": rep.coach_view,
            "fanout": fanout_summary}


def run_chunk(utterance: str, *, project_id: str,
              engine: StateEngine | None = None, db_path: str | Path | None = None,
              workspace: str | Path | None = None, tier: str = "big-pickle",
              llm: Callable[[str], str] | None = None,
              router_llm: Callable[[str], str] | None = None,
              gate: Callable[..., Any] | None = None,
              reaction_text: str | None = None,
              reaction_llm: Callable[[str], str] | None = None,
              out_dir: str | Path | None = None, retries: int = 1,
              parent_task_id: str | None = None,
              fanout_enabled: bool = True,
              fanout_executors: list[str] | None = None,
              fanout_student_factory: Callable[[Any], Callable[[str], str]] | None = None
              ) -> dict[str, Any]:
    """Run ONE conversational chunk end to end through the hybrid state machine.

    `llm` (executor slot-filler), `router_llm` (Director tier-4), `reaction_llm` (reaction tier-4)
    and `gate` (app_gates replacement) are all injectable — unit-testable with zero network. The
    default tier is a FREE lane (vague_build._TIER_ALIASES); this code never selects a paid model.
    An injected `gate` mints a server-side verdict receipt ONLY while receipts' TEST-ONLY seam
    is open (`receipts.allow_injected_gate_for_tests()`); with the seam closed -- the production
    posture -- `gate` must be None and the mint runs the real `app_gates.run_done_checks`
    itself (terra RE-REVIEW-2 #1).

    `parent_task_id` (terra HIGH #3): a follow-up chunk of an existing conversation MUST name its
    parent task; the chunk is bound to the parent's project_id, and a caller passing a DIFFERENT
    project_id (budget laundering) is refused with `project_id_mismatch`.

    `fanout_enabled` (2026-07-15, gap I3 wired): when the route's skill is a fresh_build primary
    (`fanout.fanout_supported`) AND >=2 free executors are live, the SCAFFOLD slot is filled by
    an AUTOMATIC homogeneous fan-out -- N free executors build in parallel, each minting its OWN
    server verdict receipt over its OWN candidate artifact, and the DETERMINISTIC gate +
    `rank_passers` pick the winner (judge-free). The winner's verdict_id/app_dir/checks are carried
    forward EXACTLY as the single-executor path. Falls back to one `vb.drive()` otherwise, so the
    single-worker path is behavior-preserving. An injected single `llm` pins ONE executor and
    stays on the single path; fan-out engages when no `llm` is pinned (production) or a
    `fanout_student_factory` is injected (offline dispatch for the coupling tests).

    Returns {"status": "done" | "abandoned" | "project_budget_exhausted" | ..., "task_id",
    "state", "route", "build", "reaction", "proposals", "attempts_spent"}. Proposals are QUEUED,
    never applied — apply with reaction.confirm(pid, True/False, receipt=...) after asking the
    human (the receipt comes from receipts.mint_approval on the human console)."""
    # terra RE-REVIEW-2 #3: a blank/falsy project_id is refused at intake -- it can neither
    # own a budget nor (by being falsy) slip a declared continuation past the lineage guard.
    if not isinstance(project_id, str) or not project_id.strip():
        return {"status": "invalid_project_id", "utterance": utterance,
                "reason": "project_id must be a non-empty string (terra RE-REVIEW-2 #3: a "
                          "falsy project identity cannot own or launder a rework budget)"}
    project_id = project_id.strip()
    skills = bs.load_skills(workspace)
    if not skills:
        return {"status": "no_skill", "utterance": utterance}
    if not director.fresh_build_ids(skills):
        return {"status": "no_fresh_skill", "utterance": utterance}

    # Fix #5 precheck: a project whose budget is spent gets an honest refusal, not a loop.
    # (The authoritative check is the transactional reservation below; this precheck only
    # avoids creating a task/route for an already-dead project.)
    spent = project_attempts(project_id, workspace)
    if spent >= PROJECT_REWORK_CAP:
        return {"status": "project_budget_exhausted", "project_id": project_id,
                "attempts_spent": spent, "cap": PROJECT_REWORK_CAP}

    own_engine = engine is None
    if own_engine:
        engine = StateEngine(str(db_path or (_ops_dir(workspace) / "hybrid-tasks.db")),
                             gate=make_universal_gate(),
                             workspace=str(workspace) if workspace is not None else None)
    try:
        # terra HIGH #3: child chunks are BOUND to the parent's project -- a fresh
        # project_id cannot launder a new budget for the same conversation.
        if parent_task_id is not None:
            try:
                parent = engine.get(parent_task_id)
            except KeyError:
                return {"status": "unknown_parent_task", "parent_task_id": parent_task_id}
            parent_project = (parent.get("intent") or {}).get("project_id")
            parent_project = parent_project.strip() if isinstance(parent_project, str) else ""
            # terra RE-REVIEW-2 #3: the old guard was `if parent_project and ...` -- a FALSY
            # parent project ("" / None) skipped the check entirely, so a declared child could
            # launder a fresh budget under any project_id. A declared continuation now binds to
            # its parent's project_id REGARDLESS of falsiness: a blank/absent parent project can
            # never equal the (validated, non-blank) child project_id, so it is refused too.
            if parent_project != project_id:
                return {"status": "project_id_mismatch", "project_id": project_id,
                        "parent_task_id": parent_task_id,
                        "parent_project_id": parent_project,
                        "reason": "child chunks are budget-bound to their parent's "
                                  "project_id, regardless of the parent value's falsiness "
                                  "(terra finding #3 / RE-REVIEW-2 #3)"}

        route = director.direct(utterance, skills, llm=router_llm, workspace=workspace)

        tid = engine.create_task(
            {"seeking": utterance, "project_id": project_id,
             "route": {"skill_id": route.skill_id, "tier_used": route.tier_used,
                       "confidence": route.confidence, "route_id": route.route_id}},
            track="app_build", parent_id=parent_task_id)

        # terra RE-REVIEW #1: the receipt is minted BY RUNNING the gate, inside
        # receipts.run_and_record_smoke_verdict -- there is no `passed` the caller can set.
        # The passing bit is taken from the GateVerdict the real gate returns over the real
        # artifact. This is the single gate execution (its verdict is reused as the build
        # result), and the receipt is digest-bound to the artifact + checks so the engine can
        # require it match THIS task's SCAFFOLD artifact.
        # terra RE-REVIEW-2 #1 (callback seam SEALED): `gate=None` (production) means the mint
        # runs app_gates.run_done_checks ITSELF and stamps that identity on the receipt. An
        # injected `gate` is only honored while receipts' TEST-ONLY seam is open
        # (allow_injected_gate_for_tests) -- in a production process an injected callback can
        # neither mint nor validate a receipt, so run_chunk(gate=fake) cannot forge a pass.
        holder: dict[str, Any] = {}

        def receipted_gate(app_dir: Any, checks: Any) -> Any:
            vid, v = receipts.run_and_record_smoke_verdict(
                task_id=tid, app_dir=app_dir, checks=checks,
                run_checks=gate, workspace=workspace)
            holder["verdict_id"] = vid
            holder["app_dir"] = app_dir
            holder["checks"] = checks
            return v

        build: dict[str, Any] = {}
        reaction_obj: rx.Reaction | None = None
        proposals: list[str] = []
        env = engine.get(tid)
        for _ in range(_MAX_STEPS):
            state, seq = env["state"], env["seq"]
            if state in ("DONE", "ABANDONED"):
                break
            if state == "SCAFFOLD":
                # terra HIGH #3: RESERVE the attempt BEFORE execution, transactionally.
                res = reserve_attempt(project_id, tid, workspace=workspace)
                if not res.get("ok"):
                    # Budget exhausted (possibly mid-chunk): stop honestly. The task stays
                    # non-terminal; the reaper will move it to STALLED, resumable if the
                    # human raises the budget. No silent loop, no fake closeout.
                    return {"status": "project_budget_exhausted", "project_id": project_id,
                            "task_id": tid, "state": state,
                            "attempts_spent": res.get("spent", 0),
                            "cap": res.get("cap", PROJECT_REWORK_CAP),
                            "route": asdict(route)}
                attempt_dir = None
                if out_dir is not None:
                    attempt_dir = Path(out_dir) / f"attempt_{res['attempt_seq'] - 1}"
                holder.clear()  # never reuse a stale receipt across attempts
                # === FAN-OUT INTEGRATION SEAM (gap I3, cortex_core/fanout.py) — NOW WIRED =======
                # This is the ONE point where the state machine fills the SCAFFOLD slot. When
                # `fanout.fanout_supported(route.skill_id)` holds (a fresh_build primary -- a slot
                # N free models can fill independently) AND the probe/roster reports >=2 live free
                # executors, the state machine AUTO-fans-out: N free executors build in parallel,
                # EACH minting its OWN server verdict receipt over its OWN candidate artifact
                # (receipt_task_id=tid), and the DETERMINISTIC gate + rank_passers pick the winner
                # (JUDGE-FREE — no LLM verdict). The receipt-race the old TODO flagged is GONE: the
                # shared `holder` is never written by an executor; each executor's receipt lives in
                # its OWN run_one_executor closure (fanout.ExecAttempt.verdict_id), and the fan-in
                # copies ONLY the winner's verdict_id/app_dir/checks into `holder`. The server-owned-
                # verdict invariant is preserved (each receipt is server-minted + digest-bound to
                # THAT candidate's artifact + gate identity; the winner's is re-validated at SMOKE).
                # Falls back to a single vb.drive() when not fan-out-eligible or <2 executors.
                fo_result = None
                # An injected single `llm` PINS one executor (the single path) -- fan-out uses its
                # own N-executor pool, so it engages only when no single llm is pinned (production)
                # or a fan-out dispatch is explicitly injected (offline coupling tests).
                fanout_ok = fanout_enabled and (llm is None or fanout_student_factory is not None)
                if fanout_ok and fanout.fanout_supported(route.skill_id):
                    execs = fanout._restrict_to_available(
                        fanout_executors or fanout.DEFAULT_EXECUTORS, workspace)
                    if len(execs) >= 2:
                        fo_result = fanout.fanout(
                            utterance, executors=execs, retries=retries, workspace=workspace,
                            reviewer=None, receipt_task_id=tid, receipt_run_checks=gate,
                            student_factory=(fanout_student_factory or fanout._build_student))
                if fo_result is not None:
                    build = _winner_build(fo_result, holder)
                else:
                    build = vb.drive(utterance, tier=tier, llm=llm, gate=receipted_gate,
                                     retries=retries, workspace=workspace, out_dir=attempt_dir,
                                     primary_skill_id=route.skill_id)
                # Submit the app_dir AND the exact checks the gate ran (from the receipted
                # gate wrapper) so the engine persists a server-computed digest of BOTH and
                # can bind the SMOKE receipt to this task's artifact + checks (terra #1).
                env = engine.step(tid, "cortex_submit_artifact",
                                  {"status": build.get("status"),
                                   "app_dir": holder.get("app_dir") or build.get("app_dir"),
                                   "checks": holder.get("checks"),
                                   "skills": build.get("skills", [])}, seq=seq)
            elif state == "SMOKE":
                # The SERVER-OWNED verdict (terra HIGH #1): submit only the opaque receipt
                # minted by the gate run. bad_slot => no gate run => no receipt => the
                # engine's bound gate fails CLOSED and reworks (an honest fail, never a
                # waved-through pass). This driver never forwards a boolean.
                env = engine.step(tid, "cortex_submit_smoke",
                                  {"verdict_id": holder.get("verdict_id")}, seq=seq)
                # On gate-fail the ENGINE decides: rework to SCAFFOLD (loop re-enters above)
                # or ABANDONED past the caps — this driver never chooses the transition.
            elif state == "SHOW":
                if reaction_text is not None:
                    reaction_obj = rx.classify_reaction(reaction_text, llm=reaction_llm,
                                                        workspace=workspace)
                    proposals = rx.proposals_from_reaction(
                        reaction_obj, route_id=route.route_id,
                        skill_ids=build.get("skills", []), workspace=workspace)
                env = engine.step(tid, "cortex_submit_reaction",
                                  {"reaction": asdict(reaction_obj) if reaction_obj else None,
                                   "proposals": proposals}, seq=seq)
            elif state == "CLOSEOUT":
                env = engine.step(tid, "cortex_write_closeout",
                                  {"task": utterance,
                                   "result": f"build={build.get('status')} "
                                             f"passed={build.get('passed')}",
                                   "route_id": route.route_id,
                                   "gate": {"passed": build.get("passed"),
                                            "failure_class": build.get("failure_class"),
                                            "verdict_id": holder.get("verdict_id")}},
                                  seq=seq)
            else:  # STALLED or an unknown state: stop; resumption is a separate, explicit act
                break
            if not env.get("ok"):
                # A refusal here is a driver bug (stale seq etc.) — resync once, then bail.
                env = engine.get(tid)

        final = engine.get(tid)
        status = {"DONE": "done", "ABANDONED": "abandoned"}.get(final["state"], "incomplete")
        return {"status": status, "task_id": tid, "state": final["state"],
                "route": asdict(route), "build": build,
                "reaction": asdict(reaction_obj) if reaction_obj else None,
                "proposals": proposals,
                "attempts_spent": project_attempts(project_id, workspace)}
    finally:
        if own_engine:
            engine.close()
