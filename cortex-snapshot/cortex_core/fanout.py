"""Fan-out / fan-in parallel executor -- the coordinator that sits AROUND the existing
deterministic gate. Implements docs/research/fanout-executor-design-2026-07-11.md.

    one task
      -> N mid-tier FREE models fill the ONE slot IN PARALLEL   (fan-out)
      -> the DETERMINISTIC gate verifies each build              (already exists)
      -> compare passing results by cost / speed / quality       (fan-in, deterministic)
      -> a STRONG non-executor model reviews ONLY failures        (non-verdict)
      -> save each failure as a permanent, replayable regression check

This is a thin coordinator over primitives that already exist and are already unit-testable:
`vague_build.drive()` (the per-executor build+gate spine), `app_gates.run_done_checks()` (the
verdict), `judge.concurrency_slot()` (cross-process provider caps), `judge.apply_min_max_tokens()`
(reasoning-token floors), and `research._llm_complete()` (model-agnostic, Retry-After-honoring
dispatch). No new gate, no new dispatcher -- just glue.

HARD INVARIANTS (fail-closed, enforced at module load AND fanout() entry):
  * Executors are FREE models ONLY. Paid tiers and premium reviewer tiers
    (opus/sonnet/fable-max/haiku) are hard-rejected -- reviewers are NON-executors.
  * The DETERMINISTIC gate selects the winner. An LLM may only diagnose FAILURES; it can
    never overturn a verdict or pick among passers. The objective-lane judge-free invariant.
  * Concurrency caps + token floors are enforced by REUSING the shared cortex_core.model_dispatch
    primitives (concurrency_slot / apply_min_max_tokens), never by bypassing them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cortex_core import app_gates, model_dispatch, research, vague_build
from cortex_core.app_contract import GateVerdict
from cortex_core.config import resolve_workspace


# --------------------------------------------------------------------------- #
# 1. The executor registry -- FREE models only                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExecutorSpec:
    name: str            # stable id used in scoreboard/regressions
    tier: str            # a judge.py tier that supplies endpoint URL + key
    model_id: str | None  # model_override; None -> use the tier's cfg.model
    lane: str            # the SHARED provider budget this competes for (see §4)
    cost_weight: int     # fan-in scarcity weight (all $0; this ranks lane pressure)


# Pinned per docs/MODELS-TIER-LIST.md + docs/OPERATING-PLAN.md role table. Model ids are the
# exact served ids (cross-checked against vague_build._TIER_ALIASES + judge tier constants).
EXECUTORS: dict[str, ExecutorSpec] = {
    "laguna-m.1": ExecutorSpec("laguna-m.1", "openrouter",    "poolside/laguna-m.1:free",   "openrouter",    1),
    "north-mini": ExecutorSpec("north-mini", "openrouter",    "cohere/north-mini-code:free", "openrouter",   1),
    "laguna-xs":  ExecutorSpec("laguna-xs",  "openrouter",    "poolside/laguna-xs-2.1:free", "openrouter",   1),
    "big-pickle": ExecutorSpec("big-pickle", "opencode-zen",  "big-pickle",                  "opencode",     0),
    "aux":        ExecutorSpec("aux",        "ninerouter-aux", None,                         "ninerouter-aux", 0),
    "qwen35b":    ExecutorSpec("qwen35b",    "qwen35b",       None,                          "qwen35b",       2),  # slow bulk, opt-in
}

DEFAULT_EXECUTORS = ["laguna-m.1", "big-pickle", "north-mini", "aux"]  # the task's 4

# --- fail-closed guard sets -----------------------------------------------------------------
# Free tiers an executor MAY run on (positive allowlist -- re-asserted per OPERATING-PLAN role
# table: only these free lanes are executor-eligible).
_FREE_EXECUTOR_TIERS = frozenset({
    "openrouter", "opencode-zen", "opencode-zen2", "ninerouter-aux", "qwen35b",
})

# Premium in-harness reviewer tiers -- reviewer-only, NEVER executors (§8).
_PREMIUM_REVIEWER_TIERS = frozenset({"opus", "sonnet", "fable-max", "haiku", "chatgpt-5.5xhigh"})

# Paid lanes hard-banned as executors (OPERATING-PLAN "BANNED"): the paid umans/glm-5.2 9router
# connection, the direct paid glm/deepseek lanes, and opencode-GO (cheap-sub, not free, and can
# serve the banned glm-5.2/deepseek-v4-pro/qwen3.7-max/kimi/minimax models).
_BANNED_PAID_TIERS = frozenset({
    "ninerouter", "glm5.2", "deepseek", "openrouter-paid",
    "opencode", "opencode2",
})

_BANNED_EXECUTOR_TIERS = _PREMIUM_REVIEWER_TIERS | _BANNED_PAID_TIERS

ORACLE_AUTHOR = "deterministic:build_skills"   # never a model id (anti-circularity, §6)


class BannedExecutorError(RuntimeError):
    """Raised when a non-free / premium / paid tier is offered as an executor (fail-closed)."""


def _assert_free_executor(spec: ExecutorSpec) -> None:
    """Fail-closed: an executor MUST be a free tier, never a premium/paid one, and an
    opencode-zen model_override must still be in the tier's own allowlist (override bypasses it)."""
    if spec.tier in _BANNED_EXECUTOR_TIERS:
        raise BannedExecutorError(
            f"executor {spec.name!r} tier {spec.tier!r} is a banned (paid/premium) tier -- "
            "executors are FREE models only; premium tiers are reviewer-only")
    if spec.tier not in _FREE_EXECUTOR_TIERS:
        raise BannedExecutorError(
            f"executor {spec.name!r} tier {spec.tier!r} is not on the free-executor allowlist "
            f"{sorted(_FREE_EXECUTOR_TIERS)}")
    if spec.tier in model_dispatch.OPENCODE_ZEN_TIERS and spec.model_id is not None:
        if spec.model_id not in model_dispatch.OPENCODE_ZEN_MODEL_ALLOWLIST:
            raise BannedExecutorError(
                f"opencode-zen model_override {spec.model_id!r} is not in "
                f"OPENCODE_ZEN_MODEL_ALLOWLIST (model_override bypasses the tier check -- re-asserted)")


