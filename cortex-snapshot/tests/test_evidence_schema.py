"""RED-first tests for the universal run-evidence schema (GAP-CLOSURE J2).

SPEC: evals/RESULTS-LEDGER-SPEC.md (EvidenceBundle section). Written before
the code is finalized; they pin the anti-circular guard (oracle_version +
oracle_fixture_sha256 REQUIRED), the builder's git-commit fill, and the
forward-gating CI check.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cortex_core import evidence_schema as es
from cortex_core import results_ledger as rl

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _valid_bundle(**over) -> dict:
    b = {
        "evidence_schema_version": es.EVIDENCE_SCHEMA_VERSION,
        "trace_id": "run-0001",
        "model": "qwen35b",
        "model_exact_id": "qwen3-4b-2507",
        "git_commit": "41271f3",
        "dataset_version": "objective_tool_calling@2026.3.23",
        "holdout_version": "holdout-v1",
        "oracle_version": "bfcl_ast_checker@2026.3.23",
        "oracle_fixture_sha256": _SHA_A,
        "artifact_shas": [_SHA_B],
        "verdict": "pass",
        "abstained": False,
        "ts": "2026-07-14T00:00:00Z",
    }
    b.update(over)
    return b


# --- validation: a complete bundle validates ---

def test_complete_bundle_validates():
    ok, problems = es.validate_evidence_bundle(_valid_bundle())
    assert ok, problems
    assert problems == []


def test_optional_fields_allowed():
    ok, problems = es.validate_evidence_bundle(
        _valid_bundle(image_digest="sha256:deadbeef", confidence=0.87)
    )
    assert ok, problems


def test_confidence_and_image_digest_may_be_null():
    ok, problems = es.validate_evidence_bundle(
        _valid_bundle(image_digest=None, confidence=None)
    )
    assert ok, problems


# --- anti-circular guard: oracle binding is REQUIRED ---

def test_missing_oracle_fixture_sha256_fails():
    b = _valid_bundle()
    del b["oracle_fixture_sha256"]
    ok, problems = es.validate_evidence_bundle(b)
    assert not ok
    assert any("oracle_fixture_sha256" in p for p in problems)


def test_missing_oracle_version_fails():
    b = _valid_bundle()
    del b["oracle_version"]
    ok, problems = es.validate_evidence_bundle(b)
    assert not ok
    assert any("oracle_version" in p for p in problems)


def test_placeholder_oracle_fixture_sha_rejected():
    # anti-circular: a non-sha placeholder cannot bind a verdict to an instrument
    ok, problems = es.validate_evidence_bundle(_valid_bundle(oracle_fixture_sha256="TODO"))
    assert not ok
    assert any("oracle_fixture_sha256" in p for p in problems)


# --- other field validation ---

@pytest.mark.parametrize("field", list(es.REQUIRED_FIELDS))
def test_every_required_field_is_required(field):
    b = _valid_bundle()
    del b[field]
    ok, _ = es.validate_evidence_bundle(b)
    assert not ok


def test_unknown_field_rejected():
    ok, problems = es.validate_evidence_bundle(_valid_bundle(surprise=1))
    assert not ok
    assert any("surprise" in p for p in problems)


def test_abstained_must_be_bool():
    ok, _ = es.validate_evidence_bundle(_valid_bundle(abstained="yes"))
    assert not ok


def test_bad_ts_rejected():
    ok, _ = es.validate_evidence_bundle(_valid_bundle(ts="yesterday"))
    assert not ok


def test_artifact_shas_must_be_sha_list():
    ok, _ = es.validate_evidence_bundle(_valid_bundle(artifact_shas=["not-a-sha"]))
    assert not ok


def test_confidence_out_of_range_rejected():
    ok, _ = es.validate_evidence_bundle(_valid_bundle(confidence=1.5))
    assert not ok


def test_non_dict_rejected():
    ok, problems = es.validate_evidence_bundle(["not", "a", "dict"])
    assert not ok and problems


# --- builder fills git_commit + ts ---

def test_builder_fills_git_commit():
    b = es.build_evidence_bundle(
        trace_id="run-x", model="qwen35b", model_exact_id="qwen3-4b",
        dataset_version="d@1", holdout_version="h@1",
        oracle_version="checker@1", oracle_fixture_sha256=_SHA_A,
        verdict="pass",
    )
    # a real sha (this IS a git repo) or the honest 'unknown' sentinel
    real_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(_REPO_ROOT),
        capture_output=True, text=True,
    ).stdout.strip()
    assert b["git_commit"] == real_head
    assert b["git_commit"] and b["git_commit"] != "unknown"
    # ts auto-filled and schema-shaped
    assert es._TS_RE.match(b["ts"])
    ok, problems = es.validate_evidence_bundle(b)
    assert ok, problems


def test_builder_rejects_missing_oracle_binding():
    # oracle_fixture_sha256 is a required kwarg; a placeholder must not build
    with pytest.raises(ValueError):
        es.build_evidence_bundle(
            trace_id="run-x", model="m", model_exact_id="m1",
            dataset_version="d", holdout_version="h",
            oracle_version="o", oracle_fixture_sha256="TODO", verdict="pass",
        )


# --- results_ledger wiring ---

def test_ledger_accepts_valid_evidence(tmp_path):
    led = tmp_path / "results.jsonl"
    row = {
        "run_id": "obj.tool.demo", "ts": "2026-07-14T00:00:00Z",
        "lane": "objective_tool_calling", "metric": "cross_val_agreement",
        "value": 0.9993, "n": 3045, "decision": "SHIPPED",
        "source_file": "evals/objective_tool_calling/hard_gold.jsonl",
        "commit": "41271f3", "provenance": "committed-artifact",
        "evidence": _valid_bundle(),
    }
    assert rl.append_result(row, ledger_path=led) is True
    loaded = rl.load_results(ledger_path=led)
    assert loaded[0]["evidence"]["oracle_fixture_sha256"] == _SHA_A


def test_ledger_rejects_invalid_evidence(tmp_path):
    led = tmp_path / "results.jsonl"
    bad = _valid_bundle()
    del bad["oracle_fixture_sha256"]
    row = {
        "run_id": "obj.tool.bad", "ts": "2026-07-14T00:00:00Z",
        "lane": "x", "metric": "m", "value": 1, "n": 1, "decision": "MEASURED",
        "source_file": "x", "commit": None, "provenance": "recomputed",
        "evidence": bad,
    }
    with pytest.raises(ValueError):
        rl.append_result(row, ledger_path=led)


# --- CI check: passes on a good row, fails on a tagged row missing the bundle ---

def _run_ci(*files) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "ci" / "check_evidence_bundles.py"),
         "--json", *[str(f) for f in files]],
        capture_output=True, text=True,
    )


def test_ci_passes_on_good_row(tmp_path):
    f = tmp_path / "results.jsonl"
    row = {"run_id": "r1", "evidence": _valid_bundle()}
    f.write_text(json.dumps(row) + "\n", encoding="utf-8")
    cp = _run_ci(f)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    report = json.loads(cp.stdout)
    assert report["total_scoped"] == 1
    assert report["total_problems"] == 0


def test_ci_fails_on_tagged_row_missing_bundle(tmp_path):
    # a row that OPTED IN (carries an evidence object at floor version) but whose
    # bundle is incomplete must fail -- forward-gating with teeth.
    f = tmp_path / "results.jsonl"
    bad = _valid_bundle()
    del bad["oracle_fixture_sha256"]
    f.write_text(json.dumps({"run_id": "r1", "evidence": bad}) + "\n", encoding="utf-8")
    cp = _run_ci(f)
    assert cp.returncode == 1
    report = json.loads(cp.stdout)
    assert report["total_problems"] >= 1


def test_ci_exempts_and_counts_legacy_rows(tmp_path):
    # legacy rows (no evidence object) are exempt AND counted, never silently skipped
    f = tmp_path / "results.jsonl"
    lines = [
        json.dumps({"run_id": "legacy1", "value": 1}),
        json.dumps({"run_id": "legacy2", "value": 2}),
        json.dumps({"run_id": "new1", "evidence": _valid_bundle()}),
    ]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cp = _run_ci(f)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    report = json.loads(cp.stdout)
    assert report["total_exempt"] == 2
    assert report["total_scoped"] == 1
