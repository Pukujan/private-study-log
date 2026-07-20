"""Per-tenant API key issuance (H2b / browser-extension auth).

The owner mints SCOPED keys that clients (a browser extension, any connecting agent) present as the
bearer. Only the **SHA-256 + metadata** is stored; the raw key is returned ONCE at issuance. Keys are
independently **rotatable/revocable**, so a leaked browser key is killed without touching anyone else.
Non-admin by design: a key's scope is `read` (search + guidance) or `tenant_write` (writes land in
that tenant's own plane) -- never admin. Design: docs/research/browser-extension-and-key-issuance-*.

Store note: the registry (hashes + metadata, no raw keys) lives at `<workspace>/logs/api_keys.json`
(gitignored -- auth state, never the public repo). On an ephemeral hosted disk this needs mirroring to
durable storage (like the telemetry sink) before multi-client hosting -- tracked as a follow-up.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cortex_core.authz import hash_token

SCOPES = {"read", "tenant_write"}

# H2: relative-TTL grammar. `30d` / `12h` / `90d` / `45m` / `60s` / `2w`. An absolute ISO-8601
# date/datetime is also accepted. None (the default) means the key never expires -- back-compat.
_TTL_RE = re.compile(r"^(\d+)\s*([smhdw])$")
_TTL_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _coerce_now(now: datetime | str | None) -> datetime:
    """Resolve the reference 'now' to a tz-aware UTC datetime. Injected for deterministic tests;
    defaults to the real wall clock. A naive datetime/string is assumed UTC."""
    if now is None:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(now) if isinstance(now, str) else now
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _ttl_to_expiry(ttl: str | None, now: datetime) -> str | None:
    """Turn a TTL spec into an absolute ISO-8601 expiry, or None for a never-expiring key.
    Accepts a relative window (`30d`, `12h`, ...) or an absolute ISO date/datetime."""
    if ttl is None:
        return None
    s = str(ttl).strip()
    if not s:
        return None
    m = _TTL_RE.match(s.lower())
    if m:
        return (now + timedelta(seconds=int(m.group(1)) * _TTL_SECONDS[m.group(2)])).isoformat()
    dt = datetime.fromisoformat(s)  # absolute date/datetime; ValueError bubbles up on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _is_expired(rec: dict, now: datetime) -> bool:
    exp = rec.get("expires_at")
    if not exp:
        return False
    return now >= _coerce_now(exp)


def _status(rec: dict, now: datetime) -> str:
    """Computed status: revoked (terminal) > expired (past its window) > active."""
    if rec.get("status") == "revoked":
        return "revoked"
    return "expired" if _is_expired(rec, now) else "active"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store(store_path: str | Path | None) -> Path:
    if store_path is not None:
        return Path(store_path)
    from cortex_core.config import resolve_workspace
    return resolve_workspace() / "logs" / "api_keys.json"


def _load(store_path) -> dict:
    p = _store(store_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(store_path, data: dict) -> None:
    p = _store(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def issue_key(label: str, scope: str = "read", tenant_id: str | None = None,
              no_log: bool = False, ttl: str | None = None,
              store_path: str | Path | None = None,
              now: datetime | str | None = None) -> tuple[str, str]:
    """Mint a scoped key. Returns `(key_id, raw_key)` -- the raw key is shown ONCE; only its hash is
    stored. `tenant_id` defaults to the key_id (each key its own tenant plane). `no_log` sets the
    per-tenant no-log flag (GAP G6): when True the server never logs/mirrors this tenant's usage.
    `ttl` (H2) sets an expiry -- `30d`/`12h`/`90d` or an absolute ISO date; None (default) = never
    expires (back-compat). `now` is injected for deterministic tests."""
    scope = scope if scope in SCOPES else "read"
    ref = _coerce_now(now)
    raw = "cortex_" + secrets.token_urlsafe(32)
    key_id = "ck_" + uuid.uuid4().hex[:12]
    data = _load(store_path)
    data[key_id] = {"key_id": key_id, "sha256": hash_token(raw), "label": label, "scope": scope,
                    "tenant_id": tenant_id or key_id, "no_log": bool(no_log),
                    "status": "active", "created": ref.isoformat(),
                    "ttl": (str(ttl).strip() or None) if ttl is not None else None,
                    "expires_at": _ttl_to_expiry(ttl, ref)}
    _save(store_path, data)
    return key_id, raw


def verify_key(raw: str | None, store_path: str | Path | None = None,
               now: datetime | str | None = None) -> dict | None:
    """`{key_id, tenant_id, scope, no_log}` for a valid ACTIVE, non-expired key, else None
    (constant-time; fail-closed). An EXPIRED key (past its H2 TTL window) verifies as None --
    expired != active. `no_log` is the G6 flag; absent in a legacy record -> False. `now` injectable."""
    if not raw:
        return None
    ref = _coerce_now(now)
    h = hash_token(raw)
    for rec in _load(store_path).values():
        if rec.get("status") == "active" and hmac.compare_digest(rec.get("sha256", ""), h):
            if _is_expired(rec, ref):
                return None                                  # expired -> fail closed
            return {"key_id": rec["key_id"], "tenant_id": rec["tenant_id"], "scope": rec["scope"],
                    "no_log": bool(rec.get("no_log", False))}
    return None


def set_expiry(key_id: str, ttl: str | None, store_path: str | Path | None = None,
               now: datetime | str | None = None) -> bool:
    """H2: set / extend / clear a key's expiry after issuance. `ttl` = `30d`/`12h`/absolute-date
    (recomputed from `now`), or None to clear it (never expires). Returns True if the key existed."""
    data = _load(store_path)
    rec = data.get(key_id)
    if rec is None:
        return False
    ref = _coerce_now(now)
    rec["ttl"] = (str(ttl).strip() or None) if ttl is not None else None
    rec["expires_at"] = _ttl_to_expiry(ttl, ref)
    _save(store_path, data)
    return True


def set_no_log(key_id: str, no_log: bool, store_path: str | Path | None = None) -> bool:
    """GAP G6: set/clear the per-tenant no-log flag for `key_id`. The owner controls this; a no-log
    tenant's queries are never logged or mirrored server-side. Returns True if the key existed."""
    data = _load(store_path)
    rec = data.get(key_id)
    if rec is None:
        return False
    rec["no_log"] = bool(no_log)
    _save(store_path, data)
    return True


