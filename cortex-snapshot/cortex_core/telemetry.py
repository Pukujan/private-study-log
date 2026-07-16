"""Durable telemetry sink (opt-in): mirror audit closeouts to S3-compatible storage (Cloudflare R2).

WHY: a served brain on Railway has an EPHEMERAL disk -- every closeout a connected agent writes
there is lost on redeploy. But those closeouts (especially failures) are the fuel of the
self-learning loop: patterns are mined from them, the state machine is tuned by them. So when the
opt-in env is present, every closeout written on the serving plane is mirrored to durable object
storage, and `cortex-telemetry-import` pulls the durable stream down into the canonical corpus
(`audit/audit-log-remote/`) on the owner's machine, where learning actually runs.

Design rules:
  * OPT-IN -- no env configured means a true no-op (zero network, zero cost, local use unaffected).
  * FAIL-OPEN -- the sink must NEVER break or delay a closeout write; any failure returns False.
  * No heavy deps -- SigV4 is ~40 lines of stdlib hmac/hashlib; R2 speaks S3 SigV4 (region "auto").
  * Import is idempotent -- a file already in audit-log-remote/ is never re-fetched.

Env contract (values live in gitignored .env locally / Railway variables when hosted):
  CORTEX_TELEMETRY_S3_ENDPOINT   e.g. https://<account-id>.r2.cloudflarestorage.com
  CORTEX_TELEMETRY_S3_BUCKET     bucket name
  CORTEX_TELEMETRY_S3_KEY_ID     access key id
  CORTEX_TELEMETRY_S3_SECRET     secret access key
  CORTEX_TELEMETRY_PREFIX        logical plane name, e.g. "railway-prod" (default "default")
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

_REGION = "auto"  # R2's region; also valid for real S3 when the endpoint pins the region
_REMOTE_SHARD = ("audit", "audit-log-remote", "agent")

_ENV_KEYS = ("CORTEX_TELEMETRY_S3_ENDPOINT", "CORTEX_TELEMETRY_S3_BUCKET",
             "CORTEX_TELEMETRY_S3_KEY_ID", "CORTEX_TELEMETRY_S3_SECRET")


def _cfg(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    import os
    if env is None:
        # os.environ wins (Railway variables), then fill gaps from the repo's gitignored .env so a
        # LOCAL user who drops R2 creds into .env is picked up without a global dotenv load.
        e = dict(os.environ)
        try:
            from cortex_core.judge import load_env
            for k, v in load_env().items():
                e.setdefault(k, v)
        except Exception:  # noqa: BLE001 -- .env absent/unreadable must not break telemetry
            pass
    else:
        e = env
    vals = {k: (e.get(k) or "").strip() for k in _ENV_KEYS}
    if not all(vals.values()):
        return None
    return {"endpoint": vals["CORTEX_TELEMETRY_S3_ENDPOINT"].rstrip("/"),
            "bucket": vals["CORTEX_TELEMETRY_S3_BUCKET"],
            "key_id": vals["CORTEX_TELEMETRY_S3_KEY_ID"],
            "secret": vals["CORTEX_TELEMETRY_S3_SECRET"],
            "prefix": (e.get("CORTEX_TELEMETRY_PREFIX") or "default").strip().strip("/")}


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return _cfg(env) is not None


# ------------------------------------------------------------------------------ SigV4 (stdlib-only)
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sigv4_headers(method: str, endpoint: str, path: str, query: str,
                   payload: bytes, key_id: str, secret: str) -> dict[str, str]:
    host = urllib.parse.urlparse(endpoint).netloc
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date = amz_date[:8]
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical = "\n".join([
        method, urllib.parse.quote(path), query,
        "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash])
    scope = f"{date}/{_REGION}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret}".encode("utf-8"), date), _REGION), "s3"),
              "aws4_request")
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
                                f"SignedHeaders={signed}, Signature={signature}")
    return headers


def _request(cfg: dict[str, str], method: str, key: str, payload: bytes = b"",
             query: str = "", timeout: float = 15.0) -> bytes:
    path = f"/{cfg['bucket']}/{key}" if key else f"/{cfg['bucket']}"
    url = cfg["endpoint"] + path + (f"?{query}" if query else "")
    headers = _sigv4_headers(method, cfg["endpoint"], path, query, payload,
                             cfg["key_id"], cfg["secret"])
    req = urllib.request.Request(url, data=payload if method == "PUT" else None,
                                 method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - operator-configured sink
        return resp.read()


# ------------------------------------------------------------------------------ mirror (fail-open)
def mirror_file(path: str | Path, env: Mapping[str, str] | None = None) -> bool:
    """Best-effort PUT of one file to the durable sink under {prefix}/{filename}. True on success,
    False on ANY failure or when telemetry is not configured -- never raises, never blocks the
    caller's own write (fail-open: losing one mirror beats breaking a closeout)."""
    cfg = _cfg(env)
    if cfg is None:
        return False
    try:
        p = Path(path)
        _request(cfg, "PUT", f"{cfg['prefix']}/{p.name}", p.read_bytes())
        return True
    except Exception:  # noqa: BLE001 -- fail-open by contract
        return False


