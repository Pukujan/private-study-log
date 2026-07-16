# Durable gap tracking: independent Codex design review

**Verdict:** do not make the living ontology the source of truth for gaps. Build one small, typed, append-only gap ledger, link it to the existing task ledger, and derive phase and graph views from those two logs. Ontology integration is optional and should earn its place with a multi-hop retrieval win. This disagrees with treating “gaps as graph entities” as the primary design: that would put a project-control primitive behind a larger, currently unwired subsystem.

## Findings that constrain the design

The current plan is a prose table. It mixes identity, priority, closure action, proof metric, and occasional status in cells (`docs/GAP-CLOSURE-PLAN.md:9-17`, `docs/GAP-CLOSURE-PLAN.md:50-57`), then restates a manually ordered plan (`docs/GAP-CLOSURE-PLAN.md:103-104`). The fact-check finding is already explicit: headline numbers lived only in prose and lacked a committed results ledger (`docs/GAP-CLOSURE-PLAN.md:27-34`). The later sweep found catalog/disk drift in both directions (`docs/GAP-CLOSURE-PLAN.md:90-99`). This is not a markdown-formatting bug; markdown is being asked to serve as a database, event history, scheduler, and report.

The repo already has the correct small primitive. `task_ledger.py` defines append-only JSONL whose current state is the last snapshot for an id (`cortex_core/task_ledger.py:10-20`), serializes read-check-append with an exclusive-create lock (`cortex_core/task_ledger.py:115-147`), skips a torn final line (`cortex_core/task_ledger.py:168-183`), and atomically rejects a second claimant (`cortex_core/task_ledger.py:251-287`). Reuse that discipline rather than inventing a new service.

The state engine solves a different problem well: each call is a SQLite `BEGIN IMMEDIATE` superstep, with sequence fencing and idempotency (`cortex_core/state_engine.py:13-28`); it exposes phase-specific tools (`cortex_core/state_engine.py:384-393`); and replay rebuilds state from events (`cortex_core/state_engine.py:1010-1041`). It should execute work and emit task state, not become the canonical gap catalog.

The requested `cortex_core/results_ledger.py` is not present in this checkout as reviewed. The prose plan specifies the intended `evals/results.jsonl` shape (`docs/GAP-CLOSURE-PLAN.md:31-32`), but this design cannot cite or depend on implementation that is concurrently “being built.” The gap ledger should share a tiny JSONL/locking helper with task/results ledgers later, not import an unlanded module now.

## 1. Ground-truth schema and storage

Store committed events at `gaps/gap_ledger.jsonl`; keep a human-readable generated view at `docs/GAP-CLOSURE-PLAN.generated.md`. The generated markdown is disposable and must carry “GENERATED; DO NOT EDIT.” The JSONL is authoritative.

Use full-state snapshots per mutation, matching `task_ledger.py`, because they are cheaper to implement and inspect than a patch language. Each line has this v1 contract:

```json
{
  "schema_version": 1,
  "event_id": "gap-event-<uuid7>",
  "event": "create|update|claim|release|close|reopen",
  "gap_id": "GAP-CORTEX-0021",
  "title": "Durable gap tracking",
  "status": "open|ready|active|blocked|verifying|closed|wont_fix",
  "phase": "7|cross_phase|backlog",
  "priority": "P0|P1|P2",
  "blocks": ["GAP-CORTEX-…"],
  "blocked_by": ["GAP-CORTEX-…"],
  "closes_metric": {
    "name": "gap_ledger_recountability",
    "operator": "eq",
    "target": 1,
    "unit": "boolean",
    "result_refs": ["run-…"]
  },
  "evidence": [
    {"ref": "tests/test_gap_ledger.py:42", "kind": "test", "sha256": "optional"}
  ],
  "owner_agent": "codex/session-or-agent-id|null",
  "task_ids": ["task-<uuid7>"],
  "created_at": "RFC3339 UTC",
  "claimed_at": "RFC3339 UTC|null",
  "updated_at": "RFC3339 UTC",
  "closed_at": "RFC3339 UTC|null",
  "author_agent": "codex/session-or-agent-id",
  "reason": "short mutation rationale"
}
```

Rules matter more than extra fields:

