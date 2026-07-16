# ChatGPT Deep Research Brief v3 — Cortex Narrowing Loop Architecture

## Date: 2026-07-16
## Repo: github.com/Pukujan/private-study-log
## Path: cortex-snapshot/ (491 source files as plain text)

---

## What You're Looking At

Cortex is a knowledge engineering system built to make AI agents reliable. 491 files, 135 Python modules, a 7-phase state machine, research sufficiency receipts, a Hermes enforcement hook, audit logs, KEDB pattern system, multi-agent fan-out, and a scribe that auto-writes audits from transcripts.

It was built over 12 days by multiple AI agents (Claude Opus, Codex, GLM-5.2) with minimal human review. The codebase grew through uncontrolled expansion — exactly the failure mode it was designed to prevent.

This is the consolidation moment. But more than that: it's a **redesign of how humans and agents interact before any work begins.**

---

## What Changed From v2

v2 asked for a consolidation plan: retain/replace/merge/delete the existing 491 files. That's still needed (deliverable 7).

But the user reviewed v2 and fundamentally changed the interaction model. The old model: agent researches → presents decision matrix → human picks → agent builds → agent presents closeout draft → human reviews.

The new model: **narrowing dialogue → locked outcome → autonomous execution → result review → optimization only**.

The key differences:

1. **Agent asks questions BEFORE presenting options.** Not "here are 4 paths, pick one." Instead: "here's what I found, here's what I need to know from you to narrow this down." Multiple rounds possible. Always plain language.

2. **Every search gets smart context injection.** Agents don't search blindly. A context packet — built incrementally from the narrowing dialogue — feeds every search and research step. This uses existing subagent/fan-out infrastructure.

3. **Options must be researched, not guessed.** The impact matrix doesn't show estimates. It shows proof: pattern matches from audit logs, mechanism validation from codebase analysis, scope precedents from history, failure modes from KEDB.

4. **No closeout draft for human review.** Human sees the RESULT — what was actually built — not a document about it. The audit log writes automatically via the scribe. The human reviews the thing, not the paperwork.

5. **Auto-loop until outcome is met.** Once the human locks the outcome, the agent works autonomously through phases until the outcome is achieved. Human is NOT in the implementation loop. They only re-enter if the result doesn't satisfy.

6. **MAPE-K is optimization only.** No scope expansion. No new features. Just: make what exists faster, safer, more adopted.

7. **Governance loop is the spine.** The architecture's origin story is MCP bloat → lazy loading → progressive disclosure → agent wrapper → state machine → multi-agent fan-out → scribe → context injection → narrowing dialogue. Every new capability must fit this governance spine or it's a regression.

---

## The Expected Outcome (What We Want From You)

### 1. Context Packet System Design

The smart context injection layer. This is the hardest piece and the most critical.

**What we need:**
- How context chunks are structured (intent, narrowing Q&A, research findings, prior context)
- How they're stored and retrieved (existing brain search? new mechanism?)
- How they're injected into every search/research call
- How the context packet updates after each narrowing exchange
- How it leverages existing fan-out/subagent infrastructure (`fanout.py`, `decomposer.py`, `mission_driver.py`)
- How it uses existing brain search + audit logs + KEDB patterns as source material
- How subagents parallelize research with injected context

**What exists to build on:**
- `cortex_core/search.py` — BM25 + ontology RRF search
- `cortex_core/fanout.py` — homogeneous best-of-N parallel execution
- `cortex_core/mission_driver.py` — heterogeneous decomposition + parallel workers with receipts
- `cortex_core/decomposer.py` — manifest validation, spawn gate
- `.cortex/scripts/scribe.py` — audit from transcripts
- `cortex_core/research_sufficiency.py` — receipts with source tracking

**Rejected output:**
- A new search engine (we have one)
- A new context window management system (that's the host's job)
- Vague "use embeddings" recommendations without specific implementation

### 2. Narrowing Dialogue Design

**What we need:**
- How the agent generates narrowing questions (what triggers a question, what sources inform it)
- How Q&A is recorded in the context packet
- When narrowing is "done enough" to move to option research
- How many rounds are typical (1? 3? variable?)
- How the agent handles "I don't know" or "that's not what I meant" from the human
- The human-facing format (plain language, multiple choice, no technical artifacts)

