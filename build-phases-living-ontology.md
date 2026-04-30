# Regulatory Impact Demo — Concrete Build Plan
## Scorealytics Hiring Demo | litigation-rag | v3.0
**Date:** April 30, 2026
**Status:** Phase 1 complete. Phase 2 is next.

---

## What This Document Is

This is the working build plan for the `regulatory-impact-demo` module. It is not a concept document — it is the step-by-step sequence of what to build, in what order, with exact success conditions for each phase. Every decision in here was made for a specific reason. Those reasons are explained so you can defend them in the interview.

Keep this open while building. When a phase is complete, mark it done and move to the next one.

---

## The One-Sentence Version

> Build a backend module inside the existing `litigation-rag` monolith that compares a proposed ESG policy change against EU/ESRS E1 and US/SEC regulations using real Qdrant vector retrieval and real Neo4j graph context, then shows what happens when the system encounters a term it has never seen before, and how two jurisdictions can be connected via typed bridge edges that carry legal nuance — proving a federated path toward universal regulatory coverage.

---

## Why This Demo Exists

The Scorealytics CTO posted a hiring requirement that named three specific technical problems:

```
Problem 1: Vector databases alone hit similarity walls
           → Similar text does not always mean similar legal obligation

Problem 2: Graph databases add context but create a fixed vocabulary
           → New regulatory terms break the system

Problem 3: The long-term goal is universal coverage of global regulation
           → But a universal schema collapses legal nuance
```

This demo addresses all three directly and in sequence. Stage 1 proves the vector + graph combination. Stage 2 demonstrates the fixed vocabulary wall and the living ontology answer. Stage 3 shows how districts federate toward universal coverage without losing jurisdiction-specific accuracy.

The demo is built inside the existing monolith — not in a new repo — because that proves the candidate can ship within existing architecture.

---

## What Is Already Built

```
✅ Module scaffold — backend and frontend folders exist
✅ All service files — created as stubs (not yet implemented)
✅ All adapter files — created as stubs
✅ All fixture files — EU and US regulatory chunks and Cypher seeds
✅ Phase 1 — EU + US seed into real Qdrant and Neo4j (COMPLETE)

Seed result confirmed:
  mode:             full_embedder (real vectors, not placeholders)
  EU graph:         17 nodes, 22 relationships
  US graph:         8 nodes, 9 relationships
  EU Qdrant chunks: 6
  US Qdrant chunks: 2
  vector dimension: 384 (all-MiniLM-L6-v2)
  fallback_used:    false
```

Work starts at Phase 2.

---

## Repository Structure

Only touch files inside these paths. Do not modify any other module.

```
litigation-rag/
├── backend/src/
│   ├── modules/
│   │   └── regulatory-impact-demo/     ← YOUR MODULE
│   │       ├── index.js
│   │       ├── router.js
│   │       ├── API.md
│   │       ├── adapters/
│   │       │   ├── neo4j.adapter.js
│   │       │   └── qdrant.adapter.js
│   │       ├── evals/
│   │       │   └── critical-path.test.js
│   │       ├── fixtures/
│   │       │   ├── eu-esrs-e1-seed.cypher
│   │       │   ├── eu-regulatory-chunks.json
│   │       │   ├── us-sec-climate-seed.cypher
│   │       │   └── us-regulatory-chunks.json
│   │       └── services/
│   │           ├── analyze.js
│   │           ├── bridge-detector.js
│   │           ├── confidence-scorer.js
│   │           ├── federated-analyze.js
│   │           ├── ontology-expander.js
│   │           ├── policy-change-diff.js
│   │           ├── seed.js
│   │           └── topic-detector.js
│   └── shared/                         ← READ ONLY — do not modify
│       ├── graph/index.js              ← Neo4j driver
│       ├── vector/index.js             ← Qdrant driver
│       ├── events/index.js             ← Event bus
│       └── llm/index.js               ← Available if needed
├── frontend/src/
│   ├── core/
│   │   └── router.jsx                 ← Only frontend file you touch
│   └── modules/
│       └── regulatory-impact-demo/    ← YOUR MODULE
│           ├── api/client.js
│           ├── index.jsx
│           ├── components/
│           │   ├── ConfidenceMeter.jsx
│           │   ├── GraphPath.jsx
│           │   └── ImpactMatrix.jsx
│           └── pages/
│               ├── DistrictView.jsx
│               ├── BridgeBuilder.jsx
│               └── FederationMap.jsx
└── python/services/embedder/
    └── main.py                        ← Embedder service — runs separately
```

