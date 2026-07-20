# Task: Extend enforcement hook to require cortex_search before non-trivial work

## Context

You are modifying a Hermes Agent plugin called `cortex-assured-driver`. This plugin
enforces discipline on an AI assistant (the "agent") that tends to skip research
before doing work. The plugin uses Hermes lifecycle hooks to block undisciplined
tool calls.

## Problem

The agent has a retrieval tool called `cortex_search` (exposed as an MCP tool with
the name `mcp_cortex_local_read_cortex_search`). The agent almost never calls it —
it uses grep/terminal instead, even when doing complex multi-step work. We need to
**force the agent to call `cortex_search` at least once before it can do non-trivial
work** (delegation, file writes, patches) in sessions that are NOT already in an
assured Cortex run (those are already gated).

## What to implement

Add a **search-required gate** to the existing hook:

1. **Track in `post_tool_call`:** When `tool_name` matches `cortex_search` (using
   the existing `_is_tool` helper — the MCP tool name is
   `mcp_cortex_local_read_cortex_search`), set a flag `_SESSIONS[session_id]["searched"] = True`.

2. **Enforce in `pre_tool_call`:** When `tool_name` is `delegate_task`, `write_file`,
   or `patch` — and the session is NOT in an assured run (no existing state in
   `_SESSIONS`) — block unless `searched` is True. The block message should tell the
   agent to call `cortex_search` first.

3. **Exempt trivial writes:** Don't block writes to files in the profile root
   (HANDOFF.md, SESSION-*.md, etc.) — only block writes that look like code/plugin
   changes. Use a simple heuristic: if the `path` argument contains `plugins/`,
   `skills/`, `cortex/`, or ends in `.py`, it's non-trivial. Everything else passes.

4. **Session end clears state:** The existing `_on_session_end` already clears
   `_SESSIONS` — no change needed.

## Hermes hook API (source-backed)

The plugin registers hooks via `ctx.register_hook(name, callback)`. Valid hooks:

- `pre_tool_call(tool_name, args, task_id, session_id, tool_call_id, turn_id, api_request_id, middleware_trace)` → Return `{"action": "block", "message": "..."}` to block. Return `None` to allow. First block wins.
- `post_tool_call(tool_name, args, result, task_id, session_id, status, duration_ms, ...)` → Return value ignored (observer-only). Use to track state.
- `on_session_end(session_id)` → Return value ignored. Use to clean up.

MCP tool naming convention: `mcp_{sanitized_server_name}_{tool_name}`.
- Server `cortex-local-read` → tools prefixed `mcp_cortex_local_read_`
- So `cortex_search` → `mcp_cortex_local_read_cortex_search`

## Existing code

The full current `__init__.py` is in this directory. The full test file is in
`tests/test_plugin.py`. Read both before making changes.

## What to deliver

1. **Modify `__init__.py`** — add the search-required gate. Keep all existing
   functionality intact. Add new tests for the new behavior.
2. **Add tests to `tests/test_plugin.py`** — at minimum:
   - `test_search_required_before_delegate` — delegate blocked without prior search
   - `test_search_unblocks_delegate` — delegate allowed after cortex_search called
   - `test_trivial_write_not_blocked` — writing HANDOFF.md allowed without search
   - `test_code_write_blocked_without_search` — writing a .py file blocked without search
   - `test_assured_session_not_affected_by_search_gate` — existing assured-run behavior unchanged
3. **Run the tests** — `python -m pytest tests/test_plugin.py -v` and make sure ALL
   tests pass (both old and new).

## Constraints

- Do NOT remove or weaken any existing enforcement (assured-run blocks, Docker blocks, etc.)
- Do NOT add external dependencies — stdlib only (json, re, threading, typing)
- Keep the code style consistent with the existing file
- The `_SESSIONS` dict is the existing state store — use it, don't replace it
- The `searched` flag should coexist with assured-run state in the same dict
- If a session has assured-run state, the search gate should NOT apply (assured runs have their own discipline)
