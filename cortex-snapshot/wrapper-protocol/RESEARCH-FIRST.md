# Research-first — the cheapest first move, recorded for free

The single most valuable habit Cortex enforces is: **search the corpus before
you build.** Not because a gate forces you to — because it is genuinely the
cheapest first move, and because skipping it is exactly the failure this whole
project exists to prevent (guessing a parameter that contradicts a recorded
decision produces garbage evidence; see `CLAUDE.md` "Research-first is a HARD
pre-flight").

## Why it is the cheapest move (disclosure, not demand)

At SEARCH the protocol surfaces one tool: search. The corpus is right here in
the repo. Reaching for it costs you one call and usually *saves* you the far
larger cost of rebuilding something already decided, or contradicting a recorded
threshold. The disciplined path is the lazy path — that is the design.

Search order (mirrors the repo's `AGENTS.md`):

1. `audit/audit-log-*` — every prior closeout / decision.
2. `library/` and `docs/` — the plan docs, prior reviews, research base.
3. Refresh/reindex stale docs if needed.
4. Only then the web (fetch-and-populate first, web last).

With the corpus tools available:

```
cortex-search --hybrid "<your question>"     # CLI, no MCP needed
# or MCP: cortex_search
```

## It is recorded at ZERO extra tool calls (passive receipts)

Every search mints a **receipt** as a side effect (`scripts/receipts.py`). You
do not perform an extra "log my research" step — the receipt is exhaust from a
search you were going to run anyway. The receipt is digest-bound to the actual
results, so it records *that a real search happened and what it returned*.

- With the MCP server: the server mints the receipt automatically.
- Without a server (L0): call `receipts.mint_receipt(store, "search", query,
  results)` — or let the scorer read search events straight from the transcript.
  Either way the search becomes a witnessed fact the scorer can see.

The scorer later checks: **did the first search/receipt happen before the first
mutation?** If yes, research-first is honored — a green SLI. If you mutated code
before ever searching, that is a *visible number*, not a refusal.

## "No coverage found" is a legal, witnessed result

If you search and the corpus has nothing, **say so explicitly** and proceed. A
receipt with `n_hits: 0` is a first-class outcome — it is the witnessed record
that you looked and there was nothing, which is exactly what protects the next
agent from assuming coverage exists. Never skip the search just because you
suspect it will be empty; the empty result is the evidence.

## What research-first is NOT

- Not a wall. Nothing refuses your edit because a receipt is missing.
- Not a ceremony. One search call is the whole obligation; the recording is
  automatic.
- Not "search until the gate is satisfied." You search, you cite what you found
  (or found nothing), you move on.
