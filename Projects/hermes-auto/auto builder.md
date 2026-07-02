# Hermes Evidence-Based Evolution System

## Source-backed handoff for failure repair, experimentation, upgrade, and subtraction

## 0. Core thesis

Hermes should not only react to failures. It should run two evidence-driven loops:

```text id="zztg9q"
1. Stability loop
   Detect repeated failures, regressions, flaky behavior, slow recovery, and stale dependencies.

2. Exploration loop
   Continuously research better tools, scrape official docs/release notes/papers, prototype candidates, and test whether they move the system forward.
```

The goal is not random self-modification. The goal is **evidence-gated evolution**:

```text id="57em3i"
observe → learn → research → propose → test → compare → promote → archive → repeat
```

This maps closely to the **MAPE-K** self-adaptive systems model: Monitor, Analyze, Plan, Execute over shared Knowledge. MAPE-K is a standard control-loop model in autonomic/self-adaptive systems research.

---

# 1. What already exists in production

No single mature platform does the full Hermes vision end-to-end. But the pieces exist.

## Observability / black-box recorder

Use **OpenTelemetry** for structured traces, metrics, and logs. OpenTelemetry is a vendor-neutral observability framework for generating, collecting, and exporting telemetry data such as traces, metrics, and logs.

For LLM/agent observability, use OpenTelemetry-compatible tools such as:

```text id="02q9kt"
Langfuse
Phoenix / Arize
OpenInference
OpenLIT-style OTEL integrations
```

Langfuse provides LLM observability with traces, latency, cost, scoring, and OpenTelemetry-native integration. Phoenix/OpenInference standardizes LLM calls, tool invocations, retrieval operations, and agent steps as OpenTelemetry-compatible traces.

## Testing / gauntlet

Use normal software test tools:

```text id="aa70vq"
pytest      = Python/unit/integration/functional tests
Playwright  = browser/UI/end-to-end tests
k6          = API/load/performance tests
Toxiproxy   = network failure simulation
```

pytest scales from small tests to complex functional testing; Playwright supports browser/end-to-end automation; k6 supports load/performance testing; Toxiproxy simulates network conditions for testing, CI, and development environments.

For LLM and agent evals:

```text id="e4nxqu"
Promptfoo
DeepEval
SWE-bench-style fixtures
```

Promptfoo supports deterministic, model-graded, JSON, similarity, and custom assertions. DeepEval provides LLM/agent evaluation metrics, including tool correctness and argument correctness. SWE-agent research evaluates coding agents by making them edit repos and run tests.

## Coding agents / implementation agents

Existing references:

```text id="5fzo3g"
OpenHands Software Agent SDK
SWE-agent
LangGraph
Microsoft Agent Framework
```

OpenHands provides Python and REST APIs for building agents that work with code, including local or sandboxed execution. SWE-agent shows that specially designed agent-computer interfaces improve software-engineering agent performance. LangGraph supports durable execution, persistence, streaming, and human-in-the-loop agent workflows. Microsoft Agent Framework supports graph-based workflows, checkpointing, type-safe routing, and human-in-the-loop support.

## Dependency upgrade bots

Existing tools:

```text id="j3w5v0"
Renovate
Dependabot
```

Renovate automatically opens PRs for dependency and lockfile updates and can be configured to reduce noise. Dependabot creates automated PRs for version and security updates.

But dependency automation cannot be trusted blindly. Research on Dependabot found mixed results: projects reduced technical lag, but compatibility scores were scarce, developers configured bots to reduce notification fatigue, and some projects deprecated Dependabot in favor of alternatives.

Another study found that tests covered only 58% of direct dependency calls and 20% of transitive dependency calls in the analyzed Java projects, and detected only 47% of direct and 35% of indirect artificial dependency faults on average. That means Hermes should combine tests with impact analysis, runtime traces, and staged rollout rather than “tests passed, auto-merge everything.”

## Automated refactoring / subtractor tools

Existing tools:

```text id="2otgsk"
OpenRewrite
Moderne
```

OpenRewrite is an open-source automated refactoring ecosystem for source code, including recipes for framework migrations, security fixes, and consistency tasks. Moderne builds around OpenRewrite for large-scale automated code remediation and migration.

LLM-based refactoring research also supports caution: one empirical study found that LLMs can recommend useful refactorings but may also produce unsafe changes that alter behavior or introduce syntax errors, so LLM suggestions should be re-applied through tested refactoring engines or strong test gates.

