# Cortex Redesign — CORRECTED Spec (disclosure + detection, not coercion)

> **Supersedes the coercive parts of `cortex-redesign-reconciled-spec.md`.**
> Two independent past-learning reviews — `reviewed/cortex-redesign-vs-past-learning-fable.md`
> and `reviewed/cortex-redesign-vs-past-learning-codex.md` — CONVERGED (blind to
> each other) on the same reversal: the earlier gates rebuilt our own diagnosed
> **Disease B (mandatory-pipeline coercion)** with better locks. Grounded in
> `docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md`,
> `docs/research/SELF-LEARNING-FLYWHEEL-PROTOCOL-2026-07-07.md`,
> `docs/research/TRUNCATION-CONTEXT-MANAGEMENT-DEEP-RESEARCH-2026-07-08.md`.
> Reconciled 2026-07-13. **Nothing ships without a measured A/B/C delta.**

## Thesis (both reviewers, one line)
**Make research + docs the *easiest* path, record them automatically, detect skips deterministically *afterward*, and refuse only on narrow safety failures — never on process compliance.** This resolves the owner's two truths at once: *agents skip research/docs* AND *governance became bigger than the work.*

## KILL (these are Disease B / the ritual in disguise — both reviewers agree)
- **Receipt-*gated* SEARCH_BRAIN transition.** Keep server-minted receipts (unfakeable); **remove the refusal placement** — refusal `how_to_comply` text is itself a measured context-bloat/tool-loop contributor (ARCH-DEBUG §3/§42).
- **DOC_SYNC / DOC_UPDATE as a refusal state** with `rework_to` loop + PreToolUse denials — the strongest Disease-B variant yet. The `docs.map.yaml` is valuable **as a post-hoc detector, not a wall.**
- **Per-task in-band closeout ceremony.** Keep the *atomic server-owned write* (fixes a real state/filesystem inconsistency); **remove the ritual** — a transcript-driven scribe writes the audit *after* the work (the flywheel's "observe immediately, batch-decide later").
- **Re-armed strict refusal profiles** (`CORTEX_STRICT_OVERRIDES`, forced-pipeline for Hermes). Keep `run_id`/receipts/provenance as *records checked after the run*; **do not enable strict refusal before the C-arm proves it helps** — re-arming gates whose own comments say they "manufacture tool-call loops," on the weak models that "treat protocol as the task," is the exact mistake.

## THE REPLACEMENT — disclosure + detection (both reviewers, identical)
1. **Progressive disclosure.** State machine surfaces a tiny name-index + the phase's curated tools; research is the *cheapest* action. (`phase_legal_tools`, `state_engine.py:384` — already "the disclosure controller.") **Near-zero resting surface**: name-index (~250 tok) → schema on demand → skill body per phase. **Stop loading duplicate Cortex surfaces.**
2. **Passive receipts.** `cortex_search` / scope-packs / deep-research mint server-side, digest-bound receipts as a *side effect* — research is witnessed at **zero extra tool calls**, never refused.
3. **Native work proceeds** with no per-tool denials.
4. **Cheap deterministic post-hoc scorer** turns skipping into a visible **SLI**: first-search/receipt vs first-mutation timestamp; `docs.map.yaml` ∩ actual diff; test/verify evidence; run-bound closeout digest; refusal/loop/protocol-only-turn/context-token counts.
5. **Transcript-fed subagent scribe** reads transcript + receipt store + git + scorer → writes a detailed **atomic** closeout asynchronously. (OTel/Langfuse as durable source.)
6. **Standing batch digest** consumes violations/failure-clusters. **One** bounded Stop-warning is tested *only if* measured skip rates stay high.

### Over-correction guard (Fable — decided now)
Killing the ritual too hard = skips return *silently* (detection decays into WARN fatigue, FLYWHEEL §3.3). Counter: the skip-rate SLI gets a **named standing consumer** (the count-triggered meso-loop digest) + a pre-registered deterministic trigger that arms the bounded Stop-check — **the system tightens by policy, not by a future agent's memory.**

## Two accuracy corrections (Codex caught these — fix before claiming them)
- **`.mcp.json` loads `cortex-brain` + `claude-bias` — only ONE is a Cortex server.** The "two cortex servers = the 50k" claim needs a live `list_tools` probe. Policy stands (never load duplicate Cortex surfaces), but state it accurately.
- **"No external dependencies" is NOT literally true today:** `research.py` imports **PyYAML + Anthropic at module load**. The scaffold must ship a **stdlib-native corpus-first path**; model framing/summarization + Grokto stay **optional**.

## groktocrawl / deep research
`deep_research.py` is structurally ideal (async handoff, heartbeat, persistent records, corpus-first, bounded fetch, cite-check, graceful fail). Fold in as **pure disclosure**. **GroktoCrawl = an optional HTTP adapter, never vendored or default-loaded**; keep the native backend (`/llms.txt`, Markdown negotiation, bounded fetch, corpus indexing, no new service); allowlist Grokto's separate SSRF boundary. *Evidence it's needed:* the Hermes transcript shows deep-research started, polled twice, abandoned, replaced by manual search — the process record lived *only* in the transcript.

## Oracle policy (deterministic per axis — NO judge in any ship verdict)
- task success → subprocess/behavioral checks; research → timestamp/order + citation-path; docs → diff/set; audit → schema + event-digest; context → tokenizer measurement. Judges may *annotate later*, never establish ground truth (per the objective hard-gold lab).

## OTel + Langfuse (fail-open viewers, never enforce)
Local flight recorder (`telemetry.py`) = source of truth. OTel captures durable ordering/model-identity/latency/tokens/tool-events; Langfuse = dashboards for skip-rate/refusal-loops/cost/per-model outcomes. **Remote sinks are fail-open aggregators — they enforce nothing.**

## Data governance — consenting collaborator telemetry (both reviewers)
- **USE for:** lane design, failure fingerprinting/mining, capacity/latency/routing/tool-surface analysis, *candidate* objective-test generation.
- **NEVER for ground-truth labels.** A candidate failure is *replayed against a deterministic checker*; if none can decide → `UNVERIFIABLE`/quarantined.
- **Anti-distillation:** raw prompts/responses/**proprietary-model outputs**/file-contents/secrets/CoT never enter the trainable corpus. *Even a deterministic pass does not make proprietary output trainable* — quarantined analysis store only, with provenance + exclusion flags, in gitignored `ops-local/`.
- **Consent envelope:** `consent_id`, scope, provenance, deployment-plane, model/vendor identity, run/session pseudonyms, corpus/code version, capture time, redaction status, retention, trust tier. Opt-in, revocable, time-bounded, HMAC-keyed purge path, auditable consent event, fail-closed scrub at ingest. Default egress = **structured metrics only.**

## What actually needs building (adjacent plumbing — NOT engine surgery)
name-index/schema lazy loader · deterministic curated preload for weak models · turn-boundary disclosure (`list_changed` across-turns-only) · passive receipt minting (new receipt kind) · transcript/git/OTel scorer · async scribe · one-server/default-surface cleanup. The kernel already routes disclosure (`phase_legal_tools`) and already models the deep-research track (`state_engine.py:140`).

## A/B/C — pre-registered, deterministic metrics only, ship on measured delta
- **A** = vanilla (no scaffold/enforcement) · **B** = detection-only (disclosure + passive receipts + near-zero surface + transcript audit + **zero refusals**) · **C** = coercive (the old receipt/DOC_SYNC/run-scoped-refusal design).
- Identical tasks/models/fresh-worktrees/frozen preregistration. Measure: task success, research ordering, docs correctness, audit validity, tokens, wall-clock, tool-calls, resting/peak context, refusal count, loop count, protocol-only turns, cost.
- **Ship B** if it improves discipline without harming task-success/cost. **C admissible only** if it beats B on discipline with no meaningful success/cost regression. **If B works, C is rejected. If B fails, first test one bounded Stop-check — never jump straight to refusal gates.**
