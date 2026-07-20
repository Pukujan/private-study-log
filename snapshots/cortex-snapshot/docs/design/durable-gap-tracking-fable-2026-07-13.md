# Durable, multi-agent, ground-truth gap/phase tracking — Fable design (2026-07-13)

**Reviewer stance:** independent design pass; Codex is the other reviewer; positions here were
formed from the repo + external evidence, not from Codex's draft. Disagreements are expected
and should be arbitrated on the cited evidence.

**The ask:** replace `docs/GAP-CLOSURE-PLAN.md` (prose tables) with a system where gaps, their
phases, dependencies, and closure evidence are GROUND TRUTH — durable across N concurrent
agents, auto-populated, and never manually curated back into sync.

**Verdict in one line:** a flat, typed, append-only gap ledger (a sibling of
`cortex_core/task_ledger.py`) is the canonical store; all prose/registry views become
regenerated projections with a CI diff-gate; the state engine and closeout path auto-write
phase transitions; the living ontology is an **optional projection, not the substrate** —
it is not required for v0 and should only be wired in when G2's own retrieval gate is won.

---

## 0. The evidence: what actually failed, and why "structure" alone is not the fix

The owner's framing is that prose drifts. True — but the repo shows something sharper:
**structured registries drifted too, because their write path was manual.**

1. **Prose drift (the known case).** The 2026-07-13 fact-check
   (`docs/HARNESS-SCORECARD-CONSOLIDATED.md:7-12`) found headline numbers "live only in prose
   markdown — there is no committed results ledger" (`:8`), frozen snapshots not reproducible
   from disk (3,528 vs committed files, `:9`), and primaries disagreeing (nDCG 0.444 vs 0.462
   across PHASE-GATES vs EVAL-DESIGN, `:10`). `docs/GAP-CLOSURE-PLAN.md` C1-C3
   (`docs/GAP-CLOSURE-PLAN.md:31-33`) records the same.
2. **Structured-registry drift (the case that disproves "schema fixes it").** The repo already
   HAS a structured gap registry: `templates/workspace-control-plane/gaps/` with per-gap cards,
   a `registry.md` table, and a machine-readable `index.jsonl`
   (fields `id,title,status,phase,priority,mode,next_gate,updated_at`). It drifted twice over:
   - `registry.md:5-7` opens with "**Reconciled 2026-07-06** against actual repo state (the
     status column had drifted from reality)."
   - `index.jsonl` is stale against its own sibling: it stops at GAP-CORTEX-0012 (missing
     0013-0022 that `registry.md` carries) and most rows still hold the literal template
     placeholder `"updated_at":"YYYY-MM-DD"` (`index.jsonl:1-5`). The two sources disagree on
     the same gap: `index.jsonl:6` says GAP-CORTEX-0003 is `design_locked_pending_tdd_contract`;
     `registry.md:17` says `built_v1`.
3. **Phase state lives only in prose.** `docs/PHASE-GATES.md` is one markdown table per phase
   with status embedded inline as prose ("*(done 2026-07-04)*", "closed", "SHIPPED") — no
   structured status field; machine currency-tracking is impossible from that file alone.
4. **The fix that was written and then lost proves the meta-point.**
   `cortex_core/results_ledger.py` (the C1 fix) exists ONLY in an uncommitted worktree
   (`.claude/worktrees/agent-ad704d6381d55f171/cortex_core/results_ledger.py`, 251 lines) —
   not landed in the canonical tree as of this writing (UNVERIFIED whether it lands before
   this doc merges). A durability fix that itself lives in volatile agent state is the exact
   failure mode being designed against.

**Root-cause statement:** drift is a *write-path* disease, not a *format* disease. Any design
whose currency depends on an agent (or human) remembering to update a second representation
will drift — markdown or JSONL alike. Therefore the design's center of gravity is:
**one canonical append-only store + writes that happen as side effects of events that already
occur + a deterministic diff-gate that makes divergence a CI failure, not a memory duty.**

