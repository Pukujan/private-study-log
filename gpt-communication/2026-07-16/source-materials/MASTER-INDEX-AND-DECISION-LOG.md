# Cortex Master Index & Decision Log

**Last Updated:** 2026-07-16 (issues migrated to GitHub tracker)
**Maintained By:** Hades session (manual until scribe/librarian capability is built — see PA1)
**Purpose:** **THE SINGLE SOURCE OF TRUTH for architecture decisions and context.** When a new agent starts, it reads THIS document for context, then checks the **GitHub issue tracker** for current status of all problems/decisions/edge cases.
**Issue Trackers:**
- **SCC (server code + brain):** https://github.com/Pukujan/stupidly-simple-cortex/issues — 63 issues (54 open, 9 closed). Architecture decisions, edge cases, meta-problems, code bugs, parked items.
- **cortex-agent-wrapper (Hermes plugin):** https://github.com/Pukujan/cortex-agent-wrapper/issues — 4 issues (3 open, 1 closed). Enforcement hook items only.
- **private-study-log:** No issues. Research handoffs only.
- Labels (both repos): decision, edge-case, meta-problem, parked, code-bug, opportunity, resolved, critical, high, medium, low.
- **When an item is resolved:** Close the issue in the correct repo + update this doc.

---

## How To Use This Document

1. **Session start:** Read this entire document. It is the orientation.
2. **"Where are we?":** Check Pending Decisions + Open Research Questions.
3. **"What's unsolved?":** Check Edge Cases + Meta-Problems.
4. **"Where is X?":** Check Brain Artifact Map. Every important doc in both repos is listed there with its path.
5. **When you make a decision:** Append it HERE in the same session. Do NOT create a new doc. Do NOT create a new handoff. Update THIS file.
6. **Never batch updates.** Do it in the session where the decision is made.
7. **Never delete entries.** Mark as superseded with a pointer to the replacement.

**The duplicate handoff problem (M7):** This document exists because we kept creating new handoff docs every session instead of maintaining one index. If you create a new doc instead of updating this one, you are recreating the problem.

---

## Frozen Decisions (D1-D14)

These are locked. Changing them requires explicit user approval and a documented reason.

| ID | Decision | Date | Rationale |
|----|----------|------|-----------|
| D1 | Cortex is a decision support system, not an autonomous agent | 2026-07-16 | Human must always know expected outcome before work begins. |
| D2 | Human plan gate is the primary problem-solving point | 2026-07-16 | Prevents bad work rather than catching it later. |
| D3 | Freeze rule: nothing moves until human says go | 2026-07-16 | Auto-approve only from strict MAPE-K trust profile. |
| D4 | Contract gates required before any feature implementation | 2026-07-16 | Module location, ownership, interface, dependencies, debt, duplicates. |
| D5 | MAPE-K serves post-completion as flywheel only | 2026-07-16 | Optimization, not feature creation. No scope expansion. |
| D6 | State machine gate is ON (write server only) | 2026-07-16 | Read server stays ungated so search always works. 19/19 tests passing. |
| D7 | `cortex_run_start` satisfies search gate | 2026-07-16 | Starting pipeline = search happens in phase 1. |
| D8 | Two separate pipelines: user-facing and developer-facing | 2026-07-16 | Different audiences, different gates. |
| D9 | Narrowing dialogue replaces decision matrix | 2026-07-16 | Agent asks questions BEFORE presenting options. Human narrows intent first. From v3 architecture. |
| D10 | Context packet system for smart injection | 2026-07-16 | Every search gets accumulated context (intent, narrowing Q&A, research findings, prior context). Not a new search engine — a context router on existing search. |
| D11 | Options must have proof of viability before presentation | 2026-07-16 | Pattern match, mechanism validation, dependency verification, scope precedent, failure mode check. Unproven options labeled UNPROVEN. |
| D12 | Auto-loop: implement → check → continue until outcome met | 2026-07-16 | Deterministic TDD checks, not LLM judgment. Budget caps prevent infinite loops. Human NOT in implementation loop. |
| D13 | Governance spine is mandatory for all new capabilities | 2026-07-16 | MCP bloat → lazy loading → progressive disclosure → wrapper → state machine → fan-out → scribe → context injection → narrowing dialogue. New capability must fit this arc or it's a regression. |
| D14 | Three-authority sufficiency model | 2026-07-16 | From KNOWLEDGE-ESCALATION.md: Cortex policy gate (deterministic), independent domain evaluator, human owner. Builder never has final sufficiency authority. |

---

## Pending Decisions (P1-P14)

