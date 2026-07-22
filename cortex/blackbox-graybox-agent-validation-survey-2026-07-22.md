# Black-Box and Gray-Box Validation of AI Agents

**Evidence, Circularity Risks, Public Benchmarks, and Production Tooling**

*Technical Survey and Position Paper — study-log entry, 2026-07-22.*

> Provenance note: assembled by the owner from personal study with AI assistance. Citations were
> AI-suggested and have **not yet been independently verified**; several carry 2026 arXiv IDs and
> should be confirmed before this is treated as settled reference. Filed to the Cortex study log for
> the record and for later verification.

## Abstract

AI agents are difficult to evaluate because their behavior emerges from multi-turn interaction, tool use, external state, retrieval, memory, orchestration, and probabilistic model outputs. Evaluating only the final answer can miss unsafe or defective execution paths, while evaluating internal traces can overconstrain valid implementations or create a circular system in which the agent, evaluator, trace generator, and acceptance criteria all share the same assumptions.

This paper distinguishes black-box validation, which evaluates externally observable behavior without depending on internal implementation details, from gray-box validation, which evaluates selected internal information such as tool trajectories, intermediate states, coverage, policy decisions, and execution traces. It argues that the two approaches provide complementary evidence but cannot validate one another automatically.

Black-box validation is strongest for measuring real task completion and implementation-independent behavior. Gray-box validation is strongest for diagnosis, process invariants, coverage analysis, and detecting accidental success. Both are vulnerable to oracle errors, benchmark gaming, model-judge bias, adaptive overfitting, correlated evaluator failures, and distribution shift.

Publicly available benchmarks including τ-bench, AgentBoard, AgentBench, SWE-bench, WebArena, OSWorld, BFCL, AgentDojo, Agent Security Bench, AgentDyn, FuzzBench, and Magma provide useful components for evaluating these approaches. Production platforms such as Inspect AI, LangSmith, Braintrust, and Arize Phoenix operationalize output-level and trajectory-level evaluations. However, none of these tools independently proves that a combined black-box and gray-box evaluation pipeline is correct, independent, or resistant to circular validation.

The central conclusion is that black-box and gray-box validation should be treated as independent evidence channels whose oracles, instrumentation, and failure modes must themselves be tested.

---

## 1. Introduction

A conventional software function can often be evaluated by providing an input and comparing the resulting output against an expected value. AI agents are more complicated. An agent may:

- conduct a multi-turn conversation;
- retrieve external information;
- invoke tools;
- modify files or databases;
- delegate work to other models;
- maintain memory;
- recover from failed actions;
- make nondeterministic decisions;
- produce an apparently correct outcome through an incorrect process.

A useful evaluation method must therefore answer at least two distinct questions:

1. Did the agent produce the correct externally observable result?
2. Did the agent reach that result through an acceptable execution process?

Black-box validation primarily addresses the first question. Gray-box validation primarily addresses the second, although the boundary is not absolute.

The broader software-testing literature identifies the difficulty of determining whether a result is correct as the test-oracle problem. Even when an execution is fully observable, the evaluator may not possess a complete or reliable definition of the correct behavior [1].

For AI agents, the oracle problem becomes more severe because natural-language requirements may be ambiguous, several outputs may be acceptable, external environments may change, and an evaluator model may share the same misconceptions as the agent being evaluated.

---

## 2. Definitions

### 2.1 Black-box validation

Black-box validation evaluates a system through its externally observable interface without relying on knowledge of its internal implementation.

The validator may observe: user inputs; final responses; externally visible tool effects; database or filesystem state; public errors and status codes; timing and resource consumption; whether the requested task was completed.

The validator ordinarily does not depend on: internal prompts; source code; hidden reasoning; intermediate plans; internal memory; model-provider identity; private tool-selection logic.

A black-box evaluation can be represented as:

```
Initial environment state + User request
        ↓
Agent system
        ↓
Final response and external state
        ↓
Outcome oracle
```

For example, an airline-support agent may be instructed to cancel an eligible reservation. The evaluator can inspect whether the reservation was actually cancelled, whether the correct fees were applied, and whether unrelated reservations remained unchanged.

