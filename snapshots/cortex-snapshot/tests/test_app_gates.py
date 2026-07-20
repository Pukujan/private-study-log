"""Tests for cortex_core.app_gates — the deterministic behavioral gate (§2.3 items 7-30).

Every process-launching test writes a tiny INLINE app.py (stdlib http.server + sqlite3)
into tmp_path and drives it through run_done_checks. No M2 fixtures (unit independence),
no network beyond localhost, no LLM.
"""
from __future__ import annotations

import ast
import json
import re
import socket
import sys
import time
from pathlib import Path

import pytest

from cortex_core import app_gates
from cortex_core.app_gates import (
    GateContext,
    coach_view,
    load_hidden_checks,
    resolve_hidden,
    run_done_checks,
)

import random

# --------------------------------------------------------------------------- #
# Inline app sources (App-Contract compliant: --port --db, binds 127.0.0.1,    #
# prints CORTEX_APP_READY, sqlite file-backed)                                 #
# --------------------------------------------------------------------------- #
MINIMAL_APP = r'''
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = b"ok"
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers()
        self.wfile.write(b)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    srv = HTTPServer(("127.0.0.1", a.port), H)
    print("CORTEX_APP_READY", flush=True)
    srv.serve_forever()
main()
'''

GOOD_APP = r'''
import argparse, html, sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

ADMIN_TOKEN = "admintok_fixed_123"
DB = None

def init_db(path):
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, paid INTEGER DEFAULT 0, status TEXT DEFAULT 'late')")
    conn.commit(); conn.close()

def render(path_db):
    conn = sqlite3.connect(path_db, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    rows = conn.execute("SELECT name, paid, status FROM clients ORDER BY id").fetchall()
    conn.close()
    parts = ["<html><body><table>"]
    for name, paid, status in rows:
        cls = "late" if status == "late" else ""
        parts.append('<tr class="%s"><td>%s</td><td>%s</td></tr>' % (cls, html.escape(str(name)), html.escape(str(paid))))
    parts.append("</table></body></html>")
    return "".join(parts)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body=""):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/clients"):
            self._send(200, render(DB))
        else:
            self._send(404, "not found")
    def do_POST(self):
        p = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if p == "/admin/reset":
            if self.headers.get("X-Admin-Token") == ADMIN_TOKEN:
                conn = sqlite3.connect(DB, timeout=5.0); conn.execute("DELETE FROM clients"); conn.commit(); conn.close()
                self._send(200, "reset")
            else:
                self._send(403, "forbidden")
            return
        if p == "/clients":
            try:
                form = parse_qs(raw.decode("utf-8", "replace"))
            except Exception:
                self._send(400, "bad body"); return
            name = (form.get("name") or [""])[0]
            paid = (form.get("paid") or ["0"])[0]
            if not name:
                self._send(400, "name required"); return
            if len(name) > 200:
                self._send(400, "name too long"); return
            if paid not in ("0", "1"):
                self._send(400, "bad paid"); return
            status = "paid" if paid == "1" else "late"
            try:
                conn = sqlite3.connect(DB, timeout=5.0)
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("INSERT OR IGNORE INTO clients (name, paid, status) VALUES (?,?,?)", (name, int(paid), status))
                conn.commit(); conn.close()
            except Exception:
                self._send(400, "db error"); return
            self._send(200, "ok")
            return
        self._send(404, "not found")

def main():
    global DB
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    DB = args.db
    init_db(DB)
    srv = HTTPServer(("127.0.0.1", args.port), H)
    print("CORTEX_APP_READY", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
'''

MEMORY_APP = r'''
import argparse, sqlite3, html
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs
CONN = sqlite3.connect(":memory:", check_same_thread=False)
CONN.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")
CONN.commit()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, c, b=""):
        bb = b.encode("utf-8"); self.send_response(c); self.send_header("Content-Length", str(len(bb))); self.end_headers(); self.wfile.write(bb)
    def do_GET(self):
        rows = CONN.execute("SELECT name FROM clients").fetchall()
        self._s(200, "<html><table>" + "".join("<tr><td>%s</td></tr>" % html.escape(str(r[0])) for r in rows) + "</table></html>")
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n) if n else b""
        form = parse_qs(raw.decode("utf-8", "replace")); name = (form.get("name") or [""])[0]
        if name:
            CONN.execute("INSERT INTO clients (name) VALUES (?)", (name,)); CONN.commit()
        self._s(200, "ok")
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--db", required=True)
    a = ap.parse_args(); srv = HTTPServer(("127.0.0.1", a.port), H); print("CORTEX_APP_READY", flush=True); srv.serve_forever()
main()
'''

