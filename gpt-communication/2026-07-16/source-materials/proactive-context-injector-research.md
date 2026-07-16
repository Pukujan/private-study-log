# Proactive Context Injector — Research Report

**Date:** July 16, 2026
**Question:** "How to make the injector strong enough to know and find out without knowing what you are actively looking for?"
**System context:** Cortex has a living ontology (153 entities, 91 relations), BM25+vector+RRF retrieval with ontology leg OFF, scope packs (pull-only), and context packets designed but not built.

---

## Executive Summary

No production system today does what Cortex needs: **push-based proactive context surfacing from a knowledge graph without an explicit query.** Every system studied (Mem0, Letta, Zep/Graphiti, GraphRAG, LightRAG) is fundamentally **query-triggered** — retrieval happens when the user/agent asks something. The gap is between "I asked about Claude" (pull) and "I mentioned Claude in passing and the system proactively surfaced the family-bias finding, the circular-validation pattern, the cross-vendor quarantine, the expired fable-max entity, and the scorecard retraction" (push).

The answer is a **separate injection layer** that sits between input ingestion and agent reasoning, using entity-linking as its trigger and a multi-signal relevance scorer (no explicit query needed). The ontology provides the data; the gap ledger provides the urgency; the injection layer provides the push mechanism.

---

## (a) Approaches That Exist for Proactive Context Surfacing

### 1. Pre-Session Memory Injection (Mem0 pattern)
- **How it works:** Before agent invocation, `memory.search(query=user_input)` retrieves top-K memories → injects into system prompt → agent processes with context → after response, `memory.add()` extracts new memories.
- **Verdict:** This is the closest production pattern but is still **pull-based** — it requires the user's message as the query. It does not surface things the user *didn't* mention.
- **Cortex already has this:** `cortex_scope_pack` and `cortex_search` are exactly this pattern.

### 2. Letta/MemGPT Memory Blocks with Pressure Alerts
- **How it works:** Core memory blocks (personality, user info) are always in-context. Archival memory is paged in via `archival_memory_search`. Memory pressure warnings are injected as `system_alert` at 0.9× context window, naming the exact remedial functions.
- **Proactive element:** The pressure warning is genuinely proactive — it fires on a condition (context window filling up) without the agent asking. But it's reactive to context state, not to semantic content.
- **Copy-able pattern:** `chars_current/chars_limit` budget annotations on every response; pressure warnings that name remedial actions.

### 3. Graphiti/Zep Temporal Knowledge Graph
- **How it works:** Temporal KG stores fact-validity windows (valid_at/invalid_at edges). Retrieval uses hybrid: semantic + keyword + graph traversal. Facts expire and become invalid over time.
- **Proactive element:** Graphiti pre-computes graph + facts asynchronously for low-latency deterministic retrieval. It tracks *when facts are true*, not just *that they are true*.
- **Relevance to Cortex:** The ontology already has bi-temporal status (active/superseded/deprecated/expired). The fable-max entity is `expired` — Graphiti's pattern would naturally surface "this entity was recently invalidated" as a temporal event.

### 4. Microsoft GraphRAG — Community Detection + Summarization
- **How it works:** Documents → entity/relationship extraction → community detection (Leiden algorithm) → community summaries → two query modes:
  - **Local search:** entity-centric, pulls surrounding subgraph
  - **Global search:** community-summary-centric, answers broad thematic questions
- **Proactive element:** Community detection clusters related entities without a query — it reveals "these entities are about the same topic" structurally. Community summaries pre-compute what's relevant to each cluster.
- **Relevance to Cortex:** The 153 entities could be clustered into communities (e.g., {Claude, family-bias, scorecard-retraction, circular-validation} form a natural cluster). When any member is mentioned, the community summary is the injection candidate.

### 5. LightRAG — Dual-Level Retrieval
- **How it works:** Two retrieval modes:
  - **Low-level (specific):** Entity-centric keyword + graph traversal — finds specific facts about mentioned entities
  - **High-level (abstract):** Topic/keyword-based broader retrieval — finds related concepts
