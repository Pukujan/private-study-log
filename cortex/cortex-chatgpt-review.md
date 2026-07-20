# ChatGPT External Review of Cortex Pipeline — Consolidated

**Date:** 2026-07-16  
**Reviewer:** ChatGPT (external, adversarial)  
**Subject:** Cortex pipeline architecture at `D:\claude\cortex-agent-wrapper`  
**Source reviewed:** `cortex-pipeline-architecture.md` + public Cortex snapshot  

---

## Verdict 1: Initial Assessment

### What ChatGPT Verified

The architecture document reports that all 13 calls in a real walk received injected context, averaging 4,954 characters. It describes the search, ranker, token-budget and session-context modules. It reports 617 passing tests.

The public Cortex snapshot contains a real fused retrieval implementation:
- BM25 is always used
- Vector and ontology retrieval are optional legs
- Ranked lists combined through Reciprocal Rank Fusion (RRF)
- If vector or ontology retrieval fails, search falls back to BM25
- A trusted project-state projection can remove superseded documents after fusion

The ontology implementation is real — its tests construct two documents with completely disjoint vocabulary, connect them through a citation edge, and verify that ontology expansion retrieves the second document even though BM25 cannot. Tests also verify determinism and that disabling ontology is a true no-op.

### Key Finding From Cortex's Own Code

> Ontology retrieval was a net wash on the dense Cortex corpus and therefore ships off by default. The code recommends enabling it for scattered, loosely connected corpora where linked documents do not share vocabulary.

This supports the pattern-based approach: ontology should be routed by task and corpus conditions, not permanently enabled.

### The Central Problem

**The pipeline proves context injection — not retrieval completeness.**

It can reliably do: search several ways → fuse rankings → deduplicate → fit context into a budget → inject it into every LLM call.

It does NOT guarantee: every important document needed for the answer was found, the correct/current version was selected, and every required evidence category survived the token budget.

### Sandbox Reproduction

ChatGPT built a deterministic miniature reproduction with BM25, semantic retrieval proxy, RRF fusion, ontology traversal, current-vs-historical filtering, task-specific routing, token-budget selection, and multi-document answer requirements.

Nine synthetic tasks covered: direct lookup, current authority, historical lookup, multi-document synthesis, and relationship traversal.

| Pipeline | Mean recall@3 | Mean MRR | Wrong stale hits |
|----------|--------------|----------|-----------------|
| BM25 | 0.889 | 0.759 | 4 |
| BM25 + semantic RRF | 0.944 | 0.889 | 4 |
| Hybrid + ontology | 0.944 | 0.944 | 4 |
| Ontology + always-current filtering | 0.889 | 0.889 | 0 |
| Task-adaptive pipeline | **1.000** | **1.000** | **0** |

These are synthetic demonstration results, not production benchmarks. Key behaviors:
- Hybrid retrieval improved ranking over BM25
- Ontology expansion recovered distributed evidence
- Always filtering historical material broke a legitimate historical question
- Routing retrieval according to the task avoided that failure
- Ontology improved relational tasks but was unnecessary for simple direct lookups

### Token-Budget Failure Test

A 30-word context budget for a question requiring: (1) the current architecture decision, (2) its verification evidence.

**Ordinary rank-first budget selected:** auth_test, atlas_overview — **omitted the actual current decision**

**Schema-aware budget selected:** auth_current, auth_test — **both required evidence types fit inside the same budget**

> TokenBudget currently appears to optimize which highest-ranked chunks fit. It should optimize whether all required evidence slots are covered.

### What the Flowchart Is Missing

**Current structure:**
```
Prompt → Search → Rank → Budget → Inject → LLM
```

**Proposed stronger structure:**
```
User Prompt
    ↓
Task / Domain / Risk Classifier
    ↓
Retrieval Skill Selector
    ↓
Evidence Schema Builder
    ↓
Query Decomposition
    ↓
┌─────────────────────────────────────────┐
│ BM25 │ Vector │ Ontology │ Temporal     │
│ Code │ Decisions │ Failures │ Workflows │
└─────────────────────────────────────────┘
    ↓
Candidate Fusion
    ↓
Authority + Supersession Resolution
    ↓
Evidence-Slot Coverage Check
    ↓
Missing-Evidence Expansion Loop
    ↓
Coverage-Aware Token Budget
    ↓
Context Injector
    ↓
LLM
    ↓
Citation / Claim / Completeness Verification
    ↓
Retrieval Evaluation + Pattern Learning
```

