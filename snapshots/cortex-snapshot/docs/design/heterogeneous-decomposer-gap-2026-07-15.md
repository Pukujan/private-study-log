# GAP: heterogeneous task-decomposer for the Cortex state machine (2026-07-15)

## The gap (honest)
The Cortex plane2 **state machine drives ONE agent through phases** (research→…→closeout).
`cortex_core/fanout.py` adds **homogeneous** fan-out (N models run the SAME slot → deterministic
gate picks best-of-N). **Neither decomposes a goal into N DIFFERENT sub-tasks and assigns each to
a different worker** — the heterogeneous *decompose → assign → spawn-parallel → fan-in* pattern a
human orchestrator (or Claude, by hand) actually runs. Owner's observation (2026-07-15): *"this
[manual decompose-and-delegate] sounds better than what the state machine is doing right now."*
Agreed — and we won't pretend the state machine does it today, because it doesn't.

**Prior art that DOES do a version of this:** Hermes's **kanban auto-decompose** (`auto_decompose:
true` + `kanban_decomposer` model + dispatcher + `max_in_progress_per_profile` concurrency cap) —
live in hades's gateway. So the decompose-and-dispatch driver exists at the *Hermes* layer; the
*Cortex* state machine has only single-track coercion + homogeneous fanout.

## Why it's not trivial (the invariants a real decomposer must not break)
1. **Judge-free verdict path** — no LLM may decide pass/fail; a decomposer may *split* work but the
   gate still grades each sub-result mechanically.
2. **Fan-in receipt race** — `receipts.py` mints ONE server-owned verdict receipt into a shared
   holder; N heterogeneous workers would race it. Each worker needs its OWN receipt + the fan-in
   carries the winner/aggregate `verdict_id` (same fix the homogeneous fanout coupling needs).
3. **Deterministic phase gates** — `state_engine.py` write-lock + `(task_id, seq)` idempotency +
   atomic claim acquisition must still hold when sub-tasks are child rows.
4. **Conflict avoidance** — heterogeneous workers must get **disjoint file-lanes** (what the manual
   orchestrator does by hand: "you own these files, you own those") so parallel edits don't clobber.

## Research commissioned (independent, non-Anthropic + Anthropic, cross-checked)
- **Codex terra** (local CLI, non-Anthropic): `reviewed/decomposer-research-terra-2026-07-15.md` —
  design for where the decomposer sits, the decompose→assign→spawn→reconcile contract, per-worker
  receipts, disjoint-lane assignment, judge-free preservation, ranked build steps. *(running)*
- **Fable (2nd Claude account)**: to be run in the owner's 2nd session (this session can't route to
  the 2nd account) — same brief, for cross-check. Paste-ready prompt below. Output lands here as
  `reviewed/decomposer-research-fable-2026-07-15.md`.

Cross-check the two (converge = build; diverge = a third arbiter or owner decides). **Anti-circular
note:** a Claude model designing a Claude-orchestrated system is self-referential — terra's
independent (non-Anthropic) view is the load-bearing one; Fable is the anchor, not the verdict.

## CHOSEN APPROACH (owner, 2026-07-15): ADOPT Hermes kanban — do NOT build a native decomposer
Adopt-don't-build. Instead of a native Cortex decomposer, **adopt Hermes kanban's `auto_decompose` +
dispatcher + WIP-cap** as the orchestration layer (hades runs it live; phantomic's driver is a Hermes
agent — both real users have it; MULTIAGENT.md already assumes "your Hermes driver does the orchestration").

**Load-bearing constraint (terra):** kanban lets the MODEL decide task completion ("done"). That breaks
Cortex's judge-free invariant. So the adopt is a SPLIT of responsibility, not a wholesale import:
- **kanban owns:** decompose → dispatch → WIP-cap (the machinery).
- **Cortex owns the verdict:** each worker's "done/correct?" is decided by the **deterministic gate +
  per-worker server receipt** — NEVER the model (same per-worker-receipt fix the homogeneous fanout
  coupling needs).
- **Only real work:** wire kanban's completion check to call the Cortex gate/receipt instead of trusting
  the agent's self-reported "done." One integration point, not a new subsystem.

**Boundary:** works for any Hermes-running driver (hades ✓, phantomic ✓). A bare-wrapper user with no
Hermes needs a fallback — terra's native PARTITION-seam design (above) is kept as that fallback spec,
NOT the primary plan.

## Status
**BUILD NOW — nothing deferred** (owner, 2026-07-15: "build the partition seam too, leave nothing for
later"). Ship BOTH paths:
1. **Native Cortex decomposer** (terra's PARTITION-seam design) — `cortex_core/decomposer.py` +
   `run_mission()` + deterministic manifest validator; works with NO Hermes (ships to the wrapper).
2. **kanban-adopt wiring** — kanban decompose/dispatch/WIP + Cortex gate/receipt for the verdict.
Do NOT claim the state machine auto-decomposes until wired + tested. Build order: standalone
manifest-validator (no state_engine deps) → PARTITION wiring (after the homogeneous-fanout coupling
frees state_engine/receipts) → kanban-adopt integration. Judge-free throughout: the model PROPOSES a
manifest; the server VALIDATES/persists; the deterministic gate + per-worker receipt decide done.

---
### Paste-ready Fable brief (run in the 2nd Claude account session)
> Spawn a subagent with **model: fable**. Repo `d:/claude/stupidly-simple-cortex` (read-only fine).
> Research: how to wire a REAL **heterogeneous** task-decomposer into the Cortex plane2 state
> machine — decompose one goal into N DISJOINT DIFFERENT sub-tasks, assign each to the right
> worker/tier, spawn in parallel, fan-in/reconcile — without breaking (a) the judge-free verdict
> path, (b) the fan-in receipt race in `cortex_core/receipts.py` (one server receipt, shared holder,
> N workers race it), (c) the deterministic phase gates in `cortex_core/state_engine.py` +
> `plane2_driver.py`. Read `cortex_core/fanout.py` (existing HOMOGENEOUS best-of-N — generalize it),
> `state_engine.py`, `receipts.py`, `director.py`, `docs/research/fanout-executor-design-2026-07-11.md`.
> Compare to Hermes kanban auto-decompose. Deliver: (1) where the decomposer sits, (2) the
> decompose→assign→spawn→reconcile contract + data shapes, (3) per-worker receipts to kill the fan-in
> race, (4) disjoint file-lane assignment to avoid clobbering, (5) judge-free preservation, (6)
> ranked minimal build steps, (7) risks. Cite file:line. ~900 words. Write the result to
> `reviewed/decomposer-research-fable-2026-07-15.md` and DO NOT commit.
