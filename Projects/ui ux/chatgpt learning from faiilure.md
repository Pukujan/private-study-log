It is strong enough, but I would tighten it in three places because Codex has already shown the exact failure pattern.

## What is strong already

The handoff correctly says:

```txt
Direct port, not reconstruction.
HTML prototype is UI/UX source of truth.
Existing API/auth/data contracts stay preserved.
Old post-login UI is disposable.
Login stays untouched.
Socials has special exception.
```

That is the right framing.

## What may still be too loose

The phrase:

```txt
Convert the accepted HTML structure into React components as literally as practical.
```

is good, but Codex might abuse “as practical” and reconstruct again.

Better:

```txt
Preserve the prototype DOM hierarchy, class names, and CSS structure unless there is a React-specific reason not to.
Any changed structure must be justified by data wiring, accessibility, or routing needs — not personal layout preference.
```

## One missing guardrail

Add a **before/after visual checkpoint requirement**:

```txt
Before claiming completion, compare the running React screen against the accepted HTML prototype at /app/minions, /app/forge, /app/settings, and /app/socials.

If the result still looks like the old dashboard, stop and continue porting. Do not report success because tests/build pass.
```

This matters because last time it passed tests and build but visually failed.

## Another missing guardrail

Add this:

```txt
Do not summarize the task as “prototype-style layout.”
Do not implement “prototype-inspired” components.
The expected result is a recognizable React conversion of the HTML prototype.
```

Because Codex kept saying “prototype-style,” then drifting.

## One possible simplification

It is a little long, but not too long for Codex. The length is justified because the failure mode is specific.

The strongest version should have a short command block at the top:

```txt
IMPLEMENTATION MODE: DIRECT PORT

Start from hades_os_post_login_ux_v4.html.
Convert its DOM/CSS/interaction model into React.
Delete/replace the old post-login presentation layer.
Preserve login, auth, routes, API hooks, backend contracts, and Socials existing cards/order.
Do not create a similar design. Do not visually align. Port it.
Tests/build passing is not enough; visual match is required.
```

## My recommendation

Keep the full handoff, but add a **non-negotiable top section** like this:

```txt
NON-NEGOTIABLE

This is not a visual alignment task.
This is not a prototype-inspired rewrite.
This is not a patch of the old UI.

This is a direct port of hades_os_post_login_ux_v4.html into React.

Preserve the prototype DOM hierarchy, class names, CSS tokens, scroll containers, and interaction behavior as much as possible.

The old post-login UI should be removed wherever it conflicts with the prototype.

Do not claim completion just because tests/build pass.
Completion requires the running app to visually match the accepted HTML prototype.
```

That would make it strong enough.
