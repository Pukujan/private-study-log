# Cortex harness — consolidated scorecard: everything we've learned, all metrics, over time

**Date:** 2026-07-13 · **Purpose:** the single file that consolidates the results, metrics, and decisions scattered across `evals/reports/` (19 lane reports), `evals/promotion_decisions/` (ledgers), `calibration/`, `docs/PHASE-GATES.md`, and the design/arbitration docs — plus an honest **measured-vs-aspirational** status and the **improvement-over-time decision log**. Maintained by hand until the live scorecard data plane exists (Phase 6, OTel — `cortex_core/scorecard.py` proves the *mechanism* on synthetic data only today).

> **Read this with the arbitration in mind:** an independent Fable + Codex arbitration (2026-07-13, `docs/design/scc-success-metrics-arbitration-*.md`) converged that SCC is *"mostly aspirational, not yet measurable"* as a harness. This scorecard separates **measured-with-real-evidence** from **defined-but-unmeasured** from **aspirational.**

> ### ⚠️ FACT-CHECK CORRECTIONS (this doc was adversarially audited against primary artifacts 2026-07-13 and was WRONG in several places — corrections applied inline, but read these first)
> - **Provenance is mostly hand-written.** The retrieval numbers, the 99.93% cross-val, the ragtruth 0.429, and the scope-pack 96.1% live **only in prose markdown — there is no committed results ledger** for them. Machine-generated artifacts exist only for the objective/BFCL lanes (`hard_gold*.jsonl`, `promotion_decisions/*.jsonl`, `live_manifest.json`) and calibration (`calibration/results/*.json`).
> - **`3,528` is a frozen ledger snapshot that does NOT match the committed files** (on-disk `hard_gold.jsonl`=131, live=381, and the catalog now reports **5,804 records / 29 lanes**). Not reproducible from disk.
> - **`48` frozen tests actually tallies to 41** across the checker test files. **`n≈20`** graded queries is **17**. **nDCG** disagrees across primaries (`PHASE-GATES.md` 0.444→0.654 vs `EVAL-DESIGN` 0.462→0.650). **chunk_recall 0.733** is a *two-step* result (raw 0.467→0.667, then the distance-threshold fix 0.667→0.733).
> - **Live tool-calling per-model rates were wrong** (GLM=0.833 not 0.92; qwen35b=0.917; no gemma/DeepSeek pair in the manifest).
> - **The "A/B/C is stub-only" claim was over-broad:** the *director ablation* (`bakeoff.py`) drove a **real** free model; only the *ab_cortex_scaffold* rig had used the stub — and that rig has now run a **real** fresh-Hermes A/B/C (see §3.5).

---

## 1. The improvement-over-time decision log (what the gates actually decided)

Cortex's real signal is not a single metric climbing — it's **gated decisions where the eval said no.** In chronological order:

| When | Change tested | Metric that decided | Decision |
|---|---|---|---|
| 2026-07-04 | Retrieval 2.3 BM25+**vector RRF hybrid** | graded eval: nDCG@5 **0.462→0.650**, chunk_recall@5 **0.467→0.733** (after `MAX_VECTOR_DISTANCE=1.10` fixed a negatives regression) | **SHIPPED** default-on |
| 2026-07-04 | Retrieval 2.5 chunking v2 | graded eval: **byte-identical** to v1 | **REJECTED** |
| 2026-07-04 | Retrieval 2.6 ms-marco reranker | graded eval: full rerank **hurt** recall 0.733→0.600 | **REJECTED**, reverted |
| 2026-07-04 | Judge rubric **v1→v2** | Cohen's κ: Haiku/Sonnet **0.61→0.92**, GLM 0.49→0.70, **4B regressed 0.60→0.52** | Rubric kept; lesson: *match rubric complexity to judge capacity* |
| 2026-07-05 | Stage-2 objective lanes | 3,528 hard-gold, 48 frozen tests, 2A cross-validates BFCL at **99.93%**; judge-consensus gold minted **0** (objective replaces panel) | **SHIPPED** (lab) |
| 2026-07-12 | JSON-schema real-suite triple-check | 390/390 agree; found+fixed **3 real oracle bugs** (incl. boolean-equality gold-poisoning) | oracle hardened |
| 2026-07-12 | ragtruth backend | agreed with humans only 0.429 < 0.744 baseline | **REJECTED** (would ship biased gold) |
| 2026-07-12 | A/B/C director ablation | search lever works; a bad `max_tokens=300` param manufactured a false `coerce≈4/7` "done ≠ task-success" finding — **the corrected authoritative run (12k-token floor) has coerce=0** | false finding retracted; research-first-on-params became a rule |

