"""Mission driver: wire the native heterogeneous decomposer into the state machine at the
PARTITION seam (terra's design -- ``reviewed/decomposer-research-terra-2026-07-15.md``).

``run_mission()`` is the sibling of ``plane2_driver.run_build()``. Where ``run_build`` coerces
ONE external model through the build track (single-worker), ``run_mission`` coordinates N
HETEROGENEOUS child workers that each own a DISJOINT slice of one goal:

    goal
      -> propose_manifest(goal)      a FREE model PROPOSES a decomposition (never decides)
      -> validate_manifest(...)      the DETERMINISTIC, judge-free gate -- the ONLY authority
                                     over whether a proposal may spawn anything. INVALID =>
                                     rejected BEFORE any task/child is created (no model bypass).
      -> mission chart PARTITION     the persisted, gate-validated manifest advances the mission
                                     INTAKE -> PARTITION -> DISPATCH, and DISPATCH ATOMICALLY
                                     materializes one child task per worker with its owns_units +
                                     claims (state_engine._materialize_partition; the partition
                                     coverage gate + _claim_conflicts enforce MECE/exclusivity).
      -> parallel child drivers      each child runs its OWN app_build chart in its OWN
                                     artifact_lane, minting its OWN server verdict receipt over
                                     its OWN artifact (cortex_core.receipts) -- no shared holder
                                     to race (the fan-out coupling agent's per-worker path).
      -> deterministic fan-in        every REQUIRED unit's child receipt is RE-VALIDATED
                                     server-side (validate_smoke_receipt, digest+task bound). A
                                     child cannot forge or cross-claim another's receipt
                                     (VERDICT_TASK_MISMATCH / ARTIFACT_TASK_MISMATCH). Any
                                     required child failing => the mission fails CLOSED (no MERGE,
                                     never a waved pass).
      -> MERGE / REVIEW / closeout   declared reducers fold the child artifacts; the existing
                                     mission REVIEW scope-check + closeout finish the chart.

Judge-free invariant (NON-NEGOTIABLE)
-------------------------------------
No model decides any child's completion. The decomposer model only PROPOSED the split.
``validate_manifest`` (deterministic set/graph check) and the per-child server receipts
(deterministic gate, server-minted, digest-bound) are the ONLY authorities. Every state
transition is owned by the ``StateEngine`` under its write-lock + (task_id, seq) idempotency;
``run_mission`` only ever submits the engine-declared advance tool for the current phase.

v0 scope (honest, per terra's research doc)
-------------------------------------------
Children run the **app_build** receipt-bearing chart regardless of the proposer's ``track``
label (retained in the child intent for provenance). terra's v0 recommends restricting
heterogeneous slices to independently gateable app_build work; research/prose slices need
their own deterministic receipt/evidence type before they can be a mission child (they must
NOT be waved through on a default-gate success). ``plane2_driver.run_build`` is UNCHANGED.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cortex_core import decomposer, receipts
from cortex_core.state_engine import StateEngine, make_universal_gate

__all__ = [
    "WorkerRunContext",
    "WorkerBuild",
    "WorkerOutcome",
    "run_mission",
]

# Belt-and-braces bound on ONE child's app_build walk (SCAFFOLD->SMOKE->SHOW->CLOSEOUT plus
# rework loops). The chart's own rework_cap/esc_cap already terminate it into ABANDONED; this
# mirrors hybrid_build._MAX_STEPS / plane2_driver._MAX_STEPS.
_MAX_CHILD_STEPS = 60

# The child chart every mission worker runs (v0): the receipt-bearing app_build track. Its SMOKE
# phase is the server-owned deterministic verdict -- exactly the "done = deterministic gate +
# server receipt" the judge-free invariant requires.
_CHILD_TRACK = "app_build"

# Default profile -> FREE executor mapping for the PRODUCTION worker build (offline tests inject
# their own `worker_build`, so this is only the live default). Every target is a FREE executor in
# fanout.EXECUTORS (CLAUDE.md: workers never run a paid tier). This is a deliberate v0 default, not
# a cited decision -- extend as real profile->model mappings are settled.
_PROFILE_TO_EXECUTOR: dict[str, str] = {
    "code-low": "north-mini", "code-medium": "laguna-m.1", "code-high": "laguna-m.1",
    "research-low": "aux", "research-medium": "aux", "research-high": "aux",
    "review-medium": "big-pickle", "review-high": "big-pickle",
}
_DEFAULT_EXECUTOR = "laguna-m.1"


@dataclass(frozen=True)
class WorkerRunContext:
    """Everything a child worker needs to build its slice + mint its receipt. `task_id` is the
    ATOMICALLY-materialized child task id (the receipt is bound to it), so two children can never
    share a receipt holder."""
    task_id: str
    key: str
    objective: str
    owns_units: list[str]
    artifact_lane: str
    tier_profile: str | None
    track: str | None
    workspace: str | None


@dataclass
class WorkerBuild:
    """What a `worker_build` produces for ONE child: an artifact directory, the checks the gate
    ran, and the server verdict_id minted over THAT artifact (bound to the child's task_id)."""
    app_dir: str | None
    checks: Any
    verdict_id: str | None
    passed: bool
    status: str = "built"
    skills: list[str] = field(default_factory=list)


@dataclass
class WorkerOutcome:
    """The reconciled result for ONE child: its final chart state + the receipt the fan-in
    re-validates. `verdict_id`/`app_dir`/`checks` are the child's OWN (never another's)."""
    task_id: str
    key: str
    owns_units: list[str]
    verdict_id: str | None
    app_dir: str | None
    checks: Any
    passed: bool
    final_state: str


WorkerBuildFn = Callable[[WorkerRunContext], WorkerBuild]


# --------------------------------------------------------------------------- #
# Production default child build -- reuses the fan-out per-worker receipt path #
# --------------------------------------------------------------------------- #
def _default_worker_build(ctx: WorkerRunContext) -> WorkerBuild:
    """Live default: drive ONE FREE executor through the build+gate spine, minting a server
    receipt bound to THIS child's task_id over its OWN candidate artifact -- the exact per-worker
    receipt path the fan-out coupling agent built (fanout.run_one_executor, receipt mode). Offline
    tests inject their own `worker_build`, so this never runs under test."""
    from cortex_core import fanout  # lazy: keeps the module import light + offline-testable

    exec_name = _PROFILE_TO_EXECUTOR.get(ctx.tier_profile or "", _DEFAULT_EXECUTOR)
    spec = fanout.EXECUTORS[exec_name]
    attempt = fanout.run_one_executor(
        ctx.objective, spec, seed=0, out_root=ctx.artifact_lane,
        receipt_task_id=ctx.task_id, receipt_run_checks=None,  # None => the REAL deterministic gate
        workspace=ctx.workspace)
    return WorkerBuild(app_dir=attempt.app_dir, checks=attempt.check_specs,
                       verdict_id=attempt.verdict_id, passed=bool(attempt.passed),
                       status=attempt.status, skills=list(attempt.skills))


# --------------------------------------------------------------------------- #
# Manifest -> engine-worker shape                                             #
# --------------------------------------------------------------------------- #
def _engine_worker(w: dict[str, Any]) -> dict[str, Any]:
    """Map ONE validated manifest worker to the engine's partition-worker shape. `owns_units` +
    `claims` are read by the deterministic partition/claim gates; `track` selects the child chart
    (v0: always app_build); `intent` becomes the child task's intent (its objective + lane)."""
    key = w["key"]
    owns = list(w.get("owns_units", []) or [])
    lane = w.get("artifact_lane") or f".cortex/worktrees/{key}"
    intent = {
        "seeking": w.get("objective") or f"build {key}",
        "worker_key": key,
        "owns_units": owns,
        "artifact_lane": lane,
        "tier_profile": w.get("tier_profile"),
        "proposed_track": w.get("track"),   # provenance only; the executable child track is app_build
        "acceptance": w.get("acceptance"),
        "depends_on": list(w.get("depends_on", []) or []),
    }
    return {"key": key, "owns_units": owns, "claims": list(w.get("claims", []) or []),
            "track": _CHILD_TRACK, "intent": intent}


# --------------------------------------------------------------------------- #
# One child driver -- mirrors the app_build spine (run_build stays untouched) #
# --------------------------------------------------------------------------- #
def _run_one_child(engine: StateEngine, child_id: str, worker_build: WorkerBuildFn,
                   workspace: str | None, actor: str) -> WorkerOutcome:
    """Drive ONE materialized child through its app_build chart. Every transition is the engine's;
    this only submits the declared advance tool + fills the phase slot. The child's SMOKE phase
    re-validates the server receipt worker_build minted (bound to child_id + its artifact) -- a
    forged/cross-task/mismatched receipt fails CLOSED there. Returns the LAST build's receipt so
    the mission fan-in can independently re-validate it."""
    env = engine.get(child_id)
    intent = env.get("intent") or {}
    ctx = WorkerRunContext(
        task_id=child_id, key=intent.get("worker_key") or child_id,
        objective=intent.get("seeking") or "", owns_units=list(intent.get("owns_units") or []),
        artifact_lane=intent.get("artifact_lane") or f".cortex/worktrees/{child_id}",
        tier_profile=intent.get("tier_profile"), track=intent.get("proposed_track"),
        workspace=workspace)

    last = WorkerBuild(app_dir=None, checks=None, verdict_id=None, passed=False, status="bad_slot")
    for _ in range(_MAX_CHILD_STEPS):
        state, seq = env["state"], env["seq"]
        if state in ("DONE", "ABANDONED"):
            break
        if state == "SCAFFOLD":
            last = worker_build(ctx)   # builds the artifact + mints THIS child's server receipt
            env = engine.step(child_id, "cortex_submit_artifact",
                              {"status": last.status, "app_dir": last.app_dir,
                               "checks": last.checks, "skills": last.skills},
                              seq=seq, actor=actor)
        elif state == "SMOKE":
            env = engine.step(child_id, "cortex_submit_smoke",
                              {"verdict_id": last.verdict_id}, seq=seq, actor=actor)
        elif state == "SHOW":
            env = engine.step(child_id, "cortex_submit_reaction",
                              {"reaction": None, "proposals": []}, seq=seq, actor=actor)
        elif state == "CLOSEOUT":
            env = engine.step(child_id, "cortex_write_closeout",
                              {"task": ctx.objective,
                               "result": f"passed={last.passed}",
                               "gate": {"passed": last.passed, "verdict_id": last.verdict_id}},
                              seq=seq, actor=actor)
        else:  # STALLED / unknown: stop honestly, resumption is a separate act
            break
        if not env.get("ok"):
            env = engine.get(child_id)   # a refusal writes nothing; resync + retry

    final = engine.get(child_id)
    return WorkerOutcome(task_id=child_id, key=ctx.key, owns_units=ctx.owns_units,
                         verdict_id=last.verdict_id, app_dir=last.app_dir, checks=last.checks,
                         passed=bool(last.passed), final_state=final["state"])


# --------------------------------------------------------------------------- #
# Deterministic fan-in reconciliation -- the judge-free completion authority  #
# --------------------------------------------------------------------------- #
def _reconcile(required_units: list[str], outcomes: list[WorkerOutcome],
               workspace: str | None) -> dict[str, Any]:
    """Server-side, DETERMINISTIC fan-in: RE-validate every child's receipt (task + artifact +
    checks + gate identity bound) and require every REQUIRED unit be owned by a child whose
    receipt validates as PASSED. No LLM, no ranking of unlike outputs -- an aggregate attestation.
    Returns {passed, unit_status, missing_units, receipts, reducer_digest}."""
    unit_status: dict[str, bool] = {u: False for u in required_units}
    receipt_ids: dict[str, str] = {}
    for oc in outcomes:
        ok = False
        if oc.verdict_id and oc.app_dir:
            res = receipts.validate_smoke_receipt(
                oc.verdict_id, task_id=oc.task_id,
                expected_artifact_digest=receipts.digest_dir(oc.app_dir),
                expected_checks_digest=receipts.digest_checks(oc.checks),
                workspace=workspace)
            ok = bool(res.get("ok") and res.get("passed"))
            if oc.verdict_id:
                receipt_ids[oc.key] = oc.verdict_id
        for u in oc.owns_units:
            if u in unit_status:
                unit_status[u] = unit_status[u] or ok
    missing = sorted(u for u, ok in unit_status.items() if not ok)
    return {"passed": not missing, "unit_status": unit_status, "missing_units": missing,
            "receipts": receipt_ids}


def _apply_reducers(manifest: dict[str, Any], outcomes: list[WorkerOutcome]) -> dict[str, Any]:
    """Fold the child artifacts per the manifest's DECLARED reducers into a deterministic
    aggregate digest (v0: order the per-worker artifact digests by the reducer's declared merge
    order and hash them -- no ranking, no model). This is the MERGE attestation, not a verdict."""
    digests = {oc.key: receipts.digest_dir(oc.app_dir) if oc.app_dir else None for oc in outcomes}
    reducers = manifest.get("reducers") or []
    order: list[str] = []
    for r in reducers:
        if isinstance(r, dict) and isinstance(r.get("order"), list):
            order.extend(k for k in r["order"] if k in digests)
    if not order:
        order = sorted(digests)
    h = hashlib.sha256()
    for k in order:
        h.update(k.encode("utf-8"))
        h.update(b"\x00")
        h.update((digests.get(k) or "").encode("utf-8"))
        h.update(b"\x00")
    return {"order": order, "reducer_digest": "sha256:" + h.hexdigest(),
            "reducers": reducers, "per_worker_digest": digests}


# --------------------------------------------------------------------------- #
# run_mission -- the coordinator                                              #
# --------------------------------------------------------------------------- #
def run_mission(goal: str, *,
                db_path: str = ":memory:", workspace: str | None = None,
                engine: StateEngine | None = None,
                propose: Callable[..., dict[str, Any] | None] | None = None,
                propose_tier: str = "ollama",
                worker_build: WorkerBuildFn | None = None,
                max_parallel: int | None = None,
                actor: str = "mission-driver", lease_s: int = 600) -> dict[str, Any]:
    """Decompose `goal` into heterogeneous child workers and drive them to a reconciled mission.

    Seams (all default to the real path; offline tests inject fakes so NO live/paid call runs):
      * `propose`      -> decomposer.propose_manifest (a FREE model PROPOSES the split).
      * `worker_build` -> _default_worker_build (one FREE executor builds a slice + mints its
                          server receipt). Tests inject a fake that writes a tiny artifact + mints
                          via receipts under the child's task_id (receipts' TEST-ONLY gate seam).

    Returns a status dict:
      * "abstained"     -> the proposer returned no manifest (nothing spawned).
      * "rejected"      -> validate_manifest FAILED; `problems` names why; NOTHING was spawned.
      * "failed_closed" -> a REQUIRED unit's child receipt did not validate as passed; no MERGE.
      * "done"          -> all required children reconciled + the mission chart reached DONE.
      * "incomplete"    -> the mission chart did not reach DONE for another reason.
    """
    propose = propose or decomposer.propose_manifest
    build_fn = worker_build or _default_worker_build

    # --- 1) PROPOSE (free model) -------------------------------------------------------------- #
    manifest = propose(goal, tier=propose_tier)
    if not isinstance(manifest, dict):
        return {"status": "abstained", "reason": "proposer returned no manifest",
                "goal": goal, "manifest": None, "worker_ids": [], "outcomes": []}

    # --- 2) VALIDATE (deterministic, judge-free) -- reject BEFORE any spawn ------------------- #
    ok, problems = decomposer.validate_manifest(manifest)
    if not ok:
        # No mission task, no children -- a rejected proposal spawns NOTHING (no model bypass).
        return {"status": "rejected", "problems": problems, "goal": goal,
                "manifest": manifest, "mission_id": None, "worker_ids": [], "outcomes": []}

    workers = [w for w in manifest.get("workers", []) if isinstance(w, dict)]
    engine_workers = [_engine_worker(w) for w in workers]
    required_units = list((manifest.get("coverage_spec") or {}).get("required_units") or [])

    own_engine = engine is None
    if own_engine:
        engine = StateEngine(db_path, gate=make_universal_gate(), workspace=workspace)
    trail: list[dict[str, Any]] = []
    try:
        # --- 3) mission chart: INTAKE -> PARTITION -> DISPATCH (atomic child creation) -------- #
        mission_intent = {"seeking": goal, "role": "mission",
                          "coverage_spec": manifest.get("coverage_spec") or {}}
        mid = engine.create_task(mission_intent, track="mission", lease_s=lease_s, actor=actor)

        def advance(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
            env = engine.get(mid)
            out = engine.step(mid, tool, payload, seq=env["seq"], actor=actor)
            trail.append({"state": env["state"], "tool": tool, "ok": bool(out.get("ok")),
                          "to_state": out.get("state"), "gate": out.get("gate")})
            return out

        env = advance("cortex_submit_mission_contract",
                      {"contract": {"coverage_spec": manifest.get("coverage_spec") or {},
                                    "reducers": manifest.get("reducers") or [],
                                    "acceptance_criteria": {"policy": "all_required_pass"}}})
        if not env.get("ok"):
            return _incomplete(engine, mid, trail, "INTAKE advance refused", [])

        env = advance("cortex_submit_partition", {"workers": engine_workers})
        if not env.get("ok"):
            # The coverage gate rejected the partition (should not happen post-validate_manifest,
            # but fail CLOSED and surface the engine's own reason rather than spawn anything).
            return _incomplete(engine, mid, trail, "PARTITION gate refused", [],
                               gate=env.get("gate"))

        env = advance("cortex_dispatch_mission", {})
        worker_ids = list(env.get("worker_ids") or [])
        if not env.get("ok") or not worker_ids:
            return _incomplete(engine, mid, trail, "DISPATCH refused (claim conflict?)", [],
                               gate=env.get("gate"))

        # --- 4) spawn child drivers IN PARALLEL (bounded); each mints its OWN receipt --------- #
        # Bound: the cohort size, itself already capped by coverage_spec.max_workers (<= 8);
        # provider pressure is bounded by model_dispatch's own per-tier concurrency slots inside
        # the executor (fanout), so no new concurrency number is invented here.
        n = max_parallel or len(worker_ids)
        assert engine is not None
        eng = engine
        with ThreadPoolExecutor(max_workers=max(1, n)) as pool:
            futs = {pool.submit(_run_one_child, eng, cid, build_fn, workspace, actor): cid
                    for cid in worker_ids}
            outcomes = [f.result() for f in as_completed(futs)]
        # deterministic order: by dispatch order
        order = {cid: i for i, cid in enumerate(worker_ids)}
        outcomes.sort(key=lambda oc: order.get(oc.task_id, 99))

        # --- 5) DETERMINISTIC fan-in reconciliation (judge-free completion authority) --------- #
        recon = _reconcile(required_units, outcomes, workspace)
        status_obj = engine.mission_status(mid)
        recon["all_done"] = status_obj.get("all_done")
        recon["cohort_consistent"] = status_obj.get("cohort_consistent")
        _write_reconciliation(workspace, mid, required_units, outcomes, recon)

        # A required child failing its receipt (or a child not reaching DONE) => fail CLOSED.
        if not (recon["passed"] and status_obj.get("all_done")
                and status_obj.get("cohort_consistent")):
            return {"status": "failed_closed", "mission_id": mid,
                    "manifest": manifest, "worker_ids": worker_ids, "outcomes": outcomes,
                    "reconciliation": recon, "state": engine.get(mid)["state"], "trail": trail}

        # --- 6) MERGE (declared reducers) -> REVIEW (scope) -> CLOSEOUT ----------------------- #
        reduce_rec = _apply_reducers(manifest, outcomes)
        env = advance("cortex_submit_merge", {"merge": reduce_rec})
        if not env.get("ok"):
            return _incomplete(engine, mid, trail, "MONITOR->MERGE refused", worker_ids,
                               outcomes=outcomes, reconciliation=recon)
        env = advance("cortex_submit_review",
                      {"review": "merged", "reducer_digest": reduce_rec["reducer_digest"],
                       "scope_check": {"delivered": goal, "matches_request": True}})
        if not env.get("ok"):
            return _incomplete(engine, mid, trail, "MERGE->REVIEW refused", worker_ids,
                               outcomes=outcomes, reconciliation=recon)
        env = advance("cortex_submit_review",
                      {"review": "final scope check",
                       "scope_check": {"delivered": goal, "matches_request": True}})
        if not env.get("ok"):
            return _incomplete(engine, mid, trail, "REVIEW->CLOSEOUT refused", worker_ids,
                               outcomes=outcomes, reconciliation=recon)
        env = advance("cortex_write_closeout",
                      {"task": goal,
                       "result": f"mission reconciled: {len(worker_ids)} workers, "
                                 f"{len(required_units)} units all_required_pass",
                       "reducer_digest": reduce_rec["reducer_digest"]})

        final = engine.get(mid)
        return {"status": "done" if final["state"] == "DONE" else "incomplete",
                "mission_id": mid, "manifest": manifest, "worker_ids": worker_ids,
                "outcomes": outcomes, "reconciliation": recon, "merge": reduce_rec,
                "state": final["state"], "trail": trail}
    finally:
        if own_engine:
            engine.close()


def _incomplete(engine: StateEngine, mid: str, trail: list[dict[str, Any]], reason: str,
                worker_ids: list[str], **extra: Any) -> dict[str, Any]:
    out = {"status": "incomplete", "mission_id": mid, "reason": reason,
           "worker_ids": worker_ids, "state": engine.get(mid)["state"], "trail": trail}
    out.update(extra)
    return out


def _write_reconciliation(workspace: str | None, mission_id: str, required_units: list[str],
                          outcomes: list[WorkerOutcome], recon: dict[str, Any]) -> None:
    """Append the deterministic reconciliation attestation to a gitignored ops-local ledger --
    the aggregate 'all_required_pass' record (NOT an LLM verdict, NOT a winner receipt). Telemetry
    must never break the run, so any IO error is swallowed."""
    try:
        if workspace is None or not Path(str(workspace)).is_dir():
            return
        root = Path(str(workspace)) / "ops-local"
        root.mkdir(parents=True, exist_ok=True)
        rec = {"mission_id": mission_id, "required_units": required_units,
               "cohort": [oc.task_id for oc in outcomes],
               "receipts": recon.get("receipts"), "policy": "all_required_pass",
               "passed": recon.get("passed"), "missing_units": recon.get("missing_units"),
               "ts": time.time()}
        with (root / "mission-reconciliation.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 -- telemetry must never break the mission
        pass
