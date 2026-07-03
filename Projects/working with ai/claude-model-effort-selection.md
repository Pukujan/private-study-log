# Claude Task Pipeline — Model, Effort, Research, and the Cortex Build Loop

> Companion to the OpenAI GPT-5.4-mini / GPT-5.5 reasoning-effort playbook
> already in use for Codex (model split, standard loop, handoff/exit
> criteria — sourced from OpenAI's "Using GPT-5.5," "Reasoning models," and
> "Model selection" docs). This is the Claude-native counterpart, sourced
> from Anthropic's current API/SDK reference (model catalog cached
> 2026-06-24) and web-verified where the skill reference didn't cover it —
> not inferred by analogy. Two things diverge enough from the OpenAI shape
> to call out explicitly: **not every Claude tier has an effort dial**, and
> **Claude has no single "Research mode" toggle**. Two model-tier ladders
> below: **before** and **after** 2026-07-07, since Fable 5 drops out of
> the affordable rotation on that date.

## 1. Two independent knobs

- **Model tier** — which Claude model (Haiku / Sonnet / Opus / Fable). Sets the
  capability ceiling, price, and context window.
- **Effort** (`output_config.effort`, GA, no beta header) — how hard *that*
  model thinks on a given request: `low | medium | high | xhigh | max`.

Tier answers "how smart is the model I'm calling"; effort answers "how hard
does it try this time." They're set independently, and — the first place
Claude's mechanics diverge from a single reasoning_effort dial — **not every
tier has the effort knob**.

## 2. Model tiers

| Model | ID | Input / Output per MTok | Context | Max output | Effort support | Thinking default |
|---|---|---|---|---|---|---|
| Haiku 4.5 | `claude-haiku-4-5` | $1 / $5 | 200K | 64K | **None — errors if set** | No adaptive/extended thinking |
| Sonnet 5 | `claude-sonnet-5` | $3 / $15 (intro $2/$10 through 2026-08-31) | 1M | 128K | low→max (first Sonnet tier with `xhigh`) | **Adaptive ON by default** (omitting `thinking` still runs adaptive) |
| Opus 4.8 | `claude-opus-4-8` | $5 / $25 | 1M | 128K | low→max | **OFF unless set explicitly** (omitting `thinking` = no thinking) |
| Opus 4.7 | `claude-opus-4-7` | $5 / $25 | 1M | 128K | low→max | OFF unless set explicitly |
| Fable 5 | `claude-fable-5` | $10 / $50 | 1M | 128K | low→max (depth control only) | **Always on — cannot be disabled** (`{type:"disabled"}` 400s) |

