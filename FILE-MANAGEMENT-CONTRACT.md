# File-Management Contract — `Pukujan/private-study-log`

**Status:** DRAFT for owner approval (B28)  
**Created:** 2026-07-19  
**Governing principle:** Organizational infrastructure, not code. Owner approves every move.

---

## 1. What this repo is

`private-study-log` is the owner's **personal study and research log repository**. It holds:
- **Cortex study logs** — analysis, distillation, and research notes produced during Cortex/Hermes development
- **Project artifacts** — design docs, scope files, and research for specific projects
- **Snapshots** — point-in-time copies of codebases for reference
- **Communications** — transcripts or exports from external model interactions

This repo is currently **PUBLIC despite its name** — flagged as an open owner decision. Until visibility changes, treat every file as potentially exposed.

---

## 2. Canonical folder structure

```
private-study-log/
  MANIFEST.md                  # Human-readable index of all files (this repo's TOC)
  FILE-MANAGEMENT-CONTRACT.md  # This file — the rules

  cortex/                      # All Cortex/Hermes study logs, distillations, research
    <YYYY-MM-DD>-<slug>.md     # Study logs (date-prefixed)
    <YYYY-MM-DD>-<slug>.html   # Rendered/visual study logs
    <topic-slug>.md             # Standing reference docs (no date prefix if evergreen)

  projects/                    # Project-specific artifacts (one subdir per project)
    <project-name>/            # Lowercase-hyphen slugs
      <files...>

  snapshots/                   # Point-in-time codebase copies
    <repo-name>-<date>/        # e.g. cortex-snapshot/

  communications/              # External model transcripts/exports
    <provider>-<YYYY-MM-DD>/   # e.g. gpt-communication/2026-07-16/

  archive/                     # Retired or superseded files
    <YYYY-QN>/                 # Quarterly archive buckets
```

### Rules

| Rule | Detail |
|------|--------|
| **R1: Date prefix on new study logs** | Every new study log file must start with `YYYY-MM-DD-` followed by a lowercase-hyphen slug. Example: `2026-07-19-agent-reliability-review.md` |
| **R2: Lowercase-hyphen slugs everywhere** | No `SHOUTING_CAPS`, no `CamelCase`, no spaces in filenames. Existing files with old conventions are grandfathered (see §5). |
| **R3: Root stays clean** | Only `MANIFEST.md`, `FILE-MANAGEMENT-CONTRACT.md`, and `README.md` live at root. Everything else goes in a category folder. |
| **R4: Projects by name** | Each project gets one subdir under `projects/`. Project scope docs, design notes, and related artifacts go there. |
| **R5: Cortex logs in `cortex/`** | All study logs, distillations, flowcharts, and research notes about Cortex/Hermes go in `cortex/`. This includes both markdown and HTML formats. |
| **R6: Snapshots are frozen** | Files under `snapshots/` are point-in-time copies. They are never edited after creation — if the source changes, make a new snapshot with a new date suffix. |
| **R7: MANIFEST is source of truth** | The MANIFEST.md tracks every file in the repo with metadata. When you add/move/delete a file, update the MANIFEST in the same commit. |

---

## 3. MANIFEST schema

Each entry in MANIFEST.md records:

| Field | Required | Description |
|-------|----------|-------------|
| `path` | Yes | Relative path from repo root |
| `category` | Yes | One of: `cortex-study-log`, `cortex-reference`, `project-artifact`, `snapshot`, `communication`, `meta` |
| `date` | If applicable | ISO date (`YYYY-MM-DD`) of the content, not the file creation date |
| `topic` | Yes | Short description (e.g. "agent reliability adversarial review") |
| `source_model` | If known | Which model produced or was the subject of the content |
| `status` | Yes | One of: `current`, `historical`, `superseded`, `archived` |
| `notes` | No | Extra context (e.g. "pre-dates this contract", "mirrored from local repo") |

---

## 4. Naming conventions (new files)

### Study logs (cortex/)
```
cortex/<YYYY-MM-DD>-<topic-slug>.md       # Markdown study log
cortex/<YYYY-MM-DD>-<topic-slug>.html     # Rendered study log (downloadable)
cortex/<topic-slug>.md                     # Evergreen reference (no date prefix)
```

### Project artifacts (projects/)
```
projects/<project-name>/<descriptive-name>.md
```
Project names are lowercase-hyphen. Example: `projects/hermes-auto/`, `projects/context-engineering/`.

### Snapshots (snapshots/)
```
snapshots/<repo-name>-<YYYY-MM-DD>/
```

### Communications (communications/)
```
communications/<provider>-<YYYY-MM-DD>/<files>
```

---

## 5. Legacy file handling

Files that predate this contract are **grandfathered in place**. They do NOT need to be renamed or moved immediately. However:

1. They should be listed in the MANIFEST with `"notes": "pre-dates contract"` or similar.
2. When a legacy file is substantively updated, rename it to follow the new convention at that time.
3. The owner may authorize a one-time bulk reorg pass (see `REORG-PLAN.md` in this contract directory).

### Existing root-level files (legacy, to be categorized)

The following files currently live at the repo root and need categorization:

| File | Suggested category | Action |
|------|-------------------|--------|
| `agent-reliability-and-adversarial-review-study-log-2026-07-17.md` | `cortex-study-log` | Move to `cortex/` |
| `build-phases-living-ontology.md` | `cortex-reference` | Move to `cortex/` |
| `cortex-chatgpt-review.md` | `cortex-study-log` | Move to `cortex/` |
| `cortex-concepts-and-glossary.md` | `cortex-reference` | Move to `cortex/` |
| `cortex-pipeline-architecture.md` | `cortex-reference` | Move to `cortex/` |
| `cortex-research-path-flowchart-2026-07-19.html` | `cortex-study-log` | Move to `cortex/` |
| `cortex-research-path-flowchart-2026-07-19.md` | `cortex-study-log` | Move to `cortex/` |
| `innovation-explore-exploit-and-kernel-prior-art-study-log-2026-07-17.md` | `cortex-study-log` | Move to `cortex/` |
| `simple-codegraph-agent-contract-blog.md` | `cortex-study-log` | Move to `cortex/` |
| `CHATGPT-DEEP-RESEARCH-BRIEF-v2-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `CHATGPT-DEEP-RESEARCH-BRIEF-v3-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `CORTEX-ALIGNMENT-REVIEW-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `CORTEX-DEEP-RESEARCH-HANDOFF-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `CORTEX-EXPECTED-OUTCOME-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `CORTEX-RESEARCH-REVIEW-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `CORTEX-TARGET-ARCHITECTURE-v3-2026-07-16.md` | `communication` | Move to `communications/chatgpt-2026-07-16/` |
| `Database-architecture-study-log-4-30-26.md` | `project-artifact` | Move to `projects/database-architecture/` |
| `Vector-db-learning-neoj-qdrant.md` | `project-artifact` | Move to `projects/database-architecture/` |
| `solving llm problem.md` | `project-artifact` | Move to `projects/working-with-ai/` |
| `dev log 04 30 2026.md` | `meta` | Move to `archive/2026-Q2/` or `communications/` (needs owner clarification) |
| `SNAPSHOT-README.md` | `snapshot` | Move to `snapshots/cortex-snapshot/` (goes with `cortex-snapshot/` dir) |

### Existing `Projects/` subdirs (legacy, in place)

| Subdir | Action |
|--------|--------|
| `Projects/Context-engineering/` | Rename to `projects/context-engineering/` when contents are updated |
| `Projects/hades os/` | Rename to `projects/hades-os/` |
| `Projects/hermes-auto/` | Rename to `projects/hermes-auto/` |
| `Projects/hermes/` | Rename to `projects/hermes/` |
| `Projects/seo-automation/` | Rename to `projects/seo-automation/` |
| `Projects/ui ux/` | Rename to `projects/ui-ux/` |
| `Projects/vastai/` | Rename to `projects/vastai/` |
| `Projects/working with ai/` | Rename to `projects/working-with-ai/` |
| `Projects/Ai agent.md` | Move to `projects/ai-agent/` (or appropriate subdir) |
| `Projects/Litigation copilot.md` | Move to `projects/litigation-copilot/` |
| `Projects/ebt_coordination_copilot_project_scope.md` | Move to appropriate project subdir |
| `Projects/litigation_ai_copilot_platform_scope.md` | Move to appropriate project subdir |

### `cortex-snapshot/` (legacy, frozen)

This is a point-in-time code snapshot. Move to `snapshots/cortex-snapshot/` and do not edit after the move.

---

## 6. Retention and archival

| Status | Meaning |
|--------|---------|
| `current` | Actively relevant; the latest version of this topic |
| `historical` | Was current once; kept for reference, not updated |
| `superseded` | Replaced by a newer file; kept for audit trail |
| `archived` | Moved to `archive/` quarterly bucket; retrieved only if needed |

Archival is manual, MANIFEST-driven. No automated cron.

---

## 7. Relationship to local repo (`stupidly-simple-cortex`)

The local repo has `research/study-log/` containing study logs produced during Cortex development (currently: `fable-vs-kimi-p3b-study-2026-07-19.html`). These are the **local copies** of study logs that also get pushed to `private-study-log`.

**Rule:** When a study log is produced in the local repo, it lives in `research/study-log/` locally AND gets published to `private-study-log/cortex/`. The MANIFEST in the private repo should note which files also have local copies.

---

## 8. Open decisions (owner must decide)

1. **Visibility:** Make the repo private? (Currently public despite name.)
2. **Bulk reorg:** Approve the move-list in `REORG-PLAN.md` for a one-time pass?
3. **`dev log 04 30 2026.md`:** Category? (communication? meta? archive?)
4. **Project scope files** in `Projects/`: Do `Ai agent.md`, `Litigation copilot.md`, etc. get their own project subdirs, or stay as loose files?

---

## 9. Maintenance protocol

1. **Before creating a new file:** Check this contract for the correct folder and naming convention.
2. **After creating/moving/deleting:** Update `MANIFEST.md` in the same commit.
3. **Periodically:** Review `status` fields in the MANIFEST. Flip `current` -> `historical` when a file is superseded. Flip `historical` -> `archived` after 90 days idle.
4. **Never:** Commit secrets, API keys, or access tokens to this repo (it is or may become public).