Recent research introduced AgentEval, a black-box framework that mines conversational workflow graphs from observed agent interactions. It then replays paths to important workflow boundaries, such as confirmation or identity-checking gates, and perturbs the interaction at those boundaries. On four τ³-bench agents, AgentEval covered between 23 and 38 distinct boundaries per agent, compared with 12 boundaries for a prompt-only baseline [2].

This provides direct evidence that structured black-box exploration can reveal state-dependent failures without source-code access. It does not prove that black-box testing can enumerate every relevant state or detect every possible failure.

### 2.2 Gray-box validation

Gray-box validation examines externally observable behavior while also using selected internal information.

The evaluator does not necessarily receive unrestricted source-code or prompt access. Instead, it may inspect limited evidence such as: sequences of tool calls; tool arguments; intermediate environment states; retrieval results; execution spans; code coverage; branch coverage; policy decisions; memory reads and writes; latency by processing stage; generated plans; sandbox events; intermediate task progress.

A gray-box evaluation can be represented as:

```
Initial environment state + User request
        ↓
Agent system
        ├── final response
        ├── tool trajectory
        ├── intermediate states
        ├── execution metadata
        └── selected instrumentation
        ↓
Outcome oracle + process oracle
```

The term gray-box is well established in software testing, especially coverage-guided fuzzing. Coverage-based gray-box fuzzers observe limited program-execution feedback, such as newly reached control-flow paths, and use that information to guide future test generation without performing complete semantic analysis of the program [3].

In AI-agent evaluation, closely related approaches are often called: trajectory evaluation; trace-based evaluation; process evaluation; progress evaluation; step-level evaluation; execution-path evaluation.

AgentBoard is an example of a trajectory-aware evaluation framework. It supplements final success rates with progress rates, grounding accuracy, subskill analysis, long-range interaction measurements, and trajectory visualization. Its progress metrics correlated above 0.95 with human progress assessments on the benchmark's evaluated tasks [4].

Therefore, in this paper, gray-box agent validation means any evaluation that uses selected internal execution evidence in addition to final observable outcomes.

### 2.3 White-box validation

White-box validation has broad access to implementation details, potentially including: complete source code; system and developer prompts; orchestration logic; policy definitions; memory implementation; model configurations; test-generation code; all internal state.

White-box analysis can identify structural vulnerabilities and unreachable branches, but it is more tightly coupled to the implementation. It also creates greater risk that the evaluator will validate what the system claims to do rather than what the deployed system actually does.

This paper focuses on black-box and gray-box approaches, with white-box analysis treated as a complementary engineering and auditing method.

---

## 3. The Case for Black-Box Validation

**3.1 Implementation independence.** A black-box test can remain valid when the implementation changes. The requirement "the agent must not issue a refund without the required approval" can be tested regardless of the model provider, prompt structure, orchestration framework, number of subagents, programming language, or internal authorization implementation. This makes black-box evaluation useful for comparing different agent architectures under the same observable requirements.

**3.2 End-to-end evaluation.** Black-box testing includes failures caused by interactions between components. An agent may select the correct tool and arguments but still fail because the tool adapter transforms an argument incorrectly, the environment is in an unexpected state, the API call is retried twice, the final response claims success when the action failed, or a later step reverses an earlier correct action. τ-bench evaluates conversational agents by comparing the final database state after an interaction with an annotated goal state. It also introduced "pass^k", which measures whether an agent succeeds consistently across repeated executions rather than succeeding once by chance [5].

**3.3 Resistance to implementation-specific gaming.** When an evaluator does not expose its expected internal trajectory, the agent cannot pass merely by emitting expected trace labels or imitating a preferred reasoning format. This resistance is limited: an agent may still overfit to public task distributions, recognizable benchmark wording, known environment structures, or repeatedly queried hidden evaluations.

---

## 4. Strict Critique of Black-Box Validation

**4.1 The oracle can be wrong.** A black-box test is only as reliable as its expected outcome. Example — Task: repair a software defect; Oracle: the supplied tests pass; Unobserved problem: the patch breaks behavior not covered by those tests. An empirical analysis of SWE-bench Verified patches found that running additional repository tests identified an average of 7.8% of apparently plausible patches as incorrect. The reported issue-resolution rate fell by 4.5 percentage points on average. Differential testing also found that 29.6% of plausible patches behaved differently from their corresponding developer patches [6].

