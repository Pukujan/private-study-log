"""Production lane adapters — real checker dispatch over real third-party cases (P2 live).

The unit runner (`runner.py`) proves the aggregation logic; this wires a real deterministic
checker to a real third-party dataset and a real (free) model, producing the artifact that
answers "is a free model good enough?": a judge-free pass-rate on cases NO Anthropic model
authored. First lane = coding (mbpp) via the objective_coding `check_solution` test-executor.

Verdict vocabulary (kept distinct, per P4): pass / fail (code ran, tests decided) /
parse_fail (model ignored the code contract) / abstain (no gradable case).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

from . import case_authorship, extract
from . import research
from .metrics import wilson_ci

_REPO = pathlib.Path(__file__).resolve().parent.parent
_LEDGER = _REPO / "evals" / "promotion_decisions" / "stage2_objective_promotions.jsonl"
_REPORTS = _REPO / "evals" / "reports"


def _check_solution():
    """Load evals/objective_coding/checker.check_solution by path (avoids package setup)."""
    p = _REPO / "evals" / "objective_coding" / "checker.py"
    spec = importlib.util.spec_from_file_location("_obj_coding_checker", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # so the module's @dataclass can resolve __module__
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.check_solution


def build_coding_prompt(record: dict) -> str:
    prob = record["problem"]
    tests = prob.get("test_list", [])
    hint = tests[0] if tests else ""
    return (
        "Write a single self-contained Python function that solves this task.\n"
        f"Task: {prob['prompt']}\n"
        f"It must satisfy, for example: {hint}\n"
        "Respond with ONLY the function definition inside a ```python code block — no prose."
    )


def grade_coding_record(record: dict, model_output: str, check_solution=None) -> tuple[str, str]:
    """Return (verdict, detail). verdict in {pass, fail, parse_fail, abstain}."""
    tests = record.get("problem", {}).get("test_list", [])
    if not tests:
        return ("abstain", "no test_list")
    if not extract.has_code_fence(model_output):
        return ("parse_fail", "no code block in output")
    code = extract.normalize_output(model_output, "code_only").code or ""
    if not code.strip():
        return ("parse_fail", "empty code block")
    # Hold out the last test as a hidden check (anti-gaming); rest are visible.
    visible = "\n".join(tests[:-1]) if len(tests) > 1 else tests[0]
    hidden = tests[-1] if len(tests) > 1 else ""
    check = check_solution or _check_solution()
    res = check(code, visible, hidden)
    return ("pass" if res.verdict == "pass" else "fail", res.failed_stage)


def run_coding_lane(model_tier: str, dataset_path: str, max_cases: int = 20,
                    max_tokens: int = 900, verbose: bool = True, complete_fn=None) -> dict:
    """Grade a free/any model over a real third-party coding lane; write scoreboard + ledger.

    `complete_fn(prompt) -> str|None` overrides dispatch (for models reachable only by raw
    model-id on a shared endpoint, e.g. opencode-go); default uses the `model_tier` dispatch.
    """
    path = pathlib.Path(dataset_path)
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))
            if len(records) >= max_cases:
                break

    check = _check_solution()  # load once
    counts = {"pass": 0, "fail": 0, "parse_fail": 0, "abstain": 0}
    skipped_non_third_party = 0
    for i, rec in enumerate(records, 1):
        author = case_authorship.classify(str(path))
        if author != "third_party":
            skipped_non_third_party += 1
            continue
        prompt = build_coding_prompt(rec)
        raw = complete_fn(prompt) if complete_fn else research._llm_complete(prompt, model_tier, max_tokens)
        out = raw or ""
        verdict, detail = grade_coding_record(rec, out, check_solution=check)
        counts[verdict] += 1
        if verbose:
            print(f"[{i:3d}/{len(records)}] {verdict:9s} {rec.get('task_id','')} {detail}")

    graded = counts["pass"] + counts["fail"]  # parse_fail/abstain excluded from accuracy denom
    n_total = sum(counts.values())
    accuracy = counts["pass"] / graded if graded else 0.0
    lo, hi = wilson_ci(counts["pass"], graded)
    scoreboard = {
        "lane": "coding", "case_authorship": "third_party", "source": str(path),
        "model": model_tier, "label_authority": "objective_checker:check_solution",
        "judge_in_verdict_path": False,
        "n_total": n_total, "graded": graded, **counts,
        "accuracy": round(accuracy, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4),
        "parse_failure_rate": round(counts["parse_fail"] / n_total, 4) if n_total else 0.0,
    }

    _REPORTS.mkdir(parents=True, exist_ok=True)
    safe_tier = model_tier.replace("/", "_")
    (_REPORTS / f"scoreboard_{safe_tier}_coding.json").write_text(
        json.dumps(scoreboard, indent=2), encoding="utf-8")
    with _LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(scoreboard) + "\n")
    if verbose:
        print(f"\nSCOREBOARD {model_tier} coding: pass={counts['pass']}/{graded} "
              f"acc={accuracy:.3f} [{lo:.3f},{hi:.3f}] parse_fail={counts['parse_fail']} "
              f"-> {_REPORTS / f'scoreboard_{safe_tier}_coding.json'}")
    return scoreboard
