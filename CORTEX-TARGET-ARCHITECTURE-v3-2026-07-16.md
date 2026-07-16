# Cortex Target Architecture v3 — The Narrowing Loop

## Date: 2026-07-16
## Status: Active design target. Supersedes v1/v2.

---

## The Core Shift

Previous versions had the agent research, then present a decision matrix, then wait for the human to pick. That's too late. The human's intent isn't understood well enough at that point.

The new model is a **narrowing dialogue**: the agent researches enough to ask smart questions, the human answers, the agent researches deeper, and only THEN presents researched options with proven outcomes. The human never sees a matrix built on guesses.

---

## The Complete Flow

```
1. HUMAN STATES INTENT (vague is fine)
   "I want to fix AI reliability"
   "I want better search"
   ↓
2. AGENT: INITIAL RESEARCH
   Searches brain for prior work, patterns, failures
   Researches external context
   BUT — every search/research step gets SMART CONTEXT INJECTION
   (Cortex injects what the agent is looking for and why,
    based on the intent + all prior conversation)
   ↓
3. AGENT → HUMAN: NARROWING QUESTIONS
   1 question or several. Always plain language.
   Always prioritizing human understanding.
   "When you say 'AI reliability,' do you mean:
    A) the agent skips research and rebuilds existing solutions
    B) the agent produces work that doesn't match what you wanted
    C) the agent hallucinates or makes things up
    D) something else?"
   ↓
4. HUMAN ANSWERS
   ↓
5. AGENT: DEEPER RESEARCH
   Now researches with the narrowed context
   Options themselves are researched — not guessed
   Smart context injection updates for each search based on
   the narrowing conversation so far
   ↓
6. AGENT → HUMAN: IMPACT MATRIX + EXPECTED OUTCOME
   Each option has:
   - What it produces (plain language)
   - Numeric value/risk/cost/scope-creep %
   - PROOF that it will work — before building, not after
   - What it unlocks and forecloses
   - Future-facing but grounded (no hallucination)
   ↓
7. HUMAN: picks, modifies, or rejects
   ↓
8. OUTCOME LOCKED. Nothing built yet.
   ↓
9. PROCEDURE BEGINS:
   - Research again if anything is missing
   - Plan: break into phases, mini-phases, success conditions, TDD
   - AUTO-LOOP: implement → check against expected outcome →
     if not met, continue — automatically, no human needed
   - Stays under scope. Stays under outcome.
   ↓
10. DONE: audit log written AUTOMATICALLY (no human review of the log)
    Human sees the RESULT — what was actually built
    Not a document about it. The thing itself.
   ↓
11. IF NOT SATISFIED:
    Agent asks further questions
    Each decision has numeric + plain language options
    Smart, future-facing, but proven — not hallucinated
   ↓
12. ONCE FINALIZED:
    MAPE-K begins — strictly under scope:
    - Optimization (make what exists faster/cheaper)
    - Adoption loop (more agents use it)
    - Efficiency focus (less waste)
    - Hardening focus (fewer failures)
    NO scope creep. No expansion. No new features.
```

---

## Smart Context Injection (The Hard Piece — Made Easier)

### The Problem
Every search and research step needs context: what is the agent looking for, why, and what has already been found. Without this, agents search blindly and miss relevant things — exactly how things are being missed today.

### Why It's Easier Than It Looks
We already have:

1. **Subagents ready to parallelize.** Multiple agents can fan out and gather information simultaneously. The cortex-agent-wrapper's `fanout.py` + `decomposer.py` + `mission_driver.py` already do heterogeneous decomposition: split a mission into independent slices, each carrying its own receipt, fan out to parallel executors, merge/verify through the state machine.

2. **Audit logs and relevant documents can be better arranged.** The brain already has search (BM25 + ontology RRF). The scribe already writes audits from transcripts. Context can be pulled from structured audit history, not just raw search.

3. **Context packets saved in chunks.** Instead of one massive context blob, context is chunked: the intent, the narrowing Q&A, the research findings, the prior search receipts. Each chunk is independently retrievable and injectable. Only the relevant chunk gets injected into each search — not the whole conversation.

