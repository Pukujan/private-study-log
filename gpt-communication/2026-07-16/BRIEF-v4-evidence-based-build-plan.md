# ChatGPT Brief v4 — Evidence-Based Build Plan (No More v0s)

## Date: 2026-07-16
## From: Pujan (via Hades)
## To: ChatGPT (chat mode, deep research)
## Subject: The v0 disease killed Cortex. Build the final thing.

---

## Why This Brief Exists

Three prior briefs (v1-v3) asked ChatGPT to design Cortex's architecture. Those produced design docs. The design docs were never built. This brief asks a different question: **given the evidence that the v0/quick-fix approach is the root cause of failure, what is the final build plan — no v0, no MVP, no "ship it and iterate"?**

---

## The Thesis (Evaluate Against Evidence)

Every v0 quick fix created downstream problems that became new issues. Those issues were designed against (more docs, more specs), but the fixes were never built. The result: 14 subsystems at 40-60% completion, 53+ GitHub issues with no dependency mapping, and the actual feature the human wanted — decision support — was never built.

The solution is not another v0. It's to stop building infrastructure and build the actual product: **a system where the human says "I want this, I don't want that" and Cortex helps them figure out how.**

---

## The Evidence (11 Concrete Failure Trails)

| # | What was built | As a v0/quick win | The downstream problem it created | Tracked as issue |
|---|---|---|---|---|
| 1 | 38 MCP tools | "Ship them all, consolidate later" | Disease A: 12,237 tokens of bloat | 6+ issues |
| 2 | Structured gap registry | v0 markdown registry | Drifted (index.jsonl stale vs registry.md vs reality) | Gap ledger designed |
| 3 | Gap ledger code | Built July 14 as v0 | ZERO dependency data ever populated | P15 (created today) |
| 4 | Living ontology | Built for versioning only | `implements`/`part_of` predicates designed but never populated (0 relations) | Multiple issues |
| 5 | Retrieval fusion (BM25+vector+ontology RRF) | Built and measured | Showed no win (nDCG −0.024), shipped OFF | Cyclical trap |
| 6 | Fable-max rubrics | Claude authored rubrics | Rubrics validated Claude's own work → circular validation → "no family bias" claim retracted | 8+ edge cases |
| 7 | Context packets (D10) | Design doc only, frozen | No implementation issue, fell through cracks | D10 (frozen) |
| 8 | Confidence ledger | Schema designed today | No code, no data | Not yet tracked |
| 9 | Risk tradeoff ledger | Schema designed today | No code, no data | Not yet tracked |
| 10 | Context injector | Full implementation sketch today | No code, no data | Not yet tracked |
| 11 | 53 GitHub issues | Created in one day with labels | No dependency graph, no prerequisite mapping, no tiering | The issues themselves are the problem |

### The Pattern

```
Agent designs ambitious feature
    ↓
Builds v0 "quick win" (1-day MVP)
    ↓
v0 creates a new problem (bloat, drift, no automation, circular validation)
    ↓
New problem becomes a new issue
    ↓
Issue is designed against (more docs, more specs)
    ↓
Design creates MORE features to fix the problem
    ↓
Those features are designed but not built
    ↓
Unbuilt features become MORE issues
    ↓
Back to: agent designs ambitious feature
```

### What Got Built vs What Was Needed

| What was built (infrastructure) | What was needed (product) |
|---|---|
| 14 subsystems (ontology, eval lab, pattern library, etc.) | Decision support: "I want this, I don't want that, how do we do it?" |
| 4,700 test cases | Comparable paths with risk/value tradeoffs |
| 30 MCP tools | Proactive context surfacing (not query-triggered) |
| 153 ontology entities, 91 relations | Dependency mapping between decisions |
| Risk tiers in prose | Structured risk tradeoffs with tension types |
| Pattern library (10 patterns) | Confidence scoring with outcome tracking |

---

## What Today's Research Found (4 Subagent Reports)

Four parallel research subagents investigated: (1) confidence scoring, (2) risk tradeoff modeling, (3) proactive context injection, (4) strict red-team pushback against adding any of these to the ontology.

### Finding 1: No production system has calibrated decision-level confidence scoring

Surveyed: LangGraph, Vercel AI SDK, AutoGen, CrewAI, GraphRAG, OpenAI Deep Research, StackAI, LightRAG, IBM MAPE-K.

- LangGraph: manual interrupt (True/False). No confidence computation.
- Vercel AI SDK: static approval statuses. Developer writes the logic.
- AutoGen: message-count termination. Agent says "TERMINATE" (self-assessment).
- CrewAI: guardrail returns (bool, Any) — binary pass/fail.
- GraphRAG: LLM-generated importance score 0-100. Closest, but uncalibrated.
- OpenAI Deep Research: opaque stopping criteria.
- MAPE-K: policy-based loop, no confidence.

**The gap:** No system implements outcome tracking (circling back to verify whether a confidence assessment was justified). This is the missing piece for Brier score calibration.

