# Risk Tradeoff Modeling in Decision Trees and Dependency Graphs

## Research Summary

**Question:** When a decision has multiple paths, how do you model "too loose = problem X, too strict = problem Y" as a structured relationship between decisions?

**Context:** Cortex has risk tiers (LOW/MEDIUM/HIGH) defined in prose but not implemented. 53 GitHub issues have no dependency mapping. The ontology schema has `depends_on`, `implements`, `part_of` — but these are empty. Codex and Fable (July 13 arbitration) said the ontology should be a derived projection, not the substrate.

---

## (a) Structures That Exist for Modeling Risk Tradeoffs

### 1. Risk Matrices (PMBOK / MIL-STD-882 / ISO 31000)

**Structure:** A 2D grid of Likelihood × Severity, producing a categorical risk level (Low/Medium/High/Critical).

**How it models tradeoffs:** It doesn't — at least not directly. The risk matrix assigns a single risk score to a single hazard. The tradeoff is implicit: you choose which risks to mitigate and which to accept, given finite resources.

**Key limitation (Cox, 2008 "What's Wrong with Risk Matrices?"):**
- **Poor resolution:** Matrices can correctly compare <10% of randomly selected hazard pairs.
- **Range compression:** Quantitatively very different risks get identical ratings.
- **Worse than random:** For negatively correlated frequency/severity, matrices produce worse-than-random decisions.
- **Ambiguous inputs:** Different users get opposite ratings of the same quantitative risk.
- **Suboptimal allocation:** Categories don't support effective resource prioritization.

**Relevance to Cortex:** The existing LOW/MEDIUM/HIGH tier system is exactly this kind of flat matrix. It has the same problems — it cannot express the tradeoff relationship itself, only the resulting label.

### 2. FMEA (Failure Mode and Effects Analysis)

**Structure:** A worksheet with columns for: Failure Mode → Cause → Effect → Severity (S) → Probability (P) → Detection (D) → RPN (Risk Priority Number = S × P × D).

**How it models tradeoffs:** Each failure mode gets a single RPN. The tradeoff is between mitigation strategies: reducing severity vs. reducing probability vs. improving detection. FMEA treats these as independent axes you can tune separately, but the tradeoff itself is not a first-class relationship — it's an analyst's mental model.

**Key structural insight:** FMEA is **inductive** (forward logic) — it goes from component failure to system effect. It's a single-point-of-failure analysis: "one failure at a time." It does NOT model interactions between failure modes or between mitigation decisions.

**Extension — FMECA:** Adds criticality analysis (RPN ranking) but still doesn't model tradeoffs between decisions.

### 3. Fault Tree Analysis (FTA)

**Structure:** A deductive (backward logic) tree from a top event (undesired state) down to basic events. Uses Boolean logic gates:
- **AND gate:** All inputs must occur for the output to occur (redundancy — a safety feature).
- **OR gate:** Any single input causes the output (single points of failure).
- **Priority AND / Inhibit gates:** Specialized variants.

**How it models tradeoffs:** FTA models the *combinatorial structure* of how failures combine. A tradeoff appears as a design choice: adding an AND gate (redundancy) reduces probability but increases complexity/cost. But FTA doesn't quantify the tradeoff — it computes the probability of the top event given the tree structure.

**Key structural insight:** The AND/OR gate structure IS the primitive for modeling "too loose vs. too strict":
- **Too loose (permissive design):** More OR gates → more paths to failure → higher probability.
- **Too strict (conservative design):** More AND gates → more redundant barriers → higher complexity/cost → potential for new failure modes.

This is the closest classical structure to what Cortex needs, but it's unidirectional (failure → effect), not bidirectional (decision A → risk of overconstraint vs. decision B → risk of underconstraint).

### 4. Bayesian Decision Networks / Influence Diagrams

**Structure:** A DAG with four node types:
- **Decision nodes** (rectangles): choices to be made.
- **Uncertainty nodes** (ovals): random variables with conditional probability tables.
- **Deterministic nodes** (double ovals): functions of other nodes.
- **Value nodes** (octagons/diamonds): utility functions.

Three arc types:
- **Functional arcs** → value node: what the utility depends on.
- **Conditional arcs** → uncertainty node: probabilistic conditioning.
- **Informational arcs** → decision node: what's known when the decision is made.

**How it models tradeoffs:** This is the most powerful formalism. The value node encodes a utility function that can explicitly represent competing objectives. "Too loose" and "too strict" are different decision alternatives, and the value node computes expected utility for each. The tradeoff is **quantified** — you can compute the expected utility of each path and pick the optimum.

