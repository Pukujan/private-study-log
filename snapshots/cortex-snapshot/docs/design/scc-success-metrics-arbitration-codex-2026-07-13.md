# SCC success metrics arbitration — Codex independent review

Date: 2026-07-13  
Reviewer: GPT-5.6 Codex  
Disposition: **mostly aspirational, not yet measurable as an agent harness**

## Verdict and definition of success

SCC succeeds as a harness only when it changes agent behavior and verified task outcomes, not when it merely contains modules, schemas, or prose describing those behaviors. A successful harness deterministically routes a task through the phases its risk requires; supplies current, findable evidence before mutation; acquires a missing source; records provenance out of band; fans out independent work within a declared capacity budget; and prevents unsupported completion from shipping. It must do this with zero process refusals, a bounded context surface, and negligible ceremony.

The corpus contains credible components for retrieval, fetching, contracts, ontology structure, trace capture, and objective gates. That is not the same as an integrated harness. The current experiment admits that B/C behavior is not wired and delegates it to a future `AgentInvoker` (`evals/ab_cortex_scaffold/PREREGISTRATION.md:75-84`; `evals/ab_cortex_scaffold/runner.py:130-145`). The default invoker creates an empty transcript and no closeout (`runner.py:70-81`). Therefore no current Kurzweil result can establish SCC-level success.

## Eight owner goals: operational definitions and implementation judgment

The thresholds below are preregistration candidates. “Oracle” means task/domain truth is required; “deterministic” means the harness can check event/file structure without semantic judgment.