def revoke_key(key_id: str, store_path: str | Path | None = None) -> bool:
    data = _load(store_path)
    rec = data.get(key_id)
    if rec and rec.get("status") == "active":
        rec["status"] = "revoked"
        rec["revoked"] = _now()
        _save(store_path, data)
        return True
    return False


def rotate_key(key_id: str, store_path: str | Path | None = None,
               now: datetime | str | None = None) -> tuple[str, str]:
    """Revoke `key_id` and issue a fresh key with the same label/scope/tenant/no-log. A relative
    TTL (`30d`) is carried and given a FRESH window from the rotation moment; an absolute-date
    expiry is carried as-is. Returns the new `(key_id, raw_key)`."""
    old = _load(store_path).get(key_id)
    if not old:
        raise KeyError(key_id)
    ref = _coerce_now(now)
    revoke_key(key_id, store_path)
    old_ttl = old.get("ttl")
    new_id, raw = issue_key(old["label"], scope=old["scope"], tenant_id=old["tenant_id"],
                            no_log=bool(old.get("no_log", False)), ttl=old_ttl,
                            store_path=store_path, now=ref)
    data = _load(store_path)
    data[new_id]["rotated_from"] = key_id
    # absolute-date expiry (no relative ttl) carries forward unchanged
    if not old_ttl and old.get("expires_at"):
        data[new_id]["expires_at"] = old["expires_at"]
    _save(store_path, data)
    return new_id, raw


def list_keys(store_path: str | Path | None = None,
              now: datetime | str | None = None) -> list[dict]:
    """Metadata only -- never the raw key or its hash. `status` is COMPUTED
    (active / expired / revoked) against `now`; `expires_at` is the absolute H2 expiry (None=never)."""
    ref = _coerce_now(now)
    return [{"key_id": r["key_id"], "label": r["label"], "scope": r["scope"],
             "tenant_id": r["tenant_id"], "no_log": bool(r.get("no_log", False)),
             "status": _status(r, ref), "created": r.get("created"),
             "expires_at": r.get("expires_at")}
            for r in _load(store_path).values()]