- **Incremental updates:** Unlike GraphRAG, LightRAG supports incremental graph updates without full re-index — critical for a living ontology.
- **Proactive element:** The dual-level structure means a mention of "Claude" triggers both a low-level retrieval (specific Claude entity + its relations) AND a high-level retrieval (the broader topic of model bias / calibration / evaluation methodology).
- **Relevance to Cortex:** This dual-level approach maps directly to the need. When "Claude" is mentioned, low-level pulls {model:claude, rubric:..., gap:...} and high-level pulls {bias, calibration, family-bias-finding, scorecard-retraction}.

### 6. A-RAG — Agentic Retrieval (Hierarchical Retrieval Interfaces)
- **How it works:** Reframes RAG as a harness tool-design problem. Instead of injecting retrieved documents at pipeline time, exposes three retrieval tools (keyword search, semantic search, chunk read) and lets the agent pull information incrementally per reasoning step.
- **Key insight:** "Retrieval becomes a tool call in the agent loop, not a preprocessing step."
- **Verdict:** This is pull-based but moves the pull decision into the agent's reasoning loop rather than a fixed pipeline. More flexible but still requires the agent to *decide to search*.

### 7. Anthropic Context Engineering
- **Pattern:** Context is built from multiple sources: system prompt (static), conversation history (growing), tool results (injected), retrieved context (pulled). The discipline is about managing the *total context budget* — what gets included, what gets compressed, what gets evicted.
- **Key principle:** Context should be *just enough, not more*. Over-injection degrades performance — models get confused by irrelevant context ("lost in the middle" problem).
- **Relevance:** This is the cautionary principle. A proactive injector must be aggressive about *not* injecting — relevance scoring must be strict.

### 8. Recommendation Engine Approaches (for query-less relevance)

This is the most directly applicable field, because recommendation engines face the exact same problem: **decide what to surface when there's no explicit query.**

#### Collaborative Filtering (CF)
- **User-based CF:** "Users who engaged with X also engaged with Y" → surface Y when X is mentioned
- **Item-based CF:** "Items frequently co-occur" → surface items that co-occur with mentioned entity
- **Application to Cortex:** If entity A (Claude) is mentioned, and in prior sessions entities A+B+C (family-bias, scorecard-retraction) were always co-retrieved, surface B and C. This is a "co-mention graph" approach.
- **Problem:** Cold start (new entities have no co-mention history). Cortex's 153 entities are small enough that CF signal would be sparse.

#### Content-Based Filtering
- **How it works:** Each item has a feature vector (keywords, topics, embeddings). Surface items whose vectors are close to the mentioned entity's vector.
- **Application to Cortex:** Embed each ontology entity's summary text. When "Claude" is mentioned, compute cosine similarity against all entity embeddings and surface the top-K. This is essentially what vector retrieval does — but the trigger is entity mention detection, not a search query.

#### Knowledge-Graph-Based Recommendations
- **How it works:** Use graph structure (paths, neighborhoods, meta-paths) to recommend items. Entity A is recommended because there's a short graph path from entity B to A, or they share many common neighbors.
- **Application to Cortex:** This is the ontology's native strength. When "Claude" is mentioned, traverse the graph: Claude → authored_by → rubrics → validated_by → checkers; Claude → references → bias-finding-doc; Claude → supersedes → older-model. The graph path *is* the relevance signal — no query needed.
- **Meta-path approach:** Define typed paths like `model → authored_by → rubric → validated_by → checker` or `model → references → finding → supersedes → old_finding`. Entities on these paths are injection candidates.

---

## (b) What Triggers Injection

### Trigger Taxonomy (from least to most sophisticated)

| Trigger | How it works | Example for Cortex | Proactive? |
|---|---|---|---|
| **Entity mention** | NER / string matching detects "Claude" in input text | "Claude" mentioned → surface `model:claude` entity + 1-hop neighbors | Yes — no query needed |
| **Topic detection** | Embedding-based topic classification of the input | Topic = "model evaluation" → surface bias findings, calibration gaps | Yes — detects implicit topic |
| **Decision context** | Agent is about to make a decision (tool call, code change) → surface relevant patterns/gaps | Agent calls `cortex_contract` for a Claude task → surface circular-validation warning | Yes — intercepts action |
| **Temporal trigger** | Entity status changes (expired, superseded, deprecated) → proactively notify | `fable-max` entity set to `expired` → notify all sessions that reference it | Yes — event-driven |
| **Graph-neighborhood trigger** | Any mention of an entity in a community → surface the community summary | "Claude" mentioned → surface the {Claude, bias, calibration} community summary | Yes — structural |
| **Pattern co-occurrence** | Statistical: entities that co-occur in prior sessions → surface together | Claude + scorecard always co-retrieved → surface scorecard-retraction when Claude mentioned | Yes — learned |