---

## Shared Driver Imports

These are the exact import paths. Use them word for word. Do not use `driver.js` directly.

```javascript
import { getGraphDriver }  from "../../shared/graph/index.js"   // Neo4j
import { getVectorDriver } from "../../shared/vector/index.js"  // Qdrant
import { getEventBus }     from "../../shared/events/index.js"  // Events
import { chat }            from "../../shared/llm/index.js"     // LLM (optional)
```

---

## The Three Database Roles

Each database answers one specific question. This is the architectural decision you need to be able to explain.

```
Qdrant  → "Which chunks MEAN the same as this clause?"
          Semantic similarity. Cosine distance on 384-dim vectors.
          Jurisdiction isolation via payload filter on jurisdiction_id.

Neo4j   → "What does this chunk mean inside the regulatory structure?"
          Graph traversal. Region → Framework → Obligation → Topic → Evidence.
          Bridge edges between jurisdictions carry caveats and reliability.

Postgres → "What did we analyze, decide, and export?"
           Canonical audit trail. Deferred to production — not active in demo.
```

---

## The Confidence Formula

Every confidence score in this system is computed the same way. This formula must be implemented exactly as written. It is what makes the demo defensible.

```
Component              Weight   Source
--------------------   ------   ------------------------------------
retrieval_score        0.40     Top Qdrant cosine score
graph_match_score      0.35     Fraction of expected graph hops resolved
evidence_coverage      0.25     Fraction of required EvidenceTypes covered

raw_score = (retrieval_score × 0.40)
          + (graph_match_score × 0.35)
          + (evidence_coverage × 0.25)

final_score = min(raw_score, lowest_applicable_cap)
```

### Caps

```
Cap    Trigger
-----  -------------------------------------------------
0.45   citations array is empty
0.55   regulation status is repealed or outdated
0.40   unknown concept, no candidates surfaced
0.65   unknown concept, candidates surfaced but unverified
0.70   bridge exists but reliability == "unverified"
```

If multiple caps apply, the lowest one wins. Every applied cap appears in `caps_applied`.

### Confidence Levels

```
>= 0.85  → high
>= 0.70  → medium_high
>= 0.55  → medium
>= 0.40  → low
<  0.40  → very_low
```

### Review Flag

```javascript
requires_human_review = (
  caps_applied.length > 0 ||
  ['conflict', 'uncertain'].includes(impact_status) ||
  final_score < 0.70 ||
  bridge_reliability === 'unverified'
)
```

---

## The Demo Clauses

These are the only inputs needed for the demo. Do not add more.

```
Known clauses (Stage 1):
  CLAUSE_001: "We disclose climate-related risks annually in our sustainability report."
  CLAUSE_002: "We report Scope 1 and Scope 2 greenhouse gas emissions where data is available."

Proposed change (Stage 1):
  PCH_001: "Add Scope 3 emissions disclosure and board oversight of climate-related risks."
    PROPOSED_001: "We disclose Scope 3 greenhouse gas emissions annually."
    PROPOSED_002: "Our board of directors oversees climate-related risks."

Wall-test clauses (Stage 2 — exact terms from hiring post):
  UNKNOWN_001: "We assess biodiversity impact near our supply chain facilities."
  UNKNOWN_002: "Our anti-greenwashing disclosures follow EU consumer protection standards."
```

