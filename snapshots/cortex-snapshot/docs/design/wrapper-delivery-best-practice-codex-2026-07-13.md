# Cortex-local agent-wrapper delivery: Codex independent design

**Status:** SDD proposal; second reviewer; intentionally disagree-by-default  
**Date:** 2026-07-13  
**Scope:** delivery and assimilation contract, not a redesign of the Cortex engine

## Decision in one paragraph

Do **not** ship “clone a private repo, expose two keys, run `init.py`, probe every
9router model, and let the agent assimilate a large protocol folder” as the product
contract. A clone is an acquisition mechanism, not onboarding. Ship one small,
versioned and signed wrapper release whose sole public entry point is **`cortex init`**.
The command performs a non-secret preflight, asks the human for one explicit data-use
choice, writes only a bounded managed stanza for the detected host, configures exactly
one read-only Cortex MCP server, and optionally probes a *selected bounded set* of
9router candidates. The server exposes a compact onboarding/status resource for facts
that may change. The two keys remain legitimate capabilities, but neither key is an
assimilation step and 9router is an optional execution adapter, not a condition of using
the read-only brain. No devcontainer is part of the default path.

This differs from the current approach because the repository already learned that the
right mechanism is “make the good path cheapest, record automatically, detect skips
afterward, and refuse only for safety,” not a better-worded workflow mandate
(`docs/design/cortex-redesign-CORRECTED-spec.md:13-20`). It also learned that the often
repeated 50k explanation is not established: one measured 38-tool server is 12,237
tokens, while duplicate surfaces and accumulated refusal text are amplifiers
(`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:38-56,89-114`).

## 1. SDD: delivery contract

### 1.1 Actors and invariants

The **collaborator** owns the workstation, keys, local `.cortex/` data, consent choice,
and final approval of configuration changes. The **host agent** (Hermes, Claude or
Codex) consumes a short native instruction stanza and ordinary MCP capabilities; it is
not made the installer or policy adjudicator. The **wrapper CLI** is the deterministic
installer/doctor/uninstaller. The **Cortex brain** authenticates a least-privilege read
tenant and is the authority for server capabilities and current onboarding facts.
The **9router adapter** discovers and tests models but cannot change the host's policy
or send task content during installation.

The release MUST preserve these invariants:

1. **Native work is never refused for process compliance.** No missing search receipt,
   spec, test, doc, closeout, phase or consent-to-telemetry may deny an editor/tool call.
   Process omissions may become local post-hoc facts only. This is the corrected
   design's explicit zero-refusal B arm (`docs/design/cortex-redesign-CORRECTED-spec.md:22-28,55-58`).
2. **Near-zero resting context.** The host stanza contains identity, one sentence of
   use, opt-out/offline behavior and a pointer to on-demand help—no state chart, tier
   catalog or governance manual. The corpus target is about 250 tokens for an index,
   because even the 2,554-token seven-tool “core” consumes 32% of an 8k window
   (`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:183-205`).
3. **One Cortex surface.** Init detects an existing Cortex endpoint and reports a
   conflict; it never silently adds a duplicate. Removing a duplicate server was found
   to be at least as important as tiering (`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:103-114,175-181`).
4. **Consent is enforced, not narrated.** Until the server has bound a consent policy
   to the tenant, no query is sent. `private` means the server's request path writes no
   query, tool-event, mirror, trace or durable access-log payload attributable to the
   request. Operational counters, if unavoidable, must be content-free, tenant-free,
   documented, and tested. Consent is scoped, revocable, time-bounded and purgeable,
   matching the corpus envelope (`docs/design/cortex-redesign-CORRECTED-spec.md:46-50`).
5. **Offline is a capability, not fake parity.** L0 supplies the small local guide and
   project-local files; it returns an explicit `offline/no-corpus` result and never
   claims access to the brain's corpus/oracles. The current DATA-USE document correctly
   admits this distinction (`../cortex-agent-wrapper/DATA-USE.md:7-13`).