BUTTON_FAKE_APP = r'''
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, c, b=""):
        bb = b.encode("utf-8"); self.send_response(c); self.send_header("Content-Length", str(len(bb))); self.end_headers(); self.wfile.write(bb)
    def do_GET(self): self._s(200, "<html>static page, nothing here</html>")
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n: self.rfile.read(n)
        self._s(200, "ok")
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--db", required=True)
    a = ap.parse_args(); srv = HTTPServer(("127.0.0.1", a.port), H); print("CORTEX_APP_READY", flush=True); srv.serve_forever()
main()
'''

CANARY_APP = r'''
import argparse, os
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        secret = os.environ.get("APP_SECRET_CANARY", "")
        body = ("<html>debug secret=%s</html>" % secret).encode("utf-8")
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self): self.do_GET()
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--db", required=True)
    a = ap.parse_args(); srv = HTTPServer(("127.0.0.1", a.port), H); print("CORTEX_APP_READY", flush=True); srv.serve_forever()
main()
'''

FILE_SERVE_APP = r'''
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, unquote
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        rel = unquote(urlparse(self.path).path).lstrip("/")
        try:
            with open(rel, "rb") as f: data = f.read(); code = 200
        except Exception:
            data = b"not found"; code = 404
        self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self): self.do_GET()
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--db", required=True)
    a = ap.parse_args(); srv = HTTPServer(("127.0.0.1", a.port), H); print("CORTEX_APP_READY", flush=True); srv.serve_forever()
main()
'''

UNICODE_APP = r'''
import argparse, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = b"ok"; self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b)
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--db", required=True)
    a = ap.parse_args()
    sys.stdout.buffer.write("héllo → ✓ CORTEX_APP_READY\n".encode("utf-8")); sys.stdout.flush()
    srv = HTTPServer(("127.0.0.1", a.port), H); srv.serve_forever()
main()
'''

CRASH_APP = 'import sys\nsys.stderr.write("boom crash detail\\n")\nsys.stderr.flush()\nsys.exit(2)\n'
HANG_APP = 'import time\ntime.sleep(600)\n'


def _variant(base, old, new):
    out = base.replace(old, new)
    assert out != base, f"variant substitution was a no-op: {old!r}"
    return out


ALWAYS_LATE_APP = _variant(GOOD_APP, 'cls = "late" if status == "late" else ""', 'cls = "late"')
NO_CLASS_APP = _variant(GOOD_APP, 'cls = "late" if status == "late" else ""', 'cls = ""')
NO_ESCAPE_APP = _variant(GOOD_APP, 'html.escape(str(name))', 'str(name)')
UNPROTECTED_APP = _variant(GOOD_APP, 'self.headers.get("X-Admin-Token") == ADMIN_TOKEN', 'True')
FIVEHUNDRED_EMPTY_APP = _variant(GOOD_APP, 'self._send(400, "name required")', 'self._send(500, "name required")')
WRONG_TABLE_APP = (GOOD_APP.replace("EXISTS clients (", "EXISTS clientz (")
                   .replace("FROM clients", "FROM clientz").replace("INTO clients", "INTO clientz"))
MISSING_COLUMN_APP = _variant(
    GOOD_APP,
    "clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, paid INTEGER DEFAULT 0, status TEXT DEFAULT 'late')",
    "clients (id INTEGER PRIMARY KEY AUTOINCREMENT, paid INTEGER DEFAULT 0, status TEXT DEFAULT 'late')",
)


def _write_app(tmp_path: Path, source: str) -> Path:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.py").write_text(source, encoding="utf-8")
    return app_dir


def _fast_ctx(**kw) -> GateContext:
    kw.setdefault("seed", 1234)
    kw.setdefault("start_timeout_s", 8.0)
    return GateContext(**kw)