| # | Goal | Deterministic success metric and constraint | Oracle need | Corpus status |
|---|---|---|---|---|
| 1 | Proper state routing | For 100% of non-trivial mutation runs, the append-only event trace matches the task-type transition automaton; a SEARCH receipt precedes first mutation; RESEARCH is required and precedes SDD for tasks whose frozen risk classifier says `external_evidence_required`; illegal/skipped transitions = 0. Classifier accuracy on a human-frozen 100-task routing set ≥95%, with zero false negatives on high-risk tasks. | Transition/order is deterministic; whether RESEARCH was required needs a frozen routing oracle. | **Partial, not proven end-to-end.** The corrected design proposes timestamp/order scoring and says a disclosure controller exists (`docs/design/cortex-redesign-CORRECTED-spec.md:23-27,53`). But the experiment measures only `research_cited`, not legal transitions (`PREREGISTRATION.md:104-110`), and its scaffold behavior is unwired (`runner.py:130-138`). Older roadmap evidence explicitly described orchestration as prose with no persisted handoff/enforcement (`docs/ROADMAP.md:99-106`); later modules do not cure the missing integrated trial. |
| 2 | Automatic provenance by background agents | ≥99% of completed runs produce, within 60 s of terminal event, an atomic closeout whose `event_digest` matches the immutable transcript, whose author role is `scribe`, and whose process PID/run id differs from the foreground executor; foreground closeout-tool calls = 0; sampled claim-to-event precision ≥95%. | Digest, author/process separation, timing and atomicity are deterministic. Claim relevance/faithfulness needs a calibrated human/semantic oracle. | **Mostly design, not operationalized.** The design explicitly requires a transcript-fed asynchronous subagent scribe (`CORRECTED-spec.md:19,27`). Current A/B/C only checks schema + transcript digest (`PREREGISTRATION.md:104-110`) and cannot prove background authorship. Citation-required scribe remains trigger-gated (`docs/PHASE-GATES.md:219`). Trace capture is a synchronous local append with optional mirrors, not a transcript-to-closeout agent (`cortex_core/trace_capture.py:50-77`). |
| 3 | Always-updated resources/docs; docs-first | On every mutation run: relevant-doc recall@5 ≥0.95 on a task/domain holdout; first relevant local search precedes mutation in 100%; if a governed artifact changes, every path required by a frozen `docs.map` is updated or an explicit machine-checkable `not_applicable` rule passes; stale served sources = 0 under per-source TTL/version rules; freshness lag p95 ≤24 h. | Ordering, mapping, hashes, TTL are deterministic. Relevance and whether docs should change require a per-task oracle/manifest. | **Partial.** Search/fetch/reindex and findability mechanisms exist; the roadmap records fetch→auto-reindex and closeout→search (`docs/ROADMAP.md:28-35`). The corrected spec calls docs mapping a post-hoc detector (`CORRECTED-spec.md:18,26`). But A/B/C's `docs_updated` is only a task-specific diff/set check (`PREREGISTRATION.md:42-43,104-110`), not freshness, relevance, docs-first use, or ongoing refresh. Scope-pack recall preservation is explicitly unmeasured (`docs/PHASE-GATES.md:151`). |
| 4 | Living ontology/findability | On a versioned, independently authored query set covering aliases, supersession, relations, and natural-language lookup: recall@5 ≥0.90, MRR ≥0.80, zero-result rate ≤2%; ontology-enabled retrieval must improve recall@5 by ≥5 percentage points or MRR by ≥0.05 over retrieval-only baseline with no >2-point regression in any domain; 100% of accepted artifacts have a valid current entity and provenance edge within 24 h. | Schema, coverage, latency and deltas are deterministic once gold query→entity/doc relevance is frozen; gold relevance needs humans/domain oracle. | **Partial structure, outcome open.** Schema, append-only entities/relations and queries exist, but the phase gate plainly says ontology is not fused into RRF, lacks a golden-set retrieval win, and lacks Stage-D summaries (`docs/PHASE-GATES.md:189-202`). A/B/C has no ontology or findability axis. |
| 5 | Contract + file/folder structure | For 100% of mutation tasks, before first write a schema-valid task contract exists with resolving evidence refs, acceptance checks, allowed write roots and expected outputs; post-run diff contains 0 paths outside allowed roots; repository layout linter has 0 violations; trivial chore contract generation p95 ≤1 s and ≤3 lines. | Schema/path/order checks are deterministic. Correct acceptance criteria and classification need a frozen task oracle or review sample. | **Partial, with real components but wrong experimental coverage.** Contract validation/prefill and path-resolving evidence are reported built; ceremony timing remains deferred (`docs/PHASE-GATES.md:129`). The old write-refusal gate exists (`PHASE-GATES.md:130`), but the corrected design rejects process refusals (`CORRECTED-spec.md:16-20`). A/B/C does not score contract validity, allowed paths, or layout. |
| 6 | Automatic scraper on missing resource | For every local zero-result/low-confidence query below frozen threshold, exactly one deduplicated acquisition job is enqueued within 5 s; on an allowlisted fixture set, ≥95% yields normalized, source-attributed, searchable content within 5 min; 100% enforce SSRF/content-type/size/license controls; repeated identical gaps create 0 duplicate documents; unresolved jobs emit a terminal reason. | Trigger, queueing, safety, normalization, indexing and dedup are deterministic. Whether the acquired source answers the gap needs a relevance oracle. | **Partial plumbing, not automatic guarantee.** Fetch→search is demonstrated (`docs/ROADMAP.md:28-30`) and the design calls for corpus-first bounded fetch (`CORRECTED-spec.md:38`). The roadmap's gap-driven acquisition is a proposal (`docs/ROADMAP.md:175-178`), while its audit documents poisoning/dedup failures in the earlier fetch path (`ROADMAP.md:77-85`). No A/B/C axis exercises a real scanned image, real OCR/TTS, or missing-resource acquisition (`PREREGISTRATION.md:45-48`). |
| 7 | Tiered parallelization by availability | For tasks with ≥2 independent eligible subtasks, fanout begins before any child finishes; assigned concurrency = `min(available_slots, risk_cap, independent_subtasks)`; utilization ≥80% while backlog ≥capacity; no side-effect conflicts or duplicate ownership; join accounts for 100% of children; versus serial baseline, p50 wall time improves ≥25% with verified success no worse than 2 percentage points and cost ≤1.15×. Tasks with no independent subtasks must not fan out. | Scheduling, capacity, overlap, ownership and joins are deterministic. Correct decomposition/independence and output quality require task gold or a frozen decomposition oracle. | **Not measured; at best separate components/design.** Multi-agent is the stated purpose, but the roadmap observed concurrency hazards (`docs/ROADMAP.md:119-122`). Current runner executes arms/trials sequentially inside nested loops (`runner.py:156-183`); it measures tool calls/time, not child count, overlap, capacity use, join integrity, or tier routing. |
| 8 | Hallucination catching + eval gates | 100% of externally verifiable claims and completion claims carry resolvable evidence; deterministic checker false-negative rate = 0 on a seeded adversarial suite; unsupported-claim detection recall ≥95%, precision ≥90%; no run may be marked/shipped PASS unless every mandatory objective checker passes; hidden-holdout pass recorded for risk-triggered tasks; LLM judges never establish hard gold. | Artifact/test/citation claims can use deterministic oracles. Open-ended factual entailment needs a trusted corpus/human oracle; judge annotations cannot be gate truth. | **Partial checker ecosystem, not an integrated hallucination gate.** The design correctly says deterministic checks establish ground truth and judges only annotate (`CORRECTED-spec.md:41`). Closeout evidence resolution exists, but relevance is deferred (`docs/PHASE-GATES.md:131`); hidden holdouts are planned (`PHASE-GATES.md:134`). The A/B/C ship function is explicitly advisory (`runner.py:225-228`) and can ship on aggregate discipline without a hallucination axis. |

