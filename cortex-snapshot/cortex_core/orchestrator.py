"""Driver -> tiered-worker parallel orchestration loop (GAP I3).

`MULTIAGENT.md` was pattern-only: it described a strong driver decomposing a task and
fanning out to cheaper tiered workers, but no spawner code existed. This module is that
spawner, built on the substrate that already exists rather than inventing a new one:

    task
      -> decompose()                 a STRONG driver splits the task into subtasks, each
                                     tagged with a ROLE (research/spec vs. mechanical impl).
                                     Injectable (Callable) so tests need no network; the
                                     default (`llm_decompose`) calls a strong tier and parses
                                     JSON, degrading to a single passthrough subtask when no
                                     model is reachable (the research._llm_complete pattern).
      -> assign_tier()               ROLE -> model tier via ROLE_TIER_DEFAULTS (strong tier
                                     for research/spec, weak/free tier for mechanical impl),
                                     grounded in docs/MODEL-ROLES.md. Overridable per call.
      -> fan out (BOUNDED)           N subtasks run concurrently under TWO independent caps:
                                       * a global `max_workers` thread-pool bound, AND
                                       * the per-tier cross-process cap in
                                         judge.MAX_CONCURRENT_BY_TIER (qwen35b = 2,
                                         account-wide) enforced by judge.concurrency_slot.
                                     Belt-and-braces: whichever is tighter wins per tier. The
                                     qwen 2-concurrent account cap therefore holds even if a
                                     caller sets max_workers=20.
      -> worker()                    each subtask runs through the SAME discipline as every
                                     other model call here: the default worker dispatches to
                                     the subtask's tier via research._llm_complete (retry /
                                     backoff / concurrency_slot already live in that path).
                                     Injectable for tests. A worker failure is CAUGHT, retried
                                     up to `retries`, and recorded as a failed WorkerResult --
                                     it is never lost and never crashes the fan-out.
      -> reconcile()                 results are gathered, partitioned into succeeded/failed,
                                     mapped back to their subtask ids, and returned with the
                                     MEASURED peak concurrency so the bound is auditable, not
                                     asserted.

Anti-circularity / anti-bloat notes:
  * No new MCP tool. This is a library + `python -m cortex_core.orchestrator` entry only.
  * Default driver + worker tiers are NON-Anthropic (glm5.2 / qwen35b) per the standing
    "no Anthropic model in a default path" rule; a caller may pass fable/opus/etc. explicitly.
  * Reversible: a single new module + its tests; touches nothing existing.
"""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from cortex_core import judge as _J

# --- Role -> tier policy (docs/MODEL-ROLES.md) ---------------------------------------------
# Roles that need judgment/design/synthesis get a capable ("strong") tier; mechanical,
# high-volume, checker-guardable work gets a cheap/free ("weak") tier. Both DEFAULTS are
# non-Anthropic (anti-circularity: no Claude model in a default path). Override per call.
STRONG_ROLES = frozenset({"research", "spec", "design", "review", "synthesis", "plan"})

# glm5.2: capable non-Anthropic worker/judge (MODEL-ROLES §A/§B). qwen35b: primary strong
# worker/generator, free, 2-concurrent account cap (MODEL-ROLES §B; judge.MAX_CONCURRENT_BY_TIER).
DEFAULT_STRONG_TIER = "glm5.2"
DEFAULT_WEAK_TIER = "qwen35b"

# Global fan-out bound. Kept modest by default so a stray large decomposition can't stampede
# every free endpoint at once; the per-tier caps (judge.MAX_CONCURRENT_BY_TIER) are the hard
# account-safety floor underneath this. Both are configurable per call.
DEFAULT_MAX_WORKERS = 4

# Per-subtask worker completion budget (a caller can raise it for build-style turns; the
# tier's own reasoning-budget floor still applies via judge.apply_min_max_tokens downstream).
DEFAULT_MAX_TOKENS = 1500


@dataclass(frozen=True)
class Subtask:
    """One unit of decomposed work, already assigned a role and a model tier."""
    subtask_id: str
    prompt: str
    role: str
    tier: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerResult:
    """The outcome of running ONE subtask. A failure is a result with ok=False and a
    captured error -- never a dropped subtask and never a raised exception past the pool."""
    subtask_id: str
    role: str
    tier: str
    ok: bool
    output: str | None
    error: str | None
    attempts: int


