"""Phase 4.4 LLM-judge dispatch: the *semantic* half of the evaluator.

`cortex_core/evaluator.py` grades structurally (evidence type + count + lexical
relevance) — cheap, deterministic scaffolding that catches gross evidence-theater
and missing evidence but can't judge whether evidence *actually* supports a claim.
This module adds that judgment via an LLM, preserving MARCH asymmetry: the judge
sees ONLY the claim (task + task_type) and the evidence — never the actor's prose.

Multi-tier, cross-vendor by design (the agnosticism rule: don't let a model grade
its own family's work — since the actors here are usually Claude, the API judges are
non-Claude). The API tiers below are configured from environment (.env), all
OpenAI-compatible `/chat/completions` endpoints:

  glm5.2    — GLM 5.2  (strong contender; free, 1 concurrent)
  qwen35b   — Qwen 3.6 (lower tier; free, slower, 2 concurrent)
  deepseek  — DeepSeek (lower tier; cheap)
  openrouter— gateway  (PAID — use sparingly, smaller tests only)
  ninerouter— proxy gateway with multiple upstream APIs

JUDGE LADDER (JUDGE axis = measured Cohen's kappa vs Fable gold; see JUDGE_LADDER).
This is NOT the dispatch/capability axis (cortex_core/model_tiers.py) — a strong
executor can be a weak judge (qwen35b: 0.982 executor, kappa 0.227 judge).
    1. Fable Max   — PRIMARY / gold judge; the anchor every lower rung calibrates to
    2. Opus        — secondary anchor (0.931 objective)
    3. Sonnet      — kappa 0.924 @rubric_v2
    4. Haiku       — kappa 0.922 @rubric_v2 (ties Sonnet; cheapest high-stakes judge)
    5. GLM 5.2     — kappa 0.702 (general-trust)
    6. ChatGPT 5.5xhigh — public-strong but UNMEASURED on our task (provisional)
    7. Qwen 4B (Ollama) — kappa 0.604 @v1 only (calibrated bulk-screen lane)
    8. OpenRouter (google/gemma-3-27b-it) — kappa 0.459
    9. DeepSeek   — kappa 0.405 (strong coder, weak judge)
   10+. 9router gateway lanes (declared != served unverified) + opencode executor
        lanes — provisional judges; ...LAST: qwen35b (kappa 0.227, worst measured).
Use the strongest judge affordable for the stakes; the ladder exists for cost/volume,
and every rung below Fable is only trusted as far as it agrees with Fable (measured
by the calibration harness).

The top two rungs — Fable Max and Opus — are NOT API tiers and are deliberately
absent from _TIER_ENV: they run *in-harness* (this Claude Code session's own model,
or a subagent spawned with model="fable"/"opus"), not behind a REST endpoint. So the
gold/secondary judges are orchestrated at the harness level (calibration anchor /
high-stakes claims), while this module handles the cheaper external rungs (GLM,
DeepSeek, Qwen, Ollama) and the paid OpenRouter gateway. (Haiku is available in-harness
too as an extra cheap rung, but is not part of the ranked ladder above.)

No new hard dependency: uses httpx (already present via anthropic). Reads .env with
a tiny built-in parser so it works without python-dotenv.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from . import otel
from .audit import validate_evidence
from .evaluator import AtomicClaim, EvaluatorGrade, Verdict

# --------------------------------------------------------------------------- #
# Model DISPATCH plumbing now lives in the PUBLIC-safe cortex_core.model_dispatch #
# module (extracted 2026-07-14). judge.py RE-EXPORTS every dispatch name from     #
# there so all existing `judge.<name>` call sites keep working unchanged, while    #
# fanout.py / model_probe.py import dispatch WITHOUT importing this private judge   #
# module. This module keeps ALL of its judging / rubric / calibration / ladder IP. #
# --------------------------------------------------------------------------- #
from .model_dispatch import (  # noqa: E402,F401 -- re-export for backward compat
    _TIER_ENV,
    NINEROUTER_MAX_CONCURRENCY,
    NINEROUTER_TIERS,
    PROMETHEUS_TIERS,
    PROMETHEUS_MAX_CONCURRENCY,
    PROMETHEUS_PC_SHARES_LOCAL_GPU,
    _LOCAL_NOKEY_TIERS,
    _OLLAMA_DEFAULT_URL,
    _PLACEHOLDERS,
    CLI_TIERS,
    CODEX_CLI_BIN,
    CLAUDE_CLI_BIN,
    CLI_MODEL_MAP,
    OPENCODE_MODEL_ALLOWLIST,
    OPENCODE_TIERS,
    OPENCODE_ZEN_MODEL_ALLOWLIST,
    OPENCODE_ZEN_TIERS,
    MIN_MAX_TOKENS_BY_TIER,
    apply_min_max_tokens,
    MAX_CONCURRENT_BY_TIER,
    _LOCK_DIR,
    _LOCK_STALE_S,
    ConcurrencySlotTimeoutError,
    concurrency_slot,
    TierConfig,
    load_env,
    _resolve_codex_cli_bin,
    get_tier_config,
    list_ollama_models,
    _chat_completions_url,
    _extract_content,
    _extract_usage,
    _extract_sse_content,
    _is_sse_response,
    _response_text,
    llm_complete,
)

# Canonical judge ladder, strongest first. (rank, tier, access). The top rungs
# run in-harness (subagent), the rest are dispatched by this module. "access" tells
# the calibration harness which path to use.
#
# NOTE: this ordering is the JUDGE axis (Cohen's kappa agreement with the Fable-Max
# gold) -- NOT the dispatch/capability axis (that lives in cortex_core/model_tiers.py;
# a strong executor can be a weak judge). The top rungs are now grounded on MEASURED
# kappa, no longer a pure name-prior; the un-measured rungs stay provisional / probe-
# first and are called out per-line. fable-max is fixed at #1 (it IS the gold anchor,
# not measured against itself). Measured kappa (calibration/CALIBRATION-REPORT-2026-07-04.md,
# reconciled in docs/research/model-tier-list-benchmarked-2026-07-14.md §3/§6b):
#   sonnet@v2 0.924 · haiku@v2 0.922 · glm5.2@v2 0.702 · ollama qwen3-4b@v1 0.604 ·
#   openrouter gemma-3-27b 0.459 · deepseek/v4-flash 0.405 · qwen35b 0.227 (worst).
# Corrections vs the old prior: haiku 7->4 (ties sonnet under rubric_v2); qwen35b 6->26
# (worst measured judge, despite a fine EXECUTOR score -- exec != judge); ollama up to a
# calibrated bulk-screen lane; deepseek down to its measured 0.405. Un-measured lanes
# (chatgpt-5.5xhigh on OUR task, every 9r-* gateway where declared != served, the
# opencode executor lanes) stay provisional -- calibrate before trusting as judges.
JUDGE_LADDER: list[tuple[int, str, str]] = [
    (1, "fable-max", "cli"),          # Claude Fable via claude -p --model fable (gold anchor, fixed)
    (2, "opus", "cli"),               # Claude Opus via claude -p --model opus (0.931 objective; 2nd anchor)
    (3, "sonnet", "cli"),             # Claude Sonnet via claude -p --model sonnet (kappa 0.924)
    (4, "haiku", "cli"),              # Claude Haiku via claude -p --model haiku (kappa 0.922 -- MOVED UP from 7; cheapest high-stakes judge @rubric_v2)
    (5, "glm5.2", "api"),             # Umans GLM-5.2 (kappa 0.702 general-trust; obj 0.928)
    (6, "chatgpt-5.5xhigh", "cli"),   # ChatGPT 5.5xhigh via codex CLI -- public-strong but UNMEASURED on our task; provisional
    (7, "ollama", "local"),           # qwen3:4b-16k (kappa 0.604 @rubric v1 ONLY -- calibrated bulk-screen judge; NEVER give it v2)
    (8, "openrouter", "api"),         # google/gemma-3-27b-it (kappa 0.459; paid)
    (9, "deepseek", "api"),           # DeepSeek API (kappa 0.405 -- strong coder, weak judge)
    # --- 9router gateway lanes: declared != served is unverified (Phase-6 gap); provisional judges ---
    (10, "ninerouter", "api"),        # 9router proxy gateway (umans-glm-5.2)
    (11, "9r-sonnet-4.6", "api"),     # Anthropic Sonnet 4.6 via 9Router (free)
    (12, "9r-opus-4.6", "api"),       # Anthropic Opus 4.6 via 9Router (free)
    (13, "9r-gemini-3.1-pro", "api"), # Gemini 3.1 Pro via 9Router (free)
    (14, "9r-gemini-3.5-flash", "api"), # Gemini 3.5 Flash via 9Router (free)
    (15, "9r-gpt-oss-120b", "api"),   # OpenAI GPT-OSS 120B via 9Router (free)
    (16, "9r-deepseek-3.2", "api"),   # DeepSeek 3.2 via 9Router (free)
    (17, "9r-sonnet-4.5", "api"),     # Sonnet 4.5 via 9Router (free)
    (18, "9r-gemini-3-flash", "api"), # Gemini 3 Flash via 9Router (free)
    (19, "9r-gemini-preview", "api"), # Gemini 3 Flash preview -- UNKNOWN/probe-first (429 rate-limited)
    (20, "9r-gpt-oss-ollama", "api"), # GPT-OSS 120B via Ollama on 9Router (free)
    # --- opencode executor lanes: strong/measured EXECUTORS, un-measured/weak JUDGES ---
    (21, "opencode", "api"),          # deepseek-v4-flash (exec 1.000 our lane; judge kappa 0.405)
    (22, "opencode2", "api"),         # opencode-go acct 2
    (23, "opencode-zen", "api"),      # big-pickle (exec 0.964 our lane; judge un-measured)
    (24, "opencode-zen2", "api"),     # opencode-zen acct 2
    (25, "ninerouter-aux", "api"),    # 9router "aux" -> big-pickle
    (26, "qwen35b", "api"),           # qwen3.6-35b-a3b -- kappa 0.227, WORST measured judge (DEMOTED from 6); fine executor, never a judge
]

# The API/local rungs this module can dispatch directly (excludes in-harness rungs,
# which the orchestrator runs as subagents).
DISPATCHABLE_TIERS = [tier for _, tier, access in JUDGE_LADDER if access != "in-harness"]
IN_HARNESS_TIERS = [tier for _, tier, access in JUDGE_LADDER if access == "in-harness"]


_SYSTEM_PROMPT = """\
You are an impartial EVALUATOR in an audit system. You grade whether the supplied \
EVIDENCE actually supports a CLAIM about completed work.