## Is the Kurzweil A/B/C sufficient?

No. It is a useful evaluator-plumbing smoke test and a narrowly gradeable product slice, not validation of SCC as a harness. Its task oracle checks OCR text similarity, a valid WAV, timing-map shape/overlap, note fields, and one reading-log edit (`PREREGISTRATION.md:31-43`). It honestly substitutes ground-truth text for a scan and does not run real OCR/TTS (`PREREGISTRATION.md:45-48`). That tests output plumbing, not autonomous acquisition or robust assistive software.

The stated “8 axes” are better described as outcome/discipline/overhead measures: task success, research ordering, docs correctness, audit validity, tokens, time/tool calls, context, and refusals/loops/protocol/cost (`CORRECTED-spec.md:56-58`; `PREREGISTRATION.md:104-110`). Coverage against the owner goals is:

- Goal 1: **weak partial** — research citation/order only; no state automaton.
- Goal 2: **weak partial** — closeout validity, not autonomous/background authorship.
- Goal 3: **partial** — one prescribed doc diff, not retrieval recall or freshness.
- Goal 4: **miss** — no ontology/findability evaluation.
- Goal 5: **miss** — no contract/layout/diff-boundary evaluation.
- Goal 6: **miss** — no gap-triggered acquisition; even real OCR/TTS is excluded.
- Goal 7: **miss** — no fanout, capacity, overlap, join, or tier metric.
- Goal 8: **partial** — milestone artifact checks, but no claim-evidence/hallucination gate.

Add these deterministic axes before making any SCC ship claim:

1. `state_routing_correctness`: legal-transition rate, required-phase recall, search/research-before-mutation rate, illegal transition count.
2. `findability`: frozen per-domain query set; recall@5/MRR/zero-result; ontology ablation; accepted-artifact graph coverage and freshness lag.
3. `contract_structure`: pre-write contract validity, acceptance-check execution, allowed-path diff, layout-linter violations.
4. `gap_acquisition`: trigger precision/recall, enqueue latency, extraction success, dedup, safety violations, searchable-within-SLO.
5. `background_provenance`: separate scribe identity/process, closeout latency, transcript digest, foreground ceremony calls, sampled faithfulness.
6. `parallelization`: eligible fanout recall, unnecessary fanout rate, concurrency utilization, overlap, child reconciliation, conflict count, serial-speedup ablation.
7. `hallucination_gate`: evidence coverage, citation resolution/entailment, adversarial unsupported-claim recall/precision, false PASS count, hidden-holdout result.

The ship score must not average away failures. Every hard guardrail and every task-mandatory functional axis must pass; only then compare B with A on success/cost.

## Hard pass/fail constraints

These are gates, not means and not discipline-score inputs:

- **Anti-coercion:** `refusal_count == 0` for process/governance reasons in every B or D trial. Safety/security refusals are separately classified and may pass only when the fixture expects them. Any `DOC_SYNC`, receipt, contract, or run-scope process refusal fails the arm. This follows B's explicit zero-refusal definition (`PREREGISTRATION.md:67-73`) and the corrected design's rejection of compliance walls (`CORRECTED-spec.md:16-20`).
- **Context:** `context_peak_tokens < 50,000` on every trial, `context_resting_tokens ≤1,000` before task-specific retrieval, and delivered Cortex scope pack ≤4,000 tokens by default. Token accounting must use the actual model tokenizer where available; the current chars/4 method is explicitly only an estimate (`cortex_core/packs.py:24,34-38`). Report p50/p95/max; one breach fails the arm.
- **No governance ritual:** foreground `protocol_only_turns == 0`; foreground closeout calls = 0; process-only tool calls ≤1 (initial lazy index/schema discovery); ceremony tokens ≤2% of total and time-to-first-meaningful-action regression ≤5% versus A. Research and verification are work, not ceremony. The current ship rule merely includes protocol means and does not gate them (`runner.py:206-220,232-240`).
- **No false completion:** mandatory objective checker failures, missing child joins, unresolved contract evidence, or invalid transcript digest make `task_passes=false`; aggregates cannot override this.
- **Cost/success non-inferiority:** B/D verified success no worse than A by a preregistered paired confidence bound (not raw equality of five means), and mean cost ≤1.15× A. Default N=5 is too small for a general harness claim (`PREREGISTRATION.md:86-97`).

