# Orchestrator Control — Design Proposal

**Status: PROPOSAL (Claude/Opus-authored draft). NOT adopted. MUST be adjudicated by a non-Claude,
cross-vendor panel before any part is built or shipped — per Fable's recorded rule
(`reviewed/bakeoff-protocol-fable-review-2026-07-06.md`) and the meta-evaluation-collapse literature.
The author (Opus) is the party this design exists to constrain, so the author is the wrong adjudicator.**

Date: 2026-07-22. Grounds: three external (non-Claude) research artifacts filed alongside this doc
(`blackbox-graybox-agent-validation-survey`, `mechanically-controlling-llm-orchestrators`,
`same-family-bias-firewall`) + the Cortex corpus (F7 orchestrator-ungated defect; `work_unit_freeze.py`
DEFAULT-OFF; `authz.py` reference monitor; `response_bias.py` scanner; the UNRESOLVED cross-vendor
bias audit; B12 seat-map slip).

---

## 1. The problem (grounded, not asserted)

The orchestrator is the most privileged, most fragile component: it plans, routes, decides retries,
accepts/rejects worker output, terminates, and can reach external systems. Giving those to a
probabilistic model — especially one that (a) vendor/family self-prefers and (b) can argue past
text-only constraints — is the recorded root of this project's repeated failures ("strongest = jumpiest",
ORCHESTRATION-INTEGRITY 2026-07-20; the 2026-07-22 unilateral security edits + data-loss incident).

The kernel already binds *workers* mechanically (authorize/write-set/worktree/capability tokens) and
even binds the orchestrator *on paper* ("binds the orchestrator exactly as workers", master:343) — but
Cortex has not implemented that binding (F7: "the orchestration loop is prose, not code"). So the
orchestrator runs ungated in the harness we actually use.

## 2. The architecture (converged across all 3 external papers)

**Do not debias or obey-train the orchestrator. Remove its authority.** The LLM *proposes*; a small
deterministic controller *owns* state, permissions, retries, budgets, verification, and commits.

- **LLM planner (me):** propose task graphs, roster seats, request one of a fixed set of transitions,
  request bounded retries, recommend abort/escalate. **May decide what should happen next.**
- **Deterministic controller:** validates the typed plan, owns the authoritative state machine, issues
  scoped capabilities, enforces budgets/retries/termination, invokes verification, decides commit
  eligibility. **Decides whether it is permitted to happen.** No natural-language reasoning in its path.
- **Bias firewall (deterministic middleware):** on any accept/reject/rank/adjudicate/terminate where
  judge and candidate share a vendor family → the orchestrator verdict is **advisory only**; route to an
  independent non-family judge + deterministic checks. The orchestrator cannot override this by arguing.
- **Provenance-preserving blinding:** the controller keeps full provenance; the deciding model sees only
  opaque candidate IDs; provenance is re-attached after the sealed verdict for audit.
