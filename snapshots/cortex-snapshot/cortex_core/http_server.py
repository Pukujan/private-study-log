"""HTTP transport for the Cortex MCP server (GAP-CORTEX-0015 H1).

The stdio server (`cortex-mcp`) is for a locally-spawned client (Hermes, a CLI). To
*host* Cortex -- serve the brain to a remote client over the network (Railway, a VPS) --
the same tool surface has to speak HTTP. This module is that transport and nothing more:
it wraps the identical `FastMCP` app (`cortex_core.mcp.mcp`, all 30 registered tools
as of G5 2026-07-14, the same
served-mode admin gate and dual-plane routing) in `streamable-http` and runs it under
uvicorn. No tool logic changes; only how bytes arrive.

Two safety layers, both env-gated and off by default (so local/owner use is unaffected):

- **Connection auth (`CORTEX_HTTP_BEARER_SHA256`)**: when set, every request except the
  health probe must carry `Authorization: Bearer <token>` whose SHA-256 matches. This is
  the *front door* -- it decides who may reach the tools at all. The corpus asset stays
  protected even without it, because reads are bounded (no bulk export) and writes are
  already admin-gated by `authz`; but a public endpoint should still set it so the server
  isn't an open relay. Hash a token the same way as the admin secret:
  `python -c "from cortex_core.authz import hash_token; print(hash_token('...'))"`.
- **Write auth**: unchanged -- served mode (`CORTEX_SERVER_MODE=served`) makes the canonical
  brain immutable without the admin token, via the existing `_admin_gate` in `mcp.py`.

Health: `GET /healthz` returns 200 without auth (Railway/most PaaS healthchecks are
unauthenticated). The MCP endpoint itself is at `/mcp`.

**Host header allow-list**: the MCP SDK's streamable-http transport enables DNS-rebinding
protection by default, which rejects any `Host` header not in an explicit allow-list --
correct for a browser-facing localhost dev server, but it means a bare deploy returns
`421 Invalid Host header` for its own public domain. `_configure_transport_security` builds
that allow-list from (in order): `CORTEX_HTTP_ALLOWED_HOSTS` (explicit, comma-separated,
for a custom domain), else Railway's auto-injected `RAILWAY_PUBLIC_DOMAIN`, else `localhost`
and `127.0.0.1` (so local `cortex-mcp-http` runs keep working). This does not weaken the
bearer gate above -- it only tells the transport which hostnames a legitimate deploy answers
to; a mismatched Host header is still a rebinding attempt, not a real client.

Honest scope: this is H1 (transport) + a connection-auth floor. It is *single-tenant
served-brain* ready (one admin's brain, read by authenticated clients). Hardened
*per-tenant* identity -- distinct customers, isolated write folders, revocable creds -- is
H2b and is deliberately NOT here; do not expose untrusted multi-tenant writes on this alone.
"""

from __future__ import annotations

import hmac
import os

from .authz import hash_token
from .config import make_stdio_encoding_safe

_HEALTH_PATH = "/healthz"


class _BearerAuthMiddleware:
    """Pure-ASGI bearer gate. Rejects any non-exempt request lacking a valid
    `Authorization: Bearer <token>` (constant-time hash compare). Exempts the health
    path so PaaS probes still pass."""

    def __init__(self, app, expected_sha256: str, exempt: tuple[str, ...] = (_HEALTH_PATH,)) -> None:
        self.app = app
        self.expected = expected_sha256.strip().lower()
        self.exempt = exempt

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path == e or path.startswith(e.rstrip("/") + "/") for e in self.exempt):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not self._authorized(token):
            from starlette.responses import PlainTextResponse

            return await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)

    def _authorized(self, token: str) -> bool:
        """Accept the shared transport bearer (owner/admin) OR any valid per-tenant issued API key
        (multi-tenant: a browser extension / connecting agent presents its issued key). Constant-time
        for the shared bearer; fail-closed if the key store is unavailable."""
        if not token:
            return False
        if hmac.compare_digest(hash_token(token).lower(), self.expected):
            return True
        try:
            from cortex_core.keys import verify_key
            return verify_key(token) is not None
        except Exception:  # noqa: BLE001 -- key store unavailable must never 500 the gate
            return False


