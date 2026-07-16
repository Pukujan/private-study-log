"""Phase-1 extraction tests: the PUBLIC-safe cortex_core.model_dispatch shim.

Covers the dispatch primitives moved out of judge.py (tier resolution, token floors,
concurrency slots, the raw OpenAI-compatible completion) AND the headline success
criterion: fanout.py imports and runs WITHOUT judge.py present. No network / paid calls --
httpx.post is mocked throughout.
"""
from __future__ import annotations

import builtins
import sys

import pytest

import cortex_core.model_dispatch as md


# --------------------------------------------------------------------------- #
# 1. Token floor                                                              #
# --------------------------------------------------------------------------- #
def test_apply_min_max_tokens_raises_below_floor():
    # 9Router / OpenCode-Zen reasoning tiers have a recorded 12000 floor.
    assert md.apply_min_max_tokens("ninerouter", 300) == 12000
    assert md.apply_min_max_tokens("opencode-zen", 100) == 12000


def test_apply_min_max_tokens_never_lowers_above_floor():
    assert md.apply_min_max_tokens("ninerouter", 50000) == 50000


def test_apply_min_max_tokens_ungated_tier_passthrough():
    assert md.apply_min_max_tokens("glm5.2", 1500) == 1500


# --------------------------------------------------------------------------- #
# 2. Tier -> (url, key, model) resolution (portable across an arbitrary env)  #
# --------------------------------------------------------------------------- #
def test_get_tier_config_resolves_from_supplied_env():
    env = {"GLM_API_URL": "https://glm.example/v1", "GLM_API_KEY": "sk-x", "GLM_MODEL": "glm-5.2"}
    cfg = md.get_tier_config("glm5.2", env=env)
    assert (cfg.url, cfg.key, cfg.model) == ("https://glm.example/v1", "sk-x", "glm-5.2")


def test_get_tier_config_unconfigured_raises():
    # A truthy env lacking the GLM keys -> blanks -> "not configured" (env={} would be
    # falsy and fall back to load_env()/the real .env, which may actually configure it).
    with pytest.raises(RuntimeError):
        md.get_tier_config("glm5.2", env={"UNRELATED": "x"})


def test_get_tier_config_unknown_tier_raises():
    with pytest.raises(ValueError):
        md.get_tier_config("does-not-exist", env={})


def test_get_tier_config_cli_tier_returns_stub():
    cfg = md.get_tier_config("fable-max", env={})
    assert cfg.tier == "fable-max" and cfg.url == "" and cfg.model == ""


def test_get_tier_config_enforces_opencode_zen_allowlist():
    env = {"OPENCODE_ZEN_API_URL": "u", "OPENCODE_API_KEY": "k", "OPENCODE_ZEN_MODEL": "not-allowed"}
    with pytest.raises(RuntimeError):
        md.get_tier_config("opencode-zen", env=env)


def test_chat_completions_url_normalization():
    assert md._chat_completions_url("https://x/v1") == "https://x/v1/chat/completions"
    assert md._chat_completions_url("https://x/v1/chat/completions") == "https://x/v1/chat/completions"