**4.2 Correct outcome, incorrect cause.** The observed result may be correct accidentally. Expected: an unauthorized transfer must not happen; Observed: no transfer happens; Actual reason: the payment service crashed. The black-box result passes a narrow "no transfer" check but does not demonstrate that authorization or safe failure handling worked correctly.

**4.3 Limited diagnosis.** A black-box failure may reveal that the system is wrong without revealing why (planning, retrieval, tool-selection, invalid arguments, environment mismatch, memory corruption, policy error, tool execution, or final-response hallucination). Without selected internal evidence, remediation may require expensive manual investigation.

**4.4 Sparse coverage.** A passing sample does not prove that nearby or hidden behaviors are correct. Multi-turn agents can have large state spaces; important boundaries may require specific sequences of earlier actions before they become reachable. AgentEval's workflow-graph approach was designed specifically because ordinary prompt sampling has difficulty reaching these state-dependent boundaries [2].

**4.5 Nondeterministic success.** An agent may pass one run and fail another because of model sampling, tool latency, retrieval variation, changed environment state, simulated-user behavior, or race conditions. Single-run accuracy can therefore overestimate reliability. Repeated metrics such as "pass^k" expose systems that succeed occasionally but cannot do so consistently [5].

**4.6 Public benchmark contamination.** Public benchmarks improve reproducibility but expose tasks, test distributions, prompts, and sometimes complete solutions. Models or agent scaffolds may be trained, tuned, or manually optimized against the benchmark. A high score can then partly measure benchmark familiarity instead of generalized ability. Continuously refreshed benchmarks such as SWE-bench-Live were proposed partly to reduce staleness and contamination risks present in static issue-resolution benchmarks [7].

---

## 5. The Case for Gray-Box Validation

**5.1 Diagnosing where failure occurred.** A trace can separate a wrong final result into: the correct tool was never called; the wrong tool was selected; the right tool received incorrect arguments; the tool succeeded but the agent misread the result; the environment changed after execution. This makes gray-box evaluation particularly valuable during development and regression analysis.

**5.2 Detecting accidental success.** Suppose an agent completes a transaction but performs unnecessary privileged calls, retries a non-idempotent operation, reads unrelated confidential records, ignores a required confirmation, or reaches the correct state through an unsafe shortcut. A final-state oracle may mark the task successful; a trajectory-level evaluator can detect the process violation.

**5.3 Measuring partial progress.** Final success can be too coarse for long-horizon tasks. An agent that completes 90% of a workflow and fails at the final step receives the same binary score as one that takes no useful action. AgentBoard's progress-rate methodology was developed to capture incremental advancement and distinguish systems with similar low final success but substantially different partial capabilities [4].

**5.4 Coverage-guided test generation.** In conventional software security, coverage-guided gray-box fuzzers prioritize inputs that reach new paths. FuzzBench provides a public, reproducible platform for comparing fuzzers across real-world targets, while Magma supplements coverage measurements with known ground-truth vulnerabilities [8, 9]. A related agent-testing approach could guide test generation using unexplored tool sequences, unvisited workflow states, untested policy branches, unseen error types, untested memory transitions, or low-frequency environment states.

**5.5 Efficiency and cost analysis.** Trajectory evidence can measure number of tool calls, unnecessary retries, model-call count, token consumption, latency, repeated retrieval, and progress per step. OSWorld-Human adds human-determined trajectories to OSWorld and found that even high-scoring computer-use agents often took substantially more steps than necessary [10]. An agent may therefore be externally correct but operationally inefficient.

---

## 6. Strict Critique of Gray-Box Validation

**6.1 Instrumentation is not ground truth.** A trace is an observation produced by an instrumentation system, not automatically a complete record of reality. It may omit direct network calls, writes made by another process, delayed side effects, out-of-band communication, failed logging operations, or events after trace completion. If the agent generates its own authoritative trace, it may emit a convincing but false account. Gray-box evidence should be corroborated against independently observed environment state whenever possible.

