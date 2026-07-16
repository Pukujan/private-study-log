# Decision-Level Confidence Scoring in Production Systems

## Research Summary

### (a) What Production Systems Actually Do

#### 1. LangGraph — Interrupt-Based Human Approval (No Confidence Score)
LangGraph's `interrupt()` function is the closest thing to a confidence gate in the LangChain ecosystem, but it is **entirely manual** — it does not compute confidence. The developer places `interrupt()` at a point in the graph where they want human input. The pattern is:
- `interrupt(payload)` pauses execution, saves state via checkpointer, waits for `Command(resume=True/False)`.
- The **"approve or reject" pattern** is literally `interrupt()` → human passes `True` or `False` on resume.
- There is **no automatic confidence calculation**. The decision to interrupt is hardcoded by the developer or conditionally triggered by application logic (e.g., `if tool_name == "dangerous_tool": interrupt()`).
- **Key takeaway**: LangGraph delegates confidence assessment entirely to the human. The framework provides the *pause/resume* mechanism but not the *scoring* mechanism.

#### 2. Vercel AI SDK — Tool Approval with Static Statuses (No Confidence Score)
Vercel AI SDK's `toolApproval` mechanism is the most structured approval system found, but again **does not compute confidence**:
- `toolApproval` accepts four statuses per tool: `'not-applicable'` (auto-run), `'approved'` (auto-approve with recorded audit), `'denied'` (auto-deny with recorded audit), `'user-approval'` (pause for human).
- A `GenericToolApprovalFunction` can make per-call decisions: receives `toolCall`, `tools`, `messages`, `runtimeContext`. Can return any status including `'user-approval'`.
- The developer **writes the confidence logic themselves** inside the approval function. The SDK provides the routing/audit infrastructure but no confidence scoring.
- **Key takeaway**: Vercel's model is "you decide the policy, we handle the plumbing." The `reason` field on auto-approvals/denials is the closest thing to a confidence annotation: `{ type: 'denied', reason: 'blocked by policy' }`.

#### 3. AutoGen — Termination Conditions (Count/Message-Based, Not Confidence-Based)
AutoGen v0.4's termination conditions are **structural, not confidence-based**:
- `MaxMessageTermination(n)`: stop after N messages.
- `TextMentionTermination("TERMINATE")`: stop when agent says a keyword.
- `TokenUsageTermination(max_tokens)`: stop on token budget.
- `HandoffTermination(target)`: stop when handoff occurs.
- `TimeLimitTermination(timeout)`: stop on wall clock.
- `ExternalTermination`: stop via external signal.
- Combinators: `StopMessageTermination`, `|` (any), `&` (all).
- **None of these evaluate decision quality or research sufficiency.** They are resource limits and conversation flow signals, not confidence assessments.
- The `TextMentionTermination` is the closest analogue: the agent itself decides to say "TERMINATE" — a **self-assessment signal** with no calibration or verification of that signal.
- **Key takeaway**: AutoGen has no confidence scoring at all. It relies on the LLM to self-terminate or on hard resource limits.

#### 4. CrewAI — Guardrails as Validation Functions (Binary Pass/Fail, No Score)
CrewAI's `guardrail` system is the most interesting because it **does have a structured return format** that could carry confidence:
- `guardrail` / `guardrails`: list of `Callable` functions that validate task output before proceeding.
- `guardrail_max_retries`: how many times to retry if validation fails (default 3).
- The guardrail function returns `(bool, Any)` — `(True, output)` to accept, `(False, feedback)` to reject with feedback for retry.
- **The boolean is binary pass/fail, not a confidence score.** But the structure naturally extends to carrying a confidence value.
- CrewAI Enterprise adds a "Hallucination Guardrail" feature — but this is a separate model check, not a confidence score on the decision.
- `human_input=True` on a task triggers a human review prompt — again, no confidence assessment, just a gate.
- `output_json` / `output_pydantic`: enforces structured output via Pydantic models. The schema validation is a form of confidence (did the output conform?), but it's binary validation, not calibrated scoring.
- **Key takeaway**: CrewAI's guardrail return tuple `(bool, Any)` is the closest production pattern to a confidence gate — it's the most extensible structure for adding a confidence score.

#### 5. GraphRAG — Importance Rating via LLM-Generated Score (Closest to Decision Confidence)
GraphRAG's global search is the **only system found that actually computes a numerical score as part of its retrieval pipeline**:
- In the **map step**, each community report chunk generates intermediate responses as JSON: `{"points": [{"description": "...", "score": <0-100>}]}`
- The `score` is an **importance score** (0-100) indicating how important each point is for answering the user's question. A score of 0 means "I don't know."
- In the **reduce step**, points are **filtered by score** — only the highest-scoring points from intermediate responses are aggregated into the final response.
- This is **LLM-generated self-assessment of importance**, not calibrated confidence. The LLM assigns the score; there is no post-hoc verification.
- **Key insight**: GraphRAG's score is "how relevant is this information to the question" — which is a form of *research sufficiency* assessment, but it's uncalibrated (no ground truth, no historical accuracy tracking).
- **Key takeaway**: GraphRAG's `score: 0-100` on intermediate points is the closest production implementation to "is the research sufficient for this question." But it's prompt-derived, not calibrated.

