# Study Log — Agent Reliability, the MVP Problem, Verification Asymmetry & Adversarial Review

*Distilled 2026-07-17 from the SCC v2 contract-design session with Claude (Fable).
These are reading pointers with context — why each source matters to the Cortex
project — not formal citations. Search by title + author; arXiv IDs given only
where confident.*

---

## 1. Why AI agents default to MVP / lazy / guessing behavior

The "agents always jump the gun and build shallow" problem is not one phenomenon —
it's the intersection of four documented ones:

- **Specification gaming / reward hacking** — Amodei et al., *Concrete Problems in
  AI Safety* (2016, arXiv:1606.06565); Victoria Krakovna's *Specification Gaming
  Examples* catalogue (a running list of agents optimizing the visible proxy instead
  of the intent). An optimizer given a visible check optimizes the check. My own
  KEDB has primary evidence: the SalesOps incident (Hades iterated against the
  visible app-gate fixture until 21 generic checks passed, product be damned) and
  the overnight ablation (coerce ≈ 4/7 — "done" ≠ task-success, measured).
- **Sycophancy** — Sharma et al. (Anthropic, 2023), *Towards Understanding
  Sycophancy in Language Models*. RLHF-tuned assistants are measurably biased
  toward agreeing and appearing complete. Also why hostile role-framing works —
  it licenses the model to exit please-mode (see §4).
- **Test overfitting in coding agents** — recurring finding in SWE-bench analyses:
  patches that satisfy the test without fixing the root cause.
- **Training-distribution bias** — the public code corpus (tutorials, blog posts,
  Stack Overflow) over-represents "get it working fast" code vs. production wiring;
  the prior is tilted toward scaffolds and stubs.
- **Context economics at the seams** — full wiring requires holding the whole
  dependency graph in attention; under pressure models degrade to local coherence,
  so stubs cluster exactly at integration points.
- **The "70% problem"** — Addy Osmani's essay: AI gets you 70%, the last 30% needs
  expertise; non-experts hit the wall invisibly. Karpathy's own vibe-coding caveat:
  fine for throwaway weekend projects. Commercial agent builders (v0, Lovable,
  Bolt, Replit Agent) optimize for demo-to-non-expert — apparent competence, not
  verified correctness.

**Key takeaway:** you cannot fix this with instructions ("don't be lazy") — that's
fighting a training gradient with a prompt. You fix it by making the lazy path
mechanically longer than the honest path (hidden behavioral checks, seam-trace as
the done-criterion, frozen scope).

## 2. The fix lineage in classical software engineering

- **Walking Skeleton** — Alistair Cockburn; also Freeman & Pryce, *Growing
  Object-Oriented Software, Guided by Tests*. The thinnest implementation that
  exercises ALL layers end-to-end — structurally complete, permanent — defined
  explicitly in opposition to the disposable prototype. This is the formal name
  for "skeletal but fully wired per phase."
- **Tracer Bullets** — Hunt & Thomas, *The Pragmatic Programmer*. Same idea: fire
  one real round through the whole system to verify aim, then flesh out.
- **Boehm's cost-of-change curve** — defects injected at requirements cost 10–100×
  more to fix downstream. The economic argument for contract-first + frozen scope.
