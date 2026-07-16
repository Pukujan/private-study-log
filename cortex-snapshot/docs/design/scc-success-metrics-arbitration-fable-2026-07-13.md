# SCC Success-Metric Arbitration — Fable (reviewer 3 of 3)

Date: 2026-07-13. Role: independent arbiter (with Codex/GPT-5.6 and GLM),
anti-circular convention — disagree by default, cite file:line for every claim,
say plainly what is NOT operationalized.

Scope read: `evals/ab_cortex_scaffold/PREREGISTRATION.md` + `runner.py` +
`common_checks.py`, `docs/design/cortex-redesign-CORRECTED-spec.md`,
`.cortex/` wrapper (`scripts/scorer.py`, `scripts/scribe.py`,
`protocol/STATE-MACHINE.md`), `cortex_core/{state_engine,scorecard,packs,
reaction,ontology,contract,evaluator,trace_capture,keys_cli,mcp}.py`,
`docs/COMPLIANCE-ANTI-DISTILLATION.md`,
`docs/research/cot-trace-capture-2026-07-11.md`,
`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md`,
`evals/objective_research/*`.

---

## HEADLINE FINDINGS (adversarial summary first)

1. **Nothing has been validated yet.** The A/B/C harness is honestly built but
   has produced **zero real-agent data**: the only invoker that runs today is
   `StubAgentInvoker`, which "always scores as an honest FAIL"
   (`PREREGISTRATION.md:76-84`; `runner.py:70-81`). The scaffold behaviors under
   test "are not wired into this harness build" (`PREREGISTRATION.md:78-80`).
   Any claim that SCC's goals are "validated" today is false; what exists is a
   well-frozen *design of a validation*. That is the single most important
   thing for the arbitration record.
2. **Of the owner's 8 harness goals: 3 have real deterministic metrics today
   (state routing [partial], auto provenance [mechanism], docs-first
   [detection]); 2 are partially measurable (ontology/findability,
   contract/structure); 3 are aspirational with no metric and mostly no code
   (auto-scraper, tiered parallelization, runtime hallucination-catching
   outside the eval lab).** Details + citations in §1.
3. **The "~50k context wall" premise handed to this arbitration is itself
   contradicted by the corpus.** The recorded decision says the 50k figure is
   "NOT confirmed; measurement contradicts it" — one server's whole surface is
   ~12k (`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:9,47,103-104,288`).
   The constraint should be re-based on measured numbers (§3), not the folk
   figure. Holding tests to an unverified wall reproduces exactly the guessed-
   parameter failure the corpus warns about (CLAUDE.md research-first
   pre-flight; the 2026-07-12 max_tokens incident).
4. **The A/B/C metric set is necessary but not sufficient** — it measures
   *discipline presence*, not *discipline quality*, and four of the eight goals
   have no axis at all (§2). The `discipline = mean(3 rates)` aggregate
   (`runner.py:233-236`) hides which axis moved; report per-axis.
5. **The constraint axes (tokens, context, cost) are currently unmeasurable**:
   they come from `metrics.json`, which the runner expects to be
   "runner-recorded" (`common_checks.py:163-168`) but nothing writes for a real
   `CommandAgentInvoker` run. Until that wiring exists, refusal/loop counts are
   the only constraints the harness can actually enforce.

---

## §1 — SCC success-metric spec, per owner goal

Legend: **[D]** deterministic check suffices; **[O]** needs an oracle (or must
honestly abstain). Status: IMPLEMENTED / PARTIAL / NOT OPERATIONALIZED.

### G1. State routing (no skipped SEARCH/RESEARCH) — PARTIAL [D]
- **What exists:** first-research-before-first-mutation ordering, deterministic
  timestamp comparison: `check_research_cited` (`common_checks.py:58-88`),
  wrapper twin `research_first` (`.cortex/scripts/scorer.py:12`). Server-side
  the state chart starts at SEARCH_BRAIN (`cortex_core/state_engine.py:78`) and
  `phase_legal_tools` is the disclosure controller (`state_engine.py:384`);
  the wrapper's chart is `STATE-MACHINE.md:17`
  (SEARCH→RESEARCH→SDD→TDD→IMPLEMENT→VERIFY→DOC→CLOSEOUT), explicitly "not a
  gate" (`STATE-MACHINE.md:42-44`).
