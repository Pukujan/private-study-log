issue still using old css

# Codex Handoff: Direct HTML Prototype Port With New Prototype Shell

## Non-Negotiable Implementation Mode

This is **not** a visual alignment task.
This is **not** a prototype-inspired rewrite.
This is **not** a patch of the old UI.
This is **not** a cleanup of the old dashboard.
This is **not** CSS layering over the old post-login app.

This is a **direct HTML prototype port into React**.

You will be given the accepted HTML prototype file:

```txt
hades_os_post_login_ux_v4.html
```

Use that HTML file as the UI implementation source.

Do not rely on memory.
Do not rely only on this handoff.
Open the HTML file and port its DOM/CSS/interaction structure.

---

# Core Problem To Avoid

Previous attempts failed because they edited the old post-login UI into shape.

That produced:

```txt
old UI + prototype patches + old CSS conflicts
```

That is not acceptable.

The required result is:

```txt
prototype UI port + existing API/data/auth contracts underneath
```

So do **not** keep working inside the old UI structure as the base.

Create a fresh prototype-derived implementation and route the authenticated app to it.

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

# Required File Strategy

Do not keep editing the old post-login UI into shape.

Create a new direct-port implementation file, for example:

```txt
frontend/src/modules/hades/HadesPrototypeApp.jsx
```

or:

```txt
frontend/src/modules/hades/HadesPostLoginPrototype.jsx
```

This new file should be based on the accepted HTML prototype.

The old HadesApp file may be used only to inspect:

```txt
- auth/session hooks
- logout behavior
- route assumptions
- API/data hooks
- existing Socials card rendering/order
```

Do not use the old HadesApp visual structure as the base.

Once the new prototype app renders correctly, swap the authenticated route/import so the active post-login app uses the new prototype file.

The old UI can remain temporarily unused only if:

```txt
- it is not imported by the active authenticated route
- its CSS is not active
- it does not affect the visible app
```

---

# Required CSS Strategy

Create a new prototype-only stylesheet, for example:

```txt
frontend/src/styles/hadesPrototype.css
```

Port the CSS from:

```txt
hades_os_post_login_ux_v4.html
```

into that stylesheet.

The prototype stylesheet should become the only active visual system for the authenticated post-login shell.

Do **not** layer prototype CSS over the old dashboard CSS.

Do **not** keep legacy dashboard CSS as fallback.

Do **not** override old CSS one issue at a time.

Delete or disconnect the old post-login/dashboard stylesheet from the active route.

Allowed CSS:

```txt
- prototype CSS from hades_os_post_login_ux_v4.html
- narrow compatibility CSS for preserved Socials cards
- login/auth CSS untouched
```

Not allowed in the active post-login route:

```txt
- old dashboard shell CSS
- old desktop panel CSS
- old rail/sidebar CSS
- old page wrapper CSS
- old generic card system CSS
- old minion layout CSS
- old forge layout CSS
- old settings layout CSS
- old bottom nav CSS
```

If old CSS affects any of these, the task is not complete:

```txt
- authenticated app shell
- header
- status / XP card
- Minions screen
- Forge screen
- Settings screen
- notification dropdown
- bottom nav
- minion detail view
- minion cards
- past summons
- scroll panels
```

---

# Preserve / Do Not Edit

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

---

# Socials Exception

Socials is the only partial UI exception.

Keep the existing Socials page cards and order first.

Then place any new prototype-style assignment/permission controls below them.

Required order:

```txt
Socials screen
├── existing Socials cards/order first
└── new Hades/prototype assignment or permission controls below
```

Do not keep the old dashboard shell just to preserve Socials.

Instead:

```txt
- extract or reuse only the existing Socials card rendering needed
- place it inside the new prototype shell
- isolate any required styles narrowly
```

If Socials needs old styling, isolate it under a narrow class, for example:

```css
.legacy-socials-card {
  /* only styles required for preserved Socials cards */
}
```

Do not preserve broad old layout CSS for Socials.

---

