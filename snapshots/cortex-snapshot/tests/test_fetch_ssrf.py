"""SSRF / local-file-read guard tests for ``fetch_document``.

These are the *red* tests for the SSRF fix described in
``reviewed/ssrf-fix-contract-2026-07-03.md`` (formalizing finding #1 of
``reviewed/opus-deep-review-2026-07-03.md``). They are written against the
public ``fetch_document`` boundary and follow the existing convention in
``tests/test_fetch_doc.py``: a fake ``opener`` callable is injected instead
of making real network requests. Host resolution is injected the same way,
via a fake ``resolver`` callable, so no real DNS lookups happen either.

Contract summary the tests depend on:
  * A URL whose scheme is not ``http``/``https`` (``file``, ``ftp``,
    ``gopher``, ``dict``, ...) is rejected with ``ValueError`` *before* the
    opener is ever called.
  * A URL whose host resolves to a loopback / private / link-local /
    otherwise non-global address is rejected with ``ValueError`` before any
    read. Literal-IP hosts are checked directly; named hosts are resolved
    through the injected ``resolver`` (so DNS rebinding cannot bypass a
    string match on the hostname).
  * A public ``http(s)`` URL is allowed through to the opener.
  * The opener is invoked with an explicit, positive ``timeout``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_core.fetch import fetch_document


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
    """Records how it was invoked. Signature accepts ``timeout`` so the fix
    threads a timeout into it (the existing single-arg openers in
    ``test_fetch_doc.py`` deliberately do not accept it and stay unchanged)."""

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


def _public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


# ---------------------------------------------------------------------------
# Scheme allowlist: non-http(s) schemes rejected before any read
# ---------------------------------------------------------------------------


def test_file_url_rejected_before_read(tmp_path: Path) -> None:
    """The exact live-demonstrated exploit: ``file://`` must be refused and
    no read must happen."""
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "file:///etc/passwd",
            "ssrf-probe",
            workspace=workspace,
            opener=opener,
        )

    assert opener.calls == [], "opener must not be called for a file:// URL"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/secret",
        "gopher://example.com:70/1",
        "dict://example.com/d:word",
    ],
)
def test_non_http_schemes_rejected(tmp_path: Path, url: str) -> None:
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(url, "scheme-probe", workspace=workspace, opener=opener)

    assert opener.calls == [], f"opener must not be called for {url!r}"


# ---------------------------------------------------------------------------
# Host allowlist: loopback / private / link-local rejected (literal hosts)
# ---------------------------------------------------------------------------


def test_loopback_ipv4_literal_rejected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "http://127.0.0.1/admin",
            "loopback-probe",
            workspace=workspace,
            opener=opener,
        )

    assert opener.calls == []


def test_loopback_ipv6_literal_rejected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "http://[::1]/admin",
            "loopback6-probe",
            workspace=workspace,
            opener=opener,
        )

    assert opener.calls == []


def test_private_range_literal_rejected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "http://10.0.0.1/internal",
            "private-probe",
            workspace=workspace,
            opener=opener,
        )

    assert opener.calls == []


def test_cloud_metadata_endpoint_rejected(tmp_path: Path) -> None:
    """169.254.169.254 is link-local (the classic cloud metadata SSRF
    target) and must be rejected."""
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "metadata-probe",
            workspace=workspace,
            opener=opener,
        )

    assert opener.calls == []


# ---------------------------------------------------------------------------
# Host allowlist: named hosts resolved, not string-matched (DNS rebinding)
# ---------------------------------------------------------------------------


def test_localhost_hostname_rejected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "http://localhost/admin",
            "localhost-probe",
            workspace=workspace,
            opener=opener,
            resolver=lambda _host: ["127.0.0.1"],
        )

    assert opener.calls == []


def test_dns_rebinding_public_host_resolving_private_rejected(tmp_path: Path) -> None:
    """A perfectly public-looking hostname that *resolves* to a private
    address must be rejected. This proves the guard resolves the host and
    checks the IP, rather than string-matching the hostname."""
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    with pytest.raises(ValueError):
        fetch_document(
            "http://totally-legit-public-site.example.com/doc",
            "rebind-probe",
            workspace=workspace,
            opener=opener,
            resolver=lambda _host: ["10.0.0.1"],
        )

    assert opener.calls == []


# ---------------------------------------------------------------------------
# Happy path (non-regression control) + explicit timeout
# ---------------------------------------------------------------------------


def test_public_ip_url_allowed(tmp_path: Path) -> None:
    """A public host must NOT be rejected. Uses a literal public IP so no
    real DNS lookup is needed. This is a control: it should stay green on
    both the unfixed and fixed code (the guard must not over-block)."""
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    path = fetch_document(
        "https://93.184.216.34/doc",
        "public-doc",
        workspace=workspace,
        opener=opener,
    )

    assert path.exists()
    assert len(opener.calls) == 1
    assert "Source: https://93.184.216.34/doc" in path.read_text(encoding="utf-8")


def test_opener_invoked_with_explicit_timeout(tmp_path: Path) -> None:
    """The opener must be called with an explicit, positive timeout so a
    hanging server cannot block the fetch indefinitely. Uses a literal
    public IP so the request is allowed through to the opener."""
    workspace = _make_workspace(tmp_path)
    opener = _SpyOpener()

    fetch_document(
        "http://93.184.216.34/doc",
        "timeout-doc",
        workspace=workspace,
        opener=opener,
    )

    assert len(opener.calls) == 1
    timeout = opener.calls[0]["timeout"]
    assert timeout is not None, "opener was called without an explicit timeout"
    assert isinstance(timeout, (int, float)) and timeout > 0, (
        f"expected a positive numeric timeout, got {timeout!r}"
    )