**The pattern that improved over time:** the eval learned to say *no* — two retrieval changes rejected, a whole lane (ragtruth) rejected, a rubric that helped big judges but hurt small ones, gold-poisoning caught. A gate that never rejects is a rubber stamp; ours rejects.

## 2. Quantitative results, consolidated (with sources)

**Retrieval (graded eval, `tests/graded_queries.yaml` — flagged DRAFT/agent-authored/pending-human-review):** nDCG@5 **0.462→0.650** (`PHASE-GATES.md` records **0.444→0.654** for the same run — a cross-doc inconsistency) · chunk_recall@5 raw **0.467→0.667**, then the distance-threshold fix **0.667→0.733** (two-step, not raw A/B) · chunk_mrr 0.425→0.503 · context_precision@5 0.147→0.213. **Honest n=17** (not ≈20) → a one-query swing ≈6%; trust trends over single numbers. **No committed results ledger — these live only in prose markdown.**

**Calibration (`calibration/`):** Cohen's κ vs Fable-Max anchor; rubric v2 lifted capable judges to κ≈0.92, regressed the 4B; a vendor-independent **punt bias** (weak judges over-use `unverifiable`); the earlier "no family bias" claim **retracted as confounded** (gold + rubric both Anthropic-authored). Family-bias remains **unresolved** pending objective third-party gold.

**Objective lanes (`evals/reports/STAGE2_SUMMARY.md`):** the Stage-2 *ledger snapshot* records **3,528** hard-gold / 5 lanes (93% is BFCL-synthetic tool-calling; the catalog has since grown to **5,804 records / 29 lanes**, `OBJECTIVE-GOLD-CATALOG.md`) · **~41** frozen checker tests (the "48" is summary prose; the test files tally to 41) · 9 honest quarantines · **zero judges in any verdict path** (confirmed in the ledger, `"judge_in_verdict_path": false`) · 2A local checker vs BFCL ast_checker agree **99.93%** (report-level — the full 3,045-row comparison is not a committed ledger).

**Bad-oracle catches:** boolean-equality gold-poisoning (type-aware `_json_equal`), mutation/perturbation `*_ineffective` quarantines firing, ragtruth rejection, the `max_tokens=300` vs recorded `12000` floor.

## 3. Goal → metric status (the honest scorecard — from the Fable+Codex arbitration)

| SCC goal | Status | Evidence / gap |
|---|---|---|
| Docs-first / never-guess | **PARTIAL (measured)** | A/B/C `research_cited` checks search-before-first-mutation (`common_checks.py:58-88`); no freshness check |
| Auto provenance (bg-agent audit) | **PARTIAL (measured)** | scribe + digest-binding exist; **no content-fidelity check** (a boilerplate closeout bound to the right digest passes) |
| State routing | **PARTIAL** | only search-before-mutation; **no SDD/TDD/VERIFY ordering** metric |
| Ontology / findability | **PARTIAL** | Phase-2 findability proven for the *corpus*; **nothing measures findability of new work** |
| Contract + file/folder | **PARTIAL** | scaffold provides it; **no placement linter / structure metric** |
| Hallucination catching / eval gates | **PARTIAL (lab only)** | strong in the lab (objective lanes, anti-evidence-theater, faithfulness); **not a runtime axis** |
| Auto-scraper if missing | **ASPIRATIONAL** | deep-research/fetch exists; **no auto-acquire-on-miss metric** |
| Tiered parallelization (subagents) | **ASPIRATIONAL** | **no code, no metric** |
| **Overall harness validated?** | **PARTIAL (first real evidence 2026-07-13)** | The first **real** fresh-Hermes A/B/C ran on gravebuster (§3.5): **B/detection-only ships over A/vanilla** (discipline 1.000 vs 0.667, 0 refusals) — but n=3, one easy task, and the entire B>A lift is the auto-closeout artifact (base model already researches-first). Cost/context axes still unwired; ~40% of goals measured, ~25% aspirational. |

