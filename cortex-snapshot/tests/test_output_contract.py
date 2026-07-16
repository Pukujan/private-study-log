"""Local telemetry v1: turn the MCP's own logs/mcp-events.jsonl into per-session output-contract
records + a failure digest, so 'who did what task, what tools, where it fell short' is computable
locally (no R2/redeploy). Observable-only fields (server-emitted, ungameable); the rubric maps to
computed checks: closeout coverage (F6), brain-first (F3), override use (F4)."""
from __future__ import annotations

from cortex_core import output_contract as oc


def _ev(session, tool, ts, **d):
    return {"ts": ts, "session_id": session, "agent_id": f"agent-{session}",
            "declared_model": "qwen", "role": "builder", "tool": tool, **d}


def test_records_group_by_session_and_count_tools():
    events = [
        _ev("s1", "cortex_register", "2026-07-06T10:00:00+00:00"),
        _ev("s1", "cortex_search", "2026-07-06T10:00:05+00:00"),
        _ev("s1", "cortex_search", "2026-07-06T10:00:09+00:00"),
        _ev("s1", "cortex_write_log", "2026-07-06T10:02:00+00:00"),
        _ev("s2", "cortex_register", "2026-07-06T11:00:00+00:00"),
    ]
    recs = {r["session_id"]: r for r in oc.records_from_events(events)}
    assert set(recs) == {"s1", "s2"}
    assert recs["s1"]["agent_id"] == "agent-s1"
    assert recs["s1"]["tools_used"]["cortex_search"] == 2
    assert recs["s1"]["duration_s"] == 120.0


def test_closeout_coverage_flag_catches_the_no_audit_log_failure():
    # s1 closes out; s2 never does (the acute Hermes F6)
    events = [
        _ev("s1", "cortex_register", "2026-07-06T10:00:00+00:00"),
        _ev("s1", "cortex_write_log", "2026-07-06T10:01:00+00:00"),
        _ev("s2", "cortex_register", "2026-07-06T11:00:00+00:00"),
        _ev("s2", "cortex_fetch_doc", "2026-07-06T11:01:00+00:00"),
    ]
    recs = {r["session_id"]: r for r in oc.records_from_events(events)}
    assert recs["s1"]["closeout_coverage"] is True
    assert recs["s2"]["closeout_coverage"] is False


def test_brain_first_flag_catches_wrote_before_searching():
    # s1 searched before fetching (good); s2 fetched with no prior search (the F3 shape)
    events = [
        _ev("s1", "cortex_search", "2026-07-06T10:00:00+00:00"),
        _ev("s1", "cortex_fetch_doc", "2026-07-06T10:00:10+00:00"),
        _ev("s2", "cortex_fetch_doc", "2026-07-06T11:00:00+00:00"),
    ]
    recs = {r["session_id"]: r for r in oc.records_from_events(events)}
    assert recs["s1"]["brain_first"] is True
    assert recs["s2"]["brain_first"] is False


def test_override_and_deep_research_flags():
    events = [
        _ev("s1", "cortex_register", "2026-07-06T10:00:00+00:00"),
        _ev("s1", "contract_override", "2026-07-06T10:00:10+00:00", reason="x"),
        _ev("s1", "cortex_deep_research", "2026-07-06T10:00:20+00:00"),
    ]
    r = oc.records_from_events(events)[0]
    assert r["override_used"] is True and r["deep_research_used"] is True


def test_digest_aggregates_failures():
    events = [
        _ev("s1", "cortex_search", "2026-07-06T10:00:00+00:00"),
        _ev("s1", "cortex_write_log", "2026-07-06T10:01:00+00:00"),
        _ev("s2", "cortex_fetch_doc", "2026-07-06T11:00:00+00:00"),  # no search, no closeout
    ]
    d = oc.digest(oc.records_from_events(events))
    assert d["sessions"] == 2
    assert d["closeout_coverage_rate"] == 0.5
    assert d["brain_first_rate"] == 0.5
    assert "s2" in d["no_closeout_sessions"]
    assert "s2" in d["wrote_before_search_sessions"]


def test_ingest_writes_to_zone_l_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    (tmp_path / "audit").mkdir()
    logs = tmp_path / "logs"; logs.mkdir()
    import json
    with (logs / "mcp-events.jsonl").open("w", encoding="utf-8") as fh:
        for e in [_ev("s1", "cortex_register", "2026-07-06T10:00:00+00:00"),
                  _ev("s1", "cortex_write_log", "2026-07-06T10:01:00+00:00")]:
            fh.write(json.dumps(e) + "\n")
    n = oc.ingest(tmp_path)
    assert n == 1
    store = tmp_path / "ops-local" / "telemetry" / "session_records.jsonl"
    assert store.exists() and len(store.read_text(encoding="utf-8").splitlines()) == 1
    assert oc.ingest(tmp_path) == 0  # idempotent: session already recorded
