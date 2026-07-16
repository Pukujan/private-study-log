# ChatGPT Deep Research Brief v5 — Does Fable's Build Plan Deliver Cortex?

## Date: 2026-07-16
## From: Pujan (via Hades)
## To: ChatGPT (deep research)
## Subject: Evaluate Fable's build plan against the Cortex definition. Does it deliver?

---

## Why This Brief Exists

Two independent models have now produced two artifacts:

1. **ChatGPT (chat mode)** produced a definition of what Cortex IS — a runtime-independent automation and assurance platform that prevents downstream problems created by AI agents.
2. **Fable (Claude-lineage)** produced a 4-build plan for how to build it.

This brief asks deep research to evaluate whether Fable's plan actually delivers the system ChatGPT defined. Not "is the plan architecturally correct?" — but "does building this plan result in the product described by the definition?"

The focus is the final output, the final metric, the final usage. Nothing else matters if it doesn't meet the expected outcome.

---

## The Cortex Definition (What Must Be Delivered)

> Cortex is an agent-runtime automation and assurance platform. It exposes a service contract that any AI agent can use to turn vague goals into researched, evidence-bound outcomes; coordinate strong and weak models; execute complex workflows; independently verify delivered work; prevent repeated downstream failures; preserve institutional knowledge; and continuously reduce the judgment, labor, rework, and maintenance costs created by trusting AI without sufficient controls.

### The problem Cortex solves

AI agents can be capable and still create recurring damage:
- Solving the wrong interpretation of the request
- Rebuilding something that already exists
- Choosing an approach without researching alternatives
- Producing plausible but weak plans
- Expanding scope while implementing
- Declaring their own work correct
- Losing decisions between sessions
- Repeating previously discovered failures
- Leaving behind documentation, state, and code that disagree
- Saving effort today while creating maintenance cost tomorrow

### The three loops

**Decision loop:** vague intent → narrowing → proactive recall → iterative research → evidence-backed recommendation → human outcome lock

**Delivery loop:** locked outcome → scoped execution → deterministic verification → bounded rework → measured result → automatic closeout

**Optimization loop:** predictions vs outcomes → calibration → detect waste and fragility → propose bounded improvements → human-approved tuning

### The cost model Cortex must measure

Immediate cost + verification cost + expected rework + maintenance burden + future coordination cost + risk of repeated failure

A cheap implementation that creates recurring drift may be more expensive than a slower, properly governed one.

### The six platform layers

1. **Runtime contract** — connects any agent host to Cortex, defines governed work
2. **Workflow engine** — tracks phases, scope, budgets, retries, ownership, legal actions
3. **Knowledge intelligence** — proactively supplies relevant decisions, patterns, failures, procedures, project state
4. **Decision engine** — iterative research, evidence-backed recommendations, helps human freeze outcome
5. **Execution and assurance** — routes strong/weak models, captures artifacts, runs independent checks, prevents self-certification
6. **Learning and optimization** — compares predicted vs actual outcomes, improves retrieval/routing, detects waste, proposes bounded improvements

### The 7 corrections from earlier ChatGPT review (MUST be reflected in the plan)

1. **Evidence labels:** Every value labeled MEASURED, DERIVED, FORECAST, QUALITATIVE, or UNKNOWN. No false precision.
2. **Not everything is calculable:** Distinguish machine-decidable facts from human judgment, model interpretation, unresolved uncertainty. Never hide a value judgment inside a numeric formula.
3. **Model recommends; does not authorize:** Human retains authority over intent, trade-offs, scope, sufficiency. Model is recommendation engine, not sovereign.
4. **Injection needs refusal boundary:** Surface → rank → explain relevance → expose conflicts → bind to task. Not "paste documents into context."
5. **Knowledge lifecycle:** capture → classify provenance → verify → promote → reuse → revalidate → supersede or retire. Closeout ≠ trusted guidance.
6. **MAPE-K frozen constraints:** Never reduce evidence requirements for speed. Never convert advisory to oracle. Never broaden scope. Never auto-promote popular knowledge.
7. **Abstention principle:** Cortex's intelligence comes partly from knowing when it must abstain. Not everything is knowable or calculable.

---

## Evidence From The Brain (Source Citations For Evaluation)

