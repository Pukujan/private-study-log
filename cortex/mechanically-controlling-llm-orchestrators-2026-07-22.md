# Mechanically Controlling LLM Orchestrators

**Research Evidence, Production Architecture, Public Benchmarks, and Strict Critique**

*Owner-supplied, AI-assisted. Citations AI-suggested; production-system refs (Temporal, Durable
Functions, Step Functions, OPA/Cedar/SPIFFE/in-toto) are solid, several 2026 arXiv IDs are UNVERIFIED.
Filed to the Cortex study log 2026-07-22. The strongest answer here is not a better orchestrator prompt;
it is to remove control-plane authority from the LLM orchestrator.*

## Abstract

The orchestrator is frequently the most fragile and privileged component of an AI-agent system. It
determines which workers run, what information they receive, when retries occur, whether outputs are
accepted, when execution terminates, and which actions reach external systems. Giving those
responsibilities to a probabilistic language model creates structural risks: uncontrolled loops,
incorrect delegation, lost state, premature termination, excessive resource use, ignored verification
results, and unauthorized actions.

Recent studies support the observation that many multi-agent failures arise from orchestration,
specification, handoff, verification, and termination problems rather than from insufficient model
intelligence alone. Harness-Bench further demonstrates that the execution harness can materially change
completion, efficiency, and failure behavior even when the underlying model is held constant [1, 2].

The most mature production precedent comes from durable workflow systems such as Temporal and Azure
Durable Functions. These require orchestration logic to be deterministic and largely side-effect-free,
while external I/O and fallible work are delegated to separately managed activities. This suggests an LLM
should not be the real orchestrator; it should be a constrained planner that proposes actions to a
deterministic workflow controller.

> **Probabilistic models propose work. A deterministic controller owns state transitions, permissions,
> retries, budgets, verification, and commits.**

## 1. Evidence that orchestration is a major failure source

**1.1 Multi-agent failures are often structural.** The MAST study examined five popular multi-agent
frameworks across 150+ tasks and identified 14 recurring failure modes grouped into (1) specification /
system-design failures, (2) inter-agent misalignment, (3) task verification and termination failures.
Simple role-prompt / orchestration-instruction improvements did **not** eliminate the problems — deeper
architectural change was required [1]. These map onto practical orchestrator failures: delegating the
wrong task, losing requirements during handoff, accepting an incomplete result, propagating one worker's
mistake, invoking agents without progress, failing to recognize completion, terminating before
verification, continuing after completion. (Public: `multi-agent-systems-failure-taxonomy/MAST`.)

**1.2 Harness design changes agent performance.** Harness-Bench evaluated model+harness combos on 106
sandboxed tasks / 5,194 trajectories and found substantial differences in completion, efficiency,
process quality, and failure by harness configuration — not merely the model [2]. SWE-agent similarly
found a purpose-built agent-computer interface materially improved SWE performance [3]. Conclusion:
*"Claude"/"Codex" is not the complete agent; model + context manager + tools + permissions + retry
policy + state representation + orchestration loop together are the operative system.* Swapping the
model while keeping uncontrolled orchestration may not fix the problem.

## 2. The critical design error

A common architecture gives one LLM: understand task → plan → choose agents → write prompts → distribute
context → call tools → inspect results → decide correctness → retry/replan → determine completion →
perform final action. This combines five different authorities: **planning** (what should happen),
**scheduling** (what runs next), **information** (who sees what), **evaluation** (whether work is
acceptable), **effect** (durable changes). A single probabilistic component should not hold all five.
Instructions like "always run tests before finishing / never retry more than three times / do not modify
policy files / wait for every reviewer" are behavioral *requests*, not enforced invariants.

## 3. The strongest production precedent

- **Azure Durable Functions:** orchestrator code must be deterministic (state reconstructed by
  event-history replay); no direct I/O — I/O lives in separately scheduled activity functions [4]. The
  LLM invocation should be an *activity*, not the orchestration kernel.
- **Temporal:** workflow logic deterministic; external/nondeterministic calls belong in Activities; event
  history resumes after failure without repeating recorded decisions [5].
- **AWS Step Functions:** workflows as state machines with explicit retries, catches, timeouts, fallback
  transitions [6].

These do not solve AI reasoning; they ensure execution follows *declared* transition/retry/timeout/
recovery semantics rather than whatever an LLM decides at runtime.

## 4. Planner vs orchestrator

**LLM planner** may: interpret/decompose a task, propose a workflow, identify dependencies, recommend
worker roles + verification, request a bounded replan — it emits a *proposal*. **Deterministic
controller** validates the proposed workflow, owns the authoritative state machine, decides the legal
transition, dispatches registered worker types, assigns capabilities, enforces budgets, records
outcomes, invokes verification, selects retry/abort/escalation, authorizes commitment — it does not
reason in natural language. This is a control-plane inversion: LLM planner → proposes typed plan;
deterministic control plane → validates → schedules → authorizes → records → terminates.

