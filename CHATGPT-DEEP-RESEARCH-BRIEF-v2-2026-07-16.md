# ChatGPT Deep Research Brief v2 — Cortex Consolidation

## Date: 2026-07-16
## Repo: github.com/Pukujan/private-study-log
## Path: cortex-snapshot/ (491 source files as plain text)

---

## What You're Looking At

Cortex is a knowledge engineering system built to make AI agents reliable. It has 491 files, 135 Python modules, a 7-phase state machine, research sufficiency receipts, a Hermes enforcement hook, audit logs, and KEDB pattern system. It has been built over 12 days by multiple AI agents (Claude Opus, Codex, GLM-5.2) with minimal human review.

The codebase has grown through uncontrolled expansion — exactly the failure mode it was designed to prevent. This is the consolidation moment.

---

## The Expected Outcome (What We Want From You)

A **grounded consolidation plan** that takes the existing 491 files and produces:

### 1. Retain / Replace / Merge / Delete Map
For every major module in cortex_core/, classify it as:
- **RETAIN** — works, is used, no duplication
- **REPLACE** — superseded by another mechanism, migrate and delete
- **MERGE** — duplicates another mechanism, combine into one
- **DELETE** — dead code, unused, or failed experiment

Use the IMPORT-MAP.json, DIRECTORY-TREE.txt, and the source files to make this determination. Do not guess — cite the specific file and line evidence.

### 2. State Machine Integration Plan
The state machine (state_engine.py) has 7 phases: SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT → DONE. The Hermes hook (cortex-assured-driver) gates writes.

We need you to design:
- How the **path synthesis** (producing 2-4 comparable paths with trade-offs) integrates as a new phase or sub-phase between RESEARCH and PLAN
- How the **human plan gate** (human approves path + expected outcome before any building) integrates as a state transition blocker between PLAN and SPEC
- How the **contract gate** (module boundaries, duplicate check, debt assessment) integrates as a gate between PLAN and SPEC (or within SPEC)
- How the **machine-decidable process check** (phase_order, closeout_fidelity, claim_faithfulness) integrates as a gate between REVIEW and CLOSEOUT
- How the **human outcome review** integrates as a gate between CLOSEOUT and DONE
- How the **scope creep warning** fires at every phase transition
- How the **sufficiency receipt** from research_sufficiency.py gates the RESEARCH → PLAN transition

Show the exact state_engine.py functions that would need modification. Show the new states/transitions in the state machine chart. Show how the Hermes hook enforces each new gate.

### 3. Architecture Consolidation
The codebase has known duplications:
- Standalone search gate (cortex-assured-driver) vs SEARCH_BRAIN phase (state_engine)
- Manual closeouts (disk writes) vs cortex_write_log (MCP)
- Ad-hoc patterns (JSON files) vs PatternRecord (patterns.py)
- mcp.py is 3,074 lines and should be split