def _configure_transport_security(mcp) -> None:
    """Point the SDK's DNS-rebinding Host/Origin allow-list at wherever this process is
    actually reachable, so a legitimate deploy doesn't 421 itself. See module docstring."""
    from mcp.server.transport_security import TransportSecuritySettings

    explicit = (os.environ.get("CORTEX_HTTP_ALLOWED_HOSTS") or "").strip()
    if explicit:
        hosts = [h.strip() for h in explicit.split(",") if h.strip()]
    else:
        railway_domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
        hosts = [railway_domain] if railway_domain else []
        hosts += ["localhost", "127.0.0.1"]
        # Port-agnostic local dev (cortex-mcp-http on a non-default $PORT).
        hosts += ["localhost:*", "127.0.0.1:*"]

    origins = [f"https://{h}" for h in hosts if h and ":" not in h] + [
        f"http://{h}" for h in hosts if h and ":" not in h
    ]
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def build_app():
    """Build the Starlette ASGI app: the FastMCP streamable-http app + a `/healthz` route,
    wrapped in the bearer gate when `CORTEX_HTTP_BEARER_SHA256` is set."""
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from .mcp import mcp  # import here so building the app doesn't require HTTP deps for stdio use

    # stateless_http: no server-side session store between requests -- each call is
    # self-contained. This is what lets the server survive a Railway redeploy or run behind
    # a load balancer without sticky sessions. Our tools are request/response, so nothing is lost.
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    _configure_transport_security(mcp)

    app = mcp.streamable_http_app()

    async def _health(_request):
        return PlainTextResponse("ok")

    app.router.routes.insert(0, Route(_HEALTH_PATH, _health, methods=["GET"]))

    async def _admin_keys(request):
        """Owner-only key issuance over HTTP (so no container shell is needed to mint a
        per-tenant key). Gated by the ADMIN token (SHA-256 compare vs CORTEX_ADMIN_TOKEN_SHA256),
        NOT the transport bearer -- this path is exempt from the bearer middleware and does its
        own stronger admin check. Writes to the server's own key store (CORTEX_WORKSPACE/logs)."""
        from starlette.responses import JSONResponse

        admin_sha = (os.environ.get("CORTEX_ADMIN_TOKEN_SHA256") or "").strip().lower()
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not admin_sha or not token or not hmac.compare_digest(hash_token(token).lower(), admin_sha):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        action = str(body.get("action") or "issue")
        from cortex_core import keys as _keys

        if action == "list":
            return JSONResponse({"keys": _keys.list_keys()})
        if action == "revoke":
            return JSONResponse({"revoked": _keys.revoke_key(str(body.get("key_id") or ""))})
        if action == "rotate":
            kid, raw = _keys.rotate_key(str(body.get("key_id") or ""))
            return JSONResponse({"key_id": kid, "raw": raw})
        # default: issue
        label = str(body.get("label") or "client")
        scope = str(body.get("scope") or "read")
        key_id, raw = _keys.issue_key(label, scope=scope)
        return JSONResponse({"key_id": key_id, "raw": raw, "scope": scope, "label": label})

    app.router.routes.insert(0, Route("/admin/keys", _admin_keys, methods=["POST"]))

    bearer = (os.environ.get("CORTEX_HTTP_BEARER_SHA256") or "").strip()
    if bearer:
        # /admin/* is exempt from the transport-bearer gate; _admin_keys enforces the stronger
        # admin-token check itself.
        app.add_middleware(_BearerAuthMiddleware, expected_sha256=bearer, exempt=(_HEALTH_PATH, "/admin"))
    return app


def main() -> None:
    """`cortex-mcp-http` entrypoint. Binds 0.0.0.0:$PORT (Railway injects $PORT) and serves
    the MCP over streamable-http."""
    make_stdio_encoding_safe()
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    if not (os.environ.get("CORTEX_HTTP_BEARER_SHA256") or "").strip():
        # Loud, non-fatal: a hosted endpoint with no bearer is an open door. Local runs can
        # ignore it; a deploy should set the env. Not a hard fail so owner-mode HTTP still works.
        print(
            "cortex-mcp-http: WARNING no CORTEX_HTTP_BEARER_SHA256 set -- the endpoint is "
            "unauthenticated. Set it before exposing publicly (see cortex_core/http_server.py).",
            flush=True,
        )
    # Mirror per-session telemetry records to durable storage (R2) on a background thread, so the
    # rich signal survives this server's ephemeral disk / a redeploy. No-op unless configured.
    try:
        from . import telemetry
        if telemetry.start_background_flush():
            print("cortex-mcp-http: durable telemetry flush started (records/ -> R2)", flush=True)
    except Exception:  # noqa: BLE001 -- telemetry must never block serving
        pass
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
