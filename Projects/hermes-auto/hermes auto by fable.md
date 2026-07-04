# Hermes Research-Harness Review: Gap Analysis, Knowledge-Source Repository, and Action Plan

## TL;DR
- The three Hermes documents are strong on the *evolution loop* (MAPE-K, observability, gauntlet, rollout) but thin on the *research/knowledge layer* the user actually wants to upgrade: agent memory architecture, ontology engineering practice, deep-research agent design, claim verification, and retrieval evaluation are the biggest gaps. The four cited "2026" harness papers (Agentic Harness Engineering / AHE, HarnessFix, Self-Harness, AIDev) are all **REAL and verified** on arXiv.
- Most of the heavyweight infra in the blueprints (Kubernetes Argo Rollouts, LaunchDarkly SaaS, Temporal clusters, Neo4j, Postgres+Timescale) is unrealistic and unnecessary for a solo Windows dev; a SQLite-first stack (FTS5 + sqlite-vec hybrid RRF, Litestream backups, OpenFeature/Flagsmith flags, git-worktree shadow deploys, APScheduler/Prefect + Task Scheduler) delivers the same capability at a fraction of the ops burden.
- The core deliverable is a curated, link-verified knowledge-source catalog (Part 2) plus a phased, Windows-realistic build plan (Part 3) that turns Cortex's inbox→reviewed→accepted pipeline into a production deep-research harness feeding the living ontology.

---

## PART 1 — GAP ANALYSIS

### 1a. The research/planning layer (the user's priority)

**What the corpus already has:** Document 3 covers literature ingestion (Grobid, OpenCitations), vector/structured storage comparison, RAG frameworks, claim generation with validation contracts, NLI-based contradiction detection, and provenance via DVC/MLflow/W&B. Cortex itself has the inbox→reviewed→accepted pipeline, FTS5 hybrid search, audit logs, and a living ontology. This is a solid skeleton.

**What is MISSING or under-specified:**

1. **Agent memory architecture as a first-class concern.** None of the documents treat memory consolidation, forgetting policies, or episodic/semantic/procedural memory scoping as an explicit design axis. This is the single biggest gap given the user's goal. The field has consolidated around three memory scopes (episodic, semantic, procedural) and mature frameworks — MemGPT/Letta (OS-style tiered memory), mem0 (hybrid vector+graph+KV), and Zep/Graphiti (temporal knowledge graph with fact-validity windows). Zep's Graphiti is directly relevant to a "living ontology" because it stores *validity intervals* on facts rather than snapshots — exactly the substrate needed for knowledge decay/freshness tracking, which the corpus mentions but does not operationalize. Zep reports **94.8% vs MemGPT's 93.4% on the Deep Memory Retrieval benchmark** (arXiv:2501.13956: "In the DMR benchmark, which the MemGPT team established as their primary evaluation metric, Zep demonstrates superior performance (94.8% vs 93.4%)"), plus up to an 18.5% accuracy gain on LongMemEval at ~90% lower latency.

2. **Ontology engineering as a discipline.** The corpus says "living ontology" but has no reference to SKOS, OWL, competency questions, or ontology-evolution methodology. Competency questions are the standard mechanism for deciding *what an ontology must answer* — which is precisely the "what to research next" and "missing ontology coverage" signal the user wants.

3. **Deep-research agent architecture patterns.** The corpus does not reference the now-canonical designs: Anthropic's orchestrator-worker multi-agent research system (lead researcher + parallel subagents + a separate CitationAgent), OpenAI Deep Research, GPT Researcher (plan-and-solve recursive tree exploration), or Stanford STORM (perspective-driven question generation + knowledge curation). Anthropic's setup — a Claude Opus 4 lead directing Claude Sonnet 4 subagents — **outperformed single-agent Claude Opus 4 by 90.2% on their internal research eval**, while consuming roughly 15× the tokens of a normal chat (token usage alone explains ~80% of BrowseComp performance variance). STORM is especially relevant because its whole point is knowledge curation with citations, and it introduced the FreshWiki dataset for freshness.

4. **Retrieval/RAG evaluation is named but not operationalized.** Document 3 lists frameworks; it does not commit to RAGAS (reference-free faithfulness/context-precision/recall), ARES (fine-tuned judges + prediction-powered inference), or the retrieval benchmarks (BEIR, MTEB) needed to actually measure whether the harness's retrieval is good. Without this, "evaluation of the research harness itself" (Part 3 item 5) has no metrics.

