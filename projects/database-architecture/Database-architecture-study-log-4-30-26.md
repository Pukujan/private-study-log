# Study Log — Database Architecture
**Date:** April 30, 2026
**Source document:** db-learning.md
**Project:** Regulatory Impact Demo — Scorealytics Hiring Demo
**Time spent:** Full session

---

## What I Was Trying to Understand

Coming into this I knew Postgres well from the litigation annotator. I had heard of vector databases and graph databases but had no real idea what they did or why you would use them instead of Postgres. I also did not understand why this project needed three databases at all. That felt overcomplicated to me.

By the end I understood why each database exists, what problem it solves that the others cannot, and how they hand off data to each other in a single API call.

---

## The Central Insight I Kept Coming Back To

Every database was chosen because it answers a question the other two cannot answer well.

```
Qdrant  → "Which chunks MEAN the same as this sentence?"
Neo4j   → "What does this chunk mean inside the regulatory structure?"
Postgres → "What did we analyze, decide, and export?"
```

Before today I would have tried to use Postgres for all three. I now understand why that creates problems. Postgres can do semantic search with the pgvector extension but it is bolted on — not native. Postgres can do relationship traversal but it requires 4-5 JOINs and the structure of how things connect becomes invisible in the schema. The right tool for each job is the entire architectural argument.

---

## Postgres — What I Already Knew vs. What I Learned

### What I already knew
Postgres is a relational database. Tables, rows, columns, SQL. I use this in the litigation annotator. Joins, foreign keys, indexes. Comfortable territory.

### What I learned today
The limit is meaning. Postgres stores and retrieves exact values. It has no concept of semantic similarity. You cannot ask Postgres "give me rows most similar in meaning to this sentence" without an extension. This is the wall that makes a vector database necessary.

Also learned where Postgres fits in this specific project — it is deferred to production because for the demo everything is in memory. When it is added, it becomes the canonical audit trail: every result, every annotation, every export. It does not participate in the retrieval or scoring — it just persists the record of what happened.

### Things that made it click
The fake SQL query in the doc:
```sql
SELECT * FROM regulatory_chunks
ORDER BY similarity_to('We disclose climate-related risks annually') DESC;
```
This is not real SQL. Postgres cannot do this. Seeing it written as a fake query that does not work made the limitation concrete rather than abstract.

---

## Qdrant — Completely New Territory

### What I did not understand before
I had heard "vector database" but thought it was just Postgres with extra steps. It is not. The entire data model is different.

### What a vector actually is
An embedding is a list of numbers — 384 of them in this project — that represents the meaning of a piece of text. The embedder (our Python service running `all-MiniLM-L6-v2`) converts text into these numbers.

The key thing: **similar meaning produces similar numbers**. Two sentences about climate risk disclosure will produce number arrays that are close to each other in mathematical space. Two sentences about completely unrelated topics will produce number arrays that are far apart.

Cosine similarity measures how close two vectors are and gives a score from 0 to 1.

### The data structure
No tables. Collections and points.

```json
{
  "id": "a3f2c1d4-... (UUID, not a string slug)",
  "vector": [0.032, -0.14, 0.87, ...],
  "payload": { "chunk_id": "EU_ESRS_E1_001", "region": "EU", ... }
}
```

Three parts: the ID, the vector (meaning), the payload (metadata for filtering).

### How search works
1. Take the clause text
2. Send it to the Python embedder → get 384 numbers back
3. Send those numbers to Qdrant with a filter (only EU jurisdiction)
4. Qdrant finds the points whose vectors are closest to yours
5. Returns chunk IDs and similarity scores
6. Those scores become `retrieval_score` in the confidence formula

### The problems we hit
Two real things broke during setup that I need to remember:

**Point IDs must be UUIDs.** String slugs like `EU_ESRS_E1_001` are rejected. We fixed this by hashing the string with SHA1 to get a stable UUID. The original string stays in the payload where we can still read it.

**Payload indexes must be created before filtering.** Without an index on `jurisdiction_id`, Qdrant scans every single point to apply the filter. At 8 chunks this is fine. At millions of chunks this becomes catastrophically slow. We had to delete the collection and recreate it because you cannot add indexes to an existing collection retroactively.

### What still feels fuzzy
The 384 dimensions are hard to visualise. The document says to think of it as a 2D space where similar concepts cluster together — that helps but I know the reality is 384-dimensional and I cannot picture that. I understand the concept of "closer = more similar" well enough to work with it.

---

## Neo4j — Also New, But Clicked Faster

### What I did not understand before
I knew graph databases existed but thought of them as a special-purpose thing for social network data. Did not understand why you would use one for regulatory text.

### The core idea
Nodes are things. Relationships are named arrows between things. The structure of how things connect is part of the data — not implied by foreign keys.

```
(Region:EU) -[HAS_FRAMEWORK]-> (Framework:ESRS)
            -[HAS_OBLIGATION]-> (Obligation:Climate Risk Disclosure)
            -[SUPPORTED_BY_CHUNK]-> (Chunk:EU_ESRS_E1_001)
```

### Cypher vs SQL — the comparison that made it click
The same query in both languages:

```cypher
MATCH (r:Region)-[:HAS_FRAMEWORK]->(f)-[:HAS_OBLIGATION]->(o)-[:SUPPORTED_BY_CHUNK]->(c)
WHERE c.id = 'EU_ESRS_E1_001'
RETURN r.name, f.name, o.text, c.citation_label
```

```sql
SELECT r.name, f.name, o.text, c.citation_label
FROM regions r
JOIN frameworks f ON f.region_id = r.id
JOIN obligations o ON o.framework_id = f.id
JOIN regulatory_chunks c ON c.obligation_id = o.id
WHERE c.id = 'EU_ESRS_E1_001'
```

Same result. But Cypher makes the shape of the relationship visible in the code itself. As the graph grows this matters a lot. At four JOINs it is still readable. At eight or ten JOINs it becomes a wall of SQL where the structure is completely invisible.

### Why Neo4j specifically for bridges
This was the most important thing I learned about why a graph database is necessary rather than just convenient.

Bridge edges between EU and US topics carry their own data:
```
TRANSLATES_TO {
  bridge_type: "semantic_equivalence",
  confidence: 0.72,
  caveats: ["EU requires double materiality"],
  reliability: "unverified"
}
```

In Postgres this requires a separate junction table. The relationship between two topics is not a first-class thing — it has to live in its own table with foreign keys pointing at the two topic rows. In Neo4j the relationship carries data natively. The caveats, the confidence, the reliability all live on the edge itself.

This matters for the living ontology too. New Topic nodes can be created at runtime without any schema migration. `origin: dynamic` marks them as human-promoted so the growth is auditable.

### The AuraDB username problem
This hit us during setup and is worth remembering. The AuraDB documentation says the username is `neo4j`. For our instance it is `d5fca390` (the instance ID). The standard connectivity check `verifyConnectivity()` does NOT test authentication — it only checks if the server is reachable on the network. Auth only fails when you run a real Cypher query.

Always test with `RETURN 1 AS n` after connecting. Never trust `verifyConnectivity()` as an auth check.

---

## How The Three Databases Hand Off Data

This is the sequence I want to be able to explain from memory:

```
1. POST /analyze arrives with a policy clause

2. Topic detector (backend)
   keyword map → topic: climate_risk_disclosure

3. Python embedder
   clause text → [0.032, -0.14, 0.87, ...] (384 numbers)

4. Qdrant
   input: the 384 numbers + filter: jurisdiction EU
   output: chunk IDs + similarity scores
   e.g. EU_ESRS_E1_001 at 0.84, EU_ESRS_E1_003 at 0.71

5. Neo4j
   input: the chunk IDs from Qdrant
   output: graph paths — Region → Framework → Obligation → Topic → Evidence
   also: graph_match_score (fraction of expected hops resolved)

6. Confidence scorer (pure backend math, no AI)
   retrieval_score  × 0.40
   graph_match_score × 0.35
   evidence_coverage × 0.25
   = raw_score
   then apply caps if triggered
   = final_score

7. Response
   impact_status, confidence_score, citations, graph paths, caps_applied
```

The handoff is: Qdrant produces chunk IDs → Neo4j takes those chunk IDs and explains what they mean → backend combines both into a score.

---

## The Confidence Scoring Formula

Before today this was abstract. Now I understand every component:

| Component | Weight | Where it comes from |
|---|---|---|
| retrieval_score | 0.40 | Top Qdrant similarity score |
| graph_match_score | 0.35 | Fraction of graph hops Neo4j resolved |
| evidence_coverage | 0.25 | Fraction of required evidence types covered |

The caps are what make it honest:
- No citations at all → max 0.45
- Qdrant score below 0.65 → max 0.60
- No Neo4j graph context → max 0.65
- Outdated regulation → max 0.55
- Unverified bridge → max 0.70

The lowest cap wins if multiple apply. All caps surface in `caps_applied` so the caller knows exactly why the score is low. This is the important design decision — confidence is computed from evidence, not from asking an LLM how confident it feels.

---

## Living Ontology — Why It Matters

The static vocabulary problem: if you build a graph with fixed terms and a new regulatory concept appears (greenwashing, biodiversity, scope 3), the system either misclassifies it or fails silently. Every new term requires a developer to update the schema and redeploy.

The living ontology solution:
1. Unknown term arrives
2. System caps confidence at 0.40 (does not guess)
3. System finds nearest known concepts in Qdrant vector space
4. System proposes edges to existing Neo4j nodes
5. Returns response with candidates visible
6. Human reviews via `/ontology/expand`
7. New Topic node created with `origin: dynamic`
8. Next analyze call resolves the term correctly

The `origin: dynamic` field is the audit trail. You can always query which nodes were seeded vs. which grew from use. The ontology grows without redeployment and without schema migration.

---

## Annotation — The Feedback Loop I Had Not Thought About

Every annotation does three things:
1. Updates `human_annotation_calibration` in the confidence formula for similar future results
2. Determines whether the result is eligible for eval or finetune export
3. Builds the labelled dataset that will eventually train a real classifier

The eligibility table I need to remember:

| Decision | Eval | Finetune |
|---|---|---|
| approve | ✓ | ✓ |
| correct_impact_status | ✓ | ✓ |
| mark_citation_insufficient | ✓ | ✗ |
| reject_hallucinated_reasoning | ✓ (negative) | ✗ |
| needs_legal_review | ✗ | ✗ |

Eligibility is derived from decision type at export time. It is not stored on the annotation record. One source of truth.

---

## Questions I Still Have

**On Qdrant:**
- How does HNSW (the index type) actually find nearest neighbors faster than scanning everything? I know it is a graph-based approximation algorithm but I do not understand the mechanism.
- When we add more jurisdictions, do we create separate collections per jurisdiction or keep one collection and rely on payload filters? The current design uses payload filters — I want to understand at what scale that breaks down.

**On Neo4j:**
- What happens to performance when the graph has thousands of nodes? Does Cypher traversal slow down the same way SQL JOINs do at scale?
- How do you handle conflicting obligations — cases where EU requires something that contradicts US requirements? The plan mentions `conflict_detection` as a confidence component but I do not fully understand how it is computed.

**On the confidence formula:**
- The weights (0.40, 0.35, 0.25) seem reasonable but are they based on anything empirical or are they initial guesses that get calibrated over time through annotation?
- What happens to `human_annotation_calibration` when annotations disagree with each other? How do you handle two reviewers who made opposite decisions on the same result type?

**On the living ontology:**
- At what point does a `dynamic` topic node get promoted to a `seed` node? Or does it stay dynamic forever?
- If a human promotes a term incorrectly — approves an edge that turns out to be wrong — how do you retract it?

---

## Things That Surprised Me

The embedder being a separate Python service was not something I expected. I assumed embedding would be handled inside the Node backend. Having it as a separate FastAPI service is clean because it means you can swap the embedding model without touching the backend code — as long as the vector dimension stays the same.

The fact that `verifyConnectivity()` does not test auth. This bit us hard during setup. I would have assumed a connectivity check tested the full connection including credentials. It does not. This is worth writing down in capital letters.

The federation argument — that EU "climate risk disclosure" and US "climate risk disclosure" are not the same obligation — is obvious in hindsight but I had not thought about it before. The CTO's long-term goal is a universal schema for all global regulations. The counter-argument in this plan is that collapsing jurisdictions into one schema loses the legal nuance that makes the product valuable. Bridge edges with caveats are the architectural answer to that tension.

---

## What I Want to Be Able to Explain in the Interview

Without looking at notes, I should be able to answer:

**"Why three databases?"**
Each answers a different question. Qdrant for semantic similarity, Neo4j for structural relationships, Postgres for canonical records. Postgres can do the other two but fights you on both.

**"How does the confidence score work?"**
Three components weighted and summed. Caps applied for specific failure conditions. Lowest cap wins. Computed from retrieval evidence, not from LLM self-report.

**"What happens when the system encounters a term it has never seen?"**
Confidence is capped at 0.40. Dynamic candidates are surfaced from Qdrant nearest neighbors and proposed Neo4j edges. Human reviews and approves. New Topic node created with `origin: dynamic`. Re-analyze resolves.

**"How do you scale this to more jurisdictions?"**
Add a new district with its own jurisdiction-scoped nodes in Neo4j and chunks in Qdrant. Build bridge edges to existing districts. Bridges default to `unverified` until human approval. The local schema stays accurate. Global coverage grows incrementally.

**"How does this get better over time?"**
Annotations update calibration. Approved and corrected annotations build a labelled finetune dataset. Unknown terms feed the ontology expansion workflow. Eventually the keyword-based topic detector gets replaced by a classifier trained on annotation data — but the API contract never changes.

---

## Phase Status as of Today

| Phase | Status |
|---|---|
| Phase 0: Scaffold | ✅ Was already complete when we started |
| Phase 1: EU + US seed | ✅ Completed this session — real data in real infrastructure |
| Phase 2: Local analyze | 🔨 Next — this is where it starts doing something visible |
| Phase 3 onwards | Not started |

Seed result confirmed:
- mode: `full_embedder` (real vectors, not placeholders)
- EU graph: 17 nodes, 22 relationships
- US graph: 8 nodes, 9 relationships
- EU chunks: 6, US chunks: 2
- Vector dimension: 384

---

## How to Study This Further

**Read next:**
- The Microsoft GraphRAG paper — this is the academic foundation for combining vector retrieval with graph context
- Qdrant documentation on HNSW index — understand why nearest neighbor search is fast
- Neo4j documentation on Cypher query planning — understand what happens at scale

**Build next:**
- Phase 2: implement `analyze.js`, `confidence-scorer.js`, `topic-detector.js`
- Before writing code, read the current stubs and understand what is already scaffolded
- The confidence scorer is the most important service — it is what the CTO will examine most carefully

**Test understanding:**
- Can I draw the full `/analyze` call flow from memory without looking at the diagram?
- Can I explain why payload indexes matter without looking at the notes?
- Can I explain the federation argument without reading the plan?

If the answer to all three is yes, I understand this material well enough for the interview.