6. **Idempotent and reversible.** Init may create `.cortex/config.json`, a minimal
   `.cortex/README.md`, secret placeholders, and one delimited host stanza. Re-running
   produces no semantic diff; `cortex uninstall` removes only manifest-owned files and
   the exact managed stanza. Existing host instructions are never replaced.
7. **Fail closed on secrets and data egress; fail open on work.** Missing/invalid keys,
   TLS/auth failures and consent mismatch disable remote Cortex/9router calls, give one
   actionable diagnostic, and leave native agent work available.

### 1.2 Versioned machine contract

The release contains a small signed `cortex-wrapper-manifest.json` with:

```json
{
  "schema_version": 1,
  "release": "1.0.0",
  "min_python": "3.11",
  "managed_paths": [".cortex/config.json", ".cortex/README.md"],
  "hosts": ["hermes", "claude", "codex"],
  "mcp_server_name": "cortex-brain",
  "mcp_capability": "read",
  "consent_modes": ["private", "metrics", "off"],
  "default_context_budget_tokens": 1000
}
```

The signature/checksum establishes release integrity; it does **not** confer trust on
runtime server responses or turn consent into boilerplate. Init records the release
digest and exactly which files/stanzas it owns. Configuration validation is JSON-schema
or equivalent deterministic validation; prose is never the source of truth.

**Training-knowledge best practice:** this applies common CLI-init and package-manager
patterns: dry-run, explicit diff, idempotence, ownership manifest, reversible uninstall,
non-interactive flags for CI, and exit codes with machine-readable diagnostics. It also
uses the AGENTS.md convention of a short repository-scoped instruction file and the MCP
resource convention for on-demand orientation. These practices are not established by a
specific local corpus experiment here; they are external engineering knowledge and must
be validated by the tests below.

### 1.3 Minimal assimilation flow the host SHOULD follow

“Assimilation” is discovery, not behavioral takeover:

1. Human runs `cortex init` (or `cortex init --dry-run`). The CLI detects the host,
   Python/network state, duplicate MCP registrations and whether this is an existing
   project. It prints the exact proposed writes.
2. Human chooses `off`, `private`, or `metrics`. No preselected positive-consent option.
   `off` installs local L0 only. `private` enables brain reads under a server-enforced
   no-content/no-tenant-log policy. `metrics` shows fields, purpose, retention and purge
   command before affirmative confirmation.
3. Human supplies the read-only brain credential through the host's secret store or
   environment. Init validates scope with a metadata/status call that carries no task
   content. The CLI never prints or persists the raw key in its manifest.
4. If the collaborator wants 9router, they supply its key and choose candidate models or
   a maximum probe count. Init lists IDs first, then probes only the selection. No model
   response is needed for basic Cortex use.
5. After approval, init atomically writes the minimal files and one native host stanza.
   The stanza says, in substance: “Cortex offers optional read-only prior-work search.
   Use it when relevant; absence/offline never blocks work. Load `cortex://onboarding`
   only when setup/help is needed.”
6. On the first relevant task, the host MAY call compact `cortex_status` and search. It
   SHOULD use returned evidence as evidence, not authority, and proceed normally on
   `no_coverage`, `offline`, or `unauthorized`. It MUST NOT walk phases merely to prove
   compliance.
7. Post-run detection/scribing, if enabled, runs locally and asynchronously. It cannot
   add a foreground turn or a stop gate. Remote export follows the selected consent
   envelope and defaults to structured metrics, consistent with the corrected design's
   local-source/fail-open-viewer split (`docs/design/cortex-redesign-CORRECTED-spec.md:40-50`).

The host **SHOULD**, rather than MUST, search Cortex before a materially relevant external
search or design decision. This deliberately disagrees with the current unconditional
“search the brain FIRST” host mandate (`../cortex-agent-wrapper/AGENTS.md:21-28`). A trivial
format, local fact, or explicitly offline task should not pay a protocol turn. Detection
can later show whether the heuristic is too permissive.

### 1.4 Consent and governance flow