def main(argv: list[str] | None = None) -> int:
    """`cortex-key`: owner-side API-key admin (issue/list/revoke/rotate + GAP G6 no-log flag).

    The no-log flag is the server-enforced half of the DATA-USE.md opt-out promise: a tenant marked
    no-log has their queries neither logged locally nor mirrored to R2. No MCP connection needed;
    operates directly on the key store (`<workspace>/logs/api_keys.json` by default)."""
    import argparse
    import json as _json
    from cortex_core.config import make_stdio_encoding_safe
    make_stdio_encoding_safe()
    p = argparse.ArgumentParser(prog="cortex-key", description="Cortex API-key admin (owner-side)")
    p.add_argument("--store", default=None, help="key store path (default <workspace>/logs/api_keys.json)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="mint a scoped key (raw key shown ONCE)")
    p_issue.add_argument("label")
    p_issue.add_argument("--scope", default="read", choices=sorted(SCOPES))
    p_issue.add_argument("--tenant-id", default=None)
    p_issue.add_argument("--no-log", action="store_true", help="G6: never log/mirror this tenant")
    p_issue.add_argument("--ttl", default=None,
                         help="H2 expiry: 30d/12h/90d or an absolute ISO date (default: never expires)")

    sub.add_parser("list", help="list key metadata (never the raw key or hash)")

    p_exp = sub.add_parser("set-expiry", help="H2: set/extend/clear a key's expiry")
    p_exp.add_argument("key_id")
    p_exp.add_argument("--ttl", default=None,
                       help="30d/12h/absolute-date; omit (or empty) to clear -> never expires")

    p_revoke = sub.add_parser("revoke", help="revoke a key immediately")
    p_revoke.add_argument("key_id")

    p_rot = sub.add_parser("rotate", help="revoke + reissue with the same scope/tenant/no-log")
    p_rot.add_argument("key_id")

    p_nl = sub.add_parser("no-log", help="G6: set/clear a tenant's no-log flag")
    p_nl.add_argument("key_id")
    g = p_nl.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", dest="set_flag", action="store_true", help="stop logging this tenant")
    g.add_argument("--clear", dest="clear_flag", action="store_true", help="resume logging this tenant")

    a = p.parse_args(argv)
    if a.cmd == "issue":
        kid, raw = issue_key(a.label, scope=a.scope, tenant_id=a.tenant_id, no_log=a.no_log,
                             ttl=a.ttl, store_path=a.store)
        exp = next((r["expires_at"] for r in list_keys(store_path=a.store) if r["key_id"] == kid), None)
        print(_json.dumps({"key_id": kid, "api_key": raw, "scope": a.scope, "no_log": a.no_log,
                           "expires_at": exp,
                           "warning": "raw key shown ONCE -- store it now; only its SHA-256 is kept"},
                          indent=2))
        return 0
    if a.cmd == "list":
        print(_json.dumps(list_keys(store_path=a.store), indent=2))
        return 0
    if a.cmd == "set-expiry":
        ok = set_expiry(a.key_id, a.ttl, store_path=a.store)
        print(f"{a.key_id}: expiry {'set' if a.ttl else 'cleared (never expires)'}" if ok
              else "no such key")
        return 0 if ok else 1
    if a.cmd == "revoke":
        print("revoked" if revoke_key(a.key_id, store_path=a.store) else "no such active key")
        return 0
    if a.cmd == "rotate":
        try:
            nid, raw = rotate_key(a.key_id, store_path=a.store)
        except KeyError:
            print("no such key")
            return 1
        print(_json.dumps({"key_id": nid, "api_key": raw, "rotated_from": a.key_id}, indent=2))
        return 0
    if a.cmd == "no-log":
        want = bool(a.set_flag)
        ok = set_no_log(a.key_id, want, store_path=a.store)
        print(f"{a.key_id}: no_log={'on' if want else 'off'}" if ok else "no such key")
        return 0 if ok else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
