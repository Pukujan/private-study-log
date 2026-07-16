# Cortex — complete capability sweep (from-scratch re-plan foundation)

**Date:** 2026-07-13 (living; dated addenda below) · **Method:** evidence-based,
file:line-cited, gaps-first, no cheerleading. Every row was verified against live
code/tests/committed numbers, not against prose claims. `UNVERIFIED` = could not confirm
from disk. This sweep is deliberately exhaustive: it covers the whole surface (ROADMAP
phases 0–8, all 40 MCP tools, both planes, wrapper) and calls out what the two prior
partial sweeps (brain-fitness = GAP-CLOSURE §G; delivery = GAP-CLOSURE §H) did **not**
cover. (Counts current as of the 2026-07-15 addendum: **30 MCP tools** after the four
dispatcher families were consolidated; **73 deterministic eval lanes**.)

> **SOURCE OF TRUTH:** This capability sweep is the canonical built/missing manifest —
> consult (and update) it **before** claiming any capability is built or missing. Rows
> carry a dated status; when a gap closes, flip the row here in the same change (do not
> leave the claim to live only in a closeout or a code comment). **Addenda:** 2026-07-15 —
> **G2 (living ontology → retrieval fusion) CLOSED**: fused, measured, and turned ON for
> our own corpus (see the Living-ontology row and Top-GAP #1).
>
> **Addendum 2026-07-15 — orchestration + fleet-ops now BUILT (was "aspirational skin"):**
> - **State-machine auto-delegation / fan-out wiring.** `cortex_core/fanout.py` (homogeneous
>   best-of-N over live executors, receipt-gated) is now wired *into* the enforced state
>   machine at the scaffold/build track, not just a standalone CLI. Each fan-out branch is
>   graded by the same deterministic receipt gate (no judge). Colon-in-lane-key lock paths are
>   Windows-safe (`model_dispatch._slot_dir_name`).
> - **Native heterogeneous decomposer + mission driver.** `cortex_core/decomposer.py` (terra
>   partition seam: split a mission into independent, differently-charted slices) +
>   `cortex_core/mission_driver.py` (`run_mission`: fan the slices out, each carrying its **own
>   per-worker receipt**, then merge/verify back through the state machine). Exposed via the
>   mission MCP tools (`cortex_spawn_mission`, `cortex_dispatch_mission`, `cortex_submit_partition`,
>   `cortex_submit_merge`, `cortex_mission_status`, `cortex_acquire_claims`,
>   `cortex_submit_mission_contract`). Both halves are PUBLIC-safe and vendored into the wrapper.
> - **`cortex-models` provider probe.** `cortex_core/model_probe.py` discovers which of the
>   caller's own lanes are reachable (free-only liveness, never charges a paid token, never logs a
>   secret) → `model_availability.json`; fan-out then restricts itself to live executors.
> - **Key expiry (TTL) + owner key dashboard.** `cortex_core/keys.py` gains per-key TTL/expiry;
>   `cortex_core/keys_dashboard.py` is the owner-only local key CRUD/audit view (gap H2). These
>   stay **brain-server-side only** — deliberately NOT vendored into the public wrapper.

> **One-line verdict:** Cortex is a large, genuinely-built *retrieval + objective-eval
> lab* (Plane B engine + 63 deterministic eval lanes are real and tested) wearing an
> *aspirational harness/orchestration/self-learning* skin (arbitration, self-learning
> flywheel, ontology-in-retrieval, OTel, receipts, consent — all built-as-parts but
> unwired, default-off, or unmeasured). The gap between "modules exist" and "wired into
> a live, measured path" is the dominant finding.

---

## PART 1 — Capability coverage matrix

Columns: **B**=Built · **W**=Wired into a live path · **T**=Tested · **M**=Measured
(real committed number). Cell values: ✔ = yes · ~ = partial · ✗ = no.

### Plane B — local retrieval engine (over the user's OWN corpus)