### Recommended Multi-Trigger Architecture for Cortex

```
Input Text
    │
    ▼
┌─────────────────────────┐
│  1. ENTITY LINKER        │  ← NER + fuzzy match + embedding match against ontology entities
│     (spaCy/transformers) │     Output: [(entity_id, mention_span, confidence)]
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  2. GRAPH EXPANDER       │  ← For each linked entity, traverse N-hop neighborhood
│     (ontology.py)        │     Output: [(entity_id, relation_path, hop_distance)]
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  3. RELEVANCE SCORER     │  ← Score each candidate by: graph proximity,
│     (multi-signal)       │     entity status (expired=high priority),
│                          │     co-occurrence frequency, recency of last surfacing,
│                          │     decision context match
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  4. BUDGET GATE          │  ← Token budget for injection (e.g., 2K tokens max)
│     + DEDUP              │     Dedup against already-in-context items
│                          │     Gate: only inject if score > threshold
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  5. CONTEXT PACKET       │  ← Format as structured context packet
│     FORMATTER            │     (the designed-but-unbuilt context_packet)
└───────────┬─────────────┘
            │
            ▼
    Injected into system prompt / tool response
```

### Key Design Decisions for Triggers

1. **Entity linking is the primary trigger** — it's deterministic, cheap, and the ontology already has the entity names. A simple BM25/fuzzy match against entity names + aliases would catch "Claude", "strong models", "fable-max", "scorecard" etc.

2. **Status-change events are temporal triggers** — when `fable-max` becomes `expired`, that's an event. The injector should proactively surface "fable-max is now expired" in any session that touches calibration or model selection. The ontology's bi-temporal status (active/superseded/deprecated/expired) is already built — the injector just needs to query it.

3. **Decision-context triggers fire on tool calls** — when an agent calls `cortex_contract` for a task involving Claude, the injector checks: "Is there a gap, pattern, or finding that applies to this task type + model?" and surfaces it. This is the Letta `RequiredBeforeExitToolRule` pattern inverted: instead of "you must do X before exiting", it's "you should know X before proceeding."

---

## (c) How to Score Relevance When There's No Explicit Query

This is the core problem. Without a query, you can't compute query-document similarity. The scoring must be based on:

### Scoring Signals

| Signal | Formula | Weight | Notes |
|---|---|---|---|
| **Graph proximity** | `1 / (1 + hop_distance)` | 0.30 | 1-hop = 0.5, 2-hop = 0.33, 3-hop = 0.25 |
| **Entity status** | expired=1.0, superseded=0.8, deprecated=0.6, active=0.3 | 0.20 | Expired/superseded entities are HIGH priority — they represent "what changed" |
| **Relation type** | `references`=1.0, `supersedes`=0.9, `validated_by`=0.7, `authored_by`=0.5, `depends_on`=0.6 | 0.15 | Some relations are more informative than others |
| **Co-occurrence frequency** | `log(1 + count)` normalized | 0.10 | Entities that co-occur in prior sessions |
| **Recency of last surfacing** | `exp(-age_days / 30)` | 0.10 | Recently surfaced items get slight boost (momentum) OR penalty (anti-repetition) |
| **Decision context match** | `cosine(embed(task_context), embed(entity_summary))` | 0.15 | If we know what the agent is doing, match against that |

### Composite Score

```python
def score_injection_candidate(entity, mention_context, graph_context):
    graph_proximity = 1.0 / (1 + graph_context.hop_distance(entity))
    
    status_scores = {
        "expired": 1.0, "superseded": 0.8, "deprecated": 0.6, "active": 0.3
    }
    status_score = status_scores.get(entity.status, 0.3)
    
    relation_scores = {
        "references": 1.0, "supersedes": 0.9, "validated_by": 0.7,
        "depends_on": 0.6, "authored_by": 0.5, "part_of": 0.4, "implements": 0.4
    }
    relation_score = relation_scores.get(graph_context.relation_type(entity), 0.3)
    
    cooccurrence = math.log(1 + graph_context.cooccurrence_count(entity)) / 10.0
    
    recency = math.exp(-graph_context.days_since_surfaced(entity) / 30.0)
    
    context_match = cosine_similarity(
        embed(mention_context), embed(entity.summary)
    )
    
    return (
        0.30 * graph_proximity +
        0.20 * status_score +
        0.15 * relation_score +
        0.10 * cooccurrence +
        0.10 * recency +
        0.15 * context_match
    )
```

