"""Ownership + admin authentication for the Cortex MCP server.

Cortex is a workspace (files) served over MCP. This module answers one
question: *may this caller mutate the canonical corpus/rubrics/gold?* The
model (see docs/CORTEX-ROUTES-AND-OWNERSHIP.md, the authoritative doctrine):

- **owner mode** (default): the Cortex is running on the machine that owns the
  files. The owner is implicitly admin; writes flow exactly as before. Nothing
  changes for local dev or a locally-spawned orchestrator (Hermes) -- this keeps
  the change backward-compatible.
- **served mode**: the Cortex is exposed as a shared, admin-owned *instruction
  server* -- its accumulated rubrics, calibration, and gold are a permanent
  asset meant to make connected agents smarter. In this mode the corpus is
  **immutable without admin authentication**: any connected agent may READ
  (search / status / scope_pack), but only a session that presented a valid
  admin token at register time may mutate.

The admin secret is never stored or transmitted in the clear here: the server
holds only a SHA-256 hash (env `CORTEX_ADMIN_TOKEN_SHA256`), and a caller proves
admin by presenting the raw token, which is hashed and compared in constant time.
Generate the hash without it ever touching argv/logs: `python -m cortex_core.authz
--hash` (reads the token with no echo).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
from typing import Mapping

SERVER_MODE_ENV = "CORTEX_SERVER_MODE"
ADMIN_HASH_ENV = "CORTEX_ADMIN_TOKEN_SHA256"
# A SECOND, independent secret that gates LOCAL CONFIG CHANGES -- repointing which
# workspace/brain a client uses, changing routing/mode. Distinct from the admin token (which
# gates corpus WRITES): this stops a connected agent from silently reconfiguring the local
# setup to bypass the harness. Like the admin token, only the SHA-256 hash is stored. When it
# is unset (the owner default), config changes are allowed -- backward compatible.
CONFIG_PASSCODE_HASH_ENV = "CORTEX_CONFIG_PASSCODE_SHA256"

MODE_OWNER = "owner"
MODE_SERVED = "served"
_VALID_MODES = {MODE_OWNER, MODE_SERVED}


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def resolve_server_mode(env: Mapping[str, str] | None = None) -> str:
    """Resolve the server mode from the environment. Anything unrecognized
    (including unset) falls back to `owner` -- the safe default that preserves
    today's local-write behavior. A server is only locked down when it's
    *explicitly* published with CORTEX_SERVER_MODE=served."""
    raw = (_env(env).get(SERVER_MODE_ENV) or MODE_OWNER).strip().lower()
    return raw if raw in _VALID_MODES else MODE_OWNER


def hash_token(token: str) -> str:
    """SHA-256 hex of a token. The stored admin secret is this hash, never the
    raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def admin_configured(env: Mapping[str, str] | None = None) -> bool:
    return bool((_env(env).get(ADMIN_HASH_ENV) or "").strip())


def verify_admin_token(token: str | None, env: Mapping[str, str] | None = None) -> bool:
    """True iff `token` hashes to the server-configured admin hash. Constant-time
    compare (hmac.compare_digest) so a timing side-channel can't leak the hash.
    False if no admin hash is configured or no token is presented -- never a
    fail-open."""
    if not token:
        return False
    stored = (_env(env).get(ADMIN_HASH_ENV) or "").strip().lower()
    if not stored:
        return False
    return hmac.compare_digest(hash_token(token).lower(), stored)


def mutation_requires_admin(env: Mapping[str, str] | None = None) -> bool:
    """Whether a corpus mutation must be admin-authenticated in the current mode.
    Owner mode: False (owner is implicitly admin). Served mode: True (the
    canonical instruction server is immutable to non-admins)."""
    return resolve_server_mode(env) == MODE_SERVED


def config_change_requires_passcode(env: Mapping[str, str] | None = None) -> bool:
    """True iff a local config passcode is configured. When unset (owner default), local config
    changes are allowed with no passcode -- backward compatible, exactly like the admin token."""
    return bool((_env(env).get(CONFIG_PASSCODE_HASH_ENV) or "").strip())


def verify_config_passcode(token: str | None, env: Mapping[str, str] | None = None) -> bool:
    """True iff `token` hashes to the configured config-passcode hash. Constant-time; fail-closed
    (False on no token or no configured hash) -- never a fail-open."""
    if not token:
        return False
    stored = (_env(env).get(CONFIG_PASSCODE_HASH_ENV) or "").strip().lower()
    if not stored:
        return False
    return hmac.compare_digest(hash_token(token).lower(), stored)


def authorize_config_change(
    passcode: str | None, env: Mapping[str, str] | None = None
) -> tuple[bool, str]:
    """Gate a LOCAL config change (repoint the workspace/brain a client uses, change routing/mode).
    Allowed when no passcode is configured (owner default) OR a matching passcode is presented;
    refused otherwise. Returns (allowed, reason) so callers can surface why."""
    if not config_change_requires_passcode(env):
        return True, "no config passcode configured (owner mode) -- change allowed"
    if verify_config_passcode(passcode, env):
        return True, "config passcode verified"
    return False, (
        "local config change refused: a valid config passcode is required "
        f"({CONFIG_PASSCODE_HASH_ENV} is set). Present the passcode to make this change."
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(
        description="Cortex admin-token hashing. Never stores, echoes, or logs the raw token."
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help=f"read an admin token with no echo and print the {ADMIN_HASH_ENV}=<hash> line to paste into .env",
    )
    parser.add_argument(
        "--hash-config",
        action="store_true",
        help=f"read a LOCAL CONFIG passcode with no echo and print the {CONFIG_PASSCODE_HASH_ENV}=<hash> line",
    )
    args = parser.parse_args(argv)
    if args.hash or args.hash_config:
        label = "Admin token" if args.hash else "Config passcode"
        env_key = ADMIN_HASH_ENV if args.hash else CONFIG_PASSCODE_HASH_ENV
        token = getpass.getpass(f"{label} (input hidden): ")
        if not token:
            print("no token provided", file=sys.stderr)
            return 1
        print(f"{env_key}={hash_token(token)}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
