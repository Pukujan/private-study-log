"""Model scorecards (Phase 6 core — the buildable part, no external service).

BUILD-PLAN §1: "nothing about 'which model should do this' is ever opinion." A scorecard row
records how a model ACTUALLY performed on a task type, sourced in trust order:
  1. deterministic logic checks (test exit codes, eval results)  <- ground truth, buildable now
  2. gateway records (LiteLLM spend/served-model)                <- Phase 6, integration seam (not wired)
  3. OTel spans (tokens/latency)                                 <- Phase 6, integration seam (not wired)
  4. LLM-judge grades                                            <- never sole evidence
  5. self-report                                                 <- recorded, never trusted

This module builds source #1 today by ingesting the OBJECTIVE evaluator-eval leaderboard
(`evals/evaluator_eval/combined_leaderboard.json`) — verified_success_rate = a model's accuracy
against checker-decided gold, the least-biased signal available. Cost/latency/token columns exist
but are NULL and flagged `gateway_not_wired`: honest absence, never a fabricated number. Sparse
cells back off task_type -> global (no suggestion below a minimum n).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cortex_core.config import resolve_workspace

MIN_N = 20  # no verified-success suggestion below this many tasks (BUILD-PLAN §1 backoff rule)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_scorecards (
  model TEXT NOT NULL, provider TEXT, task_type TEXT NOT NULL,
  n_tasks INTEGER NOT NULL, verified_success_rate REAL,
  self_report_vs_verified_gap REAL,
  avg_cost_usd REAL, p50_latency_s REAL, avg_output_tokens REAL,
  source TEXT NOT NULL, window TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (model, task_type, window)
);
"""


@dataclass
class Scorecard:
    model: str
    provider: str
    task_type: str
    n_tasks: int
    verified_success_rate: float | None
    source: str
    self_report_vs_verified_gap: float | None = None
    avg_cost_usd: float | None = None       # gateway-sourced (Phase 6) -> None until wired
    p50_latency_s: float | None = None
    avg_output_tokens: float | None = None

    def asdict(self):
        d = self.__dict__.copy()
        d["gateway_metrics"] = "not_wired (Phase 6 LiteLLM/OTel seam)"
        return d


def _db(ws: Path) -> sqlite3.Connection:
    d = ws / "scorecards"
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(d / "scorecards.sqlite")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def _upsert(conn, sc: Scorecard, window: str, now: str):
    conn.execute(
        "INSERT OR REPLACE INTO model_scorecards (model,provider,task_type,n_tasks,"
        "verified_success_rate,self_report_vs_verified_gap,avg_cost_usd,p50_latency_s,"
        "avg_output_tokens,source,window,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sc.model, sc.provider, sc.task_type, sc.n_tasks, sc.verified_success_rate,
         sc.self_report_vs_verified_gap, sc.avg_cost_usd, sc.p50_latency_s, sc.avg_output_tokens,
         sc.source, window, now))


def ingest_evaluator_eval(workspace: Path | None = None, window: str = "eval-2026-07",
                          now: str | None = None) -> int:
    """Roll the objective evaluator-eval leaderboard into scorecards (source #1: logic checks).

    verified_success_rate = the model's accuracy vs checker-decided gold on the objective-
    evaluation task type. Only RELIABLE rows (adequate parse rate) are ingested.
    """
    ws = resolve_workspace(workspace) if workspace is None else Path(workspace)
    lb_path = ws / "evals" / "evaluator_eval" / "combined_leaderboard.json"
    if not lb_path.exists():
        return 0
    lb = json.loads(lb_path.read_text(encoding="utf-8"))
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn = _db(ws)
    n = 0
    for r in lb.get("leaderboard", []):
        if not r.get("reliable") or r.get("accuracy") is None:
            continue
        sc = Scorecard(model=r["model"], provider=r.get("family", "?"),
                       task_type="objective_evaluation", n_tasks=r.get("parseable", 0),
                       verified_success_rate=r["accuracy"],
                       source="objective_checker_eval (logic-check tier)")
        _upsert(conn, sc, window, now)
        n += 1
    conn.commit()
    conn.close()
    return n


def query(model: str, task_type: str = "objective_evaluation",
          workspace: Path | None = None) -> dict | None:
    """Scorecard lookup with task_type -> global backoff and a min-n suggestion gate."""
    ws = resolve_workspace(workspace) if workspace is None else Path(workspace)
    conn = _db(ws)
    row = conn.execute(
        "SELECT model,provider,task_type,n_tasks,verified_success_rate,source FROM model_scorecards "
        "WHERE model=? AND task_type=? ORDER BY updated_at DESC LIMIT 1", (model, task_type)).fetchone()
    if row is None:  # backoff: any task_type for this model
        row = conn.execute(
            "SELECT model,provider,task_type,n_tasks,verified_success_rate,source FROM model_scorecards "
            "WHERE model=? ORDER BY n_tasks DESC LIMIT 1", (model,)).fetchone()
    conn.close()
    if row is None:
        return None
    n = row[3]
    return {"model": row[0], "provider": row[1], "task_type": row[2], "n_tasks": n,
            "verified_success_rate": row[4], "source": row[5],
            "suggestion_eligible": n >= MIN_N,
            "note": None if n >= MIN_N else f"n={n} < MIN_N={MIN_N}; no suggestion emitted",
            "gateway_metrics": "not_wired (Phase 6)"}


def leaderboard(task_type: str = "objective_evaluation", workspace: Path | None = None) -> list:
    ws = resolve_workspace(workspace) if workspace is None else Path(workspace)
    conn = _db(ws)
    rows = conn.execute(
        "SELECT model,provider,verified_success_rate,n_tasks,source FROM model_scorecards "
        "WHERE task_type=? ORDER BY verified_success_rate DESC", (task_type,)).fetchall()
    conn.close()
    return [{"model": r[0], "provider": r[1], "verified_success_rate": r[2], "n_tasks": r[3],
             "source": r[4]} for r in rows]


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Model scorecards (Phase 6 core)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    pq = sub.add_parser("query"); pq.add_argument("model"); pq.add_argument("--task-type", default="objective_evaluation")
    pl = sub.add_parser("leaderboard"); pl.add_argument("--task-type", default="objective_evaluation")
    a = p.parse_args(argv)
    if a.cmd == "ingest":
        print(f"ingested {ingest_evaluator_eval()} model scorecards from the objective eval")
    elif a.cmd == "query":
        print(json.dumps(query(a.model, a.task_type), indent=2))
    elif a.cmd == "leaderboard":
        for r in leaderboard(a.task_type):
            print(f"  {r['model']:28s} verified={r['verified_success_rate']} n={r['n_tasks']} [{r['provider']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
