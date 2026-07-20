# Cortex expected behavior

**Audience:** people asking Cortex-driven agents to do real work.  
**Status:** normative target plus an explicit current-capability warning.  
**Rule:** procedure, result correctness, evidence, independence, and repeatability are separate findings.
**Active frozen baseline:** `evals/cross_driver_assurance/contracts/production-behavior-v2/`.

## The promise

Cortex is a model-agnostic research, execution, and assurance engine. A runtime such as Claude,
Codex, or Hermes supplies models and local tools. Cortex supplies grounded context, contracts,
workflow state, mission coordination, oracle policy, provenance, and learning records.

Cortex is successful only when the requested real-world task is completed correctly and the user can
inspect evidence supporting that conclusion. Following a state machine is not task success. Passing a
visible generic fixture is not product success. A model's claim that it tested its work is not proof.

## What a user must see before material work or delegation starts

For a non-trivial task, the driver must show:

1. the selected workflow profile and whether Cortex is actually connected;
2. the key-free model roster, each model's evidence-backed capability tier, last-probed
   availability/freshness, free/cost status, and explicit reasons it is not routable;
3. the active Cortex run ID;
4. important assumptions and questions requiring human decisions;
5. required phases, tools, research, mission behavior, telemetry, and fallback policy;
6. the user outcomes and observable acceptance criteria that define success;
7. which evaluation details are hidden from the builder; and
8. what evidence and human review will be required before completion.

`cortex_status` exposes the compact roster immediately. The full joined view is available through
`cortex_dispatch_tier(action="catalog")`. Reading either view makes no provider call and reveals no
credentials. `LIVE` means only that a recent probe passed; stale, unprobed, unqualified, or missing
task-scorecard evidence remains visibly blocked and is never promoted by a model-name guess.
An adopted provider-discovery roster may also be attached: status shows its counts/freshness and the
full catalog shows every entry, but its user/provider-declared tiers never replace evidence-backed
classification or exact route qualification.

The resting MCP surface must stay small. New capabilities go behind an existing action dispatcher,
phase-state disclosure, a resource, or a lazily loaded skill instead of becoming an always-visible
tool. Hades forces Hermes tool search on so non-core MCP/plugin schemas are discovered only when
needed. Docker execution is routed to gravebuster over SSH; local Windows Docker is limited to
bounded availability diagnostics unless the owner explicitly requests local use or repair.

Before an active run exists, an environment-readiness check shows connection/tool/model/telemetry
problems but cannot activate governance. After an assured run and route exist, a separately signed
governance-activation preflight must bind the contract, run, route, evaluator readiness, and evidence.

If required Cortex capabilities are unavailable, the visible mode follows the fallback frozen in the
execution contract: `BLOCKED` for fail-closed, `UNGOVERNED_RUN` for mark-ungoverned, or
`ADVISORY_RUN` when advisory continuation was explicitly allowed. The underlying evidence question
may remain `UNRESOLVED`. No fallback may later be described as governed.

## Expected task behavior

### Vague requests

A vague request is research input, not an implementation specification. Before implementation, the
driver produces an approved brief or clearly labels its defaults. Depending on the task this includes
actors, jobs, domain terms, constraints, unknowns, domain/data models, state transitions, permissions,
user journeys, design direction, acceptance criteria, and an evaluator-only oracle plan.

### Existing software

For an existing repository or application, Cortex reconstructs public claims and expected behavior,
runs the real artifact, maps roles/workflows/data states, checks relevant boundaries, records gaps, and
proposes adoption of established tools before custom implementation. Logs and model summaries cannot
replace direct inspection.

### Research, external discovery, and adoption

Every non-trivial task starts with canonical Brain recall and relevant local project/KEDB history.
Escalation to the internet is caused by recorded coverage, corroboration, freshness, version, risk, or
source-diversity gaps--never by model confidence. A web-capable driver discovers candidates, Cortex
registers and fetches accepted sources into a reproducible local corpus, and the research report names
unanswered questions and failed providers honestly. Tool failure is `ENVIRONMENT_UNAVAILABLE`, not a
license to invent a research result.

Source requirements are frozen per claim and risk rather than as one universal link count. Material
claims prefer primary authority; load-bearing claims require independent corroboration; adoption
decisions include official docs, license, maintenance/release, security, integration/exit cost, and a
production/user signal where available. UX/branding work also inspects comparable products and leaves
subjective product identity acceptance to the owner. The full routing and current-capability truth is
in [KNOWLEDGE-ESCALATION.md](KNOWLEDGE-ESCALATION.md).

Cortex never declares research universally complete. It may only issue `SUFFICIENT_FOR_DECISION` for
a named decision, risk tier, evidence-policy version, remaining uncertainty, reviewer, and time. The
mechanical Cortex gate may reject weak evidence but does not supply domain judgment; an independent
domain evaluator reviews substantive adequacy, and a qualified human must approve high-consequence
legal, medical, financial, security, or safety assumptions. The researcher/driver cannot approve its
own sufficiency.

### Implementation and repair

The driver works only inside the frozen execution contract. Parallel work uses declared claims and a
verified merge. Tests and screenshots must bind to the final artifact hash. A stale artifact, unmerged
delegate output, or unrelated completed task cannot unlock completion.

### Verification

The builder never certifies itself. Deterministic checks establish narrow facts; an external observer
replays user journeys and boundary cases; models may provide adversarial review but not hard verdicts;
the human owner decides ambiguous intent and product-quality boundaries.

## Completion report

Every run reports these axes separately:

| Axis | Question |
|---|---|
| Procedure | Did the observed event sequence satisfy the frozen execution contract? |
| Behavior | Did the final artifact satisfy the frozen success contract? |
| Evidence | Do claims resolve to runtime receipts, hashes, traces, and reproduced checks? |
| Independence | Was success decided outside the builder and its visible fixtures? |
| Repeatability | Did another driver or clean replay reproduce the result where required? |
| Human acceptance | Did the owner accept the ambiguous/product-quality portions? |

Allowed verdicts are `PASS`, `FAIL`, `ABSTAIN`, `UNRESOLVED`, and
`ENVIRONMENT_UNAVAILABLE`. Missing required evidence never becomes `PASS`.

## Current warning

The target above is not uniformly enforced today. The `assured_build` and `assured_research` charts now
fail closed on a server-stored, decision-specific research receipt, and composite recall is available
through the main search/scope-pack tools. The older `build` and `research` charts remain compatibility
routes and are not equivalent to assured execution. Capability-qualified route planning, joined run
identity, signed research authority, signed governance-activation verification, and signed
external-result receipt verification now exist in the local core. This says **built locally**, not
deployed or proved on the live route. A key-free joined model catalog is now present in status, and the Hades
assured-driver hook is built and enabled in the profile, but the running gateway has not yet been
restarted and replayed. Route-to-model-call enforcement, production evaluator/human signer services,
automatic external discovery, universal live evaluator runner, and the cross-driver harness remain incomplete. The Hermes SalesOps incident proved that a wrapper can run
without Cortex and still present a plausible completion. Until those integrations are shipped, users
must require the explicit assured track, a run ID, an externally signed governance activation, and independently
signed receipts before accepting the label "Cortex-governed."