| Capability | Purpose (cite) | B | W | T | M | STATUS | Evidence | To close |
|---|---|---|---|---|---|---|---|---|
| Hybrid retrieval (BM25 + query ladder) | ROADMAP §4 Ph1; PHASE-GATES 0.3 | ✔ | ✔ | ✔ | ✔ | **WORKS** | `search.py:505` search(); AND→OR ladder `:548`; MCP `mcp.py:1331`, CLI `cortex-search` | — |
| Vector leg (sqlite-vec + model2vec) | PHASE-GATES 2.1–2.3 | ✔ | ✔ | ✔ | ~ | **WORKS** (default-on hybrid, degrades to BM25 w/o `[vector]`) | `vector.py`; `MAX_VECTOR_DISTANCE=1.10` `vector.py:44`; MCP passes `use_vector=True` `mcp.py:1348` | measured only on n=17 agent-authored, human-review-**pending** set (`EVAL-DESIGN-PHASE2.md:168`) |
| **Living ontology** | ROADMAP Ph4/7.5; PHASE-GATES 7.5 | ✔ | ✔ | ✔ | ✔ | **WORKS / FUSED (G2 closed 2026-07-15)** — ontology-expansion leg fused into RRF; **measured win** on scattered corpus + **now net-positive on our own dense corpus**, ON by config | `ontology.py` real graph; `_ontology_leg` + `_search_fused` `search.py:672-884` (RRF-fuses BM25[+vec]+ontology); config switch `_ontology_fusion_config` `search.py:528`; **`docs/ontology/retrieval.yaml` `enabled:true` for THIS corpus** (auto-fuses every search). Scattered gate nDCG 0.077→0.779 / recall 0.20→1.00; dense re-measure 2026-07-15 nDCG +0.020 / recall +0.200 / mrr +0.117, single-hop 0.000 no-op | closed — `evals/reports/ontology_retrieval_scattered_gate.md` + `evals/reports/ontology_retrieval_dense_remeasure_2026-07-15.md` |
| Scope packs | PHASE-GATES 5.2 | ✔ | ✔ | ✔ | ~ | **WORKS** | `packs.py:102`; MCP `cortex_scope_pack` `mcp.py:1374` | "96.1% context cut" lives only in prose, **no committed results ledger** (fact-check C1) |
| Deep research (v0/v1) | ROADMAP "Deep research"; DEEP-RESEARCH-DESIGN | ✔ | ✔ | ✔ | ~ | **PARTIAL** | `research.py:617` run_research; async `deep_research.py`; MCP `cortex_deep_research` `mcp.py:1696` | `research_v2_experimental.py` is a self-described throwaway, unwired — retire/merge |
| KEDB / patterns (self-learning) | ROADMAP §9; PHASE-GATES 5.1 | ✔ | ~ | ✔ | ✗ | **GAP** — promotion real but **no non-CLI caller; flywheel is manual** | `patterns.py:178` promote_candidates; only caller is its own CLI `patterns.py:225` | auto-trigger promote_candidates post-closeout (GAP-CLOSURE G1) |
| Workspace-parameterization (Plane B core) | wrapper README "Plane B" | ✔ | ✔ | ✔ | n/a | **WORKS** — genuinely runs over an arbitrary corpus | `config.py:72` resolve_workspace (env-first); dual-plane `:120`; `test_dual_plane.py` | — |
| Doc freshness / bi-temporal / incremental reindex | GAP-CLOSURE G3 | ~ | ~ | ~ | ✗ | **PARTIAL** — smart staleness *detect*, but **full O(corpus) rebuild; no bi-temporal validity** | staleness fast-path `search.py:462`; but `rebuild_vectors` re-embeds whole corpus `vector.py:138`; no valid-from/to in schema | per-doc incremental upsert + validity windows |

### Evaluation / trust plane (the strongest half of the system)

