# Cortex governed wrapper — handoff (for phantomic)

> **Boundary reminder:** MCP/context reduction was a primary reason for this wrapper. Keep it a thin,
> portable adapter with lazy tier lookup and skills/resources. Do not duplicate the main Cortex's
> assured state, sufficiency, route/run receipts, living-ontology authority, KEDB/oracle policy, or
> external-verifier authority here.

> **2026-07-15 correction:** the local trial below demonstrated structural phase coercion, not
> an assured research/evaluation boundary. The vendored local path is now labeled
> `LEGACY_UNASSURED`; its model can author the JSON accepted as SEARCH_BRAIN/RESEARCH evidence.
> Use `cortex-govern --legacy-local ...` only with that limitation. A current governed claim
> requires the main Cortex MCP assured tracks plus signed external preflight/evaluator receipts.

**What this is:** a wrapper that drives *your own* model through a deterministic state machine so
an agent **cannot skip the discipline** — it must search prior knowledge, research, plan, spec,
implement, get its work reviewed against the request, and write a grounded closeout, *in that
order*, with every transition owned by the engine (not the model). You bring your own 9Router
key; nothing of mine is needed and nothing of yours leaves your machine.

This doc is deliberately honest about what was tested, how, and **where the real gaps are** — so
you're not surprised in real use.

---

## 1. What was tested for your use case, and how

The end-to-end gate ran **your actual use case** as the trial mission:

> *reverse-engineer Kurzweil 3000 key features **and** organize the scattered corpus into a
> portable, human-readable project structure.*

**How (not a scaffold — a live governed run):**
- A real external model drove the whole thing through the state machine as **enforcer** — the
  model filled one phase slot at a time and the engine gated every transition. The model I used
  as your stand-in was **`big-pickle`** on an OpenCode-Zen/9Router-style OpenAI-compatible lane
  (same shape as your 9Router). Swap in your model and it runs identically.
- **Real parallel fan-out:** the mission partitioned into ≥3 disjoint-claim workers
  (features / organize / retrieval) driven concurrently — measured 21–23 s per-call overlap, so
  the parallelism is real, not cosmetic.
- **Real deliverables, bound to the walk:** each worker's actual output (the Kurzweil report,
  the reorganized corpus, the findability index) is carried *inside* the governed IMPLEMENT
  payload by sha256 and surfaced at REVIEW — so REVIEW judges a real artifact, not a narration.
- **Deterministic checkers decide pass/fail**, never a model judging itself: 10 success criteria
  (governed traversal, adversarial-probe refusal, live-model drive, parallelism, scribe
  closeouts, librarian bookkeeping, self-learning mint, deliverable quality, cost/latency, and a
  single no-LLM verdict path).

**Result (honest):** `STRUCTURAL_PASS_P0_BLOCKED` — **10 / 10 structural criteria PASS** on the
real run. The findability leg cleared its bar using the *real* shipped hybrid search engine
(recall@5 = 0.633 ≥ 0.60), and the governed walk enforcement is real — in an earlier run a
worker **honestly ABANDONED** rather than wave through a hollow review; the fix was to bind the
deliverable into the walk, not to weaken the gate.

Evidence in the repo: `reviewed/e2e-1-governed-trial-gate-result-2026-07-14.md` (the honest
7/10 first run), `reviewed/e2e-1-governed-trial-fixes-result-2026-07-14.md` (the 10/10 fix run),
and the full machine bundle under `evals/e2e/run_evidence/` (call ledger, verdict, prereg).

---

## 2. The honest gaps (what "structural pass" does NOT mean)

**It is a structural pass, not a *certified* pass — and that ceiling is by design, not a bug.**

- **No out-of-band provenance substrate (the "P0" root).** The call ledger and closeouts are
  written by the *same process* that runs the pipeline. That's trust-level-2 evidence (the calls
  happened, tokens were spent, the walk was coerced) — but it is **not** a gateway capturing
  request/response bytes under a *separate OS identity* with an external signed clock. So the
  wrapper can prove *to you* that the discipline was enforced; it cannot yet prove it to a
  hostile third party who assumes the harness itself lies. Building that substrate (out-of-band
  gateway, separate-OS identity, external clock, OS-attributed write sandbox) is a separate piece
  of work, deliberately not done. For self-governing your own agent, structural is what matters.
