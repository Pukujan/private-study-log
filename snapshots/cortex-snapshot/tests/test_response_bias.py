from __future__ import annotations

import json

from cortex_core import response_bias as rb


def _jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_scans_claude_assistant_messages_only(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _jsonl(transcript, [
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "weak model?"}]},
            "sessionId": "s1",
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "sessionId": "s1",
            "timestamp": "2026-07-08T00:00:00Z",
            "slug": "plan",
            "message": {
                "role": "assistant",
                "model": "claude-opus",
                "content": [{"type": "text", "text": "This is a weak model in a Claude-shaped harness."}],
            },
        },
    ])

    scan = rb.scan_roots([tmp_path])
    assert scan.files_scanned == 1
    assert scan.assistant_messages_scanned == 1
    assert scan.flagged_messages == 1
    assert scan.by_category["model_blame"] == 1
    assert scan.by_category["claude_centric"] == 1
    assert {h.model for h in scan.hits} == {"claude-opus"}
    assert all("weak model?" not in h.snippet for h in scan.hits)  # user text ignored


def test_scans_hermes_raw_transcript_shape(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    _jsonl(transcript, [
        {"turn": 0, "raw": "The circular validation proves the workflow is fine.", "tool": "done"},
        {"turn": 1, "raw": "", "tool": None},
    ])

    scan = rb.scan_roots([transcript])
    assert scan.assistant_messages_scanned == 1
    assert scan.by_category["circular_validation"] == 1
    assert scan.by_category["over_certainty"] == 1


def test_prometheus_render_omits_raw_snippets_but_counts_hits(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _jsonl(transcript, [{
        "type": "assistant",
        "uuid": "a1",
        "sessionId": "s1",
        "message": {
            "role": "assistant",
            "model": "claude",
            "content": [{"type": "text", "text": "SLSA provenance drift is clearly relevant."}],
        },
    }])

    text = rb.render_prometheus(rb.scan_roots([tmp_path]))
    assert "claude_bias_signal_hits_total" in text
    assert 'category="governance_drift"' in text
    assert 'category="over_certainty"' in text
    assert "SLSA provenance drift" not in text


def test_mcp_tool_wrapper_returns_report(tmp_path):
    from cortex_core.response_bias_mcp import claude_bias_scan

    transcript = tmp_path / "session.jsonl"
    _jsonl(transcript, [{
        "type": "assistant",
        "uuid": "a1",
        "sessionId": "s1",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Evidence theater."}]},
    }])
    report = claude_bias_scan(str(tmp_path), limit=5)
    assert report["hits_total"] == 1
    assert report["hits"][0]["category"] == "governance_drift"
