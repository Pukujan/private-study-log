"""R1 (DNS-rebinding TOCTOU IP-pinning) + R2 (NAT64 prefix) *red* tests.

These are the failing tests for the fix described in
``reviewed/ssrf-r1-pinning-fix-contract-2026-07-03.md`` (formalizing findings
R1 [MEDIUM] and R2 [LOW] of ``reviewed/ssrf-fix-review-2026-07-03.md``). They
are additive to ``tests/test_fetch_ssrf.py`` (the original 12 SSRF tests),
which is left untouched.

Why a second file, and why it reaches one layer deeper than
``tests/test_fetch_ssrf.py``:

  The original suite injects a *fake opener* and a *fixed resolver*, so it can
  prove ``_validate_url`` rejects a host whose resolved IP is non-global. It
  *cannot* see R1: the residual gap is that the real network path
  (``_default_opener`` -> ``urllib`` -> the socket layer) does its **own**
  DNS resolution at connect time, so a low-TTL rebinding attacker can pass
  validation as "public" and then have the socket connect somewhere private.
  The validated IP is never pinned into the connection. Proving that gap is
  closed requires observing *what IP the socket is actually told to dial*, not
  just what ``_validate_url`` decides -- so these tests bind to the pinning
  seam the contract mandates:

    * ``fetch._PinnedHTTPConnection`` / ``fetch._PinnedHTTPSConnection`` --
      ``http.client`` connection subclasses whose ``connect()`` dials a
      caller-supplied ``pinned_ip`` (via an injectable ``create_connection``)
      while leaving ``self.host`` as the original hostname, so the ``Host``
      header and TLS SNI still use the real name.
    * ``fetch._default_opener(url, timeout=None, resolver=None,
      create_connection=None)`` -- the ``create_connection`` seam is threaded
      down into the pinned connections so a test can record the dialed address
      end-to-end, including that a *lying* resolver's validated IP (not an
      independent re-resolution of the hostname) is the IP dialed.

No real network or DNS is used: the socket layer is replaced by a recording
fake, and host resolution is the injected ``resolver`` callable.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import pytest

from cortex_core import fetch as fetch_mod


# ---------------------------------------------------------------------------
# Fakes: a recording socket connector + minimal socket / TLS-context doubles
# ---------------------------------------------------------------------------


class _FakeSocket:
    """Just enough socket surface for a pinned ``connect()`` to complete."""

    def setsockopt(self, *args, **kwargs) -> None:
        return None

    def close(self) -> None:
        return None


class _RecordingConnector:
    """Stands in for ``socket.create_connection``. Records every ``(ip, port)``
    it is asked to dial. Optionally raises straight after recording, to
    short-circuit before any real I/O when driven through the full opener."""

    def __init__(self, *, raise_after: bool = False) -> None:
        self.addresses: list[tuple] = []
        self._raise_after = raise_after

    def __call__(self, address, *args, **kwargs):
        self.addresses.append(tuple(address))
        if self._raise_after:
            raise OSError("stop-before-real-io")
        return _FakeSocket()


class _FakeSSLContext:
    """Records the ``server_hostname`` (SNI) it is asked to wrap with."""

    def __init__(self) -> None:
        self.server_hostname = "__unset__"

    def wrap_socket(self, sock, server_hostname=None, **kwargs) -> _FakeSocket:
        self.server_hostname = server_hostname
        return _FakeSocket()


class _FakeResponse:
    def __init__(self, text: str = "hello world") -> None:
        self._text = text

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


class _SpyOpener:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url: str, timeout=None, **kwargs) -> _FakeResponse:
        self.calls.append({"url": url, "timeout": timeout, "kwargs": kwargs})
        return _FakeResponse()


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _clear_proxy_env(monkeypatch) -> None:
    """Neutralize any ambient HTTP(S) proxy so the opener exercises the direct
    connection path (the one pinning is about). Without this, urllib's default
    ProxyHandler reroutes the request to the proxy host and the test would
    observe the proxy's address instead of the pinned origin IP."""
    for var in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# R1 mechanism (unit): the pinned connection dials the pinned IP, not the host,
# and preserves the original hostname for the Host header / TLS SNI.
# ---------------------------------------------------------------------------


def test_pinned_http_connection_dials_pinned_ip_not_host() -> None:
    """``_PinnedHTTPConnection.connect()`` must dial the pinned IP, while
    leaving ``self.host`` (used for the ``Host`` header) as the real name."""
    rec = _RecordingConnector()
    conn = fetch_mod._PinnedHTTPConnection(
        "totally-legit-public-site.example.com",
        80,
        pinned_ip="93.184.216.34",
        create_connection=rec,
    )

    conn.connect()

    assert rec.addresses == [("93.184.216.34", 80)], (
        "connection must dial the validated/pinned IP, not the hostname"
    )
    assert conn.host == "totally-legit-public-site.example.com", (
        "original hostname must be preserved for the Host header"
    )