### Evidence Schema Example

For an incident investigation:

```yaml
required_evidence:
  - symptom
  - affected_component
  - root_cause
  - remediation
  - verification
  - current_status

optional_evidence:
  - similar_incidents
  - rejected_fixes
  - historical_context
```

Retrieval does not stop because it found five highly relevant chunks. It stops because every mandatory slot is: covered, unsupported, conflicted, or explicitly unavailable.

### Role of the Living Ontology

The ontology should not merely be another equal RRF list. It should perform several separate jobs:

1. **Expansion** — find connected documents that share no vocabulary
2. **Authority** — determine active, superseded, deprecated and historical material
3. **Coverage** — describe which evidence types a complete answer requires
4. **Conflict detection** — identify claims or decisions that disagree
5. **Workflow routing** — select retrieval paths appropriate for incidents, code changes, research, architecture or verification
6. **Learning** — preserve successful paths as candidate retrieval skills

The learned/"neural" layer chooses routes, infers provisional relationships and adjusts weights. The symbolic ontology retains meaning, provenance, validity periods and inspectability.

### Research Support

- **RRF** — originally shown to combine multiple retrieval rankings more effectively than individual rankers. Subsequent analysis found RRF can be parameter-sensitive and learned score fusion may outperform it in some domains. Strong default, not universal optimum.
- **Adaptive-RAG** — dynamically choosing no retrieval, single-step retrieval or iterative retrieval according to question complexity improves both accuracy and efficiency over applying one strategy universally.
- **KG²RAG** — uses semantic retrieval for initial seeds, then expands and organizes chunks through knowledge-graph relationships. Improvements reported for multi-hop questions. Closely matches Cortex's ontology test mechanism.
- **GeAR** — graph expansion can augment BM25 or other base retrievers on multi-hop retrieval tasks.
- **OG-RAG** — grounding retrieval in a domain ontology and selecting a minimal set of structured factual clusters. 55% increase in accurate-fact recall, 40% improvement in response correctness over baselines (benchmark-specific).
- **Microsoft GraphRAG** — ordinary top-k vector retrieval struggles with corpus-wide or distributed questions; graph structure and community summaries improve comprehensiveness and diversity. Higher indexing and operational costs — appropriate retrieval mode depends on the question.

---

## Verdict 2: After Challenge (Corrected Assessment)

### ChatGPT's Correction

> "Credible system" was too strong. What I could support was: Cortex contains credible retrieval components and a real implementation path. I did not establish that the complete pipeline is production-reliable.

> The correct description is: Cortex has a credible experimental retrieval architecture, but its production retrieval reliability remains unproven.

### What the Repository Actually Proves

| Claim | Supported? |
|-------|-----------|
| The modules and retrieval mechanisms exist | **Yes** |
| Ontology can recover a linked document BM25 misses | **Yes**, in a controlled test |
| Context is injected into every recorded call | **Reported** by the project |
| Retrieval is more reliable than strong baselines | **Not yet demonstrated** |
| Important knowledge will not be missed | **Not demonstrated** |
| Production-grade at corpus scale | **Not independently demonstrated** |

The 617 passing tests establish functional and contract behavior; they do NOT establish retrieval recall, ranking quality or answer completeness on unseen queries.

### Comparable Production Pipelines

