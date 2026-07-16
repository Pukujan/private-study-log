# Cortex Code Snapshot — Sanitized Archive

## Purpose
Grounded consolidation plan for ChatGPT deep research. This snapshot contains the latest local Cortex code (ahead of GitHub) so architecture recommendations target current APIs, not outdated versions.

## Source
- **Repo:** D:\claude\stupidly-simple-cortex
- **Git commit:** 28f752f0689ceaab316b23214d7ce2327391bd30
- **Snapshot date:** 2026-07-16
- **Hermes plugin:** D:\hermes\profiles\hades\plugins\cortex-assured-driver

## What's included
- `cortex_core/` — full Python source (state engine, MCP server, search, research sufficiency, assurance, patterns, audit)
- `tests/` — test suite
- `docs/design/` — design documents
- `docs/harness/` — harness contracts (KNOWLEDGE-ESCALATION.md, CAPABILITY-STATUS.md, CONTRACTS.md, etc.)
- `schemas/` — JSON schemas (research sufficiency, evidence, etc.)
- `hermes-plugin/cortex-assured-driver/` — Hermes hook code (search gate, safety valve, assured-track enforcement)
- `pyproject.toml` — project config
- `README.md` — project overview
- `DIRECTORY-TREE.txt` — full git-tracked file listing
- `GIT-STATUS.txt` — working tree status
- `GIT-DIFF.txt` — uncommitted changes
- `GIT-COMMIT.txt` — current commit hash

## What's excluded (sanitized)
- .env files, provider.env, credentials
- Databases (.db, .sqlite, .sqlite3)
- Gold/evaluator data, eval results/reports
- ops-local/ (runtime state)
- __pycache__, .pyc files
- Logs, traces
- Client-confidential content

## Key files for architecture review
- `cortex_core/mcp.py` — MCP server (3074 lines), all Cortex tools, state machine gate
- `cortex_core/state_engine.py` — StateEngine, 7-phase pipeline
- `cortex_core/research_sufficiency.py` — decision-bound sufficiency receipts (867 lines)
- `cortex_core/search.py` — BM25 + ontology RRF fused search
- `cortex_core/audit.py` — closeout writer
- `cortex_core/patterns.py` — KEDB pattern system
- `docs/harness/KNOWLEDGE-ESCALATION.md` — the search/sufficiency contract
- `docs/harness/CAPABILITY-STATUS.md` — honest capability matrix
- `hermes-plugin/cortex-assured-driver/__init__.py` — the enforcement hook we built today
- `hermes-plugin/cortex-assured-driver/tests/test_plugin.py` — 19 tests, all passing