def _assert_independence(spec: ExecutorSpec, reviewer_id: str | None) -> None:
    """Anti-circularity (§6): the oracle author is deterministic code, and the reviewer is a
    strong NON-executor. Never one model both builds and judges."""
    assert spec.name != ORACLE_AUTHOR, "an executor may never be the oracle author"
    assert spec.name != reviewer_id, "reviewer must not be an executor"
    assert reviewer_id not in EXECUTORS, "reviewer (strong model) must not be in the executor pool"


# Fail-closed at import: every shipped executor must pass the free-only guard.
for _spec in EXECUTORS.values():
    _assert_free_executor(_spec)


# --------------------------------------------------------------------------- #
# 2. Dispatch: per-executor student, reusing judge.py (never bypassing it)    #
# --------------------------------------------------------------------------- #
# Fan-out lane caps. Registered under a distinct "fanout-lane:*" key space so they add a
# cross-process funnel for the fan-out WITHOUT changing the global behavior of the bare
# "openrouter"/"opencode" tiers for non-fanout callers. The paced OpenRouter free lane is a
# true 1-wide funnel (MODELS-TIER-LIST §TIER-1b "~1 call per few seconds"); the shared
# opencode account budget is ~4 lanes (OPERATING-PLAN "opencode-go + opencode-zen SHARED").
# qwen35b already has a global tier cap of 2 (judge.MAX_CONCURRENT_BY_TIER); ninerouter-aux is
# ungated by design. NOTE (honest debt, design §11.2): this key space does NOT coordinate with
# non-fanout openrouter/opencode traffic -- global cross-caller coordination for those shared
# accounts remains the open §4a gap.
_FANOUT_LANE_CAPS: dict[str, int] = {"openrouter": 1, "opencode": 4}
_OPENROUTER_MIN_INTERVAL_S = 3.0

