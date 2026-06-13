june 13 2026 1:54pM handoff contract for codex

# Codex Handoff: Direct Port Hades OS HTML Prototype Into React

## Non-Negotiable Implementation Mode

This is **not** a visual alignment task.
This is **not** a prototype-inspired rewrite.
This is **not** a patch of the old UI.
This is **not** another attempt to make the old dashboard “look closer.”

This is a **direct port** of the accepted standalone HTML prototype into the React app.

Prototype source of truth:

```txt
hades_os_post_login_ux_v4.html
```

Codex must start from the accepted prototype structure, not from the old HadesApp layout.

Preserve the prototype DOM hierarchy, class names, CSS tokens, scroll containers, and interaction behavior as much as practical.

Any structural deviation from the prototype must be justified by one of these reasons only:

```txt
- React state requirements
- real data/API wiring
- routing requirements
- accessibility requirements
```

Do not change structure because of personal layout preference or because the old UI already has a different component shape.

The old post-login UI should be removed wherever it conflicts with the prototype.

Do not claim completion just because tests/build pass.

Completion requires the running React app to visually match the accepted HTML prototype.

---

# Task

Directly port the accepted standalone HTML prototype into the real Hades OS React frontend.

Prototype source of truth:

```txt
hades_os_post_login_ux_v4.html
```

This prototype is already visually accepted.

Do **not** reinterpret it.
Do **not** loosely reconstruct it.
Do **not** make a “similar” UI using the old app.
Do **not** call the implementation “prototype-style” and then invent a different layout.

Convert the accepted HTML/CSS/interaction structure into React as directly as possible.

---

# Priority Order

Follow this priority order exactly:

```txt
1. Existing API/auth/data contracts
2. Accepted HTML prototype UI/UX
3. Existing React post-login UI
```

Meaning:

```txt
If old React UI conflicts with the prototype, delete/replace the old UI.

If prototype mock data conflicts with real API/data contracts, keep the real API/data contracts.

If prototype needs display-only fields that the API does not provide, derive them in frontend adapters.

Do not change backend/API/auth contracts for visual fields.
```

---

# Core Instruction

The current post-login React UI is not the visual source of truth anymore.

The accepted HTML prototype is the visual source of truth.

The old post-login UI should be treated as disposable presentation code.

Preserve only the real app contracts underneath it.

This is a:

```txt
direct prototype-to-React UI port
```

Not a:

```txt
visual alignment pass
partial patch
dashboard cleanup
prototype-inspired reconstruction
prototype-style approximation
```

---

# Keep / Preserve

Preserve these exactly unless already broken:

```txt
- login screen
- auth flow
- Supabase/session behavior
- API clients
- existing data-fetching hooks
- backend request/response contracts
- database schema
- environment variables
- route/auth boundaries
- Hermes/runtime contracts
- Discord bot/API contracts
```

Do not edit:

```txt
frontend/src/auth/loginTemplate.html
frontend/src/auth/LoginPage.jsx
frontend/src/auth/AuthProvider.jsx
backend/**
```

Also preserve the user-specific Socials rule:

```txt
Keep the existing Socials page cards/order first.
Place any new assignment/permission UI below the existing Socials cards.
Do not replace Socials with only the prototype permission cards.
```

---

# Replace

Replace the old post-login presentation layer:

```txt
- old authenticated shell
- old post-login header
- old status/dashboard layout
- old Minions screen
- old Forge screen
- old Settings screen
- old bottom nav
- old notification dropdown
- old minion detail presentation
- old post-login CSS visual language
```

The goal is for the running app to stop looking like the old Hades dashboard and start looking like the accepted prototype.

---

# Direct Port Requirement

Do not start from the old HadesApp layout and “make it look close.”

Start from the accepted HTML prototype structure.

Convert the prototype DOM/CSS into React components.

The port should preserve the prototype’s recognizable structure as much as possible:

```txt
.viewport
  .phone
    .forge-glow
    .app
      .header
        .top
        .status
      .content
        .screen
      .bottom
        .nav
```

Preserve prototype class names where practical.

Preserve prototype CSS variables where practical.

Preserve prototype section order where practical.

Preserve prototype scroll containers where practical.

Preserve prototype card hierarchy where practical.

Do not replace the visual structure with a new hand-designed component layout.

---

# Required App Shell

The authenticated app shell must match the prototype structure:

```txt
Viewport
└── Phone shell
    ├── Forge glow/background layer
    ├── App grid
    │   ├── Header
    │   │   ├── HADES OS title
    │   │   ├── section subtitle
    │   │   ├── notification button
    │   │   ├── theme button
    │   │   └── Level 1 / XP status card
    │   │
    │   ├── Content
    │   │   ├── Minions screen
    │   │   ├── Forge screen
    │   │   ├── Socials screen
    │   │   ├── Settings screen
    │   │   └── Minion Detail view
    │   │
    │   └── Bottom nav
    │       ├── Minions
    │       ├── Forge
    │       ├── Socials
    │       └── Settings
```

Primary nav must be exactly:

