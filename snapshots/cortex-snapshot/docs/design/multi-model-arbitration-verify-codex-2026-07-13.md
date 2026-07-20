# Independent verification — corrected multi-model arbitration design (Codex, second reviewer)

**Date:** 2026-07-13  
**Verdict:** **Corrections are real but insufficient. ADOPT only as a quarantined, source-backed decision aid; do not trust or train on its outputs until a held-out oracle-backed validation passes.**

## 1. Residual overclaims

1. **The REVIEW branch is still prose, not a small reuse of an existing abstention path.** REVIEW currently has exactly pass → `CLOSEOUT` and fail → `IMPLEMENT` (`cortex_core/state_engine.py:114-119`, `cortex_core/state_engine.py:1240-1266`, `cortex_core/state_engine.py:1288-1323`). Exhausting the caps produces terminal `ABANDONED`, not `ABSTAIN`, a human escalation, or a successful uncertainty result (`cortex_core/state_engine.py:1325-1357`). Thus the insertion point is correctly renamed REVIEW, but “existing path,” “logged SUCCESS,” and “~2-line addition” in the design (`docs/design/multi-model-arbitration-in-cortex-2026-07-13.md:76-85`) overstate what exists and understate the schema/API/tests required.

2. **The DAFE correction is not completed in §2.** The table still labels a cited Cortex design “Two judges + conditional arbitration” (`docs/design/multi-model-arbitration-in-cortex-2026-07-13.md:35`), but that source explicitly starts with **three** jurors, then adds two (`docs/research/vendor-lane-FINAL-synthesis-2026-07-10.md:143-150`). The proposed §4 protocol does use two families plus a conditional third (`docs/design/multi-model-arbitration-in-cortex-2026-07-13.md:70`), but it is new design, not an existing Cortex match. Move the row to gaps/prior design and state that it is being superseded.

3. **“The council never mints gold / deterministic checkers stay the only gold source” is false as a repository-wide present-tense claim.** The running calibration panel promotes ≥3-family agreement and writes files literally named `cross_vendor_synthetic_gold` (`ops/calibration_panel.py:186-241`, `ops/calibration_panel.py:244-280`). Stage 2 itself correctly produced none (`evals/reports/STAGE2_SUMMARY.md:35-42`), but the legacy panel can mint it. The corrected design must require that arbitration outputs use a non-gold type such as `advisory_semi_gold`, are excluded from training/promotion, and cannot enter any gold export. Otherwise “never mints trainable gold” is an intention contradicted by live code and naming.

4. **Prometheus is correctly moved to gaps, but §2 maturity text still lists its veto among “rows above” even though it is no longer a row** (`docs/design/multi-model-arbitration-in-cortex-2026-07-13.md:39`). This is editorial, not architectural. The substantive treatment is now honest: the real run had κ=0 and near-universal punts (`inbox/HERMES-RESUME-golden-eval-phases-2026-07-09.md:43-47`). Keep Prometheus out of the first build.

5. **The research hierarchy is presented too universally.** DAFE/CLEV studies evaluation of free-form QA, Tool-MAD studies fact verification, and ReConcile reports benchmark reasoning gains; they do not establish a general ordering for subjective design, legal, medical, or other high-stakes decisions. The hierarchy at `docs/design/multi-model-arbitration-in-cortex-2026-07-13.md:13-19` should be labeled a Cortex risk policy inferred from bounded studies, not a peer-reviewed universal ranking.

6. **Several numerical/causal claims lack actual citations.** The design has no bibliography, and file names are not citations for the research claims at `docs/design/multi-model-arbitration-in-cortex-2026-07-13.md:21-26`. In particular, Tool-MAD supports heterogeneous tools/adaptive retrieval and “up to 5.5%” on four fact-verification benchmarks (`docs/cortex-1/tool-mad-fact-verification.md:12`), but the fetched source does not support the design’s “>~3 rounds stops helping and can hurt” attribution. Likewise the “2.1–3.4× cost” and inter-human/inter-LLM agreement claims need their exact primary papers and experimental scope or must be removed.

## 2. Adopt or hold under the non-specialist constraint

**Adopt the tier, but only as a quarantined fallback decision aid.** The safer/simpler baseline is already the design’s default: source retrieval plus explicit `ABSTAIN`, with the two-juror/conditional-third path opt-in only when a decision is useful and reversible. A non-specialist cannot turn consensus into validation; the tier is valuable because it can expose disagreements and evidence, not because it substitutes for expertise.

The concrete failure mode that makes adoption wrong is **correlated, source-laundered error**: different vendors repeat the same false or misapplied source, agree confidently, and the owner treats agreement as truth. This is especially likely where all models share web/training sources or cannot assess domain-specific applicability.

