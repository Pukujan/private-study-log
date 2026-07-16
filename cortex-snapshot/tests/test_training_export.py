"""Frozen tests for the unified evaluator-training export."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.training_export.build_export import run, _split, _normalize, TIER, WEIGHT  # noqa: E402


def test_split_deterministic_and_stable():
    assert _split("coding__00001") == _split("coding__00001")
    assert _split("coding__00001") in ("train", "holdout")


def test_export_wellformed_and_disjoint():
    run()
    import json
    tr = [json.loads(l) for l in (ROOT / "evals/training_export/evaluator_train.jsonl")
          .read_text(encoding="utf-8").splitlines() if l.strip()]
    ho = [json.loads(l) for l in (ROOT / "evals/training_export/evaluator_holdout.jsonl")
          .read_text(encoding="utf-8").splitlines() if l.strip()]
    ids_tr, ids_ho = {r["id"] for r in tr}, {r["id"] for r in ho}
    assert ids_tr.isdisjoint(ids_ho), "train and holdout must not overlap"
    assert len(tr) > len(ho) > 0
    for r in tr + ho:
        assert r["gold_binary"] in ("pass", "fail")
        assert r["objectivity_tier"] in ("strong", "medium")
        assert r["weight"] == WEIGHT[r["objectivity_tier"]]
        assert "input" in r and r["label_authority"]


def test_security_label_mapping():
    r = _normalize("security", {"objective_label": "secure", "code": "x=1",
                                "vulnerability_class": "sql_injection", "detector_classes": []}, 0)
    assert r["gold_binary"] == "pass"
    r2 = _normalize("security", {"objective_label": "vulnerable", "code": "eval(x)",
                                 "vulnerability_class": "dangerous_eval",
                                 "detector_classes": ["dangerous_eval"]}, 1)
    assert r2["gold_binary"] == "fail"


def test_research_support_mapping():
    r = _normalize("research", {"claim": "x", "objective_status": "QUOTE_SUPPORTED",
                                "supported": True, "cited_sources": {}}, 0)
    assert r["gold_binary"] == "pass"
    r2 = _normalize("research", {"claim": "x", "objective_status": "QUOTE_UNSUPPORTED",
                                 "supported": False, "cited_sources": {}}, 1)
    assert r2["gold_binary"] == "fail"