- **Governance binds the *walk*, not your model's competence.** The engine guarantees the agent
  can't skip phases and can't fake a closeout — it does **not** make a weak model produce good
  code. A too-weak model will honestly ABANDON at REVIEW (you'll see it), which is the point, but
  it means model choice still matters.
- **The organizer and some lanes are proof-of-scale, not battle-hardened at your volume.** The
  corpus-organize leg was verified zero-loss on 657 files; it hasn't been stress-run on a
  100k-file tree.

None of these block you using it today. They're the difference between "I can trust this ran
honestly" (yes) and "a court-grade adversary can't dispute it" (not yet).

---

## 3. How you run it

```bash
git clone https://github.com/Pukujan/stupidly-simple-cortex
cd stupidly-simple-cortex
pip install -e ".[vector]"          # [vector] gives the real hybrid search (recommended)

# point it at YOUR 9Router (this file holds your key — it is gitignored, never commit it):
cat > provider.env <<'EOF'
NINEROUTER_API_URL=https://<your-9router-endpoint>/v1
NINEROUTER_API_KEY=<your key>
NINEROUTER_MODEL=<your model id>
EOF

cortex-govern --selftest                       # wiring check: no key, no tokens, proves the
                                               # engine coerces the phases end-to-end
cortex-govern "reverse-engineer <X> and write a portable report"   # a real governed run
```

`cortex-govern` writes everything to `./cortex-govern-runs/<timestamp>/`: the engine db, the
workspace, an append-only `call_ledger.jsonl`, and `result.json` (the full coerced walk —
`status`, `state`, and the ordered `trail` of every phase the engine forced). `status == "done"`
is granted **only** when the walk reaches a grounded closeout; `abandoned` means a deterministic
gate refused — inspect the trail to see which phase and why.

The multi-worker mission fan-out that the gate exercised lives in `evals/e2e/runner.py` if you
want the parallel-workers version rather than a single governed build.

---

## 4. (Optional) Connect your agent (Hermes) to the shared brain over MCP

`cortex-govern` gives you the **discipline** and runs fully offline. If you *also* want your
agent to reach the shared Cortex **knowledge brain** — hybrid corpus search, budget-capped
scope-packs, the pattern library (known failures/fixes), deep research, and the verified write
path with its faithfulness oracle — connect it to the hosted MCP server. This is optional and
separate from the wrapper; the two compose (brain = the tools/knowledge/oracle, wrapper = the
discipline that makes the agent use them in order).

It needs a **bearer key, which is not in the repo. Ask the owner for it on Discord** — he'll DM
you the key + the Railway URL. Paste this to request it:

> Hey — can you issue me a Cortex MCP read key + the hosted (Railway) URL so my Hermes agent can
> connect to the brain? I need a bearer token for `Authorization: Bearer …`.

Once you have the URL + key, point any MCP client (Claude Code, or whatever MCP transport your
Hermes uses) at the hosted server as a **streamable-http** MCP server:

```json
{
  "mcpServers": {
    "cortex-brain": {
      "type": "http",
      "url": "https://stupidly-simple-cortex-production.up.railway.app/mcp",
      "headers": { "Authorization": "Bearer <the token he DMs you>" }
    }
  }
}
```

First two calls, in order: **`cortex_register`** (stamps your session), then
**`cortex_onboarding`** — the server itself then tells you every tool, when to use each, the
read-before-write discipline, and the per-stage reasoning tiers (generated from live state, so it
can't go stale). Reads (`cortex_search`, `cortex_scope_pack`, `cortex_status`) are open on a read
key; **writing to the owner's canonical brain additionally requires his admin token — which you
won't have, by design**, so your key is read-only and safe. Health check (no auth):
`GET https://stupidly-simple-cortex-production.up.railway.app/healthz` → `200`.

## 5. What you still decide / provide

- **Your model + 9Router key** — you bring both; the wrapper is model-agnostic (any
  OpenAI-compatible lane: `ninerouter`, `opencode-zen`, `openrouter`, `openai-compatible`).
- Everything here is on the branch `claude/corpus-agent-orchestration-review-121x1q`. If you're
  reading this before it's merged to `main`, check out that branch.

Questions worth asking me back: which of your real tasks to trial first, and whether you want the
P0 provenance substrate built (only needed if you must prove honesty to a hostile third party,
not just to yourself).
