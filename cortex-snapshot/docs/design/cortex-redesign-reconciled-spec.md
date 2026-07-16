# Cortex Redesign — Reconciled Spec (Fable × Codex, anti-circular)

> Synthesis of two independent, blind-to-each-other reviews:
> - **Fable** design contracts: `docs/design/cortex-local-redesign-contracts-fable.md`
> - **Codex** independent review: `reviewed/cortex-redesign-codex-review.md`
>
> Owner requirements: (1) research-first + doc/README-update + closeout enforced
> *deterministically on the orchestrator*, not by memory; (2) a **read-folder,
> zero-install** scaffold a host agent (Hermes/Claude/Codex) just *reads*;
> (3) per-project modular audit + a living ontology; (4) validate with a real
> A/B test (SDD/TDD/metrics), not pre-written tests. Reconciled 2026-07-13.

## The headline: they independently agreed

Both reviews, with no knowledge of each other, reached the **same core conclusions** — including **independently choosing `pre-commit` as the A/B test tool** and near-identical metrics. That convergence is the strongest signal here.

### AGREE (both, high confidence — build this)

1. **A read-folder scaffold is a PROTOCOL, not enforcement.** An agent can ignore a folder, edit files, and run shell commands without ever reading it. "Structural enforcement" is only real where a **host hook (or MCP)** actually intercepts native tools. So the design is **two-tier / a ladder**, and we must **market the folder honestly as portable governance**, not as universal fail-closed enforcement.
   - Fable: **L0 folder binds → L1 hooks structural → L2 MCP server-enforced.**
   - Codex: **portable read-folder protocol + optional strong-enforcement adapter (host hook / MCP).**
   - *Identical shape.* Honest ceiling, stated by both: without a hook, "impossible to skip" degrades to **"impossible to skip *undetected*."**

2. **The `StateEngine` is a good kernel but is NOT the orchestrator's boundary today.** The chart already starts at `SEARCH_BRAIN` and has real correctness (SQLite fencing, idempotent transitions, event-sourced replay), but: native Edit/Bash never enter it; refusals are "guidance, not walls" (`state_engine.py:40-42`); the MCP gates (`_contract_gate`, `_forced_docs_gate`, mandatory-state gate) default **OFF**, are session- (not run-) bound, and bypassable with a free-text override. The repo's own `README.md:221-228` admits the state machine is **shadow-only**. → **Extend, don't rebuild.**

3. **"Research happened" must be RECEIPT-PROVEN, not tool-call-proven.** A `cortex_search` call is not evidence the agent used the result. Both require server-verifiable receipts binding: query + corpus snapshot + result paths/hashes + citation span + a decision field linked to that citation (and a no-hit result is a *legal, witnessed* finding). Otherwise the system "rewards evidence-shaped paperwork."