This matches the repo's own settled doctrine: detection over coercion
(`docs/design/cortex-redesign-CORRECTED-spec.md:14-31`), and the two named diseases to avoid:
Disease A eager context bloat (12,237 tokens of tool schemas measured,
`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:38-41`) and Disease B
mandatory-pipeline coercion (`:42-44`).

---

## 1. Q1 — Is a structured ground-truth schema better than prose? Yes; here is the schema

Yes — with the caveat above (schema + automated write path + projection gate, or it re-drifts).

### 1.1 The canonical store

`gaps/gap_ledger.jsonl` at the workspace root (same substrate class as
`logs/task_ledger.jsonl`, `task_ledger.py:10-13`): **append-only JSONL, one full state
snapshot per line, last-line-per-`gap_id` wins**, exactly the reduction `task_ledger.py`
already implements (`_current_state`, `task_ledger.py:186-195`). Nothing is ever rewritten in
place; the file is simultaneously the event log (audit trail) and, reduced, the current state.
It is committed to git (unlike `logs/`), because gap state is project ground truth, not
run telemetry — git then gives replication, review, and history-of-history for free.

### 1.2 The gap record (schema_version 1)

```json
{
  "schema_version": 1,
  "gap_id": "GAP-CORTEX-0031",
  "title": "Ontology not fused into retrieval RRF",
  "status": "open",
  "phase": "phase-7",
  "priority": "P1",
  "blocks": ["GAP-CORTEX-0034"],
  "blocked_by": [],
  "closes_metric": "graded_eval.multi_hop.ndcg > bm25_vector_baseline",
  "evidence": [
    {"path": "docs/design/cortex-complete-feature-sweep-2026-07-13.md", "line": 30,
     "kind": "diagnosis"}
  ],
  "owner_agent": null,
  "source": "arbitration-2026-07-13",
  "verified": false,
  "supersedes": null,
  "event": "create",
  "actor": "fable-orchestrator",
  "ts": "2026-07-13T21:04:00+00:00"
}
```

Field notes (each earns its place; anything not listed is rejected — anti-bloat):

- **`gap_id`** — stable, human-legible, monotonic within a namespace (`GAP-CORTEX-NNNN`),
  continuing the existing convention (`templates/workspace-control-plane/gaps/`). Never reused.
- **`status`** — small closed enum, one hop richer than task_ledger's four
  (`task_ledger.py:40`): `proposed | open | claimed | blocked | closed_pending_verify |
  closed | wont_fix | superseded`. The split of `closed_pending_verify` from `closed` is the
  anti-evidence-theater move (see §5.2): an agent can *report* closure; only a deterministic
  check or a human flips `verified: true` and status `closed`.
- **`phase`** — the roadmap/gate phase this gap belongs to (joins to `docs/PHASE-GATES.md`
  gate ids, which `ontology_seed.py:104-118` already regex-mints as entities).
- **`blocks` / `blocked_by`** — adjacency lists of `gap_id`s. This is how GitHub/Linear-class
  trackers model dependencies (see §4); at this corpus's scale (~36 gaps in
  `GAP-CLOSURE-PLAN.md`, ~26 in the old registry) transitive-blocker queries are a trivial
  in-memory BFS over the reduced ledger — no graph store needed.
- **`closes_metric`** — the machine-checkable closure condition, ideally a pointer into the
  results ledger (`evals/results.jsonl` lane/metric per the worktree draft's schema:
  `run_id, ts, lane, metric, value, n, decision, source_file, commit, provenance`). This is the
  join that makes closure *provable*: a gap closes by citing a results-ledger row / passing
  test / commit, not by prose assertion.
- **`evidence`** — list of `{path, line, kind}`; REQUIRED non-empty on any
  `closed_pending_verify`/`closed` event (same provenance discipline as ontology entities'
  mandatory `source_path`, `ontology.py:238-264`).
- **`owner_agent`** — claim holder; claim semantics identical to `task_ledger.claim_task`
  (`task_ledger.py:251-287`): read-check-append under the exclusive lock, exactly one of two
  racing claimants wins.
- **`verified`** — false by default (`ai_discovered`, per the corpus-wide honest-debt rule);
  true only via deterministic check or human action, recorded as its own event.