def test_extract_content_prefers_content_then_reasoning():
    assert md._extract_content({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    assert md._extract_content(
        {"choices": [{"message": {"content": "", "reasoning_content": "fallback"}}]}
    ) == "fallback"


# --------------------------------------------------------------------------- #
# 3. Concurrency slot (cross-process; no-op for ungated tiers)                #
# --------------------------------------------------------------------------- #
def test_concurrency_slot_noop_for_ungated_tier():
    with md.concurrency_slot("glm5.2"):
        pass  # ungated -> yields immediately, no lock file created


def test_concurrency_slot_acquires_and_releases_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "_LOCK_DIR", tmp_path / ".locks")
    with md.concurrency_slot("qwen35b"):  # capped at 2
        held = list((tmp_path / ".locks" / "qwen35b").glob("slot_*.lock"))
        assert len(held) == 1  # exactly one slot held inside the body
    # released on exit
    assert not list((tmp_path / ".locks" / "qwen35b").glob("slot_*.lock"))


def test_slot_dir_name_sanitizes_windows_unsafe_chars():
    # ':' (NTFS alternate-data-stream separator) and other reserved chars are illegal in a
    # Windows path component and must be mapped to '_' for the lock DIRECTORY name.
    assert md._slot_dir_name("fanout-lane:opencode") == "fanout-lane_opencode"
    assert md._slot_dir_name('a:b<c>d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert md._slot_dir_name("qwen35b") == "qwen35b"  # already safe -> unchanged


def test_concurrency_slot_colon_lane_key_uses_safe_dir(tmp_path, monkeypatch):
    # Regression: a gated lane key with a colon (fanout registers "fanout-lane:opencode")
    # must not build a path with a colon (invalid on Windows). The logical key still drives
    # the limit lookup; only the on-disk directory name is sanitized.
    monkeypatch.setattr(md, "_LOCK_DIR", tmp_path / ".locks")
    monkeypatch.setitem(md.MAX_CONCURRENT_BY_TIER, "fanout-lane:opencode", 4)
    with md.concurrency_slot("fanout-lane:opencode"):
        safe_dir = tmp_path / ".locks" / "fanout-lane_opencode"
        assert safe_dir.is_dir()
        assert len(list(safe_dir.glob("slot_*.lock"))) == 1
        assert ":" not in safe_dir.name  # the lane component itself carries no colon
    assert not list(safe_dir.glob("slot_*.lock"))  # released on exit


# --------------------------------------------------------------------------- #
# 4. Raw completion (mocked httpx -- never a real/paid call)                  #
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self):
        return self._json


def _stub_env(monkeypatch):
    monkeypatch.setattr(
        md, "get_tier_config",
        lambda tier, env=None: md.TierConfig(tier=tier, url="http://fake.local", key="k", model="m"),
    )
    monkeypatch.setattr(md.time, "sleep", lambda *_a, **_k: None)


def test_llm_complete_returns_content(monkeypatch):
    _stub_env(monkeypatch)

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResp(200, {"choices": [{"message": {"content": "answer"}}]})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    assert md.llm_complete("q", "stub-tier", max_tokens=100) == "answer"


def test_llm_complete_degrades_to_none_on_persistent_5xx(monkeypatch):
    _stub_env(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResp(502)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    assert md.llm_complete("q", "stub-tier", max_tokens=100) is None
    assert calls["n"] == 4  # retried within budget, then graceful None


def test_llm_complete_model_override_used_in_body(monkeypatch):
    _stub_env(monkeypatch)
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(json)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    md.llm_complete("q", "stub-tier", max_tokens=100, model_override="poolside/laguna:free")
    assert seen["model"] == "poolside/laguna:free"


# --------------------------------------------------------------------------- #
# 5. judge.py re-exports the SAME objects (behavior-preserving shim)          #
# --------------------------------------------------------------------------- #
def test_judge_reexports_are_identical_objects():
    import cortex_core.judge as J
    assert J.get_tier_config is md.get_tier_config
    assert J.concurrency_slot is md.concurrency_slot
    assert J.apply_min_max_tokens is md.apply_min_max_tokens
    assert J.MAX_CONCURRENT_BY_TIER is md.MAX_CONCURRENT_BY_TIER
    assert J.TierConfig is md.TierConfig
    assert J._extract_content is md._extract_content
    assert J.OPENCODE_ZEN_MODEL_ALLOWLIST is md.OPENCODE_ZEN_MODEL_ALLOWLIST


# --------------------------------------------------------------------------- #
# 6. HEADLINE: fanout imports + runs WITHOUT judge.py present                 #
# --------------------------------------------------------------------------- #
def test_fanout_imports_and_dispatches_without_judge(monkeypatch):
    """Success criterion for Phase 1: with cortex_core.judge blocked from import, fanout
    still imports and its dispatch primitives still work."""
    # Drop any already-imported fanout/judge so the block takes effect on fresh import.
    for m in ("cortex_core.fanout", "cortex_core.judge"):
        sys.modules.pop(m, None)

    real_import = builtins.__import__

    def blocked_import(name, *a, **k):
        if name == "cortex_core.judge" or name.endswith(".judge"):
            raise ImportError("judge.py is blocked for this test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    monkeypatch.setitem(sys.modules, "cortex_core.judge", None)  # None -> ImportError on import

    import importlib
    fanout = importlib.import_module("cortex_core.fanout")
    assert fanout.EXECUTORS  # module loaded and its executor registry is populated
    # The fan-out lane cap was registered on the SHARED model_dispatch dict at import.
    assert "fanout-lane:openrouter" in md.MAX_CONCURRENT_BY_TIER
    # And a dispatch primitive works with judge absent.
    assert md.apply_min_max_tokens("ninerouter", 100) == 12000
