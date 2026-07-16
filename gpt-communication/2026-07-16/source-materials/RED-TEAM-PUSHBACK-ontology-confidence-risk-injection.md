# RED-TEAM PUSHBACK: Adding Confidence Scoring, Risk Tradeoffs, and Context Injection to the Living Ontology

**Date:** 2026-07-16  
**Stance:** STRICT OPPOSITION. Every argument below is sourced to the corpus.  
**Verdict:** DO NOT ADD. These features are scope creep against the ontology's design intent, contradict two independent arbitration verdicts, attempt to monetize a retrieval surface that measurably failed, and re-introduce the exact circular-validation anti-pattern the project was built to prevent.

---

## 1. The ontology was designed as a VERSIONING system, not a decision-support system

The ontology's own schema declares its purpose unambiguously:

> "A self-maintaining structured knowledge graph that IS the current state of the project, replacing accreting markdown. It answers **'which doc/rubric/gap is CURRENT'** — the exact question that handoff-prose proliferation kept getting wrong."
> — `docs/ontology/schema.yaml:1-6`

> "It answers the one question prose kept getting wrong: **'which doc / rubric / gap is CURRENT?'**"
> — `docs/ontology/README.md:5-6`

The design contract is **version currency tracking** — bi-temporal invalidation, `supersedes`-chain resolution, `status` lifecycle. That is what was built, tested, and shipped. Confidence scoring, risk tradeoff modeling, and proactive context injection are **decision-support metadata** — an entirely different category of system. They answer "how sure are we?" and "what should we do?" and "what context should we inject?" — none of which the ontology was designed, tested, or validated to answer.

**This is textbook scope creep.** The ontology solved one problem well (currency tracking). Adding decision-level metadata asks it to solve three more problems it was never architected for, with no evidence it can handle them.

---

## 2. Both Codex AND Fable (July 13, independent arbitration) said the ontology should be a DERIVED PROJECTION, not the substrate

Two independent reviewers — GPT-5.6 Codex and Claude Fable — reached the same verdict on July 13, 2026, without coordinating:

**Codex** (`docs/design/durable-gap-tracking-codex-2026-07-13.md:3`):
> "Do not make the living ontology the source of truth for gaps. Build one small, typed, append-only gap ledger, link it to the existing task ledger, and derive phase and graph views from those two logs. **Ontology integration is optional and should earn its place with a multi-hop retrieval win.**"

**Codex** (`:77-81`):
> "Therefore ontology is **optional, derived, and one-way**: `gap ledger -> projector -> ontology gap entities/relations`. Never write gap state through the ontology. Never read operational status from it."

**Codex** (`:139`):
> "**Ontology is not required.** The first build is complete and useful without it. Promote the projector only if measured multi-hop demand and retrieval quality justify the added surface."

**Fable** (`docs/design/durable-gap-tracking-fable-2026-07-13.md:14-15`):
> "the living ontology is an **optional projection, not the substrate** — it is not required for v0 and should only be wired in when G2's own retrieval gate is won."

**Fable** (`:184-194`):
> "at 36-300 gaps, the graph value of gaps-as-entities is a BFS over an adjacency list — the ontology's marginal value for *this* use is a nicer query surface, which does not cover its recorded 6-8× cost while G2 is unresolved. The correct dependency direction is: **gap ledger (canonical) → ontology sync (derived projection)**."

**Fable** (`:421-422`):
> "**Ontology's role:** OPTIONAL derived projection (idempotent sync), gated on G2's measured retrieval win. NOT required for v0; explicitly not the substrate."

**The build note confirms consensus** (`docs/design/gap-ledger-v0-build-note-2026-07-14.md:61`):
> | Ontology as substrate | **No** — optional derived projection, gated on G2 retrieval win | **No** — optional, one-way, gated on measured multi-hop win | **Agreed. Not built.** Ontology stays unwired; gap ledger is canonical. |

Adding confidence/risk/injection to the ontology **makes it the substrate again**. If confidence scores live in the ontology, every consumer must read from the ontology to get confidence — the ontology becomes the source of truth for decision metadata, inverting the agreed dependency direction. This directly contradicts the only two independent design reviews the project has, which were explicitly sought to prevent exactly this kind of scope expansion.