There is one setup-time decision and no per-task ceremony. `off` means no Cortex network
connection. `private` means remote retrieval is allowed only after the server attests the
tenant's enforced no-log policy; if it cannot attest, the client behaves as `off`.
`metrics` permits only the displayed structured fields; query text, local files,
prompts/responses, secrets and proprietary outputs remain excluded. A separate explicit
future grant would be required for redacted artifacts. Revocation takes effect before the
next request; `cortex consent show|set|revoke|purge` is deterministic and auditable.

This fixes a material false contract in the current wrapper: it defaults a local env flag
to opt-out (`../cortex-agent-wrapper/.env.example:12-15`) but says opt-out users still use
the brain while the owner manually applies a no-log flag and purge
(`../cortex-agent-wrapper/DATA-USE.md:29-38`). A client-only flag that the server does not
enforce is not consent control. It cannot satisfy “opt-out truly no-logs.”

LICENSE and DATA-USE are short human documents backed by manifest identifiers and
machine checks. VALIDATION distinguishes tested facts, limitations and aspirational
claims. The CLI requires acceptance only where law/license requires it; acceptance may
prevent installation or remote service access, but never blocks unrelated host-agent
work. That is a product boundary, not an in-band plan-mode gate.

### 1.5 Failure modes explicitly prohibited

- **Protocol-as-task:** no mandated chart recitation, project creation, spec or closeout
  before ordinary work. Weak models were observed bouncing through refusal-directed tool
  calls and treating protocol as the task (`docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md:60-67`).
- **Documentation injection:** do not ask every host to read `START-HERE`, `STATE-MACHINE`,
  `MULTIAGENT`, DATA-USE and a 358-row model table at startup. Progressive disclosure is
  name/index → schema → body on demand (`docs/design/cortex-redesign-CORRECTED-spec.md:22-27`).
- **Probe storm:** never liveness-call every advertised router model by default. The
  current command explicitly probes every model (`../cortex-agent-wrapper/.cortex/scripts/ninerouter_tiers.py:27-32,226-243`),
  and the checked-in result is 358 models with only eight responding
  (`../cortex-agent-wrapper/.cortex/models.tiers.md:8-13`). This wastes money/time, creates
  privacy egress, and produces a 33KB prompt magnet.
- **Host-file takeover:** no default `--host all`, no creation of three instruction files,
  and no “all output lives under `.cortex/projects`” rule. The present init defaults to all
  hosts and edits/creates their root rule files (`../cortex-agent-wrapper/.cortex/init.py:35-69,93-107`),
  while its read-folder tells agents to place all output under the wrapper hierarchy
  (`../cortex-agent-wrapper/.cortex/START-HERE.md:96-102`). That conflicts with an existing
  repository's layout and makes the wrapper the task.
- **Narrative/code divergence:** do not duplicate the server state chart locally. The
  present wrapper says its chart must be manually kept in sync
  (`../cortex-agent-wrapper/.cortex/protocol/STATE-MACHINE.md:8-18`); the corpus has already
  caught prose/code phase divergence elsewhere. Current server facts belong in the MCP
  resource/status response.
- **Security theater:** `.env` gitignore is useful but insufficient. Never put secrets in
  generated command lines, logs, tier markdown, manifests, telemetry or exception text.
- **Devcontainer as prerequisite:** it adds image supply-chain, Docker, filesystem and host
  integration complexity without solving a stdlib thin-client problem. Offer it later as
  an optional reproducibility fixture, not delivery.

## 2. The one clean delivery shape

**Recommendation: signed release + `cortex init`, with server-owned on-demand MCP
onboarding.** This is one product shape, not three competing installers:

```text
verified release / private source checkout
        └── cortex init [--dry-run] [--host detected]
              ├── consent + secret preflight
              ├── one MCP registration
              ├── minimal .cortex config/readme + one host stanza
              └── optional bounded 9router selection/probe
                         ↓
              cortex://onboarding (loaded only on demand)
```

The private repo can remain how the collaborator receives source, but “clone and let the
agent assimilate” is not the interface. `init.py` is close in spirit—stdlib, idempotent,
managed blocks and no process refusals are good (`../cortex-agent-wrapper/.cortex/init.py:3-19`)—but it conflates installing host policy with creating a Cortex-contained project,
defaults to all hosts, and has no consent enforcement, integrity manifest, duplicate-server
check, doctor, rollback or bounded router probe.