### Key Insight: Status as a Relevance Signal

The most novel signal for Cortex is **entity status as a relevance multiplier**. When `fable-max` becomes `expired`, that's a high-relevance event regardless of graph proximity — it means "something you might be relying on is no longer valid." The scorecard retraction (family-bias claim retracted as confounded) is another example — a retracted finding has higher injection priority than an active one because it represents *corrected knowledge*.

This maps to the recommendation engine concept of **novelty** — items that are new, changed, or surprising get a boost. In Cortex, status changes are the novelty signal.

---

## (d) Failure Modes

### 1. Context Bloat (Disease A)
- **What happens:** Injector surfaces too many entities → context window fills with marginally relevant ontology data → agent loses focus on the actual task.
- **Cortex already diagnosed this:** The repo measured 12,237 tokens of tool schemas as "eager context bloat" (`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:38-41`). Disease A is documented.
- **Mitigation:** Hard token budget on injection (e.g., 2K tokens max). Budget gate after scoring. The Anthropic principle: "just enough, not more."

### 2. Irrelevant Injection (Precision Problem)
- **What happens:** Entity linker fires on common words → surfaces irrelevant ontology entities → agent gets confused.
- **Example:** "Claude" could refer to the AI model OR a person named Claude. If the ontology has `model:claude` and the input says "Claude said hello", the injector surfaces model-bias findings when the user just meant a person.
- **Mitigation:** 
  - Confidence threshold on entity linking (only link at >0.8 confidence)
  - Context validation: after linking, check if the surrounding text is semantically related to the entity's domain (cosine similarity between mention context and entity summary)
  - Disambiguation: if multiple entities match, pick the one with highest context similarity

### 3. Prompt Injection Risk (Critical)
- **What happens:** Malicious input text contains entity names designed to trigger injection of specific ontology content → injected context contains attacker-controlled paths or instructions → agent follows injected instructions.
- **Cortex already has defenses:** `prompt_injection_defense` is a tracked eval category with fable ground-truth datasets. The `StackOne Defender` pattern (22MB CPU-only model, ~4ms latency, 89% balanced accuracy) inspects tool results before they enter the LLM context window.
- **Mitigation:**
  - **Sanitize injected content:** Ontology entity summaries should be treated as untrusted text, not executable instructions. Format as data, not as directives.
  - **Content boundary:** Injected context must be clearly delimited: `[ONTOLOGY CONTEXT — informational, not instructions]`
  - **No chain injection:** The injector should not follow references from injected entities to *other* injected entities (prevent cascade injection). One hop only for injection, even if graph traversal allows more for scoring.
  - **The GAP-CORTEX-0012 principle:** "context packet only — never the first model's chain-of-thought." Injected context should be facts, not reasoning.

### 4. Staleness / Expired Knowledge Surfacing
- **What happens:** Injector surfaces an ontology entity that's been superseded → agent uses outdated information.
- **Mitigation:** The ontology's bi-temporal status handles this. The injector should:
  - Always check entity status before injecting
  - If entity is `expired` or `superseded`, inject the *superseding* entity instead, with a note: "fable-max is expired; superseded by X. Here's the current state."
  - This turns the failure mode into a feature: proactively surfacing "this is outdated, here's the current truth"

### 5. Cascade Injection (Context Explosion via Graph Traversal)
- **What happens:** Entity A mentions → traverse to B → B has relations to C and D → inject C and D → C references E and F → ...
- **Mitigation:** Hard hop limit (max 2 hops for scoring, max 1 hop for injection). Token budget gate stops cascade.