### How It Works

```
CONTEXT PACKET (built incrementally through the narrowing dialogue):

chunk_1: INTENT
  - Original human statement (verbatim)
  - Agent's interpretation (one sentence)

chunk_2: NARROWING Q&A
  - Each question asked
  - Each human answer
  - What the answer ruled in/out

chunk_3: RESEARCH FINDINGS
  - Brain search results (with receipts)
  - External research results
  - What was found, what was NOT found
  - Coverage gaps identified

chunk_4: PRIOR CONTEXT
  - Relevant audit logs from similar past work
  - Relevant patterns from KEDB
  - Relevant scope-packs from the brain

INJECTION RULE:
  - Every search/research call receives the relevant chunks
  - "Relevant" = determined by the current phase + what's being searched
  - At narrowing phase: inject INTENT + PRIOR CONTEXT (know what to ask)
  - At research phase: inject INTENT + NARROWING Q&A (know what to find)
  - At option presentation: inject ALL chunks (full context for synthesis)
  - At implementation: inject LOCKED OUTCOME + SPEC (know what to build)
```

This isn't a new search engine. It's a **context router** that sits between the narrowing dialogue and the existing search/research tools. The search engine stays the same. The context that feeds it gets smarter.

### How Subagents Power This

```
When the agent starts researching:

DRIVER (strong model, e.g. GLM-5.2)
  reads the context packet, decomposes research into parallel sub-tasks
  ├─ WORKER 1: searches brain for prior patterns matching intent
  ├─ WORKER 2: searches audit logs for similar past failures
  ├─ WORKER 3: researches external approaches/solutions
  └─ WORKER 4: searches for scope creep precedents
     ↓
  each worker drops transcript → scribe writes audit
  driver collects, merges, synthesizes
  context packet updated with findings
```

The workers are cheap (Ollama, free tiers). The driver is strong. The brain search is shared. The audit trail gets richer as you parallelize — it doesn't slow down. This is already built (`fanout.py`, `mission_driver.py`, `scribe.py`).

---

## The Governance Loop (Why This Architecture Exists)

### Origin: MCP Bloat

The original problem wasn't "AI is unreliable." It was **MCP context bloat**. When an agent has 50+ tools loaded in its context window, it:
- Loses focus on the task
- Calls the wrong tools
- Wastes tokens on tool selection overhead
- Misses relevant tools because they're buried in noise

### The Solution Chain

```
MCP BLOAT
  → LAZY LOADING (only load tools when needed)
    → PROGRESSIVE DISCLOSURE (state machine surfaces right tools per phase)
      → AGENT WRAPPER (thin transport, brain connection, discipline)
        → STATE MACHINE (prevents phase skipping)
          → MULTI-AGENT FAN-OUT (driver + cheap workers)
            → SCRIBE (auto-audit from transcripts, zero ritual)
              → CONTEXT INJECTION (smart context per search)
                → NARROWING DIALOGUE (human intent understood before work)
```

Each step solved a real problem the previous step exposed:

| Step | Problem It Solved | How |
|---|---|---|
| Lazy loading | Too many tools in context | Only load tools when the phase needs them |
| Progressive disclosure | Agent doesn't know which tool to reach for | State machine surfaces the cheapest, right tool per phase |
| Agent wrapper | Host agent context too heavy | Thin transport, brain stays remote, wrapper is portable |
| State machine | Agent skips research, jumps to implementation | Phase ordering enforced — "skipping a phase is not expressible" |
| Multi-agent fan-out | Single agent too slow for research-heavy work | Driver decomposes, cheap workers parallelize, each carries receipt |
| Scribe | Agents waste tokens writing audits instead of working | Audit generated from transcript automatically — zero ceremony |
| Context injection | Agents search blindly, miss relevant things | Context packet feeds every search with accumulated understanding |
| Narrowing dialogue | Agent builds on misunderstood intent | Human narrows intent BEFORE options are researched and presented |

### What This Means For The Architecture

