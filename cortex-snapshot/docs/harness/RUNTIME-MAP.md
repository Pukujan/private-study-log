# Cortex end-to-end runtime map

This map names the actor, operation, and required receipt for one governed run. A component merely
existing in the repository does not satisfy a step; the receipt must be attributable to the same run.

| Stage | Owner | Cortex/runtime operation | Required observable output |
|---|---|---|---|
| Request | Human | Submit task and constraints | Immutable request digest |
| Environment readiness | Runtime wrapper | Before a run exists, inspect MCP configuration, handshake/tool discovery, model endpoint reachability, and telemetry availability | Driver/model/config snapshot and readiness problems; this preliminary check cannot emit `GOVERNED_ACTIVE` |
| Register | Cortex MCP | `cortex_register` and status/orientation | Server-issued session identity, access mode, and immediate key-free model roster with probe freshness |
| Shape | Strong planner + human | Retrieval/research and success-contract drafting | Source-backed brief, unknowns, human decisions, frozen success-contract hash |
| Contract | Runtime + Cortex | Freeze execution contract | Frozen execution-contract hash, required tools/phases/fallback |
| Start | Cortex state engine | `cortex_run_start` on an explicit assured track | Active task/run ID, assurance mode, initial state and sequence |
| Model route | Cortex + runtime | Inspect catalog, apply stage band/capability/independence/scorecard requirements, then bind the selected route to a real call under the active run | Route ID plus externally observable exact provider/model call; stale/unknown remains `UNRESOLVED` |
| Governance activation | External preflight signer + Cortex verifier + wrapper display | After the assured run and route exist, verify the signed observation against the frozen contract, live tools/model call, joined IDs, evaluator readiness, and telemetry policy | `GOVERNED_ACTIVE` only for a valid unexpired signed observation; otherwise the contract's frozen fallback result |
| Research/spec | Runtime models | Legal state steps plus local tools | State receipts and hashed research/spec artifacts before mutation |
| Mission plan | Cortex mission engine | Contract, partition, claims, dispatch | Mission ID, disjoint claims, worker IDs and sequence receipts |
| Execute | Runtime wrapper | Filesystem/shell/browser/API operations; Docker execution routes to gravebuster | Native/remote tool events, model identity, artifact hashes, container/test receipts, and bounded outputs joined to the run |
| Fan-in | Cortex + runtime | Worker reconciliation and merge | Complete cohort, per-worker receipts, merge receipt tied to artifact hash |
| Evaluate | External evaluator | Hidden task-specific checks and direct artifact inspection | Oracle version/fixture hash, browser/test traces, pass/fail/abstain |
| Correlate | OTel/Langfuse/evidence store | Join MCP, native tools, model calls and evaluator events | Same run ID across available evidence planes; missing-policy verdict |
| Closeout | Cortex | Evidence-index closeout and KEDB update | Claims linked to mechanical evidence; failures recorded as incidents |
| Replay | Different driver/clean environment | Repeat required scenarios | Independent result and documented variance |
| Accept | Human | Review ambiguous/product-quality evidence | Accepted, rejected, or unresolved decision |

## Trust boundaries

- Runtime agents and models are untrusted planners/builders/reviewers.
- Cortex state receipts prove only the procedure they record.
- Cortex gates prove only their declared oracle scope.
- OTel and Langfuse make events inspectable; they do not establish correctness.
- External reproduction establishes observable behavior.
- The human owner establishes ambiguous intent and product-quality acceptance.

## Failure behavior

| Failure | Required result |
|---|---|
| MCP server cannot start or required tools are absent | Apply the frozen fallback: `BLOCKED`, `UNGOVERNED_RUN`, or `ADVISORY_RUN` as configured; never claim governance |
| Signed governance activation is missing, expired, or wrongly bound | Apply the frozen fallback. Missing evidence may remain `UNRESOLVED`; it never becomes `GOVERNED_ACTIVE` |
| Provider lists a model but a real completion fails | Lane unavailable; select a permitted verified fallback or stop |
| Required state phase is skipped/stale/malformed | Procedure `FAIL`; task result remains separately evaluated |
| Required telemetry is missing | Contract-defined `FAIL`, `UNRESOLVED`, or `ENVIRONMENT_UNAVAILABLE`; never pass |
| Generic checks pass but task behavior fails | Behavior `FAIL` |
| Builder-authored claim lacks mechanical evidence | Evidence `UNRESOLVED` or `FAIL` |
| Delegate output is not in the final artifact | Mission/behavior `FAIL` |
| Human decision is required but absent | Human acceptance `UNRESOLVED` |
