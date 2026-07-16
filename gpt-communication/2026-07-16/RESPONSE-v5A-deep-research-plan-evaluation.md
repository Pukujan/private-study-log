# ChatGPT Deep Research Response v5-A — Plan Evaluation (First Pass)

## Date: 2026-07-16
## From: ChatGPT (deep research)
## Responding to: BRIEF-v5-deep-research-plan-evaluation.md

---

## Verdict

No — not as written.

Fable correctly diagnoses the failure mode and its guardrails should be retained. But the four builds deliver mostly the front half of the decision loop. They do not deliver the full Cortex product.

The central mistake: Build 1 alone is treated as the critical path while state-machine enforcement, automatic closeout, gap integration and MAPE-K are outside it. Narrowing dialogue cannot by itself prevent scope expansion, self-certification, bypassed verification, lost execution state, or downstream maintenance cost.

---

## 1. Coverage Against The Cortex Definition

| Cortex Layer | Fable Plan | Verdict | What's Missing |
|---|---|---|---|
| Runtime contract | No | Missing | No mandatory host protocol, normalized lifecycle events, assured/advisory distinction, native-tool enforcement |
| Workflow engine | Assumed existing | Partial | Not connected into one enforced run |
| Knowledge intelligence | Builds 1-2 | Partial | No contradiction handling, refusal boundary, or proven downstream benefit |
| Decision engine | Build 1 | Mostly partial | Human authorization and decision-bound sufficiency not wired to execution |
| Execution and assurance | Not addressed | Missing | Existing mission, receipts, deterministic gates not integrated |
| Learning and optimization | Builds 3-4 | Insufficient | Recording speculative scores ≠ MAPE-K optimization loop |

**Loop coverage:**
- Decision loop: Partial to strong
- Delivery loop: Weak
- Optimization loop: Mostly absent

**Key finding:** The current code already has pieces Fable underuses:
- State engine: event-sourced, single-writer, idempotent, resumable, claim-aware, rework-bounded
- Assured tracks already require decision-bound research-sufficiency receipts, support UNRESOLVED and ABSTAIN
- app_build binds deterministic smoke verdicts, supports bounded rework
- Missions decompose heterogeneous work, assign disjoint ownership, bind receipts per worker
- Capability router considers model capability, independence, availability, returns UNRESOLVED
- Wrapper already represents runtime-independent client boundary

---

## 2. Decisions on Fable's Four Builds

| Proposal | Decision | Reason |
|---|---|---|
| Narrowing dialogue | RETAIN, but not standalone | Must be inside an end-to-end assured run |
| Context packet | RETAIN AS PROJECTION | Do not create another canonical JSONL store |
| Proactive injector | RETAIN AS EXPERIMENTAL/DEFAULT-OFF | Requires no-injection boundary and measured benefit |
| Confidence ledger | REPLACE | Combines mathematically incompatible quantities |
| Risk ledger | MERGE | Tradeoffs belong with decision/outcome events |
| Ontology as decision authority | REJECT | Keep as currency/status and optional retrieval |
| Existing state engine | RETAIN AND EXTEND | Already owns execution legality and durable task state |
| Existing eval lanes | RETAIN AS ACTIVE INFRASTRUCTURE | Not "finished" until they cover shipped task classes |
| Separate self-learning subsystem | DEFER | Optimization should consume real run outcomes |
| Five-tool MCP target | REQUIRE EVIDENCE | Progressive disclosure > arbitrary count |

### The confidence design is not safe to build

Fable's confidence proposal combines:
- A probability-like research-sufficiency score
- A path-success probability
- Cohen's kappa as "agent reliability"
- A weighted geometric mean
- A Brier score over the composite

This is not statistically valid. Brier evaluates probabilistic predictions against outcomes. Cohen's kappa measures annotator agreement, not probability of success.

Correct approach:
- Brier score: path_success_probability ONLY
- Track separately: research policy status, evidence coverage, reviewer agreement/kappa, human approval, deterministic verifier result
- No universal "confidence" composite until enough outcome history + empirically justified calibration model

### Risk tier must remain policy-controlled

Tradeoffs are evidence, but risk tier cannot be mechanically derived from accumulated tradeoffs. Acceptable balance between safety, speed, cost, maintenance is partly human/policy judgment.

Use: tradeoff evidence + deterministic facts + human-approved risk policy → risk tier
Not: number/shape of tradeoffs → automatic LOW/MEDIUM/HIGH

---

## 3. Corrected Build Order

### BUILD A — One governed vertical slice
Goal: Prove Cortex prevents downstream damage on one real task class.

Required assured path:
SEARCH_BRAIN → NARROW → RESEARCH → RESEARCH_DECISION → OUTCOME_LOCK → PLAN → SPEC → IMPLEMENT ↔ REVIEW → automatic CLOSEOUT → DONE / ABSTAINED