**Value of information:** Influence diagrams uniquely support computing "how much should I pay to reduce uncertainty before deciding?" — the expected value of perfect/imperfect information. This is exactly the "should I gather more evidence before committing to a risk tier?" question.

**Key structural insight:** The utility function IS the tradeoff model. Instead of "too loose = problem X, too strict = problem Y" as separate edges, you encode both outcomes in a single value node that takes the decision and the uncertain state as inputs. The decision with the highest expected utility is the answer.

**Limitation:** Requires specifying probability distributions and utility functions. For a system like Cortex where risks are qualitative ("security risk", "maintenance burden"), this is heavy machinery that may not have the data to calibrate.

### 5. Game Theory Payoff Matrices

**Structure:** An N-dimensional matrix where each cell contains a payoff (or cost) for a combination of strategies chosen by the players.

**How it models tradeoffs:** Directly. Each row is a strategy, each column is a scenario/opponent move, and each cell is the outcome. The tradeoff is literally the matrix: different cells have different costs, and you choose the strategy that minimizes worst-case loss (minimax) or maximizes expected payoff.

**Key structural insight:** A payoff matrix is the simplest possible structure for "too loose vs. too strict":
```
                    Scenario A (benign)    Scenario B (adversarial)
Too loose           ✓ fast                ✗ catastrophic breach
Too strict          ✗ slow/overengineered ✓ safe
Balanced            ~ acceptable          ~ acceptable
```

But payoff matrices require enumerating all scenarios, which is the hard part. They also assume you can quantify payoffs, which is the same problem as Bayesian networks.

### 6. AI/ML System Approaches

#### OpenAI — Deliberative Alignment
OpenAI's o1/o3 models use "deliberative alignment": the model reasons about safety policies at inference time, considering the tradeoff between helpfulness and safety. The structure is:
- A set of safety rules (parsed from a spec).
- At inference, the model reasons about whether a request violates those rules.
- The "tradeoff" is implicit in the reasoning chain, not structured as a graph edge.

**Relevance:** This is LLM-as-judge over rules, not a structured tradeoff model. It demonstrates that for nuanced tradeoffs, the reasoning is done by the model, not by a pre-computed graph. The tradeoff is emergent from the rules + reasoning, not stored as an explicit relationship.

#### Anthropic — Constitutional AI
Claude's Constitutional AI uses a constitution (set of principles) and has the model evaluate its own outputs against those principles. Tradeoffs between principles (e.g., honesty vs. helpfulness) are resolved by the model's reasoning, not by a pre-computed priority ordering.

**Key insight:** Both OpenAI and Anthropic treat tradeoff resolution as a **runtime reasoning problem**, not a **structural data problem**. They don't model "too loose → problem X" as a stored edge. They store principles and let the model reason about the tradeoff at decision time.

#### LangGraph — Risk-Aware Routing
LangGraph models workflows as state graphs with conditional edges. A "router" node uses structured output (e.g., a Pydantic model with a `step` field) to route to different downstream nodes.

**Risk-aware routing pattern:**
```
Input → Router → [safe_path | unsafe_path | review_path]
```
The router is an LLM call with structured output. The "risk tradeoff" is: route everything to review = safe but slow; route autonomously = fast but risky. The tradeoff is encoded in the routing logic (a function), not as a data structure.

**Key insight:** LangGraph models the tradeoff as a **conditional edge** in the execution graph. The routing decision is made at runtime, not stored as a persistent relationship. This is closer to what Cortex needs — a graph structure where edges represent risk-bearing decisions — but LangGraph's edges are transient (per-execution), not persistent (across the project lifecycle).

### 7. Issue Tracker Models

#### GitHub
- **Sub-issues** (replacing retired tasklists): hierarchical parent-child decomposition.
- **Linked PRs**: `closes #N`, `fixes #N`, `resolves #N` keywords.
- **No native dependency/blocking model.** GitHub issues don't have a `blockedBy` field. Workarounds: labels (`blocked`), custom GitHub Projects fields, or third-party actions.
- **No risk field at all.** No priority field natively (only labels or Projects custom fields).

#### Jira
Jira has the richest issue-link model:
- `blocks` / `is blocked by`
- `is caused by` / `causes`
- `is duplicated by` / `duplicates`
- `is implemented by` / `implements`
- `clones` / `is cloned by`
- `relates to`
- `reviews` / `is reviewed by`

Jira also has native priority (Blocker/Critical/Major/Minor/Trivial) and native risk fields (in advanced boards).

**Key structural insight:** Jira's link types are **directional binary predicates** between issues. They model *that* A blocks B, but not *why* or *what the tradeoff is*. A `blocks` edge says "B can't start until A is done" — it's a scheduling constraint, not a risk tradeoff.

