"""Owner-only LOCAL CRUD dashboard for the per-tenant API key store (H2 companion).

A small self-contained web UI over `cortex_core.keys` so the owner can issue / revoke / rotate /
set-expiry / toggle-no-log keys from a browser instead of the CLI. It is deliberately locked down --
this is owner tooling, not a hosted surface:

- **Localhost only.** It refuses to bind anything but 127.0.0.1 / ::1 / localhost. There is no code
  path that opens it to a network interface; `serve()` raises on a non-loopback host.
- **Admin-bearer gated (`CORTEX_HTTP_BEARER_SHA256`).** Every `/api/*` request must carry
  `Authorization: Bearer <token>` whose SHA-256 matches the configured hash (constant-time compare).
  If the hash is unset the server refuses to start -- it never runs unauthenticated, even locally.
- **No secret ever leaves the box.** The list view is metadata-only (no raw key, no stored SHA-256).
  A raw key is shown exactly ONCE, in the issue/rotate response, and is never logged.

It reuses `keys.py` for all mutations -- the store format and safety invariants live in one place.
Launch: `cortex-keys-dashboard` (see pyproject). Set `CORTEX_HTTP_BEARER_SHA256` first
(`python -c "from cortex_core.authz import hash_token; print(hash_token('...'))"`).
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path

from .authz import hash_token
from .config import make_stdio_encoding_safe

BEARER_ENV = "CORTEX_HTTP_BEARER_SHA256"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Self-contained page: no external CDN, no remote fonts/scripts (same CSP discipline as the
# artifact/HTTP surfaces). The admin token is pasted once and kept only in this tab's memory.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cortex Keys -- owner dashboard</title>
<style>
 :root{color-scheme:light dark}
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:1.5rem;
   max-width:1100px;margin-inline:auto;line-height:1.4}
 h1{font-size:1.25rem;margin:0 0 .25rem}
 .sub{opacity:.7;font-size:.85rem;margin-bottom:1rem}
 fieldset{border:1px solid #8886;border-radius:8px;margin:0 0 1rem;padding:.75rem 1rem}
 legend{font-weight:600;padding:0 .4rem}
 label{display:inline-block;font-size:.85rem;margin:.2rem .6rem .2rem 0}
 input,select,button{font:inherit;padding:.35rem .5rem;border-radius:6px;border:1px solid #8886;
   background:Canvas;color:CanvasText}
 button{cursor:pointer;background:#3b82f6;color:#fff;border:0}
 button.ghost{background:#8883;color:CanvasText}
 button:hover{filter:brightness(1.08)}
 table{border-collapse:collapse;width:100%;font-size:.85rem}
 th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid #8883;white-space:nowrap}
 .st-active{color:#16a34a;font-weight:600}
 .st-expired{color:#d97706;font-weight:600}
 .st-revoked{color:#dc2626;font-weight:600}
 .raw{font-family:ui-monospace,Menlo,Consolas,monospace;background:#fde68a;color:#000;
   padding:.5rem .75rem;border-radius:6px;word-break:break-all;margin:.5rem 0}
 .msg{font-size:.85rem;margin:.5rem 0;min-height:1.2em}
 .err{color:#dc2626}
 code{font-family:ui-monospace,Menlo,Consolas,monospace}
</style></head><body>
<h1>Cortex Keys &mdash; owner dashboard</h1>
<div class="sub">Localhost only &middot; admin-bearer gated &middot; raw keys shown once, never stored.</div>

<fieldset><legend>Admin token</legend>
 <label>Bearer <input id="tok" type="password" size="44" placeholder="paste CORTEX_HTTP_BEARER value"></label>
 <button onclick="refresh()">Connect / refresh</button>
 <div id="authmsg" class="msg"></div>
</fieldset>

<fieldset><legend>Issue a key</legend>
 <label>Label <input id="i_label" placeholder="phantomic"></label>
 <label>Scope <select id="i_scope"><option>read</option><option>tenant_write</option></select></label>
 <label>TTL <input id="i_ttl" size="8" placeholder="30d (blank=never)"></label>
 <label><input id="i_nolog" type="checkbox"> no-log</label>
 <button onclick="issue()">Issue</button>
 <div id="raw" class="raw" style="display:none"></div>
 <div id="issuemsg" class="msg"></div>
</fieldset>

<fieldset><legend>Keys</legend>
 <div id="listmsg" class="msg"></div>
 <table><thead><tr><th>key_id</th><th>label</th><th>scope</th><th>status</th>
   <th>expires_at</th><th>no_log</th><th>actions</th></tr></thead>
 <tbody id="rows"></tbody></table>
</fieldset>

<script>
function tok(){return document.getElementById('tok').value.trim()}
async function api(path,body){
  const r=await fetch(path,{method:body?'POST':'GET',
    headers:{'Authorization':'Bearer '+tok(),'Content-Type':'application/json'},
    body:body?JSON.stringify(body):undefined});
  if(r.status===401)throw new Error('401 unauthorized -- check the bearer token');
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function refresh(){
  const am=document.getElementById('authmsg'),lm=document.getElementById('listmsg');
  am.textContent='';lm.textContent='';lm.className='msg';
  try{
    const d=await api('/api/keys');
    am.textContent='Connected. '+d.keys.length+' key(s).';
    const rows=document.getElementById('rows');rows.innerHTML='';
    for(const k of d.keys){
      const tr=document.createElement('tr');
      const act=k.status==='revoked'?'':
        `<button class="ghost" onclick="rotate('${k.key_id}')">rotate</button> `+
        `<button class="ghost" onclick="revoke('${k.key_id}')">revoke</button> `+
        `<button class="ghost" onclick="setexp('${k.key_id}')">set-ttl</button> `+
        `<button class="ghost" onclick="nolog('${k.key_id}',${!k.no_log})">${k.no_log?'log':'no-log'}</button>`;
      tr.innerHTML=`<td><code>${esc(k.key_id)}</code></td><td>${esc(k.label)}</td>`+
        `<td>${esc(k.scope)}</td><td class="st-${esc(k.status)}">${esc(k.status)}</td>`+
        `<td>${esc(k.expires_at)||'never'}</td><td>${k.no_log?'yes':'no'}</td><td>${act}</td>`;
      rows.appendChild(tr);
    }
  }catch(e){am.textContent=e.message;am.className='msg err'}
}
async function issue(){
  const m=document.getElementById('issuemsg'),raw=document.getElementById('raw');
  m.textContent='';m.className='msg';raw.style.display='none';
  try{
    const d=await api('/api/keys/issue',{label:document.getElementById('i_label').value,
      scope:document.getElementById('i_scope').value,ttl:document.getElementById('i_ttl').value,
      no_log:document.getElementById('i_nolog').checked});
    raw.textContent='RAW KEY (shown once, copy now): '+d.raw;raw.style.display='block';
    m.textContent='Issued '+d.key_id;refresh();
  }catch(e){m.textContent=e.message;m.className='msg err'}
}
async function revoke(id){if(!confirm('Revoke '+id+'?'))return;
  try{await api('/api/keys/revoke',{key_id:id});refresh()}catch(e){alert(e.message)}}
async function rotate(id){if(!confirm('Rotate '+id+'? Old key dies immediately.'))return;
  try{const d=await api('/api/keys/rotate',{key_id:id});
    const raw=document.getElementById('raw');raw.textContent='NEW RAW KEY (shown once): '+d.raw;
    raw.style.display='block';refresh()}catch(e){alert(e.message)}}
async function setexp(id){const t=prompt('New TTL for '+id+' (e.g. 30d / 12h / ISO date; blank=never):','');
  if(t===null)return;try{await api('/api/keys/set-expiry',{key_id:id,ttl:t});refresh()}catch(e){alert(e.message)}}
async function nolog(id,v){try{await api('/api/keys/no-log',{key_id:id,no_log:v});refresh()}catch(e){alert(e.message)}}
</script></body></html>"""