5. **Claim verification grounded in real datasets.** NLI contradiction detection is mentioned generically. The corpus does not anchor to SciFact, FEVER/FEVEROUS, or AVeriTeC — the standard SUPPORT/REFUTE/NEI datasets and label schemes that a claim-verification module should mirror. AVeriTeC's four-way scheme (Supported/Refuted/NotEnoughEvidence/ConflictingEvidence-Cherrypicking) is a better model for epistemic-confidence scoring than a binary contradiction flag.

6. **Scholarly-source APIs and credibility triangulation.** No mention of OpenAlex, Semantic Scholar Graph API, Crossref, arXiv API, or Unpaywall — the free, key-optional APIs that make automated citation verification and source-credibility scoring possible. OpenAlex alone now indexes **over 474 million scholarly works** (as of early Feb 2026, following the Nov 2025 "Walden" rewrite), adding roughly **50,000 new works daily** from Crossref, PubMed, DataCite and HAL, with a free API allowing up to 100,000 requests/day. Triangulation across ≥2 of these is the concrete mechanism for the "source credibility scoring" gap.

7. **Knowledge-graph construction from documents.** The "living ontology upgrade" has no reference to GraphRAG (Microsoft), LightRAG (dual-level, incremental updates, runs on a 30B open model), or nano-graphrag (the hackable implementation most third-party benchmarks actually use). LightRAG's incremental-update design is the key differentiator vs. Microsoft GraphRAG's expensive full re-index — critical for a living, continuously-updated ontology.

8. **Freshness/decay is described but not scheduled.** The corpus mentions freshness tracking but gives no re-verification schedule, decay function, or deprecation trigger. Temporal KGs (Graphiti) and FreshQA/FreshWiki-style freshness benchmarks are the missing pieces.

### 1b. The full evolution loop — what the documents miss, conflict on, or over-engineer

**Redundancy/conflict between the documents.** Documents 1 and 2 are near-duplicates on MAPE-K, observability, gauntlet, and progressive delivery, but they *conflict on the evidence store*: Doc 1 recommends PostgreSQL+pgvector as the v1 store (SQLite only as a prototype, Neo4j deferred), while Doc 2 proposes a staged SQLite WAL → Postgres+Timescale → Apache AGE/Neo4j graph progression. For a solo Windows dev this conflict should be resolved decisively in favor of **SQLite-first, indefinitely** — the "upgrade to Postgres" step is premature optimization that neither the data volume nor the single-node deployment justifies.

**What is unrealistic for a solo Windows dev (defer or substitute):**

- **Argo Rollouts / Flagger / Kayenta** — all require Kubernetes. Massive overkill. Substitute: **git-worktree-based shadow deployments** + a **process manager (NSSM to run Python as a Windows service)** + OpenFeature-spec flags backed by a self-hosted **Flagsmith** or **Unleash** (or even a SQLite flag table). Blue-green on one box = two worktrees + a junction/symlink swap.
- **LaunchDarkly** — SaaS with per-seat cost. Substitute: OpenFeature + self-hosted Flagsmith/Unleash/Flipt (Flipt is a single binary, no DB).
- **Temporal cluster** — durable execution is valuable but a Temporal *cluster* is heavy ops. Substitute for solo scale: **Prefect** (Python-native, minimal setup, runs locally on Windows), **APScheduler** (in-process cron replacement), or Windows **Task Scheduler** driving Python entrypoints. Keep the *concept* of durable, resumable workflows; drop the cluster.
- **Neo4j / Apache AGE / Timescale / pgvector** — defer all. sqlite-vec now runs "anywhere SQLite runs (Linux/MacOS/Windows)" and pairs with FTS5 BM25 for hybrid retrieval via reciprocal rank fusion. A graph layer, if ever needed, can be an edge table in SQLite or LightRAG's built-in store before any dedicated graph DB.
- **k6 / Toxiproxy / heavy gauntlet** — keep pytest and Promptfoo/DeepEval; treat load/chaos tooling as optional for a single-user research harness.

**What the evolution-loop documents get right and should keep:** MAPE-K as the framing (verified back to Kephart & Chess and the IBM blueprint), OpenTelemetry GenAI semantic conventions + OpenInference as the observability spine (the user is already adding OTel — good), the subtractor/archive-first retirement idea, and the phased instrument→generate→gauntlet→guarded-rollout structure. The AHE, HarnessFix, and Self-Harness papers (all real) provide the *observability-driven, trace-grounded, reversible-edit* discipline that should govern how Hermes changes its own harness.

### 1c. Verification of the cited 2026 papers (honesty check)

