# Cortex research path — end-to-end flowchart (2026-07-19, v2 mobile)

**Audience:** owner (phone + laptop)  
**Diagram standard:** `WORK-METHODOLOGIES.md` **M27** (vertical / mobile-first; dual channel)  
**HTML (best on phone):** [cortex-research-path-flowchart-2026-07-19.html](./cortex-research-path-flowchart-2026-07-19.html)

All Mermaid below is **`flowchart TB`** (top→bottom). Each section has a text step list first.

---

## 0. One-line model

```text
Cortex  = local memory + fetch of REGISTERED URLs + cite/sufficiency gates
Driver  = web discovery + tools + mutation
Bare llm_complete = judge over a pack (NOT research)
fanout (this repo) = multi-model BUILD (NOT web-research fanout)
```

---

## 1. Target path (contract)

**Steps:**
1. User request  
2. Brain recall  
3. Tenant / local recall  
4. Coverage + freshness + risk → sufficient **or** driver web  
5. register_source  
6. bounded_fetch + SHA-256  
7. Cited local report  
8. Freeze research brief → freeze contracts  
9. Execute → eval / human → KEDB  

```mermaid
flowchart TB
  U["1 · User request"] --> B["2 · Brain recall"]
  B --> L["3 · Tenant / local recall"]
  L --> D{"4 · Coverage + risk?"}
  D -->|sufficient| FR["8a · Freeze research brief"]
  D -->|insufficient| W["5 · Driver web discovery"]
  W --> R["6 · register_source"]
  R --> F["7 · fetch + SHA-256"]
  F --> RP["Cited local report"]
  RP --> FR
  FR --> FC["8b · Freeze contracts"]
  FC --> X["9 · Execute"]
  X --> E["Hidden eval / human"]
  E --> K["KEDB / promotion"]
```

---

## 2. What cortex_research does

**Steps:** question → optional frame → load registry → select sources → fetch registered only → local gather → cite_check → needs_sources? → optional summarize → write_report  

```mermaid
flowchart TB
  Q["1 · question"] --> FR{"2 · do_frame?"}
  FR -->|yes| H1["Haiku → sub-questions"]
  FR -->|no| SQ["sub_questions = question"]
  H1 --> REG["3 · load sources.yaml"]
  SQ --> REG
  REG --> SEL["4 · select_sources"]
  SEL --> BF{"5 · do_fetch?"}
  BF -->|yes| GET["fetch REGISTERED urls only"]
  BF -->|no| EV["6 · gather_evidence LOCAL"]
  GET --> EV
  EV --> CC["7 · cite_check"]
  CC --> GAP["8 · needs_sources?"]
  GAP --> SUM{"9 · summarize?"}
  SUM -->|yes| H2["Haiku summarize"]
  SUM -->|no| WR["10 · write_report"]
  H2 --> WR
```

---

## 3. Driver + Cortex + sufficiency

**Steps:** user → search → research → needs_sources? → (web → register → re-run) **or** policy → independent → human → receipt  

```mermaid
flowchart TB
  U["User question"] --> CS["search / scope_pack"]
  CS --> CR["cortex_research"]
  CR --> NS{"needs_sources?"}
  NS -->|yes| W["Driver web tools"]
  W --> REG["register_source"]
  REG --> CR
  NS -->|no| POL["Policy floor"]
  POL --> IND["Independent review"]
  IND --> HUM["Human high-risk"]
  HUM --> REC["SUFFICIENT / UNRESOLVED / ABSTAIN"]
```

---

## 4. Bare panel vs real research

### A — Bare llm_complete (NOT research)

```mermaid
flowchart TB
  O1["Orchestrator packs corpus"] --> M1["Sol / Grok — no tools"]
  M1 --> A1["Answer over pack only"]
```

### B — Real Cortex research

```mermaid
flowchart TB
  O2["Tool-capable driver"] --> L2["Local search"]
  L2 --> G2["needs_sources?"]
  G2 --> W2["Web discovery"]
  W2 --> R2["register + fetch + re-run"]
  R2 --> S2["Sufficiency receipt"]
```

---

## 5. Improvements ranked

| # | Improvement | Fixes |
|---|-------------|--------|
| 1 | Tool-loop researchers | Agents validate outside the pack |
| 2 | Mandatory external leg | Stops corpus-only theater |
| 3 | Join driver web → task_id | Audit trail |
| 4 | Multi-agent research fanout | ≠ build fanout |
| 5 | Non-Claude summarizer | Anti-circularity |
| 6 | Source diversity gates | Counts ≠ corroboration |
| 7 | assured_research on OpenCode | Researched = receipt |

---

## Methodology

- **M27** owner-legible diagrams (vertical / mobile-first) — `docs/methodology/WORK-METHODOLOGIES.md`
- **Research-only draft contract** — `docs/design/DRAFT-CONTRACT-research-only-path-2026-07-19.md`

*v2 rebuild 2026-07-19: all charts TB; dual text+diagram; phone column.*