# --------------------------------------------------------------------------- #
# Check-spec builders                                                         #
# --------------------------------------------------------------------------- #
def _buttons_spec(token="btn_token"):
    ph = "@hidden:" + token
    return {"kind": "buttons_work", "actions": [
        {"name": "add_client",
         "request": {"method": "POST", "path": "/clients", "form": {"name": ph}},
         "expect": {"status_lt": 400},
         "state_change": {"get_path": "/clients", "must_contain": ph,
                          "before_must_not_contain": ph}}]}


def _persist_spec():
    return {"kind": "data_persists",
            "resource": {"create": {"method": "POST", "path": "/clients",
                                    "form": {"name": "@hidden:persist_token"}},
                         "read_path": "/clients", "table": "clients", "column": "name"}}


def _logic_spec():
    return {"kind": "logic_works",
            "db_setup": {"table": "clients",
                         "rows": [{"name": "@hidden:late_name", "status": "late"},
                                  {"name": "@hidden:paid_name", "status": "paid"}]},
            "cases": [{"get_path": "/", "row_containing": "@hidden:late_name", "has_class": "late"},
                      {"get_path": "/", "row_containing": "@hidden:paid_name", "not_has_class": "late"}]}


def _schema_spec():
    return {"kind": "schema_real", "db_file": "app.db", "table": "clients",
            "required_columns": ["id", "name"],
            "row_delta": {"via": {"method": "POST", "path": "/clients",
                                  "form": {"name": "@hidden:delta_token"}}, "expect_delta": 1}}


def _input_spec():
    return {"kind": "input_handling",
            "target": {"method": "POST", "path": "/clients", "fields": {"name": "text", "paid": "bool"}},
            "variants": ["empty_required", "type_invalid", "oversized", "duplicate", "malformed_body"],
            "policy": {"max_field_bytes": 4096},
            "safe": {"status_lt": 500, "process_alive": True, "canary_row_survives": True}}


