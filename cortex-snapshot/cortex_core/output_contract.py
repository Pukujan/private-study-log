"""Local telemetry v1 (the easy, no-R2/no-redeploy slice of the improvement loop).

The MCP already records every tool call to `logs/mcp-events.jsonl` (ts, agent_id, model, role, tool,
detail). This turns that raw stream into per-session **output-contract records** (who did what task,
which tools, how long, where it fell short) and a **failure digest** whose checks are the
Hermes-review rubric made computable: closeout coverage (F6 -- the "no audit logs" failure),
brain-first (F3 -- wrote before searching), override use (F4 -- bypassed the contract gate).

Design per docs/research/automated-improvement-loop-and-security-design-2026-07-06.md:
  * Observable-only fields, emitted by the server itself -> ungameable (the agent can't under-report
    a tool call the server logged).
  * Records accumulate in gitignored **Zone-L** (`ops-local/telemetry/`), NOT the repo -- so the raw
    telemetry never bloats git. Only distilled, gate-approved improvements ever enter the repo.
  * `records_from_events` / `digest` are pure functions over event dicts (testable, no I/O).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Tool classes for the rubric checks.
_SEARCH_TOOLS = {"cortex_search", "cortex_scope_pack"}
_WRITE_TOOLS = {"cortex_write_log", "cortex_fetch_doc"}
_OVERRIDE_TOOLS = {"contract_override", "forced_docs_override"}
_CLOSEOUT_TOOL = "cortex_write_log"

_ZONE_L = ("ops-local", "telemetry")
_STORE = "session_records.jsonl"


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def records_from_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group raw MCP events by session into observable output-contract records."""
    by_session: dict[str, list[dict]] = {}
    for e in events:
        sid = e.get("session_id")
        if sid:
            by_session.setdefault(sid, []).append(e)

    records = []
    for sid, evs in by_session.items():
        evs = sorted(evs, key=lambda x: x.get("ts") or "")
        tools_used: dict[str, int] = {}
        first_search_i = first_write_i = None
        for i, e in enumerate(evs):
            t = e.get("tool", "")
            tools_used[t] = tools_used.get(t, 0) + 1
            if t in _SEARCH_TOOLS and first_search_i is None:
                first_search_i = i
            if t in _WRITE_TOOLS and first_write_i is None:
                first_write_i = i
        ts0, ts1 = _parse_ts(evs[0].get("ts", "")), _parse_ts(evs[-1].get("ts", ""))
        duration = (ts1 - ts0).total_seconds() if ts0 and ts1 else None
        # brain-first: a search happened before any write, or there were no writes at all.
        brain_first = first_write_i is None or (
            first_search_i is not None and first_search_i < first_write_i)
        # "where it took too long": the longest gap between consecutive events = the step the agent
        # spent the most wall-clock on (slow tool or long think). Operational signal for evaluation.
        slowest_gap_s = 0.0
        slowest_gap_after = None
        for i in range(len(evs) - 1):
            a, b = _parse_ts(evs[i].get("ts", "")), _parse_ts(evs[i + 1].get("ts", ""))
            if a and b:
                g = (b - a).total_seconds()
                if g > slowest_gap_s:
                    slowest_gap_s, slowest_gap_after = g, evs[i].get("tool")
        first = evs[0]
        records.append({
            "session_id": sid,
            "agent_id": first.get("agent_id"),
            "declared_model": first.get("declared_model"),
            "role": first.get("role"),
            "tools_used": tools_used,
            "n_events": len(evs),
            "started": evs[0].get("ts"),
            "ended": evs[-1].get("ts"),
            "duration_s": duration,
            "closeout_count": tools_used.get(_CLOSEOUT_TOOL, 0),
            "closeout_coverage": tools_used.get(_CLOSEOUT_TOOL, 0) >= 1,
            "brain_first": brain_first,
            "override_used": any(tools_used.get(t, 0) for t in _OVERRIDE_TOOLS),
            "deep_research_used": tools_used.get("cortex_deep_research", 0) >= 1,
            "slowest_gap_s": round(slowest_gap_s, 1),        # where it took too long (max step gap)
            "slowest_gap_after": slowest_gap_after,
            "stalled": slowest_gap_s > 120,                  # a >2min gap = probable stall/timeout
        })
    return records


