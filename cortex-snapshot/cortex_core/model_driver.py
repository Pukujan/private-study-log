"""Canonical OpenAI-compatible model driver + append-only call ledger (SHIPPED).

This is the production home of the `llm(prompt) -> str` callable that Plane-2's governed
loop (`cortex_core.plane2_driver.run_build`) calls once per phase, plus the append-only JSONL
call ledger that records every model call. It talks to any OpenAI-compatible endpoint
(9router / opencode-zen / openrouter / a local Ollama) configured via a `provider.env`.

`cortex-govern` (`cortex_core.govern`) uses this to let an operator drive THEIR OWN model
through the deterministic state machine on THEIR OWN task -- e.g. phantomic pointing the
`ninerouter` lane at his own 9Router key. Nothing here is Cortex-corpus-specific.

The E2E harness (`evals/e2e/driver_client.py`) re-exports these names so the governed-trial
runner and this shipped path share ONE implementation (no drift).

HONEST PROVENANCE LIMIT (spec 1.2 P0.1): the call ledger is written by the SAME process that
runs the pipeline -- it is trust-level-2 evidence (the call happened, tokens were spent), NOT
an out-of-band gateway byte-capture under a separate OS identity. It corroborates; it is never
the trusted root. See docs/PHANTOMIC-HANDOFF.md and the e2e gate report S3/S9.

Stdlib only (urllib) so it runs on any venv without httpx/openai installed.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# The recorded reasoning-lane floor (judge.MIN_MAX_TOKENS_BY_TIER == 12000, CLAUDE.md incident
# 2026-07-12). Below it, reasoning models silently return content="" / finish_reason="length".
MIN_MAX_TOKENS = 12000


def load_provider_env(path: str | Path) -> dict[str, str]:
    """Parse a provider.env (KEY=VALUE lines) into a dict. Blank/`#` lines skipped."""
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _chat_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class CallLedger:
    """Append-only per-run LLM call ledger (one JSONL row per model call)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, row: dict[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")


class ModelDriver:
    """A callable strong-model driver bound to one actor + one served-model lane.

    `driver(prompt) -> str` issues one real chat completion, records the call to the ledger,
    and returns the assistant content. Raises on hard transport failure only after retries.
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, actor: str,
                 ledger: CallLedger, max_tokens: int = MIN_MAX_TOKENS,
                 temperature: float = 0.2, timeout: float = 180.0, retries: int = 3,
                 system_prompt: str | None = None, lane: str = "unknown"):
        if max_tokens < MIN_MAX_TOKENS:
            max_tokens = MIN_MAX_TOKENS  # enforce the recorded floor, never silently below
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.actor = actor
        self.ledger = ledger
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries
        self.lane = lane
        self.system_prompt = system_prompt or (
            "You are a rigorous senior engineer driving one phase of a governed build "
            "pipeline. Answer ONLY with the JSON object the phase asks for, no prose "
            "outside the JSON.")
        self.calls = 0

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _chat_url(self.base_url), data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "Accept": "application/json",
                     # some gateways 403 a bare python-urllib UA; present a normal client UA.
                     "User-Agent": "cortex-govern-driver/1.0 (OpenAI-compatible)"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        # Some proxies return SSE even for stream=False; try JSON first, then SSE.
        try:
            return json.loads(raw), raw
        except ValueError:
            return _parse_sse(raw), raw

    def __call__(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        last_err = ""
        for attempt in range(self.retries + 1):
            ts_start = time.time()
            try:
                data, raw = self._post(payload)
                ts_end = time.time()
                content, finish, in_tok, out_tok = _extract(data)
                self.calls += 1
                self.ledger.record({
                    "actor": self.actor, "lane": self.lane, "model": self.model,
                    "ts_start": ts_start, "ts_end": ts_end,
                    "input_tokens": in_tok, "output_tokens": out_tok,
                    "max_tokens": self.max_tokens, "finish_reason": finish,
                    "request_sha256": sha256_hex(req_bytes),
                    "response_content_sha256": sha256_hex(content or ""),
                    "prompt_sha256": sha256_hex(prompt),
                    "ok": bool(content), "attempt": attempt,
                })
                if content:
                    return content
                last_err = f"empty content (finish_reason={finish})"
            except urllib.error.HTTPError as exc:
                ts_end = time.time()
                last_err = f"HTTP {exc.code}: {exc.reason}"
                self.ledger.record({
                    "actor": self.actor, "lane": self.lane, "model": self.model,
                    "ts_start": ts_start, "ts_end": ts_end, "ok": False,
                    "error": last_err, "max_tokens": self.max_tokens, "attempt": attempt,
                    "request_sha256": sha256_hex(req_bytes)})
                if exc.code in (429, 500, 502, 503) and attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
            except Exception as exc:  # noqa: BLE001
                ts_end = time.time()
                last_err = f"{type(exc).__name__}: {exc}"
                self.ledger.record({
                    "actor": self.actor, "lane": self.lane, "model": self.model,
                    "ts_start": ts_start, "ts_end": ts_end, "ok": False,
                    "error": last_err, "max_tokens": self.max_tokens, "attempt": attempt,
                    "request_sha256": sha256_hex(req_bytes)})
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
        raise RuntimeError(f"model call failed for actor={self.actor}: {last_err}")


def _parse_sse(raw: str) -> dict[str, Any] | None:
    """Collapse an SSE stream into a single OpenAI-shaped dict (best effort)."""
    content_parts: list[str] = []
    finish = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        for ch in obj.get("choices", []):
            delta = ch.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            msg = ch.get("message") or {}
            if msg.get("content"):
                content_parts.append(msg["content"])
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    if not content_parts and finish is None:
        return None
    return {"choices": [{"message": {"content": "".join(content_parts)},
                         "finish_reason": finish}]}


def _extract(data: dict[str, Any] | None) -> tuple[str, str | None, int | None, int | None]:
    if not isinstance(data, dict):
        return "", None, None, None
    choices = data.get("choices") or []
    content = ""
    finish = None
    if choices:
        ch = choices[0]
        msg = ch.get("message") or {}
        content = msg.get("content") or ch.get("text") or ""
        finish = ch.get("finish_reason")
    usage = data.get("usage") or {}
    return content, finish, usage.get("prompt_tokens"), usage.get("completion_tokens")


# Known provider.env lanes -> (URL_KEY, API_KEY_KEY, MODEL_KEY). Add a lane by adding a row.
LANES: dict[str, tuple[str, str, str]] = {
    "ninerouter-aux": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_AUX_MODEL"),
    "ninerouter": ("NINEROUTER_API_URL", "NINEROUTER_API_KEY", "NINEROUTER_MODEL"),
    "opencode-zen": ("OPENCODE_ZEN_API_URL", "OPENCODE_ZEN_API_KEY", "OPENCODE_ZEN_MODEL"),
    "openrouter": ("OPENROUTER_API_URL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    "openai-compatible": ("OPENAI_API_URL", "OPENAI_API_KEY", "OPENAI_MODEL"),
}


def make_driver_from_lane(env: dict[str, str], lane: str, actor: str, ledger: CallLedger,
                          **kw: Any) -> ModelDriver:
    """Build a ModelDriver from a provider.env lane. Known lanes map to (URL,KEY,MODEL) env keys."""
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}; known {sorted(LANES)}")
    uk, kk, mk = LANES[lane]
    url, key, model = env.get(uk, ""), env.get(kk, ""), env.get(mk, "")
    if not (url and key and model):
        missing = [n for n, v in ((uk, url), (kk, key), (mk, model)) if not v]
        raise RuntimeError(f"lane {lane!r} not configured: missing {missing}")
    return ModelDriver(base_url=url, api_key=key, model=model, actor=actor, ledger=ledger,
                       lane=lane, **kw)