The MCP onboarding resource complements rather than replaces init: init owns local,
host-specific configuration; the server resource owns current endpoint capabilities,
data policy attestation and compact help. A server-only onboarding resource cannot install
the connection needed to reach itself. A devcontainer is disproportionate. A signed
manifest alone is integrity metadata, not an onboarding UX.

## 3. Frozen deterministic TDD list

All network tests use a local fake Cortex server, fake 9router and a socket-deny fixture.
No LLM judge determines pass/fail. Freeze fixtures, expected bytes/JSON, clocks, random
IDs and platform path variants. Run on Windows and Linux.

1. **`test_clean_install_dry_run_has_zero_writes_and_zero_network`** — on an empty repo,
   dry-run reports the exact proposed paths/stanza and performs no filesystem mutation,
   DNS, socket or subprocess launch.
2. **`test_clean_install_one_host_idempotent_and_reversible`** — init detects the fixture's
   host, preserves pre-existing instructions byte-for-byte outside one managed block,
   creates only manifest-owned files, second run has empty diff, and uninstall restores
   the original tree exactly.
3. **`test_duplicate_cortex_surface_is_not_added`** — with any equivalent Cortex MCP URL or
   server identity already registered, init exits with a diagnostic and does not add a
   second surface. It never edits unrelated MCP servers.
4. **`test_secret_non_disclosure`** — seeded canary keys appear in no argv capture, stdout,
   stderr, config, manifest, host file, tier file, exception, transcript or telemetry;
   the MCP auth header is present only at the transport seam.
5. **`test_host_assimilation_is_bounded_and_advisory`** — generated host stanza matches the
   frozen compact text, contains no phase walk/refusal/mandatory-search language, and its
   actual tokenizer count is ≤250. The on-demand onboarding resource is ≤750 tokens.
6. **`test_host_can_complete_without_cortex_calls`** — a deterministic fake-host task can
   read/write/test/finish with zero Cortex calls and zero warnings/refusals. This proves
   Cortex absence is not a process gate.
7. **`test_relevant_search_happy_path`** — after private/metrics policy is server-bound,
   status then one search returns typed `results`; no extra receipt/advance/closeout call
   is required and any receipt is a response side effect.
8. **`test_consent_off_means_zero_network`** — init and all wrapper commands except explicit
   consent change/doctor-local execute under a socket-deny fixture; no logs are created.
9. **`test_consent_private_truly_no_logs`** — the fake server rejects use unless it can bind
   `private`; after status/search, its query log, access-content log, mirror queue, trace
   store and tenant metric store remain byte-empty. Only a fixed aggregate process-health
   counter may change if the published contract permits it.
10. **`test_consent_private_attestation_failure_degrades_offline`** — if server version or
    policy attestation cannot guarantee private mode, no query body is transmitted and
    the client returns typed offline/privacy-unavailable status.
11. **`test_consent_metrics_exact_allowlist_revocation_and_purge`** — only frozen allowed
    metric fields are emitted; canary query/file/output strings never appear; revoke
    prevents the next export; purge by consent/tenant ID removes all indexed copies and
    returns a verifiable receipt.
12. **`test_offline_L0_degradation`** — DNS failure, timeout, 401, 403, 429 and malformed MCP
    responses each produce one short typed diagnostic, no retry loop, and leave local
    scaffold/native tools usable. Result explicitly says `no_corpus`, not “no coverage.”
13. **`test_zero_process_coercion_and_refusal_gates`** — across missing-search, missing-spec,
    missing-test, stale-doc and missing-closeout fixtures, every native tool action remains
    allowed; process refusal count, loop count and protocol-only turns are all zero. Safety
    fixtures remain separately allowed to fail closed.
14. **`test_no_foreground_governance_ritual`** — completion performs zero foreground
    closeout calls, zero mandatory phase transitions and at most one lazy discovery call;
    a local post-hoc scorer/scribe, if enabled, cannot add a host turn. This reflects the
    corpus removal of in-band closeout ceremony (`docs/design/cortex-redesign-CORRECTED-spec.md:16-28`).
