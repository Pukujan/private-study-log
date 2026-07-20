"""Tests for the calibration harness (cortex_core/calibration.py). No network."""

import json

import pytest

from cortex_core import calibration as C


# ---- Cohen's kappa ----

def test_kappa_perfect_agreement():
    g = ["supported", "unsupported", "partially_supported", "unverifiable"]
    assert C.cohens_kappa(g, list(g)) == pytest.approx(1.0)


def test_kappa_worse_than_chance_is_negative():
    g = ["supported", "supported", "unsupported", "unsupported"]
    p = ["unsupported", "unsupported", "supported", "supported"]
    assert C.cohens_kappa(g, p) < 0


def test_kappa_single_label_returns_agreement():
    # All gold + pred one label -> pe == 1.0; guard returns raw agreement (1.0).
    assert C.cohens_kappa(["supported"] * 4, ["supported"] * 4) == 1.0


def test_kappa_empty_is_zero():
    assert C.cohens_kappa([], []) == 0.0


def test_kappa_partial_agreement_between_0_and_1():
    g = ["supported", "supported", "unsupported", "unverifiable"]
    p = ["supported", "unsupported", "unsupported", "unverifiable"]  # 3/4 agree
    k = C.cohens_kappa(g, p)
    assert 0.0 < k < 1.0


# ---- confusion matrix ----

def test_confusion_matrix_counts():
    labels = ["supported", "unsupported"]
    g = ["supported", "supported", "unsupported"]
    p = ["supported", "unsupported", "unsupported"]
    m = C.confusion_matrix(g, p, labels)
    assert m["supported"]["supported"] == 1
    assert m["supported"]["unsupported"] == 1
    assert m["unsupported"]["unsupported"] == 1


# ---- anchor set loading ----

def test_load_anchor_set(tmp_path):
    p = tmp_path / "anchor.yaml"
    p.write_text(
        "cases:\n"
        "  - {id: x1, task_type: bugfix, claim: 'fix', evidence: [], gold_verdict: unverifiable}\n"
        "  - {id: x2, task_type: feature, claim: 'add', "
        "evidence: [{type: file, ref: a.py}], gold_verdict: partially_supported, probes: semantic}\n",
        encoding="utf-8",
    )
    cases = C.load_anchor_set(p)
    assert len(cases) == 2
    assert cases[0].id == "x1" and cases[0].gold_verdict == "unverifiable"
    assert cases[1].probes == "semantic"


# ---- JudgeRun scoring ----

def _cases():
    return [
        C.AnchorCase(id="c1", task_type="bugfix", claim="a", evidence=[], gold_verdict="supported"),
        C.AnchorCase(id="c2", task_type="feature", claim="b", evidence=[], gold_verdict="unsupported",
                     probes="semantic"),
        C.AnchorCase(id="c3", task_type="docs", claim="c", evidence=[], gold_verdict="unverifiable"),
    ]


def test_score_verdicts_and_summary():
    cases = _cases()
    verdicts = {"c1": "supported", "c2": "supported", "c3": "unverifiable"}  # c2 wrong
    run = C.score_verdicts("sometier", cases, verdicts)
    s = run.summary()
    assert s["n"] == 3
    assert s["accuracy"] == pytest.approx(2 / 3, abs=1e-3)  # summary rounds to 4dp
    # probe accuracy: only c2 is a probe, and it was wrong -> 0.0
    assert s["probe_accuracy"] == 0.0
    assert s["probe_n"] == 1
    assert any(d["id"] == "c2" for d in s["disagreements"])


def test_score_verdicts_missing_id_counts_as_unverifiable():
    cases = _cases()
    run = C.score_verdicts("t", cases, {"c1": "supported"})  # c2, c3 missing
    preds = {r["id"]: r["pred"] for r in run.rows}
    assert preds["c2"] == "unverifiable"
    assert preds["c3"] == "unverifiable"


def test_run_from_results_file_harness_format(tmp_path):
    cases = _cases()
    f = tmp_path / "sometier-x.json"
    f.write_text(json.dumps({
        "summary": {"tier": "sometier"},
        "rows": [
            {"id": "c1", "gold": "supported", "pred": "supported"},
            {"id": "c2", "gold": "unsupported", "pred": "unsupported"},
            {"id": "c3", "gold": "unverifiable", "pred": "supported"},
        ],
    }), encoding="utf-8")
    run = C._run_from_results_file(f, cases)
    s = run.summary()
    assert s["n"] == 3
    assert s["accuracy"] == pytest.approx(2 / 3, abs=1e-3)  # summary rounds to 4dp


def test_run_from_results_file_subagent_format(tmp_path):
    cases = _cases()
    f = tmp_path / "sonnet_verdicts.json"
    f.write_text(json.dumps({
        "judge": "sonnet",
        "verdicts": {
            "c1": {"verdict": "supported"},
            "c2": {"verdict": "unsupported"},
            "c3": {"verdict": "unverifiable"},
        },
    }), encoding="utf-8")
    run = C._run_from_results_file(f, cases)
    assert run.tier == "sonnet"
    assert run.summary()["accuracy"] == 1.0