# Direct Port Requirement

Start from the accepted HTML file.

Convert the prototype DOM/CSS into React components as literally as practical.

Preserve the prototype’s recognizable structure:

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

Any structural deviation from the prototype must be justified by one of these reasons only:

```txt
- React state requirements
- real data/API wiring
- routing requirements
- accessibility requirements
```

Do not change structure because the old UI has a different component shape.

---

# Required App Shell

The authenticated app shell must match the prototype:

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

Primary nav must be:

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

# Screen Requirements

## Minions Screen

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

Do not allow minion cards to overflow into Minion Slots.

---

## Forge Screen

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

Each Past Summon card must show:

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

## Socials Screen

Required order:

```txt
Socials screen
├── existing Socials cards/order first
└── prototype-style assignment/permission controls below
```

Do not replace Socials with only the prototype mock permissions.

---

## Settings Screen

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

Keep logout wired to existing auth/session flow.

---

## Notification Dropdown

Port the prototype notification dropdown.

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

Clicking a notification item should open the related Minion Detail.

Clicking an open-location button can show an inline mock context panel.

Do not add tooltip clutter.

---

## Minion Detail View

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

Command Syntax and plain explanation must remain visible.

Keep `!hades` follow-up examples.

---

# Data Wiring Rule

Use existing real hooks/API data where available.

Do not preserve old UI components just because they already know how to read data.

Instead:

```txt
1. Use the existing hook/API result.
2. Convert it into a prototype-shaped view model.
3. Render it inside the prototype UI.
```

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

Open and inspect:

```txt
hades_os_post_login_ux_v4.html
```

Use the HTML file itself as the UI source.

## Step 3 — Create new prototype app file

Create:

```txt
frontend/src/modules/hades/HadesPrototypeApp.jsx
```

or equivalent.

Do not begin by patching the old HadesApp JSX.

## Step 4 — Create new prototype stylesheet

Create:

```txt
frontend/src/styles/hadesPrototype.css
```

or equivalent.

Do not begin by patching the old hades.css.

## Step 5 — Convert prototype HTML/CSS to React

Port the prototype DOM/CSS/interaction model into the new files.

Use React state only to replace prototype script behavior.

## Step 6 — Wire existing contracts underneath

Use existing hooks/API/session/logout behavior.

Map real data into prototype view models.

Use mock fallback only for display-only fields not yet available.

## Step 7 — Swap active route/import

Point the authenticated Hades route to the new prototype implementation.

Ensure old post-login UI is not on the active render path.

Ensure old post-login CSS is not active.

## Step 8 — Run tests/build

Run:

```txt
npm --prefix frontend test
npm --prefix frontend run build
```

Passing tests/build is required but not sufficient.

## Step 9 — Visual checkpoint before claiming completion

Run the frontend and inspect:

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
- active route uses the prototype-derived app shell
- old post-login shell is not on the render path
- old post-login CSS is not active
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

# Failure Conditions

This implementation is considered failed if:

```txt
- it edits the old UI into shape instead of creating a prototype-derived implementation
- it still looks like the old post-login dashboard
- it only loosely resembles the prototype
- it reconstructs a new design inspired by the prototype
- it calls the result “prototype-style” instead of porting the prototype
- it keeps old UI structure that conflicts with the prototype
- it layers prototype CSS over old dashboard CSS
- it keeps broad old dashboard selectors active
- it changes API/backend/auth contracts
- it removes existing Socials cards/order
- it breaks login
- scroll panels overflow into other sections
- notification dropdown leaves the phone shell
- Minion Detail becomes a generic profile page
- tests/build pass but visual match fails
```

---

# Final Working Note

Do not continue cleaning the old UI.

Do not continue patching CSS conflicts.

Do not continue preserving old visual helpers.

The requested implementation mode is:

```txt
New prototype-derived React shell
+ new prototype-only stylesheet
+ route swap
+ existing API/auth/data contracts wired underneath
```

Start from the accepted HTML prototype file, not from the old HadesApp structure.