15. **`test_bounded_resting_and_peak_context`** — measure with each supported host/model
    tokenizer: wrapper-added resting context ≤1,000 tokens, host stanza ≤250, default
    retrieved scope ≤4,000, no checked-in model catalog is injected, and peak wrapper
    contribution is reported rather than compared to the unverified folklore 50k.
16. **`test_bounded_router_probe`** — default init makes zero inference probes; explicit
    selection probes no more than the declared cap, once per model, with fixed timeout and
    concurrency. Unknown/failed models are reported, not retried recursively. Manual
    allow/tier choices survive refresh.
17. **`test_manifest_signature_and_tamper_detection`** — valid release verifies before
    mutation; changing a managed payload fails with zero writes. An unsigned development
    mode requires an explicit flag and visibly records that fact.
18. **`test_schema_and_version_compatibility`** — old supported config migrates atomically;
    unknown future major versions refuse only wrapper configuration/remote connection,
    never native work, and preserve the original file.
19. **`test_claims_match_validation_record`** — README/VALIDATION claims are generated or
    linted against frozen test-result identifiers; aspirational capabilities cannot be
    labeled measured. The corrected design requires measured deltas before shipping
    (`docs/design/cortex-redesign-CORRECTED-spec.md:11,55-58`).

Release is all-or-nothing for tests 1–18. Test 19 prevents a passing mechanism test from
becoming an inflated efficacy claim.

## 4. What the reactive delivery gets wrong, and the minimal replacement

The current work contains several good local mechanisms—read-scoped MCP, stdlib init,
managed markers, explicit offline claims, deterministic scribe/scorer language and repeated
anti-coercion warnings. The problem is that successive needs were each bolted on as another
document/script/rule: host onboarding, then model gateway, then probe/table, then
assimilation, then scribe metrics, then compliance. Its own git log records exactly that
sequence (`../cortex-agent-wrapper/.git/logs/HEAD:1-6`). That is provenance of reactive
construction, not proof the resulting contract composes.

Specific failures:

- The wrapper calls itself “zero install,” yet onboarding requires cloning a special work
  repo, editing `.env`, probing a router, reading a tier table, connecting MCP, running init
  and moving work under a new hierarchy (`../cortex-agent-wrapper/README.md:32-54`). This is
  ceremony disguised as assimilation.
- It installs behavior in three root host files and makes an unconditional first-search
  instruction (`../cortex-agent-wrapper/AGENTS.md:7-28`), recreating protocol salience for
  exactly the weak models known to confuse protocol with task.
- The 33KB generated router catalog is a context-bloat asset, and probing all advertised
  models is unbounded relative to the user's actual candidates.
- Its opt-out is a promise/request, not an enforced transport/server property. This is the
  highest-severity defect because README says local opt-out while queries still reach an
  owner-logging server (`../cortex-agent-wrapper/README.md:98-101` and
  `../cortex-agent-wrapper/DATA-USE.md:15-38`).
- It duplicates changing server workflow facts into local prose and explicitly demands
  manual synchronization, guaranteeing drift.
- It describes “no coercion” metrics as hard gates (`../cortex-agent-wrapper/README.md:62-69`),
  an ambiguous phrase that risks turning evaluation guardrails into runtime refusal gates.
  They must be release-test invariants only.

**Minimal principled replacement:** keep the thin read-only MCP connection, retain a
trimmed stdlib local L0, and refactor `init.py` into the single `cortex init` contract.
Delete checked-in generated model rows; make 9router optional and bounded. Replace the
large host files with one detected-host managed stanza. Move changing onboarding/state to
an on-demand MCP resource. Add signed ownership manifest, doctor/uninstall, and—before any
collaborator query—server-enforced `off/private/metrics` consent semantics. Freeze the tests
above before implementation. Do not add a devcontainer, new state engine, plan-mode gate,
mandatory contract, or per-task consent/closeout prompt.

That is the smallest delivery that preserves what Cortex learned without making Cortex
the collaborator's work.