---

## API Endpoints

```
GET  /api/regulatory-impact-demo/health
GET  /api/regulatory-impact-demo/architecture
POST /api/regulatory-impact-demo/seed
POST /api/regulatory-impact-demo/analyze
POST /api/regulatory-impact-demo/analyze-change
POST /api/regulatory-impact-demo/ontology/expand
POST /api/regulatory-impact-demo/bridges/suggest
POST /api/regulatory-impact-demo/bridges/approve
GET  /api/regulatory-impact-demo/export/report
```

---

## Result ID Format

Result IDs must be deterministic so annotations and exports stay stable across runs.

```
Format: RESULT_{clause_id}_{region}_{framework}

Examples:
  RESULT_CLAUSE_001_EU_ESRS
  RESULT_PROPOSED_001_EU_ESRS
  RESULT_CLAUSE_001_US_SEC
```

---

## Phase 2 — Local Analyze + Confidence Scoring

**Status:** Next
**Objective:** `/analyze` returns a real Qdrant match, a real Neo4j graph path, and a deterministic confidence score for a known clause.

### Files to implement

```
services/topic-detector.js
services/confidence-scorer.js
services/analyze.js
```

### Topic detector — exact keyword map

```javascript
// services/topic-detector.js
// This is the complete keyword map for the demo.
// Do not expand it. The wall-test clauses must stay unknown.

const topicMap = [
  { keywords: ['climate', 'risk'],          topic: 'climate_risk_disclosure' },
  { keywords: ['scope 1'],                  topic: 'ghg_emissions_scope_1_2' },
  { keywords: ['scope 2'],                  topic: 'ghg_emissions_scope_1_2' },
  { keywords: ['scope 3'],                  topic: 'ghg_emissions_scope_3' },
  { keywords: ['board', 'climate'],         topic: 'climate_governance_oversight' },
  { keywords: ['board', 'risk'],            topic: 'climate_governance_oversight' },
];

// Unknown terms that must hit the wall:
// 'biodiversity' → UNKNOWN
// 'greenwashing' → UNKNOWN
// anything else  → general_sustainability_disclosure
```

### Confidence scorer — implementation contract

```javascript
// services/confidence-scorer.js
// Must return this exact shape on every call — no exceptions.

{
  confidence_score: 0.78,           // final_score after caps
  confidence_level: "medium_high",  // derived from score band
  components: {
    retrieval_score: 0.84,          // from Qdrant
    graph_match_score: 0.82,        // from Neo4j
    evidence_coverage: 0.76         // from Neo4j
  },
  caps_applied: [],                 // array of triggered caps
  requires_human_review: true       // derived from caps + status + score
}
```

### Analyze service — call sequence

```
1. detectTopic(clause)
   → if unknown: surface candidates, cap at 0.40, return early
   → if known: continue

2. embed clause text via embedder service
   POST http://{EMBEDDER_URL}/embed { text: clause }
   → returns { embedding: float[384] }

3. search Qdrant
   filter: jurisdiction_id = jurisdiction
   vector: embedding from step 2
   limit: 5
   → returns chunk matches with scores

4. get graph context from Neo4j
   input: chunk IDs from step 3
   MATCH paths from each chunk back through
   Obligation → Framework → Region + EvidenceTypes
   → returns paths and evidence coverage

5. score
   compute raw_score from components
   apply caps
   derive confidence_level and requires_human_review

6. assemble response
   result_id: RESULT_{clause_id}_{region}_{framework}
   schema_version: "1.0.0"
   all fields as per sample response below
```

### Sample request

```json
{
  "clause_id": "CLAUSE_001",
  "policyClause": "We disclose climate-related risks annually in our sustainability report.",
  "regions": ["EU"]
}
```

### Sample response