| Capability | Purpose | B | W | T | M | STATUS | Evidence | To close |
|---|---|---|---|---|---|---|---|---|
| Objective eval lanes | ROADMAP "Objective hard-gold lab"; CLAUDE.md | ✔ | ✔ | ✔ | ✔ | **WORKS** (strongest asset) | **63 `evals/objective_*` dirs on disk**, 59 deep `tests/test_objective_*` + integrity floor; deterministic checkers (e.g. `objective_ledger_balances/checker_ledger.py`); `judge_in_verdict_path:false` committed in `promotion_decisions/stage2_objective_promotions.jsonl` | see catalog drift below |
| — catalog/ledger accuracy | — | — | — | — | — | **GAP** | catalog claims **29 lanes / 5,804 records**; disk has **63 lanes / 4,551 hard_gold rows** — stale/inconsistent | regenerate ledger from disk (GAP-CLOSURE C2) |
| Calibration (Cohen's κ / bias) | ROADMAP §4 Ph4.4 | ✔ | ✔ | ✔ | ✔ | **WORKS** | `calibration.py:70` cohens_kappa; `calibration/results/LEADERBOARD.md` (22 judges, sonnet κ=0.9241, prometheus κ=0.0); BIAS-AUDIT.md | family-bias remains **unresolved** (gold+rubric both Anthropic-authored) |
| LLM-judge | ROADMAP §4 Ph4.4 | ✔ | ✔ | ✔ | ✔ | **WORKS** | `judge.py:185` JUDGE_LADDER (26 tiers); `MIN_MAX_TOKENS_BY_TIER` floor present `judge.py:270` | — |
| **Arbitration (multi-model)** | design docs 2026-07-13 | ✗ | ✗ | ✗ | ✗ | **ASPIRATIONAL / UNBUILT in code** | `grep arbitrat cortex_core/` = **0 matches**; exists only as `docs/design/multi-model-arbitration-*`; closest analog is promotion-gate 3-family agreement `promotion.py:87` | implement `arbitrate(question, task_type)` + tests (GAP-CLOSURE B1) |
| Anti-distillation | commit c4e96c7a; CLAUDE.md flywheel | ~ | ✗ | ✔ | ✗ | **PARTIAL/THIN** — 46-line guard over **stub id lists**, not a wired exporter | `distill.py:19` _PROPRIETARY_BLOCKLIST + load-time assert `:25`; but no importer, "pluggable trainer backend in production" `:6` absent | wire to real `trace_capture.distillation_records()` + trainer |
| Faithfulness (RAGAS-style) | PHASE-GATES 2D; CHECKERS | ✔ | ✔ | ✔ | ✔ | **WORKS** | `faithfulness.py`; measured on 500 HaluEval pairs, hardened balanced_acc 0.849 (`FAITHFULNESS_HALUEVAL_CROSSVAL.md`) | — |
| Evaluator + MARCH asymmetry | PHASE-GATES 4.4 | ✔ | ✔ | ✔ | ~ | **WORKS** | `evaluator.py` extracts claim only, never actor reasoning `:47,92,182` | — |
| Scorecard rollup | ROADMAP §5; PHASE-GATES 1.4/6.4 | ~ | ~ | ✔ | ~ | **PARTIAL (honest-null)** | `scorecard.py` synthetic-gate; `scorecards.py` ingests real leaderboard but cost/latency/token cols `not_wired (Phase 6)` `scorecards.py:51` | wire OTel/gateway data plane |
| Prometheus veto | PHASE-GATES; BUILD-PLAN | ~ | ~ | ✔ | ✔ | **PARTIAL (scope-limited)** — gates synthetic-gold promotion only, NOT objective verdict path; κ=0 confirmed | `promotion.py:87`; κ=0 committed `calibration/results/prometheus-*.json` | fix native-template dispatch or drop (GAP-CLOSURE B3) |
| Oracle cross-validation strength | GAP-CLOSURE D1 | ~ | ~ | ✔ | ~ | **PARTIAL** — dual-oracle beyond BFCL exists (ledger int-cents crosscheck `run_ledger.py:41`) but **advisory, not gating**; minority of 63 lanes | per-lane 2nd independent checker as a gate |

### Orchestration / execution plane

| Capability | Purpose | B | W | T | M | STATUS | Evidence | To close |
|---|---|---|---|---|---|---|---|---|
| State machine | ROADMAP §9; PHASE-GATES; GAP-CORTEX-0020 | ✔ | ✔ | ✔ | ~ | **WORKS** | `state_engine.py` Harel statechart on SQLite; 4 tracks BUILD/RESEARCH/MISSION/APP_BUILD `:61`; MCP `cortex_run_start/step/state` | ABSTAIN/human REVIEW-exit not built (GAP-CLOSURE B2) |
| Mission / multi-agent tools | ROADMAP §9 fleet; MISSION_TRACK | ~ | ~ | ✔ | ✗ | **PARTIAL** — tools **track/coordinate** state; do NOT spawn workers | `cortex_spawn_mission/submit_partition/dispatch_mission/submit_merge` `mcp.py:1129–1330`; MISSION_TRACK workers "run their own charts in parallel" `state_engine.py:250` — but actual spawning is the client's job | — |
| agent_runner (single executor) | director-cascade plan | ✔ | ~ | ✔ | ✗ | **PARTIAL** — real single-task executor (subprocess tool exec + live `qwen_complete`), **no parallel-worker spawner** | `agent_runner.py:719` run_task; `qwen_complete` `:1023`; no spawn/ThreadPool fn | — |
| Director cascade (cheap→expensive routing) | director-cascade-plan-2026-07-10 | ✔ | ~ | ✔ | ~ | **PARTIAL** — real 4-tier router (rules→embedding→centroid→LLM) | `director.py:1–30`; trains from `ops-local/routing-log.jsonl` | needs live routing-log data to leave seed-bootstrap |
| Bake-off / A-B-C ablation rig | bakeoff-on-state-machine-2026-07-06 | ✔ | ~ | ✔ | ~ | **PARTIAL** — runs on the state machine w/ pluggable subject; drove a **real free model** (director ablation) | `bakeoff.py:1`; ab_cortex_scaffold `runner.py` | powered N, harder tasks, weaker models (GAP-CLOSURE A1) |
| phase_runtime (leases/heartbeat/resume) | mcp.py forced pipeline step 4 | ✔ | ✔ | ✔ | ✗ | **WORKS** | `phase_runtime.py`; MCP `cortex_phase_*` `mcp.py:1004–1110`; `test_phase_runtime.py` | — |
| task_ledger (peer coordination) | GAP-CORTEX-0016 | ✔ | ✔ | ✔ | ✗ | **WORKS** | `task_ledger.py:1` append-only JSONL + O_EXCL claim lock; MCP `cortex_tasks_*` | — |
| Strong-driver + parallel-cheap-workers | wrapper MULTIAGENT.md | ✗ | ✗ | ✗ | ✗ | **ASPIRATIONAL** — pattern documented; **no code spawns/parallelizes**; left entirely to the client | `MULTIAGENT.md:22` "does NOT spawn or parallelize agents by itself"; `models.tiers.md` tier table only | build (or the client owns it) |

### Verified write path / gates / provenance / infra plane

| Capability | Purpose | B | W | T | M | STATUS | Evidence | To close |
|---|---|---|---|---|---|---|---|---|
| Contract + write gates | ROADMAP Ph4; PHASE-GATES 4.1–4.2 | ✔ | ~ | ✔ | ✗ | **GAP** — **all coercion gates DEFAULT OFF** | contract `mcp.py:1612`; `_contract_gate_on` default 0 `:285`; `_forced_pipeline_on` 0 `:105`; `_mandatory_state_machine_on` 0 `:279`; `_admin_gate_on` 0 `:294`. Only ON: scope/visual review gates `:829,838` | out-of-box there is **no mandatory write gating**; enabling needs env flags |
| Per-tenant keys / scopes | ROADMAP §10; CORTEX-ROUTES | ✔ | ✔ | ✔ | n/a | **WORKS** | `keys.py:54` issue_key (SHA-256-only, scopes read/tenant_write); MCP `cortex_issue_key/rotate/revoke`; CLI `cortex-key` | — |
| Plane A (remote brain read) vs Plane B (local write) split | wrapper README; GAP-CORTEX-0015 | ✔ | ✔ | ✔ | n/a | **WORKS** (when `CORTEX_BRAIN_WORKSPACE` set) | `_read_ws mcp.py:143` / `_write_ws :194` / `_dual_plane :173`; tenant can't escape via `workspace=` | unset ⇒ single-plane (no split) |
| Per-tenant no-log / consent / DO_NOT_TRACK | GAP-CLOSURE G6/H2 | ✗ | ✗ | ✗ | ✗ | **UNBUILT** | `grep DO_NOT_TRACK / CORTEX_DATA_CAPTURE cortex_core/` = **0 matches**; `_log_event` logs every call `mcp.py:343`; consent is owner-mediated prose only (`DATA-USE.md`) | server-honored no-log flag at key issuance + deletion path |
| HTTP / streamable transport | ROADMAP §10 | ✔ | ✔ | ✔ | ✗ | **WORKS** (single-tenant served) | `http_server.py:117` uvicorn + FastMCP; bearer middleware `:52`; `cortex-mcp-http` | hardened multi-tenant writes explicitly not here `:35` |
| Telemetry — R2 mirror + Langfuse | ROADMAP §8; PHASE-GATES 6.2 | ✔ | ~ | ✔ | ✗ | **PARTIAL** — real code, **unconfigured by default** (no-op unless env creds) | `telemetry.py` SigV4 R2 sink; `langfuse_sink.py` HTTP ingest; gated on `CORTEX_TELEMETRY_S3_*` / `LANGFUSE_*` | set creds + show a real object/trace landing |
| CoT / reasoning-trace capture | ROADMAP §8; cot-trace-capture doc | ✔ | ~ | ✔ | ✗ | **PARTIAL** — `cot` field populated only where a caller supplies it (eval harness); `capture_build` defaults cot="" | `trace_capture.py:26`; wired from `vague_build.py:301` + `generate_live.py` | confirm non-empty cot from a real builder run |
| Receipts / provenance | ROADMAP Appendix (NabaOS); PHASE-GATES Ph8 | ✔ | ~ | ✔ | ✗ | **PARTIAL** — SHA-256 (not HMAC) digest receipts, fail-closed, but **state-machine consumer unwired to any entrypoint** (Phase-8 trigger-gated) | `receipts.py:161` smoke-verdict; consumer `hybrid_build.run_chunk` has no CLI/MCP caller | wire to a shipped entrypoint |
| OpenTelemetry export | ROADMAP §8; PHASE-GATES 3.5/6.2 | ✗ | ✗ | ✗ | ✗ | **UNBUILT** | `grep opentelemetry\|otlp cortex_core/` = **0 matches**; langfuse_sink explicitly "NO OTLP dep" | instrument + OTLP exporter |
| Onboarding / guided flow / next_actions | ROADMAP §10 | ✔ | ✔ | ✔ | ✗ | **WORKS** | `mcp.py:494` _next_actions; `cortex_onboarding` `:618`; forced-pipeline steps `:88` | — |

### Delivery — the wrapper scaffold (`d:\claude\cortex-agent-wrapper\`)

| Capability | B | W | T | M | STATUS | Evidence |
|---|---|---|---|---|---|---|
| Read-folder scaffold (L0/L1/L2, detection-over-coercion) | ✔ | ✔ | ✔ | ~ | **WORKS** (stdlib, zero-install) | `.cortex/START-HERE.md`; scorer/scribe/receipts `.cortex/scripts/*`; L0 free, L1 Stop-hook, L2 MCP |
| Scribe (transcript→closeout) | ✔ | ✔ | ✔ | n/a | **WORKS** | `.cortex/scripts/scribe.py`; emits run-bound `event_digest`+`tests_passed` (schema fix, GAP-CLOSURE F1) |
| 9router tier table (strong/medium/weak) | ✔ | ✔ | ~ | n/a | **WORKS** | `models.tiers.md` (358 models tiered, 8 responding); `ninerouter_tiers.py --probe` |
| Multi-agent driver+workers pattern | ✗ | ✗ | ✗ | ✗ | **ASPIRATIONAL** | `MULTIAGENT.md` — doc only; the wrapper does **not** spawn/parallelize |
| Update channel / versioning | ✗ | — | — | — | **UNBUILT** (GAP-CLOSURE H1) | vendored-and-forgotten; no `cortex update` |
| Consent surface (DO_NOT_TRACK, key expiry) | ~ | — | — | — | **PARTIAL/GAP** (GAP-CLOSURE H2) | `DATA-USE.md` opt-out default, but owner-mediated; no client-side enforcement |

---

## PART 2 — Collaborator use-case coverage

His real need has **two halves**. The A/B/C testing so far (the `kurzweil_ocr_tts`
milestone) exercises a thin slice of half #1 and **nothing of half #2**.

### Half #1 — reverse-engineer Kurzweil 3000 (OCR→TTS + understanding the software)

| Sub-need | Serving capability | Ready? | Gap |
|---|---|---|---|
| Understand K3000 feature set | deep research (`research.py`) + corpus | **~READY** | did one real research pass (`ab_cortex_scaffold/research/kurzweil-3000-and-open-components.md`) |
| OCR scanned page → text | *(none in Cortex)* | **NOT SERVED** | real Tesseract/OCR **not wired**; only a deterministic *evaluator* + fixtures (`kurzweil_checks.py`, PREREGISTRATION.md "Real…OCR…are not wired") |
| Text → TTS audio + word-timing | *(none in Cortex)* | **NOT SERVED** | edge-tts/Chatterbox named but not integrated into the harness |
| Grade the OCR→TTS pipeline output | objective checker (`kurzweil_checks.py`) | **READY** | deterministic ocr_accuracy/audio/timing/note checks exist + fixtures |
| Shape the build (research→SDD→TDD) | state machine + contract + wrapper protocol | **~READY** | discipline exists; enforcement default-off |

**Half #1 verdict:** Cortex serves the *shaping and grading* of the K3000 rebuild, not
the *doing* — no OCR/TTS engine is in-repo. The A/B/C run proved the deterministic
oracle, on a stand-in ground-truth text file, not a real scanned image.

### Half #2 — organize a huge chain of projects + scattered data + retrieval over it (Plane B)

| Sub-need | Serving capability | Ready? | Gap |
|---|---|---|---|
| Index his own large/scattered corpus locally | Plane B (`cortex_core` workspace-parameterized) | **READY** | genuinely runs over an arbitrary corpus (`config.resolve_workspace`) |
| Single-hop retrieval over it (BM25+vector) | hybrid search | **READY** | works; but only measured on *this* repo's tiny corpus, never a big scattered one |
| **Multi-hop / relational retrieval (living ontology)** | ontology | **READY (G2 closed 2026-07-15)** | ontology-expansion leg **fused into retrieval RRF** (`search.py:672-884`), config-gated per workspace; **measured** win on scattered corpus (nDCG 0.077→0.779) and net-positive on our dense corpus (recall +0.200, nDCG +0.020); ceiling is edge quality on a real scattered corpus |
| Organize a chain of projects | wrapper `projects/<slug>/` scaffold | **~READY** | folder convention exists; not an automated organizer/importer of pre-existing scattered data |
| Keep it fresh as data changes | freshness/bi-temporal | **NOT READY** | full-rebuild only; no incremental per-doc, no validity windows |
| Strong-driver + parallel cheap workers on 9router | tier table + MULTIAGENT pattern | **NOT READY** | pattern documented, **no orchestration code**; parallelization is the client's job |

**Half #2 verdict:** This is **Plane B territory and it is the least-served half.** The
core engine runs locally over his corpus (good), but the two features that make "organize
+ retrieve over a huge scattered corpus" actually valuable — the **living ontology fused
into multi-hop retrieval** and **freshness/incremental reindex** — are built-but-unwired
or unbuilt, and the parallel-worker fan-out he wants is a doc, not code.

### What fraction of his real need is actually served today?

**Roughly one-third.** Grading/shaping infrastructure is real (objective lanes, deterministic
K3000 oracle, discipline scaffold, local single-hop search). The *doing* of half #1 (OCR/TTS)
and the *high-value core* of half #2 (multi-hop ontology retrieval + freshness + parallel
workers) are not served. The A/B/C evidence to date (n=3, one easy OCR-shaped task, one model)
tests neither half's hard part.

