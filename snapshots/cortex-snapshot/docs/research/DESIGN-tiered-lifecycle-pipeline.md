# DESIGN: Model-tier + reasoning-effort routed lifecycle pipeline

Date: 2026-07-06. Design-tier synthesis artifact. Integrates the six+ 2026-07-06 research
reports with the user's routing refinement into one design. **Reconciles into** (does not
duplicate) `docs/SERVER-DRIVEN-PIPELINE.md` / GAP-CORTEX-0020 (state machine) and
GAP-CORTEX-0021 (capacity routing) — §7 lists exactly what changes in each.

**The lifecycle (user spec):** research → design → phase-based plan → TDD success
conditions → implementation → review → **actual test run by lower-tier models through the
non-admin route** → capture + log → plan changes → improve and iterate.

**The core constraint (user refinement — the design's spine):** route BOTH model tier AND
reasoning effort per stage, optimizing **quality-per-budget across the whole pipeline**,
never raw quality of any single stage. Reasoning effort is front-loaded to the stages that
carry genuine uncertainty (research framing, design) and **dropped to floor once a stage's
output is fully specified** — downstream stages consume *specification*, not *reasoning*,
so paying for reasoning there is pure waste. Corollary that makes this non-obvious: the
routing table needs a **ceiling (`max_model_class`) as well as a floor** — the live failure
that motivated GAP-0021 was Fable burned on web-fetch subagents, i.e. a *floor-only* policy
can't prevent overspend.

---

## 1. Per-stage routing table (the policy, v1)

Tier vocabulary (maps onto the built `JUDGE_LADDER`/tier dispatch in
`cortex_core/judge.py` — the mechanism exists, this is the policy):
`frontier` (Fable-class) > `strong` (high-effort Opus-class) > `mid` (low-effort
Opus / Sonnet-class) > `small` (Haiku / GLM / Qwen-4B-class) > `micro` (cheapest
capable local model).

| # | Stage | model tier (floor–ceiling) | reasoning effort | Objective optimized | Rationale (report + finding) |
|---|---|---|---|---|---|
| 1a | research.fetch / sweep | micro–small | none | recall per dollar | GAP-0021's own live evidence (Fable on fetch legs = the bug). Corpus sweep **P3**: a 4B model hits 87% when the harness compensates — fetch/extract is maximally harness-compensable work. Deterministic byte caps + SSRF checks do the safety (guardrails report §6), not the model. |
| 1b | research.frame / decompose | mid | **high** | sub-question boundary quality | Parallel-orchestration §1: Anthropic's multi-agent system found the decomposition failure mode is *prompt-level* — vague subtask boundaries cause duplicated/misread work; each subtask needs objective + output format + tool guidance + boundaries. Framing is where uncertainty is highest per token; front-load effort here, not in the fetches it spawns. |
| 1c | research.consolidate / contradiction-check | strong | high | synthesis faithfulness | GAP-0021 rule ("strong models consolidate"); memory sweep **P12** (facts-as-objects): cascaded cheap summarization destroys 60% of facts — consolidation is where cheap tiers actively corrupt. Verified downstream by the 2D faithfulness checker (objective lane), so this is the last judgment-heavy research step. |
| 2 | design | **frontier (Fable) — floor AND ceiling** | **max** | uncertainty burn-down; decision quality | User spec: Fable = plan/design ONLY. This is the stage where genuine unknowns get resolved; every later stage merely executes its output. P18's warning applies *to its output format*, not its existence — see §2: design must emit artifacts, not advice. |
| 3 | phased plan | frontier (same Fable session as 2) | high (down from max — the unknowns are now resolved) | decomposition into a machine-consumable DAG | Corpus sweep **P23** (open-multi-agent): plan = task-DAG-as-*data* for a deterministic scheduler, frozen, replayable. Parallel-orchestration §1: subtask cards carry objective/format/boundaries + declared `path_claims` so the ledger can enforce disjointness (§2 there). The plan is `cortex_contract(phases[])` — the frozen artifact. |
| 4 | TDD success conditions | strong (high-Opus) | high | **executable spec richness** | Corpus sweep **P18**: review gains scale with spec richness (+9.8pp rich vs +2.3pp lean) — the tests are the spec-richness lever, so they get real effort. **P5** (cwc): default-FAIL criteria — every condition starts false. Rules-files report §2 (Devin playbooks): *Specifications-as-postconditions* feeding the evaluator is "the thing no surveyed product ships." Hidden holdout split authored here (Stage-2 lane 2B, built) and never leaves the server. |
| 5 | implementation | mid (low-effort Opus) | **low** | pass the visible tests at minimum cost | **P18**: weak-generate → strong-review WINS (90.2% pass@1). **P1**: 2/10→10/10 from shrinking the tool space alone — "loop structure, not model size, is the binding constraint." The reasoning is already encoded in the tests (§2); paying for reasoning here re-derives what stage 4 froze. |
| 6 | review | strong (high-Opus), **fresh context** | high | catch what tests structurally can't (design intent, security, maintainability) | **P18** strong-review is the winning half; **P19**: cross-context review works *because of separation* (F1 28.6% vs 24.6%, p=.008) — reviewer gets artifact + contract + rubric, **never the builder transcript**; **P22**: intrinsic self-correction degrades — the verdict must be external. |
| 7 | test run (non-admin route) | small–mid, **ceiling enforced** | **none** | objective verdict per dollar + live product dogfood | Zero-judgment stage: the deterministic checker decides pass/fail, never the runner (Stage-2 rule, "zero judges in any verdict path"). Letta/steering report **B2** (Taskmaster autopilot): evidence-gated phase transitions with server-validated `testResults` is the exact published shape. **P6**: 98.4% of Claude Code is deterministic infrastructure — this stage is 100% deterministic on the verdict side. Full wire design §3. |
| 8 | capture + log | same session as 7 (mechanical) | none | ledger/audit integrity | Evidence schema v2 (built) does the validation; **P5**: evidence-provenance gate — the server refuses evidence refs never touched in-session. No intelligence needed; refusing to spend any is the point. |
| 9 | plan changes / iterate triage | **routed by failure class** (§5) — server rule, not model judgment | escalating only on spec-fail | cheapest tier that can absorb the failure | Durable-execution report §1e (Temporal `non_retryable_error_types`): classify failures, route by class. **P10 + P22**: retry memory must be seeded by the *external* verdict, observer-authored. Bounded by GAP-0020's 3-attempt rule. |

**The effort curve, stated once:** effort tracks *residual uncertainty*, which is
monotone-decreasing through stages 2→5 by construction (each stage's artifact removes the
uncertainty the next stage would otherwise face). It re-enters only at stage 6 (semantic
review — tests can't see everything) and at iterate-time proportional to failure class.
The pipeline's budget shape is therefore a front-loaded spike (1b–4), a cheap flat middle
(5, 7, 8), and one paid checkpoint (6).

---

## 2. The P18 reconciliation — why executable TDD dissolves the −2.4pp

This is load-bearing, not an assumption. The argument:

**What P18 actually measured.** Strong-plans→weak-codes degrades (−2.4pp) when the handoff
artifact is **prose**. The mechanism is corpus sweep **P15** made concrete: prose is a
probabilistic artifact crossing a deterministic interface — the weak implementer must
*reconstruct the planner's reasoning* from lossy natural language, and reconstructs it
worse than it would have reasoned natively. The planner's intelligence doesn't transfer;
the ambiguity does. Meanwhile weak-generate→strong-review wins, and its gains scale with
spec richness (+9.8pp rich vs +2.3pp lean).

**The design move.** The design→implementation handoff in this pipeline is **never prose**.
Stage 4 compiles the design into *executable TDD success conditions*: a runnable visible
test suite + machine-checkable postconditions (Devin's Specifications pattern, rules-files
report §2) + a hidden server-side holdout (2B, built). The implementer receives red tests
and a bounded task: make them green without touching `wont_touch`.

**Why this dissolves the degradation.** The −2.4pp exists because the implementer must
*recover missing reasoning*. Against executable tests there is no missing reasoning to
recover: the reasoning is **encoded in the tests** — every design decision that matters is
either checkable by execution (in the suite) or out of scope (not the implementer's
problem). Interpretation is replaced by red/green. An executable test is the *richest
possible specification* — it sits past the top of P18's spec-richness axis, where review
gains were largest — so the pipeline lands on P18's winning branch twice: rich-spec
generation (stage 5 implements against maximal spec) AND strong review after (stage 6).
The losing branch's failure channel (reasoning lost in prose translation) has no medium
left to occur in.

**Two guards that keep this honest.** (a) Anti-overfit: the implementer can only game the
*visible* tests; the hidden holdout (never revealed, run by a different session — §3.6)
catches teach-to-the-test. (b) Anti-drift: `cortex_contract`'s `wont_touch` + path-claims
(parallel-orchestration §1/§2) bound what the low-effort implementer may touch, so
"cheap and correct" can't silently become "cheap and creative."

**Consequence for stage 5's routing:** low-effort mid-tier is not a compromise; it is the
*optimum*. A frontier model implementing against a frozen executable spec re-derives, at
frontier prices, decisions the spec already froze — negative marginal quality per dollar.

---

## 3. The non-admin test-run stage (stage 7) — wire-level design

**The idea:** lower-tier models execute the actual tests **through the hosted MCP's
non-admin route**. The test-runners are therefore ordinary tenant users of the served-brain
product — this stage *dogfoods the hosted product while it validates the code*. Every
friction a runner hits is product telemetry; every verdict feeds the model scorecards
(GAP-0020 step 7 LEARN).

**Reuses, unchanged:** dual-plane (H2a — runner reads brain, writes only its tenant
folder), evidence schema v2 (`validate_evidence`), task ledger + atomic claim,
`_contract_gate`, capability profiles (parallel-orchestration §5).

**Plugs into GAP-0020 as an evidence-gated phase transition** — the exact Taskmaster
autopilot pattern (Letta/steering report B2: `autopilot_complete_phase` requires
`testResults: {total, passed, failed}` and the server validates against the phase,
including the RED-expects-failure inversion).

### What crosses the wire

**Setup (server-side, at stage 4 close):** the TDD contract is persisted as data attached
to the ledger task: `tdd_contract: {suite_id, run_command, command_hash, visible_tests:
[test_id...], expected: {phase: "GREEN", total: N, min_passed: N} | {phase: "RED",
expect_failures: [test_id...]}}`. Holdout tests live brain-side, admin-only; their IDs
never appear in any tenant-visible payload.

1. **Register (non-admin):** runner calls `cortex_register` with a tenant token → session
   bound to a `small`-tier profile; `tools/list` is filtered to the runner's needs
   (claim/update/write_log/status — hiding tools beats rejecting calls, rules-files §6 +
   parallel-orchestration §5). Response carries the Swarm-style routine (OpenAI/NeMo
   report A4): numbered steps, tool named per step.
2. **Claim:** `cortex_tasks_claim(task_id)` → `{lease_id, lease_expires_at, attempt,
   journal, tdd_contract (visible part), branch, last_green_commit, resume_token,
   next_action: "run_tests", next_steps: "<imperative: checkout <branch>, run
   <run_command>, report via cortex_tasks_update>"}`. Claim is filtered by tier band
   (§4a): a frontier session claiming this task gets a structured refusal naming the
   cheaper route. (Durable-execution §4 composite: claim returns the memoized journal;
   Temporal 1a: heartbeat details.)
3. **Run (client-side):** the runner executes the suite in its own shell — the one thing
   the server structurally cannot do (parallel-orchestration §4: the server cannot enforce
   what happens outside tool calls; that boundary is *why this stage needs a client at
   all*).
4. **Report:** `cortex_tasks_update(task_id, lease_id, idempotency_key, phase="test_run",
   evidence={schema_version: 2, kind: "test_results", testResults: {suite_id, total,
   passed, failed, failures: [{test_id, message_digest}], duration_s, command_hash,
   output_digest}})`. Update-not-Signal (durable-execution 1c): validate → record → return
   synchronously; fence-checked; idempotent on retry (Restate 2c — timing-out CLIs WILL
   double-submit).
5. **Server validates (the gate):** (a) evidence schema v2; (b) `suite_id` matches the
   task's TDD contract; (c) `command_hash` matches the registered command — anti
   "ran different tests"; (d) results vs `expected`, with the RED/GREEN inversion (a RED
   phase report of all-passing is a *failure* of the gate); (e) lease/fence valid;
   (f) failure list ∩ visible_tests only (a runner reporting unknown test_ids is flagged).
   Verdict is deterministic — no judge anywhere in the path (Stage-2 rule).
6. **Holdout leg:** a *separate* ledger task, claimable only by a session ≠ the
   implementer's and ≠ the visible-run session (P19 separation, applied to execution).
   Same wire shape; the holdout suite is delivered at claim time as an opaque runnable
   bundle (test bodies, not names-in-advance), results validated identically.
7. **Transition:** PASS both legs → `next` advances the state machine (→ review if not yet
   done, else closeout). FAIL → server records the failure fingerprint, `next` = retry
   guidance with failing-test list + matching KEDB pattern if the signature is known;
   per-stage bounded counter++ (§5). Either way the runner's own closeout
   (`cortex_write_log` referencing contract_id + task_id + verdict) clears its session
   debt — the runner experiences the full tenant pipeline, which is the dogfood.

### The honest trust problem (named, mitigated, open)

The runner **self-reports** results; a hallucinating or lazy runner could fabricate
`testResults`. Mitigations layered: `command_hash` + `output_digest` (server can demand
the digest-preimage on audit / spot-check), the holdout run by a *different* session with
no incentive coupling to the implementer, structured failure lists that are hard to
fabricate *consistently* across two independent runners, and — when stakes warrant — an
N-of-M quorum of runner sessions (cheap, since runners are the cheapest tier). What this
is NOT: cryptographic attestation. Whether digest+quorum+separation suffices, or the
server eventually needs its own execution sandbox (recurring cost = user decision), is
open question #1 (§8).

---

## 4. Two enforcement surfaces

### 4a. SERVED to tenant orchestrators (Hermes) — the GAP-0021 `capacity` block, extended

The triage response (`cortex_scope_pack`, GAP-0020 piece 2) currently plans a per-task
`capacity` block. This design extends it to a **per-stage pipeline array** for any task
that triage classifies as multi-stage:

```json
"capacity": {
  "task_class": "feature-build",
  "pipeline": [
    {"stage": "research.fetch",  "min_class": "micro", "max_class": "small",   "effort": "none", "why": "harness-compensable retrieval (P3)"},
    {"stage": "design",          "min_class": "frontier", "max_class": "frontier", "effort": "max",  "why": "uncertainty burn-down; output must be artifacts (P18/P23)"},
    {"stage": "tdd_author",      "min_class": "strong", "max_class": "strong", "effort": "high", "why": "spec richness is the review lever (P18)"},
    {"stage": "implement",       "min_class": "mid",   "max_class": "mid",     "effort": "low",  "why": "reasoning already encoded in tests"},
    {"stage": "review",          "min_class": "strong", "max_class": "strong", "effort": "high", "why": "fresh-context review (P19), external verdict (P22)"},
    {"stage": "test_run",        "min_class": "small", "max_class": "mid",     "effort": "none", "why": "deterministic verdict; ceiling enforced — overspend guard"}
  ],
  "within_your_class": ["research.fetch", "test_run"],
  "advice": "decompose: hand fetch+test_run to your cheap CLIs; escalate design"
}
```

Hermes reads `pipeline` to assign sub-tasks across its CLIs. Two enforcement levels,
honestly distinguished (rules-files headline: **advisory prose never binds; only
server-side tool-boundary checks do**):
- **Advisory:** the block itself + `advice` prose. Steers cooperative orchestrators; binds
  nothing.
- **Binding:** at `tasks_claim`. `cortex_register` already collects the session's model;
  the profile carries its tier. Claim filtering (parallel-orchestration §5, built shape)
  gains a **band check both directions**: below `min_class` → hard reject with escalation
  advice ("this task needs a stronger model — decompose or escalate"); above `max_class` →
  `reject_content`-style refusal (OpenAI/NeMo A2 three-valued ladder) naming the cheaper
  route, **overridable via the escalation call** (one request, always granted, always
  logged — Phase-5 loop) so a fleet of only-strong agents can't deadlock. Every override is
  an audit event feeding the cost scorecard.

### 4b. ENFORCED in Cortex's own pipelines (deep_research, evals)

The routing table lives as **versioned config, in the corpus**:
`config/capacity_policy.yaml` — one file, schema `{stage: {min_class, max_class, effort,
rationale}}` + a tier→concrete-model map per deployment. The policy is the durable asset
(same law as rubrics — calibration finding: "the durable asset is the rubric, not the
model"; corpus sweep P2: policy as an editable doc a runtime interprets, changes are doc
edits not redeploys).

Enforcement, concretely:
1. `cortex_core/research.py` fetch legs, framing, and summarize models resolve **through
   the table** (config lookup, not hardcoded defaults). Fetch legs default to the cheapest
   configured tier; consolidation to `strong`.
2. **Loud violation log:** any pipeline step that instantiates a model above its stage's
   `max_class` emits a `CAPACITY_VIOLATION` warning line with {stage, model, policy} — the
   never-again guard for the Fable-on-fetch-legs incident. (Warn, not crash: availability
   fallback upward must stay possible, but never silent.)
3. Eval harnesses (`judge.py` dispatch, calibration runs) resolve judge tiers through the
   same table — `JUDGE_LADDER` already implements the mechanism; the table becomes its
   policy source.
4. A trivial CI/doctor check (`cortex-doctor`): grep for concrete model IDs in pipeline
   code outside the table + the tier map; flag as drift.

---

## 5. The iterate loop — failure-class routing, bounded

On any gate failure, the server (not the model — Inngest 3e: code-based router, "LLM
routing is what we're explicitly NOT depending on") classifies and routes. Failure classes
per Temporal's `non_retryable_error_types` discipline (durable-execution §1e):

| Failure class | Detected by | Who re-engages | At what effort | What's served with the retry |
|---|---|---|---|---|
| **test-fail** (visible suite) | stage-7 gate | mid-tier implementer (stage 5) | low (unchanged — the spec still holds) | failing-test list + matching KEDB pattern + Reflexion note (P10) *authored by the server from the external verdict, never self-generated* (P22) |
| **holdout-fail, visible-pass** | stage-7 holdout leg | strong tier (stage 4 author) at high effort — the visible suite under-specified; fix the *spec*, then implementer re-runs cheap | high | the holdout failure *category* (never the holdout tests themselves — anti-oracle rule) |
| **review-fail** (semantic) | stage-6 reviewer | mid-tier implementer at **medium** effort (uncertainty re-entered the system) | medium | reviewer findings verbatim as the next builder-session starting prompt (P21, verbatim recipe) |
| **spec-fail** (tests contradictory / implementer stuck 2× on the same test / architecture check fails) | repeated same-signature failure, or 2E checker | strong tier revisits TDD conditions at high effort; if the contract's `wont_touch` or the phase DAG itself is implicated → **frontier (Fable) re-entry as a plan-change** | high / max | full failure fingerprint + phase journal |

**Bounds (GAP-0020's rule, refined per-stage):** 3 distinct attempts per stage → honest-
failure closeout (`next: cortex_write_log`), failure fingerprint hashed, ≥2 cross-history
matches → KEDB pattern proposal (all existing design). New bound this design adds:
**at most ONE frontier (Fable) re-entry per task without human approval** — escalation is
the expensive direction, and an unbounded design-thrash loop is the costliest failure mode
this routing can produce. A Fable re-entry is a first-class **plan-change event**: logged
with `{trigger_verdict, design_delta, phases_invalidated}`, and it re-freezes the plan
(P23 freeze/replay — the old plan artifact is superseded, never deleted, P13).

**Every re-route logs the delta:** `{from_stage, failure_class, verdict_ref, tier_engaged,
effort, attempt}` appended to the task journal. This is the raw feed for the GAP-0021 data
loop (model scorecards → the capacity table gets refined from observed pass rates per tier
per stage — the table is seeded from research, tuned from evidence).

---

## 6. Lifecycle ↔ GAP-0020 state machine mapping (reconciliation view)

The lifecycle does not replace the 0-7 state machine; it **types the EXECUTE phases**.
Stages 1a–1c live in state 2 (KNOWLEDGE FILL); stages 2–3 produce the contract (state 3);
stages 4–7 are contract phases with `stage` + `capacity` typing inside state 4 (EXECUTE),
with stage 7 doubling as the objective half of state 5 (EVAL GATE) — the eval gate's
"coding → objective test execution" row IS stage 7, now specified to run through the
non-admin route; stage 6 (review) is the eval gate's judge-panel/fresh-context half; stage
8 is state 6 (CLOSEOUT); stage 9 is the RETRY edge + state 7 (LEARN) feed.

---

## 7. What changes where (explicit deltas)

**GAP-CORTEX-0020 (`docs/SERVER-DRIVEN-PIPELINE.md` + gap file):**
1. `cortex_contract(phases[])` schema: each phase gains `stage` (lifecycle vocabulary) and
   `capacity {min_class, max_class, effort}` fields, defaulted from the policy table by
   task_class. The `next` engine reads them to compose capacity-aware `next` blocks.
2. State 5 EVAL GATE: the coding row is now explicitly the **non-admin test-run stage**
   (§3) — evidence-gated phase transition with server-validated `testResults`, RED/GREEN
   inversion semantics, holdout-by-different-session. (Also absorbs the sweep's flagged
   gap: P19 isolation requirement stated explicitly at the gate.)
3. Bounded retry: flat 3-attempt counter becomes **per-stage counter + failure-class
   router** (§5 table) + the one-Fable-re-entry bound. Honest-failure closeout unchanged.
4. `cortex_register` session state: record model class + declared effort tier (model
   collection already planned; tier binding is new).
5. `tasks_claim`: tier band check both directions (floor hard, ceiling soft via
   escalation), per §4a.

**GAP-CORTEX-0021 (gap file):**
1. Policy table gains the **`max_model_class` ceiling** (was floor-only) and becomes
   per-**lifecycle-stage**, not per-work-class only; work classes {fetch, extract, sweep,
   summarize, frame, consolidate, judge, design, code-gen, code-review} map onto stage
   vocabulary {research.fetch, research.frame, research.consolidate, design, plan,
   tdd_author, implement, review, test_run, log, iterate_triage}.
2. `capacity` block in triage becomes the **`pipeline` array** (§4a) for multi-stage
   tasks; single-stage tasks keep the flat block.
3. Table ships as `config/capacity_policy.yaml` (versioned corpus asset) + tier→model map;
   own-pipeline enforcement per §4b including the `CAPACITY_VIOLATION` loud log and the
   doctor drift check.
4. The "next gate" in 0021 extends: the v1 policy table must be written **before the
   `next`-engine TDD contract freezes** (unchanged), and now must include the stage axis
   and ceiling so 0020's phase schema can reference it.

**No change needed:** dual-plane, write-gate, evidence schema v2, ledger claim atomicity,
escalation loop, KEDB floor — all consumed as-is.

---

## 8. Open questions for the (high-Opus) reviewer

1. **Runner-report trust (§3):** is `command_hash` + `output_digest` + different-session
   holdout + optional N-of-M quorum sufficient integrity for self-reported test results,
   or does credible verification require a server-side execution sandbox (recurring cost,
   user decision)? Related: can Railway's environment run *any* constrained execution, or
   is the quorum path the only realistic one?
2. **Ceiling enforcement semantics (§4a):** hard-reject above-`max_class` claims risks
   deadlock when only strong agents are online; the design chooses soft-reject +
   always-granted escalation + audit. Is that the right trade, and should repeated
   ceiling overrides auto-tighten anything (e.g., trigger a fleet-composition alert)
   rather than just accumulate in the log?
3. **The one-Fable-re-entry bound (§5):** is a single frontier design re-entry per task
   the right budget? What evidence must a plan-change event carry to justify burning it
   (vs. closing honest-failure and letting a human re-scope) — and does the RED/GREEN
   inversion need a third state for "spec-fail discovered during GREEN" so the counter
   attributes the attempt to stage 4 rather than stage 5?
4. (Minor) **Effort truthfulness:** `reasoning_effort` is client-controlled for models
   the server doesn't run; register can only record *declared* effort. Acceptable for v1
   (scorecards will out persistent mismatch), but worth a reviewer sanity-check.
