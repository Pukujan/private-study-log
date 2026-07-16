# E2E-1 — Success/Failure Spec for the Live Governed Pipeline Test (HARD delivery gate)

**Status:** v1 — **NOT design-frozen.** sol@xhigh (independent Codex leg)
**rejected** freeze: as written, every criterion is passable by a ceremonial
harness because nothing anchors the runtime artifacts to a trusted root. This
revision folds in that critique — it adds the missing **P0 trusted-provenance
substrate** (§1.2) that the HARD gate now depends on, fixes the concrete bugs
sol found, and tightens thresholds. **Design-freeze is BLOCKED until the §1.2
substrate is built and the residual items in §8 are closed.** Full arbitration
record + every Fable-vs-sol disagreement: §8.
**Date:** 2026-07-14
**Owner constraints (fixed):** machine-decidable only — no human sign-off may
gate a PASS; align with inspect-ai Task/Scorer/log conventions; deterministic
checkers are truth; **no LLM judge in any verdict path** (judges may annotate,
never decide — the Oracle policy); anti-evidence-theater (a scaffold,
format-adapter, or fake closeout is a FAILURE, not a pass).

This document defines the pass/fail contract for the test **before** the test
is built. The test itself (harness + verifier) must be built *to* this spec;
the verifier recomputes every criterion from primary artifacts and its exit
code is the gate.

---

## 0. What is being tested (one sentence)

A live run on gravebuster in which a real strong-model driver, given the
collaborator's actual use case (reverse-engineer Kurzweil 3000's key features
+ organize a scattered corpus into a portable, human-readable project
structure), is driven through the Cortex state machine **as enforcer**
(SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT, per
`cortex_core/state_engine.py` `BUILD_TRACK`/`MISSION_TRACK`), fans out real
parallel subagents via `spawn_mission`, has scribe agents write
machine-evidenced closeouts, a librarian agent do the corpus/audit
bookkeeping, and `cortex-self-learning` (GAP G1, `cortex_core/self_learning.py`
— commit f626951 / dfa7bd9, currently on the
`claude/corpus-agent-orchestration-review-121x1q` lineage; must be merged to
the run SHA before the test is built) mint oracle candidates from the real
outcomes.

### 0.1 Why the prior runs do NOT count (the bar this test must clear)

The gravebuster Kurzweil A/B/C runs (`~/kurzweil-abc/results/REPORT.md`,
`results-powered/REPORT.md`, 2026-07-14) were real Hermes runs but are labeled
**Scaffolded** and are explicitly NOT a pass of this gate, because:

1. **The state machine never enforced anything.** A/B/C varied only a
   *system-prompt discipline scaffold*; no `StateEngine` gated any transition.
2. **The C-arm brain call was harness-injected** ("the brain is queried by the
   trial runner and handed to the agent as a reference doc"), not an agent
   tool call.
3. **A format-adapter closeout was written** to bridge the wrapper's
   schema_version-3 scribe output to the evaluator's expected schema — the
   REPORT itself flags this as a schema-lineage mismatch. That adapter pattern
   is a named disqualifier below (F3).
4. Media was a deterministic stub (`build_media.py`), identical in every arm.
5. Fan-out was a fixed two-lane pattern wired by the runner, not a
   mission-partitioned parallel dispatch.

Every one of those five gaps maps to a hard criterion or disqualifier here.

### 0.2 Trust order this spec inherits

`docs/BUILD-PLAN.md` §1: (1) deterministic logic checks = ground truth;
(2) gateway records; (3) OTel/flight-recorder spans; (4) LLM-judge grades
never as sole evidence; (5) self-report recorded, never trusted. Every
criterion below is decided at level (1), evidenced at levels (1)–(3), and no
criterion consumes level (4) or (5) as its deciding input.

---

## 1. Definitions (so criteria are checkable, not vibes)

- **Engine DB** — the run's `StateEngine` SQLite file (`task`, `event`,
  `gate_verdict`, `claim` tables). The event log is event-sourced; `replay()`
  rebuilds `(state, seq)` from it (state_engine.py:1025-1036).
- **Flight recorder** — `logs/events.jsonl` (Phase-0 artifact) plus the MCP/
  HTTP server request log: every `cortex_search`/`cortex_fetch_doc`/advance
  tool call as received server-side.
- **Call ledger** — per-agent LLM API call records: `{actor, ts_start, ts_end,
  model, input_tokens, output_tokens, max_tokens, finish_reason}`. Source:
  gateway/provider usage logs where a gateway exists; otherwise the harness's
  own per-call records cross-checked against provider usage counters. Trust
  level 2.
- **Actor** — the `actor` string recorded on engine events and ledger entries.
  Distinct roles (driver, worker-N, scribe, librarian) MUST register distinct
  actor ids at `cortex_register` time.
- **Real LLM call** — a call-ledger entry with `output_tokens > 0` and
  `finish_reason != "length"` -- and, on any reasoning-model lane,
  `max_tokens >= 12000` (the recorded floor, `judge.MIN_MAX_TOKENS_BY_TIER`;
  below it these lanes silently return empty content — the 2026-07-12
  incident). A run violating the floor produces garbage evidence and is
  INVALID, not failed (§5).
- **Pre-registered** — committed to the repo, content-hashed, and the hash
  recorded in the run manifest **before** the driver's first LLM call. Hidden
  holdout portions live gitignored (the anti-oracle rule, as in
  `calibration/holdout/` and the 2B hidden tests) but their *hashes* are in
  the manifest, so post-hoc swapping is detectable.
- **Verifier** — one deterministic program (stdlib + repo code only, no
  network, no LLM imports — AST-checkable exactly like
  `test_no_llm_or_judge_in_the_verdict_path` in `tests/test_self_learning.py`)
  that reads primary artifacts and emits `verdict.json` + exit code 0/1. The
  verifier is pre-registered and frozen with its own unit tests (as the 48
  frozen checker tests do for the Stage-2 lanes).

### 1.2 P0 — trusted-provenance substrate (prerequisite, sol HIGH #1,3,4,10,14,19)

**The load-bearing critique.** If the same harness that runs the pipeline also
writes the engine DB, ledgers, flight recorder, closeouts, timestamps, and
deliverable bundle, then content-hashing those artifacts proves internal
*consistency*, not *authenticity* — a harness can synthesize a mutually
consistent world after the fact and pass every S-criterion without any real
agent doing any real work. Every criterion below therefore assumes, and is void
without, this substrate:

- **P0.1 Evaluator-owned, append-only event capture.** An LLM **gateway/proxy**
  (e.g. LiteLLM — the Phase-6 plane, `docs/BUILD-PLAN.md` trust level 2) sits
  between every agent and every model and records, in a store the *solver
  harness cannot write*, the request bytes, response bytes, tool-call bytes,
  served model, tokens, latency, and billed cost of every call. The engine DB
  and flight recorder are written by the **Cortex server process**, run under a
  **separate OS identity** from the driver/worker/scribe/librarian agents, so
  agents reach state only through mediated tools, never by direct file writes.
  The verifier reads gateway + server stores, not harness-authored copies.