- **`event` / `actor` / `ts`** — event-sourcing minimums; `event ∈ {create, claim, release,
  update, block, close_pending, verify_close, reopen, supersede}`.

### 1.3 Why append-only event lines beat a mutable table (or prose)

- Every prior state is retained → the audit trail is the store (the same argument the state
  engine already runs: "the log IS the audit-closeout source", `state_engine.py:36-38`).
- Concurrent writers never rewrite each other — append + lock is the proven local primitive
  (`task_ledger.py:15-20`); a torn final line from a crash is skipped, not poisoning
  (`task_ledger.py:177-182`).
- Disagreement becomes impossible *by construction* between "history" and "current": current
  state is derived, not separately maintained. The registry.md/index.jsonl split (two
  hand-maintained representations) is structurally unrepresentable.
- External best practice agrees — see §4 (event sourcing, issue-tracker schemas, ADRs).

---

## 2. Q2 — Is the living ontology the right substrate? No (not as the substrate). Here's the honest accounting

The ontology (`cortex_core/ontology.py`) was *designed* for exactly this question — its
docstring: "which doc / rubric / gap is CURRENT ... replacing accreting markdown"
(`ontology.py:1-9`). It shares the ledger substrate (append-only JSONL + the same lock,
`ontology.py:11-32,50-53`), already declares `gap` and `phase` entity types and `depends_on`
/`supersedes` predicates (`docs/ontology/schema.yaml:34-54,61-101`), and has bi-temporal
invalidate-not-delete edges (`ontology.py:159-183`).

So why not just put gaps there?

**What the ontology adds beyond a flat ledger, concretely:**
1. Typed relations with schema validation across *heterogeneous* entity types
   (gap→module, gap→metric, rubric→domain) — a flat gap ledger only relates gaps to gaps.
2. Bi-temporal edge validity (`invalid_from`, `ontology.py:167`) — the ledger's last-wins
   reduction keeps history but does not model "this dependency was true from A to B".
3. `supersedes`-chain resolution (`current_version`, `ontology.py:544-574`).

**What it does NOT currently give you:**
1. **No generic multi-hop query.** `neighbors` is strictly one-hop (`ontology.py:515-541`);
   the only >1-hop walk is `current_version`, and it follows only `supersedes`
   (`ontology.py:544-574`). "What transitively blocks X" would have to be written either way —
   and over a reduced flat ledger it is the same ~15-line BFS.
2. **No retrieval payoff yet.** It is unwired into search: zero ontology references in
   `cortex_core/search.py`; `_search_rrf` fuses only BM25+vector (search.py:603, per
   `docs/design/cortex-complete-feature-sweep-2026-07-13.md:30,119,200`), and `.jsonl`/`.yaml`
   are excluded from the index (`docs/ontology/schema.yaml:18-20`). The recorded cost claim:
   "carries 6–8× cost with no payoff" (`docs/GAP-CLOSURE-PLAN.md:64`, gap G2).
3. **No write-path automation.** It is seeded by a scan (`ontology_seed.py`), i.e. it is a
   *projection of other files* — including, today, a projection of the drifted `registry.md`
   (`ontology_seed.py:120-125`). Making a projection the source of truth inverts the
   dependency and imports the drift.