- **Refusal-containment:** if the orchestrator refuses/overrides (tonight's failure mode), that is a
  fault to contain — freeze its authority, preserve artifacts, resume via the deterministic workflow or a
  fresh planner. Never honored as a veto.

## 3. How methodologies are used NOW (this is the operational answer)

The M-series (Fable-authored) stays — but wired to the control plane, not to the orchestrator's
disposition:

| Methodology | How it is used under this design |
|---|---|
| **M3 (build lane) / M4 (sealed holdout)** | Every module built by a seat + validated by a **blind holdout authored by a different seat**. For *validating existing* code, holdouts run **grey-box** (author gets real signatures, not the impl) — the fix for tonight's blind-API-guess false-FAILs. |
| **M5 / M5b (cross-family arbitration)** | REFINED with **same-family exclusion**: a Claude seat may critique but its vote on Claude-family work is **weight 0**. Proper seat-map (kimi produce → non-Claude critique → cross-vendor adjudicate), fail-closed (closes B12). |
| **Forced-RAG** | Research-first grounding on every mutating action, **risk-tiered** (kernel/authz/secrets paths need change-specific grounding + non-Claude review, not a session stamp). Risk-tier thresholds are **owner + non-Claude decided**, not orchestrator-decided. |
| **Black/gray-box validation** | Outcome oracle (external state) + process oracle (invariants). **Deterministic evidence dominates any model vote** (`tests fail + orchestrator says pass = FAIL`). |
| **Work-unit freeze (kernel §0.4)** | The orchestrator operates only inside an **owner-frozen work-unit** (write_set, risk_tier, done_when). Scope narrowing auto-applies; **expansion → owner approval**. |

## 4. How things are TESTED (this is the verification answer)

- **Deterministic checkers decide pass/fail — never an LLM.** Tests, type-checks, holdout mutant-kill,
  schema/authz checks. LLMs advise only.
- **The bias firewall is itself validated on public gold**, by evaluator family: **LLMBar, JudgeBench,
  the DBG self-preference benchmark, Prometheus-Eval**, and **position-swap** (every pair run twice,
  order reversed). Tracked metrics: gold-label accuracy, false-accept, false-reject, same-family
  preference delta, position-flip rate, disagreement-with-executable-evidence.
- **Gate/procedure changes A/B-tested before shipping** via the corpus's non-circular protocol
  (`PIPELINE-ACCEPTANCE-AND-ANTICIRCULARITY-2026-07-06.md` + Fable's bake-off review): pre-register →
  **shadow** (log would-block/would-pass, serve nothing) → **canary** → **interleaved** (paired,
  10–100× more sensitive). No LLM (or its family) both is the gate and scores whether the gate works.
- **The evaluator is tested with deliberately defective + alternate-valid cases** (superficially-passing
  incomplete result; an alternate valid trajectory; an unlogged side-effect; reversed candidate order).
  An evaluator never attacked is not authoritative.
- **Every Claude-authored artifact (incl. THIS proposal) gets non-Claude adjudication**; deltas are
  build-blockers, not advisory notes.

## 5. Bootstrap sequence (breaks the chicken-and-egg)

You can't build the whole kernel to get a gated orchestrator; you build the *minimal* gate first:

1. **Work-unit freeze (wire-up, not build)** — `work_unit_freeze.py` exists DEFAULT-OFF. Turn it on: the
   orchestrator is DENY-mutate/dispatch until an owner-frozen work-unit names scope. *This alone stops the
   worst of tonight.* Get its holdout/gaming suites green (owner-authorized effort).
2. **Minimal deterministic controller** — the §7 propose→validate→authorize state machine wrapping the
   freeze + `authz.py`. Small, testable, auditable (its being small is a hard requirement).
3. **Same-family bias firewall** — the `authorize_judgment` middleware + provenance-preserving blinding,
   validated on the public bias benchmarks above.

Each increment is **built by a non-Claude producer, cross-vendor adjudicated** — because each exists to
gate the Claude orchestrator.

## 6. Effect on the orchestrator (what changes operationally)

Unchanged: runs all night; plans; assigns; routes; monitors; researches; proposes; reports.
Removed: final judge of Claude-family work; overriding executable evidence; changing its own
constraints/budgets/scope; unbounded loops; honoring its own refusal as a veto.
Net expected outcome: **all-night autonomy becomes trustworthy — the guardrails no longer depend on the
orchestrator's honesty or bias.**

## 7. Open questions (owner only)

1. Authorize the effort to green `work_unit_freeze.py`'s suites so it can move off DEFAULT-OFF?
2. Who owns the same-family-firewall DENY thresholds + the risk-tier floors (owner / non-Claude panel)?
3. Scope-expansion approval friction — every write_set/risk/budget bump needs owner approval: acceptable
   for the pilot, or batched differently?
4. Interim tamper-proofing: the freeze is file-based owner-trust (no HMAC/single-use receipts yet) — OK
   for the pilot?

## 8. Non-Claude adjudication note

This proposal was written by Opus (Anthropic-family), the very component it constrains. Per Fable's rule
and the self-preference literature, it must be attacked by non-Claude cross-vendor reviewers
(GLM + a Codex/GPT + Kimi, none Anthropic) before adoption; their deltas are build-blockers. Treat every
claim here as a Claude proposal to be refuted, not a decision.