def _authorized(request, expected_sha256: str) -> bool:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not token:
        return False
    return hmac.compare_digest(hash_token(token).lower(), expected_sha256)


def build_app(bearer_sha256: str, store_path: str | Path | None = None):
    """Build the Starlette app. `bearer_sha256` MUST be a non-empty configured hash -- an empty
    value raises (the server never runs unauthenticated). All `/api/*` routes are 401 without a
    matching bearer; `/` serves the (secret-free) HTML shell."""
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Route

    expected = (bearer_sha256 or "").strip().lower()
    if not expected:
        raise ValueError(
            f"{BEARER_ENV} is not set -- the keys dashboard refuses to run unauthenticated. "
            "Set it first: python -c \"from cortex_core.authz import hash_token; print(hash_token('...'))\""
        )
    from . import keys as _keys

    async def index(_request):
        return HTMLResponse(_PAGE)

    def _guard(request):
        if not _authorized(request, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    async def _body(request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001
            return {}

    async def list_keys(request):
        g = _guard(request)
        if g is not None:
            return g
        # metadata only -- keys.list_keys never returns the raw key or the stored sha256
        return JSONResponse({"keys": _keys.list_keys(store_path=store_path)})

    async def issue(request):
        g = _guard(request)
        if g is not None:
            return g
        b = await _body(request)
        label = str(b.get("label") or "client").strip() or "client"
        scope = str(b.get("scope") or "read")
        ttl = b.get("ttl") or None
        no_log = bool(b.get("no_log", False))
        try:
            key_id, raw = _keys.issue_key(label, scope=scope, no_log=no_log, ttl=ttl,
                                          store_path=store_path)
        except ValueError as e:  # bad TTL spec
            return JSONResponse({"error": f"invalid ttl: {e}"}, status_code=400)
        # raw shown ONCE here; never persisted, never logged
        return JSONResponse({"key_id": key_id, "raw": raw, "scope": scope, "label": label})

    async def revoke(request):
        g = _guard(request)
        if g is not None:
            return g
        b = await _body(request)
        return JSONResponse({"revoked": _keys.revoke_key(str(b.get("key_id") or ""),
                                                         store_path=store_path)})

    async def rotate(request):
        g = _guard(request)
        if g is not None:
            return g
        b = await _body(request)
        try:
            new_id, raw = _keys.rotate_key(str(b.get("key_id") or ""), store_path=store_path)
        except KeyError:
            return JSONResponse({"error": "no such key"}, status_code=404)
        return JSONResponse({"key_id": new_id, "raw": raw})

    async def set_expiry(request):
        g = _guard(request)
        if g is not None:
            return g
        b = await _body(request)
        ttl = b.get("ttl") or None
        try:
            ok = _keys.set_expiry(str(b.get("key_id") or ""), ttl, store_path=store_path)
        except ValueError as e:
            return JSONResponse({"error": f"invalid ttl: {e}"}, status_code=400)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    async def no_log(request):
        g = _guard(request)
        if g is not None:
            return g
        b = await _body(request)
        ok = _keys.set_no_log(str(b.get("key_id") or ""), bool(b.get("no_log", False)),
                              store_path=store_path)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/api/keys", list_keys, methods=["GET"]),
        Route("/api/keys/issue", issue, methods=["POST"]),
        Route("/api/keys/revoke", revoke, methods=["POST"]),
        Route("/api/keys/rotate", rotate, methods=["POST"]),
        Route("/api/keys/set-expiry", set_expiry, methods=["POST"]),
        Route("/api/keys/no-log", no_log, methods=["POST"]),
    ]
    return Starlette(routes=routes)


def _require_localhost(host: str) -> str:
    """Refuse to bind anything but the loopback interface. This is owner-only tooling; there is no
    supported way to expose it on a network interface."""
    h = (host or "").strip().lower()
    if h not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"keys dashboard refuses to bind {host!r}: owner-only, localhost only "
            f"(allowed: {', '.join(sorted(_LOOPBACK_HOSTS))}). Never expose it on a public interface."
        )
    return "127.0.0.1" if h == "localhost" else h