---

## PART 3 — What was MISSED (newly surfaced, adversarial)

Items no prior sweep (brain-fitness §G, delivery §H) or design doc flagged:

1. **Lane-catalog drift is worse than the fact-check said.** Prior fact-check flagged
   "3,528 vs 131." Disk truth today: **63 objective lanes / 4,551 hard_gold rows** vs the
   catalog's **29 lanes / 5,804 records**. So there are **~34 built lanes not in the promoted
   catalog at all**, and the total record count is *lower* on disk than claimed. The trust
   surface (the catalog) misrepresents the built surface in both directions.

2. **`promotion_decisions/` has 0 per-lane promotion files** — the "promotion ledger"
   is 3 aggregate JSONLs, not a per-lane decision trail. The promotion gate that CLAUDE.md
   leans on as the audit spine is thinner than described.

3. **The forced pipeline advertises a workflow the server cannot run.** `cortex_register`
   hands every agent an 8-step MANDATORY pipeline (`mcp.py:88`) whose steps 5–7 ("strong
   model writes tests", "cheap model implements", "big model reviews") are explicitly "the
   client's to orchestrate." The server *presents* multi-model orchestration it has no code
   to perform — a documentation/capability mismatch a weak model will trip on.

4. **39/40 MCP tools ship even when their enforcement is off.** The four coercion gates
   default OFF, yet all the gate-support tools (contract, run_start/step, mission suite)
   remain in the tool list — schema token-bloat (GAP-CLOSURE G5) *plus* an agent sees tools
   whose enforcement is inert, inviting no-op tool loops.

5. **`response_bias` / `format_fairness` / `bias.py` cluster is unmentioned anywhere in
   ROADMAP/PHASE-GATES.** There's a whole shipped MCP surface (`cortex-response-bias-mcp`,
   `response_bias_mcp.py`, `format_fairness.py`, `bias.py`) with a test (`test_response_bias.py`)
   that no plan doc accounts for — orphan capability, provenance/purpose UNVERIFIED.