def test_pinned_https_connection_preserves_sni_and_host() -> None:
    """``_PinnedHTTPSConnection.connect()`` must dial the pinned IP but hand the
    original hostname to TLS as ``server_hostname`` (SNI), so certificate
    validation and virtual-hosted TLS still work against the real name."""
    rec = _RecordingConnector()
    conn = fetch_mod._PinnedHTTPSConnection(
        "totally-legit-public-site.example.com",
        443,
        pinned_ip="93.184.216.34",
        create_connection=rec,
    )
    fake_ctx = _FakeSSLContext()
    conn._context = fake_ctx

    conn.connect()

    assert rec.addresses == [("93.184.216.34", 443)], (
        "TLS connection must dial the validated/pinned IP"
    )
    assert fake_ctx.server_hostname == "totally-legit-public-site.example.com", (
        "TLS SNI must be the original hostname, not the pinned IP"
    )
    assert conn.host == "totally-legit-public-site.example.com"


# ---------------------------------------------------------------------------
# R1 TOCTOU closure (integration through _default_opener): given a resolver
# that returns a specific public IP, the socket is told to dial *that* IP --
# proving the connection uses the validated resolution rather than an
# independent re-resolution of the hostname (the check-vs-use window is shut).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected_port",
    [
        ("http://totally-legit-public-site.example.com/doc", 80),
        ("https://totally-legit-public-site.example.com/doc", 443),
    ],
)
def test_default_opener_pins_validated_ip_and_does_not_reresolve(
    monkeypatch, url: str, expected_port: int
) -> None:
    """A resolver returning ``93.184.216.34`` is the ONLY source of that IP;
    a re-resolving implementation would instead hand the made-up hostname to
    the OS resolver (which this test does not control and which would never
    return 93.184.216.34 for that name). So a recorded dial of exactly
    ``(93.184.216.34, port)`` proves the validated IP was pinned into the
    socket -- the DNS-rebinding TOCTOU is closed on the initial hop."""
    _clear_proxy_env(monkeypatch)
    rec = _RecordingConnector(raise_after=True)

    with pytest.raises((URLError, OSError)):
        fetch_mod._default_opener(
            url,
            timeout=7,
            resolver=lambda _host: ["93.184.216.34"],
            create_connection=rec,
        )

    assert rec.addresses == [("93.184.216.34", expected_port)], (
        "the socket must be told to dial the resolver-validated IP, not a "
        "re-resolution of the hostname"
    )


# ---------------------------------------------------------------------------
# R2 (LOW): NAT64 well-known prefix embedding a private IPv4 must be rejected.
# ---------------------------------------------------------------------------


# 64:ff9b::/96 embeds an IPv4 in its low 32 bits; ``is_global`` sees only the
# v6 wrapper and reports these as global. Each embeds a non-global IPv4.
_NAT64_PRIVATE = [
    "64:ff9b::7f00:1",     # -> 127.0.0.1   (loopback)
    "64:ff9b::a00:1",      # -> 10.0.0.1    (private)
    "64:ff9b::a9fe:a9fe",  # -> 169.254.169.254 (link-local cloud metadata)
]


@pytest.mark.parametrize("nat64_ip", _NAT64_PRIVATE)
def test_is_global_ip_rejects_nat64_embedded_private(nat64_ip: str) -> None:
    assert fetch_mod._is_global_ip(nat64_ip) is False, (
        f"NAT64 address {nat64_ip} embeds a non-global IPv4 and must not be "
        f"treated as global"
    )


@pytest.mark.parametrize("nat64_ip", _NAT64_PRIVATE)
def test_nat64_embedded_private_rejected_end_to_end(tmp_path: Path, nat64_ip: str) -> None:
    """Through the public boundary: a URL whose literal IPv6 host is a NAT64
    address embedding a private IPv4 is refused before any read."""
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_mod.fetch_document(
            f"http://[{nat64_ip}]/latest/meta-data/",
            "nat64-probe",
            workspace=workspace,
            opener=opener,
        )

    assert opener.calls == [], "opener must not be called for a NAT64-embedded private host"


# ---------------------------------------------------------------------------
# Control (green on both unfixed and fixed code): the R2 hardening must not
# over-block genuine public addresses.
# ---------------------------------------------------------------------------


def test_is_global_ip_still_allows_public_addresses() -> None:
    assert fetch_mod._is_global_ip("93.184.216.34") is True
    assert fetch_mod._is_global_ip("2606:2800:220:1:248:1893:25c8:1946") is True