---

## 3. The ontology's retrieval fusion was MEASURED and FAILED — adding more metadata to a system that can't prove retrieval value is adding cost without payoff

**The 2026-07-13 gate result** (`evals/reports/ontology_retrieval_gate.md:5-12`):
> "**PARKED — flag ships default OFF.** ... No clean, material multi-hop win. BM25 already scores nDCG≈0.95 on the multi-hop set (cross-referenced docs share vocabulary, so flat search already spans the citation graph); the ontology leg is a net wash — it rescues a couple of chunk hits but regresses doc-level nDCG/MRR on the same set and does nothing for single-hop natural-language queries. **Do not ship a 6–8× graph cost for a wash.**"

The measured numbers (`:30-42`):

| set | arm | nDCG@5 | chunk_recall@5 | chunk_mrr |
|-----|-----|-------:|---------------:|----------:|
| multi-hop | OFF | 0.950 | 0.800 | 0.679 |
| multi-hop | ON | 0.926 | 0.900 | 0.658 |
| **Δ** | | **−0.024** | **+0.100** | **−0.021** |

**The nDCG went NEGATIVE.** The recall gain was exactly one net query at the boundary. MRR regressed. This is a wash, not a win.

The 2026-07-15 re-measurement DID clear the threshold after the corpus grew (nDCG +0.020, recall +0.200) — but only barely, only on multi-hop, and only after BM25 recall degraded enough to create a gap for the ontology to fill. The single-hop queries remain an exact no-op (`retrieval.yaml:19-20`). The global default remains OFF.

**The point:** the ontology's ONE measured retrieval function barely clears its gate under the most favorable conditions. It has no proven track record of adding value to the system. Adding confidence scoring, risk tradeoffs, and context injection — three more layers of unmeasured metadata — to a system whose only measured output is a marginal retrieval gain is **building on sand**. You're adding complexity to a system that has not proven it can handle the complexity it already has.

---

## 4. Confidence scoring in a graph requires traversal to compute aggregates — which is expensive and unmeasured

The ontology currently has **no multi-hop query capability**. Per Fable's design review (`docs/design/durable-gap-tracking-fable-2026-07-13.md:170-173`):

> "**No generic multi-hop query.** `neighbors` is strictly one-hop (`ontology.py:515-541`); the only >1-hop walk is `current_version`, and it follows only `supersedes` (`ontology.py:544-574`). 'What transitively blocks X' would have to be written either way — and over a reduced flat ledger it is the same ~15-line BFS."

Confidence scoring at the entity level is useless; what matters is confidence along a path or across a subgraph. "How confident is this decision chain?" requires traversing from evidence → conclusion → decision, aggregating confidence at each hop. That means:

1. **Multi-hop traversal must be built first** — it doesn't exist today. The ontology has exactly one traversal operation (`neighbors`, one-hop) and one chain follower (`current_version`, `supersedes` only).
2. **Aggregate confidence computation is O(n) in path length per query** — with 91 relations across 153 entities, this is trivial today, but the cost grows with graph density and is **completely unmeasured**. There is no benchmark, no latency profile, no context-cost measurement for graph traversal.
3. **The recorded cost claim is 6-8×** (`docs/GAP-CLOSURE-PLAN.md:64`, cited by both Fable and Codex). That was for retrieval fusion alone. Adding confidence aggregation on top of traversal on top of retrieval is compounding unmeasured cost on unmeasured cost.

The project's own principle: **research before building, measure before shipping.** Confidence scoring in a graph is research that hasn't been done, on a substrate that hasn't been validated, for a use case that hasn't been measured. It violates every gate the project has.

---

## 5. Risk tradeoffs in a graph create a combinatorial explosion of edges

The ontology currently has 91 relations across 10 predicate types. The predicate distribution:

| predicate | count |
|-----------|------:|
| references | 36 |
| depends_on | 15 |
| covers | 15 |
| authored_by | 14 |
| calibrated_on | 6 |
| validated_by | 4 |
| supersedes | 1 |
| **implements** | **0** |
| **part_of** | **0** |

Two predicates (`implements`, `part_of`) are **defined in the schema but completely empty** — designed but never populated. This is the "empty predicates" signal: the graph already has designed-but-unused structure. Adding risk tradeoff edges means:

1. **Every decision-risk pair becomes an edge.** If entity A has a risk tradeoff against entity B, that's a new edge type. If it's directional (A trades off against B ≠ B trades off against A), that's two edges per pair.
2. **Risk tradeoffs are not binary — they're weighted, contextual, and temporal.** "A trades off against B by 0.3 under condition C" means each edge carries a weight, a condition, and a validity window. The schema currently has no mechanism for edge attributes beyond `status`, `invalid_from`, `summary`, and `source_paths`.
3. **The combinatorial explosion:** with 153 entities, a fully connected risk graph has up to 153×152/2 = 11,628 potential tradeoff pairs. Even at 1% density, that's ~116 new edges — more than the current total edge count. Each needs provenance, validation, bi-temporal tracking, and maintenance.
4. **No query exists that would consume these edges.** There is no "show me the risk tradeoffs for this decision" query in the ontology's CLI, MCP, or API. You'd be adding edges that nothing reads.

This is **premature optimization of a non-problem.** Nobody has asked "what are the risk tradeoffs between these ontology entities?" — because the ontology doesn't model decisions, it models document currency.

---

## 6. The cyclical trap: adding features to the ontology to make it useful requires the ontology to already be useful, which it isn't

The ontology's proven value is narrow and measured:
- **Currency tracking** ("which doc is current") — works, tested, shipped.
- **Retrieval fusion** — measured, barely cleared its gate on 2026-07-15 after initially failing on 2026-07-13. Single-hop: exact no-op. Multi-hop: marginal gain.
- **Cross-type queries** (gap→module, rubric→domain) — designed, schema exists, **no multi-hop query engine built**.
- **`implements`/`part_of` predicates** — defined, **zero relations populated**.

The proposed additions (confidence, risk, injection) presuppose that the ontology is already a functioning decision-support substrate. It is not. It is a versioning system with a marginal retrieval gain. Adding decision-level features to make it "more useful" creates a dependency cycle:

- Confidence scoring requires the ontology to be the source of truth for confidence → but the ontology is a derived projection, not the substrate.
- Risk tradeoffs require the ontology to model decisions → but the ontology models document currency, not decisions.
- Context injection requires the ontology to know what context is relevant → but the ontology's retrieval fusion is a marginal win on multi-hop queries only, and a no-op on single-hop.

**You cannot add features to make a system useful if the features themselves require the system to already be useful.** This is the bootstrapping paradox. The ontology needs to prove its value with what it has before adding more surface area.

---

## 7. The flat ledger approach is simpler, proven at this scale, and doesn't require the ontology at all

The gap ledger (`cortex_core/gap_ledger.py`) was designed July 13, built July 14, and is the **canonical store** for gap/phase tracking. Both Codex and Fable independently concluded it should be the substrate, with the ontology as an optional derived projection.

**Codex** (`docs/design/durable-gap-tracking-codex-2026-07-13.md:67-75`):
> "The ontology already uses almost the same physical model: append-only entity/relation JSONL reduced by last id... That similarity is an argument for a shared storage helper, not for making ontology authoritative. What a graph adds beyond a ledger is real but narrow... A flat ledger already answers the operational questions with adjacency lists and a 30-line DFS/topological sort. For dozens or hundreds of gaps, `blocks`/`blocked_by`, `phase`, `task_ids`, and `result_refs` are a sufficient small graph view."

**Fable** (`docs/design/durable-gap-tracking-fable-2026-07-13.md:322-332`):
> "Graph stores earn their cost only when variable-depth traversal dominates at large scale; 'you don't need a graph database to follow a hierarchy of nodes' — recursive CTE / in-memory BFS over an adjacency list is fully sufficient below ~10^5 nodes (benchmark cliff cited at 335K nodes). Jira itself ships its blocks-graph as a flat edge table."

The gap ledger already has:
- `blocks`/`blocked_by` — dependency edges (flat adjacency lists)
- `closes_metric` — closure conditions (machine-checkable)
- `evidence[]` — provenance with `file:line` resolution
- `verified` flag — deterministic verification, not LLM judgment
- `effective_status` — derived at read time (blocked-ness computed from blockers)
- `phase_rollup()` — phase status derived from gap statuses