## Private local trace/oracle loop for a READ-scoped collaborator

“Own CoT” must be stated carefully. The wrapper can capture reasoning the collaborator/model explicitly emits, tool events, prompts, outputs, diffs, and verifier results. It cannot recover a provider's hidden chain-of-thought, and must not pretend that a transcript is hidden CoT. The current `TraceRecord` supports explicit `cot`, tool calls, output and gate verdict (`cortex_core/trace_capture.py:26-39`), writes a local JSONL (`trace_capture.py:42-61`), and selects PASS records (`trace_capture.py:108-113`). However, its optional R2/Langfuse mirrors mean it is not private-by-construction (`trace_capture.py:62-75`), and its compliance audit notes that deterministic PASS does not authorize proprietary-output training (`docs/COMPLIANCE-ANTI-DISTILLATION.md:228-242`).

Minimal stdlib-only wrapper, buildable now:

1. Create collaborator-owned `.cortex/` with mode `local_private`, permissions restricted to that OS user, and files `events.jsonl`, `traces.jsonl`, `oracles/`, `closeouts/`, `state.json`, `consent.json`. Default `egress=false`, `training_use=false`, `owner_visibility=false`.
2. Run the collaborator agent as a subprocess with stdout/stderr pipes. The wrapper appends length-bounded JSON events with monotonic sequence, timestamp, run id, actor id, event type, content hash, explicit-visible-reasoning field if supplied, tool arguments/results after local redaction, and verifier result. Use `json`, `hashlib`, `subprocess`, `threading`, `queue`, `pathlib`, `sqlite3`, `secrets`, and `hmac` only.
3. A separate local scribe subprocess tails `events.jsonl` after the foreground terminal event, reconstructs facts, hashes the transcript, and atomically writes `closeouts/<run>.json` via temp-file + `os.replace`. It must never call an external model by default. A deterministic template produces actions, evidence refs, failures, and unresolved questions. If a local model is later enabled, its prose remains annotation and all facts must resolve to event ids.
4. Generate **his own** candidate oracles only from his tasks and opt-in artifacts: extract observed input/output shapes, commands, exit codes, file hashes, invariants and regression examples into `oracles/candidates.jsonl`. Promotion requires replay on a clean fixture, deterministic repeatability (for example 3/3), provenance to his run, no secret/PII, and explicit local approval for ambiguous semantics. PASS traces are examples, not automatically truth.
5. On the next similar task, retrieve only locally by task/domain tags and show the collaborator the proposed oracle/task improvements. Apply only after local acceptance. Owner receives nothing unless the collaborator flips a scoped, revocable consent record; opt-in export should default to aggregate metrics, with an explicit second choice for redacted artifacts.
6. Disable telemetry mirrors in this mode and add a test that monkeypatches sockets/HTTP clients to fail if invoked. Provide local purge by run/consent id and verify all referenced records are removed or tombstoned.

This is feasible with stdlib. Reliable semantic closeout faithfulness and open-ended oracle generation are not stdlib-deterministic; the minimal version must remain evidence-template + replay based. Also fix terminology: this is **local trace-derived improvement**, not automatic CoT distillation.

## Documentation contract and research-first task shaping

Metrics must live beside the domain, not in one universal score that hides applicability.

Proposed SCC repository structure:

```text
docs/harness/
  SUCCESS.md                 # invariant definition, hard guardrails, aggregation rules
  STATE-MACHINE.md           # states, legal edges, entry/exit receipts, risk classifier
  PRIVACY-AND-PROVENANCE.md  # local/remote planes, consent, scribe identity, retention
  TASK-SHAPING.md            # research -> SDD -> TDD lifecycle
domains/<domain>/
  DOMAIN.md                  # scope, authoritative sources, freshness policy
  METRICS.yaml               # metric id, formula, unit, threshold, applicability
  ORACLES.md                 # authority, known blind spots, human-review policy
  fixtures/                  # frozen public cases
  holdouts/                  # access-controlled evaluator cases
tasks/<task-id>/
  REQUEST.md                 # raw request, immutable
  ROUTING.json               # classifier inputs/result and required phases
  RESEARCH.md                # local searches, gaps, fetched sources, citations
  SPEC.md                    # SDD behavior, non-goals, interfaces, file boundaries
  ACCEPTANCE.yaml            # checker ids, thresholds, evidence requirements
  TEST-PLAN.md               # TDD cases and hidden-test policy
  RUN.json                   # versions, arm, model, budgets, consent, digests
  RESULT.json                # deterministic metrics and guardrails
```