# =========================================================================== #
# 7 — import firewall (THE no-LLM proof)                                       #
# =========================================================================== #
def test_gate_module_imports_are_stdlib_only():
    src_path = Path(app_gates.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    allowed_from = {"app_contract", "cortex_core.app_contract"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in stdlib:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                if node.module != "app_contract":
                    offenders.append(f".{node.module}")
            else:
                if node.module in allowed_from:
                    continue
                top = (node.module or "").split(".")[0]
                if top not in stdlib:
                    offenders.append(node.module)
    assert offenders == [], f"non-stdlib / non-app_contract imports: {offenders}"


# 8 — resolve_hidden determinism + non-mutation
def test_resolve_hidden_same_name_same_token_and_seed_reproducible():
    spec = {"a": "@hidden:tok", "b": ["@hidden:tok", "@hidden:other"], "c": "plain"}
    v1, v2 = {}, {}
    r1 = resolve_hidden(spec, random.Random(7), v1)
    r2 = resolve_hidden(spec, random.Random(7), v2)
    assert v1 == v2 and v1  # identical vaults across equal seeds
    assert r1["a"] == r1["b"][0]  # same name -> same token
    assert r1["a"] != r1["b"][1]
    for tok in v1.values():
        assert re.fullmatch(r"cx[0-9a-f]{16}", tok)
    assert spec["a"] == "@hidden:tok"  # original not mutated
    assert r1["c"] == "plain"


# 9 — app_starts passes on a minimal app
def test_app_starts_passes_on_minimal_app(tmp_path):
    app_dir = _write_app(tmp_path, MINIMAL_APP)
    v = run_done_checks(app_dir, [{"kind": "app_starts"}], ctx=_fast_ctx())
    assert v.passed
    assert v.failure_class is None
    assert len(v.results) == 1 and v.results[0].kind == "app_starts"


# 10 — crash before ready is START_FAIL, stderr tail captured
def test_app_starts_crash_is_start_fail(tmp_path):
    app_dir = _write_app(tmp_path, CRASH_APP)
    v = run_done_checks(app_dir, [{"kind": "app_starts"}], ctx=_fast_ctx())
    assert not v.passed
    assert v.failure_class == "START_FAIL"
    assert "boom crash detail" in v.results[0].detail


# 11 — hang is START_FAIL within budget, no orphan
def test_app_starts_hang_is_start_fail_and_no_orphan(tmp_path):
    app_dir = _write_app(tmp_path, HANG_APP)
    t0 = time.time()
    v = run_done_checks(app_dir, [{"kind": "app_starts"}],
                        ctx=GateContext(seed=1, start_timeout_s=2.0))
    elapsed = time.time() - t0
    assert not v.passed
    assert v.failure_class == "START_FAIL"
    assert elapsed < 20.0
    assert "killed=True" in v.results[0].detail


# 12 — ENV_FAIL retries once on a fresh port
def test_env_fail_retries_once_on_fresh_port(tmp_path, monkeypatch):
    app_dir = _write_app(tmp_path, MINIMAL_APP)
    occ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occ.bind(("127.0.0.1", 0)); occ.listen()
    bad_port = occ.getsockname()[1]
    calls = {"n": 0}

    def fake_alloc(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return bad_port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
        return p

    monkeypatch.setattr(app_gates, "_alloc_port", fake_alloc)
    try:
        v = run_done_checks(app_dir, [{"kind": "app_starts"}], ctx=GateContext(seed=1))
        assert v.passed
        assert v.env_retries == 1
    finally:
        occ.close()


# 13 — unknown kind is ENV_FAIL, never a pass
def test_unknown_check_kind_is_env_fail_never_pass(tmp_path):
    app_dir = _write_app(tmp_path, MINIMAL_APP)
    v = run_done_checks(app_dir, [{"kind": "nonsense"}], ctx=_fast_ctx())
    assert v.passed is False
    assert v.failure_class == "ENV_FAIL"


# 14 — buttons_work requires a VISIBLE state change
def test_buttons_work_requires_visible_state_change(tmp_path):
    app_dir = _write_app(tmp_path, BUTTON_FAKE_APP)
    v = run_done_checks(app_dir, [_buttons_spec()], ctx=_fast_ctx())
    assert not v.passed
    assert v.failure_class == "BUTTON_FAIL"


# 15 — buttons_work passes on real CRUD, hidden token round-trips
def test_buttons_work_passes_on_real_crud(tmp_path):
    app_dir = _write_app(tmp_path, GOOD_APP)
    v = run_done_checks(app_dir, [_buttons_spec()], ctx=_fast_ctx())
    assert v.passed, [r.detail for r in v.results]
    assert v.failure_class is None


# 16 — data_persists kills the process and the row survives
def test_data_persists_kills_process_and_survives(tmp_path):
    app_dir = _write_app(tmp_path, GOOD_APP)
    v = run_done_checks(app_dir, [_persist_spec()], ctx=_fast_ctx())
    assert v.passed, [r.detail for r in v.results]
    detail = [r.detail for r in v.results if r.kind == "data_persists"][0]
    m = re.search(r"pid_a=(\d+) pid_b=(\d+)", detail)
    assert m and m.group(1) != m.group(2)  # two distinct PIDs
    assert "sql_ok=True" in detail  # gate re-opened the sqlite file and found the row


# 17 — in-memory db fails persistence
def test_data_persists_fails_in_memory_db(tmp_path):
    app_dir = _write_app(tmp_path, MEMORY_APP)
    v = run_done_checks(app_dir, [_persist_spec()], ctx=_fast_ctx())
    assert not v.passed
    assert v.failure_class == "PERSISTENCE_FAIL"


# 18 — hidden token absent from specs and coach_view
def test_data_persists_hidden_token_absent_from_specs_and_coach_view(tmp_path):
    app_dir = _write_app(tmp_path, GOOD_APP)
    checks = [_persist_spec()]
    v = run_done_checks(app_dir, checks, ctx=_fast_ctx())
    detail = [r.detail for r in v.results if r.kind == "data_persists"][0]
    tok = re.search(r"token=(cx[0-9a-f]{16})", detail).group(1)
    assert tok not in json.dumps(checks)
    assert tok not in json.dumps(coach_view(v))


# 19 — schema_real checks columns + row delta
def test_schema_real_checks_columns_and_row_delta(tmp_path):
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [_schema_spec()], ctx=_fast_ctx()).passed

    wrong = _write_app(tmp_path / "w", WRONG_TABLE_APP)
    vw = run_done_checks(wrong, [_schema_spec()], ctx=_fast_ctx())
    assert not vw.passed and vw.failure_class == "SCHEMA_FAIL"

    miss = _write_app(tmp_path / "m", MISSING_COLUMN_APP)
    vm = run_done_checks(miss, [_schema_spec()], ctx=_fast_ctx())
    assert not vm.passed and vm.failure_class == "SCHEMA_FAIL"


# 20 — logic positive AND negative
def test_logic_works_positive_and_negative(tmp_path):
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [_logic_spec()], ctx=_fast_ctx()).passed, "good logic should pass"

    always = _write_app(tmp_path / "a", ALWAYS_LATE_APP)
    va = run_done_checks(always, [_logic_spec()], ctx=_fast_ctx())
    assert not va.passed and va.failure_class == "LOGIC_FAIL"  # negative case catches it

    none = _write_app(tmp_path / "n", NO_CLASS_APP)
    vn = run_done_checks(none, [_logic_spec()], ctx=_fast_ctx())
    assert not vn.passed and vn.failure_class == "LOGIC_FAIL"  # positive case catches it


# 21 — input handling: safe failure, no corruption
def test_input_handling_safe_failure_and_no_corruption(tmp_path):
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [_input_spec()], ctx=_fast_ctx()).passed, "good input handling should pass"

    bad = _write_app(tmp_path / "b", FIVEHUNDRED_EMPTY_APP)
    vb = run_done_checks(bad, [_input_spec()], ctx=_fast_ctx())
    assert not vb.passed and vb.failure_class == "INVALID_INPUT_FAIL"