| ID | Decision Needed | Options | Status |
|----|----------------|---------|--------|
| P1 | State machine phases vs hooks vs hybrid | **RESOLVED by P2.** Task-typed router replaces fixed phases. Confidence thresholds (85-92% auto, 8-15% human). Budget hard stops. |
| P2 | Skills vs state machine vs task-typed router | **RESOLVED — Option C.** Research: no production system uses fixed state machine. Task-typed routing is dominant (LangGraph, StackAI, Vercel, CrewAI). |
| P3 | Risk-tiered review vs human-reviews-everything | Alignment review found "review everything" overcorrects. Need to define risk tiers (low/medium/high). v3 architecture defines: LOW auto-advances, MEDIUM human approves plan, HIGH human approves plan+contract+reviews result. |
| P4 | Model routing per phase | **RESOLVED.** Planning→frontier, routing→cheap, execution→mid-tier. 50-70% cost reduction. |
| P5 | Evidence theater prevention | Builder never authors the only checks. Need independent evaluator. v3 architecture defines proof chain (5 sources). |
| P6 | GrokToCrawl as default search | **RESOLVED.** Config changed to firecrawl, env vars set. |
| P7 | ChatGPT deep research engagement | Brief v3 written (9 deliverables, success/fail criteria). Pushed to GitHub. Awaiting ChatGPT output. |
| P8 | Mem0 as context injector | Research complete. Mem0 v2.0.10 has embedder bug (ignores ollama config). Bug confirmed across 6 GitHub issues. Fix: upgrade to v2.0.12+. No standard hardware detection pattern exists. Alternatives: Graphiti (temporal KG), Letta (.af serialization), Zep CE. |
| P9 | Hermes restart/session recovery | Research complete. LangGraph = gold standard (checkpointers, PostgresSaver). Agent File (.af) = closest standard format. Cheapest fix = task state JSON file. No framework has auto-context-injection on restart. |
| P10 | Sandboxed test Hermes instance | Research complete. No framework has built-in shadow testing. LangSmith eval-driven loop = best pattern. Recommendation: hades-test profile, not separate Docker. |
| P11 | GrokToCrawl as default search backend | **RESOLVED.** Done 2026-07-16. |
| P12 | Model discovery (INIT phase) | Proposed in PROBLEM-MODEL-DISCOVERY doc. Two new state machine phases: INIT (probe models) + CONFIRM_REQUIREMENTS (read back to human). 7 code bugs found in model_dispatch.py. **Awaiting human approval.** |
| P13 | Onboarding / first-run experience | Part of P12. Goal: new user clones repo, wrapper auto-discovers available models, surfaces them. Zero manual setup. Currently: agent guesses, gets confused, tries to change models itself. |
| P14 | Retain/replace/merge/delete map for 491 files | Deliverable 7 in ChatGPT brief v3. Awaiting ChatGPT deep research output. |

---

## Edge Cases (E1-E25)