for _lane, _cap in _FANOUT_LANE_CAPS.items():
    model_dispatch.MAX_CONCURRENT_BY_TIER.setdefault(f"fanout-lane:{_lane}", _cap)

_pace_lock = threading.Lock()
_last_call_at: dict[str, float] = {}


def _pace_lane(lane: str) -> None:
    """Stagger bursty free lanes (OpenRouter) so N executors submit spaced, not simultaneously."""
    if lane != "openrouter":
        return
    with _pace_lock:
        now = time.monotonic()
        last = _last_call_at.get(lane, 0.0)
        wait = _OPENROUTER_MIN_INTERVAL_S - (now - last)
        if wait > 0:
            time.sleep(wait)
        _last_call_at[lane] = time.monotonic()


def _build_student(spec: ExecutorSpec) -> Callable[[str], str]:
    """ExecutorSpec -> a bound single-shot completion. Mirrors vague_build._default_student but
    carries the model_override, applies the per-tier reasoning-token FLOOR, and acquires the
    fan-out lane slot (in addition to the tier-level slot _llm_complete acquires internally)."""
    _assert_free_executor(spec)  # belt-and-suspenders: never dispatch a banned tier

    def student(prompt: str) -> str:
        with model_dispatch.concurrency_slot(f"fanout-lane:{spec.lane}"):
            _pace_lane(spec.lane)
            return research._llm_complete(
                prompt, spec.tier,
                max_tokens=model_dispatch.apply_min_max_tokens(spec.tier, 6000),
                model_override=spec.model_id,
            ) or ""

    return student


# --------------------------------------------------------------------------- #
# 3. Per-executor run                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class ExecAttempt:
    executor: str
    status: str                          # "built" | "bad_slot" | "no_skill" | "bad_primary"
    passed: bool
    failure_class: str | None            # gate class, or synthetic "SLOT_FAIL" for bad_slot
    app_dir: str | None
    slot: dict | None
    skills: list[str]
    skipped: list[dict]
    attempts: int                        # slot-fill attempts (from drive)
    elapsed_s: float                     # wall clock of the whole build+gate
    check_specs: list[dict]              # the FULL merged VISIBLE specs the gate ran
    verdict_detail: list[tuple[str, bool]]  # (kind, passed) per check -- reviewer input
    seed: int
    tier: str = ""
    model_id: str | None = None
    coach_view: dict = field(default_factory=dict)
    # The server-owned verdict receipt this executor minted over ITS OWN candidate artifact
    # (fan-in coupling, 2026-07-15). None when no gate ran (bad_slot) or receipt mode is off.
    # Each executor mints its OWN receipt -> no shared holder to race; the fan-in carries the
    # WINNER's verdict_id forward as the task's SCAFFOLD verdict.
    verdict_id: str | None = None


def _default_gate_impl(app_dir: Path, checks: list[dict], *, seed: int,
                       hidden_dir: str | Path | None = None) -> GateVerdict:
    """THE gate call (pure app_gates.run_done_checks -- no model). Seed pinned for reproducibility;
    optional hidden holdout checks attached (never persisted into the regression corpus)."""
    hidden = app_gates.load_hidden_checks(Path(hidden_dir), "crud") if hidden_dir else None
    return app_gates.run_done_checks(
        app_dir, checks, hidden_checks=hidden,
        ctx=app_gates.GateContext(seed=seed))


