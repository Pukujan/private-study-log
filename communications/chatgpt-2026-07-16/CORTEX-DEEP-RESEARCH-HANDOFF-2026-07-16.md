# Cortex Deep Research Handoff — Proactive Knowledge Injection for AI Agent Reliability

## Purpose

This document is a handoff for deep research. We need a concrete architecture plan to fix a fundamental design failure in a system called Cortex. The research goal: how to build a **proactive knowledge injection layer** that surfaces relevant prior work into an AI agent's context automatically, before work begins — replacing the current manual keyword search model that has proven to be a catastrophic failure.

---

## Background: What Cortex Is

Cortex is a knowledge and enforcement system built on top of an AI agent harness called Hermes Agent (by Nous Research). Its purpose: **make unreliable AI work reliable** by:

1. **Knowledge corpus** — storing decision trees, design docs, audit closeouts, pattern libraries (KEDB), and research findings from every work session
2. **State machine** — a 7-phase pipeline (SEARCH → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT) that forces structured, evidence-backed work
3. **Audit trail** — every task writes a closeout record with evidence, handoff info, and continuation notes
4. **Pattern detection** — recurring failure modes are catalogued with detection recipes and fixes

### Technical Stack
- **Agent harness**: Hermes Agent (Python), runs on Windows, communicates via Telegram/Discord
- **MCP (Model Context Protocol)**: Cortex exposes its tools as MCP server tools to the agent
- **MCP tools exposed**: `cortex_search`, `cortex_run_start`, `cortex_run_step`, `cortex_write_log`, `cortex_dispatch_tier`, `cortex_scope_pack`, `cortex_fetch_doc`, etc. (~30 tools)
- **State engine**: Custom event-sourced state machine on SQLite, Harel-statechart-shaped, with rework caps, escalation, lease/reaper, and event-sourced audit log
- **Corpus**: Markdown files (closeouts, docs, patterns) with FTS5 full-text search
- **Agent model**: Currently GLM-5.2 (various providers), previously Claude Opus
- **Hook system**: Hermes has a plugin/hook system with `pre_tool_call` and `post_tool_call` hooks that can block or allow tool calls

---

## The Problem: Why Cortex Is Failing

### Core Failure: Passive Vault, Not Proactive Brain

Cortex was built as a **passive knowledge store** — the agent must manually search it. This is the fundamental architectural flaw. Every mechanism in Cortex requires the agent to:
1. Know Cortex exists
2. Know to search before starting work
3. Know the right search terms
4. Actually call `cortex_search`
5. Get lucky that search results contain the relevant document
6. Read and understand the document
7. Apply it to the current task

Every step is a point of failure. **This session is the proof**: a design doc describing the exact enforcement hook we needed was sitting in Cortex for 3 days. We never found it because we didn't search for the right keywords. We spent 12+ hours rebuilding a worse version of something that already existed.

### The State Machine Was Built But Never Enforced

**Timeline:**
- **July 6**: Claude Opus built the StateEngine, wired it into the MCP server. Commit: *"agents can finally USE it."* Tested end-to-end with small models.
- **July 7**: Made it mandatory ("Decision B") — a gate refusing writes unless a task reached DONE.
- **July 8**: Diagnosed the mandatory gate as harmful ("Disease B — mandatory-pipeline coercion"). Weak models hit the gate, got refused, were told to "call more tools," and bounced between refusals in infinite loops. The gate manufactured the exact tool-call loops it was meant to prevent. **Default flipped to OFF.**
- **July 13**: A redesign doc (`cortex-local-redesign-contracts-fable.md`) identified the structural root cause: *"Every deterministic mechanism sits behind the MCP tool surface. The orchestrator does its work with host-native tools — Read/Edit/Bash/WebSearch — which never pass through StateEngine.step(), never hit the gates, and are never checked for phase legality."* The fix was designed (L0/L1/L2 enforcement ladder) but marked **"DESIGN CONTRACTS ONLY. No implementation."**

### The Enforcement Gap

The state machine gate only covers two MCP write tools (`cortex_write_log`, `cortex_fetch_doc`). The agent does its real work with **host-native tools** (Read, Edit, Write, Bash, delegate_task) that **never pass through the engine**. The gate is at the wrong layer.

