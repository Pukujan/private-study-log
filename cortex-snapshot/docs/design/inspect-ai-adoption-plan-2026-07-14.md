# inspect-ai adoption plan (E1 build-vs-adopt spike) — 2026-07-14

**Owner decision (input):** ADOPT UK AISI `inspect-ai` as the eval *runner*; keep
Cortex's distinctive governance layer on top. This doc records the **spike that
proves the boundary is clean**, the **adopt-cost vs maintain-cost** verdict, and
the **honest scope** (this is a first-cut spike, NOT a migration).

Research-first basis (corpus): `reviewed/build-vs-buy-audit-2026-07-11.md` already
concluded — with the build-vs-buy line table — *"Benchmark/agent runner → Inspect
AI | not built (good)"*, i.e. adopt a runner rather than grow a custom one, while
KEEPING the moat (deterministic app-gates, template renderers, **objective
oracles/checkers**, anti-circularity promotion). This spike executes that line for
one lane. `docs/ROADMAP.md` §8 stance ("run one, build zero") is the same
discipline applied to observability.

---

## 1. What the spike actually did (RUN, not just designed)

Ported **one** objective lane — **2B coding** (`evals/objective_coding/`) — onto
inspect-ai's `Task` / `Solver` / `Scorer` abstraction, and **ran it end-to-end**.

- `inspect_ai==0.3.246` **pip-installs here** and the eval **ran** (Windows,
  Python in the shared venv). Not an against-docs paper port.
- Adapter: `evals/inspect_port/coding_inspect.py` (~200 LOC) + frozen governance
  tests `evals/inspect_port/test_governance.py` (4 tests, green).
- **Result:** `coding_lane` task, **21 samples, accuracy 1.000, mean 1.000** — the
  deterministic checker reproduces every gold verdict through inspect's runner. A
  native inspect `.eval` log was written to `evals/inspect_port/logs/`.

### The boundary that was preserved (the whole point)
| Cortex guarantee | How the port keeps it |
|---|---|
| **Deterministic-checker-as-truth** | The inspect `@scorer` *calls the same* `evals.objective_coding.checker.check_solution` (real subprocess test execution). inspect **orchestrates + logs**; it never decides pass/fail. |
| **No-judge-in-verdict-path** | No model in the scorer. `assert_no_judge()` fail-loud gate rejects any record whose `label_authority != subprocess_test_execution`, at ingest *and* in the framework-free core. A test asserts a judge-labeled record raises. |
| **Provenance tiers** | `provenance_tier` + `label_authority` + `verdict_authority` ride through inspect `Sample.metadata → Score.metadata` unchanged; verified present in the run's score metadata. |
| **Anti-distillation** | Solver is a deterministic **replay** of stored candidate code (offline). No Claude/model output enters the trainable corpus via this path; swapping in `generate()` for live models reuses the *same* deterministic scorer, so labels still come from the checker, not the model. |

"Keep governance on top" is **clean**: inspect owns dataset iteration, async
orchestration, metrics, logging, sandboxing, provider abstraction. Cortex owns the
**verdict** (checker), the **provenance/no-judge gate** (wrapper), and the
**promotion ledger** (untouched). The seam is the `@scorer` body + the ingest gate.

---

## 2. What ported cleanly vs what didn't

**Ported cleanly**
- Dataset → `MemoryDataset[Sample]` (1:1 with `hard_gold.jsonl`). Trivial.
- Verdict → `@scorer` wrapping the existing checker verbatim. Trivial, and this is
  exactly where the guarantee lives, so it stayed in Cortex's hands.
- Metrics/logging/TUI/`.eval` log → free from inspect (replaces the hand-rolled
  `run_manifest.json` counting for *this* view; the ledger stays authoritative).
- Provenance metadata pass-through → free (`Sample.metadata`/`Score.metadata`).

**Did NOT port cleanly / left out (honest)**
- **CLI task discovery** (`inspect eval <file>`) did not find the task when the
  `@task` decorator was applied via a `try/except` alias (inspect scans source for
  `@task`). Worked via the programmatic `inspect_ai.eval(coding_lane(), ...)` path.
  A real adoption should use a bare top-level `@task` (guarded import) so CLI
  discovery works — minor, noted.