## Progressive rollout / old-vs-new competition

Existing production patterns:

```text id="9txktu"
canary release
blue-green release
shadow mode
A/B testing
multi-armed bandits
automated rollback
feature flags
```

Argo Rollouts supports canary, blue-green, canary analysis, experimentation, and progressive delivery on Kubernetes. Flagger automates progressive delivery and can roll back when metrics fail. LaunchDarkly supports feature flags, progressive rollouts, automated rollback, experimentation, A/B/n testing, and multi-armed bandits.

This is the production version of:

```text id="zknb59"
old implementation = baseline
new implementation = candidate
run both
measure both
promote only if candidate wins
rollback if candidate regresses
```

## Research/documentation scraping

Existing tools for research ingestion:

```text id="g74a3i"
Firecrawl
Tavily
Jina Reader
Diffbot
```

Firecrawl provides search, crawl, scrape, extraction, and indexing for AI-ready web data. Tavily provides search, extract, crawl, map, and research APIs for AI agents. Jina Reader converts web pages to LLM-friendly Markdown. Diffbot provides AI web data extraction and knowledge graph features.

Hermes should use these to create **candidate hypotheses**, not automatic upgrades.

---

# 2. What research already backs this direction

## MAPE-K / autonomic systems

MAPE-K is the correct theoretical frame:

```text id="ddmw3d"
Monitor   = traces, logs, metrics, test results
Analyze   = failure/success pattern detection
Plan      = propose repair or experiment
Execute   = patch, rollout, rollback, archive
Knowledge = database, graph, memory, postmortems
```

MAPE-K is described in self-adaptive systems literature as Monitor-Analyze-Plan-Execute over shared Knowledge.

## SRE / DORA

Google SRE postmortem guidance backs the “learn from failure without blame” model: postmortems should identify contributing causes and prevent recurrence. DORA metrics provide delivery and stability metrics such as change failure rate, deployment frequency, lead time, and recovery-oriented measures.

Hermes should track both negative and positive signals:

```text id="feplnj"
Negative:
- repeated failures
- regressions
- rollbacks
- flaky tests
- high retry count
- slow recovery

Positive:
- stable pass history
- reduced latency
- improved retrieval accuracy
- lower retry rate
- successful canary
- successful rollback test
- lower maintenance burden
```

## Agent harness evolution research

The closest research to Hermes-style self-improvement is **Agentic Harness Engineering**, which describes an observability-driven closed loop for evolving coding-agent harnesses. It emphasizes component observability, trajectory/experience observability, and decision observability, where edits become falsifiable predictions checked against later outcomes.

**HarnessFix** is also directly relevant: it proposes trace-guided diagnosis and repair of agent harness flaws by converting raw execution traces into a representation that can attribute failures to responsible steps and harness layers, then validate scoped repairs.

The **LLM Readiness Harness** paper is relevant to production gating: it combines automated benchmarks, OpenTelemetry observability, and CI quality gates into deployment readiness decisions for LLM/RAG systems.

These are research systems, not turnkey production platforms. But they support the direction: traces, evidence, scoped edits, test gates, and measurable outcomes.

---

# 3. Required Hermes architecture

## A. Observation layer

Must capture:

```text id="m93tuq"
OpenTelemetry traces
agent run spans
tool call spans
LLM call spans
subagent spans
queue job spans
error/retry spans
stdout/stderr
raw tool inputs/outputs
file reads/writes
git diffs
test results
eval results
```

OpenTelemetry gives the structured trace backbone; raw logs preserve evidence that traces summarize but do not fully contain.

## B. Strong database layer

A strong database is mandatory. Without it, Hermes cannot learn relationships between failures, passes, causes, candidates, rollbacks, false positives, and component health.

Start local:

```text id="rl1li6"
SQLite WAL
SQLite FTS5
structured relationship tables
optional sqlite-vec
```

SQLite FTS5 provides full-text search over indexed content.

Scale later if needed:

```text id="romfc1"
Postgres + pgvector
TimescaleDB
Neo4j
ClickHouse
```

pgvector adds vector similarity search to Postgres; TimescaleDB is a Postgres extension for time-series/event analytics; Neo4j models data as nodes and relationships for graph analysis.

Core tables:

```text id="f4ntoz"
runs
spans
tool_calls
model_calls
files_touched
git_diffs
test_results
eval_results
failures
failure_signatures
success_signatures
components
capabilities
dependencies
candidate_replacements
research_sources
experiments
a_b_trials
canary_trials
mutation_decisions
rollbacks
false_positives
postmortems
closeouts
```

## C. Capability registry

Hermes needs to reason in terms of capabilities, not random scripts.

Example:

```yaml id="g1xt3o"
capability: search
current_impl: fts5_hybrid_v1
candidate_impl: sqlite_vec_rrf_v2
status: shadow_testing
success_metrics:
  - recall_at_k
  - latency_p95
  - stale_result_rate
  - error_rate
rollback:
  command: restore_search_v1
risk: medium
owner_agent: hermes-upgrade-governor
```

This is custom, but the pattern is backed by production rollout tools that compare baseline and candidate variants before promotion.

## D. Research and documentation scraper

Hermes needs a dedicated exploration pipeline:

```text id="04ezqr"
official docs
release notes
GitHub repos
papers
changelogs
benchmarks
migration guides
security advisories
```

Scrapers should create structured candidate records:

```yaml id="vj0v95"
candidate: OpenTelemetry agent tracing
source_type: official_docs
claim: standard traces/metrics/logs across components
source_quality: high
expected_gain: automatic run reconstruction
risk: low-medium
test_plan:
  - one trace per agent run
  - child spans for tool calls
  - verify raw log linkage
decision: prototype
```

Use Firecrawl/Tavily/Jina/Diffbot-style tools for search, extraction, crawl, and Markdown conversion.

## E. Gauntlet

Hermes needs one command that can prove a candidate is better or safer.

```text id="acg2ze"
hermes doctor
hermes gauntlet
hermes eval
hermes load
hermes chaos
hermes trace-check
```

Under the hood:

```text id="fu8goh"
pytest
Playwright
k6
Toxiproxy
Promptfoo
DeepEval
custom Hermes scenario tests
```

This combines standard test tools, load testing, failure injection, and LLM/agent evals.

---

# 4. The two loops Hermes needs

## Loop 1: Stability loop

Purpose:

```text id="osc5gz"
keep Hermes alive
reduce repeated failures
protect the baseline
lower recovery time
avoid silent regressions
```

Flow:

```text id="ix9uah"
OpenTelemetry + logs + tests
        ↓
failure signatures
        ↓
recurrence/severity scoring
        ↓
repair proposal
        ↓
branch/prototype
        ↓
gauntlet
        ↓
shadow/canary
        ↓
promote or rollback
        ↓
postmortem + memory update
```

This loop is backed by SRE postmortems, DORA metrics, OpenTelemetry, and progressive delivery.

## Loop 2: Exploration loop

Purpose:

```text id="cvk335"
avoid stagnation
discover better tools
test promising architectures
move the system forward
replace old parts when evidence wins
```

Flow:

```text id="1w2tox"
research/docs scraper
        ↓
candidate registry
        ↓
hypothesis
        ↓
sandbox prototype
        ↓
gauntlet/evals
        ↓
shadow mode
        ↓
A/B or canary
        ↓
promote / reject / archive
```

This loop uses production experimentation patterns such as feature flags, A/B testing, canary rollout, progressive delivery, and multi-armed bandits.

Critical rule:

```text id="jjwvia"
Research creates hypotheses.
Tests create belief.
Shadow/canary evidence creates promotion.
```

---

# 5. Scoring model

Do not call it “emotion” in implementation. Use evidence-backed scores.

## Repair priority

```text id="icv6vf"
repair_priority =
  repeated_failure_count
+ severity
+ blast_radius
+ recovery_cost
+ user_pain
+ agent_retry_count
- rollback_risk
- diagnosis_uncertainty
```

Inputs should come from traces, logs, test results, postmortems, and DORA/SRE-style metrics.

## Exploration priority

```text id="hde9za"
exploration_priority =
  expected_gain
+ source_quality
+ testability
+ reversibility
+ strategic_fit
- integration_complexity
- migration_risk
- maintenance_cost
```

This protects Hermes from chasing every shiny new tool.

## Promotion rule

A candidate should not become default unless:

```text id="c55ip9"
- tests pass
- evals pass
- rollback exists
- blast radius is understood
- old and new were compared
- candidate beats baseline on defined metrics
- no critical regressions appear in shadow/canary
```