def _to_attempt(spec: ExecutorSpec, r: dict, captured: dict, elapsed: float,
                seed: int) -> ExecAttempt:
    status = r.get("status")
    common = dict(executor=spec.name, tier=spec.tier, model_id=spec.model_id,
                  elapsed_s=round(elapsed, 3), seed=seed)
    if status == "built":
        verdict: GateVerdict | None = captured.get("verdict")
        detail = ([(res.kind, bool(res.passed)) for res in verdict.results]
                  if verdict is not None else list(r.get("checks", [])))
        return ExecAttempt(
            status="built", passed=bool(r.get("passed")),
            failure_class=r.get("failure_class"),
            app_dir=r.get("app_dir"), slot=r.get("slot"),
            skills=list(r.get("skills", [])), skipped=list(r.get("skipped", [])),
            attempts=int(r.get("attempts", 0)),
            check_specs=list(captured.get("checks", [])),
            verdict_detail=detail, coach_view=dict(r.get("verdict", {})),
            verdict_id=captured.get("verdict_id"), **common)
    # bad_slot / no_skill / bad_primary: a real executor failure, no app snapshot exists.
    fclass = {"bad_slot": "SLOT_FAIL", "no_skill": "NO_SKILL",
              "bad_primary": "BAD_PRIMARY"}.get(status, "SLOT_FAIL")
    return ExecAttempt(
        status=status or "bad_slot", passed=False, failure_class=fclass,
        app_dir=None, slot=None, skills=[], skipped=[],
        attempts=int(r.get("attempts", 0)), check_specs=[], verdict_detail=[], **common)


def run_one_executor(utterance: str, spec: ExecutorSpec, *, seed: int,
                     out_root: str | Path, retries: int = 1,
                     hidden_dir: str | Path | None = None,
                     gate_sem: threading.Semaphore | None = None,
                     workspace: str | Path | None = None,
                     student_factory: Callable[[ExecutorSpec], Callable[[str], str]] = _build_student,
                     gate_impl: Callable[..., GateVerdict] | None = None,
                     receipt_task_id: str | None = None,
                     receipt_run_checks: Callable[..., GateVerdict] | None = None) -> ExecAttempt:
    """Drive ONE executor through the build+gate spine. `student_factory`/`gate_impl` are injected
    for offline tests (real ones default). The gate closure (a) pins the seed, (b) attaches hidden
    holdout checks, (c) captures the FULL merged visible checks + verdict -- which drive's return
    dict deliberately narrows. Zero change to drive.

    RECEIPT MODE (fan-in coupling, 2026-07-15): when `receipt_task_id` is set, this executor's
    gate is the SERVER-OWNED verdict path -- it grades through
    `receipts.run_and_record_smoke_verdict`, which RUNS the deterministic gate and mints a
    receipt bound to THIS executor's own candidate artifact (digest) + checks + gate identity,
    under `receipt_task_id`. The minted `verdict_id` is captured PER-EXECUTOR (its own closure),
    so N parallel executors never race a shared holder. `receipt_run_checks=None` (production)
    runs the real `app_gates.run_done_checks` (gate_id=REAL_GATE_ID); offline tests inject a
    2-arg gate AND open receipts' test seam. In receipt mode `gate_impl`/`hidden_dir` are not
    used -- the receipt gate IS the single grading run (mirrors hybrid_build's single-executor
    receipted_gate), so the ranking bit and the server verdict come from ONE gate execution."""
    gate_impl = gate_impl or _default_gate_impl
    student = student_factory(spec)
    app_out = Path(out_root) / spec.name / "app"   # unique parent -> no path-probe clash (§4b)
    captured: dict = {}

    def gate(app_dir: Path, checks: list[dict]) -> GateVerdict:
        if gate_sem:
            gate_sem.acquire()
        try:
            if receipt_task_id is not None:
                # Per-executor server receipt over THIS candidate artifact (no shared holder).
                from cortex_core import receipts
                vid, v = receipts.run_and_record_smoke_verdict(
                    task_id=receipt_task_id, app_dir=app_dir, checks=checks,
                    run_checks=receipt_run_checks, workspace=workspace)
                captured["checks"], captured["verdict"], captured["verdict_id"] = checks, v, vid
                return v
            v = gate_impl(app_dir, checks, seed=seed, hidden_dir=hidden_dir)
            captured["checks"], captured["verdict"] = checks, v
            return v
        finally:
            if gate_sem:
                gate_sem.release()

    t0 = time.monotonic()
    r = vague_build.drive(utterance, llm=student, gate=gate, out_dir=app_out,
                          retries=retries, workspace=workspace)
    elapsed = time.monotonic() - t0
    return _to_attempt(spec, r, captured, elapsed, seed)


