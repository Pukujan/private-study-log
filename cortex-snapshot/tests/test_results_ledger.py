"""RED-first tests for the committed results ledger (GAP-CLOSURE C1/C2/C3).

SPEC: evals/RESULTS-LEDGER-SPEC.md. These are written BEFORE the code and
must fail (RED) until cortex_core/results_ledger.py exists and is correct.
"""
from __future__ import annotations

import json

import pytest

from cortex_core import results_ledger as rl


def _valid_row(**over) -> dict:
    row = {
        "run_id": "retrieval.ndcg_at_5.demo",
        "ts": "2026-07-13T00:00:00Z",
        "lane": "retrieval",
        "metric": "ndcg_at_5",
        "value": 0.3857,
        "n": 17,
        "decision": "BASELINE",
        "source_file": "cortex_core/graded_eval.py",
        "commit": "1d7a5a0",
        "provenance": "recomputed",
    }
    row.update(over)
    return row


# --- append/load round-trip ---

def test_append_then_load_round_trip(tmp_path):
    led = tmp_path / "results.jsonl"
    row = _valid_row()
    assert rl.append_result(row, ledger_path=led) is True
    loaded = rl.load_results(ledger_path=led)
    assert len(loaded) == 1
    assert loaded[0]["run_id"] == "retrieval.ndcg_at_5.demo"
    assert loaded[0]["value"] == 0.3857
    assert loaded[0]["provenance"] == "recomputed"


def test_load_missing_file_returns_empty(tmp_path):
    assert rl.load_results(ledger_path=tmp_path / "nope.jsonl") == []


def test_append_is_line_atomic_jsonl(tmp_path):
    led = tmp_path / "results.jsonl"
    rl.append_result(_valid_row(run_id="a"), ledger_path=led)
    rl.append_result(_valid_row(run_id="b"), ledger_path=led)
    lines = [l for l in led.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    for l in lines:
        json.loads(l)  # each line is a standalone JSON object


# --- idempotency + no duplicate run_ids ---

def test_idempotent_identical_row_is_noop(tmp_path):
    led = tmp_path / "results.jsonl"
    assert rl.append_result(_valid_row(), ledger_path=led) is True
    assert rl.append_result(_valid_row(), ledger_path=led) is False  # no-op
    assert len(rl.load_results(ledger_path=led)) == 1


def test_duplicate_run_id_different_content_raises(tmp_path):
    led = tmp_path / "results.jsonl"
    rl.append_result(_valid_row(value=0.5), ledger_path=led)
    with pytest.raises(ValueError):
        rl.append_result(_valid_row(value=0.9), ledger_path=led)
    assert len(rl.load_results(ledger_path=led)) == 1


# --- schema validation ---

@pytest.mark.parametrize("missing", ["run_id", "ts", "lane", "metric", "value", "n", "decision", "source_file", "commit", "provenance"])
def test_missing_required_field_raises(tmp_path, missing):
    led = tmp_path / "results.jsonl"
    row = _valid_row()
    del row[missing]
    with pytest.raises(ValueError):
        rl.append_result(row, ledger_path=led)


def test_null_value_rejected(tmp_path):
    led = tmp_path / "results.jsonl"
    with pytest.raises(ValueError):
        rl.append_result(_valid_row(value=None), ledger_path=led)


def test_bad_provenance_rejected(tmp_path):
    led = tmp_path / "results.jsonl"
    with pytest.raises(ValueError):
        rl.append_result(_valid_row(provenance="made-up"), ledger_path=led)


def test_bad_decision_rejected(tmp_path):
    led = tmp_path / "results.jsonl"
    with pytest.raises(ValueError):
        rl.append_result(_valid_row(decision="totally-shipped"), ledger_path=led)


def test_bad_ts_shape_rejected(tmp_path):
    led = tmp_path / "results.jsonl"
    with pytest.raises(ValueError):
        rl.append_result(_valid_row(ts="yesterday"), ledger_path=led)


def test_string_value_allowed_for_two_step(tmp_path):
    led = tmp_path / "results.jsonl"
    assert rl.append_result(
        _valid_row(run_id="r.chunk_recall", metric="chunk_recall_at_5",
                   value="0.467->0.667->0.733", provenance="reconciled"),
        ledger_path=led) is True


def test_n_may_be_null(tmp_path):
    led = tmp_path / "results.jsonl"
    assert rl.append_result(_valid_row(n=None), ledger_path=led) is True


# --- rollup / render_scorecard ---

def test_render_scorecard_contains_known_rows():
    rows = [
        _valid_row(run_id="retrieval.rrf.ndcg", metric="ndcg_at_5",
                   value="0.444->0.654", decision="SHIPPED",
                   source_file="docs/PHASE-GATES.md", provenance="reconciled"),
        _valid_row(run_id="obj.tool.crossval", lane="objective_tool_calling",
                   metric="cross_val_agreement", value=0.9993, n=3045,
                   decision="SHIPPED", provenance="committed-artifact"),
        _valid_row(run_id="retr.rerank", metric="chunk_recall_at_5",
                   value="0.733->0.600", decision="REJECTED", provenance="prose-only"),
    ]
    out = rl.render_scorecard(rows)
    assert isinstance(out, str)
    # decision-log table shows shipped + rejected decisions
    assert "SHIPPED" in out and "REJECTED" in out
    # the actual numbers must appear (re-countable from the ledger)
    assert "0.444->0.654" in out
    assert "0.9993" in out or "0.9993" in out.replace(" ", "")
    # provenance summary surfaces how much is still prose-only
    assert "prose-only" in out
    # lanes/metrics named
    assert "cross_val_agreement" in out
    assert "objective_tool_calling" in out


def test_render_scorecard_empty_rows_is_safe():
    out = rl.render_scorecard([])
    assert isinstance(out, str) and len(out) > 0


def test_default_ledger_is_committed_evals_file():
    # the one committed file the SPEC names
    assert rl.DEFAULT_LEDGER.name == "results.jsonl"
    assert rl.DEFAULT_LEDGER.parent.name == "evals"
