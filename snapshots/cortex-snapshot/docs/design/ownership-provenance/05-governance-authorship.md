# 05 — Governance / Legal Framing: How the Record Supports the Human-Authorship Claim

> **NOT LEGAL ADVICE.** This is an engineering design informed by public guidance and
> case law as of 2026-07. Copyright in AI-assisted works is unsettled and
> fact-specific; the owner should confirm this framing with qualified counsel before
> relying on it in any registration, transaction, or dispute.

## 1. The legal landscape (sources)

- **Human authorship is required.** *Thaler v. Perlmutter* (D.C. Cir., Mar 18 2025)
  held the Copyright Act requires a human author; a machine cannot be the author. The
  Supreme Court denied certiorari (Mar 2 2026), leaving that holding intact.
  ([opinion PDF](https://media.cadc.uscourts.gov/opinions/docs/2025/03/23-5233.pdf);
  [Skadden](https://www.skadden.com/insights/publications/2025/03/appellate-court-affirms-human-authorship);
  [Baker Donelson on cert denial](https://www.bakerdonelson.com/supreme-court-denies-certiorari-in-thaler-v-perlmutter-ai-cannot-be-an-author-under-the-copyright-act)).
  Note Thaler is the *easy* case — he listed the machine as sole author — and it is
  usable here **only as the negative floor** (red-team #19): the D.C. Circuit expressly
  did NOT decide whether Thaler could be author *by making and using* the machine
  (that argument was waived below), and cert denial is not merits approval. Our case is
  the opposite posture — a human claiming authorship of AI-assisted work — which Thaler
  neither blesses nor bars.
- **AI as a tool vs. AI as author.** The USCO's March 2023 registration guidance
  (88 Fed. Reg. 16190) and its **Copyright and AI, Part 2: Copyrightability** report
  (Jan 29 2025) draw the line at human creative control: outputs are protectable where
  a human "determined sufficient expressive elements" — via perceptible human-authored
  input, or **creative selection, coordination, arrangement, or modification** of AI
  output — while **prompts alone are insufficient**; and using AI to *assist* creation
  of a larger human-directed work does not bar protection.
  ([USCO AI hub](https://www.copyright.gov/ai/);
  [Part 2 report](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf);
  [LoC summary](https://blogs.loc.gov/copyright/2025/02/inside-the-copyright-offices-report-copyright-and-artificial-intelligence-part-2-copyrightability/)).
  Two honesty notes (red-team #16, #19): this is **agency guidance, not a judicial
  holding**; and Part 2 explicitly says *detailed instructions without control over how
  the system executes them* are insufficient, and iterative re-prompting can be mere
  "re-rolling" — project **direction is not itself authorship of expression**. The
  protectable residue is narrow: human-authored text, minimally creative modification,
  and thin compilation-level selection/arrangement, bounded by
  [17 U.S.C. §102(b) (no protection for systems/methods/processes) and §103(b)
  (compilation copyright covers only the human contribution)](https://www.copyright.gov/title17/92chap1.html).
- **Work made for hire cannot launder machine output.** 17 U.S.C. §101/§201(b) vests
  employer ownership of works by *human* employees (or commissioned works only within
  the enumerated statutory categories, with a signed writing); an AI is neither, so
  WMFH gives no path to owning pure machine output. It stays relevant only for any
  *human* contributors, which the chain-of-title ledger (§4.6) covers.
- **The contract leg (distinct from copyright).** Anthropic's commercial terms assign
  to the customer Anthropic's "right, title and interest **(if any)**" in outputs
  ([Anthropic commercial terms archive](https://www.anthropic.com/legal/archive/7e37c9e3-af36-4555-93f0-2c677d027003);
  [analysis](https://terms.law/2024/08/24/who-owns-claudes-outputs-and-how-can-they-be-used/)).
  Honest reading: an assignment conveys only what exists — if no copyright subsists in
  an output, the assignment creates none; but it settles *as between the parties* that
  Anthropic claims nothing, and contract + trade-secret + first-possession are real
  ownership-adjacent protections even where copyright is thin. Current terms at signup
  time should be preserved in the chain-of-title ledger (counsel item).
- **EU context (narrow — red-team #20).** The EU AI Act's Article 50 transparency
  obligations principally bind *providers* of generative systems (machine-readable
  marking of outputs); deployer disclosure duties are narrower (deepfakes, certain
  public-interest text, with editorial-responsibility exceptions)
  ([Article 50 text](https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng);
  the final Code of Practice on marking was published June 2026 per the red-team's
  citation — [Commission page](https://digital-strategy.ec.europa.eu/en/policies/code-practice-ai-generated-content)).
  No claim is made that this repo has Article 50 obligations or that this design
  satisfies any; the relevance is directional only — the regulatory world rewards
  honest AI-involvement disclosure, which this record practices, and a record that
  *hides* AI involvement would be both risky and falsifiable.

## 2. The affirmative argument the record will make (and its honest scope)

The narrative is "**I directed, selected, arranged, and decided; the AI was my tool**" —
but stated with the red-team's discipline (#16/#17): **process control is factual
evidence, not authorship of expression**. The record supports three distinct claims,
of different strengths:

1. **Factual rebuttal (strongest):** "the human wasn't really involved / this was
   fabricated" is answered by the directive-to-work joins, override rows, and the
   anchoring stack. This is the claim the record proves best.
2. **Copyright (narrow, per USCO):** protectable elements are the human-authored
   text, minimally creative modifications, and thin compilation-level
   selection/arrangement — NOT the agent-written code as such, and not architectural
   or ship/no-ship choices (§102(b) systems/methods). To make this concrete the build
   includes an **artifact-level authorship matrix** (one small table in
   `provenance/CHAIN-OF-TITLE.md`): per major artifact class, what the human authored
   directly, what the human modified (before/after preserved in git), what is
   AI-generated under direction, and what is excluded.
3. **Contract/title (independent of copyright):** provider output-assignment terms,
   contributor status, dependency licenses — the chain-of-title ledger (§4.6).

| What each piece evidences | Evidence piece | Concretely |
|---|---|---|
| Direction / control over the process (factual) | Directive log (02) + transcript audit (04) | Closeout-bearing tasks trace to timestamped human directives; overrides show control was real, not rubber-stamping (seed decisions #3, #8) |
| **Selection** among alternatives (thin-compilation leg + factual) | Decision log (01), `path-vs-alt` column | Ship/no-ship gates (Phase-2: shipped RRF, rejected reranker + chunking-v2 on measurement); arbitration verdicts *accepted by the owner* |
| **Arrangement / coordination** (thin-compilation leg + factual) | Repo structure + decision log + PHASE-GATES | Corpus architecture, phase plan, sharding, ops boundary — owner-set structure the AI executed within |
| **Modification / iteration** (factual; copyright only where the modification is itself creative expression) | Engineering log (03) + closeouts + git diffs | Measured, owner-commissioned experiment loops changing the work (rubric v1→v2, quarantines, held features) |
| Integrity + existence-by-time of all the above | Anchoring stack (04 §3) | Signed envelopes + Merkle head + Bitcoin/TSA timestamps: byte-integrity proven, existence bounded — event time and identity corroborated only (04 §0) |

Two honest strengtheners built into the design: (a) the record **also documents what
the AI did** — decision rows marked `ORCHESTRATOR`/`ARBITRATION` and the mining
attribution rule (01 §4) mean OWNER rows are credible precisely because not everything
is claimed as OWNER; (b) once bootstrapped, the record grows append-only ahead of any
dispute — but this is true only from the first anchor forward (red-team #22): today,
pre-build, it protects nothing, which is why Step 0 is to anchor immediately.

## 3. Red-team: what a challenger attacks, and what answers it

| Attack | Answer in the record | Residual weakness (honest) |
|---|---|---|
| "Fabricated after the fact" | Chained Merkle anchors + OpenTimestamps/RFC-3161: everything provably existed by anchor time; GitHub push history corroborates | Back-logged (mined) rows are only provably as old as the **first anchor**, not their claimed event dates; mitigated by pointers to independently-dated substrates (GitHub server-side history, Anthropic-side session records), and tiered back-fill labels (01 §4) that admit this |
| "Git dates are forgeable" | Conceded in-design (04 §3.1); time never rests on git dates — only on external anchors + third-party witnesses | Pre-first-anchor commit dates remain self-asserted; corroboration, not proof |
| "The hashes could commit to anything — show the transcripts" | Production through counsel under court-controlled procedures (04 §5; sealing is the court's call, not ours to promise); commitment+nonce match proves the produced file is the committed one | Requires the private archive AND nonces to survive (04 §1); a lost private file leaves an unverifiable commitment. Hash-match ≠ admissibility — FRE 902(14) certification + custody SOP needed (04 §7) |
| "A human summary in the public index could lie" | Paraphrases carry no evidentiary weight by design; weight is in the hash-committed verbatim record | None significant — the design never asks anyone to trust a paraphrase |
| "Prompts alone aren't authorship — this is just prompt logs" | The record's center of gravity is NOT prompts: selection, arrangement, override, measured iteration — plus the artifact-level authorship matrix identifying actual human expression, and the contract/title leg that doesn't depend on copyright at all | **The genuinely open legal question.** How much control suffices is undecided; per-line code authorship remains mostly AI, and direction/management is not expression (red-team #16). Counsel question #1 |
| "The AI made the real decisions; the human rubber-stamped" | `decided-by` honesty: ORCHESTRATOR/ARBITRATION rows exist and are labeled; override rows (#8, #10, #11) show non-acceptance happened; directive-coverage metric is measured, gaps listed | Attribution of mined historical rows depends on owner verification actually being done (the `legacy-unanchored` count must trend to zero) |
| "Anyone could have typed at the keyboard" | Externally-timestamped signed envelopes bind anchors to a pinned key (04 §3.2); accounts (GitHub, Anthropic) bind sessions to his identities | A key signature proves the key, not the person (red-team #8); hardware custody + independent fingerprint pinning narrow it; standard e-evidence identity caveat remains |
| "Selective record — you logged wins, hid the rest" | Prefix-verified append-only manifest makes deletion detectable *after first anchor*; census reporting (04 §6) makes silent omission an inconsistency; honest-gap reporting (02 §2) and negative results in EL (03) | **Completeness is not provable** (red-team #3): pre-anchor omission cannot be excluded cryptographically; the census + provider-export reconciliation is corroboration, and breadth (hundreds of hashed sessions, orphans listed) is credibility, not proof |

## 4. Governance rules (the policy layer that keeps the record credible)

1. **Append-only, everywhere.** Corrections are new rows citing old ids. An edited
   provenance file after its anchor is a broken anchor — treat as an incident.
2. **Attribution honesty** is a standing obligation (inherits seed decision-log rule):
   never promote ORCHESTRATOR→OWNER without the owner's confirming pointer.
3. **Disclose AI involvement** in the repo's public description of itself (also the
   EU-transparency direction). The claim is *directed authorship*, not human-typed code.
4. **Anchor before publish**: milestone anchors run before any external delivery
   (fits the F4/owner-outward-act rule, seed #16).
5. **Registration decision is the owner's + counsel's**: if a U.S. copyright
   registration is ever filed, USCO rules require disclosing and disclaiming
   AI-generated material ([88 FR 16190 guidance via USCO AI hub](https://www.copyright.gov/ai/));
   this record is exactly the documentation that makes such a filing honest and
   defensible — but *whether/what* to file is out of scope here.
6. **Chain of title is its own ledger (red-team #18).** Authorship evidence ≠
   ownership evidence. `provenance/CHAIN-OF-TITLE.md` (one file) records: every human
   contributor and their basis (owner / assignment / WMFH within statutory limits),
   the AI providers used and their output-assignment terms as of use (preserved copy),
   dependency licenses of anything vendored, the repo's own outbound license, and the
   legal owner (individual vs. any future entity). Plus the artifact-level authorship
   matrix (§2.2).

## 5. Honest limits (repeat of the ones that matter most)

- **Not legal advice; unsettled law.** The sufficiency threshold for human control is
  the open question of this decade; no record guarantees a legal outcome. USCO reports
  are agency guidance; Thaler decides only that a machine cannot be the author.
- **The system's defensible claim is narrow** (adopted verbatim from the sol@xhigh
  red-team): it *"can preserve owner-attested records, prove their byte-level
  integrity, and establish an external upper bound on when each anchored version
  existed. Separate evidence is required for event time, human identity, completeness,
  expressive authorship, and ownership."* Everything beyond that is corroboration.
- **Retroactive time is corroborated, not proven.** Cryptographic time starts at the
  first anchor; a record fabricated just before its first anchor is cryptographically
  indistinguishable. The defense is starting NOW and anchoring frequently, forever.
- **Completeness is not provable** — census + reconciliation make omission visible
  going forward, not impossible retroactively.
- **The private archive (and its nonces) is a single point of failure** unless the
  two-offsite-copies + fixity discipline is kept.
- **The record proves process, not per-token authorship** — by design; anyone wanting
  per-line human authorship evidence will not find it here, and the design does not
  pretend otherwise. The copyright residue it supports is thin; the factual-rebuttal
  and contract/title legs are where it is strong.
