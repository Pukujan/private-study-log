# Adversarial critique — "Multi-model arbitration in Cortex" (Fable, disagree-by-default)

Date: 2026-07-13. Target:
`docs/design/multi-model-arbitration-in-cortex-2026-07-13.md`. Convention:
the target was written by a Claude model about Claude-adjacent design — circular
by construction — so this critique re-verifies every load-bearing citation
against HEAD and tries to break the design, not endorse it. Every claim below is
cited to a file:line I opened this session.

---

## VERDICT IN ONE LINE

The doc's *diagnosis* is sound and its honesty in §3/§7 is real, but it contains
**one hard factual error** (§5 branches on a `VERIFY` phase that does not exist
in the engine it targets), **one overclaiming table** (§2 presents coded-but-
never-run and prose-only mechanisms as things Cortex "already matches"), and
**one internal contradiction** (2-juror-start vs 3-juror-start between §4 and the
§2 citation). All three are the kind a non-Claude reviewer will reject on sight.

---

## 1. Does §2 "where Cortex already matches" OVERCLAIM? (re-verified against HEAD)

I re-read each cited source. Row-by-row verdict:

| §2 row | Cited evidence | Verified? | Verdict |
|---|---|---|---|
| External oracle > council; 3,528 records; Stage-2 zero judge-gold | `STAGE2_SUMMARY.md:37-42` | **YES, accurate** — "Stage 2 ran **no judge panels** and produced **no cross_vendor_synthetic_gold**" (:37-40) | **KEEP.** The strongest, cleanest claim in the doc. |
| Heterogeneous families, not copies | `ops/calibration_panel.py:42-65` | Partially — `PANEL_TIERS`(:42-53)+`PANEL_FAMILIES`(:56-65) exist and span 8 families | **DOWNGRADE.** The doc says "`PANEL_FAMILIES` enforces the ≥3-family count." It does **not** — it is a plain dict. Enforcement is a *different* file: `min_k_families` gate (`promotion.py:75-78`). Cite the gate, not the dict. |
| Blind + randomize | `evals/README.md:44-58` | **YES, accurate** — blinding(:44-48), randomization+`position_unstable`(:50-53), anti-style prompt(:55-58) | **KEEP.** |
| Two judges + conditional arbitration | `vendor-lane-FINAL-synthesis:143-152` | Verified the passage exists — but it describes **three** jurors up front (":143-152 step 2: *three independent juror votes*"), then +2 on dispute | **DOWNGRADE + FIX (see §contradiction).** It is honestly labeled "design intent," which §3.1 reinforces — but it is mis-paired with the DAFE "two + conditional third" control it's placed against. |
| Independence / anti-circular | `evals/README.md:23-28` | **YES, accurate** — "Fable authored → Fable judged → Fable label = gold is circular and forbidden" (:25-26), retraction of "no family bias" (:26-28) | **KEEP.** |
| Abstain as legal outcome | Stage-2 2D `UNVERIFIABLE` | **YES** — confirmed independently (`evals/objective_research/citation_checker.py:12,69`) | **KEEP.** |
| Conditional required-veto arbiter | `promotion.py:87-92` | Code is accurate: `prometheus_not_dissenting_gate` requires present ∧ ¬dissent (:87-92) | **DOWNGRADE — this is the worst overclaim.** The gate is **coded but has never fired in a real verdict.** Two independent confirmations: (a) Prometheus scored **κ=0** in the one real panel run (`inbox/HERMES-RESUME-golden-eval-phases-2026-07-09.md:43-47` — "answered `unverifiable` almost everywhere," row possibly invalid); (b) `STAGE2_SUMMARY.md:37-42` — Prometheus was **absent from all Stage-2 verdict paths**, "its required-veto role has nothing to gate here." So the row belongs in §3 (gaps), not §2 (matches). |

**Confirmed overclaims (the list the coordinator asked for):**
1. **"`PANEL_FAMILIES` enforces the ≥3-family count"** — false; the dict enforces
   nothing. Enforcement is `promotion.min_k_families` (`promotion.py:75-78`).
