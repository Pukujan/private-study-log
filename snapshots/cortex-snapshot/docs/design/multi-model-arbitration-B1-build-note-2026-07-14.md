# GAP B1 build note — multi-model arbitration as a shadow/quarantine-only lane

**Date:** 2026-07-14 · **Status:** BUILT (TDD, 15 frozen tests green) · CLI-only, no MCP tool.
**Module:** `cortex_core/arbitrate.py` · **CLI:** `cortex-arbitrate` · **Tests:** `tests/test_arbitrate.py`

## What this is

The DAFE pattern (2 independent cross-family jurors + a *conditional* third
tiebreaker) for tasks with **no deterministic oracle and no available human** —
the middle rung of Cortex's evidence hierarchy. Built to the corrected design
verdict: **ADOPT, but strictly shadow/quarantine-only**
(`docs/design/multi-model-arbitration-verify-codex-2026-07-13.md` §2/§3;
critique-fable §3; in-cortex §4/§5).

## The hard guarantees (frozen by tests)

Every output is a hard-quarantined `advisory_semi_gold` record whose trust flags
are **structurally hard-coded false** in `AdvisoryRecord.to_dict()`:
`is_gold=False, trainable=False, promotable=False, can_mutate_state=False,
can_authorize_action=False, quarantined=True`. `write_advisory()` refuses to
persist anything that is not this exact non-gold type (belt-and-suspenders).

The lane **defaults to ABSTAIN** — the owner is a non-expert, which *increases*
the need for abstention (Codex §4: model agreement is not authoritative). The
three tests the coordinator required:
- **(a) disagreement → ABSTAIN, no gold** — `decide()` returns ABSTAIN when two
  jurors disagree and the conditional third hasn't/can't resolve.
- **(b) output quarantined/advisory-tagged** — `record_type == "advisory_semi_gold"`,
  written only under `<workspace>/arbitration/quarantine/`.
- **(c) never writes a trainable-gold sink** — a static source guard asserts the
  module never names `write_cross_vendor_results`, never imports
  `cortex_core.promotion`, and never emits a `cross_vendor_synthetic_gold-` file.

## Decision logic (abstain-first)

```
strong_agreement := ≥2 opinions, DISTINCT families, SAME decisive verdict
                    (supported/strongly_supported/unsupported), each ≥ min_conf (0.7)

decide(jurors, arbiter):
  jurors strongly agree                      → RESOLVED_WITH_EVIDENCE (advisory, stop; DAFE cost win)
  no arbiter yet, no agreement               → ABSTAIN (the floor)
  arbiter forms cross-family majority        → RESOLVED_WITH_EVIDENCE (advisory)
  arbiter itself UNVERIFIABLE                → ABSTAIN (evidence too thin)
  confident SUPPORTED vs UNSUPPORTED split,
    arbiter engaged but no majority          → NEEDS_HUMAN_BINARY
  else                                       → ABSTAIN
```

Terminal set is exactly `{RESOLVED_WITH_EVIDENCE, ABSTAIN, NEEDS_HUMAN_BINARY}` —
there is **no** resolve-to-gold / promote outcome. `RESOLVED_WITH_EVIDENCE` is
still advisory: council agreement, never ground truth.

## Research-first decisions (cited)

- **DAFE 2-start + conditional third**, not vendor-lane 3-start (critique §3,
  Codex §3). Implemented as two jurors; third arbiter only on non-agreement.
- **Exclude Prometheus** (κ=0 in the one real run; absent from all Stage-2 paths).
  Not in `JUROR_TIERS`; `pick_juror_tiers` also skips any `prometheus*` tier.
- **Anti-circular juror selection** — `pick_juror_tiers(exclude_families=...)`
  drops the artifact's authoring family (e.g. `anthropic`), one tier per family
  (`evals/README.md` "Fable authored → Fable judged = forbidden").
- **12000 max_tokens floor** respected: the real-dispatch seam applies
  `judge.apply_min_max_tokens(tier, 12000)` before calling `llm_judge` (below the
  floor reasoning tiers silently return `content=""` — `judge.MIN_MAX_TOKENS_BY_TIER`).
- **≤1 targeted research round** on the dispute (Tool-MAD: >1 hurts) — `research_fn`
  is an optional single-shot enrichment seam, best-effort.
- **`changed_correct_to_incorrect`** is a first-class field (sycophancy metric,
  demanded "from day one"). It is `None` in the live no-oracle path (honestly
  unknown), computed only when a reference verdict is supplied — never a
  fabricated `False`.

## The cross_vendor_synthetic_gold minting path — verified, untouched

Codex was right: the path **still exists**. `ops/calibration_panel.py`
`write_cross_vendor_results()` (≈:244-287) writes
`calibration/results/cross_vendor_synthetic_gold-<stamp>.json` on ≥3-family
agreement, and `cortex_core/promotion.py` still classifies that tier as trainable
(`TRAINABLE`, `test_promotion.py`). **This build does not touch either.** The
arbitration lane is fully separate: it imports neither the panel writer nor the
promotion module, and its only sink is `arbitration/quarantine/`. Closing that
legacy minting path is a *different* task (out of scope here); this lane simply
never feeds it.

## Honest debt

- **No held-out validation gate yet** (Codex §3.5). Per the design, arbitration
  outputs must stay labeled experimental/untrusted until a frozen, contamination-
  checked, oracle-backed selective-accuracy + false-resolution gate passes
  (adversarial shared-source / stale-source / prompt-injection cases, vendor-family
  ablation). Not built — this ships the mechanism + the quarantine contract, not a
  trust claim.
- **`min_confidence=0.7`** is a conservative prior, not a calibrated threshold; no
  prior corpus decision pinned this exact value. Tunable via `--min-confidence`.
- **Targeted-research phase is a stub seam** — `research_fn` is injectable but not
  wired to `cortex-research` by default (offline-first for testability). Wiring it
  is a small follow-up.
- Live CLI needs configured judge keys (`.env`); with none it fails honestly
  (exit 2) rather than fabricating a verdict.