- **Gap:** only the SEARCH→first-mutation *pair* is checked. Nothing measures
  SDD-before-TDD, TDD-before-IMPLEMENT, VERIFY-before-DOC — all deterministic
  from transcript event types + git (test-file commits vs impl commits).
- **Proposed metric:** `phase_order_score` = normalized longest-common-
  subsequence of observed phase-typed events vs the task-typed expected chart
  (task-typing per `STATE-MACHINE.md:51-57` / `docs.map.yaml
  no_doc_task_types`). **Threshold:** score = 1.0 counts as routed; the
  per-run *skip-rate SLI* must have its pre-registered named consumer + trigger
  (`cortex-redesign-CORRECTED-spec.md:31`, the over-correction guard) — a
  metric nobody reads is WARN-fatigue by design.

### G2. Auto provenance (background-agent closeouts, not rituals) — PARTIAL [D]
- **What exists:** the transcript→scribe mechanism: `.cortex/scripts/scribe.py`
  ("transcript -> atomic per-project closeout. Replaces the ceremony", :2;
  "Exit 0 always — the scribe records, it never blocks", :209), and the
  run-binding check `event_digest == sha256(transcript.jsonl)`
  (`common_checks.py:151-157`) which makes a copy-pasted closeout
  deterministically detectable.
- **Gap 1:** in *this* repo the discipline is still hand-invoked
  (`cortex write-log`, CLAUDE.md Closeouts section, Stop-hook nudge) — the
  "written BY background agents" goal is only true inside the wrapper flow.
- **Gap 2 (bigger):** `check_closeout_written` verifies presence + schema +
  digest, **not content fidelity**. A scribe that writes "task done, tests
  passed" bound to the right digest passes. Fidelity is deterministically
  checkable: `files_changed` in the closeout ⊆ actual `git diff` set;
  `tests_passed` must equal the recorded `test_run` exit codes
  (`scorer.py:26`). Propose `closeout_fidelity` [D]. Free-prose accuracy of
  the *result narrative* is [O] — annotate later, never gate (oracle policy,
  `cortex-redesign-CORRECTED-spec.md:40-41`).
- **Threshold:** closeout coverage ≥ 95% of non-trivial runs (measured against
  git, per the closeout-coverage-vs-git SLI already named at
  `cortex_core/mcp.py:404`), fidelity mismatches = 0.

### G3. Always-updated docs, DOCS-FIRST (never guess) — PARTIAL [D]
- **What exists:** docs-updated detection — diff vs pristine seed + required
  substrings (`common_checks.py:91-113`); wrapper `docs_current` = git-diff ∩
  `docs.map.yaml` doc-targets, task-typed (`scorer.py:13`). "Docs-first"
  (agents consult before acting) is G1's research receipt.
- **Gap:** no **freshness** metric. "Always-updated" is unmeasured: nothing
  computes doc-target age vs the code it maps to. `ontology.py` has no
  staleness field (grep: only a lock stale-steal, `ontology.py:50`).
