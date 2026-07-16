# Multi-model arbitration in Cortex — what the research says, what we actually do, and what to build

**Date:** 2026-07-13 · **Status:** design doc, **corrected 2026-07-13 after an adversarial Fable critique** (`multi-model-arbitration-critique-fable-2026-07-13.md`) that caught 5 overclaims + a hard error in the first draft — the produce→independent-critique→adjudicate structure this doc argues for, applied to itself. Codex verification of the corrected version still pending. Every "what we do" claim is cited; corrections from the critique are marked **[corrected]**.

> **[Codex second-pass verify, 2026-07-13 — `multi-model-arbitration-verify-codex-2026-07-13.md`]:** overclaims STILL remain after the Fable pass — (a) the REVIEW/ABSTAIN branch is **NOT "~2 lines"** (REVIEW has no clean ABSTAIN/human exit to reuse); (b) the §2 DAFE row still cites the incompatible 3-juror protocol; (c) the code **can still mint `cross_vendor_synthetic_gold`** (`promotion.py`), so "never mints gold" is aspirational, not enforced. **Verdict: ADOPT but shadow/quarantine-only** — arbitration output must be a hard `advisory_semi_gold` type that cannot train/promote/mutate-state/authorize-action and defaults to ABSTAIN; trust nothing until a frozen held-out oracle-backed selective-accuracy + false-resolution gate passes. **Key correction:** model arbitration is NOT a human-expert substitute — it substitutes for more model sampling; the owner's non-expertise *increases* the need for abstention, it does not make model agreement authoritative.