OUTCOME_LOCK must require: human approval identifier, scope digest, allowed mutation surface, acceptance-check digest, research-sufficiency receipt, impact forecast with evidence labels.

REVIEW must never use the permissive default_gate for assured tracks.

### BUILD B — Proactive context injection
Retain proposed stages but:
1. Fixed weights are uncalibrated hypotheses, not production policy
2. Injector must be able to return: NO_RELEVANT_CONTEXT, CONFLICTING_CONTEXT, STALE_CONTEXT, UNRESOLVED
3. Store injection events in existing run/project event stream
4. Context packet = derived artifact, not another authority

### BUILD C — Runtime and task-class expansion
Goal: Make proven vertical slice portable across hosts.

Host service contract must require: register host/session, start assured run, attach run/task/route IDs, report native tool intent, obey allow/block/downgrade result, submit artifacts, submit external verdict IDs, receive next legal action.

Correct behavior when Cortex unavailable:
- During advisory work: allow with explicit advisory status
- During assured work: downgrade to UNASSURED or pause. Never continue while retaining assured claim.

### BUILD D — Outcome telemetry and constrained optimization
Goal: Learn from real work after workflow is proven.

Add typed events to existing authoritative stream:
intent_recorded, narrowing_answered, evidence_bound, outcome_locked, route_selected, artifact_committed, verification_recorded, human_result_recorded, forecast_resolved, downstream_incident_recorded, optimization_proposed

---

## 4. Frozen Service Contract

| Player | Owns | Required Output | Must Never Own |
|---|---|---|---|
| Human owner | Intent, tradeoffs, scope exceptions, consequential approval | Outcome lock or rejection | Implementation correctness |
| Host runtime | Native-tool interception, event forwarding | Correlated run/tool events | Verdicts or receipts |
| Cortex state engine | State, legality, retries, leases, scope, identifiers | Authoritative transition event | Semantic correctness |
| Knowledge layer | Retrieval, provenance, status, contradictions | Ranked bounded context or abstention | Authorization |
| Strong driver | Narrowing, decomposition, recommendation, synthesis | Evidence-linked plan | Certifying its own work |
| Worker models | Bounded labor | Artifact + execution evidence | Scope changes |
| Independent verifier | Acceptance-check execution | PASS, FAIL, ABSTAIN or ENVIRONMENT_UNAVAILABLE | Changing human intent |
| Capability router | Evidence-aware model-role selection | Route receipt or UNRESOLVED | Silent dispatch authority |
| Scribe/projections | Human-readable summaries, durable views | Derived closeout/result presentation | Canonical truth |
| MAPE-K | Bounded optimization proposals | Measured proposal with rollback | New features or weaker assurance |

---

## 5. Measurement Plan

| Failure Cortex Should Prevent | Metric |
|---|---|
| Wrong interpretation | Intent corrections detected before first mutation |
| Rebuilding existing work | Duplicate solution or prior-art reuse rate |
| Skipped research | Mutations lacking valid research receipt |
| Weak planning | First-verification failure and bounded-rework count |
| Scope expansion | Unauthorized file/resource/action count |
| Self-certification | Claimed passes without independent receipt |
| Lost decisions | Successful resume rate after restart |
| Repeated failures | Incident recurrence within 30/90 days |
| Docs/state/code disagreement | Projection or artifact consistency violations |
| Hidden maintenance cost | Rework and regressions within 7/30 days |
| Excess judgment burden | Human minutes per verified outcome |
| Excess labor cost | Tokens, API cost, elapsed time per verified outcome |
| Poor routing | Verified success and cost by model, role, task type |
| Harmful injection | Injection precision, omission rate, negative outcome delta |
| Bad forecasts | Brier score by task type for genuine probability forecasts |

Efficacy test: compare repeated matched tasks under A (no Cortex), B (advisory/retrieval only), C (full assured Cortex vertical slice).

Ship criterion: repeatable improvement in verified outcomes or downstream rework without unacceptable increases in human time, cost, or latency.

---

## Final Decision

Do not give Hermes Fable's four builds unchanged.

**Approve:** Fable's failure diagnosis, narrowing dialogue, proactive injection as measured experiment, ontology remaining non-authoritative, consumer/wiring/measurement guardrails.

**Replace:** Build 1 (narrowing interface) → Build A (one complete governed decision-to-delivery vertical slice).

**Merge:** Fable's confidence and risk ledgers into existing event-sourced decision/outcome record.

**Defer:** Calibrated confidence and MAPE-K automation until real production outcomes exist.

Then add injection, cross-runtime enforcement, and broader task coverage around the proven vertical slice.