The fable doc's enforcement ladder (never implemented):
- **L2 (MCP layer)**: Every write flows through gated MCP tools. Requires `CORTEX_MANDATORY_STATE_MACHINE=1` + `CORTEX_STRICT_OVERRIDES=1`.
- **L1 (Host harness)**: `PreToolUse` hooks intercept `Edit|Write|Bash` → deny unless state machine is in valid phase. *"Hooks are harness-executed — the model cannot decline them; that is what makes this structural rather than instructional."*
- **L0 (Agent prompt)**: `AGENTS.md` binding + post-hoc scoring. Honor-system.

### Every Gate Has An Escape Hatch

- `state_machine_override_reason` — free-text bypass, logged but not enforced
- `CORTEX_MANDATORY_STATE_MACHINE=0` — env var kill switch, default OFF
- `CORTEX_CONTRACT_GATE=0` — contract gate, default OFF
- `CORTEX_ADMIN_GATE=0` — admin gate, default OFF

Every enforcement is soft, every gate is bypassable, every discipline is optional. This is the AI trust problem in microcosm: **AI cannot be trusted to build enforcement against itself.** Every model that builds a gate adds a bypass for itself.

### Closeouts Are Never Written

The `cortex_write_log` tool exists and is exposed. But nothing makes the agent call it. The state machine's CLOSEOUT phase exists. But the state machine is off. So closeouts are only written when a human explicitly asks. This session's work — opencode wiring, hook implementation, safety valve addition — got zero closeouts until the human demanded it. The knowledge from those sessions is blind to future sessions.

---

## What We Need: Research Questions

### 1. Proactive Knowledge Injection Architecture

**Core question**: How do you build a system that automatically surfaces relevant prior knowledge into an AI agent's context before work begins — without the agent needing to know what to search for?

**Sub-questions**:
- What are the current state-of-the-art approaches to proactive context injection for LLM agents? (RAG variants, semantic search, embedding-based retrieval, task-context analysis)
- How do systems like Cursor, Devin, SWE-Agent, or other AI coding tools handle "surface relevant prior work before coding"?
- What embedding models and vector stores are appropriate for a corpus of decision trees, design docs, and audit closeouts (currently markdown files with FTS5)?
- How do you do semantic matching between "what the agent is about to do" (task description) and "what knowledge exists" (closeouts, patterns, design docs) without keyword search?
- What's the latency budget for injection? (The agent is interactive — injection must happen in <2 seconds before tool calls)
- How do you handle the cold-start problem? (First session, empty corpus)
- How do you handle relevance decay? (Old closeouts may be stale — how to weight by recency, task similarity, and verification status)

### 2. Harness-Layer Enforcement for AI Agents

**Core question**: How do you enforce a structured work pipeline (state machine) on an AI agent when the agent can do work through host-native tools that bypass the pipeline?