This follows the same principle as canary/progressive delivery: release to a controlled subset, measure, and roll back if metrics fail.

---

# 6. Subtractor layer

Hermes should not only add. It should retire.

Existing tools that support this idea:

```text id="rirx40"
Renovate / Dependabot = dependency upgrade proposals
OpenRewrite / Moderne = automated refactor/migration
Argo / Flagger / LaunchDarkly = safe rollout and rollback
```

The subtractor lifecycle:

```text id="8j5td1"
active
→ candidate_found
→ shadow_testing
→ canary
→ promoted
→ old_fallback
→ old_deprecated
→ old_archived
→ old_removed
```

OpenRewrite/Moderne are useful when old code can be replaced through deterministic recipes. Renovate/Dependabot are useful when old dependencies can be upgraded through PRs. Progressive delivery tools are useful when old and new behavior must be compared gradually.

Do not delete immediately:

```text id="gzdh96"
archive first
fallback second
remove only after stability window
```

---

# 7. Human approval and governance

Hermes can auto-research, auto-prototype, auto-test, and auto-summarize. It should not automatically promote high-risk core mutations without approval.

Require human approval for:

```text id="jjhvy2"
database migrations
security/auth changes
memory deletion
core dispatcher/routing changes
dependency removals
production default changes
anything without rollback
```

LangGraph documents human-in-the-loop middleware that can pause risky tool calls for review, and Microsoft Agent Framework supports human-in-the-loop workflow interactions.

---

# 8. Concrete implementation plan

## Phase 1 — Instrument everything

Build first:

```text id="6zba1s"
OpenTelemetry spans for:
- agent runs
- tool calls
- LLM calls
- subagent dispatch
- queue jobs
- errors/retries
```

Also capture:

```text id="70a03l"
raw stdout/stderr
tool inputs/outputs
file touches
git diffs
test outputs
```

Success criterion:

```text id="k67c8z"
Given one Hermes task, we can reconstruct what ran, when it ran, what failed, which files changed, and where the raw evidence lives.
```

## Phase 2 — Build the audit database

Start with SQLite:

```text id="ypj5xq"
runs
spans
files_touched
test_results
failures
components
capabilities
candidate_replacements
mutation_decisions
```

Add FTS5 for search over logs, closeouts, and summaries. SQLite FTS5 is explicitly built for efficient full-text search across document collections.

## Phase 3 — Create Hermes Gauntlet

Start with deterministic tests:

```text id="yneyfo"
pytest for scripts/modules
Playwright for browser/extension/UI
k6 for endpoints/load
Toxiproxy for network/service failure
```

Then add LLM/agent tests:

```text id="y2zvqs"
Promptfoo for prompt/output assertions
DeepEval for agent/tool correctness
Hermes scenario fixtures for full workflow tests
```

## Phase 4 — Add research ingestion

Use Firecrawl/Tavily/Jina Reader-style tools to ingest official docs, release notes, GitHub repos, and papers into candidate records. These tools already support search, extraction, crawling, and Markdown conversion for AI workflows.

Every research item becomes a hypothesis:

```yaml id="j8lfy5"
hypothesis: "sqlite-vec may improve local semantic retrieval."
evidence:
  - official docs
  - repo maturity
  - known risks
test_plan:
  - latency benchmark
  - recall benchmark
  - rebuild reliability
  - fallback to FTS5
```

## Phase 5 — Add builder/subtractor agents

Builder agent:

```text id="ar5y7z"
creates branch
integrates candidate behind flag
writes tests
runs gauntlet
records diff
```

Subtractor agent:

```text id="7eg7rw"
marks old path fallback-only
updates docs
archives old scripts/configs
opens removal PR after stability window
```

Use OpenHands/SWE-agent-style tools as references for code-editing agents with execution environments and tests.

## Phase 6 — Add shadow/canary promotion

For each capability:

```text id="074ie3"
run old and new side-by-side
record metrics
compare outputs
route small share to candidate
promote only if candidate wins
rollback on regression
```

This follows documented canary/progressive delivery patterns from Argo Rollouts and Flagger, and experimentation/feature-flag patterns from LaunchDarkly.

## Phase 7 — Add durable orchestration

Once the workflow is clear, use Temporal or a similar workflow engine.

Temporal persists workflow state and provides retries, task queues, signals, and timers.

