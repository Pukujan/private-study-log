# The Same-Family Bias Firewall

**Mechanically revoking an orchestrator's authority to judge its own model family**

*Owner-supplied, AI-assisted. Citations AI-suggested; core refs (Panickssery, Prometheus-2, LLMBar,
JudgeBench, position-bias) are real, a few 2026 arXiv IDs UNVERIFIED. Filed to the Cortex study log
2026-07-22. Companion to `mechanically-controlling-llm-orchestrators` — this is the specific bias
mechanism for that architecture.*

## Thesis

This is a credible self/family-preference failure, but the correct mechanical response is **not** to let
a neutral judge (Prometheus) "argue with" the orchestrator (Opus). The stronger approach:

> **Keep Opus as the planner, but mechanically revoke its authority to judge Claude-family work.**

LLM evaluators can recognize and favor their own generations, sometimes even without disclosed source
identity [1]; the bias can extend to same-family models and persist under objective rubrics [2][3].
But not every same-family preference is bias — stronger models may genuinely be better, and earlier
self-preference measurements had confounds [4][5]. Therefore: **do not auto-reject Claude work —
disqualify Claude from being its final evaluator.**

## Provenance and blinding are compatible

Do not discard provenance; split it into two planes. A **trusted provenance plane** keeps
artifact→model, signatures, timestamps, prompts, run IDs. An **evaluation plane** shows the deciding
model only opaque candidate IDs + rubric + tests + evidence. After the evaluator commits its verdict:
seal + hash the verdict → reveal provenance to audit → compute conflict/bias stats → store verdict +
full provenance. This is **provenance-preserving blinding, not provenance removal.** Caveat: anonymity
alone is insufficient — a model can recognize its own stylistic distribution [1], so you also need
**same-family judge exclusion**, not just hidden labels.

## The mechanical bias firewall

Deterministic middleware between the orchestrator and all evaluation decisions:

```
def authorize_judgment(event):
    same_family = (event.judge_family == event.candidate_family)
    if same_family and event.action in {"accept","reject","rank","adjudicate","terminate"}:
        return {"allowed": False, "reason": "SAME_FAMILY_CONFLICT",
                "required_route": "independent_evaluation"}
    return {"allowed": True}
```

On a same-family conflict: the orchestrator verdict becomes **advisory only**, it cannot advance or
terminate the workflow, and an independent evaluation is required. **The orchestrator cannot override
this decision through natural-language reasoning** — that is the crucial part.

## Where Prometheus fits

Prometheus 2 (open evaluator model, rubric-based direct scoring + pairwise) [6] is useful as an
independent shadow judge, rubric critic, disagreement detector, evaluator for Anthropic-generated
artifacts, and structured-feedback source. It should **not** be the sole truth oracle, redirect the
workflow itself, weaken gates, be trusted over executable evidence, or be the only replacement for Opus.
Safer route: Claude artifact detected → Opus may review but cannot decide → Prometheus 2 (anonymous) +
a non-Anthropic frontier judge (anonymous) + deterministic checks → controller combines evidence. **For
code, deterministic evidence dominates:** `tests fail + type-check fails + Opus says Claude is correct =
FAIL`. No model vote overrides an executable contradiction.

## Trigger on conflicted decisions, not generation

Running an extra judge on every Claude *call* adds cost and risks a new circular loop. Trigger on
**same-family relationship AND a decision with pipeline authority**: Claude generates code/researches →
no extra judge yet; Opus accepts Claude output / rejects non-Claude work / overrides another judge /
attempts termination → independent judge / adjudication / evidence-gate first.

## Mandatory counter-bias checks

1. **Position-swap:** run each pair twice with order reversed; a changed winner = unstable (position bias
   is well documented [7]).
2. **Same-family exclusion:** a model's judgment of its own family gets **weight = 0** (may criticize, not
   decide).
3. **Independent first judgments:** every judge commits before seeing others' opinions (else the first
   persuasive critic anchors the rest).
4. **Evidence-first:** give the judge requirement + candidate + test evidence + observed behavior +
   rubric; do **not** give other verdicts, vendor identities, Opus-written summaries, or "Claude says
   this is correct."
5. **Disagreement as a real state:** PASS / FAIL / DISPUTED / INCONCLUSIVE — Opus cannot translate
   DISPUTED into PASS.
6. **Refusal containment:** when Opus refuses to continue because another model's work was accepted →
   `ORCHESTRATOR_NONCOMPLIANCE` → freeze Opus authority → preserve completed artifacts → resume via
   deterministic workflow or a fresh planner. **The refusal is a worker crash, not a veto.**

## Two different "Prometheus" systems

Prometheus 2 = an LLM judge. Prometheus monitoring + Alertmanager = metrics/alert-routing. The monitoring
side can track `same_family_conflict_total`, `orchestrator_override_attempt_total`,
`orchestrator_refusal_total`, `judge_position_flip_total`, `judge_disagreement_total`,
`claude_family_acceptance_rate`, `non_claude_acceptance_rate`. But Alertmanager groups/dedupes/routes
alerts [8] — it should **not** be the synchronous enforcement boundary. Enforcement belongs in the
middleware/authorization kernel; monitoring records and alerts on what happened.

## Public bias-checking benchmarks

LLMBar (adversarial instruction-following pairs) [9]; JudgeBench (objectively-labelled hard pairs across
knowledge/reasoning/math/coding) [10]; Self-Preference/DBG (measures self-preference controlling for real
quality diffs; public code/data) [3]; Prometheus-Eval (public models + rubric utilities) [6];
position-bias suites (run every pair twice, order reversed) [7]. Track by evaluator family: gold-label
accuracy, false-accept, false-reject, same-family preference delta, position-swap flip rate, inter-run
inconsistency, disagreement-with-executable-evidence.

## Recommended architecture

Provenance registry (full identity) → hidden mapping → anonymization broker → Candidate A/B/C → {Prometheus
evaluator, external judge (other family), deterministic checks} → deterministic adjudicator → provenance
re-attached after verdict → audit record.

> **Opus may orchestrate work, but it may not adjudicate work produced by Claude-family models — and it
> may never override deterministic evidence or an independent dispute state.**

That preserves Claude Code as the primary productive environment without letting Claude-family loyalty,
stylistic familiarity, or genuine disagreement capture the whole pipeline.

## References

[1] Panickssery, Bowman, Feng. "LLM Evaluators Recognize and Favor Their Own Generations." NeurIPS 2024. arXiv:2404.13076.
[2] Pombal, Rei, Martins. "Self-Preference Bias in Rubric-Based Evaluation." arXiv:2604.06996, 2026 (UNVERIFIED).
[3] Chen et al. "Beyond the Surface: Measuring Self-Preference in LLM Judgments." EMNLP 2025. arXiv:2506.02592.
[4] Chen et al. "Do LLM Evaluators Prefer Themselves for a Reason?" arXiv:2504.03846, 2025.
[5] Roytburg et al. "Are LLM Evaluators Really Narcissists?" arXiv:2601.22548, 2026 (UNVERIFIED).
[6] Kim et al. "Prometheus 2." EMNLP 2024. arXiv:2405.01535.
[7] Shi et al. "Judging the Judges: Position Bias in LLM-as-a-Judge." arXiv:2406.07791, 2024.
[8] Prometheus Project. "Alertmanager Documentation."
[9] Zeng et al. "Evaluating LLMs at Evaluating Instruction Following (LLMBar)." ICLR 2024. arXiv:2310.07641.
[10] Tan et al. "JudgeBench." arXiv:2410.12784, 2024.