def serve(host: str = "127.0.0.1", port: int = 8787,
          bearer_sha256: str | None = None, store_path: str | Path | None = None) -> None:
    """Validate (localhost + admin bearer configured) and serve. Raises before binding if the host
    is not loopback or the bearer hash is unset -- fail-closed, never a silent open door."""
    import uvicorn

    host = _require_localhost(host)
    if bearer_sha256 is None:
        bearer_sha256 = os.environ.get(BEARER_ENV) or ""
    app = build_app(bearer_sha256, store_path=store_path)  # raises if bearer unset
    print(f"cortex-keys-dashboard: owner-only key admin at http://{host}:{port} "
          f"(localhost only, admin-bearer gated). Ctrl-C to stop.", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> int:
    """`cortex-keys-dashboard`: launch the owner-only local key-management web UI."""
    import argparse

    make_stdio_encoding_safe()
    p = argparse.ArgumentParser(
        prog="cortex-keys-dashboard",
        description="Owner-only LOCAL key-management dashboard (localhost only, admin-bearer gated).")
    p.add_argument("--host", default="127.0.0.1",
                   help="loopback host only (127.0.0.1/localhost/::1); refuses any other")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--store", default=None,
                   help="key store path (default <workspace>/logs/api_keys.json)")
    a = p.parse_args(argv)
    try:
        serve(host=a.host, port=a.port, store_path=a.store)
    except ValueError as e:
        print(f"cortex-keys-dashboard: {e}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
