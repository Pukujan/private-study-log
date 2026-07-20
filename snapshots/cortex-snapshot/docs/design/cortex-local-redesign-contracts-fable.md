# Cortex-local redesign — design contracts (Fable, 2026-07-13)

**Status: DESIGN CONTRACTS ONLY. No implementation in this change.**
Author: Fable (lead-architect pass). Anti-circularity: Codex/terra review in
parallel; every claim about current behavior below is cited to a real file/line
read during this pass, not assumed. Where a claim is a judgment call or an
unresolved risk, it is marked **UNCERTAIN** or listed in §5.

The owner's problem, verbatim intent: research-first and doc-update are
enforced by *memory and discipline*, which fails — including for the
orchestrator itself (CLAUDE.md records the 2026-07-12 `max_tokens=300` vs
recorded-12000-floor incident, caught by the user). These must become
**deterministic and structural**. Plus: Cortex must become a wrap-a-host-agent
scaffold (Hermes first, then Claude Code, Codex), the flat 848-file audit log
must become per-project-for-humans + ontology-for-agents, and the whole thing
must be validated by a real A/B test, not pre-written tests.

---

## 0. Grounding: what exists today (verified by reading, with the exact gap)

| Piece | Where | Verified behavior |
|---|---|---|
| State engine | `cortex_core/state_engine.py` | Event-sourced, single-writer SQLite engine. `BUILD_TRACK` (lines 75–137): `SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT → DONE`. Charts are data; `register_track` (373–381) validates fail-at-load; `_validate_chart` deep-freezes (661–694). |
| Phase-scoped tool legality | `state_engine.py:384–398` (`phase_legal_tools`), `_legal_tools` (824–830), legality refusal in `step()` (1086–1095) | The server discloses/permits exactly the current phase's advance tool + extras. Illegal tool ⇒ `ILLEGAL_IN_STATE` refusal with `do_instead`. |
| Bound deterministic gate (the pattern to copy) | `APP_BUILD_TRACK` `SMOKE.bound_gate: "smoke_verdict"` (`state_engine.py:328–331`), `smoke_verdict_gate` (467–514), reserved-topology immutability `_RESERVED_TRACKS`/`_APP_BUILD_REQUIRED` (599–633), receipts in `cortex_core/receipts.py` (`validate_smoke_receipt`) | A caller CANNOT forge a pass: the server mints a receipt by *running* the deterministic check, the gate re-validates receipt↔task↔artifact-digest binding, and the chart topology that binds the gate is immutable under `register_track`. This is the strongest enforcement primitive in the codebase. |
| MCP write gates | `cortex_core/mcp.py`: `_forced_docs_gate` (115–140), `_state_machine_gate` (311–340), `_contract_gate` (395–432), `_admin_gate` (435–491) | They gate **only the MCP write tools** (`cortex_fetch_doc` at 1453, `cortex_write_log` at 1563–1566). |
| Per-workspace engine over MCP | `mcp.py:816–999` (`_run_engine`, `cortex_run_start/step/state`); state DB at `<workspace>/logs/state_engine.sqlite` (872) | An agent that *chooses* to call `cortex_run_start` is driven phase-by-phase with envelopes/refusals. |
| Gate defaults | `mcp.py:105–108` (`CORTEX_FORCED_PIPELINE` default `"0"`), 279–282 (`CORTEX_MANDATORY_STATE_MACHINE` default `"0"`), 285–291 (`CORTEX_CONTRACT_GATE` default OFF — "manufactures tool-call loops"), 294–304 (`CORTEX_ADMIN_GATE` default OFF, same reason) | **Every discipline gate defaults OFF**, and each is bypassable with a free-text `override_reason` (logged only: 122–127, 322–325, 417–419). |
| SEARCH gate strength | `default_gate` (`state_engine.py:436–445`) | "Permit any well-formed phase report." `SEARCH_BRAIN` advances on ANY dict submitted via `cortex_report_findings` — a fabricated "I searched" passes. |
| Doc-update phase | `BUILD_TRACK` | **Does not exist.** `REVIEW → CLOSEOUT` directly; nothing anywhere requires docs/README to be current before closeout. |
| Closeouts | `cortex_core/audit.py`: `write_closeout` (220–260), `choose_audit_dir` (71–83), `MAX_AUDIT_FILES_PER_SHARD = 500` (21) | Flat `audit/audit-log-N/agent/cortex-closeout__<ts>-<slug>__<uuid7>.{md,json}`; shard rolls at 500 `.md`. `audit/audit-log-1/agent/` currently holds 848 files. No project dimension anywhere in the schema. |
| Ontology | `cortex_core/ontology.py` (docstring 1–33; `Entity` 134–157, `Relation` 159–177), `docs/ontology/{entities,relations}.jsonl`, `schema.yaml`; CLI `cortex-ontology` (`pyproject.toml:62`) | Append-only JSONL, last-record-wins, bi-temporal edge invalidation, schema-validated, provenance-required (`source_paths`). **Exists and is exactly the right substrate — extend, don't build.** |
| Onboarding | `cortex_core/onboarding.py` (`TOOL_GUIDANCE`, anti-drift test), `cortex_core/workspace_scaffold.py` (idempotent AGENTS.md/MANIFEST drop, never overwrites) | In-band self-describing guide + a proven "drop governance files idempotently" pattern. |
| Host files | repo root `CLAUDE.md`, `AGENTS.md`, `SOUL.md`, `HANDOFF.md`; `.mcp.json` (hosted Railway HTTP brain + stdio `claude-bias`) | Per-host onboarding-by-convention already works: each host reads its own file. |

