# cortex-local workspace manifest

Machine source of truth: `manifest.json` (this file is the human-readable view of it — if they
disagree, `manifest.json` wins). Naming contract for new output and the retention policy are in
`FILE-MANAGEMENT-PROPOSAL-2026-07-07.md` §4-5 (still the governing doc; this manifest implements it).

**Any agent working in this workspace should read `AGENTS.md` first** — it's the short,
always-read-this pointer to the naming contract below. Added 2026-07-07 after Hermes wrote 9
scrape/report files straight to root during a Discord-scraping task, twice, because nothing told
it not to.

## Root — workspace runtime (`do_not_move: true`, path-resolved, never touch)

| Path | Purpose |
|---|---|
| `audit/`, `contracts/`, `logs/`, `library/` | Cortex/Hermes workspace runtime — path-resolved by `CORTEX_WORKSPACE` |
| `docs/`, `corpus/`, `reports/` | Ambiguous provenance — left alone, unconfirmed |
| `research/sources.yaml`, `research/run_deep_research.py`, `research/deep_research_error.txt`, `research/deep_research_result.json`, `research/tasks/` | Pre-existing tooling/output, predates this session (2026-07-06) — left in place |

## Root — session output, currently LIVE (do not move, real running dependents)

| Path | Why it's pinned |
|---|---|
| `browser_extension/` | In active use by Hermes right now |

## Moved into `work/` today (2026-07-07)

| From (old root path) | To |
|---|---|
| `EXTENSION_STATUS/` | `work/status/browser-extension/` |
| `audit-reads/` | `work/audits/2026-07-06-ssc-audit-reads/` |
| `repo-changes/` | `work/audits/2026-07-06-owner-route-changes/` |
| `research/{adopt_dont_build_ui, brand_and_ux_research, deep_research_state_machine, logging_modularization, multimodel_research_fanout, self_improvement_protocols}/` | `work/research/2026-07-07-multimodel-fanout/` |
| `discord_*.{png,txt,md}` (9 files, written to root by Hermes) | `work/audits/2026-07-07-discord-scrape/` |
| `community_projects_report.md` (written to root by Hermes) | `work/audits/2026-07-07-discord-scrape/` |
| `benchmark_runs/` | `work/benchmarks/2026-07-07-dashboard-mcp/runs/` |
| `CONSOLIDATED_REPORT/` | `work/benchmarks/2026-07-07-dashboard-mcp/consolidated/` |
| `dashboard_A_no_mcp/` | `work/benchmarks/2026-07-07-dashboard-mcp/dashboards/A_no_mcp/` |
| `dashboard_B_with_mcp/` | `work/benchmarks/2026-07-07-dashboard-mcp/dashboards/B_with_mcp/` |
| `dashboard_C_opus_choice/` | `work/benchmarks/2026-07-07-dashboard-mcp/dashboards/C_opus_choice/` |
| `dashboard_D_legora_brand/` | `work/benchmarks/2026-07-07-dashboard-mcp/dashboards/D_legora_brand/` |

The 4 dashboard dev servers (A:5173, B:5174, C:5182, D:5190) were stopped cleanly, the 6 items
above moved, each dashboard's server-side `CORPUS_ROOT`/`REPORT_DIR` path resolution updated for
the new depth (`dataLoader.js`/`corpusApi.js`/`cortexApi.js`), and all 4 servers relaunched from
their new locations on their original ports — verified live via `/api/tasks` or `/api/tree`
returning real consolidated-report data at the new `corpusRoot`/`root` path. The two consumers
outside this workspace (`D:\claude\stupidly-simple-cortex\ops\_consolidate_benchmark_report.mjs`,
`ops\qwen_benchmark_runner.py`) were updated to the new `RUNS_DIR`/`OUT_DIR`/`BENCH_ROOT` paths
in the same change.

## Moved in FROM another repo (2026-07-07)