- **Proposed metric:** `doc_staleness` [D]: for every code path changed in
  window W, the mapped doc target's last git-modified date must be ≥ the code
  change (or the run's closeout must declare `no_doc_task_type`). Threshold:
  stale-doc count trending to 0; any doc target >N days behind its code is a
  digest item.

### G4. Living ontology / findability — PARTIAL [D]
- **What exists:** the ontology itself (`cortex_core/ontology.py:1-24`,
  entities/relations JSONL + `schema.yaml`, exposed as
  `cortex_ontology_query`, `onboarding.py:68`), and — the strongest
  findability instrument in the repo — the graded chunk-level retrieval eval
  that actually gated Phase 2 ship/no-ship decisions (`cortex-graded-eval`,
  `cortex_core/graded_eval.py`; CLAUDE.md Phase 2: shipped RRF, rejected KP
  miner + reranker on measurement).
- **Gap:** nothing ties *new work* to findability. Findability of the corpus
  as-of-Phase-2 is proven; findability of what agents add every day is not.
- **Proposed metrics [D]:** (a) `orphan_rate`: fraction of docs/decisions
  merged in window W that `cortex_search --hybrid` fails to return top-k for
  their own canonical title/decision query — pure retrieval, no judge;
  (b) `ontology_lag`: entities named in merged closeouts absent from
  `docs/ontology/entities.jsonl` after N days (string-match against the
  schema's entity types). Thresholds: orphan_rate < 5%, lag trending down.

### G5. Contract + file/folder structure — PARTIAL [D]
- **What exists:** the approach contract + shared taxonomy
  (`cortex_core/contract.py:33` TASK_TYPES; validation at :135-136), the
  wrapper's project template (`.cortex/projects/_TEMPLATE/` with
  docs/audit/decisions/tests/research + `docs.map.yaml`), and the write-gate
  (`_contract_gate`, CLAUDE.md Phase 4.2).
- **Gap:** no placement linter. The "research dumps go in `research/`, never
  `docs/`" lesson (CLAUDE.md, fable-sources pollution) is prose, not a check.
- **Proposed metric [D]:** `placement_violations` = files created by a run that
  land outside the template/docs.map globs for their declared type. Threshold:
  0 per run (warn-level, post-hoc — never a refusal, per the KILL list,
  `cortex-redesign-CORRECTED-spec.md:16-20`).

### G6. Auto-scraper when a resource is missing — NOT OPERATIONALIZED
- **What exists:** manual/bounded fetch (`cortex-fetch`, research pipeline
  bounded fetch + cite-check with UNANSWERED flagging, CLAUDE.md Deep research
  v0/v1) and a GroktoCrawl *design* as optional adapter
  (`cortex-redesign-CORRECTED-spec.md:38`; `.cortex/research/GROKTOCRAWL.md`).
- **Gap:** there is **no trigger loop** — no code path notices "needed resource
  missing" and fetches (repo grep for auto-fetch-on-miss: nothing but a note
  that sharpened sources are "not auto-fetched yet",
  `docs/research/verification-pass-notes-2026-07-04.md:41`).
- **Proposed metric [D]:** `resource_gap_closure_rate` = fraction of UNANSWERED
  sub-questions (already deterministically flagged by cite-check) that acquire
  a fetched+indexed source within N subsequent runs. Today this measures 0 by
  construction; that honesty is the point. The metric can exist *before* the
  feature and will prove the feature when built.

### G7. Tiered parallelization (many subagents by availability) — NOT OPERATIONALIZED
- **What exists:** practice, not code — worktree parallel agents in the eval
  lab (memory: eval-lab flywheel), the model-tier delegation *rule* (memory:
  Opus research / Haiku implementation), and the scorecard rollup that *could*
  drive routing but whose data plane is explicitly deferred: "Real data
  collection is Phase 6 scope" (`cortex_core/scorecard.py:6-9`), with an
  n≥MIN_N=10 backoff floor (`scorecard.py:78-104`).