The governance loop is the **spine**. Every new capability must:
1. Not re-introduce MCP bloat (stay lazy-loaded, progressive disclosure)
2. Fit into the state machine (extend phases, don't bypass them)
3. Use the fan-out pattern for research-heavy steps (driver + workers)
4. Auto-audit via scribe (no manual closeout ceremony)
5. Receive context injection (no blind searches)
6. Narrow with the human before building (no assumed intent)

If a proposed capability violates any of these, it's a governance regression — not an addition.

---

## Forced Gates (No Agent Skips What's Mandatory)

### The Gate Structure

The state machine already enforces phase ordering — "skipping a phase is not expressible" (plane2_driver.py docstring). The Hermes hook (cortex-assured-driver, 19 tests passing) gates writes until search is performed.

The new gates that need to be added:

```
GATE 1: RESEARCH → NARROWING
  Blocked until: at least one brain search has been performed
                  with context injection
  Evidence: search receipt with context packet attached
  Bypass: NONE (mandatory)

GATE 2: NARROWING → OPTION RESEARCH
  Blocked until: at least one narrowing question has been asked
                  AND human has answered
  Evidence: narrowing Q&A recorded in context packet
  Bypass: NONE (mandatory)

GATE 3: OPTION RESEARCH → OUTCOME LOCK
  Blocked until: 2-4 options presented with:
    - Numeric value/risk/cost/scope-creep per option
    - Proof of viability (research-backed, not estimated)
    - Expected outcome per option (what "done" looks like)
    - Human has selected, modified, or rejected
  Evidence: human selection recorded
  Bypass: NONE (mandatory — this IS the freeze rule)

GATE 4: OUTCOME LOCK → IMPLEMENTATION
  Blocked until: plan broken into phases with:
    - Mini-phases with success conditions
    - TDD conditions written
    - Contract gate passed (module, boundaries, duplicates checked)
  Evidence: plan + contract registered
  Bypass: AUTO-APPROVE only if ALL five:
    1. Serves approved outcome
    2. No code debt
    3. No structure debt
    4. Reversible
    5. Within scope

GATE 5: IMPLEMENTATION → DONE
  Blocked until: auto-loop has achieved expected outcome
    (measured against locked outcome, not agent's redefined goal)
  Evidence: outcome checklist all green
  Bypass: NONE — but human is NOT in this loop
    Human only re-enters at step 10/11 (result review)
```

### Risk-Tiered Gate Strictness

| Risk tier | Gates 1-3 | Gate 4 | Gate 5 |
|---|---|---|---|
| LOW (config, test patch, doc) | All mandatory | Auto-approve eligible | Auto-loop |
| MEDIUM (feature, refactor) | All mandatory | Human approves plan | Auto-loop |
| HIGH (architecture, security, brain) | All mandatory | Human approves plan + contract | Auto-loop, human reviews result |

The narrowing dialogue (Gates 1-3) is ALWAYS mandatory regardless of risk tier. The human ALWAYS narrows before options are presented. What changes by risk tier is how much the human approves before implementation and whether they see the result.

---

## Proof Before Building

### The Problem
Every previous version of Cortex presented estimates: "85% reduction in repeated work." These were guesses dressed up as metrics. The human approved based on hallucinated confidence.

### The Requirement
Every option in the impact matrix must have **proof of viability** before it's presented:

1. **Pattern match**: has this type of solution worked before? (from audit log)
2. **Mechanism validation**: does the proposed mechanism exist and function? (from codebase analysis)
3. **Dependency verification**: are all dependencies available and compatible? (from IMPORT-MAP.json)
4. **Scope precedent**: what did similar work actually cost and produce? (from audit log)
5. **Failure mode check**: how did similar approaches fail before? (from KEDB patterns)

If proof can't be established, the option is labeled `UNPROVEN` — not hidden, not removed, but honestly flagged. The human can still choose it, but they know it's a bet, not a sure thing.

### What Proof Looks Like

```
OPTION A: Proactive Injection Layer
├─ Value: 85% reduction in repeated work
├─ PROOF:
│  ├─ Pattern match: 3 prior incidents of repeated work (audit-log-1, audit-log-2)
│  ├─ Mechanism: injection trigger exists in search.py:_proactive_query() (lines 234-280)
│  ├─ Dependencies: search.py (exists), state_engine.py (exists), Hermes hook (exists)
│  ├─ Scope precedent: similar work (audit-log-3) took 8h, produced 72% reduction
│  └─ Failure modes: scope creep into search redesign (KEDB pat-hermes-001)
├─ Risk: 20% scope creep (based on 3 prior similar tasks, 1 crept)
├─ Cost: ~8 hours (based on audit-log-3 precedent, ±2h)
└─ Expected outcome: agent surfaces relevant brain content before human asks,
   measured by: injection_rate > 0 (did it fire?), relevance_score > 0.7
```

The percentages are grounded in actual history. The cost is based on precedent. The failure modes are from recorded patterns. This is not a hallucinated estimate — it's a researched prediction.

---

## The Auto-Loop

### Current State
The state machine goes IMPLEMENT → REVIEW → CLOSEOUT linearly. No loop. If the implementation doesn't meet the outcome, the agent either fakes a pass or abandons.

### Required Behavior

```
OUTCOME LOCKED (human-approved expected outcome + success conditions)
  ↓
PLAN (phases, mini-phases, TDD conditions)
  ↓
IMPLEMENT PHASE 1
  ↓
CHECK: does phase 1 output meet phase 1 success conditions?
  ├─ YES → next phase
  └─ NO → continue implementing phase 1 (no human intervention)
  ↓
IMPLEMENT PHASE 2
  ↓
CHECK: does phase 2 output meet phase 2 success conditions?
  ├─ YES → next phase
  └─ NO → continue
  ↓
... (repeat for all phases) ...
  ↓
ALL PHASES GREEN → CHECK: does total output meet expected outcome?
  ├─ YES → DONE (write audit log, show human the result)
  └─ NO → identify gap, implement fix, re-check
  ↓
DONE → human sees result
```

The human is NOT in this loop. They approved the outcome. The agent works until it hits it. Human only re-enters if the result doesn't satisfy — and even then, the agent asks smart narrowing questions, not "what do you want me to do?"

### Guard Rails
- **Scope lock**: the auto-loop cannot add mechanisms, files, or components beyond the approved plan. If the agent determines the outcome CAN'T be met within scope, it stops and asks the human — it doesn't expand scope.
- **Budget cap**: configurable max iterations or max time per phase. If exceeded, stop and report.
- **No fake passes**: the check is deterministic (TDD conditions, not LLM judgment). An agent can't self-certify.

---

## What The Human Sees (And Doesn't)

### What The Human Sees

**During narrowing (steps 3-7):**
- Plain-language questions with multiple-choice options
- Impact matrix with numeric value/risk/cost + proof
- Expected outcome per option in plain language

**After completion (step 10):**
- The RESULT itself — what was built, what changed, what it does now
- Not a 12,000-word document about the process
- Not a closeout draft for review
- The actual thing, demonstrated or shown

**If not satisfied (step 11):**
- Agent asks: "The outcome was X. You expected Y. Here are 3 options to close the gap: A) [numeric + plain language], B) [...], C) [...]"
- Each option has proof, cost, risk
- Human picks, loop continues

### What The Human Does NOT See

- The audit log (written automatically, available if they want to read it, but never presented as the primary output)
- The implementation process (the auto-loop runs without human involvement)
- Technical artifacts unless they explicitly ask ("what does Path A involve?")
- Closeout documents (the scribe writes them, the human doesn't review them)
- Decision trees or technical architecture diagrams (default view is plain language + numbers)

### Audit Log: Available, Not Presented

The audit log is still critical — it's how MAPE-K calibrates, how patterns get identified, how future agents learn. But it's **background infrastructure**, not the human-facing output. The human can read it anytime (it's human-readable, modularized, properly documented), but it's never the thing shown as "here's what we did."

---

## MAPE-K: Optimization Only, No Expansion

### What MAPE-K Does

```
AFTER outcome is finalized (step 12):

MONITOR:
  - Is the built thing being used? (adoption rate)
  - Is it performing as expected? (efficiency metrics)
  - Are there failures? (error rate, failure modes)

ANALYZE:
  - Where is waste? (slow paths, redundant calls, unnecessary steps)
  - Where is fragility? (error-prone paths, untested edge cases)
  - Where is adoption friction? (agents not using it, bypassing it)

PLAN:
  - Optimization: "This path takes 8 steps, 3 are redundant. Remove them for 37% speedup."
  - Hardening: "This path fails on edge case X. Add test + fix for 90% reliability."
  - Adoption: "3 agents bypass this gate. Add progressive disclosure nudge."

EXECUTE:
  - Human decides: optimize now or accept current state
  - NO new features. NO scope expansion. NO new mechanisms.
  - Only: make what exists faster, safer, more adopted.

KNOWLEDGE:
  - Calibration data feeds back into:
    - Cost estimates (predicted vs actual)
    - Scope creep predictions (predicted vs actual)
    - Value predictions (predicted vs actual)
  - Future impact matrices become more accurate
```

### What MAPE-K Does NOT Do

- Does NOT suggest new features
- Does NOT expand scope
- Does NOT propose new mechanisms
- Does NOT add new phases to the state machine
- Does NOT restructure the codebase

MAPE-K is a **tuning loop**, not a **building loop**. It makes what exists better. If the human wants something new, that starts a new narrowing dialogue from step 1.

---

## File Safety, Modularization, Documentation

### File Safety
- Every write goes through the state machine (cortex_write_log via MCP)
- Hermes hook gates writes until mandatory steps are complete
- Escape hatch exists but is logged (can't silently bypass)
- All writes are atomic (write-lock + idempotency)

### Modularization (Contract Gate)
Before any implementation:
```
CONTRACT: [Feature Name]
├─ Module: where this code lives
├─ Owns: what this module is responsible for
├─ Does NOT own: explicit boundaries
├─ Exposes: interface to other modules
├─ Depends on: upstream dependencies
├─ Debt assessment: LOW / MEDIUM / HIGH
├─ Duplicate check: scans codebase for existing similar mechanism
├─ Removal plan: what breaks if this is deleted
└─ GATE: APPROVED or BLOCKED
```

### Documentation (Human-Readable, Always)
- Every module has a README readable in under 60 seconds
- Every doc has last-verified date + stale threshold
- Code changes flag associated docs for re-verification
- Stale docs show "⚠ UNVERIFIED"
- No doc enters the brain without a human-readable summary at top
- Audit logs are human-readable: plain language, not JSON dumps
- Everything available to read at any time — but never forced on the human as the primary output

---

## Existing Capabilities — Honest Status

| Capability | Status | What's Built | What's Missing |
|---|---|---|---|
| Brain search (BM25 + ontology RRF) | BUILT | nDCG@5 0.650, chunk_recall@5 0.733 | Smart context injection layer |
| State machine (7 phases) | BUILT, ON, NEVER ENFORCED LIVE | state_engine.py, plane2_driver.py | Narrowing phase, auto-loop, forced gates 1-5 |
| Hermes hook | BUILT | cortex-assured-driver, 19 tests | Gate 1-5 enforcement |
| Multi-agent fan-out | BUILT | fanout.py, decomposer.py, mission_driver.py | Context packet routing to workers |
| Scribe (auto-audit) | BUILT | scribe.py writes audit from transcripts | Human-readable format for audit output |
| Research sufficiency receipts | BUILT | research_sufficiency.py, 867 lines | Wired to narrowing gate (Gate 2) |
| KEDB patterns | BUILT | PatternRecord, promotion requires ≥2 occurrences | Scope creep pattern detection |
| Lazy loading / progressive disclosure | BUILT | phase_legal_tools, tier_lookup.py | Context packet as progressive disclosure unit |
| Audit log | BUILT | Not human-readable | Human-readable format, auto-written (no ceremony) |
| Smart context injection | NOT BUILT | — | Context packet router, chunk management, injection rules |
| Narrowing dialogue | NOT BUILT | — | Question generation, Q&A recording, context packet building |
| Impact matrix with proof | NOT BUILT | — | Research-backed option presentation, proof chain |
| Auto-loop until outcome met | NOT BUILT | — | Loop in state machine, deterministic outcome check |
| Result presentation (not closeout) | NOT BUILT | — | Human sees what was built, not a document about it |
| Scope creep warning | NOT BUILT | — | Fires at phase transitions, budget caps, scope locks |
| Contract gate | NOT BUILT | — | Module/boundary/debt/duplicate check |
| MAPE-K optimization loop | NOT BUILT | — | Monitor/analyze/plan/execute under scope only |
| Doc health monitoring | NOT BUILT | — | Stale detection, verification flags |

---

## What This Solves

### The Human-Doesn't-Understand Loop (SOLVED)
Previous versions: agent does work, human can't tell if it's right, agent writes a 12,000-word doc, human can't read it, human approves because they can't evaluate, knowledge enters brain unvalidated.

New version: agent narrows with human FIRST, human picks from researched options with PROOF, agent works until outcome is met, human sees the RESULT (not the paperwork), if unsatisfied the agent asks smart questions. Human always understands because the narrowing happened before building.

### The AI-Search-Is-Dumb Loop (SOLVED)
Previous versions: agent searches blindly, misses relevant context, rebuilds existing solutions, contradicts recorded decisions.

New version: every search gets smart context injection from the accumulated context packet. Subagents parallelize research. Brain + audit logs + KEDB patterns all feed into the context. The agent searches with awareness, not blind luck.

### The Scope-Creep Loop (SOLVED)
Previous versions: agent starts with one mechanism, expands to 15, nobody notices for days.

New version: outcome is LOCKED before implementation. Auto-loop stays within scope. If outcome can't be met within scope, agent STOPS and asks — it doesn't expand. MAPE-K is optimization only, never expansion. Scope creep warnings fire at every phase transition.

### The Governance Regression Loop (PREVENTED)
The MCP bloat → lazy loading → wrapper → state machine arc exists for a reason. Every new capability must fit into this governance spine. No new mechanism that bypasses the state machine, re-introduces context bloat, skips the scribe, or searches without context injection.

---

## What ChatGPT Deep Research Should Produce

A grounded implementation plan that:

1. **Designs the context packet system** — how chunks are structured, stored, retrieved, and injected into every search/research call. How it leverages existing fan-out/subagent infrastructure. How it uses existing brain search + audit logs + KEDB patterns as source material.

2. **Designs the narrowing dialogue** — how the agent generates questions, how Q&A is recorded, how context packet updates after each exchange, when narrowing is "done enough" to move to option research.

3. **Designs the impact matrix with proof** — how proof of viability is established before building, what sources are queried (audit log, KEDB, codebase, IMPORT-MAP), how UNPROVEN is labeled, what the human-facing format looks like.

4. **Designs the auto-loop** — how it integrates into the existing state machine (REVIEW → IMPLEMENT → REVIEW loop), how deterministic outcome checks work, how scope lock prevents expansion, how budget caps work.

5. **Designs the forced gates** — how Gates 1-5 integrate into state_engine.py and the Hermes hook, which existing functions need modification, what new functions are needed.

6. **Designs the governance loop integration** — how the MCP bloat → lazy loading → wrapper → state machine arc is documented and enforced as a constraint on all new capabilities. How the contract gate checks for governance regression.

7. **Produces the retain/replace/merge/delete map** — for the existing 491 files, classify each major module. How existing mechanisms (fan-out, scribe, receipts, state machine) map to the new architecture.

8. **Designs MAPE-K as optimization-only** — what it monitors, how it analyzes, what it plans, how it executes — all strictly under scope. How it feeds calibration data back to future impact matrices.

9. **Designs the human-readable output layer** — result presentation format (not closeout), audit log format (available, not primary), doc health monitoring, the "human sees the thing, not the document" principle.

Every recommendation must reference specific files, functions, and line numbers from the cortex-snapshot. Aspirational capabilities labeled explicitly. Under 3,000 words of prose (tables/code excluded).
