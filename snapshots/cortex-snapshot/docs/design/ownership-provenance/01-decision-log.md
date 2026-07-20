# 01 — Enriched, Sharded, Back-Logged Decision Log

**Extends:** `docs/DECISION-LOG.md` (the 17-row seed table the owner started, currently
uncommitted in the main checkout). That seed covers ~one session; this design makes it
(a) evidentially complete per row, (b) sharded so each file stays human-verifiable,
(c) back-logged across the entire project history.

## 1. Why the enrichment matters legally

The U.S. Copyright Office's January 2025 *Copyrightability* report says AI outputs are
protectable where a human "determined sufficient expressive elements" — creative
**selection, coordination, arrangement, or modification** of AI output, or use of AI as
an assisting **tool** in a larger human work; **prompts alone do not** confer authorship
([USCO Part 2 report](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf);
[Library of Congress summary](https://blogs.loc.gov/copyright/2025/02/inside-the-copyright-offices-report-copyright-and-artificial-intelligence-part-2-copyrightability/)).
So each row must capture not just *what* was decided but the human's **creative control
acts**: the problem HE framed, the alternatives HE weighed, the choice HE made, and the
verifiable pointer proving it happened when claimed. A bare "decision: X" row is weak
evidence; a row tying problem → purpose → decider → why → pointer is much stronger.
**Scope honesty (red-team #16/#17):** this record is *factual process evidence*, not
authorship of expression by itself — selection/arrangement supports at most a thin
compilation claim, and architectural or ship/no-ship choices may be unprotectable
systems/methods under [17 U.S.C. §102(b)](https://www.copyright.gov/title17/92chap1.html).
The decision log rebuts "the human wasn't driving"; it does not, alone, make the owner
the author of agent-written code (see 05 §2).

## 2. Enriched row schema

One markdown table row per decision. Columns (superset of the seed's 6):

| Field | Content | Example |
|---|---|---|
| `id` | `D-YYYYMMDD-NN`, stable, never reused | `D-20260714-03` |
| `date` | ISO date (UTC) of the decision | `2026-07-14` |
| `problem` | The problem/gap **the owner identified** (one clause) | "single decision table too big to verify" |
| `decision` | What was decided (bold headline, as in seed) | **Shard the decision log** |
| `path-vs-alt` | Path chosen vs. the alternative(s) rejected/overridden | shards+index vs. one mega-table |
| `decided-by` | Seed legend kept: `OWNER` / `ARBITRATION` / `MEASUREMENT` / `ORCHESTRATOR` (+ note when awaiting owner confirmation) | `OWNER` |
| `why` | Rationale in the decider's terms | "a human must be able to open one file and verify it" |
| `pointer` | ≥1 verifiable pointer: commit sha, session-id (→ `provenance/transcripts/`), closeout filename, PR # | `6d8b8b8` · `ts:3a47e8fb…` |
| `prov` | Provenance tier of the ROW itself: `contemporaneous` / `independently-corroborated` / `backfilled-owner-attested` / `legacy-unanchored` (see §4) | `contemporaneous` |

Rules: append-only (corrections are new rows referencing the old id, never edits —
same discipline as the audit closeouts in `audit/**`); `pointer` is mandatory — a row
with no pointer is flagged in `INDEX.md` as unanchored; `decided-by` honesty rule from
the seed is preserved verbatim (distinguish OWNER calls from ORCHESTRATOR proposals).

## 3. Sharding + index (so nothing outgrows a human)

**Shard by domain, order by date inside each shard.** Domains are the project's real
work areas (from `docs/PHASE-GATES.md` / the corpus), initially:

```
provenance/decisions/DL-corpus-retrieval.md    # phases 0–2: index, chunking, hybrid search
provenance/decisions/DL-eval-oracles.md        # phase 4 + evals/ lab, judges, gold, calibration
provenance/decisions/DL-platform-mcp.md        # phases 3,5: MCP, packs, patterns, infra, CI
provenance/decisions/DL-wrapper-delivery.md    # two-plane wrapper, phantomic delivery, state engine
provenance/decisions/DL-governance.md          # ops boundary, provenance system itself, policies
provenance/decisions/DL-side-projects.md       # Hades, Hermes-adjacent calls that touched this repo
```

- **Split rule:** a shard passing ~50 rows splits by time (`DL-eval-oracles-2.md`,
  index updated). 50 rows ≈ one long screenful — the verifiability ceiling.
- **New domain rule:** only when ≥5 rows would land in it; otherwise `DL-governance.md`
  takes strays. (Anti-bloat: domains are capped by need, not taxonomy ambition.)
- **`provenance/INDEX.md`** lists every shard: domain, date range, row count, count of
  `legacy-unanchored` rows, latest anchor covering it. One screen, updated on append.
- Standing decisions (seed rows 9, 15, 16) get `date=(standing)` plus the date first
  recorded, and live in the domain they govern.

## 4. Back-logging the full history (the mining plan)

Goal: capture (attempt) **every decision ever made** since `8cc0bf9` (2026-06-24, first
commit). Five sources, mined in this order — cheapest/most-structured first:

| Pass | Source | What it yields | Extraction |
|---|---|---|---|
| 1 | `git log` (398+ commits, all branches) | The **timestamp spine**: dates, shas, authors, and shippable decisions ("shipped X", "not shipped Y", reverts) | Deterministic script: `git log --all --format` → candidate rows from commit subjects; every mined row gets its sha pointer for free |
| 2 | `audit/**` closeouts (JSON, `timestamp`+`task`+`result`) | Per-task outcomes and embedded decisions ("deferred 2.4 KP miner", quarantines) | Deterministic parse of the JSON; each closeout filename is the pointer |
| 3 | `docs/PHASE-GATES.md` | Every gate decision + its evidence citation (ship/no-ship/defer per phase item) | Manual-with-AI-assist: gates are already decision-shaped; ~1 row per gate item |
| 4 | `docs/**` + `reviewed/**` | Design decisions (ADR-0001, EVAL-DESIGN-PHASE2 ship/no-ship calls) and review-driven fixes (e.g. "1 HIGH + 5 MED all fixed") | AI-assisted extraction, each row pointing at the doc + the commit that landed it |
| 5 | 528 session transcripts (`.jsonl`, harness project dir) | The **human-directive layer**: what the owner actually typed — focus-setting, gap-spotting, overrides ("he'll just skip it", the max_tokens=300 catch) | Script parses `role:user` messages per session → candidate directives; AI summarizes; feeds BOTH this log and 02-directive-log |

**Honesty tiers for rows** (mirrors seed decision #14; tightened per red-team #2 —
provenance is assigned from the **earliest immutable receipt**, never from the alleged
event date, and a retrospective row can NEVER be upgraded to `contemporaneous`):

- `contemporaneous` — row written at decision time AND covered by an anchor within the
  current cadence window. Forward-only; nothing back-filled ever qualifies.
- `independently-corroborated` — back-filled, and the pointer is an artifact with its
  own third-party date (GitHub server-side history, provider-side session records).
- `backfilled-owner-attested` — back-filled and owner-confirmed later. This proves the
  owner *now* adopts the reconstruction, not that it is contemporaneous evidence.
- `legacy-unanchored` — reconstructed, not yet confirmed. Counted in `INDEX.md`.

Owner sampling of back-filled rows (02 §4) is reported as a **cohort statistic**
("14% of 2026-06 rows sampled, 0 errors") — it never flips unsampled rows to a
verified tier.

**Attribution honesty in mining:** a mined row may only say `decided-by: OWNER` when the
pointer shows the owner's own words (transcript user-message) or act (his commit, his
merge). Decisions visible only in AI-authored commits/closeouts default to
`ORCHESTRATOR` or `legacy-unanchored` until the owner confirms — over-claiming OWNER
attribution in back-fill is precisely what a challenger would attack (see 05 §3).

**Volume control:** the back-log targets *consequential* decisions (the seed's own bar:
roadblocks, overrides, ship/no-ship, accepted arbitration). Pass-1 commit mining will
surface hundreds of candidates; the filter is "did this choose between real
alternatives or direct subsequent work?" — routine mechanical commits are represented
by the git history itself, which remains the exhaustive lower layer and needs no table.

## 5. Migration of the seed

`docs/DECISION-LOG.md`'s 17 rows are re-homed into the shards with ids assigned
(`D-20260713-01` …), pointers added where recoverable (most reference work with
commits/transcripts from 2026-07-13/14). **Tier honesty (red-team #2):** the seed was
written near-contemporaneously but was uncommitted and unanchored at design time, so
its rows enter as `backfilled-owner-attested` (or `independently-corroborated` where a
pointer carries its own third-party date) — NOT `contemporaneous`. The old file becomes
a one-line pointer to `provenance/INDEX.md` so existing references don't break.