Every metric record needs: stable id/version, goal, domain/task applicability predicate, exact numerator/denominator, unit, threshold and direction, sampling window, oracle authority/version, data source/event schema, exclusions, missing-data behavior (fail closed for mandatory metrics), anti-gaming note, owner, review cadence, and retirement/supersession rule. Publish per-task results and per-domain stratified confidence intervals; never report only a global mean.

Encode task shaping as executable state, not prompt advice:

```text
INTAKE -> CLASSIFY -> LOCAL_SEARCH -> [GAP_ACQUIRE -> RESEARCH]
       -> SDD_SPEC -> ORACLE_FREEZE -> TDD_RED -> EXECUTE
       -> VERIFY -> SCRIBE_CLOSEOUT -> LEARN_CANDIDATES
```

- A strong designer may shape `ROUTING.json`, `RESEARCH.md`, `SPEC.md`, and candidate `ACCEPTANCE.yaml`, but cannot execute the implementation in the same blinded evaluation lane.
- Deterministic validators block **task launch**, not ordinary tool use: execution is not scheduled until required artifacts parse, citations resolve, acceptance criteria map to checkers, and at least one RED test is observed for TDD-applicable work. This is orchestration readiness, not an in-band refusal loop.
- Freeze spec/oracles before executor assignment; record hashes in `RUN.json`. Hidden holdouts stay evaluator-only. If research changes the task materially, increment spec version and re-freeze rather than silently editing gold.
- The routing oracle exempts trivial chores and exploration from heavyweight SDD/TDD. This preserves proportionality already recognized by the contract design (`docs/PHASE-GATES.md:129`).

Collaborator wrapper README should be much smaller:

```text
README.md
  What it provides / does not provide
  Local-only privacy default and exact captured fields
  Five-state user flow: search -> shape -> run -> verify -> local closeout
  Where .cortex data lives; inspect/export/purge commands
  Consent choices and explicit statement that owner sees nothing by default
  Metric card for the current task/domain
  Failure recovery and zero-refusal promise
  Limitations: no hidden-CoT access; semantic oracles require opt-in review
```

## A fourth D arm?

**Yes, but as a preregistered second-stage arm, not as an excuse to dilute the current broken A/B/C.** B already is detection-only. A proposed D (“detection-only + private local oracle loop”) adds a distinct causal mechanism: retained personal traces, background local scribe, and replay-promoted local oracles. Comparing B versus D isolates whether that loop improves the same collaborator on later related tasks. Calling D merely “detection-only” would duplicate B.

D requires a longitudinal, repeated-task design: randomize collaborators or task sequences; use an initial cold task, then related transfer tasks; prevent cross-arm memory leakage; compare verified success, repeat-error rate, time-to-first-correct-action, oracle precision/recall, and privacy egress. Hard gates remain zero process refusals, <50k peak context, zero foreground ritual, and zero unconsented egress. A single Kurzweil task cannot measure learning, so adding D to the present one-shot runner would yield no interpretable evidence.

Recommended sequence: first wire and validate real A versus B on the expanded harness axes. Then run paired B versus D longitudinally. Retain C only as the preregistered loser-hypothesis control; do not allow coercion to become the default merely because B's implementation is absent.

## Final arbitration

The right headline is **mostly aspirational, not measurable**. The corpus demonstrates several useful mechanisms, and it is unusually candid about open gaps. But the present A/B/C measures a narrow artifact task plus three discipline proxies, while the B/C behaviors themselves are delegated out of the runner. It misses four owner-critical outcomes outright—findability, contract/layout correctness, automatic acquisition, and parallelization—and only weakly proxies routing, background provenance, freshness, and hallucination control. No SCC ship decision is valid until the behavior is wired, the missing deterministic axes and hard guardrails are preregistered, and a real agent completes enough paired and longitudinal trials to support non-inferiority claims.