Use it for:

```text id="9tkkcj"
research → candidate → build → gauntlet → shadow → canary → promote/archive
```

---

# 9. What Hermes can do better than existing tools

Existing tools are strong but fragmented.

```text id="qkvne5"
Renovate/Dependabot upgrade dependencies, but do not understand full Hermes behavior.
OpenRewrite migrates code, but does not decide whether the migration improves agent success.
Argo/Flagger roll out versions, but do not understand LLM semantic regressions.
Promptfoo/DeepEval evaluate outputs, but do not own the whole mutation lifecycle.
Langfuse/Phoenix trace agents, but do not automatically prune old systems.
OpenHands/SWE-agent can patch code, but do not provide the full evidence-governed upgrade economy.
```

Hermes can improve on this by joining them through one database and one evidence policy:

```text id="vajfp2"
traces
+ raw evidence
+ tests
+ evals
+ research candidates
+ old-vs-new comparison
+ rollback records
+ postmortems
+ capability registry
```

The novel part is not inventing a brand-new tool. The novel part is integrating existing production practices into one local evolution loop.

---

# 10. Final mutation lifecycle

```text id="v8w4fm"
1. Monitor
   OTel traces, raw logs, tests, evals, and runtime metrics are captured.

2. Learn baseline
   Hermes records what works, what fails, what is stable, and what is flaky.

3. Research
   Docs/paper/release-note scrapers create candidate hypotheses.

4. Score
   Repair priority and exploration priority are calculated from evidence.

5. Propose
   Candidate is registered against a capability.

6. Prototype
   Builder integrates candidate behind a feature flag or branch.

7. Test
   Gauntlet runs unit, integration, E2E, load, chaos, and agent eval tests.

8. Shadow
   Old and new run side-by-side without risking default behavior.

9. Compare
   Database compares pass rate, error rate, latency, quality, cost, retries, rollback safety, and maintenance burden.

10. Canary / A-B
   Small controlled traffic or task share goes to candidate.

11. Promote
   Candidate becomes default only if evidence beats baseline.

12. Fallback
   Old path remains available during rollback window.

13. Archive
   Subtractor archives old path after stability window.

14. Remove
   Old dependency/scripts/configs/docs are removed only after checks pass.

15. Remember
   Database, postmortems, docs, closeouts, and long-term memory are updated.

16. Repeat
   The promoted candidate becomes the new baseline.
```

---

# 11. Final-stage mutation example

```text id="nmj3d5"
Hermes observes:
  Search subsystem v1 has recurring stale-result failures,
  high retry count, and poor recall on gauntlet fixtures.

Hermes researches:
  Official docs and papers suggest a candidate hybrid retrieval path.

Hermes prototypes:
  Candidate v2 is implemented behind a feature flag.

Hermes tests:
  v2 passes pytest, workflow scenarios, retrieval evals, and trace-checks.

Hermes shadows:
  v1 and v2 run on the same queries; outputs and metrics are logged.

Hermes compares:
  v2 improves recall and lowers stale-result rate without unacceptable latency or regression.

Hermes canaries:
  A small controlled task share routes to v2.

Hermes promotes:
  v2 becomes default.

Hermes subtracts:
  v1 becomes fallback, then deprecated, then archived, then removed after stability window.

Hermes learns:
  The decision record, metrics, rollback plan, postmortem, and capability registry are updated.
```

---

# 12. Non-negotiable safety rules

```text id="rrd3ze"
No raw self-modification without traces.
No promotion without tests.
No deletion without archive.
No high-risk change without rollback.
No core-system mutation without human approval.
No research claim becomes truth until tested.
No LLM-generated refactor is trusted without deterministic verification.
```

These rules are supported by the known limitations of dependency-update automation, LLM refactoring risk, and production progressive-delivery practice.

---

# 13. Short version

Hermes should evolve like this:

```text id="ye0t3s"
OpenTelemetry = senses
raw logs = evidence
database = memory
SRE/DORA metrics = health scoring
research scraper = scout
gauntlet = survival environment
builder = mutation
subtractor = cleanup metabolism
canary/A-B = controlled selection
human approval = immune checkpoint
mem0 = distilled lessons
```

Final principle:

```text id="u9uryk"
Always observe.
Always research.
Always test.
Promote only with evidence.
Archive what loses.
Keep rollback.
Keep learning.
```