All four are REAL and verified on arXiv (current date July 2026):
- **Agentic Harness Engineering (AHE)** — arXiv 2604.25850, "Observability-Driven Automatic Evolution of Coding-Agent Harnesses." Real; has official code.
- **HarnessFix** — arXiv 2606.06324, "From Failed Trajectories to Reliable LLM Agents: Diagnosing and Repairing Harness Flaws." Real.
- **Self-Harness** — arXiv 2606.09498, "Harnesses That Improve Themselves." Real; three-stage Weakness Mining → Harness Proposal → Proposal Validation loop.
- **AIDev** — arXiv 2602.09185, "AIDev: Studying AI Coding Agents on GitHub," 932,791 agent-authored PRs across 116,211 repos. Real; a Zenodo record exists.

No fabrication needed — the user's citations check out. (Adjacent real work worth noting: arXiv 2606.01770 "Adaptive Auto-Harness," and the survey arXiv 2605.18747 "Code as Agent Harness.")

---

## PART 2 — KNOWLEDGE-SOURCE REPOSITORY

Priority key: **P0** = read first / foundational; **P1** = high value, read in month 1; **P2** = reference as needed. "Status" marks whether the source is already covered by the user's corpus (**Covered**) or a **NEW** addition filling a Part-1 gap.

### Deep-research agent architectures

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Anthropic — How we built our multi-agent research system | https://www.anthropic.com/engineering/multi-agent-research-system | Orchestrator-worker pattern: lead researcher plans + writes plan to memory, spawns parallel subagents, separate CitationAgent attributes every claim. Setup beat single-agent Opus 4 by 90.2% on internal eval at ~15× token cost. Token-budgeting discipline. | P0 | NEW |
| Anthropic — Building effective agents | https://www.anthropic.com/engineering/building-effective-agents | Canonical agent design patterns (prompt chaining, routing, orchestrator-workers, evaluator-optimizer). Baseline vocabulary for Hermes control graph. | P0 | NEW |
| OpenAI — Introducing deep research | https://openai.com/index/introducing-deep-research/ | Product-level model of an autonomous multi-step research agent; framing for plan→search→synthesize with citations. | P1 | NEW |
| GPT Researcher | https://github.com/assafelovic/gpt-researcher | Plan-and-solve + recursive "deep research" tree (configurable depth/breadth); parallelized agents; local + web research; report with citations. Directly portable planning loop. | P0 | NEW |
| Stanford STORM | https://github.com/stanford-oval/storm | Perspective-driven question generation, multi-turn conversation simulation, knowledge curation to cited article. FreshWiki dataset. Co-STORM adds human-in-the-loop. | P0 | NEW |
| STORM paper (NAACL 2024) | https://arxiv.org/abs/2402.14207 | Method detail behind the repo; how to generate a research outline from multi-perspective questioning. | P1 | NEW |
| LangChain open_deep_research | https://github.com/langchain-ai/open_deep_research | LangGraph implementation of a deep-research agent; control-graph reference with HITL checkpoints. | P1 | NEW |
| HuggingFace open deep research (smolagents) | https://huggingface.co/blog/open-deep-research | Open reproduction reaching **55.15% pass@1 on the GAIA validation set** (vs ~67% for OpenAI Deep Research; up from ~46% for Magentic-One); code-agent approach to tool use. Repo: https://github.com/huggingface/smolagents/tree/main/examples/open_deep_research | P1 | NEW |

### Agent memory & knowledge management

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| CoALA — Cognitive Architectures for Language Agents | https://arxiv.org/abs/2309.02427 | The organizing framework: working vs long-term memory (episodic/semantic/procedural), internal vs external actions, decision loop. Use as the master schema for Hermes memory tables. | P0 | NEW |
| MemGPT / Letta paper | https://arxiv.org/abs/2310.08560 | OS-style tiered memory (main context = RAM, archival = disk); agent self-manages paging via memory tools. Model for Cortex's context-management layer. | P1 | NEW |
| Letta repo | https://github.com/letta-ai/letta | Reference implementation + REST API; self-editing memory blocks. | P2 | NEW |
| mem0 | https://github.com/mem0ai/mem0 | Hybrid vector+graph+KV memory with automatic fact extraction; pragmatic personalization layer. | P1 | NEW |
| Zep: Temporal KG Architecture for Agent Memory | https://arxiv.org/abs/2501.13956 | Temporal knowledge graph storing fact-validity windows — the key idea for a *living* ontology with decay/freshness. 94.8% vs MemGPT 93.4% on DMR. | P0 | NEW |
| Graphiti repo | https://github.com/getzep/graphiti | Non-lossy incremental KG updates with validity intervals; directly implements knowledge-decay tracking. | P1 | NEW |
| LlamaIndex | https://github.com/run-llama/llama_index | Data framework: knowledge-graph index, ingestion pipelines, query engines. Reference patterns even if not adopted wholesale. | P2 | Covered (partial) |

