# 03 — Engineering Log Family (Learned / Tested / Measured)

**Purpose.** Distinct from decisions: this family records what the project **learned** —
tests run, real numbers, A/B/C results, benchmark outcomes, and lessons. Same
lightweight/sharded/indexed/tamper-evident infrastructure as the decision logs,
different content. It serves two masters at once:

1. **Engineering history** — the durable "what did we measure and what did it teach us"
   record the self-learning loop needs (today these numbers are scattered across
   `evals/reports/`, `calibration/*.md`, closeouts, and memory files).
2. **Authorship evidence, indirectly** — a contemporaneous record of experiments the
   owner commissioned and acted on is classic development-process evidence: it shows a
   directed, iterative human process rather than one-shot generation (the process
   record USCO Part 2 contemplates when it credits iterative human control and
   modification — see 05 §2).

## 1. Row schema

Domain shards `provenance/engineering/EL-<domain>.md` (same domain set + split rule as
01 §3), append-only:

| Field | Content | Example |
|---|---|---|
| `id` | `E-YYYYMMDD-NN` | `E-20260712-04` |
| `kind` | `experiment` (we ran it) / `observation` (seen in our data, not a controlled run) / `external-claim` (literature we relied on) — red-team #15: these have different evidentiary strength and must not be conflated | `experiment` |
| `date` | ISO date (UTC) | `2026-07-12` |
| `what` | The experiment/test/measurement, one clause | ontology A/B on dense vs. scattered corpus |
| `result` | The **numbers** — verbatim, with units/metric names | `0.077 → 0.779 recall on scattered; net wash on dense` |
| `lesson` | What it taught, one clause (may be "none — negative result") | park ontology for dense corpora |
| `method` | Reproducibility pointer. For `kind=experiment` REQUIRED: exact command, dataset digest, code/config digest, raw-result-artifact digest (a timestamped number without these proves only that someone wrote the number down — red-team #15) | `cortex-graded-eval`, config sha, results-jsonl sha256 |
| `pointer` | commit sha / report path / closeout / raw-data path (raw dumps stay in non-indexed `research/` or `evals/`, per the F2 rule) | `evals/reports/STAGE2_SUMMARY.md` |
| `prov` | `contemporaneous` / `independently-corroborated` / `backfilled-owner-attested` / `legacy-unanchored` | |
| `linked-decision` | `D-…` id(s) the result fed (e.g. a MEASUREMENT-decided row) | `D-20260714-06` |

Seed examples the owner named, as they would appear: "big-pickle measured **0.964** on
the BFCL lane" (`linked-decision` → seed decision #6); "rubric_v2: Haiku/Sonnet κ
0.61→0.92, GLM 0.49→0.70, 4B **regressed** 0.60→0.52"; "TDD test-first ordering: no
measured effect (external evidence, Fucci et al. 2017 — cite in `pointer`)". The Fucci
row is `kind=external-claim`; the big-pickle row is `kind=experiment` and must carry
its command + digests — the log records what the project *learned and relied on*, but
never lets a citation masquerade as a run.

## 2. Relationship to decisions (one number, two logs, no duplication)

The measurement lives HERE (full numbers + method); the decision row (01) carries only
the headline and the `E-` id. Rule of thumb: **numbers in EL, choices in DL**, joined by
ids. A `MEASUREMENT`-decided row in the decision log without a linked `E-` row is
flagged by the index as incomplete.

## 3. Back-fill sources

Richest first: `evals/reports/**` + `evals/promotion_decisions/*.jsonl` (already
structured, numbers verbatim), `calibration/*.md` (κ tables), `audit/**` closeouts with
`tests`/metrics fields, `reviewed/**` (finding counts), memory/overnight-findings docs,
then transcripts for numbers that never landed in a report (the anti-pattern this log
exists to end). Same tiering and owner-sampling as 02 §4.

## 4. Why this belongs in the tamper-evident envelope

Engineering claims are the most attackable part of the corpus ("the numbers were made
up later"). Because EL shards live in git and are covered by the same anchor chain
(04 §3), every recorded number provably existed by its anchor date, in that form —
the same property protecting the decision record protects the measurement record.
The transcript audit feeds both: a disputed number can be traced to the session that
produced it, whose full transcript is hash-committed.
