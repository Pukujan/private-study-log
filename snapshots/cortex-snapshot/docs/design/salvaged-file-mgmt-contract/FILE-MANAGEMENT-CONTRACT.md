# File-Management Proposal — `D:\hermes\cortex-local\` (2026-07-07)

**Status: DESIGN / PROPOSAL ONLY.** Nothing has been moved, renamed, or deleted.
This is a read-only survey plus a move-list for the user to approve or edit before
any reorganization pass runs.

---

## 1. What this directory actually is

`cortex-local\` is **two things stacked on top of each other**:

1. **A Cortex *workspace* runtime** — the standard path-resolved layout a local
   Cortex/Hermes instance expects (`corpus\`, `docs\`, `library\`, `logs\`, `audit\`,
   `contracts\`). Per `docs/CORTEX-ROUTES-AND-OWNERSHIP.md`, a "route" is just *which
   workspace directory a process resolves to* — these names are load-bearing.
   **Moving them can silently break a running/next Cortex or Hermes process.**
2. **This session's benchmark sprawl** — everything the multi-lane dashboard/benchmark
   run dumped at the root next to (1).

## 2. Categorization of every top-level entry

| Entry | What it is | Provenance | Live now? |
|---|---|---|---|
| `benchmark_runs\` | Per-task run dirs (`taskNN_*`, `_v1/_v2_prefix`, `_run_summary_*.json`) + transcripts | **SESSION** | **ACTIVE** (14:01) |
| `CONSOLIDATED_REPORT\` | Output of `ops/_consolidate_benchmark_report.mjs` (deliverables/closeout/REPORT per task) | **SESSION** | **ACTIVE** (14:00) |
| `dashboard_A_no_mcp\` | React dashboard, no-MCP arm (has `node_modules`, `dist`) | **SESSION** | **ACTIVE — being written NOW** (14:01) |
| `dashboard_B_with_mcp\` | React dashboard, with-MCP arm (no build yet) | **SESSION** | **ACTIVE — being written NOW** (14:01) |
| `EXTENSION_STATUS\` | Browser-extension status/design/built-code snapshot | **SESSION** | recent (13:58) |
| `audit-reads\` | LIVING_ONTOLOGY / REPO_MANIFEST / SSC audit outputs (07-06) | **SESSION** | idle |
| `repo-changes\` | `owner_route_changes_20260706.json` | **SESSION** | idle |
| `research\` | Deep-research fan-out outputs (`*_state_machine`, `multimodel_research_fanout`, etc.) | **SESSION (mixed)** — but `sources.yaml` + `run_deep_research.py` are pre-existing tooling | recent (13:25) |
| `audit\` | Cortex closeout audit log (`audit-log-1/agent/*`) | **WORKSPACE runtime** (session writes into it) | **ACTIVE** (13:13) |
| `contracts\` | Write-gate contract JSON | **WORKSPACE runtime** | **ACTIVE** (13:57) |
| `logs\` | `mcp-events`, `search-telemetry`, `state_engine.sqlite*` | **WORKSPACE runtime** | **ACTIVE** (13:57) |
| `library\` | `cortex-library\` search index + sources | **WORKSPACE runtime** | **ACTIVE** — index rebuilt (13:21) |
| `docs\` | `cortex-1\` (248 corpus docs) + `research\` | **WORKSPACE / substrate, UNCLEAR** | idle |
| `corpus\` | empty | **WORKSPACE substrate, UNCLEAR** | empty |
| `reports\` | empty | **UNCLEAR** (pre-existing placeholder) | empty |
| `run_deep_research.py` | Deep-research entrypoint script | **PRE-EXISTING tooling** | idle |

**Bottom line:** the sprawl to corral is `benchmark_runs`, `CONSOLIDATED_REPORT`,
`dashboard_A/B`, `EXTENSION_STATUS`, `audit-reads`, `repo-changes`, `research`. The
workspace-runtime dirs (`audit`, `contracts`, `logs`, `library`, `docs`, `corpus`) are
**path-resolved and should stay at root** — reorganizing them is a Cortex-routing risk,
not housekeeping.

---

## 3. Proposed `MANIFEST.md` + `manifest.json` schema

`MANIFEST.md` = human-readable table (like §2 above), regenerated from `manifest.json`.
`manifest.json` = machine source of truth every future agent reads before writing:

```json
{
  "workspace": "D:/hermes/cortex-local",
  "updated": "2026-07-07",
  "schema_version": 1,
  "entries": [
    {
      "path": "work/benchmarks/2026-07-07-dashboard-mcp/",
      "purpose": "Dashboard MCP-vs-no-MCP benchmark session",
      "provenance": "session",          // session | workspace-runtime | pre-existing | unclear
      "owner": "hermes-benchmark",      // agent/lane that writes here
      "status": "live",                 // live | idle | archived
      "retention": "archive-after-30d",
      "do_not_move": false,             // true = path-resolved, moving breaks a process
      "consumers": ["ops/_consolidate_benchmark_report.mjs"]
    }
  ]
}
```

`do_not_move: true` is the guardrail — set on every workspace-runtime dir so a future
cleanup agent refuses to touch them.

---

## 4. Naming contract for future output

Root stays small. **All new session output goes under `work/`**, never the root:

```
work/
  benchmarks/<YYYY-MM-DD>-<slug>/     # one session = one dated dir
      runs/  consolidated/  dashboards/<arm>/
  research/<YYYY-MM-DD>-<topic-slug>/
  audits/<YYYY-MM-DD>-<slug>/
  status/<component>/
```
Rules: (a) dated `YYYY-MM-DD` prefix on every session dir; (b) lowercase-hyphen slugs
(no more `SHOUTING_CAPS` + mixed `_v1_prefix` conventions); (c) benchmark *arms* are
subdirs of one session dir, not sibling roots; (d) the root holds only workspace-runtime
dirs + `MANIFEST.md`/`manifest.json`.

## 5. Retention / archival convention (practical)

- **live** → in place. **idle >30d** → `work/_archive/<YYYY-QN>/…` (move, keep tree). 
- `node_modules\`, `dist\` are **never archived, never committed** — regenerable; add to
  a `.gitignore`/cleanup allow-delete list.
- `manifest.json` `status` is flipped to `archived` at move time (one edit, auditable).
- Archival is a **manual, manifest-driven pass**, not a cron job — matches this repo's
  "cited evidence, not automation-on-trust" posture.

---

## 6. CONCRETE move-list (for approval)

**Safe to move once the session is idle** (session sprawl, not path-resolved):

| From | To |
|---|---|
| `benchmark_runs\` | `work/benchmarks/2026-07-07-dashboard-mcp/runs/` |
| `CONSOLIDATED_REPORT\` | `work/benchmarks/2026-07-07-dashboard-mcp/consolidated/` |
| `dashboard_A_no_mcp\` | `work/benchmarks/2026-07-07-dashboard-mcp/dashboards/A_no_mcp/` |
| `dashboard_B_with_mcp\` | `work/benchmarks/2026-07-07-dashboard-mcp/dashboards/B_with_mcp/` |
| `EXTENSION_STATUS\` | `work/status/browser-extension/` |
| `audit-reads\` | `work/audits/2026-07-06-ssc-audit-reads/` |
| `repo-changes\` | `work/audits/2026-07-06-owner-route-changes/` |
| `research\` (session outputs only) | `work/research/2026-07-06-multimodel-fanout/` |

**⚠ MUST update in the same approved pass** (or the script orphans):
`d:\claude\stupidly-simple-cortex\ops\_consolidate_benchmark_report.mjs` hardcodes
`RUNS_DIR = 'D:/hermes/cortex-local/benchmark_runs'` and
`OUT_DIR = 'D:/hermes/cortex-local/CONSOLIDATED_REPORT'`. If `benchmark_runs` /
`CONSOLIDATED_REPORT` move, **both constants must change to the new paths** before the
script is run again.

**⚠ DO NOT MOVE mid-session:** `dashboard_A/B`, `benchmark_runs`, `CONSOLIDATED_REPORT`
are being written **right now** (mtimes 14:00–14:01) — moving them mid-write corrupts an
active agent's output. Wait for the run to finish.

**DO NOT MOVE (workspace-runtime, path-resolved — moving risks breaking Cortex/Hermes):**
`audit\`, `contracts\`, `logs\`, `library\`, `docs\`, `corpus\`. Keep at root; catalog
them in the manifest with `do_not_move: true`.

**Unclear — leave, flag for user:**
- `corpus\` (empty), `reports\` (empty) — pre-existing placeholders; unknown if a process
  writes to them. Leave.
- `docs\` — 248 corpus docs + research; mtime 07-06 is inside this session's window, so
  provenance is genuinely ambiguous (could be the pre-existing indexed corpus **or** a
  session copy). **Do not touch until the user confirms** which.
- `research\sources.yaml`, `run_deep_research.py` — pre-existing tooling co-located with
  session research output; if `research\` moves, keep these two at root (they're inputs,
  not outputs).

---

## 7. Research note (brief)

The emerging pattern for multi-agent output is exactly this: a declarative,
agent-consumable **manifest** (YAML/JSON) as the interface between agents, plus a
**hierarchical storage strategy** separating artifact types (geometry/results/logs →
here: runs/consolidated/dashboards/logs). This proposal follows both.

Sources:
- [Agentic Configuration Manifests — EmergentMind](https://www.emergentmind.com/topics/agentic-configuration-manifests)
- [LLM Multi-Agent Systems: Challenges and Open Problems (arXiv)](https://arxiv.org/pdf/2402.03578)
- [OrgAgent: Organize Your Multi-Agent System like a Company (arXiv)](https://arxiv.org/pdf/2604.01020)
