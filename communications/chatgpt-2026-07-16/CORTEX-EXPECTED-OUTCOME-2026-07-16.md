# Cortex Expected Outcome (v2 — Integrated)

*Revised after strict alignment review against 4 existing Cortex documents: KNOWLEDGE-ESCALATION.md, DESIGN-tiered-lifecycle-pipeline.md, HARNESS-SCORECARD-CONSOLIDATED.md, e2e-success-failure-spec.md. See CORTEX-ALIGNMENT-REVIEW-2026-07-16.md for the full review.*

---

## What Cortex Will Be

Cortex is a future planning tool. You give it a vague goal. It researches, synthesizes 2-4 paths with real trade-offs in plain numbers, warns you about scope creep risk, and waits for you to choose. Nothing gets built until you say go. After you choose, it executes within a contract that prevents duplicate code and structural debt. When it's done, you verify the outcome matches what you approved. If it doesn't, it goes back. If it does, the knowledge enters the brain and MAPE-K calibrates future predictions.

---

## What Already Exists (Do Not Duplicate)

Cortex already has sophisticated mechanisms. Any consolidation plan must build ON these, not replace them:

| Existing Document | What It Solved | Status |
|---|---|---|
| KNOWLEDGE-ESCALATION.md (July 15) | Three-authority sufficiency model, research receipts, source policy, brain promotion pipeline | Built, not enforced |
| DESIGN-tiered-lifecycle-pipeline.md (July 6) | 9-stage model routing with per-stage reasoning effort, TDD success conditions, failure taxonomy | Designed, partially implemented |
| HARNESS-SCORECARD-CONSOLIDATED.md (July 13) | Honest measured-vs-aspirational status, improvement decision log, metric gaps identified | Maintained by hand |
| e2e-success-failure-spec.md (July 14) | Machine-decidable pass/fail contract, anti-evidence-theater, trusted provenance requirement | REJECTED freeze — needs trusted substrate |
| research_sufficiency.py (867 lines) | Receipt store, policy/proposal/attestation validation, Ed25519 envelopes | Built, not wired to enforcement |
| state_engine.py | 7-phase state machine (SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT → DONE) | Built, turned ON today, never enforced in live run |
| cortex-assured-driver (Hermes hook) | Pre-tool gate: blocks writes until search performed, escape hatch, state machine awareness | Built, 19 tests pass |

---

## How A Human Knows It's Done

Each criterion maps to a measurable metric. If the metric doesn't exist yet, it's labeled **[ASPIRATIONAL]**.