# 22 — reflected XSS escape
def test_security_reflected_escape(tmp_path):
    spec = {"kind": "security_controls", "tests": ["reflected_escape"],
            "write": {"method": "POST", "path": "/clients", "field": "name"},
            "read_paths": ["/", "/clients"]}
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [spec], ctx=_fast_ctx()).passed

    bad = _write_app(tmp_path / "b", NO_ESCAPE_APP)
    vb = run_done_checks(bad, [spec], ctx=_fast_ctx())
    assert not vb.passed and vb.failure_class == "SECURITY_FAIL"


# 23 — secret canary never in responses/logs
def test_security_canary_never_in_responses_or_logs(tmp_path):
    spec = {"kind": "security_controls", "tests": ["canary_secret"], "read_paths": ["/", "/clients"]}
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [spec], ctx=_fast_ctx()).passed

    bad = _write_app(tmp_path / "b", CANARY_APP)
    vb = run_done_checks(bad, [spec], ctx=_fast_ctx())
    assert not vb.passed and vb.failure_class == "SECURITY_FAIL"


# 24 — protected route state unchanged
def test_security_protected_route_state_unchanged(tmp_path):
    spec = {"kind": "security_controls", "tests": [],
            "protected": [{"method": "POST", "path": "/admin/reset",
                           "expect_without_auth": {"status_in": [401, 403, 404],
                                                   "state_unchanged": True}}]}
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [spec], ctx=_fast_ctx()).passed

    bad = _write_app(tmp_path / "b", UNPROTECTED_APP)
    vb = run_done_checks(bad, [spec], ctx=_fast_ctx())
    assert not vb.passed and vb.failure_class == "SECURITY_FAIL"


# 25 — path traversal probe never served
def test_security_path_traversal_probe_never_served(tmp_path):
    spec = {"kind": "security_controls", "tests": ["path_traversal"]}
    good = _write_app(tmp_path / "g", GOOD_APP)
    assert run_done_checks(good, [spec], ctx=_fast_ctx()).passed

    bad = _write_app(tmp_path / "b", FILE_SERVE_APP)
    vb = run_done_checks(bad, [spec], ctx=_fast_ctx())
    assert not vb.passed and vb.failure_class == "SECURITY_FAIL"