**Three dimensions, not one score:**
1. Research sufficiency: "Do we know enough?"
2. Path success probability: "Will this work?"
3. Agent self-assessment trust: "Can we trust the agent's confidence?"

**Where it belongs:** Flat append-only ledger (not ontology). The `outcome` field (initially `pending`, resolved later) enables Brier score calibration over time.

**Red-team counter:** The gap ledger ALREADY has `verified` (false by default, flipped by deterministic check) and `evidence[]` (file:line provenance). That's a MORE honest confidence model than a numeric LLM-authored score. The oracle policy forbids LLM judgment in verdict paths.

### Finding 2: Every system that reduced risk to a single label failed

- Risk matrices (PMBOK): "worse than random" for negatively correlated frequency/severity (Cox 2008)
- NASA pre-Challenger: matrix missed interaction effects
- GRC tools (Archer, 200+ fields): nobody fills them in
- OWASP risk rating: gamed or skipped
- DREAD: same range-compression problem
- Jira links: teams only use `blocks`, never `causes`

**The lesson:** The tradeoff relationship must be the substrate. The label (LOW/MEDIUM/HIGH) is a derived projection, not the model. Cortex's existing LOW/MEDIUM/HIGH risk tiers are exactly this kind of flat label that loses information.

**Where it belongs:** Flat ledger with structured fields: `risk_if_taken`, `risk_if_not_taken`, `tension_type` (precision_vs_recall, security_vs_usability, etc.).

### Finding 3: No production system does push-based proactive context injection

Surveyed: Mem0, Letta, Zep/Graphiti, GraphRAG, LightRAG.

All are query-triggered (pull). The closest structural approaches are GraphRAG's community detection (clusters entities without a query) and recommendation engines (which face the same "no explicit query" problem).

**The answer:** A separate injection layer (`injector.py`) with:
- Entity-linking trigger (detect mentions of known entities in input text)
- Graph expansion (traverse ontology N-hop neighborhood)
- Multi-signal relevance scoring (graph proximity 30%, entity status 20%, relation type 15%, co-occurrence 10%, recency 10%, decision-context match 15%)
- Budget gate (hard token cap, e.g. 2K tokens)
- Context packet formatter (the designed-but-unbuilt artifact from D10)

**Novel insight:** Expired/superseded entities should score HIGHER for injection — they represent corrected knowledge ("something you might be relying on is no longer valid").

**Where it belongs:** Separate `injector.py` module. NOT in the ontology (data layer). NOT in the gap ledger (project-control layer). A peer module that reads from both.

**Red-team counter:** Context injection is a retrieval problem, and the ontology's retrieval is a wash on single-hop. You cannot build context injection on a retrieval surface that doesn't retrieve. The injector should be eval-gated.

### Finding 4: Red-team pushback (14 arguments against adding anything to the ontology)

All four subagents — including the one told to argue FOR — independently agreed: flat ledgers, not ontology.

Key arguments:
1. Ontology was designed as a versioning system, not decision-support — scope creep
2. Both Codex AND Fable (July 13, independent arbitration) said "derived projection, not substrate"
3. Retrieval fusion measured and FAILED (nDCG −0.024)
4. No multi-hop query engine exists
5. Risk tradeoffs = combinatorial explosion (153 entities → 11,628 potential pairs)
6. Cyclical trap: features require ontology to already be useful
7. Gap ledger already has `verified` + `evidence[]` — more honest confidence model
8. Builder=reviewer anti-pattern (circular validation)
9. `implements`/`part_of` already empty — more empty structure
10. No consumer exists for confidence/risk/injection data
11. Violates Disease A (bloat) and Disease B (governance ritual)
12. The alternative: build in flat ledger, gate on measured win, get independent reviewer

Full research reports:
- `D:\workspace\decision-confidence-research.md`
- `D:\workspace\research-risk-tradeoff-modeling.md`
- `D:\workspace\proactive-context-injector-research.md`
- `D:\workspace\RED-TEAM-PUSHBACK-ontology-confidence-risk-injection.md`

---

## The Core Problem

The strict counter-arguments are architecturally correct but **don't fix the problem.** They say "put it in flat ledgers, not the ontology." But:

- The gap ledger code exists but has ZERO dependency data populated
- The confidence ledger doesn't exist — code or data
- The risk tradeoff ledger doesn't exist — code or data
- The injector doesn't exist — code or data
- The ontology's `implements` and `part_of` predicates are empty

The red team's "alternative that respects all constraints" is: **build four more things that don't exist.** That's the same disease. Design → don't build → create issues → design more → don't build more.

---

## What We Need From You (ChatGPT)

### 1. Evidence Assessment

Does the evidence support the thesis that v0/quick-fix approach is the root cause? Be specific. Cite the failure trail above. If the evidence points to a different root cause, say so — don't validate the thesis just because we asked.

### 2. What The Human Actually Needs

The user says Cortex should be: "I want this. I don't want that. How do we do it."