1. You state a goal in one sentence and get back a decision matrix in under 30 seconds of reading
2. Every path shows value %, risk %, cost, scope creep %, what it unlocks, what it shuts off
3. Nothing is built until you explicitly say "go" **[ASPIRATIONAL — hook exists but doesn't gate on plan approval]**
4. When work finishes, you get a 1-paragraph summary + evidence pack — not a 12,000-word doc
5. You can accept or reject in one tap
6. The system warns you before scope expands beyond what you approved **[ASPIRATIONAL]**
7. Documents are short, current, and human-readable — or they're flagged as stale **[ASPIRATIONAL]**
8. The codebase structure is understandable by opening any folder and reading its README **[ASPIRATIONAL — 135 modules, no READMEs, mcp.py is 3,074 lines]**

**Existing metrics from scorecard that must be satisfied:**
- `phase_order_score` — did the state machine enforce phase ordering? **[MISSING]**
- `closeout_fidelity` — do files-changed match git diff? Do tests-passed match recorded exit codes? **[MISSING]**
- `claim_faithfulness` — do citations actually support claims? **[MISSING]**
- `findability_probe` — can a new agent find relevant brain content? **[MISSING]**
- `placement_violations` — does code live where the contract says it should? **[MISSING]**

---

## Risk-Tiered Human Review (not every task)

*Adopted from KNOWLEDGE-ESCALATION.md's three-authority model. Our v1 was wrong — human review on every task creates a bottleneck.*

| Risk tier | What happens | Human involvement |
|---|---|---|
| **LOW** (config change, test patch, doc update) | Mechanical floor passes → auto-advance | None. Sampled audit (5-20%) for drift detection |
| **MEDIUM** (new feature, refactoring, API change) | Mechanical floor + independent evaluator | Human reviews outcome, not process |
| **HIGH** (architecture change, security, data model, brain promotion) | All authorities must pass | Human approves plan AND outcome |

Auto-approve (for LOW tier only) requires ALL five:
1. Serves an already-approved expected outcome
2. No future code debt
3. No future structure debt
4. Reversible
5. Within scope

MAPE-K trust profile determines auto-approve eligibility based on historical outcomes for similar action types.

---

## The Two Gates (Split, Not Merged)

*v1 had one human gate. The e2e spec (July 14) says "no human sign-off may gate a PASS." Resolution: two layers, complementary.*

### Gate 1: Machine-Decidable Process Check (from e2e spec)
- Did the state machine enforce phase ordering? → `phase_order_score`
- Did the closeout have real evidence (not theater)? → `closeout_fidelity`
- Do citations support claims? → `claim_faithfulness`
- Did the agent search before mutating? → `research_cited`
- Were tools within approved scope? → `tool_audit`
- **NO LLM JUDGE in any verdict path** — deterministic checkers only
- **Anti-evidence-theater**: scaffold, format-adapter, or fake closeout = FAILURE, not pass

### Gate 2: Human Outcome Review (from our expected outcome)
- Does the result match what the human approved as the expected outcome?
- Did scope creep occur despite warnings?
- Is the knowledge worth promoting to the brain?
- Human accepts, rejects, or defers

**Neither gate alone is sufficient.** Machine checks verify process compliance. Human review verifies outcome alignment. Both must pass for DONE.

---

## Per-Stage Model Routing

*Adopted from DESIGN-tiered-lifecycle-pipeline.md. Our v1 ignored cost optimization entirely.*

| Stage | Model Tier | Reasoning Effort | What It Optimizes |
|---|---|---|---|
| Research (fetch/sweep) | micro–small | none | recall per dollar |
| Research (frame/decompose) | mid | HIGH | sub-question boundary quality |
| Research (consolidate) | strong | HIGH | synthesis faithfulness |
| Path synthesis | frontier | MAX | decision quality, trade-off clarity |
| Plan | frontier (same session) | HIGH | decomposition into machine-consumable DAG |
| TDD success conditions | strong | HIGH | executable spec richness |
| Implementation | mid | LOW | pass visible tests at minimum cost |
| Review | strong (FRESH CONTEXT) | HIGH | catch what tests can't |
| Test run | small–mid (ceiling enforced) | none | objective verdict per dollar |
| MAPE-K analysis | mid | medium | prediction calibration |

**Effort curve**: tracks residual uncertainty, which is monotone-decreasing through stages. Front-loaded spike (research + path synthesis + plan), cheap flat middle (implementation, test), one paid checkpoint (review).

---

## Research Sufficiency Receipts

*Adopted from KNOWLEDGE-ESCALATION.md. Our v1 skipped this — the human can't review research quality without knowing what they're reviewing.*

Before plan gate, the research phase must produce a sufficiency receipt:

- **SUFFICIENT_FOR_DECISION** — frozen receipt authorizes dependent work
- **UNRESOLVED** — reopens bounded research
- **ABSTAIN** — closes honestly without unlocking dependent work

The receipt contains:
1. Original question + decomposed sub-questions
2. Brain/tenant queries, hits, corpus paths read
3. Coverage, corroboration, freshness, contradictions, unanswered questions
4. External search queries + outcomes (including failures)
5. Every accepted source's URL, type, authority, independence group, capture time, hash
6. Frozen report hash + success-contract criteria it informed
7. Any human decision, advisory fallback, or non-pass status

The driver that performed the research **never has final sufficiency authority**.

---

## Evidence Theater Prevention

*Adopted from e2e-success-failure-spec.md. Our v1 didn't address this.*

An agent can produce a convincing-but-fake closeout. Protections:

1. **No format adapters** — if the closeout schema doesn't match, it FAILS, not passes with a bridge
2. **Content fidelity check** — files-changed in closeout must match `git diff`; tests-passed must match recorded exit codes
3. **Citation verification** — citations must actually support the claims they're attached to
4. **No LLM judge in verdict path** — judges may annotate, never decide
5. **Trusted provenance** — runtime artifacts must be anchored to a trusted root (the e2e spec's §1.2 substrate, currently unbuilt)

---

## What The Human Sees

### Decision Matrix (default view — plain numbers, no technical jargon)

| | Path A: Injection Layer | Path B: State Machine | Path C: Human Gates | Path D: Phased (All) |
|---|---|---|---|---|
| **Value** | 85% reduction in repeated work | 70% reduction in skipped research | 90% reduction in unvalidated knowledge | 95% root cause addressal |
| **Risk** | 20% scope creep | 35% weak models break | 10% human bottleneck | 40% scope creep, 15% integration |
| **Cost** | ~8 hours | ~4 hours | ~6 hours + 15 min/task | ~18 hours over 3 phases |
| **Unlocks** | cross-session memory, auto-closeouts | phase-gated pipeline, validated closeouts | trusted knowledge promotion | everything above |
| **Shuts off** | standalone search gate | ad-hoc closeouts | autonomous closeouts | nothing (phased) |
| **Scope creep** | MEDIUM (40%) | LOW (15%) | LOW (10%) | HIGH (55%) |

### Technical elaboration (optional, on request)
- "What does Path A involve?" → architecture breakdown
- "Why 85%?" → evidence chain with prior incidents
- "What does 'scope creep into search redesign' mean?" → specific risk description

---

## Scope Creep Warning System **[ASPIRATIONAL]**

Fires at:
- Every state machine phase transition
- When work items not in the approved spec are added
- When task duration exceeds estimate by 2x
- When scope (mechanisms, files, components) exceeds approved plan by 30%

What the human sees:
```
⚠ SCOPE WARNING
Approved: 3 mechanisms, ~8 hours
Current: 5 mechanisms, ~14 hours
Overage: +2 mechanisms (+67%), +6 hours (+75%)
New item: "telemetry layer" was not in approved plan
Action: Continue expanded scope? Return to original? Stop?
```

Scope creep prediction based on (from GroktoCrawl research):
1. Historical scope creep (from audit log)
2. Subsystem breadth (from import map)
3. Dependency depth (from IMPORT-MAP.json)
4. Integration surface (how many existing mechanisms need modification)
5. Precedent (has this type of work crept before?)

---

## The Freeze Rule

Until the human says "go":
- No building. No code changes. No file writes. No state mutations.
- The agent can search, research, synthesize paths. It cannot act.

---

## Contract Gates **[ASPIRATIONAL]**

Before implementation of any approved feature:
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

Must integrate with `placement_violations` metric from scorecard.

---

## MAPE-K (Three Functions) **[ASPIRATIONAL]**

1. **Prediction calibration** (post-completion): predicted vs actual outcomes
2. **Auto-approve eligibility** (pre-action): trust profile from historical outcomes
3. **Structural health monitoring** (ongoing): duplicates, coupling, debt accumulation

---

## The Two Pipelines

### User Pipeline (human-facing)
```
1. State intent (one sentence, vague is fine)
2. Cortex researches + produces sufficiency receipt
3. Read decision matrix (paths with %, cost, risk)
4. Choose a path (or reject all, or request modifications)
5. Wait (Cortex executes within contract)
6. Read outcome (1 paragraph + evidence pack)
7. Accept, reject, or request changes
```

### Developer Pipeline (builder-facing)
```
1. Search brain for existing mechanisms (cortex_search)
2. Research external evidence (cortex_research) → sufficiency receipt
3. Path synthesis (2-4 paths with trade-offs)
4. Human plan gate (developer approves path + expected outcome)
5. Contract gate (module, boundaries, debt, duplicate check)
6. Implement (within contract, per-stage model routing)
7. Test (against approved TDD success conditions)
8. Machine-decidable process check (phase_order, closeout_fidelity, claim_faithfulness)
9. Independent evaluator review (fresh context, strong model)
10. Human review gate (accept / reject / defer)
11. If accepted: promote to brain, update patterns, MAPE-K calibrates
```

---

## Document Health **[ASPIRATIONAL]**

- Every doc has last-verified date + stale threshold
- Code changes flag associated docs for re-verification
- Stale docs show "⚠ UNVERIFIED"
- Doc health dashboard: X current, Y stale, Z orphaned
- No doc enters brain without human-readable summary at top

---

## Codebase Structure (Target) **[ASPIRATIONAL]**

```
cortex_core/
├── search/          ← brain search, scope packs, retrieval
├── research/        ← external evidence gathering, sufficiency receipts
├── planning/        ← path synthesis, decision matrix, contract gates
├── execution/       ← state machine, implementation tracking
├── review/          ← evaluator, closeout drafting, evidence packs
├── knowledge/       ← brain promotion, patterns, MAPE-K calibration
└── harness/         ← Hermes hooks, MCP server, lifecycle
```

Every directory has a README readable in under 60 seconds. Every module has a contract header.

---

## Existing Capabilities Status (Honest)

| Capability | Status | Evidence |
|---|---|---|
| Brain search (BM25 + vector RRF) | BUILT | nDCG@5 0.650, chunk_recall@5 0.733 |
| Research sufficiency receipts | BUILT | research_sufficiency.py, 867 lines |
| State machine (7 phases) | BUILT, ON, NEVER ENFORCED IN LIVE RUN | e2e spec §0.1: "state machine never enforced anything" |
| Hermes hook (cortex-assured-driver) | BUILT | 19 tests pass |
| Audit log | BUILT | Not human-readable |
| KEDB patterns | BUILT | Promotion requires ≥2 occurrences |
| Path synthesis | NOT BUILT | — |
| Scope creep warning | NOT BUILT | — |
| Contract gates | NOT BUILT | — |
| Human-readable output | NOT BUILT | — |
| Doc health monitoring | NOT BUILT | — |
| MAPE-K continuous monitoring | NOT BUILT | — |
| Machine-decidable process checks | NOT BUILT | 5 metrics identified, none implemented |
| Trusted provenance substrate | NOT BUILT | e2e spec rejected freeze without it |
| Per-stage model routing | DESIGNED | Not implemented in live pipeline |