# --------------------------------------------------------------------------- #
# 4. Fan-out -> fan-in orchestration                                          #
# --------------------------------------------------------------------------- #
@dataclass
class FanoutResult:
    utterance: str
    seed: int
    attempts: list[ExecAttempt]
    ranking: list[ExecAttempt]        # passers only, best-first (deterministic)
    winner: ExecAttempt | None
    failures: list[ExecAttempt]
    regression_paths: list[str]
    review: dict | None               # strong-model diagnosis packet (§8), when there were failures


def _restrict_to_available(executors: list[str], workspace: str | Path | None) -> list[str]:
    """Prefer executors the probe (`cortex-models` / model_probe.py) marked AVAILABLE. The
    probe reports availability per dispatch TIER; an executor is kept if its tier is live.

    Degrades gracefully: if no probe has been run (model_availability.json absent) OR the
    filter would leave zero executors (stale/partial probe), the original list is returned
    unchanged -- the probe is an optimization, never a hard gate that can strand a run."""
    try:
        from . import model_probe
        available_tiers = model_probe.load_available_executors(workspace)
    except Exception:  # noqa: BLE001 -- probe module/file issue must never break fanout
        available_tiers = None
    if not available_tiers:
        return executors  # no probe yet -> no restriction
    kept = [e for e in executors if e in EXECUTORS and EXECUTORS[e].tier in available_tiers]
    return kept or executors  # empty intersection (stale probe) -> degrade to the full list


def _rank_key(a: ExecAttempt) -> tuple:
    cost = EXECUTORS[a.executor].cost_weight if a.executor in EXECUTORS else 9
    return (
        cost,                      # cost: cheaper (less rate-limit-scarce) lane first
        len(a.skipped),            # quality: fewer dropped follow-ons
        a.attempts,                # quality: fewer slot retries
        round(a.elapsed_s, 2),     # speed
        a.executor,                # deterministic tiebreak
    )


def fanout_supported(route_skill_id: str | None) -> bool:
    """The state-machine trigger (design §"when the task supports it"): fan-out applies when the
    task has ONE independently-fillable slot -- i.e. a `fresh_build` primary (the single JSON slot
    N executors can each fill on their own). Follow-on-only / non-build routes are single-lane and
    stay on the ordinary single-executor path. See the documented seam in hybrid_build.py SCAFFOLD."""
    return route_skill_id == "scaffold-crud-sqlite"


def rank_passers(passers: list[ExecAttempt]) -> list[ExecAttempt]:
    """The fan-in comparison: DETERMINISTIC ranking of gate-PASSING attempts by cost/quality/speed.
    No model participates -- the gate already decided PASS/FAIL, this only orders the survivors."""
    return sorted(passers, key=_rank_key)


def _task_signature(a: ExecAttempt) -> str:
    return "+".join(sorted(a.skills)) if a.skills else "(none)"


