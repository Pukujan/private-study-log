"""Frozen tests for model scorecards (cortex_core/scorecards.py) — Phase 6 core."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import scorecards as SC  # noqa: E402


def _seed_leaderboard(ws: Path):
    d = ws / "evals" / "evaluator_eval"
    d.mkdir(parents=True)
    (d / "combined_leaderboard.json").write_text(json.dumps({"leaderboard": [
        {"model": "fable", "family": "anthropic", "accuracy": 0.944, "parseable": 72, "reliable": True},
        {"model": "prometheus", "family": "open-eval", "accuracy": 0.652, "parseable": 66, "reliable": True},
        {"model": "flaky", "family": "x", "accuracy": 1.0, "parseable": 4, "reliable": False},
        {"model": "tiny", "family": "y", "accuracy": 0.9, "parseable": 10, "reliable": True},
    ]}), encoding="utf-8")


def test_ingest_only_reliable_rows(tmp_path):
    _seed_leaderboard(tmp_path)
    n = SC.ingest_evaluator_eval(tmp_path)
    assert n == 3  # the unreliable 'flaky' row is excluded
    models = {r["model"] for r in SC.leaderboard(workspace=tmp_path)}
    assert "flaky" not in models and "fable" in models


def test_verified_rate_is_the_accuracy(tmp_path):
    _seed_leaderboard(tmp_path)
    SC.ingest_evaluator_eval(tmp_path)
    q = SC.query("fable", workspace=tmp_path)
    assert q["verified_success_rate"] == 0.944 and "logic-check" in q["source"]


def test_min_n_gates_suggestion(tmp_path):
    _seed_leaderboard(tmp_path)
    SC.ingest_evaluator_eval(tmp_path)
    assert SC.query("fable", workspace=tmp_path)["suggestion_eligible"] is True   # n=72
    assert SC.query("tiny", workspace=tmp_path)["suggestion_eligible"] is False   # n=10 < MIN_N


def test_gateway_metrics_marked_not_wired_never_faked(tmp_path):
    _seed_leaderboard(tmp_path)
    SC.ingest_evaluator_eval(tmp_path)
    q = SC.query("fable", workspace=tmp_path)
    assert "not_wired" in q["gateway_metrics"]  # honest absence, not a fabricated cost/latency


def test_query_backs_off_when_task_type_missing(tmp_path):
    _seed_leaderboard(tmp_path)
    SC.ingest_evaluator_eval(tmp_path)
    # a different task_type falls back to the model's best available row
    q = SC.query("fable", task_type="nonexistent_task", workspace=tmp_path)
    assert q is not None and q["model"] == "fable"
