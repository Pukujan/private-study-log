# Hades OS UX Decision Log — Future User Routes and Role-Based Experiences

## Core Decision

Hades OS should eventually support multiple user routes, but MVP should stay as a single-user founder build.

For now:

```txt
Hades MVP = Founder Mode
```

Founder Mode means:

```txt
one account
consumer-style minion UX
private Forge/dev tools
basic settings
theme switcher
locked previews for future user types
```

Do not build all user types in MVP.

Instead, design the UI structure so future roles can be added without rewriting the app.

---

# Future User Routes

Hades can later split into different user experiences:

```txt
Hades for Casual
Hades for Developer
Hades for Creator
Hades for Business
```

Each route should share the same Hades core, but expose different modules, onboarding, permissions, and defaults.

---

# 1. Hades for Casual

## Purpose

For normal users who want simple automation without technical setup.

## UX Focus

```txt
easy onboarding
starter minions
guided automation
social commands
inbox alerts
themes
profile card
marketplace browsing
```

## Main Tabs

```txt
Home
Minions
Inbox
Market
Me
```

## User Language

```txt
Minions
Inventory
Slots
Equip
Inbox
Themes
Profile
Commands
```

## Hidden From Casual Users

```txt
Forge
GitHub resolver
repo tasks
workers
approval logs
developer handoff packets
system settings
```

---

# 2. Hades for Developer

## Purpose

For users who want Hades to help with software development, repo work, GitHub issues, coding-agent handoffs, and automation tools.

## UX Focus

```txt
Forge console
GitHub task packets
repo-aware tools
manual automations
task runs
logs
worker handoff
approval gates
```

## Main Tabs

```txt
Home
Forge
Tasks
Logs
Minions
Settings
```

## Developer Modules

```txt
GitHub Ticket Resolver
Create Tool / Minion
Manual Automation Builder
Task Runs / Logs
Repo Context
Worker Handoff
Approvals
Deployments later
```

## User Language

```txt
Forge
Tools
Tasks
Runs
Logs
Modules
Workers
Approvals
Handoffs
```

---

# 3. Hades for Creator

## Purpose

For users who create minions, commands, templates, skins, automations, or marketplace products for others.

## UX Focus

```txt
creator studio
build minion
test minion
publish minion
creator profile
marketplace listing
revenue preview
community reputation
```

## Main Tabs

```txt
Studio
My Minions
Market
Analytics
Profile
```

## Creator Modules

```txt
Minion Builder
Command Builder
Template Builder
Skin/Profile Card Builder
Test Sandbox
Publish Flow
Creator Storefront
Marketplace Analytics
```

## User Language

```txt
Create
Publish
Test
Creator Store
Rent
Sell
Trial
Credits
Featured
```

---

# 4. Hades for Business

## Purpose

For teams, companies, and organizations that want shared minions, approvals, permissions, workflows, and integrations.

## UX Focus

```txt
team workspaces
shared minions
admin permissions
approval workflows
business integrations
audit logs
billing
department-level automation
```

## Main Tabs

```txt
Workspace
Team Minions
Approvals
Integrations
Logs
Admin
```

## Business Modules

```txt
Team Workspace
User Roles
Shared Inventory
Approval Rules
Audit Logs
Billing
Business Integrations
Department Minions
```

## User Language

```txt
Workspace
Team
Admin
Policy
Approval
Audit
Integration
Billing
Permissions
```

---

# MVP Route Decision

MVP should not build all four routes.

MVP should build:

```txt
Founder Mode
```

Founder Mode includes both:

```txt
Consumer layer:
- onboarding
- Ask Hades chat
- starter minions
- level progress
- inventory preview
- inbox preview
- theme switcher
- locked marketplace/social previews

Private Forge layer:
- create tool/minion
- manual automation
- GitHub task packet helper
- task logs
- locked workers/approvals/deployments
```

Founder Mode is basically:

```txt
Hades for Casual + private Hades for Developer access
```

This matches the current need:

```txt
The founder wants to use the fun minion/level system personally,
but also wants private development tools that normal users cannot access.
```

---

# Route Visibility Rules

## MVP

Only one real role:

```txt
founder
```

Founder can see:

```txt
Home
Ask Hades
Minions
Inbox
Market preview
Me
Settings
Forge
```

Normal users do not exist yet.

---

## Later

Add role-based visibility:

```ts
type HadesUserRole =
  | "casual"
  | "developer"
  | "creator"
  | "business_admin"
  | "founder"
```

Feature access can be controlled by:

```ts
type FeatureFlag = {
  key: string
  enabled: boolean
  requiredRole?: HadesUserRole
  requiredPlan?: string
  requiredLevel?: number
}
```

---

# Future Route Structure

Suggested future frontend route structure:

```txt
/app
  /home
  /ask
  /minions
  /inbox
  /market
  /me
  /settings

/forge
  /overview
  /tools
  /automations
  /github
  /task-runs
  /workers
  /approvals
  /logs

/creator
  /studio
  /my-minions
  /publish
  /analytics
  /storefront

/business
  /workspace
  /team-minions
  /integrations
  /approvals
  /audit
  /billing
```

MVP should only implement:

```txt
/app
/forge
```

Creator and Business routes should remain locked previews.

---

# Navigation Rule

Do not mix every route into one overwhelming nav.

Use role-aware navigation.

## Founder MVP Nav

Mobile bottom nav:

```txt
Home
Minions
Inbox
Market
Me
```

Founder-only entry:

```txt
Forge
```

Forge can appear as:

```txt
a button inside Me
a floating forge button
a locked/admin card on Home
or a hidden route only founder can access
```

## Later Casual Nav

```txt
Home
Minions
Inbox
Market
Me
```

## Later Developer Nav

```txt
Home
Forge
Tasks
Logs
Settings
```

## Later Creator Nav

```txt
Studio
Minions
Market
Analytics
Me
```

## Later Business Nav

```txt
Workspace
Team
Approvals
Logs
Admin
```

---

# UX Guardrail

The user should never see all product complexity at once.

Hades should show only what matches:

```txt
their role
their level
their plan
their active route
their connected socials
their unlocked features
```

This keeps Hades easy for casual users while still allowing advanced developer, creator, and business modes later.

---

# Product Memory

The long-term route model is:

```txt
Hades for Casual
Hades for Developer
Hades for Creator
Hades for Business
```

But MVP is:

```txt
Hades Founder Mode
```

Founder Mode lets the founder:

```txt
use Hades like a normal consumer
collect/use minions
level up
customize theme/profile
use offline chat
access private Forge/dev tools
create tools/minions
generate GitHub task packets
view task logs
```

Future user routes should be designed into the architecture, but not fully implemented yet.
