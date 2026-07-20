# Architecture map — where things actually are

**Read this before answering any "where is X / how does X work" question about this wrapper.**
Several different things get called "the state machine"; a blind grep surfaces the wrong one.
Everything below is vendored in this repo (`cortex_core/`) unless marked **NOT SHIPPED**.

## ⭐ THE state machine (what people usually mean)

| File | What it is |
|---|---|
| **`cortex_core/state_engine.py`** | **The chart engine.** Holds every track's chart (`build`, `research`, `app_build`, `mission`) and owns ALL transitions deterministically — write-lock + `(task_id, seq)` idempotency + atomic claim acquisition. The mission chart is `INTAKE → PARTITION → DISPATCH → MONITOR → MERGE → REVIEW`. |
| **`cortex_core/plane2_driver.py`** | **The coercion.** `run_build()` drives ONE model through the build-track chart. Its docstring: *"'Skipping a phase' is therefore not expressible"* — the engine picks every transition; the model only fills the current phase's content slot. |
| **`cortex_core/mission_driver.py`** | **`run_mission()`** — heterogeneous decomposition: propose → validate → atomic child tasks → parallel child drivers (each mints its **own** server receipt) → deterministic reconcile → MERGE. v0: children run the `app_build` receipt chart. |
| **`cortex_core/decomposer.py`** | The manifest contract + **`validate_manifest()`** — the pure-deterministic spawn gate (coverage / exclusivity / DAG / claim-globs). A model may *propose* a split; this code decides if it's legal. |
| **`cortex_core/fanout.py`** | Fan-out/fan-in: N **free** models fill ONE slot in parallel; the deterministic gate + `rank_passers` pick the winner. Judge-free; free-only guards fail closed. |
| **`cortex_core/receipts.py`** | **Server-owned verdicts.** The server mints a `verdict_id` bound to task + artifact digest + gate identity; a caller can only hand back that id. This is why a model cannot forge "done." Per-worker receipts make the fan-in race-safe. |
| **`cortex_core/govern.py`** | `cortex-govern` — explicit `LEGACY_UNASSURED` local structural loop; real runs require `--legacy-local`. |

> **`.cortex/protocol/STATE-MACHINE.md` is the *zero-install twin*** — a prose mirror of the chart
> for agents that DON'T run the engine. It says "disclosure, not coercion" because without the
> engine nothing enforces. **If you actually run `plane2_driver`/`govern`, phase order IS
> coercive, but evidence authority is still LEGACY_UNASSURED.** Coercion prevents phase skipping;
> it does not prove that model-authored search/research evidence is true.

## NOT SHIPPED (don't look for these here)
- **`hybrid_build.py`** — the SCAFFOLD seam that auto-invokes fanout in the owner's brain. **Absent
  here**, so you get `fanout` + `run_mission` as callable pieces, but **not** the automatic
  fan-out-on-SCAFFOLD coupling. Your driver decides when to call them.
- **`judge.py`, `calibration.py`, `promotion.py`, `promotion_state.py`, `evaluator.py`,
  `arbitrate.py`, `oracle_crossval.py`, `keys*.py`** — the private evaluation/gold/calibration
  layer. Deliberately never vendored. (`model_dispatch.py` is the PUBLIC-safe dispatch shim
  extracted from it — tiers/concurrency/token-floors, **no judging IP**.)

## Other things also called "state machine" (don't confuse)
- **`app_contract.py` + `app_gates.py`** — the deterministic **behavioral check vocabulary** for
  built apps (subprocess + HTTP + sqlite, no LLM). A *gate*, not a lifecycle.
- The **artifact-trust lifecycle** (`observed → … → hard_gold → trainable_gold`) lives in the
  owner's private brain (`promotion_state.py`) — **not here**.

## Plane B — your own local Cortex (over YOUR corpus)
`search.py` (BM25 + vector + **ontology** RRF), `ontology.py`, `packs.py`, `ingest.py`,
`fetch.py`, `freshness.py`, `memory.py`, `config.py` (`CORTEX_WORKSPACE` picks the corpus).
`pip install -e .` → `cortex-ingest <dir>` → `cortex-search --hybrid "<q>"`. Nothing leaves your machine.

## Models
`model_dispatch.py` (tiers/concurrency/token floors), `model_probe.py` (**`cortex-models`** — what
YOUR keys can actually reach), `model_tiers.py` (the tier list: which models are free executors vs
premium reviewers).

## How to answer "where is X" properly
1. Check this map. 2. Read the actual file, cite `file:line`. 3. If the map and the code disagree,
**say so** — the code wins and the map is stale. Never answer "it's not in the files I read"
without checking here first.