#### Linear
Linear has:
- Priority (Urgent/High/Medium/Low/No priority)
- Dependencies (blocks / blocked by — same as Jira)
- Project + cycle assignment

Linear's model is simpler than Jira's: fewer link types, but cleaner priority semantics. Dependencies are first-class in the UI (visible on cards, enforced in scheduling).

**Across all three:** No issue tracker models risk tradeoffs. They model dependencies (blocks/blocked_by) and priority (urgency), but not the relationship "choosing path A creates risk X, choosing path B creates risk Y, and these risks are in tension."

---

## (b) Graph (Ontology) vs. Flat Ledger — Where Should Risk Tradeoffs Live?

### The Cortex Arbitration (July 13, Codex + Fable)

Both agents converged: **the ontology should be a derived projection, not the substrate.** The substrate is a flat append-only ledger (JSONL). The ontology is a view computed from it.

This is already implemented:
- `gap_ledger.py`: append-only JSONL, `blocks`/`blocked_by`/`supersedes`/`superseded_by` as authored fields, `_blocker_graph()` derives the bidirectional dependency graph.
- `project_state_projection.py`: "pure projections" — the reducer owns truth, projections render it.
- `ontology_seed.py`: scans the corpus and upserts entities + structural relations.
- `docs/ontology/schema.yaml`: 11 relation types, all structural facts (depends_on, supersedes, implements, etc.).

### Recommendation: Risk Tradeoffs Belong in the Ledger, Projected into the Ontology

**Why:**

1. **Risk tradeoffs are decisions, not facts.** The ontology schema deliberately only materializes relations it can derive "structurally and correctly" (ontology_seed.py docstring: "it does not guess semantic edges a wrong one of which would be worse than none"). A risk tradeoff is a judgment call — it should be authored as an event in the ledger, with provenance, not asserted as a structural fact.

2. **The ledger is already the pattern for mutable state.** Gaps have `blocks`/`blocked_by` as authored events. Risk tradeoffs are the same shape: an authored assertion that decision A is in tension with decision B, with a reason and provenance.

3. **The ontology can project risk tradeoffs as edges.** A `risk_tradeoff` edge in the ontology would be derived from ledger entries — just as the blocker graph is derived from `blocked_by` events. This keeps the ontology as a view, per the arbitration.

4. **Flat ledger avoids graph rigidity.** A risk tradeoff is inherently multi-dimensional (the same decision can have tradeoffs along security, performance, maintainability axes). A graph edge is binary (A—tradeoff—B), which loses the dimensionality. A ledger entry can carry structured fields for each axis.

### What Goes Where

| Layer | What lives here |
|---|---|
| **Ledger (JSONL)** | Authored risk events: "decision X has tradeoff Y with severity Z along axis W" |
| **Ontology (derived)** | `risk_tradeoff` edges projected from ledger entries; risk tier computed from accumulated tradeoffs |
| **Projection (rendered)** | Risk dashboard, dependency+risk graph visualization, "what are the open tradeoffs for this gap?" |

---

## (c) Concrete Schema for a Risk-Tradeoff Edge/Field

### Ledger Entry (the substrate)

```jsonl
{
  "schema_version": 1,
  "event_id": "risk-event-019234ab-...",
  "event": "assert_tradeoff",
  "tradeoff_id": "RISK-001",
  "decision_id": "GAP-CORTEX-005",
  "path": "permissive_validation",
  "axis": "security",
  "risk_if_taken": {
    "tier": "HIGH",
    "description": "SSRF bypass via DNS rebinding remains possible",
    "failure_mode": "unauthorized_internal_access"
  },
  "risk_if_not_taken": {
    "tier": "MEDIUM",
    "description": "Valid requests with edge-case IPs get rejected, breaking legitimate users",
    "failure_mode": "false_positive_rejection"
  },
  "tension_type": "precision_vs_recall",
  "mitigation": "pin IPs + add allowlist for known-safe ranges",
  "evidence": [
    {"kind": "test", "path": "tests/test_ssrf_pinning.py"},
    {"kind": "url", "path": "https://owasp.org/Top10/A10_2021-Server-Side_Request_Forgery"}
  ],
  "author_agent": "opus",
  "reason": "SSRF fix needs IP pinning but that breaks legitimate internal API calls",
  "created_at": "2026-07-16T12:00:00Z",
  "status": "open"
}
```

### Key Fields Explained