6. **No capability serves "import/ingest a pre-existing scattered data pile."** Every
   ingestion path is single-URL `cortex_fetch_doc` or manual doc drop. The collaborator's
   half #2 explicitly needs bulk-ingest of *existing* disorganized data; there is no
   directory-crawler / bulk importer / dedup-on-ingest. This is the single biggest unbuilt
   thing for his actual use case and no sweep named it.

7. **Anti-distillation is asserted structurally but never exercised on real data.** The
   guard (`distill.py`) protects against proprietary content entering a trainable export
   *that does not exist yet* (no importer, stub id lists). The recent commit "drop Claude
   from distill CoT export" hardened a guard on a path with no live producer/consumer — a
   compliance control over an empty pipe.

8. **CoT capture defaults to empty for the real builder.** `capture_build` (the live
   `cortex-build` path) passes `cot=""` unless a caller supplies it; only the eval harness
   populates it. The "CoT→oracle loop" the collaborator delivery wants (GAP-CLOSURE F3) has
   no live CoT source feeding it.

9. **Receipts are fail-closed and well-built but unreachable.** `hybrid_build.run_chunk` —
   the state-machine consumer of the receipt-gated SMOKE verdict — has no CLI/MCP entrypoint.
   A genuinely good provenance mechanism is dead code from a shipping standpoint.

