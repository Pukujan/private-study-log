# Strict Alignment Review: Our Expected Outcome vs Existing Cortex Documents

## Date: 2026-07-16
## Method: Side-by-side comparison of our CORTEX-EXPECTED-OUTCOME against 4 existing Cortex documents that represent prior "frozen" decisions

---

## Documents Reviewed

1. **KNOWLEDGE-ESCALATION.md** (July 15) — the search/sufficiency contract built from ChatGPT research
2. **DESIGN-tiered-lifecycle-pipeline.md** (July 6) — the 9-stage routing pipeline (the original "frozen plan")
3. **HARNESS-SCORECARD-CONSOLIDATED.md** (July 13) — what's actually measured vs aspirational
4. **e2e-success-failure-spec.md** (July 14) — the HARD delivery gate spec (rejected freeze by independent reviewer)

---

## The Brutal Finding

**Our Expected Outcome document diverges from every existing frozen document in one critical way: the existing documents were all written by agents, for agents. Ours is written for a human. That's both why the existing docs failed (nobody read them) and why ours might succeed (someone can).**

But divergence isn't automatically right. Let me be strict about where we align, where we diverge, and where we're just plain wrong.

---

## 1. KNOWLEDGE-ESCALATION.md vs Our Expected Outcome

### What it defines (that we align with):
- Three-authority sufficiency model (policy gate → independent evaluator → human owner) ✅ — our human review gate maps to authority #3
- "The driver that performed the research never has final sufficiency authority" ✅ — our freeze rule says the same thing
- "The stop rule is decision-based, not source-count-based" ✅ — our expected outcome is decision-based
- Source registration, quarantine, promotion to brain ✅ — our knowledge promotion gated on human accept
- Receipt system (SUFFICIENT_FOR_DECISION / UNRESOLVED / ABSTAIN) ✅ — our accept/reject/defer maps to this

### Where we DIVERGE (and whether the divergence is justified):

| Topic | KNOWLEDGE-ESCALATION says | Our Expected Outcome says | Divergence justified? |
|---|---|---|---|
| Who decides sufficiency | Three authorities, human is #3 for high-consequence only | Human decides on EVERYTHING, every task | **PARTIALLY WRONG** — we overcorrected. The three-tier model is correct. Not every task needs human review. Low-risk tasks should auto-advance after mechanical floor. Our doc says "nothing moves until human says go" which is too rigid. |
| When human is involved | Only high-consequence domains (legal, medical, financial, security) | Every task, at plan gate AND review gate | **WRONG** — the existing doc's risk-tiered approach is more correct. Our plan gate for every task creates a bottleneck. StackAI's research confirms: "exception-only review" is the production pattern. |
| Research sufficiency | Detailed policy with source classes, authority, independence, freshness, contradictions | We don't mention any of this | **GAP IN OURS** — we designed the human gate but forgot the sufficiency policy that determines WHAT the human is reviewing. The human can't review research quality without the sufficiency receipt. |
| Brain promotion | Reviewed material may be "proposed for canonical Brain promotion with provenance, deduplication, license, freshness, and independent-review gates" | "If accepted: promote to brain" | **OURS IS SIMPLER** — the existing doc has a 6-step promotion pipeline. Ours is one step. The existing doc is more correct. |

### Verdict: ALIGNMENT with 3 DIVERGENCES

We align on principle (human authority, decision-based stop rule, no self-certification). We diverge on scope (every task vs risk-tiered) and depth (we skipped the sufficiency policy). The existing doc is more sophisticated than ours on the research quality side. Ours is more accessible on the human readability side.

**Action: adopt the three-tier risk model from KNOWLEDGE-ESCALATION. Not every task needs human review. Add the sufficiency receipt concept to our expected outcome.**

---

## 2. DESIGN-tiered-lifecycle-pipeline.md vs Our Expected Outcome

### What it defines (the original "frozen plan"):
A 9-stage pipeline with per-stage model routing, reasoning effort optimization, and TDD success conditions. This is the most sophisticated document in Cortex.

### Where we ALIGN:
- Stage 2 (design) = frontier model, max reasoning → our plan gate ✅
- Stage 4 (TDD success conditions) = executable spec → our "expected outcome" ✅
- Stage 3 (phased plan) = frozen artifact → our freeze rule ✅
- Stage 6 (review) = fresh context, strong model → our independent evaluator ✅
- "The implementer receives red tests and a bounded task" → our contract gate ✅
- "No LLM judge in any verdict path" → our human review gate ✅

### Where we DIVERGE:

