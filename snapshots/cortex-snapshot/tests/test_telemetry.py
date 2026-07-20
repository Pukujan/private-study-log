"""Durable telemetry sink (opt-in): closeouts written on an EPHEMERAL served plane (Railway wipes its
disk on redeploy) mirror to S3-compatible durable storage (Cloudflare R2), and a local import pulls
the durable stream down into the canonical corpus where the self-learning loop runs. Without this,
connected agents' failure data -- the fuel of self-learning -- is structurally lost.

Contract under test: OPT-IN (no env -> exact no-op, zero network), FAIL-OPEN (sink failure never
breaks or delays the closeout write), and round-trip (mirror -> import -> lands in
audit/audit-log-remote/, idempotent on re-import). The stub HTTP server stands in for R2; we assert
request shape (SigV4-authorized PUT/LIST/GET to the right keys), not AWS's crypto."""
from __future__ import annotations

import http.server
import threading
from pathlib import Path

from cortex_core import telemetry


class _StubS3(http.server.BaseHTTPRequestHandler):
    store: dict[str, bytes] = {}
    auth_headers: list[str] = []
    fail_all = False

    def log_message(self, *a):  # quiet
        pass

    def do_PUT(self):
        if self.fail_all:
            self.send_response(500); self.end_headers(); return
        self.auth_headers.append(self.headers.get("Authorization", ""))
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.store[self.path] = body
        self.send_response(200); self.end_headers()

    def do_GET(self):
        if "list-type=2" in (self.path.split("?", 1) + [""])[1]:
            keys = "".join(f"<Contents><Key>{k.split('/', 2)[2]}</Key></Contents>"
                           for k in sorted(self.store))
            xml = f'<?xml version="1.0"?><ListBucketResult>{keys}<IsTruncated>false</IsTruncated></ListBucketResult>'
            self.send_response(200); self.end_headers(); self.wfile.write(xml.encode()); return
        body = self.store.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(body)


def _serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubS3)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _env(port: int) -> dict[str, str]:
    return {
        "CORTEX_TELEMETRY_S3_ENDPOINT": f"http://127.0.0.1:{port}",
        "CORTEX_TELEMETRY_S3_BUCKET": "cortex-telemetry",
        "CORTEX_TELEMETRY_S3_KEY_ID": "testkey",
        "CORTEX_TELEMETRY_S3_SECRET": "testsecret",
        "CORTEX_TELEMETRY_PREFIX": "plane-a",
    }


def test_opt_in_off_is_a_true_noop(tmp_path):
    f = tmp_path / "c.json"; f.write_text("{}", encoding="utf-8")
    assert telemetry.enabled({}) is False
    assert telemetry.mirror_file(f, env={}) is False  # no env -> no network, no raise


def test_mirror_puts_sigv4_signed_object_under_prefix(tmp_path):
    srv = _serve(); _StubS3.store.clear(); _StubS3.auth_headers.clear(); _StubS3.fail_all = False
    f = tmp_path / "cortex-closeout__x.json"; f.write_text('{"task":"t"}', encoding="utf-8")
    assert telemetry.mirror_file(f, env=_env(srv.server_address[1])) is True
    (key, body), = _StubS3.store.items()
    assert key == "/cortex-telemetry/plane-a/cortex-closeout__x.json"
    assert body == b'{"task":"t"}'
    assert _StubS3.auth_headers[0].startswith("AWS4-HMAC-SHA256")


def test_mirror_fails_open_on_server_error_and_on_unreachable(tmp_path):
    srv = _serve(); _StubS3.fail_all = True
    f = tmp_path / "c.json"; f.write_text("{}", encoding="utf-8")
    assert telemetry.mirror_file(f, env=_env(srv.server_address[1])) is False  # 500 -> False, no raise
    dead = _env(1)  # nothing listens on port 1
    dead["CORTEX_TELEMETRY_S3_ENDPOINT"] = "http://127.0.0.1:1"
    assert telemetry.mirror_file(f, env=dead) is False  # unreachable -> False, no raise
    _StubS3.fail_all = False


def test_import_round_trip_lands_in_remote_shard_and_is_idempotent(tmp_path):
    srv = _serve(); _StubS3.store.clear(); _StubS3.fail_all = False
    env = _env(srv.server_address[1])
    a = tmp_path / "cortex-closeout__a.json"; a.write_text('{"task":"a"}', encoding="utf-8")
    b = tmp_path / "cortex-closeout__b.md"; b.write_text("task: b", encoding="utf-8")
    assert telemetry.mirror_file(a, env=env) and telemetry.mirror_file(b, env=env)

    ws = tmp_path / "ws"; (ws / "audit").mkdir(parents=True)
    n = telemetry.import_remote(ws, env=env)
    assert n == 2
    dest = ws / "audit" / "audit-log-remote" / "agent"
    assert (dest / "cortex-closeout__a.json").read_text(encoding="utf-8") == '{"task":"a"}'
    assert (dest / "cortex-closeout__b.md").exists()
    assert telemetry.import_remote(ws, env=env) == 0  # idempotent: nothing new


def test_session_records_mirror_and_pull_round_trip(tmp_path, monkeypatch):
    # the rich-signal path: MCP event log -> session records -> R2 -> import_records back
    srv = _serve(); _StubS3.store.clear(); _StubS3.fail_all = False
    telemetry._MIRRORED.clear()
    env = _env(srv.server_address[1])
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    (tmp_path / "audit").mkdir()
    logs = tmp_path / "logs"; logs.mkdir()
    import json as _j
    evs = [{"ts": "2026-07-06T10:00:00+00:00", "session_id": "s1", "agent_id": "a", "tool": "cortex_search"},
           {"ts": "2026-07-06T10:01:00+00:00", "session_id": "s1", "agent_id": "a", "tool": "cortex_fetch_doc"},
           {"ts": "2026-07-06T10:02:00+00:00", "session_id": "s2", "agent_id": "b", "tool": "cortex_fetch_doc"}]
    with (logs / "mcp-events.jsonl").open("w", encoding="utf-8") as fh:
        for e in evs:
            fh.write(_j.dumps(e) + "\n")
    n = telemetry.mirror_session_records(tmp_path, env=env)
    assert n == 2  # two sessions mirrored
    assert telemetry.mirror_session_records(tmp_path, env=env) == 0  # unchanged -> skipped (dedupe)
    assert all(k.startswith("/cortex-telemetry/records/plane-a/") for k in _StubS3.store)
    pulled = {r["session_id"]: r for r in telemetry.import_records(env=env)}
    assert set(pulled) == {"s1", "s2"}
    assert pulled["s2"]["brain_first"] is False  # rich signal survived the round-trip


def test_write_closeout_mirrors_when_enabled_and_never_breaks_when_sink_dies(tmp_path, monkeypatch):
    from cortex_core.audit import write_closeout
    srv = _serve(); _StubS3.store.clear(); _StubS3.fail_all = False
    for k, v in _env(srv.server_address[1]).items():
        monkeypatch.setenv(k, v)
    ws = tmp_path / "ws"; (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    path = write_closeout(workspace=ws, task="mirror me", result="ok", tests="passed")
    assert path.exists()
    assert any(k.endswith(".json") for k in _StubS3.store), "closeout json not mirrored"
    # sink dies -> closeout must still be written locally (fail-open)
    _StubS3.fail_all = True
    path2 = write_closeout(workspace=ws, task="sink down", result="ok", tests="passed")
    assert path2.exists()
    _StubS3.fail_all = False