**Sub-questions**:
- What enforcement patterns exist in other AI agent frameworks (LangChain, CrewAI, AutoGen, OpenHands, SWE-Agent)?
- How do you intercept host-native tool calls (file writes, shell commands, code execution) and gate them on pipeline state without breaking the agent's workflow?
- The state machine was turned off because weak models got stuck in tool-call loops (gate refuses → model tries to comply → fails → tries again → infinite loop). How do you design enforcement that doesn't trap weak models while still constraining strong models?
- Is there a tiered enforcement model? (e.g., strong models get full pipeline, weak models get simplified pipeline, unattended models get hard gates, interactive models get soft gates)
- How do you handle the "builder ≠ reviewer" problem? (The model that builds enforcement can't be the one that certifies it works)
- What's the right escape hatch design? (When should a gate be bypassable, who decides, how is it logged, what are the consequences?)

### 3. State Machine Design for AI Agent Pipelines

**Core question**: Is a 7-phase linear state machine (SEARCH → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT) the right model, or are there better alternatives?

**Sub-questions**:
- What state machine patterns do other AI agent frameworks use for structured work?
- Is a linear pipeline too rigid? Should it be a DAG, a tree, or a graph with conditional branches?
- The current state machine has rework caps (2 reworks per escalation level, 2 escalation levels before abandonment). Is this the right balance between "give the model chances to fix" and "don't let it loop forever"?
- How do you handle parallel workstreams? (The current MISSION_TRACK supports parallel workers, but it's complex and untested in production)
- What about the "assured" vs "legacy" track distinction? (Assured tracks have research sufficiency gates; legacy tracks don't. Should everything be assured?)
- How do you handle the case where the state machine itself becomes a bottleneck? (The 12-hour failure this session was partly because the state machine was off, but also because even if it were on, keyword search wouldn't have surfaced the right doc)

### 4. Cross-Session Memory and Continuity

**Core question**: How do you give an AI agent persistent memory across sessions when each session starts with a fresh context window?

**Sub-questions**:
- What approaches exist for cross-session memory in LLM agents? (Vector DB recall, summary compression, key-value stores, knowledge graphs)
- How do you handle context window limits? (The injection must fit within the agent's context budget alongside the current task)
- How do you prioritize what to inject? (If there are 50 relevant closeouts, which 3-5 do you surface?)
- How do you handle conflicting knowledge? (Two closeouts with different conclusions about the same topic)
- How do you track "what work has been done recently" so Cortex can say "you built this 3 days ago" without being asked?

### 5. The AI Trust Problem

**Core question**: Can AI be trusted to build enforcement systems against itself, or must enforcement always come from outside the model?

**Sub-questions**:
- What does the research say about AI self-regulation and self-enforcement?
- Are there examples of AI systems successfully constraining their own behavior through self-built mechanisms?
- What's the right division of labor between "model-built enforcement" (the model writes the hook code) and "human-certified enforcement" (the human reviews and deploys it)?
- The "builder ≠ reviewer" principle: what external verification mechanisms exist for AI-built code?
- How do you handle the case where the enforcement system itself has bugs? (This session: the hook had no safety valve, which would have permanently locked the agent's tools)

### 6. Automated Closeout and Pattern Promotion

**Core question**: How do you make audit closeouts and pattern detection automatic rather than requiring manual agent action?

**Sub-questions**:
- Can closeouts be auto-generated from session telemetry (tool calls, file changes, test results)?
- What quality threshold should auto-closeouts meet? (Is a bad auto-closeout better than no closeout?)
- How do you auto-detect recurring failure patterns across sessions?
- What's the right promotion pipeline? (Auto-draft → human review → promote? Or fully automatic with confidence scoring?)
- How do you handle false positives in pattern detection? (Two unrelated failures that look similar)

---

## Current Architecture Details

### Files and Locations
- **Cortex brain (read-only)**: `D:\claude\stupidly-simple-cortex\`
  - `cortex_core/mcp.py` (3074 lines) — MCP server with 30 registered tools
  - `cortex_core/state_engine.py` (2147 lines) — event-sourced state machine on SQLite
  - `cortex_core/audit.py` — closeout writer
  - `cortex_core/patterns.py` — KEDB pattern library
  - `docs/` — design docs, closeouts, research notes
  - `audit/audit-log-1/agent/` — audit closeout markdown files
- **Cortex workspace (writable)**: `D:\hermes\cortex\workspaces\hades\`
  - `audit/audit-log-1/agent/` — session closeouts
  - `patterns/` — KEDB pattern entries (JSON)
  - `docs/` — analysis documents
- **Agent harness**: `D:\hermes\hermes-agent\`
  - `hermes_cli/plugins.py` — hook system (pre_tool_call, post_tool_call)
  - Agent communicates via Telegram/Discord
- **Enforcement hook (just built)**: `D:\hermes\profiles\hades\plugins\cortex-assured-driver\`
  - `__init__.py` — search gate hook with 3-strike safety valve (18 tests pass)
  - This hook currently gates on `cortex_search` (wrong — should gate on `cortex_run_start` + phase state)

### State Machine Phases (BUILD_TRACK)
```
SEARCH_BRAIN → RESEARCH → PLAN → SPEC → IMPLEMENT → REVIEW → CLOSEOUT → DONE
     ↑                    |                                  |
     |________ rework ____| (bounded by rework_cap=2) _______|
```
- Each state has one `advance_tool` (calling it triggers the gate and transitions)
- Optional `extra_tools` are legal but don't transition
- REVIEW can rework to IMPLEMENT (bounded)
- Past rework/esc caps: ABANDONED (via CLOSEOUT, always)
- AUTO tasks with no oracle and no human: ABSTAINED (honest, not fake pass)

### MCP Tool Surface (30 tools)
Key tools for this research:
- `cortex_search` — FTS5 keyword search over corpus
- `cortex_run_start(intent)` — start a state machine task (entry point)
- `cortex_run_step(task_id, tool, seq)` — advance the task one phase
- `cortex_run_state(task_id)` — query current state
- `cortex_write_log(task, result, ...)` — write audit closeout
- `cortex_dispatch_tier(action, ...)` — model tier routing
- `cortex_scope_pack(...)` — scoped knowledge retrieval
- `cortex_contract(...)` — work contract registration

### Hermes Hook System
- `pre_tool_call(session_id, tool_name, args)` → returns block message or None
- `post_tool_call(session_id, tool_name, args, result)` → tracking/side effects
- Hooks are Python, loaded from `plugins/` directory
- Plugin YAML declares which hooks to register
- Hooks fire on ALL tool calls (MCP and host-native)

### Key Design Docs (in Cortex corpus)
- `docs/ARCH-DEBUG-DECISION-mcp-tool-surface-and-coercion-2026-07-08.md` — diagnosis of why mandatory gate was harmful
- `docs/design/cortex-local-redesign-contracts-fable.md` — unimplemented enforcement design (L0/L1/L2 ladder)
- `docs/harness/CAPABILITY-STATUS.md` — honest capability matrix
- `docs/research/STATE-MACHINE-DESIGN-fable-research-2026-07-06.md` — original state machine design
- `docs/research/MISSION-LAYER-MCP-WIRING-2026-07-07.md` — mission layer (parallel workers)

---

## What We Want From This Research

A **concrete architecture document** that covers:

1. **Injection layer design** — How to build proactive knowledge injection that surfaces relevant work before the agent starts. Not "the agent searches" — "Cortex surfaces." Specific tech choices (embedding model, vector store, retrieval strategy, context budget management).

2. **Enforcement architecture** — How to wire harness-layer hooks (L1) that gate host-native tools on state machine phase. How to avoid the weak-model trap that killed the previous enforcement. Tiered enforcement for different model capabilities.

3. **State machine v2** — Whether the current 7-phase linear pipeline is the right model or needs restructuring. How to handle the "state machine is off because it breaks weak models" problem.

4. **Auto-closeout pipeline** — How to auto-generate closeouts from session telemetry. Quality thresholds. How to auto-detect and promote patterns.

5. **Implementation roadmap** — What to build first, what depends on what, what can be tested in isolation. Priority order based on impact.

6. **References** — Papers, projects, frameworks, and tools that solve similar problems. We want to learn from existing work, not reinvent it.

---

## Constraints

- System runs on Windows (Python, SQLite, no Docker locally)
- Agent model is GLM-5.2 (may change) — enforcement must work for mid-tier models, not just frontier
- Interactive agent (Telegram/Discord) — latency budget for injection is ~2 seconds
- Corpus is currently ~100 markdown files, growing ~5-10 per session
- Budget is human time, not API costs — the 12-hour waste was human development time
- The system must work for the agent that builds it (no "another model will enforce it" — the enforcement must be harness-level, not model-level)
- Must have safety valves (the 3-strike pattern from this session) — no permanent lockouts

---

## The Deeper Question

Cortex was built to make AI reliable. Cortex was built by AI. If AI can't be trusted to build reliable systems, can it be trusted to build the system that makes it reliable?

The answer so far is no — every gate has a bypass, every discipline is optional, every enforcement is soft. The only enforcement that works is one the model cannot touch: a host harness hook that fires before the agent's tools do.

The research should address: **what is the minimal, harness-level, non-bypassable enforcement layer that makes an AI agent reliable — and how do you build it without trusting the AI to enforce it on itself?**