1. `gap_id`, `created_at`, and the semantic identity of `closes_metric` are immutable. Corrections append a new snapshot; nothing edits old lines.
2. `evidence[].ref` is repository-relative `file:line`, resolved and range-checked at write time. A close/verify mutation requires at least one evidence ref and a typed result reference when the metric is numeric. Merely finishing a task cannot close a gap.
3. `blocks` and `blocked_by` are one logical edge, normalized by the writer. Accept either input, write both projections, reject self-edges, missing targets, and cycles. Arrays are sorted for deterministic diffs.
4. Claim and terminal transitions are compare-and-append under the same lock. Only `open|ready -> active` may claim; only the owner (or an explicit recovery operation with reason) may mutate ownership. Close is conditional on the metric/evidence validator.
5. Every record is schema-validated. Unknown fields are rejected in v1 so spelling mistakes do not silently become state.
6. Current state is the last valid snapshot per `gap_id`; duplicate `event_id` is idempotent. A `gap validate` command checks the whole log, referential integrity, cycles, evidence refs, and legal status transitions.

This is established software-engineering best practice from training knowledge: event sourcing preserves an immutable change history and derives current state by folding events; mature issue schemas separate stable identity/status/priority/assignee/dependencies from comments; ADR logs are append-oriented decision history with supersession rather than silent rewriting. Those are architectural best-practice claims, not facts established by this repository, so they are intentionally labeled **training knowledge** rather than given false local citations.

## 2. Ledger versus ontology

The ontology already uses almost the same physical model: append-only entity/relation JSONL reduced by last id, with invalidation rather than deletion (`cortex_core/ontology.py:11-22`), schema and endpoint validation (`cortex_core/ontology.py:238-301`), and the task-ledger lock helpers (`cortex_core/ontology.py:30-32`, `cortex_core/ontology.py:304-354`). That similarity is an argument for a shared storage helper, not for making ontology authoritative.

What a graph adds beyond a ledger is real but narrow:

- arbitrary typed traversal across gaps, phases, metrics, documents, rubrics, and owners;
- multi-hop questions such as “what transitively blocks G2?”, “which P0 gaps close metrics used by Phase 7?”, or “which superseded evidence still supports a live gap?”;
- a single relational surface alongside other corpus entities, with referential type constraints.

A flat ledger already answers the operational questions with adjacency lists and a 30-line DFS/topological sort. For dozens or hundreds of gaps, `blocks`/`blocked_by`, `phase`, `task_ids`, and `result_refs` are a sufficient small graph view. The ontology’s current self-maintainer only seeds gaps from a prose registry (`cortex_core/ontology_seed.py:120-125`) and deliberately limits itself to structurally certain relations (`cortex_core/ontology_seed.py:11-16`). More importantly, the phase gate itself says the graph is not fused into retrieval and has not shown a golden-set retrieval win (`docs/PHASE-GATES.md:189-202`); the gap plan says to fuse-and-prove or park it (`docs/GAP-CLOSURE-PLAN.md:63-65`). Making it required now would turn an unwired P1 gap into a dependency of the P0 control plane.

Therefore ontology is **optional, derived, and one-way**:

`gap ledger -> projector -> ontology gap entities/relations`

Never write gap state through the ontology. Never read operational status from it. The projector carries `source_event_id` and is idempotent; lag or failure only degrades richer queries, never coordination. Wire it only after a benchmark shows that cross-domain multi-hop queries cannot be served cheaply by the ledger view and that ontology-on beats ontology-off. This follows Phase 7’s own “retrieval win before next stage” rule (`docs/PHASE-GATES.md:183-200`).

## 3. Automatic phase tracking across concurrent agents

Do not let agents manually set aggregate phase completion. Agents perform four cheap actions through a CLI/library/MCP surface:

1. `gap claim GAP-ID --agent A` atomically claims the gap and, if needed, creates/links a task-ledger task.
2. Existing `task_ledger` claim/update operations coordinate individual tasks. Its locked read-check-append already guarantees one winner (`cortex_core/task_ledger.py:251-319`).
3. State-engine transitions continue to record execution phase with sequence fences. The current chart is data and tools are phase-derived (`cortex_core/state_engine.py:67-83`, `cortex_core/state_engine.py:384-393`).
4. On task terminal, result append, state-engine terminal, and `gap status`, run an idempotent `gap reconcile` reducer.