2. **The "conditional required-veto arbiter" row** presented as a *match* — the
   veto is coded (`promotion.py:87-92`) but **never executed a real arbitration**
   (κ=0; absent from Stage-2). Must move to §3.
3. **The §2 table header itself** — "Where Cortex already matches it" — flattens
   three different maturity levels: *coded-and-exercised* (oracle lanes, blinding,
   anti-circular), *coded-but-never-run* (Prometheus veto, min_k_families on a
   live dispute), and *prose-only* (the 2-1→arbitrate→add-jurors escalation,
   which has **zero code** — grep for `arbitrat|2-1|juror|dissent|round` in
   `ops/calibration_panel.py` returns **no matches**). A non-Claude reviewer will
   read the undifferentiated table as claiming a running arbitration capability
   that does not exist. Split the column into those three tiers.

**§3 completeness check (coordinator asked me to confirm the gaps are stated
honestly and completely):** §3.1 (rounds are design-not-code), §3.2 (no
research-first inside the panel), §3.3 (Prometheus κ=0), §3.4 (no state-machine
default) are all **verified true and correctly stated**. The single addition §3
needs: state explicitly that the panel file `ops/calibration_panel.py` contains
**no arbitration/round/juror-escalation code at all** (grep-confirmed empty) —
§3.1 implies it but should say it flatly, because §2 row 4 and row 7 currently
read as if that code exists.

---

## 2. §5 state-machine design — is the default right, and does it branch correctly?

### The hard error first
§5 says the fix is to "branch in the existing **VERIFY** phase" and prints the
chart `SEARCH → RESEARCH → SDD → TDD → IMPLEMENT → VERIFY → DOC → CLOSEOUT`.

**There is no `VERIFY` phase in the engine.** The actual server state machine is
`BUILD_TRACK` in `cortex_core/state_engine.py:83-127`:

```
SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT → DONE
```

No `VERIFY`, no `DOC`, no `TDD`, no `SDD` node exists in code. The chart the doc
prints is the **`.cortex` wrapper's** narrative doc (`STATE-MACHINE.md:17`),
which is a *different artifact* from the engine and is explicitly "not a gate"
(`STATE-MACHINE.md:42-44`). **The doc conflates the wrapper's prose chart with
the engine it wants to modify.** You cannot "branch in the existing VERIFY
phase" of a machine that has no VERIFY phase.

### The corrected branch point
The real adjudication node in the engine is **`REVIEW`** (`state_engine.py:114-120`),
which already has the exact shape arbitration needs: an `advance_tool`
(`cortex_submit_review`), a forward edge (`next: CLOSEOUT`), and a **rework edge**
(`rework_to: IMPLEMENT`) with `rework_cap: 2` / `esc_cap: 2`
(`state_engine.py:79-82,117`). Arbitration should be a *verdict source feeding
REVIEW*, not a new phase. Concretely:

```
REVIEW (state_engine.py:114):
  ├─ deterministic oracle exists for this task_type?  → run it; it decides (advance | rework_to IMPLEMENT)
  ├─ else human available + review wanted?            → existing escalation (esc_level++, then ABANDONED via CLOSEOUT)
  └─ else (no oracle, no human, "auto"):
        → run the arbitration service (§3 below), which returns exactly one of
          {resolved-with-evidence → advance | ABSTAIN → CLOSEOUT w/ honest-uncertainty | needs_human_binary → escalate}
```

Note the engine **already abandons via CLOSEOUT past `esc_cap`**
(`state_engine.py:79-80`) — so "ABSTAIN is a legal terminal" is not a new
concept to add; it is an existing exit that today carries no evidence packet.
The build is: give that exit an *evidence-bearing, honestly-labeled* form, and
add arbitration as a third verdict source at REVIEW.