**6.2 The trajectory-equivalence problem.** Multiple valid trajectories may produce the same correct result. A gray-box evaluator that compares against one "golden trajectory" may incorrectly reject valid alternatives. The correct process oracle should usually specify invariants and forbidden actions, not require exact imitation of a reference trace. Prefer "user identity verified before write; no unrelated records accessed; exactly one successful payment operation" over "call Tool A; then Tool B; then Tool C; use exactly six steps."

**6.3 Overfitting to the evaluator.** When developers receive detailed trajectory feedback, they may optimize for the visible evaluator rather than improve general behavior — emitting preferred tool sequences, avoiding penalized-but-safe actions, producing expected trace metadata, minimizing step count at the expense of verification, or imitating the evaluator's preferred reasoning style.

**6.4 Implementation coupling.** Gray-box tests often depend on trace schemas, tool names, orchestration structure, framework-specific spans, or internal state representations. An internal refactor may break gray-box tests even when external behavior remains correct, creating maintenance cost and discouraging beneficial architecture changes.

**6.5 Instrumentation can alter behavior.** Tracing and monitoring may affect timing, concurrency, memory use, context length, model prompts, or ordering of asynchronous events — a form of observer effect. The instrumented system may not behave identically to the minimally instrumented production system.

**6.6 Exposure of sensitive information.** Agent traces may contain user data, retrieved documents, tool credentials, file contents, internal prompts, personal information, or security-sensitive actions. Gray-box evaluation increases the sensitive material collected and retained; trace access should be treated as privileged.

---

## 7. Circular Validation

**7.1 Definition.** Circular validation occurs when the system being evaluated and the system determining correctness depend on the same assumptions, models, data, or artifacts:

```
Model creates answer → Related model evaluates answer →
Evaluation confirms shared assumptions → Result treated as independent validation
```

Adding more evaluators does not eliminate circularity when they share the same error source.

**7.2 Shared-oracle circularity.** Both black-box and gray-box evaluators may derive their expected behavior from the same flawed requirement — an incorrect requirement feeding the black-box expected result, the gray-box required trajectory, the model-judge rubric, and the human review checklist alike. All layers may agree while validating the wrong behavior. The software-testing oracle literature establishes that complete and reliable oracles are frequently difficult or expensive to construct [1].

**7.3 LLM-as-judge bias.** LLM judges are scalable and useful for semantically complex outputs, but they are not neutral ground truth. The MT-Bench study found strong agreement between capable LLM judges and human preferences while also identifying position bias, verbosity bias, self-enhancement bias, and reasoning limitations [11]. Research on self-preference found that judges may favor outputs more familiar to their own model distribution, including lower-perplexity outputs, rather than purely favoring objectively better responses [12]. Using a different model vendor is helpful but does not make the evaluator independent by itself.

**7.4 Correlated model errors.** A large-scale study of more than 350 language models found substantial correlation between model errors, including across different model families and providers [13]. Multiple-model consensus may therefore represent independent confirmation, or shared training-data artifacts, shared reasoning shortcuts, shared benchmark familiarity, common task ambiguity, or common preference for a particular answer style. Consensus measures agreement, not truth.

**7.5 Adaptive holdout overfitting.** A hidden evaluation set can become effectively visible through repeated interaction. If each failed attempt reveals the number of failed cases, exact categories, detailed explanations, per-step scores, timing differences, or unlimited retries, the developer can gradually infer the hidden evaluator. Research on adaptive data analysis demonstrated that repeatedly consulting a holdout and adapting based on its results can cause overfitting even when the underlying holdout examples are never directly disclosed [14].

**7.6 Benchmark gaming.** A benchmark becomes a development target once it is public and important. Systems may optimize prompt wording, environment-specific shortcuts, known tool schemas, scoring bugs, expected task distributions, or evaluator weaknesses. This is not necessarily deliberate cheating; ordinary iterative improvement can gradually specialize a system to a benchmark.

**7.7 Trace circularity.** A gray-box evaluator becomes circular when it trusts execution records generated by the same component it is evaluating. If the agent claims the correct tool was used, authorization passed, and no unsafe action occurred, and the evaluator accepts that agent-generated trace as evidence, the loop closes. The trace should instead come from independent instrumentation or the external environment.