```json
{
  "clause_id": "CLAUSE_001",
  "jurisdiction_id": "EU_ESRS_E1",
  "schema_version": "1.0.0",
  "adapter_status": {
    "qdrant": "live",
    "neo4j": "live"
  },
  "results": [
    {
      "result_id": "RESULT_CLAUSE_001_EU_ESRS",
      "region": "EU",
      "framework": "ESRS",
      "impact_status": "partially_aligned",
      "risk_level": "medium",
      "confidence_score": 0.78,
      "confidence_level": "medium_high",
      "citations": ["EU_ESRS_E1_001"],
      "qdrant_matches": [
        {
          "chunk_id": "EU_ESRS_E1_001",
          "score": 0.84,
          "citation_label": "ESRS E1 §29 (demo paraphrase)"
        }
      ],
      "neo4j_graph_context": [
        {
          "chunk_id": "EU_ESRS_E1_001",
          "path": [
            "Region:EU",
            "Framework:ESRS",
            "Obligation:OBL_EU_001",
            "Topic:climate_risk_disclosure",
            "EvidenceType:governance_disclosure"
          ],
          "graph_match_score": 0.82
        }
      ],
      "confidence_components": {
        "retrieval_score": 0.84,
        "graph_match_score": 0.82,
        "evidence_coverage": 0.76
      },
      "caps_applied": [],
      "requires_human_review": true
    }
  ]
}
```

### Phase 2 success conditions

```
[ ] CLAUSE_001 returns confidence >= 0.70
[ ] Result ID is RESULT_CLAUSE_001_EU_ESRS
[ ] citations array is non-empty
[ ] qdrant_matches contains at least one match with a real score
[ ] neo4j_graph_context contains at least one resolved path
[ ] confidence_components all populated
[ ] No citations → cap 0.45 applied and appears in caps_applied
[ ] schema_version: "1.0.0" present in response
```

### Verification curl

```bash
curl -s -X POST http://localhost:3001/api/regulatory-impact-demo/analyze \
  -H "Content-Type: application/json" \
  -d '{"clause_id":"CLAUSE_001","policyClause":"We disclose climate-related risks annually in our sustainability report.","regions":["EU"]}' \
  | python3 -m json.tool
```

---

## Phase 3 — Living Ontology Expansion

**Status:** Not started
**Objective:** Unknown terms surface candidates. Human approval promotes them to real Topic nodes. Re-analyze resolves the term.

### Files to implement

```
services/ontology-expander.js
```

### What happens when a wall is hit

```
1. topic-detector returns topic: null, unknown: true

2. analyze service:
   a. Qdrant: find nearest known topic vectors to the unknown term's vector
   b. Neo4j: find Topic nodes reachable from those nearest chunks
   c. Assemble dynamic_candidates with suggested edges

3. Cap confidence at 0.40
4. Return response with dynamic_candidates visible
5. Human reviews and calls POST /ontology/expand
```

### Wall hit response shape

```json
{
  "clause_id": "UNKNOWN_001",
  "impact_status": "uncertain",
  "confidence_score": 0.40,
  "confidence_level": "low",
  "dynamic_candidates": [
    {
      "candidate_term": "biodiversity",
      "jurisdiction_id": "EU_ESRS_E1",
      "vector_nearest_neighbors": [
        { "node_id": "Topic:climate_risk_disclosure", "similarity": 0.68 }
      ],
      "suggested_local_edges": [
        { "to": "Topic:climate_risk_disclosure", "type": "SEMANTICALLY_RELATED_TO" }
      ]
    }
  ],
  "caps_applied": ["unknown_concept_no_candidates"],
  "requires_human_review": true
}
```

### Ontology expansion endpoint

```json
POST /api/regulatory-impact-demo/ontology/expand

{
  "candidate_term": "biodiversity",
  "jurisdiction_id": "EU_ESRS_E1",
  "approved_edges": [
    { "to": "Topic:climate_risk_disclosure", "type": "SEMANTICALLY_RELATED_TO" }
  ]
}
```

