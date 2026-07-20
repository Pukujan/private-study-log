"""Tests for the optional `playwright` fetch backend (the `[browser]` extra).

Covers, WITHOUT requiring playwright/Chromium to be installed:
  * backend selection (`_resolve_backend`: arg > env > default; unknown -> native),
  * the browser backend's SSRF boundary guard (rejects private/loopback BEFORE
    launching a browser; the per-request host-validation decision function),
  * graceful degradation (`backend="playwright"` with playwright absent falls
    back to the native path and still writes the doc, never crashes).

And, ONLY when playwright + a Chromium browser are actually installed, a real
end-to-end JS-render test: a locally served page whose content is injected by
JS after load -- proving native/urllib captures only the empty shell while the
playwright backend captures the JS-rendered content.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cortex_core import browser_fetch
from cortex_core import fetch as fetch_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


# ---------------------------------------------------------------------------
# backend selection
# ---------------------------------------------------------------------------


def test_resolve_backend_arg_wins(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_FETCH_BACKEND", raising=False)
    assert fetch_mod._resolve_backend("playwright") == "playwright"
    assert fetch_mod._resolve_backend("native") == "native"


def test_resolve_backend_env_used_when_no_arg(monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FETCH_BACKEND", "playwright")
    assert fetch_mod._resolve_backend(None) == "playwright"


def test_resolve_backend_defaults_to_native(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_FETCH_BACKEND", raising=False)
    assert fetch_mod._resolve_backend(None) == "native"


def test_resolve_backend_unknown_degrades_to_native(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_FETCH_BACKEND", raising=False)
    assert fetch_mod._resolve_backend("grokto") == "native"  # not built -> native
    assert fetch_mod._resolve_backend("nonsense") == "native"


# ---------------------------------------------------------------------------
# SSRF: the browser backend's boundary + per-request host validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "ftp://example.com/x",  # illegal scheme
    ],
)
def test_browser_boundary_refuses_non_global_before_launch(url: str) -> None:
    """`fetch_rendered_html` validates the top URL BEFORE importing/launching a
    browser, so a private/loopback/illegal target raises ValueError even in an
    environment where playwright isn't installed at all. A private-range target
    must never reach the browser engine."""
    # Resolver maps 'localhost' to loopback so the non-literal case is covered too.
    resolver = lambda host: ["127.0.0.1"] if host == "localhost" else [host]
    with pytest.raises(ValueError):
        browser_fetch.fetch_rendered_html(url, resolver=resolver)


def test_host_is_global_decision_matches_native_rule() -> None:
    """The route guard's per-sub-request decision reuses the native rule exactly:
    global public IPs pass, private/loopback/link-local fail, and it never raises."""
    pub = lambda host: ["93.184.216.34"]
    priv = lambda host: ["127.0.0.1"]
    assert browser_fetch._host_is_global("https://example.com/x", pub) is True
    assert browser_fetch._host_is_global("https://internal.example/x", priv) is False
    # unresolvable / illegal scheme -> False, not an exception
    assert browser_fetch._host_is_global("ftp://example.com/x", pub) is False


# ---------------------------------------------------------------------------
# graceful degradation: playwright requested but unavailable -> native path
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._data = text.encode("utf-8")
        self._done = False

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *a) -> None:
        return None

    def read(self, size: int | None = None) -> bytes:
        # Return the body once, then b"" so the chunked _read_capped loop ends.
        if self._done:
            return b""
        self._done = True
        return self._data


def test_playwright_unavailable_degrades_to_native(tmp_path: Path, monkeypatch) -> None:
    """Requesting the playwright backend when the extra isn't installed must NOT
    crash: it falls back to the native fetch path and still writes the doc."""
    workspace = _make_workspace(tmp_path)

    def _boom(url, resolver=None, timeout=None):
        raise browser_fetch.PlaywrightUnavailable("not installed (simulated)")

    monkeypatch.setattr(browser_fetch, "fetch_rendered_html", _boom)

    native_calls: list[str] = []

    def fake_opener(url, timeout=None):
        native_calls.append(url)
        return _FakeResponse("native fallback body")

    path = fetch_mod.fetch_document(
        "https://example.com/spa",
        "degrade-probe",
        workspace=workspace,
        opener=fake_opener,
        resolver=lambda host: ["93.184.216.34"],
        backend="playwright",
    )

    assert native_calls == ["https://example.com/spa"], "must have degraded to native opener"
    assert "native fallback body" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# REAL end-to-end JS render (only if playwright + Chromium are installed)
# ---------------------------------------------------------------------------

_SHELL_TOKEN = "LOADING_PRE_RENDER_SHELL"
_JS_TOKEN = "REAL_JS_RENDERED_CONTENT_9f3a"

_JS_PAGE = f"""<!doctype html>
<html><head><title>JS fixture</title></head>
<body><div id="app">{_SHELL_TOKEN}</div>
<script>
  document.getElementById('app').textContent = '{_JS_TOKEN}';
</script>
</body></html>
"""


class _JSFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = _JS_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a) -> None:  # silence
        return None


@pytest.fixture()
def js_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JSFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def _chromium_ready() -> bool:
    if not browser_fetch.playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_ready(), reason="playwright + Chromium not installed")
def test_native_gets_shell_playwright_gets_rendered(tmp_path: Path, monkeypatch, js_server) -> None:
    """The load-bearing proof: on a JS-rendered page, native/urllib captures only
    the pre-render shell, while the playwright backend captures the JS-injected
    content. SSRF is bypassed for the loopback fixture host only."""
    # Allow the loopback fixture host through the global-IP guard for THIS test
    # (we are testing rendering, not the SSRF guard -- that has its own tests).
    real_is_global = fetch_mod._is_global_ip
    monkeypatch.setattr(
        fetch_mod, "_is_global_ip",
        lambda ip: True if ip.startswith("127.") else real_is_global(ip),
    )
    ws = _make_workspace(tmp_path)

    native_path = fetch_mod.fetch_document(js_server, "native-shell", workspace=ws, backend="native")
    native_text = native_path.read_text(encoding="utf-8")
    assert _SHELL_TOKEN in native_text
    assert _JS_TOKEN not in native_text, "native must NOT see JS-injected content"

    pw_path = fetch_mod.fetch_document(js_server, "pw-rendered", workspace=ws, backend="playwright")
    pw_text = pw_path.read_text(encoding="utf-8")
    assert _JS_TOKEN in pw_text, "playwright backend must capture the JS-rendered content"