---

## 8. Anti-Circular Validation Design

1. **Separate outcome and process oracles.** Use distinct evaluators for "was the external task completed correctly?" and "were required invariants preserved?" The process evaluator must not reinterpret an incorrect external outcome as successful; the outcome evaluator must not ignore a critical process violation because the final state is correct.
2. **Prefer deterministic evidence where possible** — database/filesystem state, JSON structure, function-call arguments, compiler results, unit tests, access-control decisions, schema compliance, forbidden operations, exact numerical constraints. Use model judges only for semantic questions that cannot be reliably reduced to deterministic checks.
3. **Evaluate invariants, not exact trajectories.** Distinguish required invariants (permitted order, required confirmation, no sensitive-data access, no duplicate write, all mandatory subtasks) from optional implementation choices (exact wording, exact reasoning sequence, harmless extra retrieval, equivalent tool ordering, alternative valid algorithms).
4. **Independently observe final state.** Query the actual system of record after execution. If the agent claims "reservation cancelled" but the reservation database shows it active → FAIL.
5. **Blind model judges** — conceal producer identity, randomize and reverse answer ordering, remove stylistic metadata, commit initial judgments before showing other judges' opinions, calibrate against labelled examples.
6. **Limit hidden-evaluation feedback.** Development set: visible cases + detailed diagnostics. Diagnostic holdout: limited queries + broad categories. Final holdout: fresh or rarely reused cases with minimal feedback. Count every query to a protected holdout.
7. **Use metamorphic and differential testing** — reordering irrelevant records should not change the decision; adding irrelevant text should not change a tool action; equivalent phrasing should produce equivalent outcomes; removing required approval should flip allow→deny; two implementations should agree on shared requirements.
8. **Test the evaluator.** Challenge every validation component with deliberately defective cases: a superficially successful but incomplete result (final-state oracle); an alternate valid trajectory (trajectory evaluator); an intentionally unlogged side effect (trace collection); reversed candidate order with concealed authorship (LLM judge); repeated-query inference (holdout); known scoring mistakes (benchmark harness); seeded defects (human reviewer); delayed and duplicated events (production monitor). An evaluator never tested against known failures should not be treated as authoritative.

---

## 9. Publicly Available Benchmarks

*(Operational classification; many benchmarks run black-box while also collecting traces for gray-box analysis.)*

- **τ-bench / τ³-bench** — conversational tool-using agents under domain policies; primarily black-box final-state comparison plus "pass^k" consistency; simulated users may not represent production [5].
- **AgentBoard** — analytical, strong gray-box/progress elements (progress, grounding, subskill, long-range, trajectories); annotations may privilege expected solution structures [4].
- **AgentBench** — LLMs as agents across eight interactive environments; aggregate scores can hide per-domain differences [15].
- **SWE-bench** — coding agents on real GitHub issues; black-box execution via repository tests; the supplied oracle may be incomplete [6].
- **WebArena** — autonomous web interaction in self-hosted environments; multiple valid paths; environment maintenance needed for reproducibility [16].
- **OSWorld / OSWorld 2.0** — multimodal computer-use agents on real desktops (369 tasks; 2.0 adds 108 longer workflows); a correct final state does not establish the intended/safest method [17, 18].
- **BFCL** — function selection/argument generation via AST and executable validation; measures the function-calling layer, not full agent safety [19].
- **AgentDojo** — utility and security under indirect prompt injection; static attacks can become development targets [20].
- **Agent Security Bench** — attacks/defenses across agent stages; threat models cannot cover unknown attacks or all tool compositions [21].
- **AgentDyn** — dynamic open-ended prompt-injection (60 tasks, 560 injections); still a finite curated benchmark [22].
- **FuzzBench** — reproducible coverage-guided fuzzer comparison; coverage is an imperfect proxy for defect detection [8].
- **Magma** — fuzzers against known ground-truth bugs; demonstrates that internal progress metrics like coverage should be validated against meaningful ground-truth failures [9].

---

## 10. Production and Open-Source Evaluation Tools

