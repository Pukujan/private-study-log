"""Tests for the BUILD-01 / M2 gate-integrity fixtures.

These verify the FIXTURES/MUTANTS registries are well-shaped, that `standard_checks()` and
`holdout_checks()` are valid per `app_contract.validate_check_spec`, and — the load-bearing
part — that each fixture app actually LAUNCHES per App Contract 1.1 and exhibits its intended
behavior: the good app is a real working CRUD (persists, escapes, auth-gates, class-by-status),
and each bad app / mutant carries its specific defect. We drive the apps directly over HTTP with
a tiny in-test launcher; running the real gate over them is Unit 5's integration job (not here).
"""
import http.client
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cortex_core.app_contract import (  # noqa: E402
    CHECK_KINDS, FAILURE_CLASSES, validate_check_spec,
)
from evals.app_gate_fixtures import fixtures as fx  # noqa: E402
from evals.app_gate_fixtures import mutations as mut  # noqa: E402


# --------------------------------------------------------------------------- #
# Tiny App-Contract launcher (stdlib only; Windows-safe; timeout-capped)       #
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


class RunningApp:
    """Launch app.py per App Contract 1.1, wait until it binds, drive it over HTTP, kill on exit."""

    def __init__(self, app_dir: Path, db_path: Path, start_timeout_s: float = 15.0):
        self.app_dir = Path(app_dir)
        self.db_path = Path(db_path)
        self.start_timeout_s = start_timeout_s
        self.port = _free_port()
        self.proc = None
        self._outf = None
        self._errf = None

    def __enter__(self) -> "RunningApp":
        self._outf = open(self.app_dir / f"stdout_{self.port}.txt", "w",
                          encoding="utf-8", errors="replace")
        self._errf = open(self.app_dir / f"stderr_{self.port}.txt", "w",
                          encoding="utf-8", errors="replace")
        env = dict(os.environ)
        env["APP_SECRET_CANARY"] = "cxcanary_" + os.urandom(8).hex()
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc = subprocess.Popen(
            [sys.executable, "app.py", "--port", str(self.port), "--db", str(self.db_path)],
            cwd=str(self.app_dir), stdout=self._outf, stderr=self._errf, env=env,
        )
        deadline = time.time() + self.start_timeout_s
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"app exited rc={self.proc.returncode}; stderr={self.stderr()}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                    return self
            except OSError:
                time.sleep(0.05)
        self._kill()
        raise RuntimeError(f"app not ready within {self.start_timeout_s}s; stderr={self.stderr()}")

    def __exit__(self, *exc) -> None:
        self._kill()

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=10)
            except Exception:
                pass
        for f in (self._outf, self._errf):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass

    def stdout(self) -> str:
        try:
            return (self.app_dir / f"stdout_{self.port}.txt").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stderr(self) -> str:
        try:
            return (self.app_dir / f"stderr_{self.port}.txt").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def request(self, method: str, path: str, *, form=None, raw=None, headers=None):
        hdrs = dict(headers or {})
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif raw is not None:
            body = raw
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data.decode("utf-8", "replace")
        finally:
            conn.close()


def _tok(prefix="cx"):
    return prefix + os.urandom(8).hex()


# --------------------------------------------------------------------------- #
# Registry / static shape                                                     #
# --------------------------------------------------------------------------- #
def test_bad_kinds_are_the_five_designed():
    assert fx.BAD_KINDS == (
        "static_only", "memory_db_only", "fake_success", "missing_auth", "unsafe_render",
    )


def test_expected_failure_covers_all_bad_kinds_and_valid_classes():
    assert set(fx.EXPECTED_FAILURE) == set(fx.BAD_KINDS)
    for kind, cls in fx.EXPECTED_FAILURE.items():
        assert cls in FAILURE_CLASSES, (kind, cls)


def test_fixtures_registry_shape():
    assert "good" in fx.FIXTURES
    assert set(fx.FIXTURES) == {"good", *fx.BAD_KINDS}
    good = fx.FIXTURES["good"]
    assert good["expect_pass"] is True and good["expect_class"] is None
    assert callable(good["writer"]) and good["kind"] == "good"
    for kind in fx.BAD_KINDS:
        entry = fx.FIXTURES[kind]
        assert entry["expect_pass"] is False
        assert entry["expect_class"] == fx.EXPECTED_FAILURE[kind]
        assert entry["expect_class"] in FAILURE_CLASSES
        assert callable(entry["writer"]) and entry["kind"] == "bad"