**Confidence, risk, and context injection can all live in the flat ledger** as typed fields on gap records — or better, in a sibling ledger designed for decision metadata — without requiring the ontology at all. The ledger pattern is proven (task_ledger, gap_ledger both work), is simpler, and doesn't carry the 6-8× cost penalty of graph operations.

---

## 8. The user's own principle: "builder never authors the only checks" — if Claude builds confidence scoring into the ontology, Claude is also the one who validates against it

The project's production reference model states explicitly (`docs/harness/PRODUCTION-REFERENCE-MODEL.md:65`):

> "The builder may explain its result but may never be the only authority that marks it successful."

The anti-circularity doctrine is woven throughout the codebase:
- `arbitration_rigor.py:191` — "the anti-circular carrier"
- `arbitrate.py:377` — "Juror selection (cross-family, anti-circular, no Prometheus)"
- `bakeoff_authoring.py:7` — "the anti-circularity core"
- `bakeoff.py:71` — "pass/fail, never a judge (the anti-circularity core)"
- `docs/EVAL-DESIGN-PHASE2.md:54` — "a *second* defense against circular validation"

**If Claude designs and builds confidence scoring in the ontology, Claude is the builder. If Claude then validates that the confidence scoring works, Claude is the reviewer. Builder = reviewer is the exact anti-pattern the project was built to prevent.**

Fable's own design self-identified this risk (`docs/design/durable-gap-tracking-fable-2026-07-13.md:394-396`):
> "Ledger-vs-ontology verdict may be self-serving. 'Build the simple thing I can finish tonight' is a known agent bias. The check is pre-registered and objective: G2's own gate (measured multi-hop retrieval win) decides ontology wiring — not this document's author."

Adding confidence scoring, risk tradeoffs, and context injection to the ontology is **not** a measured retrieval win. It is a Claude-authored feature, proposed by a Claude-lineage agent, that would be validated by... what? There is no independent oracle for "confidence score quality." There is no frozen gold set for "risk tradeoff correctness." There is no gate that clears. The proposal asks us to trust the builder's judgment that the builder's feature is worth building.

---

## 9-14. Additional arguments

### 9. The ontology already has empty predicates — adding more empty structure is the failure pattern the project diagnosed

The `implements` and `part_of` predicates are defined in `schema.yaml:92-99` but have **zero populated relations** (verified: grep of `relations.jsonl` returns 0 matches for either). This is the "designed but never populated" pattern. The project's own fact-check (`docs/HARNESS-SCORECARD-CONSOLIDATED.md`) diagnosed structured registries drifting because their write paths were manual. Adding confidence/risk/injection predicates to the schema creates **more empty structure that will drift** unless the write path is automated — and no automation is proposed.

### 10. Context injection is a retrieval problem, and the ontology's retrieval is a marginal win