def fanout(utterance: str, executors: list[str] | None = None, *, seed: int | None = None,
           retries: int = 1, gate_workers: int | None = None,
           hidden_dir: str | Path | None = None,
           reviewer: str | None = None, workspace: str | Path | None = None,
           sink: str | Path | None = None,
           student_factory: Callable[[ExecutorSpec], Callable[[str], str]] = _build_student,
           gate_impl: Callable[..., GateVerdict] | None = None,
           receipt_task_id: str | None = None,
           receipt_run_checks: Callable[..., GateVerdict] | None = None) -> FanoutResult:
    """Dispatch N FREE executors IN PARALLEL to fill ONE slot, gate each build, then fan in:
    the DETERMINISTIC gate partitions pass/fail and a deterministic rank picks the winner among
    passers (no LLM in the verdict path). Failures are frozen as replayable regression fixtures
    and packaged for a strong NON-executor reviewer (which can diagnose but never overturn).

    `workspace` supplies the SKILLS the executors build against (the real checkout by default);
    `sink` is where the scoreboard + regression corpus are written (defaults to `workspace`).

    RECEIPT MODE (the state-machine fan-in coupling): pass `receipt_task_id` to have EACH
    executor mint its OWN server-owned verdict receipt over ITS OWN candidate artifact (bound to
    task + artifact digest + checks + gate identity). Every `ExecAttempt` then carries its own
    `verdict_id`; the winner's `verdict_id` is what hybrid_build carries forward as the task's
    SCAFFOLD verdict. `receipt_run_checks=None` = production (real deterministic gate)."""
    sink = sink if sink is not None else workspace
    executors = executors or DEFAULT_EXECUTORS
    executors = _restrict_to_available(executors, workspace)
    specs = [EXECUTORS[e] for e in executors]
    # Fail-closed BEFORE any dispatch: free-only + independence.
    for s in specs:
        _assert_free_executor(s)
        _assert_independence(s, reviewer)

    seed = seed if seed is not None else int.from_bytes(os.urandom(4), "big")
    out_root = Path(tempfile.mkdtemp(prefix="cortex_fanout_"))
    gw = gate_workers or min(os.cpu_count() or 4, len(specs))
    gate_sem = threading.Semaphore(gw)

    with ThreadPoolExecutor(max_workers=len(specs) + gw) as ex:
        futs = {ex.submit(run_one_executor, utterance, s, seed=seed, out_root=out_root,
                          retries=retries, hidden_dir=hidden_dir, gate_sem=gate_sem,
                          workspace=workspace, student_factory=student_factory,
                          gate_impl=gate_impl, receipt_task_id=receipt_task_id,
                          receipt_run_checks=receipt_run_checks): s for s in specs}
        attempts = [f.result() for f in as_completed(futs)]

    # stable, executor-input order for reproducible output
    order = {name: i for i, name in enumerate(executors)}
    attempts.sort(key=lambda a: order.get(a.executor, 99))

    passers = [a for a in attempts if a.passed]
    failures = [a for a in attempts if not a.passed]
    ranking = rank_passers(passers)
    regs = [p for a in failures if a.app_dir
            for p in [save_regression(a, utterance, sink)] if p]
    review = review_failures(utterance, failures, reviewer, sink) if failures else None
    _write_scoreboard(utterance, seed, attempts, ranking, sink)
    return FanoutResult(utterance, seed, attempts, ranking,
                        ranking[0] if ranking else None, failures, regs, review)


# --------------------------------------------------------------------------- #
# 5. Failure -> permanent, replayable regression fixture (§7b)                #
# --------------------------------------------------------------------------- #
def _sink_root(sink: str | Path | None) -> Path:
    """The literal output root for scoreboard + regression corpus. A caller-supplied path is used
    verbatim (test isolation / a mirrored offload dir); otherwise the resolved workspace."""
    return Path(sink) if sink is not None else Path(resolve_workspace(None))


def _regressions_root(sink: str | Path | None) -> Path:
    return _sink_root(sink) / "regressions"


def _slug(s: str, n: int = 40) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in (s or "").lower())
    return "-".join(filter(None, keep.split("-")))[:n] or "task"