The decisive guardrail is **hard provenance quarantine**: every output is typed `advisory_semi_gold`, never trainable/promotable, and remains `ABSTAIN` unless each decisive atomic claim has independently retrieved primary-source support that passes a checker or a held-out calibrated evidence-entailment rule. For high-impact or irreversible actions, arbitration may summarize evidence but must never authorize execution.

## 3. Research fidelity, citations, and the smallest safe build

The design is **directionally faithful, not yet citation-faithful**:

- DAFE/CLEV does support two primary judges with a third only on disagreement (`docs/cortex-1/dafe-dynamic-arbitration-free-form-qa.md:12-37`). It does **not**, from the locally fetched abstract, validate Cortex’s entire five-phase research-and-arbitration protocol or its use as semi-ground-truth outside free-form QA.
- Tool-MAD supports diverse external tools, adaptive retrieval, faithfulness/relevance scoring, and up to 5.5% improvement on four fact-verification benchmarks (`docs/cortex-1/tool-mad-fact-verification.md:12-37`). Cortex’s “one targeted round” is a conservative engineering choice, not a Tool-MAD result unless the full paper supplies that exact ablation.
- ReConcile supports heterogeneous-model value and reports up to 11.4% across seven reasoning benchmarks (`docs/cortex-1/reconcile-diverse-llm-consensus.md:62-66`), but its mechanism includes multi-round persuasion. It is evidence for diversity, not proof that consensus is truth.
- The sycophancy limitation must cite the exact inter-agent study for disagreement collapse/lower-than-single-agent accuracy, plus the exact source for changed-correct-to-incorrect and any 2.1–3.4× cost figure. Do not bundle these as generic “sycophancy work.”

At minimum cite full bibliographic entries/links for **DAFE/CLEV (arXiv:2503.08542), Tool-MAD (arXiv:2601.04742), ReConcile (ACL 2024, DOI 10.18653/v1/2024.acl-long.381), and the specific multi-agent sycophancy/MAD-limits papers**. Attach each quantitative claim to the paper, benchmark, comparator, and limitation.

### Minimal safe first build

Ship a **read-only shadow evaluator**, not a REVIEW transition:

1. Eligible input: factual, source-resolvable questions with no available deterministic checker and no expert; exclude high-stakes/irreversible decisions.
2. Two different vendor families independently retrieve and answer. Require atomic claims, primary-source URLs, short evidence spans, and source timestamps; blind identities and ordering.
3. If both answers and decisive evidence agree, emit only an advisory packet. If either answer, evidence, or entailment differs, call one different-family arbiter with both blinded packets; it may only select a source-supported claim set or `ABSTAIN`. No free-form compromise and no further rounds.
4. Persist provenance, independence/vendor family, source overlap, verdict, abstention reason, and `changed_correct_to_incorrect`. Exclude Prometheus. Never write `gold`, training, promotion, ledger, or action-authorizing records.

### Held-out gate before trusting any output

Build a frozen, contamination-checked set from tasks that *do* have authoritative answers/checkers, then hide those answers and run the exact no-oracle protocol. Gate by task type, not pooled average. Before any user-visible trust claim require, with predeclared confidence intervals:

- no worse selective accuracy than the strongest single independently researched model at matched cost;
- materially better accuracy at the chosen coverage, with abstentions scored as abstentions rather than silently dropped;
- citation existence, primary-source quality, and claim-to-evidence entailment measured separately;
- false-resolution rate and changed-correct-to-incorrect rate below frozen limits, including adversarial shared-source, stale-source, prompt-injection, and correlated-error cases;
- calibration/coverage curves and vendor-family ablations that still pass when one family and duplicated sources are removed.

Until that gate passes on a genuinely held-out set, all live outputs must be labeled **experimental/untrusted**, even when unanimous.

## 4. The remaining conceptual mistake

Model arbitration is not a human-expert substitute. It substitutes for **additional model sampling and structured criticism**. Primary sources can establish what a source says; they do not establish that the source is current, applicable, complete, or correctly interpreted in the owner’s situation. The owner’s lack of expertise therefore increases the need for abstention and scope limits—it does not increase the epistemic authority of vendor agreement.

## Tight verdict

- **Overclaims remaining: YES** — REVIEW/ABSTAIN is not implemented and is not ~2 lines; the §2 DAFE row still cites a 3+2 protocol; live code still mints `cross_vendor_synthetic_gold`; the research hierarchy is overgeneralized; several numeric sycophancy/round/cost claims lack exact primary citations.
- **Recommendation: ADOPT, quarantined/shadow-only** — decisive guardrail: a hard `advisory_semi_gold` type that cannot train, promote, mutate state, or authorize action, and defaults to `ABSTAIN` unless independently retrieved primary evidence supports every decisive claim.
- **Minimal safe first build:** two blind source-backed vendor families, one conditional different-family arbiter on disagreement, one round, abstention allowed, full provenance, no Prometheus, no state transition, and no gold/training path; trust nothing until a frozen held-out oracle-backed selective-accuracy and false-resolution gate passes.