#### 6. OpenAI Deep Research — Opaque Sufficiency Logic
OpenAI Deep Research's internal stopping criteria are **not publicly documented**. From observations:
- The system runs multiple search iterations, synthesizing findings progressively.
- It appears to stop when it reaches a "good enough" state, but the threshold is internal to OpenAI's model/prompting — not exposed as a confidence score.
- The output includes a research summary with citations, but no explicit "confidence in sufficiency" metric.
- **Key takeaway**: Deep Research's sufficiency checks are opaque, likely embedded in the model's system prompt or fine-tuning. No public confidence mechanism.

#### 7. StackAI — Gate Mechanisms (No Public Confidence Docs)
StackAI's docs (cookie-walled, minimal content retrieved) mention "Logic Gates" as a concept — conditional routing between nodes. These appear to be **rule-based conditionals** (if/then routing), not confidence-scored gates. The mechanism is visual flow routing, not calibrated confidence.

#### 8. LightRAG — No Confidence Scoring
LightRAG integrates RAGAS for evaluation (retrieval quality metrics like context precision, faithfulness, answer relevancy) and Langfuse for tracing. These are **post-hoc evaluation metrics**, not real-time confidence scores during decision-making. The system does not score confidence in retrieved information or proposed decisions during the pipeline.

#### 9. IBM MAPE-K — The Autonomic Loop (Policy-Based, Not Confidence-Based)
IBM's MAPE-K (Monitor-Analyze-Plan-Execute + Knowledge) is the classic autonomic computing loop:
- **Monitor**: Collect sensor data / system state.
- **Analyze**: Compare current state to desired state (from Knowledge base).
- **Plan**: Construct a change plan.
- **Execute**: Implement the plan.
- **Knowledge**: Shared knowledge base (policies, historical data, topology).
- The loop is **policy-driven**: rules define what actions to take in what states. There is no "confidence score" in the classic MAPE-K formulation — the system either has a policy that matches the situation or it doesn't (escalates to human).
- Modern MAPE-K extensions add "confidence" as a meta-attribute of the Analyze phase, but this is typically a simple threshold on sensor reliability, not a calibrated probability.
- **Key takeaway**: MAPE-K's contribution is the **separation of knowledge (policies) from operational state** — directly relevant to the ontology-vs-ledger question. The Knowledge component is the policy store; the Monitor/Analyze/Plan/Execute phases write operational data to a separate log.

---

### (b) The Gap Between Model Confidence and Decision Confidence

This is the critical distinction. The research reveals a **three-level gap**:

| Level | What It Measures | Production Status | Calibration Method |
|-------|-----------------|-------------------|-------------------|
| **Model Confidence** | P(output is syntactically/semantically correct) | Logits, temperature, token probabilities | Log loss, perplexity |
| **Output Confidence** | P(the answer is factually correct) | GraphRAG importance scores, RAGAS faithfulness | Brier score (rarely applied) |
| **Decision Confidence** | P(making this decision will lead to a good outcome) | **NOT IMPLEMENTED in any system found** | Cohen's kappa (inter-rater), Brier (forecasting) — but only in theory |

**The gap in detail:**

1. **Model confidence (logits)** measures "is the model internally consistent?" — it says nothing about whether the information is correct, sufficient, or whether acting on it is wise. LLM token probabilities are notoriously uncalibrated (overconfident on familiar topics, underconfident on edge cases).

2. **Output confidence** measures "is this specific answer correct?" — GraphRAG's importance score approximates this, but it's self-assessed by the same LLM that generated the answer. No system found uses Brier scores or log loss in production to calibrate output confidence against ground truth.

3. **Decision confidence** measures "will taking this path lead to the desired outcome?" — This requires:
   - **Research sufficiency**: Have we gathered enough information to decide? (No system found implements this as a scored metric. GraphRAG's importance scores are the closest, but they score relevance, not sufficiency.)
   - **Path success probability**: What's the probability this proposed action succeeds? (No system found. This would require outcome tracking — historical data on similar decisions and their results.)
   - **Agent self-assessment trustworthiness**: Can we trust the agent's own confidence claim? (Cortex's existing Cohen's kappa vs Fable-Max anchor is the most advanced approach found. AutoGen's `TextMentionTermination` is the naive version — the agent says "I'm done" and the system believes it.)