## 5. Recommended capability boundaries

**LLM planner** may read the task contract / approved research / artifact summaries, propose a typed task
graph, select registered worker roles, request a predefined transition, request a bounded retry,
recommend abort/escalation. It **must not** execute shell, write repo files, call external APIs directly,
hold credentials, mint capabilities, create worker identities, modify policy, raise budgets, change its
own prompt/model, approve its own plan, verify its own result, read hidden tests, commit external state,
or bypass required stages.

**Workers** get temporary scoped capabilities (role, task, operation, scope-glob, base-tree,
expires-after, max-writes); they cannot delegate authority directly to other workers — delegation returns
through the scheduler. **Verifier** reads candidates + runs approved checks + emits structured findings;
it cannot modify the candidate, alter the plan, weaken criteria, or issue commit authority.
**Committer** is a small deterministic service: verify digest → verify required checks → verify current
authorization → verify target state unchanged → commit the exact authorized effect → record state. No
planning, no semantic interpretation.

## 6. Mechanical gates supported by research

- **Typed plan gate:** planner outputs a restricted IR (nodes/roles/depends_on/output_schema); validator
  rejects unknown node types, undeclared tools, cycles, missing verification nodes, excessive fan-out,
  illegal data flows, absent termination. Agentproof statically extracts + verifies workflow graphs from
  LangGraph/CrewAI/AutoGen/ADK (early evidence, small corpus) [7].
- **Runtime rule gate:** AgentSpec DSL (triggers/predicates/enforcement) prevented >90% of unsafe
  code-agent executions at ms overhead — but only enforces the rules that exist [8].
- **Least-privilege tool gate:** Progent — fine-grained policies over tool names/args/context/fallback,
  deterministic restriction (AgentDojo/ASB/AgentPoison); LLM-generated policies remain a risk [9].
- **Control/data-flow separation:** CaMeL — trusted control flow vs untrusted data, capabilities + custom
  interpreter; 67% of AgentDojo with its guarantees (security benefit + utility cost) [10].
- **Trusted monitoring & deferral:** AI-control treats a capable model as untrusted, wraps it with
  monitoring/editing/auditing/deferral [11]; later work found monitoring can be defeated by adaptive
  attacks while *deferring critical actions to a trusted policy* stayed robust [12]. **Monitoring is
  weaker than removing authority over critical actions.**
- **Commit-time authorization:** re-check authorization immediately before a durable effect — one preprint
  found systems completing visible tasks after the authorizing evidence had become invalid; mitigation is
  a fail-closed commit boundary rechecking freshness/causal-dependency/resource-binding/eligibility [13]
  (emerging, not broadly replicated).

## 7. A bounded orchestration state machine

`RECEIVED → PLAN_PROPOSED → [mechanical plan validation] → PLAN_APPROVED → WORK_DISPATCHED →
WORK_COLLECTED → VERIFICATION_RUNNING → {PASS→COMMIT_READY | REPAIRABLE→REPLAN_ALLOWED | HARD_FAIL→
ABORTED}. REPLAN_ALLOWED → {budget→PLAN_PROPOSED | exhausted→ESCALATED}. COMMIT_READY → [commit-time
authorization] → HUMAN_REVIEW → {approve→COMMITTED | reject→ABORTED}.` The LLM cannot set the
authoritative state; it emits `{requested_transition, reason_code, evidence_refs}` and the deterministic
controller decides legality.

## 8. Mechanical efficiency controls

Per-stage budgets (model calls/tokens/tool calls/wall-clock/worker count/parallelism/retries/replans/
artifact+context size) the orchestrator cannot modify. Progress predicates: every iteration must produce
a measurable state change (new artifact digest, closed finding, coverage up, failures down, new evidence,
changed candidate) — `same state + same action + no new evidence = loop detected`. Retry classes by
failure type (temporary→backoff; invalid output→retry-once-with-schema-error; failed verify→replan;
unauthorized→no auto-retry; no-progress→terminate/escalate; policy-uncertainty→fail-closed; changed
target→revalidate). Limit delegation depth (≤2), active workers (≤5), replans (≤2), reviewer loops (≤3).
Prefer content-addressed artifacts over full transcripts. Use the simplest sufficient architecture
(MAST: adding agents can add coordination failures without adding capability [1]; utility-guided
orchestration models the value of another step explicitly [14]).

## 9. Recommended production stack