| Field | Type | Purpose |
|---|---|---|
| `tradeoff_id` | string | Stable ID for the tradeoff assertion (RISK-NNN) |
| `decision_id` | string | FK to the gap/issue/task this tradeoff is about |
| `path` | string | Which decision path this tradeoff describes (e.g., "permissive", "strict", "balanced") |
| `axis` | enum | Which risk dimension: `security`, `performance`, `maintainability`, `correctness`, `usability` |
| `risk_if_taken` | object | What goes wrong if you choose this path: {tier, description, failure_mode} |
| `risk_if_not_taken` | object | What goes wrong if you DON'T choose this path (the tension) |
| `tension_type` | enum | Classifies the tradeoff: `precision_vs_recall`, `security_vs_usability`, `strictness_vs_completeness`, `speed_vs_safety`, `flexibility_vs_predictability` |
| `mitigation` | string? | Proposed or applied mitigation that resolves the tension |
| `evidence` | array | Tests, URLs, commits that support the risk assessment |
| `status` | enum | `open`, `mitigated`, `accepted`, `superseded` |

### Ontology Projection (derived from ledger)

```yaml
# Added to docs/ontology/schema.yaml relation_types:
risk_tradeoff_with:
  description: >
    Subject decision has a risk tension with object decision. Derived from
    ledger entries where two paths on the same decision_id have opposing
    risk_if_taken/risk_if_not_taken. This is a PROJECTED edge — it is never
    authored directly. It exists in the ontology as a view for retrieval
    and graph queries.
  subject_types: [gap, module, phase, pattern]
  object_types: [gap, module, phase, pattern]
  
risk_axis:
  description: >
    Subject has a risk assessment along a specific axis (security,
    performance, etc.). Projected from the latest non-superseded ledger
    entry for that decision+axis.
  subject_types: [gap, module, phase, pattern]
  object_types: ["*"]
```

### Projection Query

```python
def project_risk_graph(ledger_state):
    """Derive risk_tradeoff_with edges from the ledger.
    
    A tradeoff edge exists between two decisions when:
    1. They share a decision_id and have different paths, OR
    2. One decision's risk_if_taken matches another's risk_if_not_taken
       (cross-decision tension)
    """
    edges = []
    for tradeoff in ledger_state.values():
        if tradeoff["status"] not in ("open", "mitigated"):
            continue
        # Same-decision, different-path tension
        for other in ledger_state.values():
            if (other["decision_id"] == tradeoff["decision_id"] 
                and other["path"] != tradeoff["path"]
                and other["status"] in ("open", "mitigated")):
                edges.append({
                    "from": tradeoff["decision_id"],
                    "to": other["decision_id"],
                    "predicate": "risk_tradeoff_with",
                    "axis": tradeoff["axis"],
                    "tension_type": tradeoff["tension_type"],
                })
    return edges
```

---

## (d) Examples of Systems That Tried This and Failed

### 1. NASA's Pre-Challenger Risk Matrix Failure
NASA relied on FMEA and qualitative risk matrices for system safety. Before Challenger, the risk assessment ranked O-ring erosion as "acceptable risk" because the matrix categorized it as low-probability/medium-severity. The matrix couldn't model the **interaction** between temperature and joint design — it was a single-point analysis that missed the combinatorial failure path. FTA would have caught this (an AND gate: low temp AND joint design flaw → blowby).

**Lesson:** Flat risk labels on individual components miss interaction effects. The tradeoff is in the *combination*, not in any single component.

### 2. Cox's Critique of Risk Matrices (2008)
Anthony Cox's formal analysis showed risk matrices are "worse than random" for negatively correlated frequency/severity. The matrix structure itself introduces errors: range compression assigns identical ratings to quantitatively different risks, and the bin boundaries are arbitrary.

**Lesson:** Any system that reduces a multi-dimensional risk tradeoff to a single categorical label (like LOW/MEDIUM/HIGH) will lose information and produce wrong decisions. The label is a projection, not the model.

### 3. Enterprise GRC Tools (Archer, ServiceNow GRC, MetricStream)
These tools model risk as: risk register → control mapping → assessment → treatment plan. They have rich risk taxonomies and can link risks to controls and assets.

**Why they struggle:**
- **Stale risk register:** The risk register is manually curated and drifts from reality — exactly the problem Cortex's gap_ledger.py was designed to solve with append-only events.
- **No tradeoff modeling:** GRC tools link risk → control (one-to-many), but don't model the tension between controls. "Adding control A reduces risk X but increases complexity, which creates risk Y" is not expressible.
- **Over-engineering:** The schema is so heavy ( Archer has 200+ fields per risk) that nobody fills them in, so the data is sparse and unreliable.

**Lesson:** A risk schema that's too heavy will never get populated. The ledger approach (minimal fields, append-only, provenance) avoids this.