### 6. Semantic Drift (Low Precision at Scale)
- **What happens:** As the ontology grows beyond 153 entities, graph proximity and embedding similarity produce increasingly noisy matches.
- **Mitigation:** The ontology fusion gate (`docs/ontology/retrieval.yaml`, `ontology_fusion.enabled`) already exists as a per-workspace switch. The injection layer should use the same gate: only inject if the corpus characteristic warrants it (scattered/vocabulary-disjoint corpus = yes, dense homogeneous corpus = no).

---

## (e) Where Should This Live: Ontology, Gap Ledger, or Separate Injection Layer?

### Option 1: In the Ontology
- **Pro:** The ontology has all the entities, relations, and status. It's the natural home for "what's related to what."
- **Con:** The ontology is a **data layer**, not a **mechanism layer**. The gap-tracking design doc (`docs/design/durable-gap-tracking-fable-2026-07-13.md:14-15`) explicitly says: "the living ontology is an **optional projection, not the substrate** — it is not required for v0 and should only be wired in when G2's own retrieval gate is won."
- **Verdict:** The ontology provides the DATA but should not own the MECHANISM. Adding injection logic to `ontology.py` would violate the separation between data and control flow.

### Option 2: In the Gap Ledger
- **Pro:** The gap ledger tracks what's broken/missing/urgent — it's the natural source of "what should be surfaced because it matters."
- **Con:** The gap ledger is a **project-control primitive** (tracking what needs to be fixed), not a **knowledge-surfacing primitive** (deciding what context to inject). The design doc says: "do not make the living ontology the source of truth for gaps. Build one small, typed, append-only gap ledger." The gap ledger is about gaps, not about entity relationships.
- **Verdict:** The gap ledger should be a *signal input* to the injector (high-priority gaps should influence injection scoring), but not the home of the injection mechanism.

### Option 3: Separate Injection Layer (RECOMMENDED)
- **Pro:** Clean separation of concerns. The injection layer:
  - Reads from the ontology (entity graph, relations, status)
  - Reads from the gap ledger (urgency signals)
  - Reads from the task/context state (decision context)
  - Produces context packets (the designed-but-unbuilt artifact)
  - Applies its own scoring, budgeting, and sanitization logic
- **Con:** Another component to maintain. But the architecture already has this pattern: `search.py` reads from the index, `packs.py` reads from the corpus, `gap_ledger.py` reads from the gap store. The injection layer would be a peer.
- **Verdict:** **This is the right answer.** A separate `injector.py` module that:
  1. Takes input text as its trigger (not a query)
  2. Links entities against the ontology
  3. Traverses the graph for candidates
  4. Scores with the multi-signal formula
  5. Gates on budget and threshold
  6. Produces a context packet
  7. Is invoked as a pre-processing step before the agent sees the input

### Architecture for Cortex

```
                    ┌──────────────────┐
                    │  Ontology        │  ← DATA: 153 entities, 91 relations, statuses
                    │  (ontology.py)   │
                    └────────┬─────────┘
                             │ reads
                    ┌────────▼─────────┐
                    │  Gap Ledger       │  ← DATA: urgency signals, what's broken
                    │  (gap_ledger.py)  │
                    └────────┬─────────┘
                             │ reads
                    ┌────────▼─────────┐
                    │  INJECTOR         │  ← MECHANISM: entity linking, scoring,
                    │  (injector.py)    │     budgeting, sanitization, formatting
                    └────────┬─────────┘
                             │ produces
                    ┌────────▼─────────┐
                    │  Context Packet   │  ← ARTIFACT: structured injection block
                    │  (not yet built)  │
                    └────────┬─────────┘
                             │ injected into
                    ┌────────▼─────────┐
                    │  Agent Context    │  ← system prompt / tool response / MCP response
                    └──────────────────┘
```

### How It Connects to Existing Cortex Architecture

1. **Entity linking** uses the ontology's entity names + the existing BM25 index. A fuzzy match / NER pass over input text against `entities.jsonl` names. No new infrastructure.

2. **Graph expansion** uses `ontology.py`'s existing graph traversal (the `_ontology_leg` in `search.py` already does multi-hop traversal). The injector reuses this.

3. **Status checking** uses the ontology's bi-temporal status. When `fable-max` is `expired`, the injector knows. When the scorecard is `retracted`, the injector knows.

4. **Context packet format** is the designed-but-unbuilt artifact from GAP-CORTEX-0012. The injector produces it.

