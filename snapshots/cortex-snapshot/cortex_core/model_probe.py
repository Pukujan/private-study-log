"""Portable model-availability PROBE -- for ANYONE, not just this repo's owner.

A new person clones the repo, points a provider `.env` at WHATEVER models THEY have
(their 9Router key, their Ollama, their OpenRouter, ...), runs `cortex-models`, and sees
THEIR reachable models: a table + a machine-readable ``model_availability.json`` the fanout
can consume to restrict itself to executors that are actually live.

Design rules (all enforced here):
  * GENERIC, never hardcoded to the owner's specific models. It reads the tiers the RUNNING
    USER configured via ``model_dispatch._TIER_ENV`` + their ``.env`` (``load_env``), plus the
    in-harness CLI tiers, and probes only those that resolve.
  * CHEAP + FREE-ONLY. Liveness prefers the provider's ``GET /models`` list endpoint (costs
    nothing on every provider). A 1-token completion is used as a FALLBACK **only** for tiers
    on the free-to-spend allowlist. Paid / premium tiers are NEVER charged a token -- they get
    key-present + endpoint-reachable only.
  * Respects the cross-process concurrency caps (``model_dispatch.concurrency_slot``).
  * NEVER logs a secret. The API key is only ever sent in the Authorization header; it is never
    printed, written to JSON, or included in an error string.

Stdlib only (urllib) so it runs on any venv -- no httpx/openai needed. This module carries NO
judging / rubric / calibration / leaderboard IP; it builds purely on the public
``cortex_core.model_dispatch`` dispatch shim + ``cortex_core.model_tiers`` classifier.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import model_dispatch as md
from . import model_tiers
from .config import resolve_workspace

# Tiers where a 1-token completion FALLBACK is acceptable because the model genuinely bills $0
# (local Ollama / Prometheus, the free 9Router "aux" round-robin pool + 9r-* free lanes, the
# free OpenCode-Zen stealth lanes, the free yolo-qwen35b). Everything else -- the paid umans
# ninerouter gate, opencode-go, deepseek, the paid OpenRouter gateway, glm5.2 -- is treated as
# NO-SPEND: we only ever hit its (free) /models endpoint or check key-presence, never a
# completion. Being CONSERVATIVE here is the safe default: worst case we under-probe a paid
# tier (report it "key present, unverified"), never overspend on one.
FREE_SPEND_TIERS: frozenset[str] = frozenset(
    {"ollama", "prometheus", "prometheus-mac", "prometheus-hosted",
     "ninerouter-aux", "opencode-zen", "opencode-zen2", "qwen35b"}
    | (md.NINEROUTER_TIERS - {"ninerouter"})  # the free 9r-* lanes, but NOT the paid umans gate
)

# In-harness premium reviewer tiers (subprocess CLIs, no REST endpoint). Probed by binary
# presence only -- NEVER invoked (spawning a real claude/codex would cost real tokens).
_CLI_BIN_FOR_TIER = {
    "fable-max": "claude", "opus": "claude", "sonnet": "claude", "haiku": "claude",
    "chatgpt-5.5xhigh": "codex",
}


@dataclass
class ProbeResult:
    tier: str
    model: str                 # served model id (or "" for a CLI tier / unconfigured)
    configured: bool
    available: bool | None     # True live / False unreachable / None unknown (key present, unverified)
    method: str                # models_list | completion | cli_which | key_present | unconfigured
    role: str                  # executor | reviewer
    latency_ms: int | None
    free_to_spend: bool
    detail: str = ""           # human note; NEVER contains the key


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _models_url(base: str) -> str:
    """Derive the provider's ``/models`` list endpoint from a chat base URL."""
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base + "/models"


def _role_for(tier: str, model: str) -> str:
    """executor | reviewer -- generic, derived from the capability classifier, not a hardcode.

    Premium in-harness CLI tiers and any model the tier-list classifies 'strong' are reviewers
    (strong non-executors); everything else is an executor (the free fan-out lanes)."""
    if tier in _CLI_BIN_FOR_TIER:
        return "reviewer"
    if model and model_tiers.classify(model) == "strong":
        return "reviewer"
    return "executor"