### Cypher that gets executed on approval

```cypher
MERGE (t:Topic { id: 'biodiversity_impact' })
ON CREATE SET
  t.name = 'Biodiversity Impact',
  t.status = 'active',
  t.origin = 'dynamic',
  t.jurisdiction_id = 'EU_ESRS_E1'
WITH t
MATCH (existing:Topic { id: 'climate_risk_disclosure' })
MERGE (t)-[:SEMANTICALLY_RELATED_TO { source: 'vector_co_occurrence' }]->(existing);
```

`origin: dynamic` is mandatory on every promoted node. It is the audit trail.

### Phase 3 success conditions

```
[ ] UNKNOWN_001 "biodiversity" returns confidence <= 0.40
[ ] dynamic_candidates is present and non-empty in that response
[ ] caps_applied contains "unknown_concept_no_candidates"
[ ] After /ontology/expand, Neo4j contains new Topic node with origin: dynamic
[ ] Re-analyze on UNKNOWN_001 returns confidence >= 0.55
[ ] Re-analyze result no longer shows wall hit response shape
```

### Verification curl sequence

```bash
# Step 1: confirm wall hit
curl -s -X POST http://localhost:3001/api/regulatory-impact-demo/analyze \
  -H "Content-Type: application/json" \
  -d '{"clause_id":"UNKNOWN_001","policyClause":"We assess biodiversity impact near our supply chain facilities.","regions":["EU"]}' \
  | python3 -m json.tool

# Step 2: approve expansion
curl -s -X POST http://localhost:3001/api/regulatory-impact-demo/ontology/expand \
  -H "Content-Type: application/json" \
  -d '{"candidate_term":"biodiversity","jurisdiction_id":"EU_ESRS_E1","approved_edges":[{"to":"Topic:climate_risk_disclosure","type":"SEMANTICALLY_RELATED_TO"}]}' \
  | python3 -m json.tool

# Step 3: confirm resolution
curl -s -X POST http://localhost:3001/api/regulatory-impact-demo/analyze \
  -H "Content-Type: application/json" \
  -d '{"clause_id":"UNKNOWN_001","policyClause":"We assess biodiversity impact near our supply chain facilities.","regions":["EU"]}' \
  | python3 -m json.tool
# Confidence should now be >= 0.55
```

---

## Phase 4 — US Seed + Bridge Layer

**Status:** Not started
**Objective:** Minimal US schema proves federation target. Bridge suggestions carry caveats and default to unverified.

### Files to implement

```
services/bridge-detector.js
services/federated-analyze.js
```

Fixtures already exist:
```
fixtures/us-sec-climate-seed.cypher   ← already seeded in Phase 1
fixtures/us-regulatory-chunks.json    ← already seeded in Phase 1
```

### Hard caps — do not exceed

```
US schema maximum:
  2 obligations
  2 regulatory chunks
  1 topic
  1 evidence type
```

### Bridge suggestion endpoint

```json
POST /api/regulatory-impact-demo/bridges/suggest

Response:
{
  "bridge_type": "semantic_equivalence",
  "from": { "jurisdiction": "EU_ESRS_E1", "node": "Topic:climate_risk_disclosure" },
  "to": { "jurisdiction": "US_SEC_CLIMATE", "node": "Topic:us_climate_risk" },
  "confidence": 0.72,
  "mapping_basis": "both_require_climate_governance_disclosure",
  "caveats": [
    "EU_requires_double_materiality",
    "US_focuses_on_investor_materiality_only"
  ],
  "reliability": "unverified"
}
```

Rules that must hold:
- Never return a bridge without a `caveats` array
- `reliability` always defaults to `"unverified"`
- `confidence` must be less than 1.0
- Federated confidence is capped at 0.70 while bridge is unverified

### Bridge approval endpoint