### Knowledge graphs & ontology engineering

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Microsoft GraphRAG repo | https://github.com/microsoft/graphrag | Entity/relationship extraction → community detection → community summaries → global vs local query. The reference "documents → knowledge graph" pipeline. | P0 | NEW |
| GraphRAG paper | https://arxiv.org/abs/2404.16130 | Method behind the repo (query-focused summarization over graph communities). | P1 | NEW |
| LightRAG | https://github.com/hkuds/lightrag | Dual-level retrieval, **incremental graph updates without full re-index**, runs on 30B open model. Best fit for a continuously-updated living ontology. | P0 | NEW |
| nano-graphrag | https://github.com/gusye1234/nano-graphrag | Minimal, hackable GraphRAG (~1k LoC); the implementation most third-party benchmarks actually use. Start here to learn the mechanics. | P1 | NEW |
| W3C SKOS Primer | https://www.w3.org/TR/skos-primer/ | Concept schemes, broader/narrower/related, prefLabel/altLabel — lightweight vocabulary model ideal for a solo-dev living ontology. | P0 | NEW |
| W3C OWL 2 Primer | https://www.w3.org/TR/owl2-primer/ | Classes, properties, restrictions — adopt selectively only if SKOS proves too weak. | P2 | NEW |
| Competency Questions for ontologies (Keet & Khan) | https://arxiv.org/abs/2412.13688 | Types of competency questions; use CQs as the concrete "what must the ontology answer" test set that drives gap-driven research. | P1 | NEW |
| Ontology evolution: a process-centric survey | https://oro.open.ac.uk/39267/ | Formal treatment of ontology change/versioning; deprecation and re-verification triggers. | P1 | NEW |
| schema.org | https://schema.org | Practical, battle-tested vocabulary conventions to borrow for entity typing. | P2 | NEW |

### Retrieval & RAG evaluation

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| sqlite-vec | https://github.com/asg017/sqlite-vec | Pure-C vector search extension, runs on Windows; vec0 virtual tables, KNN, binary quantization. The vector half of Cortex hybrid search. | P0 | NEW |
| Simon Willison — sqlite-vec + embeddings TIL | https://til.simonwillison.net/sqlite/sqlite-vec | Concrete SQL for vec_distance_cosine, vec0 index creation, storing embeddings. Copy-paste starting point. | P0 | NEW |
| RAGAS | https://github.com/explodinggradients/ragas | Reference-free metrics: faithfulness, answer relevance, context precision/recall; synthetic test-set generation. Core eval for the harness. | P0 | NEW |
| ARES paper | https://arxiv.org/abs/2311.09476 | Fine-tuned lightweight judges + prediction-powered inference; ~150 annotations for calibration. Statistically rigorous eval when you have a small golden set. | P1 | NEW |
| BEIR | https://arxiv.org/abs/2104.08663 | Heterogeneous zero-shot IR benchmark; retrieval metrics (nDCG@k, recall@k) methodology to adopt. | P1 | NEW |
| MTEB | https://arxiv.org/abs/2210.07316 | Embedding-model selection; leaderboard at https://huggingface.co/spaces/mteb/leaderboard to pick a Windows-friendly local embedder. | P1 | NEW |
| RAPTOR | https://arxiv.org/abs/2401.18059 | Recursive summarization tree for multi-level retrieval; good for long-document ingestion into the corpus. | P2 | NEW |
| HyDE | https://arxiv.org/abs/2212.10496 | Hypothetical-document embeddings for zero-shot dense retrieval; cheap recall boost. | P2 | NEW |
| ColBERTv2 | https://arxiv.org/abs/2112.01488 | Late-interaction retrieval; reference for reranking quality ceiling (PLAID engine: arXiv:2205.09707). | P2 | NEW |
| SQLite FTS5 docs | https://www.sqlite.org/fts5.html | BM25 ranking, tokenizers; the keyword half of hybrid RRF. | P0 | Covered |