The following are verified citations from the Cortex codebase and brain docs. Use these to ground every assessment — no pushback without a source.

### The v0 disease is documented in the brain itself

**Disease A (tool-surface bloat):**
- Source: `docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:38-46`
- Quote: "Disease A — eager tool-surface bloat. All 38 cortex_* MCP tools load into every agent's context whether used or not. Measured: 12,237 tokens for one server."
- Quote: "Status: diagnosed and baseline-measured. **Nothing shipped.** Ships only on a measured A/B delta."

**Disease B (mandatory-pipeline coercion):**
- Source: `docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:42-46`
- Quote: "Disease B — mandatory-pipeline coercion. Four stacked gates on the write path refuse until the agent has driven a chart walk / contract / doc-fetch, each refusal instructing the model to call more tools. Default-on. This manufactures the 'unending tool-call pipeline' — it doesn't prevent it."

**Evidence theater is a named anti-pattern:**
- Source: `docs/BUILD-PLAN.md:472`
- Quote: "an MCP tool that doesn't do anything real is exactly the 'evidence theater' this project's own principles warn against"
- Source: `docs/research/SELF-IMPROVEMENT-LEDGER-MINING-2026-07-07.md:95-110`
- The `evidence_theater_warning` function was actually built and tested in `cortex_core/audit.py`. It flags "fields filled with real-but-irrelevant spans." Tests pass.

### The decision log confirms Cortex's purpose is downstream prevention

