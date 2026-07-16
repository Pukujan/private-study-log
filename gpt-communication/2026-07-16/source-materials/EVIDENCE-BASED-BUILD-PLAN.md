# EVIDENCE-BASED BUILD PLAN FOR CORTEX

**Author:** Fable (Claude-lineage, acting as independent reviewer/planner)
**Date:** 2026-07-16
**Status:** Plan for the final system, not a v0.

---

## 1. EVIDENCE ASSESSMENT: Does the evidence support the v0/quick-fix thesis?

### Verdict: The thesis is **partially supported** — but the evidence points to a deeper root cause the thesis doesn't name.

The user's diagnosis is: *every v0 quick fix created downstream problems that became new issues, and the solution is to stop building v0s and build the actual final system.*

The evidence trail confirms the **mechanism** (quick fixes → downstream problems → new issues) but reveals the **root cause** is more specific than "building v0s." The root cause is: **agents built infrastructure instead of the product, and the infrastructure they built was never wired into a live path that delivers value to the human.**

### The evidence trail, traced precisely:

**1. The 38 MCP tools → Disease A → consolidation → regression issues**
- **Built:** 47 MCP tool registrations in `mcp.py` (3,074 lines). The BUILD-PLAN explicitly says the canonical surface should be ~5 tools.
- **Downstream problem:** Disease A — 12,237 tokens of tool-schema bloat measured (`WHY-CORTEX-IS-FAILING.md` cites the diagnosis).
- **The fix that became a new problem:** Tool consolidation (47→34 per the feature sweep) created tool-surface regression issues.
- **Evidence verdict:** This is a v0 disease, but the deeper issue is that the tools were built *before the product they serve was defined*. Nobody asked "what does the human need to DO?" before building 47 tools.

**2. Structured gap registry → drift → gap ledger → empty ledger → P15**
- **Built:** A structured gap registry (`templates/workspace-control-plane/gaps/index.jsonl` + `registry.md`).
- **Downstream problem:** Both drifted. `index.jsonl` stopped at GAP-CORTEX-0012; `registry.md` disagreed on GAP-CORTEX-0003's status (`design_locked` vs `built_v1`). Fable's design doc diagnosed this as "drift is a write-path disease, not a format disease" (`durable-gap-tracking-fable-2026-07-13.md:51-55`).
- **The fix that was built but not used:** `gap_ledger.py` — 907 lines of tested, schema-validated append-only ledger code. **The `gaps/` directory does not exist.** The ledger has never been populated with a single entry.
- **Evidence verdict:** This is the clearest case. The code was built (July 14), the design was sound (two independent reviewers converged), and **zero data was ever written to it.** The fix exists as code but delivers zero value because nobody seeded it and nobody wired it into the work flow.

**3. Ontology built for versioning → reviewers said "don't use for ops" → ledger never populated → ontology empty → "no relationships" problem**
- **Built:** 223 entities, 91 relations in `docs/ontology/`. The ontology works for its original purpose (currency tracking).
- **Downstream problem:** `implements` and `part_of` predicates are defined in the schema but have **ZERO populated relations** (verified: `grep` returns 0 matches). The ontology has empty structure — designed but never filled.
- **The cyclical trap:** Retrieval fusion was measured (nDCG −0.024, a wash), shipped OFF. The thing that would make the ontology useful (retrieval) is turned off. The ontology stays empty because nothing populates it. Nothing populates it because there's no consumer.
- **Evidence verdict:** The ontology is a versioning system masquerading as a decision-support system. It works for versioning. It doesn't work for decisions — and adding decision features to it (confidence, risk, injection) would repeat the exact failure: build structure, don't populate it, declare it useful.

**4. Retrieval fusion measured → no win → shipped OFF → ontology stays empty → cyclical trap**
- **Measured:** nDCG −0.024 on the multi-hop set (July 13). Re-measured July 15: nDCG +0.020, barely cleared the gate. Single-hop: exact no-op.
- **Evidence verdict:** The ontology's only measured retrieval function is marginal. This is honest engineering — the gate worked. But it means the ontology is not a retrieval substrate, and building decision-support on top of it is building on sand.

