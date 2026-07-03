# Codex Task Pipeline

Use this as the default playbook for future Hermes/Cortex work.

## Model Split

- `5.5 xhigh`: hardest review, root-cause analysis, architecture gaps, safety-sensitive changes, cross-file path cleanup.
- `5.4 mini` with `medium`: implementation once the plan is clear, especially multi-file edits and test-first fixes.
- `5.4 mini` with `low`: small doc/config changes, straightforward mechanical edits, narrow refactors.

## When To Use Each

- Use `xhigh` when the task is ambiguous, tangled, or likely to hide a path/state bug.
- Use `mini` when the handoff already says what to change and the work is mostly execution.
- Bring `xhigh` back only for final review or when a new hard edge case appears.

## Standard Loop

1. Plan
   - Define the goal, canonical paths, constraints, and done conditions.
   - Read the repo instructions and the relevant docs first.
   - If the task is messy, ask for a plan before coding.

2. Review
   - Compare the handoff against live code.
   - Identify mismatches, hidden assumptions, and stale paths.
   - Mark anything that needs tests before implementation.

3. Smoke Test
   - Run the smallest realistic checks first.
   - Confirm the bug reproduces or the existing behavior is understood.
   - Record the exact failure, not just the symptom.

4. Gap Finding
   - List what the current code does.
   - List what the target behavior should be.
   - Call out missing tests, missing branches, path drift, and secret-handling gaps.

5. TDD Handoff
   - Write the failing tests first.
   - Keep the handoff specific: file names, behaviors, commands, and done criteria.
   - Give this to `5.4 mini` for implementation.

6. Implement
   - Make the smallest code change that satisfies the red tests.
   - Do not widen scope mid-flight unless a new bug is proven.
   - Keep edits aligned with the existing repo patterns.

7. Verify
   - Re-run the exact tests that failed first.
   - Then run the nearby unit tests.
   - End with one final review pass if the change affects shared paths or state.

## Who Gets What

- `xhigh` gets:
  - architecture review
  - gap analysis
  - tricky debugging
  - final review of sensitive path/state changes

- `5.4 mini` gets:
  - the TDD handoff
  - the concrete file edits
  - the targeted test runs
  - the small follow-up fixes from review

## Good Handoff Contents

- Goal
- Current behavior
- Desired behavior
- Canonical paths
- Known failure modes
- Files to change
- Tests to add first
- Commands to run
- Definition of done

## Good Exit Criteria

- Tests fail first, then pass after the fix.
- No secrets printed or merged.
- Canonical path is used everywhere.
- Old roots are preserved as evidence unless explicitly archived.
- Final review confirms no new drift.

## Practical Rule

If the task is still fuzzy, use `xhigh` to turn it into a crisp handoff.
If the task is already crisp, use `5.4 mini` to execute it.
If the task is risky or touches shared state, end with a review pass.