| Topic | Tiered Lifecycle says | Our Expected Outcome says | Divergence justified? |
|---|---|---|---|
| Model routing | Per-stage model tier + reasoning effort optimization, with floor AND ceiling | Not mentioned | **GAP IN OURS** — the tiered pipeline solved the cost/quality optimization problem. We ignored it. Our "decision matrix" shows cost in hours but not in model tier or reasoning effort. |
| Implementation handoff | "Never prose" — executable TDD tests, machine-checkable postconditions | "Cortex executes within contract" | **OURS IS VAGUE** — the existing doc specifies exactly how the handoff works (executable tests). Ours says "within contract" without defining what the contract contains at the implementation level. |
| Failure routing | Stage 9 classifies failures and routes by class, with escalating model tiers | "Reject → return with feedback" | **OURS IS SIMPLER** — the existing doc has a sophisticated failure taxonomy. Ours is binary (accept/reject). The existing doc is more correct. |
| Sprint contracts | "Negotiated between Generator and Evaluator before each sprint" | Not mentioned | **GAP IN OURS** — the sprint contract concept IS our expected outcome, but we didn't connect it. The existing doc already solved this. |
| Effort curve | "Effort tracks residual uncertainty, which is monotone-decreasing through stages" | Not mentioned | **GAP IN OURS** — this is a key insight: reasoning effort should decrease as uncertainty decreases. Our pipeline doesn't account for this. |

### Verdict: STRONG ALIGNMENT, but we're missing the sophistication

The tiered lifecycle pipeline is the most mature document in Cortex. Our expected outcome aligns with its principles but lacks its depth. The model routing, effort curve, and failure taxonomy are all things we should adopt, not reinvent.

**Action: integrate the 9-stage routing table into our developer pipeline. The model tier + reasoning effort per stage is not optional — it's the cost optimization that makes the pipeline viable.**

---

## 3. HARNESS-SCORECARD-CONSOLIDATED.md vs Our Expected Outcome

### What it defines:
The honest measured-vs-aspirational status of every Cortex capability. This is the reality check.

### What it reveals (that our Expected Outcome ignores):

| What the scorecard says | What our Expected Outcome says | Problem |
|---|---|---|
| "Overall harness validated? PARTIAL (first real evidence 2026-07-13)" with "n=3, one easy task, one driver model" | Implies the pipeline is ready to build | **WE'RE AHEAD OF THE EVIDENCE** — the scorecard says the harness is barely validated. Our expected outcome assumes capabilities that haven't been proven. |
| "~40% of goals measured, ~25% aspirational" | Lists 8 "how a human knows it's done" criteria | **OVER HALF OUR CRITERIA DEPEND ON UNBUILT CAPABILITIES** — we're defining success criteria for a system that's 40% built. |
| "B/detection-only ships over A/vanilla (discipline 1.000 vs 0.667) — but n=3, one easy task" | Scope creep warning, contract gates, MAPE-K | **OUR ADDITIONS ARE IN THE 25% ASPIRATIONAL TIER** — the scorecard would classify most of our new capabilities (path synthesis, scope creep warning, contract gates, MAPE-K monitoring) as aspirational. |
| "Missing deterministic axes: phase_order_score, closeout_fidelity, claim_faithfulness, findability_probe, placement_violations" | Doesn't mention any of these | **GAP IN OURS** — the scorecard already identified what metrics are missing. Our expected outcome doesn't incorporate them. |
| "No committed results ledger — these live only in prose markdown" | Our expected outcome is also prose markdown | **SAME FAILURE MODE** — we're producing another prose document with no machine-verifiable metrics. |

### Verdict: WE'RE REPEATING THE PATTERN

The scorecard is the most honest document in Cortex. It says: most of this is aspirational, the evidence is thin, the metrics are missing, and the results live in prose not ledgers.

Our Expected Outcome document is **another prose document** making claims about capabilities that are aspirational. We're doing exactly what the scorecard warns against.

**Action: every criterion in "How a human knows it's done" must map to a measurable metric from the scorecard. If the metric doesn't exist yet, say so explicitly. Don't imply capability that hasn't been validated.**

---

## 4. e2e-success-failure-spec.md vs Our Expected Outcome

### What it defines:
The HARD delivery gate — a machine-decidable pass/fail contract for a live governed pipeline test. Written July 14, **rejected freeze** by an independent reviewer (sol@xhigh Codex).

### What it reveals:

| What the e2e spec says | What our Expected Outcome says | Problem |
|---|---|---|
| "Status: v1 — NOT design-frozen. sol@xhigh rejected freeze" | Implies we know what the pipeline should be | **WE'RE BUILDING ON UNFROZEN GROUND** — the e2e spec was rejected because "every criterion is passable by a ceremonial harness because nothing anchors the runtime artifacts to a trusted root." Our expected outcome doesn't address trusted provenance at all. |
| "No human sign-off may gate a PASS; deterministic checkers are truth" | Human review gate is the final authority | **DIRECT CONTRADICTION** — the e2e spec explicitly says human sign-off CANNOT gate a pass. Our expected outcome says human sign-off IS the pass. This is a real conflict. |
| "Anti-evidence-theater: a scaffold, format-adapter, or fake closeout is a FAILURE" | Doesn't mention evidence theater | **GAP IN OURS** — the e2e spec identified that agents can fake compliance. Our human review gate doesn't protect against an agent producing a convincing-but-fake closeout. |
| "The state machine never enforced anything" (about prior A/B/C runs) | Our expected outcome assumes the state machine enforces | **WE'RE ASSUMING WHAT'S BEEN PROVEN FALSE** — the e2e spec's own §0.1 says the state machine has NEVER enforced anything in a live run. Our expected outcome assumes it does. |

