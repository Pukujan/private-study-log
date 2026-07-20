# Multi-agent Cortex-Local — one driver, many parallel workers

This wrapper turns your single Hermes agent into a **driver** that orchestrates
a fleet of cheap/free **worker** agents, all sharing one brain and one audit
trail. Here's the honest division of labor — what the wrapper gives you vs. what
your driver agent does.

## What comes in the box (the wrapper)
- **The brain** — `cortex_search` / `cortex_fetch_doc` over MCP (`.mcp.json` +
  your read key in `.env`). Search-first, shared across every agent.
- **The local state machine** — `.cortex/protocol/STATE-MACHINE.md`: the phase
  flow (SEARCH → RESEARCH → SDD → TDD → IMPLEMENT → VERIFY → DOC → CLOSEOUT).
  This is the *contract* each agent follows. Detection over coercion — it guides,
  never refuses.
- **The scribe** — `.cortex/scripts/scribe.py`: reads a transcript and writes the
  closeout **for** the agent. This is the "after-transcript write-up, no ritual"
  direction — a worker just does its job and drops its transcript; the scribe
  turns it into an audit record. Zero closeout ceremony per task.
- **The provider config** — `.env`: your driver lane + all your worker lanes
  (9router, Ollama, OpenCode-Zen…), one file, pre-filled URLs.

## Two ways to fan out: native engine or your driver agent
As of the 2026-07-15 vendoring the wrapper ships a **native fan-out/fan-in engine**
(`cortex_core.fanout` + the `decomposer` + `mission_driver`), wired into the state
machine. You can drive parallel work two ways:

- **Native (in-process).** `cortex-fanout` runs homogeneous best-of-N across your live
  executors, and the **heterogeneous decomposer + mission driver** (`cortex_spawn_mission`
  / `run_mission`) split a mission into independent, per-worker slices — each carrying its
  **own receipt** — then fan them out and merge/verify them back through the same enforced
  state machine. This is deterministic scaffolding: it never invents a judge (the private
  eval/gold/judge lab is *not* vendored), it just parallelizes and gates.
- **Agent-driven.** Your Hermes/Claude driver reads the state machine and orchestrates
  cheap worker *agents* itself (the diagram below). Use this when the sub-tasks need a full
  agent loop rather than a single model call.

What the wrapper still does **not** do on its own: launch host *agent* processes for you —
the native engine parallelizes model calls / build slices, while spawning full worker agents
remains your driver's job. Either way the wrapper makes every worker cheap to launch,
disciplined, and self-auditing.

## The pattern your driver runs

```
                ┌─────────────────────────────────────────┐
                │  DRIVER (strong lane: 9router glm-5.2)    │
                │  reads STATE-MACHINE, plans, decomposes   │
                └───────────────┬───────────────────────────┘
      search-first via MCP brain│  fans out N instructions
        ┌──────────────┬────────┴───────┬──────────────┐
        ▼              ▼                ▼              ▼
   WORKER (ollama) WORKER (zen)   WORKER (9r-aux)  WORKER (ollama)
   qwen3:4b free   big-pickle     separate queue   … capped at
   local, free     free/stealth   no contention    CORTEX_MAX_PARALLEL_WORKERS
        │              │                │              │
        └──────────────┴────────┬───────┴──────────────┘
                                ▼
             each worker drops its transcript →
             .cortex/scripts/scribe.py writes a closeout per worker
             into projects/<slug>/audit/closeouts/  (no ritual)
```

0. **Discover your fleet first.** Run **`cortex-models`** (see `MODELS.md`) to
   probe which of *your* configured lanes are actually reachable. It writes
   `model_availability.json`; `cortex-fanout` then restricts its parallel workers
   to the executors the probe marked live (and degrades gracefully if you skip
   this). The probe is free-only — it never spends a token on a paid lane.
1. **Driver plans.** Your strong lane (`CORTEX_DRIVER_TIER`, e.g. 9router
   `umans/umans-glm-5.2`) reads the state machine, `cortex_search`es the brain
   for prior work, and decomposes the task into independent worker instructions.
2. **Workers run in parallel.** Dispatch each sub-instruction to a cheap lane
   from `CORTEX_WORKER_TIERS` — Ollama (local, free), OpenCode-Zen (free), the
   9router `aux` queue (separate backend, no contention). Cap concurrency with
   `CORTEX_MAX_PARALLEL_WORKERS` (bounded by your GPU + rate limits).
3. **Each worker self-audits.** A worker doesn't stop to write a closeout — it
   finishes and its transcript is handed to `scribe.py`, which writes the audit
   record. This is why the audit trail gets *richer* as you parallelize, not
   slower.
4. **Driver verifies + synthesizes.** The driver collects worker outputs, runs
   the VERIFY phase (tests / checks — the objective gate, not vibes), and writes
   the one project-level closeout.

## Why this stays fast (the two failure modes it avoids)
- **No governance ritual.** The scribe generates audits from transcripts, so
  agents spend tokens on *work*, not on writing about work.
- **No context-window wall.** Progressive disclosure (the state machine surfaces
  only the next tool or two) + one folder per project keeps each agent's context
  small. Workers stay cheap and short-lived.

## Wiring it to your models
Everything is OpenAI-compatible (`/v1`). Set the lanes in `.env`
(`CORTEX_DRIVER_TIER`, `CORTEX_WORKER_TIERS`, `CORTEX_MAX_PARALLEL_WORKERS`). Your
Hermes host reads those to decide who drives and who fans out. Free lanes
(Ollama, Zen, 9router-free models) are ideal for the wide parallel step; save the
paid gateway (OpenRouter) for a strong second opinion / cross-check lane.