10. **Two vocabularies risk re-emerging.** `contract.TASK_TYPES` is the shared taxonomy, but
    the 63 eval lanes, the director cascade routes, and the app_build track each carry their
    own category systems with no cross-map — the "one ≤10 vocabulary" gate (5.3) holds for
    contracts/patterns only, not the eval or routing planes.

---

## Top ~10 GAP/UNBUILT rows (most important first)

1. ~~**Living ontology NOT fused into retrieval**~~ **CLOSED 2026-07-15 (G2).** The
   ontology-expansion leg is now fused into the retrieval RRF (`_ontology_leg` +
   `_search_fused`, `search.py:672-884`), config-gated per workspace, and **measured**:
   scattered corpus nDCG 0.077→0.779 / recall 0.20→1.00; our own dense corpus re-measured
   net-positive (recall +0.200, nDCG +0.020, mrr +0.117, single-hop no-op) so it is turned
   **ON for this corpus** via `docs/ontology/retrieval.yaml`. Global default stays OFF.
2. **Multi-model arbitration UNBUILT in code** — design docs only; `grep arbitrat` = 0.
3. **All write-enforcement gates DEFAULT OFF** — no mandatory verified-write path out of box.
4. **Self-learning flywheel not auto-wired** — `promote_candidates` has no non-CLI caller;
   closeouts/gate-failures feed nothing automatically.