### 4. OWASP Risk Rating Methodology
OWASP provides a risk rating model with Likelihood × Impact, each broken into sub-factors (threat agent factors, vulnerability factors, technical impact, business impact).

**Why it fails in practice:** The sub-factors are subjective and the multiplication produces the same range-compression problem Cox identified. Practitioners either skip it (too complex) or game it (pick factors to get the rating they want).

**Lesson:** Multi-factor risk scoring that requires human judgment at each factor will be gamed or skipped. The model should make it easy to assert a tradeoff and hard to game it (provenance + evidence requirements).

### 5. Jira's Link Types (in practice)
Jira has the richest link model (`blocks`, `causes`, `implements`, etc.), but in practice:
- Teams only use `blocks`/`is blocked by` (scheduling) and `relates to` (catch-all).
- `causes`/`is caused by` is rarely used because root cause analysis is hard and teams don't want to commit to causal claims.
- The link types are binary predicates with no metadata — no "why", no severity, no tradeoff dimension.

**Lesson:** Rich link types that require semantic judgment will go unused. Teams default to the simplest link (`blocks`) because it's actionable. A risk tradeoff link needs to be as easy to create as a `blocks` link but carry more structured metadata.

### 6. TMS (Threat Modeling Systems) — STRIDE / DREAD / PASTA
- **STRIDE:** Categories threats (Spoofing, Tampering, Repudiation, Info Disclosure, DoS, Elevation). No tradeoff modeling — just classification.
- **DREAD:** Rates each threat on Damage, Reproducibility, Exploitability, Affected users, Discoverability (1-10 each, averaged). Same range-compression problem as risk matrices.
- **PASTA:** Process-oriented (7 steps from objectives to risk analysis). More structured but still produces a flat risk score, not a tradeoff graph.

**Lesson:** Threat modeling frameworks classify and rate but don't model the tension between mitigation paths. They answer "what's the risk?" not "what's the tradeoff between reducing this risk and creating that one?"

---

## Synthesis: What Cortex Should Do

### The Gap
Cortex's ontology has structural relations (`depends_on`, `implements`, `supersedes`) but no risk-tradeoff relations. The gap_ledger has `blocks`/`blocked_by` (scheduling dependencies) but no risk-tradeoff events. The CORTEX_SCHEMA.md has `risks TEXT` (free text) and `risk_score REAL` (a single number) — both are flat labels with no relational semantics.

### The Recommendation

1. **Add `assert_tradeoff` as a ledger event** in gap_ledger.py (or a parallel `risk_ledger.py`). This is the substrate — append-only, provenance-bearing, with the fields specified in section (c).

2. **Project `risk_tradeoff_with` edges** into the ontology from the ledger, following the same pattern as `_blocker_graph()` derives the dependency graph from `blocked_by` events. Add the relation type to `schema.yaml`.

3. **Don't try to compute optimal decisions.** Influence diagrams and Bayesian networks are theoretically correct but require probability distributions and utility functions that Cortex doesn't have. Instead, model the tradeoff as a structured assertion (what's the risk if I take this path vs. not take it) and let the human (or LLM) reason about the optimal choice at decision time — the OpenAI/Anthropic pattern.

4. **Keep `tension_type` as a controlled vocabulary.** The tradeoff types (precision_vs_recall, security_vs_usability, etc.) are the key abstraction. They let you query "show me all decisions with a security_vs_usability tension" — which is the question the user is actually asking.

5. **Don't model risk as a single label on an issue.** The failure of risk matrices, DREAD, and GRC tools all point to the same lesson: a single risk label loses information. Model the tradeoff relationship, not the label. The label (LOW/MEDIUM/HIGH) can be derived from the accumulated tradeoffs — it's a projection, not the substrate.

### Why This Works for Cortex

- **Consistent with the arbitration:** Ledger is the substrate, ontology is the view. Risk tradeoffs are authored events, projected as edges.
- **Consistent with the gap_ledger pattern:** Same append-only JSONL, same provenance model, same `_blocker_graph()` derivation pattern.
- **Doesn't require probability distributions:** The tradeoff is qualitative (risk_if_taken/risk_if_not_taken as structured prose + tier), not quantitative (expected utility). This matches Cortex's current data maturity.
- **Queryable:** `tension_type` and `axis` are enums that support structured queries. "What are all the open security tradeoffs?" is a one-liner.
- **Extensible:** If Cortex later wants to add quantitative risk scoring (Bayesian), the ledger entries already have the structure to hang probabilities off of — just add `probability` and `impact` fields to `risk_if_taken`/`risk_if_not_taken`.
