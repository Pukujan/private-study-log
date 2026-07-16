"""GAP-0022: the blinded authoring pipeline admits a well-formed authored task and rejects a
broken one -- tested with a fake completer (no real model calls)."""

from __future__ import annotations

import json

from cortex_core.bakeoff_authoring import author_batch, author_one

_GOOD = {
    "title": "clamp",
    "prompt": "Define clamp(x, lo, hi) returning x bounded to [lo, hi].",
    "entry": "clamp",
    "cases": [{"args": [5, 0, 10], "expected": 5}, {"args": [-3, 0, 10], "expected": 0},
              {"args": [99, 0, 10], "expected": 10}],
    "hidden": [{"args": [10, 0, 10], "expected": 10}],
    "reference": "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
    "wrong": ["def clamp(x, lo, hi):\n    return x\n"],
}


def test_author_one_admits_a_valid_task_with_blind_hash():
    task, why = author_one(lambda p: json.dumps(_GOOD), "glm-5.2")
    assert why == "admitted"
    assert task["author_hash"] and "glm" not in task["author_hash"]  # blind
    assert task["entry"] == "clamp"


def test_author_one_rejects_a_task_whose_reference_fails():
    bad = dict(_GOOD, reference="def clamp(x, lo, hi):\n    return x\n")  # ignores bounds -> fails hidden
    task, why = author_one(lambda p: json.dumps(bad), "some-model")
    assert task is None
    assert "reference" in why or "fails" in why


def test_author_batch_stops_at_n_and_records_rejects():
    # completer alternates good/garbage; batch should collect n good, log the garbage rejects.
    state = {"i": 0}

    def flaky(_p):
        state["i"] += 1
        return json.dumps(_GOOD) if state["i"] % 2 else "not json"

    admitted, rejects = author_batch(flaky, "mimo-2.5", 2)
    assert len(admitted) == 2
    assert len(rejects) >= 1
