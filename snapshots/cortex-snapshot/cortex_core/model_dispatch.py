"""Public-safe model DISPATCH plumbing, extracted from judge.py (2026-07-14).

This module holds the model-agnostic dispatch layer that used to live inside
``cortex_core/judge.py`` alongside the calibration / rubric / leaderboard IP:

  * tier -> (endpoint URL, API key, model) resolution from a provider .env
  * the cross-process concurrency semaphore (shared-account rate-limit caps)
  * the per-tier reasoning-token FLOOR (``apply_min_max_tokens``)
  * the raw OpenAI-compatible ``/chat/completions`` call (``llm_complete``)

Nothing here judges, calibrates, ranks, or knows a rubric/gold record. It is the
"how do I reach + call a configured model" half, and ONLY that half, so it can be
shipped publicly (the cortex-agent-wrapper) and consumed by ``fanout.py`` /
``model_probe.py`` WITHOUT dragging in the private judge module.

``judge.py`` now imports these primitives back from here (a behavior-preserving
shim -- every old ``judge.get_tier_config`` / ``judge.concurrency_slot`` / ... name
still resolves), so judge keeps ALL of its judging IP and only sources dispatch
from this module. ``research._llm_complete`` and ``vague_build`` delegate here too.

Depends only on stdlib + httpx (already present via anthropic). No rubric, no
calibration, no leaderboard, no gold -- by design.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import resolve_workspace

# --------------------------------------------------------------------------- #
# Tier -> (url_env, key_env, model_env) registry                              #
# --------------------------------------------------------------------------- #
# API tier -> (url_env, key_env, model_env). NOTE: no "fable" -- the gold tier runs
# in-harness (session model / subagent model="fable"), not via a REST endpoint.
_TIER_ENV: dict[str, tuple[str, str, str]] = {
    "glm5.2": ("GLM_API_URL", "GLM_API_KEY", "GLM_MODEL"),
    "qwen35b": ("QWEN_API_URL", "QWEN_API_KEY", "QWEN_MODEL"),
    "deepseek": ("DEEPSEEK_API_URL", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"),
    # opencode-go direct API -- TWO separate accounts (2 concurrent sessions each).
    # Serves DeepSeek V4 Flash and MiMo 2.5. Endpoint family: opencode.ai/zen/go/v1 .
    # Known-flaky (500/502/429) -> treat 5xx/429 as temporary provider degradation.
    "opencode": ("OPENCODE_API_URL", "OPENCODE_API_KEY", "OPENCODE_MODEL"),
    "opencode2": ("OPENCODE2_API_URL", "OPENCODE2_API_KEY", "OPENCODE2_MODEL"),
    # opencode-ZEN direct API -- SAME two accounts/keys as opencode/opencode2 above, but a
    # DIFFERENT endpoint family: opencode.ai/zen/v1 (no "/go/"). Zen hosts free/stealth
    # promotional models distinct from Go's curated paid catalog (OPENCODE_ZEN_MODEL_ALLOWLIST).
    "opencode-zen": ("OPENCODE_ZEN_API_URL", "OPENCODE_API_KEY", "OPENCODE_ZEN_MODEL"),
    "opencode-zen2": ("OPENCODE_ZEN2_API_URL", "OPENCODE2_API_KEY", "OPENCODE_ZEN2_MODEL"),
    "openrouter": ("OPENROUTER_API_URL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    "ninerouter": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_MODEL"),
    # Same 9router instance/key, DIFFERENT provider connection: "aux" is a routing alias
    # on the hosted instance (resolves to big-pickle server-side). A separate backend from
    # ninerouter's umans-glm-5.2 gate, so it doesn't contend with that queue.
    "ninerouter-aux": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_AUX_MODEL"),
    # Local Ollama -- no API key, OpenAI-compatible at /v1. Free + unlimited.
    "ollama": ("OLLAMA_API_URL", "OLLAMA_API_KEY", "OLLAMA_MODEL"),
    # Prometheus-Eval -- a purpose-built OPEN evaluator model, three endpoints.
    "prometheus": ("PROMETHEUS_API_URL", "PROMETHEUS_API_KEY", "PROMETHEUS_MODEL"),
    "prometheus-mac": ("PROMETHEUS_MAC_API_URL", "PROMETHEUS_MAC_API_KEY", "PROMETHEUS_MAC_MODEL"),
    "prometheus-hosted": ("PROMETHEUS_HOSTED_API_URL", "PROMETHEUS_HOSTED_API_KEY", "PROMETHEUS_HOSTED_MODEL"),
    # 9Router free models -- all share the SAME endpoint + key as ninerouter;
    # only the upstream model_id differs.
    "9r-sonnet-4.6": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_SONNET_46_MODEL"),
    "9r-opus-4.6": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_OPUS_46_MODEL"),
    "9r-gpt-oss-120b": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_GPT_OSS_120B_MODEL"),
    "9r-gemini-3-flash": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_GEMINI_3_FLASH_MODEL"),
    "9r-gemini-3.5-flash": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_GEMINI_35_FLASH_MODEL"),
    "9r-gemini-3.1-pro": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_GEMINI_31_PRO_MODEL"),
    "9r-deepseek-3.2": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_DEEPSEEK_32_MODEL"),
    "9r-sonnet-4.5": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_SONNET_45_MODEL"),
    "9r-gpt-oss-ollama": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_GPT_OSS_OLLAMA_MODEL"),
    "9r-gemini-preview": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_GEMINI_PREVIEW_MODEL"),
}

# 9Router free-tier pool: rate-limited; treat as 2 concurrent max with backoff on 429.
NINEROUTER_MAX_CONCURRENCY = 2

NINEROUTER_TIERS = frozenset({
    "ninerouter",
    "ninerouter-aux",
    "9r-sonnet-4.6",
    "9r-opus-4.6",
    "9r-gpt-oss-120b",
    "9r-gemini-3-flash",
    "9r-gemini-3.5-flash",
    "9r-gemini-3.1-pro",
    "9r-deepseek-3.2",
    "9r-sonnet-4.5",
    "9r-gpt-oss-ollama",
    "9r-gemini-preview",
})

# Every Prometheus endpoint (PC + Mac + hosted). Callers that format prompts branch on
# membership here, never on the exact string "prometheus".
PROMETHEUS_TIERS = frozenset({"prometheus", "prometheus-mac", "prometheus-hosted"})

# Each Prometheus endpoint allows this many concurrent sessions.
PROMETHEUS_MAX_CONCURRENCY = 2

# HARD scheduling constraint: the PC's local Prometheus and the OTHER local Ollama tiers
# CANNOT run at the same time -- they contend for one local GPU. A scheduler must treat
# these as MUTUALLY EXCLUSIVE on this machine.
PROMETHEUS_PC_SHARES_LOCAL_GPU = frozenset({"prometheus", "ollama"})

# Local, no-key tiers served by Ollama (PC + Mac-over-tunnel are both Ollama, no key).
_LOCAL_NOKEY_TIERS = frozenset({"ollama", "prometheus", "prometheus-mac"})

_OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"

# Blank means "not configured".
_PLACEHOLDERS = frozenset({""})

# --------------------------------------------------------------------------- #
# CLI-based tier config (subprocess dispatch; used by judge.call_cli_tier)     #
# --------------------------------------------------------------------------- #
# CLI-based tiers (called via subprocess, not REST). These need a CLI caller, not
# get_tier_config -- they have no url/key/model env vars.
CLI_TIERS = frozenset({"chatgpt-5.5xhigh", "fable-max", "opus", "sonnet", "haiku"})
CODEX_CLI_BIN = os.environ.get("CODEX_EXE", "codex")
CLAUDE_CLI_BIN = os.environ.get("CLAUDE_EXE", "claude")

# Maps tier name -> (cli_binary, --model alias)
CLI_MODEL_MAP = {
    "chatgpt-5.5xhigh": ("codex", None),      # codex exec, no --model flag needed
    "fable-max":       ("claude", "fable"),
    "opus":            ("claude", "opus"),
    "sonnet":          ("claude", "sonnet"),
    "haiku":           ("claude", "haiku"),
}


def dispatch_lane_names() -> list[str]:
    """Return every built-in dispatch lane without resolving credentials.

    This is intentionally safe for status/catalog surfaces: it exposes only the
    public logical lane names already present in source, never endpoint URLs,
    environment values, or API keys.
    """
    return sorted(set(_TIER_ENV) | set(CLI_TIERS))

# --------------------------------------------------------------------------- #
# opencode model allowlists (fail-closed against silently calling a paid model) #
# --------------------------------------------------------------------------- #
# opencode-go MODEL ALLOWLIST (user rule, updated 2026-07-07): from opencode-go use ONLY
# deepseek-v4-flash OR mimo2.5 -- no other model.
OPENCODE_MODEL_ALLOWLIST = frozenset({"deepseek-v4-flash", "mimo2.5"})
OPENCODE_TIERS = frozenset({"opencode", "opencode2"})

# opencode-ZEN model allowlist (confirmed live 2026-07-07): the five free/stealth
# promotional models Zen exposes, distinct from Go's paid catalog. Default is "big-pickle".
OPENCODE_ZEN_MODEL_ALLOWLIST = frozenset({
    "big-pickle", "mimo-v2.5-free", "deepseek-v4-flash-free",
    "north-mini-code-free", "nemotron-3-ultra-free",
})
OPENCODE_ZEN_TIERS = frozenset({"opencode-zen", "opencode-zen2"})

# --------------------------------------------------------------------------- #
# Reasoning-token floor                                                        #
# --------------------------------------------------------------------------- #
# Reasoning-budget floor (2026-07-08, corrected 2026-07-12): OpenCode Zen / 9Router
# reasoning models bill `reasoning_content` out of the SAME `max_tokens` budget as the
# visible `content` -- a caller-supplied max_tokens below this floor silently returns
# content="" with finish_reason="length" (no HTTP error to catch). max_tokens is a CEILING
# not a reservation, so a high floor costs nothing on short answers. 12000 is enough headroom
# for build-style reasoning turns without over-forcing (64000 proved STALE and harmful).
# This is the same fix contributed upstream to decolua/9router (`applyMinMaxTokens`).
MIN_MAX_TOKENS_BY_TIER: dict[str, int] = {
    "opencode-zen": 12000,
    "opencode-zen2": 12000,
    "ninerouter": 12000,
    "ninerouter-aux": 12000,
    **{tier: 12000 for tier in NINEROUTER_TIERS},
}


def apply_min_max_tokens(tier: str, max_tokens: int) -> int:
    """Raise max_tokens to the tier's reasoning-budget floor if the caller asked for less.
    Never lowers a caller-supplied value that's already above the floor."""
    floor = MIN_MAX_TOKENS_BY_TIER.get(tier)
    if floor and max_tokens < floor:
        return floor
    return max_tokens


