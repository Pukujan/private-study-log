# Production reference model for Cortex assurance

**Status:** externally grounded design baseline, written before the next driver test.  
**Decision:** Cortex should compose proven production patterns; it should not invent a private
definition of agent quality and then grade itself against that definition.

## What Cortex is trying to resemble

Cortex is not one product category. Its intended behavior is the intersection of a durable workflow
engine, an evidence-aware research system, an agent evaluation harness, an observability plane, a
software-provenance system, and a human-centered product delivery process.

| Production reference | Behavior to adopt | Cortex use | Do not copy |
|---|---|---|---|
| [Temporal durable execution](https://docs.temporal.io/) | Server-owned event history, deterministic transitions, resumability, bounded retries, idempotent side effects | Treat state-engine receipts as durable workflow history; prove interruption/resume and duplicate-call safety | A new Temporal dependency before the existing engine fails the same behavioral tests |
| [UK AISI Inspect](https://inspect.aisi.org.uk/) | Separate datasets/tasks, solvers/drivers, scorers, tools, sandboxes, and durable logs; permit rescoring without rerunning the builder | Adopt Inspect as the preferred external evaluator/runner adapter; Cortex supplies evidence, never its own final grade | A Cortex-only scorer or builder-visible fixture |
| [METR task standard](https://evaluations.metr.org/) | Reproducible environments, realistic long-horizon tasks, comparable human/agent conditions | Package vague real tasks with a clean environment, artifacts, time/tool budget, and observer-owned scoring | Toy prompts that only prove tool syntax |
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) | Real repositories/issues and executable outcome checks; keep test answers private where needed | Use real applications and user journeys, plus regression and holdout checks bound to a repository state | Treating a benchmark pass as universal software quality |
| [Anthropic agent eval guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Define capabilities with evals before agents fulfill them; retain full transcripts; score “did not break,” “did what was asked,” and “did it well” separately | Freeze behavior before execution and grade procedure, correctness, and product quality independently | Letting the same model create, see, and certify its rubric |
| [Codex best practices](https://learn.chatgpt.com/guides/best-practices.md) | Supply goal, context, constraints, and “done when”; plan difficult work; encode repository guidance; implement, test, inspect, and review | Let Cortex serve a concise task packet and enforce completion evidence while leaving valid implementation choices open | A giant prompt or transcript as the only durable project memory |
| [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents.md) | Delegate bounded independent/read-heavy work; keep requirements and decisions in the main thread; wait and consolidate; avoid conflicting parallel writes | Fan out research, exploration, tests, and independent reviews; declare dependencies for write/design workers | Dispatching a consumer in parallel with the research/specification it needs |
| [Anthropic effective-agent patterns](https://www.anthropic.com/engineering/building-effective-agents) | Choose simple workflows when steps are known; use routing, parallelization, orchestrator-workers, and evaluator-optimizer only where they fit | Select mission shape from task dependency structure and risk rather than rewarding maximum fan-out | Treating more agents or more tool calls as intelligence |
| [OpenTelemetry context propagation](https://opentelemetry.io/docs/concepts/context-propagation/) | Propagate one execution context across service and process boundaries using stable semantic fields | One run identity across MCP, native tools, model calls, delegates, research, evaluator, and artifacts | Using trace presence as proof of correctness |
| [Langfuse datasets and experiments](https://langfuse.com/docs/evaluation/experiments/datasets) | Turn reviewed production traces into versioned datasets and comparable experiment runs | Store/query traces, human scores, datasets, and experiment comparisons | Make Langfuse a verdict authority or silently train on unreviewed failures |
| [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) | Bind an artifact to who/what produced it and the materials/process used | Issue attestations tying final artifact hashes to driver, model, contract, source, tool, and evaluator receipts | Accept builder-authored provenance without an external verification root |
| [NIST SSDF](https://csrc.nist.gov/pubs/sp/800/218/final) | Track security requirements, design decisions, reused components, and release provenance | Research and evaluate third-party adoption, security boundaries, and component provenance | Security as one late generic scan |
| [OpenSSF Scorecard](https://github.com/ossf/scorecard) | Reproducible security-health signals for candidate open-source dependencies | One input to adopt/adapt/build decisions | A single aggregate score as an adoption oracle |
| [Google SRE canarying](https://sre.google/workbook/canarying-releases/) | Compare a bounded candidate against a control; measure false positives and false negatives; limit blast radius | Canary new Cortex routes and evaluator policies before a governance-wide claim | Promoting from one successful demonstration |
| [GOV.UK Service Standard](https://www.gov.uk/service-manual/service-standard/point-4-make-the-service-simple-to-use) | Understand users and the whole journey, test real interactions, define success, use proven patterns | Require domain actors, jobs, end-to-end journeys, actual user interaction, and human product acceptance | A dashboard shell as a proxy for a service |
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | Testable accessibility criteria and explicit conformance boundaries | Make applicable A/AA behaviors part of web success contracts and direct inspection | Claiming full conformance from a single automated scanner |
| [Google HEART](https://research.google/pubs/measuring-the-user-experience-on-a-large-scale-user-centered-metrics-for-web-applications/) | Map product goals to observable user signals and metrics | Define task success and product-quality evidence before UI implementation | Engagement/gamification metrics unrelated to the user's job |

## Adopt, adapt, or retain

1. **Adopt Inspect externally.** The cross-driver evaluator should be an Inspect task with an
   observer-owned scorer and sandbox. This gives Cortex a production evaluation structure without
   making Cortex the authority over Cortex.
2. **Retain the Cortex state engine conditionally.** It already follows event-sourced/durable-workflow
   ideas. Keep it only if it passes the same interruption, replay, idempotency, and retry tests expected
   of a production durable workflow. Otherwise reassess adopting Temporal rather than extending a
   home-grown approximation indefinitely.
3. **Adopt OpenTelemetry correlation and Langfuse experiment storage.** Extend existing integration;
   do not make a second tracing or experiment database.
4. **Adapt SLSA/in-toto provenance semantics.** A lightweight local attestation is sufficient at first,
   but it must be minted/verified outside the builder and bind the final artifact to its inputs.
5. **Adopt existing deterministic checkers.** Use WCAG tooling, security scanners, schema validators,
   browser automation, repository tests, and OpenSSF signals where they fit. A model only reviews facts
   that do not have an objective checker.
6. **Retain KEDB for operational memory.** First failures become incidents; repeated, reproducible
   classes become patterns. Neither becomes gold without independent evidence and promotion.

## Required separation of responsibilities

| Responsibility | Authority |
|---|---|
| Interpret the request and build | Driver/model operating under the frozen execution contract |
| Own workflow state and receipts | Cortex server |
| Discover external candidate sources | Web-capable driver/tool |
| Accept sources and product assumptions | Frozen policy plus human boundary |
| Capture traces and experiments | OTel/Langfuse; evidence only |
| Decide deterministic facts | External objective checker over the final artifact |
| Judge ambiguous product quality | Independent observer and human owner |
| Promote reusable knowledge/gold | Curated, independent promotion process |

The builder may explain its result but may never be the only authority that marks it successful.

## Evaluation doctrine

- Write expected behavior and success conditions before task execution and before authoring a test to
  match a known implementation.
- Use realistic, vague user requests, clean workspaces, real tools, and complete artifacts.
- Keep implementation freedom: contracts state observable outcomes and invariants, not a preferred
  UI layout or code structure.
- Enforce exact order only at real dependency, safety, approval, state-integrity, and provenance
  boundaries. Do not grade one arbitrary tool-call sequence when several valid approaches produce the
  same safe outcome.
- Keep scorer details and holdouts outside the builder's accessible workspace. A public contract may
  reveal what must be true; it must not reveal exact hidden inputs or shortcuts.
- Score procedure, behavior, evidence, independence, repeatability, and human acceptance separately.
- Seed known-bad artifacts and workflow mutations to measure the evaluator's false-pass rate.
- Balance research-trigger cases in both directions: novel/current/high-risk tasks where external
  discovery is mandatory, and stable well-covered tasks where needless browsing is a failure.
- Preserve non-pass states. Missing evidence is `UNRESOLVED`; provider/tool outages are
  `ENVIRONMENT_UNAVAILABLE`; absence of a legitimate authority is `ABSTAIN`.
- Convert reviewed production failures into regression candidates through Langfuse/KEDB, but do not
  let raw production traces silently become gold or training data.

## What this baseline changes

The next Cortex test is not allowed to ask merely whether Hermes called the expected tools or passed
generic app gates. It must ask whether an independently observed, durable, evidence-backed workflow
produced a complete artifact that performs the user's real journeys, survives relevant failures, and
can be reproduced by another driver without sharing the first driver's implementation.