These operationalize black-box and gray-box evaluation but do not prove the evaluation design is non-circular.

- **Inspect AI** (UK AI Security Institute) — tasks, tool agents, custom/model-graded scorers, sandbox execution, logs/traces; Inspect Evals ships 200+ evaluations incl. SWE-bench. Provides machinery, not guarantees of scorer correctness, holdout independence, judge neutrality, trace completeness, or benchmark-to-production match [23].
- **LangSmith** — offline datasets, final-response and trajectory evaluators, model judges, production tracing; trajectory evaluation may depend on LLM judges/reference trajectories; users design their own independence and anti-overfitting controls [24].
- **Braintrust** — datasets, code/LLM/trace-level scorers, online production scoring; flexibility means quality depends heavily on customer-created scorers and datasets [25].
- **Arize Phoenix** — OpenTelemetry-based observability + evaluation across model calls, retrieval, tools; observability is not validation — a complete-looking trace may omit uninstrumented effects, and a high score may rely on a weak oracle [26].

No surveyed production tool proves that combining black-box and gray-box testing eliminates circular validation. The user must still determine who creates the oracle, whether it is independent, whether traces are trustworthy, whether hidden evaluations are protected, whether model judges are calibrated, whether test cases represent production, whether evaluators have correlated errors, and whether the evaluator itself has been adversarially tested.

---

## 11. Recommended Evaluation Protocol

- **Layer A — black-box outcome:** final task completion, external state, unintended side effects, consistency across repeated runs, performance under perturbed inputs. Never rely solely on the agent's verbal success claim.
- **Layer B — gray-box invariants:** required steps, forbidden actions, tool correctness, sensitive-data access, duplication/retries, progress, resource consumption. Avoid requiring exact reference trajectories unless only one is truly valid.
- **Layer C — evaluator validation:** scoring implementation, trace completeness, judge ordering bias, judge self-preference, benchmark contamination, holdout leakage, alternative valid strategies, known defective behaviors.
- **Layer D — production validation:** sampled production traces, independently observed outcomes, human incident review, new regression cases from failures, periodically refreshed benchmarks, distribution-shift monitoring.
- **Decision rule (conjunctive):** `PASS = external outcome correct AND critical process invariants satisfied AND evaluation infrastructure healthy AND repeated-run reliability acceptable`. Model consensus must not override a deterministic external-state failure or critical invariant violation.

---

## 12. Conclusions

Black-box validation asks "did the agent produce the correct observable outcome?" Gray-box validation asks "what happened during execution, and were required process properties preserved?" Black-box is more implementation-independent and better aligned with user-visible results, but suffers from incomplete oracles, sparse coverage, weak diagnosis, nondeterminism, and benchmark gaming. Gray-box provides process visibility, diagnosis, coverage feedback, partial-progress measurement, and detection of accidental success, but can trust incomplete instrumentation, reject valid alternate strategies, couple tests to implementation details, expose sensitive information, and encourage optimization for the evaluator.

Combining both is stronger than either alone, but the combination does not automatically solve circularity: both layers may share the same incorrect specification, model biases, benchmark assumptions, or incomplete oracle. The most defensible approach: (1) use black-box outcomes as primary evidence of task completion; (2) use gray-box evidence for critical invariants and diagnosis; (3) independently observe important external effects; (4) avoid exact-trajectory matching when several strategies are valid; (5) limit adaptive access to protected evaluations; (6) calibrate and blind model judges; (7) test the evaluator with deliberately defective and alternate-valid cases; (8) treat public benchmarks as scientific instruments, not proof of production safety; (9) continuously add real production failures to the evaluation corpus.

The correct claim is not "black-box and gray-box validation prove that an agent is correct," but "together they create complementary evidence about outcomes and execution processes, provided that their oracles, instrumentation, benchmarks, and evaluators are themselves independently challenged."

---

## References

*Citations are AI-suggested and pending independent verification (see provenance note). Several carry 2026 arXiv identifiers.*

