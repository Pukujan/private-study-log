"""RED tests for Phase-0 KE item 4 — KE-02 (partial): proxy fail-fast.

Contract: ``reviewed/phase0-ke-fixes-contract-2026-07-04.md`` (§4);
PHASE-GATES 0.17; BUILD-PLAN Phase 0 addendum KE-02.

The SSRF hardening pins each connection to a validated origin IP and
*direct-dials* it (``_PinnedConnectionMixin._connect_pinned_socket``), so
``urllib``'s ``ProxyHandler`` never fires. On a proxy-only egress host — where
``HTTPS_PROXY`` / ``HTTP_PROXY`` is set — the pinned socket dials the origin
directly, is dropped, and the fetch dies with a bare connect *timeout* that
gives the operator no hint the proxy was bypassed (repro'd 2026-07-04:
proxy-403 vs pinning-timeout, two different failure modes proving the proxy
is skipped).

Desired (gate 0.17) — the cheap half only; proxy-aware pinning itself stays
trigger-gated: detect ``HTTPS_PROXY`` / ``HTTP_PROXY`` at fetch-init and
**fail fast** with a clear ``RuntimeError`` ("IP-pinning is incompatible with
an HTTP proxy … set ``CORTEX_ALLOW_PROXY=1`` to override"), raised *before*
any network use, instead of a silent timeout.

Note: this sandbox has ``HTTPS_PROXY`` set ambiently, so every test here
controls the proxy environment explicitly (``monkeypatch``) rather than
trusting the inherited environment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cortex_core.fetch as fetch_mod
from cortex_core.fetch import fetch_document

_PUBLIC_IP = "93.184.216.34"


class _SpyOpener:
    """Records every call; returns a canned body. If the proxy guard fires
    first, this must never be invoked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str, timeout=None, **kwargs):
        self.calls.append(url)

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return None

            def read(self_inner) -> bytes:
                return b"hello world"

        return _Resp()


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _clear_proxy_env(monkeypatch) -> None:
    for var in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)


def test_fetch_fails_fast_when_https_proxy_set(tmp_path: Path, monkeypatch) -> None:
    """RED: with ``HTTPS_PROXY`` set, ``fetch_document`` must raise a clear
    ``RuntimeError`` naming the proxy incompatibility and the
    ``CORTEX_ALLOW_PROXY`` opt-out — *before* touching the network. Today it
    silently proceeds (direct-dialing the pinned IP), so no such error is
    raised."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(RuntimeError) as excinfo:
        fetch_document(
            "https://example.com/doc",
            "proxy-probe",
            workspace=workspace,
            opener=opener,
            resolver=lambda _host: [_PUBLIC_IP],
        )

    message = str(excinfo.value)
    assert "proxy" in message.lower(), (
        f"error must explain the proxy incompatibility; got: {message!r}"
    )
    assert "CORTEX_ALLOW_PROXY" in message, (
        f"error must mention the CORTEX_ALLOW_PROXY opt-out; got: {message!r}"
    )
    assert opener.calls == [], "fetch must fail fast before invoking the opener (no network)"


def test_fetch_fails_fast_when_http_proxy_set(tmp_path: Path, monkeypatch) -> None:
    """RED: ``HTTP_PROXY`` (the plain-HTTP proxy variable) must trip the same
    fail-fast guard as ``HTTPS_PROXY``."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.internal:8080")
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(RuntimeError) as excinfo:
        fetch_document(
            "https://example.com/doc",
            "proxy-probe",
            workspace=workspace,
            opener=opener,
            resolver=lambda _host: [_PUBLIC_IP],
        )

    assert "CORTEX_ALLOW_PROXY" in str(excinfo.value)
    assert opener.calls == [], "fetch must fail fast before invoking the opener (no network)"


def test_fetch_proceeds_when_no_proxy_set(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): with no proxy in the environment the guard
    must not fire — fetch proceeds normally and the opener is used. Green
    today and after the fix; guards against the guard over-firing."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    _clear_proxy_env(monkeypatch)
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    path = fetch_document(
        "https://example.com/doc",
        "no-proxy-probe",
        workspace=workspace,
        opener=opener,
        resolver=lambda _host: [_PUBLIC_IP],
    )

    assert path.exists(), "fetch should succeed when no proxy is configured"
    assert opener.calls == ["https://example.com/doc"], "opener should have been used"


def test_fetch_proxy_opt_out_allows_proxy(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): ``CORTEX_ALLOW_PROXY=1`` opts out of the guard,
    so a fetch with a proxy set proceeds. Green today (the flag is unused and a
    fake opener proceeds) and after the fix (the opt-out must be honored);
    guards that the escape hatch is wired, not forgotten."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")
    monkeypatch.setenv("CORTEX_ALLOW_PROXY", "1")
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    path = fetch_document(
        "https://example.com/doc",
        "opt-out-probe",
        workspace=workspace,
        opener=opener,
        resolver=lambda _host: [_PUBLIC_IP],
    )

    assert path.exists(), "CORTEX_ALLOW_PROXY=1 must let the fetch proceed despite a proxy"
    assert opener.calls == ["https://example.com/doc"], "opener should have been used"