**Why the gap exists**: Decision confidence requires **outcome tracking** (did the decision work out?) which requires:
- A temporal gap between decision and outcome (can't score immediately).
- A definition of "good outcome" (domain-specific).
- Historical baselines (enough similar decisions to calibrate).
- **No production AI framework implements outcome tracking.** They all operate in the moment — generate, validate structurally, optionally human-approve, execute. None circle back to say "was this right?"

---

### (c) Where Confidence Scoring Belongs: Knowledge Graph/Ontology vs. Flat Ledger

**Recommendation: Flat append-only ledger, NOT the ontology.**

Both Codex and Fable independently recommended this, and the research strongly confirms it:

**Why NOT the ontology:**
1. **Ontologies represent domain knowledge, not operational state.** GraphRAG's knowledge graph stores entities and relationships — it doesn't store "how confident was the system when it answered this question." Confidence is metadata about the *process of using* the ontology, not a property of the domain itself.
2. **Ontologies are queried for reasoning, not audited for calibration.** If confidence scores are embedded in ontology entities, every query that retrieves an entity gets the confidence of *when it was last assessed* — not the confidence of *the current decision being made*. This is a category error.
3. **Confidence changes over time; ontology entities are (relatively) stable.** An entity "Company X has revenue $10M" is a fact. The confidence that "research about Company X is sufficient to make an investment decision" changes with every new piece of information gathered. Embedding the latter in the former pollutes the knowledge representation.
4. **MAPE-K confirms this separation.** The Knowledge component stores policies and topology; operational state (Monitor/Analyze results) flows through the loop and is logged separately. This is the same architecture.

**Why a flat append-only ledger:**
1. **Confidence is temporal and contextual.** A confidence score is meaningful only in context: "At time T, given evidence E, for decision D, the system assessed confidence C." This is inherently a log entry, not a graph node.
2. **Calibration requires historical data.** Cohen's kappa, Brier scores, and log loss all require comparing predictions to outcomes over time. A flat ledger of (prediction, outcome) pairs is exactly the data structure these metrics need.
3. **Append-only = immutable audit trail.** Confidence scores should never be updated in place — they should be superseded by new entries. This preserves the history needed for calibration.
4. **No schema migration needed.** If the confidence schema evolves, old entries keep their schema and new entries get the new one. Graph migrations are painful; log format evolution is trivial.
5. **GraphRAG implicitly confirms this.** Its importance scores are generated per-query, not stored in the knowledge graph. The graph stores entities/communities; scores are ephemeral.

**The ontology CAN carry one thing**: a **confidence policy** — e.g., "for decisions of type X, require evidence Y and minimum confidence Z." This is policy (stable, domain knowledge) not operational data (volatile, per-decision).

---

### (d) Concrete Schema for a Decision-Confidence Field

Based on the research, here is a schema designed to be:
- **Append-only** (ledger entry, not graph attribute)
- **Calibratable** (supports Brier score, Cohen's kappa computation)
- **Multi-dimensional** (research sufficiency, path success, agent trustworthiness as separate fields)
- **Compatible with** CrewAI's guardrail return tuple and Vercel's `reason` field

```json
{
  "$schema": "decision-confidence/v1",
  "entry_type": "decision_confidence",
  "id": "dc_<uuid>",
  "timestamp": "2026-07-16T12:00:00Z",
  "decision_id": "dec_<uuid>",

  "dimensions": {
    "research_sufficiency": {
      "score": 0.75,
      "score_type": "probability",
      "rationale": "Identified 3 comparable paths with quantitative metrics. Missing: regulatory filing data for Path B.",
      "evidence_count": 12,
      "evidence_gaps": ["regulatory_filing_path_b"],
      "method": "llm_self_assessment",
      "calibration_anchor": null
    },

    "path_success_probability": {
      "score": 0.68,
      "score_type": "probability",
      "rationale": "Historical success rate for similar paths in this domain is 68%. Current path has above-average risk on factor X.",
      "comparable_cases": 47,
      "method": "historical_baseline_adjusted",
      "calibration_anchor": "brier_score_30d"
    },

    "agent_self_assessment_trust": {
      "score": 0.82,
      "score_type": "reliability",
      "rationale": "Agent's Cohen's kappa vs Fable-Max anchor over last 50 decisions is 0.82.",
      "calibration_metric": "cohens_kappa",
      "calibration_window": "last_50_decisions",
      "calibration_anchor": "fable_max",
      "method": "inter_rater_agreement"
    }
  },

  "composite_confidence": {
    "score": 0.71,
    "method": "weighted_geometric_mean",
    "weights": {
      "research_sufficiency": 0.35,
      "path_success_probability": 0.45,
      "agent_self_assessment_trust": 0.20
    },
    "rationale": "Geometric mean penalizes any single low dimension."
  },

  "routing": {
    "action": "human_review",
    "threshold_auto": 0.85,
    "threshold_human": 0.50,
    "threshold_reject": 0.20,
    "rule": "score >= 0.85 ? auto : (score >= 0.50 ? human_review : reject_and_research)"
  },

  "outcome": {
    "status": "pending",
    "resolved_at": null,
    "actual_result": null,
    "success": null,
    "notes": null
  },

  "metadata": {
    "agent_id": "fable_v3",
    "model": "umans-glm-5.2",
    "decision_type": "path_selection",
    "session_id": "sess_<uuid>"
  }
}
```

#### Field Design Rationale:

**`dimensions` (three separate scores, not one composite):**
- The three confidence types measure fundamentally different things. Collapsing them into one number loses information needed for debugging and calibration.
- `research_sufficiency`: "Do we know enough?" — closest to GraphRAG's importance score concept, but framed as sufficiency rather than relevance.
- `path_success_probability`: "Will this work?" — requires historical outcome tracking (the `outcome` field below enables this over time).
- `agent_self_assessment_trust`: "Can we trust the agent's confidence?" — this is what Cortex's Cohen's kappa vs Fable-Max anchor already measures.

**`score_type` field:**
- `probability` (0-1): for Brier score computation.
- `reliability` (0-1): for Cohen's kappa interpretation.
- Distinguishing these prevents misuse (e.g., treating a kappa of 0.82 as a probability of 82% success).

**`method` field:**
- `llm_self_assessment`: the agent rated itself (like GraphRAG, AutoGen's TERMINATE).
- `historical_baseline_adjusted`: adjusted by outcome history (Brier-scored over time).
- `inter_rater_agreement`: Cohen's kappa or similar.
- This field is critical for calibration — you can't Brier-score an LLM self-assessment the same way you Brier-score a probability forecast.

**`composite_confidence` with geometric mean:**
- Arithmetic mean allows one high dimension to mask a low one.
- Geometric mean (or min) ensures all dimensions must be reasonable.
- Weights are explicit and tunable.

**`routing` thresholds:**
- Replaces the current prose "85-92% auto, 8-15% human" with concrete numeric thresholds.
- Three zones: auto-execute, human-review, reject-and-research-more.
- Maps directly to LangGraph's interrupt pattern (if human_review → `interrupt()`).

**`outcome` field (the key to calibration):**
- Initially `pending`. Updated later when the decision's outcome is known.
- Enables Brier score computation: `BrierScore = (composite_confidence.score - outcome.success)²`
- Over time, this data calibrates the system: if the system says "75% confident" but outcomes succeed only 50% of the time, the Brier score reveals the miscalibration.
- This is the field that **no production system implements** — and it's the gap between model confidence and decision confidence.

**Ledger entry, not ontology attribute:**
- Each entry is immutable. New assessments for the same decision create new entries.
- The ledger can be queried: "What's the Brier score of `agent_self_assessment_trust` over the last 100 decisions?"
- The ontology stores the domain knowledge (entities, paths, risk factors); the ledger stores the confidence metadata.

---

### Summary of Findings

| System | Confidence Mechanism | Scores Decisions? | Calibrated? |
|--------|---------------------|-------------------|-------------|
| LangGraph | Manual interrupt (True/False) | No | No |
| Vercel AI SDK | toolApproval statuses | No | No |
| AutoGen | Termination conditions (count/message) | No | No |
| CrewAI | Guardrail (bool, feedback) + human_input | No (binary) | No |
| GraphRAG | Importance score 0-100 (LLM-generated) | Partially (relevance) | No |
| OpenAI Deep Research | Opaque internal stopping | Unknown | Unknown |
| StackAI | Rule-based logic gates | No | No |
| LightRAG | RAGAS evaluation (post-hoc) | No | Post-hoc only |
| IBM MAPE-K | Policy-based loop | No | No |

**No production system implements calibrated decision-level confidence scoring.** The closest is GraphRAG's importance score (LLM self-assessment, uncalibrated) and CrewAI's guardrail tuple (binary, extensible but not scored). The gap between model confidence and decision confidence is **outcome tracking** — no system circles back to verify whether its confidence was justified.

For Cortex, the recommended approach is:
1. **Store confidence in a flat append-only ledger** (not the ontology).
2. **Score three dimensions separately** (research sufficiency, path success, agent trust).
3. **Add an outcome field** that gets resolved later — this enables Brier score calibration over time.
4. **Use routing thresholds** (not prose) to map confidence → auto/human/reject.
5. **Keep the ontology for domain knowledge and confidence policies**, not operational confidence data.
