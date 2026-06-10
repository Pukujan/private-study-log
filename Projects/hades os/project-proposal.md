# Hades OS Project Proposal + Technical Proposal

## MVP to V3 Roadmap

## Table of Contents

* [1. Project Thesis](#1-project-thesis)
* [2. Relationship to the Modular Monolith](#2-relationship-to-the-modular-monolith)
* [3. Hades Behavior Layer](#3-hades-behavior-layer)
* [4. Product + Technical Roadmap](#4-product--technical-roadmap)

  * [4.1 MVP / V1 — Manual Hades Console](#41-mvp--v1--manual-hades-console)
  * [4.2 V1.5 — Modular Monolith Addon Integration](#42-v15--modular-monolith-addon-integration)
  * [4.3 V2 — Coding Workflow Control](#43-v2--coding-workflow-control)
  * [4.4 V3 — Hades OS Proper](#44-v3--hades-os-proper)
* [5. MVP Vertical Build Slices](#5-mvp-vertical-build-slices)

  * [5.1 Auth Slice](#51-auth-slice)
  * [5.2 Chat Slice](#52-chat-slice)
  * [5.3 Tool Creator Slice](#53-tool-creator-slice)
  * [5.4 Manual Automations Slice](#54-manual-automations-slice)
  * [5.5 Task Runs / Logs Slice](#55-task-runs--logs-slice)
  * [5.6 GitHub Ticket Resolver Slice](#56-github-ticket-resolver-slice)
* [6. MVP Definition of Done](#6-mvp-definition-of-done)
* [7. Final Roadmap Summary](#7-final-roadmap-summary)

---

# 1. Project Thesis

Hades OS is an **agent workflow control plane**.

It starts as a simple authenticated workspace for chat, tools, manual automations, task logs, and GitHub issue task-packet generation.

Over time, it grows into an operating layer for managing prompts, workflows, coding tasks, workers, approvals, automation behavior, and agent execution.

The key idea:

```txt
Hades OS is not the coding worker itself first.
Hades OS is the command center around agents, tools, tasks, and workflow control.
```

Best one-line framing:

```txt
Hades OS starts as a simple authenticated AI workflow console and grows into a modular control plane for prompts, tools, automations, GitHub issues, coding tasks, workers, approvals, behavior rules, and eventually semi-autonomous workflows.
```

---

# 2. Relationship to the Modular Monolith

The modular monolith is not just the codebase structure.

It is the enforcement system.

The repo already handles or will handle:

```txt
module boundaries
addon rules
coding-agent constraints
allowed file scopes
no-drift behavior
task packet discipline
development-time enforcement
```

Hades OS should not recreate that in MVP.

Instead:

```txt
Hades OS consumes and operates on the existing modular-monolith addon system.
```

The distinction:

```txt
Repo addon system
= controls how coding agents work inside the repo

Hades behavior system
= controls how Hades behaves as a workflow OS
```

For MVP, the repo addon system is assumed to exist inside the codebase.

For later versions, Hades can add its own behavior layer for runtime decisions, approvals, routing, and automation safety.

---

# 3. Hades Behavior Layer

The Hades behavior layer is not the same as `AGENTS.md`.

`AGENTS.md` tells coding agents:

```txt
how to work inside the repo
```

Hades behavior rules tell Hades:

```txt
how to operate as a workflow system
```

This becomes useful later for controlling:

```txt
when Hades asks for approval
when Hades creates a saved automation
when Hades routes work to OpenCode, Codex, Claude Code, or another worker
when Hades refuses auto mode
how Hades handles failed task runs
how Hades summarizes logs
how Hades decides manual vs semi-auto vs auto
how Hades scopes GitHub tickets
how Hades prevents task drift
```

Potential later structure:

```txt
hades-behavior/
  routing-rules.md
  approval-rules.md
  automation-rules.md
  github-ticket-rules.md
  failure-handling.md
  worker-selection.md
```

For MVP, keep this lightweight.

MVP behavior rules:

```txt
1. Hades defaults to manual mode.
2. Hades does not run autonomous workflows in MVP.
3. Hades does not mutate repos in MVP.
4. Hades creates task packets, not direct code changes.
5. Hades asks for user confirmation before saving important generated outputs.
6. Hades keeps GitHub ticket resolver output short.
7. Hades references existing repo addon workflow instead of recreating it.
```

---

# 4. Product + Technical Roadmap

## 4.1 MVP / V1 — Manual Hades Console

### Product Goal

Prove the smallest real Hades loop:

```txt
login
→ chat
→ create reusable tool
→ create manual automation
→ run manual automation
→ view result/log
→ paste GitHub issue
→ generate bounded task packet
```

### MVP Includes

```txt
1. User auth
2. Simple chat
3. Tool creator
4. Manual automation management
5. Task run logs
6. Basic GitHub ticket resolver
7. Basic reference to existing modular-monolith addon workflow
```

### MVP Does Not Include

```txt
auto-running scheduled tasks
level-up system
Forge workflow language
direct repo mutation
automatic pull requests
full coding-agent execution
multi-agent orchestration
billing
multi-user workspaces
browser IDE
plugin marketplace
advanced worker routing
```

<details>
<summary>4.1 Technical specs for MVP / V1</summary>

## Stack

### Frontend

```txt
React
Vite
TypeScript
React Router
Supabase client
Tailwind or CSS modules
```

### Backend

```txt
Node.js
Express
TypeScript
Supabase Postgres
Supabase Auth verification
GitHub API integration
LLM provider wrapper
```

### Cloud

```txt
Frontend: Vercel
Backend API: Railway
Database/Auth: Supabase
Repo: GitHub
LLM: OpenAI/OpenRouter-compatible provider
```

Railway is enough for MVP because the backend only handles:

```txt
API routes
GitHub issue fetching
LLM calls
database writes
task/log records
```

Do not run these on Railway for MVP:

```txt
heavy coding agents
repo cloning
test execution
parallel worker sandboxes
self-hosted LLM inference
```

## MVP Repo Structure

```txt
hades-os/
  frontend/
    src/
      app/
        routes.tsx

      modules/
        auth/
        chat/
        tools/
        automations/
        task-runs/
        github-ticket-resolver/
        shared/

      services/
        hadesApi.ts
        githubApi.ts

  backend/
    src/
      modules/
        auth/
        chat/
        tools/
        automations/
        task-runs/
        github-ticket-resolver/
        shared/

      server.ts

  packages/
    shared/
      src/
        types/

  docs/
    project-proposal.md
    technical-proposal.md
    module-registry.md
    handoff.md

  hades-behavior/
    README.md
```

## Environment Variables

### Frontend / Vercel

```txt
VITE_API_BASE_URL=
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
```

### Backend / Railway

```txt
PORT=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
GITHUB_TOKEN=
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
FRONTEND_URL=
```

## MVP Database Tables

```txt
profiles
chat_messages
tools
automations
task_runs
github_issue_resolutions
```

## Service Layer Rule

Frontend components must not call `fetch` directly.

All backend calls go through:

```txt
frontend/src/services/hadesApi.ts
frontend/src/services/githubApi.ts
```

</details>

---

## 4.2 V1.5 — Modular Monolith Addon Integration

### Product Goal

Connect Hades more directly to the repo’s existing addon-enforced development workflow.

This is not Hades recreating the contract system.

This is Hades becoming aware of it.

V1.5 adds:

```txt
module registry reader
addon/task packet awareness
repo module selection
GitHub ticket to module mapping
short task packet generation
basic behavior rules for Hades decisions
```

Example V1.5 flow:

```txt
GitHub issue
→ Hades identifies target module
→ Hades references repo addon rules
→ Hades creates short task packet
→ user sends packet to coding worker
→ Hades stores result/log
```

V1.5 should still avoid direct repo mutation.

<details>
<summary>4.2 Technical specs for V1.5</summary>

## New Capability

Hades should become aware of the repo’s existing modular-monolith addon system.

It should read or reference:

```txt
module registry
available modules
module names
module descriptions
addon-enforced task packet format
allowed workflow categories
existing handoff conventions
```

## New Suggested Module

```txt
frontend/src/modules/repo-context/
backend/src/modules/repo-context/
```

## New Backend Routes

```txt
GET /api/repo-context/modules
GET /api/repo-context/addon-summary
POST /api/repo-context/resolve-module
```

## New Data Shapes

```ts
type RepoModule = {
  id: string
  name: string
  description: string
  path: string
  category: "frontend" | "backend" | "shared" | "fullstack"
}

type AddonSummary = {
  id: string
  repoName: string
  summary: string
  availableWorkflows: string[]
  updatedAt: string
}

type ModuleResolution = {
  issueOrTask: string
  likelyModule: string | null
  confidence: "low" | "medium" | "high"
  reason: string
}
```

## V1.5 Database Additions

Optional tables:

```txt
repo_modules
repo_addon_summaries
```

## V1.5 Behavior Rules

```txt
1. Hades may identify likely modules.
2. Hades may reference addon workflow names.
3. Hades may generate short task packets.
4. Hades still does not mutate repo files.
5. Hades still does not directly run coding workers.
```

## Completion Criteria

V1.5 is complete when:

```txt
1. Hades can show available repo modules.
2. Hades can map a GitHub issue to a likely module.
3. Hades can generate a short task packet referencing the existing addon workflow.
4. The user can copy/export the packet.
5. Hades stores the packet and resolution history.
```

</details>

---

## 4.3 V2 — Coding Workflow Control

### Product Goal

Hades becomes useful for managing coding-agent work.

Not by replacing Codex, OpenCode, Claude Code, or Cursor, but by controlling the workflow around them.

V2 adds:

```txt
coding task manager
repo/module context
worker handoff packets
manual approval flow
result tracking
diff/test summary storage
worker selection
```

Supported worker targets:

```txt
OpenCode
Codex
Claude Code
Cursor
self-hosted Qwen agent
manual copy-paste handoff
```

V2 can start without direct worker execution.

The first useful version can simply:

```txt
create task packet
export/copy prompt
save worker result
track status
approve or reject result
```

Later V2 can add direct integrations.

<details>
<summary>4.3 Technical specs for V2</summary>

## New Modules

```txt
frontend/src/modules/coding-tasks/
frontend/src/modules/workers/
frontend/src/modules/approvals/

backend/src/modules/coding-tasks/
backend/src/modules/workers/
backend/src/modules/approvals/
```

## New Backend Routes

### Coding Tasks

```txt
GET /api/coding-tasks
POST /api/coding-tasks
GET /api/coding-tasks/:taskId
PATCH /api/coding-tasks/:taskId
POST /api/coding-tasks/:taskId/mark-sent
POST /api/coding-tasks/:taskId/attach-result
```

### Workers

```txt
GET /api/workers
POST /api/workers
PATCH /api/workers/:workerId
```

### Approvals

```txt
GET /api/approvals
POST /api/approvals
POST /api/approvals/:approvalId/approve
POST /api/approvals/:approvalId/reject
```

## New Data Shapes

```ts
type CodingTask = {
  id: string
  userId: string
  title: string
  sourceType: "manual" | "github_issue" | "automation"
  sourceId: string | null
  targetRepo: string | null
  targetModule: string | null
  taskPacket: string
  status: "draft" | "ready" | "sent" | "in_review" | "approved" | "rejected" | "failed"
  workerType: "manual" | "opencode" | "codex" | "claude_code" | "cursor" | "custom"
  createdAt: string
  updatedAt: string
}

type Worker = {
  id: string
  userId: string
  name: string
  type: "opencode" | "codex" | "claude_code" | "cursor" | "custom"
  mode: "manual_handoff" | "api" | "local"
  enabled: boolean
  createdAt: string
  updatedAt: string
}

type Approval = {
  id: string
  userId: string
  targetType: "coding_task" | "automation_run" | "worker_result"
  targetId: string
  status: "pending" | "approved" | "rejected"
  notes: string | null
  createdAt: string
  updatedAt: string
}
```

## V2 Database Additions

```txt
coding_tasks
workers
approvals
worker_results
```

## V2 Behavior Rules

```txt
1. Hades can create coding tasks.
2. Hades can export handoff packets.
3. Hades can track worker type.
4. Hades can store worker output manually.
5. Hades requires approval before marking work accepted.
6. Hades does not need direct worker execution yet.
```

## Completion Criteria

V2 is complete when:

```txt
1. User can create a coding task from a GitHub resolver output.
2. User can select target worker type.
3. User can copy/export worker handoff packet.
4. User can paste/save worker result.
5. User can approve or reject the result.
6. Hades stores the full coding task lifecycle.
```

</details>

---

## 4.4 V3 — Hades OS Proper

### Product Goal

This is where Hades starts feeling like an actual operating system for AI work.

V3 adds:

```txt
onboarding phases
unlock system
auto mode
Forge workflow language
advanced worker routing
more complete automation controls
Hermes/Hades behavior rules
```

### Onboarding Phases

Instead of leaning too hard into gaming, Hades should use structured onboarding.

Example:

```txt
Phase 1: Manual workspace
Phase 2: Saved prompts/tools
Phase 3: Manual automations
Phase 4: GitHub issue task packets
Phase 5: Coding worker handoff
Phase 6: Semi-auto workflows
Phase 7: Auto workflows with approvals
```

### Unlock System

The unlock system should be based on reliability, not game points.

Example:

```txt
manual task runs successfully 10 times
→ unlock scheduled draft mode

GitHub issue packets are approved repeatedly
→ unlock worker handoff

approval history is stable
→ unlock semi-auto execution
```

### Forge Workflow Language

Forge should wait until V3.

It should start as readable workflow syntax, not a full programming language.

Example:

```txt
WHEN new GitHub issue is created
USE github_ticket_resolver
CREATE coding_task_packet
REQUIRE approval
SEND to OpenCode
LOG result
```

<details>
<summary>4.4 Technical specs for V3</summary>

## New Modules

```txt
frontend/src/modules/onboarding/
frontend/src/modules/unlocks/
frontend/src/modules/workflow-dsl/
frontend/src/modules/auto-mode/
frontend/src/modules/behavior-rules/

backend/src/modules/onboarding/
backend/src/modules/unlocks/
backend/src/modules/workflow-dsl/
backend/src/modules/auto-mode/
backend/src/modules/behavior-rules/
```

## New Backend Routes

### Onboarding

```txt
GET /api/onboarding/state
POST /api/onboarding/complete-step
```

### Unlocks

```txt
GET /api/unlocks
POST /api/unlocks/evaluate
```

### Workflow DSL

```txt
POST /api/workflows/parse
POST /api/workflows/validate
POST /api/workflows
GET /api/workflows
GET /api/workflows/:workflowId
```

### Auto Mode

```txt
POST /api/auto-runs
GET /api/auto-runs
POST /api/auto-runs/:runId/pause
POST /api/auto-runs/:runId/approve
```

### Behavior Rules

```txt
GET /api/behavior-rules
POST /api/behavior-rules
PATCH /api/behavior-rules/:ruleId
```

## New Data Shapes

```ts
type OnboardingState = {
  id: string
  userId: string
  currentPhase: number
  completedSteps: string[]
  createdAt: string
  updatedAt: string
}

type Unlock = {
  id: string
  userId: string
  key: string
  label: string
  unlocked: boolean
  reason: string | null
  unlockedAt: string | null
}

type WorkflowDefinition = {
  id: string
  userId: string
  name: string
  source: string
  parsedJson: Record<string, unknown>
  enabled: boolean
  createdAt: string
  updatedAt: string
}

type BehaviorRule = {
  id: string
  userId: string
  category: "routing" | "approval" | "automation" | "failure" | "worker_selection"
  ruleText: string
  enabled: boolean
  createdAt: string
  updatedAt: string
}
```

## V3 Database Additions

```txt
onboarding_states
unlocks
workflow_definitions
auto_runs
behavior_rules
```

## V3 Behavior Rules

```txt
1. Auto mode is locked until reliability thresholds are met.
2. Workflow DSL must validate before saving.
3. Risky workflows require approval.
4. Worker routing must respect behavior rules.
5. Failed auto-runs pause related workflow until reviewed.
6. Hades should explain why a capability is locked.
```

## Completion Criteria

V3 is complete when:

```txt
1. User has visible onboarding phases.
2. Unlocks are based on successful behavior, not arbitrary points.
3. User can write and validate a simple Forge workflow.
4. User can enable semi-auto workflow with approval gates.
5. Hades behavior rules affect routing, approval, and failure handling.
6. Hades feels like an operating layer, not just a CRUD dashboard.
```

</details>

---

# 5. MVP Vertical Build Slices

Do not build horizontally.

Do not do:

```txt
all backend
all frontend
then connect later
```

Build vertical slices.

---

## 5.1 Auth Slice

```txt
landing page
→ login
→ Supabase Auth
→ protected dashboard
```

<details>
<summary>5.1 Technical requirements</summary>

## Frontend

```txt
frontend/src/modules/auth/
frontend/src/app/routes.tsx
```

Required UI:

```txt
login page
logout action
protected dashboard route
loading session state
```

## Backend

```txt
backend/src/modules/auth/
```

Required backend behavior:

```txt
verify Supabase JWT
return current user profile
protect API routes
```

## Routes

```txt
GET /api/auth/me
```

## Done When

```txt
1. User can log in.
2. User can log out.
3. Protected dashboard blocks unauthenticated users.
4. Backend routes reject missing/invalid auth.
```

</details>

---

## 5.2 Chat Slice

```txt
existing chat UI
→ service layer
→ POST /api/chat
→ assistant response
→ message renders in UI
```

<details>
<summary>5.2 Technical requirements</summary>

## Frontend

```txt
frontend/src/modules/chat/
frontend/src/services/hadesApi.ts
```

Required UI:

```txt
chat input
message list
assistant response
loading state
error state
```

## Backend

```txt
backend/src/modules/chat/
```

## Routes

```txt
POST /api/chat
GET /api/chat/messages
```

## Data Shape

```ts
type ChatMessage = {
  id: string
  userId: string
  role: "user" | "assistant" | "system"
  content: string
  createdAt: string
}
```

## Done When

```txt
1. User sends message from UI.
2. Backend receives real request.
3. Assistant response is saved.
4. UI renders real backend response.
5. Chat history reloads from database.
```

</details>

---

## 5.3 Tool Creator Slice

```txt
existing tool form
→ service layer
→ POST /api/tools
→ saved tool renders in list
```

<details>
<summary>5.3 Technical requirements</summary>

## Frontend

```txt
frontend/src/modules/tools/
frontend/src/services/hadesApi.ts
```

Required UI:

```txt
tool list
create tool form
edit tool form
delete tool action
```

## Backend

```txt
backend/src/modules/tools/
```

## Routes

```txt
GET /api/tools
POST /api/tools
PATCH /api/tools/:toolId
DELETE /api/tools/:toolId
```

## Data Shape

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

## Done When

```txt
1. Tool list loads from database.
2. User creates a tool from UI.
3. New tool renders in existing list.
4. User can edit/delete tool.
5. No local-only fake save remains.
```

</details>

---

## 5.4 Manual Automations Slice

```txt
existing automation form
→ service layer
→ POST /api/automations
→ saved automation renders in list
```

<details>
<summary>5.4 Technical requirements</summary>

## Frontend

```txt
frontend/src/modules/automations/
frontend/src/services/hadesApi.ts
```

Required UI:

```txt
automation list
create automation form
edit automation form
delete automation action
manual mode badge
```

## Backend

```txt
backend/src/modules/automations/
```

## Routes

```txt
GET /api/automations
POST /api/automations
PATCH /api/automations/:automationId
DELETE /api/automations/:automationId
```

## Data Shape

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

## Done When

```txt
1. Automation list loads from database.
2. User creates manual automation.
3. New automation renders in existing list.
4. User can edit/delete automation.
5. Auto mode is not available yet.
```

</details>

---

## 5.5 Task Runs / Logs Slice

```txt
click Run
→ POST /api/automations/:id/run
→ task run created
→ output shown in logs
```

<details>
<summary>5.5 Technical requirements</summary>

## Frontend

```txt
frontend/src/modules/task-runs/
frontend/src/modules/automations/
frontend/src/services/hadesApi.ts
```

Required UI:

```txt
run button
run status
output panel
task run history
error display
```

## Backend

```txt
backend/src/modules/task-runs/
backend/src/modules/automations/
```

## Routes

```txt
POST /api/automations/:automationId/run
GET /api/task-runs
GET /api/task-runs/:runId
```

## Data Shape

```ts
type TaskRun = {
  id: string
  userId: string
  automationId: string | null
  input: string | null
  output: string | null
  status: "running" | "completed" | "failed"
  error: string | null
  startedAt: string
  completedAt: string | null
}
```

## Done When

```txt
1. User clicks Run on manual automation.
2. Backend creates task_run record.
3. LLM output is saved.
4. UI shows completed output.
5. Failed runs show error state.
6. Run history reloads from database.
```

</details>

---

## 5.6 GitHub Ticket Resolver Slice

```txt
paste GitHub issue
→ POST /api/github/issues/resolve
→ issue fetched
→ task packet generated
→ resolution shown in UI
```

<details>
<summary>5.6 Technical requirements</summary>

## Frontend

```txt
frontend/src/modules/github-ticket-resolver/
frontend/src/services/githubApi.ts
```

Required UI:

```txt
GitHub issue input
repo owner/name fields or URL parser
issue number field
resolve button
summary panel
likely module display
task packet panel
acceptance checklist
saved resolutions list
```

## Backend

```txt
backend/src/modules/github-ticket-resolver/
```

## Routes

```txt
POST /api/github/issues/resolve
GET /api/github/resolutions
GET /api/github/resolutions/:resolutionId
```

## Request Shape

```ts
type ResolveGithubIssueRequest = {
  repoOwner: string
  repoName: string
  issueNumber: number
}
```

## Response Shape

```ts
type ResolveGithubIssueResponse = {
  resolution: GithubIssueResolution
}
```

## Data Shape

```ts
type GithubIssueResolution = {
  id: string
  userId: string
  repoOwner: string
  repoName: string
  issueNumber: number
  issueTitle: string
  issueBody: string
  issueUrl: string
  summary: string
  likelyModule: string | null
  taskPacket: string
  acceptanceChecklist: string[]
  status: "draft" | "ready" | "sent" | "completed" | "failed"
  createdAt: string
  updatedAt: string
}
```

## Internal Resolver Prompt

```md
You are Hades OS GitHub Ticket Resolver.

Given a GitHub issue, create a short implementation task packet.

Return:

1. Issue summary
2. Likely target module
3. Required files or module area
4. Existing addon/workflow reference if relevant
5. Acceptance checklist
6. Out-of-scope items

Rules:

- Keep the packet short.
- Do not include full project history.
- Do not suggest unrelated features.
- Do not auto-code the issue.
- Do not mutate repository files.
- Generate a packet suitable for OpenCode, Codex, Claude Code, Cursor, or manual implementation.
```

## Done When

```txt
1. User pastes GitHub issue URL or issue details.
2. Backend fetches real issue from GitHub API.
3. LLM creates summary and task packet.
4. Resolution is saved in database.
5. UI renders summary, likely module, task packet, and checklist.
6. No repo files are modified.
```

</details>

---

# 6. MVP Definition of Done

MVP is done when:

```txt
1. Supabase login works
2. User can chat
3. User can create tools
4. User can create manual automations
5. User can run manual automations
6. User can view task run logs
7. User can paste a GitHub issue and get a task packet
8. GitHub resolver references the existing modular-monolith addon workflow
9. Frontend API calls go through service layer
10. Modules follow the modular monolith structure
11. Vercel frontend and Railway backend are deployed
```

---

# 7. Final Roadmap Summary

## MVP

```txt
Authenticated chat + tools + manual automations + task logs + basic GitHub issue resolver
```

## V1.5

```txt
Hades reads/uses the existing modular-monolith addon system and generates better bounded task packets
```

## V2

```txt
Coding task manager + worker handoff + approval system
```

## V3

```txt
Onboarding phases + unlockable automation + Forge workflow language + Hades behavior system + OS-like control layer
```
