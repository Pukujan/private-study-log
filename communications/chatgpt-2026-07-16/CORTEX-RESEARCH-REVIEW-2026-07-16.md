# Initial Review: Does the Target Architecture Match Production Reality?

## Date: 2026-07-16
## Sources: GroktoCrawl research on production AI harness patterns (2025-2026)

---

## What This Review Covers

Research was done on how production AI agent harnesses handle:
1. Human-in-the-loop approval gates
2. Scope creep detection and prevention
3. MAPE-K / autonomic computing feedback loops
4. Architecture contract gates and technical debt prevention
5. Plan approval before execution
6. Human-readable output and decision support

Sources scraped and analyzed:
- Zylos.ai: "Agent Harness Design Patterns" (2026) — the most comprehensive
- StackAI: "Human-in-the-Loop AI Agents: Approval Workflows"
- AI Agentic Engineering Academy: "Managing Scope Creep in Autonomous Systems"
- Fast.io: "Human-in-the-Loop AI Agents: Complete Guide"
- Dart.ai: "How AI helps detect and prevent project scope creep"

---

## Verdict: The Architecture Is Right, But The Framing Is Wrong

### What the research confirms we got RIGHT:

**1. Human plan gate before execution** ✅
Every production harness has this. The Zylos article calls it "Pre-authorization gates" — "Before an agent begins a work block, a human reviews and approves the plan." StackAI calls it "separate intent from execution" — the agent proposes, human approves, then execution happens. This is now industry standard in 2026.

**2. Scope creep as a structural property, not human failure** ✅
The Agentic Engineering Academy article is the most precise: "In agentic projects, scope creep has an additional source that most PM frameworks do not account for — the agent itself. An agent with broad tool access and flexible instructions will naturally expand its behavior over time." Three mechanisms produce agent-driven creep: tool availability, model updates, and prompt drift. This is exactly what we identified.

**3. Generator-Evaluator separation** ✅
Anthropic's production harness uses Planner → Generator → Evaluator. The Evaluator is a separate agent that tests the Generator's work against predetermined criteria. "Never let the generating agent be the evaluating agent." Our closeout-as-draft model aligns with this.

**4. Sprint contracts** ✅
Anthropic uses "sprint contracts" — structured agreements on what testable behaviors demonstrate completion, negotiated BEFORE code is written. This is our "expected outcome" concept exactly. The research confirms this is the key bridge between plan and implementation.

**5. State lives on disk, not in context** ✅
The Ralph Loop pattern: "state lives on disk, not in context." The agent's continuity comes from structured external artifacts. Cortex's audit log and workspace pattern aligns with this.

**6. Build for deletion** ✅
Anthropic's principle: "Find the simplest solution possible, and only increase complexity when needed." And: "build for deletion — modular designs that allow components to be removed or replaced are essential because model releases will continuously invalidate assumptions." Our contract gate with removal plans aligns.

### What the research says we got WRONG or are MISSING:

---

## CRITICAL GAP 1: The Architecture Doc Is The Problem

### What the research says:
Every production harness emphasizes **human readability**. StackAI: "The evidence pack is the difference between a 15-second approval and a 15-minute investigation." The reviewer's job is "not to re-do the agent's work; it's to verify it quickly."

Dart.ai's scope creep system: "Personalized scope summaries — generates role-specific updates on project scope and boundaries for each stakeholder."

The Zylos article: "A 2026 survey of over 900 executives found that over half of production agents run without any security oversight or logging — a systemic risk that harness design can directly address by making approval gates structural rather than optional."

### What we did:
We wrote a 12,000-word architecture document that no human will ever read. Every Cortex document is the same — thousands of words, technical jargon, no summary, no human-readable layer. The system that's supposed to give humans control produced documentation that removes human control.

### The fix:
Every Cortex output must have TWO layers:
1. **Human layer** — 1 paragraph, plain language, what this means for you, what you need to decide
2. **Technical layer** — the full detail, available on request, not the default

This is not optional. The research is clear: production harnesses that don't produce human-readable output fail in practice because the human can't participate.

---

## CRITICAL GAP 2: No Canonical Pipeline For Developer vs User

### What the research says:
Anthropic's harness defines clear roles: Planner (what/why), Generator (how), Evaluator (verification). Each role has its own interface and its own contract.

StackAI defines "allowed actions" per role, "evidence packs" tailored to the reviewer's role, and SLAs per action type.

The Agentic Engineering Academy: "The team must decide what the agent is allowed to know, what it is allowed to do, what evidence it must produce, and which actions require a human decision."

### What we did:
We wrote one pipeline that conflates "developer of Cortex" and "user of Cortex" as the same person with the same needs. They're not.

**Developer of Cortex**: needs to understand the internals, modify the engine, add mechanisms, maintain the codebase. Their pipeline is: research → design contract → implement → test → review → merge.

**User of Cortex**: needs to give intent, understand options, make decisions, verify outcomes. Their pipeline is: state goal → read path comparison → choose → wait → read outcome → accept/reject.

