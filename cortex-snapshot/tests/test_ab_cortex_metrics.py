"""A3 (GAP-CLOSURE): the REAL agent-invoker must write a per-trial metrics.json
so the cost/context axes stop being null and summarize() can roll them up.

RED-first: written before the runner.py wiring exists. No LLM/judge/network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1] / "evals" / "ab_cortex_scaffold"
sys.path.insert(0, str(HARNESS_ROOT))

import runner as runner_mod  # noqa: E402


def _write_trial(trial_dir: Path, *, with_metrics: bool) -> None:
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "meta.json").write_text(
        json.dumps({"run_id": "t-x", "arm": "B", "milestone": "precommit_smoke", "task": "x"}),
        encoding="utf-8")
    (trial_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": 1, "type": "tool_call", "tool": "search"}) + "\n"
        + json.dumps({"ts": 2, "type": "mutation", "path": "README.md"}) + "\n",
        encoding="utf-8")
    if with_metrics:
        (trial_dir / "metrics.json").write_text(json.dumps({
            "wall_clock_s": 12.5, "tokens_total": 8000, "cost_usd": 0.034,
            "context_resting_tokens": 5000, "context_peak_tokens": 9000,
        }), encoding="utf-8")


# --- the helper the real invoker uses to persist metrics ---

def test_write_metrics_json_from_agent_sidecar(tmp_path):
    trial = tmp_path / "trial"
    _write_trial(trial, with_metrics=False)
    # the agent reports what only it can know (tokens/cost/context) in a sidecar
    (trial / "agent_metrics.json").write_text(json.dumps({
        "tokens_total": 8000, "cost_usd": 0.034,
        "context_resting_tokens": 5000, "context_peak_tokens": 9000,
    }), encoding="utf-8")

    runner_mod.write_trial_metrics(trial, wall_clock_s=12.5)

    m = json.loads((trial / "metrics.json").read_text(encoding="utf-8"))
    assert m["wall_clock_s"] == 12.5           # harness-measured, authoritative
    assert m["cost_usd"] == 0.034              # merged from the agent sidecar
    assert m["context_resting_tokens"] == 5000
    assert m["context_peak_tokens"] == 9000
    assert m["tokens_total"] == 8000


def test_write_metrics_json_without_sidecar_is_honest_null(tmp_path):
    """No sidecar -> wall clock is still recorded, cost/context are honestly
    null (the harness must not fabricate cost)."""
    trial = tmp_path / "trial"
    _write_trial(trial, with_metrics=False)
    runner_mod.write_trial_metrics(trial, wall_clock_s=3.0)
    m = json.loads((trial / "metrics.json").read_text(encoding="utf-8"))
    assert m["wall_clock_s"] == 3.0
    assert m["cost_usd"] is None
    assert m["context_resting_tokens"] is None


def test_command_invoker_writes_metrics_json(tmp_path):
    """The real (CommandAgentInvoker) path must always leave a metrics.json,
    merging an agent-emitted sidecar."""
    trial = tmp_path / "trial"
    _write_trial(trial, with_metrics=False)
    # a trivial 'agent' script that emits the sidecar. Run via a script file so
    # the command template (which uses str.format) contains no literal braces.
    agent_script = tmp_path / "fake_agent.py"
    agent_script.write_text(
        "import json, pathlib\n"
        "pathlib.Path('agent_metrics.json').write_text(json.dumps({\n"
        "  'tokens_total': 100, 'cost_usd': 0.01,\n"
        "  'context_resting_tokens': 500, 'context_peak_tokens': 900}))\n",
        encoding="utf-8")
    py = sys.executable
    cmd = f'"{py}" "{agent_script}"'
    invoker = runner_mod.CommandAgentInvoker(cmd)
    invoker.run(trial_dir=trial, arm="B", milestone="precommit_smoke",
                task_prompt="x", seed_dir=HARNESS_ROOT / "SEEDED-REPO")

    assert (trial / "metrics.json").exists()
    m = json.loads((trial / "metrics.json").read_text(encoding="utf-8"))
    assert m["cost_usd"] == 0.01
    assert m["context_peak_tokens"] == 900
    assert isinstance(m["wall_clock_s"], (int, float)) and m["wall_clock_s"] >= 0


def test_summarize_rolls_up_nonnull_cost_and_context(tmp_path):
    """A results.jsonl whose verdicts carry a metrics bundle (from a trial dir
    that HAS a metrics.json) must roll up non-null cost/context axes."""
    import evaluator as evaluator_mod  # noqa: E402

    results = tmp_path / "results.jsonl"
    with results.open("w", encoding="utf-8") as f:
        for i in range(2):
            trial = tmp_path / f"trial-{i}"
            _write_trial(trial, with_metrics=True)
            verdict = evaluator_mod.evaluate_trial(trial, harness_root=HARNESS_ROOT)
            verdict["trial_idx"] = i
            f.write(json.dumps(verdict, default=str) + "\n")

    summary = runner_mod.summarize(results)
    b = summary["B"]
    assert b["mean_cost_usd"] is not None and b["mean_cost_usd"] > 0
    assert b["mean_context_resting_tokens"] is not None
    assert b["mean_context_peak_tokens"] is not None
    assert b["mean_tokens_total"] == 8000
