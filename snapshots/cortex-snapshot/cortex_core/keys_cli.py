"""`cortex-key` — owner CLI for per-tenant API key issuance/rotation/revocation.

Thin argparse wrapper over ``cortex_core.keys``. The owner mints SCOPED,
REVOCABLE bearer keys that connecting clients (a friend's agent wrapper, a
browser extension) present to the HTTP MCP. Only the SHA-256 + metadata is
stored; the raw key is printed ONCE at issuance.

IMPORTANT — where the store lives: keys are written to
``<CORTEX_WORKSPACE>/logs/api_keys.json``. The DEPLOYED server reads that same
path (on Railway, the ``/data`` volume). So to issue a key the connecting
client can actually use, run this **in the server's environment** — e.g.
``railway ssh`` into the running container, or ``railway run`` with
``CORTEX_WORKSPACE`` pointed at the durable volume — NOT against a throwaway
local workspace the server never sees.

Examples:
    cortex-key issue --label "phantomic" --scope read
    cortex-key list
    cortex-key rotate ck_ab12cd34ef56
    cortex-key revoke ck_ab12cd34ef56
"""
from __future__ import annotations

import argparse
import sys

from cortex_core import keys


def _cmd_issue(a: argparse.Namespace) -> int:
    key_id, raw = keys.issue_key(a.label, scope=a.scope, tenant_id=a.tenant, store_path=a.store)
    print(f"key_id : {key_id}")
    print(f"scope  : {a.scope}")
    print(f"tenant : {a.tenant or key_id}")
    print("")
    print("RAW KEY (shown ONCE — copy it now, it is not stored and cannot be recovered):")
    print(f"  {raw}")
    print("")
    print("Give this to the client for their .env  CORTEX_HTTP_BEARER=<key>.")
    return 0


def _cmd_list(a: argparse.Namespace) -> int:
    rows = keys.list_keys(store_path=a.store)
    if not rows:
        print("(no keys issued)")
        return 0
    for r in rows:
        print(f"{r['key_id']}  {r['status']:<8} {r['scope']:<12} {r['label']!r}  (tenant {r['tenant_id']}, {r.get('created','?')})")
    return 0


def _cmd_revoke(a: argparse.Namespace) -> int:
    ok = keys.revoke_key(a.key_id, store_path=a.store)
    print(f"revoked {a.key_id}" if ok else f"no active key {a.key_id} to revoke")
    return 0 if ok else 1


def _cmd_rotate(a: argparse.Namespace) -> int:
    new_id, raw = keys.rotate_key(a.key_id, store_path=a.store)
    print(f"rotated {a.key_id} -> {new_id} (old key is now revoked)")
    print("")
    print("NEW RAW KEY (shown ONCE):")
    print(f"  {raw}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cortex-key", description="Cortex per-tenant API key management")
    p.add_argument("--store", default=None,
                   help="explicit key-store path (default: <CORTEX_WORKSPACE>/logs/api_keys.json)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("issue", help="mint a new scoped key (raw shown once)")
    pi.add_argument("--label", required=True, help="human label, e.g. the client's name")
    pi.add_argument("--scope", default="read", choices=sorted(keys.SCOPES),
                    help="read = search+guidance (recommended for external clients); tenant_write = writes to that tenant's plane")
    pi.add_argument("--tenant", default=None, help="tenant id (default: the new key_id)")
    pi.set_defaults(fn=_cmd_issue)

    pl = sub.add_parser("list", help="list keys (metadata only, never raw)")
    pl.set_defaults(fn=_cmd_list)

    pr = sub.add_parser("revoke", help="revoke a key by id")
    pr.add_argument("key_id")
    pr.set_defaults(fn=_cmd_revoke)

    pt = sub.add_parser("rotate", help="revoke a key and issue a fresh one (same label/scope/tenant)")
    pt.add_argument("key_id")
    pt.set_defaults(fn=_cmd_rotate)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