### The Critical Contradiction

The e2e spec says: **"machine-decidable only — no human sign-off may gate a PASS."**

Our Expected Outcome says: **"Human review gate: accept → DONE"**

These directly contradict each other.

**Who's right?**

The e2e spec's reasoning: human sign-off is subjective, non-reproducible, and creates a bottleneck. Deterministic checkers are truth.

Our reasoning: without human review, the agent grades its own homework, and the knowledge entering the brain is unvalidated.

**Resolution:** Both are right, at different layers.
- **Machine layer**: deterministic checks MUST pass (the e2e spec is correct here)
- **Human layer**: human accepts the OUTCOME (what was built matches what they wanted), not the PROCESS (whether tests passed)

The e2e spec gates on process (did the state machine enforce? did the closeout have real evidence?). Our human gate gates on outcome (does the result match what the human approved?). These are complementary, not contradictory.

**Action: split the gate. Machine-decidable checks for process compliance (from e2e spec). Human review for outcome alignment (from our expected outcome). Neither alone is sufficient.**

---

## Summary: Alignment Scorecard

| Dimension | Aligns with existing docs? | Diverges? | Net assessment |
|---|---|---|---|
| Human authority | ✅ KNOWLEDGE-ESCALATION authority #3 | We over-applied it to every task | Adopt risk-tiered model |
| Freeze before execution | ✅ Tiered lifecycle stage 3 | We missed model routing | Add per-stage routing |
| Expected outcome as anchor | ✅ Tiered lifecycle stage 4 (TDD conditions) | We made it prose, not executable | Must be machine-checkable |
| Independent review | ✅ Tiered lifecycle stage 6 | We didn't specify fresh context | Add context isolation |
| No self-certification | ✅ KNOWLEDGE-ESCALATION, e2e spec | Consistent | Aligned |
| Scope creep warning | ❌ Not in any existing doc | New contribution | Valid — but aspirational |
| Contract gates | ❌ Not in any existing doc | New contribution | Valid — but needs the placement_violations metric from scorecard |
| MAPE-K continuous monitoring | ❌ Not in any existing doc | New contribution | Valid — but aspirational |
| Path synthesis / decision matrix | ❌ Not in any existing doc | New contribution | Valid — this is the actual product |
| Doc health system | ❌ Not in any existing doc | New contribution | Valid — addresses real failure |
| Human-readable output | ❌ Not in any existing doc | New contribution | Valid — addresses real failure |
| Two pipelines (user vs developer) | ❌ Not in any existing doc | New contribution | Valid — addresses conflation |
| Evidence theater prevention | ⚠️ e2e spec has it, we don't | Gap in ours | Must add |
| Trusted provenance | ⚠️ e2e spec requires it, we don't | Gap in ours | Must add |
| Failure taxonomy | ⚠️ Tiered lifecycle has it, we don't | Gap in ours | Must add |
| Sufficiency receipts | ⚠️ KNOWLEDGE-ESCALATION has them, we don't | Gap in ours | Must add |

---

## What This Means For The ChatGPT Handoff

Our Expected Outcome document gets the **direction right** but the **depth wrong**. It identifies capabilities that are genuinely missing (path synthesis, scope creep warning, human-readable output, doc health, two pipelines). But it:

1. **Ignores existing sophistication** — the tiered lifecycle pipeline already solved model routing and effort optimization. KNOWLEDGE-ESCALATION already solved sufficiency policy. We should be building ON these, not replacing them.

2. **Repeats the prose-only failure mode** — the scorecard explicitly warns that results live "only in prose markdown." Our expected outcome is also prose markdown. Every criterion should map to a machine-checkable metric.

3. **Doesn't address evidence theater** — the e2e spec identified that agents can fake compliance. Our human review gate doesn't protect against this.

4. **Contradicts the e2e spec on human sign-off** — but this is resolvable by splitting machine-decidable process checks from human outcome review.

5. **Claims capabilities that are aspirational** — the scorecard says 25% of Cortex is aspirational. Most of our new capabilities (scope creep warning, MAPE-K monitoring, contract gates) fall in that aspirational tier. We should label them as such.

---

## What To Do Before Giving This To ChatGPT

1. **Integrate the risk-tiered model** from KNOWLEDGE-ESCALATION — not every task needs human review
2. **Add per-stage model routing** from the tiered lifecycle — this is the cost optimization
3. **Map every "how a human knows it's done" criterion** to a measurable metric from the scorecard
4. **Add evidence theater prevention** from the e2e spec
5. **Split the gate** — machine-decidable process checks + human outcome review
6. **Label aspirational capabilities explicitly** — don't imply they're ready
7. **Reference the existing documents** — ChatGPT needs to know what already exists so it doesn't duplicate
