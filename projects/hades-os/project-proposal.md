# Hades OS Project Proposal + Technical Proposal

## MVP to V3 Roadmap

## Purpose of This Document

This document is for future remembrance and scope control.

It explains what Hades OS is, what we are about to build first, what must stay inside MVP, and what must remain locked for later phases.

The most important current decision:

```txt
Hades OS MVP is not just a dashboard.
Hades OS MVP is a mobile-first, offline-safe command chat and workflow console.
```

The MVP should let the user start talking to Hades from a phone immediately, even with unstable network conditions.

The UI may visually include the larger V3 roadmap shell, but only MVP behavior should be implemented.

A locked future screen is complete when it clearly renders as locked.

---

# Table of Contents

* [1. Project Thesis](#1-project-thesis)

* [2. Product Identity](#2-product-identity)

* [3. Relationship to the Modular Monolith](#3-relationship-to-the-modular-monolith)

* [4. Hades Behavior Layer](#4-hades-behavior-layer)

* [5. Current Strategic Decision](#5-current-strategic-decision)

* [6. Product + Technical Roadmap](#6-product--technical-roadmap)

  * [6.1 MVP / V1 — Mobile-First Manual Hades Console](#61-mvp--v1--mobile-first-manual-hades-console)
  * [6.2 V1.5 — Modular Monolith Addon Integration](#62-v15--modular-monolith-addon-integration)
  * [6.3 V2 — Coding Workflow Control](#63-v2--coding-workflow-control)
  * [6.4 V3 — Hades OS Proper](#64-v3--hades-os-proper)

* [7. MVP Technical Foundation](#7-mvp-technical-foundation)

* [8. MVP Data Model](#8-mvp-data-model)

* [9. Mobile-First Offline Command Chat](#9-mobile-first-offline-command-chat)

* [10. MVP Vertical Build Slices](#10-mvp-vertical-build-slices)

  * [10.1 Auth Slice](#101-auth-slice)
  * [10.2 Mobile Offline Chat Slice](#102-mobile-offline-chat-slice)
  * [10.3 Tool Creator Slice](#103-tool-creator-slice)
  * [10.4 Manual Automations Slice](#104-manual-automations-slice)
  * [10.5 Task Runs / Logs Slice](#105-task-runs--logs-slice)
  * [10.6 GitHub Ticket Resolver Slice](#106-github-ticket-resolver-slice)
  * [10.7 Locked Roadmap Shell Slice](#107-locked-roadmap-shell-slice)

* [11. MVP Definition of Done](#11-mvp-definition-of-done)

* [12. Scope Guardrails](#12-scope-guardrails)

* [13. Final Roadmap Summary](#13-final-roadmap-summary)

---

# 1. Project Thesis

Hades OS is an **agent workflow control plane**.

It starts as a simple authenticated workspace for:

```txt
chat
tools
manual automations
task logs
GitHub issue task-packet generation
offline-safe command messaging
```

Over time, it grows into an operating layer for managing:

```txt
prompts
workflows
coding tasks
workers
approvals
automation behavior
agent execution
repo-aware task handoffs
semi-autonomous workflows
```

The key idea:

```txt
Hades OS is not the coding worker itself first.
Hades OS is the command center around agents, tools, tasks, and workflow control.
```

Best one-line framing:

```txt
Hades OS starts as a mobile-first authenticated AI workflow console and grows into a modular control plane for prompts, tools, automations, GitHub issues, coding tasks, workers, approvals, behavior rules, and eventually semi-autonomous workflows.
```

The MVP should prove the user can open Hades from a phone, send commands, keep pending commands safe during unreliable network conditions, and see real task/chat history backed by Supabase.

---

# 2. Product Identity

Hades OS should feel like:

```txt
a mobile command center
a control plane
a workflow console
a task packet generator
an agent operations layer
```

It should not feel like:

```txt
a generic SaaS dashboard
a toy gamified assistant
a full browser IDE
a coding agent replacing Codex/OpenCode
a massive automation platform on day one
```

The design direction:

```txt
dark forge / obsidian style
professional
mobile-first
chat-first
large touch targets
card-based workflows
clear locked roadmap sections
control-plane feeling
minimal gaming language
```

The long-term product can include onboarding phases and unlocks, but those should be reliability-based, not superficial game points.

---

# 3. Relationship to the Modular Monolith

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
agent-readable module summaries
testing discipline
handoff conventions
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

MVP should only reference the modular-monolith/addon workflow in bounded places, especially the GitHub ticket resolver.

It should not try to rebuild the whole repo enforcement system.

---

# 4. Hades Behavior Layer

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
how Hades treats offline pending commands
how Hades handles cancel/undo behavior
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
  offline-command-rules.md
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
8. Hades treats offline pending messages as local drafts until synced.
9. Hades uses only server-synced messages as official backend context.
10. Hades does not allow true post-sync message editing in MVP.
```

---

# 5. Current Strategic Decision

The current strategy is:

```txt
Build the full V3-style UI shell now.
Implement only MVP behavior.
Keep future features locked.
Make MVP mobile-first and offline-safe.
```

This means the UI may visually include:

```txt
MVP active features:
- Auth / login
- Mobile-first chat
- Offline pending command queue
- Tools
- Manual automations
- Task run logs
- GitHub ticket resolver
- Roadmap / locked modules

Locked roadmap placeholders:
- V1.5 modular-monolith addon integration
- V2 coding task manager
- V2 worker routing
- V2 approvals
- V3 onboarding phases
- V3 unlock system
- V3 Forge workflow language
- V3 auto mode
- V3 Hades behavior rules
```

Important rule:

```txt
A locked screen is complete when it renders as locked.
It is not a request to implement the feature.
```

MVP must prioritize:

```txt
phone-first usage
offline command safety
persistent chat state
real Supabase-backed history
Railway backend API
small vertical slices
scope control
```

---

# 6. Product + Technical Roadmap

## 6.1 MVP / V1 — Mobile-First Manual Hades Console

### Product Goal

Prove the smallest real Hades loop:

```txt
login
→ open mobile-first Hades app
→ send command in chat
→ command saves locally immediately
→ command appears as pending if offline or slow
→ user can edit/undo pending command before sync
→ multiple offline commands stay ordered
→ commands sync when network returns
→ backend deduplicates retries
→ Hades responds using synced context
→ result is saved to Supabase
→ create reusable tool
→ create manual automation
→ run manual automation
→ view result/log
→ paste GitHub issue
→ generate bounded task packet
```

MVP should answer this question:

```txt
Can the user start talking to Hades from a phone tomorrow and trust that commands do not disappear when the network is unstable?
```

### MVP Includes

```txt
1. User auth
2. Mobile-first app shell
3. Simple chat
4. Offline-first message outbox
5. Multiple pending offline messages
6. Pending message edit before sync
7. Pending message undo before sync
8. Ordered offline command sync
9. Retry on reconnect
10. Idempotency keys to prevent duplicate sends
11. Server-confirmed chat history
12. Tool creator
13. Manual automation management
14. Task run logs
15. Basic GitHub ticket resolver
16. Basic reference to existing modular-monolith addon workflow
17. Locked V1.5 / V2 / V3 roadmap placeholders
```

### MVP Does Not Include

```txt
React Native app
native push notifications
true native background workers
post-sync message editing
full message version history
collaborative editing
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
direct worker APIs
repo cloning
test execution sandboxes
self-hosted LLM inference on Railway
```

### MVP Product Shape

The MVP frontend should feel like:

```txt
a mobile command app
not a desktop admin panel
```

The primary user experience:

```txt
open phone
send Hades a command
see pending state immediately
continue with bad network
let sync happen when possible
check task status later
copy/export task packet if needed
```

---

## 6.2 V1.5 — Modular Monolith Addon Integration

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

### V1.5 Technical Specs

New capability:

```txt
Hades should become aware of the repo’s existing modular-monolith addon system.
```

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

New suggested modules:

```txt
frontend/src/modules/repo-context/
backend/src/modules/repo-context/
```

New backend routes:

```txt
GET /api/repo-context/modules
GET /api/repo-context/addon-summary
POST /api/repo-context/resolve-module
```

New data shapes:

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

V1.5 database additions:

```txt
repo_modules
repo_addon_summaries
```

V1.5 behavior rules:

```txt
1. Hades may identify likely modules.
2. Hades may reference addon workflow names.
3. Hades may generate short task packets.
4. Hades still does not mutate repo files.
5. Hades still does not directly run coding workers.
```

Completion criteria:

```txt
1. Hades can show available repo modules.
2. Hades can map a GitHub issue to a likely module.
3. Hades can generate a short task packet referencing the existing addon workflow.
4. The user can copy/export the packet.
5. Hades stores the packet and resolution history.
```

---

## 6.3 V2 — Coding Workflow Control

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

### V2 Technical Specs

New modules:

```txt
frontend/src/modules/coding-tasks/
frontend/src/modules/workers/
frontend/src/modules/approvals/

backend/src/modules/coding-tasks/
backend/src/modules/workers/
backend/src/modules/approvals/
```

New backend routes:

```txt
GET /api/coding-tasks
POST /api/coding-tasks
GET /api/coding-tasks/:taskId
PATCH /api/coding-tasks/:taskId
POST /api/coding-tasks/:taskId/mark-sent
POST /api/coding-tasks/:taskId/attach-result

GET /api/workers
POST /api/workers
PATCH /api/workers/:workerId

GET /api/approvals
POST /api/approvals
POST /api/approvals/:approvalId/approve
POST /api/approvals/:approvalId/reject
```

New data shapes:

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

V2 database additions:

```txt
coding_tasks
workers
approvals
worker_results
```

V2 behavior rules:

```txt
1. Hades can create coding tasks.
2. Hades can export handoff packets.
3. Hades can track worker type.
4. Hades can store worker output manually.
5. Hades requires approval before marking work accepted.
6. Hades does not need direct worker execution yet.
```

Completion criteria:

```txt
1. User can create a coding task from a GitHub resolver output.
2. User can select target worker type.
3. User can copy/export worker handoff packet.
4. User can paste/save worker result.
5. User can approve or reject the result.
6. Hades stores the full coding task lifecycle.
```

---

## 6.4 V3 — Hades OS Proper

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

### V3 Technical Specs

New modules:

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

New backend routes:

```txt
GET /api/onboarding/state
POST /api/onboarding/complete-step

GET /api/unlocks
POST /api/unlocks/evaluate

POST /api/workflows/parse
POST /api/workflows/validate
POST /api/workflows
GET /api/workflows
GET /api/workflows/:workflowId

POST /api/auto-runs
GET /api/auto-runs
POST /api/auto-runs/:runId/pause
POST /api/auto-runs/:runId/approve

GET /api/behavior-rules
POST /api/behavior-rules
PATCH /api/behavior-rules/:ruleId
```

New data shapes:

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

V3 database additions:

```txt
onboarding_states
unlocks
workflow_definitions
auto_runs
behavior_rules
```

V3 behavior rules:

```txt
1. Auto mode is locked until reliability thresholds are met.
2. Workflow DSL must validate before saving.
3. Risky workflows require approval.
4. Worker routing must respect behavior rules.
5. Failed auto-runs pause related workflow until reviewed.
6. Hades should explain why a capability is locked.
```

Completion criteria:

```txt
1. User has visible onboarding phases.
2. Unlocks are based on successful behavior, not arbitrary points.
3. User can write and validate a simple Forge workflow.
4. User can enable semi-auto workflow with approval gates.
5. Hades behavior rules affect routing, approval, and failure handling.
6. Hades feels like an operating layer, not just a CRUD dashboard.
```

---

# 7. MVP Technical Foundation

## 7.1 Stack

### Frontend

```txt
React
Vite
TypeScript
React Router
Supabase client
Tailwind or CSS modules
IndexedDB for offline outbox
PWA-ready app shell
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
Idempotency handling
Async task/message status handling
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
idempotent command intake
basic async status updates
```

Do not run these on Railway for MVP:

```txt
heavy coding agents
repo cloning
test execution
parallel worker sandboxes
self-hosted LLM inference
large background worker fleets
```

---

## 7.2 MVP Repo Structure

```txt
hades-os/
  frontend/
    src/
      app/
        routes.tsx
        AppShell.tsx

      modules/
        auth/
        dashboard/
        chat/
        offline-outbox/
        tools/
        automations/
        task-runs/
        github-ticket-resolver/
        roadmap/
        shared/

      services/
        hadesApi.ts
        githubApi.ts
        outboxStore.ts
        syncService.ts

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
          idempotency/

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

---

## 7.3 Environment Variables

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

---

## 7.4 Service Layer Rule

Frontend components must not call `fetch` directly.

All backend calls go through:

```txt
frontend/src/services/hadesApi.ts
frontend/src/services/githubApi.ts
```

Offline queue persistence and sync should go through:

```txt
frontend/src/services/outboxStore.ts
frontend/src/services/syncService.ts
```

The UI should not directly know low-level IndexedDB mechanics.

---

## 7.5 Mobile-First Frontend Requirement

The Hades frontend should be designed like a mobile command app, not a traditional desktop SaaS dashboard.

Required UI direction:

```txt
phone-first layout
bottom tab navigation
large touch targets
chat-first home screen
card-based workflows
offline/sync status indicator
pending command tray
task status feed
locked roadmap cards
installable PWA shell
```

Desktop can exist, but mobile is the primary experience.

Desktop layout may use:

```txt
centered mobile app container
expanded panels
optional side rail
same bottom-nav logic if useful
```

Recommended main navigation:

```txt
Chat
Tasks
Tools
GitHub
More / Roadmap
```

---

# 8. MVP Data Model

## 8.1 Required MVP Tables

```txt
profiles
chat_conversations
chat_messages
tools
automations
task_runs
github_issue_resolutions
```

Optional for MVP:

```txt
idempotency_keys
```

The system can either store idempotency records in a separate table or as unique fields on relevant tables.

---

## 8.2 Profile

```ts
type Profile = {
  id: string
  email: string
  displayName: string | null
  createdAt: string
  updatedAt: string
}
```

---

## 8.3 Chat Conversation

```ts
type ChatConversation = {
  id: string
  userId: string
  title: string | null
  createdAt: string
  updatedAt: string
}
```

---

## 8.4 Chat Message

```ts
type ChatMessage = {
  id: string
  userId: string
  conversationId: string
  clientMessageId: string | null
  idempotencyKey: string | null
  sequenceNumber: number | null
  role: "user" | "assistant" | "system"
  content: string
  status: "queued" | "syncing" | "running" | "completed" | "failed" | "cancelled"
  createdAt: string
  updatedAt: string
}
```

Notes:

```txt
clientMessageId:
  client-generated ID for local pending messages

idempotencyKey:
  prevents duplicate backend writes during retries

sequenceNumber:
  preserves ordering for multiple offline commands

status:
  supports pending/sync/running/completed UI state
```

---

## 8.5 Local Outbox Item

Stored in IndexedDB, not Supabase.

```ts
type LocalOutboxItem = {
  localId: string
  userId: string
  conversationId: string
  parentLocalId: string | null
  sequenceNumber: number
  type: "chat_message" | "automation_run" | "github_resolver"
  payload: unknown
  status: "draft" | "pending" | "syncing" | "sent" | "failed" | "cancelled"
  createdAt: string
  updatedAt: string
  retryCount: number
  idempotencyKey: string
}
```

Do not rely only on:

```txt
React state
localStorage
in-memory queues
```

IndexedDB is required for MVP offline command persistence.

---

## 8.6 Tool

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

---

## 8.7 Automation

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

---

## 8.8 Task Run

```ts
type TaskRun = {
  id: string
  userId: string
  automationId: string | null
  clientRequestId: string | null
  idempotencyKey: string | null
  input: string | null
  output: string | null
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "cancel_requested"
  error: string | null
  startedAt: string
  completedAt: string | null
}
```

---

## 8.9 GitHub Issue Resolution

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

---

# 9. Mobile-First Offline Command Chat

## 9.1 Why This Is MVP

The user wants to use Hades mostly from a phone.

The user may be:

```txt
on the subway
walking
using unstable mobile data
minimizing the browser/app
switching between apps
opening the app later
```

Therefore, Hades must not behave like fragile web chat where a dropped network request loses the command.

The MVP chat must feel like:

```txt
send now
save locally immediately
sync when possible
show clear state
never silently lose a command
```

---

## 9.2 Offline Chat Flow

Required flow:

```txt
user sends message
→ message saves to IndexedDB immediately
→ UI shows message as Pending
→ app attempts to sync
→ if offline, message stays pending
→ user may edit or undo before sync
→ when online, sync service sends message to Railway
→ Railway verifies auth
→ Railway deduplicates by idempotency key
→ Railway writes to Supabase
→ backend generates assistant response
→ frontend reloads or updates message status
```

---

## 9.3 Multiple Offline Commands

Hades must support multiple offline commands.

Example:

```txt
1. "Review GitHub issue 12"
2. "Actually focus only on backend"
3. "Make the output short"
```

These should not be treated as random independent messages.

They should be stored as an ordered local command timeline.

Required behavior:

```txt
1. Multiple offline messages are allowed.
2. Each pending message gets a sequence number.
3. Pending messages sync in the same order they were created.
4. The sync service must not send message #3 before message #2.
5. The backend should preserve the final synced order.
6. Duplicate retries must not create duplicate server messages.
```

Recommended MVP behavior:

```txt
If several offline messages sync together, preserve them as ordered user messages and generate one assistant response after the final synced offline message.
```

This avoids Hades responding separately to every offline command when the user was really refining one intent while offline.

---

## 9.4 Pending Message Editing

Pending local messages may be edited before sync.

Safe rule:

```txt
local pending = editable
synced/running/completed = not editable in MVP
```

If a message has not reached the backend yet, editing should update the IndexedDB record.

Example:

```txt
Original pending message:
"make github packet"

Edited before sync:
"make github packet for issue 12 and keep it short"
```

Only the edited version should sync.

---

## 9.5 Post-Sync Edit Rule

Synced messages are not editable in MVP.

Once a message reaches the backend, it becomes part of the official server conversation timeline.

If the user wants to change meaning after sync, they should send a correction as a new message.

Example:

```txt
"Correction: I meant issue 13, not issue 12."
```

Reason:

```txt
If Hades has already used a synced message as context, silently editing that message later can make the conversation history logically false.
```

True versioned edits can be added later, but not in MVP.

---

## 9.6 Context Rules

Hades should build backend context only from synced server-confirmed messages.

Pending local messages display in the UI, but they are not official backend context until synced.

MVP context rules:

```txt
1. Local pending messages appear in the UI immediately.
2. Backend context uses only synced server messages.
3. Pending messages become official context only after sync.
4. Multiple offline messages must sync in order.
5. If multiple pending messages sync together, Hades may generate one assistant response after the final synced message.
6. Corrections after sync should be sent as new messages.
7. Synced message editing is out of scope for MVP.
```

---

## 9.7 Undo / Cancel Rules

Use this lifecycle:

```txt
local_pending
→ syncing
→ synced_queued
→ running
→ completed
```

Rules:

```txt
local_pending:
  user can edit
  user can undo
  item is deleted from IndexedDB
  nothing reaches backend

syncing:
  user can no longer hard-delete safely
  show syncing state

synced_queued:
  user can cancel if backend has not started processing

running:
  user can request cancel
  backend marks cancel_requested if supported

completed:
  cannot truly unsend
  user may delete/archive locally
```

UI labels:

```txt
Pending · Edit · Undo
Syncing
Queued · Cancel
Running
Completed
Failed · Retry
Cancelled
```

---

## 9.8 Backend Idempotency

Every offline-capable request should include an idempotency key.

Example:

```ts
type OfflineCommandRequest<TPayload> = {
  clientRequestId: string
  idempotencyKey: string
  payload: TPayload
}
```

Backend behavior:

```txt
1. Accept client-generated idempotencyKey.
2. Check whether the command was already received.
3. If already received, return the existing server record.
4. If new, create the server record.
5. Never create duplicate task runs from retry storms.
```

Add these fields to relevant records:

```txt
client_request_id
client_message_id
idempotency_key
sequence_number
```

Apply to:

```txt
chat_messages
task_runs
github_issue_resolutions
```

---

## 9.9 Offline Chat Routes

Single message:

```txt
POST /api/chat
GET /api/chat/messages
```

Single message request:

```ts
type SendChatMessageRequest = {
  conversationId: string
  clientMessageId: string
  idempotencyKey: string
  sequenceNumber: number
  content: string
}
```

Batch sync:

```txt
POST /api/chat/batch-sync
```

Batch sync request:

```ts
type BatchSyncChatMessagesRequest = {
  conversationId: string
  messages: {
    clientMessageId: string
    idempotencyKey: string
    sequenceNumber: number
    content: string
  }[]
}
```

Batch sync backend rule:

```txt
1. Validate auth.
2. Sort messages by sequenceNumber.
3. Insert only messages not already synced.
4. Preserve order.
5. Generate one assistant response after the final new user message.
6. Return synced messages and assistant response.
```

Backend idempotency rule:

```txt
If idempotencyKey already exists, return the existing message/task result.
Do not create duplicates.
```

---

# 10. MVP Vertical Build Slices

Do not build horizontally.

Do not do:

```txt
all backend
all frontend
then connect later
```

Build vertical slices.

Updated MVP slice order:

```txt
Slice 1 — Auth
Slice 2 — Mobile Offline Chat
Slice 3 — Tool Creator
Slice 4 — Manual Automations
Slice 5 — Task Runs / Logs
Slice 6 — GitHub Ticket Resolver
Slice 7 — Locked Roadmap Shell
```

---

## 10.1 Auth Slice

```txt
landing page
→ login
→ Supabase Auth
→ protected mobile-first app shell
```

### Frontend

```txt
frontend/src/modules/auth/
frontend/src/app/routes.tsx
frontend/src/app/AppShell.tsx
```

Required UI:

```txt
login page
logout action
protected app route
loading session state
mobile-first authenticated shell
```

### Backend

```txt
backend/src/modules/auth/
```

Required backend behavior:

```txt
verify Supabase JWT
return current user profile
protect API routes
```

### Routes

```txt
GET /api/auth/me
```

### Done When

```txt
1. User can log in.
2. User can log out.
3. Protected app shell blocks unauthenticated users.
4. Backend routes reject missing/invalid auth.
5. Auth session persists on reload where supported.
```

---

## 10.2 Mobile Offline Chat Slice

```txt
login
→ open chat
→ send message
→ message saves locally
→ message appears pending
→ user may edit/undo before sync
→ multiple messages queue in order
→ messages sync to backend
→ backend saves messages
→ assistant response appears
→ history reloads from Supabase
```

### Frontend

```txt
frontend/src/modules/chat/
frontend/src/modules/offline-outbox/
frontend/src/services/hadesApi.ts
frontend/src/services/outboxStore.ts
frontend/src/services/syncService.ts
```

Required UI:

```txt
mobile-first chat screen
chat input
message list
pending message state
edit pending message action
undo pending message action
retry failed message action
offline/sync status indicator
assistant response
error state
history reload
```

### Backend

```txt
backend/src/modules/chat/
backend/src/modules/shared/idempotency/
```

### Routes

```txt
POST /api/chat
POST /api/chat/batch-sync
GET /api/chat/messages
```

### Done When

```txt
1. User can send a chat message from the mobile-first UI.
2. Message appears instantly before backend response.
3. Message is saved to IndexedDB.
4. Pending message survives page refresh.
5. Multiple pending messages are preserved in order.
6. User can edit a pending local message before sync.
7. User can undo a pending local message before sync.
8. Pending messages sync when network returns.
9. Duplicate retries do not create duplicate backend records.
10. Failed sync shows retry state.
11. Backend saves user messages to Supabase.
12. Backend generates assistant response after synced messages.
13. Assistant response is saved to Supabase.
14. Chat history reloads after refresh/login.
15. Synced messages are not editable in MVP.
```

---

## 10.3 Tool Creator Slice

```txt
existing tool form
→ service layer
→ POST /api/tools
→ saved tool renders in list
```

### Frontend

```txt
frontend/src/modules/tools/
frontend/src/services/hadesApi.ts
```

Required UI:

```txt
mobile-friendly tool list
create tool form
edit tool form
delete tool action
manual mode badge
```

### Backend

```txt
backend/src/modules/tools/
```

### Routes

```txt
GET /api/tools
POST /api/tools
PATCH /api/tools/:toolId
DELETE /api/tools/:toolId
```

### Data Shape

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

### Done When

```txt
1. Tool list loads from database.
2. User creates a tool from UI.
3. New tool renders in existing list.
4. User can edit/delete tool.
5. No local-only fake save remains.
```

---

## 10.4 Manual Automations Slice

```txt
existing automation form
→ service layer
→ POST /api/automations
→ saved automation renders in list
```

### Frontend

```txt
frontend/src/modules/automations/
frontend/src/services/hadesApi.ts
```

Required UI:

```txt
mobile-friendly automation list
create automation form
edit automation form
delete automation action
manual mode badge
locked auto mode indicator
```

### Backend

```txt
backend/src/modules/automations/
```

### Routes

```txt
GET /api/automations
POST /api/automations
PATCH /api/automations/:automationId
DELETE /api/automations/:automationId
```

### Data Shape

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

### Done When

```txt
1. Automation list loads from database.
2. User creates manual automation.
3. New automation renders in existing list.
4. User can edit/delete automation.
5. Auto mode is not available yet.
```

---

## 10.5 Task Runs / Logs Slice

```txt
click Run
→ POST /api/automations/:id/run
→ task run created
→ output shown in logs
```

### Frontend

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
mobile-friendly task timeline
```

### Backend

```txt
backend/src/modules/task-runs/
backend/src/modules/automations/
backend/src/modules/shared/idempotency/
```

### Routes

```txt
POST /api/automations/:automationId/run
GET /api/task-runs
GET /api/task-runs/:runId
POST /api/task-runs/:runId/cancel
```

### Data Shape

```ts
type TaskRun = {
  id: string
  userId: string
  automationId: string | null
  clientRequestId: string | null
  idempotencyKey: string | null
  input: string | null
  output: string | null
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "cancel_requested"
  error: string | null
  startedAt: string
  completedAt: string | null
}
```

### Done When

```txt
1. User clicks Run on manual automation.
2. Backend creates task_run record.
3. LLM output is saved.
4. UI shows completed output.
5. Failed runs show error state.
6. Run history reloads from database.
7. Cancel/cancel_requested status is represented safely.
```

---

## 10.6 GitHub Ticket Resolver Slice

```txt
paste GitHub issue
→ POST /api/github/issues/resolve
→ issue fetched
→ task packet generated
→ resolution shown in UI
```

### Frontend

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
mobile-friendly copy button
```

### Backend

```txt
backend/src/modules/github-ticket-resolver/
```

### Routes

```txt
POST /api/github/issues/resolve
GET /api/github/resolutions
GET /api/github/resolutions/:resolutionId
```

### Request Shape

```ts
type ResolveGithubIssueRequest = {
  repoOwner: string
  repoName: string
  issueNumber: number
}
```

### Response Shape

```ts
type ResolveGithubIssueResponse = {
  resolution: GithubIssueResolution
}
```

### Data Shape

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

### Internal Resolver Prompt

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

### Done When

```txt
1. User pastes GitHub issue URL or issue details.
2. Backend fetches real issue from GitHub API.
3. LLM creates summary and task packet.
4. Resolution is saved in database.
5. UI renders summary, likely module, task packet, and checklist.
6. No repo files are modified.
```

---

## 10.7 Locked Roadmap Shell Slice

```txt
user opens roadmap
→ sees V1.5 / V2 / V3 areas
→ future features are visible but locked
→ no backend behavior is triggered
```

### Frontend

```txt
frontend/src/modules/roadmap/
```

Required UI:

```txt
roadmap overview
V1.5 locked page
V2 locked page
V3 locked page
clear explanation of why locked
no fake backend calls
```

Locked page copy:

```txt
Locked roadmap feature.
This area is visible for product continuity but is not implemented in MVP.
```

### Done When

```txt
1. Locked pages render clearly.
2. Locked pages do not call backend routes.
3. Locked pages do not imply feature is active.
4. User can understand what comes later without scope creep.
```

---

# 11. MVP Definition of Done

MVP is done when:

```txt
1. Supabase login works.
2. User can open a mobile-first Hades app shell.
3. User can chat with Hades.
4. User can send a message while offline or with unstable network.
5. Message appears immediately as pending.
6. Pending message survives refresh/minimization where browser storage allows.
7. Multiple pending messages are preserved in order.
8. User can edit pending local messages before sync.
9. User can undo pending local messages before sync.
10. Pending messages sync safely when network returns.
11. Duplicate retries are prevented with idempotency keys.
12. Backend context uses only synced server messages.
13. Multiple synced offline messages preserve order.
14. Hades may generate one assistant response after the final synced offline message.
15. Synced messages are not editable in MVP.
16. Chat history reloads from Supabase.
17. User can create tools.
18. User can create manual automations.
19. User can run manual automations.
20. User can view task run logs.
21. User can paste a GitHub issue and get a task packet.
22. GitHub resolver references the existing modular-monolith addon workflow.
23. Frontend API calls go through service layer.
24. Modules follow the modular monolith structure.
25. V1.5 / V2 / V3 pages render as locked placeholders.
26. Vercel frontend and Railway backend are deployable.
```

---

# 12. Scope Guardrails

## 12.1 Critical MVP Guardrail

The MVP must be useful, not huge.

The core MVP promise:

```txt
The user can start talking to Hades from a phone and trust that pending commands survive unstable network conditions.
```

Everything else should support that.

---

## 12.2 Do Not Build in MVP

Do not build:

```txt
React Native app
native mobile push notifications
native background sync
post-sync message editing
message version history
multi-user workspaces
billing
plugin marketplace
browser IDE
auto mode
Forge workflow language
worker routing
worker execution
direct repo mutation
automatic pull requests
repo cloning
test execution
parallel coding agents
self-hosted LLM inference
```

---

## 12.3 React Native Decision

Do not switch to React Native for MVP.

Reason:

```txt
React/Vite + PWA is faster to ship.
The current scaffold is already React + Node.
The main hard problem is offline command persistence and backend idempotency, not native UI.
```

React Native can come later if Hades needs:

```txt
native push notifications
better background execution
mobile OS integrations
native secure storage
app store distribution
deeper phone integration
```

Build the MVP as:

```txt
mobile-first React PWA now
React Native-compatible architecture later
```

Portable pieces to preserve:

```txt
API client patterns
shared TypeScript types
task status model
offline queue rules
idempotency logic
chat state machine
automation/task-run state machine
GitHub resolver request/response shapes
```

Non-portable pieces that can be rebuilt later:

```txt
DOM layouts
CSS/Tailwind classes
React Router screens
IndexedDB wrapper
PWA service worker
web-specific navigation
```

---

## 12.4 Locked Roadmap Rule

Future features can be visible in the UI.

They must be locked.

A locked feature is complete when:

```txt
it renders clearly as locked
it explains the future phase
it does not trigger backend calls
it does not create fake implementations
it does not expand MVP scope
```

---

## 12.5 Backend Scope Rule

Railway backend in MVP should handle:

```txt
auth verification
API routes
LLM calls
database writes
GitHub issue fetching
task/log records
idempotent command intake
basic async status updates
```

Railway backend in MVP should not handle:

```txt
heavy coding agents
repo clones
test execution
parallel worker sandboxes
long-running self-hosted inference
browser IDE environments
```

---

## 12.6 Chat Context Rule

MVP context should stay simple and honest.

```txt
Only server-synced messages are official context.
Pending local messages are UI-visible but not backend context yet.
Pending messages may be edited before sync.
Synced messages are not editable in MVP.
Corrections after sync are new messages.
```

This prevents confusing chat history where Hades appears to have responded to text that no longer exists.

---

# 13. Final Roadmap Summary

## MVP / V1

```txt
Mobile-first authenticated Hades console with offline-safe chat, pending command queue, tools, manual automations, task logs, and basic GitHub issue task-packet generation.
```

MVP proves:

```txt
Hades can be used from a phone as a reliable command interface, even with unstable network conditions.
```

---

## V1.5

```txt
Hades reads/uses the existing modular-monolith addon system and generates better bounded task packets.
```

V1.5 proves:

```txt
Hades can understand repo modules and create more accurate task handoffs without mutating code.
```

---

## V2

```txt
Coding task manager + worker handoff + approval system.
```

V2 proves:

```txt
Hades can manage coding-agent workflows around Codex, OpenCode, Claude Code, Cursor, or manual handoff without needing to replace them.
```

---

## V3

```txt
Onboarding phases + unlockable automation + Forge workflow language + Hades behavior system + OS-like control layer.
```

V3 proves:

```txt
Hades can become a true operating layer for AI work, with reliability-based unlocks, approval gates, workflow behavior rules, and semi-autonomous execution.
```

---

# Final Memory Statement

The current build direction is:

```txt
Build the full V3-style Hades OS UI shell now.
Make it mobile-first.
Implement only MVP features.
Make offline-safe chat part of MVP.
Keep V1.5 / V2 / V3 features locked.
Do not switch to React Native yet.
Do not build worker execution yet.
Do not mutate repos yet.
Focus on allowing the user to start talking to Hades from phone immediately.
```