The reducer computes, never hand-curates:

- `blocked` if any transitive `blocked_by` gap is not terminal-success;
- `active` if a linked task is active or a linked state-engine run is nonterminal;
- `verifying` if all linked tasks are done but the closure metric lacks a passing typed result/evidence;
- `closed` only when the closure predicate evaluates true and required evidence resolves;
- `ready` if open, unblocked, and its phase prerequisites are met.

Phase status is a projection, not another editable ledger:

```text
phase complete = every required gap is closed|wont_fix
phase active   = any required gap is active|verifying
phase blocked  = any required gap is blocked, otherwise ready/backlog
```

Phase membership and whether a gap is `required` should live in gap records (add `required_for_phase: true|false` if needed), while the ordered workflow chart remains in `state_engine`. Emit `gaps/phase_status.json` by atomic replace for dashboards and generate markdown from the same reducer. If state-engine and JSONL writes cannot be one transaction, do not fake cross-store atomicity: use stable `task_id`/`run_id`, idempotent reconciliation, and repeat on every read/write plus CI. A crash may leave a temporarily stale projection, but cannot create conflicting ground truth.

Concurrency details: use one gap-ledger lock for every read-check-append, UUIDv7 event ids, expected prior `event_id` as an optimistic fence, bounded stale-lock recovery, single-line append plus flush/fsync for committed mutations, and torn-tail tolerance. Do **not** assume Python append alone is durable. CI runs `gap validate && gap reconcile --check`; a mismatch fails with the exact derived diff.

## 4. Anti-bloat and anti-ritual constraints

The write path must be cheaper than editing the table:

- one command to create, claim, block, or close; defaults infer agent, timestamp, linked active task/run, and phase;
- no narrative description, meeting, approval, or ADR per gap; `reason` is one short line and only required for exceptional transitions;
- no duplicate manual writes to task ledger, gap ledger, ontology, phase JSON, and markdown—the command writes one canonical event and projectors do the rest;
- default reads return a compact queue (`id title priority status blocker owner`), with evidence/history/graph loaded only on demand;
- keep statuses and priorities closed and small; do not encode workflow state in tags;
- auto-open gaps only from deterministic detectors with evidence. LLM-mined candidates enter a separate `proposed` inbox and do not become ground truth without an explicit accept event.

This avoids Disease B: the tracker observes normal work and validates closure rather than adding gates to every tool call. It avoids Disease A: agents receive the next few ready gaps, not the entire history or ontology. The state engine already supports phase-specific disclosure (`cortex_core/state_engine.py:384-393`); reuse that idea for queue reads, not coercive ceremony.

## 5. One concrete recommendation and minimal first build

Build `cortex_core/gap_ledger.py` by extracting/reusing the tested lock + JSONL reducer discipline from `task_ledger.py`; store `gaps/gap_ledger.jsonl`; add `cortex-gap create|list|show|claim|update|block|close|reconcile|validate`; and add focused tests for two-agent claim races, stale expected-event rejection, cycle rejection, torn tails, evidence `file:line` validation, task-terminal-to-`verifying`, metric-pass-to-`closed`, and deterministic phase projection.

Seed the ledger once from `docs/GAP-CLOSURE-PLAN.md`, with a checked migration report that lists every source row and refuses ambiguous duplicates. After migration, replace the hand-edited plan with its generated view. Link gaps to existing task ids; do not merge task and gap concepts. A task is executable work with a claimant and outcome; a gap is a durable deficiency with dependencies and a proof-of-closure predicate. Many tasks may attempt one gap, and one task may contribute to several gaps.

Minimal build order:

1. schema + locked append/reduce/validate;
2. import current prose and render it back deterministically;
3. task-link reconciliation + closure-metric/evidence enforcement;
4. derived `phase_status.json` and `--check` in CI;
5. only then, an optional ledger-to-ontology projector behind an experiment flag.

**Ontology is not required.** The first build is complete and useful without it. Promote the projector only if measured multi-hop demand and retrieval quality justify the added surface.
