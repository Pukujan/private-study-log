# Closeout — transcript → scribe, NOT a ceremony

The closeout is the permanent audit record: what the task was, what happened,
whether tests passed. It is the trail the self-learning loop depends on. In
Cortex it is **generated for you, after the work, from the transcript** — it is
not a ritual you perform inside the task.

This is the deliberate replacement for the old per-task in-band closeout
ceremony (every task driving an 8–9 state chart terminating in a mandatory
"now call cortex_write_closeout" step). That ceremony was governance bigger than
the work. The recorded fix is **observe immediately, batch-decide later**: record
everything cheaply as it happens, and let a scribe write the audit afterward.

## The model

```
you finish the work
        |
        v
  transcript (+ receipts + git)  --->  scribe.py  --->  atomic closeout (.md + .json)
                                          |                   |
                                      scorer SLI      projects/<slug>/audit/closeouts/
```

1. You do the task. You do **not** stop to hand-author a closeout.
2. At the host's stop point (or on a batch cadence), `scripts/scribe.py` runs.
   It reads the transcript, the receipt store, `git diff`, and the scorer's SLI.
3. It writes **one atomic** closeout pair — `<stamp>-<slug>__<run_id>.md` and
   `.json` — into the per-project audit dir. Atomic = written to a temp file then
   `os.replace`d, so a reader never sees a half-written record.

The generated closeout is *more* detailed than a hand-typed one — it has the
whole transcript, the exact files changed, the test exit codes, and the skip
SLI baked in — and it costs you nothing at task time.

## What you SHOULD do (tiny, optional)

The scribe is deterministic; it does not invent a summary with an LLM. To make
its output sharp, drop one **task event** near the start of your transcript:

```json
{"ts": 1720000000.0, "type": "task", "run_id": "run-2026-07-13-001",
 "task": "Add retry to the fetch backend", "task_type": "implementation"}
```

That is the entire "ceremony": one line, at the start, optional. Everything else
(mutations, tests, searches, result summary) the scribe derives from the events
the transcript already contains.

## Schema (matches the repo's closeouts)

The `.md` carries YAML frontmatter + a body; the `.json` is the machine record.
Fields mirror the existing `audit/audit-log-*/agent/` closeouts:

- `status` — `completed` | `abandoned`
- `task` — one line
- `result` — what happened (assembled from mutations + tests + summary events)
- `tests` — the verify evidence (commands + exit codes seen in the transcript)
- `timestamp` — UTC ISO-8601
- `run_id` — binds the closeout to the run the scorer scored
- `sli` — the scorer's skip SLI, embedded (research-first, docs-current, etc.)

## What closeout is NOT

- Not a phase you must "reach" by satisfying gates.
- Not a tool you must remember to call before the task will end.
- Not blocking. If the scribe can't run (no transcript yet), the work is still
  done; the audit is generated whenever the transcript is available. The record
  is never allowed to block the work — capture is fail-open by contract.