# --------------------------------------------------------------------------- #
# Cross-process concurrency gate (shared-account rate-limit caps)             #
# --------------------------------------------------------------------------- #
# Callers here are SEPARATE OS PROCESSES (each `python ops/*.py` background run is its own
# process), not threads in one process -- an in-memory threading.Semaphore would not see
# across them. Cross-process semaphore via a lock-file directory: up to N numbered slot
# files may exist at once per tier; acquiring tries each slot with atomic O_CREAT|O_EXCL,
# retries with backoff if all N are taken, and reclaims a slot whose lock file is older than
# _LOCK_STALE_S (a crashed holder that never released). Tiers not listed here are ungated.
MAX_CONCURRENT_BY_TIER: dict[str, int] = {
    "ninerouter": 3,
    # yolo-qwen35b: hard 2-concurrent cap, account-wide (cross-process file lock) so a stray
    # launch can't blow the cap and trigger an account-wide 429 lockout.
    "qwen35b": 2,
    # opencode-go: two accounts, ~2 concurrent sessions each; gate to be safe overnight.
    "opencode": 2,
    "opencode2": 2,
    **{tier: NINEROUTER_MAX_CONCURRENCY for tier in NINEROUTER_TIERS},
}

_LOCK_DIR = Path(__file__).resolve().parent.parent / ".locks"
_LOCK_STALE_S = 30 * 60  # 30 min -- past this, treat the lock as an abandoned/crashed holder.


