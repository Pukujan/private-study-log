"""Tests for the LLM-judge dispatch (cortex_core/judge.py).

No network: the HTTP call is injected via the `http_post` seam. Covers config
resolution, URL normalization, response parsing (fenced/prose/unparseable),
content extraction (content vs reasoning_content), and the file-evidence guards.
"""

import json
import subprocess

import pytest

from cortex_core import judge as J
from cortex_core.evaluator import AtomicClaim, Verdict


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


def _post_returning(content: str):
    """Build an http_post that returns an OpenAI-style response with `content`."""
    def _post(url, headers=None, json=None):
        return _FakeResp({"choices": [{"message": {"content": content}}]})
    return _post


def _claim():
    return AtomicClaim(claim_id="t1", task_type="bugfix", description="Fix the parser crash")


# ---- config ----

def test_get_tier_config_unknown_tier_raises():
    with pytest.raises(ValueError):
        J.get_tier_config("no-such-tier", env={})


def test_get_tier_config_unconfigured_raises():
    with pytest.raises(RuntimeError):
        J.get_tier_config("glm5.2", env={"GLM_API_URL": "", "GLM_API_KEY": "", "GLM_MODEL": ""})


def test_get_tier_config_reads_env():
    cfg = J.get_tier_config("glm5.2", env={
        "GLM_API_URL": "https://x/v1", "GLM_API_KEY": "k", "GLM_MODEL": "m"})
    assert cfg.url == "https://x/v1" and cfg.key == "k" and cfg.model == "m"


def test_ollama_needs_no_key_but_needs_model():
    cfg = J.get_tier_config("ollama", env={"OLLAMA_MODEL": "qwen3:4b-16k"})
    assert cfg.key == "ollama"  # dummy
    assert cfg.url == J._OLLAMA_DEFAULT_URL
    assert cfg.model == "qwen3:4b-16k"
    with pytest.raises(RuntimeError):
        J.get_tier_config("ollama", env={"OLLAMA_MODEL": ""})


def test_call_cli_tier_prefers_codex_exe_from_env(monkeypatch):
    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"type":"completed","last_message":"ok"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = J.call_cli_tier(
        "chatgpt-5.5xhigh",
        "hello",
        timeout=1,
        env={"CODEX_EXE": r"C:\Tools\Codex CLI\codex.exe"},
    )

    assert result["ok"] is True
    assert result["content"] == "ok"
    assert r"C:\Tools\Codex CLI\codex.exe" in str(seen["cmd"])


def test_chat_completions_url_normalization():
    assert J._chat_completions_url("https://x/v1") == "https://x/v1/chat/completions"
    assert J._chat_completions_url("https://x/v1/") == "https://x/v1/chat/completions"
    assert J._chat_completions_url("https://x/chat/completions") == "https://x/chat/completions"


def test_ladder_has_fable_at_top_and_dispatchables_exclude_in_harness():
    assert J.JUDGE_LADDER[0][1] == "fable-max"
    # fable-max/opus/sonnet are now CLI tiers (dispatchable), not in-harness
    assert "fable-max" in J.DISPATCHABLE_TIERS
    assert "opus" in J.DISPATCHABLE_TIERS
    assert "sonnet" in J.DISPATCHABLE_TIERS
    assert "glm5.2" in J.DISPATCHABLE_TIERS
    assert "ollama" in J.DISPATCHABLE_TIERS


# ---- response parsing ----

def test_parse_plain_json():
    g = J._parse_judge_response(
        '{"verdict":"supported","confidence":0.9,"reasoning":"ok","gaps":[]}', "t", 2, "glm5.2")
    assert g.verdict == Verdict.SUPPORTED and g.confidence == 0.9


def test_parse_fenced_json():
    text = "Here is my grade:\n```json\n{\"verdict\":\"unsupported\",\"confidence\":0.2,\"reasoning\":\"theater\"}\n```"
    g = J._parse_judge_response(text, "t", 2, "glm5.2")
    assert g.verdict == Verdict.UNSUPPORTED


def test_parse_prose_wrapped_json():
    text = 'The verdict is {"verdict": "partially_supported", "confidence": 0.5, "reasoning": "gap"} overall.'
    g = J._parse_judge_response(text, "t", 1, "glm5.2")
    assert g.verdict == Verdict.PARTIALLY_SUPPORTED


