# Freeze bootstrap write-lanes + v2 schemas — packaging decisions (study log)

**Type:** Owner decision log / packaging note (not a journal paper)  
**Date:** 2026-07-20  
**Status:** current (unit owner-frozen; M3 build not started)  
**Local mirror:** `research/study-log/2026-07-20-freeze-bootstrap-lanes-packaging.md`  
**Publish target:** `Pukujan/private-study-log` → `cortex/`  
**Companion study:** `research/study-log/2026-07-20-mechanical-gates-vs-prompt-discipline.md`  
**Parent freeze:** `wu_20260720_owner-freeze-wall-v2` (hash lock must stay unchanged)  
**This unit:** `wu_20260720_freeze-bootstrap-lane-v2` (owner freeze act via `python -m cortex_core.work_unit_freeze freeze … --i-am-owner`)  
**Ritual (this note):** `pack_hash:7d73a1e7fcbfc6a1` / `ritual_stamp:d9de7c2fdab13ef7`  
**Recorded also in:** `docs/design/AMENDMENTS-PENDING-2026-07-18.md` § Owner decisions 2026-07-20 [C5]

---

## 1. Why this note exists

The interim owner-freeze wall (`cortex_core/work_unit_freeze.py`) was built default-OFF [C9][C10].
Rebuilding the wall to be usable for real work requires **bootstrap write lanes** (path ALLOWs while the wall is ON) and **stricter freeze/M24 schemas** — otherwise agents cannot draft freezes, install schemas, or write study/research under DENY-by-default mutate.

This log freezes the **packaging decisions** so later sessions do not re-litigate scope.

---

## 2. Owner packaging decisions (authoritative)

| Decision | Choice | Rationale (short) |
|----------|--------|-------------------|
| Unit package | **Lanes + schemas only** | Smallest kernel-tier unit that unblocks wall-on work; long-job is separable |
| Long-job recovery | **NEXT unit** after this ships | Draft: `docs/research/OPENCODE-LONG-JOB-RECOVERY-SUBCONTRACT-DRAFT-2026-07-20.md` |
| Long-job v0 shape | **Option A** file job record + independent watchdog | OpenCode has no durable job bus; keep recovery out-of-process |
| Long-job first supervise | **External CLI only** | Not OpenCode Task subagents in v0 |
| `docs/design/` as lane? | **No** (keep-out) | Design surface is high-impact; not bootstrap scratch |
| Wall enable | Stay **default OFF** until P1–P3 pilot green | Avoid bricking sessions (study F10) |
| Risk on this unit | **kernel** + `no_v0: true` | Wall rebuild stays kernel-tier |

---

## 3. Bootstrap write lanes (intended; build pending)

Write/edit only (never Task). Exact freeze-CLI bash exempt: `python -m cortex_core.work_unit_freeze …`.

| Lane id | Paths (intent) |
|---------|----------------|
| `freeze_drafts` | `project-state/work-unit-freezes/drafts/` |
| `freeze_store` | `project-state/work-unit-freezes/` (machine twins) |
| `research_scratch` | research scratch as frozen |
| `research_durable` | `research/study-log/`, `docs/cortex-1/`, `docs/cortex-2/`, `docs/research/`, `inbox/` |
| `handoff_root` | exact `HANDOFF.md`, `LATEST-CLOSEOUT.md` |
| `audit` | `audit/`, `closeouts/` |
| `schemas_install` | `schemas/` |

**Not a lane:** `docs/design/` (owner accepted keep-out after explanation).

**Not the same as** MCP `_FORCED_PIPELINE_STEPS` in `cortex_core/mcp.py` — that is docs-before-write order for MCP sessions; bootstrap lanes are freeze-wall path ALLOWs. Different axes [C5].

Gate surface: `check_mutate_allowed` in `work_unit_freeze.py`; plugin mirror `cortex-forced-ritual.ts`.

---

## 4. Schemas in this unit (draft → install)

1. `schemas/work-unit-freeze-contract.schema.json` — v2 completeness (thin `REQUIRED_FIELDS` today is insufficient).  
2. `schemas/owner-question-round.schema.json` — M24 Q1–Q5 / A1–A4 mint.

DRAFTs already under `project-state/work-unit-freezes/drafts/`. Install under `schemas/` is part of the frozen unit’s done-when, not free-form design edits.

---

## 5. Methodology stack (unit)

Primary **M3**. Stack: M1 → M25 → M2 → M3 → M4 → M14 → M5 (if design fork) → M7 → M24/M26.

---

## 6. Status as of 2026-07-20 (session)

| Item | State |
|------|--------|
| Draft package (JSON + SUBCONTRACT + schema DRAFTs) | done |
| Owner freeze act | **done** (`wu_20260720_freeze-bootstrap-lane-v2`) |
| M3 lanes + schema install + tests | **not started** |
| Long-job research draft update + GitHub push | **pending** (interrupted) |
| Permanent wall env enable | **blocked** on pilot |

---

## 7. Honest non-claims

- This note is **not** a freeze receipt; the machine twin under `project-state/work-unit-freezes/` is.  
- Lanes listed above are **intent from owner Q&A + freeze package**; code may lag until M3 lands.  
- Private-study-log remote may still be **public despite the name** (FILE-MANAGEMENT-CONTRACT) — treat content as potentially exposed.  
- OpenCode defaults still lack Temporal-class job recovery (see long-job research draft §1).

---

## 8. Next mechanical steps

1. M3: implement lanes + install schemas + tests (`test_work_unit_freeze_bootstrap_lane.py`, `test_work_unit_freeze_schema.py`) + v1 regression.  
2. Process-scoped wall ON pilot; M7; only then owner permanent enable.  
3. Freeze + build long-job unit (Option A, external CLI only).  
4. Publish this file to `private-study-log/cortex/` when owner runs publish pass; refresh local MANIFEST via `python -m cortex_core.study_log_mgmt`.

---

## 9. Citations (forced-RAG)

- [C5] `docs/design/AMENDMENTS-PENDING-2026-07-18.md:172` — packaging decisions text.  
- [C9][C10] closeout interim wall + study log push.  
- FILE-MANAGEMENT-CONTRACT R1/R5/R7 — date-prefixed cortex study log + MANIFEST.  
- Parent isomorphism: SCC v2 plan-freeze + DENY authorize [C1][C2] (direction only; interim wall is not full kernel).
