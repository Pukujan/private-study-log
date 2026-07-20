"""Run the OpenAI Codex CLI as a judge over the blind anchor cases.

The Codex CLI is an OpenAI (GPT-5.x) agent runnable non-interactively. It is NOT an
OpenAI-compatible HTTP endpoint, so it can't go through judge.py's dispatch — it is
driven here by subprocess. Its value in calibration is as a cross-vendor (OpenAI)
independent bias anchor against the Anthropic-authored gold.

Invocation (read-only, blind to model identity, schema-enforced JSON):
  codex exec -C <repo> -s read-only --skip-git-repo-check
             --output-schema <schema> -o <msgfile> [-m <model>] [-c model_reasoning_effort=<e>]

The codex executable is machine-specific and off the tool PATH, so its absolute path
is read from CODEX_EXE (env/.env). If unset, this tier is simply skipped — no error.

Output: calibration/results/<label>_verdicts.json in the subagent-verdicts format,
so calibration.py's leaderboard/bias-audit pick it up like any other judge.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from .config import resolve_workspace
from .evaluator import AtomicClaim
from . import judge as J

# OpenAI strict structured-output requires EVERY property to be in `required`.
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["supported", "partially_supported", "unsupported", "unverifiable"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "confidence", "reasoning", "gaps"],
    "additionalProperties": False,
}


def codex_exe(env: dict[str, str] | None = None) -> str | None:
    env = env or J.load_env()
    exe = env.get("CODEX_EXE", "").strip()
    return exe or None


def run_codex_over_anchor(
    label: str,
    *,
    model: str = "",
    effort: str = "",
    system_prompt: str | None = None,
    workspace: str | Path | None = None,
    per_case_timeout: float = 300.0,
    max_cases: int | None = None,
    verbose: bool = True,
) -> dict[str, dict] | None:
    """Judge the blind anchor cases with Codex. Returns the verdicts dict, or None if
    CODEX_EXE is not configured. Writes calibration/results/<label>_verdicts.json."""
    ws = resolve_workspace(workspace) if workspace else resolve_workspace()
    exe = codex_exe()
    if not exe or not Path(exe).is_file():
        if verbose:
            print("CODEX_EXE not configured or not found — skipping codex judge.")
        return None

    blind = json.loads((ws / "calibration" / "anchor_cases_blind.json").read_text(encoding="utf-8"))
    if max_cases:
        blind = blind[:max_cases]
    verdicts: dict[str, dict] = {}

    with tempfile.TemporaryDirectory() as td:
        schema_file = Path(td) / "schema.json"
        schema_file.write_text(json.dumps(_VERDICT_SCHEMA), encoding="utf-8")
        for i, c in enumerate(blind, 1):
            claim = AtomicClaim(claim_id=c["id"], task_type=c["task_type"], description=c["claim"])
            prompt = (system_prompt or J._SYSTEM_PROMPT) + "\n\n" + J._build_user_prompt(claim, c["evidence"])
            msg_file = Path(td) / f"msg_{c['id']}.txt"
            cmd = [exe, "exec", "-C", str(ws), "-s", "read-only", "--skip-git-repo-check",
                   "--output-schema", str(schema_file), "-o", str(msg_file)]
            if model:
                cmd += ["-m", model]
            if effort:
                cmd += ["-c", f"model_reasoning_effort={effort}"]
            cmd += [prompt]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=per_case_timeout)
                raw = msg_file.read_text(encoding="utf-8").strip() if msg_file.exists() else ""
                grade = J._parse_judge_response(raw, c["id"], len(c["evidence"]), label)
                verdicts[c["id"]] = {"verdict": grade.verdict.value,
                                     "confidence": grade.confidence, "reason": grade.reasoning}
            except subprocess.TimeoutExpired:
                verdicts[c["id"]] = {"verdict": "unverifiable", "confidence": 0.0,
                                     "reason": "codex timeout"}
            if verbose:
                print(f"[{i:2d}/{len(blind)}] {c['id']}: {verdicts[c['id']]['verdict']}")

    out = ws / "calibration" / "results" / f"{label}_verdicts.json"
    out.write_text(json.dumps({"judge": label, "verdicts": verdicts}, indent=2), encoding="utf-8")
    if verbose:
        print(f"saved -> {out}")
    return verdicts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run Codex (OpenAI) as a calibration judge")
    p.add_argument("label")
    p.add_argument("--model", default="")
    p.add_argument("--effort", default="")
    p.add_argument("--rubric", default="v1", help="v1 | v2 | v2-lite")
    p.add_argument("--max-cases", type=int, default=None)
    a = p.parse_args(argv)
    from .calibration import load_rubric
    system_prompt = load_rubric(a.rubric) if a.rubric != "v1" else None
    run_codex_over_anchor(a.label, model=a.model, effort=a.effort,
                          system_prompt=system_prompt, max_cases=a.max_cases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
