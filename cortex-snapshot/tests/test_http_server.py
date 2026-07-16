"""HTTP transport (GAP-CORTEX-0015 H1): the streamable-http entrypoint + its bearer gate.

The security-relevant unit is the bearer middleware -- it decides 401-vs-through *before* any
MCP logic. That is tested here in isolation (wrapping a trivial app, so no FastMCP
session-manager singleton to fight) plus a static check that build_app() actually wires the
health route and the gate. The full live path (real uvicorn + curl: health open, /mcp 401
without token, real `initialize` with token) was verified by hand at build time; see
docs/HOSTING-RAILWAY.md."""

from __future__ import annotations

import pytest

from cortex_core.authz import hash_token
from cortex_core.http_server import (
    _HEALTH_PATH,
    _BearerAuthMiddleware,
    _configure_transport_security,
    build_app,
)

pytest.importorskip("starlette")
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def _gated_app(token: str) -> Starlette:
    async def ok(_r):
        return PlainTextResponse("through")

    async def health(_r):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"]), Route(_HEALTH_PATH, health)])
    app.add_middleware(_BearerAuthMiddleware, expected_sha256=hash_token(token))
    return app


def test_health_is_exempt_from_auth():
    with TestClient(_gated_app("s3cret")) as c:
        r = c.get(_HEALTH_PATH)
        assert r.status_code == 200 and r.text == "ok"


def test_rejected_without_bearer():
    with TestClient(_gated_app("s3cret")) as c:
        assert c.post("/mcp").status_code == 401


def test_rejected_with_wrong_bearer():
    with TestClient(_gated_app("s3cret")) as c:
        assert c.post("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_accepted_with_correct_bearer():
    with TestClient(_gated_app("s3cret")) as c:
        r = c.post("/mcp", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200 and r.text == "through"


def test_build_app_wires_health_and_gate(monkeypatch):
    # Gate present only when the env is set (owner/local HTTP leaves it open by design).
    monkeypatch.setenv("CORTEX_HTTP_BEARER_SHA256", hash_token("x"))
    app = build_app()
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert _HEALTH_PATH in paths and "/mcp" in paths
    assert any(m.cls is _BearerAuthMiddleware for m in app.user_middleware)

    monkeypatch.delenv("CORTEX_HTTP_BEARER_SHA256", raising=False)
    assert not any(m.cls is _BearerAuthMiddleware for m in build_app().user_middleware)


def test_transport_security_allows_railway_public_domain(monkeypatch):
    # Regression: the SDK's DNS-rebinding guard 421'd the deploy's OWN public domain
    # because it wasn't in the (empty-by-default) allow-list. Railway injects
    # RAILWAY_PUBLIC_DOMAIN -- pick it up automatically so a bare deploy just works.
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "myapp.up.railway.app")
    monkeypatch.delenv("CORTEX_HTTP_ALLOWED_HOSTS", raising=False)
    from cortex_core.mcp import mcp

    _configure_transport_security(mcp)
    sec = mcp.settings.transport_security
    assert "myapp.up.railway.app" in sec.allowed_hosts
    assert "https://myapp.up.railway.app" in sec.allowed_origins
    assert "localhost" in sec.allowed_hosts  # local dev still works


def test_transport_security_explicit_hosts_override(monkeypatch):
    monkeypatch.setenv("CORTEX_HTTP_ALLOWED_HOSTS", "cortex.example.com, other.example.com")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    from cortex_core.mcp import mcp

    _configure_transport_security(mcp)
    sec = mcp.settings.transport_security
    assert sec.allowed_hosts == ["cortex.example.com", "other.example.com"]
