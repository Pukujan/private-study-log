# Decision note: ship the engine as a PACKAGE instead of vendoring it (2026-07-15)

**Status: PROPOSED (owner's idea, 2026-07-15). Not built. Supersedes the vendoring approach if adopted.**

## Why this came up (the evidence, not a theory)
Vendoring `cortex_core` into `cortex-agent-wrapper` produced exactly the failure mode it was
supposed to avoid, twice in one day:
- The **`app_build` research hole** (`SCAFFOLD → SMOKE → SHOW → CLOSEOUT`, no RESEARCH phase)
  now exists in **both** the brain and the wrapper — one bug, two places to fix.
- Every engine change needs a manual re-vendor + a byte-identity check + a fresh secret-scan.
  (Verified 2026-07-15: wrapper vs brain `state_engine.py` differ in raw bytes — 1,897 CRLF —
  content-identical. Cosmetic, but it means "is it in sync?" costs a hash comparison every time.)
- The wrapper missed `decomposer`/`mission_driver` for a whole session simply because they were
  built *after* the vendor pass.
- This IS gap **H1**: *"a cloned/drop-in wrapper goes stale silently (median 155d, 27-wk patch
  lag) — the #1 structural flaw."* Vendoring is the mechanism of that staleness.

## The proposal
Extract the engine subset into its own installable package; the wrapper declares a **dependency**
on it instead of holding a copy. Updates become `pip install -U` + a pinned version, not a copy.

## The blocker: where does the package live?
| Option | Private? | Update path | Cost |
|---|---|---|---|
| Public PyPI (`cortex-engine`) | ❌ public, name permanent, yank≠delete | `pip install -U` | conflicts with owner's "nothing public" (2026-07-15) |
| **Private git dep + tags** ⭐ | ✅ | `pip install -U "cortex-engine @ git+…@v0.3.0"` | phantomic needs repo access (deploy key / collaborator) |
| Private index (GH Packages) | ✅ | `pip install -U` | auth config on his side |
| Keep vendoring + automate | ✅ | script + secret-scan per release | retains drift risk; doesn't fix H1 |

**Recommended: private git dependency with tags.** No new public exposure; phantomic pins and
updates deliberately; zero manual re-vendor; secret-scan runs **once per release** instead of on
every hand-copy.

## The real work is the SPLIT, not the packaging
The engine currently lives **inside the private brain repo, next to the gold**. Shipping it as a
dependency requires extracting the engine subset into its own repo. Good news: the vendor step
**already selects exactly that subset** (43 modules; `judge`/`calibration`/`promotion*`/
`evaluator`/`arbitrate`/`oracle_crossval`/`keys*`/`evidence_schema` and all data dirs excluded),
so the selection is solved — it's automatable. What's new is release discipline: semver, a
changelog, CI to tag/publish, and the secret-scan wired into the release gate.

## Constraints any implementation must keep
- **The private eval/gold/judge layer never ships.** The package is engine-only (retrieval +
  state machine + orchestration + dispatch shim). Same exclusion list as the vendor pass.
- The package must not depend on modules the wrapper lacks (e.g. `hybrid_build.py` is absent).
- Secret-scan is a **release gate**, not an afterthought.
- Versioning must let phantomic pin (a bad release must be pinnable-around).

## Interaction with in-flight work
Does **not** block the `app_build` research-phase fix (handed to hades 2026-07-15). It only
changes **how that fix reaches phantomic**: cut a version instead of re-vendoring. If this is
adopted, the "re-vendor these files" step in that handoff becomes "release vN and bump the
wrapper's pin."

## Open decisions for the owner
1. Private git dep vs private index vs (later) public PyPI?
2. Does phantomic get repo access (deploy key) — the private-dep path needs it.
3. Who cuts releases, and does the secret-scan gate CI?
