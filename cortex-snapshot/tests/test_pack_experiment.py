"""tests/test_pack_experiment.py — unit tests for the pack-falsification harness.

ALL unit tests use a fake `student_complete` + a spy/fake `gate_runner`. ZERO network,
ZERO real subprocesses, ZERO real LLM calls. Design: BUILD-01 §5.3.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cortex_core.app_contract import CheckResult, GateVerdict, coach_view
from cortex_core import pack_experiment as pe
from cortex_core.pack_experiment import (
    CellResult,
    ExperimentTask,
    PreregError,
    PromptPack,
    aggregate_metrics,
    build_prompt,
    load_prereg,
    preregister,
    prereg_hash,
    record_acceptance,
    retention_verdict,
    run_experiment,
    score_experiment,
    export_blind_review,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
GOOD_SLOT = '{"entity": "client", "fields": [{"name": "name", "type": "text", "required": true}]}'


def _prereg_spec(min_cell_n: int = 20, task_ids=None) -> dict:
    return {
        "schema_version": 1,
        "hypothesis": "same-family beats generic",
        "arms": ["generic", "same_family", "cross_vendor"],
        "student": "qwen35b",
        "gate_version": 1,
        "task_ids": task_ids or ["t1"],
        "criterion": {"min_lift": 0.10, "cost_tolerance": 0.15, "min_cell_n": min_cell_n},
    }


def _packs(n: int = 3, addendum_extra: str = "") -> list[PromptPack]:
    arms = ["generic", "same_family", "cross_vendor"][:n]
    return [PromptPack(pack_id=f"{a}-v1", arm=a,
                       system_addendum=f"coach text for {a}. {addendum_extra}".strip(),
                       retry_hint="Failed with {failure_class}. Emit ONE corrected JSON object.")
            for a in arms]


def _task(task_id: str = "t1", holdout_family: str = "") -> ExperimentTask:
    return ExperimentTask(
        task_id=task_id, utterance=f"build {task_id}", skill_id="scaffold-crud-sqlite",
        checks=[{"kind": "app_starts"}, {"kind": "data_persists", "resource": {
            "create": {"method": "POST", "path": "/x", "form": {"name": "@hidden:tok"}},
            "read_path": "/x", "table": "x", "column": "name"}}],
        holdout_family=holdout_family,
    )


def _verdict(*, overall=True, visible=True, hidden=True, failure_class=None, detail="", seed=0):
    results = (
        CheckResult(kind="app_starts", passed=visible, hidden=False, detail=detail),
        CheckResult(kind="data_persists", passed=hidden, hidden=True, detail=detail),
    )
    return GateVerdict(passed=overall, results=results, failure_class=failure_class,
                       hidden_coverage=True, env_retries=0, seed=seed)


class GateSpy:
    """Records every gate_runner invocation; returns a scripted verdict."""
    def __init__(self, factory):
        self.calls = []
        self.factory = factory

    def __call__(self, app_dir, checks, *, hidden_checks=None, ledger_dir=None, ctx=None):
        self.calls.append({
            "app_dir": str(app_dir), "checks": checks, "hidden_checks": hidden_checks,
            "seed": getattr(ctx, "seed", None),
        })
        return self.factory(app_dir, checks, hidden_checks, ctx)


def _pass_gate():
    return GateSpy(lambda ad, ch, hc, ctx: _verdict(seed=getattr(ctx, "seed", 0)))


def _student_good():
    return lambda prompt: GOOD_SLOT


# --------------------------------------------------------------------------- #
# Pre-registration                                                            #
# --------------------------------------------------------------------------- #
def test_preregister_is_hash_stamped(tmp_path):
    reg = preregister(_prereg_spec(), tmp_path)
    assert re.fullmatch(r"[0-9a-f]{64}", reg.sha256)
    assert (tmp_path / "PREREGISTRATION.json").exists()
    assert load_prereg(tmp_path).sha256 == reg.sha256


def test_changed_criterion_changes_hash():
    a = prereg_hash(_prereg_spec())
    tampered = _prereg_spec()
    tampered["criterion"]["min_lift"] = 0.01  # post-hoc loosening
    assert prereg_hash(tampered) != a  # the whole point: detectable


def test_preregister_rejects_incomplete_spec(tmp_path):
    bad = {"arms": ["generic"]}  # missing student/task_ids/gate_version/criterion
    with pytest.raises(PreregError):
        preregister(bad, tmp_path)


def test_run_refuses_without_prereg(tmp_path):
    with pytest.raises(PreregError):
        run_experiment(tmp_path, _packs(), [_task()], _student_good(),
                       gate_runner=_pass_gate())


def test_run_manifest_records_prereg_hash(tmp_path):
    reg = preregister(_prereg_spec(), tmp_path)
    run_experiment(tmp_path, _packs(), [_task()], _student_good(),
                   gate_runner=_pass_gate(), seed=0)
    manifest = json.loads((tmp_path / "runs" / "exp_0" / "run_manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["prereg_sha256"] == reg.sha256


def test_prereg_tamper_detected_at_scoring(tmp_path):
    preregister(_prereg_spec(), tmp_path)
    cells = run_experiment(tmp_path, _packs(), [_task()], _student_good(),
                           gate_runner=_pass_gate(), seed=0)
    # accept everything so we would otherwise reach OK/UNDERPOWERED
    for c in cells:
        record_acceptance(tmp_path, c.blind_label, True)
    # tamper AFTER the run
    reg_path = tmp_path / "PREREGISTRATION.json"
    spec = json.loads(reg_path.read_text(encoding="utf-8"))
    spec["criterion"]["min_lift"] = 0.0
    reg_path.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    assert score_experiment(tmp_path)["verdict"] == "PREREG_VIOLATION"


# --------------------------------------------------------------------------- #
# Paired design + identical gates                                             #
# --------------------------------------------------------------------------- #
def test_paired_design_every_task_every_pack(tmp_path):
    preregister(_prereg_spec(task_ids=["t1", "t2", "t3"]), tmp_path)
    tasks = [_task("t1"), _task("t2"), _task("t3")]
    cells = run_experiment(tmp_path, _packs(3), tasks, _student_good(),
                           gate_runner=_pass_gate(), seed=1)
    assert len(cells) == 9
    seen = {(c.task_id, c.arm) for c in cells}
    assert len(seen) == 9  # each (task, arm) exactly once


def test_identical_gates_across_arms(tmp_path):
    # holdout so hidden_checks is non-empty and must ALSO be identical across arms.
    hdir = tmp_path / "holdout"
    hdir.mkdir()
    (hdir / "fam.jsonl").write_text(
        json.dumps({"kind": "data_persists", "resource": {
            "create": {"method": "POST", "path": "/x", "form": {"name": "@hidden:h"}},
            "read_path": "/x", "table": "x", "column": "name"}}) + "\n",
        encoding="utf-8")
    preregister(_prereg_spec(), tmp_path)
    spy = _pass_gate()
    run_experiment(tmp_path, _packs(3), [_task("t1", holdout_family="fam")],
                   _student_good(), gate_runner=spy, holdout_dir=hdir, attempts=1, seed=7)
    assert len(spy.calls) == 3  # one gate run per arm
    first = spy.calls[0]
    for call in spy.calls[1:]:
        assert call["checks"] == first["checks"], "visible checks differ between arms"
        assert call["hidden_checks"] == first["hidden_checks"], "hidden checks differ between arms"
        assert call["seed"] == first["seed"], "gate seed differs between arms (randomness not paired)"
    assert first["hidden_checks"], "hidden_checks should have loaded from the holdout dir"


def test_pack_text_never_reaches_gate_runner(tmp_path):
    preregister(_prereg_spec(), tmp_path)
    packs = [PromptPack(pack_id=f"{a}-v1", arm=a,
                        system_addendum="PACKSENTINEL coaching " + a,
                        retry_hint="PACKSENTINEL {failure_class}")
             for a in ("generic", "same_family", "cross_vendor")]
    spy = _pass_gate()
    run_experiment(tmp_path, packs, [_task("t1")], _student_good(),
                   gate_runner=spy, attempts=1, seed=3)
    for call in spy.calls:
        blob = json.dumps(call, default=str)
        assert "PACKSENTINEL" not in blob, "pack text leaked into a gate-runner argument"


def test_hidden_token_never_in_student_prompt(tmp_path):
    preregister(_prereg_spec(), tmp_path)
    prompts: list[str] = []

    def student(prompt):
        prompts.append(prompt)
        return GOOD_SLOT

    # gate ALWAYS fails, detail leaks a hidden token; failure_class is coach-visible.
    def failing(ad, ch, hc, ctx):
        return _verdict(overall=False, hidden=False, failure_class="LOGIC_FAIL",
                        detail="row contained HIDDENTOKEN123 vault value")

    run_experiment(tmp_path, _packs(1), [_task("t1")], student,
                   gate_runner=GateSpy(failing), attempts=2, seed=0)
    assert prompts, "student was never prompted"
    for p in prompts:
        assert "HIDDENTOKEN123" not in p, "a hidden gate payload leaked into the student prompt"
    # coach_view (failure_class) DOES flow into the retry prompt
    assert any("LOGIC_FAIL" in p for p in prompts), "failure_class did not reach the retry prompt"


def test_invalid_slot_counts_as_fail_not_crash(tmp_path):
    preregister(_prereg_spec(), tmp_path)
    cells = run_experiment(tmp_path, _packs(1), [_task("t1")],
                           lambda prompt: "sorry, no json here", gate_runner=_pass_gate(),
                           seed=0)
    assert len(cells) == 1
    c = cells[0]
    assert c.slot_valid is False and c.overall_pass is False


# --------------------------------------------------------------------------- #
# Blinding                                                                     #
# --------------------------------------------------------------------------- #
def test_blind_labels_hide_provenance(tmp_path):
    preregister(_prereg_spec(), tmp_path)
    cells = run_experiment(tmp_path, _packs(3), [_task("t1")], _student_good(),
                           gate_runner=_pass_gate(), attempts=1, seed=0)
    sheet_path = export_blind_review(tmp_path)
    text = sheet_path.read_text(encoding="utf-8")
    for c in cells:
        assert c.pack_id not in text, "review sheet leaked a pack_id"
        assert c.arm not in text, "review sheet leaked an arm"
        assert re.fullmatch(r"[0-9a-f]{8}", c.blind_label)
    assert "provenance" not in text.lower() or "provenance" in text.lower()  # sheet has no provenance keys
    sheet = json.loads(text)
    for item in sheet["items"]:
        assert set(item) == {"blind_label", "task_id", "artifact_dir"}
    # sealed map round-trips label -> arm, and lives separately (scoring-only)
    sealed = json.loads((tmp_path / "runs" / "exp_0" / "blind_map.sealed.json")
                        .read_text(encoding="utf-8"))
    for c in cells:
        assert sealed[c.blind_label]["arm"] == c.arm


def test_acceptance_records_carry_no_provenance(tmp_path):
    preregister(_prereg_spec(), tmp_path)
    cells = run_experiment(tmp_path, _packs(3), [_task("t1")], _student_good(),
                           gate_runner=_pass_gate(), attempts=1, seed=0)
    for c in cells:
        record_acceptance(tmp_path, c.blind_label, True, note="looks fine")
    rows = [json.loads(l) for l in (tmp_path / "runs" / "exp_0" / "acceptance.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    for row in rows:
        assert set(row) == {"blind_label", "accepted", "note"}
        for c in cells:
            assert c.pack_id not in json.dumps(row)
            assert c.arm not in json.dumps(row)


def test_acceptance_completeness_gate(tmp_path):
    preregister(_prereg_spec(min_cell_n=1), tmp_path)
    cells = run_experiment(tmp_path, _packs(2), [_task("t1")], _student_good(),
                           gate_runner=_pass_gate(), attempts=1, seed=0)
    # record only one of the two passing cells
    record_acceptance(tmp_path, cells[0].blind_label, True)
    assert score_experiment(tmp_path)["verdict"] == "REVIEW_INCOMPLETE"
    record_acceptance(tmp_path, cells[1].blind_label, True)
    assert score_experiment(tmp_path)["verdict"] != "REVIEW_INCOMPLETE"


# --------------------------------------------------------------------------- #
# Metrics + retention (pure, table-driven)                                    #
# --------------------------------------------------------------------------- #
def _cell(task_id, arm, *, hidden, overall=True, visible=True, regression=False,
          prompt_chars=100, completion_chars=100):
    return CellResult(
        task_id=task_id, pack_id=f"{arm}-v1", arm=arm, attempts=1, slot_valid=True,
        visible_pass=visible, hidden_pass=hidden, overall_pass=overall,
        failure_class="REGRESSION_FAIL" if regression else None,
        prompt_chars=prompt_chars, completion_chars=completion_chars,
        artifact_dir="d", blind_label=f"{task_id}{arm}"[:8].ljust(8, "0"))


def test_scores_computed_from_cells():
    # same_family passes hidden 8/10, generic 5/10; acceptance 8/10 vs 6/10.
    cells, accept = [], {}
    for i in range(10):
        sf = _cell(f"t{i}", "same_family", hidden=(i < 8))
        gen = _cell(f"t{i}", "generic", hidden=(i < 5))
        cells += [sf, gen]
        accept[sf.blind_label] = i < 8
        accept[gen.blind_label] = i < 6
    per = aggregate_metrics(cells, accept)
    assert per["same_family"]["hidden_pass_rate"] == pytest.approx(0.8)
    assert per["generic"]["hidden_pass_rate"] == pytest.approx(0.5)
    assert per["same_family"]["acceptance_rate"] == pytest.approx(0.8)
    assert per["generic"]["acceptance_rate"] == pytest.approx(0.6)


def _arm(n, hidden, accept, cost_per_accepted, regression_rate=0.0):
    return {"n": n, "hidden_pass_rate": hidden, "acceptance_rate": accept,
            "cost_per_accepted": cost_per_accepted, "regression_rate": regression_rate,
            "visible_pass_rate": hidden, "overall_pass_rate": hidden,
            "mean_attempts": 1.0, "accepted": int(round(accept * n))}


CRIT = {"min_lift": 0.10, "cost_tolerance": 0.15, "min_cell_n": 20}


def test_retention_retains_when_both_lifts_and_costs_ok():
    per = {"generic": _arm(20, 0.50, 0.50, 100.0),
           "same_family": _arm(20, 0.70, 0.70, 105.0)}
    v = retention_verdict(per, CRIT)
    assert v["retain_same_family"] is True


def test_retention_refuses_when_only_one_metric_wins():
    # wins hidden (0.7 vs 0.5) but NOT acceptance (0.50 vs 0.50)
    per = {"generic": _arm(20, 0.50, 0.50, 100.0),
           "same_family": _arm(20, 0.70, 0.50, 100.0)}
    v = retention_verdict(per, CRIT)
    assert v["retain_same_family"] is False
    assert "acceptance" in v["reason"].lower()


def test_retention_refuses_when_cost_too_high():
    # wins both lifts but costs 2x -> exceeds generic*(1+tol)
    per = {"generic": _arm(20, 0.50, 0.50, 100.0),
           "same_family": _arm(20, 0.70, 0.70, 200.0)}
    v = retention_verdict(per, CRIT)
    assert v["retain_same_family"] is False
    assert "cost" in v["reason"].lower()


def test_retention_refuses_when_more_regressions():
    per = {"generic": _arm(20, 0.50, 0.50, 100.0, regression_rate=0.0),
           "same_family": _arm(20, 0.70, 0.70, 100.0, regression_rate=0.1)}
    v = retention_verdict(per, CRIT)
    assert v["retain_same_family"] is False
    assert "regression" in v["reason"].lower()


def test_underpowered_blocks_retention():
    per = {"generic": _arm(5, 0.50, 0.50, 100.0),
           "same_family": _arm(5, 0.90, 0.90, 100.0)}
    v = retention_verdict(per, CRIT)
    assert v["retain_same_family"] is False
    assert v.get("underpowered") is True


def test_retention_accepts_list_of_cells_and_prereg_object(tmp_path):
    reg = preregister(_prereg_spec(min_cell_n=1), tmp_path)
    cells = [_cell("t1", "same_family", hidden=True),
             _cell("t1", "generic", hidden=False)]
    v = retention_verdict(cells, reg)  # list + PreReg both accepted
    assert "retain_same_family" in v


# --------------------------------------------------------------------------- #
# Persistence, tolerance, live-dispatch honesty, CLI                          #
# --------------------------------------------------------------------------- #
def test_cells_jsonl_roundtrip(tmp_path):
    preregister(_prereg_spec(task_ids=["t1", "t2"]), tmp_path)
    cells = run_experiment(tmp_path, _packs(3), [_task("t1"), _task("t2")],
                           _student_good(), gate_runner=_pass_gate(), attempts=1, seed=0)
    lines = [json.loads(l) for l in (tmp_path / "runs" / "exp_0" / "cells.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == len(cells)
    assert lines[0] == cells[0].to_dict()
    assert CellResult.from_dict(lines[0]).blind_label == cells[0].blind_label


def test_missing_arm_tolerated_and_reported(tmp_path):
    preregister(_prereg_spec(min_cell_n=1), tmp_path)
    cells = run_experiment(tmp_path, _packs(2), [_task("t1")], _student_good(),
                           gate_runner=_pass_gate(), attempts=1, seed=0)
    for c in cells:
        record_acceptance(tmp_path, c.blind_label, True)
    result = score_experiment(tmp_path)
    assert set(result["arms_run"]) == {"generic", "same_family"}


def test_make_student_raises_on_unavailable_model(monkeypatch):
    from cortex_core import research
    monkeypatch.setattr(research, "_llm_complete", lambda *a, **k: None)
    student = pe.make_student("qwen35b")
    with pytest.raises(RuntimeError):
        student("any prompt")


def test_promptpack_load_reads_markdown_pack():
    repo = Path(__file__).resolve().parents[1]
    pack = PromptPack.load(repo / "packs" / "prompt-packs" / "generic.md")
    assert pack.arm == "generic"
    assert "JSON" in pack.system_addendum
    assert "{failure_class}" in pack.retry_hint


def test_build_prompt_firewall_only_coach_view_on_retry():
    task = _task("t1")
    pack = _packs(1)[0]
    verdict = _verdict(overall=False, hidden=False, failure_class="LOGIC_FAIL",
                       detail="SECRET_DETAIL_XYZ")
    retry = build_prompt(task, pack, coach_view(verdict))
    assert "LOGIC_FAIL" in retry
    assert "SECRET_DETAIL_XYZ" not in retry


def test_cli_init_refuses_overwrite(tmp_path):
    rc1 = pe.main(["init", "--exp-dir", str(tmp_path)])
    assert rc1 == 0
    before = (tmp_path / "PREREGISTRATION.json").read_text(encoding="utf-8")
    rc2 = pe.main(["init", "--exp-dir", str(tmp_path)])
    assert rc2 != 0
    assert (tmp_path / "PREREGISTRATION.json").read_text(encoding="utf-8") == before