We never separated these. The architecture doc talks about both as if they're the same flow. They're not.

### The fix:
Two canonical pipelines, clearly separated:

**User Pipeline (human-facing):**
```
1. State intent (plain language)
2. Cortex presents paths (decision matrix, plain language)
3. Human chooses path
4. Cortex executes (human waits)
5. Cortex presents outcome (1 paragraph + evidence)
6. Human accepts, rejects, or requests changes
```

**Developer Pipeline (builder-facing):**
```
1. Research existing mechanisms (search brain)
2. Design contract (module, boundaries, dependencies, debt)
3. Contract gate (duplicate check, debt assessment)
4. Human plan gate (developer approves)
5. Implement (within contract)
6. Test (against expected outcome)
7. Review (independent evaluator)
8. Closeout (draft, not final)
9. Human review gate (accept/reject)
10. If accepted: promote to brain, update patterns
```

---

## CRITICAL GAP 3: "Expected Outcome" Is Not A Section — It's The Document

### What the research says:
Anthropic's sprint contracts: "a structured agreement on what specific, testable behaviors will demonstrate completion." This is negotiated BEFORE work begins.

StackAI: "Completion criteria" must be defined before the agent starts. The agent cannot define its own completion criteria.

The Agentic Engineering Academy: "The checklist should include the owner of the workflow, the allowed tools, the risk rating for each tool, the data sources the agent can use, the completion criteria, the review path, and the rollback plan."

### What we did:
We buried "expected outcome" as step 3 of a 13-step pipeline. It should be the FIRST thing defined, before any research or planning. The entire pipeline exists to produce the expected outcome. Without it, everything downstream is the agent deciding what success looks like.

### The fix:
Rename the architecture document itself. It's not "Target Architecture" — it's "Expected Outcome." The document should answer:

1. **What will Cortex be when it's done?** (1 paragraph, plain language)
2. **How will a human know it's done?** (concrete, testable criteria)
3. **What does the human see when they use it?** (mockup of the decision matrix)
4. **What does the developer see when they build it?** (contract gate output)
5. **What happens when it's wrong?** (rollback, recovery, MAPE-K flywheel)

---

## CRITICAL GAP 4: No Document Maintenance System

### What the research says:
Zylos: "AGENTS.md / CLAUDE.md injection — context files that inject repository-level knowledge, conventions, and memory from previous sessions at startup."

The Agentic Engineering Academy: "Behavioral regression testing should be part of every model update process" and "prompt change review should be treated as a code review."

Dart.ai: "Documentation automation — creates detailed records of all scope-related decisions for audit trails and future reference."

### What we did:
Cortex has 491 files, docs spread across `docs/design/`, `docs/harness/`, audit logs, closeouts, patterns — none of them maintained, none of them indexed for human readability, most of them stale. The KNOWLEDGE-ESCALATION.md doc was written July 15 and is already behind the code. No system updates docs when code changes. No system flags stale docs. No human can find what they need.

### The fix:
Documents are treated like code:
1. Every doc has a **last-verified date** and a **stale threshold** (e.g., 7 days for design docs, 30 days for harness docs)
2. When code changes, the system flags associated docs for re-verification
3. Stale docs are marked as "UNVERIFIED — may not reflect current code"
4. The human sees a doc health dashboard: X docs current, Y docs stale, Z docs orphaned
5. No doc enters the brain without a human-readable summary at the top

---

## CRITICAL GAP 5: No Modularization A Human Can Follow

### What the research says:
Vercel's case study is the canonical example: they removed 80% of tools and got 3.5x performance improvement. "More tools create more decision branches. The model spends cognitive budget choosing among options rather than solving the actual problem."

Anthropic: "Find the simplest solution possible, and only increase complexity when needed."

The Agentic Engineering Academy: "Tool audits should be conducted at each deployment: verify that the agent's current tool access matches the approved scope, not the original design document."

### What we did:
Cortex has 135 Python modules with no clear ownership boundaries. mcp.py is 3,074 lines. Multiple competing mechanisms exist (standalone search gate vs state machine phase 1, manual closeouts vs cortex_write_log). No human can look at the structure and understand what goes where. No module has a clear contract saying "this module owns X and nothing else."

### The fix:
1. Every module has a **module contract** at the top of the file: what it owns, what it doesn't, what it exposes, what it depends on
2. The directory structure maps to the pipeline: `search/`, `research/`, `planning/`, `execution/`, `review/`, `knowledge/`
3. A human can open any directory and understand what's inside from the README
4. mcp.py gets split into focused modules (tools, state, search)
5. Duplicate mechanisms get consolidated through the contract gate process

---

## CRITICAL GAP 6: MAPE-K Is Not Just Post-Completion

### What the research says:
IBM's autonomic computing architecture (the origin of MAPE-K) defines it as a **continuous control loop**, not a post-hoc analysis. The Monitor component runs continuously. Analyze processes what Monitor observes. Plan generates corrective actions. Execute implements them. Knowledge is the shared knowledge base that all components draw from.