- **P0.2 Causal binding, not timestamp bracketing.** An advance is credited to a
  model only if the applied tool-call payload's hash **equals** the hash of a
  tool call emitted in that agent's gateway-captured model response (sol #4).
  Bracketing timestamps alone are insufficient and are demoted to corroboration.
- **P0.3 Server-issued identity.** `cortex_register` today trusts arbitrary
  `agent_id`/`model`/`role` (`cortex_core/mcp.py:529`), so one process can spoof
  all four roles (sol #10). The substrate requires **server-issued role
  credentials**; actor attribution on every event/ledger row is the credential,
  not a self-declared string. Until this lands, S3/S5/S6 role-distinctness is
  provisional (§8 debt).
- **P0.4 Evaluator-isolated holdouts + external anchor.** Holdout manifests,
  thresholds, and the preregistration timestamp live in a **separate evaluator
  account/host the solver cannot read**, released only after the artifact bundle
  is sealed (sol #19). Gitignore + published hashes prevent post-hoc swaps but
  not local reading; that is not sufficient for a HARD self-validation gate.
- **P0.5 Monotonic external clock.** A signed monotonic clock is started by the
  evaluator **before inputs become available**; all S9 timing and the §5 INVALID
  signatures are computed against it, not harness wall-clock (sol #14, #18).
- **P0.6 Sandboxed, mediated execution.** Agent file mutations occur only inside
  an evaluator-controlled sandbox where **all** writes are attributable at the OS
  level (sol #3, #10, #20). Direct writes outside mediated tools are impossible,
  not merely "discouraged."

These six are the real prerequisite the prior "Scaffolded" runs lacked. The
criteria in §2 are the *shape* of the gate; P0 is what makes them proofs of
capability rather than proofs that a harness can emit records.

### 1.1 inspect-ai alignment

Per `docs/research/cortex-harness-prior-art-and-improvement-codex-2026-07-13.md`
(adopt inspect-ai for execution/logging; keep the Cortex policy layer) and
`reviewed/build-vs-buy-audit-2026-07-11.md`:

- The whole test is expressed as **one inspect-ai `Task`** with a single
  sample (the mission). The live pipeline run is the solver (or a bridge
  solver that launches it and waits).
- **Each criterion family below is one `Scorer`** returning
  `Score(value=CORRECT|INCORRECT, explanation=..., metadata={artifacts,
  measured values})`. Scorers are thin wrappers over the verifier's per-
  criterion results — they never contain their own logic fork, so the
  inspect path and the bare-verifier path cannot disagree.
- **Success convention:** the run PASSES iff every HARD scorer is `CORRECT`
  (accuracy == 1.0 over HARD criteria). SOFT criteria (marked below) are
  recorded as metrics, never gate.
- The inspect **eval log** (`.eval`) is retained as the artifact-of-record
  wrapper, but scorers *recompute from primary artifacts*; the log's own
  claims are never input to any verdict (anti-evidence-theater: a verdict
  file asserting PASS is not evidence of PASS).
- No `model_graded_*` scorer is permitted anywhere in the verdict path.

---

## 2. SUCCESS — hard machine-checkable criteria

All S-criteria are HARD unless marked SOFT. PASS = all HARD criteria true,
zero disqualifiers (§3) triggered, run VALID (§5).

### S1. Governed phase traversal — every phase, in order, no skips

For the driver task (track `build` or the mission's per-worker build tasks —
see S4) the engine event log must show, for EVERY task that reaches `DONE`:

- (a) the ordered advance chain `SEARCH_BRAIN → RESEARCH → PLAN → SPEC →
  IMPLEMENT → REVIEW → CLOSEOUT → DONE` exactly as `BUILD_TRACK` declares —
  each `next`-transition present, none absent, extra transitions only the
  chart-legal rework loop `REVIEW → IMPLEMENT` (bounded by `rework_cap`).
  Check: fold the events; the `to_state` sequence must be a path in the chart
  graph starting at `initial` and ending at `DONE`.
- (b) `replay(task_id)` == the task row's `(state, seq)` for every task
  (event-sourcing honesty check; disagreement = corruption).
- (c) **one `gate_verdict` row per advance event**, `pass=true`, same
  `(task_id, seq)` — count(advance events) == count(passing gate verdicts).
  **The gate that produced each verdict must be a pre-registered, hash-audited
  deterministic gate** (sol #2, #16): the default gate accepts any JSON object
  and REVIEW passes with only a warning when `scope_check` is absent
  (`state_engine.py:436`, `:577-586`), so a seven-call adapter submitting junk
  payloads would traverse. This run MUST bind each phase to a
  server-owned-receipt gate whose pass bit is computed from actual phase
  artifacts + hidden acceptance checks (the `smoke_verdict_gate` /
  `receipts.validate_smoke_receipt` pattern generalized to every phase), and the
  verifier re-checks that the gate module hash is on the no-LLM allowlist.
- (d) `closeout_written = 1` on every terminal task, set server-side.
- (e) no task ends `ABANDONED`; mission task ends `DONE`.

*Threshold justification:* the chart is the spec — 7 phases because
`BUILD_TRACK` has 7 non-terminal states; nothing to tune. *Honest limit (sol
#2):* the per-phase deterministic gates beyond SMOKE do not yet exist in
`state_engine.py`; building them is a §8 blocker, not a threshold choice.

**Measured by:** verifier reads the engine DB directly; graph-path check
against the chart constant imported from `cortex_core.state_engine`.

### S2. Enforcement is real, not compliance — adversarial probes must be refused

Traversal alone is gameable: a script that submits the right tools in the
right order traverses perfectly. Enforcement is proven by **refusal
evidence**, collected the same run:

- (a) **In-run probe set (pre-registered):** the harness injects, at ≥3
  distinct phases (at minimum: one during SEARCH_BRAIN, one during IMPLEMENT,
  one after DONE), a tool call that is illegal in that state (e.g.
  `cortex_submit_patch` during SEARCH_BRAIN; any tool after terminal). Each
  probe MUST return `ok:false, code=ILLEGAL_IN_STATE` and MUST NOT change
  `(state, seq)` (verified from the DB: no event appended at probe time).
- (b) **Stale-seq probe:** ≥1 deliberate stale-seq submission must return
  `REJECTED_STALE` with nothing applied.
- (c) Probes are issued under a dedicated `actor="probe"` so they are
  distinguishable from organic driver traffic and excluded from S3–S9 counts.

If the *driver itself* organically produces `ILLEGAL_IN_STATE` refusals,
those count as additional evidence but are not required (a well-behaved
driver may never attempt a skip; we do not reward misbehavior).

*Threshold justification (revised, sol #21):* the earlier "3 probes" under-
covered the negative space. The probe set is now the **full state×illegal-tool
negative matrix** for the build chart (every non-terminal state × every tool
not in its `legal_tools`, each must refuse) **plus** the stale-seq fence, a
**boundary-violation** probe (a write inside another task's live claim must
`BOUNDARY_VIOLATION`), and an **idempotency** probe (a replayed idem_key
returns the stored envelope, applies nothing). This is still finite and fully
deterministic. *Honest limit (sol #3, #21):* probes prove the *engine* refuses;
they do not by themselves prove the engine *governs the real work* — that is
what P0.6 (all mutations mediated, no side channel) provides. Without P0.6, S2
survives only as an API unit test, not an E2E enforcement proof.

**Measured by:** probe transcript (ts, request, envelope) + engine DB showing
zero state/seq change at each probe ts; refusal envelopes carry
`code`/`legal_tools` per state_engine.py:1083-1090.

### S3. Live model actually drives — no stub invoker, no harness ventriloquism

- (a) **Causal binding (P0.2), not just bracketing** (sol #4): each phase's
  advance tool-call payload hash MUST equal the hash of a tool call in that
  agent's gateway-captured model response for that phase. Timestamp bracketing
  (a real call by the task's actor between the previous and this advance) is
  additionally required but is corroboration, not the primary check — a harness
  that makes a one-token model call and then authors the payload itself FAILS
  because the payload hash won't match any model-emitted tool call.
- (b) **Phase payloads are task-specific, not templated:** for each advance
  payload, (i) payload is non-empty JSON; (ii) no two phase payloads within a
  task are byte-identical; (iii) the SEARCH_BRAIN/RESEARCH findings payloads
  each cite ≥2 corpus paths that exist on disk (checked by stat) and were
  actually returned by a logged `cortex_search`/fetch call earlier in that
  task (flight-recorder join).
- (c) **The agent issues its own searches:** every `cortex_search` credited
  to an agent appears in the server-side flight recorder with that agent's
  actor/session id. Harness-side injection of search results (the C-arm
  pattern, results-powered/REPORT.md §"C's brain lane is harness-injected")
  is a disqualifier (F2).
- (d) SEARCH_BRAIN ordering (the A2 `research_cited` axis,
  `evals/ab_cortex_scaffold/common_checks.py::check_research_cited`): first
  logged search/citation ts **strictly precedes** the task's first mutation
  event ts.

*Threshold justification:* ≥1 call/phase is the minimum that makes "the model
was in the loop for every phase" literally true; ≥2 existing cited corpus
paths matches the A2 axis (which required non-empty citation receipts) while
adding existence + provenance-join so a fabricated path can't pass.

**Measured by:** call ledger ⋈ engine events ⋈ flight recorder (all
timestamped); path existence by stat at verify time against the run's corpus
snapshot.

### S4. Real parallel fan-out, mission-governed

- (a) The run creates a mission task + **≥3 worker tasks on the `build` track**
  under that mission's `parent_id`, atomically, with **disjoint claims**
  (`partition_coverage_gate` passed — gate_verdict row at PARTITION).
  **Topology bug (sol #6, real):** today `spawn_mission(track="build")` puts the
  mission row itself on the build track, while a `MISSION_TRACK` task has no
  children so `cortex_submit_merge` sees `all_done:false`
  (`state_engine.py:921`, `mcp.py:1291`). This run REQUIRES the fix "dispatch
  accepts an existing mission-task id and atomically creates build-track
  children under that exact parent" — a §8 blocker, not satisfiable with the
  current public APIs.
- (b) Each worker independently satisfies S1–S3 on its own build chart (own
  server-issued actor + own gateway-captured LLM calls).
- (c) **Actual temporal parallelism via provider spans, not engine ts** (sol
  #5, real): engine event timestamps are integer seconds and every worker's
  `created` event shares one creation instant, so serialized workers trivially
  "overlap." Parallelism is instead proven from **gateway-captured request
  execution spans** (sub-second, per P0.1): ≥2 worker pairs whose
  `[req_start, resp_end]` intervals overlap by a **meaningful duration
  (≥5 s)**. Engine-ts interleaving is recorded but does not decide this.
- (d) All workers reach `DONE`; the mission MERGE/REVIEW gate verdicts exist
  and pass; `mission_status` at end: `all_done: true`.

*Threshold justification:* ≥3 workers because (i) the use case decomposes
naturally into ≥3 MECE units (feature reverse-engineering research; corpus
ingest/organization; retrieval/index verification + docs), (ii) 2-lane
fan-out was already demonstrated in the scaffolded run so 2 would not exceed
the proven floor, and (iii) 3 is the smallest N where the MECE
exclusivity/exhaustiveness checks of `partition_coverage_gate` are
non-trivial. Upper bound: chart default `max_workers=8`. Overlap on ≥2 pairs
(not all pairs) tolerates honest scheduling skew while still making a
serialized run fail.

**Measured by:** engine DB (`task.parent_id`, `claim`, `gate_verdict`,
event ts interleaving) + per-worker call ledgers.

### S5. Scribe-written, machine-evidenced closeouts — no format adapter

For EVERY terminal task (mission + each worker):

- (a) A closeout exists and validates **natively** against the main-repo
  schema (`cortex_core/audit.py`: `CLOSEOUT_SCHEMA_VERSION = 3`,
  `validate_evidence` with zero unresolvable file refs, and a well-formed v3
  `handoff` — non-empty `locations` + specific `continuation`, per
  `validate_handoff_field`). Exactly one schema lineage; any post-hoc
  conversion step between what the scribe wrote and what validates is F3.
- (b) **Run-binding digest, prefix-defined** (sol #8, real self-reference bug):
  the closeout payload contains `event_digest`, but the `cortex_write_closeout`
  advance appends that payload to the same log — hashing the *final* log is
  circular. Define `event_digest` == sha256 of the **canonical-serialized event
  prefix through `seq = closeout_seq − 1`** (the terminal seq is recorded beside
  the digest), plus run_id. Verifier recomputes over that exact prefix; mismatch
  = F3. Canonical serialization (field order, encoding) is pinned in the
  pre-registered verifier.
- (c) **Machine evidence:** `evidence[]` contains ≥1 structured item of
  `type=="test"`/`type=="command"` with an **explicit numeric exit code**
  from a real subprocess executed during the task (the strongest G1 signal
  class, `test_evidence_exit`), and the flight recorder shows that subprocess
  ran (tool_call event with matching ts window).
- (d) **Authored by a scribe agent, bound to gateway bytes** (sol #5, #7, #16):
  the canonical closeout JSON's prose fields (`result`, `handoff.continuation`)
  MUST appear verbatim in a **gateway-captured response of a server-issued scribe
  identity** (P0.1/P0.3) ≠ the task's driver/worker identity. The ≥5
  content-token overlap heuristic is **removed from the verdict path** — sol #16
  correctly notes it lets the scribe LLM flip S5 via five parroted tokens, and a
  token check is a weak semantic proxy that invites exactly the LLM-in-the-loop
  the spec bans. Binding is byte-identity to a captured model response, not
  overlap.
- (e) **Authorship vs landing are split** (sol #9): the scribe *authors* the
  canonical JSON payload; the librarian (S6) may only **byte-copy** it into the
  audit storage envelope. The current `cortex_core/audit.py:220` writer adds
  timestamps/rendering/sidecar — that transformation is defined as the *storage
  envelope*, and the verdict binds to the **canonical inner payload** the scribe
  emitted, so enveloping is not the F3-prohibited conversion but re-authoring is.
- (f) Closeout evidence must be **G1-decidable** (see S7a).

*Threshold justification:* the key set and prefix-digest rule are the frozen A2
axis, hardened; prose binding is byte-identity to captured model output (no
threshold to tune) rather than the withdrawn token-overlap heuristic.

**Measured by:** closeout JSONs in `audit/audit-log-*/agent/` ⋈ engine DB
(actor, digest recompute) ⋈ call ledger ⋈ flight recorder.

### S6. Librarian does the bookkeeping; the driver stays free

- (a) A **server-issued** librarian identity (P0.3) performs ALL corpus/audit
  bookkeeping: byte-copying the scribe's canonical closeout payload into
  `audit/audit-log-*/agent/` (S5e), catalog update, reindex. Evidence:
  OS-attributed writes (P0.6) + flight-recorder entries carry the librarian
  identity. Landing is byte-copy-into-envelope only, never re-authoring.
- (b) **Driver bookkeeping count == 0:** the driver/worker actors' ledgers and
  flight-recorder entries contain zero catalog-write / reindex / audit-file
  operations. ("Main model stays free," made countable.)
- (c) Bookkeeping actually happened: post-run, (i) every closeout file is in
  the audit tree and discoverable, (ii) the index doc/chunk count increased
  by the number of new indexed docs (before/after `cortex-search --status`
  snapshot diff), (iii) new deliverable docs are retrievable: for each new
  doc, a pre-registered self-query (its title) returns it in top-5 hybrid
  results.
- (d) SOFT: librarian runs on a cheap-tier model (recorded, not gated —
  cost policy, `feedback_cheap_models_for_fetch_subagents`).

**Measured by:** flight recorder actor attribution; index status snapshots
before/after; post-run retrieval probe (deterministic recall check).

### S7. G1 mints oracles from the real outcomes

Run `cortex-self-learning` (frozen G1 module — its 16 tests must be green at
the run's SHA) over exactly this run's closeouts. **Blocker (sol #11):**
`cortex_core/self_learning.py` is NOT on this worktree's tree (it lives on the
`claude/corpus-agent-orchestration-review-121x1q` lineage, commit f626951);
it MUST be merged to the run SHA and frozen before design-freeze.

- (a) **Decidability == 100% of worker build closeouts** (sol #22): S5(f)
  already requires machine-decidable exit-code evidence in *every* worker
  closeout, so anything below 100% is internally inconsistent (and at the
  mission+3-worker minimum, 80% rounds to 100% anyway). Every worker closeout
  yields a `test_outcome` signal in {`test_evidence_exit`, `status_fail`,
  `ratio`}. The single mission-coordination closeout is the *only* permitted
  UNVERIFIABLE (it runs no subprocess); it is enumerated, not a slack budget.
- (b) **≥1 minted candidate record** lands in the quarantined JSONL with:
  label ∈ {positive, anti_pattern, UNVERIFIABLE}, deciding `signal` recorded,
  `promoted: false`, `promotion_status: "quarantined"`.
- (c) **A pre-registered fail→fix control is REQUIRED** (sol #7, #11 —
  resolving the S7(b)/(c) contradiction): the mission includes one worker unit
  whose *first* implementation attempt deterministically fails its own hidden
  acceptance test and a *later* attempt (after the chart's REVIEW→IMPLEMENT
  rework) passes it. G1 MUST mint exactly ≥1 correctly-derived `positive`
  candidate from that fail→pass group, with the deciding signal recorded. This
  is **not** a scaffolded fake failure smuggled to force a mint: it is a
  pre-registered control that exercises the rework loop and proves the miner
  derives labels from real deterministic outcomes. (The earlier draft's "if the
  run passes first-try, zero mints is fine" let S7 pass while proving nothing —
  sol #11; withdrawn.) An `anti_pattern` is additionally minted for any unit
  that fails and never passes.
- (d) **Zero guessed labels, zero auto-promotion:** no decided label on an
  `ambiguous_mixed`/`none` signal; no record with `promoted: true` or a
  TRAINABLE tier. Any violation is F5.
- (e) Provenance tier: every minted record carries tier `quarantine` (per
  `cortex_core/promotion.py` `TIER_ORDER`); promotion remains a separate,
  later, human-gated step and is OUT OF SCOPE of this test's pass/fail
  (consistent with skip-human: the TEST does not require promotion to pass).

**Measured by:** miner output JSONL + a verifier re-derivation of
`test_outcome` per closeout; module frozen-test suite result (exit code) at
run SHA.

### S8. The deliverable is real and useful — deterministic content rubric

Two halves, matching the collaborator's use case. All sub-checks
pre-registered (§1) with a gitignored hidden-holdout portion.

**Half 1 — Kurzweil 3000 feature reverse-engineering report:**

- (a) The report exists in the deliverable tree and in the corpus (indexed).
- (b) **Coverage:** ≥10 distinct feature entries; ≥7 of a 12-key
  pre-registered canonical feature manifest are covered. The manifest is
  authored (before the run, from Kurzweil/vendor public documentation — an
  objective third-party source, not any model in the run) as 12 feature keys
  each with an alias set (e.g. read-aloud/TTS; OCR/image-reader; word
  prediction; highlighting/extraction; dictionary/picture-dictionary; study
  skills/outlining; test-taking accommodations; translation; web reading;
  writing templates; audible/audio export; file-format support). 3–4 of the
  12 keys are hidden holdout (in the evaluator account, P0.4). **Coverage
  ≥10/12 overall AND ≥3 of the 3–4 hidden keys** (sol #12): the earlier ≥7/12
  was passable by copying only the 8–9 *visible* aliases into skeletal entries,
  measuring nothing hidden. ≥10/12 with a hidden-key floor forces coverage the
  solver could not have templated from visible aliases.
- (c) **Faithfulness (zero-fabrication):** every citation resolves to a
  fetched source document that exists in the corpus with gateway-logged
  provenance; every direct quote appears verbatim (whitespace-normalized) in
  its cited source; every number matches its source (the `objective_research`
  lane, `cortex_core/faithfulness.py` style). **Each of the ≥10 covered feature
  entries must carry ≥1 supported quote-or-number citation** (sol #12): without
  this, a solver evades faithfulness entirely by attaching real citations to
  quote-free/number-free paraphrases, so "zero-fabrication" would check nothing
  on most of the report. **Any** citation to a nonexistent path, or any
  unmatched quote/number, = F6. Paraphrase entailment beyond quote/number
  support is NOT checked (honest abstention, as objective lane 2D does) — so
  every feature must be anchored by at least one checkable quote/number, not
  prose alone.
- (d) ≥5 distinct external sources fetched through the governed fetch path
  (logged, bounded, HTML→text) — a one-source report is not
  reverse-engineering. SOFT above 5, HARD at ≥3 if fetch availability
  degrades (record which).

**Half 2 — corpus organized into a portable, human-readable structure:**

Input: a pre-registered scattered corpus snapshot (content-hash manifest of
every input file, committed before the run).

- (e) **Zero loss, regular files only:** the multiset of input content hashes
  ⊆ output tree hashes (byte-identical), computed over **regular files with
  symlinks/junctions/reparse-points FORBIDDEN** (sol #13) — otherwise a
  symlink farm fakes zero-loss and portability without moving anything. 100%,
  no tolerance.
- (f) **Placement into a pre-registered project mapping** (sol #13): ≥95% of
  input files placed into a project dir, AND the realized file→project mapping
  matches a **pre-registered expected mapping** (evaluator-owned, P0.4) for the
  non-holdout files — so "dump every file into one giant project" fails even at
  95% placement. Remainder ONLY in explicit `_unsorted/` with a per-file
  one-line reason.
- (g) **Human-readable:** root index enumerates every project dir (100%);
  every project dir has a `README.md` ≥200 chars; max tree depth ≤ 4; dir
  names match a pre-registered slug regex. (The prose-overlap "binding" check
  is dropped here too, consistent with S5 — token overlap is a gameable
  heuristic, sol #16; the pre-registered mapping in (f) is the real anti-stuffing
  control.)
- (h) **Portable:** all intra-tree markdown links are relative and resolve
  (0 broken, 0 absolute local paths); no machine-specific absolute paths.
- (i) **Findability, evaluator-owned index + confidence bound** (sol #13, #23):
  a **≥50-query** needle set with known single target files (≥1/3 hidden
  holdout, evaluator account) run against an **evaluator-built index over the
  organized tree** (not a solver-controlled index that could be contaminated
  with metadata tokens): **the 95% lower confidence bound on recall@5 ≥ 0.60,
  AND ≥0.15 absolute lift over a frozen evaluator-owned baseline** (BM25 or a
  fixed grep impl — pinned, not "naive"). *Justification:* Plane-B measured
  Cortex hybrid recall@5 = 0.667 vs grep 0.267 on 574 files
  (results-powered/REPORT.md §3), so 0.60 is at demonstrated capability; the
  LCB over ≥50 queries stops ~12 lucky hits from passing, and the fixed-baseline
  lift stops a sabotaged baseline from making the relative clause meaningless.
  (Sol asked ≥100 queries; I set ≥50 — authoring 100 known-single-target needles
  over a fresh corpus is a real cost and 50 already gives a usable LCB; this is
  a recorded Fable-vs-sol compromise, §8.)

**Measured by:** hash manifests, link resolver, tree walker, pre-registered
query runner — all in the verifier; manifests + holdout hashes in the run
manifest.

### S9. Cost / latency bounds

- (a) Wall clock on the **external signed clock** (P0.5), from
  input-availability → verifier completion: **≤ 4 hours** (an owner-labeled
  SLO, not a capability threshold — sol #24). Work done before this clock
  starts is disqualifying precomputation (F2).
- (b) Total LLM tokens across all actors: **≤ 2,000,000** (sol #24). The
  spec's own per-agent figures (142–200k × 4–6 agents ≈ 0.85–1.2M) put a real
  governed run under 2M; the earlier 5M was ~4× headroom that would hide a
  runaway. Recalibrate from signed dry-run distributions before freeze.
- (c) Paid cost: **≤ $15** (owner-labeled SLO); from **gateway billing
  records**, never a self-recorded price table (sol #14).
- (d) Per-call hygiene: every reasoning-lane call has `max_tokens ≥ 12000`
  (the recorded floor, `judge.MIN_MAX_TOKENS_BY_TIER`); zero calls with
  `finish_reason=="length"` on an advance-critical call. **Driver must be a
  pre-registered strong-model lane** (whitelist in the manifest), verified
  against the gateway's served-model record — not any tiny model that emits a
  marker token (sol #14).
- (e) SOFT: no task enters `STALLED`; if one does and `cortex_resume`
  recovers it, record it (resilience datum), don't fail.

*Justification:* the 21-trial A/B/C main run consumed ≈3.7M tokens
(per-arm means 142.6k/180.4k/199.5k × 7) across 21 *whole trials*; one mission
with 4–6 governed agents is ~0.85–1.2M by the same per-agent rate, so 2M is a
real ceiling with modest headroom (sol #24). 4h/$15 are explicitly owner SLOs,
not capability evidence. Blowing (a)–(c) = F8: an ungoverned-cost pipeline
fails the delivery promise. All three recalibrated from signed dry-runs before
freeze.

**Measured by:** call ledger sums; engine event timestamps; lane/price table
recorded in the run manifest.

### S10. Single deterministic verdict path

- (a) `verify_e2e` (the verifier) runs from a clean checkout of the run
  SHA + the run's artifact bundle, offline, and exits 0 with
  `verdict.json` enumerating every criterion → {pass, measured value,
  artifact paths}.
- (b) **Sandboxed, not just AST-clean** (sol #15): the AST no-LLM check is
  necessary but insufficient (dynamic import, subprocess, ctypes, transitive
  repo imports, precomputed judge files all bypass it). The verifier runs in a
  reproducible sandbox with **no network and no process creation**, a
  **transitive-import allowlist**, and **read-only content-addressed inputs**;
  reproducibility is confirmed by an independent second run on isolated
  hardware.
- (c) Re-running the verifier on the same artifacts is byte-identical (modulo
  its own run timestamp).
- (d) **Every gate implementation used in the run is hash-audited on the
  no-LLM allowlist** (sol #16): `gate_verdict.pass` is trusted only if the gate
  that produced it is a pinned deterministic gate; the MCP can otherwise layer a
  vision/semantic judge into REVIEW (`mcp.py:838`, `review_scope_gate`
  anticipates an LLM base) — that path is banned for this run.
- (e) The inspect-ai `.eval` log is an **archival consistency projection, not a
  second verdict** (sol #16): its accuracy is recomputed from the verifier's
  per-criterion results and must agree, but inspect's own scoring is never an
  independent HARD veto (avoids a second, model-adjacent gate).

---

## 3. FAILURE — disqualifiers (any one ⇒ FAIL, regardless of everything else)

- **F1 — Skipped/forged phase.** Any DONE task whose event-log fold is not a
  legal chart path; replay/task-row disagreement; a gate_verdict row missing
  or `pass=false` on an applied advance; any state change without a
  corresponding event (forgery); closeout_written set client-side.
- **F2 — Scaffold or stub invoker.** Empty call ledger for any governed
  actor; phases advanced by calls that no LLM call brackets; byte-identical
  advance payloads across phases; harness-injected search/research results
  credited to an agent (the C-arm pattern); pre-computed deliverable content
  smuggled in via the harness; probes (S2) *accepted* — an illegal tool that
  mutates state means enforcement is fake (this is the single fastest
  disqualifier).
- **F3 — Fake / format-adapter closeouts.** Any closeout that (i) fails
  native schema validation, (ii) has an event_digest that does not recompute
  from the engine event log, (iii) was produced by post-hoc conversion of
  some other artifact, (iv) whose writing actor == the task's own
  driver/worker actor or is not a registered scribe, or (v) whose prose
  fails the run-binding overlap check. One bad closeout fails the run —
  closeouts are the audit trail the self-learning loop feeds on; one forgery
  poisons the corpus.
- **F4 — No real fan-out.** <3 workers; claims not disjoint / partition gate
  bypassed; zero interval-overlapping worker pairs (serialized fan-out
  theater); any worker with an empty call ledger.
- **F5 — Minting theater or guessing.** Miner absent from the run; a decided
  label minted from an `ambiguous_mixed`/`none` signal; any record
  auto-promoted (`promoted:true`/TRAINABLE tier) in-run; miner frozen tests
  red at the run SHA; worker-closeout decidability < 100% (S7a); the required
  fail→fix control mint (S7c) absent.
- **F6 — Hallucinated / unusable deliverable.** Any fabricated citation
  (nonexistent path, unmatched quote/number); a covered feature with no
  checkable quote/number support; <10/12 canonical features or <3/4 hidden
  keys or <10 entries; any input-file content lost in reorganization; a realized
  file→project mapping that diverges from the pre-registered one; broken or
  absolute intra-tree links; findability LCB below S8(i); placement below
  S8(f) without explicit `_unsorted/` reasons.
- **F7 — Human-gated criterion.** Any criterion whose evaluation requires a
  human decision to produce the verdict — including "reads well",
  "looks organized", unreviewed judge scores promoted to gates — is void; if
  the pass depends on it, the run FAILS the spec (violates skip-human).
  Human review remains an optional later upgrade and may only ADD
  information, never be required for PASS.
- **F8 — Budget blowout.** Any S9(a–c) bound exceeded, or an advance-critical
  call truncated at `finish_reason=="length"`.
- **F9 — Evidence theater at the verdict layer.** `verdict.json`/`.eval`
  claims not reproducible from primary artifacts on re-verification; artifact
  bundle incomplete (any S-criterion's named artifact missing = that
  criterion FALSE, never "assumed pass"); missing-artifact defaults are
  fail-closed everywhere.
- **F10 — Tampered pre-registration.** Any pre-registered manifest, checker,
  threshold, holdout hash, or the verifier itself changed after the run
  started (git history + hash comparison). Changing the rubric to fit the
  run is the canonical self-grading failure.
- **F11 — Untrusted provenance / identity spoofing (sol #10, #19, #25).**
  Any governed event, ledger row, or write whose identity is self-declared
  rather than server-issued (P0.3); any holdout or preregistration anchor
  readable by the solver (P0.4); any runtime store writable by the solver
  harness (P0.1). Absent the P0 substrate, the run cannot PASS — it is at most
  INVALID (substrate not available).
- **F12 — Unmediated mutation (sol #3, #10, #20).** Any deliverable/corpus
  write not attributable to a server-issued identity through a mediated tool —
  i.e. a direct filesystem/network side channel around the engine.
- **F13 — Clock or attempt-ledger manipulation (sol #14, #18).** Work timed
  against harness wall-clock instead of the P0.5 signed clock; any reset,
  cherry-pick, or non-durable identity of the attempt/rerun counter.
- **F14 — Runtime artifact synthesis (sol #1).** Engine DB / ledger / flight
  recorder / closeouts constructed after the run to be mutually consistent,
  rather than captured live in evaluator-owned append-only stores.
- **F15 — Evidence-relevance & source-independence gaps (sol #25).** An
  `exit 0` from a subprocess unrelated to the claimed check; duplicate/mirror
  "sources" padding the ≥ source count; a retrieval index contaminated with
  injected metadata tokens; a symlink/junction faking a regular-file tree.
- **F16 — Payload not model-authored (sol #4, #5).** An advance or closeout
  payload whose hash matches no tool call / no prose span in the responsible
  agent's gateway-captured model response.

---

## 4. Measurement plan — criterion → primary artifact → check

| # | Criterion | Primary artifact(s) | Deterministic check |
|---|-----------|---------------------|---------------------|
| S1 | phase traversal | engine DB (`event`, `task`, `gate_verdict`) + gate-hash allowlist | chart-graph path fold; `replay()`==row; advance↔verdict counts; each gate on no-LLM allowlist |
| S2 | enforcement real | probe transcript + engine DB | full negative-matrix refusals; no event/seq delta at probe ts |
| S3 | live driver | **gateway capture** + engine DB | payload-hash == captured tool-call hash (P0.2); payload distinctness; cited-path stat + fetch-log join |
| S4 | parallel fan-out | engine DB (`parent_id`,`claim`) + **gateway spans** | ≥3 build-track children; disjoint claims; ≥5 s span overlap ≥2 pairs; all DONE |
| S5 | scribe closeouts | closeout JSONs + engine DB + **gateway scribe response** | native v3 schema; prefix-digest recompute; exit-code evidence; server-issued scribe identity; prose byte-in-captured-response |
| S6 | librarian | **OS write attribution** + index snapshots | librarian identity on all bookkeeping; driver bookkeeping ops == 0; doc-count diff; self-query recall; byte-copy-only landing |
| S7 | G1 minting | miner JSONL + closeouts + G1 test exit code | worker decidability == 100%; ≥1 quarantined record; **fail→fix control positive**; zero guesses/promotions |
| S8 | deliverable | deliverable tree + evaluator hash/mapping/query manifests + gateway fetch log | ≥10/12+≥3/4-hidden features w/ quote-number support; faithfulness; regular-file zero-loss; mapping match; links; recall@5 LCB ≥0.6 ∧ ≥0.15 lift |
| S9 | cost/latency | **gateway billing/usage** + external clock | sums vs 2M/4h/$15; 12k floor; whitelisted served model; no truncated advance calls |
| S10 | verdict path | verifier source + sandbox manifest + verdict.json + .eval | sandboxed no-net/no-proc + import allowlist; independent re-run identity; inspect = projection not veto |

**Artifact bundle (retained per run, content-hashed):** engine SQLite DB;
`logs/events.jsonl` + server request log; per-actor call ledgers; probe
transcript; closeout JSONs; miner JSONL; deliverable tree + input/output hash
manifests; pre-registration manifest (checker hashes, thresholds, holdout
hashes, lane/price table); `verdict.json`; inspect `.eval` log; run SHA.

---

## 5. VALID vs PASS/FAIL (so infrastructure noise can't fake either)

A run is **INVALID** (neither pass nor fail — rerun, don't conclude) iff a
*non-verdict* infrastructure precondition broke AND the break is **externally
attested**, not harness-inferred (sol #18). The only INVALID trigger is a
**provider incident attested by the gateway/provider** (P0.1) — an outage or
served-model failure the evaluator's gateway records show, independent of the
solver. The earlier "total actor tokens < 5% of the per-actor median" signature
is **removed**: a solver could deliberately starve an actor to trigger it and
launder a bad run (sol #18). Everything else is decided:

- Artifact-bundle loss **after execution begins = FAIL**, not INVALID (sol #18
  resolved the prior contradiction between §5 and F9 in favor of FAIL — a lost
  bundle is indistinguishable from a hidden one).
- A 12k-floor violation is a **harness misconfiguration = INVALID only if
  caught pre-first-advance**; after real advances it is F8 (the run is tainted).
- A pipeline-under-test break (engine error, gate crash, forged anything) is
  **FAIL**.

Every INVALID is written to a **signed, durable attempt/rerun ledger** in the
evaluator account (P0.4). Max 3 INVALID reruns per attempt id (the ledger's
identity is durable and cannot be reset — sol #18); beyond that the attempt is
FAIL. No human judgment enters the INVALID decision.

---

## 6. Honest debt / known limits of this spec

1. **"Useful to the collaborator" is proxied, not proven.** The deterministic
   rubric (S8) measures coverage/faithfulness/structure/findability — the
   machine-checkable shadow of usefulness. Whether the report/organization
   satisfies the human collaborator is exactly the human-verification
   upgrade the owner deferred. The spec says so instead of pretending.
2. **Scribe/librarian "agency" is defined structurally** (server-issued
   identity, gateway-captured own model call, byte-bound payload), not
   cognitively. A minimal prompt-wrapped call satisfies S5(d)/S6 — that is
   deliberate: the criterion is "real model-authored, run-bound,
   machine-evidenced", not "impressive".
3. **The canonical feature manifest is author-fallible.** It's built from
   vendor docs before the run; a wrong alias set could under-credit an honest
   report. Mitigation: 12 keys / pass-at-7 slack + holdout hashes; residual
   risk accepted and recorded.
4. **Anti-templating checks (S3b, S5d) are coarse token heuristics** — they
   catch copy-paste and boilerplate, not sophisticated paraphrase-gaming. A
   deliberately adversarial driver could game them; the threat model here is
   accidental scaffolding, not a hostile model. Named, not hidden.
5. **Call-ledger trust is level 2, not level 1.** Without a gateway on
   gravebuster, harness-recorded calls cross-checked against provider usage
   counters is the best available; a gateway (Phase 6) upgrades this.
6. **G1 positive-mint may legitimately be 0** on an all-pass run (S7c). The
   test proves the minting *loop* runs honestly on real outcomes; guaranteed
   fail→fix minting evidence accumulates over subsequent real runs.
7. **inspect-ai wrapping is alignment, not adoption-complete:** this spec
   binds Task/Scorer/log conventions for THIS test; migrating Cortex's wider
   runner stack onto inspect-ai remains the separate adoption track.
8. **Several HARD predicates are still semantic/process-history, not yet fully
   mechanized (sol #17).** Terms like "harness-injected", "post-hoc conversion",
   "real subprocess", "provider collapse", "advance-critical" must each be
   reduced to an enumerated event-schema + receipt rule before the verifier can
   decide them without judgment. The P0 substrate + causal-binding (P0.2) +
   gateway records mechanize most of them; the remainder are §8 open items and
   are why this spec is NOT design-frozen. F7 cannot police an undefined
   predicate — each must become a receipt rule or be removed from the HARD gate.
9. **The whole gate is only as trustworthy as the P0 substrate.** This is a
   *self*-validation test (Cortex grading Cortex); P0.1/P0.4 (evaluator-owned
   stores + isolated holdouts) are what stop the circularity. Until they exist,
   any "PASS" is provisional — sol #1's core point, accepted.

---

## 7. Pre-registration checklist (must exist, hashed, in the evaluator account
before the run — P0.4)

**P0 substrate (blocking prerequisites — §1.2):**
- [ ] LLM gateway/proxy capturing request/response/tool-call bytes + billing,
      in a solver-unwritable store (P0.1)
- [ ] Cortex server running under a separate OS identity from the agents (P0.1/P0.6)
- [ ] Server-issued role-credential mechanism replacing self-declared
      `cortex_register` identity (P0.3)
- [ ] Evaluator account holding holdouts + preregistration anchor, solver cannot read (P0.4)
- [ ] External signed monotonic clock started before inputs available (P0.5)
- [ ] Sandbox with OS-level write attribution for all agent mutations (P0.6)
- [ ] Per-phase deterministic gates (generalizing `smoke_verdict_gate`) + gate-hash allowlist (S1c/S10d)
- [ ] Mission dispatch that creates build-track children under an existing mission id (S4a fix)
- [ ] `cortex_core/self_learning.py` merged to run SHA + its 16 tests green (S7)

**Rubric artifacts:**
- [ ] Verifier source + frozen unit tests (green in CI at run SHA), sandbox manifest
- [ ] Canonical K3000 feature manifest (12 keys + aliases; 3–4 holdout in evaluator acct)
- [ ] Needle-query set (≥50; ≥1/3 holdout) + frozen evaluator-owned baseline impl + expected file→project mapping
- [ ] Input-corpus content-hash manifest (regular files only)
- [ ] Fail→fix control unit spec (S7c) with its hidden acceptance test
- [ ] Full state×illegal-tool probe matrix + stale-seq/boundary/idempotency probes (S2)
- [ ] Strong-model driver lane whitelist + lane/billing table + token/cost caps + 12k floor
- [ ] Chart version pins (`BUILD_TRACK`/`MISSION_TRACK` version strings)
- [ ] inspect-ai Task/Scorer wrapper source (archival projection, not a verdict)
- [ ] Signed attempt/rerun ledger initialized (§5)

---

## 8. Arbitration record — Fable vs sol@xhigh (independent Codex leg)

**Independent leg:** `codex exec --model gpt-5.6-sol --config
model_reasoning_effort=xhigh --sandbox read-only` (xhigh accepted — no fallback
to high needed). Full verbatim critique persisted at
`reviewed/sol-xhigh-red-team-e2e-spec-2026-07-14.md` (26 findings, 20×HIGH /
5×MED, per the capture-subagent-output rule).

**sol verdict:** **REJECT design-freeze.** Core finding (#1, #26): as drafted,
every criterion is passable by a ceremonial/lazy harness because nothing
anchors the runtime artifacts to a trusted root; content-hashing a
harness-authored world proves consistency, not authenticity. sol classified
S3/S4/S5/S6/S7 as *falling* to a scaffold and S1/S2/S8/S9/S10 as surviving only
as narrow structural checks. **I accept this verdict.** The spec's own §6.4
threat model ("accidental scaffolding, not a hostile model") was too weak for a
HARD self-validation gate; sol is right that Cortex-grading-Cortex must assume
the stronger adversary. Status downgraded from "design-frozen candidate" to
"NOT frozen; blocked on the P0 substrate."

### Accepted and folded in (changed the spec)

| sol # | Sev | Finding | Change made |
|-------|-----|---------|-------------|
| 1,14,19,25 | HIGH | No trusted root; harness owns all artifacts; holdouts readable; clock self-recorded | New **§1.2 P0 substrate** (gateway-captured bytes, separate-identity server, server-issued roles, evaluator-isolated holdouts, external signed clock, sandboxed mediated writes); F11–F14 added |
| 2,16 | HIGH | Default gate accepts any JSON; REVIEW passes on missing scope_check; vision/LLM judge can enter gate | S1c now requires pre-registered per-phase deterministic gates; S10d hash-audits every gate on a no-LLM allowlist; inspect demoted to archival projection (S10e) |
| 4,5 | HIGH | No causal binding between model output and advances/closeouts | S3a rewritten to **payload-hash == gateway-captured tool-call hash** (P0.2); S5d binds closeout prose to captured scribe response bytes; F16 added |
| 5(part),16 | HIGH | Scribe passable by parroting 5 tokens; overlap heuristic is a weak LLM-gameable proxy | **Removed the ≥5 content-token overlap check** from S5 and the parallel one from S8g; replaced by byte-identity to captured model output + pre-registered project mapping |
| 6 | HIGH | S4 unsatisfiable via current mission APIs (mission row on wrong track / no children) | S4a states the dispatch-topology fix as an explicit §7 blocker |
| 5 | HIGH | Integer-second engine ts make serialized workers "overlap" | S4c now uses sub-second **gateway execution spans**, ≥5 s overlap, not engine ts |
| 7,11 | HIGH | S7(b) mandatory mint contradicts S7(c) all-pass-fine; miner absent from tree | S7c now **requires a pre-registered fail→fix control** yielding ≥1 derived positive; miner-merge added as §7 blocker |
| 8 | HIGH | event_digest self-referential (hashes the log it's appended to) | S5b redefined as the **prefix through seq = closeout_seq−1** |
| 9 | HIGH | S5 (scribe writes) vs S6 (librarian lands) authorship conflict; audit writer transforms | S5e splits **author (scribe JSON) vs land (librarian byte-copy into envelope)**; verdict binds to canonical inner payload |
| 10 | HIGH | `cortex_register` trusts arbitrary agent_id/model/role | P0.3 server-issued identity; F11; role-distinctness marked provisional until built |
| 12 | HIGH | ≥7/12 features gameable from visible aliases; faithfulness evadable via quote-free paraphrase | S8b → **≥10/12 + ≥3/4 hidden**; S8c → every covered feature needs ≥1 checkable quote/number |
| 13 | HIGH | Token-stuffing + baseline sabotage + symlink cheats | S8e bans reparse points; S8f requires pre-registered file→project mapping; S8i uses evaluator-owned index |
| 15 | HIGH | AST purity insufficient (dynamic import/subprocess/ctypes) | S10b adds no-network/no-process sandbox + transitive-import allowlist + independent re-run |
| 18 | HIGH | INVALID is a laundering path; artifact-loss contradiction; resettable counter | §5 rewritten: only **gateway-attested** provider incidents → INVALID; **artifact loss after start = FAIL**; signed durable attempt ledger; removed the starvable <5%-tokens signature |
| 22 | HIGH | 80% decidability inconsistent with S5 per-closeout evidence | S7a → **100%** of worker build closeouts; only the mission-coord closeout may be UNVERIFIABLE |
| 24 | MED | 5M tokens ~4× loose; 4h/$15 are policy not capability | S9b → **2M**; 4h/$15 relabeled owner SLOs; recalibrate from signed dry-runs |
| 21 | MED | 3 probes under-cover the negative space | S2 → **full state×illegal-tool matrix** + stale-seq/boundary/idempotency probes |
| 17 | HIGH | Many HARD predicates still semantic/process-history | Honest-debt #8: each must become an enumerated receipt rule before freeze; named as open |

### Contested / partially accepted (Fable-vs-sol disagreements, recorded)

- **sol #23 — recall over ≥100 hidden queries.** Partially accepted. I kept the
  0.60 target and adopted the 95%-LCB + fixed-baseline-lift discipline sol asked
  for, but set the query floor at **≥50, not 100**: authoring 100 known-
  single-target needles over a freshly organized corpus is a real cost, and a
  50-query LCB already discriminates lucky hits from capability. If early runs
  show a wide CI, raise to 100. *Disagreement is on N only, not on the LCB/lift
  method — I adopted those.*
- **sol #3/#21 "S2 is only a unit test."** Accepted as a *limit*, not a reason
  to drop S2. S2 stays (it is cheap and catches a broken engine), but the spec
  now states plainly that S2 proves engine-refusal, and only P0.6 (mediated
  execution) upgrades it to an E2E enforcement proof. So S2 is not claimed as
  standalone E2E evidence — matching sol's point without deleting a useful check.
- **Overall "REJECT freeze."** Accepted in full. I did not soften it: the
  header, honest-debt #9, and the §7 blocking-prerequisites list all state that
  no PASS is claimable until the P0 substrate exists.

### What did NOT change (and why)

- The **trust order, no-judge-in-verdict-path, and anti-evidence-theater
  spine** — sol reinforced these; no change needed beyond removing the two
  token-overlap heuristics that were themselves soft.
- The **deliverable being proxied, not proven useful** (honest-debt #1) — sol
  did not contest that skip-human makes usefulness a proxy; it remains the
  deferred human-review upgrade.

### Net effect

The spec is materially stronger and materially *less done*: sol converted it
from "a rubric you could run next week" into "a rubric plus a substrate you must
build first." That is the correct outcome for a HARD gate that validates Cortex
itself — a freeze on the weaker version would have reproduced the exact
scaffold-passes-as-real failure the project exists to prevent.