Critical rules:
- You are given ONLY the claim (a task description + task_type) and a list of \
evidence items. You are NOT given the worker's own account of what they did. Grade \
the evidence, not any narrative.
- Judge SEMANTIC support: does this specific evidence plausibly demonstrate this \
specific claim? Evidence of the right *shape* that is unrelated to the claim \
(evidence-theater) must NOT count as support.
- Do not assume facts not in the evidence. If you cannot tell, say so.

Return ONLY a JSON object, no prose around it, with keys:
  "verdict": one of "supported" | "partially_supported" | "unsupported" | "unverifiable"
  "confidence": number 0.0-1.0
  "reasoning": one or two sentences, concrete
  "gaps": array of short strings naming what is missing or unconvincing (may be empty)

Verdict guide:
- "supported": the evidence, taken together, convincingly demonstrates the claim.
- "partially_supported": some relevant evidence, but a needed piece is missing/weak.
- "unsupported": evidence is present but does not relate to / does not show the claim.
- "unverifiable": too little to judge, or evidence cannot be assessed."""


PROMETHEUS_SCORE_RUBRIC = """\
Score the response according to the following rubric:
- Score 1: unsupported. The response does not demonstrate the claim; evidence is irrelevant, missing, or actively contradicts the claim.
- Score 2: partially_supported. The response touches on the claim but is incomplete, ambiguous, or partly off-target.
- Score 3: verifiable_but_flawed. The response is relevant and verifiable, but contains minor errors, omissions, or unclear reasoning that prevent full support.
- Score 4: supported. The response convincingly demonstrates the claim with adequate, correct evidence.
- Score 5: strongly_supported. The response demonstrates the claim thoroughly, precisely, and without meaningful gaps.

