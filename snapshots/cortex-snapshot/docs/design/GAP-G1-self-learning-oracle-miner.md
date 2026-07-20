# GAP G1 — the self-learning oracle-mining loop (main repo)

**Module:** `cortex_core/self_learning.py` · **CLI:** `cortex-self-learning` ·
**Tests:** `tests/test_self_learning.py` (16, all green) · **Built:** 2026-07-14

## What it is

Replay Cortex's OWN past FAILED closeouts (`audit/audit-log-*/agent/*.json`)
against a DETERMINISTIC check — never a judge — to mint local
gold / anti-patterns keyed to Cortex's own tasks. This is the main-repo port of
the wrapper's stdlib `oracle_miner.py`
(`d:/claude/cortex-agent-wrapper/.cortex/scripts/oracle_miner.py`), honoring the
FIXED rule from `LOCAL-DATA-SETUP.md` step 3.

## The FIXED rule (unchanged from the wrapper)

Group closeouts by a normalized task key (same task text across attempts), then:

| condition (deterministic) | label |
|---|---|
| task FAILED, a LATER attempt PASSES its recorded tests | `positive` (the passing closeout is the local gold / CoT) |
| task FAILED, NEVER reaches a passing attempt | `anti_pattern` |
| NO deterministic outcome to decide on | `UNVERIFIABLE` (quarantined; **NEVER guessed**) |

A task that only ever passed is not a failure→fix oracle and is not mined.

## The one real adaptation: deriving the deterministic verdict

The wrapper had a scribe-authored `tests_passed` **bool** per closeout. The main-repo
schema (`cortex_core/audit.py`) does **not** — `tests` is a free-text string, plus a
structured `evidence[]` array (schema v2+). So `test_outcome(rec) -> (bool|None, signal)`
derives the verdict with an explicit precedence and records **which signal decided it**
(provenance), so a human can audit every verdict:

1. `test_evidence_exit` — a v2 structured `evidence` item (`type=="test"`) with an
   **explicit numeric exit/return code** (`exit 0`, `returncode 1`) or a boolean
   `passed`/`ok` flag. Strongest — closest to the wrapper's recorded exit codes.
2. `status_fail` — an authoritative failure `status` (`failed`/`error`), only.
3. `ratio` — a precise keyword-adjacent `N/M tests` ratio (`6/6 tests`, `tests: 2/17`).
4. `fail_signal` / `pass_signal` — a **one-sided** token in the `tests` prose.
5. `ambiguous_mixed` → **None** — both a pass and a fail token in the prose.
6. `none` → **None** — no signal at all.

### Why so conservative (design decisions, each caught on real data)

The anti-oracle rule says a **wrong label is worse than an honest `UNVERIFIABLE`**.
Building against the real 368-closeout corpus surfaced four over-matches that a naive
port would have shipped as false labels — each was tightened:

- **Bare `fail` substring** matched prose like `"1 pre-existing unrelated ... failure"`
  in closeouts that PASSED. → failure tokens now require a nonzero count, a
  `tests failed/failing` phrase, an explicit non-pass verdict, or a nonzero exit code.
- **Bare ratio** matched a length list `"0/1/10/100"` as a `0/1` test failure. → bare
  ratios only count as a pass when `N/N` (equal, nonzero); unequal bare ratios are
  undecidable. Partial ratios must be keyword-adjacent.
- **Mixed pass+fail line** (`"771 passed, 1 failed (pre-existing)"`) is
  indistinguishable by regex from a real partial failure (`"3 failed, 14 passed"`). →
  both-signals-present is `ambiguous_mixed` → `None` (quarantined).
- **`"no tests run"` / `status: blocked`** are absence-of-signal (external block, pure
  investigation), not a test failure. → dropped from the fail vocabulary; blocked/aborted
  are UNVERIFIABLE, never anti_pattern (blaming a blocked-by-endpoint task's approach
  would be a wrong label).
- **Structured-evidence prose fallback** had the SAME trap: a detail reading
  `"850 collected, 1 pre-existing failure"` was minting a fake exit-1. → evidence now
  trusts only an explicit numeric code / boolean flag, never a prose word.

## Hard invariants (frozen by tests)

- **No LLM / judge / network in the verdict path.** Pure deterministic parsing
  (`test_no_llm_or_judge_in_the_verdict_path` inspects the module's imports via AST).
  This is the same trust order as the `evals/` objective lanes: a deterministic checker
  is ground truth; a judge is never in an objective verdict path.
- **Nothing is auto-promoted.** Output is a QUARANTINED JSONL; every record is stamped
  `promoted: false` / `promotion_status: "quarantined"`. Promotion to trainable gold is
  a separate, human-gated step (`cortex_core/promotion.py` — `hard_gold` needs an
  objective checker, `TRAINABLE` tiers). This module deliberately does **not** import or
  call `promotion`.
- **UNVERIFIABLE is surfaced, never dropped and never guessed.**

## Anti-bloat / footprint

CLI-only — **no new MCP tool**. New files only (`self_learning.py`, the test, this
note); the single-line `cortex-self-learning` entry is the only `pyproject.toml`
touch. Stdlib only, offline. Fully reversible.

## CLI

```
cortex-self-learning                 # mine <workspace>/audit -> <ws>/audit/self-learning/oracle_candidates.jsonl
cortex-self-learning --print         # summary to stdout, writes nothing
cortex-self-learning --closeouts-dir DIR --out FILE
```

## Honest status / debt

This is a **mechanism**. Run against the real 368-closeout corpus it mints **163
UNVERIFIABLE, 0 positive, 0 anti_pattern** — the honest result: the corpus records test
outcomes as free-text prose (routinely `"N passed, 1 pre-existing unrelated failure"`)
or evidence without explicit exit codes, so almost nothing is deterministically
decidable, and the miner correctly quarantines rather than guesses. The 16 synthetic
tests prove all three labels + every edge case fire correctly when the data IS clean.

**To make this loop productive on real Cortex tasks** (a forward recommendation, NOT a
backfill-by-guessing): closeouts should record a **structured test exit code** in
`evidence[]` (`{"type":"test","ref":"pytest","detail":"exit 0"}` — already supported by
`audit.py`). Then failure→fix pairs become cleanly mineable. Real promotion stays
human-gated regardless.