### Is "research-first arbitration with ABSTAIN/escalate" the right default?
**Directionally yes, but it is not the *simplest* safe default, and the doc
overstates its necessity.** Two objections a skeptic will raise:
1. **Most "auto everything" tasks in this repo DO have an oracle** (the entire
   objective-lane thesis, `CLAUDE.md` Stage 2). Routing to a council should be
   the **rare** branch, not the headline. The safer default is: *no oracle AND
   no human → **ABSTAIN + flag for human by default**, and only invoke the
   multi-model council when a pre-registered task_type says council-adjudication
   is worth its cost.* Council is itself a cost/latency/sycophancy risk (the
   doc's own §1 cites 2.1–3.4× cost and debate-induced regressions); making it
   the default for every un-oracle-able task inverts the hierarchy's own caution.
2. **ABSTAIN-by-default is strictly safer than council-by-default** and is a
   two-line change (REVIEW's no-oracle/no-human branch → honest-uncertainty
   CLOSEOUT). The council is the *opt-in enrichment* on top, gated by task_type.
   The doc should lead with abstain-default and present the council as the
   escalation, not the reverse.

So: the doc's terminal-state set `{resolved | ABSTAIN | escalate}` is correct
and the "never fake certainty" principle is correct; the **default routing is
inverted** — abstain/escalate is the safe floor, council is the paid upgrade.

---

## 3. §4 five-phase protocol — faithful to the research? Buildable? Smallest first build.

### Faithfulness — one real contradiction
- Phases map cleanly to the research controls (blind independent answers →
  no-anchoring; targeted single round → Tool-MAD's ">3 rounds hurts";
  evidence-attack-not-author → sycophancy guard; arbiter-may-abstain →
  compromise-is-wrong-objective). That structure is faithful.
- **But §4 and §2 disagree on juror count.** §4 text: "run Phase 1-2 with **two**
  families, invoke the **third** arbiter only on disagreement" — that is DAFE
  (two + conditional third), and it is what §1 cites as the cost-efficient
  control. §2 pairs "two judges + conditional arbitration" against
  `vendor-lane:143-152`, which actually prescribes **three** independent jurors
  up front, then **+2** on dispute (verified: ":143-152 step 2 'three
  independent juror votes'... step 5 'add two different-vendor jurors'"). Two-
  start and three-start are different protocols with different cost/diversity
  tradeoffs. The doc cites the 3-start design as evidence for a 2-start claim.
  **Pick one and state the tradeoff.** (My recommendation: DAFE 2-start is the
  research-faithful, cost-efficient choice; the vendor-lane 3-start was written
  for a UI-taste lane where diversity-up-front mattered more.)

### Buildable on judge.py / promotion.py / cortex-research? Yes.
The primitives exist: `judge.py` (cross-vendor blind dispatch + `JUDGE_LADDER`),
`promotion.py` gates (`min_k_families` :75-78, `no_flags_gate` :81-84,
`prometheus_not_dissenting_gate` :87-92), blinding maps (`evals/README.md:44-53`),
and `cortex-research` for the targeted-retrieval phase. What is missing is only
the *orchestrator* that sequences them — the doc is right about that.

### Smallest real first build (the coordinator's ask)
A single stdlib orchestrator function `arbitrate(question, task_type) ->
{verdict, evidence, jurors}` that:
1. **Two blind cross-vendor jurors** via existing `judge.py` dispatch (reuse the
   blinding-map machinery; pick 2 tiers from different `PANEL_FAMILIES`
   entries, neither Anthropic if the artifact is Anthropic-authored — the
   anti-circular rule, `evals/README.md:23-28`).
2. **Agree → return `resolved-with-evidence`** (attach each juror's claim +
   citation). Stop. No third model, no research round. (This is the DAFE
   cost win and the ">3 rounds hurts" guard, both from §1.)
3. **Disagree → ONE `cortex-research` targeted retrieval** on the specific
   disputed claim (not a broad re-run), then **one third-family arbiter** tier
   that receives the evidence packet + both juror verdicts but **not model
   identities**, and may return `{accept A | accept B | ABSTAIN |
   needs_human_binary}`. Never forced to pick.
4. **Terminal states** `{resolved | ABSTAIN | needs_human_binary}` — wire the
   last two to REVIEW's existing escalation/CLOSEOUT exits (§2 above), so no new
   phase is added.
5. **Log `changed-correct-to-incorrect`** as a first-class field (the sycophancy
   metric §4 correctly demands) from day one.

Explicitly **out of the minimal build:** the vendor-lane 5-juror supermajority,
Prometheus as a juror (κ=0, unproven — `inbox/...2026-07-09.md:43-47`), and any
promotion of a council verdict into trainable gold (forbidden — council output
is never gold; deterministic checkers only, `evals/README.md:23-28`,
`STAGE2_SUMMARY.md:37-42`). Council verdicts are *advisory adjudications*, and
`needs_human_binary` is the only thing that turns a proposal into state
(`vendor-lane:143-152` step "Only an explicit human binary can turn that
proposal into state, training, or a new permanent check").

---

## 4. What a non-Claude (Codex) reviewer would reject

1. **§5 VERIFY-phase error** — wrong state machine; the engine has REVIEW, not
   VERIFY (`state_engine.py:114`). Non-negotiable fix.
2. **§2 table conflation** — "already matches" presents prose-only and
   never-executed mechanisms beside genuinely-running ones. Split into
   exercised / coded-unrun / prose-only.
3. **Prometheus-veto row** — claimed as a working arbiter; it has never produced
   a valid verdict (κ=0; absent from Stage-2). Move to gaps.
4. **2-start vs 3-start contradiction** between §4 and its §2 citation.
5. **Council-as-default** — inverts the doc's own cost/sycophancy caution;
   abstain-by-default is the safer, simpler floor.
6. **Circularity caveat is correct but insufficient** — §7 says "treat every §2
   mapping as claimed until a non-Claude reviewer confirms," yet §2 still prints
   confident line numbers, two of which (the enforce claim, the veto-as-match
   claim) are wrong. The caveat does not excuse a verifiable miscitation; fix
   the citations rather than defer them.

**What is NOT overclaimed (credit where due):** the 3,528-record / zero-judge-
gold claim (`STAGE2_SUMMARY.md:37-42`), the anti-circular rule
(`evals/README.md:23-28`), blinding/randomization (`:44-58`), and the honest
§3/§7 gap-statements are all accurate and load-bearing. The hierarchy thesis
(deterministic oracle > primary-source grounding > heterogeneous arbitration >
homogeneous debate > self-validation) is genuinely Cortex's founding rule and is
fairly claimed.

---

## SUMMARY (the three deliverables)

**Confirmed overclaims:**
1. "`PANEL_FAMILIES` enforces the ≥3-family count" — the dict enforces nothing;
   `promotion.min_k_families` (`promotion.py:75-78`) does.
2. Prometheus required-veto presented as a §2 "match" — coded
   (`promotion.py:87-92`) but never fired a real verdict (κ=0
   `inbox/...2026-07-09.md:43-47`; absent from Stage-2 `STAGE2_SUMMARY.md:37-42`).
3. §2 table conflates exercised / coded-unrun / prose-only; the arbitration-
   rounds mechanism has **zero code** in `calibration_panel.py` (grep-empty).
4. §4↔§2 internal contradiction: DAFE 2-juror-start vs vendor-lane 3-juror-start.
5. §5 branches on a non-existent `VERIFY` phase.

**Corrected state-machine default:** no-oracle ∧ no-human → **ABSTAIN + flag
for human by default** (a ~2-line addition to REVIEW's exit,
`state_engine.py:114-120`, reusing the existing `esc_cap`→CLOSEOUT abandon path
:79-80). The multi-model council is the **opt-in, task_type-gated upgrade** on
top of that floor — never the default. Terminal set `{resolved | ABSTAIN |
needs_human_binary}`; the oracle (when one exists) and the human (when available)
always outrank the council; ABSTAIN is a logged success, never fake certainty.
Branch point is **REVIEW**, not VERIFY.

**Minimal buildable arbitration service:** one stdlib orchestrator
`arbitrate(question, task_type)` — two blind cross-vendor jurors via `judge.py`;
agree→return with evidence (stop, no third model); disagree→one targeted
`cortex-research` retrieval + one third-family arbiter (identity-blind, may
ABSTAIN/escalate); terminal states wired to REVIEW's existing exits; log
`changed-correct-to-incorrect` from day one. Reuse `min_k_families`/`no_flags_gate`
+ blinding maps. Exclude Prometheus (unproven), exclude any council→gold
promotion (deterministic checkers only), and let only an explicit human binary
turn a verdict into state.