Output ONLY the score in the exact format: [RESULT] <integer>"""


def _build_prometheus_prompt(
    claim: AtomicClaim,
    evidence_list: list[dict[str, Any]],
    reference_answer: str | None = None,
) -> str:
    """Build the native Prometheus-Eval absolute-rating template.

    Prometheus is trained on the |Instruction|...|Response|...|Reference Answer|...|Score Rubric|...
    format. Returning to this template fixed the kappa=0 failure observed when the
    generic JSON prompt was used.
    """
    ev_lines = []
    for i, e in enumerate(evidence_list, 1):
        t = e.get("type", "?")
        ref = e.get("ref", "")
        detail = e.get("detail", "")
        ev_lines.append(f"{i}. [{t}] {ref}" + (f" — {detail}" if detail else ""))
    ev_block = "\n".join(ev_lines) if ev_lines else "(no evidence provided)"
    ref_block = reference_answer if reference_answer is not None else "(no reference answer provided)"
    return f"""###Task Description:
Grade whether the evidence supports the claim below.

|Instruction|
CLAIM (task_type={claim.task_type}):
{claim.description}

|Response|
{ev_block}

|Reference Answer|
{ref_block}

|Score Rubric|
{PROMETHEUS_SCORE_RUBRIC}

###Response:
"""


def _parse_prometheus_response(text: str, claim_id: str, evidence_count: int, tier: str) -> EvaluatorGrade:
    """Parse a Prometheus [RESULT] N line into an EvaluatorGrade."""
    match = re.search(r"\[RESULT\]\s*(\d+)", text)
    if not match:
        return EvaluatorGrade(
            claim_id=claim_id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            reasoning=f"[{tier}] judge returned unparseable output",
            evidence_count=evidence_count,
            gaps=["Judge response did not contain [RESULT] N"],
        )
    score = int(match.group(1))
    mapping: dict[int, tuple[Verdict, float]] = {
        1: (Verdict.UNSUPPORTED, 0.0),
        2: (Verdict.PARTIALLY_SUPPORTED, 0.5),
        3: (Verdict.VERIFIABLE_BUT_FLAWED, 0.7),
        4: (Verdict.SUPPORTED, 0.9),
        5: (Verdict.STRONGLY_SUPPORTED, 1.0),
    }
    verdict, confidence = mapping.get(score, (Verdict.UNVERIFIABLE, 0.0))
    if verdict == Verdict.UNVERIFIABLE:
        return EvaluatorGrade(
            claim_id=claim_id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            reasoning=f"[{tier}] judge returned out-of-range score {score}",
            evidence_count=evidence_count,
            gaps=[f"Score {score} is outside the 1-5 Prometheus rubric"],
        )
    return EvaluatorGrade(
        claim_id=claim_id,
        verdict=verdict,
        confidence=confidence,
        reasoning=f"[{tier}] Prometheus score {score} -> {verdict.value}",
        evidence_count=evidence_count,
        gaps=[],
    )


def _build_user_prompt(
    claim: AtomicClaim, evidence_list: list[dict[str, Any]], style: str = "direct"
) -> str:
    """Frame a case for the judge. ``style`` is the PROMPT-VERSIONING axis (distinct
    from the rubric/system-prompt axis):
      direct         — ask for the JSON verdict straight away (default).
      reasoning-first — let the judge reason briefly, THEN emit the JSON. The parser
                        already extracts the trailing JSON, and reasoning-before-commit
                        tends to help weaker judges (the "encourage reasoning" finding).
    """
    ev_lines = []
    for i, e in enumerate(evidence_list, 1):
        t = e.get("type", "?")
        ref = e.get("ref", "")
        detail = e.get("detail", "")
        ev_lines.append(f"{i}. [{t}] {ref}" + (f" — {detail}" if detail else ""))
    ev_block = "\n".join(ev_lines) if ev_lines else "(no evidence provided)"
    head = f"CLAIM (task_type={claim.task_type}):\n{claim.description}\n\nEVIDENCE:\n{ev_block}\n\n"
    if style in ("reasoning-first", "reasoning_first"):
        return head + (
            "First, in 2-4 sentences, reason about which verdict fits (can it be "
            "assessed? does anything contradict? is the evidence relevant? is it "
            "sufficient?). THEN, on its own at the end, output ONLY the JSON object."
        )
    return head + "Grade whether the evidence supports the claim. Return the JSON object only."


_VALID_VERDICTS = {v.value for v in Verdict}


def _parse_judge_response(text: str, claim_id: str, evidence_count: int, tier: str) -> EvaluatorGrade:
    """Parse the model's JSON verdict. Robust to markdown fences / surrounding prose."""
    # Robust to REASONING models (chain-of-thought / <think> tags / fences / braces in
    # the reasoning). The old greedy `{.*}` over-captured and dropped qwen/GLM verdicts to
    # UNVERIFIABLE -- silently sabotaging the independent-panel thesis. Shared parser now.
    from .llm_parse import extract_json_object
    obj = extract_json_object(text)
    if obj is None:
        return EvaluatorGrade(
            claim_id=claim_id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            reasoning=f"[{tier}] judge returned unparseable output",
            evidence_count=evidence_count,
            gaps=["Judge response was not valid JSON"],
        )
    verdict_str = str(obj.get("verdict", "")).lower().strip()
    if verdict_str == "unknown":
        verdict_str = "unverifiable"
    if verdict_str not in _VALID_VERDICTS:
        verdict = Verdict.UNVERIFIABLE
    else:
        verdict = Verdict(verdict_str)
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    gaps = obj.get("gaps") or []
    if not isinstance(gaps, list):
        gaps = [str(gaps)]
    reasoning = str(obj.get("reasoning", "")).strip() or "(no reasoning given)"
    return EvaluatorGrade(
        claim_id=claim_id,
        verdict=verdict,
        confidence=confidence,
        reasoning=f"[{tier}] {reasoning}",
        evidence_count=evidence_count,
        gaps=[str(g) for g in gaps],
    )


