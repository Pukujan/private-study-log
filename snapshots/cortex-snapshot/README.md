# Cortex

> **Human starting point:** read [`docs/harness/START-HERE.md`](docs/harness/START-HERE.md) first.
> For current continuation and owner constraints, continue to [`HANDOFF.md`](HANDOFF.md). Do not
> reconstruct current state from old closeouts or chat transcripts.

**A self-learning documentation corpus + audit-log system that AI agents connect to over MCP.**
Cortex gives an agent a searchable, trust-tiered knowledge corpus, a verified write path, a
permanent audit trail, a living-ontology "what's current" graph, and a shared task ledger — so
work is *sourced and checked*, never guessed. It is built and dogfooded on its own development:
every feature has to show up in its own audit log as *used* before the next one starts.

Two ideas run through everything:

1. **Evidence over trust** — retrieval + citation on the read path; a contract + **objective
   deterministic checker** gate on the write path. Claims are verified by tests / schemas / AST
   matchers / execution, **never** by a model's say-so.
2. **Scoped, not bloated** — agents get *exactly* the knowledge a task needs (budget-capped scope
   packs), guided to the next step, with a one-line closeout at the end.

Full architecture + subsystem map: **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**.
Documentation index: **[Documentation](#documentation)** below.

## What you get

| Subsystem | What it does | Entry points |
|---|---|---|
| **Hybrid RAG** | BM25 + dense-vector (RRF) retrieval + budget-capped, task-scoped packs over the corpus | `cortex-search`, `cortex-scope-pack`, MCP `cortex_search`/`cortex_scope_pack` |
| **Verified write path** | Approach contract → objective-checker gate → structured closeout (the permanent audit trail) | `cortex-contract`, `cortex write-log`, MCP `cortex_contract`/`cortex_write_log` |
| **Objective hard-gold lab** | 73 deterministic-checker oracle lanes (execution / AST / arithmetic / citation) — **zero model judges** in any verdict path | `evals/`, `cortex-eval`, `cortex-graded-eval` |
| **Faithfulness oracle** | Deterministic, LLM-free hallucination detection (numeric / relational / temporal contradiction gates) | `cortex_core/faithfulness.py` |
| **Vague-build SaaS harness** | Turns a plain-English app request into a working CRUD app via template-injection skills, gated by ~17 deterministic behavioral checks | `cortex-build`, `cortex_core/vague_build.py`, `app_gates.py` |
| **Ownership & admin auth** | Owner-mode (local, open) vs served-mode (canonical corpus immutable without admin token) | `cortex_core/authz.py`, `docs/CORTEX-ROUTES-AND-OWNERSHIP.md` |
| **Living ontology** | Self-maintaining knowledge graph answering "which doc/rubric/gap is CURRENT" (bi-temporal, provenance) | `cortex-ontology`, MCP `cortex_ontology_query` |
| **Task ledger** | File-backed, atomic-claim coordination so multiple agents don't collide on tasks | `cortex-tasks`, MCP `cortex_tasks_list`/`_claim`/`_update` |
| **Deep research / deep audit** | Cited, corpus-resolved research; recursive audit-log digests with a faithfulness gate | `cortex-research`, `cortex-deep-research`, `cortex-deep-audit` |
| **Pattern library + registry** | Failure/success KEDB + versioned, provenance-stamped prompt/artifact registry | `cortex-pattern`, `cortex-registry` |
| **Resilience ops** | Health check, crash auto-resume (task-precise), Telegram alerts, end-to-end MCP checker | `ops/` |
| **Assurance system** | Decision-bound research sufficiency receipts, driver preflight, external evaluator verification — builder-facing tools verify, never mint, trust | `cortex_core/assurance_contracts.py`, `assurance_evaluator.py`, `assurance_result.py`, `driver_preflight.py`, `research_sufficiency.py`, `research_trust.py` |
| **Capability router** | Deterministic, evidence-aware model route planning (no completion dispatch). `catalog` returns the key-free roster; `route` creates a no-spend capability-qualified receipt or UNRESOLVED | `cortex_core/capability_router.py`, `model_catalog.py` |
| **Project state** | Deterministic append/reduce event-sourced operational state with projections, ontology sync, and CLI visibility | `cortex_core/project_state.py`, `project_state_store.py`, `project_state_projection.py`, `project_state_ontology.py`, `cortex-project-state` CLI |
| **Knowledge retrieval** | Composite, bounded local-knowledge retrieval for Cortex | `cortex_core/knowledge.py` |
| **KEDB incidents** | Structured incident records for the pre-pattern stage of the Cortex KEDB | `cortex_core/kedb_incident.py` |
| **Optional memory sidecar** | Mem0-backed closeout memory over local Ollama/Qdrant settings, fail-open | `cortex_core/memory.py`, `cortex_core/plugin.py` |

## Quick start

```bash
pip install -e .            # installs the cortex-* CLIs (add .[vector] or .[memory] for optional extras)
cortex-doctor               # sanity-check workspace + index + git hygiene

cortex-search --hybrid "rebuild lock corruption"   # BM25+vector RRF search
cortex-scope-pack --task "how does the rebuild lock work"   # budget-capped task pack
cortex-fetch --url "<url>" --name "<name>"          # SSRF-guarded fetch -> HTML->text -> corpus
cortex write-log --task "<what>" --result "<what happened>"   # closeout (the audit trail)
cortex-ontology query "cortex"                      # what entities/relations are CURRENT
```

Agents connect via the **MCP server** — locally (`cortex-mcp` stdio, registered in `.mcp.json`) or
over HTTP to a **hosted brain** (`cortex-mcp-http`, bearer-gated served mode; a live deployment runs
on Railway). After one `cortex_register`, call **`cortex_onboarding`** and the server tells you how to
operate it — every tool + when to use it, the RAG flow, per-stage reasoning tiers, the disciplines
(generated from live state, so it can't go stale) — then the whole loop is in-band. (Local stdio: use
the absolute path to the correct install in `.mcp.json`, not the bare name — a stale venv on `PATH`
shadows it.)

### Legacy local structural runner (not assured)

The local `cortex-govern` command can drive an OpenAI-compatible model (9router / opencode-zen /
openrouter / local) through ordered phase slots. It checks structural phase ordering and non-empty
outputs, but the same model can author the SEARCH_BRAIN and RESEARCH JSON accepted as evidence.
Therefore this path is `LEGACY_UNASSURED`: it is useful for local workflow experiments, not proof of
research sufficiency, external governance activation, product correctness, or independent review.

```bash
pip install -e ".[vector]"
cat > provider.env <<'EOF'                 # your endpoint + key (gitignored — never commit)
NINEROUTER_API_URL=https://<your-9router-endpoint>/v1
NINEROUTER_API_KEY=<your key>
NINEROUTER_MODEL=<your model id>
EOF
cortex-govern --selftest                   # wiring check — no key, no tokens spent
cortex-govern "add a rate-limiter to the API"   # legacy local run; not Cortex-governed
```

Each run writes an append-only call ledger + `result.json` (the full coerced walk) to
`cortex-govern-runs/<timestamp>/`. That ledger proves a local structural walk occurred; it does not
prove governance or correctness. Historical gate results, limits, and setup are in
**[`docs/PHANTOMIC-HANDOFF.md`](docs/PHANTOMIC-HANDOFF.md)**.

## The core loop (over MCP)

```
cortex_register ─► cortex_onboarding (the server self-describes how to operate it)
   └► cortex_status ("you are here" + access + next step)
   └► cortex_scope_pack  (budget-capped: patterns, prior closeouts, doc chunks)
        └► cortex_contract (documented plan before any write)
             └► EXECUTE  (objective checks: tests / schema / execution — never self-report)
                  └► cortex_write_log  (structured closeout → permanent audit trail)
```

## Command surface (CLIs)

| CLI | What it does |
|---|---|
| `cortex-govern` | legacy local structural phase runner (`LEGACY_UNASSURED`); `--selftest` is a wiring check |
| `cortex-search` | hybrid (BM25+vector RRF) corpus search; `--status`, `--no-vector` |
| `cortex-scope-pack` | budget-capped, task-scoped knowledge pack |
| `cortex-build` | vague-build SaaS harness: NL request → template-injection skills → deterministic behavioral gate |
| `cortex-fetch` | SSRF-guarded fetch + HTML→text extraction into the corpus |
| `cortex write-log` / `cortex-audit` | write / query the audit-log closeouts |
| `cortex-contract` | approach-contract intake for the verified write path |
| `cortex-ontology` | living-ontology graph (query / upsert / supersede / validate) |
| `cortex-tasks` | shared task-coordination ledger (list / claim / update) |
| `cortex-mcp` / `cortex-mcp-http` | run the MCP server (30 tools) — local stdio / hosted HTTP (bearer-gated) |
| `cortex-repo-audit` | deterministic repo-health auditor (lanes, baseline+ratchet, LLM-triage-only) |
| `cortex-eval` / `cortex-graded-eval` | retrieval eval (recall@5/MRR) + chunk-level graded eval |
| `cortex-research` / `cortex-deep-research` | cited corpus-first research (CLI / async MCP task) |
| `cortex-deep-audit` | recursive digest tree over the audit log (provenance-preserving) |
| `cortex-pattern` | failure/success pattern KEDB (occurrence-floored) |
| `cortex-registry` | versioned, provenance-stamped prompt/artifact registry |
| `cortex-doctor` | workspace/index diagnostics + git-hygiene report |
| `cortex-project-state` | operator/CI visibility for replayable project state (event log, projections, ontology sync) |

## MCP tools (30)

Core: `cortex_register`, `cortex_onboarding` (**call it first — the server tells you how to
operate it**), `cortex_status`, `cortex_search`, `cortex_scope_pack`, `cortex_fetch_doc`,
`cortex_write_log`, `cortex_contract`, `cortex_fingerprint`, `cortex_ontology_query`,
`cortex_dispatch_tier` (supports `action="dispatch"` for model completion, `action="catalog"`
for the key-free model roster, and `action="route"` for a no-spend capability-qualified route
receipt).
Consolidated dispatchers (one tool, many actions): `cortex_tasks`, `cortex_research`,
`cortex_key`, `cortex_playbook`.
State machine (enforced Plane-B build/research engine): `cortex_run_start`, `cortex_run_step`,
`cortex_run_state`, `cortex_phase_state`, `cortex_phase_heartbeat`, `cortex_phase_checkpoint`,
`cortex_phase_resume`, `cortex_report_empty_output`. Tracks: `build`, `research`,
`assured_build`, `assured_research`, `mission`, `app_build`.
Multi-agent missions (heterogeneous decomposer / fan-out / merge): `cortex_spawn_mission`,
`cortex_mission_status`, `cortex_acquire_claims`, `cortex_submit_mission_contract`,
`cortex_submit_partition`, `cortex_dispatch_mission`, `cortex_submit_merge`.
Plus prompts (`cortex_preflight`, `cortex_closeout`) and resources (`cortex://doc/{path}`,
`cortex://onboarding`).

## Ownership model (who reads/writes what)

Cortex is a **shared brain you read and a private notebook you write.** In **owner mode**
(default, local) every agent reads *and* writes freely. In **served mode** (a published,
admin-owned instruction server) the canonical corpus/rubrics/gold is **immutable without admin
authentication** — connected agents get bounded *query* access (no bulk export) and their writes
land in their own workspace. Full doctrine + routes + the moat:
**[`docs/CORTEX-ROUTES-AND-OWNERSHIP.md`](docs/CORTEX-ROUTES-AND-OWNERSHIP.md)**.

## The objective hard-gold lab (`evals/`)

Cortex's evaluators are trained/measured on **objective** labels: a deterministic **checker** —
never a model judge — decides pass/fail. This is the escape from judge-only "gold," which Stage 1
showed is confounded by self-preference, leniency, and a length artifact. Strong models are used as
*architects, edge-case generators, and reviewers* — **never** as the gold-label authority.
Judgment-only labels stay `*_semi_ground_truth`, never laundered into `hard_gold`.

**73 oracle lanes, ~5,700+ checker-decided hard-gold rows, zero judges in any verdict path**
(ledger: `evals/promotion_decisions/stage2_objective_promotions.jsonl`):

| Lane | Label authority (never a judge) | rows |
|---|---|---:|
| `tool_calling` (synthetic + live) | real **BFCL v4** `ast_checker`; cross-validated at 99.93% | 3,279 + 192 |
| `coding` | subprocess **test execution** (visible + hidden holdout) | 21 (+ MBPP runs) |
| `security_defensive` (access-control) | AST detector + **bandit** cross-check | 20 |
| `research_citation` | citation + contradiction verifier (abstains, never fakes a label) | 8 |
| `architecture` | static import-graph + AST (layering / cycles / API-compat) | 8 |
| `tenant_isolation` | runtime execution + AST detector | 18 |
| `ledger_balances` | double-entry **arithmetic** checker | 19 |
| `sql_correctness` | `sqlite` execution | 20 |
| `regex_correctness` | `re` execution | 18 |
| `datetime_correctness` | stdlib datetime computation | 20 |

Each lane declares its label authority, ships **frozen checker tests**, and self-validates by
quarantining what it can't cleanly decide (9 honest quarantines, never forced into gold). Sizes are
**honest proof-of-lane** — scale-up (SWE-bench Verified, MBPP-at-scale, CUAD, FinQA, SEC EDGAR) is a
tracked user cost decision, not a claim of coverage. `invoice_reconciliation` is landing next. See
[`evals/README.md`](evals/README.md) and `docs/research/benchmark-inventory-and-acquisition-2026-07-11.md`.

### Faithfulness oracle (deterministic, LLM-free)

A separate **no-LLM, no-network** grounding checker (`cortex_core/faithfulness.py`) flags
hallucinations with numeric / relational / temporal **contradiction gates** (spelled-out-number
mismatch, quantifier-bound violation, swapped proper-name bigram, date-ordering contradiction).
Cross-validated on 500 human-annotated HaluEval QA pairs: **grounded-accept 0.945, hallucination-
reject 0.758, balanced 0.849** — well above the lexical baseline (0.645), and the reject gain holds
on a **held-out slice** the rules were not shaped against. It deliberately **abstains** on the
entailment-required classes that need a world model (frozen as known-miss regression anchors), so the
numbers are a floor for an NLI backend to beat, not a claim of solved hallucination detection. Report:
`evals/reports/FAITHFULNESS_HALUEVAL_CROSSVAL.md`.

## The vague-build SaaS harness (`cortex-build`)

An experiment in **letting a cheap model build software without letting it decide whether the
software works.** A plain-English request ("track my members, count the active ones, let me search
them") is keyword-routed to a fresh-build skill plus any follow-on edit skills. Each skill is a
**template-injection** unit: the model fills exactly **one validated JSON slot**; the *harness*
renders the code from a fixed template. The result is then put through **~17 deterministic gate
kinds** — `app_starts`, `buttons_work`, `logic_works`, `data_persists` (write → kill → restart →
re-read from HTTP *and* the sqlite file), `security_controls` (runtime negative tests, never a code
grep), `auth_required`, `audit_trail`, `relation_integrity`, `derived_value`, `filtered_results`,
`deletes_row`, `edits_row`, `dashboard_metrics`, `detail_view`, and more. Verdicts come from
subprocess + HTTP + sqlite observation **only** — no LLM ever sees a pass/fail decision, and hidden,
seeded test payloads (`@hidden:<name>`, resolved at gate runtime) mean the spec can't encode the
answer. Mutation-integrity is proven (a broken build must fail its gate). This is the same
"generate freely, verify deterministically" discipline as the objective lab, applied to whole apps.

## Hybrid state machine — BUILT, with assured tracks

A server-side **director cascade** (cheap→expensive routing: deterministic rules → embedding router →
nearest-centroid → free-model LLM fallback), an event-sourced **`StateEngine`** (single-writer,
`BEGIN IMMEDIATE` per superstep, idempotent, lease+reaper), and a human-confirm **reaction loop** are
all built and pass the full suite. The legacy `build`/`research` tracks run in shadow — they do NOT
route or gate real builds. **Assured tracks** (`assured_build`, `assured_research`) add a
server-owned **research-sufficiency receipt gate**: only a decision-bound
`SUFFICIENT_FOR_DECISION` receipt advances to planning; `UNRESOLVED` reworks and `ABSTAIN` exits
honestly without unlocking dependent work. The machine has been **twice adversarially re-reviewed
by GPT-5.6** (xhigh effort): 5 of 7 findings are closed structurally, but **3 documented residuals
block promotion** of the legacy tracks — a missing-artifact fail-open in the SMOKE receipt path, a
repeated/racing `_apply` seam on the acceptance path, and a falsy-project continuation-laundering
gap in the rework cap. Status is honest by design: review at
`reviewed/hybrid-state-machine-codex-gpt56-terra-RE-REVIEW-ROUND2-2026-07-11.md`.

## The model fleet (generate wide, verify deterministically)

Candidate generation and distillation fan out across a **multi-vendor pool of free / cheap models**
(Qwen 35B, DeepSeek, GLM, gemma, OpenCode-Zen lanes, local Ollama), with strong models reserved for
design, edge-case authorship, and review. The invariant that makes this safe: **the fleet generates,
deterministic checkers decide** — no weak model ever authors an oracle label. Prometheus is a judge
only (it scores; it never writes) and is correctly **absent from every objective verdict path**.
Pool + routing + provider allowlists: [`docs/MODEL-ROLES.md`](docs/MODEL-ROLES.md). Durable runs land
on a separate multi-project host (**gravebuster**) behind a filesystem contract, with CI quality gates
(tests + lint/type + objective-integrity + merge-safety + secret scan) enforced in the pipeline —
infra config and secrets stay out of this public repo per `docs/OPS-BOUNDARY.md`.

## Documentation

| Doc | Topic |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | full subsystem + module map (start here) |
| [`docs/FEATURES.md`](docs/FEATURES.md) | **every feature: built vs. remaining** (status matrix) |
| [`docs/CHECKERS.md`](docs/CHECKERS.md) | checker inventory, false-pos/neg envelope, silent-failures caught |
| [`docs/CORTEX-ROUTES-AND-OWNERSHIP.md`](docs/CORTEX-ROUTES-AND-OWNERSHIP.md) | ownership, routes, admin auth, the moat |
| [`docs/MODEL-ROLES.md`](docs/MODEL-ROLES.md) | model pool, routing, provider allowlists |
| [`docs/COMPUTE-INFRA.md`](docs/COMPUTE-INFRA.md) | machines, storage policy, gravebuster |
| [`docs/PHASE-GATES.md`](docs/PHASE-GATES.md) | authoritative per-gate build status |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) / [`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md) | the plan |
| [`docs/EVAL-DESIGN-PHASE2.md`](docs/EVAL-DESIGN-PHASE2.md) / [`docs/ADR-0001-VECTOR-LEG.md`](docs/ADR-0001-VECTOR-LEG.md) | retrieval eval + vector-leg decision |
| [`docs/DEEP-RESEARCH-DESIGN.md`](docs/DEEP-RESEARCH-DESIGN.md) | deep-research design + honest limits |
| [`docs/MEM0-LOCAL-SETUP.md`](docs/MEM0-LOCAL-SETUP.md) | optional local Mem0 memory sidecar setup |
| [`docs/ontology/README.md`](docs/ontology/README.md) | the living-ontology schema + storage |
| [`docs/harness/START-HERE.md`](docs/harness/START-HERE.md) | human starting point — read first before project work |
| [`docs/harness/CAPABILITY-STATUS.md`](docs/harness/CAPABILITY-STATUS.md) | capability status matrix for the assurance system |
| [`docs/harness/CONTRACTS.md`](docs/harness/CONTRACTS.md) | execution + success contract format for assurance |
| [`docs/harness/EXPECTED_BEHAVIOR.md`](docs/harness/EXPECTED_BEHAVIOR.md) | expected behavior spec for assured runs |
| [`docs/harness/PRODUCTION-REFERENCE-MODEL.md`](docs/harness/PRODUCTION-REFERENCE-MODEL.md) | production reference model for the assurance system |
| [`docs/harness/RUNTIME-MAP.md`](docs/harness/RUNTIME-MAP.md) | runtime map of the assurance system components |
| [`docs/harness/CURRENT-STATE-AUTOMATION.md`](docs/harness/CURRENT-STATE-AUTOMATION.md) | current-state automation and project-state bootstrapping |
| [`docs/harness/KNOWLEDGE-ESCALATION.md`](docs/harness/KNOWLEDGE-ESCALATION.md) | knowledge escalation pipeline |
| `templates/workspace-control-plane/gaps/registry.md` | tracked gaps (design/build board) |

## What's built (honest status)

**Built and dogfooded:** correctness floor, measure-first eval harness, hybrid retrieval, MCP server,
approach contracts + verified write path, pattern-library/scope-pack layer, the **objective hard-gold
lab** (73 checker-decided lanes, ~5,700+ rows), the **deterministic faithfulness oracle**, the
**vague-build SaaS harness** (template-injection skills + ~17 behavioral gate kinds, mutation-integrity
proven), **Deep Audit** + **Deep Research** (v1), prompt/artifact registry, the **ownership/admin-auth
layer**, the **living-ontology + task-ledger v1**, the **hosted HTTP transport** (bearer-gated served
mode, live on Railway — H1/H2a), **in-band onboarding** (`cortex_onboarding`, generated from live
state), and the **repo-health auditor** (`cortex-repo-audit`, Phases 0–1), the **assurance system**
(decision-bound research sufficiency receipts, driver preflight, external evaluator verification —
builder-facing tools verify, never mint, trust), the **capability router** (deterministic, evidence-aware
model route planning with `catalog`/`route` actions), the **project-state event store** (append/reduce
core, projections, ontology sync, CLI), **knowledge retrieval** (composite, bounded local-knowledge),
and **KEDB incidents** (structured pre-pattern incident records).

**Built but NOT promoted (shadow / experimental):** the **hybrid state machine** legacy tracks
(`build`/`research`) are fully built and pass their suite, but run in **shadow only** — they do not
route or gate real builds. The **assured tracks** (`assured_build`/`assured_research`) add the
research-sufficiency receipt gate. 3 residuals from two GPT-5.6 adversarial reviews block promotion
of the legacy tracks.

**Honest gaps:** the living ontology's *retrieval-win* fusion is pending (mechanism built, not yet
wired into RRF); the objective lanes are proof-of-lane sized (scale-up is a user cost decision);
per-tenant identity / isolated multi-tenant writes (`GAP-CORTEX-0015` H2b) and repo-audit Phases 2–4
remain. **Full built-vs-remaining matrix: [`docs/FEATURES.md`](docs/FEATURES.md)**; per-gate evidence:
[`docs/PHASE-GATES.md`](docs/PHASE-GATES.md).

## Workspace layout

```
cortex.json                 workspace config          library/cortex-library/   search index + catalog
cortex_core/                the engine (CLI + MCP)     evals/                    objective-gold lab
docs/                       corpus shards + plans      docs/ontology/            the living-ontology graph
audit/audit-log-*/agent/    closeouts (audit trail)    calibration/              rubrics + golden sets
inbox/ reviewed/ accepted/  trust-tiered doc pipeline  registry/  patterns/      registry + KEDB
ops/                        resilience/monitoring      logs/  contracts/         runtime state
```

Set `CORTEX_WORKSPACE` to the workspace root if auto-detection misses.

## Prerequisites

- Python 3.10+ (FTS5 search); optional `.[vector]` extra for the dense vector leg.
- An MCP-capable client (e.g. Claude Code) for the connect-and-go loop.

## License

All Rights Reserved. See `LICENSE`.

---
*Built and dogfooded on its own development — if a feature isn't in the audit log as used, it gets
demoted before the next one starts.*
