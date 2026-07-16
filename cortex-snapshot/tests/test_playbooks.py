"""Browser learning-loop playbooks (design doc Part 3;
docs/research/BROWSER-LEARNING-LOOP-2026-07-07.md).

Covers: lookup on a known/unknown site, the confidence/corroboration success
path, the failure-decay -> quarantine path, the corroboration gate on an
AI-proposed locator, CSS-locator rejection, credential redaction, and (via the
MCP tool) that a report writes a REAL closeout with a handoff field.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import cortex_core.mcp as mcp_mod
from cortex_core import playbooks as pb
from cortex_core.mcp import cortex_playbook_lookup, cortex_playbook_report


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return ws


# --------------------------- normalization + lookup --------------------------- #
def test_normalize_site_id_strips_scheme_www_port_path():
    assert pb.normalize_site_id("https://www.LinkedIn.com:443/feed/x") == "linkedin.com"
    assert pb.normalize_site_id("example.com/path") == "example.com"
    assert pb.normalize_site_id("Example.COM") == "example.com"


def test_lookup_unknown_site_returns_explore_response(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    result = pb.lookup("https://never-seen.example", ws)
    assert result["exists"] is False
    assert "explore" in result["guidance"].lower()


def test_lookup_known_site_returns_real_data(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    p = pb.Playbook(
        site_id="github.com",
        confidence=0.7,
        key_locators=[{"intent": "search box", "role": "searchbox", "name": "Search",
                       "anchors": ["top nav"], "visual_fallback": "magnifier icon top-right"}],
        known_pitfalls=["command palette can steal focus"],
        verification_check={"role": "heading", "name": "Repositories", "negative_signal": "Rate limit"},
    )
    pb.save_playbook(p, ws)
    result = pb.lookup("https://github.com/foo/bar", ws)
    assert result["exists"] is True
    assert result["confidence"] == 0.7
    assert result["key_locators"][0]["role"] == "searchbox"
    assert result["verification_check"]["name"] == "Repositories"
    assert "command palette can steal focus" in result["known_pitfalls"]


# --------------------------- validation (no CSS) ------------------------------ #
def test_validate_rejects_raw_css_locator():
    p = pb.Playbook(site_id="x.com",
                    key_locators=[{"intent": "login", "role": "", "name": "", "css": "#login > .btn"}])
    ok, errors = pb.validate_playbook(p)
    assert not ok
    assert any("css" in e.lower() for e in errors)


def test_validate_accepts_intent_locator():
    p = pb.Playbook(site_id="x.com",
                    key_locators=[{"intent": "login", "role": "button", "name": "Sign in"}])
    ok, errors = pb.validate_playbook(p)
    assert ok, errors


# --------------------------- learning loop: success --------------------------- #
def test_reported_success_raises_confidence_and_corroboration(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _, s1 = pb.apply_report("acme.test", "click Sign in", "role", "success",
                            verification_result="pass", workspace=ws)
    assert s1["created"] is True
    assert s1["success_recorded"] is True
    # confidence rose from INITIAL by SUCCESS_INCREMENT
    assert s1["confidence"] == round(pb.INITIAL_CONFIDENCE + pb.SUCCESS_INCREMENT, 4)
    _, s2 = pb.apply_report("acme.test", "click Sign in", "role", "success",
                            verification_result="pass", workspace=ws)
    assert s2["confidence"] > s1["confidence"]


def test_verification_result_overrides_self_reported_outcome(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    # agent claims success, but the oracle says fail -> treated as failure
    _, s = pb.apply_report("acme.test", "x", "role", "success",
                           verification_result="fail", workspace=ws)
    assert s["success_recorded"] is False
    assert s["confidence"] < pb.INITIAL_CONFIDENCE


# --------------------------- learning loop: failure -> quarantine ------------- #
def test_repeated_failure_decays_and_quarantines(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    last = None
    for _ in range(pb.QUARANTINE_FAILURE_STREAK):
        _, last = pb.apply_report("flaky.test", "click", "role", "failure",
                                  verification_result="fail", workspace=ws)
    assert last["status"] == pb.STATUS_QUARANTINED
    assert last["needs_exploration"] is True
    assert last["confidence"] < pb.INITIAL_CONFIDENCE


def test_single_failure_degrades_before_quarantine(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    # seed a healthy playbook so one failure degrades (not immediately quarantines)
    p = pb.Playbook(site_id="ok.test", confidence=0.7, status=pb.STATUS_ACTIVE)
    pb.save_playbook(p, ws)
    _, s = pb.apply_report("ok.test", "click", "role", "failure",
                           verification_result="fail", workspace=ws)
    assert s["confidence"] == round(0.7 * pb.FAILURE_DECAY, 4)  # 0.35
    assert s["status"] == pb.STATUS_DEGRADED


# --------------------------- corroboration gate ------------------------------- #
def test_new_locator_stays_uncorroborated_until_second_success(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    loc = {"intent": "submit", "role": "button", "name": "Post", "anchors": ["composer"]}
    p1, s1 = pb.apply_report("social.test", "self-heal to new submit button", "text",
                             "success", verification_result="pass", new_locator=loc, workspace=ws)
    # version bumped, locator present but NOT yet corroborated, confidence capped
    assert s1["playbook_version"] == 2
    assert p1.key_locators[0]["corroborated"] is False
    assert p1.pending_corroboration is True
    assert p1.confidence <= pb.UNCORROBORATED_CONFIDENCE_CEILING
    # a second success corroborates it
    p2, s2 = pb.apply_report("social.test", "reuse submit button", "role",
                             "success", verification_result="pass", workspace=ws)
    assert p2.pending_corroboration is False
    assert p2.key_locators[0]["corroborated"] is True
    assert s2["corroboration_count"] >= pb.CORROBORATION_MIN


# --------------------------- redaction ---------------------------------------- #
def test_redaction_scrubs_credential_shaped_text():
    dirty = ("logged in with Authorization: Bearer abcdef1234567890 and "
             "cookie=deadbeefdeadbeefdeadbeefdeadbeef01 token=supersecretvalue "
             "jwt eyJhbGciOi.eyJzdWIiOi.SflKxwRJSM")
    clean, hit = pb.redact(dirty)
    assert hit is True
    assert "abcdef1234567890" not in clean
    assert "supersecretvalue" not in clean
    assert "eyJhbGciOi.eyJzdWIiOi.SflKxwRJSM" not in clean
    assert "REDACTED" in clean


def test_redact_obj_is_deep():
    obj = {"note": "password=hunter2secret", "list": ["cookie=abcdef123456"]}
    clean, hit = pb.redact_obj(obj)
    assert hit is True
    assert "hunter2secret" not in json.dumps(clean)
    assert "abcdef123456" not in json.dumps(clean)


# --------------------------- MCP tool: real closeout + redaction -------------- #
def _make_mcp_ws(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(ws))
    return ws


def test_mcp_lookup_unknown_then_report_writes_real_closeout(tmp_path, monkeypatch):
    ws = _make_mcp_ws(tmp_path, monkeypatch)
    # lookup unknown -> explore response
    look = asyncio.run(cortex_playbook_lookup(site="https://example.test"))
    assert look["exists"] is False

    rep = asyncio.run(cortex_playbook_report(
        site="https://example.test",
        action_taken="click the Accept button",
        locator_strategy_used="role",
        outcome="success",
        verification_result="pass",
        verification_check={"role": "heading", "name": "Dashboard"},
    ))
    # a real closeout file exists on disk, with a handoff field
    closeout = Path(rep["closeout_path"])
    assert closeout.is_file()
    data = json.loads(closeout.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["handoff"]["locations"], "closeout must carry a real handoff.locations"
    assert data["handoff"]["continuation"]
    # and the playbook was created + persisted
    assert Path(rep["playbook_path"]).is_file()
    assert rep["created"] is True


def test_mcp_report_redacts_credentials_before_logging(tmp_path, monkeypatch):
    ws = _make_mcp_ws(tmp_path, monkeypatch)
    rep = asyncio.run(cortex_playbook_report(
        site="creds.test",
        action_taken="logged in; server set cookie=deadbeefdeadbeefdeadbeefdeadbeef11",
        locator_strategy_used="role",
        outcome="success",
        verification_result="pass",
        auth_note="Authorization: Bearer sk-livesecrettoken12345",
    ))
    assert rep["redacted"] is True
    # the raw secrets must appear nowhere in the written closeout or playbook
    closeout_text = Path(rep["closeout_path"]).read_text(encoding="utf-8")
    playbook_text = Path(rep["playbook_path"]).read_text(encoding="utf-8")
    for secret in ("deadbeefdeadbeefdeadbeefdeadbeef11", "sk-livesecrettoken12345"):
        assert secret not in closeout_text
        assert secret not in playbook_text
