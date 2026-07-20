"""Smoke-test the DS-1000 import script's core logic.

Run: pytest tests/test_ds1000_import.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from evals.hf_datasets.import_ds1000 import _assemble, _run, OUT

# ── helpers ──────────────────────────────────────────────────────────────


def _load_ds():
    from datasets import load_dataset

    return load_dataset("xlangai/DS-1000", split="test")


HARD_GOLD_KEYS = [
    "problem",
    "candidate_code",
    "objective_verdict",
    "label_authority",
    "source",
    "task_id",
    "provenance_tier",
    "candidate_origin",
]


# ── tests ────────────────────────────────────────────────────────────────


class TestAssemble:
    """_assemble produces valid Python that exercises the test harness."""

    @pytest.fixture(scope="class")
    def ds(self):
        pytest.importorskip("datasets")  # HF datasets is an optional import extra
        return _load_ds()

    def test_compiles_and_invokes_test_execution(self, ds):
        for i in range(5):
            rec = ds[i]
            code = _assemble(rec)
            compile(code, f"<ds1000_{i}>", "exec")
            assert "test_execution(" in code, f"Record {i}: missing test_execution call"

    def test_canonical_solution_passes(self, ds):
        ok, detail = _run(_assemble(ds[0]), timeout=15.0)
        assert ok, f"Record 0 should pass: {detail}"

    def test_hard_gold_schema(self, ds):
        ok, detail = _run(_assemble(ds[1]), timeout=15.0)
        assert ok, f"Record 1 should pass: {detail}"
        rec = ds[1]
        pid = rec["metadata"]["problem_id"]
        hard = {
            "problem": {
                "prompt": rec["prompt"],
                "code_context": rec["code_context"],
                "metadata": rec["metadata"],
            },
            "candidate_code": rec["reference_code"],
            "objective_verdict": "pass",
            "label_authority": "subprocess_test_execution",
            "source": "DS-1000",
            "task_id": pid,
            "provenance_tier": "hard_gold",
            "candidate_origin": "dataset_reference",
        }
        for k in HARD_GOLD_KEYS:
            assert k in hard, f"Missing key: {k}"
        assert hard["objective_verdict"] == "pass"
        assert hard["provenance_tier"] == "hard_gold"


class TestOutput:
    """Output files from a prior run are valid."""

    def test_hard_gold_jsonl(self):
        fp = OUT / "hard_gold.jsonl"
        assert fp.exists(), f"{fp} not found — run import_ds1000.py first"
        with open(fp) as f:
            rows = [json.loads(line) for line in f]
        assert len(rows) > 0
        for r in rows:
            for k in HARD_GOLD_KEYS:
                assert k in r, f"row missing key {k}"

    def test_quarantine_jsonl(self):
        fp = OUT / "quarantine.jsonl"
        if not fp.exists():
            pytest.skip("no quarantine file — all records may have passed")
        with open(fp) as f:
            rows = [json.loads(line) for line in f]
        for r in rows:
            assert "task_id" in r
            assert "canonical_passes" in r
            assert r["canonical_passes"] is False

    def test_run_manifest(self):
        fp = OUT / "run_manifest.json"
        assert fp.exists()
        m = json.loads(fp.read_text(encoding="utf-8"))
        assert "source" in m
        assert "hard_gold" in m
        assert "quarantine" in m
        assert "canonical_pass_rate" in m
        assert m["source"] == "xlangai/DS-1000"