**5. Fable-max rubrics → circular validation → "no family bias" retracted → 8+ edge cases**
- **Built:** Rubrics authored by Claude (Fable-max), validated against Claude-authored gold.
- **Downstream problem:** The "no family bias" claim was **retracted as confounded** (gold + rubric both Anthropic-authored). 8+ edge cases emerged.
- **Evidence verdict:** This is the circular-validation anti-pattern the project was built to prevent. The builder authored the checks. The red-team doc (`RED-TEAM-PUSHBACK`) cites this directly: "If Claude builds it, Claude cannot be the one who validates it."

**6. Context packets (D10) → frozen decision → no implementation issue → fell through cracks**
- **Frozen:** D10 in the Master Index — "Context packet system for smart injection."
- **Built:** Nothing. No `injector.py`, no `context_packet.py` anywhere in the codebase.
- **Evidence verdict:** A frozen decision with no implementation. The design exists in the v3 architecture doc, but no code was written. This is the gap between "decided" and "built."

**7-11. The "designed today, no code" cluster**
- Gap ledger: code built, **zero data populated** (no `gaps/` directory exists).
- Confidence ledger: schema designed (today's research), no code.
- Risk tradeoff ledger: schema designed (today's research), no code.
- Context injector: full implementation sketch designed (today's research), no code.
- `implements`/`part_of` predicates: defined in ontology schema, **ZERO relations populated**.

### The deeper root cause the thesis doesn't fully name:

The evidence reveals a **three-stage failure pattern**, not just "v0 quick fixes":

| Stage | What happens | Evidence |
|-------|-------------|----------|
| **Design without consumer** | A capability is designed/built without specifying what query or human action will consume it. | `implements`/`part_of` predicates (0 relations), context packets (frozen, not built), confidence/risk ledgers (schema, no code) |
| **Build without wiring** | Code is written but never connected to a live path that produces value. | `gap_ledger.py` (907 lines, empty), 47 MCP tools (human uses ~0 of them), scribe (built, closeouts are "optional in practice") |
| **Document without measuring** | Prose claims substitute for measured outcomes. | Scorecard says "~40% measured, ~25% aspirational." Results live "only in prose markdown" per the fact-check. |

**The thesis is correct that quick fixes caused downstream problems.** But the evidence shows the disease isn't just "building v0s" — it's **building infrastructure that has no consumer, no wiring, and no measurement.** A v0 that serves a real human need and is wired into a live path is not the disease. The disease is building capability in isolation from the value it's supposed to deliver.

### What the evidence does NOT support:

The thesis implies the solution is "build the actual final system." But the evidence shows that **the final system was already designed multiple times** — v3 architecture, BUILD-PLAN phases 0-8, the tiered lifecycle pipeline, the Hermes/Cortex MCP vision. The problem isn't missing design. The problem is **the design was never connected to a build path that starts with the thing the human needs and builds outward from there.**

---

## 2. WHAT THE HUMAN ACTUALLY NEEDS

The human said: **"I want this. I don't want that. How do we do it."**

This is a decision-support request. Translated into concrete capabilities:

### What Cortex must DO (not what infrastructure it needs):

1. **Understand what the human wants** — through a narrowing dialogue, not a guess. The human is non-expert; they can't specify what they don't know to specify. Cortex must ask smart questions before presenting options.

2. **Present comparable paths with risk/value tradeoffs** — when the human says "I want X," Cortex researches 2-4 ways to achieve X, each with: what it produces (plain language), what it costs (based on precedent, not estimates), what risks it carries (structured tradeoffs, not a single label), and proof of viability (pattern match, mechanism validation, failure mode check).

3. **Surface what the human doesn't know to ask for** — when "Claude" is mentioned, Cortex proactively surfaces the family-bias finding, the circular-validation pattern, the expired fable-max entity. The human can't ask for knowledge they don't know exists.

4. **Lock the outcome before building** — the human picks an option. That option becomes the locked outcome. Implementation works toward it. The human is not in the implementation loop.

5. **Show the result, not the paperwork** — when done, the human sees what was built. Not a closeout document. Not a 12,000-word analysis. The thing itself, demonstrated.

6. **Track whether the decision was right** — over time, Cortex records outcomes. Did the path succeed? This calibration data makes future presentations more accurate. (No production system does this — today's research confirms it's the gap.)

### What Cortex must NOT be:

- **Not an eval framework.** The 63 objective eval lanes are real and valuable, but they are infrastructure, not the product. The human doesn't interact with eval lanes.
- **Not an evidence substrate.** The ontology, gap ledger, results ledger — these are prerequisites. They serve the product, they are not the product.
- **Not a knowledge graph.** The ontology is a versioning system. It's not a decision-support system. Treating it as one caused the cyclical trap.
- **Not a state machine the human drives.** The state machine exists to prevent phase skipping. But the human shouldn't have to know about phases. Cortex drives the phases; the human drives the decisions.

### The minimum viable product (the actual product, not a v0):

The human needs a **narrowing loop**:

```
Human states intent (vague is fine)
  → Cortex researches (with proactive context injection)
  → Cortex asks narrowing questions (plain language, multiple choice)
  → Human answers
  → Cortex researches deeper (options are researched, not guessed)
  → Cortex presents impact matrix with PROOF (not estimates)
  → Human picks/modifies/rejects
  → Outcome locked
  → Cortex implements (auto-loop, deterministic checks, no human in loop)
  → Human sees the result
  → If unsatisfied: Cortex asks smart questions, offers options with proof
  → Outcome recorded for calibration
```

This is the product. Everything else either serves this loop or is infrastructure that should be abandoned.

---

## 3. THE FINAL BUILD PLAN

### Design principle: Build outward from the value, not inward from the infrastructure.

Every previous plan started with infrastructure (retrieval, eval, ontology, state machine) and tried to connect it to the human. This plan starts with the human-facing capability and builds the minimum infrastructure needed to support it.

### What to build FIRST (delivers value without prerequisites):

**BUILD 1: The Narrowing Dialogue + Context Packet System**

This is the thing the human actually interacts with. It requires no ontology, no gap ledger, no confidence scoring. It requires:
- A way to capture intent (the human's statement)
- A way to ask narrowing questions (plain language, multiple choice)
- A way to record the Q&A (the context packet — chunked, not monolithic)
- A way to search the brain with the accumulated context (the existing BM25+vector retrieval already works — nDCG 0.650)
- A way to present options with proof (research-backed, not estimated)

**Why first:** This is the product. The human can use it immediately. It doesn't depend on any of the 14 subsystems being "complete." It uses what already works (retrieval, search, scope packs) and adds the missing piece: the narrowing loop that makes the human an informed decision-maker.

**What exists:** The v3 architecture document describes this flow in detail. The retrieval engine works. Scope packs work. Fan-out/mission_driver exist. The scribe exists. What's missing is the narrowing dialogue itself and the context packet router.

**Build order within BUILD 1:**
1. **Context packet store** — append-only JSONL (same pattern as task_ledger), one packet per decision session. Chunks: intent, narrowing Q&A, research findings, prior context. (~100 lines, reuses task_ledger lock discipline.)
2. **Narrowing question generator** — takes intent + context packet, produces 1-N plain-language questions with multiple-choice options. This is an LLM call with a structured prompt. No new infrastructure.
3. **Context router** — sits between the narrowing dialogue and the existing search. At narrowing phase: inject intent + prior context. At research phase: inject intent + narrowing Q&A. At option presentation: inject all chunks. (~150 lines, calls existing `search.py`.)
4. **Impact matrix formatter** — takes research results, formats as plain-language options with: what it produces, cost (from audit log precedent), risk (structured tradeoff, not single label), proof of viability (pattern match / mechanism / failure mode check). UNPROVEN labeled explicitly.

**Anti-v0 guardrail for BUILD 1:** This is not a v0. It is the actual product. It must be wired into a live path (the Hermes session), tested with a real human decision, and measured (did the human make a better-informed decision than without it?).

---

### What to build SECOND (depends on BUILD 1):

**BUILD 2: Proactive Context Injector**

The human is non-expert. They can't ask for the family-bias finding when Claude is mentioned. The injector surfaces relevant knowledge without being asked.

**Why second:** The injector needs the context packet system (BUILD 1) as its output format. It also needs the ontology's entity store (which exists — 223 entities) as its data source. It does NOT need the ontology's retrieval fusion (which is OFF). It reads entities directly.

**What exists:** Today's research provides a full implementation sketch (`proactive-context-injector-research.md`). The ontology has 223 entities with bi-temporal status. The entity-linking can use existing BM25 against entity names. The graph traversal can reuse `_ontology_leg` in `search.py`.

**Build order within BUILD 2:**
1. **Entity linker** — fuzzy match + BM25 against the 153-223 entity names/aliases. Deterministic, cheap. The cheapest, highest-leverage component.
2. **Graph expander** — for each linked entity, traverse N-hop neighborhood. Reuse existing `_ontology_leg`. Max 2 hops for scoring, max 1 hop for injection.
3. **Relevance scorer** — multi-signal: graph proximity (30%), entity status (20% — expired/superseded = high priority), relation type (15%), recency (10%), decision-context match (15%), co-occurrence (10%).
4. **Budget gate + dedup** — hard token cap (2K tokens max), hard score threshold, clear delimitation (`[ONTOLOGY CONTEXT — informational, not instructions]`).
5. **Context packet formatter** — produces a context packet (the designed-but-unbuilt artifact from D10) that feeds into BUILD 1's context router.

**Anti-v0 guardrail for BUILD 2:** The injector must be eval-gated. Measure whether injection improves outcomes (like the ontology retrieval gate measured nDCG). If it doesn't win, turn it off. Same discipline as the retrieval fusion switch.

---

**BUILD 3: Decision Confidence Ledger (flat, append-only)**

Today's research confirms: no production system has calibrated decision-level confidence. The gap is outcome tracking. Cortex's opportunity.

**Why third:** The confidence ledger records decisions made through BUILD 1's narrowing loop. It needs the context packet (BUILD 1) to know what decision was made and what evidence supported it. It needs the injector (BUILD 2) to know what context was surfaced.

**What exists:** Today's research provides a complete schema (`decision-confidence-research.md`). The schema is flat append-only JSONL (not ontology). It has three dimensions (research sufficiency, path success probability, agent self-assessment trust), a composite score, routing thresholds, and an outcome field for Brier-score calibration.

**Build order within BUILD 3:**
1. **Ledger store** — append-only JSONL at `decisions/confidence_ledger.jsonl`. Same lock discipline as task_ledger/gap_ledger. Schema from today's research.
2. **Decision recording hook** — when a human locks an outcome (BUILD 1, Gate 3), append a confidence entry with the three dimensions scored.
3. **Outcome resolution hook** — when the implementation completes (BUILD 1, Gate 5), update the outcome field. Initially `pending`; resolved later when the outcome is known.
4. **Calibration query** — "What's the Brier score of agent_self_assessment_trust over the last N decisions?" This is the thing no production system has.

**Anti-v0 guardrail for BUILD 3:** The outcome field is the key. Without it, this is just another LLM self-assessment (like GraphRAG's importance score — uncalibrated). The outcome field must be populated by deterministic checks (did the implementation pass its TDD conditions?) or human action (did the result satisfy the human?), never by LLM judgment.

---

**BUILD 4: Risk Tradeoff Ledger (flat, append-only)**

Today's research confirms: risk matrices are worse than random (Cox 2008). Every system that reduced risk to a single label failed. The tradeoff relationship must be the substrate.

**Why fourth:** The risk ledger records the tradeoffs for each option presented in BUILD 1's impact matrix. It needs the narrowing loop (BUILD 1) to know what options were presented and which was chosen.

**What exists:** Today's research provides a complete schema (`research-risk-tradeoff-modeling.md`). The schema is flat append-only JSONL with `assert_tradeoff` events, `tension_type` enum, `risk_if_taken`/`risk_if_not_taken` structured fields, and evidence links.

**Build order within BUILD 4:**
1. **Ledger store** — append-only JSONL at `decisions/risk_ledger.jsonl`. Same pattern.
2. **Tradeoff assertion hook** — when options are researched (BUILD 1, step 5), assert tradeoffs for each path.
3. **Projection query** — "What are all the open security tradeoffs?" — a one-liner over `tension_type` and `axis` enums.
4. **Risk tier derivation** — the LOW/MEDIUM/HIGH label becomes a *projection* from accumulated tradeoffs, not an authored label. This fixes the Cox 2008 problem.

**Anti-v0 guardrail for BUILD 4:** The schema must be minimal (the research warns: "A risk schema that's too heavy will never get populated"). The GRC tools (Archer, ServiceNow) failed because they had 200+ fields per risk. Keep it under 12 fields.

---

### What NOT to build (designed-but-unbuilt artifacts to abandon):

**DO NOT BUILD: Confidence/Risk/Injection in the ontology.**
- All four subagent reports (including the one arguing FOR the ontology) agreed: flat ledgers, not ontology.
- The red-team doc provides 14 sourced arguments against.
- Adding these to the ontology repeats the `implements`/`part_of` failure: designed structure that's never populated.

**DO NOT BUILD: Multi-hop query engine for the ontology.**
- The ontology has no multi-hop query. The red-team doc argues this is needed for confidence aggregation. But flat ledgers don't need multi-hop queries — they need a 15-line BFS over adjacency lists (per Codex's design doc).
- Building a multi-hop engine for a 223-entity graph is premature optimization.

**DO NOT BUILD: Community summaries (ontology Stage D).**
- Not built. Not needed for the product. The ontology's value is currency tracking, not community detection.

**DO NOT BUILD: A new state machine.**
- P2 is resolved: task-typed routing is the production pattern. The existing state_engine.py (2,147 lines) should be adapted, not replaced.

**DO NOT BUILD: Another results ledger from scratch.**
- `evals/results.jsonl` exists (36 entries). It's thin but real. Extend it, don't rebuild it.

**DO NOT BUILD: A new gap registry.**
- `gap_ledger.py` exists (907 lines). It's never been populated. The fix is to SEED it, not to rebuild it.

---

### What to ABANDON from the existing 14 subsystems:

**ABANDON: The ontology as a decision-support substrate.**
- It's a versioning system. It works for versioning. It doesn't work for decisions. Stop trying to make it do both.
- The ontology stays as-is: currency tracking + bi-temporal status + the (marginal, OFF-by-default) retrieval fusion leg. It serves the injector (BUILD 2) as a data source for entity status. It does not serve as a confidence store, risk store, or decision store.

**ABANDON: The empty predicates (`implements`, `part_of`).**
- Zero relations. Designed but never populated. Remove from the schema or leave them as documented "not yet wired" — but don't build consumers for them.

**ABANDON: The 13 excess MCP tools (47 → 5 canonical).**
- The BUILD-PLAN says the canonical surface is 5 tools. 47 exist. Consolidate aggressively. Each tool must have a consumer (a human action or an agent action that produces value). Tools with no consumer are abandoned.

**ABANDON: `research_v2_experimental.py`.**
- Self-described throwaway, unwired. The feature sweep says "retire/merge."

**ABANDON: The aspirational self-learning flywheel as currently scoped.**
- The feature sweep says it's "built-as-parts but unwired, default-off, or unmeasured." The flywheel should be a consequence of BUILD 1-4 (decisions recorded → calibration data → better predictions), not a separate subsystem to build.

**RETIRE (not abandon — mark complete): The retrieval engine.**
- BM25+vector RRF works (nDCG 0.650). Hybrid is default-on. This is done. Stop "improving" it and start using it.

**RETIRE: The objective eval lanes.**
- 63 lanes, 4,551 hard_gold rows. This is the strongest asset. It's done. It serves BUILD 3 (confidence calibration) as ground truth. Don't build more lanes; wire the existing ones into the decision loop.

---

### Dependency graph:

```
BUILD 1 (Narrowing Dialogue + Context Packet)
  │
  ├──→ BUILD 2 (Proactive Injector) — needs context packet format
  │       │
  │       └──→ reads from existing ontology (entity store, no changes needed)
  │
  ├──→ BUILD 3 (Confidence Ledger) — needs decision recording from Gate 3
  │       │
  │       └──→ reads from existing eval lanes (ground truth for calibration)
  │
  └──→ BUILD 4 (Risk Tradeoff Ledger) — needs option research from step 5
```

**Critical path:** BUILD 1 is the only thing that must come first. Everything else can be built in parallel once BUILD 1's context packet format is defined.

**What's NOT on the critical path (and should not block):**
- Ontology improvements (the ontology serves BUILD 2 as a read-only data source)
- Gap ledger population (important for project hygiene, not for the product)
- State machine gate enforcement (D6 is ON for write server; that's sufficient)
- MAPE-K (D5: optimization only, after outcome is finalized — not a build priority)

---

## 4. ANTI-V0 GUARDRAILS

The disease isn't "building v0s." It's building capability that has no consumer, no wiring, and no measurement. The guardrails target the actual disease:

### Guardrail 1: Consumer-first rule

**No capability is built unless a specific consumer is named.** The consumer is either:
- A human action (the human narrows, the human picks an option, the human sees a result), OR
- An agent action that directly serves a human action (the agent researches options, the agent injects context).

**Enforcement:** Before any build, write one sentence: "The human will use this to ___." If you can't fill in the blank, don't build it.

**Evidence:** The `implements`/`part_of` predicates have zero relations because no consumer was specified. The gap ledger has zero entries because no consumer was wired. The 47 MCP tools exist because tools were built before the product was defined.

### Guardrail 2: Wire-before-build rule

**No code is written until the wiring path is specified.** "Wiring" means: how does this code get called in a live session? What triggers it? What consumes its output?

**Enforcement:** Every build item must specify:
1. What triggers it (entity mention, human action, state transition)
2. What it reads from (existing search, existing ontology, existing ledger)
3. What it writes to (context packet, confidence ledger, risk ledger)
4. What consumes its output (the narrowing dialogue, the impact matrix, the calibration query)

**Evidence:** `gap_ledger.py` is 907 lines of tested code with zero data because it was never wired into the closeout path. The scribe exists but closeouts are "optional in practice" because nothing triggers them.

### Guardrail 3: Measure-before-ship rule

**Every capability ships with a measurement gate.** If it can't be measured, it doesn't ship. If the measurement doesn't show value, it's turned off.

**Enforcement:**
- The injector (BUILD 2) gets an eval gate: does injection improve outcomes? (Like the ontology retrieval gate measured nDCG.)
- The confidence ledger (BUILD 3) gets a calibration gate: does the Brier score improve over time?
- The narrowing dialogue (BUILD 1) gets a human-understanding gate: did the human make a more informed decision than without it? (Measured by: did they pick an option they wouldn't have known about? Did they avoid a path that would have failed?)

**Evidence:** The retrieval fusion gate worked — it said "no win, ship OFF." That's the discipline. The rubric validation failed — it validated Claude's work with Claude's gold (circular). The measurement must be independent of the builder.

### Guardrail 4: One canonical store rule

**Each type of data has exactly one canonical store.** Projections are derived, not maintained. No second representation that can drift.

**Enforcement:**
- Decisions → `decisions/confidence_ledger.jsonl` (one store, not also in the ontology)
- Risk tradeoffs → `decisions/risk_ledger.jsonl` (one store, not also in the ontology)
- Context packets → `decisions/context_packets.jsonl` (one store, not also in prose)
- Gaps → `gaps/gap_ledger.jsonl` (already designed — just needs to be SEeded)

**Evidence:** The structured gap registry drifted because `index.jsonl` and `registry.md` were two hand-maintained representations. The scorecard's headline numbers "live only in prose markdown — there is no committed results ledger." Every drift case is a second representation.

### Guardrail 5: Build-complete rule (no "designed but not built")

**A build item is not "done" until the data is populated and a real query returns a real result.** Code existing is not "built." Code wired is not "built." Code wired AND populated AND queried is "built."

**Enforcement:** Definition of done for each build:
- BUILD 1: A real human makes a real decision through the narrowing loop. The context packet has real data. The impact matrix has real options with real proof.
- BUILD 2: A real entity mention triggers real injection. The injected context is relevant (measured). The human sees something they wouldn't have known to ask for.
- BUILD 3: A real decision is recorded with real confidence scores. The outcome field is populated by a real deterministic check. The Brier score is computable.
- BUILD 4: A real option has real tradeoffs asserted. The tension_type query returns real results. The risk tier is derived from real tradeoffs.

**Evidence:** `gap_ledger.py` is "built" by code standards but has zero entries. The ontology has `implements`/`part_of` "built" by schema standards but has zero relations. "Built" without data is the failure pattern.

### Guardrail 6: Anti-circular-validation rule

**The builder never authors the only checks.** If Claude builds it, Claude cannot be the one who certifies it works.

**Enforcement:**
- BUILD 1 (narrowing dialogue): tested by a real human making a real decision, not by Claude grading Claude's output.
- BUILD 2 (injector): eval-gated against ground truth (does injection surface the right thing?), not by Claude judging Claude's injection.
- BUILD 3 (confidence): outcome populated by deterministic checks or human action, never by LLM judgment.
- BUILD 4 (risk): tradeoffs validated against real failure modes (from audit log), not by Claude estimating Claude's risk assessment.

**Evidence:** The "no family bias" claim was retracted as confounded. The red-team doc's argument #8: "If Claude designs and builds confidence scoring, Claude is the builder. If Claude then validates, Claude is the reviewer. Builder = reviewer is the exact anti-pattern."

---

## 5. SELF-ASSESSMENT

### Where this plan might be wrong:

**1. The narrowing dialogue might be harder than I'm implying.**
The v3 architecture document describes it in detail, but generating *good* narrowing questions — questions that actually help a non-expert human clarify their intent — is an LLM prompt engineering challenge. I'm treating it as "an LLM call with a structured prompt," which understates the difficulty. The prompt design, the question quality, and the human's tolerance for being asked questions are all unknowns.

**2. I may be underestimating the wiring cost.**
The plan says "reuse existing search/fan-out/scribe." But the feature sweep says these are "built-as-parts but unwired, default-off, or unmeasured." Wiring them might surface integration bugs that are expensive to fix. The gap between "module exists" and "wired into a live, measured path" is the dominant finding of the feature sweep — and I may be glossing over it.

**3. The confidence ledger might be premature.**
No production system has calibrated decision-level confidence. That's framed as "our opportunity." But it's also possible that *no production system has it because it doesn't work yet.* The outcome tracking requires a temporal gap between decision and outcome, a definition of "good outcome," and enough historical data to calibrate. Cortex may not have enough decisions to calibrate for a long time. The Brier score computation might produce noise, not signal, at low N.

**4. I'm recommending building 4 new things, which could itself become the next "14 subsystems at 40-60%."**
The anti-v0 guardrails are designed to prevent this, but they're untested. The real test is whether I (or the next agent) actually follow them. If BUILD 1 is built but not wired, or BUILD 2 is built but not eval-gated, the disease recurs. The guardrails are only as good as their enforcement.

**5. The "abandon the ontology as decision substrate" recommendation might be too aggressive.**
The ontology has 223 entities and 91 relations. It works for versioning. The injector (BUILD 2) reads from it. If the injector proves valuable, there might be a case for richer ontology features later. I'm saying "don't build decision features in the ontology," but I should acknowledge that the ontology might evolve — just not as the *substrate* for decisions.

### Where I (as a Claude-lineage model) might be biased:

**1. Quick-win bias.**
Claude models favor building things they can finish quickly. This plan recommends 4 builds, each of which is scoped to be buildable in days, not weeks. That's convenient for a Claude model — it's things I can finish. The red-team doc flags this exact risk: "'Build the simple thing I can finish tonight' is a known agent bias" (`durable-gap-tracking-fable-2026-07-13.md:394`).

**Check:** Are these builds actually the right scope, or are they the scope I find comfortable? An independent reviewer (Codex, or a non-Claude model) should evaluate whether the build scope is driven by the human's needs or by the model's capability.

**2. Self-interest in validating prior work.**
I (as Fable/Claude) authored much of the existing rubrics, the gap tracking design, and the v3 architecture. This plan validates the v3 architecture's narrowing loop as "the product." I have a self-interest in confirming that prior design work was correct.

**Check:** Is the narrowing loop actually the right product, or am I validating my own design? The alignment review (`CORTEX-ALIGNMENT-REVIEW-2026-07-16.md`) found that the v3 architecture "gets the direction right but the depth wrong" and that it "ignores existing sophistication." An independent reviewer should check whether BUILD 1 incorporates the tiered lifecycle pipeline's model routing and effort optimization, which I'm not addressing in this plan.

**3. Circular validation risk in the confidence ledger.**
I'm recommending a confidence ledger that includes "agent self-assessment trust" as a dimension, scored by Cohen's kappa vs Fable-Max anchor. If I build the confidence scoring and then validate it, I'm the builder = reviewer. The red-team doc's argument #8 applies directly.

**Check:** BUILD 3's confidence scoring must be validated by an independent model (Codex or cross-family), not by Claude. The outcome field must be populated by deterministic checks, not LLM judgment. If I can't ensure independent validation, BUILD 3 should not be built.

**4. The plan doesn't address the model routing problem.**
The alignment review found that the tiered lifecycle pipeline "already solved the cost/quality optimization problem" with per-stage model routing + reasoning effort. My plan ignores this. This might be because the narrowing loop doesn't need it (the human-facing dialogue can use whatever model is driving the session), but it might also be a gap I'm not seeing.

**Check:** Should BUILD 1 incorporate model routing (frontier for narrowing question generation, cheap for research workers)? The existing `model_dispatch.py` and `fanout.py` already do this. An independent reviewer should check whether I'm underutilizing existing infrastructure.

**5. I'm recommending building flat ledgers, which I also designed.**
The confidence ledger schema and risk tradeoff schema are from today's research, which I (as the orchestrating agent) commissioned. The red-team doc says flat ledgers are correct, and I agree. But I designed the schemas and I'm recommending building them. There's a self-interest in validating my own design work.

**Check:** The schemas should be reviewed by an independent model before building. Specifically: are the field sets minimal enough to actually get populated? (The research warns about this.) Are the enums correct? Is the append-only pattern actually simpler than alternatives?

### What an independent reviewer should check:

1. **Is BUILD 1 actually the right first thing?** Or is there a cheaper/faster path to delivering decision support that doesn't require the full narrowing loop? (E.g., could a simpler "search + inject + present" flow deliver 80% of the value?)

2. **Are the 4 builds the right scope, or should some be merged/dropped?** Could the confidence ledger and risk ledger be one ledger? (They share the same substrate pattern and both serve the decision loop.)

3. **Is the "abandon the ontology as decision substrate" recommendation correct?** The ontology has 223 entities. Could it serve the injector better than I'm crediting?

4. **Does this plan actually prevent the v0 disease, or does it just rename it?** The guardrails are structural, but enforcement is the question. Who enforces them? What happens when an agent ignores them?

5. **Is the build order correct?** Should the injector come before the narrowing dialogue? (The injector surfaces what the human doesn't know to ask for; the narrowing dialogue asks what the human wants. Which comes first?)

---

## APPENDIX: Evidence Inventory

| Claim | Source | Verified? |
|-------|--------|-----------|
| 47 MCP tools in mcp.py | `grep -c` on `cortex_core/mcp.py` | ✅ Verified directly |
| gap_ledger.py is 907 lines, gaps/ dir does not exist | `wc -l`, `ls gaps/` | ✅ Verified directly |
| Ontology: 223 entities, 91 relations | `wc -l` on entities.jsonl, relations.jsonl | ✅ Verified directly |
| implements/part_of: 0 relations | `grep -c` on relations.jsonl | ✅ Verified directly |
| results.jsonl exists, 36 entries | `wc -l evals/results.jsonl` | ✅ Verified directly |
| scribe.py exists at .cortex/scripts/ | `ls -la` | ✅ Verified directly |
| State engine: 2,147 lines | `wc -l` | ✅ Verified directly |
| 135 Python modules in cortex_core/ | `ls *.py | wc -l` | ✅ Verified directly |
| Retrieval nDCG 0.650, recall 0.733 | HARNESS-SCORECARD-CONSOLIDATED.md | ⚠️ "No committed results ledger — these live only in prose markdown" |
| "no family bias" retracted as confounded | HARNESS-SCORECARD-CONSOLIDATED.md:37 | ✅ Cited in scorecard |
| Ontology retrieval fusion: nDCG −0.024 | RED-TEAM-PUSHBACK.md:65 | ✅ Cited from eval report |
| Disease A: 12,237 tokens bloat | WHY-CORTEX-IS-FAILING.md | ✅ Cited |
| No production system has calibrated decision confidence | decision-confidence-research.md | ✅ Research finding |
| Risk matrices worse than random (Cox 2008) | research-risk-tradeoff-modeling.md:19-24 | ✅ Research finding |
| All 4 subagents agreed: flat ledgers, not ontology | RED-TEAM-PUSHBACK.md:241-248 | ✅ Explicit consensus |
| Gap ledger designed July 13, built July 14, zero data | Context provided + verified | ✅ Verified directly |
| Confidence ledger: schema designed, no code | `find . -name "*confidence*"` | ✅ Verified — no cortex code |
| Risk ledger: schema designed, no code | `find . -name "*risk*ledger*"` | ✅ Verified — none found |
| Context injector: no code | `find . -name "injector.py"` | ✅ Verified — none found |
| 53 GitHub issues, no dependency mapping | Context provided | ⚠️ Not independently verified (GitHub not accessible) |
| 14 subsystems at 40-60% completion | Context provided | ⚠️ Not independently verified |

---

*Author: Fable (Claude-lineage), 2026-07-16. This plan is evidence-based but not independently validated. The self-assessment section identifies specific points where my recommendations may be biased. An independent reviewer (non-Claude) should evaluate this plan before execution begins.*