def save_regression(a: ExecAttempt, utterance: str,
                    workspace: str | Path | None) -> str | None:
    """Freeze a gate-caught failure as a negative fixture (mirrors the mutant corpus). Only the
    VISIBLE specs + failure_class are stored -- never a hidden holdout spec (§7b anti-oracle rule).
    Returns the fixture dir, or None if there is no app snapshot to freeze (e.g. bad_slot)."""
    if not a.app_dir or not Path(a.app_dir).exists():
        return None
    import hashlib
    h = hashlib.sha256(f"{utterance}|{a.executor}|{a.seed}".encode()).hexdigest()[:8]
    root = _regressions_root(workspace)
    fx = root / (a.failure_class or "UNKNOWN") / f"{_slug(utterance)}__{a.executor}__{h}"
    (fx / "app").mkdir(parents=True, exist_ok=True)
    # snapshot the FAILING rendered app (deterministic reproduction)
    for p in Path(a.app_dir).glob("*.py"):
        shutil.copy2(p, fx / "app" / p.name)
    (fx / "checks.json").write_text(json.dumps(a.check_specs, indent=2), encoding="utf-8")
    meta = {
        "utterance": utterance, "executor": a.executor, "tier": a.tier,
        "model_id": a.model_id, "seed": a.seed, "failure_class": a.failure_class,
        "skills": a.skills, "slot": a.slot, "coach_view": a.coach_view,
        "oracle_author": ORACLE_AUTHOR, "hash": h, "ts": time.time(),
    }
    (fx / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"hash": h, "failure_class": a.failure_class,
                             "executor": a.executor, "dir": str(fx)}) + "\n")
    return str(fx)


def _load_fixtures(regressions_dir: Path):
    for meta_p in Path(regressions_dir).glob("*/*/meta.json"):
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        checks = json.loads((meta_p.parent / "checks.json").read_text(encoding="utf-8"))
        yield meta, meta_p.parent / "app", checks


def replay_regressions(regressions_dir: str | Path,
                       gate_impl: Callable[..., GateVerdict] | None = None) -> list[dict]:
    """Re-run every fixture; assert the gate STILL fails it with the same class. NO model in the
    verdict path -- pure app_gates.run_done_checks (or an injected deterministic gate for tests).
    A CI gate-guard: if a gate change silently stops catching a known failure, still_fails=False."""
    gate_impl = gate_impl or _default_gate_impl
    out: list[dict] = []
    for meta, app_dir, checks in _load_fixtures(regressions_dir):
        v = gate_impl(app_dir, checks, seed=meta["seed"])
        out.append({"fixture": meta["hash"], "still_fails": not v.passed,
                    "class_match": v.failure_class == meta["failure_class"]})
    return out


