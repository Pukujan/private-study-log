"""Smoke-test the SWE-bench Multilingual import script's output.

Run: pytest tests/test_swebench_multilingual_import.py -v

Note: the SWE-bench importers are IMPORT + STRUCTURE only — execution is
deferred to a Docker sandbox (gravebuster). So these tests validate the
importer's logic, the hard_gold schema, and the on-disk output files from a
prior run, not patch execution (unlike test_ds1000_import.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from evals.hf_datasets.import_swebench_multilingual import (
    _parse_test_ids, OUT, run,
)

# ── required keys on every hard_gold record ──────────────────────────────
HARD_GOLD_KEYS = [
    "problem",
    "expected_output",
    "validation",
    "objective_verdict",
    "label_authority",
    "source",
    "task_id",
    "provenance_tier",
    "candidate_origin",
]


# ── unit tests: pure logic (no network) ───────────────────────────────────

class TestParseTestIds:
    def _j(self, v):
        return json.dumps(v)

    def test_none(self):
        assert _parse_test_ids(None) == []

    def test_list_passthrough(self):
        assert _parse_test_ids(["a", "b"]) == ["a", "b"]

    def test_json_string_list(self):
        assert _parse_test_ids(self._j(["x", "y"])) == ["x", "y"]

    def test_json_string_non_list(self):
        assert _parse_test_ids(self._j({"k": "v"})) == []

    def test_garbage(self):
        assert _parse_test_ids("not json") == []


# ── integration: requires HF datasets lib + network ──────────────────────

class TestImport:
    """Run a tiny live import and validate schema."""
    @pytest.fixture(autouse=True)
    def _need_datasets(self):
        pytest.importorskip("datasets")

    def test_smoke_import_limit_3(self, tmp_path, monkeypatch):
        # redirect OUT to a tmp dir so we don't clobber the committed run
        monkeypatch.setattr(
            "evals.hf_datasets.import_swebench_multilingual.OUT", tmp_path)
        m = run(limit=3, split="test")
        assert m["hard_gold"] == 3
        assert m["quarantine"] == 0
        assert m["source"] == "SWE-bench/SWE-bench_Multilingual"
        assert "test" in m["repo_breakdown"] or m["hard_gold"] > 0
        rows = [json.loads(L) for L in
                (tmp_path / "hard_gold.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 3
        for r in rows:
            for k in HARD_GOLD_KEYS:
                assert k in r, f"row missing {k}"
            assert r["provenance_tier"] == "hard_gold"
            assert r["objective_verdict"] == "gold_reference_unexecuted"
            assert r["execution_status"] == "deferred_docker_sandbox"
            assert r["problem"]["prompt"].strip()
            assert r["expected_output"].strip()
            assert r["validation"]["test_patch"].strip()
        assert (tmp_path / "run_manifest.json").exists()


# ── output-file tests: validate a prior full run on disk ─────────────────

class TestOutputFiles:
    def test_hard_gold_jsonl(self):
        fp = OUT / "hard_gold.jsonl"
        if not fp.exists():
            pytest.skip("run import_swebench_multilingual.py first")
        rows = [json.loads(L) for L in fp.read_text(encoding="utf-8").splitlines()]
        assert len(rows) >= 300, f"expected 300+ rows, got {len(rows)}"
        for r in rows:
            for k in HARD_GOLD_KEYS:
                assert k in r, f"row missing key {k}"
            assert r["provenance_tier"] == "hard_gold"

    def test_quarantine_jsonl(self):
        fp = OUT / "quarantine.jsonl"
        if not fp.exists():
            pytest.skip("no quarantine")
        rows = [json.loads(L) for L in fp.read_text(encoding="utf-8").splitlines() if L.strip()]
        for r in rows:
            assert "task_id" in r
            assert "reason" in r

    def test_run_manifest(self):
        fp = OUT / "run_manifest.json"
        if not fp.exists():
            pytest.skip("run import_swebench_multilingual.py first")
        m = json.loads(fp.read_text(encoding="utf-8"))
        assert m["source"] == "SWE-bench/SWE-bench_Multilingual"
        assert "hard_gold" in m
        assert "quarantine" in m
        assert m["hard_gold"] >= 300
        assert "repo_breakdown" in m
        assert m["execution"].startswith("deferred")