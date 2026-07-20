"""GAP G6 -- per-tenant no-log enforcement (client-honored + server-enforced).

DATA-USE.md promises a collaborator can refuse data-capture and that
`CORTEX_DATA_CAPTURE=opt-out` / `DO_NOT_TRACK=1` is honored. These tests make that
REAL, not owner-mediated: a no-log tenant's query must never reach EITHER usage sink
(the MCP event log `logs/mcp-events.jsonl` -- which is also what gets mirrored to R2 --
or the search-telemetry log `logs/search-telemetry.jsonl`, which carries the raw query).

Proven here:
  (a) a no-log tenant's query is NOT written to the session-record / telemetry sinks,
  (b) a normal (consented) tenant's query IS,
  (c) the owner can set/clear the per-key no_log flag, and it survives verify/rotate,
  (d) the DEFAULT for a keyed tenant is opt-out (silence is not consent),
  (e) an unkeyed owner/CLI session is unaffected (still logs -- the owner's own brain).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cortex_core import keys
from cortex_core import mcp as m
from cortex_core.search import CortexSearchIndex


# --------------------------------------------------------------------------- helpers
def _reg():
    return getattr(m.cortex_register, "fn", m.cortex_register)


def _search():
    return getattr(m.cortex_search, "fn", m.cortex_search)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _seed_docs(workspace: Path) -> None:
    shard = workspace / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "widgets.md").write_text(
        "# Widgets\n\nThis document discusses widgets and gears.\n", encoding="utf-8"
    )


def _events(workspace: Path) -> list[dict]:
    p = workspace / "logs" / "mcp-events.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _search_telemetry(workspace: Path) -> list[dict]:
    p = workspace / "logs" / "search-telemetry.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _fake_verify(monkeypatch, *, token: str, tenant_id: str, no_log: bool):
    monkeypatch.setattr(
        "cortex_core.keys.verify_key",
        lambda t, **k: (
            {"key_id": "ck_x", "tenant_id": tenant_id, "scope": "read", "no_log": no_log}
            if t == token
            else None
        ),
    )


# --------------------------------------------------------------------------- (c) store flag
def test_no_log_flag_defaults_false_and_is_settable_and_clearable(tmp_path):
    store = tmp_path / "api_keys.json"
    key_id, raw = keys.issue_key("collab", scope="read", tenant_id="t1", store_path=store)
    # default: a freshly-issued key is NOT no-log (the flag is explicit, not implicit)
    assert keys.verify_key(raw, store_path=store)["no_log"] is False

    assert keys.set_no_log(key_id, True, store_path=store) is True
    assert keys.verify_key(raw, store_path=store)["no_log"] is True
    # visible to the owner in the metadata listing
    assert next(r for r in keys.list_keys(store_path=store) if r["key_id"] == key_id)["no_log"] is True

    assert keys.set_no_log(key_id, False, store_path=store) is True
    assert keys.verify_key(raw, store_path=store)["no_log"] is False
    # unknown key -> False, no crash
    assert keys.set_no_log("ck_nope", True, store_path=store) is False


def test_no_log_flag_survives_rotation(tmp_path):
    store = tmp_path / "api_keys.json"
    key_id, _ = keys.issue_key("collab", scope="read", tenant_id="t9", store_path=store)
    keys.set_no_log(key_id, True, store_path=store)
    new_id, new_raw = keys.rotate_key(key_id, store_path=store)
    # the tenant's privacy choice must not be silently reset by a key rotation
    assert keys.verify_key(new_raw, store_path=store)["no_log"] is True


# --------------------------------------------------------------------------- (a) suppressed
def test_no_log_tenant_query_not_logged_to_either_sink(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    _fake_verify(monkeypatch, token="nolog-key", tenant_id="t-secret", no_log=True)

    reg = _reg()("agent", "claude-x", role="builder", workspace=str(ws), api_key="nolog-key")
    sid = reg["session_id"]
    assert m._sessions[sid]["no_log"] is True
    asyncio.run(_search()(query="my-secret-query", session_id=sid, workspace=str(ws)))

    # neither the event log nor the search-telemetry log may exist / carry the query
    assert _events(ws) == []
    assert _search_telemetry(ws) == []


# --------------------------------------------------------------------------- (b) consented logs
def test_consented_tenant_query_is_logged(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    _fake_verify(monkeypatch, token="ok-key", tenant_id="t-ok", no_log=False)

    reg = _reg()(
        "agent", "claude-x", role="builder", workspace=str(ws),
        api_key="ok-key", data_capture="consent",
    )
    sid = reg["session_id"]
    assert m._sessions[sid]["no_log"] is False
    asyncio.run(_search()(query="my-consented-query", session_id=sid, workspace=str(ws)))

    tools = [e["tool"] for e in _events(ws)]
    assert "cortex_search" in tools
    assert any(e.get("query") == "my-consented-query" for e in _events(ws))
    assert any(e.get("query") == "my-consented-query" for e in _search_telemetry(ws))


# --------------------------------------------------------------------------- (d) default = opt-out
def test_keyed_tenant_defaults_to_opt_out_without_a_consent_signal(tmp_path, monkeypatch):
    """A collaborator who sends NO capture signal must default to opt-out (silence is
    not consent), even when the owner has not set the key's no_log flag."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    _fake_verify(monkeypatch, token="silent-key", tenant_id="t-silent", no_log=False)

    reg = _reg()("agent", "claude-x", role="builder", workspace=str(ws), api_key="silent-key")
    sid = reg["session_id"]
    asyncio.run(_search()(query="unconsented", session_id=sid, workspace=str(ws)))

    assert _events(ws) == []
    assert _search_telemetry(ws) == []


def test_do_not_track_forces_opt_out_over_a_consent_signal(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    _fake_verify(monkeypatch, token="dnt-key", tenant_id="t-dnt", no_log=False)

    reg = _reg()(
        "agent", "claude-x", role="builder", workspace=str(ws),
        api_key="dnt-key", data_capture="consent", do_not_track=True,
    )
    sid = reg["session_id"]
    assert m._sessions[sid]["no_log"] is True  # DO_NOT_TRACK wins over a local opt-in
    asyncio.run(_search()(query="dnt-query", session_id=sid, workspace=str(ws)))
    assert _events(ws) == []


# --------------------------------------------------------------------------- (e) owner unaffected
def test_unkeyed_owner_session_still_logs(tmp_path, monkeypatch):
    """No api_key -> owner/local/CLI context (the owner's own brain on the owner's
    machine). Enforcement must NOT touch it -- the self-learning corpus depends on it."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)

    reg = _reg()("owner-agent", "claude-x", role="reviewer", workspace=str(ws))
    sid = reg["session_id"]
    assert m._sessions[sid].get("no_log") in (False, None)
    asyncio.run(_search()(query="owner-query", session_id=sid, workspace=str(ws)))

    assert "cortex_search" in [e["tool"] for e in _events(ws)]
    assert any(e.get("query") == "owner-query" for e in _search_telemetry(ws))


# --------------------------------------------------------------------------- search-telemetry gate
def test_search_index_can_suppress_its_own_telemetry(tmp_path, monkeypatch):
    """CortexSearchIndex.search(log_telemetry=False) writes no query to the sink."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_workspace(tmp_path)
    _seed_docs(ws)
    index = CortexSearchIndex(ws)
    index.rebuild()

    index.search("widgets", log_telemetry=False)
    assert _search_telemetry(ws) == []
    index.search("widgets", log_telemetry=True)
    assert [e["query"] for e in _search_telemetry(ws)] == ["widgets"]