**What exists to build on:**
- `cortex_core/state_engine.py` — phase transitions, phase legal tools
- `cortex_core/plane2_driver.py` — drives model through chart, one phase at a time
- `cortex_core/model_dispatch.py` — per-tier model routing

**Rejected output:**
- A chatbot interface design (the host handles the conversation surface)
- Fixed question templates (questions must be dynamically generated from research)

### 3. Impact Matrix with Proof

**What we need:**
- How proof of viability is established before building
- What sources are queried: audit log (past similar work), KEDB (failure modes), codebase analysis (mechanism exists?), IMPORT-MAP (dependencies available?)
- How UNPROVEN options are labeled (not hidden, honestly flagged)
- The human-facing format: plain language + numeric value/risk/cost + proof chain
- How "future-facing but grounded" works — predictions with receipts, not hallucinated confidence

**What exists to build on:**
- `cortex_core/research_sufficiency.py` — receipt system with source tracking
- Audit logs in `.cortex/projects/*/audit/`
- KEDB patterns in patterns/

**Rejected output:**
- Confidence intervals without evidence chains
- "Based on best practices" without citing specific prior work
- Options without proof (every option must have proof or UNPROVEN label)

### 4. Auto-Loop Design

**What we need:**
- How it integrates into the existing state machine (REVIEW → IMPLEMENT → REVIEW loop, not linear)
- How deterministic outcome checks work (TDD conditions, not LLM judgment)
- How scope lock prevents expansion (can't add mechanisms/files beyond approved plan)
- How budget caps work (max iterations, max time per phase)
- What happens when outcome CAN'T be met within scope (stop and ask human, don't expand)
- How the scribe auto-writes the audit log during the loop (zero ceremony)

**What exists to build on:**
- `cortex_core/state_engine.py` — transition logic, phase ordering
- `cortex_core/plane2_driver.py` — drives model through phases
- `.cortex/scripts/scribe.py` — auto-audit from transcripts
- `cortex_core/receipts.py` — server-owned verdicts, per-worker receipts

**Rejected output:**
- LLM-as-judge for outcome checking (deterministic only)
- Infinite loops without budget caps
- Scope expansion "if needed" (never expand, always stop and ask)

### 5. Forced Gates Integration

**What we need:**
- How Gates 1-5 integrate into `state_engine.py` (which functions, which transitions)
- How the Hermes hook (`cortex-assured-driver`) enforces each gate
- Which existing functions need modification (cite file:line)
- What new functions/states are needed
- How the state machine chart changes (show the new topology)

**The five gates:**
```
Gate 1: RESEARCH → NARROWING (search performed with context injection)
Gate 2: NARROWING → OPTION RESEARCH (human answered narrowing questions)
Gate 3: OPTION RESEARCH → OUTCOME LOCK (human selected option, outcome locked)
Gate 4: OUTCOME LOCK → IMPLEMENTATION (plan + contract gate passed)
Gate 5: IMPLEMENTATION → DONE (auto-loop achieved expected outcome)
```

**What exists to build on:**
- `cortex_core/state_engine.py` — existing 7-phase chart, transition logic
- `hermes-plugin/cortex-assured-driver/__init__.py` — pre-tool gate, escape hatch
- `cortex_core/plane2_driver.py` — phase coercion, "skipping not expressible"

**Rejected output:**
- Redesigning the state machine from scratch (extend it)
- LLM-based gate decisions (deterministic checks only)
- Gates that can be silently bypassed (all bypasses are logged)

### 6. Governance Loop Documentation