def test_parse_unknown_verdict_becomes_unverifiable():
    g = J._parse_judge_response('{"verdict":"maybe","confidence":0.5}', "t", 1, "glm5.2")
    assert g.verdict == Verdict.UNVERIFIABLE


def test_parse_unknown_string_maps_to_unverifiable():
    g = J._parse_judge_response('{"verdict":"unknown","confidence":0.4}', "t", 1, "glm5.2")
    assert g.verdict == Verdict.UNVERIFIABLE


def test_parse_unparseable_is_unverifiable():
    g = J._parse_judge_response("not json at all", "t", 1, "glm5.2")
    assert g.verdict == Verdict.UNVERIFIABLE
    assert "unparseable" in g.reasoning


def test_parse_clamps_confidence():
    g = J._parse_judge_response('{"verdict":"supported","confidence":5}', "t", 1, "glm5.2")
    assert g.confidence == 1.0


def test_extract_content_prefers_content_then_reasoning():
    assert J._extract_content({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    # empty content falls back to reasoning_content
    data = {"choices": [{"message": {"content": "", "reasoning_content": "thought"}}]}
    assert J._extract_content(data) == "thought"


# ---- llm_judge end-to-end (mocked transport) ----

def test_llm_judge_parses_injected_response():
    payload = '{"verdict":"supported","confidence":0.88,"reasoning":"clear","gaps":[]}'
    g = J.llm_judge(
        _claim(), [{"type": "test", "ref": "test_parser"}],
        tier="glm5.2",
        env={"GLM_API_URL": "https://x/v1", "GLM_API_KEY": "k", "GLM_MODEL": "m"},
        http_post=_post_returning(payload),
    )
    assert g.verdict == Verdict.SUPPORTED
    assert g.confidence == 0.88
    assert g.reasoning.startswith("[glm5.2]")


def test_llm_judge_file_evidence_without_workspace_is_unverifiable():
    g = J.llm_judge(
        _claim(), [{"type": "file", "ref": "cortex_core/x.py"}],
        tier="glm5.2", workspace=None,
        env={"GLM_API_URL": "https://x/v1", "GLM_API_KEY": "k", "GLM_MODEL": "m"},
        http_post=_post_returning('{"verdict":"supported","confidence":1}'),
    )
    assert g.verdict == Verdict.UNVERIFIABLE
    assert "workspace" in g.reasoning.lower()


def test_llm_judge_bad_file_ref_short_circuits(tmp_path):
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    called = {"n": 0}

    def _post(url, headers=None, json=None):
        called["n"] += 1
        return _FakeResp({"choices": [{"message": {"content": '{"verdict":"supported"}'}}]})

    g = J.llm_judge(
        _claim(), [{"type": "file", "ref": "does_not_exist.py"}],
        tier="glm5.2", workspace=tmp_path,
        env={"GLM_API_URL": "https://x/v1", "GLM_API_KEY": "k", "GLM_MODEL": "m"},
        http_post=_post,
    )
    assert g.verdict == Verdict.UNVERIFIABLE
    assert called["n"] == 0  # never spent a token on an unresolvable ref


def test_llm_judge_transport_error_is_graded_not_raised():
    def _boom(url, headers=None, json=None):
        raise RuntimeError("connection refused")

    g = J.llm_judge(
        _claim(), [{"type": "test", "ref": "t"}],
        tier="glm5.2",
        env={"GLM_API_URL": "https://x/v1", "GLM_API_KEY": "k", "GLM_MODEL": "m"},
        http_post=_boom, retries=1,
    )
    assert g.verdict == Verdict.UNVERIFIABLE
    assert "failed" in g.reasoning


# ---- opencode-ZEN tiers (2026-07-07): free/stealth Zen models, distinct endpoint family
# from opencode/opencode2 (Go). Same two account keys, different URL + model allowlist. ----

_ZEN_ENV = {
    "OPENCODE_ZEN_API_URL": "https://opencode.ai/zen/v1",
    "OPENCODE_API_KEY": "acct1-key",
    "OPENCODE_ZEN_MODEL": "big-pickle",
    "OPENCODE_ZEN2_API_URL": "https://opencode.ai/zen/v1",
    "OPENCODE2_API_KEY": "acct2-key",
    "OPENCODE_ZEN2_MODEL": "big-pickle",
    # Go tiers' own vars must stay untouched by the Zen addition.
    "OPENCODE_API_URL": "https://opencode.ai/zen/go/v1",
    "OPENCODE_MODEL": "deepseek-v4-flash",
    "OPENCODE2_API_URL": "https://opencode.ai/zen/go/v1",
    "OPENCODE2_MODEL": "deepseek-v4-flash",
}


def test_opencode_zen_tier_resolves_distinct_zen_url_not_go_url():
    cfg = J.get_tier_config("opencode-zen", env=_ZEN_ENV)
    assert cfg.url == "https://opencode.ai/zen/v1"
    assert cfg.url != _ZEN_ENV["OPENCODE_API_URL"]  # never silently reuses the Go endpoint
    assert cfg.key == "acct1-key"
    assert cfg.model == "big-pickle"


def test_opencode_zen2_tier_uses_account_2_key_same_zen_url():
    cfg = J.get_tier_config("opencode-zen2", env=_ZEN_ENV)
    assert cfg.url == "https://opencode.ai/zen/v1"
    assert cfg.key == "acct2-key"
    assert cfg.model == "big-pickle"


def test_opencode_zen_go_tiers_unaffected_by_zen_addition():
    """The pre-existing opencode/opencode2 (Go) tiers must still resolve to the Go
    endpoint and their own allowlist -- additive only, per the constraint that Zen must
    not weaken or repoint the existing Go tiers."""
    cfg = J.get_tier_config("opencode", env=_ZEN_ENV)
    assert cfg.url == "https://opencode.ai/zen/go/v1"
    assert cfg.model == "deepseek-v4-flash"


@pytest.mark.parametrize("tier", ["opencode-zen", "opencode-zen2"])
def test_opencode_zen_allowlist_accepts_confirmed_free_models(tier):
    for model in sorted(J.OPENCODE_ZEN_MODEL_ALLOWLIST):
        env = dict(_ZEN_ENV)
        env["OPENCODE_ZEN_MODEL"] = model
        env["OPENCODE_ZEN2_MODEL"] = model
        cfg = J.get_tier_config(tier, env=env)
        assert cfg.model == model


@pytest.mark.parametrize("tier", ["opencode-zen", "opencode-zen2"])
def test_opencode_zen_allowlist_rejects_disallowed_model(tier):
    env = dict(_ZEN_ENV)
    env["OPENCODE_ZEN_MODEL"] = "gpt-5.5"  # a real Zen model, but NOT in the free allowlist
    env["OPENCODE_ZEN2_MODEL"] = "gpt-5.5"
    with pytest.raises(RuntimeError, match="not allowed"):
        J.get_tier_config(tier, env=env)


def test_opencode_zen_tiers_are_in_the_judge_ladder_and_dispatchable():
    ladder_tiers = {t for _, t, _ in J.JUDGE_LADDER}
    assert "opencode-zen" in ladder_tiers
    assert "opencode-zen2" in ladder_tiers
    assert "opencode-zen" in J.DISPATCHABLE_TIERS
    assert "opencode-zen2" in J.DISPATCHABLE_TIERS


def test_llm_judge_dispatches_opencode_zen_tier_with_injected_completion():
    payload = '{"verdict":"supported","confidence":0.7,"reasoning":"fast free judge","gaps":[]}'
    g = J.llm_judge(
        _claim(), [{"type": "test", "ref": "t"}],
        tier="opencode-zen",
        env=_ZEN_ENV,
        http_post=_post_returning(payload),
    )
    assert g.verdict == Verdict.SUPPORTED
    assert g.reasoning.startswith("[opencode-zen]")


def test_llm_judge_dispatches_opencode_zen2_tier_with_injected_completion():
    payload = '{"verdict":"unsupported","confidence":0.4,"reasoning":"acct2 lane","gaps":[]}'
    g = J.llm_judge(
        _claim(), [{"type": "test", "ref": "t"}],
        tier="opencode-zen2",
        env=_ZEN_ENV,
        http_post=_post_returning(payload),
    )
    assert g.verdict == Verdict.UNSUPPORTED
    assert g.reasoning.startswith("[opencode-zen2]")
