# Ownership Provenance & Audit System — Design Index

**Status:** DESIGN (source-backed; red-teamed by Codex `gpt-5.6-sol`@xhigh 2026-07-14,
all 22 findings reconciled — `reviewed/ownership-provenance-redteam-sol-xhigh-2026-07-14.md`).
Nothing here is built yet; until built and anchored it provides **zero** protection,
and this design's own date is self-asserted. **Step 0 of the build is to run the first
anchor immediately** — cryptographic time starts then, not before.
**Owner:** Pujan. **Date:** 2026-07-14.
**Not legal advice** — this is an engineering design informed by public legal guidance;
the owner should confirm the legal framing with counsel before relying on it in a dispute.

## What this is

A lightweight, human-verifiable, tamper-evident record of the owner's direction of this
project — the focus he set, the gaps he identified, the decisions he made, the work he
assigned and overrode. Stated at its defensible strength (adopted from the red-team):
the system **preserves owner-attested records, proves their byte-level integrity, and
establishes an external upper bound on when each anchored version existed. Separate
evidence is required for event time, human identity, completeness, expressive
authorship, and ownership** — the design supplies *corroboration* for those, never proof.
It has **two log families** on one shared infrastructure:

## What it proves (and what it only corroborates)

| Proposition | Status |
|---|---|
| Byte integrity — an anchored artifact is unmodified | **Proven** (hash + Merkle + external timestamp) |
| Existence upper bound — it existed no later than anchor time | **Proven** (OpenTimestamps / RFC 3161) |
| Event time — the events inside happened when their internal timestamps say | Corroborated only (provider-side records, GitHub history) |
| Actor identity — the owner typed the human messages / signed the anchors | Corroborated only (account bindings, pinned signing key) |
| Completeness — nothing was omitted before first anchor | **Not provable**; addressed by census reporting (04 §6) |
| Truth / authorship of content — statements are accurate; expression is human-authored | Not addressed by cryptography at all; see 05 |

- **(A) Decision/directive logs** — the *legal-provenance* leg: every human decision
  and directive, tied to problem/purpose/why/timestamp/verifiable pointer.
- **(B) Engineering logs** — the *engineering-history* leg: what was learned, tested,
  measured (real numbers, A/B/C results, benchmark outcomes, lessons).

Both are: sharded so a human can open ONE file and verify it; indexed by date;
back-logged across the whole project history (git + closeouts + gates + reviews +
528 session transcripts); and split into a PUBLIC redacted record in this repo plus
a PRIVATE full record outside it, tied together by cryptographic commitment
(hash manifest → Merkle root → signed git tag + external timestamp).

## Design documents

| Doc | Piece |
|---|---|
| [01-decision-log.md](01-decision-log.md) | Enriched decision table: schema, sharding + index, back-log mining across all history |
| [02-directive-log.md](02-directive-log.md) | Human-effort / task-assignment log: directive → AI-executed work linkage |
| [03-engineering-log.md](03-engineering-log.md) | Engineering log family: learned / tested / measured, same infrastructure |
| [04-transcript-audit.md](04-transcript-audit.md) | Transcript audit: public redacted index + private archive + crypto commitment + anchoring |
| [05-governance-authorship.md](05-governance-authorship.md) | Governance/legal framing: how 1–4 support the human-authorship claim; attack/answer analysis; caveats |

## Runtime layout (what actually gets built — the lightweight budget)

```
provenance/                     # PUBLIC, in this repo, indexed by cortex
  INDEX.md                      # one screen: every shard, date range, row count, anchor status
  decisions/DL-<domain>.md      # sharded decision tables (append-only)
  directives/HD-<YYYY-MM>.md    # human-directive log, monthly shards
  engineering/EL-<domain>.md    # engineering-log shards
  transcripts/TS-INDEX-<YYYY-MM>.md   # public redacted transcript index (dates, summaries, commitments)
  CHAIN-OF-TITLE.md             # who holds rights: contributors, provider-terms assignments, licenses
  anchors/
    manifest.jsonl              # CUMULATIVE append-only commitment log (salted hashes only — no content)
    envelope-<date>.json        # canonical signed anchor envelope (root, tree size, prev head, git ids)
    *.ots / *.tsr               # OpenTimestamps proofs / RFC 3161 tokens over each SIGNED envelope
  verify_anchor.py              # stdlib verifier + published test vectors

ops-local/provenance-private/   # PRIVATE (gitignored, per docs/OPS-BOUNDARY.md)
  transcripts/<session>/<seq>-<sha256>.jsonl  # content-addressed immutable captures (never overwrite)
  nonces/                       # per-artifact commitment nonces (revealed only with the artifact)
  handle-map.md                 # public random handle -> real session id
  redaction-map-<date>.md       # what was redacted from public rows and why (class-level)
```

Budget: the public side is **one index + ~6–10 shard files + one anchors dir**. If a
shard passes ~50 rows it splits (`-2` suffix) — that is the entire growth rule.
Anything beyond this layout is bloat and out of scope (no databases, no web UI,
no per-decision files, no duplicate copies of transcripts in-repo).

## The one-paragraph legal theory (details + caveats in 05)

Under U.S. law copyright requires a human author (Thaler v. Perlmutter, D.C. Cir.
2025 — the negative floor: a machine cannot be the statutory author), and per the
U.S. Copyright Office's 2023 registration guidance and January 2025 *Copyrightability*
report (agency guidance, not statute), AI output is protectable only to the extent a
human determined sufficient expressive elements — creative selection/arrangement/
modification; prompts and high-level direction alone are not enough. This system does
**not** claim "directing the project = authoring the code." It will build the factual
record — who framed, chose, overrode, arranged, gated, and when — that (a) supports the
narrow copyright theories that do exist (human-authored text, creative modification,
thin compilation/arrangement, per 17 U.S.C. §102(b)/§103(b) limits), (b) evidences the
contract-based ownership leg (provider output-assignment terms, chain of title), and
(c) rebuts factual challenges ("fabricated later", "the human wasn't involved").
See [05-governance-authorship.md](05-governance-authorship.md) for sources and limits.
