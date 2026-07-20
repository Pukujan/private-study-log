"""cortex-govern -- drive YOUR OWN model through the deterministic governed build track.

Give it a task and point it at your OpenAI-compatible endpoint (9router / opencode-zen /
openrouter / a local server). The Cortex state machine OWNS every transition: your model fills
exactly ONE phase slot at a time --

    SEARCH_BRAIN -> RESEARCH -> PLAN -> SPEC -> IMPLEMENT -> REVIEW -> CLOSEOUT -> DONE

-- and it CANNOT skip a phase, reorder, or jump to DONE. The model's text can *say* "skip to
done"; it changes nothing, because the driver always submits the engine's declared advance tool
for the current state and the engine still gates it. `DONE` is granted only when the walk
reaches a grounded closeout. This is the honest Plane-2 (external-model enforcement) path from
docs/PHANTOMIC-HANDOFF.md -- NOT a best-effort disclosure like an in-harness Plane-1 agent.

Usage:
    # 1. configure your endpoint (never commit this file -- it holds your key):
    #    provider.env
    #       NINEROUTER_API_URL=https://api.9router.dev/v1
    #       NINEROUTER_API_KEY=sk-...your key...
    #       NINEROUTER_MODEL=<your model id>
    cortex-govern --selftest                        # wiring check, no key / no tokens spent
    cortex-govern "add a rate-limiter to the API"   # real governed run on your lane

Every model call is written to an append-only call ledger in the run dir. HONEST LIMIT: that
ledger is written by THIS process, not an out-of-band gateway under a separate OS identity, so
it is trust-level-2 corroboration, not a certified provenance root (see the handoff doc).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import uuid
from pathlib import Path

from cortex_core.config import make_stdio_encoding_safe
from cortex_core.model_driver import LANES, CallLedger, load_provider_env, make_driver_from_lane
from cortex_core.plane2_driver import run_build


def _selftest_llm(prompt: str) -> str:
    """In-process stub model for `--selftest`: returns a generic phase payload so the walk can
    progress and you can SEE the engine coerce each phase, WITHOUT a key or spending tokens.

    It is deliberately NOT a real model and makes NO success claim -- a real DONE walk needs
    your configured lane. The selftest asserts only that the engine drove the phases."""
    return json.dumps({
        "findings": "selftest stub: surveyed the corpus and prior art",
        "evidence": ["selftest-stub-evidence-1", "selftest-stub-evidence-2"],
        "plan": "selftest plan: implement the requested change",
        "spec": "selftest spec: the change behaves as requested",
        "patch": "def answer():\n    return 42\n",
        "implementation": "def answer():\n    return 42\n",
        "review": {"delivered": "the requested change", "matches_request": True},
        "scope_check": {"delivered": "the requested change", "matches_request": True},
        "result": "selftest complete",
        "summary": "selftest closeout",
    })


def _run_dir(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
    else:
        stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        p = Path("cortex-govern-runs") / f"{stamp}-{uuid.uuid4().hex[:8]}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _print_summary(result: dict, run_dir: Path, ledger_path: Path, selftest: bool) -> None:
    status = result.get("status", "?")
    trail = result.get("trail") or []
    walked = " -> ".join(dict.fromkeys([t.get("state", "?") for t in trail])) or "(none)"
    print()
    print(f"governed walk: {walked}")
    print(f"phases coerced by the engine: {len(trail)}   final state: {result.get('state')}   status: {status.upper()}")
    if selftest:
        ok = len(trail) > 0
        print(f"SELFTEST: {'PASS' if ok else 'FAIL'} -- the engine {'drove the phases (governance wired)' if ok else 'did not advance (check install)'}.")
        print("          This is a WIRING check with a stub model; a real DONE walk needs your lane.")
    else:
        if status == "done":
            print("DONE was granted by the engine via a grounded closeout (not the model's say-so).")
        elif status == "abandoned":
            print("ABANDONED: the model could not satisfy a deterministic phase gate (honest refusal, "
                  "not a silent pass). Inspect result.json trail to see which phase.")
        else:
            print(f"status={status}: walk did not reach DONE within max_steps. See result.json.")
    print()
    print(f"run dir     : {run_dir}")
    print(f"result.json : {run_dir / 'result.json'}")
    print(f"call ledger : {ledger_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cortex-govern",
        description="Drive your own OpenAI-compatible model through the deterministic governed "
                    "build track (Plane-2 enforcement). Dry it with --selftest first.")
    ap.add_argument("task", nargs="?", help="what to build / do (the task 'seeking' string)")
    ap.add_argument("--provider-env", default="provider.env",
                    help="KEY=VALUE file with your endpoint URL/KEY/MODEL (default: ./provider.env)")
    ap.add_argument("--lane", default="ninerouter", choices=sorted(LANES),
                    help="which provider lane in the env to use (default: ninerouter)")
    ap.add_argument("--actor", default="plane2-external", help="actor label recorded in the ledger")
    ap.add_argument("--run-dir", default=None, help="where the engine db, workspace, ledger, and "
                    "result.json go (default: ./cortex-govern-runs/<timestamp>)")
    ap.add_argument("--max-steps", type=int, default=None, help="cap on coerced phase submissions")
    ap.add_argument("--selftest", action="store_true",
                    help="run a wiring check with an in-process stub model (no key, no tokens)")

    make_stdio_encoding_safe()
    args = ap.parse_args(argv)

    if not args.task and not args.selftest:
        ap.error("give a task, e.g.  cortex-govern \"add a rate-limiter\"  (or --selftest)")

    run_dir = _run_dir(args.run_dir)
    ledger_path = run_dir / "call_ledger.jsonl"
    ledger = CallLedger(ledger_path)

    if args.selftest:
        task = args.task or "selftest: add a function answer() that returns 42"
        llm = _selftest_llm
        print(f"cortex-govern --selftest (stub model, no network)  task: {task!r}")
    else:
        env = load_provider_env(args.provider_env)
        if not env:
            print(f"ERROR: provider env not found or empty: {args.provider_env}\n"
                  f"  Create it with your lane's URL/KEY/MODEL. Example for --lane ninerouter:\n"
                  f"    NINEROUTER_API_URL=https://<your-9router-endpoint>/v1\n"
                  f"    NINEROUTER_API_KEY=<your key>\n"
                  f"    NINEROUTER_MODEL=<your model id>", file=sys.stderr)
            return 1
        try:
            llm = make_driver_from_lane(env, args.lane, args.actor, ledger)
        except (ValueError, RuntimeError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        task = args.task
        print(f"cortex-govern  lane={args.lane}  model={llm.model}  task: {task!r}")

    kw = {} if args.max_steps is None else {"max_steps": args.max_steps}
    result = run_build({"seeking": task}, llm,
                       db_path=str(run_dir / "engine.db"),
                       workspace=str(run_dir / "ws"),
                       actor=args.actor, **kw)

    (run_dir / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    _print_summary(result, run_dir, ledger_path, args.selftest)

    if args.selftest:
        return 0 if (result.get("trail") or []) else 3
    return 0 if result.get("status") == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())