# --------------------------------------------------------------------------- #
# 6. Fan-in record (scoreboard) + strong-model failure review (NON-verdict)   #
# --------------------------------------------------------------------------- #
def _write_scoreboard(utterance: str, seed: int, attempts: list[ExecAttempt],
                      ranking: list[ExecAttempt], workspace: str | Path | None) -> None:
    """Append one fan-in record -- the concrete local stand-in for the Langfuse fan-in step.
    Over many runs this answers WHICH free builder wins for WHICH task shape (task_signature)."""
    try:
        root = _sink_root(workspace) / "ops-local"
        root.mkdir(parents=True, exist_ok=True)
        rec = {
            "utterance": utterance, "seed": seed,
            "task_signature": _task_signature(ranking[0]) if ranking else None,
            "winner": ranking[0].executor if ranking else None,
            "per_executor": {a.executor: {"passed": a.passed, "elapsed_s": a.elapsed_s,
                                          "attempts": a.attempts, "skipped": len(a.skipped),
                                          "failure_class": a.failure_class} for a in attempts},
        }
        with (root / "fanout-scoreboard.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 -- telemetry must never break the run
        pass


def review_failures(utterance: str, failures: list[ExecAttempt], reviewer: str | None,
                    workspace: str | Path | None) -> dict:
    """Package failures for a STRONG NON-executor reviewer. The reviewer MAY read full harness-side
    (kind, passed) detail (it is not the coach/student and its output never re-enters an executor
    prompt) but MUST NOT overturn a verdict -- the gate is ground truth. Its output is a diagnosis +
    PROPOSED new oracles, which a strong model later implements as deterministic checks.

    Cost discipline: this NEVER auto-spends a premium/paid tier. `reviewer=None` (default) returns
    the structured packet for the orchestrating strong agent to review out-of-band (design §8
    option 1, preferred). A free non-executor tier string (e.g. 'nemotron-ultra') is dispatched via
    research._llm_complete; a premium CLI tier is deferred to the agent, not auto-called here."""
    packet = {
        "utterance": utterance,
        "reviewer": reviewer,
        "failures": [{"executor": f.executor, "failure_class": f.failure_class,
                      "slot": f.slot, "verdict_detail": f.verdict_detail} for f in failures],
        "note": "reviewer diagnoses + proposes new oracles; it CANNOT overturn the gate verdict.",
    }
    if not reviewer or reviewer in _PREMIUM_REVIEWER_TIERS:
        packet["dispatch"] = "deferred_to_orchestrating_agent"
        return packet
    # Free non-executor reviewer -> autonomous diagnosis (still non-verdict).
    assert reviewer not in EXECUTORS, "reviewer must not be an executor"
    base, override = vague_build._resolve_tier(reviewer)
    prompt = ("You are a strong REVIEWER. Diagnose why each build FAILED its deterministic gate "
              "and propose new deterministic check kinds for cases the current oracles miss. You "
              "CANNOT overturn a verdict.\n\n" + json.dumps(packet["failures"], indent=2))
    try:
        diagnosis = research._llm_complete(
            prompt, base, max_tokens=model_dispatch.apply_min_max_tokens(base, 6000),
            model_override=override)
    except Exception:  # noqa: BLE001
        diagnosis = None
    packet["dispatch"] = "free_reviewer"
    packet["diagnosis"] = diagnosis
    return packet


# --------------------------------------------------------------------------- #
# 7. CLI                                                                       #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cortex-fanout",
        description="N FREE models fill ONE slot in parallel; the deterministic gate picks the winner")
    ap.add_argument("utterance", nargs="?", help="the vague task")
    ap.add_argument("--executors", default=",".join(DEFAULT_EXECUTORS),
                    help="comma list from: " + ",".join(EXECUTORS))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--gate-workers", type=int, default=None)
    ap.add_argument("--reviewer", default=None,
                    help="free non-executor tier for autonomous review; omit to defer to the agent")
    ap.add_argument("--hidden-dir", default=None)
    ap.add_argument("--replay-regressions", action="store_true")
    ap.add_argument("--regressions-dir", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.replay_regressions:
        rd = a.regressions_dir or _regressions_root(None)
        rows = replay_regressions(rd)
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(f"   {'OK' if row['still_fails'] and row['class_match'] else 'REGRESSED'}  "
                      f"{row['fixture']}  still_fails={row['still_fails']} class_match={row['class_match']}")
        return 0 if all(r["still_fails"] for r in rows) else 1

    if not a.utterance:
        ap.error("utterance is required unless --replay-regressions")

    execs = [e.strip() for e in a.executors.split(",") if e.strip()]
    unknown = [e for e in execs if e not in EXECUTORS]
    if unknown:
        print(f"[config error] unknown executor(s): {unknown}")
        return 2
    try:
        r = fanout(a.utterance, executors=execs, seed=a.seed, retries=a.retries,
                   gate_workers=a.gate_workers, reviewer=a.reviewer, hidden_dir=a.hidden_dir)
    except BannedExecutorError as e:
        print(f"[config error] {e}")
        return 2

    if a.json:
        print(json.dumps({
            "utterance": r.utterance, "seed": r.seed,
            "winner": r.winner.executor if r.winner else None,
            "ranking": [a.executor for a in r.ranking],
            "attempts": {a.executor: {"passed": a.passed, "status": a.status,
                                      "failure_class": a.failure_class,
                                      "elapsed_s": a.elapsed_s} for a in r.attempts},
            "regressions": r.regression_paths,
        }, indent=2))
    else:
        for at in r.attempts:
            tag = "PASS" if at.passed else f"FAIL ({at.failure_class})"
            print(f"   {tag:16} {at.executor:12} {at.elapsed_s:6.2f}s  skills={'+'.join(at.skills)}")
        print(f"=> winner: {r.winner.executor if r.winner else '(none passed)'}  seed={r.seed}")
        if r.regression_paths:
            print(f"   saved {len(r.regression_paths)} regression fixture(s)")
    return 0 if r.winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
