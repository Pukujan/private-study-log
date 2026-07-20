"""Deep Research Mode (GAP-CORTEX-0003) v1 — async task-handoff over the research engine.

The locked design requires the **MCP async task-handoff pattern**, not a single blocking tool
call: a long fan-out inside one MCP invocation is fragile across clients (default 60s timeout,
inconsistent progress-reset). So `cortex_deep_research` returns a `task_id` immediately, the
research runs in a background thread, and `cortex_research_status(task_id)` polls it.

v1 scope (deliberately contained, reuses what exists rather than reinventing):
  * Engine = the existing `cortex_core.research.run_research` (corpus-first search -> bounded
    fetch -> per-sub-question evidence -> cite-check -> report; optional Haiku framing/summary).
    Dedup-by-URL already lives in `fetch.py`'s catalog; corpus-first already avoids re-fetching.
  * Grounding = a faithfulness pass (`cortex_core.faithfulness`) on the finished report against
    its fetched sources — the v2-enhancement hook the gap card names, wired lightly now since the
    module already exists (GAP-CORTEX-0009 shipped).
  * Task state persisted to research/tasks/<id>.json (durability + auditability).

Explicitly v2 (out of v1 scope, per the gap card): embedding GPT-Researcher (license hygiene +
heavy deps), lead/worker multi-subagent fan-out, embedding-similarity claim clustering. v1's
"fan-out" is the existing pipeline's per-sub-question evidence gathering.
"""

from __future__ import annotations

import calendar
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from cortex_core.config import resolve_workspace_override
from cortex_core.research import run_research, _slug
from cortex_core.faithfulness import faithfulness

_TASKS: dict[str, dict] = {}
_LOCK = threading.Lock()

# Liveness: a background task's worker thread bumps `heartbeat` every _HEARTBEAT_SECONDS while it
# runs. If the owning PROCESS dies (e.g. the run was started inside an ephemeral exec sandbox that
# gets torn down), the heartbeat stops. A poll that sees state still `running`/`pending` with a
# heartbeat older than _STALE_SECONDS reports state=`died` -- an HONEST, actionable status instead
# of an eternal "running" that never resolves. This is the fix for the "background thread died with
# the process" failure: the durable record on disk alone could not distinguish running from dead.
_HEARTBEAT_SECONDS = 15
_STALE_SECONDS = 90
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> str:
    return time.strftime(_TS_FMT, time.gmtime())


def _age_seconds(ts: str | None) -> float | None:
    """Seconds between `ts` (a _TS_FMT UTC string) and now, or None if unparseable."""
    if not ts:
        return None
    try:
        return time.time() - calendar.timegm(time.strptime(ts, _TS_FMT))
    except (ValueError, TypeError):
        return None


def _liveness(rec: dict) -> dict:
    """Return `rec` unchanged unless it's a stuck-running task whose worker/process is gone; then a
    copy with state='died' + a restart hint. Never mutates a terminal (done/failed) record."""
    if rec.get("state") not in ("pending", "running"):
        return rec
    marker = rec.get("heartbeat") or rec.get("started") or rec.get("created")
    age = _age_seconds(marker)
    pid = rec.get("pid")
    pid_dead = pid is not None and not _pid_alive(int(pid))
    if (age is not None and age > _STALE_SECONDS) or pid_dead:
        died = dict(rec)
        died["state"] = "died"
        died["last_live_state"] = rec.get("state")
        died["died_reason"] = ("owning process gone" if pid_dead
                               else f"no heartbeat for {int(age)}s (worker/process died mid-run)")
        died["hint"] = ("re-issue cortex_deep_research(question) to restart; deep research runs in "
                        "the persistent MCP SERVER, not an inline exec that gets torn down")
        return died
    return rec


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness of `pid` (secondary signal; heartbeat is authoritative)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # exists but not signalable (or platform quirk) -> assume alive; heartbeat decides
    return True