```txt
Minions
Forge
Socials
Settings
```

Route behavior:

```txt
/app/home should redirect to /app/minions
```

---

# Required Screens

## 1. Minions Screen

Port from the prototype.

Required structure:

```txt
Minions screen
├── Speak to Hades card
│   ├── compact state
│   ├── expandable chat state
│   ├── input
│   ├── send button
│   ├── timestamped messages
│   └── suggested actions
│
├── Your Minions
│   ├── Active / Inactive tabs
│   └── bounded internal scroll panel
│       └── rich minion cards
│
└── Minion Slots
    ├── assigned slots
    └── empty slot
```

Active and Inactive lists must scroll internally.

Do not allow cards to overflow into Minion Slots.

---

## 2. Forge Screen

Port from the prototype.

Required structure:

```txt
Forge screen
├── Forge your minion chat card
│   ├── template chips
│   ├── Forge chat log
│   ├── summon input
│   └── Forge button
│
├── Required details card
│   ├── Template
│   ├── Mode
│   ├── Channel
│   └── Approval
│
└── Your Past Summons
    └── bounded internal scroll panel
        └── rich past summon cards
```

Past Summons must not be plain text buttons.

Each Past Summon card should show:

```txt
- icon/avatar
- name
- command or schedule
- mode
- destination
- Detail button
```

Past Summons must scroll inside its own section.

---

## 3. Socials Screen

Special rule:

```txt
Keep the existing Socials cards and order first.
```

Then add prototype-style assignment/permission controls below.

Required order:

```txt
Socials screen
├── existing/old Socials cards exactly first
└── new Hades/prototype-style assignment or permission controls below
```

Do not replace the existing socials content with only the prototype mock permissions.

---

## 4. Settings Screen

Use the prototype’s simpler settings layout.

Required:

```txt
Settings screen
├── Safety Mode card
├── Theme row
├── Account row
├── Minion limits row
├── Approval logs row
└── Logout row/button
```

Keep logout behavior wired to the existing auth/session flow.

---

## 5. Notification Dropdown

Port the prototype notification dropdown directly.

Required:

```txt
Notification dropdown
├── Manual tab
├── Auto tab
├── bounded internal scroll
├── exact location metadata
└── open-location buttons
```

Must stay inside the phone/app shell.

Must work on:

```txt
375px
390px
430px
```

Manual examples:

```txt
Discord · Hades Test Server · #cat-chaos · message 1042
Gmail · pujan@gmail.com · to: alex@example.com · subject: Summary Draft
```

Auto examples:

```txt
Gmail · pujan@gmail.com · alert rule: below $80 · no message sent
Socials · Watchlist · Show: Forge Kids · region: US
```

Clicking a notification item should open the related Minion Detail.

Clicking an open-location button can show an inline mock context panel for now.

Do not add tooltip clutter.

---

## 6. Minion Detail View

Port the prototype Minion Detail view directly.

Required order:

```txt
Minion Header
Status / Mode Card
Source / Channel Card
Destination Preview Card
Command Syntax Card
Plain Description Card
Actions
Activity Log
```

This order matters.

Do not turn Minion Detail into a generic profile page.

### Status / Mode Card

Manual example:

```txt
Status: Active
Mode: Manual summon
Destination: Discord #cat-chaos
```

Automatic example:

```txt
Status: Active
Mode: Automatic
Schedule: Every 5 hours
Destination: Gmail alert
```

### Destination Preview Card

Every minion detail should include a compact destination preview.

Discord example:

```txt
Discord Preview
#cat-chaos

Pu:
!sendcat funny lawyer cat

Cat Courier:
posts funny lawyer cat gif
```

Gmail example:

```txt
Gmail Preview

From: Hades OS
To: pujan@gmail.com
Subject: Summary Draft

Scroll Reader created a summary and is waiting for approval.
```

Automation example:

```txt
Automation Preview

Price Imp checked the keyboard.
Current price: $92
Target alert: $80
No message sent.
```

Destination Preview must appear before Command Syntax.

### Command Syntax

Keep visible:

```txt
!sendcat <description>
price tracker <product link> <target price>
!summarize <how much detail>
```

### Plain Description

Keep plain explanation below command syntax.

Keep `!hades` follow-up examples.

---

# Data Wiring Rule

Use real app hooks/API data where available.

When the prototype uses mock display fields, derive them in a frontend adapter.

Allowed:

```ts
function toMinionViewModel(minion, activityLogs) {
  return {
    ...minion,
    statusLabel: deriveStatusLabel(minion),
    modeLabel: deriveModeLabel(minion),
    destinationLabel: deriveDestinationLabel(minion),
    previewType: derivePreviewType(minion),
    previewMessages: derivePreviewMessages(minion, activityLogs),
  }
}
```

Do not change backend/API contracts just to support:

```txt
- destinationLabel
- previewType
- previewMessages
- UI tags
- mock context panel text
- status display labels
```

These are frontend presentation fields.

---

# CSS / Layout Requirements