**The single gap, stated precisely:** every deterministic mechanism above sits
*behind the MCP tool surface*. The orchestrator (Claude Code, Codex, the human
driving) does its work with **host-native tools** — Read/Edit/Bash/WebSearch —
which never pass through `StateEngine.step()`, never hit `_forced_docs_gate`,
and are never checked for phase legality. CLAUDE.md names this as the open
build idea ("route the orchestrator's own decisions through the state-engine
search-gate so this is enforced deterministically, not by memory"). Contracts
1–2 close it; Contract 3 restructures the audit record it produces; Contract 4
proves (or falsifies) that it works.

---

## Contract 1 — Deterministic task-flow gate for the ORCHESTRATOR

### 1.0 Principle

The engine already refuses illegal moves *for callers that enter it*. The
redesign has exactly two jobs:

1. **Make the chart complete** — add the missing `DOC_UPDATE` phase and make
   `SEARCH_BRAIN` unfakeable (bound deterministic gates, receipts pattern).
2. **Make entering it non-optional for the orchestrator** — intercept
   host-native tools at the host-harness layer and route the legality check to
   the engine.

Enforcement is a ladder, stated honestly (this matters — "impossible to skip"
is only literally true at levels L1/L2):

| Level | Mechanism | Hosts | Guarantee |
|---|---|---|---|
| **L2** | MCP + engine: every write flows through gated tools; chart-bound gates | Hermes (MCP *is* its harness), any MCP-first agent | Structural. Cannot be skipped or forged. |
| **L1** | Host-harness hooks (deterministic, harness-executed, model can't decline them) calling the door check | Claude Code (`PreToolUse`/`Stop` hooks) | Structural for hooked tools. |
| **L0** | Readable protocol + required evidence ledger, scored after the fact | Codex and any host without a hook layer | Honor-system in the moment; deterministic *detection*, not prevention. **UNCERTAIN whether Codex ships a usable hook layer — verify before promising L1 there.** |

### 1.1 Chart change: `BUILD_TRACK` v2 (extend, don't fork)

Bump `BUILD_TRACK` to `version: "2"` in `state_engine.py` with two changes:

```python
"SEARCH_BRAIN": {
    "advance_tool": "cortex_report_findings",
    "extra_tools": ["cortex_search"],
    "next": "RESEARCH",
    "bound_gate": "search_receipts",          # NET-NEW (mechanism reused from SMOKE)
    "instruction": "Search the corpus with cortex_search, then call "
                   "cortex_report_findings citing the receipt_ids it returned.",
},
...
"REVIEW": { ..., "next": "DOC_UPDATE", ... },  # was "CLOSEOUT"
"DOC_UPDATE": {                                 # NET-NEW STATE
    "advance_tool": "cortex_submit_doc_update",
    "extra_tools": ["cortex_search"],
    "next": "CLOSEOUT",
    "bound_gate": "docs_current",
    "rework_to": "DOC_UPDATE",   # gate fail loops in place (fix the docs), rework_cap-bounded
    "instruction": "Update every doc mapped to the code you touched "
                   "(protocol/docs.map.yaml), then call cortex_submit_doc_update "
                   "with the change manifest.",
},
```

And extend the reserved-topology immutability (the `_APP_BUILD_REQUIRED`
mechanism, `state_engine.py:604–633`) with a `_BUILD_REQUIRED` spine so no
`register_track("build", …)` can drop `SEARCH_BRAIN` as initial, the
`search_receipts` binding, `REVIEW → DOC_UPDATE → CLOSEOUT`, or the
`docs_current` binding. Today `_validate_reserved_topology` early-returns for
every track except `app_build` (line 622–623) — that early-return is the hole
to close. `_RESERVED_TRACKS` already contains `"build"` (599).

`phase_legal_tools` (384) needs **no change** for the MCP plane; see §1.4 for
its host-tool-class extension.

### 1.2 Bound gate A: `search_receipts` (research-first, unfakeable)

Copy the SMOKE receipt discipline (`smoke_verdict_gate`, 467–514) exactly:

- **Minting:** `cortex_search` (and `cortex_scope_pack`), when called with a
  `task_id`, mints a server-side **search receipt** into the workspace receipt
  store (extend `cortex_core/receipts.py`):

  ```json
  {"receipt_id": "sr_<uuid7>", "kind": "search", "task_id": "t_…",
   "session_id": "…", "query": "…", "n_hits": 3,
   "top_paths": ["docs/PHASE-GATES.md", "audit/…"], "results_digest": "sha256:…",
   "ts": 1789…}
  ```

  The digest is computed server-side over the actual result set. `n_hits: 0`
  is a **legal receipt** — "no coverage exists" is a valid, citable SEARCH
  outcome (the CLAUDE.md pre-flight explicitly allows "state plainly that none
  exists"); what is illegal is not running the search.

- **Gate:** `search_receipts_gate(phase, task, payload)` accepts only
  `{"receipt_ids": [...], "findings": "..."}`; it looks each receipt up and
  re-validates `task_id` binding + digest + minting-gate identity (mirror
  `validate_smoke_receipt`'s task/artifact binding). Missing / foreign-task /
  tampered ⇒ fail CLOSED. A payload with a `findings` prose blob and no valid
  receipt ⇒ `SEARCH_NOT_SERVER_WITNESSED` (the analogue of
  `VERDICT_NOT_SERVER_OWNED`, 492–495).
- Bind it in chart data (`bound_gate`), enforced in `StateEngine._run_gate`
  like `smoke_verdict` — so constructing the engine without
  `make_universal_gate` still fails closed (the terra fix #1 property,
  517–528).

This converts CLAUDE.md's "run the lookup anyway" from an instruction into a
precondition: **RESEARCH/PLAN are unreachable without a server-witnessed
corpus lookup.**

### 1.3 Bound gate B: `docs_current` (doc/README update, deterministic)

**Inputs the server owns:**

1. `create_task` records `git rev-parse HEAD` of the workspace into
   `intent["task_start_ref"]` (net-new, one line in `create_task`,
   `state_engine.py:898–922`; same intent-persistence move as
   `scaffold_artifact_digest`, 315 / 501–506).
2. A committed mapping file, `protocol/docs.map.yaml` (Contract 2 folder):

   ```yaml
   version: 1
   mappings:
     - code: ["cortex_core/state_engine.py", "cortex_core/mcp.py"]
       docs: ["docs/SERVER-DRIVEN-PIPELINE.md"]
     - code: ["pyproject.toml", "cortex_core/**/main*"]   # install/CLI surface
       docs: ["README.md"]
     - code: ["cortex_core/ontology.py", "docs/ontology/schema.yaml"]
       docs: ["docs/ontology/README.md"]
   default_docs: []          # unmapped code triggers nothing (explicit choice)
   ```

**Gate:** `docs_current_gate` runs `git diff --name-only <task_start_ref>` in
the workspace (server-side, subprocess — same trust class as
`app_gates.run_done_checks`). For every mapping whose `code` globs intersect
the touched set, at least one of its `docs` must ALSO appear in the touched
set **with a non-whitespace content diff**. Failure names the exact unmet
mappings. Waiver: `{"waiver": {"reason": …, "passcode": …}}` validated by
`cortex_core/authz.py`'s existing passcode machinery — **not** the free-text
`override_reason` pattern (that pattern is precisely the discipline-by-memory
failure being removed).

**Honest limit (stated, not hidden):** this proves the mapped doc *changed*,
not that it changed *well*. A one-character edit passes. Semantic doc quality
is judge territory (Phase 4.4) and stays OUT of the deterministic verdict path
— same doctrine as the objective lanes ("a deterministic checker, never a
judge, decides pass/fail"). The A/B scorer (Contract 4) measures whether
change-detection alone moves the outcome; if agents game it with trivial
edits, that is a *finding*, and the next lever is a section-anchored map
(`docs: [{path, must_touch_heading}]`), still deterministic.

### 1.4 Routing the orchestrator's host-native tools (the actual gap)

**Chart extension** — each state gains a declarative host-tool-class
allowlist (data, no engine logic):

```python
"SEARCH_BRAIN": { ..., "host_tools": ["read", "search"] },
"RESEARCH":     { ..., "host_tools": ["read", "search", "fetch"] },
"PLAN":         { ..., "host_tools": ["read"] },
"SPEC":         { ..., "host_tools": ["read", "edit:tests"] },
"IMPLEMENT":    { ..., "host_tools": ["read", "edit", "exec"] },
"REVIEW":       { ..., "host_tools": ["read", "exec"] },
"DOC_UPDATE":   { ..., "host_tools": ["read", "edit:docs"] },
"CLOSEOUT":     { ..., "host_tools": [] },
```

New module-level function beside `phase_legal_tools` (`state_engine.py:384`):

```python
def phase_legal_host_tools(track: str, state: str) -> list[str]: ...
```

**Door check** — one new stdlib-only entry point (new module
`cortex_core/door.py`, CLI `cortex-door` added to `pyproject.toml:[project.scripts]`):

```
cortex-door check --workspace <ws> --host-tool <Edit|Write|Bash|WebSearch|…> \
                  [--path <file-arg>] [--session <id>]
→ exit 0 (allow) | exit 2 (deny) + JSON on stdout:
  {"allow": false, "state": "SEARCH_BRAIN", "task_id": "t_…",
   "reason": "Edit is not legal in SEARCH_BRAIN",
   "do_instead": "cortex_search then cortex_report_findings",
   "legal_host_tools": ["read", "search"]}
cortex-door status --workspace <ws>
→ {"open_task": "t_…"|null, "state": …, "docs_gate_pending": bool}
```

It reads `<workspace>/logs/state_engine.sqlite` **read-only** (path fixed at
`mcp.py:855–872`); with no open task it maps host tools to a default-deny for
`edit/exec` and returns `do_instead: "cortex_run_start"` — i.e. *mutating the
workspace without an open engine task is itself the violation*. `read`/
`search` classes are always allowed (grounding must never be gated — gating
reads would fight the research-first goal).

**Per-host wiring:**

- **Claude Code (L1):** the scaffold folder (Contract 2) ships a settings
  fragment installing `PreToolUse` matchers for `Edit|Write|NotebookEdit|Bash`
  → `cortex-door check` (deny on exit 2, refusal JSON surfaced to the model as
  the hook message — the engine's "refusal = guidance" doctrine carried
  upward), and a `Stop` hook → `cortex-door status` that blocks stop while an
  open task is pre-`DONE`, **bounded to 2 consecutive blocks** then
  warn-through. The bound is load-bearing: the repo's own record says
  unbounded refusal loops are the failure mode that got the contract/admin
  gates defaulted OFF (`mcp.py:286–288, 295–297`). Hooks are harness-executed
  — the model cannot decline them; that is what makes this *structural* rather
  than instructional.
- **Hermes (L2):** Hermes is harness-less; the MCP is its harness (the
  forced-pipeline rationale, `mcp.py:74–86`). Its bootstrap profile
  (Contract 2 §2.4) sets `CORTEX_FORCED_PIPELINE=1`,
  `CORTEX_MANDATORY_STATE_MACHINE=1`, and a net-new
  `CORTEX_STRICT_OVERRIDES=1` under which `_forced_docs_gate` /
  `_state_machine_gate` accept only passcode-authorized waivers (reuse
  `authz.py`), not free-text `override_reason`. The `_state_machine_gate`
  KNOWN LIMITATION ("a session can drive a trivial/fake task to DONE to
  unlock writes", `mcp.py:274–276`) is largely closed by §1.2/§1.3: reaching
  DONE now costs a real witnessed search and a real doc-diff.
- **Codex (L0):** `AGENTS.md` binding + MCP write-gating only. Named honestly
  as the weakest tier; the Contract-4 scorer detects violations post-hoc from
  the transcript + git history.

### 1.5 What this contract does NOT do

No LLM judge in any verdict path. No gating of reads. No new service — the
door reads the same SQLite file the engine writes. No change to the engine's
concurrency core (`_txn`, seq fencing, idempotency, 804–895) — everything
here is chart data, two gate functions, one receipt kind, one read-only CLI.

---

## Contract 2 — The read-folder, zero-install scaffold (`.cortex/`)

**Owner constraint (2026-07-13, supersedes the earlier pip-bootstrap
framing):** the scaffold is a FOLDER the host agent READS and immediately
operates under — like AGENTS.md/CLAUDE.md today, but carrying the whole
discipline. No `pip install`, no server process as a prerequisite. The MCP is
referenced and *enhances*; it is never required for the discipline to bind.

### 2.1 Folder layout (the contract)

Dropped at a host project's root (any project, not just this repo):

```
.cortex/
  START-HERE.md                # 1 page: what this folder is, the ladder (L0/L1/L2),
                               # and the binding rule: "your first action on any
                               # task is SEARCH; you may not close without DOC-UPDATE
                               # and a CLOSEOUT record."
  protocol/
    STATE-MACHINE.md           # the SEARCH→PLAN→BUILD→DOC-UPDATE→CLOSEOUT chart as
                               # readable rules — GENERATED from state_engine.BUILD_TRACK v2
                               # (see 2.5), never hand-written
    RESEARCH-FIRST.md          # SEARCH gate: what counts as evidence, citation format,
                               # "no coverage found" is a legal finding, guessing is not
    DOC-UPDATE.md              # docs.map.yaml semantics + the README rule
    CLOSEOUT.md                # closeout record schema (mirrors audit.py
                               # CLOSEOUT_SCHEMA_VERSION fields: task/result/status/
                               # tests/evidence/handoff) + where it goes (Contract 3 layout)
    docs.map.yaml              # per-project code→docs mapping (Contract 1 §1.3)
  state/
    TASK-LOG.md                # L0 degraded-mode ledger (see 2.3)
  audit/                       # per-project closeout home (Contract 3 layout, empty scaffold)
    projects/
  brain/
    MCP.md                     # OPTIONAL enhancement: how to attach cortex-mcp / the hosted
                               # brain (.mcp.json shape, cites the Railway endpoint pattern),
                               # and the rule "if the brain is attached, the engine's ledger
                               # replaces the manual one"
  hooks/                       # OPTIONAL L1, still zero-install: plain files, no package
    door.py                    # single-file, stdlib-only mirror of cortex-door check/status
                               # (runs under any python3; if python absent, hooks simply
                               # aren't wired — degrade to L0)
    claude-settings-fragment.json  # PreToolUse/Stop hook config referencing hooks/door.py
    README.md                  # how a human merges the fragment (one manual step, optional)
```

Root-level binding stubs (host-convention files, each ≤15 lines, written
beside the folder — the `workspace_scaffold.py` idempotency rule applies:
if the host file exists, append a fenced `<!-- cortex-local:begin/end -->`
managed block instead of overwriting; never touch hand-tuned content):

- `CLAUDE.md` block → Claude Code reads it natively.
- `AGENTS.md` block → Codex and generic agents read it natively.
- `HERMES.md` → the Hermes wrapper's convention.

Each stub says the same three things: (1) "You are operating under the
Cortex-local protocol — read `.cortex/START-HERE.md` before acting."
(2) "Your first action on ANY task is SEARCH (`.cortex/protocol/RESEARCH-FIRST.md`);
you may not finish without DOC-UPDATE and CLOSEOUT." (3) "If a `cortex` MCP
server is attached, drive tasks with `cortex_run_start`/`cortex_run_step` and
obey its envelopes; the manual ledger is the fallback, not the preference."

### 2.2 "Which agent is reading me" — resolution by convention, not detection

No detection code. Each host already auto-loads exactly one of the stub files
(Claude Code → CLAUDE.md; Codex → AGENTS.md; Hermes wrapper → HERMES.md — the
pattern already live at this repo's root). The stubs converge on the same
`START-HERE.md`, which contains a per-host table (what your hook layer is,
which ladder level you run at, what your closeout command is). Identity is
then *declared* server-side if/when the brain attaches:
`cortex_register(agent_id, model, role)` (`mcp.py:528–539`) stamps it — the
folder never needs to guess.

### 2.3 The discipline as READABLE rules + the L0 ledger

`STATE-MACHINE.md` presents the exact chart as a rule table an agent binds to:

```
| Phase      | You may                      | You may NOT           | To advance, record            |
| SEARCH     | read, search corpus/audit    | edit, run, fetch web  | ≥1 search + findings w/ paths |
| PLAN       | read                         | edit, run             | step plan                     |
| BUILD      | read, edit, run tests        | close, skip tests     | patch + test evidence         |
| DOC-UPDATE | edit docs/README only        | edit code             | doc-change manifest           |
| CLOSEOUT   | write closeout record        | further edits         | closeout per CLOSEOUT.md      |
```

`state/TASK-LOG.md` is the L0 enforcement substrate — an append-only markdown
ledger with a fixed row schema:

```
## task: <slug>  (opened <ts>)
- SEARCH: queries=[…] hits=[path#Lx, …] | "no coverage" (ts)
- PLAN: <link/inline> (ts)
- BUILD: files=[…] tests=<cmd → pass/fail> (ts)
- DOC-UPDATE: docs=[…] mapped-from=[…] | waiver: <reason> (ts)
- CLOSEOUT: .cortex/audit/projects/<slug>/closeouts/…  (ts)
```

L0 honesty: nothing *prevents* a vanilla-tool agent from skipping a row in the
moment. What the ledger buys deterministically is (a) a forcing structure —
the required-fields row is much harder to "forget" than a norm, and (b)
**post-hoc verifiability**: every field cross-checks against git history and
the filesystem, which is exactly what the Contract-4 scorer does. Prevention
requires L1 hooks or the L2 brain; the folder makes both one small step away
(`hooks/README.md`, `brain/MCP.md`), and each step up is optional and
independent.

### 2.4 Degradation / enhancement ladder (normative)

- **MCP absent (L0):** the folder alone binds. All rules readable; ledger
  manual; closeouts written as files per `CLOSEOUT.md` into
  `.cortex/audit/projects/<slug>/…`. Nothing in any protocol file may assume a
  running server (acceptance check for the folder's own content).
- **Hooks merged (L1):** `hooks/door.py` enforces phase legality against the
  ledger (L0 mode: it parses `TASK-LOG.md` for the open task's phase) or
  against `logs/state_engine.sqlite` when present (L2 mode) — same file, two
  data sources, ~200 lines, stdlib only.
- **Brain attached (L2):** `brain/MCP.md` gives the `.mcp.json` /
  `config.toml` snippets (stdio `cortex-mcp` if installed, else the hosted
  HTTP endpoint — both shapes already proven in this repo's `.mcp.json`) plus
  the strict profile env (`CORTEX_FORCED_PIPELINE=1`,
  `CORTEX_MANDATORY_STATE_MACHINE=1`, `CORTEX_STRICT_OVERRIDES=1`). The
  engine's event log supersedes the manual ledger; `TASK-LOG.md` becomes a
  generated read-only view (regenerated at closeout).

### 2.5 Anti-drift: the folder is GENERATED, tested, and versioned

The rules in `protocol/STATE-MACHINE.md` and the chart in
`state_engine.BUILD_TRACK` must be the same object or they will diverge
(the stale-doc failure Cortex exists to prevent, recursively). Contract:

- New maintainer command `cortex protocol render --out <dir>` (this repo only;
  consumers of the folder never run it) renders `STATE-MACHINE.md`,
  `START-HERE.md`'s rule section, and the stub blocks from `BUILD_TRACK` v2 +
  `onboarding.TOOL_GUIDANCE`, stamping `protocol_version` = chart `version`.
- A test in this repo (the `onboarding.py` `coverage_gap` pattern) fails if
  the committed `.cortex/` template disagrees with a fresh render.
- The folder template lives at `templates/cortex-local/` in this repo and is
  copied verbatim (any file copy: `git clone`, drag-drop, `cp -r`) — copying
  a folder is the entire "install".

---

## Contract 3 — Per-project modular audit + living ontology

### 3.1 Physical layout (humans first)

```
audit/
  projects/
    <project-slug>/                 # ONE grab-and-copy unit per project
      PROJECT.yaml                  # {slug, name, description, status: active|closed,
                                    #  created, tags[], brain_entity_id}
      INDEX.md                      # generated, newest-first: date | task | status | 1-line result
      closeouts/
        <YYYY-MM>/
          cortex-closeout__<ts>-<slug>__<uuid7>.md
          cortex-closeout__<ts>-<slug>__<uuid7>.json
  audit-log-1..3/                   # legacy shards: FROZEN after migration
    agent/ + TOMBSTONE.md           # "new records land in audit/projects/…; index maps old→new"
  digests/  research-scaffold/      # unchanged
```

- Filenames unchanged (`audit.py:241` — timestamp+slug+uuid7 already
  collision-free and time-sortable).
- Month partitioning replaces the 500-file shard counter as the primary bound
  (`MAX_AUDIT_FILES_PER_SHARD`, `audit.py:21`, is kept as a per-month safety
  valve — a month dir exceeding 500 `.md` rolls to `<YYYY-MM>-b/`).
- **The human contract:** copy `audit/projects/<slug>/` and you hold the
  project's complete audit history, human-readable, with its own index —
  no grep over a global log required. Nothing inside the folder references
  other folders by relative path (cross-links go through the ontology, §3.3),
  so a copied folder is never broken.

### 3.2 Writer changes (extend `audit.py`, cite: `write_closeout` 220–260)

```python
def write_closeout(workspace, task, result, *, project: str = "", ...) -> Path
```

Project resolution order: explicit arg → `intent["project"]` on the driving
engine task → session-declared project (net-new optional arg on
`cortex_register`) → `"unfiled"`. `choose_audit_dir` (71–83) grows a
project-aware branch: when `audit/projects/` exists, route there; otherwise
legacy behavior unchanged (back-compat for un-migrated workspaces — the
scaffold folder of Contract 2 ships `audit/projects/` from day one, so new
projects are always modular). MCP `cortex_write_log` and the engine's
server-side abandonment closeout (`state_engine.py` docstring 33–36) pass
`project` through. `INDEX.md` is regenerated on every write (cheap: one dir
listing + the new record's json).

### 3.3 The living ontology as the cross-project connective tissue (agents)

Extend — do not rebuild — `cortex_core/ontology.py` (§0: append-only JSONL,
bi-temporal, schema-validated, provenance-required).

**Schema additions** (`docs/ontology/schema.yaml`):

- `entity_types`: `project`, `closeout`, `decision` (a decision is the
  distilled, citable output of a closeout — the thing the research-first gate
  looks up).
- `relation_types`:
  `part_of` (closeout|decision → project),
  `produced` (closeout → decision),
  `touches` (closeout → component),
  `informed_by` (closeout → decision|doc — the research-first citation edge,
  written from the SEARCH receipts of the driving task),
  `supersedes` (decision → decision — already the ontology's core move,
  `ontology.py:20–23`).

**Write path:** `write_closeout` appends, in the same call:
`Entity(type="closeout", source_paths=[<md path>], attributes={status, tests,
project})` + `Relation(part_of → project entity)` + one `informed_by` edge per
distinct corpus path cited in the task's search receipts. The project entity
is upserted from `PROJECT.yaml` (its `brain_entity_id`). All records carry
`author_model` and real `source_paths` — the ontology's existing provenance
rule, unchanged.

**Read path:**

- Agents: existing `cortex-ontology` CLI + the MCP ontology query surface
  (read-plane routed, `mcp.py:149–170`) answer "what has any project decided
  about X?" across all projects — the global view humans don't need but
  agents do.
- Scope packs (`cortex_core/packs.py`): net-new expansion step — given a task,
  after retrieval, pull the matched entities' 1-hop neighborhood (project,
  superseding decisions, informed_by sources) into the pack, so the pack
  carries *which decision is current* (the exact question the ontology was
  built to answer, `ontology.py:5–9`).
- The `search_receipts` gate (Contract 1) counts an ontology query as a valid
  SEARCH — decisions are the highest-value research-first target.

**Consistency rule:** the graph is the *index*, the files are the *truth*.
Rebuilding the graph from `audit/projects/**` + `docs/**` must always be
possible (`cortex ontology backfill`, idempotent by `entity_id` =
uuid5(namespace, source path)); a copied-out project folder therefore loses
nothing, and a corrupted graph is a rebuild, not a loss.

### 3.4 Migration of the existing 848 flat files

One-shot, reviewable, reversible:

1. `cortex audit-migrate --plan` (dry run): parse every
   `audit-log-*/agent/*.json` (they carry `task`, `result`, `contract_id`,
   `evidence` — `audit.py:243–250`); classify project via an explicit,
   committed rule table (`migration/project-rules.yaml`: regex over slug +
   evidence paths, e.g. `evals/objective_*` → `eval-lab`, `calibration/` →
   `judge-calibration`, `state_engine|mcp` → `cortex-core`, Hermes slugs →
   `hermes`); everything unmatched → `unfiled`. Emit a full mapping report
   (old path → new path → project, plus the unmatched count) for human review
   **before** anything moves.
2. `cortex audit-migrate --apply`: `git mv` each md/json pair (history
   preserved), write `PROJECT.yaml`/`INDEX.md` per project, drop
   `TOMBSTONE.md` in the legacy shards, backfill ontology entities/relations
   (event `"upsert"`, `author_model` stamped as the migration tool), rebuild
   the search index (the F1(a) rebuild-lock path).
3. Compatibility window: audit readers (`cortex_audit`,
   `closeout_reconcile.py`, search indexing) glob BOTH layouts until the
   legacy shards are empty; a test pins that no reader hardcodes
   `audit-log-1/agent` (grep says several currently glob
   `audit-log-*/agent` — those globs gain the `projects/*/closeouts/*` arm).

Expected mislabel rate is nonzero (**UNCERTAIN**, est. 10–20% land in
`unfiled`); that is acceptable — `unfiled` is honest, and re-filing is a
`git mv` + one ontology edge, cheap forever after.

---

## Contract 4 — The A/B validation protocol

### 4.0 Test project (concrete)

**Install, configure, use, and document `pre-commit`** (production OSS,
pre-commit.com) on a small fresh target repo, wiring two hooks: `ruff` and a
local secret-scan hook (mirrors this repo's real `ops/secret_audit.py`
boundary — a realistic, owner-relevant task). Chosen because every success
criterion is deterministically checkable, it has a real install+config+use+doc
surface, it is small enough for N repetitions, and neither arm's model can
have memorized *this* target repo.

Sizing: one task, ~30–60 min of agent work, repeated N=5 per arm (10 runs).
Target repo: a purpose-made ~15-file Python repo (committed under
`evals/ab_cortex_local/target-repo/`) seeded with (a) one lint violation,
(b) one fake API key in an untracked-history file, (c) a stale README with no
tooling section — so the task has real work and real doc debt.

### 4.1 SDD: the pre-registered spec

`evals/ab_cortex_local/SPEC.md`, committed and frozen **before any run**
(the anti-oracle rule applied to ourselves). Contents: the task prompt
(identical for both arms, verbatim), the success metrics below, the scorer
invocation, N, the model choice with its corpus citation, and the decision
rule. Any post-hoc metric change voids the run (spec carries its own sha256
in each run's output).

Task prompt (both arms, verbatim): *"Set up pre-commit in this repo with two
hooks: ruff (lint) and the provided secret-scan script. Make `pre-commit run
--all-files` pass on the clean tree and block the seeded violations. Update
the project documentation and README so a new contributor can use it. Record
what you did."*

### 4.2 TDD: frozen behavioral tests (M2)

`evals/ab_cortex_local/test_target.py`, written and frozen with the spec:

- T1 `pre-commit --version` exits 0 inside the target repo env.
- T2 after removing seeded violations per the runbook, `pre-commit run
  --all-files` exits 0.
- T3 committing a file containing the seeded fake-key pattern is blocked
  (hook exit ≠ 0).
- T4 committing a file with the seeded ruff violation is blocked.

Subprocess execution, no judge — the 2B objective-coding discipline
(`evals/objective_coding/`) reused.

### 4.3 Metrics (all deterministic; scorer runs identically on both arms)

| # | Metric | Check (script `evals/ab_cortex_local/score.py`) |
|---|---|---|
| M1 | Research-first honored | Evidence of corpus/doc consultation **before the first mutating action**. Common substrate = host transcript + git reflog + `.cortex` artifacts: A-arm additionally has receipts/ledger, but the *scored* signal is transcript-ordering (first Edit/Write/Bash-mutation timestamp vs first search/read-docs event), extracted by one regex/JSON pass applied to both arms. |
| M2 | Task success | T1–T4 pass in the run's worktree. |
| M3 | Docs updated | `docs.map.yaml` for the target repo (frozen in SPEC) satisfied: the mapped doc (`docs/tooling.md`) exists, changed, non-whitespace, contains the two hook names. |
| M4 | README updated | README diff adds a section matching `(?i)^#+.*pre-?commit` and contains the literal invocation `pre-commit run --all-files`. |
| M5 | Closeout written | A closeout record exists (A: valid per `CLOSEOUT.md` schema under `.cortex/audit/projects/…`; B: ANY self-produced task record file is accepted — scored generously toward B, deliberately, so a win on M5 is not definitional). |
| C1 | Cost | wall-clock, total tokens, tool-call count (overhead honesty). |

### 4.4 Arms

- **A (Cortex-local):** target repo + the Contract-2 `.cortex/` folder at L1
  (folder + Claude Code hooks fragment merged) — and, as a sub-arm if budget
  allows, L2 (local `cortex-mcp` attached, strict profile). L1 is the primary
  arm: it is the zero-install shape the owner is buying.
- **B (vanilla):** identical target repo worktree with **no** `.cortex/`, no
  managed CLAUDE.md block, no MCP. Same host harness, same model, same prompt.
- Model: one mid-tier model for both arms (weak/mid models are the population
  the forced pipeline exists for, `mcp.py:74–79`; a frontier model would
  ceiling M2 and mask the discipline delta). **Per the research-first
  pre-flight, the specific model + token budget are chosen by citing
  `docs/MODELS-TIER-LIST.md` + `judge.MIN_MAX_TOKENS_BY_TIER` at spec-freeze
  time, in the SPEC — not named here from memory.**
- Isolation: fresh git worktree per trial; trials alternate A/B; no shared
  state between trials.

### 4.5 Decision rule (pre-registered)

Cortex-local **validates** iff, over N=5 per arm: A ≥ B+2 trials on each of
M1, M3, M4, M5 (discipline metrics), **and** A ≥ B on M2 (must not hurt task
success), **and** A's median token cost ≤ 2× B's. It **fails** if A < B on M2
(the gates broke the work — the loop pathology of `mcp.py:286–297` recurring),
or if A's wins come only from M5 (closeout theater without research/docs).
Anything between = inconclusive → widen N before changing the design. All raw
transcripts, worktrees, and the scorer output are persisted under
`evals/ab_cortex_local/runs/` (the capture-subagent-output rule applied to
the experiment itself).

---

## 5. Risk register / open questions (opinionated, honest)

1. **Riskiest assumption (single biggest):** that the host-harness hook layer
   is a sufficient and stable deterministic interception point for the
   orchestrator's native tools. On Claude Code it is (harness-executed
   PreToolUse/Stop). On Codex it is **unverified**; on arbitrary hosts it
   doesn't exist — there, "cannot skip" honestly degrades to "cannot skip
   *undetected*" (L0). If this assumption fails broadly, the only full remedy
   is the L2 shape everywhere: agents that treat the MCP as their harness
   (Hermes) — which is exactly why Hermes is first.
2. **The gates defaulted OFF for a reason.** `_contract_gate_on` /
   `_admin_gate_on` document that refusal-coercion *manufactures tool-call
   loops* (`mcp.py:286–288, 295–297`). Contract 1 re-arms refusals with three
   mitigations (bounded Stop-hook blocks; refusals that carry `do_instead`;
   receipts that make compliance a one-call act), but whether that is enough
   is an empirical question — it is precisely what Contract 4's M2/C1 measure.
   Do not ship strict-profile defaults before the A/B result.
3. **`docs_current` is gameable by trivial edits.** Accepted, stated in §1.3;
   escalation path (section-anchored map) stays deterministic. Semantic doc
   quality never enters the verdict path.
4. **M1 cross-arm comparability.** B has no receipts, so M1 relies on
   transcript-order extraction for both arms; the extractor must be frozen in
   the SPEC and applied identically, else A is unfairly advantaged. This is
   the A/B design's weakest measurement and is flagged as such in the SPEC.
5. **Migration mislabels.** `unfiled` is the honest default; re-filing is
   cheap. The rule table is committed and reviewable before `--apply`.
6. **Folder/engine drift.** Closed structurally by generation + the
   anti-drift test (§2.5); if `cortex protocol render` is skipped, the test
   fails CI — drift is a build error, not a discovery.