def _tasks_dir(ws: Path) -> Path:
    d = ws / "research" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist(ws: Path, rec: dict) -> None:
    (_tasks_dir(ws) / f"{rec['task_id']}.json").write_text(
        json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")


def _set(ws: Path, task_id: str, **changes) -> dict:
    with _LOCK:
        rec = _TASKS.get(task_id, {})
        rec.update(changes)
        _TASKS[task_id] = rec
    _persist(ws, rec)
    return rec


def _grounding(ws: Path, result: dict) -> dict:
    """Faithfulness of the report against its fetched sources (empty-context guard applies)."""
    try:
        report = (ws / result["report_path"]).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return {"skipped": "report unreadable"}
    sources = []
    for capture in result.get("fetch", {}).get("captured", []):
        try:
            sources.append((ws / capture["corpus_path"]).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    fr = faithfulness(report, sources, backend="lexical")
    return fr.asdict()


def _heartbeat_loop(ws: Path, task_id: str, stop: threading.Event) -> None:
    """Bump `heartbeat` on disk every _HEARTBEAT_SECONDS until stopped. Dies with the process, which
    is exactly what makes a killed run detectable as stale by a later poll."""
    while not stop.wait(_HEARTBEAT_SECONDS):
        try:
            _set(ws, task_id, heartbeat=_now())
        except Exception:  # noqa: BLE001 -- a heartbeat write failure must never crash the worker
            pass


def _run_task(task_id: str, question: str, ws: Path, kwargs: dict) -> None:
    _set(ws, task_id, state="running", started=_now(), heartbeat=_now(), pid=os.getpid())
    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(ws, task_id, stop), daemon=True).start()
    try:
        result = run_research(question, workspace=ws, **kwargs)
        grounding = _grounding(ws, result)
        _set(ws, task_id, state="done", result=result, grounding=grounding, finished=_now())
    except Exception as e:  # noqa: BLE001
        _set(ws, task_id, state="failed", error=f"{type(e).__name__}: {e}"[:300], finished=_now())
    finally:
        stop.set()


def start_deep_research(question: str, workspace: str | Path | None = None,
                        *, background: bool = True, **kwargs) -> dict[str, Any]:
    """Kick off a deep-research run and return a task handle immediately (async handoff).

    kwargs pass through to run_research (topics, do_fetch, do_frame, do_summarize, max_sources...).
    background=False runs synchronously (for tests) and returns the completed record.
    """
    ws = resolve_workspace_override(workspace)
    task_id = f"{_slug(question)}-{uuid.uuid4().hex[:8]}"
    rec = {"task_id": task_id, "question": question, "state": "pending",
           "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "poll_with": "cortex_research_status"}
    with _LOCK:
        _TASKS[task_id] = rec
    _persist(ws, rec)
    if not background:
        _run_task(task_id, question, ws, kwargs)
        return research_status(task_id, ws)
    threading.Thread(target=_run_task, args=(task_id, question, ws, kwargs), daemon=True).start()
    return {"task_id": task_id, "state": "running", "poll_with": "cortex_research_status",
            "expected_duration": "minutes, not seconds -- bounded network fetches dominate; plan for "
                                 "2-10 minutes on a fetch-heavy question",
            "poll_after_seconds": 30,
            "note": "deep research runs in the background of THIS process; poll "
                    "cortex_research_status(task_id) with ~30s backoff until state is done/failed/died "
                    "-- do NOT abandon a running task after a couple of quick polls. Call this via the "
                    "MCP server (persistent), not inside an ephemeral exec sandbox -- the worker thread "
                    "dies when its process is torn down. If a later poll returns state='died', re-issue "
                    "this call to restart."}


def _with_poll_guidance(rec: dict) -> dict:
    """Annotate a status record with elapsed time + explicit poll-backoff guidance, so an impatient
    agent doesn't poll twice in two seconds and abandon a healthy multi-minute run (observed failure:
    Hermes 2-polled at 0.0s and fell back to manual search while the task was still working)."""
    if rec.get("state") not in ("pending", "running"):
        return rec
    out = dict(rec)
    elapsed = _age_seconds(rec.get("started") or rec.get("created"))
    if elapsed is not None:
        out["elapsed_seconds"] = int(elapsed)
    beat = _age_seconds(rec.get("heartbeat"))
    out["worker_alive"] = bool(beat is not None and beat <= _STALE_SECONDS)
    out["poll_after_seconds"] = 30
    out["guidance"] = ("still working -- a real run takes MINUTES (network fetches). Poll again in "
                       "~30s and keep polling while worker_alive is true; do NOT fall back to manual "
                       "search unless state becomes 'died' or 'failed'.")
    return out


def research_status(task_id: str, workspace: str | Path | None = None) -> dict[str, Any]:
    """Return the current state of a deep-research task (in-memory, else from disk), with a liveness
    check: a task stuck in running/pending whose worker/process has died is reported as `died` with a
    restart hint, not as an eternal `running`. Running tasks carry elapsed time + poll-backoff
    guidance so agents wait instead of abandoning healthy runs."""
    with _LOCK:
        rec = _TASKS.get(task_id)
    if rec is not None:
        return _with_poll_guidance(_liveness(dict(rec)))
    ws = resolve_workspace_override(workspace)
    p = _tasks_dir(ws) / f"{task_id}.json"
    if p.exists():
        return _with_poll_guidance(_liveness(json.loads(p.read_text(encoding="utf-8"))))
    return {"task_id": task_id, "state": "unknown", "error": "no such task"}


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Deep Research Mode (async task-handoff)")
    p.add_argument("question")
    p.add_argument("--topics", nargs="*", default=[])
    p.add_argument("--no-fetch", action="store_true", help="corpus-first only (no network)")
    p.add_argument("--frame", action="store_true")
    p.add_argument("--summarize", action="store_true")
    p.add_argument("--wait", action="store_true", help="run synchronously and print the result")
    a = p.parse_args(argv)
    kwargs = dict(topics=a.topics, do_fetch=not a.no_fetch, do_frame=a.frame, do_summarize=a.summarize)
    handle = start_deep_research(a.question, background=not a.wait, **kwargs)
    print(json.dumps(handle if not a.wait else research_status(handle["task_id"]), indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
