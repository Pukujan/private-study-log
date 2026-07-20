# Study Log: Raw Keyboard/App Presses

I finally got the browser interaction working reliably by avoiding the clipboard-backed text path and using the browser's lower-level control surface instead.

## What kept failing

- `playwright.locator.fill()` on the composer hit the Browser Use clipboard bridge.
- `playwright.locator.type()` also routed through the same clipboard path and failed for the same reason.
- `dom_cua.type()` and `cua.type()` looked promising, but in this environment they still triggered the clipboard shim when text entry happened through the higher-level text path.

## What worked

1. Open the live app in a fresh tab.
2. Use `dom_cua.get_visible_dom()` to inspect the currently visible elements.
3. Read the `node_id` values from the snapshot.
4. Click the textarea node directly with `dom_cua.click({ node_id })`.
5. Send individual keystrokes with `dom_cua.keypress({ keys: [...] })` or `cua.keypress({ keys: [...] })`.
6. Use normal button clicks for the rest of the flow, like `Send`, `Test`, `Save`, and `Assign`.

## Why this worked

The key breakthrough was realizing the browser wrapper exposes a raw keypress API that does not depend on the clipboard bridge. Instead of trying to paste text into the textarea, I:

- focused the real textarea node,
- sent literal key events,
- let the app's own React state update naturally from the input events.

That made the live UI behave like a real user session, while avoiding the virtual clipboard limitation entirely.

## Practical pattern

If the app needs to be tested again in this environment, the safest sequence is:

1. Scroll the composer or target panel into view if needed.
2. Snapshot the visible DOM.
3. Find the visible node IDs.
4. Click the target input or button by node ID.
5. Use raw keypresses for typing.
6. Use regular clicks for actions.

## Result

This let me complete the main flow:

- chat prompt
- draft creation
- test
- save
- social assignment

The important part is that the app was exercised through real UI controls, not a mocked state shortcut.