5. **Budget gating** follows the Letta pattern: `chars_current / chars_limit` on every injection. Hard cap (e.g., 2K tokens).

6. **Sanitization** follows the StackOne Defender pattern: injected content is data, not instructions. Clearly delimited boundary.

7. **The scope-pack relationship:** Scope packs are pull-only (the agent asks for a bounded context slice). The injector is push-only (proactively surfaces without being asked). They are complementary: the injector fires on entity mention; the scope pack fires on explicit request. The injector should be able to suggest "use `cortex_scope_pack` with entity X for deeper context" as part of its injection.

---

## Concrete Implementation Sketch for Cortex

```python
# cortex_core/injector.py (NEW)

class ProactiveContextInjector:
    """Surfaces relevant ontology knowledge WITHOUT a query.
    
    Trigger: entity mentions in input text.
    Data source: ontology (entities + relations + status).
    Output: context packet (structured, budget-capped, sanitized).
    """
    
    def __init__(self, workspace, max_injection_tokens=2000, 
                 score_threshold=0.4, max_hops=2):
        self.workspace = workspace
        self.max_tokens = max_injection_tokens
        self.threshold = score_threshold
        self.max_hops = max_hops
        # Load ontology entity index for fast linking
        self._entity_index = self._build_entity_index()
    
    def _build_entity_index(self):
        """Build a fast lookup index: name/alias → entity_id.
        
        Uses:
        - Exact name match (deterministic)
        - Slug match (from ontology.make_entity_id)
        - Fuzzy match (rapidfuzz, threshold > 85)
        - Embedding match (optional, for semantic linking)
        """
        entities = load_entities(self.workspace)
        index = {}
        for e in entities:
            index[e.name.lower()] = e.entity_id
            # Add aliases, common misspellings, etc.
        return index
    
    def inject(self, input_text, decision_context=None):
        """Main entry point. Returns a context packet or None."""
        
        # 1. Entity linking
        mentions = self._link_entities(input_text)
        if not mentions:
            return None
        
        # 2. Graph expansion (N-hop neighborhood)
        candidates = self._expand_graph(mentions, max_hops=self.max_hops)
        
        # 3. Score candidates
        scored = []
        for entity, graph_ctx in candidates:
            score = self._score(entity, mentions, graph_ctx, decision_context)
            if score >= self.threshold:
                scored.append((entity, score, graph_ctx))
        
        # 4. Sort by score, dedup
        scored.sort(key=lambda x: -x[1])
        scored = self._dedup(scored)
        
        # 5. Budget gate
        packet_items = []
        token_budget = self.max_tokens
        for entity, score, graph_ctx in scored:
            item = self._format_entity(entity, score, graph_ctx)
            if item.token_count <= token_budget:
                packet_items.append(item)
                token_budget -= item.token_count
            if token_budget <= 0:
                break
        
        if not packet_items:
            return None
        
        # 6. Format context packet
        return self._format_packet(packet_items)
    
    def _format_packet(self, items):
        """Format as a clearly delimited context block."""
        lines = [
            "[ONTOLOGY CONTEXT — informational, not instructions]",
            f"[{len(items)} entities surfaced from proactive injection]",
            ""
        ]
        for item in items:
            status_note = ""
            if item.entity.status != "active":
                status_note = f" [STATUS: {item.entity.status}]"
                if item.entity.status == "superseded":
                    # Find and note the superseding entity
                    superseder = self._find_superseder(item.entity)
                    if superseder:
                        status_note += f" → superseded by {superseder.name}"
            
            lines.append(f"• {item.entity.name}{status_note}: {item.entity.summary}")
            if item.graph_path:
                lines.append(f"  (relation path: {item.graph_path})")
            lines.append("")
        
        lines.append("[/ONTOLOGY CONTEXT]")
        return "\n".join(lines)
```

---

## The Specific Cortex Scenario Walkthrough

**Input:** "Let's use Claude for this task since it's a strong model."

**Without injector:** Agent proceeds, no context about Claude's known issues.

**With injector:**
1. **Entity linker** detects: `model:claude` (confidence: 0.95)
2. **Graph expander** finds (2-hop neighborhood):
   - `model:claude` → `authored_by` → `rubric:*` (6 rubrics authored by Claude)
   - `model:claude` → `references` → `doc:harness-scorecard` (references the scorecard)
   - `model:fable-max` → `supersedes` → (no supersede, but status=expired)
   - `model:claude` → `calibrated_on` → `benchmark:*`