| ID | Description | Severity | Resolution |
|----|-------------|----------|------------|
| E1 | Agent skips research, jumps to implementation | Critical | Gate enforces search-first (D7). |
| E2 | Agent writes closeout but no one reads it | Medium | Plan gate (D2) is primary. |
| E3 | CONFIRM_REQUIREMENTS duplicates RESEARCH_DECISION | Medium | Need to merge or differentiate. |
| E4 | Two reworkable gates can launder rework cap | High | Need single rework counter. |
| E5 | Agent fakes compliance with ceremonial harness | Critical | Need independent evaluator. |
| E6 | Scope creep during planning | High | Every decision carries scope creep warning (D4). |
| E7 | Weak models trapped at gates | High | Task-typed router (P2) + confidence thresholds fix this. |
| E8 | Docs go stale, human loses control | Medium | Doc health monitoring needed. |
| E9 | Agent doesn't know what "done" looks like | High | Expected outcome approved before work (D2). |
| E10 | No scribe/librarian — knowledge lost across sessions | Critical | See PA1. This index is the interim fix. |
| E11 | Agent validates own reasoning | High | Independent evaluator as standard. |
| E12 | Prose-only failure mode | Medium | Metrics must be machine-checkable. |
| E13 | Self-enforcement hook without escape hatch | High | **Resolved.** Pattern written (pat-hermes-001). |
| E14 | Risk-tiered review overcorrection | Medium | Production = exception-only. See P3. |
| E15 | Missing model routing in architecture | Medium | **Resolved.** See P4. |
| E16 | No framework has automatic confidence scoring | Opportunity | Our opportunity. |
| E17 | No framework detects scope creep during planning | Opportunity | Our opportunity. |
| E18 | No framework provides structured decision support | Opportunity | Our opportunity. |
| E19 | Hermes restart = blank slate | Critical | See P9. Task state file = cheapest fix. |
| E20 | Mem0 v2.0.10 embedder bug | High | See P8. Fix: upgrade to v2.0.12+. |
| E21 | `ninerouter-aux` capped at 2 but should be ungated | Medium | Code bug. In model_dispatch.py:86-101. See P12. |
| E22 | `NINEROUTER_MAX_CONCURRENCY=2` should be 3 for paid lane | Medium | Code bug. In model_dispatch.py:86. See P12. |
| E23 | Free 9r-* and ag/* models not wired into _TIER_ENV | Medium | Code bug. Free strong models unavailable. See P12. |
| E24 | `hy3`, `nemotron-ultra`, `gemma-4`, `gpt-oss-120b` not in executor registry | Medium | Code bug. Free frontier models can't fan out. See P12. |
| E25 | `model_availability.json` stale (July 15), missing opencode-zen | Low | Code bug. Agent gets wrong info. See P12. |

---

## Open Research Questions (Q1-Q10)

| ID | Question | Status |
|----|----------|--------|
| Q1 | How do production systems handle HITL gates without trapping? | **Answered.** Confidence thresholds (85-92% auto, 8-15% human). Budget hard stops. |
| Q2 | Can skills be made enforceable? | **Answered.** Yes, via separate runtime interceptor (Runtime Authority pattern). Skills = advisory, enforcement = separate layer. |
| Q3 | ChatGPT deep research architecture? | **Answered.** ReAct loop, adaptive depth, not fixed phases. Two-tier stopping (coverage + budget). |
| Q4 | Task-typed routing production patterns? | **Answered.** Dominant pattern (LangGraph, StackAI, Vercel, CrewAI). |
| Q5 | Model routing per phase? | **Answered.** 50-70% cost reduction. Cheap=router, frontier=planning, mid=execution. |
| Q6 | Cortex vs production AI harnesses? | **Answered.** 6 critical gaps found. See CORTEX-RESEARCH-REVIEW. |
| Q7 | What should ChatGPT produce? | **Answered.** Brief v3 written with 9 deliverables + success/fail criteria. |
| Q8 | Does task-typed router invalidate fixed state machine? | **Answered.** Yes — P2 resolved. |
| Q9 | Mem0 context injection viable? | **Answered.** Bug confirmed. Alternatives mapped (Graphiti, Letta, Zep). No standard hardware detection. |
| Q10 | Session recovery patterns? | **Answered.** LangGraph checkpointers = gold standard. Agent File (.af) = closest standard. |

---

## Parked Items (PA1-PA7)

| ID | Item | Revisit When |
|----|------|--------------|
| PA1 | Scribe/librarian capability | After architecture is frozen. This capability reads transcripts and appends to durable docs automatically. |
| PA2 | Doc health monitoring system | After doc format is stable. |
| PA3 | Confidence scoring calibration (0.7 threshold) | After first production runs with gate enforcement. |
| PA4 | Performance impact of hooks (+50-100ms/tool call) | After hooks deployed. |
| PA5 | Escalation path when hooks block legitimate operations | After hooks deployed. |
| PA6 | Scribe as structural capability | Same as PA1. |
| PA7 | GrokToCrawl as MCP server | After P6 resolved (done). |

---

## Meta-Problems (M1-M8)

| ID | Problem | Impact | Fix |
|----|---------|--------|-----|
| M1 | Knowledge lost across sessions | Every session starts from scratch | This index (interim) + scribe (PA1) |
| M2 | Agent validates own reasoning | Self-review misses what independent review catches | Independent evaluator as standard |
| M3 | Closeout is not automatic | Only happens when user asks | Hook or cron triggers closeout |
| M4 | No tracking of decisions | Decisions get re-litigated | This document |
| M5 | Architecture docs are prose-only | Can't machine-check compliance | Machine-checkable metrics needed |
| M6 | Research sufficiency is self-determined | Builder decides if "enough" — conflict of interest | Three-authority model (D14) |
| M7 | **Duplicate handoff files** — every session creates new docs instead of updating one index | Agent has to search multiple places to find context. Defeats the purpose of having durable docs. | **This document is the fix.** Do NOT create new handoff docs. Update THIS file. If you find a new doc, index it here and note its path. |
| M8 | **Brain artifacts not indexed** — the canonical brain has dozens of critical docs (model tier list, scorecard, tiered lifecycle, fable doc) that agents don't know exist | Agent can't find things that already exist. Recreates existing work. 12+ hours wasted this session because fable doc wasn't surfaced. | **Brain Artifact Map below** — every critical doc in both repos listed with path and one-line description. |

---

## Brain Artifact Map

**THESE ARE THE ONLY PATHS YOU NEED.** Do not search randomly. Do not create new docs. If you need something, check here first.

### Canonical Brain (read-only): D:\claude\stupidly-simple-cortex\

| What | Path | Description |
|------|------|-------------|
| Model Tier List | `docs/MODELS-TIER-LIST.md` | Complete model availability table with benchmarks, costs, roles. **The onboarding table.** |
| Model Roster | `docs/MODEL-ROSTER-9router-openrouter.md` | 317 models on 9router, 23 free on OpenRouter. Model IDs only. |
| Harness Scorecard | `docs/HARNESS-SCORECARD-CONSOLIDATED.md` | Honest measured-vs-aspirational assessment. **25% aspirational.** Fact-checked and corrected. |
| Durable Artifacts Index | `docs/DURABLE-ARTIFACTS-INDEX.md` | Find-by-intent map into the brain. If you need an oracle, checker, or tool — start here. |
| Tiered Lifecycle Pipeline | `docs/research/DESIGN-tiered-lifecycle-pipeline-2026-07-06.md` | 9-stage routing design with per-stage model tier + reasoning effort. The cost optimization design. |
| Knowledge Escalation | `docs/harness/KNOWLEDGE-ESCALATION.md` | ChatGPT output. Three-authority sufficiency model. Decision-based stop rule. |
| Fable Redesign Doc | `docs/design/cortex-local-redesign-contracts-fable.md` | L0/L1/L2 enforcement ladder. The doc we spent 12 hours not finding. Contains the hook design we rebuilt worse. |
| e2e Success/Failure Spec | `docs/design/e2e-success-failure-spec.md` | The rejected delivery gate. Agents can fake compliance with "ceremonial harness." |
| Architecture Map | `docs/ARCHITECTURE.md` or `docs/hermes/ARCHITECTURE.md` | What's where in the codebase. |
| Phase Gates | `docs/PHASE-GATES.md` | State machine phase definitions and gate semantics. |
| Model Roles | `docs/MODEL-ROLES.md` | Which model does what (driver, worker, reviewer, judge). |
| Capability Status | `docs/harness/CAPABILITY-STATUS.md` | What's built, what's aspirational. |
| Deep Research Design | `docs/DEEP-RESEARCH-DESIGN.md` | Original deep research architecture design. |
| State Engine (source) | `cortex_core/state_engine.py` | The state machine. 2147 lines. Event-sourced on SQLite. |
| MCP Server (source) | `cortex_core/mcp.py` | 3074 lines, 30 MCP tools. Gate at line 956 (write server only). |
| Model Dispatch (source) | `cortex_core/model_dispatch.py` | Per-tier model routing. Has 7 bugs (see E21-E25). |
| Fanout (source) | `cortex_core/fanout.py` | Homogeneous best-of-N parallel execution. |
| Mission Driver (source) | `cortex_core/mission_driver.py` | Heterogeneous decomposition + parallel workers with receipts. |
| Scribe (source) | `.cortex/scripts/scribe.py` | Auto-audit from transcripts. |
| Research Sufficiency (source) | `cortex_core/research_sufficiency.py` | Receipt system with source tracking. |
| Enforcement Hook (source) | `hermes-plugin/cortex-assured-driver/__init__.py` | Pre-tool gate with escape hatch. 19 tests. |
| Enforcement Tests | `hermes-plugin/cortex-assured-driver/tests/test_plugin.py` | 19/19 passing. |
| What Cortex Is | `docs/WHAT-CORTEX-IS.md` | Definition of Cortex's purpose. |
| Brain Decision Log | `docs/DECISION-LOG.md` | The brain's own historical decision log. |
| Brain Start Here | `docs/harness/START-HERE.md` | The brain's own orientation doc. Read this if lost. |
| Production Reference Model | `docs/harness/PRODUCTION-REFERENCE-MODEL.md` | What production looks like — the target. |
| Harness Contracts | `docs/harness/CONTRACTS.md` | Contract definitions for the harness. |
| Runtime Map | `docs/harness/RUNTIME-MAP.md` | What runs where at runtime. |
| Current State Automation | `docs/harness/CURRENT-STATE-AUTOMATION.md` | What's automated vs manual right now. |
| Expected Behavior | `docs/harness/EXPECTED_BEHAVIOR.md` | What the harness should do in each scenario. |
| Cortex Routes & Ownership | `docs/CORTEX-ROUTES-AND-OWNERSHIP.md` | Which routes exist, who owns what. |
| Operating Plan | `docs/OPERATING-PLAN.md` | Current operating plan. |
| Roadmap | `docs/ROADMAP.md` | Project roadmap. |
| Hermes Roadmap | `docs/hermes/ROADMAP.md` | Hermes integration roadmap. |
| Phase Gates (brain) | `docs/PHASE-GATES.md` | State machine phase definitions and gate semantics. (Already indexed above.) |
| Promotion State Machine | `docs/PROMOTION-STATE-MODEL.md` | How artifacts get promoted through quality tiers. |
| Gap Closure Plan | `docs/GAP-CLOSURE-PLAN.md` | Plan for closing known gaps. |
| Compute Infrastructure | `docs/COMPUTE-INFRA.md` | Compute resources, GPU hosts, model serving. |
| Gravebuster Layout | `docs/GRAVEBUSTER-LAYOUT.md` | Docker execution host layout (Tailscale SSH target). |
| Anti-Distillation | `docs/COMPLIANCE-ANTI-DISTILLATION.md` | Anti-distillation compliance — prevents model output leakage. |
| MCP Context Budget | `docs/MCP-CONTEXT-BUDGET.md` | How context budget is managed across MCP tools. |
| Trace Privacy | `docs/TRACE-PRIVACY-POLICY.md` | What gets traced and what doesn't — privacy boundaries. |
| Features List | `docs/FEATURES.md` | Feature inventory. |
| Flywheel Status | `docs/FLYWHEEL-STATUS.md` | Current state of the self-improvement flywheel. |
| Self-Improvement Design | `docs/EVAL-FLYWHEEL-PLAN.md` | The evaluation flywheel design. |
| Generative Oracle Design | `docs/GENERATIVE-ORACLE-DESIGN.md` | How generative oracles work. |
| Objective Gold Catalog | `docs/OBJECTIVE-GOLD-CATALOG.md` | Gold-standard evaluation catalog. |
| Objective Lanes | `docs/OBJECTIVE-LANES.md` | 5,804 records across 29 objective evaluation lanes. |
| Ingest Spec | `docs/INGEST-SPEC.md` | Document ingestion pipeline spec. |
| Ontology Retrieval | `docs/ONTOLOGY-RETRIEVAL-SPEC.md` | How ontology-based retrieval works. |
| SLI Scorecard Schema | `docs/SLI-SCORECARD-SCHEMA.md` | Service level indicator schema. |
| Review Provenance Preflight | `docs/REVIEW-PROVENANCE-PREFLIGHT.md` | Pre-flight checks for review provenance. |
| Git Merge Safety | `docs/GIT-MERGE-SAFETY.md` | Safe merge procedures. |
| Vague Build Harness | `docs/VAGUE-BUILD-HARNESS.md` | How to handle vague build instructions. |
| Concepts & Glossary | `docs/CONCEPTS-AND-GLOSSARY.md` | Cortex terminology reference. |
| Project Contract Template | `docs/PROJECT-CONTRACT-TEMPLATE.md` | Template for project contracts. |
| Server-Driven Pipeline | `docs/SERVER-DRIVEN-PIPELINE.md` | The server-driven pipeline design. |
| Arbitration Rigor | `docs/ARBITRATION-RIGOR.md` | How multi-model arbitration works with rigor. |
| Checkers | `docs/CHECKERS.md` | The checker system — what validates what. |
| MCP Stability Audit | `docs/research/MCP-STABILITY-AUDIT-2026-07-07.md` | Audit of MCP tool stability. |
| ADR-0001 Vector Legacy | `docs/ADR-0001-VECTOR-LEG.md` | Architecture decision record on vector legacy. |

### Brain Design Docs (docs/design/)

| What | Path | Description |
|------|------|-------------|
| Redesign Corrected Spec | `docs/design/cortex-redesign-CORRECTED-spec.md` | The CORRECTED redesign specification. |
| Redesign Reconciled Spec | `docs/design/cortex-redesign-reconciled-spec.md` | Reconciled version of the redesign. |
| Complete Feature Sweep | `docs/design/cortex-complete-feature-sweep-2026-07-13.md` | Full sweep of all features, what's built vs missing. |
| Engine as Package vs Vendoring | `docs/design/engine-as-package-vs-vendoring-2026-07-15.md` | Should the engine be a pip package or vendored? |
| Heterogeneous Decomposer Gap | `docs/design/heterogeneous-decomposer-gap-2026-07-15.md` | Gap analysis for heterogeneous task decomposition. |
| Inspect-AI Adoption Plan | `docs/design/inspect-ai-adoption-plan-2026-07-14.md` | Plan for adopting inspect-ai evaluation framework. |
| Multi-Model Arbitration | `docs/design/multi-model-arbitration-in-cortex-2026-07-13.md` | Multi-model arbitration design. |
| Gap Ledger Build Note | `docs/design/gap-ledger-v0-build-note-2026-07-14.md` | Building the gap ledger v0. |
| Durable Gap Tracking (Codex) | `docs/design/durable-gap-tracking-codex-2026-07-13.md` | Durable gap tracking design (Codex version). |
| Durable Gap Tracking (Fable) | `docs/design/durable-gap-tracking-fable-2026-07-13.md` | Durable gap tracking design (Fable version). |
| SCC Success Metrics | `docs/design/scc-success-metrics-arbitration-codex-2026-07-13.md` | Self-correction capability success metrics. |
| Trusted Runner Attestation | `docs/design/trusted-runner-attestation-2026-07-14.md` | Attestation for trusted runners. |
| OCR/TTS Ingest Lane | `docs/design/ocr-tts-ingest-lane-2026-07-14.md` | OCR and TTS document ingestion lane. |
| Wrapper Delivery Best Practice | `docs/design/wrapper-delivery-best-practice-codex-2026-07-13.md` | Best practices for wrapper delivery. |
| Ownership Provenance | `docs/design/ownership-provenance/` (5 docs) | Decision log, directive log, engineering log, transcript audit, governance authorship. |
| Salvaged File Mgmt Contract | `docs/design/salvaged-file-mgmt-contract/` (2 docs) | File management contract and manifest example. |

### Workspace (writable): D:\hermes\cortex\workspaces\hades\

| What | Path | Description |
|------|------|-------------|
| **THIS DOCUMENT** | `docs/MASTER-INDEX-AND-DECISION-LOG.md` | The single source of truth. Read this first. |
| Target Architecture v3 | `docs/CORTEX-TARGET-ARCHITECTURE-v3-2026-07-16.md` | **Active design.** Narrowing dialogue, context packets, auto-loop, proof before building, 5 gates, governance spine. Supersedes v1. |
| Target Architecture v1 | `docs/CORTEX-TARGET-ARCHITECTURE-2026-07-16.md` | Superseded by v3. |
| Alignment Review | `docs/CORTEX-ALIGNMENT-REVIEW-2026-07-16.md` | 7 gaps between v3 and prior ChatGPT work. |
| Expected Outcome | `docs/CORTEX-EXPECTED-OUTCOME-2026-07-16.md` | Human-readable expected outcome. Partially superseded by v3. |
| Research Review | `docs/CORTEX-RESEARCH-REVIEW-2026-07-16.md` | Production harness comparison. 6 critical gaps. |
| Model Discovery Problem | `docs/PROBLEM-MODEL-DISCOVERY-AND-REQUIREMENTS-CONFIRMATION-2026-07-16.md` | INIT + CONFIRM_REQUIREMENTS phases. 7 code bugs. Onboarding design. **Awaiting human approval.** |
| ChatGPT Brief v3 | `docs/CHATGPT-DEEP-RESEARCH-BRIEF-v3-2026-07-16.md` | Latest brief. 9 deliverables. Success/fail criteria. |
| ChatGPT Brief v2 | `docs/CHATGPT-DEEP-RESEARCH-BRIEF-v2-2026-07-16.md` | Superseded by v3. |
| Deep Research Handoff | `docs/CORTEX-DEEP-RESEARCH-HANDOFF-2026-07-16.md` | Original handoff. Pushed to GitHub. |
| Why Cortex Is Failing | `docs/WHY-CORTEX-IS-FAILING-2026-07-16.md` | Root cause: passive vault, not proactive brain. 12+ hours wasted. |
| Claude Opus AI Trust | `docs/CLAUDE-OPUS-AI-TRUST-AND-WHY-CORTEX-WAS-BUILT-WRONG-2026-07-16.md` | Why AI can't be trusted to build enforcement against itself. |
| KEDB Pattern | `patterns/pat-hermes-001-self-enforcement-hook-without-escape-hatch.json` | First KEDB entry. |
| HITL Research | `D:\workspace\production-hitl-research.md` | No framework ships confidence scoring, scope creep detection, or decision support. Our opportunity. |
| Three-Topic Research | `D:\workspace\research-three-topics.md` | Mem0 alternatives, session recovery, sandboxed testing. 25KB with all citations. |
| Session Findings | `D:\hermes\profiles\hades\SESSION-FINDINGS-2026-07-15.md` | Prior session findings. |

### GitHub (Pukujan/private-study-log)

| Item | Commit | Status |
|------|--------|--------|
| Handoff doc | bdf3b47 | Pushed |
| Code snapshot (491 files) | d1c6e38 | Pushed |
| Snapshot README | c489ebc | Pushed |

---

## Decision History (Chronological)

| Date | Decision | Impact |
|------|----------|--------|
| 2026-07-06 | State machine built by Claude Opus | 7-phase pipeline. "Agents can finally USE it." |
| 2026-07-07 | State machine made mandatory | Gate refusing writes unless task reached DONE. |
| 2026-07-08 | State machine turned OFF | Weak models trapped in tool-call loops. Diagnosed as "Disease B." |
| 2026-07-13 | Fable redesign doc written | L0/L1/L2 enforcement ladder designed. Never implemented. |
| 2026-07-13 | Harness scorecard consolidated | 25% aspirational. Fact-checked. Multiple corrections. |
| 2026-07-15 | ChatGPT deep research engaged | KNOWLEDGE-ESCALATION.md output. Three-authority sufficiency model. |
| 2026-07-16 | State machine gate turned ON (write only, D6) | Enforcement active after Hermes restart. |
| 2026-07-16 | Closeout + pattern written for hook gap | KEDB entry searchable. |
| 2026-07-16 | 7 alignment gaps found | Between v3 and prior ChatGPT work. |
| 2026-07-16 | P2 resolved: task-typed router | No production system uses fixed state machine. |
| 2026-07-16 | P4 resolved: model routing per phase | 50-70% cost reduction. |
| 2026-07-16 | GrokToCrawl set as default search | Config changed, env vars set. |
| 2026-07-16 | v3 architecture written | Narrowing dialogue, context packets, auto-loop, proof, governance spine. |
| 2026-07-16 | ChatGPT brief v3 written | 9 deliverables, success/fail criteria. Pushed to GitHub. |
| 2026-07-16 | Model discovery problem identified | INIT + CONFIRM_REQUIREMENTS phases proposed. 7 code bugs found. |
| 2026-07-16 | Master index comprehensively audited | All brain artifacts indexed. Duplicate handoff problem addressed (M7). |

---

## The 5 Gates (from v3 Architecture)

```
GATE 1: RESEARCH → NARROWING
  Blocked until: brain search performed with context injection
  Bypass: NONE (mandatory)

GATE 2: NARROWING → OPTION RESEARCH
  Blocked until: narrowing question asked AND human answered
  Bypass: NONE (mandatory)

GATE 3: OPTION RESEARCH → OUTCOME LOCK
  Blocked until: 2-4 options with proof, human selected/modified/rejected
  Bypass: NONE (mandatory — this IS the freeze rule)

GATE 4: OUTCOME LOCK → IMPLEMENTATION
  Blocked until: plan with phases, TDD conditions, contract gate passed
  Bypass: AUTO-APPROVE only if ALL five: serves outcome, no debt, no structure debt, reversible, in scope

GATE 5: IMPLEMENTATION → DONE
  Blocked until: auto-loop achieved expected outcome (deterministic check)
  Bypass: NONE — human NOT in this loop. Human re-enters at result review only.
```

### Risk-Tiered Gate Strictness

| Risk tier | Gates 1-3 | Gate 4 | Gate 5 |
|---|---|---|---|
| LOW (config, test, doc) | All mandatory | Auto-approve eligible | Auto-loop |
| MEDIUM (feature, refactor) | All mandatory | Human approves plan | Auto-loop |
| HIGH (architecture, security, brain) | All mandatory | Human approves plan + contract | Auto-loop, human reviews result |

---

## The Model Availability Table (from brain MODELS-TIER-LIST.md)

**This is the onboarding table.** When a new session starts, the agent should surface this.

| Model | Cost | Benchmark | Role |
|---|---|---|---|
| GLM-5.2 (current driver) | sub | AA 51.1 / SWE-Pro 62.1 / LiveBench 79.65 (best open coder) | Driver/orchestrator |
| big-pickle | free | stealth, no public bench — our test passed | Builder (user prefers over deepseek-flash) |
| laguna-m.1 (Poolside) | free | 72.5% SWE-bench Verified (225B/23B) | Builder |
| nemotron-3-ultra (NVIDIA) | free | AA Index 48 — top US open-weight, 200k ctx | Reviewer |
| deepseek-v4-flash-free | free | DeepSeek line, fast tier | Worker |
| qwen3-coder:free | free | Qwen coder line | Worker |
| llama-3.3-70b:free | free | Solid open mid-tier | Worker (paced) |
| gemma-4-31b:free | free | AA Index 39 | Worker |
| north-mini-code (Cohere) | free | Limited public bench | Worker |
| tencent/hy3:free | free | No public bench — test passed | Worker |
| aux (9router combo) | free | Round-robin of free models | Bulk/variable |
| opencode-zen (big-pickle) | free | Shares opencode accounts | Default free lane |
| ninerouter-aux | free | Round-robin | **Should be ungated** (see E21) |

**Full list:** `D:\claude\stupidly-simple-cortex\docs\MODELS-TIER-LIST.md` (125 lines)
**9router catalog:** `D:\claude\stupidly-simple-cortex\docs\MODEL-ROSTER-9router-openrouter.md` (317 models)

**Rule in force:** No paid 9router models (only its free 25).

---

## Scorecard Summary (from brain HARNESS-SCORECARD-CONSOLIDATED.md)

**25% of Cortex is aspirational, not measured.** Key facts:

| Metric | Value | Status |
|---|---|---|
| Retrieval nDCG@5 | 0.462→0.650 | Measured (n=17, draft queries) |
| Chunk recall@5 | 0.467→0.733 (two-step) | Measured |
| Judge Cohen's κ (rubric v2) | Haiku/Sonnet 0.92, GLM 0.70, 4B regressed 0.52 | Measured |
| Objective lanes | 5,804 records / 29 lanes | Measured (catalog) |
| Frozen tests | 41 (not 48 — prose error) | Measured |
| Cross-val (BFCL) | 99.93% | Measured (report-level) |
| SCC (self-correction capability) | Mostly aspirational | **Not measurable yet** |
| Tool-calling rate (GLM) | 0.833 (not 0.92 — prose error) | Measured |
| Family bias | Unresolved | Pending objective third-party gold |

**Full scorecard:** `D:\claude\stupidly-simple-cortex\docs\HARNESS-SCORECARD-CONSOLIDATED.md`

---

## What ChatGPT Deep Research Is Producing (Brief v3)

9 deliverables, each with specific file/function references:

1. **Context Packet System Design** — how chunks are structured, stored, injected
2. **Narrowing Dialogue Design** — question generation, Q&A recording, "done enough" criteria
3. **Impact Matrix with Proof** — proof chain sources, UNPROVEN labeling
4. **Auto-Loop Design** — IMPLEMENT→REVIEW loop with deterministic checks and budget caps
5. **Forced Gates Integration** — 5 gates in state_engine.py with specific function/line references
6. **Governance Loop Documentation** — MCP bloat → narrowing dialogue arc as constraint
7. **Retain/Replace/Merge/Delete Map** — for every major module in cortex_core/
8. **MAPE-K as Optimization Only** — monitors, analyzes, plans, executes — no scope expansion
9. **Human-Readable Output Layer** — result not document, plain language, technical on request

**Success criteria:** 12 conditions. **Failure criteria:** 11 conditions. Both in `docs/CHATGPT-DEEP-RESEARCH-BRIEF-v3-2026-07-16.md`.

---

## Update Protocol

1. **When a decision is made:** Add to Frozen Decisions (this doc), move from Pending, AND close the corresponding GitHub issue.
2. **When a problem is found:** Create a GitHub issue with appropriate labels, AND add it to Edge Cases in this doc.
3. **When research returns:** Update Open Research Questions status in this doc, AND comment on the related GitHub issue with findings.
4. **When something is parked:** Create a GitHub issue labeled "parked" with revisit condition, AND add to Parked Items in this doc.
5. **When a meta-problem is identified:** Create a GitHub issue labeled "meta-problem", AND add to Meta-Problems in this doc.
6. **Never batch updates.** Do it in the session where the decision is made.
7. **Never delete entries.** Mark as superseded with a pointer to the replacement.
8. **Never create a new handoff doc.** Update THIS file. (M7)
9. **When you discover a brain artifact not in the Brain Artifact Map:** Add it here immediately.
10. **Two systems, one source of truth:** This doc = context + architecture + brain map. GitHub issues = status tracking + labels + search. When an item resolves, close the issue. When context changes, update this doc.

---

## The Real Problem (Statement of Purpose)

> "Human never knows or verifies what the last outcome should look like. Therefore human has never really been in control."

This document exists so that:
- Every session reads ONE document and knows everything
- Decisions aren't re-litigated
- Problems don't get re-discovered
- Brain artifacts don't get lost
- No new handoff docs are created (M7)
- The human can ask "what's unsolved?" and get an immediate, complete answer
- The agent doesn't need to be "smart" — it needs to be **structured**

The agent is only smart within a session because the human trains it. Structure survives resets. Intelligence doesn't.