```json
POST /api/regulatory-impact-demo/bridges/approve
{ "bridge_id": "BRIDGE_EU_ESRS_CLIMATE_US_SEC_CLIMATE" }

→ Sets reliability: "verified" on the TRANSLATES_TO edge in Neo4j
→ Removes the 0.70 cap on federated confidence
```

### Federated analyze response (after bridge approval)

```json
POST /api/regulatory-impact-demo/analyze
{ "clause_id": "CLAUSE_001", "policyClause": "...", "include_federation": true }

Response includes:
{
  "local_result": { ... },
  "federated_context": [
    {
      "bridge_type": "semantic_equivalence",
      "target_jurisdiction": "US_SEC_CLIMATE",
      "target_topic": "us_climate_risk",
      "confidence": 0.72,
      "caveats": ["US does not require double materiality"],
      "impact_if_expanded_to_us": "aligned_with_caveats"
    }
  ],
  "federated_confidence": {
    "local_score": 0.78,
    "bridge_score": 0.72,
    "combined_score": 0.75,
    "bridge_reliability": "verified"
  }
}
```

### Phase 4 success conditions

```
[ ] /bridges/suggest returns at least one bridge with caveats
[ ] That bridge has confidence < 1.0
[ ] That bridge has reliability: "unverified"
[ ] /bridges/approve sets reliability: "verified" on the Neo4j edge
[ ] After approval, analyze with include_federation: true returns federated_context
[ ] Federated combined_score is between local_score and bridge_score
```

---

## Phase 5 — Frontend Wire-Up

**Status:** Not started
**Objective:** Three functional views that tell the Stage 1 → Stage 2 → Stage 3 narrative.

### Three views and what each one proves

```
View 1: DistrictView.jsx
  Narrative: "Start here. Perfect the local model."
  Shows: Clause input → analyze → impact matrix, confidence meter, graph path
  Key moment: Type "biodiversity" → red warning + dynamic candidates appear

View 2: BridgeBuilder.jsx
  Narrative: "Connect districts with typed bridges that carry legal nuance."
  Shows: EU nodes | US nodes | dotted bridge lines | caveat tooltips | approve button
  Key moment: Click Approve Bridge → dotted line becomes solid

View 3: FederationMap.jsx
  Narrative: "Scale toward universal coverage by adding districts and bridges."
  Shows: Both jurisdiction clusters | solid = local edges | dotted = approved bridges
  Key moment: Before/after confidence comparison showing bridge effect
```

### Rules for frontend work

```
Use only: shared/components/Button.jsx, Card.jsx, Badge.jsx
Use only: shared/api/client.js for API calls
Use only: Tailwind for styling
Do NOT install new component libraries
Do NOT add animations
Functional over beautiful — this is a technical demo, not a product pitch
```

### Phase 5 success conditions

```
[ ] All 3 tabs render without console errors
[ ] DistrictView runs a real analyze call and displays the result
[ ] Biodiversity input shows red warning with dynamic_candidates visible
[ ] BridgeBuilder shows at least one bridge with caveats on hover
[ ] FederationMap shows two distinct jurisdiction clusters
[ ] No hardcoded mock data — everything comes from real API calls
```

---

## Phase 6 — PolicyChange Diff + Export

**Status:** Not started
**Objective:** End-to-end narrative from current state to proposed state with a downloadable Markdown report.

### Diff endpoint

```json
POST /api/regulatory-impact-demo/analyze-change

{
  "policy_change_id": "PCH_001",
  "current_clauses": [
    { "clause_id": "CLAUSE_001", "text": "We disclose climate-related risks annually..." },
    { "clause_id": "CLAUSE_002", "text": "We report Scope 1 and Scope 2 emissions..." }
  ],
  "proposed_clauses": [
    { "clause_id": "PROPOSED_001", "text": "We disclose Scope 3 greenhouse gas emissions annually." },
    { "clause_id": "PROPOSED_002", "text": "Our board of directors oversees climate-related risks." }
  ],
  "regions": ["EU"]
}
```

