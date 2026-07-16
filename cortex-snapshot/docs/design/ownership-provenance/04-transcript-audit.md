# 04 — Transcript Audit: Public Redacted Index + Private Archive + Cryptographic Commitment

**The hard constraint.** This repo is PUBLIC (`project_public_repo_ops_boundary`). The
raw Claude Code session transcripts (528 `.jsonl` files at design time, in the harness
project dir `C:\Users\pujan\.claude\projects\d--claude-stupidly-simple-cortex\`) contain
personal/Hades/other-project material and MUST NOT be published. The design publishes
only **an index and hiding commitments**, keeps the full record private, and binds the
two so the private record is provably intact and bounded-in-time without exposing a
byte of content — or any correlatable metadata (red-team #11).

**What this whole mechanism proves — exactly and only this (red-team #1):** that each
committed artifact existed, byte-identical, **no later than** its anchor time, and that
the commitment log has not been altered since. It does NOT prove when the events inside
a transcript occurred, who typed them, or that the archive is complete. Those get
corroboration (§6, §7), never cryptographic proof. A transcript fabricated five minutes
before an anchor would pass every check here — which is why anchors must run frequently
and start NOW (README Step 0): the value of the record is that fabricating it *going
forward* would require faking it in near-real-time, forever, across independent systems.

## 1. Private archive (outside the public repo)

- **Content-addressed, immutable captures:**
  `ops-local/provenance-private/transcripts/<session>/<capture-seq>-<sha256>.jsonl`.
  Never overwrite; a re-archive of a still-live session is a new capture with the next
  `seq` (red-team #13). Snapshot atomically: copy to temp, hash, re-hash the source; if
  the source changed mid-copy, retry — never manifest a torn file.
- **Two encrypted offsite copies** on independent media/providers, with documented key
  recovery and a periodic fixity audit (re-hash against the manifest). A hash whose
  private bytes are lost is unverifiable — the archive, not the math, is the single
  point of failure.
- `ops-local/` is the established gitignored private zone (`docs/OPS-BOUNDARY.md`).

## 2. Public commitment (in this repo)

**(a) One cumulative manifest** — `provenance/anchors/manifest.jsonl`, **append-only
for its whole life** (not per-date files; red-team #4). One line per capture, canonical
JSON per [RFC 8785 (JCS)](https://datatracker.ietf.org/doc/html/rfc8785), fixed field
set (red-team #5):

```json
{"seq":417,"kind":"transcript","handle":"T-9f3a","capture_id":"c-2026-07-20-03",
 "version":2,"prev_artifact_hash":"<hex|null>","commit":"<hex>","period":"2026-07"}
```

- `seq` — global, gapless, immutable sequence number (the log's spine).
- `handle` — **random public pseudonym**, not the real session id; sizes, message
  counts, and exact start/end times are OMITTED, `period` is coarsened to the month
  (red-team #11 — ids/times/sizes are correlation metadata; the private
  `handle-map.md` restores the join for an adjudicator).
- `commit` — a **hiding** commitment: `SHA-256(domain-tag ‖ nonce256 ‖ artifact-bytes)`
  with a fresh random per-artifact nonce stored privately (`nonces/`), revealed only
  together with the artifact. Plain `SHA-256(file)` is binding but **not hiding** —
  anyone holding a candidate copy (breach, provider export, counterparty) could confirm
  membership, and stable hashes enable cross-dataset linkage (red-team #12).
- `version`/`prev_artifact_hash` — chains re-captures of the same session explicitly.

**(b) Merkle head over the manifest** — leaves are the RFC-8785 bytes of each line in
`seq` order; **domain-separated hashing** (`0x00‖leaf`, `0x01‖left‖right`, odd node
promoted — the [RFC 9162](https://www.rfc-editor.org/rfc/rfc9162.html) construction);
each anchor records `(tree_size, root, prev_root)`. **Append-only is verified by
prefix, not trusted by chaining (red-team #4):** because the manifest itself is public,
any verifier checks that today's manifest is a byte-prefix of nothing-removed history
(old copy ⊂ new copy, `tree_size` monotone, old root recomputable from the prefix).
This is the lightweight equivalent of CT consistency proofs, which exist for verifiers
who *cannot* see the whole log; ours can. (Deliberate simplification vs. red-team's
full-RFC-9162 recommendation — recorded in the review doc as a disagreement.)

**(c) Redacted index** — `provenance/transcripts/TS-INDEX-YYYY-MM.md`, one row per
handle: `period · handle · categorical directive summary · seq`. Summaries are
**categorical paraphrases** written for publication ("set eval-lane focus; overrode a
premature ship call"), never verbatim; sessions mixing in other-project/personal
material get a category only ("mixed session — cortex portion summarized") or no
summary. Before publication, every index/manifest change passes the existing secret
gate (`ops/secret_audit.py`) plus a correlation review (could this row be joined to
private activity?). A private `redaction-map-<date>.md` records class-level what the
paraphrases omit — documented policy, not silent editing.

**(d) Test vectors + verifier** — `provenance/anchors/verify_anchor.py` (stdlib-only)
plus published test vectors, so a third party verifies without trusting our tooling
(red-team #5).

## 3. Anchoring in time (the part that resists "fabricated later")

The unit that gets anchored is a **canonical anchor envelope** —
`provenance/anchors/envelope-<date>.json` (RFC 8785): `{tree_size, root, prev_root,
manifest_sha256, git_commit, git_tag, date}`. The envelope is **signed first, and the
signed envelope file is what gets externally timestamped** — so the external timestamp
covers both content AND the owner's signature (red-team #7: stamping a bare git SHA-1
commit id both inherits SHA-1 and leaves the signature undated).

Layers, and what each actually gives:

1. **Git.** Committing manifest+envelope puts them in git's object DAG — post-commit
   mutation changes every descendant id
   ([git as a Merkle-tree archive](https://medium.com/swlh/git-as-cryptographically-tamperproof-file-archive-using-chained-rfc3161-timestamps-ad15836b883)).
   Gives integrity + ordering. **Not time** (`GIT_COMMITTER_DATE` is self-asserted) and
   the repo's object format is SHA-1 — git is scaffolding here, never the proof.
2. **Owner signature.** The envelope is signed with the owner's key (plus
   `git tag -s provenance-anchor-YYYYMMDD` for convenience —
   [git-verify-tag](https://git-scm.com/docs/git-verify-tag)). Proves *that key*
   signed *these bytes* — not civil identity, not signing time; tags are backdatable
   and refs replaceable (red-team #8;
   [tag-rewrite attacks](https://dev.to/kanywst/hacking-github-from-tag-rewrites-to-dangling-commits-where-the-git-protocol-trusts-you-without-2o4h)).
   Mitigations: key fingerprint pinned via independent channels (GitHub profile,
   repo README, ideally a keyserver/social proof predating disputes); hardware-backed
   key custody; rotation/revocation events logged in the decision log.
3. **Third-party corroboration: the push.** GitHub-side push/PR/merge records and
   third-party clones are corroboration only — not durable cryptographic receipts
   (red-team #10).
4. **External timestamps over the signed envelope** — the load-bearing layer; do both:
   - **OpenTimestamps:** `ots stamp envelope-<date>.json.sig` → calendar aggregation →
     Bitcoin commitment
     ([Todd, announcement](https://petertodd.org/2016/opentimestamps-announcement)).
     **An anchor is not complete until `ots upgrade` yields a Bitcoin-verifiable proof**;
     record block height/hash and the exact verify command (red-team #6 — the initial
     `.ots` is only a pending calendar attestation, per the
     [client docs](https://github.com/opentimestamps/opentimestamps-client)).
   - **RFC 3161 token** over the same signed envelope
     ([RFC 3161](https://datatracker.ietf.org/doc/html/rfc3161);
     [sigstore/timestamp-authority](https://github.com/sigstore/timestamp-authority)).
     With a real validation policy (red-team #9): chosen TSA + policy OID (a counsel
     item), nonce checked, message imprint checked, and the full evidence set retained
     (request, response, TSA cert chain, contemporaneous CRL/OCSP); re-timestamp
     archives before algorithm obsolescence. PKI trust and proof-of-work trust fail
     differently — that is the point of doing both.

## 4. Cadence + procedure (lightweight)

One script (`ops/provenance_anchor.py`, stdlib `hashlib` + `ots`/TSA calls):
snapshot new captures → append manifest lines → compute root → write + sign envelope →
commit + tag → push → stamp (OTS + TSA) → later `ots upgrade` → commit proofs.
**Cadence: weekly + at every milestone/delivery** (a compromise vs. the red-team's
per-session recommendation; the up-to-a-week fabrication window is accepted and stated —
the owner can tighten cadence without design change). **Step 0: the first anchor runs
the day this is built** — every day before it is unanchored history.

## 5. Verification procedure (what a skeptic does)

1. Recompute the Merkle root from the public manifest; check `tree_size`/prefix
   against any older copy of the manifest (append-only check).
2. Verify the envelope signature against the pinned fingerprint.
3. `ots verify` the signed envelope against Bitcoin (and/or validate the `.tsr` chain)
   → the record existed by the anchored time.
4. For a disputed session: through counsel under court-controlled procedures (a
   protective order is the court's to grant, not ours to promise — red-team #14), the
   owner produces the private capture + its nonce; the verifier recomputes
   `SHA-256(tag‖nonce‖bytes)` → matches the manifest line → that exact content existed
   unmodified at anchor time. Content goes to the adjudicator only, never the repo.

## 6. Census honesty (completeness is NOT provable — red-team #3)

The owner controls both the source directory and the manifest; nothing cryptographic
prevents omitting a damaging session before its first anchor. So the design claims no
"full set." Instead it publishes a **census**: expected capture count per period, the
count actually manifested, and an explicit gap/tombstone list; where a provider-side
inventory or authenticated export exists (e.g. an Anthropic account export), reconcile
against it and record the reconciliation result. The census makes *silent* omission a
detectable inconsistency going forward; pre-first-anchor completeness remains an
owner attestation, stated as such.

## 7. Chain of custody (what a court would additionally need — red-team #14)

Hash-match proves identity with the committed bytes, not admissibility. The build
includes a one-page **preservation SOP** (who captures, with what tool/version, from
which device/account, access controls on `ops-local/`), a **custodian declaration
template** (the [FRE 902(14)](https://www.govinfo.gov/content/pkg/USCODE-2023-title28/pdf/USCODE-2023-title28-app-federalru-dup2-rule902.pdf)
qualified-person certification path for self-authenticating hash-verified copies), a
capture log, and a counsel-controlled disclosure protocol. Authentication still leaves
hearsay/weight questions — counsel's domain, flagged and not answered here.