Produce a target module structure that:
- Eliminates all duplications with specific migration paths
- Splits mcp.py into focused modules
- Adds the missing modules (path_synthesis.py, contract_gate.py, scope_monitor.py, mapek_loop.py, doc_health.py)
- Every module has a contract (owns, doesn't own, exposes, depends on)
- Maps to the target directory structure in CORTEX-EXPECTED-OUTCOME

### 4. Per-Stage Model Routing Implementation
The DESIGN-tiered-lifecycle-pipeline.md defines a 9-stage routing table with per-stage model tier and reasoning effort. This is designed but not implemented.

Produce:
- How to implement the routing table in the state machine (which state reads which tier/effort config)
- Where the routing config lives (yaml? python? inline in state_engine?)
- How the ceiling (max_model_class) is enforced — the e2e spec says weak models can fake compliance
- How the effort curve (front-loaded, cheap middle, paid checkpoint) is encoded

### 5. Evidence Theater Prevention
The e2e spec was rejected for freeze because "nothing anchors runtime artifacts to a trusted root." Design:
- What the trusted provenance substrate (§1.2 of e2e spec) looks like concretely
- How content fidelity checks work (files-changed vs git diff, tests-passed vs exit codes)
- How citation verification works
- How format adapters are detected and rejected

### 6. Human-Readable Output Layer
Every Cortex output today is 1,000+ words of technical prose. Design:
- The evidence pack format (what a human sees in 30 seconds)
- The decision matrix format (how paths are presented)
- The scope creep warning format
- The closeout summary format (1 paragraph + evidence)
- How technical elaboration is available on request without being the default

### 7. MAPE-K Integration
Three functions:
- Prediction calibration (post-completion)
- Auto-approve eligibility (pre-action)
- Structural health monitoring (ongoing)

For each, produce:
- What data it reads (audit log, git history, import map, scorecard)
- What it outputs (calibration numbers, trust profile, health dashboard)
- Where it lives in the codebase
- How it feeds back into the state machine (does MAPE-K output gate anything?)

---

## What We Do NOT Want (Rejected Outputs)

1. **A new project.** This is a consolidation of existing code, not a greenfield design. If you propose a new module, show exactly what existing code it replaces or extends.

2. **A reference architecture.** We have 5 design docs already. We don't need a 6th. We need a consolidation plan that says "delete this, merge that, keep this, add this" with specific file references.

3. **Vague recommendations.** "Consider implementing human review" is useless. "Add HUMAN_REVIEW state between CLOSEOUT and DONE in state_engine.py, gate via cortex_review_approve() in the Hermes hook, block transition via _transition_blocked() in state_engine.py:line X" is useful.

4. **Duplicating existing mechanisms.** Before proposing any new mechanism, search the codebase (IMPORT-MAP.json + source files) for an existing one. If it exists, extend it. If it doesn't, propose it new. The contract gate must apply to your own recommendations.

5. **Prose-only specifications.** Every recommendation must reference specific files, functions, and line numbers from the snapshot. If you can't point to the code, don't make the recommendation.

6. **Ignoring the scorecard.** The HARNESS-SCORECARD-CONSOLIDATED.md says 25% of Cortex is aspirational. If you recommend something that depends on aspirational capabilities, say so explicitly. Don't present aspirational as built.

7. **Redesigning the state machine from scratch.** The state machine exists and works (7 phases, tested end-to-end). Extend it. Don't replace it. Add states, transitions, gates. Show the exact modifications to the existing chart topology.

8. **A 12,000-word document.** The expected output is a consolidation plan with specific file references, not a treatise. Use tables, lists, and code snippets. If a section exceeds 500 words, it's probably wrong.

---

## Documents To Read First (Priority Order)

1. `cortex-snapshot/SNAPSHOT-README.md` — manifest and context
2. `cortex-snapshot/docs/CORTEX-EXPECTED-OUTCOME-2026-07-16.md` — the target (this document's companion)
3. `cortex-snapshot/docs/CORTEX-ALIGNMENT-REVIEW-2026-07-16.md` — strict review of target vs existing
4. `cortex-snapshot/docs/CORTEX-RESEARCH-REVIEW-2026-07-16.md` — production harness research findings
5. `cortex-snapshot/docs/CORTEX-DEEP-RESEARCH-HANDOFF-2026-07-16.md` — original handoff (context only)
6. `cortex-snapshot/docs/harness/KNOWLEDGE-ESCALATION.md` — the sufficiency contract
7. `cortex-snapshot/docs/research/DESIGN-tiered-lifecycle-pipeline.md` — the 9-stage routing design
8. `cortex-snapshot/docs/HARNESS-SCORECARD-CONSOLIDATED.md` — honest measured vs aspirational
9. `cortex-snapshot/docs/design/e2e-success-failure-spec.md` — the rejected delivery gate
10. `cortex-snapshot/IMPORT-MAP.json` — dependency map (135 modules)
11. `cortex-snapshot/DIRECTORY-TREE.txt` — full file listing
12. `cortex-snapshot/cortex_core/state_engine.py` — the state machine (most important source file)
13. `cortex-snapshot/cortex_core/mcp.py` — the MCP server (3,074 lines, needs splitting)
14. `cortex-snapshot/cortex_core/research_sufficiency.py` — receipt system
15. `cortex-snapshot/hermes-plugin/cortex-assured-driver/__init__.py` — the enforcement hook

---

## Success Criteria For Your Output

Your output succeeds if:

1. A developer can take your retain/replace/merge/delete map and execute it without ambiguity
2. A developer can take your state machine integration plan and implement it by modifying specific functions in state_engine.py
3. A developer can take your module structure and create the directories + contract headers
4. The plan references specific files, functions, and line numbers — not vague descriptions
5. Every recommendation passes its own contract gate (no duplicates, clear ownership, defined removal plan)
6. Aspirational capabilities are labeled explicitly
7. The output is under 3,000 words total (tables and code snippets don't count toward the word limit)

Your output fails if:

1. It proposes a new project instead of consolidating existing code
2. It duplicates mechanisms that already exist in the codebase
3. It ignores the scorecard's measured-vs-aspirational distinction
4. It produces prose without specific file/function references
5. It redesigns the state machine from scratch instead of extending it
6. It exceeds 3,000 words of prose (tables/code excluded)
7. It doesn't address the state machine integration (the core ask)