def llm_judge(
    claim: AtomicClaim,
    evidence_list: list[dict[str, Any]],
    tier: str = "glm5.2",
    workspace: str | Path | None = None,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    temperature: float = 0.0,
    max_tokens: int = 1500,
    retries: int = 2,
    system_prompt: str | None = None,
    prompt_style: str = "direct",
    model_override: str | None = None,
    http_post=None,
    session_id: str | None = None,
    prompt_id: str | None = None,
) -> EvaluatorGrade:
    """Grade a claim against evidence with an LLM judge (MARCH-asymmetric).

    File evidence is validated first (same as the rule-based path) so a bad ref
    short-circuits to UNVERIFIABLE without spending a token. Transient transport
    failures are retried ``retries`` times. ``http_post`` is an injection seam for
    tests: a callable(url, headers, json) -> object with .json() and
    .raise_for_status(); defaults to httpx.

    ``max_tokens`` defaults high (1500) because reasoning-tier judges (GLM/DeepSeek)
    can spend hundreds of tokens thinking before the JSON verdict — too small a
    budget truncates the closing brace and the verdict is lost.
    """
    # Reuse the rule-based file-resolution guard — cheap, deterministic, saves tokens.
    if any(e.get("type") == "file" for e in evidence_list):
        if workspace is None:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.UNVERIFIABLE, confidence=0.0,
                reasoning=f"[{tier}] file evidence present but no workspace to validate",
                evidence_count=len(evidence_list),
                gaps=["Workspace required to validate file references"],
            )
        bad = validate_evidence(evidence_list, workspace)
        if bad:
            return EvaluatorGrade(
                claim_id=claim.claim_id, verdict=Verdict.UNVERIFIABLE, confidence=0.0,
                reasoning=f"[{tier}] evidence references do not resolve: {bad}",
                evidence_count=len(evidence_list),
                gaps=[f"Evidence {r} cannot be found" for r in bad],
            )

    cfg = get_tier_config(tier, env=env)
    url = _chat_completions_url(cfg.url)
    headers = {
        "Authorization": f"Bearer {cfg.key}",
        "Content-Type": "application/json",
    }
    # Prometheus tiers use their native template, not the generic JSON prompt.
    if tier in PROMETHEUS_TIERS:
        user_content = _build_prometheus_prompt(claim, evidence_list)
        sys_content = "You are a fair evaluator. Score the response using the [RESULT] N format."
    else:
        user_content = _build_user_prompt(claim, evidence_list, prompt_style)
        sys_content = system_prompt or _SYSTEM_PROMPT

    payload = {
        "model": model_override or cfg.model,
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    last_exc: Exception | None = None
    content = ""
    in_tok: int | None = None
    out_tok: int | None = None
    # OTel cost/latency/token span (GAP I6). No-op unless the [otel] extra + CORTEX_OTEL are set;
    # when CORTEX_METRICS_LEDGER is set it also appends the disk A3 feed. prompt_id defaults to the
    # claim id so a verdict's full call chain is joinable to the client plane (PHASE-GATES 3.5).
    with otel.gen_ai_span("judge.llm_judge", session_id=session_id,
                          prompt_id=prompt_id or claim.claim_id,
                          model=(model_override or cfg.model), track="judge", env=env) as _span:
        for attempt in range(retries + 1):
            try:
                if http_post is not None:
                    resp = http_post(url, headers=headers, json=payload)
                else:
                    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
                resp.raise_for_status()
                # 9Router (and some proxies) may return SSE even when stream=False.
                if _is_sse_response(resp):
                    text = _response_text(resp)
                    if text is not None:
                        content = _extract_sse_content(text)
                else:
                    data = resp.json()
                    content = _extract_content(data)
                    in_tok, out_tok = _extract_usage(data)
                if content:
                    break
                last_exc = ValueError("empty content in judge response")
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429 and attempt < retries:
                    # Exponential backoff starting at 1s, capped at 8s.
                    backoff = min(2 ** attempt, 8.0)
                    time.sleep(backoff)
                    continue
            except Exception as exc:  # noqa: BLE001 — surface any transport/shape error as a grade
                last_exc = exc
            # brief backoff between attempts (skipped after the final try)
        if in_tok is not None or out_tok is not None:
            _span.set_usage(input_tokens=in_tok, output_tokens=out_tok)

    if not content:
        err_name = type(last_exc).__name__ if last_exc else 'no content'
        err_detail = str(last_exc) if last_exc else ""
        if isinstance(last_exc, httpx.HTTPStatusError) and last_exc.response.status_code == 429:
            err_name = "429 rate limit"
            err_detail = "rate limited after retries"
        return EvaluatorGrade(
            claim_id=claim.claim_id, verdict=Verdict.UNVERIFIABLE, confidence=0.0,
            reasoning=f"[{tier}] judge call failed after {retries + 1} attempt(s): "
                      f"{err_name}: {err_detail}",
            evidence_count=len(evidence_list),
            gaps=["Judge call did not complete"],
        )

    # Prometheus tiers use their native [RESULT] N template, not JSON.
    if tier in PROMETHEUS_TIERS:
        return _parse_prometheus_response(content, claim.claim_id, len(evidence_list), tier)
    return _parse_judge_response(content, claim.claim_id, len(evidence_list), tier)


def call_cli_tier(
    tier: str,
    prompt: str,
    *,
    timeout: int = 120,
    model: str | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """Call a CLI-based tier via subprocess. Returns {ok, content, tokens}.

    Supports two CLI backends:
      - claude -p --model <alias>  (fable-max, opus, sonnet, haiku)
      - codex exec --json           (chatgpt-5.5xhigh)
    """
    import subprocess
    import sys as _sys
    import tempfile

    run_env = {**os.environ, **(env or {})}
    is_win = _sys.platform == "win32"

    # Resolve which CLI binary + model alias to use
    if tier not in CLI_MODEL_MAP:
        return {"ok": False, "error": f"Unknown CLI tier {tier!r}"}
    cli_bin, model_alias = CLI_MODEL_MAP[tier]
    # Explicit model override takes precedence
    if model is None:
        model = model_alias

    if cli_bin == "claude":
        # claude -p --model fable --output-format text
        # Prompt goes as last positional arg, but use stdin to avoid quoting issues
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as pf:
            pf.write(prompt)
            prompt_file = pf.name
        try:
            with open(prompt_file, 'r', encoding='utf-8') as stdin_f:
                if is_win:
                    cmd_parts = [cli_bin, "-p"]
                    if model:
                        cmd_parts += ["--model", model]
                    cmd_parts += ["--output-format", "text"]
                    cmd_str = subprocess.list2cmdline(cmd_parts)
                    try:
                        result = subprocess.run(
                            cmd_str,
                            stdin=stdin_f,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            env=run_env,
                            shell=True,
                        )
                    except subprocess.TimeoutExpired:
                        return {"ok": False, "error": f"CLI timed out after {timeout}s"}
                    except FileNotFoundError:
                        return {"ok": False, "error": f"CLI binary '{cli_bin}' not found"}
                    except Exception as e:
                        return {"ok": False, "error": str(e)[:200]}
                else:
                    cmd = [cli_bin, "-p"]
                    if model:
                        cmd += ["--model", model]
                    cmd += ["--output-format", "text"]
                    try:
                        result = subprocess.run(
                            cmd,
                            stdin=stdin_f,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            env=run_env,
                        )
                    except subprocess.TimeoutExpired:
                        return {"ok": False, "error": f"CLI timed out after {timeout}s"}
                    except FileNotFoundError:
                        return {"ok": False, "error": f"CLI binary '{cli_bin}' not found"}
                    except Exception as e:
                        return {"ok": False, "error": str(e)[:200]}
            if result.returncode != 0:
                return {"ok": False, "error": f"exit {result.returncode}: {result.stderr[:200]}"}
            content = result.stdout.strip()
            if not content:
                return {"ok": False, "error": "empty output from CLI"}
            return {"ok": True, "content": content, "tokens": 0}
        finally:
            try:
                os.unlink(prompt_file)
            except Exception:
                pass

    elif cli_bin == "codex":
        # codex exec --json -o <outfile> -  (prompt via stdin)
        bin_name = _resolve_codex_cli_bin(env)
        import json as _json
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as pf:
            pf.write(prompt)
            prompt_file = pf.name
        out_file = prompt_file + '.out'
        try:
            if is_win:
                cmd_str = subprocess.list2cmdline([bin_name, "exec", "--json", "-o", out_file, "-"])
                with open(prompt_file, 'r', encoding='utf-8') as stdin_f:
                    try:
                        result = subprocess.run(
                            cmd_str,
                            stdin=stdin_f,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            env=run_env,
                            shell=True,
                        )
                    except subprocess.TimeoutExpired:
                        return {"ok": False, "error": f"CLI timed out after {timeout}s"}
                    except FileNotFoundError:
                        return {"ok": False, "error": f"CLI binary '{bin_name}' not found"}
                    except Exception as e:
                        return {"ok": False, "error": str(e)[:200]}
            else:
                cmd = [bin_name, "exec", "--json", "-o", out_file, "-"]
                with open(prompt_file, 'r', encoding='utf-8') as stdin_f:
                    try:
                        result = subprocess.run(
                            cmd,
                            stdin=stdin_f,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            env=run_env,
                        )
                    except subprocess.TimeoutExpired:
                        return {"ok": False, "error": f"CLI timed out after {timeout}s"}
                    except FileNotFoundError:
                        return {"ok": False, "error": f"CLI binary '{bin_name}' not found"}
                    except Exception as e:
                        return {"ok": False, "error": str(e)[:200]}
            if result.returncode != 0:
                return {"ok": False, "error": f"exit {result.returncode}: {result.stderr[:200]}"}
            content = ""
            try:
                with open(out_file, 'r', encoding='utf-8') as of:
                    content = of.read().strip()
            except Exception:
                pass
            if not content and result.stdout:
                for line in result.stdout.strip().split('\n'):
                    try:
                        evt = _json.loads(line)
                        if evt.get("type") == "completed":
                            content = evt.get("last_message", "").strip()
                            break
                    except _json.JSONDecodeError:
                        continue
            if not content:
                content = result.stdout.strip()
            if not content:
                return {"ok": False, "error": "empty output from CLI"}
            return {"ok": True, "content": content, "tokens": 0}
        finally:
            for f in (prompt_file, out_file):
                try:
                    os.unlink(f)
                except Exception:
                    pass

    return {"ok": False, "error": f"Unknown CLI backend for tier {tier!r}"}
