"""``_llm_complete``'s tier-dispatch path retries transient 429/502/503/504 responses with
backoff (honoring Retry-After when present) instead of silently returning None on the first
hiccup -- the fix noted in ``research.py``'s module comment for the qwen35b-backed benchmark that
was burning its turn budget on transient rate limits. This was previously claimed but untested;
these tests exercise the real retry loop with a fake ``httpx.post`` (no network, no real sleeps).
"""
from __future__ import annotations

import cortex_core.model_dispatch as MD
import cortex_core.research as R


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def _stub_tier(monkeypatch, model="stub-tier"):
    """Route _llm_complete's tier-dispatch branch to a fake, network-free TierConfig.

    The raw dispatch (tier resolution + retry loop) now lives in cortex_core.model_dispatch;
    research._llm_complete delegates to it, so the stubs/patches target that module."""
    monkeypatch.setattr(
        MD, "get_tier_config",
        lambda tier, env=None: MD.TierConfig(tier=tier, url="http://fake.local", key="k", model="m"),
    )
    monkeypatch.setattr(MD.time, "sleep", lambda *_a, **_k: None)  # no real waiting in tests


def test_llm_complete_retries_429_then_succeeds(monkeypatch):
    _stub_tier(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(429, headers={})
        return _FakeResponse(200, json_body={"choices": [{"message": {"content": "ok answer"}}]})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    out = R._llm_complete("prompt", "stub-tier", max_tokens=100)
    assert out == "ok answer"
    assert calls["n"] == 3  # two 429s absorbed by retry, third call succeeds


def test_llm_complete_honors_retry_after_header(monkeypatch):
    _stub_tier(monkeypatch)
    slept = []
    monkeypatch.setattr(MD.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(503, headers={"Retry-After": "2"})
        return _FakeResponse(200, json_body={"choices": [{"message": {"content": "recovered"}}]})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    out = R._llm_complete("prompt", "stub-tier", max_tokens=100)
    assert out == "recovered"
    assert slept[0] == 2.0  # Retry-After honored instead of the default backoff


def test_llm_complete_exhausts_retries_and_degrades_to_none(monkeypatch):
    _stub_tier(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(502, headers={})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    out = R._llm_complete("prompt", "stub-tier", max_tokens=100)
    assert out is None  # graceful degrade, never raises
    assert calls["n"] == 4  # max_attempts


def test_llm_complete_nonretryable_4xx_gives_up_immediately(monkeypatch):
    _stub_tier(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(401, headers={})  # auth error: not in the retry set

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    out = R._llm_complete("prompt", "stub-tier", max_tokens=100)
    assert out is None
    assert calls["n"] == 1  # no retry burned on a non-transient status