Positioning, as documented: Haiku 4.5 is "fastest and most cost-effective… for
simple tasks." Sonnet 5 is "the best combination of speed and intelligence in
the Sonnet tier; near-Opus quality on coding and agentic work." Opus 4.8 is
"the most capable Opus-tier model — highly autonomous, state-of-the-art on
long-horizon agentic work, knowledge work, and memory." Fable 5 is
"Anthropic's most capable widely released model, for the most demanding
reasoning and long-horizon agentic work" — and it also carries a hard
30-day-data-retention requirement (unavailable under zero data retention) and
never returns raw chain-of-thought (only `display: "summarized"` or
`"omitted"` — the model can't be prompted around this).

**Divergence from OpenAI's story:** GPT-5.4-mini's reasoning-effort levels
apply within one small/cheap model. On Claude, the cheap tier (Haiku 4.5) has
*no* effort dial at all — cost/quality control there is model choice, not a
knob within the model. The dial only exists from Sonnet 5 up.

## 3. Effort levels (Sonnet 5 / Opus 4.7+ / Fable 5)

Sourced verbatim from Anthropic's model-migration guidance for Sonnet 5 and
Opus 4.7 (the two tiers with the most detailed documented guidance):

| Level | When | Notes |
|---|---|---|
| `max` | Tasks needing the absolute ceiling, no token constraint | Diminishing returns in some cases; can overthink — test before committing |
| `xhigh` | The hardest coding and agentic use cases | Recommended setting for those; **this is Claude Code's own default** |
| `high` | **Default when `effort` is omitted.** Most use cases. | Balances token usage and intelligence; recommended *minimum* for intelligence-sensitive work |
| `medium` | Cost-saving step-down from default | On Sonnet 5, roughly comparable to Sonnet 4.6 at `high` |
| `low` | Short, scoped, latency-sensitive, non-intelligence-sensitive tasks | Chat, simple lookups, crisp mechanical edits |

Documented behavior at the low end, worth internalizing: *"Sonnet 5 respects
effort levels strictly, especially at the low end. At `low` and `medium` it
scopes work to what was asked rather than going above and beyond… on
moderately complex tasks at `low` there is some risk of under-thinking. If
you observe shallow reasoning on complex problems, raise effort to `high` or
`xhigh` rather than prompting around it."* This is the same shape as
GPT-5.4-mini's caution that higher effort isn't automatically better — both
directions (under-thinking at `low`, overthinking at `max`) are real and
documented; the fix in both cases is to raise/lower the number, not to fight
it with prompt engineering.

## 4. Rule of thumb (mirrors the Codex/GPT-5.4-mini framing 1:1)

| Task shape | Model | Effort |
|---|---|---|
| Crisp, mechanical, low-ambiguity handoff (rename, format, apply an already-decided diff, simple classification/lookup) | Haiku 4.5 | n/a — no dial |
| Real implementation work with a few moving parts (the bulk of coding/agent tasks) | Sonnet 5 | `medium`→`high`; start at `high` until measured, step down once evidence holds |
| Hard, tangled cross-file debugging; architecture/code review; second-opinion / adversarial checking | Opus 4.8 | `high`/`xhigh` |
| The genuinely hardest, longest-horizon reasoning (only while affordable) | Fable 5 | `xhigh`/`max`, and only when Opus 4.8 at its ceiling isn't enough |

## 5. Before 2026-07-07 (Fable 5 available) — full ladder

| Tier | Model | Effort | Why |
|---|---|---|---|
| Mechanical | Haiku 4.5 | — | Cheapest/fastest; no reasoning depth to tune |
| Implementation (default workhorse) | Sonnet 5 | `high` (default) → `medium` once verified | "Near-Opus quality… at Sonnet cost" |
| Deep review / tangled debugging | Opus 4.8 | `high`/`xhigh` | State-of-the-art long-horizon agentic + code review; `xhigh` is Claude Code's own default |
| Hardest reasoning, reserve for real stuck points | Fable 5 | `xhigh`/`max` (thinking always on regardless) | $10/$50 per MTok — most expensive tier by a wide margin; use deliberately, not by default |

## 6. After 2026-07-07 (Fable 5 dropped for cost) — budget ladder

| Tier | Model | Effort | Why |
|---|---|---|---|
| Mechanical | Haiku 4.5 | — | Unchanged |
| Implementation | Sonnet 5 | `medium`→`high` | Unchanged |
| Everything that would have gone to Fable 5 | Opus 4.8 | `high`/`xhigh`/`max` | Opus 4.8 is the harness's own hard default: *"ALWAYS use `claude-opus-4-8` unless the user explicitly names a different model… Never downgrade for cost — that's the user's decision, not yours."* Routing the hardest work to Opus 4.8 at its ceiling effort isn't a degraded fallback — it's the documented, always-available capability ceiling once Fable 5 is off the table. |

Re-verify this table periodically — it's a living document by design (same
convention as the other files in this directory) and Anthropic's tier
lineup and pricing shift over time; the current cache date is noted above.

## 7. Cost-mixing without losing implementation quality

This is the same design already specced in this repo's own planning docs,
now given concrete model picks:

- Let Haiku 4.5 absorb the mechanical majority of a task queue (boilerplate,
  crisp diffs, classification) and escalate only the genuinely hard slice to
  Sonnet 5 or Opus 4.8 — this is the fast-executor/escalate-on-difficulty
  pattern from `docs/BUILD-PLAN.md` §9 (the maintenance fleet) and the whole
  premise of §7 (the Haiku-parity program: strong models compile
  patterns/skills, cheap models execute against them, with escalation as the
  guardrail against silent quality loss).
- This table is a **starting prior**, not a permanent policy. `docs/BUILD-PLAN.md`
  §1 already specs a `model_scorecards` schema — per model × task_type,
  verified success rate (not self-reported) via logic checks and OTel spans.
  Once that exists, routing decisions should come from measured
  `verified_success_rate` and `self_report_vs_verified_gap` per task type,
  not from this generic table. Treat this document as what to route on
  *before* that telemetry exists.
- The false-economy trap runs in both directions: dropping effort too low on
  real work causes silent under-thinking (not an error — you have to notice
  it), while over-provisioning (Fable 5 or `max` effort on mechanical work)
  just burns the $10/$50 or $5/$25 rate for no measured gain. Both directions
  are named directly in Anthropic's own docs — tune by measuring, not by
  guessing in either direction.

## 8. Claude's Research mode — corrected after verification

**Correction:** an earlier draft of this section claimed no dedicated
"Research mode" exists on Claude. That was wrong — checked more narrowly
after a direct report of a 420-source Fable 5 research output, and a
distinct, named feature is confirmed. Leaving this note rather than quietly
fixing it, since a corpus that silently overwrites its own wrong claims is
worse than one that shows the correction.

**What it actually is, at three layers:**