### 3.5 First REAL A/B/C (gravebuster, fresh isolated Hermes, 2026-07-13)

`evals/reports/gravebuster-abc-2026-07-13/REPORT.md` (captured from the sandbox run). Fresh `hermes-agent 0.18.0` in a bare python:3.12 container, **real Hermes drove every trial** (not the stub), deterministic evaluator, no judge, Hades untouched.

| arm | task | research | docs | closeout | discipline | refusals/loops |
|---|---|---|---|---|---|---|
| A (vanilla) | 1.00 | 1.00 | 1.00 | 0.00 | **0.667** | 0 / 0 |
| B (detection-only) | 1.00 | 1.00 | 1.00 | 1.00 | **1.000** | 0 / 0 |

**Verdict: SHIP B** (discipline B>A, task/cost equal, zero coercion). **Honest caveats (from the run):** (1) the B>A delta is *entirely* the run-bound auto-closeout — base Hermes already researches-first and writes docs unprompted, so `research_cited`/`docs_updated` did **not** discriminate; B "wins" by adding an audit artifact, not by changing behavior. (2) **n=3, one easy single-page task, one driver model** — not a strong statistical claim. (3) **C (cortex+brain) blocked-on-key** — not run (B cleared the bar so C is rejected anyway). (4) OpenRouter lane was credit-dead; a second *paid* cross-vendor lane couldn't be exercised. (5) A **real schema bug** surfaced: the wrapper's `scribe.py` emits closeout `schema_version 3`, which the ab_cortex evaluator rejects (needs `tests_passed`+`event_digest`) — bridged with an adapter, flagged for a real fix. (6) `aux` confirmed the 12k-token floor live.

**Missing deterministic axes the arbitration prescribed** (to be preregistered before any ship claim): `phase_order_score`, `closeout_fidelity` (files-changed vs git diff; tests-passed vs recorded exit codes), `claim_faithfulness` (citation-checker, UNVERIFIABLE abstains), `findability_probe`, `placement_violations`.

**Hard guardrails (corrected by the arbitration):** `refusal_count(B)==0` HARD · `loop_count==0`, `protocol_only_turns==0` HARD · B resting-context ≤ 2k over A, peak ≤ 1.10×A (**NOTE:** the old "50k context wall" is recorded as **unconfirmed/false** — measured ~12k/server, `docs/ARCH-DEBUG-DECISION-...-2026-07-08.md`).

## 4. Where all the source data lives (the index this file rolls up)

- `docs/PHASE-GATES.md` — authoritative gate tracker (per-gate cited evidence).
- `evals/reports/*.md` — 19 per-lane reports (regex/sql/datetime/coding/security/research/architecture/…).
- `evals/promotion_decisions/*.jsonl` — promotion + quarantine ledgers.
- `calibration/CALIBRATION-REPORT-*.md`, `CAPSTONE-*.md` — κ + bias + rubric-versioning.
- `docs/research/harness-engineering-oracle-strengthening-2026-07-13.md` — the harness-engineering evidence map.
- `docs/design/scc-success-metrics-arbitration-{fable,codex}-2026-07-13.md` — the goal→metric arbitration.
- `docs/design/multi-model-arbitration-in-cortex-2026-07-13.md` — the arbitration-protocol design.

## 5. How to make this a LIVE dashboard (not hand-maintained)

The schema already exists (`cortex_core/scorecard.py` + `docs/SLI-SCORECARD-SCHEMA.md`, hierarchical-backoff rollup); only the **data plane** is missing (Phase 6, OTel). The build: (1) each eval run appends a typed result row to a single `evals/results.jsonl` (run_id, lane, metric, value, ts, decision); (2) a stdlib rollup renders this file's §1–§3 tables from that ledger; (3) an optional OTel/Langfuse export feeds an over-time chart. Until then, **this file is the dashboard** and is updated when a gate decides.

---

**Bottom line:** we have rich, honest, *scattered* evidence and a real decision-log of gates that said no — but **not** a live consolidated dashboard, and **not** a validated 8-goal harness. This file consolidates the former; the arbitration docs define what must be measured to earn the latter.