### Claim verification & scholarly APIs

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| SciFact | https://arxiv.org/abs/2004.14974 | Scientific claim verification with SUPPORT/CONTRADICT/NEI + rationale sentences. Model for claim-evidence records. | P1 | NEW |
| FEVER | https://arxiv.org/abs/1803.05355 | Large-scale fact extraction/verification; the canonical SUPPORTS/REFUTES/NEI scheme. | P1 | NEW |
| AVeriTeC | https://arxiv.org/abs/2305.13117 | Real-world claim verification with QA-decomposed evidence + four-way verdict (incl. ConflictingEvidence/Cherrypicking). Best model for epistemic-confidence scoring. | P0 | NEW |
| OpenAlex API | https://developers.openalex.org/ | Free, no-key scholarly graph (474M+ works, ~50k added daily; 100k req/day). Citation counts, concepts, OA links. Primary source for citation verification/triangulation. | P0 | NEW |
| Semantic Scholar Graph API | https://api.semanticscholar.org/api-docs/ | Abstracts, embeddings, recommendations, citation edges. Second triangulation source. | P0 | NEW |
| Crossref REST API | https://api.crossref.org/ | DOI resolution + metadata; authoritative publication records. | P1 | NEW |
| arXiv API | https://info.arxiv.org/help/api/index.html | Preprint metadata + new-submission feeds for opportunity-driven alerts. | P1 | NEW |
| Unpaywall | https://unpaywall.org/products/api | Legal open-access full-text links to enrich fetched sources. | P2 | NEW |

### Research-ingestion tooling

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Docling (IBM) | https://github.com/docling-project/docling | PDF/DOCX/PPTX/HTML → structured DoclingDocument preserving hierarchy; LangChain/LlamaIndex integration. Best structured-parse for the corpus. | P0 | NEW |
| MinerU | https://github.com/opendatalab/mineru | High-accuracy PDF→Markdown/JSON, VLM+OCR, strong on complex layouts. | P1 | NEW |
| marker | https://github.com/datalab-to/marker | Fast local PDF→Markdown with optional `--use_llm` accuracy boost. | P1 | NEW |
| pymupdf4llm | https://github.com/pymupdf/RAG | Lightweight, fast text+Markdown extraction for simple PDFs; low dependency. | P1 | NEW |
| Grobid | https://github.com/kermitt2/grobid | Bibliographic/citation extraction from scholarly PDFs (TEI XML). Feeds citation-verification module. | P1 | Covered |
| Firecrawl / Tavily / Jina Reader | https://github.com/mendableai/firecrawl | Web-to-markdown/search APIs for the fetch stage (already in corpus — extract clean-content + rate-limit patterns). | P2 | Covered |

### Agent evaluation & harness research

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Agentic Harness Engineering (AHE) | https://arxiv.org/abs/2604.25850 | Three observability pillars (component/experience/decision); every edit = falsifiable prediction verified next round. Governance model for Hermes self-modification. | P0 | Covered (cited) |
| HarnessFix | https://arxiv.org/abs/2606.06324 | Trace-grounded failure diagnosis → scoped, validated harness repair (HTIR). How to attribute failures to harness layers. | P1 | Covered (cited) |
| Self-Harness | https://arxiv.org/abs/2606.09498 | Weakness Mining → Harness Proposal → Proposal Validation; minimal reversible changes. | P1 | Covered (cited) |
| AIDev | https://arxiv.org/abs/2602.09185 | 932k agent-authored PRs; empirical priors on agent failure/acceptance rates for the gauntlet. | P2 | Covered (cited) |
| SWE-bench | https://arxiv.org/abs/2310.06770 | Real-world GitHub-issue resolution benchmark; structure for coding-task golden set. | P1 | Covered |
| SWE-bench Verified | https://openai.com/index/introducing-swe-bench-verified/ | 500 human-validated tasks; cleaner eval subset. (Note: OpenAI announced Sept 2025 it no longer evaluates on it — still useful as a dataset.) | P2 | NEW |
| GAIA | https://arxiv.org/abs/2311.12983 | General-assistant benchmark; multi-step tool-use tasks — model for research-task golden set. | P1 | NEW |
| τ-bench (tau-bench) | https://arxiv.org/abs/2406.12045 | Tool-agent-user interaction eval; repo https://github.com/sierra-research/tau-bench (successor tau2-bench). | P2 | NEW |
| OSWorld | https://arxiv.org/abs/2404.07972 | Real-computer multimodal agent tasks. | P2 | NEW |
| WebArena | https://arxiv.org/abs/2307.13854 | Realistic web-agent environment. | P2 | NEW |
| AgentBench | https://arxiv.org/abs/2308.03688 | Multi-environment LLM-as-agent eval. | P2 | NEW |
| OpenAI evals | https://github.com/openai/evals | Eval framework + benchmark registry; harness for regression gates. | P1 | NEW |