5. **Per-tenant consent / no-log / DO_NOT_TRACK UNBUILT** — `_log_event` logs every call;
   consent is owner-mediated prose (privacy exposure for a real collaborator).
6. **OpenTelemetry UNBUILT** — 0 imports; the whole scorecard cost/latency plane is null.
7. **Bulk-ingest of scattered data UNBUILT** (newly surfaced) — no directory crawler /
   dedup-on-ingest; blocks half #2's "organize a huge disorganized pile."
8. **Strong-driver + parallel-cheap-worker orchestration ASPIRATIONAL** — pattern doc only,
   no spawning code anywhere; the collaborator's stated 9router setup is unbuilt.
9. **Freshness / bi-temporal / incremental reindex PARTIAL** — smart detect, dumb full
   rebuild, no validity windows; docs "quietly rot."
10. **Real OCR/TTS engine for K3000 NOT wired** — only the deterministic evaluator + fixtures;
    half #1's actual doing is unbuilt.

## Newly-surfaced-missed items (Part 3 headline)

- Lane-catalog drift in **both** directions (34 uncatalogued lanes; total records lower on
  disk than claimed) + **0 per-lane promotion files**.
- The forced pipeline **advertises multi-model orchestration the server can't perform**.
- An **orphan bias/fairness MCP surface** unaccounted for in any plan.
- **No bulk-ingest capability** — the literal blocker for "organize a huge scattered corpus."
- Anti-distillation guard, CoT capture, and receipts are all **well-built controls over
  pipes with no live producer or consumer** — compliance/provenance theater risk.

---

*Method note: verified via 4 parallel code-verification passes (retrieval, eval, orchestration,
infra) cross-checked against direct reads of `mcp.py`, `search.py`, `state_engine.py`,
`agent_runner.py`, `director.py`, the wrapper, and disk-level counts of `evals/objective_*`.
Numbers cited as "measured" were traced to a committed file; everything else is marked GAP/
UNBUILT/ASPIRATIONAL or UNVERIFIED.*