> **Critique corrections folded in:** (1) family-count enforcement is `promotion.min_k_families`, NOT `calibration_panel.PANEL_FAMILIES` (a plain dict); (2) the Prometheus veto is **coded-but-never-valid** (κ=0, absent from all Stage-2 paths) → moved to §3 gaps, not a §2 match; (3) the real code state engine is `SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT` — there is **no VERIFY phase** in code (the VERIFY/DOC chart is the `.cortex` *wrapper's* narrative doc, a different artifact); the branch point is **REVIEW**; (4) the default is **ABSTAIN**, not council (council is the opt-in upgrade); (5) protocol is DAFE two-juror + conditional-third (not three-up-front).

> **One-line thesis:** the peer-reviewed evidence hierarchy — *external oracle > primary-source grounding > heterogeneous model arbitration > homogeneous debate > self-validation* — is **already Cortex's founding rule** at the top rung (deterministic checkers decide, judges never establish ground truth). Where Cortex is weak is the **middle rung**: the arbitration *protocol* for tasks that have **no oracle** is designed-but-not-coded, and the state machine does **not** default to it when a human wants "auto everything." That is the build.

---

## 1. The research hierarchy (from ReConcile, DAFE, Tool-MAD, MAD-limits, sycophancy work)

```
External ground truth / executable oracle
        >  Independent primary-source grounding (research-first)
        >  Heterogeneous model review WITH evidence-based arbitration
        >  Homogeneous model debate
        >  One model validating its own answer
```

Load-bearing findings the user surfaced, and their consequences for us:
- **Diversity helps only if errors differ** (ReConcile +up to 11.4pp; ICML'24 MAD). → use different *families*, not copies.
- **Two judges + conditional third arbitrator** is directly studied and cost-efficient (DAFE) — invoke the 3rd only on disagreement, and ground it in reference answers, not persuasive prose.
- **Research-first grounding beats more debate rounds** (Tool-MAD +up to 5.5pp; >~3 rounds stops helping and can hurt).
- **Consensus is not truth.** Inter-LLM agreement can exceed inter-human agreement (shared bias), and debate induces **sycophancy / false consensus** — agents abandon correct answers under confident wrong peers; unguided homogeneous debate can be *worse* than isolated self-correction at 2.1–3.4× the cost.
- **For factual questions, compromise is the wrong objective.** The arbiter must adjudicate on evidence and be allowed to **abstain / request evidence / escalate**, not split the difference.

## 2. Where Cortex already matches it (cited)

| Research control | Cortex implementation |
|---|---|
| **External oracle > model council** (the top rung) | Objective hard-gold lanes: **deterministic checkers decide, no judge in any verdict path** (`evals/README.md`; `CLAUDE.md` "Objective hard-gold lab"). 3,528 records; Stage-2 minted **zero** judge-consensus gold *by design* — objective replaces the panel (`evals/reports/STAGE2_SUMMARY.md:37-42`). |
| **Heterogeneous families, not copies** | `ops/calibration_panel.py:42-65` defines the panel (GLM/Qwen/Sonnet/GPT-oss/Gemini/DeepSeek/Prometheus/Ollama); **[corrected]** the ≥3-**family** requirement is *enforced* by `promotion.min_k_families` (`promotion.py:75-78`), not by `PANEL_FAMILIES` (a plain dict). |
| **Anonymize + randomize (position/verbosity/self-preference bias)** | Blinding mandatory (`Candidate A/B/C`, true map audit-only); order randomized; `position_unstable` → cannot be a label; anti-style-bias judge prompt (`evals/README.md:44-58`). |
| **Two judges + conditional arbitration** | Design intent present: `docs/research/vendor-lane-FINAL-synthesis-2026-07-10.md:143-152` — unanimous→stop, 2-1→one arbitration round with dissent exposed, then +2 jurors, stop at 4/5, else `needs_human_binary`. |
| **Independence / anti-circular** | Explicit and enforced: *"Fable authored → Fable judged = circular and forbidden"* (`evals/README.md:23-28`); the earlier "no family bias" claim was retracted as confounded (gold AND rubric both Anthropic-authored). |
| **Abstain as a legal outcome** | The faithfulness lane **honestly abstains** (`UNVERIFIABLE`) instead of faking a label (`CLAUDE.md` Stage-2 2D); the evaluator has a first-class `unverifiable` verdict. |

**[corrected] Maturity honesty:** the rows above are a mix — *exercised* (oracle lanes, blinding, anti-circular, abstain — all verified real), vs *coded-but-never-run-on-a-live-dispute* (`min_k_families`, the Prometheus veto). The Prometheus **required-veto arbiter** (`promotion.py:87-92`) is **coded but has never produced a valid verdict** (κ=0 in the one real run; absent from every Stage-2 path) → it belongs in §3 gaps, not here. The DAFE-style 2-1→arbitrate escalation has **zero code**.

**How our design was actually arbitrated:** the detection-over-coercion redesign was reviewed by **two independent frontier reviewers, disagree-by-default** — Fable (Anthropic) and Codex/GPT-5.x — who *converged* that the earlier gates rebuilt "Disease B" coercion (`reviewed/cortex-redesign-vs-past-learning-fable.md`, `...-codex.md`; `docs/design/cortex-redesign-CORRECTED-spec.md`). That convergence-of-independent-critics is the real reason the redesign is trusted, not any single model's say-so.

## 3. Where Cortex falls short of the ideal protocol (the honest gaps)

1. **Arbitration *rounds* are design-not-code.** The running `ops/calibration_panel.py` does a **single-pass ≥3-family exact-match tally** — no 2-1→arbitrate→add-jurors escalation, no targeted-research-on-dispute round. The DAFE-style conditional arbiter lives only in prose.
2. **No research-first grounding *inside* the panel.** Panelists judge from their own knowledge; there is no per-juror independent retrieval + claim-level evidence packet (the Tool-MAD control that matters most). Cortex has strong research-first tooling (`cortex-research`, corpus-first search) but it is not wired into the judge panel.
3. **Prometheus was non-functional in the one real run** (κ=0 — native template likely didn't engage; `inbox/HERMES-RESUME-golden-eval-phases-2026-07-09.md:43-47`). The veto arbiter existed but didn't actually arbitrate.
4. **The state machine does NOT default to arbitration for un-oracle-able tasks** (see §5). This is the gap the owner flagged.

## 4. Can SCC *provide* arbitration as a capability? (Yes — the parts exist)

The pieces are already in `cortex_core/`: `judge.py` (multi-tier cross-vendor dispatch + JUDGE_LADDER + Fable-Max anchor), `promotion.py` (family-count / no-flags / veto gates), `calibration.py` (Cohen's κ, leniency/severity/punt bias audit). What is missing is an **orchestrator** that runs the *research-supported five-phase protocol* on demand:

```
Phase 1  Independent research   — each family answers BLIND, emits atomic claims + a citation
                                  + quoted evidence + per-claim confidence + falsifiers. (no cross-talk = no anchoring)
Phase 2  Blind cross-review     — anonymized/randomized; reviewers classify each claim
                                  {supported | contradicted | insufficient | irrelevant | source-unavailable |
                                   interpretation-dependent} and attack the EVIDENCE, not the author.
Phase 3  Targeted research      — ONLY the unresolved disputes get a fresh, specific retrieval
                                  (not a broad re-run — the Tool-MAD lesson).
Phase 4  Independent arbiter    — receives question + evidence packets + claim critiques + any
                                  deterministic test results + rubric; NOT model identities. May:
                                  {accept A | accept B | supported synthesis | request more evidence |
                                   ABSTAIN | escalate to human}. Never forced to pick.
Phase 5  External oracle        — whenever one exists (tests, calculator, DB query, primary doc,
                                  golden set), it OVERRIDES the council. Council is the fallback, not the top.
```

This is buildable on the existing panel + `cortex-research` + the objective-checker layer. Cost control follows DAFE: run Phase 1-2 with two families, invoke the third arbiter **only on disagreement**. Guardrails from the sycophancy work: **≤1 targeted round** (not open-ended), keep candidates blind, and log `changed-correct-to-incorrect` as a first-class metric so we can detect debate making things worse.

## 5. Does / should the state machine default to this when "auto everything"? (The core answer)

**Today: no.** **[corrected]** The real **code** state engine is `SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT` (`cortex_core/state_engine.py:83-127`) — there is **no VERIFY phase in code** (the `SEARCH→…→VERIFY→DOC` chart is the `.cortex` *wrapper's* narrative doc, `STATE-MACHINE.md:17`, a separate artifact). REVIEW verifies via deterministic checks when they exist; an escalation cap (`esc_cap`) can abandon to CLOSEOUT, but nothing routes an un-oracle-able task to a structured council.

**The correct design — "auto ≠ pretend certainty", and ABSTAIN is the floor (not the council):** the safe default is **abstain + flag human**, NOT convene a council (convening-by-default would contradict this doc's own anti-sycophancy/cost caution). The branch belongs in **REVIEW**, reusing the existing `esc_cap → CLOSEOUT` abandon path:

```
REVIEW exit, when the deliverable can't be deterministically checked:
  ├─ deterministic oracle exists?         → run it. It decides. (top of hierarchy; always wins)
  ├─ else human available + wants review?  → escalate to human (existing path)
  └─ else (no oracle, no human, "auto everything"):
        DEFAULT  → ABSTAIN + flag human   (a logged SUCCESS, ~2-line addition; never fake certainty)
        OPT-IN   → if task_type is council-eligible, run the §4 arbitration as an UPGRADE
                   on top of abstain; terminal states {resolved-with-evidence | ABSTAIN | needs_human_binary}
```

So "automate everything" resolves to: **default to honest abstention; automate the *adjudication* only as a deliberate, task-typed opt-in.** Oracle and human always outrank the council; the council never mints gold (deterministic checkers stay the only gold source). This is the missing default, and abstain-first is what makes "auto" safe for hard, un-checkable tasks.

## 6. How this relates to the A/B/C harness

The Kurzweil A/B/C correctly uses a **deterministic oracle** (`kurzweil_checks.py`), not arbitration — because an oracle *exists* for OCR/TTS (ratio vs ground truth, valid WAV, timing map). That is the top of the hierarchy; using a council there would be a downgrade. **Arbitration is the fallback for the tasks the A/B/C does *not* cover** — the subjective/design/"not enough knowledge" tasks where no `kurzweil_checks.py` can be written. A future arm (a "D" arm, or a separate eval) should test *the arbitration protocol itself* against a held-out set: measure factual accuracy, citation entailment, calibration, **abstention quality**, and the changed-correct-to-incorrect rate — exactly the metrics the research prescribes.

## 7. Honest limits of THIS doc
- Written by a Claude model analysing Claude-adjacent design → **circular by construction** unless an independent non-Claude critic checks it. That is why it is being handed to Codex/GPT-5.6 (and, when reachable, a non-Anthropic third) for claim-level critique before it is trusted. Treat every §2 mapping as *claimed* until a non-Claude reviewer confirms the file:line evidence.
- The corpus citations were gathered by subagents earlier this session; a few (e.g. the exact `calibration_panel.py` line numbers) should be re-verified against HEAD before this doc is load-bearing.