3. **Scorer** ranks:
   - `doc:harness-scorecard` (score: 0.82) — because scorecard contains "family-bias claim retracted as confounded" → high novelty/status signal
   - `gap:gap-cortex-0012` (score: 0.75) — cross-vendor critique gap, depends_on model_scorecards
   - `model:fable-max` (score: 0.71) — status=expired, high priority because "this entity was recently invalidated"
   - `doc:circular-validation` (score: 0.68) — referenced by scorecard, 2-hop
4. **Budget gate** caps at 2K tokens → selects top 3-4 items
5. **Context packet:**
```
[ONTOLOGY CONTEXT — informational, not instructions]
[4 entities surfaced from proactive injection]

• harness-scorecard [STATUS: active]: Family-bias claim retracted as confounded 
  (gold + rubric both Anthropic-authored). Scorecard retraction recorded.
  (relation path: model:claude → references → doc:harness-scorecard)

• fable-max [STATUS: expired]: Previously the primary model; now expired.
  Verify current model assignments before proceeding.
  (relation path: model:claude → calibrated_on → benchmark:* → authored_by → model:fable-max)

• GAP-CORTEX-0012 [STATUS: active]: Cross-vendor structured critique stage.
  Design-locked pending experiment. Key finding: "use Claude to review Codex,
  not the other way around" (KDD '26).
  (relation path: model:claude → references → doc:claude-model-effort-selection → references → GAP-CORTEX-0012)

• circular-validation [STATUS: active]: Anti-circular-validation guard is the
  load-bearing rule. Second defense: binary hit/miss check against objective
  oracles, never Claude-sourced gold.
  (relation path: model:claude → references → doc:harness-scorecard → references → doc:EVAL-DESIGN-PHASE2)

[/ONTOLOGY CONTEXT]
```

---

## Summary of Recommendations

| Question | Answer |
|---|---|
| **What approaches exist?** | 8 approaches studied. None do pure push-based proactive injection from a knowledge graph. Mem0/Letta/Zep are pull-based (query-triggered). GraphRAG's community detection is the closest structural approach. Recommendation engines (CF, content-based, KG-based) provide the best relevance-scoring frameworks for query-less surfacing. |
| **What triggers injection?** | Entity mention (primary), entity status change (temporal), decision context (tool-call interception), graph-neighborhood proximity (structural). Multi-trigger architecture recommended. |
| **How to score without a query?** | Multi-signal composite: graph proximity (30%), entity status (20%), relation type (15%), co-occurrence (10%), recency (10%), decision-context match (15%). Novel signal: entity status as relevance multiplier (expired/superseded = high priority). |
| **What are the failure modes?** | Context bloat (Disease A, already diagnosed), irrelevant injection (precision problem), prompt injection (critical, Cortex has existing defenses), staleness (mitigated by bi-temporal status), cascade injection (mitigated by hop limit), semantic drift at scale (mitigated by per-workspace gate). |
| **Where should it live?** | **Separate injection layer** (`injector.py`). The ontology provides data, the gap ledger provides urgency signals, the injector provides the mechanism. Clean separation matches Cortex's existing architecture pattern. |

### Priority for Cortex

1. **Build the entity linker first** — it's the cheapest, highest-leverage component. A fuzzy match against the 153 entity names catches most of the value.
2. **Reuse the existing ontology graph traversal** — `_ontology_leg` in `search.py` already does multi-hop traversal. The injector calls it with a different trigger (entity mention vs search query).
3. **Build the context packet formatter** — this is the designed-but-unbuilt artifact. The format above is a starting point.
4. **Add the status-change trigger** — when `fable-max` becomes `expired`, or a scorecard is retracted, proactively surface that in the next session. This is event-driven and doesn't require entity linking.
5. **Gate everything behind a budget + threshold** — learn from Disease A. Hard token cap, hard score threshold, clear delimitation.
6. **Eval-gate it** — following Cortex's own discipline: measure whether injection improves outcomes (like the ontology retrieval gate measured nDCG). If it doesn't win, turn it off (same as the ontology fusion switch).