- Source: `docs/DECISION-LOG.md:23` (Decision #8)
- Quote: "Unit tests + a scaffolded A/B ≠ a validated harness; shipping without e2e is exactly the fake-success failure Cortex exists to prevent"
- Source: `docs/DECISION-LOG.md:24` (Decision #9, standing)
- Quote: "Tool-surface/context bloat is the exact problem the wrapper + progressive-disclosure skill-tree exist to solve"
- Source: `docs/DECISION-LOG.md:25` (Decision #10)
- Quote: "shipping it as 'deterministic' would be theater"

### The runtime contract concept already exists

- Source: `docs/DECISION-LOG.md:16` (Decision #1)
- Quote: "Two-plane wrapper — Plane A (remote brain, read-only, consent-gated) + Plane B (local cortex_core over his OWN corpus), not a thin remote-only client"
- Source: `docs/DECISION-LOG.md:22` (Decision #7)
- Quote: "Wrapper drives his external-model agent through the engine loop (can't skip); Claude-Code (Plane-1) stays disclosure-only"
- The "service contract" in ChatGPT's definition formalizes what Decision #1 already established. It's not new scope.

### Strong/weak model routing already exists

- Source: `docs/DECISION-LOG.md:20` (Decision #5)
- Quote: "Codex arbitration model BY STAKES — sol@xhigh for core-Cortex-design decisions · terra for one-off reviews · never luna (weak CLI default)"
- Source: `docs/DECISION-LOG.md:21` (Decision #6)
- big-pickle placed as upper-mid tier, later measured 0.964 on BFCL lane
- Fable's plan ignores this. The definition makes it central.

### Provenance tiers exist but are source-level, not value-level

- Source: `docs/DECISION-LOG.md:29` (Decision #14)
- Quote: "Stamp hard_gold/synthetic_consensus/advisory/non_human_verified/human_verified — honesty via labels, never via blocking"
- These are **source provenance** labels (who produced the evidence). ChatGPT's evidence labels (MEASURED/DERIVED/FORECAST/QUALITATIVE/UNKNOWN) are **value confidence** labels (what type of claim a number is making). The brain doesn't have these. That's a real gap.

### Speed over verification is a standing decision

- Source: `docs/DECISION-LOG.md:17` (Decision #2)
- Quote: "Use whatever oracle evidence exists NOW, label by provenance, human review is an optional later upgrade — never a blocker"
- Source: `docs/DECISION-LOG.md:30` (Decision #15, standing)
- Quote: "Speed over verification loops — One strong-model pass (Fable/sol/terra) = human-verify proxy; no review loops; deterministic gates are the backstop"
- ChatGPT's "abstention principle" correction (Cortex must know when to abstain) is NOT in the decision log. The brain says "be honest about provenance" but doesn't say "refuse to act when you don't know." That's a real gap.

### Measurement infrastructure exists but isn't wired

- Source: `docs/HARNESS-SCORECARD-CONSOLIDATED.md:52`
- Quote: "Hallucination catching / eval gates — PARTIAL (lab only) — strong in the lab (objective lanes, anti-evidence-theater, faithfulness); **not a runtime axis**"
- Source: `docs/GAP-CLOSURE-PLAN.md:127`
- Quote: "wire a real metrics.json + evals/results.jsonl ledger"
- Source: `evals/results.jsonl` exists with 36 entries (verified: `wc -l`)
- The cost model isn't unmeasurable — it's unmeasured. Those are different problems.

### The circular validation problem is real and documented

- Source: `docs/HARNESS-SCORECARD-CONSOLIDATED.md:37`
- The "no family bias" claim was retracted as confounded (gold + rubric both Anthropic-authored).
- The decision log does NOT record this retraction. It's in the scorecard but not in the ground-truth decision log.
- ChatGPT's correction #3 ("model recommends; does not authorize or certify") addresses this, but the definition doesn't explicitly name the circular validation risk.

### Gap ledger: built but empty

- Source: `cortex_core/gap_ledger.py` — 907 lines of tested code
- The `gaps/` directory does not exist. Zero data ever populated.
- This is the clearest evidence of the three-stage failure pattern: design without consumer → build without wiring → document without measuring.

### Empty ontology predicates

- Source: `docs/ontology/schema.yaml` — `implements` and `part_of` predicates defined
- Source: `docs/ontology/relations.jsonl` — 0 relations for either predicate (verified: `grep -c`)
- 223 entities, 91 relations exist for other predicates. The ontology works for versioning. It doesn't work for operations.

### BUILD-PLAN was already this ambitious

- Source: `docs/BUILD-PLAN.md:460-502` (Phase 3.1)
- Phase 3.1 alone includes: tool registration, SLIs, status, search, resources, prompts, Claude Code bootstrap, OAuth 2.1, progressive disclosure, stateless protocol core, RC target.
- That's 8+ capabilities in one phase. Six layers is fewer than what BUILD-PLAN describes.
- The 14 subsystems aren't evidence of over-ambition. They're evidence of building everything at 40% instead of one thing at 100%.

### Anti-v0 discipline exists in the brain but was never enforced

- Source: `docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:57`
- Quote: "Ships only on a measured A/B delta (§7), the same gate that shipped retrieval 2.3 and rejected 2.4/2.5/2.6."
- The measurement gate exists. It worked for retrieval (shipped 2.3, rejected 2.4/2.5/2.6). But it was never applied to the gap ledger, the ontology predicates, or the context packets.
- Fable's anti-v0 guardrails formalize what the brain already says but doesn't enforce.

---

## Fable's Build Plan (What Is Proposed)

### The plan's diagnosis

The v0 disease is real but the root cause is more specific: agents built infrastructure without consumers, without wiring, and without measurement. Three-stage failure pattern:
- Design without consumer (capability designed, nobody specified who uses it)
- Build without wiring (code written, never connected to a live path)
- Document without measuring (prose claims substitute for measured outcomes)

### The 4 builds

**BUILD 1: Narrowing Dialogue + Context Packet System** (no prerequisites)
- Context packet store (append-only JSONL, one per decision session)
- Narrowing question generator (LLM call with structured prompt)
- Context router (sits between dialogue and existing search, injects accumulated context)
- Impact matrix formatter (plain-language options with proof, cost, risk, UNPROVEN label)
- Anti-v0 check: wired into a live path, tested with a real human decision, measured

**BUILD 2: Proactive Context Injector** (needs BUILD 1's packet format)
- Entity linker (fuzzy match + BM25 against 153-223 entity names)
- Graph expander (traverse N-hop neighborhood, max 2 hops for scoring, 1 for injection)
- Relevance scorer (graph proximity 30%, entity status 20%, relation type 15%, recency 10%, decision-context 15%, co-occurrence 10%)
- Budget gate + dedup (2K token cap, hard score threshold, clear delimitation)
- Context packet formatter (feeds into BUILD 1's context router)
- Anti-v0 check: eval-gated, turn off if no measured improvement

**BUILD 3: Decision Confidence Ledger** (needs decision recording from BUILD 1)
- Ledger store (append-only JSONL, three dimensions: research sufficiency, path success, agent self-assessment trust)
- Decision recording hook (at outcome lock, append confidence entry)
- Outcome resolution hook (at implementation completion, update outcome field — pending → resolved)
- Calibration query (Brier score over last N decisions)
- Anti-v0 check: outcome field populated by deterministic checks, never LLM judgment

**BUILD 4: Risk Tradeoff Ledger** (needs option research from BUILD 1)
- Ledger store (append-only JSONL, assert_tradeoff events, tension_type enum)
- Tradeoff assertion hook (when options researched, assert tradeoffs for each path)
- Projection query (query by tension_type — "all open security_vs_usability tradeoffs")
- Risk tier derivation (LOW/MEDIUM/HIGH becomes projection from accumulated tradeoffs, not authored label)
- Anti-v0 check: under 12 fields per entry (research warns heavy schemas never get populated)

### What Fable says to abandon
- Ontology as decision-support substrate (keep as versioning system)
- Empty predicates (implements/part_of — 0 relations)
- 13 excess MCP tools (47 → 5 canonical)
- Experimental modules (research_v2_experimental.py)
- Aspirational self-learning flywheel as separate subsystem

### What Fable says to retire (mark complete)
- Retrieval engine (BM25+vector RRF works, nDCG 0.650)
- Eval lanes (63 lanes, 4,551 test cases — strongest asset)

### Anti-v0 guardrails (6 rules)
1. Consumer-first: no capability built unless a specific consumer is named
2. Wire-before-build: no code until wiring path specified
3. Measure-before-ship: every capability ships with a measurement gate
4. One canonical store: each data type has exactly one store
5. Build-complete: not "done" until data populated AND real query returns real result
6. Anti-circular-validation: builder never authors the only checks

### Fable's self-assessed bias risks
- Quick-win bias (builds scoped to be finishable in days)
- Self-interest in validating prior v3 architecture work
- Circular validation risk in confidence scoring
- Doesn't address model routing (tiered lifecycle pipeline)
- Guardrails are structural but enforcement is untested

---

## What We Need From Deep Research

### 1. Gap Analysis: Does Fable's plan deliver the definition?

Go through each of the six platform layers and three loops. For each, answer:
- Does Fable's plan build it? (Yes/No/Partially)
- If partially, what's missing?
- If not, should it be built or is it unnecessary for the first version?

### 2. The 7 Corrections Check

For each of ChatGPT's 7 corrections, does Fable's plan implement it? If not, what needs to change?

### 3. The Cost Model Check

The definition says Cortex must measure: immediate cost + verification cost + expected rework + maintenance burden + future coordination cost + risk of repeated failure.

Does Fable's plan build the measurement for this? If not, what's the smallest addition that would?

### 4. The Downstream Prevention Check

The core value prop is preventing downstream problems. Every v0 failure was a downstream problem. Does Fable's plan directly address this? Does BUILD 1 prevent downstream problems? Or does it just help make better decisions?

Specifically: if BUILD 1 is built and nothing else, does the human stop experiencing the 10 recurring damages (wrong interpretation, rebuilding existing, no research, weak plans, scope expansion, self-certification, lost decisions, repeated failures, doc/state/code disagreement, maintenance cost)?

### 5. The Build Order Challenge

Fable says BUILD 1 first (narrowing dialogue). But the definition says the primary value is downstream prevention. Is the narrowing dialogue the right first build? Or should the first build be the thing that prevents downstream problems (verification, scope lock, pattern matching against past failures)?

Is there a cheaper/faster path to delivering the core value that Fable's plan misses?

### 6. The Runtime Contract Question

The definition says Cortex is runtime-independent — any agent host connects through a service contract. Fable's plan doesn't mention runtime contracts at all. Is this a gap? Or is the runtime contract a later concern that doesn't block the first build?

### 7. The Strong/Weak Model Routing Question

The definition says Cortex coordinates strong and weak models — strong models handle ambiguity/decomposition/recommendation, weak models receive bounded objectives with frozen outputs. Fable's plan doesn't address this. Is this a gap that matters for the first build? Or is it handled by existing infrastructure (model_dispatch.py, fanout.py)?

### 8. What to Merge, Drop, or Reorder

Should any of Fable's 4 builds be merged? Should the confidence ledger and risk ledger be one ledger? Should the injector come before the narrowing dialogue? Should any build be split?

### 9. The Final Metric

The definition's success metric:

> Did Cortex help the human select and obtain a better outcome, with less avoidable effort and uncertainty, while preserving scope, evidence integrity and independent verification — and can those improvements be measured over repeated use?

Does Fable's plan make this metric measurable? If BUILD 1-4 are all built, can we answer this question with numbers?

### 10. Pushback That Serves the Final Output

Push back on Fable's plan where it doesn't deliver the definition. But every pushback must come with a constructive alternative — not "this is wrong" but "this is wrong, here's what would work better, and here's why it serves the final metric better."

No pushback for its own sake. Every critique must trace to: does this help the human make a better decision and get a better outcome?

---

## What We Do NOT Want

1. A new definition. We have the definition. We need to know if the plan delivers it.
2. A new architecture. We have 7+ architecture docs. We need a build plan evaluation.
3. Vague recommendations. "Consider adding verification" is useless. "BUILD 1's Gate 5 should check against KEDB pattern P-003 (scope creep signature) before allowing outcome lock" is useful.
4. Ignoring Fable's self-assessment. Fable flagged its own biases. Deep research should independently verify whether those biases actually affected the plan.
5. More than 3,000 words of prose. Tables, lists, specific references. Under 3,000 words of prose (tables/code excluded).
6. A plan that creates more designed-but-unbuilt artifacts. If deep research recommends something new, it must specify how it gets built, wired, populated, and measured.

---

## Source Materials

### The definition (what must be delivered)
- This brief contains the full definition (above)
- `gpt-communication/2026-07-16/RESPONSE-v4-cortex-definition-corrections.md` — the 7 corrections
- ChatGPT's expanded definition (provided separately in conversation, included above)

### The build plan (what is proposed)
- `source-materials/EVIDENCE-BASED-BUILD-PLAN.md` — Fable's full 38KB plan with evidence inventory

### Today's research (the evidence base)
- `source-materials/decision-confidence-research.md` — confidence scoring research
- `source-materials/research-risk-tradeoff-modeling.md` — risk tradeoff research
- `source-materials/proactive-context-injector-research.md` — context injector research
- `source-materials/RED-TEAM-PUSHBACK-ontology-confidence-risk-injection.md` — 14-argument red team

### Cortex workspace decision log
- `source-materials/MASTER-INDEX-AND-DECISION-LOG.md` — master index and decision log (current decisions vs stale architecture)

### Cortex brain (current state — accessible via live public repos)
- SCC repo: https://github.com/Pukujan/stupidly-simple-cortex (pinned at commit 28f752f0689ceaab316b23214d7ce2327391bd30)
- Wrapper repo: https://github.com/Pukujan/cortex-agent-wrapper (pinned at commit 95d72d5f9e30ba9fca983b3297d0c567df0f1bfc)
- Key files within SCC repo: `docs/ontology/README.md`, `docs/ontology/schema.yaml`, `docs/HARNESS-SCORECARD-CONSOLIDATED.md`, `docs/BUILD-PLAN.md`, `docs/DECISION-LOG.md`, `docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md`

### GitHub issues
- SCC: https://github.com/Pukujan/stupidly-simple-cortex/issues (50 open)
- Wrapper: https://github.com/Pukujan/cortex-agent-wrapper/issues (3 open)

### Prior briefs (context only)
- `gpt-communication/2026-07-16/BRIEF-v4-evidence-based-build-plan.md`
- `../../CHATGPT-DEEP-RESEARCH-BRIEF-v3-2026-07-16.md`

---

## Success Criteria

This brief succeeds if deep research produces:

1. A gap analysis showing exactly which parts of the definition Fable's plan delivers, misses, or partially addresses
2. A verdict on whether BUILD 1 is the right first build (or a constructive alternative)
3. Specific, actionable changes to Fable's plan that make it deliver the definition
4. A measurement plan: how do we know the final metric is being met?
5. Every recommendation traced to: does this help the human get a better outcome?

This brief fails if deep research produces another definition, another architecture, or pushback without constructive alternatives.
