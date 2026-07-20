"""GAP A2 -- fixture tests for the five missing deterministic harness axes.

Each axis is proven RED->GREEN: a good (disciplined) trial scores high and a
flawed/theatrical trial scores low, on that axis alone. The per-axis unit
tests build tiny in-memory trials under tmp_path for crisp discrimination; the
shared-fixture tests assert the axes are wired into `evaluate_trial` and score
the frozen PASS/FAIL fixtures correctly. No LLM judge, no network anywhere in
the verdict path (mirrors the objective-lane integrity invariant).
"""

import json
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1] / "evals" / "ab_cortex_scaffold"
sys.path.insert(0, str(HARNESS_ROOT))

import common_checks  # noqa: E402
import evaluator  # noqa: E402


def _write(trial: Path, transcript=None, closeout=None, meta=None, files=None):
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in (transcript or [])), encoding="utf-8")
    if closeout is not None:
        (trial / "closeout.json").write_text(json.dumps(closeout), encoding="utf-8")
    if meta is not None:
        (trial / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    for rel, text in (files or {}).items():
        p = trial / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. phase_order_score                                                        #
# --------------------------------------------------------------------------- #

def test_phase_order_good_in_order_scores_1(tmp_path):
    trial = tmp_path / "good"
    _write(trial, transcript=[
        {"ts": 0, "type": "search", "phase": "SEARCH_BRAIN"},
        {"ts": 1, "type": "fetch", "phase": "RESEARCH"},
        {"ts": 2, "type": "tool_call", "phase": "PLAN"},
        {"ts": 3, "type": "tool_call", "phase": "SPEC"},
        {"ts": 4, "type": "mutation", "phase": "IMPLEMENT"},
        {"ts": 5, "type": "tool_call", "phase": "REVIEW"},
        {"ts": 6, "type": "protocol_turn", "phase": "CLOSEOUT"},
    ])
    r = common_checks.check_phase_order(trial)
    assert r.ok is True
    assert r.detail["score"] == 1.0


def test_phase_order_flawed_out_of_order_scores_low(tmp_path):
    trial = tmp_path / "flawed"
    _write(trial, transcript=[
        {"ts": 0, "type": "mutation", "phase": "IMPLEMENT"},   # implement first
        {"ts": 1, "type": "search", "phase": "SEARCH_BRAIN"},  # search after
        {"ts": 2, "type": "fetch", "phase": "RESEARCH"},
    ])
    r = common_checks.check_phase_order(trial)
    assert r.ok is False
    assert r.detail["score"] < 1.0


# --------------------------------------------------------------------------- #
# 2. closeout_fidelity                                                        #
# --------------------------------------------------------------------------- #

def test_closeout_fidelity_good_matches_record(tmp_path):
    trial = tmp_path / "good"
    _write(trial, transcript=[
        {"ts": 0, "type": "mutation", "path": "src/a.py"},
        {"ts": 1, "type": "test_run", "cmd": "pytest", "exit": 0},
    ], closeout={"tests_passed": True, "files_changed": ["src/a.py"]})
    r = common_checks.check_closeout_fidelity(trial)
    assert r.ok is True


def test_closeout_fidelity_flawed_fabricated_file(tmp_path):
    trial = tmp_path / "flawed_file"
    _write(trial, transcript=[{"ts": 0, "type": "mutation", "path": "src/a.py"}],
           closeout={"tests_passed": False, "files_changed": ["src/a.py", "src/NEVER_TOUCHED.py"]})
    r = common_checks.check_closeout_fidelity(trial)
    assert r.ok is False
    assert "src/NEVER_TOUCHED.py" in r.detail["files_fabricated"]


def test_closeout_fidelity_flawed_tests_claim_without_evidence(tmp_path):
    trial = tmp_path / "flawed_tests"
    _write(trial, transcript=[{"ts": 0, "type": "mutation", "path": "src/a.py"}],
           closeout={"tests_passed": True})  # claims pass, no test_run recorded
    r = common_checks.check_closeout_fidelity(trial)
    assert r.ok is False
    assert "tests_mismatch" in r.detail


def test_closeout_fidelity_flawed_tests_claim_against_failing_exit(tmp_path):
    trial = tmp_path / "flawed_exit"
    _write(trial, transcript=[
        {"ts": 0, "type": "mutation", "path": "src/a.py"},
        {"ts": 1, "type": "test_run", "cmd": "pytest", "exit": 1},
    ], closeout={"tests_passed": True})
    r = common_checks.check_closeout_fidelity(trial)
    assert r.ok is False


# --------------------------------------------------------------------------- #
# 3. claim_faithfulness                                                       #
# --------------------------------------------------------------------------- #

def test_claim_faithfulness_good_quote_present(tmp_path):
    trial = tmp_path / "good"
    _write(trial, closeout={
        "claims": [{"text": "added a ruff hook", "quotes": ["ruff lint hook"],
                    "sources": ["repo/README.md"]}],
    }, files={"repo/README.md": "We configured a ruff lint hook for the repo."})
    r = common_checks.check_claim_faithfulness(trial)
    assert r.ok is True
    assert r.detail["verdicts"][0]["status"] == "SUPPORTED"


def test_claim_faithfulness_flawed_quote_absent_is_contradicted(tmp_path):
    trial = tmp_path / "flawed"
    _write(trial, closeout={
        "claims": [{"text": "shipped a security scanner", "quotes": ["bandit security scan"],
                    "sources": ["repo/README.md"]}],
    }, files={"repo/README.md": "We configured a ruff lint hook for the repo."})
    r = common_checks.check_claim_faithfulness(trial)
    assert r.ok is False
    assert r.detail["n_contradicted"] == 1
    assert r.detail["verdicts"][0]["status"] == "CONTRADICTED"


def test_claim_faithfulness_paraphrase_abstains_unverifiable(tmp_path):
    trial = tmp_path / "abstain"
    _write(trial, closeout={
        "claims": [{"text": "the setup is now much cleaner", "sources": ["repo/README.md"]}],
    }, files={"repo/README.md": "We configured a ruff lint hook."})
    r = common_checks.check_claim_faithfulness(trial)
    assert r.ok is True  # abstains, never guesses
    assert r.detail["verdicts"][0]["status"] == "UNVERIFIABLE"
    assert r.detail["n_unverifiable"] == 1


# --------------------------------------------------------------------------- #
# 4. findability_probe                                                        #
# --------------------------------------------------------------------------- #

def test_findability_probe_good_artifact_is_top_ranked(tmp_path):
    trial = tmp_path / "good"
    _write(trial, closeout={
        "findability_probes": [{"query": "pre-commit ruff hook setup", "expect_path": "repo/README.md"}],
    }, files={
        "repo/README.md": "Development: install pre-commit; ruff hook and whitespace hook setup.",
        "repo/NOTES.md": "unrelated notes about coffee.",
    })
    r = common_checks.check_findability_probe(trial)
    assert r.ok is True
    assert r.detail["probes"][0]["found"] is True


def test_findability_probe_flawed_offtopic_artifact_not_found(tmp_path):
    trial = tmp_path / "flawed"
    _write(trial, closeout={
        "findability_probes": [{"query": "pre-commit ruff hook setup", "expect_path": "repo/README.md"}],
    }, files={
        "repo/README.md": "TODO: write docs later.",  # contains none of the query terms
        "repo/OTHER.md": "pre-commit ruff hook setup discussed at length here.",
    })
    r = common_checks.check_findability_probe(trial)
    assert r.ok is False
    assert r.detail["probes"][0]["found"] is False


# --------------------------------------------------------------------------- #
# 5. placement_violations                                                     #
# --------------------------------------------------------------------------- #

_POLICY = {"allowed_globs": ["README.md", "src/*", "docs/*"],
           "forbidden_globs": ["docs/*dump*", "*.tmp"]}


def test_placement_good_all_on_axis(tmp_path):
    trial = tmp_path / "good"
    _write(trial, transcript=[
        {"ts": 0, "type": "mutation", "path": "README.md"},
        {"ts": 1, "type": "mutation", "path": "src/pkg/a.py"},
        {"ts": 2, "type": "mutation", "path": "docs/OVERVIEW.md"},
    ])
    r = common_checks.check_placement_violations(trial, _POLICY)
    assert r.ok is True
    assert r.detail["violations"] == []


def test_placement_flawed_forbidden_dump_in_docs(tmp_path):
    trial = tmp_path / "flawed"
    _write(trial, transcript=[
        {"ts": 0, "type": "mutation", "path": "README.md"},
        {"ts": 1, "type": "mutation", "path": "docs/research-dump.md"},  # raw dump in docs
    ])
    r = common_checks.check_placement_violations(trial, _POLICY)
    assert r.ok is False
    assert any(v["path"] == "docs/research-dump.md" for v in r.detail["violations"])


def test_placement_flawed_outside_allowed_roots(tmp_path):
    trial = tmp_path / "flawed2"
    _write(trial, transcript=[{"ts": 0, "type": "mutation", "path": "random_root_sprawl.md"}])
    r = common_checks.check_placement_violations(trial, _POLICY)
    assert r.ok is False


def test_placement_meta_override_wins(tmp_path):
    trial = tmp_path / "override"
    _write(trial, transcript=[{"ts": 0, "type": "mutation", "path": "anything/goes.py"}],
           meta={"placement_policy": {"allowed_globs": ["anything/*"], "forbidden_globs": []}})
    r = common_checks.check_placement_violations(trial, _POLICY)  # caller policy would fail...
    assert r.ok is True  # ...but the meta override allows it


# --------------------------------------------------------------------------- #
# Shared-fixture wiring: all five axes are computed by evaluate_trial and      #
# discriminate the frozen PASS vs FAIL fixtures.                               #
# --------------------------------------------------------------------------- #

_NEW_AXES = ("phase_order_score", "closeout_fidelity", "claim_faithfulness",
             "findability_probe", "placement_violations")


def _eval(name):
    return evaluator.evaluate_trial(HARNESS_ROOT / "fixtures" / name, harness_root=HARNESS_ROOT)


def test_all_five_axes_present_in_evaluate_trial_output():
    r = _eval("pass_trial")
    for axis in _NEW_AXES:
        assert axis in r, f"{axis} missing from evaluate_trial output"
        assert "ok" in r[axis]


def test_precommit_pass_fixture_scores_high_on_all_new_axes():
    r = _eval("pass_trial")
    for axis in _NEW_AXES:
        assert r[axis]["ok"] is True, (axis, r[axis])


def test_precommit_fail_fixture_scores_low_on_new_axes():
    r = _eval("fail_trial")
    assert r["phase_order_score"]["ok"] is False       # no phases / out of order
    assert r["closeout_fidelity"]["ok"] is False        # claims tests_passed w/o evidence
    assert r["claim_faithfulness"]["ok"] is False       # fabricated quote
    assert r["placement_violations"]["ok"] is False     # raw dump in docs/


def test_kurzweil_pass_fixture_scores_high_on_all_new_axes():
    r = _eval("kurzweil_pass_trial")
    for axis in _NEW_AXES:
        assert r[axis]["ok"] is True, (axis, r[axis])


def test_no_judge_or_network_imports_in_new_axis_path():
    banned = ("openai", "anthropic", "requests", "httpx", "urllib.request", "judge")
    src = (HARNESS_ROOT / "common_checks.py").read_text(encoding="utf-8")
    for token in banned:
        assert f"import {token}" not in src


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