### Four diff labels only — do not add more

```
improved       → status got better  (gap → partially_aligned, etc.)
regressed      → status got worse   (aligned → gap, etc.)
unchanged      → same status before and after
new_coverage   → topic only exists in proposed clauses
```

### Export report format — must match exactly

```markdown
# ESG Compliance Gap Analysis
## District: EU / ESRS E1
### Change: Add Scope 3 + Board Oversight

| Topic | Before | After | Change |
|-------|--------|-------|--------|
| Climate Risk Disclosure | Partially Aligned | Partially Aligned | Unchanged |
| Scope 3 Emissions | Not Covered | Partially Aligned | New Coverage |
| Board Oversight | Gap | Partially Aligned | Improved |

> **Federation Note:** This analysis covers EU/ESRS E1 only.
> Bridges to US_SEC_CLIMATE exist but are unverified.
> Cross-jurisdiction impact requires approved bridge edges.
```

### Phase 6 success conditions

```
[ ] PCH_001 diff returns Scope 3 as new_coverage
[ ] PCH_001 diff returns board oversight as improved
[ ] Export Markdown contains the Federation Note section
[ ] Export Markdown contains a table with at least 3 rows
```

---

## Phase 7 — Tests + Deployment

**Status:** Not started
**Objective:** 8 tests passing, live URL, README framed correctly.

### The 8 critical path tests

```
Test 1: /health returns 200 with module name and adapter status
Test 2: /analyze returns Qdrant matches with real citations for CLAUSE_001
Test 3: /analyze returns Neo4j graph context with resolved obligation paths
Test 4: "biodiversity" → confidence <= 0.40 and dynamic_candidates present
Test 5: /ontology/expand creates Topic with origin:dynamic → re-analyze resolves
Test 6: Lowest applicable cap wins when multiple caps trigger simultaneously
Test 7: Result IDs follow RESULT_{clause_id}_{region}_{framework}
Test 8: /bridges/suggest returns bridge with caveats and reliability:unverified
```

### Deployment targets

```
Backend:   Render or Railway (free tier)
Frontend:  Vercel (frontend/dist)
Neo4j:     AuraDB (free tier) — already running
Qdrant:    Qdrant Cloud (free tier) — already running
Embedder:  Deploy as a separate service alongside the backend
           OR run locally and point EMBEDDER_URL to it
```

### Environment variables required

```
NEO4J_URI=neo4j+s://d5fca390.databases.neo4j.io
NEO4J_USER=d5fca390
NEO4J_PASSWORD=<your password>
QDRANT_URL=https://<your-cluster>.qdrant.io:6333
QDRANT_API_KEY=<your key>
REGULATORY_IMPACT_EMBEDDER_URL=http://127.0.0.1:8000
```

### Deploy order — do not save deployment for last

```
1. Deploy Phase 0 scaffold to Render/Vercel immediately
2. Verify the pipeline works before adding any real code
3. Redeploy after every phase completion
4. If something breaks in deployment, fix it before moving to the next phase
```

### README must open with this framing

The README is the first thing the CTO reads. It must open with the technical thesis, not a feature list.

```markdown
# Regulatory Impact Demo

This module implements a district-first federated ontology for ESG regulatory
compliance analysis — built as a hiring demo for Scorealytics.

The demo proves three things that directly address the technical challenges
described in the hiring post:

1. **Vector + graph retrieval beats vectors alone.** Qdrant finds semantically
   similar regulatory chunks. Neo4j explains what those chunks mean inside the
   regulatory structure. The combination produces more accurate impact analysis
   than either alone.

2. **The static vocabulary wall is solvable.** When the system encounters an
   unknown term ("biodiversity", "greenwashing"), it caps confidence, surfaces
   dynamic candidates from Qdrant nearest neighbors, and waits for human
   approval rather than hallucinating. Approved terms become real Topic nodes
   in Neo4j without any schema migration or redeployment.

3. **Federation is the path to universal coverage.** Rather than collapsing all
   jurisdictions into one universal schema (which loses legal nuance), this
   system maintains separate schemas per jurisdiction and connects them via
   typed bridge edges that carry caveats. EU "climate risk disclosure" and US
   "climate risk disclosure" are not the same obligation — the bridge edge
   preserves that difference while still enabling cross-jurisdiction reasoning.
```

