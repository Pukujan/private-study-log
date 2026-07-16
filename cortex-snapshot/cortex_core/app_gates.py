"""cortex_core/app_gates.py — deterministic behavioral gate for built apps.
Verdicts come from subprocess + HTTP + sqlite observation ONLY. No LLM import, ever.
Follows evals/objective_coding/checker.py's subprocess/timeout/hidden-holdout discipline.
"""
from __future__ import annotations

import argparse
import http.client
import importlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import random  # noqa: E402  (kept explicit; stdlib)

from .app_contract import (CheckResult, GateVerdict, KIND_TO_CLASS,
                           HIDDEN_PREFIX, coach_view, validate_check_spec)  # noqa: F401

_HIDDEN_RE = re.compile(r"@hidden:([A-Za-z0-9_]+)")
_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>")
_CLASS_RE = re.compile(r"class\s*=\s*\"([^\"]*)\"")

# The admin token the gate provisions to the app under test via the APP_ADMIN_TOKEN env var. Apps
# read their token from the environment (no hardcoded secret in shipped source — CWE-798); the gate
# sets this known value so the `auth_required` check can exercise the correct-token path. Any
# `auth_required` check's `auth_value` must equal this.
GATE_ADMIN_TOKEN = "cortex-admin-token"


@dataclass
class GateContext:
    port: int | None = None            # None -> ephemeral (socket bind to 0)
    seed: int | None = None            # None -> int.from_bytes(os.urandom(4)); recorded
    start_timeout_s: float = 20.0      # ready-line/port-poll budget
    http_timeout_s: float = 10.0
    check_timeout_s: float = 60.0      # per-check wall clock
    restart_delay_s: float = 0.2       # jittered +/- by seeded RNG for data_persists
    python_exe: str = sys.executable
    max_env_retries: int = 1


class AppStartError(RuntimeError):
    ...


class GateEnvError(RuntimeError):   # gate-side problem -> ENV_FAIL path
    ...


@dataclass
class HttpResult:
    status: int
    body: str          # decoded utf-8/replace
    headers: dict[str, str]