def _http_get(url: str, key: str, timeout: float) -> tuple[int, bytes]:
    """GET with a Bearer header (stdlib). Returns (status, body). Key never logged."""
    headers = {"Accept": "application/json", "User-Agent": "cortex-model-probe/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def _one_token_completion(cfg: md.TierConfig, timeout: float) -> tuple[bool, str]:
    """A minimal, $0 liveness completion (max_tokens=1) for a FREE tier only. Returns
    (ok, detail). Uses urllib; the key is only ever in the Authorization header."""
    url = md._chat_completions_url(cfg.url)
    body = json.dumps({
        "model": cfg.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1, "temperature": 0, "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "cortex-model-probe/1.0"}
    if cfg.key:
        headers["Authorization"] = f"Bearer {cfg.key}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (200 <= resp.getcode() < 300), f"HTTP {resp.getcode()}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


# --------------------------------------------------------------------------- #
# Probe one tier                                                               #
# --------------------------------------------------------------------------- #
def probe_tier(tier: str, env: dict[str, str], *, timeout: float = 8.0) -> ProbeResult:
    """Liveness-probe ONE configured tier. Free-only: /models first (free everywhere); a
    1-token completion fallback only for free-to-spend tiers; paid tiers key-checked only."""
    # In-harness CLI reviewer tiers: probe by binary presence, never invoke.
    if tier in _CLI_BIN_FOR_TIER:
        bin_name = _CLI_BIN_FOR_TIER[tier]
        found = shutil.which(bin_name) is not None
        return ProbeResult(
            tier=tier, model=bin_name, configured=found, available=found or None,
            method="cli_which", role="reviewer", latency_ms=None, free_to_spend=False,
            detail=(f"{bin_name} CLI on PATH" if found else f"{bin_name} CLI not on PATH"))

    # REST tiers: resolve (url, key, model) from the user's env. Unconfigured -> report, skip.
    try:
        cfg = md.get_tier_config(tier, env=env)
    except Exception as exc:  # noqa: BLE001 -- unconfigured/blank/allowlist-reject
        return ProbeResult(tier=tier, model="", configured=False, available=None,
                           method="unconfigured", role=_role_for(tier, ""), latency_ms=None,
                           free_to_spend=tier in FREE_SPEND_TIERS,
                           detail=f"not configured ({type(exc).__name__})")

    free = tier in FREE_SPEND_TIERS
    role = _role_for(tier, cfg.model)
    t0 = time.monotonic()
    # Concurrency-cap-respecting: hold a slot for the whole probe (no-op for ungated tiers).
    with md.concurrency_slot(tier, timeout_s=max(timeout, 30.0)):
        # 1) /models list -- free on every provider, the preferred liveness signal.
        try:
            status, _ = _http_get(_models_url(cfg.url), cfg.key, timeout)
            if 200 <= status < 300:
                return ProbeResult(
                    tier=tier, model=cfg.model, configured=True, available=True,
                    method="models_list", role=role,
                    latency_ms=int((time.monotonic() - t0) * 1000), free_to_spend=free,
                    detail="reachable via GET /models")
        except Exception as exc:  # noqa: BLE001 -- connection refused / DNS / timeout
            return ProbeResult(
                tier=tier, model=cfg.model, configured=True, available=False,
                method="models_list", role=role,
                latency_ms=int((time.monotonic() - t0) * 1000), free_to_spend=free,
                detail=f"unreachable ({type(exc).__name__})")

        # 2) /models unsupported (non-2xx). Fallback: a 1-token completion, FREE tiers ONLY.
        if free:
            ok, note = _one_token_completion(cfg, timeout)
            return ProbeResult(
                tier=tier, model=cfg.model, configured=True, available=ok,
                method="completion", role=role,
                latency_ms=int((time.monotonic() - t0) * 1000), free_to_spend=True,
                detail=f"1-token liveness ({note})")

    # 3) Paid/premium tier whose /models isn't a 2xx: key present + endpoint reachable, but we
    #    will NOT spend a token to confirm the model. Report as unknown-but-configured.
    return ProbeResult(
        tier=tier, model=cfg.model, configured=True, available=None,
        method="key_present", role=role,
        latency_ms=int((time.monotonic() - t0) * 1000), free_to_spend=False,
        detail="paid/premium: key present, endpoint reachable, not token-verified")


# --------------------------------------------------------------------------- #
# Probe the whole configured fleet                                            #
# --------------------------------------------------------------------------- #
def discover_configured_tiers(env: dict[str, str]) -> list[str]:
    """Every tier the RUNNING USER has configured: REST tiers whose (url,key,model) resolve,
    plus in-harness CLI tiers whose binary is on PATH. Generic -- iterates the registry, not a
    hardcoded owner list."""
    configured: list[str] = []
    for tier in md._TIER_ENV:
        try:
            md.get_tier_config(tier, env=env)
            configured.append(tier)
        except Exception:  # noqa: BLE001 -- blank/unconfigured -> not the user's tier
            continue
    for tier, bin_name in _CLI_BIN_FOR_TIER.items():
        if shutil.which(bin_name) is not None:
            configured.append(tier)
    return configured


def probe_fleet(env: dict[str, str] | None = None, *, tiers: list[str] | None = None,
                timeout: float = 8.0) -> list[ProbeResult]:
    """Probe every configured tier (or an explicit subset). Returns ProbeResults, executors
    first then reviewers, each group by tier name for stable output."""
    env = env if env is not None else md.load_env()
    tiers = tiers if tiers is not None else discover_configured_tiers(env)
    results = [probe_tier(t, env, timeout=timeout) for t in tiers]
    results.sort(key=lambda r: (r.role != "executor", r.tier))
    return results


def _availability_doc(results: list[ProbeResult]) -> dict[str, Any]:
    """The machine-readable model_availability.json the fanout consumes."""
    return {
        "schema": "cortex.model_availability/1",
        "generated_at": md._now(),
        "available_executors": sorted(
            r.tier for r in results if r.role == "executor" and r.available is True),
        "available_reviewers": sorted(
            r.tier for r in results if r.role == "reviewer" and r.available is True),
        "results": [asdict(r) for r in results],
    }


def availability_path(workspace: str | Path | None = None) -> Path:
    return Path(resolve_workspace(workspace)) / "model_availability.json"


def write_availability(results: list[ProbeResult],
                       workspace: str | Path | None = None) -> Path:
    """Persist model_availability.json (never contains a key). Returns the path written."""
    path = availability_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_availability_doc(results), indent=2), encoding="utf-8")
    return path


def load_available_executors(workspace: str | Path | None = None) -> set[str] | None:
    """Read the probe's available executors, or None if no probe has been run (caller then
    degrades gracefully -- no restriction). Never raises."""
    try:
        doc = json.loads(availability_path(workspace).read_text(encoding="utf-8"))
        return set(doc.get("available_executors", []))
    except Exception:  # noqa: BLE001 -- absent/corrupt -> "no probe yet"
        return None


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _print_table(results: list[ProbeResult]) -> None:
    def mark(a: bool | None) -> str:
        return {True: "up", False: "DOWN", None: "?"}[a]
    print(f"{'TIER':22} {'MODEL':30} {'AVAIL':6} {'LAT(ms)':8} {'ROLE':9} METHOD")
    print("-" * 92)
    for r in results:
        lat = "" if r.latency_ms is None else str(r.latency_ms)
        print(f"{r.tier:22} {(r.model or '-')[:30]:30} {mark(r.available):6} "
              f"{lat:8} {r.role:9} {r.method}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cortex-models",
        description="Probe which models YOUR configured providers can actually reach "
                    "(free-only: /models list or a 1-token liveness on free tiers; paid tiers "
                    "key-checked, never charged).")
    ap.add_argument("--tiers", default=None,
                    help="comma list to probe (default: every configured tier)")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--json", action="store_true", help="print the availability doc as JSON")
    ap.add_argument("--no-write", action="store_true",
                    help="don't write model_availability.json")
    ap.add_argument("--env", default=None, help="path to a provider .env (default: workspace .env)")
    a = ap.parse_args(argv)

    env = md.load_env(a.env) if a.env else md.load_env()
    tiers = [t.strip() for t in a.tiers.split(",") if t.strip()] if a.tiers else None
    results = probe_fleet(env, tiers=tiers, timeout=a.timeout)

    if a.json:
        print(json.dumps(_availability_doc(results), indent=2))
    else:
        _print_table(results)
        execs = [r.tier for r in results if r.role == "executor" and r.available is True]
        print(f"\navailable executors: {', '.join(execs) or '(none)'}")

    if not a.no_write:
        path = write_availability(results)
        if not a.json:
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
