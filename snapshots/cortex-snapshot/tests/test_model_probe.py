"""Phase-2 tests for the portable model-availability PROBE (cortex_core.model_probe).

No live/paid calls -- urllib is mocked. Covers: available/unavailable classification off a
mocked endpoint; paid tiers key-checked-not-called (NEVER a completion); portability across an
ARBITRARY configured provider set (not the owner's specific models); and the availability doc /
fanout-restriction wiring.
"""
from __future__ import annotations

import io
import json

import pytest

import cortex_core.model_probe as mp
import cortex_core.model_dispatch as md


class _FakeHTTPResponse(io.BytesIO):
    def __init__(self, code: int, body: bytes = b"{}"):
        super().__init__(body)
        self._code = code

    def getcode(self):
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


# --------------------------------------------------------------------------- #
# Portability: an ARBITRARY provider set the running user configured           #
# --------------------------------------------------------------------------- #
def _arbitrary_env():
    """A made-up user's .env: one free local tier (ollama), one free gateway lane
    (ninerouter-aux), one paid tier (deepseek). NOT the owner's specific models."""
    return {
        "OLLAMA_API_URL": "http://localhost:11434/v1",
        "OLLAMA_MODEL": "someone-elses-model:latest",
        "NINEROUTER_API_URL": "https://their-9router.example/v1",
        "NINEROUTER_API_KEY": "sk-user",
        "NINEROUTER_AUX_MODEL": "aux",
        "DEEPSEEK_API_URL": "https://api.deepseek.example/v1",
        "DEEPSEEK_API_KEY": "sk-paid",
        "DEEPSEEK_MODEL": "deepseek-chat",
    }


def test_discover_is_generic_to_the_users_env():
    tiers = mp.discover_configured_tiers(_arbitrary_env())
    assert set(tiers) >= {"ollama", "ninerouter-aux", "deepseek"}
    # An UNCONFIGURED tier (no GLM keys in this env) must NOT be discovered.
    assert "glm5.2" not in tiers


# --------------------------------------------------------------------------- #
# Available / unavailable classification off a mocked /models endpoint          #
# --------------------------------------------------------------------------- #
def test_available_when_models_endpoint_200(monkeypatch):
    def fake_urlopen(req, timeout=None):
        assert req.get_method() == "GET"  # free liveness, never a POST completion
        return _FakeHTTPResponse(200, b'{"data": []}')

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    r = mp.probe_tier("ninerouter-aux", _arbitrary_env(), timeout=1.0)
    assert r.available is True and r.method == "models_list"
    assert r.role == "executor"


def test_unavailable_when_connection_refused(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    r = mp.probe_tier("ollama", _arbitrary_env(), timeout=1.0)
    assert r.available is False and r.method == "models_list"


# --------------------------------------------------------------------------- #
# FREE tier: 1-token completion FALLBACK when /models is unsupported            #
# --------------------------------------------------------------------------- #
def test_free_tier_falls_back_to_one_token_completion(monkeypatch):
    seen = {"posts": 0, "bodies": []}

    def fake_urlopen(req, timeout=None):
        if req.get_method() == "GET":
            return _FakeHTTPResponse(404)  # provider has no /models list
        # POST completion fallback -- allowed ONLY because ninerouter-aux is free-to-spend.
        seen["posts"] += 1
        seen["bodies"].append(json.loads(req.data.decode()))
        return _FakeHTTPResponse(200, b'{"choices":[{"message":{"content":"x"}}]}')

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    r = mp.probe_tier("ninerouter-aux", _arbitrary_env(), timeout=1.0)
    assert r.available is True and r.method == "completion"
    assert seen["posts"] == 1
    assert seen["bodies"][0]["max_tokens"] == 1  # a 1-token, $0 liveness ping


# --------------------------------------------------------------------------- #
# PAID tier: key-checked, endpoint-reachable, but NEVER a token spent           #
# --------------------------------------------------------------------------- #
def test_paid_tier_is_never_charged_a_token(monkeypatch):
    posts = {"n": 0}

    def fake_urlopen(req, timeout=None):
        if req.get_method() == "POST":
            posts["n"] += 1  # this MUST never happen for a paid tier
            return _FakeHTTPResponse(200, b'{}')
        return _FakeHTTPResponse(404)  # /models unsupported -> would trigger fallback IF free

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    r = mp.probe_tier("deepseek", _arbitrary_env(), timeout=1.0)
    assert posts["n"] == 0, "a PAID tier must never be charged a completion token"
    assert r.free_to_spend is False
    assert r.method == "key_present" and r.available is None


def test_deepseek_is_not_in_free_spend_set():
    # Guardrail: the free-to-spend allowlist must exclude the paid lanes.
    for paid in ("deepseek", "ninerouter", "opencode", "opencode2", "openrouter", "glm5.2"):
        assert paid not in mp.FREE_SPEND_TIERS


# --------------------------------------------------------------------------- #
# Never logs a secret                                                          #
# --------------------------------------------------------------------------- #
def test_result_never_contains_the_key(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeHTTPResponse(200, b'{"data": []}')

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    r = mp.probe_tier("deepseek", _arbitrary_env(), timeout=1.0)
    blob = json.dumps(mp.asdict(r))
    assert "sk-paid" not in blob and "sk-user" not in blob


# --------------------------------------------------------------------------- #
# Availability doc + fanout restriction wiring                                #
# --------------------------------------------------------------------------- #
def test_availability_doc_and_fanout_restriction(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        # ninerouter-aux (openrouter-adjacent free lane -> tier "ninerouter-aux") is up;
        # everything else 404 on /models and is paid -> unknown.
        return _FakeHTTPResponse(200, b'{"data": []}')

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    results = mp.probe_fleet(_arbitrary_env(), tiers=["ninerouter-aux", "deepseek"], timeout=1.0)
    doc = mp._availability_doc(results)
    assert "ninerouter-aux" in doc["available_executors"]

    # Persist to a temp workspace and confirm fanout's restriction reads it.
    monkeypatch.setattr(mp, "resolve_workspace", lambda ws=None: tmp_path)
    path = mp.write_availability(results, workspace=tmp_path)
    assert path.exists()
    got = mp.load_available_executors(workspace=tmp_path)
    assert "ninerouter-aux" in got

    # fanout._restrict_to_available keeps only executors whose TIER is live.
    from cortex_core import fanout
    # 'aux' executor spec has tier 'ninerouter-aux' (live); 'laguna-m.1' tier 'openrouter' (absent).
    kept = fanout._restrict_to_available(["aux", "laguna-m.1"], tmp_path)
    assert kept == ["aux"]


def test_fanout_restriction_degrades_without_probe(tmp_path):
    from cortex_core import fanout
    # No model_availability.json in tmp_path -> no restriction, original list returned.
    assert fanout._restrict_to_available(["aux", "laguna-m.1"], tmp_path) == ["aux", "laguna-m.1"]
