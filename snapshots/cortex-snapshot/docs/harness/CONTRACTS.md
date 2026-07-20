# Execution and success contracts

The active implementation-neutral assurance baseline for new scenarios is
`evals/cross_driver_assurance/contracts/production-behavior-v2/`. Its freeze receipt prevents a
scenario from redefining success after observing a driver or artifact. The task-level contracts below
instantiate that baseline; they may strengthen it but may not weaken it.

Cortex freezes two contracts before implementation:

- The **execution contract** says how the runtime must operate: driver/model identity, profile, mode,
  phases, tools, research/mission policies, fallback, evidence, and telemetry requirements.
- The **success contract** says what must be true of the result: user outcomes, observable behavior,
  domain invariants, acceptance criteria, prohibited behavior, oracle authority, evaluator
  independence, repeatability, and human-review boundaries.

Portable JSON Schema files:

- `schemas/assurance/execution-contract.schema.json`
- `schemas/assurance/success-contract.schema.json`
- `schemas/assurance/external-evaluation.schema.json`
- `schemas/assurance/evaluator-trust-root.schema.json`
- `schemas/assurance/assurance-receipt.schema.json`

The stdlib implementation is `cortex_core/assurance_contracts.py`. `freeze_contract()` validates a
contract and computes a canonical SHA-256 digest. Run/evidence records must carry both digests so an
agent cannot redefine either procedure or success after seeing its output.

Model identity is also multi-axis. The key-free catalog exposed by `cortex_status` joins the logical
provider lane, exact served model from the last probe, benchmarked dispatch capability, workflow
capacity band, freshness/free status, and qualification blockers. These are observations, not a
verdict or dispatch authorization. The execution contract selects a workflow-stage band; the router
then requires fresh liveness, an exact capability card, task scorecard where required, independence,
and a route receipt. The runtime must bind the actual provider/model call to that route. A static tier
name, provider `/models` listing, or stale success cannot satisfy this contract by itself.

There are two different checks that must not share one ambiguous "preflight" label:

1. **Environment readiness** happens before an active run exists. The wrapper inspects configuration,
   MCP handshake/tool discovery, endpoint reachability, and telemetry availability. It can expose
   problems but cannot emit `GOVERNED_ACTIVE` because no assured run/route binding exists yet.
2. **Signed governance activation** happens after the execution contract, explicit assured track,
   server run ID, and model route exist. `cortex_core/driver_preflight.py` applies the execution
   contract to an observation supplied and signed by an external harness or MCP Inspector. It does
   not create evidence itself.

A wrapper may display `GOVERNED_ACTIVE` only when signed governance activation binds the frozen
execution contract and confirms the live connection, required tools, real completion probe, an
`ASSURED` track, server run ID, capability-route-to-call binding, evaluator readiness, telemetry
policy, and external evidence references. Unsigned, expired, unavailable, or wrongly bound
observations can never activate governance. Their visible result follows the execution contract's
frozen fallback: `BLOCKED` for fail-closed, `UNGOVERNED_RUN` for mark-ungoverned, or `ADVISORY_RUN`
when advisory continuation was explicitly allowed. An evidence question may remain `UNRESOLVED`,
but that uncertainty does not silently select a more permissive mode.

The verifier and Hades hook are built locally; this is not a live-deployment claim. The inspected
gateway still needs restart/replay and the external preflight signer is not deployed.

## Verdict separation

Contracts never contain verdicts. The evaluator reports procedure and behavior independently. A
legal state walk with an incorrect product is procedure `PASS`, behavior `FAIL`. A correct-looking
artifact without required independent evidence is behavior `UNRESOLVED` rather than `PASS`.

`cortex_core/assurance_result.py` defines the final result shape. It requires procedure, behavior,
evidence, independence, repeatability, and human-acceptance verdicts separately. The overall verdict
is non-averaging: any failed hard axis fails the result, and uncertainty remains uncertainty.

`cortex_core/assurance_evaluator.py` is the **external assurance-result** ingest boundary. It verifies an
Ed25519 signature against the operator's public trust root, binds the evaluator identity, builder
run, execution/success contract digests, evidence-manifest digest, and external replay identity, then
writes an append-only `ar_...` receipt. The builder MCP exposes no mint action. Every receipt read
rechecks stored digests, expiry, key validity/revocation, and the external signature. The receipt DB
path is required through operator configuration (`CORTEX_ASSURANCE_DB_PATH`) and is no longer derived
from the builder workspace. Private keys, signer services, trust-root/database ACLs, rotation, and
separate service identities remain deployment responsibilities outside the builder process. Its
key activation/revocation checks are distinct from the research-sufficiency trust store and do not
imply that research-policy/source/attestation revocation operations are deployed.

## Human boundary

Deterministic oracles may decide exact behaviors within a frozen scope. External observers may replay
user journeys and environment behavior. Models may annotate or red-team. Ambiguous intent, product
quality, and oracle promotion remain human-controlled and must be recorded explicitly.