**Verdict:** at 36-300 gaps, the graph value of gaps-as-entities is a BFS over an adjacency
list — the ontology's marginal value for *this* use is a nicer query surface, which does not
cover its recorded 6-8× cost while G2 is unresolved. The correct dependency direction is:
**gap ledger (canonical) → ontology sync (derived projection)**. A ~40-line idempotent sync
(deterministic ids via `make_entity_id`, `ontology.py:92-95`, so re-syncing upserts rather
than duplicates) mirrors gap records into `gap` entities and `blocked_by` into `depends_on`
edges whenever/if G2 lands and multi-hop retrieval over the graph shows a measured win — the
gate G2 itself already specifies ("fuse ... + gate on a golden-set retrieval win, OR shelve",
`docs/GAP-CLOSURE-PLAN.md:64`). The ontology is where cross-type queries ("which gaps touch
module X that also blocks phase 7?") eventually live; it is **not required** for durable gap
tracking, and making it required would couple this system's survival to an unproven 6-8× bet.

---

## 3. Q3 — Auto phase-tracking across N agents

Three write paths, all automatic, all serialized by the same lock discipline:

### 3.1 Claim/work/close via the ledger API (the task_ledger pattern, extended)

`cortex_core/gap_ledger.py` clones the task_ledger's concurrency core:
exclusive-create lockfile (`os.O_CREAT | os.O_EXCL`) with stale-steal on dead PID/age
(`task_ledger.py:115-147`), read-check-append critical section, last-wins reduction. An agent
claims a gap exactly as it claims a task; the loser of a race is refused with the winner's
identity (`task_ledger.py:254-287` semantics). CLI + MCP surface: **one** tool
(`cortex-gap` / `cortex_gap`) with an `action` argument — not five new tools (Disease-A
budget; the G5 consolidation direction, `docs/GAP-CLOSURE-PLAN.md:67`).

### 3.2 State-engine integration: phase transitions as side effects

The state engine already event-sources every task superstep with server-written closeouts a
client cannot skip (`state_engine.py:33-38`) and records `intent` as first-class state
(`state_engine.py:898-921`). Wiring:

1. A task created to work a gap carries `intent.gap_id`.
2. On the engine's CLOSEOUT→DONE transition (and on task_ledger `update(status=done)`), the
   server-side closeout writer appends a gap event: `status=closed_pending_verify`, evidence =
   the closeout path + the task's event-log id. **The agent writes nothing extra** — the
   transition it already performs IS the gap write (passive recording, per the CORRECTED
   spec's "transcript-driven scribe" doctrine, `cortex-redesign-CORRECTED-spec.md:19,27`).
3. On ABANDONED (rework/escalation caps, `state_engine.py:33-35`), the gap event is
   `release` + a `failed_attempt` evidence entry — failure is recorded, ownership returns.
4. `verify_close` fires only from a deterministic checker: a results-ledger row satisfying
   `closes_metric`, a named test passing in CI, or an explicit human event. Judges never flip
   `verified` (the objective-lab rule: deterministic checkers, never judges, in verdict paths).

### 3.3 Concurrency safety with N agents, stated precisely

- **Within one workspace/filesystem:** the exclusive-create lockfile serializes
  read-check-append; appends are whole-line; torn trailing lines are skipped on read
  (`task_ledger.py:159-183`). Reads are lock-free and at worst stale, which the atomic claim
  then corrects — the exact contract task_ledger documents (`task_ledger.py:240-243`).
- **Across git branches/worktrees (the real N-agent topology here):** append-only JSONL is
  the merge-friendliest format git offers — concurrent branches append disjoint lines, and a
  union merge (`.gitattributes: gaps/gap_ledger.jsonl merge=union`) resolves without conflict;
  because state is last-wins *per gap_id* and events carry `ts`+`actor`, a
  reconcile-on-merge doctor rule re-sorts by `ts` and flags genuinely conflicting terminal
  events (two agents both closing one gap) instead of silently losing one. CAVEAT
  (UNVERIFIED risk, flagged honestly): union-merge does not *guarantee* causal order across
  branches; the doctor re-sort + conflict flag is the required companion, not optional.
- **Server plane (cortex-local / multi-tenant later):** the same API runs behind the MCP
  server where the state engine's `BEGIN IMMEDIATE` discipline (`state_engine.py:802-817`)
  already serializes cross-process writers; the JSONL ledger can then be re-homed onto the
  engine's SQLite with the identical record schema — the schema, not the file format, is the
  contract.

### 3.4 The projection gate (what keeps prose honest forever)

`cortex-gap render` regenerates the human views — a `docs/GAPS.md` board (successor to
`GAP-CLOSURE-PLAN.md`'s tables) and the per-phase rollup — from the reduced ledger.
CI + `cortex-doctor` run `render --check`: regenerate to a temp file, diff against the
committed view, **fail on mismatch**. Hand-editing a projection becomes a deterministic CI
failure, not a drift that waits for a fact-check. This is detection, not coercion: no agent
is refused mid-work; divergence just cannot merge.

---

## 4. Q4 — External best practice (WebSearch)

Findings from a dedicated web-research pass (2024-2026 sources; URLs cited; anything not
independently verifiable is marked). The 2026 consensus converges on a four-part shape that
this design follows:

### 4.1 Issue-tracker schemas: typed directional edges + machine-enforced state, never prose
- **GitHub Issues** made `blockedBy`/`blocking` first-class canonical fields (GA 2025-08-21,
  ≤50 links per type, API/CLI surfaced) — and notably does NOT hard-enforce them: a blocked
  issue can still close; the relation is advisory metadata over a graph. Detection over
  coercion, at GitHub scale.
  https://github.blog/changelog/2025-08-21-dependencies-on-issues/ ·
  https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies
- **Jira** stores dependencies as a flat typed edge table (`issuelink`:
  id/linktype/source/destination; `issuelinktype` gives one row two directional readings —
  "blocks"/"is blocked by"), and workflow status as an explicit per-project state machine
  (`OS_WFENTRY`/`OS_CURRENTSTEP`), never a free-text field.
  https://developer.atlassian.com/cloud/jira/platform/issue-linking-model/ ·
  https://developer.atlassian.com/server/jira/platform/database-issue-status-and-workflow/
- **Linear**: minimal canonical enums — team-scoped WorkflowState objects referenced by UUID
  (Backlog→Todo→In Progress→Done→Canceled) + numeric priority 0-4; a real MCP-server bug
  (#3150) shows what breaks when agents pass status *names* where the schema wants ids.
  https://linear.app/docs/configuring-workflows ·
  https://github.com/modelcontextprotocol/servers/issues/3150

### 4.2 Event sourcing: append-only log = truth; current state = derived projection
- Canonical pattern (Azure Architecture Center): append immutable events; serve queries from
  rebuildable projections; audit trail and point-in-time reconstruction come free. Exactly the
  task_ledger/state_engine shape this design extends.
  https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing
- Known pitfalls to design in from day one: event-schema versioning/upcasters, projection
  lag, replay cost (snapshots), operational tax; and it is overkill where history never
  derives state. https://dzone.com/articles/event-sourcing-guide-when-to-use-avoid-pitfalls
  — hence v0 here is the pragmatic middle: append-only log + one cheap deterministic
  projection, no CQRS machinery.

### 4.3 ADRs: immutable one-file-per-decision beats living prose
Accepted ADRs are never rewritten; change = a NEW record with a `superseded-by` link both
ways (MADR/adr-tools convention). The gap ledger applies the same rule at event granularity:
`supersede` is an event, never an edit.
https://github.com/architecture-decision-record/architecture-decision-record ·
https://adr.github.io/madr/

### 4.4 Multi-agent state 2025-2026: single-writer or declared reducers; scratchpads drift
- **LangGraph**: per-field declared reducers (append-accumulate vs last-write-wins) merged in
  checkpointed supersteps — the concurrency policy made explicit in the schema.
  https://docs.langchain.com/oss/python/langgraph/persistence
- **Anthropic's multi-agent research system**: lead agent persists the plan to external
  memory BEFORE spawning; subagents write findings to shared stores, lead reconciles —
  chat-return-only state is the anti-pattern (the same lesson as this repo's
  capture-subagent-output rule).
  https://www.anthropic.com/engineering/multi-agent-research-system
- **Cognition** ("Don't Build Multi-Agents", 2025; "Multi-Agents: What's Actually Working",
  2026): multi-agent works when "writes stay single-threaded" — many readers, serialized
  writers. https://cognition.com/blog/dont-build-multi-agents ·
  https://cognition.com/blog/multi-agents-working
- Shared free-form markdown "can drift when several agents update them" (practitioner
  consensus + emerging research: CodeCRDT, https://arxiv.org/pdf/2510.18893). The slogan
  "scratchpads drift, ledgers don't" as a published finding: UNVERIFIED — the underlying
  claim is well-supported, the phrase is not a citable artifact.

### 4.5 Graph DB / ontology vs flat ledger: flat wins at this scale
Graph stores earn their cost only when variable-depth traversal dominates at large scale;
"you don't need a graph database to follow a hierarchy of nodes" — recursive CTE / in-memory
BFS over an adjacency list is fully sufficient below ~10^5 nodes (benchmark cliff cited at
335K nodes). Jira itself ships its blocks-graph as a flat edge table.
https://memgraph.com/blog/graph-database-vs-relational-database ·
https://www.fusionbox.com/blog/detail/graph-algorithms-in-a-database-recursive-ctes-and-topological-sort-with-postgres/620/
"Don't build an ontology when a schema will do" as a verbatim published maxim: UNVERIFIED —
but it is the operational consensus of the cited literature (start with a typed edge list +
fixed link vocabulary; reach for a graph store only when queries are relationship-shaped,
multi-hop, AND large). This directly supports §2's verdict.

### 4.6 Append-only file ledgers: atomicity fine print
- POSIX `O_APPEND` never loses bytes locally but concurrent writers' bytes can interleave;
  the "appends ≤ PIPE_BUF are atomic" folk rule is guaranteed ONLY for pipes, not files —
  a widely repeated misconception. NFS breaks append atomicity. Practical bar: one small
  single-`write()` line per record, local FS only, and a lockfile (or single-writer funnel)
  when multiple concurrent writers exist. https://nullprogram.com/blog/2016/08/03/
  → task_ledger's lockfile-serialized append (`task_ledger.py:115-165`) already meets this
  bar; Windows `FILE_APPEND_DATA` atomicity semantics: UNVERIFIED in this pass, so the
  lockfile stays mandatory on win32.
- Durability: append ≠ durable until fsync (Redis AOF's fsync policies are the reference
  design; log replay on startup = projection rebuild).
  https://severalnines.com/blog/importance-append-only-file-redis/
- **Plain-text accounting (beancount) is the closest existing pattern to this design**: text
  ledger in git, every change a reviewable diff, deterministic validator as a pre-commit
  write-gate, merge-mediated multi-writer concurrency.
  https://beancount.io/docs/Solutions/transparent-and-auditable

### 4.7 The 2026 consensus, in one sentence
Immutable append-only records as ground truth + derived rebuildable projections + flat typed
directional edges for dependencies + single-writer/serialized or reducer-declared concurrent
writes, with deterministic validators at the write/merge boundary — and shared free-form
prose docs are the one pattern every source warns against.

---

## 5. Q5 — Anti-bloat / anti-ritual (Disease A and B), and the anti-adversarial pass

### 5.1 Anti-Disease-A (context bloat)
- One MCP tool with an `action` arg; no new schemas resident per-phase beyond it.
- Agents never load the whole ledger: the default read is `cortex-gap list --phase X --status
  open` — a scoped projection of ~10-30 lines. The full event log is for the doctor and
  audits, not for working context.
- The rendered `docs/GAPS.md` is small (one line per open gap) and is what scope-packs index.

### 5.2 Anti-Disease-B (governance ritual)
- **Zero new mandatory steps.** Nothing refuses work because a gap wasn't claimed. The
  automatic writes ride transitions agents already perform (closeout, task done); the only
  *manual* writes are `create` (replacing "add a row to a markdown table" — strictly cheaper)
  and optional `claim`.
- Closure ceremony is replaced by evidence linkage: `close_pending` costs one call with
  evidence the agent already has in hand at closeout time.
- The CI diff-gate acts on artifacts, not on agents mid-flight — the CORRECTED spec's
  "post-hoc deterministic scorer" placement (`cortex-redesign-CORRECTED-spec.md:26`).

### 5.3 Anti-adversarial: where this (Claude-built) approach is fragile
1. **The registry precedent cuts against me.** A Claude-lineage agent built
   `index.jsonl`+`registry.md` and they drifted anyway. My design differs in two enforced
   ways (single canonical store; CI render-check) — but if the render-check is not actually
   added to CI, this design decays into registry-v3. The CI check is therefore part of the
   v0 definition-of-done, not a follow-up.
2. **Self-graded closure.** An agent can mint its own closeout and cite it as closure
   evidence — evidence theater with better provenance. Mitigation is structural:
   `closed_pending_verify` ≠ `closed`; `verified` flips only deterministically/humanly; the
   scorecard counts only `verified` closures. Residual risk: gaps whose `closes_metric` is
   not machine-checkable will pool in `closed_pending_verify` — that pool size is itself an
   SLI to surface, not hide.
3. **Last-wins stomping.** Any agent can append a state that regresses another's (reopen,
   re-claim after release). History is preserved so it is auditable, but auditable ≠ noticed:
   the doctor needs explicit rules (terminal-state regression, claim-steal without release,
   two closers) that emit into the standing digest.
4. **Ledger-vs-ontology verdict may be self-serving.** "Build the simple thing I can finish
   tonight" is a known agent bias. The check is pre-registered and objective: G2's own gate
   (measured multi-hop retrieval win) decides ontology wiring — not this document's author.
5. **Git-merge causality (3.3 caveat)** is the weakest concurrency link and is marked
   UNVERIFIED until a two-branch conflict test exists (part of v0 tests).
6. **Schema evolution.** Event-sourced logs outlive schemas; `schema_version` per line +
   forgiving-read (unknown fields kept, missing fields defaulted) is required from day one,
   or the first schema change orphans the history (a known event-sourcing pitfall, §4).
7. **Windows-specific lock caveats** are inherited from task_ledger (PID-reuse on stale-steal,
   delete-pending PermissionError retry, `task_ledger.py:132-144`) — known, tested there,
   reused rather than re-invented.

---

## 6. Q6 — The recommendation and the minimal first build

**ONE design:**
- **Schema:** §1.2 gap record, event-sourced, `schema_version` 1.
- **Storage:** committed `gaps/gap_ledger.jsonl`, append-only, last-wins per `gap_id`,
  task_ledger lock discipline reused (import, don't copy — the ontology already set that
  precedent, `ontology.py:50-53`).
- **How agents write:** one `cortex-gap`/`cortex_gap` surface (`create/claim/release/
  close_pending/verify/list/render`); automatic events from state-engine CLOSEOUT and
  task_ledger done-updates via `intent.gap_id`.
- **How phases auto-track:** §3.2 — transitions agents already perform append gap events
  server-side; deterministic verify closes; `render --check` in CI keeps every human view a
  byte-exact projection.
- **Ontology's role:** OPTIONAL derived projection (idempotent sync), gated on G2's measured
  retrieval win. NOT required for v0; explicitly not the substrate.

**Minimal first build (v0, ~1 day, stdlib only):**
1. `cortex_core/gap_ledger.py` (~250 lines: schema validation, create/claim/release/
   close_pending/verify/list, render, doctor rules) + `tests/test_gap_ledger.py`
   (concurrency race, torn-line, regression-detection, two-branch union-merge reconcile).
2. **Migration:** one script parses `docs/GAP-CLOSURE-PLAN.md` (A1-I6) +
   `templates/workspace-control-plane/gaps/registry.md` into ledger `create` events —
   every migrated record `verified:false`, `source` naming the origin doc; the two dead
   registries and the prose plan get a superseded-by banner pointing at the rendered view.
3. **Render + CI gate:** `cortex-gap render` emits `docs/GAPS.md`; `cortex-doctor` +CI run
   `render --check` (fail on hand-edit/drift).
4. **One auto-write hook:** the existing closeout writer appends `close_pending` when its
   metadata carries `gap_id` — proving the zero-ceremony path end-to-end on the first real
   task.

Deferred (each behind its own evidence gate): ontology sync (G2), SQLite re-homing for the
served plane, phase-gate entities as first-class ledger records (after the gap half proves
out), results-ledger join hardening (needs C1 to land from its worktree).

---
*Author: Fable (Claude), 2026-07-13. Independent of Codex's parallel review. Everything
marked UNVERIFIED is unverified; migration output will be `ai_discovered` until human-checked.*