4. **New orchestrator track with receipt-gated transitions + a DOC gate.** Fable's `BUILD_TRACK v2` (adds a `DOC_UPDATE` state before CLOSEOUT, gated by git-diff vs a committed `docs.map.yaml`) ≡ Codex's `ORCHESTRATOR_TRACK` (`SEARCH_BRAIN→RESEARCH→SDD→TDD→IMPLEMENT→VERIFY→DOC_SYNC→REVIEW→CLOSEOUT`, each transition requiring a receipt). Merge into one track. Enforced via a stdlib `cortex-door`/`gate.py` on host PreToolUse/Stop hooks, **bounded to ~2 blocks** (the repo's history shows unbounded refusal loops are *why* the gates got turned off).

5. **Per-project modular audit + provenance-guarded ontology.** Move from the flat `audit/audit-log-1/agent/` (848 files; shards by file count, not project — `audit.py:71-87`) to `audit/projects/<slug>/{PROJECT.yaml, INDEX.md, closeouts/…}` (grab-and-copy human unit). The existing append-only, bi-temporal ontology (`docs/ontology/*.jsonl`, `ontology.py:11-32`) gains `project/closeout/decision` nodes + `part_of/informed_by/supersedes` edges. **Files = truth, graph = index (rebuildable).** Plus a reviewable migration of the 848 files (dry-run → mapping report → `git mv` + backfill + tombstones; `unfiled` as honest default).

6. **A/B test = `pre-commit` (both chose it independently), real SDD+TDD, EXTERNAL evaluator, pre-registered decision rule.** Identical task prompt, same model/settings/OS/repo; the evaluator's checks live *outside* the project (agent writes task-specific tests during the run — this is the whole point of "not pre-written tests"). Metrics: `research_cited`, `docs_updated` (README + a doc target), `closeout_written` (event-digest matches run), `task_passes` (external install/validate/fail/recover/test), + cost/TDD-ordering/bypass-attempts. **Pre-registered rule: the Cortex arm FAILS if the gates reduce task success (Codex: >10pp) or only win on "closeout theater."**

### Codex's ADDITIONS (sharper than Fable — adopt these)

- **Closeout is not atomic with state completion.** The engine marks `closeout_written` + appends an event (`state_engine.py:1240-1263`), but the *filesystem* closeout is written separately by `cortex_write_log` (`mcp.py:1556-1591`) — so the machine can believe a closeout exists while the durable artifact is absent/malformed. → **CLOSEOUT must be a single server-owned atomic operation** (state advance + file write together).
- **Session state is too coarse → run-scoped.** `completed_run`, `contract_approved`, "consulted docs" are session-bound, so this bypass works: trivial task → DONE → do *unrelated* shell/editor work → write a closeout for it. → Bind every gate to a **`run_id`, not a session.**
- **Task-type the doc requirement.** Requiring a README change for *every* task = meaningless churn. Declare a task class up front — `implementation` (README + docs), `research-only` (research report; README optional), `maintenance` (docs only if behavior/ops change). The `docs.map.yaml` gate keys off the class.
- **The ontology must not become an untrusted central DB.** A cross-project graph needs project/tenant namespaces, access-control labels, source-trust tiers, conflict/supersession rules, **quarantine for unverified agent-generated claims**, and prompt-injection protection on imported docs. **The graph must never silently promote a closeout claim into canonical truth.**

### OWNER-DECIDES

- **A/B sample size:** Fable N=5/arm; Codex ≥10 **paired, randomized order**. Codex's is more rigorous → recommend **~10 paired** if cost allows (models are free per owner), else 5.
- **When strict gate defaults ship:** both agree the gates stay OFF-by-default until the A/B proves they *help* `task_passes` (not just closeout completeness). Ship strict only after the A/B clears the task-success/cost bar.
- **"Zero-install" honesty:** L0 read-folder gives protocol + human-readable contract + audit schema with **no install**; L1/L2 enforcement (hooks/MCP) *do* need host wiring. Message it as "read-folder governance, optional enforcement adapter" — not "zero-install structural enforcement."

## Build order (highest leverage first)
1. Receipt-gated `SEARCH_BRAIN` + `DOC_SYNC` + **atomic server-owned CLOSEOUT**, all **run-scoped** (C1 + Codex's atomicity/run-scope fixes). *This alone fixes the skipping — for MCP/hook-routed work.*
2. The `.cortex/` read-folder scaffold (L0), state-machine.md **generated from** the real engine (anti-drift test), per-host onboarding by convention.
3. Per-project audit migration + ontology nodes/edges (with quarantine).
4. The `pre-commit` A/B — the gate that decides whether strict defaults ship.

## The one thing to internalize
Both models, independently, told you the same hard truth: **a folder can't force behavior.** The read-folder scaffold is genuinely valuable as portable protocol + state format + audit schema + human contract — but the moment you want "cannot skip research," you need a host hook or MCP boundary intercepting native tools. Design for both tiers; be honest about which one you're in.