# ------------------------------------------------------------------------------ import (idempotent)
def _list_keys(cfg: dict[str, str]) -> list[str]:
    query = urllib.parse.urlencode(
        sorted({"list-type": "2", "prefix": f"{cfg['prefix']}/"}.items()))
    xml = _request(cfg, "GET", "", query=query).decode("utf-8", errors="replace")
    return re.findall(r"<Key>([^<]+)</Key>", xml)


def import_remote(workspace: str | Path, env: Mapping[str, str] | None = None) -> int:
    """Pull the durable telemetry stream down into `audit/audit-log-remote/agent/` in the given
    workspace (the canonical corpus, where pattern-mining/self-learning runs). Idempotent: files
    already present are skipped. Returns the number of NEW files imported. Raises on a dead sink --
    an explicit import is a foreground operation, not a fail-open background mirror."""
    cfg = _cfg(env)
    if cfg is None:
        return 0
    dest = Path(workspace).joinpath(*_REMOTE_SHARD)
    dest.mkdir(parents=True, exist_ok=True)
    imported = 0
    for key in _list_keys(cfg):
        name = key.rsplit("/", 1)[-1]
        if not name or (dest / name).exists():
            continue
        (dest / name).write_bytes(_request(cfg, "GET", key))
        imported += 1
    return imported


# ------------------------------------------------------------------ session records (the rich signal)
_MIRRORED: set = set()          # (session_id, content_hash) already PUT -- skip unchanged
_FLUSH_STARTED = False


def mirror_session_records(workspace: str | Path | None = None,
                           env: Mapping[str, str] | None = None) -> int:
    """Build per-session output-contract records from the local MCP event log and mirror CHANGED ones
    to R2 under `records/{plane}/{session_id}.json`. This carries the rich signal (tool sequence,
    brain-first, timing, closeout coverage) even when an agent never writes a closeout. Fail-open;
    returns count mirrored this call."""
    cfg = _cfg(env)
    if cfg is None:
        return 0
    try:
        import hashlib
        from cortex_core.config import resolve_workspace
        from cortex_core.output_contract import _read_events, records_from_events
        ws = resolve_workspace(workspace)
        recs = records_from_events(_read_events(ws))
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for r in recs:
        sid = r.get("session_id")
        if not sid:
            continue
        body = json.dumps(r, ensure_ascii=False, sort_keys=True).encode("utf-8")
        tag = (sid, hashlib.sha256(body).hexdigest())
        if tag in _MIRRORED:
            continue
        try:
            _request(cfg, "PUT", f"records/{cfg['prefix']}/{sid}.json", body)
            _MIRRORED.add(tag)
            n += 1
        except Exception:  # noqa: BLE001 -- fail-open
            pass
    return n


def start_background_flush(workspace: str | Path | None = None, interval: float = 20.0) -> bool:
    """Start ONE daemon thread that periodically mirrors session records to R2 (for the long-running
    hosted server, whose event log is on ephemeral disk). No-op if telemetry unconfigured."""
    global _FLUSH_STARTED
    if _FLUSH_STARTED or not enabled(env=None):
        return False
    import threading

    def _loop():
        while True:
            time.sleep(interval)
            try:
                mirror_session_records(workspace)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_loop, daemon=True).start()
    _FLUSH_STARTED = True
    return True


def _list_keys_prefix(cfg: dict[str, str], list_prefix: str) -> list[str]:
    query = urllib.parse.urlencode(sorted({"list-type": "2", "prefix": list_prefix}.items()))
    xml = _request(cfg, "GET", "", query=query).decode("utf-8", errors="replace")
    return [k for k in re.findall(r"<Key>([^<]+)</Key>", xml) if k.startswith(list_prefix)]


def import_records(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Pull ALL `records/**` session-record objects from R2 (every plane) and return them parsed --
    the 'pull from R2' step for the improvement loop. Raises on a dead sink (explicit foreground op)."""
    cfg = _cfg(env)
    if cfg is None:
        return []
    out = []
    for key in _list_keys_prefix(cfg, "records/"):
        try:
            out.append(json.loads(_request(cfg, "GET", key)))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    """`cortex-telemetry-import`: pull the durable agent-telemetry stream into this workspace."""
    import argparse
    from cortex_core.config import make_stdio_encoding_safe, resolve_workspace
    make_stdio_encoding_safe()
    p = argparse.ArgumentParser(description="Import durable agent telemetry (R2/S3) into the corpus")
    p.add_argument("--workspace", default=None)
    a = p.parse_args(argv)
    if not enabled():
        print("telemetry not configured (CORTEX_TELEMETRY_S3_* unset) -- nothing to import")
        return 0
    n = import_remote(resolve_workspace(a.workspace))
    print(f"imported {n} new telemetry file(s) into audit/audit-log-remote/agent/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
