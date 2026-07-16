# gap_ledger v0 — build note (2026-07-14)

Implements **GAP-CORTEX-0001** (the Gap/Friction Ledger): a durable,
machine-tracked gap/phase ledger that replaces the fragile prose gap-plan
(`docs/GAP-CLOSURE-PLAN.md`). Grounded in the two converged designs
`docs/design/durable-gap-tracking-fable-2026-07-13.md` and
`docs/design/durable-gap-tracking-codex-2026-07-13.md`.

## What shipped

- `cortex_core/gap_ledger.py` — append-only JSONL ledger at
  `gaps/gap_ledger.jsonl` (committed to git). One full state-snapshot per line,
  last-line-per-`gap_id` wins. Concurrency + torn-line tolerance are
  **imported** from `task_ledger.py` (reuse, don't reinvent).
- `cortex-gap` CLI (one surface, `action` subcommands) — **no new MCP tool**
  (frozen tool-surface budget deliberately protected; see anti-bloat below).
- `tests/test_gap_ledger.py` — 31 tests, written first (TDD). Green.
- `pyproject.toml` — one new `[project.scripts]` line: `cortex-gap`.

## Root cause both designs diagnosed (the reason this exists)

Drift is a **write-path disease, not a format disease.** The repo already had a
*structured* gap registry (`templates/workspace-control-plane/gaps/index.jsonl`
+ `registry.md`) and it drifted anyway — index.jsonl still holds literal
`"updated_at":"YYYY-MM-DD"` placeholders and disagrees with its sibling
`registry.md` on GAP-CORTEX-0003's status. So schema alone does not fix drift.
The fix is: **one canonical append-only store + current state DERIVED (never
separately maintained) + a deterministic render/`--check` gate** so divergence
is a CI failure, not a memory duty.

## Schema (v1, reconciled)

Each line: `schema_version, event_id (uuid7), event, gap_id, title, status,
phase, priority, blocks, blocked_by, closes_metric, evidence[], owner_agent,
task_ids, source, verified, supersedes, superseded_by, reason, author_agent,
created_at/claimed_at/updated_at/closed_at`. Every field is grounded in one or
both designs; nothing invented.

- **Auto-derivation (the "#1 process-moat" requirement).** `effective_status`
  is derived at read time: an open/claimed gap whose blockers are not all
  terminal-success reads as `blocked` — blocked-ness can never drift from the
  blockers' real state. `phase_rollup()` derives each phase's status
  (open/active/blocked/complete) from its gaps' effective statuses. No hand-
  curated phase field.
- **Evidence-gated closure (anti-evidence-theater).** `close` moves a gap to
  `verifying` (an agent can *report* done); only `verify` — a deterministic
  check that every evidence `path[:line]` resolves against committed repo state
  — flips `verified:true` + `closed`. `--human` records an explicit human
  sign-off for non-machine-checkable metrics. Judges never flip it.

## Fable vs Codex reconciliation (disagreements = signal)

| Point | Fable | Codex | Reconciliation shipped |
|---|---|---|---|
| Status enum | 8: …`closed_pending_verify`… | 7: …`verifying`,`ready`… | Merged 8: `proposed/open/claimed/blocked/verifying/closed/wont_fix/superseded`. Took Codex's shorter **`verifying`** over Fable's `closed_pending_verify`; kept Fable's **`superseded`** as a status (reachable via Codex's supersede *event*) — both true. Dropped Codex's `ready` as an authored status: it's derived (open+unblocked), computed not stored, to keep the enum small. `claimed` over Codex's `active` to match the `claim` verb + task_ledger vocabulary. |
| Unknown fields | forgiving-read (keep, for schema evolution) | strict-reject (typos ≠ state) | **Both, at different boundaries:** strict `_validate_write_record` on WRITE (reject unknown keys); forgiving reduce on READ (keep future fields). Clean synthesis, not a compromise. |
| Evidence shape | `{path, line, kind}` | `{ref:"file:line", kind, sha256?}` | Fable's structured `{path, line, kind}` — cleaner to range-check than parsing `"file:line"`; `line` nullable for commit/url/human refs. |
| closes_metric | string pointer | structured `{name,operator,target,unit,result_refs}` | v0 = optional **string** (Codex's structured auto-check needs the results-ledger join, which is not landed in this base — both designs defer it). Recorded, not machine-evaluated yet. |
| event_id / fence | ts+actor only | uuid7 event_id + optimistic prior-event fence | Added **uuid7 `event_id`** (idempotency, matches task_ledger). Skipped the optimistic fence in v0 — the exclusive lock already serializes; both agree the lock is mandatory. |
| Edge mirroring | derive graph via BFS | writer normalizes both directions | v0 stores edges as authored; the full bidirectional graph is **derived** (`_blocker_graph`, union of `blocks` + inverse `blocked_by`) — no cross-record write amplification. Auto-mirror deferred. |
| Ontology as substrate | **No** — optional derived projection, gated on G2 retrieval win | **No** — optional, one-way, gated on measured multi-hop win | **Agreed. Not built.** Ontology stays unwired; gap ledger is canonical. |
| Reconcile writes | render-check gate | `reconcile --check` appends derived state | v0 derives blocked/phase at read time (no reconcile write loop). `render --check` is the drift gate. Reconcile-append deferred. |

## Anti-bloat (non-negotiable, honored)

- **No new MCP tool.** `mcp.py` untouched; zero `gap_ledger` references in it.
  CLI-only — the frozen tool-surface context budget is not grown.
- One CLI surface with subcommands (not five tools). Default reads are scoped
  projections (`--phase`/`--status`), never the whole log.
- Zero new mandatory steps that refuse work (Disease B): `create` replaces
  "add a markdown row"; closure is one evidence-bearing call.

## Honest debt / deferred (each behind its own gate)

- **Migration not run.** `docs/GAP-CLOSURE-PLAN.md` + the old registry are not
  yet seeded into the ledger. The API is migration-ready (`create` with
  `source`+`verified:false`); the one-shot importer is the obvious next step.
- **CI `render --check` not wired.** `render`/`render_check` + `validate` exist
  and are tested; adding them to `cortex-doctor`/CI is the follow-up that makes
  this durable-forever (both designs put the CI gate in the definition-of-done).
- **State-engine auto-write hook not wired.** The zero-ceremony path (a
  CLOSEOUT→DONE transition auto-appending a `close`/`verifying` event via
  `intent.gap_id`) is designed but not connected here.
- **closes_metric is recorded, not auto-evaluated** (needs the results ledger).
- **Git-branch merge causality** (union-merge across worktrees) is the weakest
  concurrency link, flagged UNVERIFIED by Fable; a two-branch conflict test is
  deferred. Within one filesystem, the imported lock is fully tested.
- Everything is `ai_discovered`/unverified until the user reviews it.