# --------------------------------------------------------------------------- #
# Port helpers (module-level so tests can monkeypatch _alloc_port)             #
# --------------------------------------------------------------------------- #
def _alloc_port(ctx: GateContext) -> int:
    """Return a port to bind. Fixed ctx.port if set; else an OS-ephemeral one.
    Bind-to-0 then close: the tiny close->rebind window is what ENV_FAIL retry absorbs."""
    if ctx.port is not None:
        return int(ctx.port)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _port_available(port: int) -> bool:
    """True iff we can (transiently) bind 127.0.0.1:port right now."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Hidden-placeholder resolution                                               #
# --------------------------------------------------------------------------- #
def _mint_token(rng: "random.Random") -> str:
    return "cx" + format(rng.getrandbits(64), "016x")


def resolve_hidden(spec: Any, rng: "random.Random", vault: dict[str, str]) -> Any:
    """Deep-copy spec resolving '@hidden:<name>' -> vault-cached random token
    ('cx' + 16 hex chars). Same name -> same token within one gate run.
    The input structure is never mutated."""
    if isinstance(spec, dict):
        return {k: resolve_hidden(v, rng, vault) for k, v in spec.items()}
    if isinstance(spec, (list, tuple)):
        return [resolve_hidden(v, rng, vault) for v in spec]
    if isinstance(spec, str):
        if spec.startswith(HIDDEN_PREFIX) and _HIDDEN_RE.fullmatch(spec[len(HIDDEN_PREFIX):]) is None:
            # bare "@hidden:name" where name is a plain identifier -> exact token
            name = spec[len(HIDDEN_PREFIX):]
            if _is_ident(name):
                return _vault_token(name, rng, vault)
        if HIDDEN_PREFIX in spec:
            def _repl(m: "re.Match[str]") -> str:
                return _vault_token(m.group(1), rng, vault)
            return _HIDDEN_RE.sub(_repl, spec)
    return spec


def _is_ident(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]+", name))


def _vault_token(name: str, rng: "random.Random", vault: dict[str, str]) -> str:
    if name not in vault:
        vault[name] = _mint_token(rng)
    return vault[name]


def load_hidden_checks(holdout_dir: Path, family: str) -> list[dict]:
    """Read <holdout_dir>/<family>.jsonl (one check spec per line). Missing dir/file
    -> [] (verdict.hidden_coverage False). holdout_dir is gitignored by convention."""
    p = Path(holdout_dir) / f"{family}.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------- #
# App subprocess                                                              #
# --------------------------------------------------------------------------- #
class AppProcess:
    """Context manager owning ONE app subprocess. Windows-safe kill."""

    def __init__(self, app_dir: Path, port: int, db_path: Path,
                 ctx: GateContext, env_extra: dict[str, str]) -> None:
        self.app_dir = Path(app_dir)
        self.port = int(port)
        self.db_path = Path(db_path)
        self.ctx = ctx
        self.env_extra = dict(env_extra)
        self.proc: subprocess.Popen | None = None
        self._pid: int | None = None
        self._out_path: Path | None = None
        self._err_path: Path | None = None
        self._out_f = None
        self._err_f = None

    def __enter__(self) -> "AppProcess":
        run_dir = self.db_path.parent
        run_dir.mkdir(parents=True, exist_ok=True)
        self._out_path = run_dir / f"stdout_{self.port}.txt"
        self._err_path = run_dir / f"stderr_{self.port}.txt"
        self._out_f = open(self._out_path, "w", encoding="utf-8", errors="replace")
        self._err_f = open(self._err_path, "w", encoding="utf-8", errors="replace")
        env = dict(os.environ)
        env.update(self.env_extra)
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [self.ctx.python_exe, "app.py", "--port", str(self.port), "--db", str(self.db_path)]
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.app_dir), stdout=self._out_f, stderr=self._err_f, env=env
            )
        except OSError as e:
            self._close_files()
            raise GateEnvError(f"failed to spawn app: {e}") from e
        self._pid = self.proc.pid
        deadline = time.time() + self.ctx.start_timeout_s
        while time.time() < deadline:
            rc = self.proc.poll()
            if rc is not None:
                self.kill()
                _, err = self.read_std_streams()
                raise AppStartError(f"app exited rc={rc} before ready; stderr_tail={err[-400:]}")
            if self._ready():
                return self
            time.sleep(0.1)
        self.kill()
        _, err = self.read_std_streams()
        raise AppStartError(
            f"app not ready within {self.ctx.start_timeout_s}s (killed=True); "
            f"stderr_tail={err[-400:]}"
        )

    def __exit__(self, *exc) -> None:
        self.kill()

    def _ready(self) -> bool:
        try:
            out, _ = self.read_std_streams()
            if "CORTEX_APP_READY" in out:
                return True
        except OSError:
            pass
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                return True
        except OSError:
            return False

    @property
    def pid(self) -> int:
        return int(self._pid) if self._pid is not None else -1

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 — kill must never raise
                pass
        self._close_files()

    def _close_files(self) -> None:
        for f in (self._out_f, self._err_f):
            try:
                if f is not None:
                    f.close()
            except Exception:  # noqa: BLE001
                pass
        self._out_f = None
        self._err_f = None

    def read_std_streams(self) -> tuple[str, str]:
        out = err = ""
        try:
            if self._out_path is not None and self._out_path.exists():
                out = self._out_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        try:
            if self._err_path is not None and self._err_path.exists():
                err = self._err_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return out, err

    def request(self, method: str, path: str, *, form: dict | None = None,
                raw_body: bytes | None = None, headers: dict | None = None) -> HttpResult:
        """HTTP against 127.0.0.1:port with a hard timeout. 4xx/5xx returned as
        HttpResult, never raised. Connection refused raises OSError (caller maps)."""
        hdrs = dict(headers or {})
        body: bytes | None = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif raw_body is not None:
            body = raw_body
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.ctx.http_timeout_s)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            return HttpResult(
                status=resp.status,
                body=raw.decode("utf-8", "replace"),
                headers={k: v for k, v in resp.getheaders()},
            )
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# Internal per-run context passed to check implementations                    #
# --------------------------------------------------------------------------- #
@dataclass
class _RunCtx:
    app_dir: Path
    run_dir: Path
    db_path: Path
    canary: str
    env_extra: dict[str, str]
    probe_path: Path
    probe_secret: str
    ctx: GateContext
    rng: "random.Random"
    vault: dict[str, str]
    ledger_dir: Path | None


# --------------------------------------------------------------------------- #
# sqlite helpers                                                              #
# --------------------------------------------------------------------------- #
def _open_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _open_rw(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path), timeout=5.0)


def _count(db_path: Path, table: str) -> int:
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error:
        return -1
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return -1
    finally:
        conn.close()


def _table_from_path(path: str) -> str:
    seg = path.strip("/").split("/")[0]
    return seg or "clients"


def _classes_for_needle(html_text: str, needle: str) -> set[str] | None:
    """The class set of the innermost enclosing element (walking outward until one has
    a class) that contains `needle`. None -> needle absent from the document.
    Scaffold-coupled: our templates render one <tr class="..."> per row."""
    idx = html_text.find(needle)
    if idx < 0:
        return None
    stack: list[tuple[str, str]] = []
    for m in _TAG_RE.finditer(html_text[:idx]):
        closing, tag, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    del stack[i:]
                    break
        elif selfclose:
            continue
        else:
            stack.append((tag, attrs))
    for _tag, attrs in reversed(stack):
        cm = _CLASS_RE.search(attrs)
        if cm:
            return set(cm.group(1).split())
    return set()


# --------------------------------------------------------------------------- #
# Check implementations: fn(proc, spec, rc) -> (passed: bool, detail: str)     #
# --------------------------------------------------------------------------- #
def _check_buttons_work(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    for i, action in enumerate(spec.get("actions", [])):
        req = action["request"]
        sc = action.get("state_change", {})
        getp = sc.get("get_path")
        before = proc.request("GET", getp).body if getp else ""
        bmn = sc.get("before_must_not_contain")
        if bmn and bmn in before:
            return False, f"action[{i}] token already present before action (not a real change)"
        r = proc.request(req["method"], req["path"], form=req.get("form"), headers=req.get("headers"))
        exp = action.get("expect", {})
        if "status_lt" in exp and not (r.status < exp["status_lt"]):
            return False, f"action[{i}] status {r.status} !< {exp['status_lt']}"
        after = proc.request("GET", getp).body if getp else ""
        mc = sc.get("must_contain")
        if mc and mc not in after:
            return False, f"action[{i}] NO visible state change: expected token absent from read path"
    return True, "every action produced the asserted visible state change"


def _check_logic_works(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    setup = spec.get("db_setup")
    if setup:
        table = setup["table"]
        conn = _open_rw(rc.db_path)
        try:
            for row in setup.get("rows", []):
                cols = list(row.keys())
                placeholders = ",".join("?" * len(cols))
                conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
            conn.commit()
        finally:
            conn.close()
    for i, case in enumerate(spec.get("cases", [])):
        body = proc.request("GET", case["get_path"]).body
        needle = case["row_containing"]
        classes = _classes_for_needle(body, needle)
        present = classes if classes is not None else set()
        if "has_class" in case and case["has_class"] not in present:
            return False, f"case[{i}] expected class {case['has_class']!r} on row {needle!r} (got {sorted(present)})"
        if "not_has_class" in case and case["not_has_class"] in present:
            return False, f"case[{i}] unexpected class {case['not_has_class']!r} on row {needle!r}"
    return True, "logic positive AND negative cases satisfied"


def _persist_token(create: dict) -> str:
    form = create.get("form", {}) or {}
    for v in form.values():
        if isinstance(v, str) and re.fullmatch(r"cx[0-9a-f]{16}", v):
            return v
    for v in form.values():
        if isinstance(v, str) and v:
            return v
    return ""


def _check_data_persists(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    res = spec["resource"]
    create = res["create"]
    read_path = res["read_path"]
    table = res["table"]
    column = res["column"]
    token = _persist_token(create)
    persist_db = rc.run_dir / "persist.db"

    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"data_persists port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, persist_db, rc.ctx, rc.env_extra) as a:
        pid_a = a.pid
        try:
            a.request(create["method"], create["path"], form=create.get("form"))
        except OSError as e:
            return False, f"create request failed on first process: {e}"
    # a is now dead (context-manager kill)
    _jitter_sleep(rc)

    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"data_persists restart port {port_b} unavailable")
    http_body = ""
    with AppProcess(rc.app_dir, port_b, persist_db, rc.ctx, rc.env_extra) as b:
        pid_b = b.pid
        try:
            http_body = b.request("GET", read_path).body
        except OSError:
            http_body = ""
    http_ok = bool(token) and token in http_body

    sql_ok = False
    try:
        conn = _open_ro(persist_db)
        try:
            rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
            sql_ok = any(token == str(r[0]) or token in str(r[0]) for r in rows)
        finally:
            conn.close()
    except sqlite3.Error:
        sql_ok = False

    detail = (f"pid_a={pid_a} pid_b={pid_b} token={token} "
              f"http_ok={http_ok} sql_ok={sql_ok}")
    if pid_a == pid_b:
        return False, "process was NOT restarted (identical PID): " + detail
    if http_ok and sql_ok:
        return True, "row survived kill+restart via HTTP AND sqlite file: " + detail
    return False, "row missing after restart: " + detail


def _row_id_for_token(db_path: Path, table: str, column: str, token: str) -> int | None:
    """The id of the seeded canary row (the one whose text column carries `token`), read straight
    from the file-backed sqlite -- so the delete check keys off ground truth, not the HTML view."""
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            f"SELECT id FROM {table} WHERE {column} = ? OR {column} LIKE ? ORDER BY id LIMIT 1",
            (token, f"%{token}%"),
        ).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _check_deletes_row(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """A per-row delete that (a) is GUARDED -- an unconfirmed delete is rejected and leaves the row
    intact -- and (b) truly removes the row and STAYS removed across a kill+restart (a runtime-only
    hide that leaves the sqlite row behind reappears on restart and fails). All three properties are
    un-fakeable: a delete that ignores the confirm param, a no-op delete, and a hide-don't-delete
    mutant each fail a distinct leg."""
    create = spec["create"]
    delete = spec["delete"]
    table = spec["table"]
    column = spec["column"]
    read_path = spec.get("read_path", "/")
    idp = delete.get("id_param", "id")
    cp = delete.get("confirm_param", "confirm")
    cv = delete.get("confirm_value", "yes")
    dpath = delete["path"]
    token = _persist_token(create)
    if not token:
        return False, "delete check: create form carries no identifying token"

    ddb = rc.run_dir / "delete.db"
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"deletes_row port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, ddb, rc.ctx, rc.env_extra) as a:
        try:
            a.request(create["method"], create["path"], form=create.get("form"))
        except OSError as e:
            return False, f"seed create failed: {e}"
        if token not in a.request("GET", read_path).body:
            return False, "seeded row not visible after create; cannot test delete"
        rid = _row_id_for_token(ddb, table, column, token)
        if rid is None:
            raise GateEnvError("deletes_row: seeded row id absent from sqlite")

        # (1) GUARD -- an unconfirmed delete must be rejected and MUST leave the row present.
        ru = a.request("POST", dpath, form={idp: str(rid)})
        if token not in a.request("GET", read_path).body:
            return False, (f"unconfirmed delete removed the row -- missing confirm guard "
                           f"(status={ru.status})")

        # (2) a CONFIRMED delete must remove the row from the live view.
        rcf = a.request("POST", dpath, form={idp: str(rid), cp: cv})
        if rcf.status >= 400:
            return False, f"confirmed delete rejected: status={rcf.status}"
        if token in a.request("GET", read_path).body:
            return False, "confirmed delete did NOT remove the row from the list"

    # (3) the deletion must PERSIST across kill+restart (not a runtime-only hide).
    _jitter_sleep(rc)
    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"deletes_row restart port {port_b} unavailable")
    with AppProcess(rc.app_dir, port_b, ddb, rc.ctx, rc.env_extra) as b:
        restarted = b.request("GET", read_path).body
    if token in restarted or _row_id_for_token(ddb, table, column, token) is not None:
        return False, "deleted row REAPPEARED after restart (deletion not persisted to sqlite)"
    return True, (f"unconfirmed delete blocked; confirmed delete removed row {rid} and it stayed "
                  f"gone across restart")


def _check_edits_row(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """A per-row edit that changes ONLY the targeted row and whose new value PERSISTS. Seeds the
    edited row plus a bystander row, edits the first, and asserts: the new value appears, the old
    value is gone, the BYSTANDER is untouched (an UPDATE-without-WHERE / wrong-row mutant flips the
    bystander), and the change survives a kill+restart (a runtime-only edit that never hit sqlite
    reverts). Each failure mode fails a distinct leg -- un-fakeable."""
    create = spec["create"]
    create_b = spec["create_b"]
    edit = spec["edit"]
    table = spec["table"]
    column = spec["column"]
    read_path = spec.get("read_path", "/")
    idp = edit.get("id_param", "id")
    epath = edit["path"]
    edit_form = edit["form"]
    token_old = _persist_token(create)
    token_b = _persist_token(create_b)
    token_new = edit_form.get(column)
    if not (token_old and token_b and token_new):
        return False, "edits_row: create / create_b / edit.form must each carry an identifying token"
    if len({token_old, token_b, token_new}) != 3:
        raise GateEnvError("edits_row: old/bystander/new tokens must be distinct")

    edb = rc.run_dir / "edit.db"
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"edits_row port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, edb, rc.ctx, rc.env_extra) as a:
        try:
            a.request(create["method"], create["path"], form=create.get("form"))
            a.request(create_b["method"], create_b["path"], form=create_b.get("form"))
        except OSError as e:
            return False, f"seed create failed: {e}"
        body0 = a.request("GET", read_path).body
        if token_old not in body0 or token_b not in body0:
            return False, "seeded rows not both visible after create; cannot test edit"
        rid = _row_id_for_token(edb, table, column, token_old)
        if rid is None:
            raise GateEnvError("edits_row: edited row id absent from sqlite")

        eform = dict(edit_form)
        eform[idp] = str(rid)
        re = a.request(edit.get("method", "POST"), epath, form=eform)
        if re.status >= 400:
            return False, f"edit rejected: status={re.status}"
        body1 = a.request("GET", read_path).body
        if token_new not in body1:
            return False, "edit did NOT apply the new value (no-op edit)"
        if token_old in body1:
            return False, "old value still present after edit (row not actually updated)"
        if token_b not in body1:
            return False, "bystander row changed by the edit (UPDATE hit the wrong / all rows)"

    _jitter_sleep(rc)
    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"edits_row restart port {port_b} unavailable")
    with AppProcess(rc.app_dir, port_b, edb, rc.ctx, rc.env_extra) as b:
        restarted = b.request("GET", read_path).body
    new_persisted = _row_id_for_token(edb, table, column, token_new) is not None
    if token_new not in restarted or token_old in restarted or not new_persisted:
        return False, "edited value did NOT persist across restart (runtime-only edit)"
    return True, (f"edit changed row {rid} to the new value, left the bystander untouched, and "
                  f"persisted across restart")


def _check_auth_required(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """A protected route enforces a real auth boundary: it serves the protected data ONLY with the
    correct token. Seeds a canary row via the open create endpoint, then probes the protected route
    three ways -- no header, correct header, WRONG header -- and asserts the canary is served ONLY
    under correct auth. Un-fakeable: a route that ignores auth leaks the canary with no/wrong token;
    a route that always 401s never serves it; a route that accepts any token leaks it on wrong-auth."""
    protected = spec["protected_path"]
    header = spec["auth_header"]
    value = spec["auth_value"]
    create = spec["create"]
    token = _persist_token(create)
    if not token:
        return False, "auth_required: create form carries no canary token"
    try:
        proc.request(create["method"], create["path"], form=create.get("form"))
    except OSError as e:
        return False, f"seed create failed: {e}"

    # (1) NO auth -> must reject and must NOT leak the protected data.
    r_no = proc.request("GET", protected)
    if r_no.status < 400:
        return False, f"protected route {protected!r} served WITHOUT auth (status {r_no.status})"
    if token in r_no.body:
        return False, f"protected data leaked to an UNAUTHENTICATED request at {protected!r}"

    # (2) CORRECT auth -> must accept and actually serve the protected data.
    r_ok = proc.request("GET", protected, headers={header: value})
    if r_ok.status >= 400:
        return False, f"correct auth was REJECTED at {protected!r} (status {r_ok.status})"
    if token not in r_ok.body:
        return False, f"authenticated request did not return the protected data at {protected!r}"

    # (3) WRONG auth -> must reject and must NOT leak.
    r_bad = proc.request("GET", protected, headers={header: value + "_wrong"})
    if r_bad.status < 400 or token in r_bad.body:
        return False, (f"a WRONG auth token was accepted at {protected!r} "
                       f"(status {r_bad.status}) -- the guard does not actually check the value")
    return True, (f"{protected!r} rejects no-auth AND wrong-auth (no data leak) and serves the "
                  f"protected data only under correct auth")


def _check_audit_trail(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """Every mutation is recorded to an APPEND-ONLY audit log. Seeds N distinct canary creates, then
    reads the audit table straight from sqlite and asserts (a) EACH canary appears in a log entry's
    detail (nothing went unlogged) and (b) the log holds >= N entries (append, not overwrite). A
    no-logging mutant leaves 0 entries; an overwrite-one mutant leaves 1; a log-without-detail mutant
    omits the canary — each fails a distinct leg."""
    create = spec["create"]
    audit_table = spec["audit_table"]
    detail_col = spec["detail_column"]
    adb = rc.run_dir / "audit.db"

    n = rc.rng.randint(2, 4)
    tokens: list[str] = []
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"audit_trail port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, adb, rc.ctx, rc.env_extra) as a:
        form_tmpl = create.get("form", {})
        for _ in range(n):
            form = dict(form_tmpl)
            tok = _mint_token(rc.rng)
            for k, v in form.items():
                if isinstance(v, str) and re.fullmatch(r"cx[0-9a-f]{16}", v):
                    form[k] = tok
            tokens.append(tok)
            try:
                st = a.request(create["method"], create["path"], form=form).status
            except OSError as e:
                return False, f"audit seed create failed: {e}"
            if st >= 400:
                raise GateEnvError(f"audit_trail seed create returned {st} (>=400)")

    # Read the append-only audit table from the file-backed sqlite (ground truth, not the HTML view).
    try:
        conn = _open_ro(adb)
    except sqlite3.Error as e:
        return False, f"audit table unreadable (no audit log written?): {e}"
    try:
        try:
            rows = conn.execute(f"SELECT {detail_col} FROM {audit_table}").fetchall()
        except sqlite3.Error as e:
            return False, f"audit table {audit_table!r}/{detail_col!r} missing — mutations were not logged: {e}"
    finally:
        conn.close()

    details = [str(r[0]) for r in rows]
    if len(rows) < n:
        return False, (f"audit log has {len(rows)} entries for {n} mutations — entries were "
                       f"overwritten, not appended (append-only violated)")
    missing = [t for t in tokens if not any(t in d for d in details)]
    if missing:
        return False, f"{len(missing)}/{n} mutations were NOT recorded in the audit log"
    return True, f"all {n} mutations recorded in an append-only audit log ({len(rows)} entries)"


def _check_dashboard_metrics(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """A /dashboard view whose TOTAL card and each per-field card equal the TRUE counts. Delta-based:
    seeding one row must move the total card by exactly 1; seeding n rows matching a card's predicate
    (and m that don't) must move THAT card by exactly n. A hardcoded card, a total-not-updating card,
    or a wrong-field card each fail. (Cards are verified independently — cross-card interference is
    fine because only the card under test is asserted.)"""
    get_path = spec["get_path"]
    create = spec["create"]
    total_attr = spec["total_attr"]
    total_form = spec["total_form"]
    cards = spec["cards"]

    def _read(attr: str):
        try:
            body = proc.request("GET", get_path).body
        except OSError as e:
            raise GateEnvError(f"dashboard read failed: {e}") from e
        return _metric_value(body, attr)

    # --- total card: seeding one valid row moves it by exactly 1 ---
    before = _read(total_attr)
    if before is None:
        return False, f"dashboard total card {total_attr!r} absent or non-integer at {get_path!r}"
    st = _seed_derived_row(proc, create, total_form, rc)
    if st >= 400:
        raise GateEnvError(f"dashboard total seed POST returned {st} (>=400)")
    after = _read(total_attr)
    if after is None or after - before != 1:
        return False, (f"dashboard total card moved {None if after is None else after - before} != 1 "
                       f"after one create (a hardcoded / stale total fails)")

    # --- each per-field card: delta == matching count ---
    for card in cards:
        attr = card["marker_attr"]
        preserve = card.get("predicate_field")
        b = _read(attr)
        if b is None:
            return False, f"dashboard card {attr!r} absent or non-integer"
        n_match = rc.rng.randint(2, 4)
        n_no = rc.rng.randint(1, 3)
        for i in range(n_match):
            s = _seed_derived_row(proc, create, card["match_form"], rc, preserve)
            if s >= 400:
                raise GateEnvError(f"dashboard card {attr!r} match seed {i} returned {s}")
        for i in range(n_no):
            s = _seed_derived_row(proc, create, card["nomatch_form"], rc, preserve)
            if s >= 400:
                raise GateEnvError(f"dashboard card {attr!r} nomatch seed {i} returned {s}")
        a = _read(attr)
        if a is None or a - b != n_match:
            return False, (f"dashboard card {attr!r} moved {None if a is None else a - b} != "
                           f"n_match {n_match} (hardcoded / wrong-field / total-not-filtered)")
    return True, f"dashboard total + {len(cards)} per-field card(s) all equal the true counts"


def _check_detail_view(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """GET /<entity>/<id> shows THAT record only. Seeds two canary rows A and B, requests A's detail
    page, and asserts A's token is present, B's token is ABSENT (it's not just re-rendering the list),
    and the machine-readable id marker equals A's id. A bogus id must 404. An 'ignore the id / show
    the list' mutant leaks B; a 'no 404' mutant serves a missing record."""
    create = spec["create"]
    table = spec["table"]
    column = spec["column"]
    prefix = spec["detail_path_prefix"].rstrip("/")
    id_attr = spec["id_marker_attr"]
    ddb = rc.run_dir / "detail.db"

    port = _alloc_port(rc.ctx)
    if not _port_available(port):
        raise GateEnvError(f"detail_view port {port} unavailable")
    with AppProcess(rc.app_dir, port, ddb, rc.ctx, rc.env_extra) as a:
        form_a = dict(create.get("form", {}))
        form_b = dict(create.get("form", {}))
        tok_a, tok_b = _mint_token(rc.rng), _mint_token(rc.rng)
        for f, tok in ((form_a, tok_a), (form_b, tok_b)):
            for k, v in f.items():
                if isinstance(v, str) and re.fullmatch(r"cx[0-9a-f]{16}", v):
                    f[k] = tok
        try:
            a.request(create["method"], create["path"], form=form_a)
            a.request(create["method"], create["path"], form=form_b)
        except OSError as e:
            return False, f"detail seed create failed: {e}"
        id_a = _row_id_for_token(ddb, table, column, tok_a)
        if id_a is None:
            raise GateEnvError("detail_view: seeded row A id absent from sqlite")

        r = proc_get(a, f"{prefix}/{id_a}")
        if r is None:
            return False, "detail page request dropped"
        if r.status >= 400:
            return False, f"detail page for a REAL id returned {r.status}"
        if tok_a not in r.body:
            return False, "detail page did not show the requested record"
        if tok_b in r.body:
            return False, "detail page leaked ANOTHER record (it re-renders the list, ignoring the id)"
        shown = _metric_value(r.body, id_attr)
        if shown != id_a:
            return False, f"detail id marker {id_attr!r} = {shown} != requested id {id_a}"

        rb = proc_get(a, f"{prefix}/999999")
        if rb is None or rb.status != 404:
            return False, (f"a bogus id did not 404 (got {None if rb is None else rb.status}) — a "
                           f"missing record must not be served")
    return True, f"detail page shows only record {id_a}; bogus id 404s"


def proc_get(proc: AppProcess, path: str):
    try:
        return proc.request("GET", path)
    except OSError:
        return None


def _check_relation_integrity(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """A child entity REQUIRES a valid parent foreign key. Seeds a parent (canary), then: (1) a child
    referencing that REAL parent is accepted; (2) the join view shows BOTH the child and its parent
    (the relation link); (3) a child referencing a BOGUS parent id is REJECTED. A no-FK-check mutant
    accepts the bogus child; a dropped-join mutant hides the parent; a broken child-insert hides the
    child -- each fails a distinct leg."""
    parent_create = spec["parent_create"]
    child_create = spec["child_create"]
    parent_table = spec["parent_table"]
    parent_column = spec["parent_column"]
    fk = spec["child_fk_param"]
    child_valid_form = spec["child_valid_form"]
    view_path = spec["child_view_path"]
    child_column = spec["child_column"]
    parent_tok = _persist_token(parent_create)
    child_tok = child_valid_form.get(child_column)
    if not (parent_tok and child_tok):
        return False, "relation check: parent_create / child_valid_form missing an identifying token"

    rdb = rc.run_dir / "relation.db"
    port = _alloc_port(rc.ctx)
    if not _port_available(port):
        raise GateEnvError(f"relation_integrity port {port} unavailable")
    with AppProcess(rc.app_dir, port, rdb, rc.ctx, rc.env_extra) as a:
        try:
            a.request(parent_create["method"], parent_create["path"], form=parent_create.get("form"))
        except OSError as e:
            return False, f"parent create failed: {e}"
        parent_id = _row_id_for_token(rdb, parent_table, parent_column, parent_tok)
        if parent_id is None:
            raise GateEnvError("relation_integrity: parent row id absent from sqlite")

        # (1) child with a REAL parent id -> accepted
        good = dict(child_valid_form)
        good[fk] = str(parent_id)
        rg = a.request(child_create["method"], child_create["path"], form=good)
        if rg.status >= 400:
            return False, f"child with a VALID parent id was rejected: status={rg.status}"

        # (2) join view shows the child AND its parent
        body = a.request("GET", view_path).body
        if child_tok not in body:
            return False, "child row not shown in the relation view"
        if parent_tok not in body:
            return False, "relation view does NOT show the parent (the join/link is missing)"

        # (3) child with a BOGUS parent id -> rejected (referential integrity)
        bad = dict(child_valid_form)
        bad[child_column] = _mint_token(rc.rng)   # distinct, so it can't collide with the good child
        bad[fk] = "999999"
        rb = a.request(child_create["method"], child_create["path"], form=bad)
        if rb.status < 400:
            return False, (f"a child referencing a NON-EXISTENT parent (id 999999) was ACCEPTED "
                           f"(status {rb.status}) -- no referential integrity")
    return True, "child requires a valid parent FK; join view shows the link; bogus parent rejected"


def _col_value(db_path: Path, table: str, column: str, rid: int) -> str | None:
    """The value of `column` for row id `rid`, read straight from the file-backed sqlite (ground
    truth, never the HTML). None if the row/column is unreadable."""
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (rid,)).fetchone()
        return None if row is None else str(row[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _row_exists(db_path: Path, table: str, rid: int) -> bool:
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error:
        return False
    try:
        return conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (rid,)).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _seed_minted(proc: AppProcess, create: dict, rc: _RunCtx) -> str:
    """POST one create form with the @hidden text token replaced by a FRESH minted token, so a
    single check can seed several DISTINCT rows. Returns the minted token (raises on a >=400)."""
    form = dict(create.get("form", {}) or {})
    tok = _mint_token(rc.rng)
    for k, v in list(form.items()):
        if isinstance(v, str) and re.fullmatch(r"cx[0-9a-f]{16}", v):
            form[k] = tok
    st = proc.request(create["method"], create["path"], form=form).status
    if st >= 400:
        raise GateEnvError(f"seed create returned {st} (>=400)")
    return tok


def _check_status_lifecycle(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """A status state machine that ONLY allows declared transitions. Seeds a row (status == the
    declared `initial`), then: (1) an ILLEGAL transition (to a state not reachable from initial) is
    REJECTED and leaves the status unchanged; (2) a LEGAL transition is accepted and the new status
    lands in sqlite; (3) the legal status PERSISTS across kill+restart. A no-op endpoint fails leg 2;
    an accept-anything endpoint fails leg 1; an in-memory-only endpoint fails leg 3 — each a distinct
    un-fakeable leg."""
    create = spec["create"]
    transition = spec["transition"]
    table = spec["table"]
    column = spec["column"]
    scol = spec["status_column"]
    initial = str(spec["initial"])
    legal_to = str(spec["legal_to"])
    illegal_to = str(spec["illegal_to"])
    idp = transition.get("id_param", "id")
    top = transition.get("to_param", "to")
    tpath = transition["path"]
    token = _persist_token(create)
    if not token:
        return False, "status_lifecycle: create form carries no identifying token"

    sdb = rc.run_dir / "status.db"
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"status_lifecycle port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, sdb, rc.ctx, rc.env_extra) as a:
        try:
            a.request(create["method"], create["path"], form=create.get("form"))
        except OSError as e:
            return False, f"seed create failed: {e}"
        rid = _row_id_for_token(sdb, table, column, token)
        if rid is None:
            raise GateEnvError("status_lifecycle: seeded row id absent from sqlite")
        if _col_value(sdb, table, column, rid) is None:
            raise GateEnvError("status_lifecycle: seeded row unreadable")
        if _col_value(sdb, table, scol, rid) != initial:
            return False, (f"seeded row status {_col_value(sdb, table, scol, rid)!r} != declared "
                           f"initial {initial!r} (status column not initialised)")

        # (1) ILLEGAL transition -> rejected AND status unchanged.
        ri = a.request("POST", tpath, form={idp: str(rid), top: illegal_to})
        if ri.status < 400:
            return False, (f"illegal transition {initial!r}->{illegal_to!r} was ACCEPTED "
                           f"(status {ri.status}) — the state machine does not enforce transitions")
        if _col_value(sdb, table, scol, rid) != initial:
            return False, f"illegal transition MUTATED the status to {_col_value(sdb, table, scol, rid)!r}"

        # (2) LEGAL transition -> accepted AND persisted to sqlite.
        rl = a.request("POST", tpath, form={idp: str(rid), top: legal_to})
        if rl.status >= 400:
            return False, f"legal transition {initial!r}->{legal_to!r} was REJECTED (status {rl.status})"
        if _col_value(sdb, table, scol, rid) != legal_to:
            return False, (f"legal transition did not update the status "
                           f"(got {_col_value(sdb, table, scol, rid)!r}, expected {legal_to!r})")

    # (3) the legal status must PERSIST across kill+restart.
    _jitter_sleep(rc)
    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"status_lifecycle restart port {port_b} unavailable")
    with AppProcess(rc.app_dir, port_b, sdb, rc.ctx, rc.env_extra):
        pass
    if _col_value(sdb, table, scol, rid) != legal_to:
        return False, "legal status did NOT persist across restart (in-memory-only transition)"
    return True, (f"illegal transition to {illegal_to!r} rejected; legal transition to {legal_to!r} "
                  f"applied and persisted across restart")


def _check_soft_delete(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """Archive HIDES a row from the active view but KEEPS it in sqlite (a soft delete, not a hard
    one), and restore un-hides it. Seeds a canary, then: (1) it's in the active view; (2) after
    archive it's GONE from active, PRESENT in the archived view, and the sqlite row STILL EXISTS with
    archived=1; (3) the archived state PERSISTS across restart; (4) restore returns it to active. A
    hard-delete mutant fails leg 2's 'row still in sqlite'; a no-op archive fails 'gone from active';
    a no-op restore fails leg 4 — each un-fakeable."""
    create = spec["create"]
    archive = spec["archive"]
    restore = spec["restore"]
    table = spec["table"]
    column = spec["column"]
    acol = spec["archived_column"]
    active_path = spec["active_view_path"]
    archived_path = spec["archived_view_path"]
    a_idp = archive.get("id_param", "id")
    r_idp = restore.get("id_param", "id")
    token = _persist_token(create)
    if not token:
        return False, "soft_delete: create form carries no identifying token"

    ddb = rc.run_dir / "softdel.db"
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"soft_delete port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, ddb, rc.ctx, rc.env_extra) as a:
        try:
            a.request(create["method"], create["path"], form=create.get("form"))
        except OSError as e:
            return False, f"seed create failed: {e}"
        rid = _row_id_for_token(ddb, table, column, token)
        if rid is None:
            raise GateEnvError("soft_delete: seeded row id absent from sqlite")
        if token not in a.request("GET", active_path).body:
            return False, "seeded row not in the active view before archive; cannot test soft-delete"

        # (2) archive -> gone from active, present in archived, STILL in sqlite (archived=1).
        rar = a.request("POST", archive["path"], form={a_idp: str(rid)})
        if rar.status >= 400:
            return False, f"archive rejected: status={rar.status}"
        if token in a.request("GET", active_path).body:
            return False, "archived row STILL appears in the active view (archive did not hide it)"
        if token not in a.request("GET", archived_path).body:
            return False, "archived row does NOT appear in the archived view"
        if not _row_exists(ddb, table, rid):
            return False, "archive DELETED the sqlite row (that is a hard delete, not a soft delete)"
        av = _col_value(ddb, table, acol, rid)
        if av not in ("1", "True", "true"):
            return False, f"archived flag not set in sqlite (archived_column={av!r})"

    # (3) archived state persists across restart.
    _jitter_sleep(rc)
    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"soft_delete restart port {port_b} unavailable")
    with AppProcess(rc.app_dir, port_b, ddb, rc.ctx, rc.env_extra) as b:
        if token in b.request("GET", active_path).body:
            return False, "archived row REAPPEARED in the active view after restart (archive not persisted)"
        if not _row_exists(ddb, table, rid):
            return False, "row vanished from sqlite after restart"
        # (4) restore -> back in the active view.
        rre = b.request("POST", restore["path"], form={r_idp: str(rid)})
        if rre.status >= 400:
            return False, f"restore rejected: status={rre.status}"
        if token not in b.request("GET", active_path).body:
            return False, "restore did NOT return the row to the active view"
    return True, (f"archive hid row {rid} from active (kept in sqlite, shown in archived), the state "
                  f"persisted across restart, and restore returned it to active")


def _check_assignment(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """Assignment sets an owner and a scoped view returns ONLY that owner's rows. Seeds two rows A,B,
    assigns A->owner1 and B->owner2, then: (1) owner1's scoped view contains A and NOT B; (2) the
    assignee is recorded in sqlite; (3) reassigning A->owner2 moves it (owner2's view now has BOTH,
    owner1's has neither) and persists in sqlite. A no-op assign fails leg 2; a scoped view that
    ignores the filter leaks B into owner1's view (leg 1); a no-op reassign fails leg 3."""
    create = spec["create"]
    assign = spec["assign"]
    scoped = spec["scoped_view"]
    table = spec["table"]
    column = spec["column"]
    ecol = spec["assignee_column"]
    a_idp = assign.get("id_param", "id")
    a_ap = assign.get("assignee_param", "assignee")
    get_path = scoped["get_path"]
    qp = scoped["query_param"]

    def _scoped_body(app: AppProcess, owner: str) -> str:
        sep = "&" if "?" in get_path else "?"
        return app.request("GET", get_path + sep + urllib.parse.urlencode({qp: owner})).body

    adb = rc.run_dir / "assign.db"
    owner1, owner2 = _mint_token(rc.rng), _mint_token(rc.rng)
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"assignment port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, adb, rc.ctx, rc.env_extra) as a:
        tok_a = _seed_minted(a, create, rc)
        tok_b = _seed_minted(a, create, rc)
        rid_a = _row_id_for_token(adb, table, column, tok_a)
        rid_b = _row_id_for_token(adb, table, column, tok_b)
        if rid_a is None or rid_b is None:
            raise GateEnvError("assignment: seeded row id(s) absent from sqlite")

        if a.request("POST", assign["path"], form={a_idp: str(rid_a), a_ap: owner1}).status >= 400:
            return False, "assign A->owner1 rejected"
        if a.request("POST", assign["path"], form={a_idp: str(rid_b), a_ap: owner2}).status >= 400:
            return False, "assign B->owner2 rejected"
        if _col_value(adb, table, ecol, rid_a) != owner1:
            return False, f"assignee not recorded in sqlite for A (got {_col_value(adb, table, ecol, rid_a)!r})"

        # (1) owner1's scoped view: A present, B absent.
        b1 = _scoped_body(a, owner1)
        if tok_a not in b1:
            return False, "owner1's scoped view is MISSING its own assigned row A"
        if tok_b in b1:
            return False, "owner1's scoped view LEAKED row B (the scope filter is ignored)"

        # (3) reassign A->owner2 -> moves it.
        if a.request("POST", assign["path"], form={a_idp: str(rid_a), a_ap: owner2}).status >= 400:
            return False, "reassign A->owner2 rejected"
        if _col_value(adb, table, ecol, rid_a) != owner2:
            return False, "reassign did not change A's assignee in sqlite"
        b2 = _scoped_body(a, owner2)
        if tok_a not in b2 or tok_b not in b2:
            return False, "after reassign, owner2's scoped view does not contain BOTH rows"
        b1b = _scoped_body(a, owner1)
        if tok_a in b1b:
            return False, "after reassign, owner1's scoped view still shows the moved row A"

    _jitter_sleep(rc)
    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"assignment restart port {port_b} unavailable")
    with AppProcess(rc.app_dir, port_b, adb, rc.ctx, rc.env_extra):
        pass
    if _col_value(adb, table, ecol, rid_a) != owner2:
        return False, "reassignment did NOT persist across restart"
    return True, "assign sets the owner, the scoped view is correctly filtered, reassign moves it, all persisted"


def _check_review_approval(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """Approve/reject records the deciding approver and is TERMINAL. Seeds two pending rows, then:
    (1) approving R1 sets status=approved AND records the approver in sqlite; (2) a SECOND decision on
    the already-decided R1 is REJECTED and changes nothing (terminal); (3) rejecting R2 sets
    status=rejected and records its approver; (4) the decision PERSISTS across restart. A no-op fails
    leg 1; an ignore-approver fails the approver-recorded leg; a non-terminal endpoint (allows a
    re-decision) fails leg 2 — each un-fakeable."""
    create = spec["create"]
    review = spec["review"]
    table = spec["table"]
    column = spec["column"]
    scol = spec["status_column"]
    apcol = spec["approver_column"]
    idp = review.get("id_param", "id")
    dp = review.get("decision_param", "decision")
    ap = review.get("approver_param", "approver")
    approve_val = review.get("approve_value", "approve")
    reject_val = review.get("reject_value", "reject")
    approved_state = review.get("approved_state", "approved")
    rejected_state = review.get("rejected_state", "rejected")
    rpath = review["path"]

    rdb = rc.run_dir / "review.db"
    name1, name2 = _mint_token(rc.rng), _mint_token(rc.rng)
    port_a = _alloc_port(rc.ctx)
    if not _port_available(port_a):
        raise GateEnvError(f"review_approval port {port_a} unavailable")
    with AppProcess(rc.app_dir, port_a, rdb, rc.ctx, rc.env_extra) as a:
        _seed_minted(a, create, rc)  # tok discarded; we key off ids
        tok1 = _seed_minted(a, create, rc)
        tok2 = _seed_minted(a, create, rc)
        rid1 = _row_id_for_token(rdb, table, column, tok1)
        rid2 = _row_id_for_token(rdb, table, column, tok2)
        if rid1 is None or rid2 is None:
            raise GateEnvError("review_approval: seeded row id(s) absent from sqlite")

        # (1) approve R1 -> status approved + approver recorded.
        r1 = a.request("POST", rpath, form={idp: str(rid1), dp: approve_val, ap: name1})
        if r1.status >= 400:
            return False, f"approve was rejected (status {r1.status})"
        if _col_value(rdb, table, scol, rid1) != approved_state:
            return False, f"approve did not set status to {approved_state!r} (got {_col_value(rdb, table, scol, rid1)!r})"
        if _col_value(rdb, table, apcol, rid1) != name1:
            return False, "approve did not record the approver identity in sqlite"

        # (2) a SECOND decision on the decided row is terminal -> rejected, nothing changes.
        r2 = a.request("POST", rpath, form={idp: str(rid1), dp: reject_val, ap: name2})
        if r2.status < 400:
            return False, (f"a SECOND decision on an already-approved row was ACCEPTED "
                           f"(status {r2.status}) — approval is not terminal")
        if _col_value(rdb, table, scol, rid1) != approved_state or _col_value(rdb, table, apcol, rid1) != name1:
            return False, "the terminal-state row was MUTATED by a second decision"

        # (3) reject R2 -> status rejected + approver recorded.
        r3 = a.request("POST", rpath, form={idp: str(rid2), dp: reject_val, ap: name1})
        if r3.status >= 400:
            return False, f"reject was rejected (status {r3.status})"
        if _col_value(rdb, table, scol, rid2) != rejected_state:
            return False, f"reject did not set status to {rejected_state!r}"

    _jitter_sleep(rc)
    port_b = _alloc_port(rc.ctx)
    if not _port_available(port_b):
        raise GateEnvError(f"review_approval restart port {port_b} unavailable")
    with AppProcess(rc.app_dir, port_b, rdb, rc.ctx, rc.env_extra):
        pass
    if _col_value(rdb, table, scol, rid1) != approved_state or _col_value(rdb, table, apcol, rid1) != name1:
        return False, "the approval decision did NOT persist across restart"
    return True, "approve/reject records the approver, blocks a second decision (terminal), and persists"


def _check_schema_real(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    table = spec["table"]
    req_cols = spec.get("required_columns", [])
    try:
        conn = _open_ro(rc.db_path)
    except sqlite3.Error as e:
        return False, f"cannot open db: {e}"
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not row:
            return False, f"table {table!r} does not exist"
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        missing = [c for c in req_cols if c not in cols]
        if missing:
            return False, f"table {table!r} missing required columns {missing} (has {cols})"
        before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()

    rd = spec.get("row_delta")
    if rd:
        via = rd["via"]
        try:
            proc.request(via["method"], via["path"], form=via.get("form"))
        except OSError as e:
            return False, f"row_delta write failed: {e}"
        after = _count(rc.db_path, table)
        expect = rd.get("expect_delta", 1)
        if (after - before) != expect:
            return False, f"row delta {after - before} != expected {expect}"
    return True, "schema columns present and row-count delta matches"


def _check_input_handling(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    target = spec["target"]
    method = target.get("method", "POST")
    path = target["path"]
    fields = target.get("fields", {})
    variants = spec.get("variants", [])
    policy = spec.get("policy", {})
    maxb = int(policy.get("max_field_bytes", 4096))
    safe = spec.get("safe", {})
    status_lt = safe.get("status_lt", 500)
    table = _table_from_path(path)
    text_field = next((f for f, t in fields.items() if t == "text"), next(iter(fields), "name"))
    bool_field = next((f for f, t in fields.items() if t == "bool"), None)

    for variant in variants:
        canary_tok = "cxcanary" + format(rc.rng.getrandbits(48), "012x")
        conn = _open_rw(rc.db_path)
        try:
            conn.execute(f"INSERT INTO {table} ({text_field}) VALUES (?)", (canary_tok,))
            conn.commit()
            before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

        status = 599
        try:
            if variant == "empty_required":
                form = {text_field: ""}
                if bool_field:
                    form[bool_field] = "0"
                status = proc.request(method, path, form=form).status
            elif variant == "type_invalid":
                form = {text_field: "cxvalid" + format(rc.rng.getrandbits(32), "08x")}
                if bool_field:
                    form[bool_field] = "banana"
                status = proc.request(method, path, form=form).status
            elif variant == "oversized":
                form = {text_field: "x" * (maxb * 4)}
                if bool_field:
                    form[bool_field] = "0"
                status = proc.request(method, path, form=form).status
            elif variant == "duplicate":
                name = "cxdup" + format(rc.rng.getrandbits(32), "08x")
                form = {text_field: name}
                if bool_field:
                    form[bool_field] = "0"
                proc.request(method, path, form=form)
                status = proc.request(method, path, form=form).status
            elif variant == "malformed_body":
                status = proc.request(
                    method, path, raw_body=b"\x00\xff{{{",
                    headers={"Content-Type": "application/json"},
                ).status
            else:
                status = proc.request(method, path, form={text_field: "cxx"}).status
        except OSError:
            status = 599  # connection dropped == unsafe

        if status >= status_lt:
            return False, f"variant {variant!r} returned unsafe status {status} (>= {status_lt})"
        if safe.get("process_alive", True) and not proc.alive():
            return False, f"variant {variant!r} crashed the process"
        if safe.get("canary_row_survives", True):
            body = ""
            try:
                body = proc.request("GET", path).body
            except OSError:
                body = ""
            if canary_tok not in body:
                return False, f"variant {variant!r} lost the canary row over HTTP (corruption)"
            conn = None
            found = False
            try:
                conn = _open_ro(rc.db_path)
                rows = conn.execute(f"SELECT {text_field} FROM {table}").fetchall()
                found = any(canary_tok == str(r[0]) for r in rows)
            except sqlite3.Error:
                found = False
            finally:
                if conn is not None:
                    conn.close()
            if not found:
                return False, f"variant {variant!r} lost the canary row in sqlite (corruption)"
        after = _count(rc.db_path, table)
        if after - before > 1:
            return False, f"variant {variant!r} changed row count by {after - before} (>1)"
    return True, "all malformed/empty/oversized/dup/type-invalid inputs failed safely"


def _check_security_controls(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    write = spec.get("write", {"method": "POST", "path": "/clients", "field": "name"})
    read_paths = spec.get("read_paths", ["/", "/clients"])
    table = spec.get("table", "clients")
    tests = spec.get("tests", [])

    for t in tests:
        if t == "reflected_escape":
            xss = _mint_token(rc.rng)
            payload = f"<script>cortex_xss_{xss}</script>"
            try:
                proc.request(write["method"], write["path"], form={write.get("field", "name"): payload})
            except OSError as e:
                return False, f"reflected_escape write failed: {e}"
            raw_marker = f"<script>cortex_xss_{xss}"
            for rp in read_paths:
                body = proc.request("GET", rp).body
                if raw_marker in body:
                    return False, f"reflected_escape: raw <script> served unescaped at {rp}"
        elif t == "canary_secret":
            canary = rc.canary
            for rp in read_paths:
                r = proc.request("GET", rp)
                if canary in r.body or any(canary in v for v in r.headers.values()):
                    return False, f"canary_secret: APP_SECRET_CANARY leaked in response at {rp}"
            out, err = proc.read_std_streams()
            if canary in out or canary in err:
                return False, "canary_secret: APP_SECRET_CANARY leaked into stdout/stderr"
        elif t == "path_traversal":
            probe = rc.probe_secret
            trav_paths = [
                "/../app_contract_probe.txt",
                "/%2e%2e%2fapp_contract_probe.txt",
                "/../../app_contract_probe.txt",
            ]
            for p in trav_paths:
                try:
                    body = proc.request("GET", p).body
                except OSError:
                    continue
                if probe and probe in body:
                    return False, f"path_traversal: probe file served via {p}"

    for route in spec.get("protected", []):
        method = route["method"]
        path = route["path"]
        exp = route.get("expect_without_auth", {})
        allowed = exp.get("status_in", [401, 403, 404])
        # seed a marker row so an unauthorized reset is observable
        marker = "cxmark" + format(rc.rng.getrandbits(32), "08x")
        try:
            conn = _open_rw(rc.db_path)
            try:
                conn.execute(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (marker,))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        before = _count(rc.db_path, table)
        try:
            r = proc.request(method, path)
            status = r.status
        except OSError:
            status = 599
        after = _count(rc.db_path, table)
        status_ok = status in allowed
        unchanged = (before == after) if exp.get("state_unchanged") else True
        if not status_ok:
            return False, f"protected {path}: unauth status {status} not in {allowed}"
        if not unchanged:
            return False, f"protected {path}: state changed without auth ({before} -> {after})"

    return True, "runtime security negative tests all held"


def _metric_value(html_text: str, marker_attr: str) -> int | None:
    """Read the integer value of the FIRST ``<marker_attr>="<int>"`` occurrence in the rendered
    HTML. Returns None if the attribute is absent or its value is not a plain integer — a metric
    that does not render its machine-readable count is treated as a failure, never a skip."""
    m = re.search(re.escape(marker_attr) + r'\s*=\s*"(-?\d+)"', html_text)
    if m is None:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _seed_derived_row(proc: AppProcess, create: dict, form_template: dict,
                      rc: _RunCtx, preserve: str | None = None) -> int:
    """POST one row built from ``form_template`` with a FRESH per-row-unique value so a UNIQUE/dedup
    constraint cannot collapse otherwise-identical rows. ``preserve`` names the predicate field
    whose value the metric counts -- it is NEVER perturbed. Uniquify order: the field already
    carrying a resolved @hidden token (cx + 16 hex); else a non-numeric text field (fresh token);
    else a numeric field (fresh distinct integer -- so we never mint a hex token into an int/bool
    column, which would 400). Returns the HTTP status (599 on a dropped connection)."""
    form = dict(form_template)

    def _is_num(v: object) -> bool:
        return isinstance(v, str) and v.lstrip("-").isdigit()

    # 1. the intended token field (resolved @hidden). Common case: distinct text field != predicate.
    uniq = next((k for k, v in form.items()
                 if k != preserve and isinstance(v, str) and re.fullmatch(r"cx[0-9a-f]{16}", v)), None)
    if uniq is not None:
        form[uniq] = _mint_token(rc.rng)
    else:
        # The token got clobbered (predicate IS the only text field). Uniquify another field
        # type-safely: a spare text field gets a token; else a spare numeric field gets a distinct int.
        text_field = next((k for k, v in form.items()
                           if k != preserve and isinstance(v, str) and not _is_num(v)), None)
        if text_field is not None:
            form[text_field] = _mint_token(rc.rng)
        else:
            num_field = next((k for k, v in form.items() if k != preserve and _is_num(v)), None)
            if num_field is not None:
                form[num_field] = str(rc.rng.randint(1, 2**31 - 1))
    try:
        return proc.request(create["method"], create["path"], form=form).status
    except OSError:
        return 599


def _check_derived_value(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """Verify a shown aggregate (a filtered COUNT metric card) equals the TRUE filtered count.
    Delta-based (no admin reset needed): read the metric, seed n_match predicate-SATISFYING rows
    + n_no predicate-NOT-satisfying rows (each with a distinct hidden name), re-read the metric,
    and assert it moved by EXACTLY n_match. A hardcoded number, a total-not-filtered count, a
    wrong-field count, or a missing machine-readable attribute all fail."""
    get_path = spec.get("get_path", "/")
    marker_attr = spec["marker_attr"]
    create = spec["create"]
    match_form = spec["match_form"]
    nomatch_form = spec["nomatch_form"]

    try:
        before_body = proc.request("GET", get_path).body
    except OSError as e:
        return False, f"metric read failed before seeding: {e}"
    before = _metric_value(before_body, marker_attr)
    if before is None:
        return False, (f"metric attr {marker_attr!r} absent or non-integer at {get_path!r} "
                       f"before seeding (a metric that doesn't render its machine-readable "
                       f"count fails)")

    n_match = rc.rng.randint(2, 5)
    n_no = rc.rng.randint(1, 4)
    preserve = spec.get("predicate_field")

    for i in range(n_match):
        st = _seed_derived_row(proc, create, match_form, rc, preserve)
        if st >= 400:
            raise GateEnvError(f"derived_value match seed row {i} POST returned {st} (>=400)")
    for i in range(n_no):
        st = _seed_derived_row(proc, create, nomatch_form, rc, preserve)
        if st >= 400:
            raise GateEnvError(f"derived_value nomatch seed row {i} POST returned {st} (>=400)")

    try:
        after_body = proc.request("GET", get_path).body
    except OSError as e:
        return False, f"metric read failed after seeding: {e}"
    after = _metric_value(after_body, marker_attr)
    if after is None:
        return False, (f"metric attr {marker_attr!r} absent or non-integer at {get_path!r} "
                       f"after seeding")

    delta = after - before
    if delta != n_match:
        return False, (f"metric delta {delta} != n_match {n_match} "
                       f"(before={before} after={after} n_no={n_no}); a hardcoded / "
                       f"total-not-filtered / wrong-field metric cannot match the seeded "
                       f"filtered count")
    return True, (f"metric counted EXACTLY the {n_match} predicate-matching rows "
                  f"(before={before} after={after}; {n_no} non-matching rows correctly ignored)")


def _seed_search_row(proc: AppProcess, create: dict, form_template: dict,
                     search_value: str, rc: _RunCtx) -> int:
    """POST one row built from ``form_template``, forcing the SEARCHED (token-bearing) field to
    ``search_value`` so the check controls exactly what the searched text field holds. The searched
    field is the one carrying a resolved ``@hidden`` token (``cx`` + 16 hex); every other field is
    left as the template specifies (valid required defaults). Returns the HTTP status (599 on a
    dropped connection)."""
    form = dict(form_template)
    search_field = None
    for k, v in form.items():
        if isinstance(v, str) and re.fullmatch(r"cx[0-9a-f]{16}", v):
            search_field = k
            break
    if search_field is None:
        search_field = next((k for k, v in form.items() if isinstance(v, str)), None)
    if search_field is not None:
        form[search_field] = search_value
    try:
        return proc.request(create["method"], create["path"], form=form).status
    except OSError:
        return 599


def _check_filtered_results(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    """Verify a search/filter returns EVERY matching row and NO non-matching row.

    Mint a hidden search TERM at runtime. Seed n_match rows whose searched text field CONTAINS the
    term (term embedded in a distinct per-row value so each row is unique yet all contain the term)
    and n_no rows whose searched field does NOT contain the term (distinct non-term tokens). GET the
    search endpoint with ``query_param=term`` and assert the response body contains EVERY matching
    row-value and NONE of the non-matching row-values. A no-op search (returns everything) leaks the
    non-matching values; a wrong-field / hardcoded / empty search drops the matching values. Both
    fail FILTER_FAIL. The coach only ever sees the coarse class."""
    search = spec["search"]
    get_path = search["get_path"]
    query_param = search["query_param"]
    create = spec["create"]
    match_form = spec["match_form"]
    nomatch_form = spec["nomatch_form"]

    term = "cxterm" + format(rc.rng.getrandbits(48), "012x")
    n_match = rc.rng.randint(2, 4)
    n_no = rc.rng.randint(2, 4)

    match_values: list[str] = []
    for i in range(n_match):
        # distinct per-row value that CONTAINS the term -> each row unique, all match the search
        val = term + format(rc.rng.getrandbits(32), "08x")
        st = _seed_search_row(proc, create, match_form, val, rc)
        if st >= 400:
            raise GateEnvError(f"filtered_results match seed row {i} POST returned {st} (>=400)")
        match_values.append(val)

    nomatch_values: list[str] = []
    for i in range(n_no):
        # distinct value that does NOT contain the term
        val = "cxno" + format(rc.rng.getrandbits(64), "016x")
        st = _seed_search_row(proc, create, nomatch_form, val, rc)
        if st >= 400:
            raise GateEnvError(f"filtered_results nomatch seed row {i} POST returned {st} (>=400)")
        nomatch_values.append(val)

    sep = "&" if "?" in get_path else "?"
    query_url = get_path + sep + urllib.parse.urlencode({query_param: term})
    try:
        body = proc.request("GET", query_url).body
    except OSError as e:
        return False, f"search GET {query_url!r} failed: {e}"

    missing = [v for v in match_values if v not in body]
    leaked = [v for v in nomatch_values if v in body]
    if missing:
        return False, (f"search for term {term!r} DROPPED {len(missing)}/{n_match} MATCHING rows "
                       f"(a hardcoded / wrong-field / always-empty search hides real matches): "
                       f"missing={missing}")
    if leaked:
        return False, (f"search for term {term!r} LEAKED {len(leaked)}/{n_no} NON-matching rows "
                       f"(a no-op search that ignores the query returns everything): leaked={leaked}")
    return True, (f"search returned EXACTLY the {n_match} rows whose {query_param!r} contains the "
                  f"term and none of the {n_no} that don't (n_match={n_match} n_no={n_no})")


def _check_regression(proc: AppProcess, spec: dict, rc: _RunCtx) -> tuple[bool, str]:
    ledger_file = spec["ledger_file"]
    base = rc.ledger_dir if rc.ledger_dir is not None else rc.app_dir
    ledger_path = Path(base) / ledger_file
    if not ledger_path.exists():
        return True, "regression ledger empty/absent (nothing to rerun)"
    lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            sub = json.loads(line)
        except json.JSONDecodeError:
            return False, f"regression ledger line {i} unparseable"
        sub = resolve_hidden(sub, rc.rng, rc.vault)
        kind = sub.get("kind")
        if kind == "app_starts":
            if not proc.alive():
                return False, f"regression ledger line {i} (app_starts) failed: process dead"
            continue
        fn = _DISPATCH.get(kind)
        if fn is None:
            return False, f"regression ledger line {i} unknown kind {kind!r}"
        passed, detail = fn(proc, sub, rc)
        if not passed:
            return False, f"regression ledger line {i} ({kind}) failed: {detail}"
    return True, "every accepted ledger check reran clean"


_DISPATCH = {
    "buttons_work": _check_buttons_work,
    "logic_works": _check_logic_works,
    "data_persists": _check_data_persists,
    "schema_real": _check_schema_real,
    "input_handling": _check_input_handling,
    "security_controls": _check_security_controls,
    "derived_value": _check_derived_value,
    "filtered_results": _check_filtered_results,
    "deletes_row": _check_deletes_row,
    "edits_row": _check_edits_row,
    "auth_required": _check_auth_required,
    "audit_trail": _check_audit_trail,
    "dashboard_metrics": _check_dashboard_metrics,
    "detail_view": _check_detail_view,
    "relation_integrity": _check_relation_integrity,
    "status_lifecycle": _check_status_lifecycle,
    "soft_delete": _check_soft_delete,
    "assignment": _check_assignment,
    "review_approval": _check_review_approval,
    "regression": _check_regression,
}


def _jitter_sleep(rc: _RunCtx) -> None:
    base = rc.ctx.restart_delay_s
    jitter = rc.rng.uniform(-0.5, 0.5) * base
    time.sleep(max(0.0, base + jitter))


# --------------------------------------------------------------------------- #
# Verdict assembly + orchestration                                            #
# --------------------------------------------------------------------------- #
def _build_verdict(results: list[CheckResult], hidden_coverage: bool,
                   env_retries: int, seed: int) -> GateVerdict:
    passed = all(r.passed for r in results) and len(results) > 0
    first_fail = next((r for r in results if not r.passed), None)
    failure_class = first_fail.failure_class if first_fail else None
    return GateVerdict(
        passed=passed,
        results=tuple(results),
        failure_class=failure_class,
        hidden_coverage=hidden_coverage,
        env_retries=env_retries,
        seed=seed,
    )


def _env_fail_verdict(errors: list[str], seed: int, hidden_coverage: bool,
                      env_retries: int) -> GateVerdict:
    detail = "; ".join(errors)[:400]
    result = CheckResult("app_starts", False, False, detail, "ENV_FAIL")
    return GateVerdict(
        passed=False,
        results=(result,),
        failure_class="ENV_FAIL",
        hidden_coverage=hidden_coverage,
        env_retries=env_retries,
        seed=seed,
    )


def _interleave(visible: list, hidden: list, rng: "random.Random") -> list:
    """Preserve visible order; insert each hidden check at a seeded-random position."""
    merged = list(visible)
    for hc in hidden:
        pos = rng.randint(0, len(merged))
        merged.insert(pos, hc)
    return merged


def _run_once(app_dir: Path, resolved: list, ctx: GateContext, rng: "random.Random",
              vault: dict[str, str], ledger_dir: Path | None) -> list[CheckResult]:
    run_dir = Path(tempfile.mkdtemp(prefix="cortex_gate_"))
    db_path = run_dir / "app.db"
    canary = os.urandom(16).hex()
    env_extra = {"APP_SECRET_CANARY": canary, "APP_ADMIN_TOKEN": GATE_ADMIN_TOKEN}

    # Plant the path-traversal probe OUTSIDE app_dir but reachable via `..`.
    probe_path = Path(app_dir).parent / "app_contract_probe.txt"
    probe_secret = "PROBE_" + os.urandom(8).hex()
    try:
        probe_path.write_text(probe_secret, encoding="utf-8")
    except OSError as e:
        raise GateEnvError(f"probe setup failed: {e}") from e

    rc = _RunCtx(
        app_dir=Path(app_dir), run_dir=run_dir, db_path=db_path, canary=canary,
        env_extra=env_extra, probe_path=probe_path, probe_secret=probe_secret,
        ctx=ctx, rng=rng, vault=vault, ledger_dir=ledger_dir,
    )
    results: list[CheckResult] = []
    try:
        port = _alloc_port(ctx)
        if not _port_available(port):
            raise GateEnvError(f"shared port {port} unavailable")

        try:
            proc = AppProcess(app_dir, port, db_path, ctx, env_extra)
            proc.__enter__()
        except AppStartError as e:
            results.append(CheckResult("app_starts", False, False, str(e)[:400], "START_FAIL"))
            return results

        try:
            for spec, hidden in resolved:
                kind = spec.get("kind")
                if kind == "app_starts":
                    alive = proc.alive()
                    fc = None if alive else "START_FAIL"
                    results.append(
                        CheckResult("app_starts", alive, hidden,
                                    f"pid={proc.pid} alive={alive}", fc)
                    )
                    continue
                fn = _DISPATCH[kind]
                try:
                    passed, detail = fn(proc, spec, rc)
                except GateEnvError:
                    raise
                except Exception as e:  # noqa: BLE001 — a check must never crash the gate
                    passed, detail = False, f"check raised {type(e).__name__}: {e}"
                fc = None if passed else KIND_TO_CLASS.get(kind, "ENV_FAIL")
                results.append(CheckResult(kind, passed, hidden, str(detail)[:400], fc))
        finally:
            proc.__exit__(None, None, None)
        return results
    finally:
        try:
            if probe_path.exists():
                probe_path.unlink()
        except OSError:
            pass


def run_done_checks(app_dir: Path, checks: list[dict], *,
                    hidden_checks: list[dict] | None = None,
                    ledger_dir: Path | None = None,
                    ctx: GateContext | None = None) -> GateVerdict:
    """THE gate. Orders app_starts first, seeds RNG, resolves hidden placeholders,
    runs every check (visible + hidden, seeded interleave), stops at nothing —
    ALL checks run so the verdict lists every failure, but failure_class = first failure.
    ENV_FAIL -> one full retry on a fresh port before returning ENV_FAIL."""
    ctx = ctx or GateContext()
    app_dir = Path(app_dir)
    seed = ctx.seed if ctx.seed is not None else int.from_bytes(os.urandom(4), "big")
    rng = random.Random(seed)
    hidden_checks = hidden_checks or []
    hidden_coverage = len(hidden_checks) > 0

    tagged = [(dict(c), False) for c in checks] + [(dict(c), True) for c in hidden_checks]
    if not any(c.get("kind") == "app_starts" for c, _ in tagged):
        tagged.insert(0, ({"kind": "app_starts"}, False))

    # Fail-closed static lint BEFORE any process launch: malformed -> ENV_FAIL, no retry.
    errors: list[str] = []
    for c, _ in tagged:
        errors.extend(validate_check_spec(c))
    if errors:
        return _env_fail_verdict(errors, seed, hidden_coverage, env_retries=0)

    starts = [(c, h) for c, h in tagged if c.get("kind") == "app_starts"]
    visible_rest = [(c, h) for c, h in tagged if c.get("kind") != "app_starts" and not h]
    hidden_rest = [(c, h) for c, h in tagged if c.get("kind") != "app_starts" and h]
    ordered = starts[:1] + _interleave(visible_rest, hidden_rest, rng)

    vault: dict[str, str] = {}
    resolved = [(resolve_hidden(c, rng, vault), h) for c, h in ordered]

    env_retries = 0
    for attempt in range(ctx.max_env_retries + 1):
        try:
            results = _run_once(app_dir, resolved, ctx, rng, vault, ledger_dir)
            return _build_verdict(results, hidden_coverage, env_retries, seed)
        except GateEnvError as e:
            if attempt < ctx.max_env_retries:
                env_retries += 1
                continue
            return _env_fail_verdict([str(e)], seed, hidden_coverage, env_retries)
    # Unreachable, but keep the type checker honest.
    return _env_fail_verdict(["gate exhausted retries"], seed, hidden_coverage, env_retries)


def append_ledger(ledger_file: Path, accepted_check: dict) -> None:
    """Append one accepted spec to the regression ledger (JSONL, utf-8)."""
    p = Path(ledger_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(accepted_check, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def _integrity_sweep() -> int:
    """Delegate to the M2 fixture sweep if present. Loaded dynamically (importlib) so the
    stdlib-only import firewall over this module stays intact."""
    try:
        fixtures = importlib.import_module("evals.app_gate_fixtures.fixtures")
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"integrity": "unavailable", "reason": str(e)}))
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="cortex_integrity_")) / "app"
    tmp.mkdir(parents=True, exist_ok=True)
    good = fixtures.write_good_app(tmp)  # type: ignore[attr-defined]
    verdict = run_done_checks(good.parent, fixtures.standard_checks())  # type: ignore[attr-defined]
    print(json.dumps(coach_view(verdict)))
    return 0 if verdict.passed else 1


def main(argv: list[str] | None = None) -> int:
    """CLI `cortex-app-gate`: --app-dir --checks <json file> [--holdout <dir> --family <f>]
    [--json]; exit 0 iff passed. `--integrity` delegates to the fixture sweep (M2)."""
    parser = argparse.ArgumentParser(prog="cortex-app-gate")
    parser.add_argument("--app-dir")
    parser.add_argument("--checks")
    parser.add_argument("--holdout")
    parser.add_argument("--family", default="crud")
    parser.add_argument("--ledger-dir")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--integrity", action="store_true")
    args = parser.parse_args(argv)

    if args.integrity:
        return _integrity_sweep()

    if not args.app_dir or not args.checks:
        parser.error("--app-dir and --checks are required (unless --integrity)")

    checks = json.loads(Path(args.checks).read_text(encoding="utf-8", errors="replace"))
    hidden = load_hidden_checks(Path(args.holdout), args.family) if args.holdout else None
    ledger_dir = Path(args.ledger_dir) if args.ledger_dir else None
    ctx = GateContext(seed=args.seed) if args.seed is not None else GateContext()
    verdict = run_done_checks(Path(args.app_dir), checks, hidden_checks=hidden,
                              ledger_dir=ledger_dir, ctx=ctx)
    if args.json:
        print(json.dumps({
            "pass": verdict.passed,
            "failure_class": verdict.failure_class,
            "hidden_coverage": verdict.hidden_coverage,
            "env_retries": verdict.env_retries,
            "seed": verdict.seed,
        }))
    else:
        print(json.dumps(coach_view(verdict)))
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
