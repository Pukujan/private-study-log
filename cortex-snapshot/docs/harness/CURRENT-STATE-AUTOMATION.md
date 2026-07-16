# Current-state automation contract

This document defines how Cortex should keep a large, long-lived project understandable without
letting the newest transcript, closeout, or markdown file silently replace project truth.

## Why drift occurs

Cortex currently has independently writable handoffs, closeouts, task records, ontology logs,
corpus documents, runtime receipts, and cross-repository configuration. They preserve useful
history, but no deterministic transaction promotes the new state and invalidates every affected
old projection. Search can therefore find a newer file without knowing whether it is authoritative,
and a task-local update can be mistaken for a project-wide decision.

The former closeout writer made this concrete: every task handoff overwrote root `HANDOFF.md`.
Closeouts now refresh `LATEST-CLOSEOUT.md` and preserve a deliberately authored root handoff, but
that guard is only containment. The complete solution is the reducer below.

## Separate artifact roles

| Role | Examples | Update rule |
|---|---|---|
| Normative contract | expected behavior, security policy, frozen evaluation contract | deliberate reviewed version; explicit supersession |
| Operational state | active goal, active runs, blockers, capability status, next actions | deterministic reduction of accepted events |
| Decision history | ADRs, task closeouts, incidents, research decisions | immutable append; never treated as current by recency alone |
| Evidence | test output, signed receipts, traces, captured research, evaluator results | immutable/content-addressed; authority and expiry retained |
| Projection | `HANDOFF.md`, ontology current view, status dashboard, resume pack, search index | generated from operational state; never independent truth |

## Required architecture

```text
externally verified project events or narrow task/run agent self-reports
  -> validate schema, authority, scope, evidence and expected prior revision
  -> append immutable event
  -> deterministic reducer
  -> project-state/current.json
       -> render human HANDOFF.md
       -> upsert/invalidate living-ontology entities and relations
       -> rebuild active-only retrieval index
       -> emit minimal agent resume pack
       -> publish capability/status view
```

`project-state/current.json` is the single machine-readable operational authority. It is a
materialized view and can always be rebuilt from the event log. The living ontology is a graph
projection for resolution and dependency traversal; it is not allowed to certify runtime truth.

> **Authority bootstrap is currently BLOCKED in production.** Cortex has the deterministic
> `authority_verifier(event) -> bool` callback boundary, but no production authority-verifier
> service is deployed. Event fields such as `HUMAN_OWNER`, `DOMAIN_EXPERT`, or
> `COMPONENT_OWNER` are claims, not proof. With the default verifier, only `AGENT`
> `OPERATIONAL`/`DECISION` self-reports at `TASK` or `RUN` scope may resolve. Normative,
> project/component, runtime, capability, and privileged-authority events remain `UNRESOLVED`.
> Tests may inject a deterministic verifier; that does not activate live normative authority.

## Event and current-state minimum fields

Every state-changing event needs:

- stable event, project, run, task, subject, and scope identifiers;
- event type and expected prior project revision;
- actor identity and authority class;
- `observed_at`, `valid_from`, optional expiry, and append time;
- changed claims, blockers, next actions, and affected document/capability IDs;
- evidence references with hashes and independence/provenance class;
- explicit `supersedes`/`invalidates` targets where applicable; and
- source repository plus commit/config version.

Every current subject needs exactly one lifecycle state, one authority owner, its last accepted
event, last verification time, evidence set, and any freshness deadline. Unknown or conflicting
state reduces to `UNRESOLVED`, never to whichever file is newest.

## Reducer invariants

1. At most one active authority exists for a given `(project, scope, subject, authority_role)`.
2. A newer timestamp alone cannot supersede a normative or project-wide record.
3. Scope cannot widen implicitly: a task closeout cannot replace project outcome or policy.
4. Supersession is explicit and type/scope compatible.
5. Runtime/capability claims require a cryptographically verified receipt, not a self-labeled
   `SIGNED` record, and become stale on expiry.
6. Conflicting accepted events fail reconciliation and remain visible as a blocker.
7. History is retained but excluded from default current retrieval after invalidation.
8. Projections carry the reducer revision and content hash that produced them.
9. A failed projection leaves the committed event recoverable and marks projections dirty.
10. Replaying the same event log produces byte-equivalent canonical state.

## Closeout transaction

A closeout must submit one bounded event bundle: task result, evidence, capability changes, document
impact set, blockers, next actions, and explicit supersessions. The reconciler validates the bundle,
appends it using an expected-revision check, reduces state, then regenerates all projections. It must
not directly hand-edit root handoff, ontology, and search state as unrelated writes.

Cross-repository components (wrapper, Hades, Gravebuster, evaluator, telemetry collectors) publish
versioned manifests or signed observations. Cortex joins them by project/run/route identity. Their
files or logs are evidence inputs; their local prose cannot directly promote central state.

## Retrieval behavior

The default agent query searches the active projection only, ordered by authority and scope before
semantic relevance. Historical material requires an explicit history request and is labeled with
its invalidation reason and replacement. A cold agent receives a small resume pack: locked outcome,
active work, blockers, next safe action, current normative/status documents, and evidence hashes.
It does not receive the entire accumulated corpus by default.

## Automation gates

CI and the periodic reconciler must reject or flag:

- two active authorities for the same scope;
- project-state changes without an event and evidence classification;
- broken or missing source/evidence references;
- supersession across incompatible types or wider scopes;
- changed capability/code owners with an unacknowledged document impact set;
- expired runtime claims still shown as active;
- projections whose reducer revision/hash differs from current state;
- active search documents that the graph marks superseded; and
- hand-authored edits to generated projections.

## Delivery sequence

1. **Built:** closeout overwrite guard and generated-projection labels.
2. **Built:** versioned event/current schemas and scope/authority vocabulary.
3. **Built:** deterministic reducer, explicit validity time, conflict handling, replay and authority gate.
4. **Built for task closeout:** run-bound closeout emits a recoverable event after audit commit; the
   two records are explicitly reconciled rather than falsely described as one atomic transaction.
5. **Built:** atomic current/file projections, generated handoff/resume/status/doc views, ontology
   lifecycle and unambiguous supersedes synchronization, anchored receipt and dirty recovery.
6. **Built:** active-only default retrieval with explicit history mode and query-time barriers.
7. **Open:** signed cross-repository/runtime manifest ingestion and automatic expiry.
8. **Built locally:** replay/tamper/authority-revocation/projection/ontology drift gates, CLI and
   doctor visibility. CI deployment and a trusted initial normative bootstrap remain open.

The current checkout intentionally has no `project-state/` bootstrap. The default verifier accepts
no privileged claim; only bounded agent operational/decision self-reports at task/run scope resolve.
Project, component, normative, human-owner, evaluator, runtime, and capability authority requires an
external verifier that is not yet deployed.

## Exit conditions

- A narrow closeout cannot replace a project-wide goal in any projection.
- Given only the event log, Cortex reconstructs the same current state and current document graph.
- Every displayed current claim resolves to its accepted event and evidence.
- Old documents remain auditable but cannot appear as current without an explicit history query.
- A changed/expired external component is reflected automatically or produces a visible dirty or
  unresolved state; it is never silently assumed current.
- A new agent can resume from the generated pack without reading a transcript or guessing between
  handoffs.
