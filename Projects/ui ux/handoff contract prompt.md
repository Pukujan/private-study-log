Yes. Use this as your reusable prompt after we finish any accepted HTML prototype.

# Reusable Codex Handoff Prompt

## HTML Prototype → React UI Replacement While Preserving API/Data Contracts

You are working on an existing app.

An accepted standalone HTML prototype has already been created and visually approved.

Your job is **not** to loosely reinterpret it.

Your job is to use the accepted HTML prototype as the **UI/UX ground truth** and rebuild the existing post-login React UI around it while preserving the existing app contracts.

---

# Critical Priority Order

Follow this priority order exactly:

```txt
1. Existing API/auth/data contracts
2. Accepted HTML prototype UI/UX
3. Existing React UI implementation
```

This means:

```txt
- If existing UI conflicts with the prototype, the prototype wins.
- If prototype mock data conflicts with real API contracts, the real API contracts win.
- If existing React presentation components conflict with the prototype, replace the presentation components.
```

---

# Core Instruction

The existing React UI is no longer the visual source of truth.

The accepted standalone HTML prototype is the UI/UX source of truth.

Rebuild the relevant React UI to match the accepted HTML prototype as closely as possible.

However, preserve:

```txt
- existing API clients
- existing data-fetching hooks
- existing auth/session flow
- existing backend request/response contracts
- existing route contracts unless routing is already broken
- existing persisted data shapes
- existing environment variable assumptions
```

This is a **UI shell replacement**, not a backend or product architecture rewrite.

---

# Do Not Do This

Do not:

```txt
- reconstruct a new design merely inspired by the prototype
- partially patch the old UI until it kind of resembles the prototype
- paste the prototype HTML as a disconnected mock screen
- create a parallel demo app inside the real app
- replace API hooks with mock data
- change backend contracts
- change auth flow
- rename existing endpoints
- redesign database/data models
- invent new product flows not shown in the prototype
```

Any solution that creates a weak hybrid of the old UI and the prototype is considered failed.

Any solution that looks different from the accepted HTML because you “adapted” it too much is considered failed.

---

# What You May Replace

You may replace the old presentation layer for the relevant app area.

Allowed to replace:

```txt
- old page layout
- old visual components
- old card components
- old tab layouts
- old notification dropdown UI
- old detail page presentation
- old CSS/modules/styles for this UI area
- old local visual state that only controls presentation
```

The old React UI is disposable.

The old API/data contracts are not disposable.

---

# What You Must Preserve

Preserve existing:

```txt
- API clients
- service functions
- data hooks
- auth/session logic
- route boundaries
- backend request/response shapes
- persisted model names
- environment variables
- error/loading behavior where already wired
```

If the accepted prototype needs extra display fields that the API does not provide, create a frontend adapter/view model.

Do not change the backend for display-only fields.

Example:

```ts
const minionViewModel = {
  ...minion,
  destinationLabel: deriveDestinationLabel(minion),
  previewType: derivePreviewType(minion),
  previewMessages: derivePreviewMessages(minion),
}
```

---

# Implementation Mode

Use the accepted HTML prototype like a Figma file plus interaction spec.

Port directly:

```txt
- layout
- spacing
- section order
- card hierarchy
- typography
- colors/theme tokens
- scroll behavior
- dropdown bounds
- tab behavior
- detail-page structure
- preview card styling
- bottom navigation behavior
```

Do not port directly:

```txt
- mock-only data model
- standalone HTML script structure
- fake API assumptions
- prototype-only global state
```

---

# Required Workflow

Before editing, inspect the current app and identify:

```txt
- existing API/data hooks
- existing routes
- current post-login UI entry point
- components that are purely presentational
- components that contain API/data logic
```

Then:

```txt
1. Freeze existing API/auth/data contracts.
2. Replace the visual component tree using the accepted HTML prototype as the UI source.
3. Connect existing real data into the new visual components.
4. Add frontend adapters only for display-only fields.
5. Use mock fallback only where the real API has no equivalent yet.
6. Preserve loading, error, and empty states.
7. Test at mobile widths.
8. Compare against the accepted HTML prototype.
```

---

# Visual Acceptance Rules

The final React UI should visually match the accepted HTML prototype.

Check:

```txt
- same app shell feel
- same section order
- same card hierarchy
- same bottom nav behavior
- same notification dropdown behavior
- same detail-page structure
- same scroll containment
- same theme feel
- same mobile-first layout
```

Test widths:

```txt
375px
390px
430px
```

---

# Scroll Guardrail

Scroll containment is mandatory.

Do not allow cards/lists to escape their panels.

Required principle:

```css
.shell {
  height: 100%;
}

.main {
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

Common failure to avoid:

```txt
Parent has overflow hidden, but child list has no constrained height.
Result: cards escape and overlap lower sections.
```

This is not acceptable.

---

# Final Definition of Done

The implementation is done only when:

```txt
- the accepted HTML prototype is clearly reflected in the real React UI
- the old visual UI has been replaced, not weakly patched
- existing API/auth/data contracts remain intact
- no backend rewrite was introduced
- no route/auth/data contract was broken
- scroll behavior works inside the app shell
- the result does not look like a loose reconstruction
```

The strongest success signal:

```txt
The UI feels like the accepted HTML prototype,
while the existing app data/API contracts continue working underneath it.
```