In production AI harnesses, this maps to:
- **Monitor**: continuous health checks (task success rate, error patterns, scope drift)
- **Analyze**: root cause classification (prompt issue? tool issue? model issue? process issue?)
- **Plan**: corrective action proposal (with cost/risk/scope assessment)
- **Execute**: human-approved correction
- **Knowledge**: the brain, updated with what was learned

The Agentic Engineering Academy confirms: "When the agent fails, decide whether the fix belongs in the prompt, the retrieval layer, the tool contract, the permission model, the evaluation suite, or the human process."

### What we did:
We treated MAPE-K as a post-completion flywheel only. It should be continuous. The monitor should be watching the codebase structure, the doc health, the scope creep signals — all the time, not just after a task completes.

### The fix:
MAPE-K runs as a background process:
1. **Monitor** continuously checks: doc staleness, duplicate mechanisms, module size growth, scope creep signals
2. **Analyze** classifies findings: is this urgent? Is it debt? Is it a duplicate?
3. **Plan** proposes corrections with cost/risk/scope
4. **Execute** only after human approval
5. **Knowledge** feeds back into the brain

---

## What Production Harnesses Do That Cortex Doesn't

| Capability | Production Standard (2026) | Cortex Today |
|---|---|---|
| Human-readable output (1 paragraph + evidence) | Standard (StackAI, Zylos) | NOT BUILT — 12,000 word docs |
| Plan approval before execution | Standard (Zylos, StackAI, Anthropic) | NOT BUILT |
| Scope creep detection (continuous) | Emerging (Dart.ai, Agentic Engineering Academy) | NOT BUILT |
| Generator-Evaluator separation | Standard (Anthropic) | NOT BUILT (agent grades own work) |
| Sprint contracts / expected outcome | Standard (Anthropic) | NOT BUILT |
| Tool audits | Recommended (Agentic Engineering Academy) | NOT BUILT |
| Behavioral regression testing | Recommended (Agentic Engineering Academy) | NOT BUILT |
| Document health monitoring | Emerging | NOT BUILT |
| Module contracts | Standard (Vercel, Anthropic) | NOT BUILT |
| Build for deletion | Standard (Anthropic) | NOT BUILT |
| Idempotent execution | Standard (StackAI) | NOT BUILT |
| Evidence packs for reviewers | Standard (StackAI) | NOT BUILT |
| Risk-tiered approval (auto-approve low risk) | Standard (StackAI) | NOT BUILT |
| Audit trail (who approved what when) | Standard (StackAI) | BUILT (audit log) but not human-readable |
| Continuous MAPE-K monitoring | Standard (IBM autonomic) | NOT BUILT (post-completion only in our design) |

---

## What The Architecture Doc Should Actually Be

The doc should be called **"Cortex Expected Outcome"** and it should be short. The research is clear: production systems produce human-readable output first, technical detail second.

### The Expected Outcome Document Should Be:

```
# Cortex Expected Outcome

## What Cortex Will Be
[1 paragraph, plain language]

## How A Human Knows It's Done
[Concrete, testable criteria — not "it works" but "human can read 1 paragraph and make a decision in 30 seconds"]

## What The Human Sees
[The decision matrix mockup — paths with value %, risk %, cost, scope creep %]

## What The Developer Sees
[The contract gate — module, boundaries, dependencies, debt, duplicate check]

## What Happens When It's Wrong
[Rollback, recovery, MAPE-K correction cycle]

## The Two Pipelines
[User pipeline — 6 steps, plain language]
[Developer pipeline — 10 steps, with contract gates]
```

That's it. No 12,000 words. No technical jargon in the human layer. The technical detail lives in the code, in the contract gates, in the import maps — not in a document the human is expected to read.

---

## Research Sources

1. **Zylos.ai** — "Agent Harness Design Patterns: The Infrastructure Layer That Makes AI Work" (March 2026)
   - 6 canonical harness components
   - Generator-Evaluator loops
   - Sprint contracts
   - Ralph Loop pattern
   - Build for deletion principle
   - "The model is commodity, the harness is moat"

2. **StackAI** — "Human-in-the-Loop AI Agents: Approval Workflows"
   - 5 approval workflow patterns
   - Evidence packs
   - Risk-tiered approval
   - Idempotent execution
   - Separate intent from execution
   - SLAs and escalation

3. **AI Agentic Engineering Academy** — "Managing Scope Creep in Autonomous Systems"
   - Agent-driven scope creep (3 mechanisms: tools, model updates, prompt drift)
   - Tool audits
   - Behavioral regression testing
   - Prompt change review
   - Operating checklist
   - Learning loop classification

4. **Fast.io** — "Human-in-the-Loop AI Agents: Complete Guide"
   - 3 HITL patterns: Approval Gate, Escalation Trigger, Collaborative Workspace
   - Confidence-based escalation

5. **Dart.ai** — "How AI helps detect and prevent project scope creep"
   - AI-powered early warning systems
   - Language pattern analysis for scope creep
   - Automated boundary protection
   - Impact prediction before approval