"Proactive context injection" means: given a task, decide what ontology context to inject into the agent's context window. This is a **retrieval** problem — and the ontology's retrieval fusion was measured as a wash on 2026-07-13 and a marginal win on 2026-07-15. The single-hop queries (the ones context injection would actually serve, since most tasks don't name ontology entities) are an exact no-op. You cannot build context injection on a retrieval surface that doesn't retrieve.

### 11. The ontology's `.jsonl`/`.yaml` files are not indexed by corpus search — adding decision metadata creates a shadow system

Per `schema.yaml:18-20`:
> "Neither .jsonl nor .yaml is picked up by the corpus search index (it scans *.md only), so this structured data never poisons BM25 retrieval — it is queried through the ontology tools, not full-text search."

This means ontology metadata lives in a **parallel information space** that agents must explicitly query through `cortex_ontology_query`. Adding confidence/risk/injection creates a shadow decision-support system that agents must know to query, know how to query, and trust the results of — with no evidence that agents currently do or will.

### 12. The project's stated diseases (A and B) are directly violated

**Disease A (context bloat):** Adding confidence/risk/injection metadata to the ontology increases the surface area agents must load. The measured cost of ontology fusion is 6-8× (`docs/GAP-CLOSURE-PLAN.md:64`). Adding decision metadata on top compounds this.

**Disease B (governance ritual):** If confidence scores and risk tradeoffs become ontology entities, they become part of the "structured ground truth" that CI validates. This creates new mandatory maintenance — confidence scores must be kept current, risk tradeoffs must be reviewed — which is exactly the kind of ceremony the project was designed to eliminate.

### 13. No consumer exists for this data

Nobody has asked "what is the confidence score of this ontology entity?" Nobody has asked "what are the risk tradeoffs between these two gaps?" Nobody has asked "what context should the ontology inject for this task?" The ontology's consumers are:
- `cortex-ontology` CLI (currency queries, neighbor lookups)
- `cortex_ontology_query` MCP tool (same, for agents)
- `search.py` ontology leg (retrieval fusion, marginal)

None of these consumers have a code path that would use confidence, risk, or injection metadata. You'd be adding data that nothing reads.

### 14. The flat ledger already has `verified` and `evidence[]` — confidence is already modeled, more honestly

The gap ledger already has a confidence-like field: `verified` (false by default, flipped only by deterministic check or human action) and `evidence[]` (list of `{path, line, kind}` with `file:line` resolution). This is a **more honest** confidence model than a numeric score: it says "this is verified by a deterministic check" or "this is not verified" — binary, auditable, and not subject to LLM judgment inflation. A numeric confidence score would be an LLM-authored number with no oracle — exactly what the project's oracle policy forbids.

---

## Summary: the case against

| # | Argument | Evidence |
|---|----------|----------|
| 1 | Scope creep: ontology is a versioning system, not decision-support | `schema.yaml:1-6`, `README.md:5-6` |
| 2 | Both independent reviewers said: derived projection, not substrate | Codex `:3,77-81,139`; Fable `:14-15,184-194,421-422`; build note `:61` |
| 3 | Retrieval fusion measured and failed (nDCG −0.024, wash) | `ontology_retrieval_gate.md:5-12,30-42` |
| 4 | Graph traversal for confidence aggregates is expensive and unmeasured | Fable `:170-173`; no multi-hop query exists; 6-8× cost recorded |
| 5 | Risk tradeoffs = combinatorial edge explosion (153 entities → 11K+ pairs) | `schema.yaml:92-99`; 0 `implements`/`part_of` relations populated |
| 6 | Cyclical trap: features require the system to already be useful | Ontology has no proven decision-support value; single-hop retrieval = no-op |
| 7 | Flat ledger is simpler, proven, doesn't need the ontology | Codex `:67-75`; Fable `:322-332`; gap_ledger.py built and tested |
| 8 | Builder=reviewer anti-pattern (circular validation) | `PRODUCTION-REFERENCE-MODEL.md:65`; anti-circularity throughout codebase |
| 9 | Empty predicates already exist (`implements`, `part_of` = 0 relations) | Verified: 0 matches in `relations.jsonl` |
| 10 | Context injection is retrieval; ontology retrieval is marginal/no-op on single-hop | `retrieval.yaml:19-20`; `ontology_retrieval_gate.md:62-66` |
| 11 | `.jsonl`/`.yaml` not indexed → shadow system agents must explicitly query | `schema.yaml:18-20` |
| 12 | Violates Disease A (bloat) and Disease B (ritual) | 6-8× cost; new mandatory maintenance |
| 13 | No consumer exists for confidence/risk/injection data | CLI, MCP, and search.py have no code paths for this |
| 14 | Gap ledger already has `verified` + `evidence[]` — more honest confidence model | `gap_ledger.py` schema; oracle policy forbids LLM judgment in verdict paths |

---

## The alternative that respects all constraints

If confidence, risk, and context-injection are genuinely needed:

1. **Build them in a flat ledger** (sibling of `gap_ledger.py`), not the ontology. The ledger pattern is proven, simpler, and doesn't carry graph-traversal cost.
2. **Gate on a measured retrieval win first.** The ontology's own G2 gate says "fuse and prove, or park." Adding decision metadata without proving retrieval value violates this gate.
3. **Get an independent reviewer.** If Claude builds it, Claude cannot be the one who validates it. Use Codex or a cross-family model for the review, per the anti-circularity doctrine.
4. **Start with the consumer.** Build the query that would use confidence/risk/injection BEFORE adding the data. If no consumer can be specified, the data is premature.

**Until these conditions are met: DO NOT ADD.**