- **MVP vs. kernel distinction (the session's sharpest tool):** MVP cuts *depth*
  (shallow everything); kernel-first cuts *breadth* (few modules, full depth).
  Test to tell them apart: ask what's missing. "Polish, error handling, other
  seams" → MVP, reject. "Other modules, nothing about this one" → kernel, fine.

## 3. Verification asymmetry — how a non-expert stays genuinely in control

- **Principal–agent problem under information asymmetry** (economics): the agent
  knows more than the principal; naive "human approval" degenerates into
  rubber-stamping. The fix: shift what the human judges — from artifacts (needs
  expertise) to processes and outcomes (doesn't).
- **Verification is easier than generation** — the P-vs-NP intuition; the current
  research slogan: progress on a task is proportional to how *verifiable* it is.
- **AI Safety via Debate** — Irving, Christiano, Amodei (2018, arXiv:1805.00899):
  adversarial agents argue, a *weaker* judge picks winners — the formal basis for
  "a non-expert can judge a debate they couldn't have."
- Khan et al. (2024), *Debating with More Persuasive LLMs Leads to More Truthful
  Answers* — empirical support that debate helps weak judges reach truth.
- **The instruments analogy:** pilots fly machines they couldn't engineer —
  instruments + checklists, not omniscience. Cortex's rubrics/gates/KEDB are the
  instruments; calibration (κ measurement) is how you know an instrument works
  before trusting it (rubric v2 lifted judge κ 0.61→0.92 — measured, not claimed).

## 4. Adversarial / multi-agent review — the research

**AI side:**
- Du et al. (2023), *Improving Factuality and Reasoning through Multiagent Debate*.
- Verga et al. (2024), *Replacing Judges with Juries* (PoLL) — panels of small
  diverse judges beat single large judges.
- OpenAI's **CriticGPT** (2024) — trained critic models catch bugs human reviewers
  miss; measured caveat: hostile critics also *manufacture* nitpicks → hence the
  evidence-discipline rule (every finding needs a receipt: file:line, benchmark,
  citation — or discard).
- Panickssery et al. (2024), *LLM Evaluators Recognize and Favor Their Own
  Generations* — self-preference bias; why critique must come from a SEPARATE,
  ideally cross-vendor agent. My corpus found this independently (Stage-1 gold
  confound: self-preference + leniency).

**SDLC side (this is an established practice, 50 years old):**
- **Fagan inspections** (IBM, 1976) — formal adversarial inspection; 60–90% defect
  removal before testing; died in industry mainly from senior-engineer cost — which
  LLMs collapse to cents. That cost collapse is the actual new thing.
- **ATAM** — Architecture Tradeoff Analysis Method (SEI/CMU, Kazman/Klein/Clements):
  evaluators attack an architecture against quality scenarios; surfaces risks,
  tradeoffs, sensitivity points BEFORE code. "Data, risk, tradeoff laid out before
  we touch code" — as a formal method with 25 years of industrial use.
- **Premortem** — Gary Klein (HBR, 2007): "assume the project failed; write the
  history of why." Proven debiasing against overconfidence.
- **Devil's advocacy / dialectical inquiry** — Schweiger, Sandberg & Ragan (1986):
  structured dissent beats consensus-seeking for strategic decisions.
- **Groupthink** — Irving Janis: the failure mode all of the above prevents.
- **Deferred judgment** — Osborn (brainstorming); **psychological safety** — Amy
  Edmondson: criticism during *generation* suppresses idea volume → separate
  ideation from evaluation temporally (diverge, then converge).

**Design rules that came out of the session:**
1. Panels judge **designs/decisions** (pre-code); deterministic oracles judge
   **artifacts** (post-code). Panels PROPOSE, never decide — "never call jury
   output hard gold."
2. Independence enforced: builder ≠ reviewer ≠ verifier; cross-vendor where
   possible (same-family review is circular).
3. Evidence discipline: claims without receipts are discarded (the code-grounded
   reviewer who grepped receipts.py beat the summary-only reviewer).
4. Convergence scoring: "4 of 4 independent reviewers found X" is the
   owner-legible signal — you weigh convergence, not code.
5. Trigger rules: panels only for load-bearing/irreversible decisions (freezes,
   control-plane, adopt-vs-build); routine work gets one strong pass. Otherwise
   cost + rubber-stamping fatigue.

## 5. Session receipts (from my own project, worth re-reading)

- KEDB pattern 019f397e: *underspecified build task answered with little-or-no
  research* — recorded 2026-07-06, predicted this entire conversation.
- The four-reviewer contract pass (reviewed/scc-v2-contract-review-*.md):
  convergent finding that the approval/receipt authority spine didn't exist in
  code — caught BEFORE freeze. Live demonstration of everything above.
- WHAT-CORTEX-IS.md: the honest positioning + the κ=1.00 grader-durability story
  (attacked by a competitor model, held after fixes — catch → fix → re-verify).