Translate this into concrete capabilities. What does Cortex need to DO (not what infrastructure does it need)? The human is non-expert. They need:
- Comparable paths with risk/value tradeoffs surfaced proactively
- Plain language, not technical artifacts
- Proof-backed options, not estimates
- Confidence that's calibrated (not self-assessed)
- Knowledge that surfaces itself without being asked

### 3. The Final Build Plan

Not a v0. Not an MVP. The actual final system. What to build, in what order, with dependencies.

Include:
- **What to build FIRST** — the thing that delivers value without prerequisites
- **What to build SECOND** — things that depend on the first
- **What NOT to build** — designed-but-unbuilt artifacts that should be abandoned
- **What to ABANDON** from the existing 14 subsystems — things built that aren't needed for decision support
- **Dependency graph** — which build step blocks which

For each step, specify:
- What it does (one sentence)
- What existing code it extends (file references if possible)
- What the success criterion is (deterministic, not LLM judgment)
- What the anti-v0 check is (how do we know this isn't another quick fix?)

### 4. Anti-v0 Guardrails

How do we prevent the quick-fix disease from recurring? What structural checks prevent agents from building v0s that create downstream problems?

Consider:
- A "definition of done" that's not negotiable
- A "v0 detector" — structural signals that a build is a quick fix, not the final thing
- Scope lock enforcement — once the plan is agreed, no new features
- Build completeness check — every build step must have a consumer, a test, and a measured outcome

### 5. Self-Assessment

Where might this plan be wrong? What assumptions is it making that could be incorrect? What should an independent reviewer check?

If you (ChatGPT) have a bias toward designing rather than building, flag it. If the plan looks like it could become another designed-but-unbuilt artifact, say so.

---

## What We Do NOT Want

1. Another design doc. We have 7+ design docs. We need a BUILD plan.
2. Another v0 or MVP approach. The evidence says v0s cause the disease.
3. Vague recommendations. "Implement confidence scoring" is useless. "Add `outcome` field to gap_ledger.py entries, resolve via deterministic check at gate 5, compute Brier score over last 50 decisions" is useful.
4. Duplicating existing mechanisms. Before proposing anything, check if it already exists.
5. Ignoring the evidence. If the evidence contradicts your recommendation, follow the evidence.
6. More architecture. The architecture is done. What's missing is the product.
7. Prose without specifics. Every recommendation must reference specific files, functions, or existing artifacts.
8. A 12,000-word document. Tables, lists, code. Under 3,000 words of prose.

---

## Source Materials

### Today's research (read these first)
- `D:\workspace\decision-confidence-research.md` — confidence scoring research
- `D:\workspace\research-risk-tradeoff-modeling.md` — risk tradeoff research
- `D:\workspace\proactive-context-injector-research.md` — context injector research
- `D:\workspace\RED-TEAM-PUSHBACK-ontology-confidence-risk-injection.md` — 14-argument red team

### Prior briefs (for context, not for building)
- `private-study-log/CHATGPT-DEEP-RESEARCH-BRIEF-v3-2026-07-16.md` — prior brief (architecture focus)
- `private-study-log/CORTEX-TARGET-ARCHITECTURE-v3-2026-07-16.md` — target architecture
- `private-study-log/CORTEX-ALIGNMENT-REVIEW-2026-07-16.md` — alignment review

### Cortex brain (read for current state)
- `D:\claude\stupidly-simple-cortex\docs\ontology\README.md` — ontology purpose
- `D:\claude\stupidly-simple-cortex\docs\ontology\schema.yaml` — ontology schema
- `D:\claude\stupidly-simple-cortex\docs\HARNESS-SCORECARD-CONSOLIDATED.md` — honest scorecard
- `D:\claude\stupidly-simple-cortex\docs\design\durable-gap-tracking-codex-2026-07-13.md` — Codex's review
- `D:\claude\stupidly-simple-cortex\docs\design\durable-gap-tracking-fable-2026-07-13.md` — Fable's review
- `D:\claude\stupidly-simple-cortex\docs\BUILD-PLAN.md` — existing build plan
- `D:\hermes\cortex\workspaces\hades\docs\MASTER-INDEX-AND-DECISION-LOG.md` — master index

### GitHub issues
- SCC: https://github.com/Pukujan/stupidly-simple-cortex/issues (50 open)
- Wrapper: https://github.com/Pukujan/cortex-agent-wrapper/issues (3 open)

---

## Success Criteria

This brief succeeds if ChatGPT produces a plan where:

1. A developer can read it and know exactly what to build first, second, third
2. Every build step has a deterministic success criterion (not LLM judgment)
3. Every build step has an anti-v0 check (how do we know this isn't another quick fix?)
4. The plan does NOT propose building four more ledgers that don't exist
5. The plan addresses the cyclical problem (things are missed because nothing surfaces them)
6. The plan starts from what exists and builds the product, not more infrastructure
7. The human's actual need ("I want this, I don't want that, how do we do it") is the deliverable, not a side effect
8. Under 3,000 words of prose (tables/code excluded)

This brief fails if ChatGPT produces another architecture document that will be filed and never built.