def digest(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the rubric checks across sessions -- the actionable local improvement signal."""
    n = len(records)
    if not n:
        return {"sessions": 0}
    no_closeout = [r["session_id"] for r in records if not r["closeout_coverage"]]
    wrote_before = [r["session_id"] for r in records if not r["brain_first"]]
    overrides = [r["session_id"] for r in records if r["override_used"]]
    tool_hist: dict[str, int] = {}
    for r in records:
        for t, c in r["tools_used"].items():
            tool_hist[t] = tool_hist.get(t, 0) + c
    durations = [r["duration_s"] for r in records if r["duration_s"] is not None]
    stalled = [r["session_id"] for r in records if r.get("stalled")]
    # slowest step across all sessions -- the operational "where did time go" signal
    slow = max((r for r in records if r.get("slowest_gap_s")), default=None,
               key=lambda r: r.get("slowest_gap_s", 0))
    # ranked next-step signals: the most common failure first, each with a candidate fix -> lets the
    # improvement loop pick the highest-leverage change from real data instead of guessing.
    signals = [
        ("no_closeout", len(no_closeout),
         "agents don't write closeouts -> no self-learning record. Enforce per-phase closeout in the harness."),
        ("wrote_before_search", len(wrote_before),
         "wrote before searching the brain -> shallow/ungrounded. Gate writes on a prior search."),
        ("stalled", len(stalled),
         "a >2min step gap -> slow tool / hang / timeout not handled. Add per-op timeout+retry policy."),
        ("override_used", len(overrides),
         "bypassed the contract gate via override on a build task. Require a contract for build tasks."),
    ]
    next_step_signals = [
        {"signal": name, "count": c, "rate": round(c / n, 3), "candidate_fix": fix}
        for name, c, fix in sorted(signals, key=lambda s: -s[1]) if c
    ]
    return {
        "sessions": n,
        "closeout_coverage_rate": round(1 - len(no_closeout) / n, 3),
        "brain_first_rate": round(1 - len(wrote_before) / n, 3),
        "override_rate": round(len(overrides) / n, 3),
        "stalled_rate": round(len(stalled) / n, 3),
        "next_step_signals": next_step_signals,
        "slowest_step": ({"session": slow["session_id"], "gap_s": slow["slowest_gap_s"],
                          "after_tool": slow.get("slowest_gap_after")} if slow else None),
        "no_closeout_sessions": no_closeout,
        "wrote_before_search_sessions": wrote_before,
        "stalled_sessions": stalled,
        "override_sessions": overrides,
        "tool_histogram": dict(sorted(tool_hist.items(), key=lambda kv: -kv[1])),
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
    }


def _read_events(workspace: Path) -> list[dict[str, Any]]:
    log_path = workspace / "logs" / "mcp-events.jsonl"
    if not log_path.exists():
        return []
    out = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def ingest(workspace: str | Path | None = None) -> int:
    """Read the MCP event log, build session records, append NEW ones to Zone-L (gitignored). Returns
    the count of newly-recorded sessions. Idempotent: a session already stored is skipped."""
    from cortex_core.config import resolve_workspace
    ws = resolve_workspace(workspace)
    store = ws.joinpath(*_ZONE_L, _STORE)
    store.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("session_id"))
            except json.JSONDecodeError:
                continue
    new = [r for r in records_from_events(_read_events(ws)) if r["session_id"] not in seen]
    if new:
        with store.open("a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new)


def main(argv=None) -> int:
    """`cortex-telemetry-digest`: ingest the MCP event log into Zone-L and print the failure digest."""
    import argparse
    from cortex_core.config import make_stdio_encoding_safe, resolve_workspace
    make_stdio_encoding_safe()
    p = argparse.ArgumentParser(description="Local telemetry digest (who did what, where it fell short)")
    p.add_argument("--workspace", default=None)
    a = p.parse_args(argv)
    ws = resolve_workspace(a.workspace)
    n = ingest(ws)
    store = ws.joinpath(*_ZONE_L, _STORE)
    records = [json.loads(x) for x in store.read_text(encoding="utf-8").splitlines()] if store.exists() else []
    print(f"ingested {n} new session(s); {len(records)} total in Zone-L")
    print(json.dumps(digest(records), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