- **Product layer — claude.ai's "Research" feature.** Activated via the
  `+` button → "Research" in the chat UI (web/desktop/mobile); a blue
  indicator marks it active. Distinct from a normal chat turn: it instructs
  Claude to browse multiple sources and compile a cited report rather than
  answer from training data, can run **up to 45 minutes**, and
  auto-enables extended thinking so the model plans its search strategy
  before executing rather than guessing. Beta rollout was Max/Team/Enterprise
  first, Pro later — check current availability if it matters for your plan.
- **Architecture layer — orchestrator + parallel subagents.** Per
  Anthropic's own engineering writeup ("How we built our multi-agent
  research system" — the same paper already cited in `docs/ROADMAP.md` §9
  for the +90.2%-over-single-agent result): a **lead agent** analyzes the
  query, develops a strategy, and spawns **parallel subagents** to explore
  different aspects simultaneously. Each subagent is an intelligent filter —
  it iteratively searches and returns findings to the lead agent, which
  compiles the final answer. *"Complex research tasks might use more than
  10 subagents."* Ten-plus subagents each running several searches is
  exactly how a single research task accumulates hundreds of individual
  source citations — the 420-source report is consistent with this
  architecture, not an outlier.
- **API/SDK layer — no single parameter for any of this.** There is no
  `mode: "research"` field on the Messages API. The product feature above
  is Anthropic's *own* application of two things any caller can compose:
  the agentic `web_search` server tool (progressive multi-query search with
  citations, controlled via `max_uses`/domain lists) run inside an
  orchestrator-plus-parallel-subagents pattern.

**What this means for Cortex specifically:** the "Deep Research → reason
over it → implement" pattern Codex's playbook describes has two honest
paths here, not one:

1. **Use the product feature directly** when a human is driving a claude.ai
   session — genuinely the closest thing to Codex's "Deep Research" step,
   confirmed real and citation-heavy.
2. **Compose it yourself** for the MCP/API-driven path Cortex actually runs
   (no claude.ai chat surface in the loop). This repo already has the
   pieces: `cortex_search` first (cheap, already-vetted — the mandated
   first step in `AGENTS.md`), `web_search` only on a documented miss
   (today a manual `curl` per `.hermes.md`; `docs/BUILD-PLAN.md` Phase 3's
   MCP tool surface is where it becomes a real tool), and — for genuinely
   broad research tasks — the same orchestrator/parallel-subagent shape via
   the `Agent` tool (spawn several research-focused subagents in parallel,
   each covering one facet, synthesized by the calling agent). That's not
   hypothetical: it's the same mechanism used earlier this session to plan
   the Opus dogfood-review pass. `docs/BUILD-PLAN.md` §9's
   one-write-path-per-artifact rule still applies — parallel subagents for
   *reading/researching*, one owner for the resulting write.
3. Synthesize the gathered evidence at `high`/`xhigh` effort on the
   planning tier (Opus 4.8, or Fable 5 pre-2026-07-07).
4. Hand the synthesis to the implementation tier (Sonnet 5 medium/high, or
   Haiku 4.5 for the mechanical slice) as a contract — see §11 below.

## 9. Full pipeline — Cortex-native translation of the Codex playbook

Codex's "Standard Loop" (Plan → Review → Smoke Test → Gap Finding → TDD
Handoff → Implement → Verify) already has a near-exact structural match in
this repo — the `cortex-build-pipeline` skill's four phases (Plan →
Implementation TDD RED→GREEN→REFACTOR → Review → Close) plus
`docs/PHASE-GATES.md`'s evidence-gate discipline. Rather than building a
parallel structure, map onto what's already here:

| Codex stage | Cortex/Claude equivalent | Owner tier |
|---|---|---|
| *(Deep Research insertion point)* | `cortex_search` → `web_search` on documented miss (§8) | Same tier as Plan, run before/interleaved with it |
| Plan | `cortex-skill` preflight (search audit logs/docs per `AGENTS.md`'s order) + `cortex-build-pipeline` Phase 0 | Opus 4.8/Fable 5 at `high`/`xhigh` for real plans; Sonnet 5 `high` for small ones |
| Review | Compare the approach contract (`docs/BUILD-PLAN.md` Phase 4) against live code; same content as Codex's "Review" | Same tier as Plan |
| Smoke Test | `docs/PHASE-GATES.md` discipline: run the smallest realistic check, record the exact failure (not just the symptom) | Any tier — the record becomes the contract's evidence |
| Gap Finding | `cortex-doctor` + `cortex_search` + (once built) the KEDB pattern library (`docs/BUILD-PLAN.md` Phase 5): current behavior vs. target, missing tests, path drift, secret-handling gaps | Opus 4.8/Sonnet 5 at `high` |
| TDD Handoff | The Phase 4 **approach contract** — already spec'd, see §11 mapping below | Written by the planning tier |
| Implement | `cortex-build-pipeline` Phase 1 (RED → GREEN → REFACTOR), checkpointed | Sonnet 5 `medium`/`high`, or Haiku 4.5 for the smallest mechanical patch |
| Verify | `docs/PHASE-GATES.md` gate evidence (cited, never asserted) + re-run the exact failing tests, then nearby tests + `cortex-write-log` closeout | Implementation tier for the mechanical re-run; planning tier for a final review pass if shared state/paths are touched |

## 10. Who gets what

| Owner | Gets |
|---|---|
| Opus 4.8 (or Fable 5 pre-2026-07-07) at `high`/`xhigh` | Architecture review, gap analysis, tangled cross-file debugging, final review of anything touching shared state/paths — same list as Codex's `xhigh` |
| Sonnet 5 at `medium`/`high` | Executing the approach-contract handoff, concrete file edits, targeted test runs, small follow-up fixes from review — Codex's "5.4 mini" role |
| Haiku 4.5 | The smallest mechanical slice within that: doc/config tweaks, narrow renames — Claude's cheapest tier is a genuinely separate model (no effort dial), unlike GPT-5.4-mini's `low` setting within one model |

## 11. Good handoff contents — already spec'd as the Phase 4 contract

Codex's handoff fields map almost field-for-field onto the approach-contract
schema already written in `docs/BUILD-PLAN.md` Phase 4:

| Codex field | Cortex Phase 4 contract field |
|---|---|
| Goal | `task_type` + planned approach |
| Current behavior / Desired behavior | `evidence_refs` (what was consulted) + planned approach |
| Canonical paths | *(not yet explicit — worth adding verbatim, see below)* |
| Known failure modes | The Phase 4.5 false-positive checklist |
| Files to change / Tests to add first / Commands to run | `verification_steps` |
| Definition of done | `acceptance_criteria` |

**One concrete improvement to backport:** Codex's schema names "Canonical
paths" and "Commands to run" as their own explicit fields rather than
folding them into `verification_steps`. That's a small, free refinement
worth adopting when Phase 4 actually gets implemented — path drift and
missing/wrong commands are exactly the kind of thing a vague field lets
slip through.

## 12. Good exit criteria — already spec'd, one gap to note

Codex: tests fail then pass; no secrets printed or merged; canonical path
used everywhere; old roots preserved as evidence unless explicitly archived;
final review confirms no new drift.

Cortex equivalent, already written down:
- Tests red→green, evidence cited not asserted → `docs/PHASE-GATES.md`
  Phase 4 gate ("100% of closeouts carry machine evidence… zero
  scribe-asserted success").
- Old roots preserved as evidence unless archived → `docs/ROADMAP.md` §3
  principle 5 ("invalidate, don't delete" — supersede semantics on
  `accepted/`, `deprecated/` for retired docs).
- No secrets printed or merged → not yet an explicit gate anywhere in this
  repo's docs. Worth adding to Phase 4/6 as its own checklist item rather
  than assuming it falls out of the others — it doesn't.

## 13. Practical rule

- Task is still fuzzy → Opus 4.8 (or Fable 5, pre-2026-07-07) at `xhigh`
  turns it into a crisp handoff/contract.
- Task is already crisp → Sonnet 5 at `medium`/`high` (or Haiku 4.5 for the
  smallest slice) executes it.
- Task is risky or touches shared state → end with an Opus 4.8 `xhigh`
  review pass before closeout.
- Task needs a lot of external evidence before coding → corpus search
  first, `web_search` only on a documented miss, synthesize at
  `high`/`xhigh`, then hand off. There's no separate "Research mode" to
  reach for — this composition **is** Claude's research step.

## Sources

Anthropic's current Claude API/SDK skill reference bundled in this
environment (model catalog cached 2026-06-24): `shared/models.md`
(model catalog, positioning, pricing), `shared/model-migration.md`
§"Choosing an effort level on Opus 4.7" and §"Choosing an effort level on
Claude Sonnet 5" (the two sourced effort tables above), and the top-level
skill's §"Thinking & Effort (Quick Reference)" (per-tier thinking defaults,
effort GA status, `xhigh` availability). Web-verified for §8 (no cached
skill coverage of this, and the first verification pass was incomplete —
see the correction note in §8 itself): [Use research on Claude — Claude Help Center](https://support.claude.com/en/articles/11088861-use-research-on-claude),
[How we built our multi-agent research system — Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system),
[Claude's AI research mode now runs for up to 45 minutes before delivering reports](https://aicommission.org/2025/05/claudes-ai-research-mode-now-runs-for-up-to-45-minutes-before-delivering-reports/),
[Web search tool — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool),
[Introducing web search on the Anthropic API](https://claude.com/blog/web-search-api).
For anything time-sensitive, treat `platform.claude.com` as the live source
of truth over this cached snapshot.