@dataclass(frozen=True)
class Reconciliation:
    """The reconciled whole: every subtask accounted for, succeeded/failed partitioned,
    and the MEASURED peak in-flight concurrency for auditing the bound."""
    task: str
    results: list[WorkerResult]
    max_workers: int
    peak_concurrency: int

    @property
    def succeeded(self) -> list[WorkerResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[WorkerResult]:
        return [r for r in self.results if not r.ok]

    @property
    def all_ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    def output_map(self) -> dict[str, str | None]:
        """subtask_id -> output (None for failures). The reconciliation the caller consumes."""
        return {r.subtask_id: r.output for r in self.results}


def assign_tier(role: str, *, strong_tier: str = DEFAULT_STRONG_TIER,
                weak_tier: str = DEFAULT_WEAK_TIER) -> str:
    """Map a subtask role to a model tier. Strong for research/spec, weak/free otherwise."""
    return strong_tier if role in STRONG_ROLES else weak_tier


# --- Decomposition (the strong driver) -----------------------------------------------------

def _coerce_subtasks(raw: Any, *, strong_tier: str, weak_tier: str) -> list[Subtask]:
    """Turn a driver's decomposition (list of dicts / strings) into typed, tiered Subtasks."""
    out: list[Subtask] = []
    for i, item in enumerate(raw or []):
        if isinstance(item, str):
            prompt, role, meta = item, "impl", {}
        elif isinstance(item, dict):
            prompt = str(item.get("prompt") or item.get("task") or "").strip()
            role = str(item.get("role") or "impl").strip() or "impl"
            meta = {k: v for k, v in item.items() if k not in ("prompt", "task", "role")}
        else:
            continue
        if not prompt:
            continue
        tier = item.get("tier") if isinstance(item, dict) and item.get("tier") else \
            assign_tier(role, strong_tier=strong_tier, weak_tier=weak_tier)
        out.append(Subtask(subtask_id=f"st-{i}-{uuid.uuid4().hex[:8]}",
                           prompt=prompt, role=role, tier=str(tier), meta=meta))
    return out


def llm_decompose(task: str, *, driver_tier: str = DEFAULT_STRONG_TIER,
                  max_tokens: int = 2000) -> list[dict[str, Any]]:
    """Default driver: ask a STRONG tier to split `task` into subtasks as JSON.

    Returns a list of {"prompt", "role"} dicts. Degrades to a single passthrough subtask
    when no model is reachable (research._llm_complete returns None) -- the orchestrator
    still runs, just without a real split, exactly like research's frame-step fallback."""
    from cortex_core import research as _R
    prompt = (
        "You are a task DECOMPOSER for a parallel worker pool. Split the task below into "
        "2-6 independent subtasks. For each, choose a role from "
        f"{sorted(STRONG_ROLES)} (needs a capable model) or 'impl'/'mechanical' (cheap model). "
        'Reply ONLY with a JSON array of objects: [{"prompt": "...", "role": "..."}].\n\n'
        f"TASK:\n{task}\n"
    )
    text = _R._llm_complete(prompt, model=driver_tier, max_tokens=max_tokens)
    if not text:
        return [{"prompt": task, "role": "impl"}]
    # Tolerate models that wrap JSON in prose/fences.
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, list) and parsed:
                return parsed
        except json.JSONDecodeError:
            pass
    return [{"prompt": task, "role": "impl"}]


