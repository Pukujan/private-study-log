from __future__ import annotations

import json

from ops import hermes_prometheus_exporter as exp


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_analyze_run_derives_tool_categories_contract_modes_and_edit_status(tmp_path):
    run = tmp_path / "bench" / "task07_postfix_abcdef1_ninerouter-aux"
    run.mkdir(parents=True)
    (run / "index.html").write_text("<h1>done</h1>", encoding="utf-8")
    (run / "result_summary.json").write_text(json.dumps({
        "task_id": "task07",
        "closed_out": True,
        "finished": True,
        "elapsed_s": 12.5,
    }), encoding="utf-8")
    _write_jsonl(run / "transcript.jsonl", [
        {"turn": 0, "tool": "cortex_search", "payload": {"query": "x"}},
        {"turn": 1, "tool": None, "payload": {}},
        {"turn": 2, "tool": "cortex_contract", "payload": {"task": "t"}},
        {"turn": 3, "tool": "cortex_contract", "payload": {
            "task": "t",
            "planned_approach": "build",
            "acceptance_criteria": ["index.html exists"],
            "verification_steps": ["inspect"],
        }},
        {"turn": 4, "tool": "write_file", "payload": {
            "path": "index.html",
            "content": "<!-- SECTION:hero:PENDING -->\n<!-- SECTION:footer:PENDING -->",
        }},
        {"turn": 5, "tool": "read_file", "payload": {"path": "index.html", "offset": 0}},
        {"turn": 6, "tool": "read_file", "payload": {"path": "index.html", "offset": 0}},
        {"turn": 7, "tool": "edit_file", "payload": {
            "path": "index.html",
            "find": "<!-- SECTION:hero:PENDING -->",
            "replace": "<section>Hero</section><!-- SECTION:hero:DONE -->",
        }},
        {"turn": 8, "tool": "read_file", "payload": {"path": "index.html", "offset": 4000}},
        {"turn": 9, "tool": "edit_file", "payload": {
            "path": "index.html",
            "find": "<!-- SECTION:missing:PENDING -->",
            "replace": "x",
        }},
        {"turn": 10, "tool": "cortex_report_empty_output", "payload": {"task_id": "t_1"}},
        {"turn": 11, "tool": "cortex_write_log", "payload": {"task": "t", "result": "done"}},
    ])

    metrics = exp.analyze_run(run)
    assert metrics.transcript_turns == 12
    assert metrics.null_tool_turns == 1
    assert metrics.closed_out is True
    assert metrics.finished is True
    assert metrics.index_html_present is True
    assert metrics.labels["model"] == "ninerouter-aux"
    assert metrics.labels["harness_version"] == "abcdef1"
    assert metrics.category_counts["productive"] == 3
    assert metrics.category_counts["orientation"] == 4
    assert metrics.category_counts["ceremony"] == 3
    assert metrics.category_counts["phase_runtime"] == 1
    assert metrics.contract_modes == {"prefill": 1, "submit": 1}
    assert metrics.read_offset_calls == 1
    assert metrics.read_duplicate_range_calls == 1
    assert metrics.edit_statuses["applied"] == 1
    assert metrics.edit_statuses["refused_zero_match"] == 1
    assert metrics.read_edit_ratio == 1.5


def test_render_prometheus_exposes_expected_series(tmp_path):
    run = tmp_path / "bench" / "task15"
    run.mkdir(parents=True)
    (run / "result_summary.json").write_text(json.dumps({
        "task_id": "task15",
        "tier": "glm-5.2",
        "closed_out": False,
    }), encoding="utf-8")
    _write_jsonl(run / "transcript.jsonl", [
        {"turn": 0, "tool": "run_shell", "payload": {"cmd": "echo hi"}},
        {"turn": 1, "tool": "done", "payload": {}},
    ])

    text = exp.render_prometheus([exp.analyze_run(run)])
    assert 'hermes_agent_run_info{batch="bench",harness_version="unknown",model="glm-5.2"' in text
    assert 'hermes_agent_tool_calls{batch="bench",category="productive"' in text
    assert 'tool="run_shell"' in text
    assert 'hermes_agent_tool_category_calls{batch="bench",category="ceremony"' in text
    assert 'hermes_agent_tool_category_calls{batch="bench",category="phase_runtime"' in text
    assert 'hermes_agent_closeout_success{batch="bench"' in text
    assert " 0" in text


def test_discover_run_dirs_accepts_root_or_nested_benchmark(tmp_path):
    direct = tmp_path / "direct"
    direct.mkdir()
    (direct / "transcript.jsonl").write_text("", encoding="utf-8")
    nested = tmp_path / "root" / "batch" / "task"
    nested.mkdir(parents=True)
    (nested / "transcript.jsonl").write_text("", encoding="utf-8")
    assert exp.discover_run_dirs([direct]) == [direct]
    assert exp.discover_run_dirs([tmp_path / "root"]) == [nested]
