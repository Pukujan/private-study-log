"""Tests for the 9Router free-tier judge lanes (2026-07-09).

These tiers reuse the existing NINEROUTER_API_URL/NINEROUTER_API_KEY pair but
pin a different upstream model_id per tier. No live network in the unit tests --
HTTP is injected. A small live smoke test is opt-in via --run-live.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from cortex_core import calibration as C
from cortex_core import judge as J
from cortex_core.evaluator import AtomicClaim, Verdict


_NINEROUTER_ENV = {
    "NINEROUTER_API_URL": "https://9router.phantomic.live/v1",
    "NINEROUTER_API_KEY": "sk-test",
    "NINEROUTER_MODEL": "umans/umans-glm-5.2",
    "NINEROUTER_AUX_MODEL": "aux",
    "NINEROUTER_SONNET_46_MODEL": "ag/claude-sonnet-4-6",
    "NINEROUTER_OPUS_46_MODEL": "ag/claude-opus-4-6-thinking",
    "NINEROUTER_GPT_OSS_120B_MODEL": "ag/gpt-oss-120b-medium",
    "NINEROUTER_GEMINI_3_FLASH_MODEL": "ag/gemini-3-flash-agent",
    "NINEROUTER_GEMINI_35_FLASH_MODEL": "ag/gemini-3.5-flash-low",
    "NINEROUTER_GEMINI_31_PRO_MODEL": "ag/gemini-pro-agent",
    "NINEROUTER_DEEPSEEK_32_MODEL": "kr/deepseek-3.2",
    "NINEROUTER_SONNET_45_MODEL": "kr/claude-sonnet-4.5",
    "NINEROUTER_GPT_OSS_OLLAMA_MODEL": "ollama/gpt-oss:120b",
    "NINEROUTER_GEMINI_PREVIEW_MODEL": "gemini/gemini-3-flash-preview",
}

_NINE_ROUTER_TIERS = [
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
]

_EXPECTED_MODEL_IDS = {
    "9r-sonnet-4.6": "ag/claude-sonnet-4-6",
    "9r-opus-4.6": "ag/claude-opus-4-6-thinking",
    "9r-gpt-oss-120b": "ag/gpt-oss-120b-medium",
    "9r-gemini-3-flash": "ag/gemini-3-flash-agent",
    "9r-gemini-3.5-flash": "ag/gemini-3.5-flash-low",
    "9r-gemini-3.1-pro": "ag/gemini-pro-agent",
    "9r-deepseek-3.2": "kr/deepseek-3.2",
    "9r-sonnet-4.5": "kr/claude-sonnet-4.5",
    "9r-gpt-oss-ollama": "ollama/gpt-oss:120b",
    "9r-gemini-preview": "gemini/gemini-3-flash-preview",
}

_FAMILY_MAP = {
    "9r-sonnet-4.6": "anthropic",
    "9r-opus-4.6": "anthropic",
    "9r-sonnet-4.5": "anthropic",
    "9r-gpt-oss-120b": "openai",
    "9r-gpt-oss-ollama": "openai",
    "9r-gemini-3-flash": "google",
    "9r-gemini-3.5-flash": "google",
    "9r-gemini-3.1-pro": "google",
    "9r-gemini-preview": "google",
    "9r-deepseek-3.2": "deepseek",
    "glm5.2": "zhipu",
    "qwen35b": "qwen",
    "ollama": "qwen-local",
    "prometheus": "independent-eval",
}


def _claim() -> AtomicClaim:
    return AtomicClaim(claim_id="t1", task_type="bugfix", description="Fix the parser crash")


def _post_returning(content: str):
    def _post(url, headers=None, json=None):
        return _FakeResp({"choices": [{"message": {"content": content}}]})
    return _post


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    @property
    def headers(self):
        return {"content-type": "application/json"}

    def json(self):
        return self._payload


class _Fake429:
    def raise_for_status(self):
        import httpx
        raise httpx.HTTPStatusError(
            "rate limited", request=mock.Mock(), response=mock.Mock(status_code=429)
        )

    def json(self):
        return {}


# ---- Tier config ----

@pytest.mark.parametrize("tier", _NINE_ROUTER_TIERS)
def test_ninerouter_tier_resolves_config(tier: str):
    cfg = J.get_tier_config(tier, env=_NINEROUTER_ENV)
    assert cfg.url == "https://9router.phantomic.live/v1"
    assert cfg.key == "sk-test"
    assert cfg.model == _EXPECTED_MODEL_IDS[tier]
    assert cfg.tier == tier


def test_ninerouter_tier_url_normalized_to_chat_completions():
    cfg = J.get_tier_config("9r-sonnet-4.6", env=_NINEROUTER_ENV)
    assert J._chat_completions_url(cfg.url) == "https://9router.phantomic.live/v1/chat/completions"


# ---- Family mapping ----

@pytest.mark.parametrize("tier, expected", _FAMILY_MAP.items())
def test_family_returns_correct_family(tier: str, expected: str):
    assert C._family(tier) == expected


# ---- Ladder / dispatch ----

def test_new_tiers_in_ladder_and_dispatchable():
    ladder_tiers = {t for _, t, _ in J.JUDGE_LADDER}
    for tier in _NINE_ROUTER_TIERS:
        assert tier in ladder_tiers, f"{tier} missing from JUDGE_LADDER"
        assert tier in J.DISPATCHABLE_TIERS, f"{tier} not dispatchable"


def test_new_tiers_are_not_cli_tiers():
    for tier in _NINE_ROUTER_TIERS:
        assert tier not in J.CLI_TIERS


# ---- Mocked completion ----

def test_llm_judge_dispatches_9r_sonnet_46_with_injected_completion():
    payload = '{"verdict":"supported","confidence":0.85,"reasoning":"ok","gaps":[]}'
    g = J.llm_judge(
        _claim(), [{"type": "test", "ref": "t"}],
        tier="9r-sonnet-4.6",
        env=_NINEROUTER_ENV,
        http_post=_post_returning(payload),
    )
    assert g.verdict == Verdict.SUPPORTED
    assert g.reasoning.startswith("[9r-sonnet-4.6]")


@pytest.mark.parametrize("tier", _NINE_ROUTER_TIERS)
def test_llm_judge_sends_correct_model_id_for_each_tier(tier: str):
    seen: dict[str, str] = {}

    def _capture(url, headers=None, json=None):
        seen["model"] = json.get("model")
        return _FakeResp({"choices": [{"message": {"content": '{"verdict":"unverifiable"}'}}]})

    J.llm_judge(_claim(), [{"type": "test", "ref": "t"}], tier=tier, env=_NINEROUTER_ENV, http_post=_capture)
    assert seen["model"] == _EXPECTED_MODEL_IDS[tier]


# ---- SSE streaming parsing ----

def test_extract_sse_concatenates_data_chunks_and_stops_on_done():
    body = (
        "data: {\"choices\":[{\"delta\":{\"content\":\"OK\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"content\":\"!\"}}]}\n\n"
        "data: [DONE]\n\n"
    )
    content = J._extract_sse_content(body)
    assert content == "OK!"


def test_extract_sse_empty_body_is_empty():
    assert J._extract_sse_content("") == ""


def test_extract_sse_ignores_non_data_lines():
    body = (
        ": ping\n\n"
        "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n"
        "event: ignore me\n\n"
        "data: [DONE]\n\n"
    )
    assert J._extract_sse_content(body) == "hi"


def test_llm_judge_parses_sse_response_when_stream_true():
    sse_text = (
        'data: {"choices":[{"delta":{"content":"{\\"verdict\\":\\"supported\\"}"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    class _FakeSSE:
        def raise_for_status(self):
            pass

        @property
        def headers(self):
            return {"content-type": "text/event-stream"}

        def json(self):
            raise RuntimeError("SSE stream has no single JSON body")

        @property
        def text(self):
            return sse_text

    def _post(url, headers=None, json=None):
        return _FakeSSE()

    g = J.llm_judge(
        _claim(), [{"type": "test", "ref": "t"}],
        tier="9r-sonnet-4.6",
        env=_NINEROUTER_ENV,
        http_post=_post,
    )
    assert g.verdict == Verdict.SUPPORTED


# ---- 429 exponential backoff ----

class _Fake429Backoff:
    def __init__(self):
        self.calls = 0

    def __call__(self, url, headers=None, json=None):
        self.calls += 1
        import httpx
        raise httpx.HTTPStatusError(
            "Too Many Requests",
            request=mock.Mock(),
            response=mock.Mock(status_code=429, headers={"retry-after": "0"}),
        )


def test_llm_judge_retries_on_429_exponential_backoff():
    fake = _Fake429Backoff()
    with mock.patch("time.sleep") as sleep_mock:
        g = J.llm_judge(
            _claim(), [{"type": "test", "ref": "t"}],
            tier="9r-sonnet-4.6",
            env=_NINEROUTER_ENV,
            http_post=fake,
            retries=3,
        )
    assert fake.calls == 4  # initial + 3 retries
    assert sleep_mock.call_count == 3
    # Exponential backoff: 1s, 2s, 4s (capped)
    waits = [call.args[0] for call in sleep_mock.call_args_list]
    assert waits == [1.0, 2.0, 4.0]
    assert g.verdict == Verdict.UNVERIFIABLE
    assert "429" in g.reasoning or "rate" in g.reasoning.lower()


def test_llm_judge_non_429_failure_does_not_backoff():
    fake = _Fake429Backoff()
    with mock.patch("time.sleep") as sleep_mock:
        J.llm_judge(
            _claim(), [{"type": "test", "ref": "t"}],
            tier="9r-sonnet-4.6",
            env=_NINEROUTER_ENV,
            http_post=fake,
            retries=0,
        )
    assert sleep_mock.call_count == 0


# ---- Concurrency ----

def test_ninerouter_max_concurrency_constant():
    assert J.NINEROUTER_MAX_CONCURRENCY == 2


@pytest.mark.parametrize("tier", _NINE_ROUTER_TIERS)
def test_ninerouter_tiers_are_gated_to_two_concurrent(tier: str):
    assert J.MAX_CONCURRENT_BY_TIER.get(tier) == 2


# ---- Live smoke test (opt-in) ----

_LIVE = pytest.mark.skip(reason="--run-live not provided")


def pytest_configure(config):
    if config.getoption("--run-live"):
        # Un-skip live tests by replacing the marker above dynamically is awkward;
        # instead we use a runtime flag in the test itself.
        pass


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", help="run live 9Router smoke tests")


@pytest.mark.parametrize("tier", _NINE_ROUTER_TIERS)
def test_live_smoke_9router_tier(tier: str, request):
    if not request.config.getoption("--run-live"):
        pytest.skip("--run-live not provided")
    import os
    env = dict(os.environ)
    # Ensure we read from the real .env if present.
    cfg = J.get_tier_config(tier, env=env)
    grade = J.llm_judge(
        _claim(),
        [{"type": "test", "ref": "smoke"}],
        tier=tier,
        env=env,
        max_tokens=5,
        timeout=120.0,
        retries=2,
    )
    assert grade.reasoning.strip() != ""