def default_worker(subtask: Subtask, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Default worker: dispatch the subtask to its tier through the shared research
    completion path (retry/backoff already live there). Raises on an empty/failed
    completion so the orchestrator's failure handling records it honestly."""
    from cortex_core import research as _R
    out = _R._llm_complete(subtask.prompt, model=subtask.tier, max_tokens=max_tokens)
    if out is None or not out.strip():
        raise RuntimeError(f"tier {subtask.tier!r} returned no completion for {subtask.subtask_id}")
    return out


# --- The fan-out loop ----------------------------------------------------------------------

def _run_one(subtask: Subtask, worker: Callable[[Subtask], str], retries: int,
             live: dict[str, int], lock: threading.Lock) -> WorkerResult:
    """Run one subtask under its tier's cross-process concurrency slot, with bounded retries.

    The per-tier slot (judge.concurrency_slot) is acquired INSIDE the pool thread, so the
    qwen 2-concurrent account cap holds no matter how wide the thread pool is. `live`/`lock`
    track measured in-body concurrency so the bound is auditable."""
    last_err: str | None = None
    attempts = 0
    for attempt in range(1, max(1, retries) + 1):
        attempts = attempt
        try:
            with _J.concurrency_slot(subtask.tier):
                with lock:
                    live["n"] += 1
                    live["peak"] = max(live["peak"], live["n"])
                try:
                    output = worker(subtask)
                finally:
                    with lock:
                        live["n"] -= 1
            return WorkerResult(subtask_id=subtask.subtask_id, role=subtask.role,
                                tier=subtask.tier, ok=True, output=output,
                                error=None, attempts=attempts)
        except Exception as exc:  # noqa: BLE001 -- a worker failure must be recorded, not lost
            last_err = f"{type(exc).__name__}: {exc}"
            continue
    return WorkerResult(subtask_id=subtask.subtask_id, role=subtask.role,
                        tier=subtask.tier, ok=False, output=None,
                        error=last_err, attempts=attempts)


def orchestrate(task: str, *,
                decompose: Callable[[str], list[Any]] | None = None,
                worker: Callable[[Subtask], str] | None = None,
                max_workers: int = DEFAULT_MAX_WORKERS,
                retries: int = 1,
                strong_tier: str = DEFAULT_STRONG_TIER,
                weak_tier: str = DEFAULT_WEAK_TIER) -> Reconciliation:
    """Decompose `task` with a strong driver, fan out to tiered workers under bounded
    concurrency, and reconcile.

    Concurrency is bounded TWO ways at once: `max_workers` caps the thread pool globally, and
    each subtask additionally acquires its tier's slot in judge.MAX_CONCURRENT_BY_TIER
    (qwen35b=2, account-wide) -- the tighter of the two wins per tier, so account caps hold
    regardless of `max_workers`.

    `decompose` and `worker` are injectable (no network in tests). A worker exception is caught,
    retried up to `retries` times, and returned as a failed WorkerResult -- the fan-out never
    loses a subtask and never propagates a worker exception. Returns a Reconciliation whose
    `peak_concurrency` is MEASURED, not assumed."""
    decompose = decompose or (lambda t: llm_decompose(t, driver_tier=strong_tier))
    worker = worker or default_worker

    subtasks = _coerce_subtasks(decompose(task), strong_tier=strong_tier, weak_tier=weak_tier)
    if not subtasks:
        return Reconciliation(task=task, results=[], max_workers=max_workers, peak_concurrency=0)

    live: dict[str, int] = {"n": 0, "peak": 0}
    lock = threading.Lock()
    results: list[WorkerResult] = []

    # max_workers must be >=1; never spawn more pool threads than there are subtasks.
    pool_size = max(1, min(max_workers, len(subtasks)))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futs = {pool.submit(_run_one, st, worker, retries, live, lock): st for st in subtasks}
        for fut in as_completed(futs):
            results.append(fut.result())

    # Deterministic order (fan-out completion order is nondeterministic): sort by subtask id.
    results.sort(key=lambda r: r.subtask_id)
    return Reconciliation(task=task, results=results, max_workers=max_workers,
                          peak_concurrency=live["peak"])


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m cortex_core.orchestrator",
        description="Driver -> tiered-worker parallel orchestration (GAP I3).")
    ap.add_argument("task", help="the task to decompose and fan out")
    ap.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--strong-tier", default=DEFAULT_STRONG_TIER)
    ap.add_argument("--weak-tier", default=DEFAULT_WEAK_TIER)
    ap.add_argument("--dry-run", action="store_true",
                    help="decompose + assign tiers only; do not dispatch workers")
    args = ap.parse_args(argv)

    if args.dry_run:
        subs = _coerce_subtasks(
            llm_decompose(args.task, driver_tier=args.strong_tier),
            strong_tier=args.strong_tier, weak_tier=args.weak_tier)
        for s in subs:
            print(f"[{s.role:>10} -> {s.tier:<12}] {s.prompt[:80]}")
        return 0

    rec = orchestrate(args.task, max_workers=args.max_workers, retries=args.retries,
                      strong_tier=args.strong_tier, weak_tier=args.weak_tier)
    print(f"task: {rec.task}")
    print(f"workers: {len(rec.results)}  ok: {len(rec.succeeded)}  "
          f"failed: {len(rec.failed)}  peak_concurrency: {rec.peak_concurrency}/{rec.max_workers}")
    for r in rec.results:
        tag = "OK " if r.ok else "ERR"
        detail = (r.output or "")[:70] if r.ok else (r.error or "")
        print(f"  {tag} [{r.role}->{r.tier}] {r.subtask_id}: {detail}")
    return 0 if rec.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