- **Windows sandbox**: inspect's control server warns `module 'socket' has no
  attribute 'AF_UNIX'` and runs "without control surface" (cosmetic here because
  the checker already sandboxes via `subprocess -I -S`). inspect's **docker**
  sandbox is the portable option; on gravebuster (Linux Docker host, per memory)
  it would run fully. The current checker's subprocess isolation was deliberately
  kept as the verdict path so the spike is byte-for-byte the Cortex checker.
- **Quarantine honesty gates** (`reference_broken`, `mutation_ineffective` in
  `run_coding.py`) were **not** re-expressed as inspect constructs — they are
  gold-*minting* logic, not gold-*running* logic. Adoption should keep minting in
  Cortex and only run/serve the frozen gold through inspect. (Design boundary:
  inspect runs gold; Cortex mints + governs it.)
- Only 1 of 5 lanes, and only the *replay* solver (no live-model generation) —
  see scope caveats.

---

## 3. Adopt-cost vs maintain-cost

**Maintain-cost (status quo, per lane):** the coding lane is ~114 LOC checker +
87 LOC runner + fixtures. Cheap to keep, zero external deps, fully offline,
deterministic. Across 5 lanes we already own working runners. The custom runner
cost is *low and paid*.

**Adopt-cost:**
- **Dependency weight:** `inspect_ai` pulls **~30 transitive packages** (pydantic
  v2, boto3/aioboto3/s3fs, textual, debugpy, universal-pathlib, zstandard…);
  the package itself is ~709 py files. This is a **heavy** optional dep — the
  reason to keep it strictly behind the `[inspect]` extra and never in core.
- **Per-lane port:** ~1–2 hrs each at this pattern (dataset + scorer + gate).
  The scorer is a thin wrapper over the existing checker, so cost is low *because*
  the moat (checkers) is reused, not rewritten.
- **Risk surface:** pydantic-v2 / boto3 version pins could conflict with core;
  isolating in the extra + CI-in-a-separate-env mitigates. Framework churn
  (inspect is pre-1.0, 0.3.x) is a real maintenance tax.

**Verdict:** **Adopt as an OPTIONAL runner/serving layer, incrementally, keeping
the checkers + minting + ledger in Cortex.** The win is *not* saving runner LOC
(ours is cheap) — it is **free orchestration, logs, provider abstraction, and
sandboxing** for *live-model* generation and for a standard, shareable eval log
format, without surrendering the deterministic verdict. Do **not** put inspect in
core; do **not** move gold-minting or promotion into it. If inspect's churn or dep
weight bites, the seam is one `@scorer` + a loader — reversible by deleting
`evals/inspect_port/` (the objective lanes keep working untouched).

---

## 4. Recommended adoption path (incremental, reversible)

1. **Now (this spike):** coding lane adapter behind `[inspect]` extra; parity CLI
   + frozen governance tests. **Done.**
2. **Next (cheap):** port the remaining objective lanes' *scorers* the same way
   (tool-calling → wrap BFCL `ast_checker`; security → AST+bandit; research →
   faithfulness checker; architecture → import-graph). Each is a scorer wrapper;
   gold-minting stays in Cortex.
3. **Live-model generation:** replace the replay solver with `generate()` to drive
   real models through inspect's provider abstraction (Claude/GLM/Qwen/…) — the
   deterministic scorer is unchanged, so labels still come from the checker. This
   is where inspect earns its weight (retries, concurrency, cost/token logging).
4. **Sandbox on Linux:** run execution lanes under inspect's docker sandbox on
   gravebuster (never local Docker, per memory) instead of the hand-rolled
   subprocess; only if we need stronger isolation than `subprocess -I -S`.
5. **Serving/log format:** treat inspect `.eval` logs as an interchange format;
   the Cortex promotion ledger (`evals/promotion_decisions/`) remains the
   authoritative verdict store.

**Guardrails that must survive adoption (governance-on-top):**
- Verdict authority stays a deterministic checker in every scorer — enforce with
  the `assert_no_judge` gate + a frozen test per lane.
- inspect stays an **optional** dep; `evals/objective_*/` must import and run
  without it (verified: parity CLI + tests run framework-free).
- Gold-minting + quarantine + promotion stay Cortex-owned; inspect only *runs*
  frozen gold.

---

## 5. Honest scope

- **Spike, not migration.** 1/5 lanes, replay solver only (no live generation
  through inspect yet), CLI-discovery caveat, Windows sandbox caveat. The 4 other
  lanes are *designed-analogous* but **unbuilt**.
- The parity result (21/21) proves *the runner reproduces the checker's verdicts*,
  not that inspect adds accuracy — by design it must add **zero** verdict
  influence.
- Reversible: delete `evals/inspect_port/` + the `[inspect]` extra; nothing in the
  objective lanes depends on inspect.

## 5b. sol@xhigh red-team (mandatory gate) — FILE-VERIFIED, verdict CLEAN-PASS: NO → folded

Red-teamed by **OpenAI `gpt-5.6-sol` @ `model_reasoning_effort=xhigh`** via Codex
CLI 0.144.1, `--sandbox read-only` (no effort fallback needed). Full transcript +
run notes: `reviewed/inspect-ai-adoption-sol-redteam-2026-07-14.md`.

**Run honesty:** the first two attempts crashed — Codex's interactive file-tool
("code-mode host") failed on handshake on this Windows box (an environmental
Codex-CLI bug, NOT a cyber-filter and NOT a code finding); sol correctly refused to
fabricate citations. The **clean, file-verified** run inlined all four files
(line-numbered) into the prompt so no file-tool was needed, in an isolated scratch
dir (no security-heavy corpus). That run returned exact `file:line`-cited findings.

**Verdict: CLEAN-PASS = NO on the first-cut spike.** The findings were *correct* —
they caught real overclaims:
- `authoritative_verdict()` was **never called in the inspect Task path** — the
  scorer returned its own `Score`; "advisory Score" was cosmetic.
- `assert_no_judge` trusts a self-declared string (a model grader could self-label);
  "not bypassable" was overclaimed.
- `Score.value` was checker-vs-gold **parity**, not candidate pass/fail — accuracy
  1.0 is a reproduction rate, not a pass-rate.
- `stamp_candidate_provenance` was **unused/bypassable**; the scorer copied the gold
  sample's tier onto candidates.

**MUST-FIX → disposition (folded unless marked adoption-blocker)**
1. **Verdict-of-record produced by Cortex, ignoring inspect Score** — *FOLDED:*
   `authoritative_run()` (framework-free, inspect-free) is the verdict-of-record;
   the inspect `@scorer` is explicitly `advisory_only` and now **delegates to the
   same `authoritative_verdict()`**, so the two paths cannot diverge. Test
   `test_authoritative_run_is_inspect_free_and_of_record`.
2. **Separate checker_verdict from parity** — *FOLDED:* scorer surfaces
   `checker_verdict` + `parity`; the authoritative CLI reports candidate verdict
   dist (8 pass / 13 fail) distinct from parity (21/21).
3. **Non-trainable model-output lineage gate** — *FOLDED:* `stamp_candidate_
   provenance()` is now **default-deny** (only trusted `replay`/`reference` sources
   trainable; any live model name → `model_generated`/`trainable=false`) and is
   wired into the scorer keyed on the **solver source**, not copied gold tier.
   Tests `test_provenance_default_deny`, `test_live_model_candidate_is_not_trainable_gold`.
4. **assert_no_judge overclaim** — *FOLDED:* docstring corrected to
   "necessary-but-not-sufficient"; enforced in the scoring path, not just ingest.
5. **Optional-dep boundary** — *FOLDED:* `test_core_lane_does_not_import_inspect_ai`.

**ADOPTION-BLOCKERS (NOT folded — honest, full-migration scope):**
- Scorer/finalizer **allowlist** with a real inspect **integration test** proving no
  alternate/model scorer can be substituted (a per-lane manifest).
- **Sandbox** for live model-authored code before execution — the checker assumes
  trusted local code (`checker.py:11-13`); live gen needs inspect's docker sandbox
  (on gravebuster, never local Docker).
- **Ledger-authorized promotion** transition (model_generated → trainable is a
  separate explicit step, never automatic on a green scorer).
- **Exact-pin + hash-lock** inspect/checker/adapter versions, stamped per run.
- Drift negative tests (wrapped model graders, multiple scorers, wrong-score
  selection, metric-threshold promotion, runtime mutation, optimized-Python).

**Boundary verdict (honest):** now **clean-by-construction for the replay spike** —
authoritative vs advisory split is real in code, candidate provenance is
default-deny, no judge in the verdict-of-record path. It is **NOT yet enforced
against a hostile/misconfigured live solver**; that gap (allowlist-proven,
sandbox, ledger promotion) is the **adoption gate**, not a spike blocker. Do not
claim a clean full-migration pass until the adoption-blockers above are built.

## 6. Artifacts
- Adapter: `evals/inspect_port/coding_inspect.py`
- Tests: `evals/inspect_port/test_governance.py` (9 green)
- Run log: `evals/inspect_port/logs/*_coding-lane_*.eval`
- Optional dep: `pyproject.toml` `[project.optional-dependencies] inspect`
- Red-team (sol@xhigh): `reviewed/inspect-ai-adoption-sol-redteam-2026-07-14.md`