Use the prototype CSS vocabulary and structure.

Required behavior:

```txt
- mobile-first shell
- fixed bottom nav
- contained content area
- bounded scroll panels
- dark forge / underworld background
- pixel/fantasy styling
- warm Ember Forge default theme
- Arcane Night theme
- Grove theme
- rounded cards
- readable hierarchy
```

Fonts:

```css
--font-title: "Press Start 2P", "Courier New", Courier, monospace;
--font-body: "VT323", "Courier New", Courier, monospace;
```

Scroll containment is mandatory.

Use this pattern where needed:

```css
.shell {
  height: 100%;
}

.main,
.content {
  min-height: 0;
}

.panel {
  min-height: 0;
  overflow: hidden;
}

.panelScroll {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
}
```

Avoid this bug:

```txt
Parent has overflow hidden, but child list has no constrained height.
Cards visually escape and overlap other sections.
```

---

# Required Implementation Workflow

## Step 1 — Inspect contract boundaries

Before editing, identify:

```txt
- auth/login files to avoid
- API clients/hooks to preserve
- current route definitions
- current HadesApp entry point
- existing Socials cards/order
```

## Step 2 — Open the accepted prototype

Open and use:

```txt
hades_os_post_login_ux_v4.html
```

Do not rely on memory of the prototype.

Do not rely only on the handoff text.

The HTML file itself is the UI source.

## Step 3 — Remove old post-login shell

Do not preserve the old shell if it conflicts with the prototype.

Replace it with prototype shell:

```txt
.viewport
.phone
.forge-glow
.app
.header
.content
.bottom
.nav
```

## Step 4 — Convert prototype HTML to JSX

Convert the accepted HTML structure into React components as literally as practical.

Suggested components:

```txt
HadesAppShell
HadesHeader
StatusCard
NotificationDropdown
BottomNav
MinionsScreen
HadesChatCard
MinionListPanel
MinionCard
MinionSlots
ForgeScreen
ForgeChatCard
RequiredDetailsCard
PastSummonsPanel
PastSummonCard
SocialsScreen
SettingsScreen
MinionDetailView
DestinationPreviewCard
ThemeSwitcher
```

Do not over-abstract if it causes visual drift.

## Step 5 — Port CSS from prototype

Move the prototype CSS vocabulary into the post-login stylesheet.

Replace old post-login visual CSS.

Keep login CSS untouched.

## Step 6 — Wire real data through adapters

Use existing hooks/API data.

Map real data into prototype-shaped view models.

Use mock fallback only for display-only fields that do not exist yet.

## Step 7 — Run tests and build

Run:

```txt
npm --prefix frontend test
npm --prefix frontend run build
```

Passing tests/build is required, but not sufficient.

## Step 8 — Visual checkpoint before claiming completion

Before reporting success, inspect the running app against the accepted HTML prototype.

Run:

```txt
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Use actual Vite output port if different.

Check:

```txt
/app/minions
/app/forge
/app/socials
/app/settings
```

If the result still looks like the old dashboard, do not claim completion.

If the result is only “prototype-style” but not recognizably the accepted prototype, do not claim completion.

Continue porting.

---

# Acceptance Checklist

The implementation is accepted only if:

```txt
- login screen is unchanged
- auth still works
- backend/API contracts are unchanged
- post-login shell looks like the accepted prototype
- old dashboard shell is gone
- Minions screen matches prototype structure
- Forge screen matches prototype structure
- Settings screen matches prototype structure
- Socials keeps old cards first
- bottom nav is fixed and prototype-style
- notification dropdown stays inside phone bounds
- notification dropdown has Manual / Auto tabs
- notification dropdown scrolls internally
- Active / Inactive minion lists scroll internally
- Past Summons scrolls internally
- Minion Detail opens from cards/logs
- Minion Detail section order is correct
- Status / Mode / Destination card is visible
- Destination Preview card is visible
- Discord/Gmail/Automation previews exist
- command syntax remains visible
- plain explanation remains visible
- !hades follow-up examples remain visible
- timestamps remain visible
- theme switcher works
- no content overlaps bottom nav
```

Visual check widths:

```txt
375px
390px
430px
```

---

# Visual Failure Conditions

This implementation is considered failed if:

```txt
- it still looks like the old post-login dashboard
- it only loosely resembles the prototype
- it reconstructs a new design inspired by the prototype
- it calls the result “prototype-style” instead of porting the prototype
- it keeps old UI structure that conflicts with the prototype
- it changes API/backend/auth contracts
- it removes existing Socials cards/order
- it breaks login
- scroll panels overflow into other sections
- notification dropdown leaves the phone shell
- Minion Detail becomes a generic profile page
- tests/build pass but visual match fails
```

---

# Current Working Note

Do not perform another “visual alignment” pass.

Do not perform another “prototype-style” rewrite.

The requested implementation mode is:

```txt
Direct HTML prototype port into React,
with old post-login presentation removed,
and existing API/auth/data contracts wired underneath.
```

Start from the accepted prototype structure, not the old HadesApp structure.