[1] Barr, Harman, McMinn, Shahbaz, Yoo. "The Oracle Problem in Software Testing: A Survey." IEEE TSE 41(5), 507–525, 2015. DOI: 10.1109/TSE.2014.2372785.
[2] Lin, Yu, Zhang, Briand, Niland, Muñoz. "Mining Workflow Graphs for Black-Box Boundary Testing of Conversational LLM Agents." arXiv:2607.06873, 2026.
[3] Böhme, Pham, Roychoudhury. "Coverage-Based Greybox Fuzzing as Markov Chain." ACM CCS 2016; extended in IEEE TSE.
[4] Ma et al. "AgentBoard: An Analytical Evaluation Board of Multi-turn LLM Agents." NeurIPS D&B, 2024. arXiv:2401.13178. Repo: hkust-nlp/AgentBoard.
[5] Yao, Shinn, Razavi, Narasimhan. "τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains." arXiv:2406.12045, 2024. Repo: sierra-research/tau-bench.
[6] Yang et al. "Are 'Solved Issues' in SWE-bench Really Solved Correctly? An Empirical Study." arXiv:2503.15223, 2025.
[7] Zhang et al. "SWE-bench Goes Live!" arXiv:2505.23419, 2025.
[8] Metzman et al. "FuzzBench: An Open Fuzzer Benchmarking Platform and Service." ESEC/FSE 2021. Repo: google/fuzzbench.
[9] Hazimeh et al. "Magma: A Ground-Truth Fuzzing Benchmark." Proc. ACM Meas. Anal. Comput. Syst. 4(3), 2020. arXiv:2009.01120.
[10] Abhyankar, Qi, Zhang. "OSWorld-Human: Benchmarking the Efficiency of Computer-Use Agents." arXiv:2506.16042, 2025. Repo: WukLab/osworld-human.
[11] Zheng et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." NeurIPS 2023. arXiv:2306.05685.
[12] Wataoka, Takahashi, Ri. "Self-Preference Bias in LLM-as-a-Judge." arXiv:2410.21819, 2024.
[13] Kim, Garg, Peng, Garg. "Correlated Errors in Large Language Models." ICML, PMLR 267, 2025.
[14] Dwork, Feldman, Hardt, Pitassi, Reingold, Roth. "Generalization in Adaptive Data Analysis and Holdout Reuse." NeurIPS 2015. arXiv:1506.02629.
[15] Liu et al. "AgentBench: Evaluating LLMs as Agents." ICLR 2024. arXiv:2308.03688. Repo: THUDM/AgentBench.
[16] Zhou et al. "WebArena: A Realistic Web Environment for Building Autonomous Agents." arXiv:2307.13854, 2023. Repo: web-arena-x/webarena.
[17] Xie et al. "OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments." NeurIPS 2024. arXiv:2404.07972. Repo: xlang-ai/OSWorld.
[18] Yuan et al. "OSWorld 2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks." arXiv:2606.29537, 2026.
[19] Patil et al. "Berkeley Function-Calling Leaderboard." UC Berkeley Sky Computing Lab. Repo: ShishirPatil/gorilla.
[20] Debenedetti, Zhang, Balunovic, Beurer-Kellner, Fischer, Tramèr. "AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents." NeurIPS D&B, 2024. arXiv:2406.13352. Repo: ethz-spylab/agentdojo.
[21] Zhang et al. "Agent Security Bench: Formalizing and Benchmarking Attacks and Defenses in LLM-Based Agents." ICLR 2025. arXiv:2410.02644. Repo: agiresearch/asb.
[22] Li, Wen, Shi, Zhang, Xiao. "AgentDyn: A Dynamic Open-Ended Benchmark for Evaluating Prompt Injection Attacks of Real-World Agent Security Systems." arXiv:2602.03117, 2026. Repo: SaFo-Lab/AgentDyn.
[23] UK AI Security Institute. "Inspect AI" / "Inspect Evals." Repos: UKGovernmentBEIS/inspect_ai, UKGovernmentBEIS/inspect_evals.
[24] LangChain. "LangSmith Trajectory Evaluations and Agent Evals." Product documentation.
[25] Braintrust. "Systematic Evaluation, Trace Scorers, and Online Scoring." Product documentation.
[26] Arize AI. "Phoenix: Open-Source AI Observability and Evaluation." Repo: Arize-ai/phoenix.
