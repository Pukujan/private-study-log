# Hades OS Project Proposal

## MVP → V3 Roadmap

## Core Product Thesis

**Hades OS is an agent workflow control plane.**

It should not start as a full coding agent, full OS, or full automation platform.

It should start as a simple authenticated workspace where a user can:

```txt
chat
save tasks
create tools/prompts
run manual automations
track results
```

Then it grows into:

```txt
a modular operating layer for agents, workflows, coding tasks, approvals, context routing, and automation unlocks
```

Your modular monolith architecture becomes the enforcement system that keeps the app stable as agents add features.

---

# Product Direction

## What Hades OS is

```txt
A control console for managing AI workflows, prompts, tools, tasks, and eventually coding-agent execution.
```

## What Hades OS is not in MVP

```txt
Not a full autonomous OS
Not a Codex clone
Not a browser IDE
Not a multi-agent swarm platform yet
Not a gamified RPG workflow system yet
Not a marketplace
Not a complex worker scheduler
```

The OS feeling should emerge later through structure, not be forced in v1.

---

# Architecture Principle

Hades OS should be built as a **modular monolith first**.

Each feature should live inside a strict module boundary:

```txt
auth
chat
tools
automations
task-runs
logs
settings
```

Each module should include:

```txt
module contract
API routes
service layer
UI components
types/models
tests
agent rules
```

The purpose is not just clean code.

The purpose is to make Hades agent-codeable without chaos.

---

# MVP: Hades OS v1

## Goal

Prove the core product loop:

```txt
login
→ chat
→ create reusable tool/prompt
→ save manual automation
→ run manual task
→ view result/log
```

## MVP Scope

### 1. Auth

Use Supabase Auth.

Required:

```txt
login
logout
session check
protected app route
user profile basics
```

No custom auth system.

---

### 2. Simple Chat

A clean chat interface where the user can talk to Hades.

MVP chat does not need complex memory, tool calling, or agent routing yet.

Required:

```txt
send message
receive assistant response
store chat messages
show loading/error states
```

---

### 3. Tool Creator

A tool is a reusable prompt/instruction block.

Required fields:

```ts
type Tool = {
  id: string
  userId: string
  name: string
  description: string
  instructions: string
  mode: "manual"
  createdAt: string
  updatedAt: string
}
```

MVP tools are manual only.

No plugin system yet.

---

### 4. Manual Automation Management

An automation is a saved task/prompt workflow.

Required fields:

```ts
type Automation = {
  id: string
  userId: string
  title: string
  prompt: string
  mode: "manual"
  status: "saved" | "running" | "completed" | "failed"
  createdAt: string
  updatedAt: string
}
```

Required actions:

```txt
create automation
view automations
edit automation
delete automation
run automation manually
view result
```

---

### 5. Task Runs / Logs

Every manual run should create a task run record.

```ts
type TaskRun = {
  id: string
  automationId: string
  userId: string
  input: string | null
  output: string | null
  status: "running" | "completed" | "failed"
  error: string | null
  startedAt: string
  completedAt: string | null
}
```

This is important because later auto mode will reuse the same run system.

---

## MVP Modules

```txt
src/modules/auth
src/modules/chat
src/modules/tools
src/modules/automations
src/modules/task-runs
src/modules/shared
```

## MVP Completion Criteria

MVP is complete only when:

```txt
1. User can login
2. User can chat
3. User can create a tool
4. User can create a manual automation
5. User can run a manual automation
6. User can see the output/log
7. All completed flows use real backend data
8. No mock-only completed flows remain
```

---

# V1.5: Stability + Contract Enforcement Layer

## Goal

Make Hades safe for agent-assisted development.

This is where your modular monolith matters heavily.

## Add

### 1. Module Registry

A registry that defines what each module owns.

```ts
type ModuleRegistryEntry = {
  name: string
  owns: string[]
  allowedRoutes: string[]
  serviceFile: string
  contractFile: string
  testFiles: string[]
}
```

Example:

```txt
automation-management owns:
- src/modules/automations/**
- src/services/automationApi.ts
- backend/modules/automations/**
```

---

### 2. Agent Task Packet Generator

Instead of sending huge contracts to agents, Hades generates small task packets.

```txt
user request
→ classify module
→ pull relevant module contract
→ generate short task packet
→ send to coding worker
```

This solves your context consumption issue.

---

### 3. No-Drift Rules

Every task packet should include:

```txt
target module
allowed files
forbidden files
API shape
UI shape
service-layer rule
completion proof
```

This turns your architecture into an enforcement system.

---

### 4. Browser-Proof Acceptance

A feature is not done unless:

```txt
existing UI
→ service layer
→ backend route
→ real response
→ UI updates
```

No backend-only “done.”

---

# V2: Coding Workflow Control

## Goal

Hades becomes useful for managing coding-agent work.

Not by replacing Codex/OpenCode/Claude Code, but by controlling them.

## Add