_UNSAFE_PATH_CHARS = re.compile(r'[:<>"/\\|?*]')


def _slot_dir_name(tier: str) -> str:
    """Map a logical tier/lane key to a filesystem-safe lock-directory name. Replaces the
    characters illegal in a Windows path component (':' etc.) with '_'. Collisions between
    distinct logical keys are acceptable here: distinct keys map to distinct dir names as long
    as they differ outside the unsafe set, and the only cost of a collision is shared slots."""
    return _UNSAFE_PATH_CHARS.sub("_", tier)


class ConcurrencySlotTimeoutError(RuntimeError):
    """Raised when no concurrency slot for a gated tier freed up within the wait budget."""


@contextmanager
def concurrency_slot(tier: str, timeout_s: float = 900.0, poll_s: float = 1.0):
    """Cross-process semaphore. No-ops (yields immediately) for tiers not in
    MAX_CONCURRENT_BY_TIER. For gated tiers, blocks until one of the tier's N slots is free,
    holds it for the `with` body, and always releases on exit (including on exception)."""
    limit = MAX_CONCURRENT_BY_TIER.get(tier)
    if not limit:
        yield
        return

    # The logical tier key may contain characters illegal in a Windows path component
    # (notably ':' as in "fanout-lane:opencode", which denotes an NTFS alternate data
    # stream). Sanitize for the lock DIRECTORY name only; the logical key above is untouched.
    tier_dir = _LOCK_DIR / _slot_dir_name(tier)
    tier_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_s
    held_path: Path | None = None

    while held_path is None:
        for i in range(limit):
            slot_path = tier_dir / f"slot_{i}.lock"
            try:
                fd = os.open(str(slot_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}:{time.time()}".encode("utf-8"))
                os.close(fd)
                held_path = slot_path
                break
            except FileExistsError:
                try:
                    if time.time() - slot_path.stat().st_mtime > _LOCK_STALE_S:
                        slot_path.unlink(missing_ok=True)  # reclaim an abandoned slot
                except OSError:
                    pass
                continue
            except OSError:
                # Windows race: a concurrent holder unlink()ing this exact slot while we
                # O_EXCL-open it surfaces as PermissionError (a subclass of OSError), NOT
                # FileExistsError. It means "slot busy right now", not a real failure --
                # treat it like a taken slot and retry, never propagate. (GAP I3, 2026-07-14.)
                continue
        if held_path is not None:
            break
        if time.time() > deadline:
            raise ConcurrencySlotTimeoutError(
                f"no {tier!r} concurrency slot (max {limit}) freed up within {timeout_s}s"
            )
        time.sleep(poll_s)

    try:
        yield
    finally:
        try:
            held_path.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Tier config resolution                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class TierConfig:
    tier: str
    url: str
    key: str
    model: str


def load_env(env_path: str | Path | None = None) -> dict[str, str]:
    """Return a dict of env vars, overlaying a .env file (if present) onto os.environ.

    .env is the source of truth for keys, so .env values take precedence over os.environ.
    Tiny hand parser (KEY=VALUE, ignores comments/blank lines) to avoid a python-dotenv
    hard dependency. Never logs values.
    """
    merged: dict[str, str] = dict(os.environ)
    if env_path is None:
        env_path = resolve_workspace() / ".env"
    env_path = Path(env_path)
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            merged[key.strip()] = val.strip()
    return merged


def _resolve_codex_cli_bin(env: dict[str, str] | None = None) -> str:
    """Resolve the Codex CLI binary, preferring the repo .env at call time."""
    if env is None:
        env = load_env()
    return (
        env.get("CODEX_EXE")
        or os.environ.get("CODEX_EXE")
        or CODEX_CLI_BIN
        or "codex"
    ).strip() or "codex"


def get_tier_config(tier: str, env: dict[str, str] | None = None) -> TierConfig:
    """Resolve a tier's (url, key, model) from env. Raises if unknown/unconfigured.

    Ollama is special-cased: no API key required (local server ignores auth) and the
    URL defaults to localhost, so only a model tag is strictly needed.
    """
    if tier in CLI_TIERS:
        # CLI tiers don't have url/key/model -- they're called via subprocess.
        return TierConfig(tier=tier, url="", key="", model="")
    if tier not in _TIER_ENV:
        raise ValueError(f"Unknown tier {tier!r}; known: {sorted(_TIER_ENV)}")
    env = env or load_env()
    url_env, key_env, model_env = _TIER_ENV[tier]
    url = env.get(url_env, "").strip()
    key = env.get(key_env, "").strip()
    model = env.get(model_env, "").strip()

    # Enforce the opencode-go model allowlist (user rule): only deepseek-v4-flash or mimo2.5.
    if tier in OPENCODE_TIERS and model and model not in OPENCODE_MODEL_ALLOWLIST:
        raise RuntimeError(
            f"{tier}: model {model!r} is not allowed. opencode-go permits ONLY "
            f"{sorted(OPENCODE_MODEL_ALLOWLIST)} (user rule). Fix {model_env} in .env."
        )

    # Enforce the opencode-ZEN model allowlist: only the confirmed free/stealth Zen models.
    if tier in OPENCODE_ZEN_TIERS and model and model not in OPENCODE_ZEN_MODEL_ALLOWLIST:
        raise RuntimeError(
            f"{tier}: model {model!r} is not allowed. opencode-zen permits ONLY "
            f"{sorted(OPENCODE_ZEN_MODEL_ALLOWLIST)}. Fix {model_env} in .env."
        )

    if tier in _LOCAL_NOKEY_TIERS:
        # PC-local tiers may default to the localhost Ollama. A keyless-but-REMOTE tier
        # (prometheus-mac, reached over an SSH tunnel) must NOT silently fall back to localhost.
        if not url:
            if tier == "prometheus-mac":
                raise RuntimeError(
                    f"{tier} needs an explicit {url_env} (the SSH-tunnel URL, e.g. "
                    "http://127.0.0.1:11445/v1). It must not fall back to localhost -- "
                    "that is this PC's Ollama, not the Mac's."
                )
            url = _OLLAMA_DEFAULT_URL
        key = key or "ollama"  # dummy; local server ignores it
        if not model:
            raise RuntimeError(
                f"{tier} tier needs a model tag. Set {model_env} in .env "
                "or use list_ollama_models() to discover one."
            )
        return TierConfig(tier=tier, url=url, key=key, model=model)

    missing = [
        name
        for name, val in ((url_env, url), (key_env, key), (model_env, model))
        if val in _PLACEHOLDERS
    ]
    if missing:
        raise RuntimeError(
            f"Tier {tier!r} not configured (blank/placeholder: {missing}). "
            f"Set these in .env."
        )
    return TierConfig(tier=tier, url=url, key=key, model=model)


def list_ollama_models(base_url: str = _OLLAMA_DEFAULT_URL, timeout: float = 5.0) -> list[str]:
    """Return installed Ollama model tags via /api/tags ([] if server is down)."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        resp = httpx.get(f"{root}/api/tags", timeout=timeout)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# OpenAI-compatible response helpers                                           #
# --------------------------------------------------------------------------- #
def _chat_completions_url(base: str) -> str:
    """Normalize a provider base URL to its /chat/completions endpoint."""
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _extract_content(data: dict[str, Any]) -> str:
    """Pull the assistant text from an OpenAI-compatible response.

    Reasoning models (GLM 5.2, DeepSeek) may put chain-of-thought in ``reasoning_content``
    and the answer in ``content`` -- or, when truncated, leave ``content`` empty. Prefer
    ``content``; fall back to ``reasoning_content``.
    """
    msg = data["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if content:
        return content
    return (msg.get("reasoning_content") or "").strip()


def _extract_usage(data: dict[str, Any]) -> tuple[int | None, int | None]:
    """Pull (input_tokens, output_tokens) from an OpenAI-compatible ``usage`` block, if present."""
    u = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(u, dict):
        return (None, None)

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return (_int(u.get("prompt_tokens")), _int(u.get("completion_tokens")))


def _extract_sse_content(text: str) -> str:
    """Concatenate SSE ``data:`` chunks into one string, stopping at ``[DONE]``."""
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in obj.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content") or ""
            parts.append(content)
    return "".join(parts)


def _is_sse_response(resp) -> bool:
    """Detect whether a response object is an SSE stream."""
    ct = ""
    if hasattr(resp, "headers"):
        headers = resp.headers
        if isinstance(headers, dict):
            ct = headers.get("content-type", "")
        else:
            ct = getattr(headers, "get", lambda k, default="": default)("content-type", "")
    return "text/event-stream" in ct or ct == ""


def _response_text(resp) -> str | None:
    """Best-effort extraction of response text; returns None if unavailable."""
    if hasattr(resp, "text"):
        return resp.text
    try:
        return resp.read()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Raw OpenAI-compatible completion (model-agnostic dispatch)                   #
# --------------------------------------------------------------------------- #
def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _llm_error_log_path() -> Path:
    """Resolve the diagnostic log path lazily (NOT at import) so this module imports cleanly
    in a bare checkout / arbitrary CWD where resolve_workspace() can't find a marker."""
    return resolve_workspace() / "logs" / "llm_dispatch_errors.jsonl"


def _log_llm_error(tier: str, real_model: str, prompt: str, max_tokens: int, resp) -> None:
    """Append a diagnostic record on any >=400 response, so an unexplained failure captures
    its REAL cause live, in production, instead of requiring after-the-fact guesswork."""
    try:
        log_path = _llm_error_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _now(),
            "tier": tier,
            "model": real_model,
            "status_code": resp.status_code,
            "prompt_chars": len(prompt),
            "prompt_tail_500": prompt[-500:],  # what precedes the failure, not the start
            "max_tokens": max_tokens,
            "response_body": resp.text[:2000],
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 -- diagnostic logging must never break the real call
        pass


def llm_complete(prompt: str, tier: str, max_tokens: int,
                 model_override: str | None = None, *,
                 env: dict[str, str] | None = None) -> str | None:
    """Model-AGNOSTIC single-shot OpenAI-compatible completion against a configured tier.

    ``tier`` is a dispatch-tier name resolved from .env (``glm5.2``, ``qwen35b``, ``ollama``,
    ``opencode``, ``ninerouter``, ...). Returns None when the tier is unconfigured/unreachable
    so callers DEGRADE GRACEFULLY instead of crashing.

    ``model_override``: when set, the tier named by ``tier`` supplies only the endpoint URL +
    API key, but the request's ``model`` field is this literal id instead of the tier's
    configured ``cfg.model`` -- drives many models that share one endpoint/key through the
    existing retry/backoff/concurrency path without minting a new env-var tier per model.

    This is the ``claude-*``/anthropic-free half: the Anthropic SDK branch lives in
    ``research._llm_complete``, which delegates the tier branch here.
    """
    try:
        cfg = get_tier_config(tier, env=env if env is not None else load_env())

        # Apply the per-tier reasoning-budget FLOOR (MIN_MAX_TOKENS_BY_TIER, 12000 for the
        # OpenCode-Zen / 9Router reasoning tiers). Below-floor silently returns content="".
        max_tokens = apply_min_max_tokens(tier, max_tokens)
        body = {"model": model_override or cfg.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": 0, "stream": False}
        url = _chat_completions_url(cfg.url)
        headers = {"Authorization": f"Bearer {cfg.key}", "Content-Type": "application/json"}

        # A transient 429/5xx is NOT the same failure as "tier unconfigured". Retry with
        # backoff on 429/500/502/503/504, honoring Retry-After when present; genuinely
        # unconfigured/unreachable tiers still degrade to None below.
        max_attempts = 4
        backoff_s = 1.0
        # Client-side concurrency gate: for tiers in MAX_CONCURRENT_BY_TIER, block here until
        # one of N cross-process slots is free. No-op for any other tier. Held for the whole
        # retry loop, not just one attempt, since a retry is still load on the same instance.
        with concurrency_slot(tier):
            for attempt in range(max_attempts):
                try:
                    resp = httpx.post(url, headers=headers, json=body, timeout=120)
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                        retry_after = resp.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff_s
                        time.sleep(delay)
                        backoff_s *= 2
                        continue
                    if resp.status_code >= 400:
                        _log_llm_error(tier, cfg.model, prompt, max_tokens, resp)
                    resp.raise_for_status()
                    return _extract_content(resp.json()) or None
                except httpx.HTTPStatusError:
                    break  # non-retryable status (e.g. 4xx auth/validation)
                except Exception:  # noqa: BLE001 -- network/timeout -- retry within budget
                    if attempt < max_attempts - 1:
                        time.sleep(backoff_s)
                        backoff_s *= 2
                        continue
                    break
        return None
    except Exception:  # noqa: BLE001 -- unconfigured/unreachable tier -> graceful skip
        return None