# 26 — regression reruns ledger and names the violation
def test_regression_reruns_ledger_and_names_violation(tmp_path):
    app_dir = _write_app(tmp_path, GOOD_APP)
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    line0 = {"kind": "buttons_work", "actions": [
        {"name": "ok", "request": {"method": "POST", "path": "/clients", "form": {"name": "@hidden:reg0"}},
         "expect": {"status_lt": 400},
         "state_change": {"get_path": "/clients", "must_contain": "@hidden:reg0",
                          "before_must_not_contain": "@hidden:reg0"}}]}
    line1 = {"kind": "buttons_work", "actions": [
        {"name": "bad", "request": {"method": "POST", "path": "/clients", "form": {"name": "@hidden:reg1"}},
         "expect": {"status_lt": 400},
         "state_change": {"get_path": "/clients", "must_contain": "ZZZ_NEVER_APPEARS_MARKER",
                          "before_must_not_contain": "ZZZ_NEVER_APPEARS_MARKER"}}]}
    (ledger_dir / "gate_ledger.jsonl").write_text(
        json.dumps(line0) + "\n" + json.dumps(line1) + "\n", encoding="utf-8")
    spec = {"kind": "regression", "ledger_file": "gate_ledger.jsonl"}
    v = run_done_checks(app_dir, [spec], ledger_dir=ledger_dir, ctx=_fast_ctx())
    assert not v.passed
    assert v.failure_class == "REGRESSION_FAIL"
    detail = [r.detail for r in v.results if r.kind == "regression"][0]
    assert "line 1" in detail


# 27 — hidden holdout loading + flag
def test_hidden_holdout_loading_and_flag(tmp_path):
    app_dir = _write_app(tmp_path, GOOD_APP)
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    (holdout / "crud.jsonl").write_text(
        json.dumps(_buttons_spec("btn_hidden")) + "\n", encoding="utf-8")

    hidden = load_hidden_checks(holdout, "crud")
    assert len(hidden) == 1
    v = run_done_checks(app_dir, [_buttons_spec()], hidden_checks=hidden, ctx=_fast_ctx())
    assert v.passed
    assert v.hidden_coverage is True
    assert any(r.hidden for r in v.results)

    # absent dir -> no coverage, no error
    v2 = run_done_checks(app_dir, [_buttons_spec()],
                         hidden_checks=load_hidden_checks(tmp_path / "nope", "crud"),
                         ctx=_fast_ctx())
    assert v2.hidden_coverage is False


# 28 — seed reproducibility end-to-end
def test_seed_reproducibility_end_to_end(tmp_path):
    app_dir = _write_app(tmp_path, GOOD_APP)

    def _run(seed):
        v = run_done_checks(app_dir, [_persist_spec()], ctx=GateContext(seed=seed, start_timeout_s=8.0))
        detail = [r.detail for r in v.results if r.kind == "data_persists"][0]
        tok = re.search(r"token=(cx[0-9a-f]{16})", detail).group(1)
        vector = tuple((r.kind, r.passed) for r in v.results)
        return tok, vector

    tok_a, vec_a = _run(42)
    tok_b, vec_b = _run(42)
    tok_c, _vec_c = _run(43)
    assert tok_a == tok_b and vec_a == vec_b  # same seed reproduces payload + pass/fail
    assert tok_a != tok_c  # different seed -> different token


# 29 — Windows-safe unicode output
def test_gate_windows_safe_unicode_output(tmp_path):
    app_dir = _write_app(tmp_path, UNICODE_APP)
    v = run_done_checks(app_dir, [{"kind": "app_starts"}], ctx=_fast_ctx())
    assert v.passed
    # detail is a real str decoded with errors="replace" — no crash accessing it
    assert isinstance(v.results[0].detail, str)


# 30 — all checks run, first failure classifies
def test_all_checks_run_but_first_failure_classifies(tmp_path):
    app_dir = _write_app(tmp_path, MEMORY_APP)
    checks = [_logic_spec(), _persist_spec()]  # logic first in spec order
    v = run_done_checks(app_dir, checks, ctx=_fast_ctx())
    assert not v.passed
    kinds = {r.kind: r.passed for r in v.results}
    assert kinds.get("logic_works") is False
    assert kinds.get("data_persists") is False
    assert v.failure_class == "LOGIC_FAIL"  # first failing check in execution order