### 1. Coding Task Manager

Saved coding tasks:

```ts
type CodingTask = {
  id: string
  title: string
  targetRepo: string
  targetModule: string
  taskPacket: string
  status: "draft" | "queued" | "running" | "review" | "approved" | "failed"
  workerType: "manual" | "opencode" | "codex" | "claude_code" | "custom"
  createdAt: string
  updatedAt: string
}
```

---

### 2. Worker Routing

Hades can prepare tasks for:

```txt
OpenCode
Codex
Claude Code
self-hosted Qwen agent
manual copy-paste handoff
```

V2 does not need perfect direct integration with every worker.

It can start with:

```txt
generate task packet
copy/export prompt
save result/log
manual approval
```

Then later direct execution can come in.

---

### 3. Repo/Module Context

Hades stores:

```txt
repo name
module list
module contracts
service files
test commands
allowed edit scopes
```

This is where your modular monolith becomes the backbone.

---

### 4. Review + Approval Flow

Every coding task should have:

```txt
generated task packet
worker output
diff summary
test result
contract drift checklist
manual approve/reject
```

---

# V3: Hades OS Proper

## Goal

This is where the “OS” feeling becomes real.

V3 is not just task management. It becomes a workflow system that teaches, unlocks, and automates over time.

## Add

### 1. Onboarding Phases

Instead of gaming too hard, use onboarding as structured progression.

Example phases:

```txt
Phase 1: Manual workspace
Phase 2: Saved prompts/tools
Phase 3: Manual automations
Phase 4: Coding task packets
Phase 5: Worker routing
Phase 6: Semi-auto workflows
Phase 7: Auto workflows with approvals
```

This gives the “level up” feeling without making it childish.

---

### 2. Unlock System

Users unlock more automation only after safer behavior is proven.

Example:

```txt
manual task runs 10 times successfully
→ unlock scheduled draft mode

contract tests pass repeatedly
→ unlock worker routing

approval history stable
→ unlock semi-auto execution
```

This is actually a strong safety/product idea.

Unlocks are based on reliability, not game points.

---

### 3. Auto Mode

Auto mode can include:

```txt
scheduled tasks
conditional tasks
repo checks
daily summaries
watchers
notification triggers
```

But auto mode should always include:

```txt
approval controls
logs
pause switch
failure handling
permission scopes
```

---

### 4. Forge Language / Workflow DSL

The Forge language should wait until V3.

It should not be a programming language first.

It should start as a readable workflow syntax:

```txt
WHEN new issue is created
USE repo_triage_tool
CREATE coding_task
REQUIRE approval
SEND to OpenCode
LOG result
```

The purpose is to let users describe workflows clearly.

---

### 5. Theme + Design System

The design system and on-the-fly themes can become part of V3 identity.

But in MVP, only basic theme support is needed.

V3 can include:

```txt
workspace themes
module themes
workflow states
status-based visuals
user-customizable console
```

---

# Recommended Build Order

## Phase 0 — Foundation

```txt
Set up modular monolith structure
Set up Supabase Auth
Set up database tables
Set up module contracts
Set up service layer pattern
Set up no-direct-fetch rule
```

## Phase 1 — MVP Vertical Slices

```txt
Slice 1: Auth works from UI
Slice 2: Chat works from UI
Slice 3: Tool creator works from UI
Slice 4: Manual automation CRUD works from UI
Slice 5: Run manual automation and show logs
```

## Phase 2 — Contract Enforcement

```txt
Module registry
Agent task packet format
Allowed-file scope
Contract drift checklist
Acceptance test format
```

## Phase 3 — Coding Task Layer

```txt
Create coding task
Generate bounded handoff packet
Export to OpenCode/Codex/Claude Code
Track result manually
Approve/reject result
```

## Phase 4 — Worker Integration

```txt
OpenCode integration first
Then Codex/Claude Code handoff support
Then optional self-hosted worker
```

## Phase 5 — V3 OS Layer

```txt
Onboarding phases
Unlock system
Auto mode
Workflow DSL
Advanced themes
Multi-worker routing
```

---

# Core Rule for the Whole Project

Do not build Hades horizontally.

Do not do:

```txt
all backend
all frontend
all auth
all automation
then connect
```

Build vertical slices:

```txt
existing UI
→ contract
→ service layer
→ backend
→ real response
→ UI update
→ test/proof
```

---

# Project Summary

## MVP

```txt
Authenticated chat + tools + manual automations + logs
```

## V1.5

```txt
Module contracts + task packets + no-drift enforcement
```

## V2

```txt
Coding task manager + worker handoff + approval system
```

## V3

```txt
Onboarding phases + unlockable automation + Forge workflow language + OS-like control layer
```

Best one-line proposal:

> **Hades OS starts as a simple authenticated AI workflow console and grows into a modular control plane for prompts, tools, automations, coding tasks, workers, approvals, and eventually semi-autonomous workflows.**
