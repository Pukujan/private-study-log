# The state machine — phases as PROGRESSIVE DISCLOSURE

The work moves through phases. Each phase's only job is to **surface the right
tools and the right next move** — never to refuse you, never to loop you. This
is disclosure, not coercion: at any phase the phase's tools are the *cheapest*
thing to reach for, so doing the right thing is the path of least resistance.

> Keep this in sync with `cortex_core/state_engine.py` `BUILD_TRACK` (the
> server-side chart). The names below mirror that chart; when the engine adds or
> renames a phase, update this file so the folder and the server tell the same
> story. The engine's own docstring calls `phase_legal_tools` "the server's
> disclosure controller" — this document is its zero-install twin.

## The phases

```
SEARCH -> RESEARCH -> SDD (spec) -> TDD (tests) -> IMPLEMENT -> VERIFY -> DOC -> CLOSEOUT
```

| Phase | What you do | What the phase surfaces (cheapest next move) |
|-------|-------------|----------------------------------------------|
| **SEARCH** | Search the corpus for prior decisions before you touch anything. | `cortex_search` / `cortex-search --hybrid`; the corpus dirs. A search here mints a receipt automatically. |
| **RESEARCH** | Close the open questions the search surfaced; optionally deep-research. | `cortex_search`, `cortex_deep_research` (optional). "No coverage found" is a legal result. |
| **SDD** | Write the spec: the success conditions, in plain terms. | A short spec note. What "done" means, before you build. |
| **TDD** | Write the tests that encode those success conditions first. | The test file(s). Red before green. |
| **IMPLEMENT** | Make the change. | Your editor / native tools — nothing is denied here. |
| **VERIFY** | Run the tests / drive the change end-to-end; capture evidence. | `pytest` / the project's verify path. The exit code is the evidence. |
| **DOC** | Update the docs the change actually affects (see `DOC-UPDATE.md`). | `docs.map.yaml` tells you which doc targets your changed files map to. |
| **CLOSEOUT** | Nothing — the scribe writes the audit from the transcript. | `scripts/scribe.py` (or the server) generates it. See `CLOSEOUT.md`. |

## How "progressive disclosure" actually behaves here (no MCP)

Without a server, this document *is* the disclosure surface: it names, per phase,
the ≤ few tools worth reaching for so you never carry the whole toolbox in your
head. With the MCP server (L2), the same phases become server-driven — the
`state_engine.py` chart surfaces the phase's `advance_tool` + `extra_tools` at
each turn (`phase_legal_tools`), keeping the resting tool surface near-zero. Both
tell the identical story; L2 just makes it mechanical.

## What this state machine is NOT

- It is **not a gate**. Reaching IMPLEMENT does not require a server-witnessed
  search first. If you skipped SEARCH, that's recorded and scored *afterward* as
  a visible SLI — you are never refused at the transition.
- There is **no rework loop**. VERIFY failing does not throw you back into a
  refusal state that instructs you to "call more tools." You just fix it and
  re-run, like normal engineering.
- The phases are a **recommended order that surfaces the right tools**, so the
  disciplined path is also the easiest path. That is the whole mechanism.

## Task-typed: not every task walks the full chart

A **research-only** task stops at RESEARCH (no SDD/TDD/IMPLEMENT/DOC of code).
A **maintenance** task may skip SDD/TDD. `docs.map.yaml`'s `no_doc_task_types`
lets the scorer know a research-only or maintenance task shouldn't be dinged for
"missing docs." Declare your task type (see `CLOSEOUT.md`) so detection stays
honest and you get no meaningless README-churn nags.