- **Proposed metric [D once dispatch is logged]:** `dispatch_conformance` =
  fraction of subagent spawns whose tier matches the scorecard-recommended
  tier for the task_type (given the min_n floor; below floor = "no
  recommendation", conformance N/A). Plus `parallel_efficiency` = wall-clock
  vs sum-of-subagent-clock. Neither is computable today — no dispatch log
  exists. **Say it plainly: this goal is aspirational.**

### G8. Hallucination catching + eval gates — IMPLEMENTED for the lab, NOT for the runtime harness
- **What exists (strong):** the objective hard-gold lab — deterministic
  checkers, zero judges in verdict paths (CLAUDE.md Stage 2; 3,528 records);
  citation/quote/number verification with honest UNVERIFIABLE abstention
  (`evals/objective_research/citation_checker.py:12,69`); numeric
  contradiction detection, precision-scoped
  (`contradiction_checker.py:1-6,39-40`); anti-evidence-theater semantic
  relevance in the evaluator (`cortex_core/evaluator.py:22,128,163,191,244`);
  hidden holdout tests in 2B (anti-gaming); PASS-only distillation view over
  deterministic gate verdicts (`cortex_core/trace_capture.py:5-10`;
  `docs/research/cot-trace-capture-2026-07-11.md:46-49` — "ast_checker remains
  the sole, deterministic pass/fail authority").
- **Gap:** none of this runs against a *live agent's claims* during normal
  work. `evaluator.grade_claim` covers closeout evidence; nothing checks the
  factual claims an agent makes mid-run or in summaries.
- **Proposed metric:** `claim_faithfulness` — run the citation-checker class
  of verifiable claims (quote/number/citation) over closeouts + final
  summaries against the artifacts they cite; **abstain UNVERIFIABLE** on
  paraphrase (exactly the 2D policy). [D] for the verifiable class, [O] —
  honestly out of scope — for the rest. Threshold: 0 CONTRADICTED claims per
  run (hard); UNVERIFIABLE rate reported, not gated.

---

## §2 — Is the Kurzweil A/B/C sufficient?

**Coverage map** (axes from `PREREGISTRATION.md:104-110` /
`common_checks.collect_metrics:162-187`):

| Owner goal | Covered by current axes? |
|---|---|
| G1 state routing | **Partial** — `research_cited` is one pairwise ordering, not phase-order conformance |
| G2 auto provenance | **Partial** — `closeout_written` = presence+digest, no fidelity |
| G3 docs-first | **Partial** — `docs_updated` diff/substring; no freshness |
| G4 ontology/findability | **NO axis** |
| G5 contract/structure | **NO axis** (task_passes checks artifacts, not placement) |
| G6 auto-scraper | **NO axis** |
| G7 parallelization | **NO axis** (single-agent trials by design) |
| G8 hallucination | **Partial** — `task_passes` is deterministic artifact checking (good), but no claim-faithfulness axis; refusal/loop/protocol axes cover the *anti*-goal (Disease B), which is right |

**Verdict: sufficient for the question it froze** — "does detection-only
scaffolding beat vanilla and coercion on discipline without cost?" — and the
oracle policy (stdlib+subprocess only, no judge, self-tested fixtures,
`PREREGISTRATION.md:131-136`) is exactly the right convention. **Not sufficient
as a validation of the 8-goal harness thesis.** Four goals have no axis; two
axes measure presence where the goal is quality.

**Missing axes to add (all deterministic):**
1. `phase_order_score` (§1-G1, LCS vs task-typed chart).
2. `closeout_fidelity` (§1-G2: files_changed ⊆ git diff; tests_passed ==
   recorded exit codes).
3. `claim_faithfulness` (§1-G8, CONTRADICTED must be 0; UNVERIFIABLE reported).
4. `findability_probe` (§1-G4: post-run, the run's own new doc/decision must be
   retrievable top-k by its canonical query — a one-call deterministic probe).
5. `placement_violations` (§1-G5).
6. (Deferred until the features exist) `resource_gap_closure_rate`,
   `dispatch_conformance`.

**Design criticisms independent of coverage:**
- `discipline = mean(research_cited_rate, docs_updated_rate,
  closeout_written_rate)` (`runner.py:233-236`) equal-weights three
  presence-checks and can mask a regression in one behind gains in another.
  Keep the ship rule (it's frozen), but the SUMMARY must report per-axis
  deltas; a B win driven solely by closeout presence is not "discipline."
- **N=5 default** (`PREREGISTRATION.md:88`) has no power justification
  anywhere in the preregistration. With binary axes, 5 paired trials can only
  detect near-total separations. Pre-register a minimum N per claimed delta
  before the first real report, or the first SUMMARY.md will overclaim.
- **The constraint axes are unwired:** `tokens_total`, `cost_usd`,
  `context_*` come from `metrics.json` (`common_checks.py:167-168`) which no
  real invoker writes yet. The ship rule's cost clause
  (`PREREGISTRATION.md:121`) is thus currently unevaluable. This must be
  wired before any B-vs-A verdict is issued.
- Honest credit: randomized arm order with fixed seed, fresh worktree per
  trial, pluggable invoker, advisory-only ship rule
  (`PREREGISTRATION.md:88-102,129`) are all correct experimental hygiene, and
  the "C rejected without being run if B works" rule encodes the past-learning
  reversal properly.

---

## §3 — Hard constraints the test must hold (pass/fail guardrails)

1. **Zero refusals in arm B — HARD.** Any `refusal` event in a B trial is a
   build bug in the scaffold, not a data point; the trial is invalid and B is
   mis-implemented. (B is *defined* as zero-refusal:
   `PREREGISTRATION.md:70-71`; refusal text is itself a measured bloat/loop
   contributor, `cortex-redesign-CORRECTED-spec.md:17`.) `refusal_count(B) == 0`.
2. **No governance-ritual overhead.** `protocol_only_turns(B) == 0` hard
   (closeout is scribe-written post-hoc, so B has *no legitimate* protocol-only
   turn; these counts are "the Disease-B signature we watch for in OURSELVES",
   `scorer.py:16-17`). `loop_count(B) == 0` hard.
3. **Context: measure, don't folk-number.** The "~50k wall" is recorded as
   unconfirmed/false (`ARCH-DEBUG-DECISION...2026-07-08.md:9,47,288` — one
   server ≈12k measured). Guardrails: (a) scaffold resting overhead
   `context_resting_tokens(B) − context_resting_tokens(A) ≤ 2,000 tokens`
   (the spec promises a ~250-token name-index / near-zero resting surface,
   `cortex-redesign-CORRECTED-spec.md:23`); (b) `context_peak_tokens(B) ≤
   context_peak_tokens(A) × 1.10`. Both HARD once the metrics wiring (§2)
   exists; until then no context claim may be made at all.
4. **Cost:** `mean_cost_usd(B) ≤ 1.15 × mean_cost_usd(A)` (already frozen,
   `PREREGISTRATION.md:121`) — keep.
5. **Task success non-inferiority:** `task_success_rate(B) ≥
   task_success_rate(A)` (frozen, `PREREGISTRATION.md:120-121`) — keep; a
   discipline win that costs task success is Disease B by other means.
6. **No judge anywhere in a verdict** (frozen oracle policy,
   `PREREGISTRATION.md:131-136`) — any new axis added per §2 must clear the
   same fixture self-test bar before its numbers are trusted.

---

## §4 — Local CoT/oracle loop for phantomic (READ-scoped collaborator)

**Ground truth on isolation:** it is already *cryptographically-scoped, not
politeness-scoped*. `cortex-key issue --label "phantomic" --scope read`
(`cortex_core/keys_cli.py:17`); a READ key "is read-only EVERYWHERE (defense in
depth) … must never write even in owner mode" (`cortex_core/mcp.py:449-455`).
So nothing phantomic captures locally can reach the owner's brain through the
key he holds. Egress default is "structured metrics only" behind an opt-in,
revocable consent envelope (`cortex-redesign-CORRECTED-spec.md:46-50`).

**Minimal buildable version (yes, stdlib-only):**
1. **Capture:** a `trace_capture`-lite in his `.cortex/scripts/` — the core of
   `cortex_core/trace_capture.py` is already stdlib (json/time/dataclasses/
   pathlib, :17-21); strip the R2/Langfuse mirror blocks (:62-76) and the
   `cortex_core.config` import (:43), write to HIS gitignored
   `ops-local/trace-capture.jsonl` (same FAIL-OPEN contract, :12-13). CoT comes
   from whatever his runner exposes (`reasoning_content` / visible pre-answer
   CoT, per the capture spec `docs/research/cot-trace-capture-2026-07-11.md:37`)
   — a transcript-format question, not a dependency question.
2. **Score:** he already has the offline deterministic scorer
   (`scorer.py:31` "Stdlib only. Offline. No install." — including its own
   YAML-subset parser, :61-89) and the scribe (exit-0, :209). Each run yields
   an SLI verdict locally.
3. **Local oracle generation (the loop):** a stdlib `miner.py` that joins
   `trace-capture.jsonl` × `sli.json` × git and (a) clusters `failure_class`,
   (b) for failures a deterministic check can decide (failed `test_run` +
   diff → a replayable pytest fixture into his project's `tests/`), emits a
   candidate oracle; (c) everything else → UNVERIFIABLE quarantine — the exact
   Stage-2 / data-governance rule: "a candidate failure is replayed against a
   deterministic checker; if none can decide → UNVERIFIABLE/quarantined"
   (`cortex-redesign-CORRECTED-spec.md:48`).
4. **Task improvement:** his `distillation_records()`-equivalent (PASS-only
   view over deterministic verdicts, `trace_capture.py:7-9`) is a local,
   correctness-guaranteed exemplar store for HIS models; a weekly digest .md
   from the miner is the feedback surface. Nothing leaves his machine unless
   he opts into the consent envelope.

**One anti-distillation caution to write into his wrapper README:** if any of
his executor models are proprietary (Claude/GPT), their outputs stay in his
quarantined local store and never become training targets — the same latent
risk the compliance audit flagged for the owner
(`docs/COMPLIANCE-ANTI-DISTILLATION.md:12-33,41-44`). Deterministic PASS does
not launder proprietary output into trainable data
(`cortex-redesign-CORRECTED-spec.md:49`).

---

## §5 — A "D" arm?

**Position: define D now, run it only after B produces real (non-stub) data
and beats A.** Three arms answer the frozen question (detection vs nothing vs
coercion); adding a fourth at the stub stage is arm-count theater.

**What D should be when earned: B + local-oracle feedback ("does the flywheel
turn?").** A/B/C test *static* scaffolding; no arm tests SCC's actual thesis —
that detection SLIs, fed back, change behavior over time (the named-consumer
requirement, `cortex-redesign-CORRECTED-spec.md:31`). D = the B scaffold plus
the §4 miner injecting the previous trials' SLI failures into the next trial's
disclosure surface (e.g., START-HERE shows "research skipped in 2 of last 3
runs" + top failure cluster).

- **Measurement (deterministic):** identical axes, plus a pre-registered
  within-arm trend statistic — skip-rate slope across trial index. D wins iff
  slope(D) < slope(B) (skips fall with feedback) with no task-success/cost
  regression under the same §3 guardrails.
- **Pre-registered caveat:** D deliberately breaks trial independence
  (feedback couples trial i to i+1), so it needs its own larger N and must be
  reported as a trend, never pooled with B's i.i.d. rates.
- **Reject the alternative D candidates:** "D = coercion-lite" re-tests the
  loser hypothesis the spec already bounds ("if B fails, first test ONE
  bounded Stop-check", `PREREGISTRATION.md:123-124` — that is a B-variant, not
  a new arm); "D = different model tier" varies two things at once and belongs
  in a separate scorecard experiment (G7), not this preregistration.

---

## Arbitration bottom line

SCC's *conventions* are genuinely strong — deterministic oracles, frozen
preregistration, honest quarantine, anti-coercion learned the hard way. But as
of 2026-07-13 the harness goal-set is **~40% measured, ~35% partially measured,
~25% purely aspirational**, the A/B/C run has produced **no real-agent
evidence**, its constraint axes are **unwired**, and one of the constraints
handed to this very arbitration (the 50k wall) is contradicted by the corpus's
own recorded measurement. The right next moves, in order: (1) wire
`metrics.json` recording into `CommandAgentInvoker`, (2) add the
`closeout_fidelity` + `phase_order_score` + `findability_probe` axes with
fixtures, (3) run B vs A for real at a power-justified N, (4) only then argue
about D.