**Azure AI Search**
- Hybrid search runs BM25 and vector searches in parallel, combines with RRF, can apply a learned semantic reranker afterward
- Azure's agentic retrieval pipeline: analyzes conversation → decomposes compound questions → concurrent subqueries across knowledge sources → keyword/vector/hybrid retrieval → semantic reranking → merged grounding content + citations + query execution plan
- Microsoft reports ~36% higher response quality than traditional single-shot RAG (Microsoft's own benchmark)

**Elasticsearch** — supports hybrid full-text and vector retrieval, officially recommends RRF for combining result sets (production feature, not research prototype)

**Google Vertex AI Search** — semantic search, synonym understanding, spelling correction, autosuggest, structured and unstructured data retrieval, self-learning ranking

**Microsoft GraphRAG** — entity/relationship extraction, hierarchical community summaries. Used through Microsoft Discovery (Azure-based scientific research platform). DRIFT Search combines local retrieval, graph/community info, and auto-generated follow-up questions. Beat GraphRAG local search on comprehensiveness in 78% of comparisons, diversity in 81% (LLM judge — encouraging, not definitive).

### A Better Corpus Is Not Just a Vector Database

The strongest design uses several synchronized representations of the same source material:

```
Authoritative source store
    ↓
Structured document projection
    ↓
Lexical index + vector index
    ↓
Relationship/ontology index
    ↓
Hierarchical summaries
    ↓
Task-specific retrieval planner
```

#### 1. Authoritative Source Layer

```yaml
document_id:
source_uri:
content_hash:
version:
status: active | superseded | historical | draft
valid_from:
valid_until:
supersedes:
authority:
permissions:
ingested_at:
```

The original document — not its embedding or generated summary — remains the source of truth.

#### 2. Search Projection

Every chunk should retain enough parent context to remain meaningful. Cortex currently uses fixed chunking at ~1,500 characters. Fixed character boundaries can separate definitions, evidence and conclusions. Prefer boundaries based on headings, paragraphs, code symbols, tables, decisions and incident sections.

#### 3. Contextualized Chunks

Prepend parent-document context before creating BM25 and vector representations:

```
Document: Authentication ADR 42
Section: Verification
Status: Current
System: Atlas

[original chunk]
```

Anthropic reported: adding chunk-specific document context reduced top-20 retrieval failures by 35% for embeddings, 49% when combined with contextual BM25, 67% when a reranker was added.

#### 4. Multiple Levels of Granularity

- Atomic chunks for precise facts
- Sections for connected explanations
- Full parent documents for completeness
- Project or topic summaries for global questions

RAPTOR organizes content into a hierarchy of chunks and progressively higher summaries. GraphRAG uses entity communities and community summaries for corpus-wide questions.

#### 5. Optional Multi-Vector Representation

ColBERT-style retrieval stores multiple token-level vectors and uses late interaction. ColBERTv2 reported stronger retrieval across several benchmarks while compressing storage overhead 6-10×.

### Query-Pattern Algorithms: The Missing Control Layer

```
User request
    ↓
Intent and task classifier
    ↓
Evidence schema
    ↓
Query generation
    ↓
Retriever routing
    ↓
Candidate generation
    ↓
Fusion and reranking
    ↓
Authority and coverage checks
```

#### Example Task Patterns

| Detected task | Generated retrieval pattern |
|--------------|---------------------------|
| Exact identifier or error code quoted | BM25, exact field lookup, code index |
| Current decision | decision query + active-status filter + supersession traversal |
| Incident investigation | symptom + root cause + remediation + verification subqueries |
| "What changed?" | temporal range + version diff + superseded/current records |
| Distributed answer | decompose by required evidence categories |
| Relationship question | entity linking + bounded graph traversal |
| Broad project summary | hierarchical summaries or graph communities |
| Historical question | explicitly allow superseded documents |
| Contradiction check | original claim + negated variants + conflict edges |

#### Practical Query-Planning Algorithm (15 steps)

1. **Preserve exact identifiers** — file names, ticket numbers, function names, quoted phrases, versions
2. **Normalize the remainder** — spelling correction, abbreviation expansion, aliases, domain terminology
3. **Classify the task** — exact, semantic, relational, temporal, multi-document, global, historical/current
4. **Build an evidence schema** — e.g., incident: symptom, component, cause, fix, verification, current status
5. **Generate separate subqueries** — exact lexical, semantic paraphrase, one per evidence slot, relationship query, current-authority query, conflict/contradiction query
6. **Route each subquery** — BM25, dense/late-interaction retrieval, graph traversal, structured SQL/filter, code/AST search, hierarchical summary search
7. **Retrieve a broad candidate pool**
8. **Fuse results** — RRF as safe baseline, learned fusion when enough labeled data exists
9. **Rerank candidates** — query-document cross-encoder, task-specific importance signals
10. **Resolve lifecycle and authority** — current vs historical, draft vs accepted, superseded vs active
11. **Test evidence coverage** — which required slots are covered? which conflict? which remain absent?
12. **Generate follow-up searches only for missing slots**
13. **Select context by coverage, not solely rank**
14. **Retrieve parent context and citations**
15. **Stop on coverage saturation or budget limits**

### What Top Search Systems Do Differently

Multi-stage retrieval, not one search followed by one ranking:

```
Query understanding
→ query rewriting/expansion
→ inexpensive candidate generation
→ multiple retrieval channels
→ fusion
→ expensive reranking of a smaller set
→ freshness, authority and policy signals
→ deduplication and diversity
→ final result presentation
```

Microsoft found conversational query rewriting improved ranking accuracy by 12% on TREC conversational benchmark. Bing research formulated candidate match planning as reinforcement learning, reducing index blocks accessed by up to 20% with little quality degradation. Azure AI Search can generate up to ten rewritten queries but warns rewriting can lose exact terms (product codes, identifiers) — so exact and semantic variants should run in parallel.

### What Cortex Should Become

**Current:**
```
prompt → hybrid search → chunk ranker → token budget → injection
```

**Stronger:**
```
prompt
→ task-pattern classifier
→ evidence-schema compiler
→ query planner
→ task-routed retrieval channels
→ fusion
→ semantic reranking
→ authority resolution
→ evidence coverage loop
→ coverage-aware budget
→ context injection
```

The ontology becomes useful where the query requires relationships or distributed evidence. It stays disabled for simple direct searches when evaluation shows no gain.

### Required Benchmark for Proof

The decisive proof should come from a held-out Cortex benchmark covering:
- Exact identifier retrieval
- Paraphrases and vocabulary mismatch
- Distributed multi-file answers
- Current versus superseded decisions
- Incident cause, fix and verification
- Contradictory sources
- Global corpus questions
- Code and documentation relationships

**Metrics:**
- Recall@k
- MRR / NDCG
- All-required-documents recall
- Current-authority accuracy
- Stale-source leakage
- Contradiction recall
- Unsupported-claim rate
- Latency
- Token cost

Until that benchmark shows improvement over BM25, hybrid retrieval and hybrid-plus-reranker baselines:

> Cortex has a credible experimental retrieval architecture, but its production retrieval reliability remains unproven.

---

## Summary of Both Verdicts

### What ChatGPT Confirmed Works
- ✅ BM25 + vector + ontology RRF fusion (real implementation)
- ✅ Ontology graph traversal recovers linked documents BM25 misses (tested)
- ✅ Current-document filtering removes superseded material
- ✅ Determinism verified, disabling ontology is a true no-op
- ✅ Context injected into every recorded LLM call (13/13, 4954 avg chars)
- ✅ 617 tests establish functional and contract behavior
- ✅ Graceful fallback to BM25 when vector/ontology fails
- ✅ Complete prompt logging

### What ChatGPT Identified as Missing
- ❌ Task-conditioned evidence coverage before context injection
- ❌ Evidence schema (required slots: symptom, root cause, remediation, verification, etc.)
- ❌ Coverage-aware token budget (optimizes for coverage, not just relevance)
- ❌ Missing-evidence expansion loop (search again for unfilled slots)
- ❌ Query decomposition and task-pattern routing
- ❌ Authority/supersession resolution as a distinct step
- ❌ Citation/claim/completeness verification after LLM response
- ❌ Retrieval evaluation benchmark (recall@k, MRR, stale-source leakage, etc.)
- ❌ Production retrieval reliability not independently demonstrated

### The Core Shift

ChunkRanker should stop asking:
> "Which chunks are most relevant?"

And start asking:
> "Which smallest set of current, non-duplicated chunks satisfies every evidence requirement of this task?"

### Comparable Systems in Production
- Azure AI Search (hybrid + RRF + agentic retrieval)
- Elasticsearch (hybrid + RRF, production feature)
- Google Vertex AI Search (semantic + self-learning ranking)
- Microsoft GraphRAG / DRIFT Search (graph communities + follow-up questions)
- RAPTOR (hierarchical chunk summaries)
- ColBERTv2 (late interaction, multi-vector)

---

*Source: Two ChatGPT external reviews of Cortex pipeline architecture, 2026-07-16. Sandbox reproduction by ChatGPT. Research citations include RRF, Adaptive-RAG, KG²RAG, GeAR, OG-RAG, GraphRAG, DRIFT, RAPTOR, ColBERTv2, Anthropic contextual retrieval, Azure AI Search, Elasticsearch, Google Vertex AI Search.*