def test_registry_writer_writes_correct_app(tmp_path):
    # The registry writer must produce the same source as the direct writer, at app.py.
    d1, d2 = tmp_path / "reg", tmp_path / "dir"
    p1 = fx.FIXTURES["missing_auth"]["writer"](d1)
    p2 = fx.write_bad_app("missing_auth", d2)
    assert p1.name == "app.py"
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Check-suite validity (no gate run — pure lint via app_contract)             #
# --------------------------------------------------------------------------- #
def test_standard_checks_all_valid_and_cover_every_kind():
    checks = fx.standard_checks()
    kinds = [c["kind"] for c in checks]
    for kind in CHECK_KINDS:
        assert kind in kinds, f"standard_checks missing {kind}"
    for c in checks:
        assert validate_check_spec(c) == [], (c["kind"], validate_check_spec(c))


def test_standard_checks_order_is_load_bearing():
    kinds = [c["kind"] for c in fx.standard_checks()]
    # data_persists must precede the other file-coupled checks so no-persistence apps
    # classify as PERSISTENCE_FAIL rather than a later class.
    assert kinds.index("data_persists") < kinds.index("schema_real")
    assert kinds.index("data_persists") < kinds.index("logic_works")
    assert kinds.index("data_persists") < kinds.index("input_handling")
    assert kinds[0] == "app_starts"


