# Trusted-Runner Attestation Layer — design + build (2026-07-14)

Status: **BUILT (core) + DESIGNED (phased hooks)**. Branch:
`claude/corpus-agent-orchestration-review-121x1q` (worktree).

## The hole (the single keystone the red-teams converged on)

> "There is no authenticated provenance boundary — `evidence` is
> caller-populated, so any tier is forgeable by a caller willing to
> fabricate evidence."

Concretely, before this change:

- `promotion.classify(item_id, evidence)` reads a **caller-supplied dict**.
  A caller who writes `{"checker_decided": True, "objective_verdict":
  "pass", "label_authority": "bfcl_ast_checker"}` reaches `hard_gold`
  (trainable) without any checker ever having run. `objective_checker_gate`
  checks the *shape* of the claim, never its *authenticity*.
- `registry.register(..., trust_tier="hard_gold")` writes whatever tier the
  caller names straight into the trainable corpus.
- `cortex_register(role=...)` stamps an **arbitrary self-claimed role** onto
  the session.

Structural discipline (the gates) is real. Evidence **authenticity** was
never proven. This layer proves it.

> Note on scope: this branch is based off `main` and does **not** contain
> the three sol@xhigh red-team review files or the `provenance_tiers.py` /
> `docs/design/ownership-provenance/` modules named in the build brief —
> those were produced on sibling branches not merged here. This design is
> therefore built against the *actual* integration points in this tree:
> `promotion.py` (`classify`/`decide`), `registry.py` (`register`/export),
> `keys.py`, `authz.py`, and `mcp.cortex_register`. The reconciliation and
> the security property are identical to the brief; only the file the tier
> logic lives in differs (`promotion.py`, not `provenance_tiers.py`).

## The elegant reconciliation (never-wait ⨯ attestation)

Owner policy is **never block on a human**. Attestation must not reintroduce
a human-in-the-loop wait. It doesn't, because attestation is issued by the
**server**, deterministically, at machine speed:

| Evidence carries…                          | Lands at tier            | Trainable? | Usable now? |
|--------------------------------------------|--------------------------|-----------|-------------|
| bare caller dict, no attestation           | `non_human_verified`     | no        | **yes**     |
| a valid **server-signed attestation**      | up to `hard_gold` etc.   | **yes**   | yes         |
| a **forged / expired / replayed / wrong-issuer** attestation | `quarantine` | no | no |

So: **use everything now, labeled** (never-wait preserved), but **only
attested evidence earns the trainable/authoritative tiers** (trust is real).
Unattested is never *silently* trusted — it is explicitly labeled
`non_human_verified`, and a *forged* attestation is never silently
downgraded to usable; it is quarantined as a laundering attempt.

## What an attestation is

A server-issued, signed record binding five things:

```
payload = {
  "check":            <name of the deterministic check that ran>,
  "result":           <its verdict: pass|vulnerable|secure|...>,
  "request_sha256":   <sha256 of the captured request / tool-call bytes>,
  "subject_sha":      <sha256 of the evidence/artifact this attests to>,
  "role_credential":  <a server-signed role credential (see below)>,
  "issuer":           <the server's identity id>,
  "issued_at":        <server-clock ISO8601 (external-clock hook, phased)>,
  "expires_at":       <issued_at + TTL>,
  "nonce":            <uuid4, single-use — replay defense>,
}
signature = HMAC-SHA256(server_secret, canonical_json(payload))
```

`verify_attestation` rejects, in order: unknown/wrong issuer → bad signature
→ expired → replayed nonce → subject mismatch. Any failure ⇒ NOT verified ⇒
the caller's tier claim is quarantined.

### Signing primitive: HMAC-SHA256 (pure stdlib), server-held secret