### Observability for agents

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| OTel GenAI semantic conventions | https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/ | gen_ai.* span/attribute schema for LLM calls, tool calls, retrieval, agent spans; OTEL_SEMCONV_STABILITY_OPT_IN. Standardize Cortex's new OTel here. | P0 | Covered |
| OpenInference spec | https://github.com/Arize-ai/openinference | LLM-specific conventions: full prompt/completion capture, first-class retrieval spans, broad auto-instrumentation. Better default for RAG-heavy debugging. | P0 | Covered |
| Arize Phoenix | https://github.com/Arize-ai/phoenix | Local, OSS trace viewer/eval UI; runs on Windows; ingests OpenInference. | P1 | Covered |
| Langfuse | https://github.com/langfuse/langfuse | Self-hostable LLM observability + eval + prompt management. | P1 | Covered |
| OpenLIT | https://github.com/openlit/openlit | OTel-native GenAI observability; supports OpenInference instrumentors. | P2 | Covered |

### Orchestration & durable execution (solo/Windows)

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Prefect | https://github.com/PrefectHQ/prefect | Python-native flows/tasks, minimal setup, runs locally on Windows; cron + event triggers. Primary orchestrator. Docs: https://docs.prefect.io | P0 | NEW |
| APScheduler | https://github.com/agronholm/apscheduler | In-process scheduling; simplest replacement for the existing 16 cron jobs. | P1 | NEW |
| Dagster | https://github.com/dagster-io/dagster | Asset-centric orchestration with lineage/freshness policies — conceptually aligned with knowledge-asset freshness (heavier setup). | P2 | NEW |
| Windmill | https://github.com/windmill-labs/windmill | Fast workflow engine + web IDE; polyglot. Alternative if a UI is wanted. | P2 | NEW |
| Temporal | https://github.com/temporalio/temporal | Durable-execution concepts (keep the ideas, defer the cluster). | P2 | Covered |

### Solo-scale progressive delivery

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| OpenFeature | https://openfeature.dev/ | Vendor-neutral flag API/spec; avoid lock-in, swap providers freely. Adopt the SDK. | P0 | NEW |
| Flagsmith (OSS) | https://www.flagsmith.com/open-source | Self-hosted flags + remote config; OpenFeature provider; solo-friendly. | P1 | NEW |
| Unleash | https://github.com/Unleash/unleash | Self-hosted flags, gradual rollout strategies, audit logs. | P1 | NEW |
| Flipt | https://github.com/flipt-io/flipt | Single binary, no external DB, Git-native — lowest ops for one box. | P1 | NEW |

### SQLite production patterns

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Litestream | https://github.com/benbjohnson/litestream | Streaming replication/backup of SQLite to file/S3; disaster recovery without corrupting the DB. | P0 | NEW |
| sqlite-utils (Simon Willison) | https://github.com/simonw/sqlite-utils | CLI + Python lib for schema/insert/migrations; pairs with sqlite-vec plugin. Daily-driver tooling. | P0 | NEW |
| Datasette | https://github.com/simonw/datasette | Instant read UI/JSON API over SQLite; inspect the corpus and ontology. | P1 | NEW |
| SQLite WAL docs | https://www.sqlite.org/wal.html | WAL-mode concurrency guidance (already used); tuning for reader/writer concurrency. | P1 | Covered |

### Autonomic computing & self-adaptive systems theory

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Kephart & Chess — The Vision of Autonomic Computing | https://doi.org/10.1109/MC.2003.1160055 | The founding self-managing-systems vision (self-configuring/healing/optimizing/protecting). Framing for Hermes autonomy levels. | P1 | Covered (cited) |
| IBM MAPE-K Architectural Blueprint | (original IBM PDF link-rotted; cite via Kephart & Chess DOI + secondary literature) | Monitor-Analyze-Plan-Execute over shared Knowledge; the loop Hermes implements. | P1 | Covered (cited) |

### Scientific-discovery agents (inspiration)

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Sakana AI Scientist (published in Nature) | https://www.nature.com/articles/s41586-025-09640-5 | End-to-end research loop: idea generation → experiment → paper → review; population-based idea scoring. Inspiration for hypothesis generation. (If the DOI page differs, reach via the Sakana blog linked from the paper.) | P1 | NEW |
| Google AI co-scientist | https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/ | Multi-agent hypothesis generation + tournament-style ranking/debate for idea prioritization. | P1 | NEW |

### Prompt/context engineering for research agents