Human task → task-contract compiler → LLM planner (read-only activity) → typed plan validator →
Temporal/Durable Functions/Step Functions → policy engine → scoped worker identity+capability →
sandboxed worker → independent verifier → commit-time authorization → sole committer → attested result.
Components: a durable workflow controller (owns state/retries/timeouts/recovery/legal transitions — not
the LLM); a policy engine (OPA decision/enforcement separation; Cedar analyzable authz) [15,16]; workload
identity (SPIFFE/SPIRE per-worker crypto identity, not a shared orchestration credential) [17]; evidence
(in-toto signed step metadata) [18]. LangGraph gives graphs/checkpoints/persistence/human-interrupts [19]
but its nodes can run arbitrary functions, so it is a workflow framework, **not by itself a
non-bypassable authority boundary** — run it inside a bounded worker or behind an external controller.

## 10. Public benchmarks

MAST (multi-agent failure taxonomy) [1]; Harness-Bench (harness effects) [2]; Terminal-Bench 2 (89 hard
terminal tasks) [20]; τ-bench/τ²-bench (multi-turn tool use + policy + repeat reliability) [21];
AgentDojo (prompt injection, adaptive attacks) [22]; Agent Security Bench [23]; ControlArena (trusted
monitoring / defer-to-trusted / sabotage) [24].

## 11. Strict critique

Mechanical gates enforce only *encoded* properties (a state machine guarantees verify-before-commit, not
that the verifier checks the right property). Restriction reduces flexibility on novel tasks. Fine-grained
policy can explode / cause hard-to-debug denials / pressure toward wildcard permissions. Moving authority
out of the LLM does not delete the root of trust — it moves it to the workflow definition/policy/identity/
artifact-store/verifier/committer, which must be smaller, testable, auditable. Valid actions can compose
into an invalid sequence (per-call authz necessary but insufficient; need sequence + commit-level policy).
Monitoring can be evaded [12]. Human gates become ceremonial if the reviewer cannot see the exact diff /
unresolved findings / external effects / attested artifact / skipped steps. Benchmark improvement is not
production proof.

## 12. Central recommendation

The practical answer is not to make the orchestrator sufficiently obedient — it is to ensure that
disobedience has limited consequences. The LLM orchestration component should have broad ability to
propose, narrow ability to request, **no** ability to authorize, **no** direct ability to commit, bounded
ability to retry, and **no** ability to change its own constraints. The durable controller owns state,
legal transitions, capability issuance, budgets, retries, termination, verification requirements, commit
eligibility.

> **The orchestrator may decide what it believes should happen next. It must not decide whether it is
> permitted to happen next.**

## References

[1] Cemri et al. "Why Do Multi-Agent LLM Systems Fail?" NeurIPS D&B 2025. arXiv:2503.13657.
[2] Yao et al. "Harness-Bench." arXiv:2605.27922, 2026 (UNVERIFIED).
[3] Yang et al. "SWE-agent: Agent-Computer Interfaces…" NeurIPS 2024. arXiv:2405.15793.
[4] Microsoft. "Durable Task Programming Model / Orchestrator Code Constraints." Microsoft Learn.
[5] Temporal. "Workflows, Determinism, Activities, Versioning." Platform docs.
[6] AWS. "Step Functions: Task States, Retry, Catch, Timeout." AWS docs.
[7] Xavier et al. "Agentproof: Static Verification of Agent Workflow Graphs." arXiv:2603.20356, 2026 (UNVERIFIED).
[8] Wang, Poskitt, Sun. "AgentSpec." arXiv:2503.18666, 2025.
[9] Shi et al. "Progent: Programmable Privilege Control for LLM Agents." arXiv:2504.11703, 2025.
[10] Debenedetti et al. "Defeating Prompt Injections by Design (CaMeL)." arXiv:2503.18813, 2025.
[11] Greenblatt, Shlegeris, Sachan, Roger. "AI Control." ICML 2024. arXiv:2312.06942.
[12] Kutasov et al. "Evaluating Control Protocols for Untrusted AI Agents." arXiv:2511.02997, 2025 (UNVERIFIED).
[13] Santos-Grueiro. "Temporary Authority, Permanent Effects." arXiv:2607.10487, 2026 (UNVERIFIED).
[14] Liu, Zhao, Xu. "Utility-Guided Agent Orchestration." arXiv:2603.19896, 2026 (UNVERIFIED).
[15] Open Policy Agent Project docs. [16] Cutler et al. "Cedar." arXiv:2403.04651, 2024.
[17] CNCF. "SPIFFE/SPIRE." [18] in-toto Project. [19] LangChain. "LangGraph" docs.
[20] Merrill et al. "Terminal-Bench." arXiv:2601.11868, 2026 (UNVERIFIED).
[21] Sierra Research. "τ-bench / τ²-bench." [22] Debenedetti et al. "AgentDojo." NeurIPS D&B 2024. arXiv:2406.13352.
[23] Zhang et al. "Agent Security Bench." ICLR 2025. arXiv:2410.02644.
[24] UK AISI + Redwood Research. "ControlArena."