The threat model is a **caller who does not hold the server's key** forging
evidence. On the actual deployment (single host, owner-privileged, the
server both signs and verifies) a symmetric secret the server holds and the
caller does not is exactly sufficient — and it is pure stdlib (`hmac`,
`hashlib`), matching this repo's no-new-dependency posture (`cryptography`
is not a declared dependency). The secret lives in `CORTEX_ATTEST_SECRET`
or a gitignored `<workspace>/logs/attest_secret.json` (0600-intent,
auto-generated once, never committed) — the same store discipline as
`keys.py`'s `api_keys.json`.

**Phased upgrade (designed, not built): asymmetric Ed25519.** When Cortex
becomes multi-host, swap the HMAC for an Ed25519 keypair so verifiers hold
only the *public* key. The `attestation.py` seam (`_sign`/`_verify_sig`)
isolates this to two functions; the payload/verify logic is unchanged.

## The four boundary pieces

### 1. Server-signed attestation (`cortex_core/attestation.py`) — BUILT

`issue_attestation(...)`, `verify_attestation(...)`, an issuer identity, a
file-backed single-use **nonce store** for replay defense, and the
`_sign`/`_verify_sig` HMAC seam.

- **Gateway byte-capture hook (phased):** `request_sha256` binds the bytes.
  In this build the caller/issuer passes the bytes; in the full deployment a
  gateway in front of the server captures them out-of-band so the *executor*
  can't choose what gets hashed. The field and its verification exist now;
  the out-of-band capture is the phased half. Honestly labeled.
- **External-clock hook (phased):** `issued_at` is the server clock today.
  The payload reserves room for an RFC-3161 / OpenTimestamps token
  (`external_timestamp`) so a signed third-party clock can be bound later
  without a format change. Built: server clock + TTL + expiry check. Phased:
  the external TSA round-trip.

### 2. `derive_tier` — BUILT (`promotion.derive_tier`)

The new attestation-aware entry point (additive; `classify`/`decide`
untouched so every existing caller keeps working):

```
derive_tier(item_id, evidence, *, verifier=verify_attestation, now=None, nonce_store=None)
  attestation present & valid   -> classify()   (can reach hard_gold / cross_vendor)
  attestation present & INVALID -> QUARANTINE   (anti-laundering — never downgraded)
  attestation ABSENT            -> classify(), but any TRAINABLE result is
                                   relabeled -> non_human_verified (usable, not trainable)
```

`non_human_verified` is added to `TIER_ORDER` (above `quarantine`, below the
trainable tiers) and to `registry.TRUST_TIERS`. It is explicitly **not** in
`TRAINABLE`.

### 3. Role credential (`attestation.issue_role_credential` / `verify_role_credential`) — BUILT

Registration issues a **server-signed** credential binding
`{key_id, tenant_id, role}`. `authenticate_role(claimed_role, credential,
key_info)` returns the authoritative role:

- a **privileged** role (`admin`, `gold_author`, `trainer`) REQUIRES a valid
  signed credential bound to the presenting key — a bare self-claim is
  rejected (falls back to the unprivileged `agent`, and the attempt is
  recorded);
- an unprivileged role is a harmless self-claim (never-wait: still usable).

`cortex_register` now records both `claimed_role` and the authenticated
`role` + `role_authenticated` boolean. Because `role` was previously only
*descriptive* (privilege is `is_admin` / key `scope`), this is additive and
backward-compatible; it closes the hole *before* any future op trusts
`role`, and it authenticates the `role_credential` an attestation carries.

### 4. Mandatory training-export chokepoint — BUILT (`registry.register` + `assert_attested_for_training`)

`register()` now refuses to write a **trainable** tier
(`hard_gold` / `cross_vendor_synthetic_gold`) unless the metadata carries an
attestation that verifies against the subject content. Non-trainable tiers
(the existing seed path: `weak_candidate_exemplar` / `unverified` /
`non_human_verified`) are unaffected — so no existing flow breaks, and
nothing reaches a trainable sink without passing attestation verification.
`assert_attested_for_training(record)` is the reusable predicate any other
export path calls.