### Phase 7 success conditions

```
[ ] npm test passes all 8 tests
[ ] Live backend URL responds to /health in < 3 seconds
[ ] Live frontend URL loads all 3 tabs without errors
[ ] README opens with the federation framing paragraph above
[ ] You can walk through the demo live without notes
```

---

## Copilot / Kimi Code Prompt Template

Copy this exactly at the start of every phase prompt. Replace `[N]` and the objective.

```
You are working inside an EXISTING modular monolith called litigation-rag.
Do NOT create new repos. Do NOT install new npm packages without asking.
Do NOT modify any file outside regulatory-impact-demo/ except frontend/src/core/router.jsx.

SHARED DRIVER IMPORTS (use these exactly, do not deviate):
  Neo4j:  import { getGraphDriver }  from "../../shared/graph/index.js"
  Qdrant: import { getVectorDriver } from "../../shared/vector/index.js"
  Events: import { getEventBus }     from "../../shared/events/index.js"

EMBEDDER:
  Endpoint: POST {REGULATORY_IMPACT_EMBEDDER_URL}/embed
  Request:  { text: string }
  Response: { embedding: float[] }  (384 dimensions)
  Fallback: if HTTP fails, use deterministic placeholder vectors and log clearly

PHASE [N]: [objective]
[specific files to implement and success conditions]

STOP after this phase. Output "PHASE [N] COMPLETE" and the verification curl.
Wait for NEXT before continuing.
Do NOT touch files from previous phases unless I explicitly say "fix [filename]."
```

---

## Interview Questions to Prepare

When the demo is working, be ready to answer these without looking at the plan.

**On the architecture:**
- Why three databases instead of one?
- Why Qdrant over pgvector?
- Why Neo4j over a junction table approach in Postgres?
- What would break if you used a universal schema instead of federation?

**On the confidence scoring:**
- How is the confidence score computed?
- What causes a confidence cap to apply?
- How does the system know when it is uncertain?
- Why is confidence computed from evidence rather than from asking the LLM?

**On the living ontology:**
- What happens when the system encounters a term it has never seen?
- How does a new term get promoted into the ontology?
- How do you prevent bad promotions from corrupting the graph?
- How is this different from just adding the term to the keyword map?

**On federation:**
- Why not just build a universal schema?
- How do bridge edges carry legal nuance?
- What is the difference between EU and US climate risk disclosure obligations?
- How does federation scale toward global coverage?

**On the annotation loop:**
- How does human annotation improve future confidence scores?
- What is the difference between eval and finetune export eligibility?
- Why is eligibility derived from decision type rather than stored on the record?

---

## Current Phase Summary

| Phase | Status | What It Proves |
|---|---|---|
| Phase 0: Scaffold | ✅ Complete | Module loads inside existing monolith |
| Phase 1: EU + US seed | ✅ Complete | Real vectors in Qdrant, real graph in Neo4j |
| Phase 2: Local analyze | 🔨 Build next | Qdrant + Neo4j + confidence score working end-to-end |
| Phase 3: Living ontology | Pending | Unknown terms handled without hallucination |
| Phase 4: Bridge layer | Pending | Cross-jurisdiction federation with caveats |
| Phase 5: Frontend | Pending | Three-view narrative visible and interactive |
| Phase 6: Diff + export | Pending | Before/after comparison with downloadable report |
| Phase 7: Tests + deploy | Pending | 8 tests passing, live URL, README framed |