| From (source repo) | To |
|---|---|
| `D:\claude\stupidly-simple-cortex\docs\research\{technique-virtualized-scroll-cdp-2026-07-07.md, technique-cdp-refuses-real-chrome-profile-2026-07-07.md, COMMUNITY-TOOLS-SURVEY-2026-07-07.md}` | `work/research/2026-07-07-server-corpus-cleanup/` |

Cortex's server repo (`stupidly-simple-cortex`) reindexes everything under its own `docs/` into
the served corpus every `cortex-search --index` run, so anything written there permanently grows
what every future `cortex-search` query has to rank against. These 3 docs were grep-verified
(against `cortex_core/`, `tests/`, `docs/`, `docs/ontology/entities.jsonl`) to have **zero code
references and zero incoming doc cross-links** — pure session narrative (two n=1 browser-CDP
technique notes below the KEDB promotion floor) or a community-research dump with no other doc
pointing at it. Moved here so they stay fetchable/searchable from this workspace without bloating
the primary served corpus. Full rationale + everything that was checked-and-kept instead:
`D:\claude\stupidly-simple-cortex\docs\research\SERVER-CORPUS-CLEANUP-2026-07-07.md`.

## Moved in FROM another repo (2026-07-07, round 2)

| From (source repo) | To |
|---|---|
| `D:\claude\stupidly-simple-cortex\docs\research\{DASHBOARD-D-PROJECT-CONTRACT-2026-07-07.md, ORCHESTRATION-VS-CHOREOGRAPHY-AND-STATE-MACHINE-VS-AUDIT-2026-07-07.md, auto-research__what-is-model2vec-and-why-was-it-chosen-for-cortex-retrieval.md, auto-research__what-is-the-rebuild-lock-and-how-does-it-prevent-index-corru.md}` | `work/research/2026-07-07-server-corpus-cleanup-round2/` |
| `D:\claude\stupidly-simple-cortex\ops\{opencode_cna_outreach.py, qwen_benchmark_wave3_tasks.py, qwen_dashboard_retest.py, qwen_pipeline_check.py, _consolidate_benchmark_report.mjs}` | `work/benchmarks/2026-07-07-dashboard-mcp/ops-scripts/` |

Round 2 of the same server-corpus cleanup, re-run because the first pass's cutoff was its own
write time (17:28) and the session kept writing to `docs/research/` and `ops/` for another two
hours. Same grep-verified zero-references test as round 1. The 4 docs: one documents an *external*
Hermes-workspace dashboard build (not this repo's own code), one is explicitly "research only, no
code changes" with zero incoming cross-links, and two are raw no-LLM auto-research dumps (excluded
from round 1 as too-fresh, now stale enough to evaluate). The 5 ops/ scripts are one-off
qwen/opencode benchmark and report-consolidation tooling for tonight's dashboard benchmark run,
colocated with the benchmark data they process rather than dumped in `research/`. **Two ops/
scripts were deliberately left in place**: `qwen_benchmark_round2.py` (its launched process was
confirmed still running via `Get-CimInstance Win32_Process` at audit time) and
`qwen_benchmark_runner.py` (a live import of the running script, plus code-referenced by filename
in `cortex_core/mcp.py:56` and `tests/test_mcp_server.py:898`) — see `deferred_moves` in
`manifest.json`. Full rationale:
`D:\claude\stupidly-simple-cortex\docs\research\SERVER-CORPUS-CLEANUP-ROUND2-2026-07-07.md`.

## Idle, not yet archived

- `builds/research_track_feature/` — mirrored audit trail from the RESEARCH_TRACK build. Retention: archive after 30 days.

## Naming contract for anything new (from the approved proposal, restated)

All new session output goes under `work/`, never the root:
```
work/
  benchmarks/<YYYY-MM-DD>-<slug>/     # one session = one dated dir
      runs/  consolidated/  dashboards/<arm>/
  research/<YYYY-MM-DD>-<topic-slug>/
  audits/<YYYY-MM-DD>-<slug>/
  status/<component>/
```
Lowercase-hyphen slugs, dated prefixes, no more `SHOUTING_CAPS`/`_v1_prefix` siblings.