def test_holdout_checks_valid_and_seedable(tmp_path):
    import json
    import random
    for seed in (1, 2, 3):
        for c in fx.holdout_checks(random.Random(seed)):
            assert validate_check_spec(c) == [], (c, validate_check_spec(c))
    jsonl = fx.seed_holdout_dir(tmp_path / "holdout", seed=7)
    assert jsonl.exists() and jsonl.name == "crud.jsonl"
    lines = [ln for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 1
    for ln in lines:
        assert validate_check_spec(json.loads(ln)) == []


# --------------------------------------------------------------------------- #
# App-Contract compliance (static + launch) for every fixture                 #
# --------------------------------------------------------------------------- #
def _all_app_sources():
    yield "good", fx.GOOD_APP_SOURCE
    for kind in fx.BAD_KINDS:
        yield kind, fx.bad_app_source(kind)
    for m in mut.MUTANTS:
        yield f"mutant:{m.mutant_id}", m.mutate_good()


@pytest.mark.parametrize("name,source", list(_all_app_sources()))
def test_every_app_source_compiles_and_is_contract_shaped(name, source, tmp_path):
    import py_compile
    p = tmp_path / "app.py"
    p.write_text(source, encoding="utf-8")
    py_compile.compile(str(p), doraise=True)  # mutated source must still be valid Python
    assert "CORTEX_APP_READY" in source, name
    assert "--port" in source and "--db" in source, name


@pytest.mark.parametrize("kind", ["good", *fx.BAD_KINDS])
def test_every_fixture_app_launches_and_binds(kind, tmp_path):
    app_dir = tmp_path / kind
    if kind == "good":
        fx.write_good_app(app_dir)
    else:
        fx.write_bad_app(kind, app_dir)
    with RunningApp(app_dir, app_dir / "app.db") as app:
        status, _ = app.request("GET", "/clients")
        assert status == 200
        assert "CORTEX_APP_READY" in app.stdout()


# --------------------------------------------------------------------------- #
# GOOD app behaves like a real, correct CRUD                                  #
# --------------------------------------------------------------------------- #
def test_good_app_is_real_working_crud(tmp_path):
    fx.write_good_app(tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        name = _tok()
        s0, before = app.request("GET", "/clients")
        assert s0 == 200 and name not in before
        s1, _ = app.request("POST", "/clients", form={"name": name, "paid": "0"})
        assert s1 == 200
        s2, after = app.request("GET", "/clients")
        assert s2 == 200 and name in after  # real, visible state change


def test_good_app_persists_across_kill_and_restart(tmp_path):
    fx.write_good_app(tmp_path)
    db = tmp_path / "app.db"
    name = _tok("cxpersist")
    with RunningApp(tmp_path, db) as app:
        app.request("POST", "/clients", form={"name": name, "paid": "1"})
    # process is dead; row must be on disk and visible to a fresh process
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in conn.execute("SELECT name FROM clients").fetchall()]
    finally:
        conn.close()
    assert name in rows, "good app did not persist to the sqlite file"
    with RunningApp(tmp_path, db) as app2:
        _, body = app2.request("GET", "/clients")
        assert name in body


def test_good_app_escapes_reflected_html(tmp_path):
    fx.write_good_app(tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        marker = _tok("xss")
        payload = f"<script>{marker}</script>"
        app.request("POST", "/clients", form={"name": payload, "paid": "0"})
        _, body = app.request("GET", "/clients")
        assert f"<script>{marker}" not in body           # raw script never served
        assert "&lt;script&gt;" in body                  # escaped form present


def test_good_app_admin_reset_requires_auth(tmp_path):
    fx.write_good_app(tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        name = _tok()
        app.request("POST", "/clients", form={"name": name, "paid": "0"})
        # no token -> refused, state unchanged
        s_no, _ = app.request("POST", "/admin/reset")
        assert s_no in (401, 403)
        _, body = app.request("GET", "/clients")
        assert name in body
        # correct token -> reset
        s_ok, _ = app.request("POST", "/admin/reset",
                              headers={"X-Admin-Token": fx.ADMIN_TOKEN})
        assert s_ok == 200
        _, cleared = app.request("GET", "/clients")
        assert name not in cleared


def test_good_app_row_class_reflects_status(tmp_path):
    fx.write_good_app(tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        late = _tok("late")
        paid = _tok("paid")
        app.request("POST", "/clients", form={"name": late, "status": "late"})
        app.request("POST", "/clients", form={"name": paid, "status": "paid"})
        _, body = app.request("GET", "/")
        # the late row's enclosing <tr> carries class="late"; the paid row does not
        assert f'<tr class="late"><td>{late}' in body
        assert f'<tr class="late"><td>{paid}' not in body


def test_good_app_handles_malformed_body_safely(tmp_path):
    fx.write_good_app(tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        status, _ = app.request("POST", "/clients", raw=b"\x00\xff{{{",
                                headers={"Content-Type": "application/json"})
        assert status < 500                      # safe failure, not a crash
        s2, _ = app.request("GET", "/clients")   # process still alive
        assert s2 == 200


# --------------------------------------------------------------------------- #
# Each BAD app has its specific defect present                                #
# --------------------------------------------------------------------------- #
def test_static_only_shows_no_visible_state_change(tmp_path):
    fx.write_bad_app("static_only", tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        name = _tok()
        s, _ = app.request("POST", "/clients", form={"name": name, "paid": "0"})
        assert s == 200                          # fakes success
        _, body = app.request("GET", "/clients")
        assert name not in body                  # ...but nothing changed -> BUTTON_FAIL


def _assert_not_persisted(tmp_path, kind):
    db = tmp_path / "app.db"
    name = _tok("cxdefect")
    with RunningApp(tmp_path, db) as app:
        app.request("POST", "/clients", form={"name": name, "paid": "0"})
        _, live = app.request("GET", "/clients")
        assert name in live                      # works IN-process (buttons would pass)
    # fresh process on the same --db must NOT see it
    with RunningApp(tmp_path, db) as app2:
        _, body = app2.request("GET", "/clients")
        assert name not in body, f"{kind} unexpectedly persisted across restart"
    # ...and it is not in the sqlite file either
    found = False
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            found = any(name == r[0] for r in conn.execute("SELECT name FROM clients").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        found = False
    assert not found, f"{kind} wrote to the sqlite file"


def test_memory_db_only_does_not_persist(tmp_path):
    fx.write_bad_app("memory_db_only", tmp_path)
    _assert_not_persisted(tmp_path, "memory_db_only")


def test_fake_success_does_not_persist(tmp_path):
    fx.write_bad_app("fake_success", tmp_path)
    _assert_not_persisted(tmp_path, "fake_success")


def test_missing_auth_resets_without_token(tmp_path):
    fx.write_bad_app("missing_auth", tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        name = _tok()
        app.request("POST", "/clients", form={"name": name, "paid": "0"})
        _, before = app.request("GET", "/clients")
        assert name in before
        s, _ = app.request("POST", "/admin/reset")   # NO token
        assert s == 200                              # unauthenticated reset succeeds -> defect
        _, after = app.request("GET", "/clients")
        assert name not in after


def test_unsafe_render_reflects_raw_script(tmp_path):
    fx.write_bad_app("unsafe_render", tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        marker = _tok("xss")
        app.request("POST", "/clients", form={"name": f"<script>{marker}</script>", "paid": "0"})
        _, body = app.request("GET", "/clients")
        assert f"<script>{marker}</script>" in body   # served unescaped -> SECURITY defect


# --------------------------------------------------------------------------- #
# Mutants: differ from good in exactly the intended way                       #
# --------------------------------------------------------------------------- #
def test_mutant_registry_shape():
    ids = [m.mutant_id for m in mut.MUTANTS]
    assert len(ids) == len(set(ids)), "duplicate mutant ids"
    assert len(mut.MUTANTS) == 6
    classes = {m.expected_class for m in mut.MUTANTS}
    # one mutant per catchable failure class the fixtures target
    assert classes == {
        "PERSISTENCE_FAIL", "SECURITY_FAIL", "LOGIC_FAIL", "INVALID_INPUT_FAIL", "SCHEMA_FAIL",
    }
    for m in mut.MUTANTS:
        assert m.expected_class in FAILURE_CLASSES
        assert m.gate_assertion and m.description


@pytest.mark.parametrize("m", mut.MUTANTS, ids=lambda m: m.mutant_id)
def test_each_mutant_changes_source_exactly_once(m):
    mutated = m.mutate_good()
    assert mutated != fx.GOOD_APP_SOURCE, f"{m.mutant_id} was a no-op"
    # single-substring surgery: exactly one anchor occurrence replaced
    assert fx.GOOD_APP_SOURCE.count(m._old) == 1, f"{m.mutant_id} anchor not unique"
    assert m._old not in mutated or m._new in mutated


def test_mutant_apply_raises_on_missing_anchor():
    bogus = mut.Mutant("bogus", "d", "LOGIC_FAIL", "none", _old="NOT_IN_SOURCE", _new="x")
    with pytest.raises(mut.MutationNoop):
        bogus.apply(fx.GOOD_APP_SOURCE)


def test_write_mutant_app_writes_compilable_app(tmp_path):
    import py_compile
    for m in mut.MUTANTS:
        d = tmp_path / m.mutant_id
        p = mut.write_mutant_app(m.mutant_id, d)
        assert p.name == "app.py"
        py_compile.compile(str(p), doraise=True)


def test_drop_commit_mutant_loses_data_on_restart(tmp_path):
    mut.write_mutant_app("drop_commit", tmp_path)
    _assert_not_persisted(tmp_path, "drop_commit")


def test_skip_escape_mutant_reflects_raw_script(tmp_path):
    mut.write_mutant_app("skip_escape_name", tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        marker = _tok("xss")
        app.request("POST", "/clients", form={"name": f"<script>{marker}</script>", "paid": "0"})
        _, body = app.request("GET", "/clients")
        assert f"<script>{marker}</script>" in body


def test_always_late_mutant_mislabels_paid_row(tmp_path):
    mut.write_mutant_app("always_late_class", tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        paid = _tok("paid")
        app.request("POST", "/clients", form={"name": paid, "status": "paid"})
        _, body = app.request("GET", "/")
        assert f'<tr class="late"><td>{paid}' in body   # paid row wrongly marked late


def test_unprotect_admin_mutant_resets_without_token(tmp_path):
    mut.write_mutant_app("unprotect_admin", tmp_path)
    with RunningApp(tmp_path, tmp_path / "app.db") as app:
        name = _tok()
        app.request("POST", "/clients", form={"name": name, "paid": "0"})
        s, _ = app.request("POST", "/admin/reset")     # no token
        assert s == 200
        _, after = app.request("GET", "/clients")
        assert name not in after


def test_drop_id_column_mutant_removes_id_column(tmp_path):
    mut.write_mutant_app("drop_id_column", tmp_path)
    db = tmp_path / "app.db"
    with RunningApp(tmp_path, db) as app:
        app.request("POST", "/clients", form={"name": _tok(), "paid": "0"})
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()]
    finally:
        conn.close()
    assert "name" in cols and "id" not in cols   # schema_real requires id -> would fail