## Anti-laundering invariants (the tests pin these)

1. unattested evidence → `non_human_verified` **and usable** (not blocked).
2. valid signed attestation → `hard_gold` / trainable.
3. forged / replayed / expired / wrong-issuer attestation → `quarantine`
   (never silently trusted, never silently downgraded to usable).
4. a privileged self-claimed role with no signed credential → rejected.
5. the training-export chokepoint refuses unattested trainable data.

## Honest limits (BUILT vs DESIGNED)

- **BUILT:** HMAC signing/verification, issuer binding, expiry, single-use
  nonce replay defense, subject binding, `derive_tier`, the
  `non_human_verified` tier, role credentials + `authenticate_role`, the
  `register()` trainable chokepoint, `cortex_register` role authentication.
- **PHASED (designed, seams in place):** (a) out-of-band **gateway
  byte-capture** — today the issuer hashes the bytes it's given; (b)
  **external signed clock** (RFC-3161 / OTS) — today the server clock; (c)
  **separate-OS-identity** for the signing process and (d) **asymmetric
  Ed25519** for multi-host verification. These do not weaken the security
  property *on the actual single-host deployment* (the server holds the
  secret; the caller does not); they harden it for a hostile-host / multi-
  tenant future. Called out so nobody mistakes the phased half for built.
- The secret-at-rest is only as strong as the host: an attacker with
  read access to `logs/attest_secret.json` (i.e. already owner-privileged on
  the single host) can forge. That is the same trust boundary as the admin
  token and the API-key store — attestation does not claim to defend against
  a compromised owner host, and says so.

## Post-review hardening (sol@xhigh, 2026-07-14 — folded)

The design was red-teamed by `gpt-5.6-sol` at `model_reasoning_effort=xhigh`
(`reviewed/trusted-runner-attestation-sol-xhigh-2026-07-14.md`). The HMAC
core was found sound against a remote caller; six real gaps were surfaced.
Folded:

- **Verdict binding (P0):** a signature proves the server *signed*, not that
  the check *passed*. `verify_attestation(require_passing=True)` (default) and
  the registry chokepoint now reject a genuine `result="fail"` attestation, so
  it can't authorize a trainable tier.
- **Role fail-open (P0):** a privileged role now requires a *complete* bound
  key principal (`key_id`+`tenant_id`); a stolen credential presented with no
  key binding fails closed.
- **Subject canonicalization (P1):** `_evidence_subject_sha` rejects
  non-JSON-native values (`default=str` removed) and non-string keys, closing
  the `"pass"`-stringify and `{1:...}`/`{"1":...}` aliasing collisions.
- **Replay by default (P1, partial):** the registry authoritative sink now
  defaults to a persistent per-workspace nonce store, so replay is defended
  even when no store is injected. Fully-atomic DB-uniqueness single-consume is
  the phased hardening.
- **Future-issuance + weak-secret guards (P2):** an attestation issued in the
  future (beyond 300s skew) is rejected; an env-supplied secret under 32 chars
  is refused rather than signed with.

**Structural boundaries (documented, per sol P0 #2/#3):** `issue_attestation`
and `issue_role_credential` are **server-internal only** — never exposed as
MCP tools, so there is no caller-reachable signing oracle. The registry
trainable write is the **single authoritative sink**; raw `classify()` output
carries no authoritative meaning. The next phase signs a *complete policy
decision* (audience, tenant, item identity, approved checker+version, tier,
operation) and re-verifies at read/export time, not only at registration.

**Honest residual (unchanged, sol-agreed):** on the single owner-privileged
host, an in-process caller that can `import cortex_core` and read
`logs/attest_secret.json` can still forge — the OS account is the trust root
there. This layer closes the **remote, caller-populated evidence** hole the
three red-teams named; it does not defend a compromised owner host. Separate-
OS signing identity, Ed25519, gateway byte-capture, and the external signed
clock remain phased.