| Source | Link | What to extract | Priority | Status |
|---|---|---|---|---|
| Anthropic — Effective context engineering | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | Context-window budgeting, compaction, note-taking, sub-agent isolation — directly applicable to long research runs. | P0 | NEW |
| Anthropic — Contextual Retrieval | https://www.anthropic.com/news/contextual-retrieval | Contextual Embeddings + Contextual BM25 to cut retrieval failures; pairs with Cortex hybrid RRF. | P0 | NEW |

---

## PART 3 — ACTION PLAN (solo dev, Windows, SQLite-first)

### Guiding decisions
- **Resolve the Doc 1 vs Doc 2 store conflict in favor of SQLite indefinitely.** FTS5 + sqlite-vec hybrid RRF, WAL mode, Litestream backups. No Postgres/Neo4j/Timescale until you have a concrete, measured reason.
- **Defer all k8s/cluster tooling.** Argo/Flagger/Kayenta/LaunchDarkly/Temporal-cluster → OpenFeature + Flagsmith/Flipt + git-worktree shadow deploys + Prefect/APScheduler + NSSM services.
- **Adopt OpenInference + OTel GenAI conventions now** (you're already adding OTel) and view traces in local Phoenix.

### Weeks 1–2 — immediate wins on the existing stack
1. **Hybrid retrieval.** Add sqlite-vec alongside FTS5; implement reciprocal rank fusion (RRF) over BM25 + vector results. Use Simon Willison's TIL as the code template. Pick an embedder from the MTEB leaderboard that runs locally on Windows.
2. **Provenance + claim schema.** Extend the accepted-doc tables with `source_id`, `url`, `retrieved_at`, `parser`, `checksum`; a `claims` table (claim text, supporting/refuting evidence spans, verdict ∈ {Supported, Refuted, NotEnoughEvidence, Conflicting} — the AVeriTeC scheme); and a `contradictions` flag linking claim pairs.
3. **Citation verification.** Wire OpenAlex + Semantic Scholar + Crossref lookups; require ≥2 independent confirmations to mark a citation "verified." Store DOIs/arXiv IDs.
4. **Backups + inspection.** Turn on Litestream; stand up Datasette read-only over the corpus and ontology.
5. **Instrument.** Emit OpenInference spans for plan/search/fetch/parse/extract/verify stages; view in Phoenix.

### Month 1 — the research planning loop
Build a **research-priority queue** with two scoring tracks (mirroring the corpus's repair/exploration scores but for knowledge):

- **Gap-driven score** = w1·(failure-cluster recency×frequency) + w2·(stale-knowledge age past its re-verify date) + w3·(low claim confidence) + w4·(missing ontology coverage, measured by unanswered **competency questions**).
- **Opportunity-driven score** = w5·(arXiv new-submission match to tracked topics) + w6·(new release/version of a tracked tool) + w7·(citation-graph novelty from OpenAlex).

Competency questions (SKOS/CQ practice) become the concrete test of "what the ontology must answer"; unanswered CQs are the cleanest gap signal. Use the Anthropic multi-agent pattern: a lead planner writes the plan to a `research_plans` table, spawns bounded parallel fetch/extract workers, and a dedicated citation/verification step attributes every accepted claim.

### Month 1–3 — the research execution pipeline
Implement the multi-stage flow as Prefect flows (or APScheduler jobs) driven by Windows Task Scheduler/NSSM:

`plan → search (Tavily/Firecrawl + arXiv/OpenAlex) → fetch → parse (Docling primary, pymupdf4llm fast-path, MinerU for hard layouts) → extract claims → verify citations (OpenAlex/S2/Crossref triangulation) → score credibility → detect contradictions (NLI, FEVER/SciFact-style) → update ontology → generate hypotheses`

- **Ontology update:** start with a **SKOS-style** concept scheme in SQLite (concepts + broader/narrower/related edges + prefLabel/altLabel). Layer **LightRAG** or **nano-graphrag** for automated entity/relation extraction with **incremental updates** (avoid Microsoft GraphRAG's full re-index cost). Adopt **Graphiti's fact-validity-window idea** for decay.
- **Hypotheses → builder handoff:** each accepted, high-confidence, actionable finding becomes a `hypothesis` record (claim, evidence, confidence, proposed change, predicted effect) — the integration point where research output feeds the evolution loop. Govern every self-change with the **AHE discipline**: pair each edit with a falsifiable prediction verified on the next cycle.

### Knowledge-lifecycle management
- **Freshness/decay:** assign each claim a domain-specific half-life (tool/version facts decay fast; foundational theory slow). Re-verify when age > half-life or when a contradicting source arrives.
- **Deprecation triggers:** superseded version, failed re-verification, or a higher-confidence contradicting claim → move to `deprecated/` (Cortex already has this folder) with an audit trail, never hard-delete (archive-first, per the subtractor pattern).
- **Re-verification schedule:** a nightly Prefect job re-runs verification on the N most-stale, highest-impact claims.

### Evaluating the research harness itself
- **Golden research tasks:** a small set of questions with known good answers/sources (STORM/GAIA-style); grade retrieval with nDCG@k/recall@k (BEIR methodology) and answers with RAGAS faithfulness/context-precision.
- **Claim-accuracy audits:** weekly sample of accepted claims re-checked against sources (mirror Cortex's weekly failure review); track verdict-accuracy and contradiction-detection precision/recall against a FEVER/SciFact-derived mini-set.
- **Regression gates:** wire into OpenAI-evals or Promptfoo so a harness change must not drop retrieval/claim metrics before promotion.

### Explicit defer list (with substitutes)
| Defer | Substitute |
|---|---|
| Kubernetes + Argo Rollouts/Flagger/Kayenta | git-worktree shadow deploy + NSSM service + OpenFeature/Flipt flags |
| LaunchDarkly | Self-hosted Flagsmith/Unleash/Flipt |
| Temporal cluster | Prefect + APScheduler + Task Scheduler |
| Neo4j / Apache AGE | SKOS edges in SQLite → LightRAG/nano-graphrag |
| Postgres + pgvector + Timescale | SQLite + sqlite-vec + FTS5 + Litestream |
| k6 / Toxiproxy | pytest + Promptfoo/DeepEval only |

### Recommended reading order
1. Anthropic multi-agent research system + Building effective agents (architecture spine).
2. CoALA + Zep/Graphiti (memory & living-ontology model).
3. sqlite-vec TIL + FTS5 docs + Litestream (immediate build).
4. GPT Researcher + STORM (planning loop to copy).
5. RAGAS + BEIR/MTEB (how to measure the harness).
6. AVeriTeC + SciFact + OpenAlex/Semantic Scholar docs (claim verification).
7. LightRAG/nano-graphrag + SKOS Primer + Competency Questions (ontology upgrade).
8. AHE / HarnessFix / Self-Harness (self-modification governance).

---

## Recommendations (staged)
- **Now (weeks 1–2):** ship hybrid RRF search, provenance/claim/contradiction tables, OpenAlex+S2+Crossref citation verification, Litestream, and OpenInference tracing. Benchmark to change plans: if local embedding latency > ~200 ms/query or recall@10 < 0.6 on your golden set, switch embedder (MTEB) or add Contextual Retrieval.
- **Month 1:** stand up the research-priority queue with the two scoring tracks and a competency-question coverage metric. Threshold: if unanswered-CQ count isn't falling week over week, the planner's weights need tuning.
- **Months 1–3:** implement the full pipeline in Prefect; add LightRAG incremental KG updates and Graphiti-style validity windows; wire the hypothesis→builder handoff under AHE prediction discipline. Threshold to introduce heavier infra: only adopt Postgres/Neo4j/Temporal-cluster if single-node SQLite write contention or graph-query latency is *measured* to block you — not preemptively.
- **Ongoing:** weekly claim-accuracy audit + retrieval-metric regression gate before any harness promotion.

## Caveats
- The four "2026" harness papers are real and verified, but they are recent and their empirical claims (e.g., Terminal-Bench pass@1 lifts) are single-team results; treat as design inspiration, not settled fact.
- Memory-framework benchmark numbers (Zep vs mem0 LongMemEval, Mem0 LoCoMo scores) are vendor-run with differing judge models and are contested; do not over-index on any single leaderboard figure.
- The IBM MAPE-K blueprint's original PDF URL has suffered link rot; cite the concept via Kephart & Chess (DOI) and secondary literature.
- sqlite-vec is young and pre-1.0; validate index performance on your corpus size before committing, and keep FTS5 BM25 as the reliable backbone.
- OpenAlex's exact work count is a moving figure (reported at ~474–477M around early 2026); use it as an order-of-magnitude, not a fixed number.
- A few sources surfaced during research were aggregators/secondary (Medium, Substack, vendor blogs); the catalog above deliberately links to primary sources (arXiv, official GitHub, W3C, official docs) wherever possible. Two links to verify on first click: the Sakana AI Scientist Nature DOI and the Google AI co-scientist blog slug — if either 404s, reach them via the Sakana blog and research.google respectively.