**What we need:**
- The full MCP bloat → lazy loading → progressive disclosure → wrapper → state machine → fan-out → scribe → context injection → narrowing dialogue arc, documented as a constraint
- How the contract gate checks for governance regression (does a new capability re-introduce MCP bloat? bypass the state machine? skip the scribe?)
- How this arc becomes a built-in duty of Cortex itself (Cortex must prevent its own scope creep, not just user projects')

**What exists to build on:**
- `ARCHITECTURE.md` — existing architecture map
- `HANDOFF.md` — governance wrapper handoff, LEGACY_UNASSURED correction
- `.cortex/protocol/STATE-MACHINE.md` — progressive disclosure twin
- `MULTIAGENT.md` — driver + workers pattern, zero governance ritual
- `README.md` — boundary: thin transport, lazy skills, no duplication

**Rejected output:**
- Treating governance as a docs exercise (it must be enforced in code)
- Proposing mechanisms that violate the governance spine

### 7. Retain / Replace / Merge / Delete Map

For every major module in `cortex_core/` and `hermes-plugin/`, classify as:
- **RETAIN** — works, is used, no duplication
- **REPLACE** — superseded by another mechanism, migrate and delete
- **MERGE** — duplicates another mechanism, combine into one
- **DELETE** — dead code, unused, or failed experiment

Use `IMPORT-MAP.json`, `DIRECTORY-TREE.txt`, and source files to make this determination. Cite specific files and line evidence.

### 8. MAPE-K as Optimization Only

**What we need:**
- What MAPE-K monitors (adoption rate, efficiency, failure rate) — NOT new feature opportunities
- How it analyzes waste, fragility, adoption friction
- What it plans: optimization (remove redundant steps), hardening (fix edge cases), adoption (nudge bypassing agents) — NOT new mechanisms
- How it executes: human decides, under scope, no expansion
- How calibration data feeds back to future impact matrices (cost estimates, scope creep predictions, value predictions)
- Where it lives in the codebase

**Rejected output:**
- MAPE-K suggesting new features or scope expansion
- MAPE-K as a building loop (it's a tuning loop)

### 9. Human-Readable Output Layer

**What we need:**
- The result presentation format (human sees what was built, not a document about it)
- The audit log format (available, human-readable, but NOT the primary output)
- The narrowing question format (plain language, multiple choice)
- The impact matrix format (numbers + plain language + proof)
- The "not satisfied" follow-up format (numeric + plain language options with proof)
- Doc health monitoring (stale detection, verification flags)
- How technical elaboration is available on request without being default

**Rejected output:**
- 12,000-word closeout documents
- JSON dumps as human-facing output
- Decision trees or architecture diagrams as default view

---

## What We Do NOT Want (Rejected Outputs)

1. **A new project.** Consolidate and extend existing code. If you propose a new module, show exactly what existing code it replaces or extends.

2. **A reference architecture.** We have 5 design docs already. We need an implementation plan with specific file references.

3. **Vague recommendations.** "Consider implementing human review" is useless. "Add NARROWING state between RESEARCH and PLAN in state_engine.py, gate via _transition_blocked() at line X, enforce via cortex-assured-driver pre_tool_hook" is useful.

4. **Duplicating existing mechanisms.** Before proposing any new mechanism, search the codebase. If it exists, extend it.

5. **Prose-only specifications.** Every recommendation must reference specific files, functions, and line numbers from the snapshot.

6. **Ignoring the scorecard.** 25% of Cortex is aspirational. If you recommend something that depends on aspirational capabilities, label it.

7. **Redesigning the state machine from scratch.** Extend it. Add states, transitions, gates. Show the exact modifications.

8. **MAPE-K as a feature factory.** MAPE-K optimizes and hardens. It does not expand scope.

9. **Closeout drafts for human review.** The human sees the result, not the paperwork. The audit log writes automatically.

10. **Options without proof.** Every option in the impact matrix must have proof of viability or be labeled UNPROVEN.

11. **A 12,000-word document.** Use tables, lists, code snippets. Under 3,000 words of prose (tables/code excluded).

---

## Source Baseline Confirmation

**No Cortex or Hermes plugin source has changed after snapshot commit `d1c6e38`.** The snapshot at `cortex-snapshot/` reflects the current local state exactly. Verified by normalized diff (CRLF stripped) of `mcp.py`, `cortex-assured-driver/__init__.py`, and `tests/test_plugin.py` against live files — all three diffs are empty. Line-level recommendations against the snapshot will target current code.

**Snapshot source commit:** `28f752f0689ceaab316b23214d7ce2327391bd30` (stupidly-simple-cortex brain repo)

---

## Documents To Read First (Priority Order)

> **Note on paths:** documents at the repository root were added after the cortex-snapshot was frozen. They are NOT under `cortex-snapshot/docs/`. The paths below reflect their actual locations in the repo. Documents inside `cortex-snapshot/` are frozen source or docs that shipped with the code.

1. `cortex-snapshot/SNAPSHOT-README.md` — manifest and context
2. `CORTEX-TARGET-ARCHITECTURE-v3-2026-07-16.md` — **the target architecture (read this first, it supersedes all prior versions)**
3. `CORTEX-EXPECTED-OUTCOME-2026-07-16.md` — prior expected outcome (repository root, context only, partially superseded by v3)
4. `CORTEX-ALIGNMENT-REVIEW-2026-07-16.md` — strict review of target vs existing (repository root)
5. `CORTEX-RESEARCH-REVIEW-2026-07-16.md` — production harness research findings (repository root)
6. `CORTEX-DEEP-RESEARCH-HANDOFF-2026-07-16.md` — original handoff (repository root, context only)
7. `cortex-snapshot/docs/harness/KNOWLEDGE-ESCALATION.md` — the sufficiency contract
8. `cortex-snapshot/docs/research/DESIGN-tiered-lifecycle-pipeline.md` — the 9-stage routing design
9. `cortex-snapshot/docs/HARNESS-SCORECARD-CONSOLIDATED.md` — honest measured vs aspirational
10. `cortex-snapshot/docs/design/e2e-success-failure-spec.md` — the rejected delivery gate
11. `cortex-snapshot/IMPORT-MAP.json` — dependency map (135 modules)
12. `cortex-snapshot/DIRECTORY-TREE.txt` — full file listing
13. `cortex-snapshot/cortex_core/state_engine.py` — the state machine (most important source file)
14. `cortex-snapshot/cortex_core/mcp.py` — the MCP server (3,074 lines, needs splitting)
15. `cortex-snapshot/cortex_core/research_sufficiency.py` — receipt system
16. `cortex-snapshot/hermes-plugin/cortex-assured-driver/__init__.py` — the enforcement hook
17. `cortex-snapshot/hermes-plugin/cortex-assured-driver/tests/test_plugin.py` — 19 tests
18. `cortex-snapshot/cortex_core/fanout.py` — parallel execution engine
19. `cortex-snapshot/cortex_core/mission_driver.py` — heterogeneous decomposition
20. `cortex-snapshot/cortex_core/plane2_driver.py` — phase coercion driver
21. `cortex-snapshot/ARCHITECTURE.md` — architecture map (what's where)
22. `cortex-snapshot/HANDOFF.md` — governance wrapper handoff, LEGACY_UNASSURED
23. `cortex-snapshot/MULTIAGENT.md` — driver + workers pattern
24. `cortex-snapshot/README.md` — wrapper boundary and plane A/B split

---

## Success Criteria

Your output succeeds if:

1. A developer can take your context packet design and implement it using existing fan-out + search infrastructure
2. A developer can take your narrowing dialogue design and build the question generation + Q&A recording flow
3. A developer can take your auto-loop design and modify state_engine.py to loop IMPLEMENT → REVIEW until outcome is met
4. A developer can take your forced gates plan and implement them by modifying specific functions in state_engine.py and the Hermes hook
5. A developer can take your retain/replace/merge/delete map and execute it without ambiguity
6. Every recommendation references specific files, functions, and line numbers
7. Every recommendation passes its own contract gate (no duplicates, clear ownership, defined removal plan)
8. Aspirational capabilities are labeled explicitly
9. MAPE-K is designed as optimization-only with no scope expansion
10. The human-facing output layer is designed as "result, not document"
11. The governance loop arc is documented as a constraint on all new capabilities
12. The output is under 3,000 words of prose (tables/code excluded)

Your output fails if:

1. It proposes a new project instead of consolidating existing code
2. It duplicates mechanisms that already exist
3. It ignores the scorecard's measured-vs-aspirational distinction
4. It produces prose without specific file/function references
5. It redesigns the state machine from scratch
6. It exceeds 3,000 words of prose
7. It doesn't address the context packet system (the core ask)
8. It doesn't address the narrowing dialogue (the core interaction model)
9. It proposes MAPE-K as a feature factory instead of optimization-only
10. It presents closeout drafts for human review instead of results
11. It presents options without proof of viability
