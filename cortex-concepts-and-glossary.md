# Cortex — Concepts & Glossary (study guide)

*A plain-language map of everything this project does and every term we've been throwing around.
Read top-to-bottom for the mental model; use the glossary at the end as a quick reference. Safe to
paste into ChatGPT and ask it to quiz you or go deeper on any section.*

---

## 0. The one-paragraph mental model

Cortex exists to answer one question honestly: **"Did an AI actually do the task correctly, or does it
just look like it did?"** The core belief is **evidence over trust** — never take a model's word for
whether its work is good; prove it with something that *can't* be fooled. The thing that proves it is
called an **oracle**. Everything else in the project is either (a) building better oracles, (b) making
sure the oracles themselves aren't secretly cheatable, or (c) using the oracles to make AI agents
better over time (the **flywheel**).

---

## 0.5 What "durable artifact" means (the most important term)

A **durable artifact** is anything we produce that **keeps its value without the expensive/expiring
model** — it's captured, reusable, and outlives whatever helped create it. It's the opposite of
**ephemeral**.

This is the point of the whole project: Fable (the top model) was *expiring*, so we spent that
ephemeral resource to mint *durable* assets that work forever after it's gone. The mantra:
**"the durable asset is the checker/rubric/exemplar — never the model."**

"Durable artifact" is a **category, not one thing.** It's the whole combined family — **not just
tests**:

- **oracles / checkers**, **gold datasets** (hard + trainable), **eval lanes**,
- **calibration anchors + κ numbers**, **checker-cores + resolvers**,
- **KEDB failure patterns**, **captured chain-of-thought (CoT) / reasoning traces**,
- the **tier list**, **rubrics + exemplars**.

Three things make something durable: (1) **model-free at runtime** (or it's frozen data), (2)
**captured & committed** (not stranded in a chat/temp transcript — that's ephemeral and the exact
failure this project fights), (3) **reusable / reproducible**.

- **Ephemeral:** a model's live judgment, a quota, a one-off answer, findings left only in chat.
- **Durable:** the checker it produced, the gold it graded, the calibration number, the frozen trace.

---

## 1. The central idea: oracles (a.k.a. checkers)

- **Oracle / checker** — a piece of code that looks at an AI's answer and returns **pass/fail** as
  *ground truth*. Think "the answer key" or "the referee." The key property: it decides by a
  **mechanism**, not an opinion.
- **Test oracle** (the computer-science term) — this is where the word "oracle" comes from. It has
  **nothing to do with Oracle the company** (Oracle GoldenGate etc.). It just means "the authority
  that says whether a test passed."
- **Deterministic** — same input → same output, every time, with no randomness or judgment. A
  deterministic checker is trustworthy because it can't be sweet-talked.
- **Judge-free / "no judge in the verdict path"** — the rule that **no language model is allowed to
  decide pass/fail.** A model can *generate* a candidate answer, but only code (running tests,
  checking state, matching structure) may *grade* it. The moment a model grades, you're back to
  "trust me," which is the thing we refuse.

**Why it matters:** a model asked "is this code correct?" can be wrong or flattering. Code that
*runs the tests* can't be. That's the whole game.

---

## 2. What the oracle grades: "gold"

- **Gold / gold data / hard gold** — a collection of examples where we *know* the right answer,
  produced by an oracle. Like a graded exam pile. "Hard gold" = graded by a deterministic oracle
  (the trustworthy kind).
- **Trainable gold** — gold that's clean enough to *train future models on*. Extra rules apply here
  (see anti-distillation).
- **Provisional gold** — gold we've flagged as "don't fully trust yet" (e.g. the checker that graded
  it was later found gameable). Honesty knob: we mark it rather than pretend.
- **Soft anchor** — a *weaker* kind of reference produced by a top model's **judgment** (Fable), not
  by a deterministic oracle. It's a hypothesis, never treated as final truth (see calibration).
- **Provenance** — the recorded history of a piece of gold: *which model produced it, when, verified
  how.* "Provenance-verified" means we confirmed it (e.g. read the actual model id the server
  returned), not just took the request's word for it.
- **Holdout** — a hidden slice of the data that models are *never shown*, kept secret so a model
  can't quietly memorize the answer key and cheat the test. Ours is chosen by a cryptographic hash so
  it's unpredictable.

---

## 3. Judges, calibration, and κ (kappa)

Sometimes a task is fuzzy ("is this a *good* rubric?") and no simple checker exists. Then you might
use a strong model as a **judge** — but judges are risky (they flatter, they drift). So:

- **Calibration** — measuring *how much you can trust a judge* by comparing it to a reference.
- **Cohen's kappa (κ)** — a number from ~0 to 1 that says **how much two graders agree**, beyond
  random chance. κ = 1.0 means perfect agreement; κ = 0 means no better than coin-flips. We use it to
  ask: *"Does our deterministic checker reproduce the top model's judgment?"* When κ = 1.00 (as it
  did for one domain), the code fully replaces the model — the durable win.
