# 02 — Human-Effort / Task-Assignment Log (Directive Log)

**Purpose.** The decision log records *choices*; this log records *drive*: every human
DIRECTIVE — assign a task, set focus, identify a gap, decide, override — and links the
AI-executed work back to the directive that spawned it. **What it is evidence of
(red-team #16):** the factual claim that the human directed every unit of work — which
rebuts "the human wasn't involved" and supports the tool-under-direction narrative. It
is NOT, by itself, authorship of expression: the USCO's Part-2 report holds that
prompts/instructions without control over the expressive execution are insufficient,
and project direction is not copyright authorship
([USCO AI guidance hub](https://www.copyright.gov/ai/)). The legal weight-bearing
analysis lives in 05; this log supplies the facts.

## 1. Row schema

Monthly shards `provenance/directives/HD-YYYY-MM.md`, append-only, one row per directive:

| Field | Content |
|---|---|
| `id` | `H-YYYYMMDD-NN` |
| `ts` | ISO timestamp (UTC) — from the transcript message or the moment of logging |
| `type` | `assign` · `focus` · `gap` · `decide` · `override` · `stop` |
| `directive` | The human instruction, summarized in ≤2 lines **in the owner's framing** (public-safe paraphrase; the verbatim text stays in the private transcript) |
| `spawned` | What AI work it produced: commit sha(s), closeout filename(s), doc path(s), subagent-review path(s) in `reviewed/` |
| `session` | Session-id → row in `provenance/transcripts/TS-INDEX-*.md` (which carries the hash of the full private transcript) |
| `linked-decision` | `D-…` id(s) when the directive produced a logged decision |

The `type` taxonomy is deliberately the owner's actual repertoire, visible throughout
the corpus: e.g. `override` (seed decision #8 — owner overrode the premature
"delivery-ready"), `gap` (owner caught the max_tokens=300 vs 12000-floor garbage run,
`feedback_research_first_orchestrator`), `stop` (public-repo ops boundary: no outward
push without approval, seed #16).

## 2. The linkage argument (directive → work)

Each row makes a two-way join a human can check in minutes:

- **Forward:** directive `H-…` → `spawned` commits/closeouts. Open the commit; it
  postdates the directive and matches its scope.
- **Backward:** any substantial commit or closeout → grep `provenance/directives/`
  for its sha → the human directive that caused it, timestamped and hash-anchored.

Coverage metric (red-team #21 — the denominator must be predefined, or "every
non-trivial unit" invites cherry-picking): **the capture population is the set of
closeout-bearing tasks in `audit/**`** — an independently existing, already-timestamped
census that this log does not control. `INDEX.md` reports: closeouts traced to a
directive / total closeouts, plus the orphan list. Gaps are listed, not hidden — an
honest "94% traced; 12 orphans listed" survives scrutiny better than a suspicious 100%.
Link-rot is bounded by the same denominator: a quarterly reconciliation script recounts
the join and publishes the error rate.

## 3. Capture going forward (cheap, at the moment it happens)

- End-of-task discipline already exists: the closeout (`cortex write-log`). Extend the
  habit, not the tooling: when writing a closeout for owner-directed work, append the
  `H-` row in the same breath and put its id in the closeout `task` text. One line each way.
- Session-level: at session end, the directives are exactly the owner's messages —
  the same material the transcript audit (04) hashes. Summarize the 1–5 that were
  directives; skip chit-chat.
- No new tooling required for v1. (Optional later: a `cortex directive` CLI shim that
  appends the row and returns the id — only if the manual habit proves lossy.)

## 4. Back-fill

Pass 5 of the mining plan (01 §4) is the primary source: parse `role:user` messages
from the 528 transcripts, cluster per session, AI-summarize candidates, owner skims and
confirms. Mined rows carry the back-fill tiers of 01 §4 (`independently-corroborated` / `backfilled-owner-attested` / `legacy-unanchored` — never `contemporaneous`).
The join targets for `spawned` come from passes 1–2 (commits + closeouts within the
session's time window — the harness dir name and timestamps make the correlation
mechanical). Expect the back-fill to be *sampled-verified*: the owner confirms a random
sample per month rather than all rows, and `INDEX.md` records the sampling rate —
again, honest beats exhaustive.

## 5. Public-safety of this log

Directive summaries are **paraphrases written for publication**, never verbatim
transcript excerpts — verbatim owner messages may contain personal/Hades/other-project
material (the reason the raw transcripts stay private, 04 §1). The evidentiary weight
does not come from the paraphrase; it comes from the `session` pointer to a
hash-committed private transcript that can be produced under seal if ever needed.