- **Anti-distillation / "Anthropic outputs stay out of trainable gold"** — a rule: we don't train on
  our *own family's* model outputs (it's circular and self-flattering). Only **non-Anthropic** models
  (GLM, Qwen, DeepSeek, Gemini, etc.) produce *trainable* candidates. Anthropic models (Claude/Fable)
  are used only as *anchors* and *verifiers*, never as training material.

---

## 4. The oracle "lanes" (how oracles are packaged)

- **Lane** — one self-contained oracle for one kind of task, e.g. `objective_cwe_patch_execution`
  grades security-bug fixes. We have ~69 of them.
- **Objective lane** — a lane whose verdict comes from execution/structure, no judge.
- **Stage-2 contract** — the checklist every lane must satisfy to be trustworthy: a committed
  checker, a manifest with file hashes, **frozen tests** (tests that lock in "good passes, bad
  fails"), **reference pass/fail controls**, honest **quarantine**, and no judge.
- **Mutation gate / mutation testing** — you deliberately break the correct answer in small ways
  ("mutants") and check the oracle *catches* them. If a mutant sneaks through, your oracle has a blind
  spot. It proves the checker has teeth.
- **Quarantine** — when the oracle *can't cleanly decide*, it says "I don't know" (abstains) and
  records why, instead of guessing. Guessing = fake gold.

---

## 5. Kinds of oracles we built

- **BFCL** — a public benchmark for **tool/function calling** (does the AI call the right function
  with the right arguments?). Graded by BFCL's own checker. Our strongest lane.
- **External agent-oracles** (adopted public benchmarks, graded by *final state*, not vibes):
  - **AppWorld** — grades the final **database state** across simulated apps (did the agent actually
    change the right data?).
  - **Terminal-Bench** — grades the final **container/filesystem state** after a shell task.
  - **Spider 2.0-DBT** — grades **SQL** by running it and comparing result tables.
  - **MCPMark** — grades **MCP tool agents** against real service state.
- **Cyber lanes** (defensive security, graded by execution):
  - **CWE-patch** — did a fix actually stop the vulnerability? (CWE = a catalog of software weakness
    types, e.g. CWE-89 = SQL injection.)
  - **crypto-misuse** — weak hashing, reused encryption nonces, disabled TLS checks, etc.
  - **SSRF / path-traversal** — graded by a **canary**: a fake internal server/file; if the code
    touches it when fed an attack, it fails. (SSRF = tricking a server into making requests it
    shouldn't.)
  - **authz-bypass** — a **truth-table** of who-can-do-what; the candidate's access-control code is
    run against every cell.
  - **prompt-injection** — does malicious text hidden in a document hijack the agent? (Graded by a
    **canary token** that must never leak.) *Currently marked provisional* — the check was too
    pattern-based.
- **Checker-cores** — 5 reusable grading building-blocks that turn a top model's rubric *judgment*
  into deterministic code (so no model is needed at run time).
- **Resolvers** — the harder building-blocks that need an external fact (git history, a citation
  index) to decide; they compute it deterministically or honestly abstain.
- **Browser-use oracle + KEDB** — grades whether an agent *drove a browser correctly* (right clicks,
  no loops) against gold trajectories (Mind2Web), plus a **KEDB** (Known Error Database) of common
  browser-agent mistakes agents can look up to avoid repeating them.

---

## 6. The models & how we use them

- **Fable** — the strongest model (our "gold anchor" / top judge). Its subscription was expiring, so
  we spent it on the *unrepeatable* work: calibrating rubrics and designing checkers.
- **Codex sol / terra / luna** — GPT-5.6 variants used as an **independent (non-Anthropic)
  verifier**. **sol** = strongest, for core-design red-teams; **terra** = one-off reviews; **luna** =
  weak, avoid. Used because a competitor model can't rubber-stamp our work.
- **The fleet** — the many cheap cross-vendor models (via **9Router**, **OpenCode-Zen big-pickle**,
  **OpenRouter free** models) that *generate* candidate answers in bulk. They generate; the oracle
  grades.
- **Tier list** (`model_tiers.py`) — our measured ranking of which models are strong / mid / weak, so
  we route the right model to the right job.
- **Red-team / adversarial verification** — deliberately *attacking* our own oracle to find how a
  cheater could pass it. If a competitor model can game the checker, so can a trained model. We fix
  the hole before trusting the gold.

---

## 7. The flywheel (how oracles make agents smarter)

1. Cheap models **generate** lots of candidate answers.
2. The **oracle grades** them (pass/fail, no judge).
3. The **passes** become trainable gold; the **failures** become KEDB patterns ("don't do this").
4. Future/retried agents get **better** by learning from that gold + avoiding known failures.
5. Repeat. Each turn, the corpus of proven-good behavior grows — and because oracles (not opinions)
   decide, it can't rot into self-congratulation.

**live-gen** = the engine that runs step 1–3 at volume across the fleet.

---

## 8. The wrapper side (a related but separate thing)

- **cortex-govern / the wrapper** — a tool that forces *any* model through a fixed sequence of steps
  (search → research → plan → spec → implement → review → closeout) so an agent **can't skip the
  discipline**. It only enforces *order + grounding*; the oracles above are what check *quality*.
- **Plane-1 vs Plane-2** — Plane-1 = an in-app agent that merely *discloses* its steps (best-effort).
  Plane-2 = an external model *forced* through the state machine (real enforcement).
- **MCP (Model Context Protocol)** — a standard way for an AI agent to call external tools (like
  Cortex's search) in-band. "The brain over MCP" = agents querying Cortex's knowledge corpus as tools.
- **Fan-out / delegator** — an orchestrator that splits a task and hands pieces to other
  models/profiles in parallel, using the tier list to pick who.

---

## 9. How it all connects (the big picture)

```
                evidence over trust
                        │
        ┌───────────────┴────────────────┐
        ▼                                 ▼
   ORACLES (checkers)              the WRAPPER (cortex-govern)
   decide pass/fail by mechanism  forces the discipline / order
        │                                 
        ├── graded by execution/state/structure — NO model judges
        │
        ├── produce GOLD ──► trainable (non-Anthropic only) ──► train better agents
        │                └► failures ──► KEDB ──► agents avoid repeats
        │
        ├── verified by RED-TEAMS (Codex, non-Anthropic) so they can't be gamed
        │
        └── FABLE calibrates the fuzzy ones ──► checker-cores/resolvers make them judge-free (κ measures success)
                        │
                        ▼
                 the FLYWHEEL: generate → grade → learn → repeat
```

**In one breath:** oracles are trustworthy referees; gold is what they produce; calibration + κ tell
us when code can replace a model's judgment; red-teams keep the referees honest; the fleet generates
at volume; the flywheel turns all of it into agents that measurably improve — with no step ever
resting on "trust me."

---

## Glossary quick-reference

| Term | One-liner |
|---|---|
| **Oracle / checker** | Code that decides pass/fail as ground truth (the referee). |
| **Deterministic** | Same input → same output; no judgment. |
| **Judge-free** | No language model allowed to decide pass/fail — only code. |
| **Gold / hard gold** | Examples with known-correct answers, graded by an oracle. |
| **Trainable gold** | Gold clean enough to train future models on. |
| **Provisional** | Flagged "don't fully trust yet." |
| **Soft anchor** | A top model's *judgment* as a hypothesis, not final truth. |
| **Provenance** | Recorded history of who/what produced a datum, verified. |
| **Holdout** | Hidden data models never see, to prevent memorizing the answer key. |
| **Judge** | A model used to grade fuzzy tasks (risky; must be calibrated). |
| **Calibration** | Measuring how much a judge can be trusted. |
| **Cohen's κ (kappa)** | 0–1 agreement score between two graders (1 = perfect). |
| **Anti-distillation** | Don't train on your own model family's outputs (circular). |
| **Lane** | One packaged oracle for one task type (~69 exist). |
| **Stage-2 contract** | The checklist a lane must meet to be trustworthy. |
| **Mutation gate** | Deliberately break the answer; the oracle must catch it. |
| **Quarantine / abstain** | Oracle says "I don't know" instead of guessing. |
| **Red-team** | Attack your own oracle to find how a cheater passes. |
| **BFCL** | Public tool-calling benchmark (our strongest lane). |
| **CWE** | Catalog of software-weakness types (e.g. CWE-89 = SQLi). |
| **SSRF** | Tricking a server into making requests it shouldn't. |
| **Canary** | A fake secret/server; if code touches it under attack, it fails. |
| **Truth-table** | Every who-can-do-what combination, computed as ground truth. |
| **KEDB** | Known Error Database — catalog of failure patterns to avoid. |
| **Checker-core / resolver** | Reusable building-blocks that make judgment deterministic. |
| **Fable** | Strongest model; our gold anchor / calibrator. |
| **Codex sol/terra** | Non-Anthropic (GPT) models used as independent verifiers. |
| **The fleet** | Many cheap cross-vendor models that generate candidates in bulk. |
| **Tier list** | Measured ranking of model strength for routing. |
| **live-gen** | The engine that generates candidates at volume for grading. |
| **Flywheel** | generate → grade → learn → repeat; agents improve over time. |
| **cortex-govern / wrapper** | Forces a model through fixed disciplined steps. |
| **Plane-1 / Plane-2** | Disclosure-only agent vs. forced-through-the-machine agent. |
| **MCP** | Standard for agents to call external tools (like Cortex search). |
| **Fan-out / delegator** | Splits a task across models/profiles in parallel. |
